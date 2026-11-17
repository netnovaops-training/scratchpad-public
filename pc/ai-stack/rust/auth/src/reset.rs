//! One-time codes for "I forgot my password".
//!
//! WHY A FILE AND NOT MEMORY
//! Same reasoning as the people file next to it: a plain JSON file you can
//! open and read is answerable and recoverable. It also survives a container
//! restart, which matters because a code that quietly stops working while
//! somebody is mid-reset looks exactly like "this site is broken".
//!
//! WHAT IS STORED, AND WHAT IS NOT
//! The code itself is NEVER stored. Only an argon2id hash of it, the same way
//! passwords are handled, so reading this file does not hand anyone a working
//! code. That matters more than it sounds: the file sits in the same volume as
//! people.json, and a reset code is a password for one minute.
//!
//! WHAT MAKES A 6-DIGIT CODE SAFE
//! Six digits is only a million possibilities, which is nothing to a script.
//! It is acceptable ONLY because all of these hold at once:
//!   * it expires in 15 minutes,
//!   * it is single use -- consumed the moment it works,
//!   * five wrong guesses destroy it,
//!   * requesting a new one replaces the old one, so an attacker cannot
//!     accumulate live codes by spamming the form, and
//!   * THE GUESS BUDGET BELONGS TO THE ADDRESS, NOT TO THE CODE.
//! Remove any one of those and the length becomes the problem.
//!
//! That last one was missing until 2026-09-07, and its absence cancelled the
//! third. Five wrong guesses destroyed the code -- and then asking for another
//! one handed out a fresh five. A million possibilities, five at a time, with
//! nothing to stop the loop. The counters now outlive the code they were
//! issued for, and there is a cooldown and an hourly cap on asking.

use std::path::Path;

use rand::rngs::SysRng;
use rand::rand_core::UnwrapErr;
use rand::RngExt;
use serde::{Deserialize, Serialize};

/// How long a code is good for.
pub const TTL_SECONDS: u64 = 15 * 60;

/// Wrong guesses allowed before the code is destroyed.
pub const MAX_ATTEMPTS: u8 = 5;

/// How long the guess and send budgets last, independent of any single code.
///
/// THIS IS WHAT CLOSES THE HOLE. Until 2026-09-07 `issue` set attempts back to
/// zero and there was no limit on asking for a code, so MAX_ATTEMPTS above
/// bought nothing: guess five times, press "email me a code" again, guess five
/// more, for as long as you like. Six digits is a million possibilities, which
/// is a real wall only if the guesses are actually capped -- and they were not.
/// It also meant anybody who knew Davetta's address could fill her inbox.
///
/// So the budget belongs to the ADDRESS FOR AN HOUR, not to the code. Asking
/// for a new code no longer refills it.
pub const WINDOW_SECONDS: u64 = 60 * 60;

/// Codes that may be sent to one address within a window.
pub const MAX_SENDS: u8 = 5;

/// Shortest gap between two codes to the same address. Stops the inbox
/// flooding on its own, before the hourly cap is even reached.
pub const RESEND_COOLDOWN: u64 = 60;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Reset {
    pub email: String,
    /// argon2id of the code. The code itself is never written down.
    /// EMPTY means the budget record outlived the code it was issued for --
    /// the counters have to survive the code, or destroying the code would
    /// destroy the evidence of how many guesses have been used.
    pub hash: String,
    /// Unix seconds when this stops working.
    pub expires: u64,
    /// Counts UP, and CARRIES ACROSS re-issues inside the window.
    #[serde(default)]
    pub attempts: u8,
    /// When the guess and send budgets reset. Records are kept until this,
    /// even after their code is dead.
    #[serde(default)]
    pub window_ends: u64,
    /// Codes sent to this address in the current window.
    #[serde(default)]
    pub sends: u8,
    /// When the most recent code went out, for the cooldown.
    #[serde(default)]
    pub last_sent: u64,
}

impl Reset {
    /// Whether this record still carries a code somebody could get right.
    fn code_live(&self, now: u64) -> bool {
        !self.hash.is_empty() && self.expires > now && self.attempts < MAX_ATTEMPTS
    }
}

/// What happened when somebody asked for a code.
pub enum Issued {
    /// Send it.
    Sent,
    /// Too soon after the last one. Seconds still to wait.
    TooSoon(u64),
    /// Out of codes for this window. Seconds until it resets.
    TooMany(u64),
}

pub fn read(path: &Path) -> Vec<Reset> {
    let Ok(bytes) = std::fs::read(path) else {
        return Vec::new();
    };
    serde_json::from_slice::<Vec<Reset>>(&bytes).unwrap_or_default()
}

/// Write atomically -- temp file then rename. A torn read here would look
/// like "nobody has a pending code", which would strand whoever was mid-reset.
pub fn write(path: &Path, list: &[Reset]) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let mut json = serde_json::to_vec_pretty(list)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    json.push(b'\n');
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, json)?;
    std::fs::rename(tmp, path)
}

/// A fresh 6-digit code, from the OS CSPRNG.
///
/// `gen_range` over the whole 6-digit space rather than six separate digits:
/// it is one draw from a uniform range, so no digit position is biased, and
/// the leading zero is preserved by formatting rather than by chance.
pub fn new_code() -> String {
    let n: u32 = UnwrapErr(SysRng).random_range(0..1_000_000);
    format!("{n:06}")
}

/// Drop records whose WINDOW has passed -- not merely whose code has.
///
/// Pruning on the code's expiry, as this used to, threw the counters away with
/// it: a burned-out code disappeared and the next request started from zero.
/// The record has to outlive the code for the budget to mean anything.
pub fn prune(list: &mut Vec<Reset>, now: u64) {
    list.retain(|r| r.window_ends > now);
}

/// Store a code for this address, replacing any previous one -- if the address
/// is still allowed one.
///
/// Replacing rather than appending is deliberate: asking for a second code
/// invalidates the first, so nobody can press the button twenty times and then
/// have twenty live codes to try against.
///
/// ATTEMPTS CARRY OVER. That is the whole point of this function's shape: a
/// new code must not hand out a fresh five guesses, or the cap on guessing is
/// decorative and six digits can be walked at leisure.
pub fn issue(list: &mut Vec<Reset>, email: &str, code: &str, now: u64)
    -> anyhow::Result<Issued>
{
    prune(list, now);

    let (attempts, window_ends, sends, last_sent) =
        match list.iter().find(|r| r.email == email) {
            Some(r) => (r.attempts, r.window_ends, r.sends, r.last_sent),
            None => (0, now + WINDOW_SECONDS, 0, 0),
        };

    if last_sent > 0 && now < last_sent + RESEND_COOLDOWN {
        return Ok(Issued::TooSoon(last_sent + RESEND_COOLDOWN - now));
    }
    if sends >= MAX_SENDS {
        return Ok(Issued::TooMany(window_ends.saturating_sub(now)));
    }

    let hash = common::people::hash_password(code)?;
    list.retain(|r| r.email != email);
    list.push(Reset {
        email: email.to_string(),
        hash,
        expires: now + TTL_SECONDS,
        attempts,
        window_ends,
        sends: sends + 1,
        last_sent: now,
    });
    Ok(Issued::Sent)
}

/// What happened when somebody submitted a code.
pub enum Check {
    /// Correct, in date, and now consumed.
    Ok,
    /// Wrong code. The attempt has been counted.
    Wrong,
    /// No live code for this address -- never issued, expired, already used,
    /// or burned through its attempts.
    None,
}

/// Verify and CONSUME a code.
///
/// On success the entry is removed before this returns, so the same code can
/// never be used twice -- including by two requests arriving together, because
/// the caller holds the file lock across this whole operation.
pub fn check(list: &mut Vec<Reset>, email: &str, code: &str, now: u64) -> Check {
    prune(list, now);
    let Some(i) = list.iter().position(|r| r.email == email) else {
        return Check::None;
    };
    if !list[i].code_live(now) {
        // The budget record is still here, which is the point -- but there is
        // no live code behind it, so there is nothing to check.
        return Check::None;
    }
    if common::people::verify(&list[i].hash, code) {
        // THE CODE IS CLEARED, THE RECORD IS NOT. Removing the whole entry
        // would hand back the send budget the moment somebody succeeded, and
        // the counters are what stop the next hour of guessing.
        list[i].hash.clear();
        list[i].expires = 0;
        Check::Ok
    } else {
        list[i].attempts += 1;
        // Burn the code rather than leave a nearly-dead one lying around --
        // but keep the record so the used-up guesses stay used up.
        if list[i].attempts >= MAX_ATTEMPTS {
            list[i].hash.clear();
            list[i].expires = 0;
        }
        Check::Wrong
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn issued(list: &mut Vec<Reset>, email: &str, code: &str, now: u64) -> Issued {
        issue(list, email, code, now).expect("hashing must not fail")
    }

    /// THE BUG THIS FILE WAS CHANGED FOR, pinned so it cannot come back.
    ///
    /// Before 2026-09-07: five wrong guesses destroyed the code, asking again
    /// minted a new one with attempts back at zero, and six digits could be
    /// walked one button press at a time. The guess budget must survive the
    /// code it was issued for.
    #[test]
    fn a_new_code_does_not_hand_back_the_used_guesses() {
        let mut list = Vec::new();
        let mut now = 1_000_000;
        assert!(matches!(issued(&mut list, "a@b.c", "111111", now), Issued::Sent));

        for _ in 0..MAX_ATTEMPTS {
            assert!(matches!(check(&mut list, "a@b.c", "000000", now), Check::Wrong));
        }

        // Past the cooldown, well inside the window.
        now += RESEND_COOLDOWN + 1;
        assert!(matches!(issued(&mut list, "a@b.c", "222222", now), Issued::Sent));

        // The new code must be dead on arrival: the guesses are already spent.
        assert!(matches!(check(&mut list, "a@b.c", "222222", now), Check::None),
                "a fresh code must not resurrect a spent guess budget");
    }

    #[test]
    fn a_second_code_inside_the_cooldown_is_refused() {
        let mut list = Vec::new();
        let now = 2_000_000;
        assert!(matches!(issued(&mut list, "a@b.c", "111111", now), Issued::Sent));
        assert!(matches!(issued(&mut list, "a@b.c", "222222", now + 1),
                         Issued::TooSoon(_)),
                "holding the button down must not send a second code");
    }

    #[test]
    fn an_address_runs_out_of_codes_within_the_window() {
        let mut list = Vec::new();
        let mut now = 3_000_000;
        for i in 0..MAX_SENDS {
            assert!(matches!(issued(&mut list, "a@b.c", "111111", now), Issued::Sent),
                    "send {i} should have gone out");
            now += RESEND_COOLDOWN + 1;
        }
        assert!(matches!(issued(&mut list, "a@b.c", "111111", now), Issued::TooMany(_)),
                "the hourly cap is what stops an inbox being filled");
    }

    #[test]
    fn the_budget_clears_when_the_window_passes() {
        let mut list = Vec::new();
        let mut now = 4_000_000;
        for _ in 0..MAX_SENDS {
            issued(&mut list, "a@b.c", "111111", now);
            now += RESEND_COOLDOWN + 1;
        }
        assert!(matches!(issued(&mut list, "a@b.c", "111111", now), Issued::TooMany(_)));
        now += WINDOW_SECONDS;
        assert!(matches!(issued(&mut list, "a@b.c", "111111", now), Issued::Sent),
                "a lockout that never clears is a lockout, not a limit");
    }

    /// Succeeding must not refund the send budget either -- otherwise the
    /// hourly cap is escapable by getting one code right.
    #[test]
    fn a_correct_code_is_consumed_and_the_budget_stands() {
        let mut list = Vec::new();
        let now = 5_000_000;
        issued(&mut list, "a@b.c", "424242", now);
        assert!(matches!(check(&mut list, "a@b.c", "424242", now), Check::Ok));
        assert!(matches!(check(&mut list, "a@b.c", "424242", now), Check::None),
                "a code must work exactly once");
        assert_eq!(list.iter().find(|r| r.email == "a@b.c").map(|r| r.sends), Some(1),
                   "the send must still be counted after a success");
    }
}
