//! Trial access: a fixed number of questions, redeemed once with an invite
//! code, for somebody who has no household account at all.
//!
//! IN `common` RATHER THAN IN THE AUTH SERVICE, because two binaries need the
//! same definition of a trial: auth spends the questions, and the admin
//! console mints the codes and shows what has been used. Two copies of these
//! structs would be two definitions of the file format, and the first
//! disagreement between them would be a silently corrupted trials.json.
//!
//! WHAT A TRIAL DELIBERATELY IS NOT. It is not a cheap household account. The
//! assistant this app normally talks to can read Paperless documents, the
//! shelf, recipes and Casey's service record, and NONE of that may be reachable
//! by somebody who typed an email address into a phone. So a trial reaches
//! exactly one thing -- `/app/chat`, which is a plain proxy to the model with
//! no household context injected -- and every other `/app/*` route refuses it
//! by default rather than by having been remembered. See `Caller` in llm.rs:
//! the allowed list is short and explicit, so a route added next year is
//! closed to trials until somebody opens it on purpose.
//!
//! WHY AN INVITE CODE RATHER THAN OPEN SELF-SERVE. Jason's choice, and the
//! right one: without it, anybody who comes across the APK gets 8 model calls
//! against any address they care to type, and can repeat that with throwaway
//! addresses indefinitely. The code does not authenticate anyone -- it is not
//! a password and is not treated as one -- it just means trials come from
//! people he actually sent the app to.
//!
//! THE EMAIL IS NOT VERIFIED, and nothing here pretends otherwise. It is how
//! the person is addressed and how Jason knows who is asking to continue; it
//! is not evidence of identity, which is why a trial gets no access to
//! anything belonging to anybody.

use std::path::Path;

use rand::rngs::SysRng;
use rand::rand_core::UnwrapErr;
use rand::Rng;
use serde::{Deserialize, Serialize};

/// Questions in a trial, unless the invite says otherwise. Jason's number.
pub const DEFAULT_QUOTA: u32 = 8;

/// A trial token stops working after this, quota or no quota. The quota is the
/// real limit; this exists so an abandoned token is not valid forever on a
/// phone somebody sold.
pub const TRIAL_TTL: u64 = 60 * 60 * 24 * 30;

fn default_quota() -> u32 {
    DEFAULT_QUOTA
}
fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Store {
    #[serde(default)]
    pub invites: Vec<Invite>,
    #[serde(default)]
    pub trials: Vec<Trial>,
}

/// A code Jason hands out with the app link. One code can serve everybody he
/// sends it to, or he can mint one per person and see who used it.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Invite {
    /// Stored normalised (upper case, letters and digits only) so that what
    /// somebody types on a phone keyboard matches what was written down.
    pub code: String,
    /// What this code was for, in Jason's words. Shown in the admin only.
    #[serde(default)]
    pub label: String,
    #[serde(default = "default_quota")]
    pub quota: u32,
    #[serde(default = "default_true")]
    pub active: bool,
    #[serde(default)]
    pub created: u64,
    /// How many DIFFERENT people may redeem it. 0 means no limit.
    #[serde(default)]
    pub max_uses: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Trial {
    pub email: String,
    pub code: String,
    /// The bearer token the app holds. Opaque and random -- deliberately NOT a
    /// JWT: a trial has server-side state (a counter) on every single call, so
    /// there is nothing to gain from a token that can be validated without
    /// looking, and a revocable one is strictly better.
    pub token: String,
    pub used: u32,
    pub quota: u32,
    pub started: u64,
    pub last: u64,
    /// Jason has already been told this trial ran out. Without it he gets an
    /// email every time the person taps send again, which is precisely when
    /// somebody taps send again.
    #[serde(default)]
    pub notified: bool,
}

impl Trial {
    pub fn remaining(&self) -> u32 {
        self.quota.saturating_sub(self.used)
    }
    pub fn expired(&self, now: u64) -> bool {
        now.saturating_sub(self.started) > TRIAL_TTL
    }
}

/// Upper case, letters and digits only.
///
/// Phone keyboards autocapitalise, autocorrect and helpfully insert spaces, and
/// a person reading a code off a message will type the hyphens they see. None
/// of that should be the difference between a code working and not.
pub fn normalise_code(raw: &str) -> String {
    raw.chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .map(|c| c.to_ascii_uppercase())
        .collect()
}

/// 32 bytes from the OS CSPRNG, hex. Not guessable, and not derived from the
/// email or the code -- knowing either must not let anybody construct a token.
pub fn new_token() -> String {
    let mut bytes = [0u8; 32];
    UnwrapErr(SysRng).fill_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// A short human code, for the admin to hand out. Ambiguous characters are
/// left out: O/0 and I/1 cost more in mistyped codes than they add in space.
pub fn new_invite_code() -> String {
    const ALPHABET: &[u8] = b"ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    let mut bytes = [0u8; 8];
    UnwrapErr(SysRng).fill_bytes(&mut bytes);
    bytes
        .iter()
        .map(|b| ALPHABET[*b as usize % ALPHABET.len()] as char)
        .collect()
}

pub fn read(path: &Path) -> Store {
    match std::fs::read_to_string(path) {
        Ok(text) => serde_json::from_str(&text).unwrap_or_else(|e| {
            // Loud, and then empty. A corrupt file must not be silently
            // replaced with a blank one on the next write -- but refusing to
            // start would take the whole sign-in service down over a feature
            // nobody is mid-way through using.
            tracing::error!("trials file at {} is not valid JSON: {e}", path.display());
            Store::default()
        }),
        Err(_) => Store::default(),
    }
}

pub fn write(path: &Path, store: &Store) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let text = serde_json::to_string_pretty(store)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    // Written beside the target and renamed, so a crash mid-write leaves the
    // previous file intact rather than a truncated one.
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, text)?;
    std::fs::rename(&tmp, path)
}

#[derive(Debug)]
pub enum StartError {
    UnknownCode,
    CodeDisabled,
    CodeFull,
    BadEmail,
}

impl StartError {
    /// What the person is told. Deliberately the SAME sentence for a code that
    /// never existed and one that was switched off: the difference is Jason's
    /// business, and telling a stranger which of the two it was turns the
    /// endpoint into a way to test codes.
    pub fn message(&self) -> &'static str {
        match self {
            StartError::BadEmail => "That does not look like an email address.",
            _ => "That code is not valid. Check it with whoever sent you the app.",
        }
    }
}

/// Redeem a code, or hand back the trial this person already has.
///
/// RETURNING THE EXISTING TRIAL IS THE POINT, not a convenience. If this minted
/// a fresh one per call, the quota would reset every time somebody reinstalled
/// the app or tapped the button twice -- which is not a trial, it is an
/// unlimited account with extra steps.
pub fn start(store: &mut Store, email: &str, code: &str, now: u64) -> Result<Trial, StartError> {
    let email = crate::people::normalise(email);
    if !email.contains('@') || !email.contains('.') || email.len() < 5 {
        return Err(StartError::BadEmail);
    }
    let code = normalise_code(code);

    let invite = store
        .invites
        .iter()
        .find(|i| i.code == code)
        .ok_or(StartError::UnknownCode)?
        .clone();

    if !invite.active {
        return Err(StartError::CodeDisabled);
    }

    if let Some(existing) = store.trials.iter_mut().find(|t| t.email == email) {
        // A trial that ran out of time gets a new token and a new clock, but
        // NOT a new counter -- see above.
        if existing.expired(now) {
            existing.token = new_token();
            existing.started = now;
        }
        existing.last = now;
        return Ok(existing.clone());
    }

    if invite.max_uses > 0 {
        let redeemed = store.trials.iter().filter(|t| t.code == code).count() as u32;
        if redeemed >= invite.max_uses {
            return Err(StartError::CodeFull);
        }
    }

    let trial = Trial {
        email,
        code,
        token: new_token(),
        used: 0,
        quota: invite.quota.max(1),
        started: now,
        last: now,
        notified: false,
    };
    store.trials.push(trial.clone());
    Ok(trial)
}

#[derive(Debug)]
pub enum Charge {
    /// The question may go ahead. `remaining` is what is left AFTER it.
    Ok { email: String, remaining: u32 },
    /// Out of questions. `notify` is true exactly once, for the call that
    /// first discovers it, so Jason gets one email rather than one per tap.
    Exhausted { email: String, notify: bool },
    /// Not a trial token, or one that has passed its TTL.
    Invalid,
}

/// Look up a trial by token WITHOUT spending anything.
pub fn peek<'a>(store: &'a Store, token: &str, now: u64) -> Option<&'a Trial> {
    store
        .trials
        .iter()
        .find(|t| t.token == token && !t.expired(now))
}

/// Spend one question.
///
/// CALLED ONLY AFTER THE UPSTREAM ACCEPTED THE REQUEST. Charging first would
/// mean a model outage silently eats somebody's trial, and eight is a small
/// enough number that losing one to a 502 is the difference between a fair
/// trial and an annoying one.
pub fn charge(store: &mut Store, token: &str, now: u64) -> Charge {
    let Some(trial) = store
        .trials
        .iter_mut()
        .find(|t| t.token == token && !t.expired(now))
    else {
        return Charge::Invalid;
    };

    trial.last = now;

    if trial.used >= trial.quota {
        let notify = !trial.notified;
        trial.notified = true;
        return Charge::Exhausted {
            email: trial.email.clone(),
            notify,
        };
    }

    trial.used += 1;
    Charge::Ok {
        email: trial.email.clone(),
        remaining: trial.remaining(),
    }
}
