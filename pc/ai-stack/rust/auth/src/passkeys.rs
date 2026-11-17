//! Passkey (WebAuthn) sign-in: a SECOND way in, beside the password.
//!
//! THE PASSWORD STAYS. Nothing here changes how a password sign-in works, and
//! if anything in this file breaks (a damaged passkeys.json, a wrong setting, a
//! phone that will not cooperate) the password form is still there. That is the
//! whole safety net, so it is kept that way on purpose.
//!
//! WHAT A PASSKEY IS, HERE
//! The phone or laptop keeps a private key behind a fingerprint, face or PIN.
//! This server keeps only the matching PUBLIC key and a counter, in
//! passkeys.json. There is nothing in that file that signs anybody in by being
//! read, which is why it can sit beside people.json without raising the stakes.
//!
//! SAME SESSION AS THE PASSWORD. A passkey sign-in ends in `issue_session`, the
//! very function the password path uses, so the cookie, forward_auth and the
//! OIDC clients behind it notice no difference. That cookie is stamped with the
//! password fingerprint, so a person needs a password on file for a session to
//! verify at all. The rules below follow from that: passkeys are for people who
//! have a password, and a person flagged `must_change` signs in with the
//! password first.
//!
//! USERNAMELESS. Sign-in starts with no email: the browser offers the passkeys
//! it has for this site, the chosen one carries the user handle (a random id
//! stored here, not the email), and this file maps it back to the person.
//! Enrolment asks for a discoverable (resident) key so that always works.
//!
//! STATE IN MEMORY, SINGLE USE. The half-finished challenge for a sign-in or an
//! enrolment lives in memory for five minutes and is removed the moment it is
//! looked up, successful or not. A restart only means "try again".

use std::collections::HashMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use askama::Template;
use axum::{
    extract::State,
    http::{header, HeaderMap, StatusCode},
    response::{Html, IntoResponse, Response},
    Json,
};
use rand::Rng;
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::{Digest, Sha256};
use webauthn_rs::prelude::{
    DiscoverableAuthentication, DiscoverableKey, Passkey, PasskeyAuthentication,
    PasskeyRegistration, PublicKeyCredential, RegisterPublicKeyCredential, Url, Uuid, Webauthn,
    WebauthnBuilder,
};

use crate::AppState;

/// How long a started sign-in or enrolment may sit before it is dead.
const CHALLENGE_LIFE: Duration = Duration::from_secs(300);
/// Hard cap on challenges waiting at once, so a flood cannot eat memory.
const MAX_PENDING: usize = 500;
const NAME_MAX: usize = 64;
const LABEL_MAX: usize = 64;

/// Read by the sign-in page to decide whether to show the passkey button.
pub static ENABLED: AtomicBool = AtomicBool::new(false);

const SCRIPT: &str = include_str!("../templates/passkey.js");

// -----------------------------------------------------------------------------
//  The file: passkeys.json
// -----------------------------------------------------------------------------

#[derive(Serialize, Deserialize, Default, Clone)]
pub struct PasskeyFile {
    #[serde(default)]
    pub users: Vec<UserKeys>,
}

#[derive(Serialize, Deserialize, Clone, Default)]
pub struct UserKeys {
    pub email: String,
    /// The WebAuthn "user handle": random, so the email never leaves the server.
    /// Left out in a hand-written entry (for a label only): one is made when
    /// the first passkey is added.
    #[serde(default)]
    pub user_id: String,
    /// Optional name shown in the passkey manager. Empty means "work it out
    /// from people.json / the email" (see `display_label`). Set on the account
    /// page, or by hand in this file.
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub keys: Vec<StoredKey>,
    /// Password sign-in is off for this person. Only ever on while they hold
    /// at least two lasting passkeys; see `can_set_passkey_only`. Lives here,
    /// not in people.json, so that file's shape is untouched.
    #[serde(default)]
    pub passkey_only: bool,
    /// "Add a passkey?" offer after a password sign-in: do not ask again
    /// before this unix time (0 = ask).
    #[serde(default)]
    pub offer_snooze_until: u64,
    /// "Don't ask again" was chosen.
    #[serde(default)]
    pub offer_never: bool,
    /// Most passkeys this person may hold; 0 means the default (5). Set by the
    /// owner.
    #[serde(default)]
    pub cap: u32,
    /// A guest (an employer, a visitor): may hold only ONE temporary passkey,
    /// and nothing works for them after `account_expires`. Set by the owner.
    #[serde(default)]
    pub guest: bool,
    /// Unix seconds when a guest account ends; 0 = no end date.
    #[serde(default)]
    pub account_expires: u64,
}

pub const DEFAULT_CAP: usize = 5;
pub const MAX_CAP: usize = 20;

/// How many passkeys this person may hold.
pub fn cap_for(user: Option<&UserKeys>) -> usize {
    match user {
        Some(u) if u.cap > 0 => (u.cap as usize).min(MAX_CAP),
        _ => DEFAULT_CAP,
    }
}

/// Has this person's (guest) account ended?
pub fn account_expired(u: &UserKeys, now: u64) -> bool {
    u.account_expires != 0 && now >= u.account_expires
}

/// May this person add a passkey of `days` length right now? Guests are held to
/// one temporary passkey that never outlives the account.
pub fn enrol_allowed(user: Option<&UserKeys>, days: u64, now: u64) -> Result<(), String> {
    if let Some(u) = user {
        if account_expired(u, now) {
            return Err("This account has ended.".to_string());
        }
        if u.keys.len() >= cap_for(Some(u)) {
            return Err(format!(
                "You already have the most passkeys this account allows ({}). \
                 Remove one first, or ask Jason to raise the limit.",
                cap_for(Some(u))
            ));
        }
        if u.guest {
            if days == 0 {
                return Err(
                    "A guest account can only hold a temporary passkey. Pick 1, 7 or 30 days."
                        .to_string(),
                );
            }
            if !u.keys.is_empty() {
                return Err("A guest account can hold one passkey. Remove it first.".to_string());
            }
            if u.account_expires != 0 && now + days * 86400 > u.account_expires {
                return Err(
                    "That would last longer than this guest account. Pick a shorter time."
                        .to_string(),
                );
            }
        }
    }
    Ok(())
}

/// The caller's address as Caddy reports it: the right-most X-Forwarded-For
/// entry (Caddy SETS the header to the address it saw, see the (appproxy)
/// snippet). If it does not parse as an address, everyone without one shares
/// the "unknown" bucket, which only ever limits that bucket.
pub fn client_ip(headers: &HeaderMap) -> String {
    headers
        .get("x-forwarded-for")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.rsplit(',').next())
        .map(str::trim)
        .and_then(|s| s.parse::<std::net::IpAddr>().ok())
        .map(|ip| ip.to_string())
        .unwrap_or_else(|| "unknown".to_string())
}

/// How long "Not now" holds the offer off.
pub const SNOOZE_SECS: u64 = 7 * 86400;

/// Should the "add a passkey?" page appear for this person right now?
/// Yes when they have no passkey, have not said "don't ask again", and any
/// snooze has run out. A person with no entry in the file at all is "yes".
pub fn offer_due(user: Option<&UserKeys>, now: u64) -> bool {
    match user {
        None => true,
        Some(u) => u.keys.is_empty() && !u.offer_never && !u.guest && now >= u.offer_snooze_until,
    }
}

#[derive(Serialize, Deserialize, Clone)]
pub struct StoredKey {
    /// Short random id for the Remove button. Not the credential id.
    pub id: String,
    pub name: String,
    pub added: u64,
    #[serde(default)]
    pub last_used: u64,
    /// Unix seconds when a LOANER passkey stops working; 0 means it never does.
    #[serde(default)]
    pub expires: u64,
    /// What the browser said it was when this passkey was added, reduced to a
    /// family like "Chrome on Android" (see `ua_family`). Never an address.
    #[serde(default)]
    pub added_device: String,
    /// The same, for the most recent sign-in, and the site address it was used
    /// on (e.g. "example.org"). For the owner's view only.
    #[serde(default)]
    pub last_device: String,
    #[serde(default)]
    pub last_host: String,
    /// Public key, counter and flags. Nothing secret.
    pub passkey: Passkey,
}

#[derive(Debug, PartialEq)]
pub enum LoadError {
    Corrupt,
}

/// Missing or empty file means "no passkeys yet". A file that does not parse is
/// an ERROR, never "empty": treating damage as empty would let the next save
/// quietly overwrite everybody's passkeys.
pub fn load(path: &Path) -> Result<PasskeyFile, LoadError> {
    load_any(path)
}

fn load_any<T: serde::de::DeserializeOwned + Default>(path: &Path) -> Result<T, LoadError> {
    let bytes = match std::fs::read(path) {
        Ok(b) => b,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(T::default()),
        Err(_) => return Err(LoadError::Corrupt),
    };
    if bytes.iter().all(|b| b.is_ascii_whitespace()) {
        return Ok(T::default());
    }
    serde_json::from_slice(&bytes).map_err(|_| LoadError::Corrupt)
}

fn write_private(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let mut o = std::fs::OpenOptions::new();
    o.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600);
    }
    let mut f = o.open(path)?;
    f.write_all(bytes)?;
    f.sync_all()
}

/// Temp file (mode 600), keep the previous file as .bak, then rename into
/// place, so a reader never sees half a file.
pub fn save(path: &Path, file: &PasskeyFile) -> std::io::Result<()> {
    save_any(path, file)
}

fn save_any<T: Serialize>(path: &Path, file: &T) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let mut json = serde_json::to_vec_pretty(file)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    json.push(b'\n');
    let tmp = path.with_extension("tmp");
    write_private(&tmp, &json)?;
    if path.exists() {
        let bak = path.with_extension("json.bak");
        if let Err(e) = std::fs::copy(path, &bak) {
            tracing::warn!("could not keep a .bak of the passkey file: {e}");
        }
    }
    std::fs::rename(&tmp, path)
}

impl PasskeyFile {
    fn user(&self, email: &str) -> Option<&UserKeys> {
        self.users.iter().find(|u| u.email == email)
    }
    fn cred_in_use(&self, id: &webauthn_rs::prelude::CredentialID) -> bool {
        self.users
            .iter()
            .any(|u| u.keys.iter().any(|k| k.passkey.cred_id() == id))
    }
}

/// May this key be removed? Not if it would leave the person with no way in.
/// (Today every account that can hold a session has a password, so this only
/// bites if that ever changes; it is here so the rule lives in one place.)
pub fn can_remove(has_password: bool, key_count: usize) -> bool {
    has_password || key_count > 1
}

pub fn remove_key(
    file: &mut PasskeyFile,
    email: &str,
    key_id: &str,
    has_password: bool,
) -> Result<String, &'static str> {
    let Some(u) = file.users.iter_mut().find(|u| u.email == email) else {
        return Err("I can't find that passkey.");
    };
    let Some(pos) = u.keys.iter().position(|k| k.id == key_id) else {
        return Err("I can't find that passkey.");
    };
    if !can_remove(has_password, u.keys.len()) {
        return Err(
            "That is your only way to sign in, so it can't be removed. Set a password first.",
        );
    }
    if u.passkey_only {
        let lasting_left = u
            .keys
            .iter()
            .enumerate()
            .filter(|(i, k)| *i != pos && k.expires == 0)
            .count();
        if lasting_left < MIN_FOR_ONLY {
            return Err(
                "Passkey-only is on, and it needs at least two passkeys. Turn it off first.",
            );
        }
    }
    let gone = u.keys.remove(pos);
    Ok(gone.name)
}

/// Passkey-only needs this many lasting (non-loaner) passkeys.
pub const MIN_FOR_ONLY: usize = 2;

/// Rules for switching password sign-in off for one person.
pub fn can_set_passkey_only(keys: &[StoredKey], now: u64) -> Result<(), &'static str> {
    let lasting = keys
        .iter()
        .filter(|k| k.expires == 0 && !expired(k, now))
        .count();
    if lasting < MIN_FOR_ONLY {
        return Err("Add at least two passkeys first (loaners don't count), so losing one phone can't lock you out.");
    }
    Ok(())
}

fn expired(k: &StoredKey, now: u64) -> bool {
    k.expires != 0 && now >= k.expires
}

/// Drop loaner passkeys whose time is up. Anyone left with fewer than two
/// lasting passkeys loses the passkey-only switch, since the guard that
/// allowed it no longer holds. Returns (email, key name) for the audit lines.
pub fn prune_expired(file: &mut PasskeyFile, now: u64) -> Vec<(String, String)> {
    let mut gone = Vec::new();
    for u in &mut file.users {
        // A guest account that has ended loses every passkey.
        if account_expired(u, now) {
            let email = u.email.clone();
            gone.extend(u.keys.drain(..).map(|k| (email.clone(), k.name)));
            u.passkey_only = false;
            continue;
        }
        let mut i = 0;
        while i < u.keys.len() {
            if expired(&u.keys[i], now) {
                gone.push((u.email.clone(), u.keys.remove(i).name));
            } else {
                i += 1;
            }
        }
        if u.passkey_only && u.keys.iter().filter(|k| k.expires == 0).count() < MIN_FOR_ONLY {
            u.passkey_only = false;
        }
    }
    gone
}

/// "Lost phone": remove every passkey except `keep` (or all of them when
/// `keep` is None). Clears passkey-only, which can no longer hold. Returns
/// how many were removed, or an error when `keep` is not one of theirs.
pub fn revoke_others(
    file: &mut PasskeyFile,
    email: &str,
    keep: Option<&str>,
) -> Result<usize, &'static str> {
    let Some(u) = file.users.iter_mut().find(|u| u.email == email) else {
        return Ok(0);
    };
    if let Some(k) = keep {
        if !u.keys.iter().any(|x| x.id == k) {
            return Err("I can't find that passkey.");
        }
    }
    let before = u.keys.len();
    u.keys.retain(|x| Some(x.id.as_str()) == keep);
    u.passkey_only = false;
    Ok(before - u.keys.len())
}

/// The label shown for a person in their passkey manager (Proton Pass, phone,
/// Windows). Order: the label set for them in passkeys.json; else their name
/// from people.json (read only; that file has ONE `name` field, used as
/// "<name> Passkey", which gives "First Last Passkey" or "First Passkey"
/// depending on what was typed there); else the part of the email before the
/// @. Plain text, at most 64 characters, never containing "private key".
pub fn display_label(override_label: &str, person_name: &str, email: &str) -> String {
    let ok = |s: &str| !s.is_empty() && !has_private_key_words(s);
    let o = override_label.trim();
    if ok(o) {
        return o.chars().take(LABEL_MAX).collect();
    }
    let n = person_name.trim();
    if !n.is_empty() {
        let l = format!(
            "{} Passkey",
            n.chars().take(LABEL_MAX - 8).collect::<String>()
        );
        if ok(&l) && !l.chars().any(|c| c.is_control()) {
            return l;
        }
    }
    let local = email.split('@').next().unwrap_or("").trim();
    let local = if local.is_empty() { "My" } else { local };
    format!(
        "{} Passkey",
        local.chars().take(LABEL_MAX - 8).collect::<String>()
    )
}

fn has_private_key_words(s: &str) -> bool {
    s.to_lowercase().contains("private key")
}

/// A label somebody types: empty clears it; otherwise plain text up to 64
/// characters. Stored exactly as given (apostrophes and all).
pub fn clean_label(raw: &str) -> Result<String, &'static str> {
    let l = raw.trim();
    if l.chars().count() > LABEL_MAX {
        return Err("Use a shorter label (64 characters at most).");
    }
    if l.chars().any(|c| c.is_control()) {
        return Err("That label has characters I can't use.");
    }
    if has_private_key_words(l) {
        return Err("Please don't use those words in the label.");
    }
    Ok(l.to_string())
}

/// A free name for the next passkey: the label, or "label 2", "label 3"...
pub fn free_name(label: &str, keys: &[StoredKey]) -> String {
    let taken = |n: &str| keys.iter().any(|k| k.name.eq_ignore_ascii_case(n));
    if !taken(label) {
        return label.to_string();
    }
    for i in 2..100 {
        let tail = format!(" {i}");
        let base: String = label
            .chars()
            .take(NAME_MAX.saturating_sub(tail.chars().count()))
            .collect();
        let n = format!("{base}{tail}");
        if !taken(&n) {
            return n;
        }
    }
    label.to_string()
}

/// The browser and system, as a short family name, from a User-Agent string.
/// Deliberately coarse: no versions, no device models, and it never sees an IP
/// address. "Chrome on Android", "Safari on iOS", "Firefox on Windows"...
pub fn ua_family(ua: &str) -> String {
    let u = ua;
    let browser = if u.contains("Edg/") || u.contains("EdgA/") || u.contains("EdgiOS/") {
        "Edge"
    } else if u.contains("OPR/") || u.contains("Opera") {
        "Opera"
    } else if u.contains("Firefox/") || u.contains("FxiOS/") {
        "Firefox"
    } else if u.contains("SamsungBrowser") {
        "Samsung Internet"
    } else if u.contains("Chrome/") || u.contains("CriOS/") {
        "Chrome"
    } else if u.contains("Safari/") {
        "Safari"
    } else {
        "Unknown browser"
    };
    let os = if u.contains("Android") {
        "Android"
    } else if u.contains("iPhone") || u.contains("iPad") || u.contains("iPod") {
        "iOS"
    } else if u.contains("Windows") {
        "Windows"
    } else if u.contains("CrOS") {
        "ChromeOS"
    } else if u.contains("Mac OS X") || u.contains("Macintosh") {
        "macOS"
    } else if u.contains("Linux") || u.contains("X11") {
        "Linux"
    } else {
        ""
    };
    if u.trim().is_empty() {
        String::new()
    } else if os.is_empty() {
        browser.to_string()
    } else {
        format!("{browser} on {os}")
    }
}

fn device_of(headers: &HeaderMap) -> String {
    headers
        .get(header::USER_AGENT)
        .and_then(|v| v.to_str().ok())
        .map(ua_family)
        .unwrap_or_default()
}

/// "https://app.example.org" -> "app.example.org" (no port, no path).
fn host_of_origin(headers: &HeaderMap) -> String {
    headers
        .get(header::ORIGIN)
        .and_then(|v| v.to_str().ok())
        .and_then(|o| o.strip_prefix("https://"))
        .map(|h| h.trim_end_matches('/').chars().take(80).collect())
        .unwrap_or_default()
}

/// Unix seconds -> "2026-10-27 14:05 UTC".
pub fn when_text(unix: u64) -> String {
    if unix == 0 {
        return "never".to_string();
    }
    let (y, m, d) = civil_from_days((unix / 86400) as i64);
    let secs = unix % 86400;
    format!(
        "{y:04}-{m:02}-{d:02} {:02}:{:02} UTC",
        secs / 3600,
        (secs % 3600) / 60
    )
}

/// Unix seconds -> "2026-10-27".
pub fn date_text(unix: u64) -> String {
    if unix == 0 {
        return String::new();
    }
    let (y, m, d) = civil_from_days((unix / 86400) as i64);
    format!("{y:04}-{m:02}-{d:02}")
}

/// What the owners are told when anybody adds a passkey (names only).
pub fn owner_notice(person: &str, date: &str) -> (String, String) {
    (
        format!("{person} added a passkey on {date}"),
        format!(
            "{person} added a passkey on {date}.\n\n\
             You are getting this because you are an owner of the sign-in service. \
             You can see every passkey (by name, never the keys) on the owner page, \
             and revoke one there.\n"
        ),
    )
}

/// A name sent by a client: empty means "set it automatically".
pub fn optional_name(raw: &str) -> Result<Option<String>, &'static str> {
    if raw.trim().is_empty() {
        Ok(None)
    } else {
        clean_name(raw).map(Some)
    }
}

/// A name for a passkey: 1–32 visible characters.
///
/// Rules (shown to users as "one symbol, not at the front, no emoji"):
/// - 1–32 chars after whitespace trim
/// - No control characters
/// - No emoji (checked by Unicode code-point ranges)
/// - No invisible/bidirectional override characters that are used in
///   Unicode homograph and XSS payloads (zero-width, RTL overrides, BOM, etc.)
/// - At most one "symbol" character — anything that is not alphanumeric or
///   whitespace — and it may not be the first character
pub fn clean_name(raw: &str) -> Result<String, &'static str> {
    let name = raw.trim();
    if name.is_empty() {
        return Err("Give the passkey a name, like Pixel or Windows Hello.");
    }
    if name.chars().count() > 32 {
        return Err("Use a shorter name (32 characters at most).");
    }
    if name.chars().any(|c| c.is_control()) {
        return Err("That name has characters I can't use.");
    }

    // Reject emoji by code-point range (no external crate needed).
    let is_emoji = |c: char| matches!(c as u32,
        0x2600..=0x26FF   | // Miscellaneous Symbols
        0x2700..=0x27BF   | // Dingbats
        0xFE00..=0xFE0F   | // Variation Selectors (emoji modifiers)
        0x1F300..=0x1F5FF | // Misc Symbols & Pictographs
        0x1F600..=0x1F64F | // Emoticons
        0x1F680..=0x1F6FF | // Transport & Map
        0x1F900..=0x1F9FF | // Supplemental Symbols
        0x1FA00..=0x1FAFF   // Symbols & Pictographs Extended-A
    );
    if name.chars().any(is_emoji) {
        return Err("Emoji are not allowed in passkey names.");
    }

    // Reject invisible and bidirectional-override characters used in homograph
    // and injection attacks: zero-width chars, RTL/LTR controls, BOM, soft hyphen.
    let is_dangerous = |c: char| matches!(c as u32,
        0x00AD           | // soft hyphen (invisible in many renderers)
        0x200B..=0x200F  | // zero-width space, ZWNJ, ZWJ, LRM, RLM
        0x202A..=0x202E  | // LRE, RLE, PDF, LRO, RLO (bidi override)
        0x2060..=0x2064  | // word joiner, invisible separators
        0x2066..=0x2069  | // LRI, RLI, FSI, PDI
        0xFEFF             // BOM / zero-width no-break space
    );
    if name.chars().any(is_dangerous) {
        return Err("That name has characters I can't use.");
    }

    // At most one non-alphanumeric, non-space character (the "one symbol" rule).
    // It may not be the first character (prevents e.g. script-prefix tricks).
    let is_symbol = |c: char| !c.is_alphanumeric() && !c.is_whitespace();
    let symbols: Vec<usize> = name.char_indices()
        .filter(|(_, c)| is_symbol(*c))
        .map(|(i, _)| i)
        .collect();
    if symbols.len() > 1 {
        return Err("Only one symbol is allowed.");
    }
    if symbols.len() == 1 && symbols[0] == 0 {
        return Err("The symbol can't be the first character.");
    }

    Ok(name.to_string())
}

fn random_hex(n: usize) -> String {
    let mut b = vec![0u8; n];
    rand::rand_core::UnwrapErr(rand::rngs::SysRng).fill_bytes(&mut b);
    crate::hex(&b)
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    // A poisoned lock still holds usable data; never panic a request over it.
    m.lock().unwrap_or_else(|e| e.into_inner())
}

// -----------------------------------------------------------------------------
//  Challenges: five minutes, one use
// -----------------------------------------------------------------------------

pub struct Challenges<T> {
    map: HashMap<String, (Instant, T)>,
}

impl<T> Challenges<T> {
    pub fn new() -> Self {
        Challenges {
            map: HashMap::new(),
        }
    }

    /// Store a challenge and hand back its random token. None when too many
    /// are waiting.
    pub fn put(&mut self, value: T, now: Instant) -> Option<String> {
        self.map
            .retain(|_, (at, _)| now.duration_since(*at) < CHALLENGE_LIFE);
        if self.map.len() >= MAX_PENDING {
            return None;
        }
        let token = random_hex(32);
        self.map.insert(token.clone(), (now, value));
        Some(token)
    }

    /// Remove and return it, if it exists and is still fresh. Removed either
    /// way: a challenge never gets a second look.
    pub fn take(&mut self, token: &str, now: Instant) -> Option<T> {
        let (at, v) = self.map.remove(token)?;
        if now.duration_since(at) < CHALLENGE_LIFE {
            Some(v)
        } else {
            None
        }
    }
}

// -----------------------------------------------------------------------------
//  Recovery links: "add a passkey on a new device, by email"
// -----------------------------------------------------------------------------

/// A recovery link is good for fifteen minutes.
pub const RECOVERY_LIFE: u64 = 900;

#[derive(Serialize, Deserialize, Default)]
pub struct RecoveryFile {
    #[serde(default)]
    pub items: Vec<RecoveryItem>,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct RecoveryItem {
    pub email: String,
    /// SHA-256 of the token. The token itself exists only in the email, so a
    /// copy of this file cannot be turned into a working link.
    pub hash: String,
    pub expires: u64,
}

pub fn token_hash(token: &str) -> String {
    crate::hex(&Sha256::digest(token.as_bytes()))
}

impl RecoveryFile {
    /// Make a token for `email`, unless an unexpired one already exists: then
    /// None. Asking again must not replace (and so cancel) a link already on
    /// its way, or a stranger typing someone's address could keep killing the
    /// real link.
    pub fn issue_if_free(&mut self, email: &str, now: u64) -> Option<String> {
        self.items.retain(|i| now < i.expires);
        if self.items.iter().any(|i| i.email == email) {
            return None;
        }
        Some(self.issue(email, now))
    }

    /// Make a token for `email`, dropping any older one for the same account.
    /// Production code goes through `issue_if_free`.
    pub fn issue(&mut self, email: &str, now: u64) -> String {
        self.items.retain(|i| now < i.expires && i.email != email);
        let token = random_hex(32);
        self.items.push(RecoveryItem {
            email: email.to_string(),
            hash: token_hash(&token),
            expires: now + RECOVERY_LIFE,
        });
        token
    }

    /// Whose link this is, without using it up.
    pub fn peek(&self, token: &str, now: u64) -> Option<String> {
        let h = token_hash(token);
        self.items
            .iter()
            .find(|i| i.hash == h && now < i.expires)
            .map(|i| i.email.clone())
    }

    /// Use a token. It is removed whether or not it was still fresh.
    pub fn redeem(&mut self, token: &str, now: u64) -> Option<String> {
        let h = token_hash(token);
        let pos = self.items.iter().position(|i| i.hash == h)?;
        let item = self.items.remove(pos);
        self.items.retain(|i| now < i.expires);
        if now < item.expires {
            Some(item.email)
        } else {
            None
        }
    }
}

// -----------------------------------------------------------------------------
//  Rate limits
// -----------------------------------------------------------------------------

/// "No more than `max` events per `window`" per key. Flat and self-clearing,
/// like the password lockout, and in memory for the same reason (a restart is
/// the escape hatch).
#[derive(Default)]
pub struct Limiter {
    events: HashMap<String, Vec<Instant>>,
}

impl Limiter {
    /// Seconds to wait if the key is over its limit.
    pub fn blocked(
        &mut self,
        key: &str,
        max: usize,
        window: Duration,
        now: Instant,
    ) -> Option<u64> {
        if self.events.len() > 2000 {
            self.events.retain(|_, v| !v.is_empty());
        }
        let v = self.events.entry(key.to_string()).or_default();
        v.retain(|t| now.duration_since(*t) < window);
        if v.len() >= max {
            let waited = now.duration_since(v[0]);
            return Some(window.saturating_sub(waited).as_secs() + 1);
        }
        None
    }

    pub fn record(&mut self, key: &str, now: Instant) {
        self.events.entry(key.to_string()).or_default().push(now);
    }
}

// -----------------------------------------------------------------------------
//  Settings and shared state
// -----------------------------------------------------------------------------

enum Pending {
    Register {
        email: String,
        name: String,
        user_id: Uuid,
        state: PasskeyRegistration,
        /// Unix seconds when this passkey stops working; 0 = never (loaner).
        expires: u64,
        /// Started from an emailed link: finishing needs no session, because
        /// the single-use token IS the proof. Nothing else is opened by it.
        via_recovery: bool,
    },
    Login(DiscoverableAuthentication),
    /// An owner action waiting for the owner's own passkey to confirm it.
    Owner {
        owner: String,
        action: OwnerAction,
        state: PasskeyAuthentication,
    },
}

pub struct Passkeys {
    webauthn: Webauthn,
    /// Exact origins the browser may report. Anything else is refused.
    origins: Vec<String>,
    /// Also accept https origins on subdomains of `rp_id`.
    subdomains: bool,
    rp_id: String,
    file: PathBuf,
    /// Hashed one-time recovery links (passkey-recovery.json beside the other).
    recovery_file: PathBuf,
    /// Start of the emailed link, e.g. https://example.org/auth
    link_base: String,
    /// Addresses allowed to use the owner tools (AUTH_OWNER_EMAILS). Empty
    /// means those tools are off.
    owners: Vec<String>,
    /// Held across a whole read-modify-write of the file.
    file_lock: Mutex<()>,
    /// Same, for the recovery file. Taken BEFORE file_lock, never after.
    recovery_lock: Mutex<()>,
    pending: Mutex<Challenges<Pending>>,
    limits: Mutex<Limiter>,
}

impl Passkeys {
    /// From the environment. None when switched off or when the settings are
    /// unusable; either way the password sign-in carries on untouched.
    pub fn from_env(data_dir: &Path) -> Option<Passkeys> {
        if !common::env_bool("AUTH_PASSKEYS", true) {
            tracing::info!("passkeys are switched off (AUTH_PASSKEYS=off)");
            return None;
        }
        let rp_id = common::env_or("AUTH_PASSKEY_RP_ID", "example.org");
        let origin = common::env_or("AUTH_PASSKEY_ORIGIN", "https://example.org");
        let extra: Vec<String> = common::env_or("AUTH_PASSKEY_EXTRA_ORIGINS", "")
            .split(',')
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect();
        let file = match std::env::var("AUTH_PASSKEY_FILE") {
            Ok(v) if !v.trim().is_empty() => PathBuf::from(v),
            _ => data_dir.join("passkeys.json"),
        };
        let subdomains = common::env_bool("AUTH_PASSKEY_SUBDOMAINS", false);
        match Passkeys::new(&rp_id, &origin, &extra, subdomains, file) {
            Ok(mut p) => {
                p.owners = common::env_or("AUTH_OWNER_EMAILS", "")
                    .split(',')
                    .map(common::people::normalise)
                    .filter(|s| !s.is_empty())
                    .collect();
                tracing::info!(rp_id = %rp_id, origin = %origin, owners = p.owners.len(), "passkeys ready");
                Some(p)
            }
            Err(e) => {
                tracing::error!("passkeys are OFF, bad settings: {e}");
                None
            }
        }
    }

    pub fn new(
        rp_id: &str,
        origin: &str,
        extra: &[String],
        subdomains: bool,
        file: PathBuf,
    ) -> Result<Passkeys, String> {
        let main = Url::parse(origin).map_err(|e| format!("AUTH_PASSKEY_ORIGIN: {e}"))?;
        let mut b = WebauthnBuilder::new(rp_id, &main)
            .map_err(|e| format!("rp id and origin do not fit together: {e}"))?
            .rp_name("Household sign-in")
            .allow_subdomains(subdomains);
        let mut origins = vec![origin.trim_end_matches('/').to_string()];
        for x in extra {
            let u = Url::parse(x).map_err(|e| format!("AUTH_PASSKEY_EXTRA_ORIGINS: {e}"))?;
            b = b.append_allowed_origin(&u);
            origins.push(x.trim_end_matches('/').to_string());
        }
        let webauthn = b
            .build()
            .map_err(|e| format!("could not set up WebAuthn: {e}"))?;
        Ok(Passkeys {
            webauthn,
            origins,
            subdomains,
            rp_id: rp_id.to_string(),
            owners: Vec::new(),
            recovery_file: file.with_file_name("passkey-recovery.json"),
            link_base: common::env_or(
                "AUTH_PASSKEY_LINK_BASE",
                &format!("{}/auth", origin.trim_end_matches('/')),
            ),
            file,
            file_lock: Mutex::new(()),
            recovery_lock: Mutex::new(()),
            pending: Mutex::new(Challenges::new()),
            limits: Mutex::new(Limiter::default()),
        })
    }
}

// -----------------------------------------------------------------------------
//  Helpers for the handlers
// -----------------------------------------------------------------------------

fn jerr(code: StatusCode, msg: &str) -> Response {
    (code, Json(json!({ "error": msg }))).into_response()
}

fn wait_msg(secs: u64) -> String {
    format!(
        "Too many tries. Wait {secs} second{} and try again.",
        if secs == 1 { "" } else { "s" }
    )
}

fn off() -> Response {
    jerr(
        StatusCode::SERVICE_UNAVAILABLE,
        "Passkeys are switched off on this site. Use your password.",
    )
}

/// Browsers always send Origin on a fetch POST. It must be one of ours: this is
/// the guard against another site making a signed-in browser do these things.
fn origin_ok(pk: &Passkeys, headers: &HeaderMap) -> bool {
    headers
        .get(header::ORIGIN)
        .and_then(|v| v.to_str().ok())
        .map(|o| pk.origin_allowed(o))
        .unwrap_or(false)
}

impl Passkeys {
    /// Exact match on the list, or (when switched on) https on a real
    /// subdomain of the rp id. The dot is part of the test so that
    /// "evil-example.org" and "example.org.evil.com" fail.
    pub fn origin_allowed(&self, origin: &str) -> bool {
        if self.origins.iter().any(|x| x == origin) {
            return true;
        }
        if !self.subdomains {
            return false;
        }
        let Some(host) = origin.strip_prefix("https://") else {
            return false;
        };
        let host = host.strip_suffix('/').unwrap_or(host);
        // no port, path, userinfo
        if host.is_empty() || host.contains(['/', ':', '@', '?', '#']) {
            return false;
        }
        host.strip_suffix(self.rp_id.as_str())
            .map(|front| front.len() > 1 && front.ends_with('.'))
            .unwrap_or(false)
    }
}

fn bad_origin() -> Response {
    jerr(
        StatusCode::FORBIDDEN,
        "That request did not come from this site's own page. Reload the page and try again.",
    )
}

/// The password lockout is shared with the password form, so guessing the
/// password through "Add a passkey" costs the same as guessing it at sign-in.
fn gate_wait(state: &AppState, email: &str) -> Option<u64> {
    let gates = lock(&state.gates);
    let until = gates.get(email)?.locked_until?;
    let now = Instant::now();
    if now < until {
        Some((until - now).as_secs() + 1)
    } else {
        None
    }
}

fn gate_fail(state: &AppState, email: &str) {
    let mut gates = lock(&state.gates);
    let g = gates.entry(email.to_string()).or_default();
    g.fails += 1;
    if let Some(d) = crate::lock_for(g.fails) {
        g.locked_until = Some(Instant::now() + d);
    }
}

fn gate_clear(state: &AppState, email: &str) {
    lock(&state.gates).remove(email);
}

fn ago(then: u64) -> String {
    if then == 0 {
        return "never".to_string();
    }
    let days = common::people::now().saturating_sub(then) / 86400;
    match days {
        0 => "today".to_string(),
        1 => "yesterday".to_string(),
        n => format!("{n} days ago"),
    }
}

// -----------------------------------------------------------------------------
//  Account page, script
// -----------------------------------------------------------------------------

pub struct KeyRow {
    pub id: String,
    pub name: String,
    pub added: String,
    pub last: String,
    /// "" for a lasting passkey, else e.g. "Loaner, ends in 3 days".
    pub loaner: String,
}

#[derive(Template)]
#[template(path = "account.html")]
struct AccountTemplate {
    email: String,
    enabled: bool,
    problem: bool,
    keys: Vec<KeyRow>,
    can_remove: bool,
    passkey_only: bool,
    can_enable_only: bool,
    /// The name the next passkey will be saved under (set automatically; there
    /// is no box to rename it).
    next_name: String,
    guest_note: String,
    is_owner: bool,
}

fn loaner_note(k: &StoredKey, now: u64) -> String {
    if k.expires == 0 {
        return String::new();
    }
    if now >= k.expires {
        return "Loaner, expired".to_string();
    }
    let days = (k.expires - now) / 86400;
    match days {
        0 => "Loaner, ends today".to_string(),
        1 => "Loaner, ends in 1 day".to_string(),
        n => format!("Loaner, ends in {n} days"),
    }
}

const CSS: &str = include_str!("../templates/auth.css");
/// The clip inside the round passkey button on the sign-in page: 360x360,
/// silent, a 10 second loop. The ".v1" in the name is the version: when the clip
/// is swapped, bump it (and the name in templates/signin.html) so browsers that
/// cached the old one for a year fetch the new one. See PASSKEYS.md.
const VIDEO_ORB: &[u8] = include_bytes!("../static/passkey-orb.v1.mp4");

/// "bytes=a-b", "bytes=a-" or "bytes=-n" for a body of `total` bytes, as an
/// inclusive (start, end). None when it is not a single satisfiable range.
pub fn parse_range(h: &str, total: usize) -> Option<(usize, usize)> {
    let spec = h.trim().strip_prefix("bytes=")?;
    if spec.contains(',') || total == 0 {
        return None;
    }
    let (a, b) = spec.split_once('-')?;
    let (start, end) = if a.is_empty() {
        let n: usize = b.parse().ok()?;
        if n == 0 {
            return None;
        }
        (total.saturating_sub(n), total - 1)
    } else {
        let s: usize = a.parse().ok()?;
        let e: usize = if b.is_empty() {
            total - 1
        } else {
            b.parse().ok()?
        };
        (s, e.min(total - 1))
    };
    if start > end || start >= total {
        return None;
    }
    Some((start, end))
}

/// A year, immutable: the file name carries the version, so a changed clip gets
/// a new name instead of waiting for caches to expire.
const IMMUTABLE: &str = "public, max-age=31536000, immutable";

fn video_response(headers: &HeaderMap, data: &'static [u8]) -> Response {
    let total = data.len();
    let base = |code: StatusCode| {
        Response::builder()
            .status(code)
            .header(header::CONTENT_TYPE, "video/mp4")
            .header(header::ACCEPT_RANGES, "bytes")
            .header(header::CACHE_CONTROL, IMMUTABLE)
    };
    let built = match headers.get(header::RANGE).and_then(|v| v.to_str().ok()) {
        None => base(StatusCode::OK)
            .header(header::CONTENT_LENGTH, total)
            .body(axum::body::Body::from(data)),
        Some(h) => match parse_range(h, total) {
            Some((s, e)) => base(StatusCode::PARTIAL_CONTENT)
                .header(header::CONTENT_RANGE, format!("bytes {s}-{e}/{total}"))
                .header(header::CONTENT_LENGTH, e - s + 1)
                .body(axum::body::Body::from(&data[s..=e])),
            None => base(StatusCode::RANGE_NOT_SATISFIABLE)
                .header(header::CONTENT_RANGE, format!("bytes */{total}"))
                .body(axum::body::Body::empty()),
        },
    };
    built.unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}

/// /static/auth.css and /static/signin-bg.mp4, served from inside the binary.
pub async fn static_file(
    axum::extract::Path(file): axum::extract::Path<String>,
    headers: HeaderMap,
) -> Response {
    match file.as_str() {
        "auth.css" => (
            [
                (header::CONTENT_TYPE, "text/css; charset=utf-8"),
                (header::CACHE_CONTROL, "public, max-age=3600"),
            ],
            CSS,
        )
            .into_response(),
        "passkey-orb.v1.mp4" => video_response(&headers, VIDEO_ORB),
        _ => StatusCode::NOT_FOUND.into_response(),
    }
}

pub async fn script() -> Response {
    (
        [
            (
                header::CONTENT_TYPE,
                "application/javascript; charset=utf-8",
            ),
            (header::CACHE_CONTROL, "no-cache"),
        ],
        SCRIPT,
    )
        .into_response()
}

pub async fn account_page(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    let Some(email) = crate::cookie_email(&state, &headers) else {
        return crate::render_signin(
            "Sign in first.",
            "",
            "/auth/account",
            StatusCode::UNAUTHORIZED,
        );
    };
    let has_password = common::people::find(&crate::people(&state), &email)
        .map(|p| p.has_password())
        .unwrap_or(false);
    let mut problem = false;
    let mut keys = Vec::new();
    let mut can_remove_any = true;
    let mut passkey_only = false;
    let mut can_enable_only = false;
    let now = common::people::now();
    let person_name = common::people::find(&crate::people(&state), &email)
        .map(|p| p.name.clone())
        .unwrap_or_default();
    let mut next_name = display_label("", &person_name, &email);
    let mut guest_note = String::new();
    if let Some(pk) = state.passkeys.as_ref() {
        let _g = lock(&pk.file_lock);
        match load(&pk.file) {
            Ok(f) => {
                if let Some(u) = f.user(&email) {
                    can_remove_any = can_remove(has_password, u.keys.len());
                    passkey_only = u.passkey_only;
                    can_enable_only = can_set_passkey_only(&u.keys, now).is_ok();
                    next_name = free_name(&display_label(&u.label, &person_name, &email), &u.keys);
                    if u.guest {
                        guest_note = format!(
                            "This is a guest account until {}. It can hold one temporary passkey.",
                            last_day_text(u.account_expires)
                        );
                    }
                    keys = u
                        .keys
                        .iter()
                        .map(|k| KeyRow {
                            id: k.id.clone(),
                            name: k.name.clone(),
                            added: ago(k.added),
                            last: ago(k.last_used),
                            loaner: loaner_note(k, now),
                        })
                        .collect();
                }
            }
            Err(_) => problem = true,
        }
    }
    let is_owner = state
        .passkeys
        .as_ref()
        .map(|p| p.owners.contains(&email))
        .unwrap_or(false);
    let html = AccountTemplate {
        email,
        enabled: state.passkeys.is_some(),
        problem,
        keys,
        can_remove: can_remove_any,
        passkey_only,
        can_enable_only,
        next_name,
        guest_note,
        is_owner,
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    Html(html).into_response()
}

// -----------------------------------------------------------------------------
//  Enrol: add a passkey (needs the session AND the password again)
// -----------------------------------------------------------------------------

#[derive(Deserialize)]
struct StartReg {
    /// Not sent by the pages (the name is set automatically); kept so other
    /// clients can still pass one.
    #[serde(default)]
    name: String,
    password: String,
    /// 0 = a lasting passkey; 1, 7 or 30 = a loaner that expires.
    #[serde(default)]
    days: u64,
}

/// Asking for the password again is deliberate. A session lasts six months, so
/// "has a cookie" is not proof of "is the owner right now", and a passkey added
/// from a borrowed or stolen session would outlive a password change.
pub async fn register_start(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let Some(pk) = state.passkeys.as_ref() else {
        return off();
    };
    if !origin_ok(pk, &headers) {
        return bad_origin();
    }
    let Some(email) = crate::cookie_email(&state, &headers) else {
        return jerr(StatusCode::UNAUTHORIZED, "Sign in first.");
    };
    let Ok(b) = serde_json::from_str::<StartReg>(&body) else {
        return jerr(
            StatusCode::BAD_REQUEST,
            "Fill in the name and your password.",
        );
    };
    let name = match optional_name(&b.name) {
        Ok(n) => n,
        Err(m) => return jerr(StatusCode::BAD_REQUEST, m),
    };
    let now = Instant::now();
    {
        let mut l = lock(&pk.limits);
        let key = format!("enrol:{email}");
        if let Some(w) = l.blocked(&key, 10, CHALLENGE_LIFE, now) {
            return jerr(StatusCode::TOO_MANY_REQUESTS, &wait_msg(w));
        }
        l.record(&key, now);
    }
    if let Some(w) = gate_wait(&state, &email) {
        return jerr(
            StatusCode::TOO_MANY_REQUESTS,
            &format!("Too many wrong passwords. Try again in {w} seconds."),
        );
    }
    let list = crate::people(&state);
    let Some(person) = common::people::find(&list, &email) else {
        return jerr(StatusCode::UNAUTHORIZED, "Sign in again.");
    };
    if !common::people::verify(&person.hash, &b.password) {
        gate_fail(&state, &email);
        tracing::warn!(email = %email, "wrong password while adding a passkey");
        return jerr(StatusCode::UNAUTHORIZED, "That password isn't right.");
    }
    gate_clear(&state, &email);

    match begin_registration(pk, &email, &person.name, name, b.days, false, now) {
        Ok((token, options)) => Json(json!({ "token": token, "options": options })).into_response(),
        Err(r) => r,
    }
}

/// Days a loaner passkey lasts: none (a lasting one), 1, 7 or 30.
fn loaner_expiry(days: u64, now_unix: u64) -> Result<u64, &'static str> {
    match days {
        0 => Ok(0),
        1 | 7 | 30 => Ok(now_unix + days * 86400),
        _ => Err("Pick 1, 7 or 30 days for a loaner passkey."),
    }
}

/// The part shared by "add a passkey" and "add one from the emailed link":
/// checks, then a stored single-use challenge. Err is a ready plain-words reply
/// (a whole Response, hence the allow: boxing it would only add noise here).
#[allow(clippy::result_large_err)]
fn begin_registration(
    pk: &Passkeys,
    email: &str,
    person_name: &str,
    name: Option<String>,
    days: u64,
    via_recovery: bool,
    now: Instant,
) -> Result<(String, serde_json::Value), Response> {
    let expires =
        loaner_expiry(days, common::people::now()).map_err(|m| jerr(StatusCode::BAD_REQUEST, m))?;
    let (user_id, exclude, display, name) = {
        let _g = lock(&pk.file_lock);
        let file = match load(&pk.file) {
            Ok(f) => f,
            Err(_) => {
                tracing::error!("passkeys.json does not parse; refusing to add to it");
                return Err(jerr(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "The passkey list looks damaged, so I won't change it. Tell Jason.",
                ));
            }
        };
        match file.user(email) {
            Some(u) => {
                if let Err(m) = enrol_allowed(Some(u), days, common::people::now()) {
                    return Err(jerr(StatusCode::BAD_REQUEST, &m));
                }
                let display = display_label(&u.label, person_name, email);
                // The page never asks for a name: it is the person's label,
                // made unique ("... 2"). A name sent by some other client is
                // still honoured, and still has to be new.
                let chosen = match name {
                    Some(n) => {
                        if u.keys.iter().any(|k| k.name.eq_ignore_ascii_case(&n)) {
                            return Err(jerr(
                                StatusCode::BAD_REQUEST,
                                "You already have a passkey with that name.",
                            ));
                        }
                        n
                    }
                    None => free_name(&display, &u.keys),
                };
                (
                    Uuid::parse_str(&u.user_id).unwrap_or_else(|_| new_user_id()),
                    u.keys
                        .iter()
                        .map(|k| k.passkey.cred_id().clone())
                        .collect::<Vec<_>>(),
                    display,
                    chosen,
                )
            }
            None => {
                let display = display_label("", person_name, email);
                let chosen = name.unwrap_or_else(|| display.clone());
                (new_user_id(), Vec::new(), display, chosen)
            }
        }
    };
    let (ccr, state_reg) = pk
        .webauthn
        .start_passkey_registration(
            user_id,
            email,
            &display,
            if exclude.is_empty() {
                None
            } else {
                Some(exclude)
            },
        )
        .map_err(|_| {
            jerr(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Could not start that. Try again.",
            )
        })?;
    // Ask for a discoverable key so sign-in can start without an email.
    // Done on the JSON so it needs nothing from the library's own types. The
    // attachment stays unrestricted and attestation stays "none", so password
    // managers (Proton Pass) and synced passkeys are welcome.
    let mut options = serde_json::to_value(&ccr).unwrap_or(serde_json::Value::Null);
    if options["publicKey"]["authenticatorSelection"].is_null() {
        options["publicKey"]["authenticatorSelection"] = json!({});
    }
    options["publicKey"]["authenticatorSelection"]["residentKey"] = json!("required");
    options["publicKey"]["authenticatorSelection"]["requireResidentKey"] = json!(true);
    let token = lock(&pk.pending).put(
        Pending::Register {
            email: email.to_string(),
            name,
            user_id,
            state: state_reg,
            expires,
            via_recovery,
        },
        now,
    );
    match token {
        Some(t) => Ok((t, options)),
        None => Err(jerr(
            StatusCode::SERVICE_UNAVAILABLE,
            "Busy right now. Try again in a minute.",
        )),
    }
}

fn new_user_id() -> Uuid {
    let mut b = [0u8; 16];
    rand::rand_core::UnwrapErr(rand::rngs::SysRng).fill_bytes(&mut b);
    Uuid::from_bytes(b)
}

#[derive(Deserialize)]
struct FinishReg {
    token: String,
    credential: serde_json::Value,
}

pub async fn register_finish(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let Some(pk) = state.passkeys.as_ref() else {
        return off();
    };
    if !origin_ok(pk, &headers) {
        return bad_origin();
    }
    let session_email = crate::cookie_email(&state, &headers);
    let Ok(b) = serde_json::from_str::<FinishReg>(&body) else {
        return jerr(
            StatusCode::BAD_REQUEST,
            "That did not look right. Try again.",
        );
    };
    // Taken (and gone) before anything else can fail: one use only.
    let taken = lock(&pk.pending).take(&b.token, Instant::now());
    let Some(Pending::Register {
        email: for_email,
        name,
        user_id,
        state: reg_state,
        expires,
        via_recovery,
    }) = taken
    else {
        return jerr(
            StatusCode::BAD_REQUEST,
            "That took too long or was already used. Start again.",
        );
    };
    // A normal enrolment must come from the same signed-in person who started
    // it. One that began at an emailed link has no session and needs none: the
    // single-use token was the proof, and this is the only thing it opens.
    let email = for_email;
    if !via_recovery && session_email.as_deref() != Some(email.as_str()) {
        return jerr(
            StatusCode::FORBIDDEN,
            "That belongs to a different sign-in. Start again.",
        );
    }
    let Ok(cred) = serde_json::from_value::<RegisterPublicKeyCredential>(b.credential) else {
        return jerr(
            StatusCode::BAD_REQUEST,
            "The device's answer was not readable. Try again.",
        );
    };
    let passkey = match pk.webauthn.finish_passkey_registration(&cred, &reg_state) {
        Ok(p) => p,
        Err(_) => {
            tracing::warn!(email = %email, "a passkey was not accepted on adding");
            return jerr(
                StatusCode::BAD_REQUEST,
                "That passkey was not accepted. Try again.",
            );
        }
    };

    let _g = lock(&pk.file_lock);
    let mut file = match load(&pk.file) {
        Ok(f) => f,
        Err(_) => {
            return jerr(
                StatusCode::INTERNAL_SERVER_ERROR,
                "The passkey list looks damaged, so I won't change it. Tell Jason.",
            );
        }
    };
    if file.cred_in_use(passkey.cred_id()) {
        return jerr(
            StatusCode::BAD_REQUEST,
            "That passkey is already registered.",
        );
    }
    if file.user(&email).is_none() {
        file.users.push(UserKeys {
            email: email.clone(),
            user_id: user_id.to_string(),
            keys: Vec::new(),
            ..Default::default()
        });
    }
    let Some(u) = file.users.iter_mut().find(|u| u.email == email) else {
        return jerr(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Could not save that. Try again.",
        );
    };
    // The rules again at the moment of saving: the owner may have changed the
    // cap, or ended a guest account, since this was started.
    if let Err(m) = enrol_allowed(Some(&*u), u64::from(expires != 0), common::people::now()) {
        return jerr(StatusCode::BAD_REQUEST, &m);
    }
    // A hand-written entry (just a label, say) has no user handle yet.
    if Uuid::parse_str(&u.user_id).is_err() {
        u.user_id = user_id.to_string();
    }
    u.keys.push(StoredKey {
        id: random_hex(6),
        name: name.clone(),
        added: common::people::now(),
        last_used: 0,
        expires,
        added_device: device_of(&headers),
        last_device: String::new(),
        last_host: String::new(),
        passkey,
    });
    if let Err(e) = save(&pk.file, &file) {
        tracing::error!("could not write the passkey file: {e}");
        return jerr(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Could not save that. Try again.",
        );
    }
    tracing::info!(email = %email, key = %name, loaner = expires != 0, via_link = via_recovery,
                   "passkey added");
    notify(
        &email,
        "A passkey was added to your account",
        format!(
            "A passkey named \"{name}\" was just added to your sign-in account.\n\n\
             If that was you, nothing more to do.\n\
             If it was not, sign in and remove it on the passkeys page, and change \
             your password. Whoever added it can sign in as you until it is removed.\n"
        ),
    );
    // And the owners hear about it whoever added it: "<Name> added a passkey on <date>".
    let who = common::people::find(&crate::people(&state), &email)
        .map(|p| {
            if p.name.trim().is_empty() {
                email.clone()
            } else {
                p.name.clone()
            }
        })
        .unwrap_or_else(|| email.clone());
    for o in owner_recipients(pk) {
        let (subject, text) = owner_notice(&who, &date_text(common::people::now()));
        notify(&o, &subject, text);
    }
    Json(json!({ "ok": true })).into_response()
}

// -----------------------------------------------------------------------------
//  Remove
// -----------------------------------------------------------------------------

#[derive(Deserialize)]
struct Remove {
    id: String,
}

pub async fn remove(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let Some(pk) = state.passkeys.as_ref() else {
        return off();
    };
    if !origin_ok(pk, &headers) {
        return bad_origin();
    }
    let Some(email) = crate::cookie_email(&state, &headers) else {
        return jerr(StatusCode::UNAUTHORIZED, "Sign in first.");
    };
    let Ok(b) = serde_json::from_str::<Remove>(&body) else {
        return jerr(
            StatusCode::BAD_REQUEST,
            "That did not look right. Try again.",
        );
    };
    let has_password = common::people::find(&crate::people(&state), &email)
        .map(|p| p.has_password())
        .unwrap_or(false);
    let _g = lock(&pk.file_lock);
    let mut file = match load(&pk.file) {
        Ok(f) => f,
        Err(_) => {
            return jerr(
                StatusCode::INTERNAL_SERVER_ERROR,
                "The passkey list looks damaged, so I won't change it. Tell Jason.",
            );
        }
    };
    let name = match remove_key(&mut file, &email, &b.id, has_password) {
        Ok(n) => n,
        Err(m) => return jerr(StatusCode::BAD_REQUEST, m),
    };
    if let Err(e) = save(&pk.file, &file) {
        tracing::error!("could not write the passkey file: {e}");
        return jerr(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Could not save that. Try again.",
        );
    }
    tracing::info!(email = %email, key = %name, "passkey removed");
    Json(json!({ "ok": true })).into_response()
}

// -----------------------------------------------------------------------------
//  Passkey-only switch, and "lost phone"
// -----------------------------------------------------------------------------

/// True when password sign-in is switched off for this person: they chose
/// passkey-only, or their guest account has ended. Fails open on anything odd
/// (damaged file, fewer than two lasting passkeys left): the worst a mistake
/// here may do is leave the password working.
pub fn password_off(state: &AppState, email: &str) -> bool {
    let Some(pk) = state.passkeys.as_ref() else {
        return false;
    };
    let _g = lock(&pk.file_lock);
    let Ok(f) = load(&pk.file) else {
        return false;
    };
    let now = common::people::now();
    f.user(email)
        .map(|u| {
            account_expired(u, now)
                || (u.passkey_only && can_set_passkey_only(&u.keys, now).is_ok())
        })
        .unwrap_or(false)
}

/// Has this person's guest account ended? Read fresh from the file, so it is
/// checked on every request that carries a session (nothing is cached). Any
/// problem reading the file counts as "no".
pub fn account_ended(state: &AppState, email: &str) -> bool {
    let Some(pk) = state.passkeys.as_ref() else {
        return false;
    };
    match load(&pk.file) {
        Ok(f) => f
            .user(email)
            .map(|u| account_expired(u, common::people::now()))
            .unwrap_or(false),
        Err(_) => false,
    }
}

#[derive(Deserialize)]
struct OnlyReq {
    on: bool,
}

pub async fn set_only(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let Some(pk) = state.passkeys.as_ref() else {
        return off();
    };
    if !origin_ok(pk, &headers) {
        return bad_origin();
    }
    let Some(email) = crate::cookie_email(&state, &headers) else {
        return jerr(StatusCode::UNAUTHORIZED, "Sign in first.");
    };
    let Ok(b) = serde_json::from_str::<OnlyReq>(&body) else {
        return jerr(
            StatusCode::BAD_REQUEST,
            "That did not look right. Try again.",
        );
    };
    let _g = lock(&pk.file_lock);
    let Ok(mut file) = load(&pk.file) else {
        return jerr(
            StatusCode::INTERNAL_SERVER_ERROR,
            "The passkey list looks damaged, so I won't change it. Tell Jason.",
        );
    };
    let Some(u) = file.users.iter_mut().find(|u| u.email == email) else {
        return jerr(StatusCode::BAD_REQUEST, "Add passkeys first.");
    };
    if b.on {
        if let Err(m) = can_set_passkey_only(&u.keys, common::people::now()) {
            return jerr(StatusCode::BAD_REQUEST, m);
        }
    }
    u.passkey_only = b.on;
    if let Err(e) = save(&pk.file, &file) {
        tracing::error!("could not write the passkey file: {e}");
        return jerr(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Could not save that. Try again.",
        );
    }
    tracing::info!(email = %email, on = b.on, "passkey-only changed");
    Json(json!({ "ok": true })).into_response()
}

#[derive(Deserialize)]
struct LostReq {
    /// The passkey being used right now, to keep. Absent means remove them all.
    #[serde(default)]
    keep: Option<String>,
}

/// "I lost a device": remove every passkey except the one in hand (or all of
/// them, and then use the emailed link). Also signs every session out, because
/// a lost phone may be holding one.
pub async fn lost(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let Some(pk) = state.passkeys.as_ref() else {
        return off();
    };
    if !origin_ok(pk, &headers) {
        return bad_origin();
    }
    let Some(email) = crate::cookie_email(&state, &headers) else {
        return jerr(StatusCode::UNAUTHORIZED, "Sign in first.");
    };
    let Ok(b) = serde_json::from_str::<LostReq>(&body) else {
        return jerr(
            StatusCode::BAD_REQUEST,
            "That did not look right. Try again.",
        );
    };
    let removed = {
        let _g = lock(&pk.file_lock);
        let Ok(mut file) = load(&pk.file) else {
            return jerr(
                StatusCode::INTERNAL_SERVER_ERROR,
                "The passkey list looks damaged, so I won't change it. Tell Jason.",
            );
        };
        let n = match revoke_others(&mut file, &email, b.keep.as_deref()) {
            Ok(n) => n,
            Err(m) => return jerr(StatusCode::BAD_REQUEST, m),
        };
        if let Err(e) = save(&pk.file, &file) {
            tracing::error!("could not write the passkey file: {e}");
            return jerr(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Could not save that. Try again.",
            );
        }
        n
    };
    crate::revoke_all(&state, &email);
    tracing::info!(email = %email, removed, kept = b.keep.is_some(), "lost device: passkeys revoked, sessions ended");
    notify(
        &email,
        "Passkeys were removed from your account",
        format!(
            "{removed} passkey(s) were removed from your sign-in account and every \
             signed-in device was signed out.\n\nIf that was not you, change your \
             password now.\n"
        ),
    );
    Json(json!({ "ok": true, "removed": removed })).into_response()
}

// -----------------------------------------------------------------------------
//  Recovery by email: a link that opens ONE door, "add a passkey"
// -----------------------------------------------------------------------------

/// A plain email, sent in the background so the reply takes the same time
/// whether or not anything is sent (no clue about which addresses exist).
fn notify(to: &str, subject: &str, body: String) {
    let (to, subject) = (to.to_string(), subject.to_string());
    tokio::spawn(async move {
        match crate::mail::send_text(&to, &subject, &body).await {
            Ok(true) => tracing::info!(email = %to, "notice emailed"),
            Ok(false) => {}
            Err(e) => tracing::warn!(email = %to, "notice email failed: {e}"),
        }
    });
}

#[derive(Template)]
#[template(path = "recover.html")]
struct RecoverTemplate {
    /// "ask", "sent" or "use"
    mode: String,
    token: String,
    enabled: bool,
    error: String,
}

fn render_recover(
    mode: &str,
    token: &str,
    enabled: bool,
    error: &str,
    code: StatusCode,
) -> Response {
    let html = RecoverTemplate {
        mode: mode.to_string(),
        token: token.to_string(),
        enabled,
        error: error.to_string(),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    (code, Html(html)).into_response()
}

#[derive(Deserialize)]
pub struct RecoverQuery {
    #[serde(default)]
    t: String,
}

/// GET never uses the token up: mail scanners and link previews open links.
pub async fn recover_page(
    State(state): State<Arc<AppState>>,
    axum::extract::Query(q): axum::extract::Query<RecoverQuery>,
) -> Response {
    let on = state.passkeys.is_some();
    if q.t.is_empty() {
        render_recover("ask", "", on, "", StatusCode::OK)
    } else {
        render_recover("use", &q.t, on, "", StatusCode::OK)
    }
}

pub async fn recover_send(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let Some(pk) = state.passkeys.as_ref() else {
        return render_recover("ask", "", false, "", StatusCode::OK);
    };
    if !origin_ok(pk, &headers) {
        return render_recover(
            "ask",
            "",
            true,
            "That request did not come from this site's own page. Reload the page and try again.",
            StatusCode::FORBIDDEN,
        );
    }
    let email = common::people::normalise(&crate::form_field(&body, "email").unwrap_or_default());
    if email.is_empty() {
        return render_recover(
            "ask",
            "",
            true,
            "Type your email address.",
            StatusCode::BAD_REQUEST,
        );
    }
    // The page is the same whatever happens below, so it never says whether
    // an address has an account.
    let sent = || render_recover("sent", "", true, "", StatusCode::OK);
    let now = Instant::now();
    {
        let hour = Duration::from_secs(3600);
        let mut l = lock(&pk.limits);
        // Per address and per client address; no shared bucket a stranger
        // could fill to stop the household from asking.
        let per_address = format!("recover:{email}");
        let per_ip = format!("recover-ip:{}", client_ip(&headers));
        if l.blocked(&per_address, 3, hour, now).is_some()
            || l.blocked(&per_ip, 20, hour, now).is_some()
        {
            tracing::info!(email = %email, "recovery link asked too often; not sending");
            return sent();
        }
        l.record(&per_address, now);
        l.record(&per_ip, now);
    }
    // Everything that differs between a known and an unknown address (reading
    // the people list, the file write, the mail) happens AFTER we have
    // answered, so the reply is the same page in the same time either way.
    let st = state.clone();
    tokio::spawn(async move { recover_job(&st, &email) });
    sent()
}

/// The work behind a recovery request. Never says anything to the browser.
fn recover_job(state: &AppState, email: &str) {
    let Some(pk) = state.passkeys.as_ref() else {
        return;
    };
    let now_unix = common::people::now();
    let known = common::people::find(&crate::people(state), email)
        .map(|p| p.has_password())
        .unwrap_or(false);
    // A guest whose account has ended gets nothing.
    let ended = account_ended(state, email);
    if !known || ended {
        tracing::info!(email = %email, "recovery link asked for an address with no usable account");
        return;
    }
    let token = {
        let _g = lock(&pk.recovery_lock);
        let Ok(mut rec) = load_any::<RecoveryFile>(&pk.recovery_file) else {
            tracing::error!("passkey-recovery.json does not parse; no link sent");
            return;
        };
        let Some(t) = rec.issue_if_free(email, now_unix) else {
            tracing::info!(email = %email, "a recovery link is already outstanding; not sending another");
            return;
        };
        if let Err(e) = save_any(&pk.recovery_file, &rec) {
            tracing::error!("could not write the recovery file: {e}");
            return;
        }
        t
    };
    tracing::info!(email = %email, "recovery link requested");
    notify(
        email,
        "Add a passkey to your account",
        format!(
            "Someone asked to add a passkey to your sign-in account. Open this link \
             on the device you want to add:\n\n    {}/recover?t={token}\n\n\
             It works once and expires in 15 minutes. It can only add a passkey; it \
             does not sign anyone in.\n\nIf you did not ask for this, ignore this \
             message. Nothing has changed.\n",
            pk.link_base
        ),
    );
}

#[derive(Deserialize)]
struct RecStart {
    token: String,
    /// Not sent by the page (set automatically); see StartReg.
    #[serde(default)]
    name: String,
    #[serde(default)]
    days: u64,
}

/// Turns a link into one registration challenge. Opens nothing else.
pub async fn recover_start(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let Some(pk) = state.passkeys.as_ref() else {
        return off();
    };
    if !origin_ok(pk, &headers) {
        return bad_origin();
    }
    let Ok(b) = serde_json::from_str::<RecStart>(&body) else {
        return jerr(
            StatusCode::BAD_REQUEST,
            "That did not look right. Try again.",
        );
    };
    let now = Instant::now();
    let try_key = format!("recover-try:{}", client_ip(&headers));
    {
        let mut l = lock(&pk.limits);
        if let Some(w) = l.blocked(&try_key, 20, FAIL_WINDOW, now) {
            return jerr(StatusCode::TOO_MANY_REQUESTS, &wait_msg(w));
        }
    }
    // A bad name must not burn the link, so check it first.
    let name = match optional_name(&b.name) {
        Ok(n) => n,
        Err(m) => return jerr(StatusCode::BAD_REQUEST, m),
    };
    let _g = lock(&pk.recovery_lock);
    let Ok(mut rec) = load_any::<RecoveryFile>(&pk.recovery_file) else {
        return jerr(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Recovery isn't working right now. Tell Jason.",
        );
    };
    let unix = common::people::now();
    let Some(email) = rec.peek(&b.token, unix) else {
        lock(&pk.limits).record(&try_key, now);
        return jerr(
            StatusCode::BAD_REQUEST,
            "That link has expired or was already used. Ask for a new one.",
        );
    };
    let person_name = common::people::find(&crate::people(&state), &email)
        .map(|p| p.name.clone())
        .unwrap_or_default();
    // Checks first, so a refusal (name taken, too many keys) leaves the link
    // alive; it is used up only once a challenge really exists.
    let (challenge, options) =
        match begin_registration(pk, &email, &person_name, name, b.days, true, now) {
            Ok(x) => x,
            Err(r) => return r,
        };
    rec.redeem(&b.token, unix);
    if let Err(e) = save_any(&pk.recovery_file, &rec) {
        tracing::error!("could not write the recovery file: {e}");
        lock(&pk.pending).take(&challenge, now);
        return jerr(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Could not do that. Try again.",
        );
    }
    tracing::info!(email = %email, "recovery link used");
    Json(json!({ "token": challenge, "options": options })).into_response()
}

// -----------------------------------------------------------------------------
//  The "add a passkey?" offer after a password sign-in
// -----------------------------------------------------------------------------

#[derive(Template)]
#[template(path = "offer.html")]
struct OfferTemplate {
    next: String,
    default_name: String,
}

/// What the password paths (sign-in, first password choice) call instead of
/// `issue_session`. The session is ALWAYS issued; the only question is whether
/// an offer page stands between the person and where they were going. Anything
/// odd (passkeys off, damaged file) means no offer, never a block.
pub fn after_password(state: &AppState, email: &str, next: &str) -> Response {
    let session = crate::issue_session(state, email, next);
    let Some(pk) = state.passkeys.as_ref() else {
        return session;
    };
    let person_name = common::people::find(&crate::people(state), email)
        .map(|p| p.name.clone())
        .unwrap_or_default();
    let (due, default_name) = {
        let _g = lock(&pk.file_lock);
        match load(&pk.file) {
            Ok(f) => {
                let u = f.user(email);
                let label = display_label(
                    u.map(|u| u.label.as_str()).unwrap_or(""),
                    &person_name,
                    email,
                );
                (
                    offer_due(u, common::people::now()),
                    free_name(&label, u.map(|u| u.keys.as_slice()).unwrap_or(&[])),
                )
            }
            Err(_) => (false, String::new()),
        }
    };
    if !due {
        return session;
    }
    let Ok(html) = (OfferTemplate {
        next: next.to_string(),
        default_name,
    })
    .render() else {
        return session;
    };
    let mut out = HeaderMap::new();
    if let Some(c) = session.headers().get(header::SET_COOKIE) {
        out.insert(header::SET_COOKIE, c.clone());
    }
    out.insert(
        header::CACHE_CONTROL,
        "no-store"
            .parse()
            .unwrap_or_else(|_| header::HeaderValue::from_static("no-store")),
    );
    (StatusCode::OK, out, Html(html)).into_response()
}

#[derive(Deserialize)]
struct OfferChoice {
    /// "later" (7 days) or "never"
    choice: String,
}

pub async fn offer_choice(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let Some(pk) = state.passkeys.as_ref() else {
        return off();
    };
    if !origin_ok(pk, &headers) {
        return bad_origin();
    }
    let Some(email) = crate::cookie_email(&state, &headers) else {
        return jerr(StatusCode::UNAUTHORIZED, "Sign in first.");
    };
    let Ok(b) = serde_json::from_str::<OfferChoice>(&body) else {
        return jerr(StatusCode::BAD_REQUEST, "That did not look right.");
    };
    if b.choice != "later" && b.choice != "never" {
        return jerr(StatusCode::BAD_REQUEST, "That did not look right.");
    }
    let _g = lock(&pk.file_lock);
    let Ok(mut file) = load(&pk.file) else {
        return jerr(
            StatusCode::INTERNAL_SERVER_ERROR,
            "The passkey list looks damaged, so I won't change it. Tell Jason.",
        );
    };
    if file.user(&email).is_none() {
        file.users.push(UserKeys {
            email: email.clone(),
            user_id: new_user_id().to_string(),
            ..Default::default()
        });
    }
    if let Some(u) = file.users.iter_mut().find(|u| u.email == email) {
        if b.choice == "never" {
            u.offer_never = true;
        } else {
            u.offer_snooze_until = common::people::now() + SNOOZE_SECS;
        }
    }
    if let Err(e) = save(&pk.file, &file) {
        tracing::error!("could not write the passkey file: {e}");
        return jerr(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Could not save that. Try again.",
        );
    }
    tracing::info!(email = %email, choice = %b.choice, "passkey offer answered");
    Json(json!({ "ok": true })).into_response()
}

/// Drop entries for people who are no longer in people.json. Does nothing when
/// the list of known people is empty (an unreadable people.json looks empty,
/// and that must never wipe everybody's passkeys). Returns (people, passkeys)
/// removed.
pub fn prune_unknown(file: &mut PasskeyFile, known: &[String]) -> (usize, usize) {
    if known.is_empty() {
        return (0, 0);
    }
    let (mut people, mut keys) = (0, 0);
    file.users.retain(|u| {
        let keep = known.contains(&u.email);
        if !keep {
            people += 1;
            keys += u.keys.len();
        }
        keep
    });
    (people, keys)
}

/// Remove all of one person's passkeys and reset their passkey settings
/// (passkey-only off, the "add a passkey?" answers forgotten). Returns how many
/// passkeys went. The label, cap and guest settings are the owner's and stay.
pub fn wipe_user(file: &mut PasskeyFile, email: &str) -> usize {
    let Some(u) = file.users.iter_mut().find(|u| u.email == email) else {
        return 0;
    };
    let n = u.keys.len();
    u.keys.clear();
    u.passkey_only = false;
    u.offer_never = false;
    u.offer_snooze_until = 0;
    n
}

/// After an emailed password reset: every passkey for this person goes, and so
/// does any recovery link still outstanding for them.
pub fn wipe_for_reset(state: &AppState, email: &str) {
    let Some(pk) = state.passkeys.as_ref() else {
        return;
    };
    let _r = lock(&pk.recovery_lock);
    if let Ok(mut rec) = load_any::<RecoveryFile>(&pk.recovery_file) {
        let before = rec.items.len();
        rec.items.retain(|i| i.email != email);
        if rec.items.len() != before {
            let _ = save_any(&pk.recovery_file, &rec);
        }
    }
    let _g = lock(&pk.file_lock);
    let Ok(mut file) = load(&pk.file) else {
        return;
    };
    let n = wipe_user(&mut file, email);
    if n == 0 && file.user(email).is_none() {
        return;
    }
    match save(&pk.file, &file) {
        Ok(()) => tracing::info!(email = %email, removed = n, "password reset: passkeys removed"),
        Err(e) => tracing::error!("could not write the passkey file: {e}"),
    }
}

/// After an ordinary password change: passkeys stay. If passkey-only is on but
/// the passkeys behind it are no longer enough, switch it off so the person
/// still has a way in.
pub fn after_password_change(state: &AppState, email: &str) {
    let Some(pk) = state.passkeys.as_ref() else {
        return;
    };
    let _g = lock(&pk.file_lock);
    let Ok(mut file) = load(&pk.file) else {
        return;
    };
    let Some(u) = file.users.iter_mut().find(|u| u.email == email) else {
        return;
    };
    if u.passkey_only && can_set_passkey_only(&u.keys, common::people::now()).is_err() {
        u.passkey_only = false;
        if save(&pk.file, &file).is_ok() {
            tracing::info!(email = %email, "passkey-only switched off: not enough passkeys behind it");
        }
    }
}

impl Passkeys {
    /// The pruning run (at startup, then every fifteen minutes): remove loaner
    /// passkeys whose time has passed, every passkey of an ended guest account,
    /// and entries for people no longer in people.json. Counts only in the log
    /// for the last; names for the rest.
    pub fn prune_on_start(&self, known_people: &[String]) {
        let _g = lock(&self.file_lock);
        let Ok(mut f) = load(&self.file) else {
            return;
        };
        let gone = prune_expired(&mut f, common::people::now());
        let (people, keys) = prune_unknown(&mut f, known_people);
        if gone.is_empty() && people == 0 {
            return;
        }
        match save(&self.file, &f) {
            Ok(()) => {
                for (email, name) in gone {
                    tracing::info!(email = %email, key = %name, "expired passkey removed");
                }
                if people > 0 {
                    tracing::info!(
                        people,
                        keys,
                        "passkey entries for people no longer on the list removed"
                    );
                }
            }
            Err(e) => tracing::error!("could not write the passkey file: {e}"),
        }
    }
}

// -----------------------------------------------------------------------------
//  The name shown in the passkey manager
// -----------------------------------------------------------------------------

// People do not name their own passkeys: each key is saved under the person's
// label, which only an owner can set (owner tools, confirmed by passkey; or by
// hand in passkeys.json). Passkeys already made keep the name they were
// created with (the device stores it).

/// Owner action: set (or clear, when empty) a person's label.
fn exec_label(state: &AppState, pk: &Passkeys, owner: &str, email: &str, label: &str) -> Response {
    let label = match clean_label(label) {
        Ok(l) => l,
        Err(m) => return jerr(StatusCode::BAD_REQUEST, m),
    };
    let target = common::people::normalise(email);
    if common::people::find(&crate::people(state), &target).is_none() {
        return jerr(StatusCode::BAD_REQUEST, "No such person.");
    }
    let _g = lock(&pk.file_lock);
    let Ok(mut file) = load(&pk.file) else {
        return jerr(
            StatusCode::INTERNAL_SERVER_ERROR,
            "The passkey list looks damaged, so I won't change it.",
        );
    };
    user_entry(&mut file, &target).label = label;
    if let Err(e) = save(&pk.file, &file) {
        tracing::error!("could not write the passkey file: {e}");
        return jerr(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Could not save that. Try again.",
        );
    }
    tracing::info!(owner = %owner, email = %target, "owner set a passkey label");
    Json(json!({ "ok": true })).into_response()
}

/// This person's entry, made (with a fresh user handle) if it is not there.
fn user_entry<'a>(file: &'a mut PasskeyFile, email: &str) -> &'a mut UserKeys {
    if let Some(i) = file.users.iter().position(|u| u.email == email) {
        return &mut file.users[i];
    }
    file.users.push(UserKeys {
        email: email.to_string(),
        user_id: new_user_id().to_string(),
        ..Default::default()
    });
    let last = file.users.len() - 1;
    &mut file.users[last]
}

// -----------------------------------------------------------------------------
//  Owner tools: see, revoke, cap, guest accounts
//
//  Only the addresses in AUTH_OWNER_EMAILS may use these (and only with a
//  signed-in session). With that unset they are switched off, and the same
//  things can still be done by editing passkeys.json (see PASSKEYS.md).
// -----------------------------------------------------------------------------

/// Days since 1970-01-01 for a calendar date (proleptic Gregorian).
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = (if y >= 0 { y } else { y - 399 }) / 400;
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

fn civil_from_days(z: i64) -> (i64, i64, i64) {
    let z = z + 719468;
    let era = (if z >= 0 { z } else { z - 146096 }) / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// "2026-12-31" -> the moment that day is over (start of the next day, UTC), so
/// an account "until" a date works through that whole date.
pub fn parse_end_of_day(s: &str) -> Option<u64> {
    let mut it = s.trim().split('-');
    let (y, m, d) = (it.next()?, it.next()?, it.next()?);
    if it.next().is_some() || y.len() != 4 || m.len() != 2 || d.len() != 2 {
        return None;
    }
    let (y, m, d): (i64, i64, i64) = (y.parse().ok()?, m.parse().ok()?, d.parse().ok()?);
    if !(2000..=2200).contains(&y) || !(1..=12).contains(&m) || !(1..=31).contains(&d) {
        return None;
    }
    let days = days_from_civil(y, m, d);
    if civil_from_days(days) != (y, m, d) {
        return None; // 31 February and the like
    }
    u64::try_from((days + 1) * 86400).ok()
}

/// The last day an account works, as "YYYY-MM-DD" (the reverse of the above).
pub fn last_day_text(expires: u64) -> String {
    if expires == 0 {
        return String::new();
    }
    let (y, m, d) = civil_from_days(((expires - 1) / 86400) as i64);
    format!("{y:04}-{m:02}-{d:02}")
}

/// "added from Chrome on Android; last used from Safari on iOS via example.org"
/// (parts left out where the service saw nothing).
pub fn device_line(k: &StoredKey) -> String {
    let mut parts = Vec::new();
    if !k.added_device.is_empty() {
        parts.push(format!("added from {}", k.added_device));
    }
    if !k.last_device.is_empty() || !k.last_host.is_empty() {
        let mut s = String::from("last used from ");
        s.push_str(if k.last_device.is_empty() {
            "an unknown browser"
        } else {
            &k.last_device
        });
        if !k.last_host.is_empty() {
            s.push_str(" via ");
            s.push_str(&k.last_host);
        }
        parts.push(s);
    }
    parts.join("; ")
}

/// Who gets the "<Name> added a passkey" notice: the owner addresses.
pub fn owner_recipients(pk: &Passkeys) -> Vec<String> {
    pk.owners.clone()
}

pub struct OwnerKey {
    pub id: String,
    pub name: String,
    /// Created, as "2026-10-27".
    pub added: String,
    /// Last used, as "2026-10-27 14:05 UTC" or "never".
    pub last: String,
    /// "Chrome on Android via example.org", where the service saw one.
    pub device: String,
    pub ends: String,
}

pub struct OwnerPerson {
    pub email: String,
    pub name: String,
    /// The label set for them (empty = automatic) and what it works out to.
    pub label: String,
    pub effective: String,
    pub guest: bool,
    pub ends: String,
    pub cap: usize,
    pub keys: Vec<OwnerKey>,
}

#[derive(Template)]
#[template(path = "owner.html")]
struct OwnerTemplate {
    people: Vec<OwnerPerson>,
}

/// Owner check shared by the owner endpoints. Err is a ready reply.
#[allow(clippy::result_large_err)]
fn owner_gate<'a>(
    state: &'a AppState,
    headers: &HeaderMap,
    is_post: bool,
) -> Result<(&'a Passkeys, String), Response> {
    let Some(pk) = state.passkeys.as_ref() else {
        return Err(off());
    };
    if is_post && !origin_ok(pk, headers) {
        return Err(bad_origin());
    }
    let Some(email) = crate::cookie_email(state, headers) else {
        return Err(jerr(StatusCode::UNAUTHORIZED, "Sign in first."));
    };
    if pk.owners.is_empty() {
        return Err(jerr(
            StatusCode::FORBIDDEN,
            "The owner tools are switched off (AUTH_OWNER_EMAILS is not set).",
        ));
    }
    if !pk.owners.contains(&email) {
        tracing::warn!(email = %email, "someone who is not an owner tried the owner tools");
        return Err(jerr(
            StatusCode::FORBIDDEN,
            "That page is for the owner only.",
        ));
    }
    Ok((pk, email))
}

pub async fn owner_page(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    let (pk, _owner) = match owner_gate(&state, &headers, false) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let now = common::people::now();
    let file = {
        let _g = lock(&pk.file_lock);
        match load(&pk.file) {
            Ok(f) => f,
            Err(_) => {
                return jerr(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "The passkey list looks damaged, so it can't be shown. Fix or restore passkeys.json.",
                )
            }
        }
    };
    let people = crate::people(&state)
        .iter()
        .map(|p| {
            let u = file.user(&p.email);
            OwnerPerson {
                email: p.email.clone(),
                name: p.name.clone(),
                label: u.map(|u| u.label.clone()).unwrap_or_default(),
                effective: display_label(
                    u.map(|u| u.label.as_str()).unwrap_or(""),
                    &p.name,
                    &p.email,
                ),
                guest: u.map(|u| u.guest).unwrap_or(false),
                ends: u
                    .map(|u| last_day_text(u.account_expires))
                    .unwrap_or_default(),
                cap: cap_for(u),
                keys: u
                    .map(|u| {
                        u.keys
                            .iter()
                            .map(|k| OwnerKey {
                                id: k.id.clone(),
                                name: k.name.clone(),
                                added: date_text(k.added),
                                last: when_text(k.last_used),
                                device: device_line(k),
                                ends: loaner_note(k, now),
                            })
                            .collect()
                    })
                    .unwrap_or_default(),
            }
        })
        .collect();
    let html = OwnerTemplate { people }
        .render()
        .unwrap_or_else(|_| "<h1>template error</h1>".into());
    Html(html).into_response()
}

/// What an owner action asks for. Chosen on the page, held on the server while
/// the owner proves it with their own passkey, then run (see `owner_begin`).
#[derive(Deserialize, Clone)]
#[serde(tag = "action", rename_all = "lowercase")]
enum OwnerAction {
    Revoke {
        email: String,
        /// One passkey; absent means all of that person's passkeys.
        #[serde(default)]
        id: Option<String>,
    },
    Cap {
        email: String,
        cap: u32,
    },
    Label {
        email: String,
        #[serde(default)]
        label: String,
    },
    Guest {
        email: String,
        guest: bool,
        /// "YYYY-MM-DD": the last day the account works.
        #[serde(default)]
        expires: String,
    },
}

struct OwnerRevoke {
    email: String,
    id: Option<String>,
}

/// Revoke one passkey or all of a person's. Always allowed (this is the
/// owner's override of the "last way in" rule), and takes effect on the very
/// next request: sign-in reads the file fresh every time.
fn exec_revoke(state: &AppState, pk: &Passkeys, owner: &str, b: OwnerRevoke) -> Response {
    let target = common::people::normalise(&b.email);
    let removed = {
        let _g = lock(&pk.file_lock);
        let Ok(mut file) = load(&pk.file) else {
            return jerr(
                StatusCode::INTERNAL_SERVER_ERROR,
                "The passkey list looks damaged, so I won't change it.",
            );
        };
        let Some(u) = file.users.iter_mut().find(|u| u.email == target) else {
            return jerr(StatusCode::BAD_REQUEST, "That person has no passkeys.");
        };
        let removed = match &b.id {
            Some(id) => {
                let Some(pos) = u.keys.iter().position(|k| k.id == *id) else {
                    return jerr(StatusCode::BAD_REQUEST, "I can't find that passkey.");
                };
                let k = u.keys.remove(pos);
                if u.passkey_only && can_set_passkey_only(&u.keys, common::people::now()).is_err() {
                    u.passkey_only = false;
                }
                tracing::info!(owner = %owner, email = %target, key = %k.name, "owner revoked a passkey");
                1
            }
            None => {
                let n = u.keys.len();
                u.keys.clear();
                u.passkey_only = false;
                tracing::info!(owner = %owner, email = %target, removed = n, "owner revoked all passkeys");
                n
            }
        };
        if let Err(e) = save(&pk.file, &file) {
            tracing::error!("could not write the passkey file: {e}");
            return jerr(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Could not save that. Try again.",
            );
        }
        removed
    };
    if b.id.is_none() {
        // Revoking everything also ends their open sessions.
        crate::revoke_all(state, &target);
    }
    Json(json!({ "ok": true, "removed": removed })).into_response()
}

/// Set the most passkeys a person may hold (1 to 20; 0 puts back the default
/// of 5). Passkeys already over a lowered cap stay until removed.
fn exec_cap(state: &AppState, pk: &Passkeys, owner: &str, email: &str, cap: u32) -> Response {
    if cap as usize > MAX_CAP {
        return jerr(StatusCode::BAD_REQUEST, "The most you can allow is 20.");
    }
    let target = common::people::normalise(email);
    if common::people::find(&crate::people(state), &target).is_none() {
        return jerr(StatusCode::BAD_REQUEST, "No such person.");
    }
    let _g = lock(&pk.file_lock);
    let Ok(mut file) = load(&pk.file) else {
        return jerr(
            StatusCode::INTERNAL_SERVER_ERROR,
            "The passkey list looks damaged, so I won't change it.",
        );
    };
    user_entry(&mut file, &target).cap = cap;
    if let Err(e) = save(&pk.file, &file) {
        tracing::error!("could not write the passkey file: {e}");
        return jerr(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Could not save that. Try again.",
        );
    }
    tracing::info!(owner = %owner, email = %target, cap, "owner set a passkey cap");
    Json(json!({ "ok": true })).into_response()
}

/// Mark a person as a guest with an end date, or back to a normal account.
/// Becoming a guest removes any passkeys they already had (a guest may hold
/// only one temporary passkey); from the end date every way in is refused.
fn exec_guest(
    state: &AppState,
    pk: &Passkeys,
    owner: &str,
    email: &str,
    guest: bool,
    expires: &str,
) -> Response {
    let target = common::people::normalise(email);
    if common::people::find(&crate::people(state), &target).is_none() {
        return jerr(StatusCode::BAD_REQUEST, "No such person.");
    }
    let end = if guest {
        match parse_end_of_day(expires) {
            Some(e) if e > common::people::now() => e,
            _ => {
                return jerr(
                    StatusCode::BAD_REQUEST,
                    "Pick the last day as a date in the future, like 2026-12-31.",
                )
            }
        }
    } else {
        0
    };
    let _g = lock(&pk.file_lock);
    let Ok(mut file) = load(&pk.file) else {
        return jerr(
            StatusCode::INTERNAL_SERVER_ERROR,
            "The passkey list looks damaged, so I won't change it.",
        );
    };
    let u = user_entry(&mut file, &target);
    let dropped = if guest && !u.guest {
        let n = u.keys.len();
        u.keys.clear();
        u.passkey_only = false;
        n
    } else {
        0
    };
    u.guest = guest;
    u.account_expires = end;
    if let Err(e) = save(&pk.file, &file) {
        tracing::error!("could not write the passkey file: {e}");
        return jerr(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Could not save that. Try again.",
        );
    }
    tracing::info!(owner = %owner, email = %target, guest, dropped, "owner changed a guest account");
    Json(json!({ "ok": true })).into_response()
}

// ---- every owner action needs a fresh passkey assertion from the owner -----
//
// Step 1 (`owner_begin`): the page says what it wants done. The server checks
// the session is an owner's, remembers the request, and sends a passkey
// challenge limited to THAT OWNER'S OWN passkeys with user verification
// required. Step 2 (`owner_confirm`): the owner's device answers; only if the
// answer verifies is the remembered request run. A stolen session cookie is
// therefore not enough to touch anybody's passkeys. The challenge is single
// use and lasts five minutes like the others.

pub async fn owner_begin(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let (pk, owner) = match owner_gate(&state, &headers, true) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let Ok(action) = serde_json::from_str::<OwnerAction>(&body) else {
        return jerr(StatusCode::BAD_REQUEST, "That did not look right.");
    };
    let now = Instant::now();
    {
        let key = format!("owner:{owner}");
        let mut l = lock(&pk.limits);
        if let Some(w) = l.blocked(&key, 20, CHALLENGE_LIFE, now) {
            return jerr(StatusCode::TOO_MANY_REQUESTS, &wait_msg(w));
        }
        l.record(&key, now);
    }
    let own_keys: Vec<Passkey> = {
        let _g = lock(&pk.file_lock);
        match load(&pk.file) {
            Ok(f) => f
                .user(&owner)
                .map(|u| {
                    let t = common::people::now();
                    u.keys
                        .iter()
                        .filter(|k| !expired(k, t))
                        .map(|k| k.passkey.clone())
                        .collect()
                })
                .unwrap_or_default(),
            Err(_) => Vec::new(),
        }
    };
    if own_keys.is_empty() {
        return jerr(
            StatusCode::BAD_REQUEST,
            "Owner changes need one of your own passkeys. Add one on your account page first.",
        );
    }
    let Ok((rcr, st)) = pk.webauthn.start_passkey_authentication(&own_keys) else {
        return jerr(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Could not start that. Try again.",
        );
    };
    let Some(token) = lock(&pk.pending).put(
        Pending::Owner {
            owner,
            action,
            state: st,
        },
        now,
    ) else {
        return jerr(
            StatusCode::SERVICE_UNAVAILABLE,
            "Busy right now. Try again in a minute.",
        );
    };
    Json(json!({ "token": token, "options": rcr })).into_response()
}

#[derive(Deserialize)]
struct OwnerConfirm {
    token: String,
    credential: serde_json::Value,
}

pub async fn owner_confirm(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let (pk, owner) = match owner_gate(&state, &headers, true) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let Ok(b) = serde_json::from_str::<OwnerConfirm>(&body) else {
        return jerr(StatusCode::BAD_REQUEST, "That did not look right.");
    };
    let taken = lock(&pk.pending).take(&b.token, Instant::now());
    let Some(Pending::Owner {
        owner: for_owner,
        action,
        state: auth_state,
    }) = taken
    else {
        return jerr(
            StatusCode::BAD_REQUEST,
            "That took too long or was already used. Start again.",
        );
    };
    if for_owner != owner {
        return jerr(
            StatusCode::FORBIDDEN,
            "That belongs to a different sign-in.",
        );
    }
    let Ok(cred) = serde_json::from_value::<PublicKeyCredential>(b.credential) else {
        return jerr(
            StatusCode::BAD_REQUEST,
            "The device's answer was not readable.",
        );
    };
    let res = match pk
        .webauthn
        .finish_passkey_authentication(&cred, &auth_state)
    {
        Ok(r) => r,
        Err(_) => {
            tracing::warn!(owner = %owner, "an owner action was not confirmed by a passkey");
            return jerr(
                StatusCode::UNAUTHORIZED,
                "Your passkey did not confirm that. Nothing was changed.",
            );
        }
    };
    {
        // Keep the counter honest, as a normal sign-in does.
        let _g = lock(&pk.file_lock);
        if let Ok(mut file) = load(&pk.file) {
            if let Some(u) = file.users.iter_mut().find(|u| u.email == owner) {
                for k in &mut u.keys {
                    k.passkey.update_credential(&res);
                }
                let _ = save(&pk.file, &file);
            }
        }
    }
    match action {
        OwnerAction::Revoke { email, id } => {
            exec_revoke(&state, pk, &owner, OwnerRevoke { email, id })
        }
        OwnerAction::Cap { email, cap } => exec_cap(&state, pk, &owner, &email, cap),
        OwnerAction::Label { email, label } => exec_label(&state, pk, &owner, &email, &label),
        OwnerAction::Guest {
            email,
            guest,
            expires,
        } => exec_guest(&state, pk, &owner, &email, guest, &expires),
    }
}

// -----------------------------------------------------------------------------
//  Sign in
// -----------------------------------------------------------------------------

pub async fn login_start(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    let Some(pk) = state.passkeys.as_ref() else {
        return off();
    };
    if !origin_ok(pk, &headers) {
        return bad_origin();
    }
    let now = Instant::now();
    {
        // Per client address, never one shared bucket: a shared one could be
        // used up by a stranger to stop everybody's passkey sign-in.
        let key = format!("login-start:{}", client_ip(&headers));
        let mut l = lock(&pk.limits);
        if let Some(w) = l.blocked(&key, 60, Duration::from_secs(60), now) {
            return jerr(StatusCode::TOO_MANY_REQUESTS, &wait_msg(w));
        }
        l.record(&key, now);
    }
    let Ok((rcr, st)) = pk.webauthn.start_discoverable_authentication() else {
        return jerr(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Could not start that. Try again.",
        );
    };
    let Some(token) = lock(&pk.pending).put(Pending::Login(st), now) else {
        return jerr(
            StatusCode::SERVICE_UNAVAILABLE,
            "Busy right now. Try again in a minute.",
        );
    };
    Json(json!({ "token": token, "options": rcr })).into_response()
}

#[derive(Deserialize)]
struct FinishLogin {
    token: String,
    credential: serde_json::Value,
    #[serde(default)]
    next: String,
}

const FAIL_WINDOW: Duration = Duration::from_secs(300);

fn refused() -> Response {
    // One message for every way this can fail, so it says nothing about who
    // has an account here.
    jerr(
        StatusCode::UNAUTHORIZED,
        "That passkey wasn't accepted here. Use your password instead.",
    )
}

/// For a passkey that is known but no longer valid: expired, revoked, or on an
/// account that has ended. The same plain words for all of them.
fn gone() -> Response {
    jerr(
        StatusCode::UNAUTHORIZED,
        "That passkey isn't valid any more. Use your password or another passkey.",
    )
}

pub async fn login_finish(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let Some(pk) = state.passkeys.as_ref() else {
        return off();
    };
    if !origin_ok(pk, &headers) {
        return bad_origin();
    }
    let now = Instant::now();
    // Failures are counted per client address and per account, never in one
    // shared bucket (a stranger could fill that and lock everyone out of
    // passkeys). Junk that does not even parse as a credential counts nowhere.
    let ip_key = format!("fail-ip:{}", client_ip(&headers));
    {
        let mut l = lock(&pk.limits);
        if let Some(w) = l.blocked(&ip_key, 20, FAIL_WINDOW, now) {
            return jerr(StatusCode::TOO_MANY_REQUESTS, &wait_msg(w));
        }
    }
    let Ok(b) = serde_json::from_str::<FinishLogin>(&body) else {
        return jerr(
            StatusCode::BAD_REQUEST,
            "That did not look right. Try again.",
        );
    };
    let fail = |email: Option<&str>| {
        let mut l = lock(&pk.limits);
        l.record(&ip_key, now);
        if let Some(e) = email {
            l.record(&format!("fail:{e}"), now);
        }
    };
    let taken = lock(&pk.pending).take(&b.token, now);
    let Some(Pending::Login(st)) = taken else {
        return jerr(
            StatusCode::BAD_REQUEST,
            "That took too long or was already used. Try again.",
        );
    };
    let Ok(cred) = serde_json::from_value::<PublicKeyCredential>(b.credential) else {
        return refused();
    };
    let Ok((uid, cred_id)) = pk.webauthn.identify_discoverable_authentication(&cred) else {
        return refused();
    };
    let cred_id = cred_id.to_vec();

    let _g = lock(&pk.file_lock);
    let mut file = match load(&pk.file) {
        Ok(f) => f,
        Err(_) => {
            tracing::error!("passkeys.json does not parse; passkey sign-in is unavailable");
            return jerr(
                StatusCode::INTERNAL_SERVER_ERROR,
                "Passkeys aren't working right now. Use your password.",
            );
        }
    };
    let uid_text = uid.to_string();
    let Some(idx) = file.users.iter().position(|u| u.user_id == uid_text) else {
        fail(None);
        return refused();
    };
    let email = file.users[idx].email.clone();
    if let Some(w) = lock(&pk.limits).blocked(&format!("fail:{email}"), 3, FAIL_WINDOW, now) {
        return jerr(StatusCode::TOO_MANY_REQUESTS, &wait_msg(w));
    }
    let Some(kidx) = file.users[idx]
        .keys
        .iter()
        .position(|k| k.passkey.cred_id().as_ref() == cred_id.as_slice())
    else {
        // Revoked (or never ours): read fresh from the file on every attempt,
        // so a revoke takes effect on the very next request.
        fail(Some(&email));
        tracing::warn!(email = %email, "a revoked or unknown passkey was refused");
        return gone();
    };
    // An expired loaner, or a guest account past its end date, is refused here
    // even if nothing has removed it yet.
    let now_unix = common::people::now();
    if expired(&file.users[idx].keys[kidx], now_unix) || account_expired(&file.users[idx], now_unix)
    {
        tracing::info!(email = %email, key = %file.users[idx].keys[kidx].name,
                       "an expired passkey was refused");
        return gone();
    }
    // The account must still exist, have a password, and not be waiting on a
    // forced password change; see the top of this file for why.
    let list = crate::people(&state);
    let usable = common::people::find(&list, &email)
        .map(|p| p.has_password() && !p.must_change)
        .unwrap_or(false);
    if !usable {
        tracing::warn!(email = %email, "passkey sign-in refused: no usable password on the account");
        return jerr(
            StatusCode::UNAUTHORIZED,
            "Sign in with your password this time. Passkeys work after that.",
        );
    }
    let dk = DiscoverableKey::from(&file.users[idx].keys[kidx].passkey);
    let res = match pk
        .webauthn
        .finish_discoverable_authentication(&cred, st, &[dk])
    {
        Ok(r) => r,
        Err(_) => {
            fail(Some(&email));
            tracing::warn!(email = %email, "a passkey sign-in was not accepted");
            return refused();
        }
    };
    {
        let k = &mut file.users[idx].keys[kidx];
        k.passkey.update_credential(&res);
        k.last_used = common::people::now();
        // For the owner's view: browser family and site address only.
        k.last_device = device_of(&headers);
        k.last_host = host_of_origin(&headers);
    }
    if let Err(e) = save(&pk.file, &file) {
        // The sign-in is genuine; only the counter bookkeeping failed.
        tracing::error!("could not write the passkey file: {e}");
    }
    let name = file.users[idx].keys[kidx].name.clone();
    tracing::info!(email = %email, key = %name, "signed in with a passkey");

    let next = crate::safe_next(&b.next);
    let session = crate::issue_session(&state, &email, &next);
    let mut out = HeaderMap::new();
    if let Some(c) = session.headers().get(header::SET_COOKIE) {
        out.insert(header::SET_COOKIE, c.clone());
    }
    (out, Json(json!({ "ok": true, "next": next }))).into_response()
}

// -----------------------------------------------------------------------------
//  Tests
// -----------------------------------------------------------------------------

#[cfg(test)]
mod tests;
