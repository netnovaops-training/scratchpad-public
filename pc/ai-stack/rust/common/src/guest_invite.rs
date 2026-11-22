//! Guest invites: a short, shareable link that lets Jason pre-configure an
//! account (label, which pages, how long) and hand it to somebody who has no
//! email on file at all -- they just click the link and make a passkey.
//!
//! WHY THIS IS A SEPARATE FILE FROM people.json/passkeys.json, NOT A FIELD
//! ON EITHER. An invite is a THING THAT HAPPENS ONCE: created, maybe
//! redeemed, done. People and passkeys are accounts that persist. Mixing a
//! one-shot event into either file would mean every reader of that file has
//! to understand and skip over dead invites forever. This file can be wiped
//! clean any time with no loss -- by the time an invite is redeemed, the
//! account it made lives on in people.json and passkeys.json same as any
//! other, and the invite record itself has no further job.
//!
//! WHY THE CODE IS STORED HASHED, NOT PLAIN. Same discipline this project
//! already uses for the email-recovery link (see auth's RecoveryFile): if
//! this file were ever copied or leaked, a hash cannot be turned back into a
//! working link, but a plaintext code can.
//!
//! IN `common`, NOT IN EITHER BINARY, for the same reason people.rs and
//! trial.rs are: admin MINTS an invite (writes this file plus a matching
//! person+guest record), auth REDEEMS one (reads this file, starts the
//! passkey ceremony). Two copies of this struct would be two definitions of
//! the file format, and the first disagreement is a silently broken invite.

use std::path::Path;

use rand::rand_core::UnwrapErr;
use rand::rngs::SysRng;
use rand::Rng;
use sha2::{Digest, Sha256};

/// How many characters in a shareable code. 14, from a 33-character alphabet
/// with no ambiguous characters (see `new_code`) is about 70 bits of entropy
/// -- nowhere near brute-forceable, same standard as every other generated
/// secret in this project. This is a stronger code than the (deliberately
/// weaker, "does not authenticate anyone") trial invite code in trial.rs,
/// because redeeming THIS one creates a real, scoped account.
const CODE_LEN: usize = 14;

#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize)]
pub struct Store {
    #[serde(default)]
    pub invites: Vec<Invite>,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Invite {
    /// SHA-256 of the code. The code itself exists only in the link Jason
    /// sends; a copy of this file cannot be turned into a working invite.
    pub code_hash: String,
    /// The synthetic placeholder email created for this guest (e.g.
    /// "guest-brandon-a3f9@invite.local"). Never real, never shown to the
    /// guest, exists only because people.json is keyed by email -- see
    /// people.rs. This is how the redemption page finds the matching
    /// Person/UserKeys record.
    pub target_email: String,
    /// Jason's own name for this invite, already made unique ("Test-3") by
    /// the admin side before this is written -- see `disambiguate_label` in
    /// the admin binary. Shown to the guest on the redemption page and used
    /// as their passkey's display name.
    pub label: String,
    /// How many days the matching guest account (and its one passkey) last,
    /// from the moment it was CREATED -- 1, 7 or 30, matching the existing
    /// guest-account day choices already used by the owner tools
    /// (see auth's exec_guest / enrol_allowed). Needed again at redemption
    /// time to pass the same value into begin_registration's loaner-days
    /// parameter, so the one passkey a guest may hold expires exactly when
    /// the account does, never later.
    pub days: u64,
    #[serde(default)]
    pub created: u64,
    /// Burned the moment a passkey is actually created from this invite (see
    /// the "WHEN THIS IS MARKED REDEEMED" note on `redeem` below). A
    /// redeemed invite is kept, not deleted, purely as a short audit trail
    /// on the admin side ("Brandon's invite was used on this date") --
    /// nothing reads this field for access control after redemption, since
    /// the account's own passkey and account_expires are what gate access
    /// from that point on.
    #[serde(default)]
    pub redeemed: bool,
    #[serde(default)]
    pub redeemed_at: u64,
}

pub fn code_hash(code: &str) -> String {
    let digest = Sha256::digest(code.trim().to_ascii_uppercase().as_bytes());
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

/// A short human code, same alphabet and generation method as
/// `trial::new_invite_code` (ambiguous characters -- O/0, I/1 -- left out, so
/// nothing is lost retyping one off a text message), just longer: this one
/// grants real scoped access, the trial code does not.
pub fn new_code() -> String {
    const ALPHABET: &[u8] = b"ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    let mut bytes = [0u8; CODE_LEN];
    UnwrapErr(SysRng).fill_bytes(&mut bytes);
    bytes
        .iter()
        .map(|b| ALPHABET[*b as usize % ALPHABET.len()] as char)
        .collect()
}

pub fn read(path: &Path) -> Store {
    match std::fs::read_to_string(path) {
        Ok(text) => serde_json::from_str(&text).unwrap_or_else(|e| {
            // Loud, then empty -- same choice trial.rs makes: a corrupt file
            // must not be silently replaced, but refusing to start the whole
            // sign-in service over a feature nobody is mid-invite on is worse.
            tracing::error!("guest-invites file at {} is not valid JSON: {e}", path.display());
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
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, text)?;
    std::fs::rename(&tmp, path)
}

#[derive(Debug, PartialEq, Eq)]
pub enum RedeemError {
    /// Wrong code, already redeemed, or simply never existed -- ONE message
    /// for all three (see below).
    Invalid,
}

/// Look up an invite by its plaintext code, without spending it. Used by the
/// GET page (so a mail/link scanner opening the URL early cannot burn it) and
/// by the POST start-of-registration step (checks run before anything is
/// marked redeemed).
///
/// A REDEEMED INVITE RETURNS NONE, same as a wrong or unknown code -- the
/// caller cannot tell "that code never existed" from "that code was already
/// used", which matters: telling a stranger the difference turns this into a
/// way to probe which codes Jason has handed out.
pub fn peek<'a>(store: &'a Store, code: &str) -> Option<&'a Invite> {
    let h = code_hash(code);
    store
        .invites
        .iter()
        .find(|i| i.code_hash == h && !i.redeemed)
}

/// WHEN THIS IS MARKED REDEEMED (changed 2026-11-11): only once the passkey
/// is actually verified and saved (auth's register_finish), not when the
/// challenge is issued. The old "spend at challenge" rule locked guests out
/// for real: a cancelled or failed phone prompt left them with no passkey
/// AND a dead link (reproduced live in Chrome with test guest Test-E). The
/// race the old rule guarded against is still closed: register_finish
/// holds the invite lock, re-checks the code is unspent, and refuses the
/// second of two parallel finishes.
pub fn redeem(store: &mut Store, code: &str, now: u64) -> Result<Invite, RedeemError> {
    let h = code_hash(code);
    let item = store
        .invites
        .iter_mut()
        .find(|i| i.code_hash == h && !i.redeemed)
        .ok_or(RedeemError::Invalid)?;
    item.redeemed = true;
    item.redeemed_at = now;
    Ok(item.clone())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mk(code: &str, email: &str, label: &str) -> Invite {
        Invite {
            code_hash: code_hash(code),
            target_email: email.to_string(),
            label: label.to_string(),
            days: 7,
            created: 1000,
            redeemed: false,
            redeemed_at: 0,
        }
    }

    #[test]
    fn a_code_redeems_once_then_refuses() {
        let mut store = Store::default();
        store.invites.push(mk("ABCD1234567890", "guest-x@invite.local", "Test-1"));

        assert!(peek(&store, "ABCD1234567890").is_some());
        let redeemed = redeem(&mut store, "ABCD1234567890", 2000).expect("first redeem works");
        assert_eq!(redeemed.target_email, "guest-x@invite.local");

        // Second redemption of the SAME code must fail.
        assert!(peek(&store, "ABCD1234567890").is_none());
        assert!(redeem(&mut store, "ABCD1234567890", 2001).is_err());
    }

    #[test]
    fn an_unknown_code_is_refused_the_same_way_as_a_redeemed_one() {
        let mut store = Store::default();
        store.invites.push(mk("REALCODE000000", "guest-y@invite.local", "Robin"));
        // Never existed:
        assert!(redeem(&mut store, "NOPE00000000000", 10).is_err());
    }

    #[test]
    fn the_code_is_never_stored_in_plain_text() {
        let code = "SECRETCODE1234";
        let inv = mk(code, "guest-z@invite.local", "Brandon");
        assert_ne!(inv.code_hash, code);
        assert_eq!(inv.code_hash.len(), 64, "sha-256 hex is 64 characters");
    }

    #[test]
    fn code_matching_is_case_and_trim_insensitive_like_trial_codes() {
        let mut store = Store::default();
        store.invites.push(mk("ABCD1234567890", "guest-x@invite.local", "Test-1"));
        // Typed lowercase, or with stray whitespace from a copy-paste:
        assert!(peek(&store, "abcd1234567890").is_some());
        assert!(peek(&store, "  ABCD1234567890  ").is_some());
    }

    #[test]
    fn new_code_has_no_ambiguous_characters_and_the_right_length() {
        for _ in 0..50 {
            let c = new_code();
            assert_eq!(c.len(), CODE_LEN);
            assert!(!c.contains(['O', '0', 'I', '1']), "code {c} has an ambiguous character");
        }
    }

    #[test]
    fn a_round_trip_keeps_everything() {
        let dir = std::env::temp_dir().join("guest-invite-round-trip-test");
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("guest-invites.json");

        let mut store = Store::default();
        store.invites.push(mk("ROUNDTRIP00001", "guest-rt@invite.local", "RoundTrip"));
        write(&p, &store).unwrap();

        let back = read(&p);
        assert_eq!(back.invites.len(), 1);
        assert_eq!(back.invites[0].label, "RoundTrip");
        assert!(!back.invites[0].redeemed);
        std::fs::remove_file(&p).ok();
    }
}
