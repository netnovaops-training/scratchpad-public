//! Share links: one script, read-only, no sign-in, 30 days, revocable.
//!
//! Stored in /data/shares.json (token -> script id). A link names a script by an unguessable
//! token, never by path, and it is checked against the CURRENT scan every time it is opened: if
//! the script is gone, renamed or now mentions Garden/Robin, the link stops working.
//!
//! Before a link is made, the text is checked for things that must not leave the house
//! (`blockers`). These are the same kinds of finding the 2026-10-14 scratchpad classification
//! listed (personal email, home network address, PC paths, test passwords) plus key formats.
//! A script with any finding cannot be shared; it can still be viewed by the owner.

use std::path::{Path, PathBuf};

use rand::rand_core::UnwrapErr;
use rand::rngs::SysRng;
use rand::Rng;
use serde::{Deserialize, Serialize};

pub const DAYS: i64 = 30;

#[derive(Clone, Serialize, Deserialize)]
pub struct Share {
    pub token: String,
    pub id: String,
    pub created: i64,
    pub expires: i64,
}

pub fn path(data: &Path) -> PathBuf {
    data.join("shares.json")
}

pub fn load(data: &Path) -> Vec<Share> {
    std::fs::read_to_string(path(data))
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

/// Write to a temp file then rename, so a crash mid-write can't leave a half file.
pub fn save(data: &Path, list: &[Share]) -> std::io::Result<()> {
    let tmp = data.join("shares.json.tmp");
    std::fs::write(&tmp, serde_json::to_vec_pretty(list).unwrap_or_default())?;
    std::fs::rename(tmp, path(data))
}

pub fn token() -> String {
    let mut b = [0u8; 24];
    UnwrapErr(SysRng).fill_bytes(&mut b);
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// Reasons this text must not be shared publicly. Empty = OK to share.
pub fn blockers(text: &str) -> Vec<&'static str> {
    let mut why = Vec::new();
    let lower = text.to_lowercase();
    if lower.contains("-----begin") && lower.contains("private key") {
        why.push("a private key");
    }
    let token_prefixes = ["ghp_", "github_pat_", "glpat-", "sk-ant-", "sk_live_", "akia", "xoxb-",
                          "xoxp-", "pst_", "aiza", "hf_"];
    if text.split(|c: char| !(c.is_ascii_alphanumeric() || c == '_' || c == '-')).any(|w| {
        let wl = w.to_lowercase();
        w.len() >= 20 && token_prefixes.iter().any(|p| wl.starts_with(p))
    }) {
        why.push("an API key or token");
    }
    if has_email(text) {
        why.push("an email address");
    }
    if ["192.168.", "10.0.0.", "100.84.", "100.64.", "172.16.", "172.17.", "172.18.", "172.20."]
        .iter()
        .any(|k| text.contains(k))
    {
        why.push("a home network address");
    }
    if ["c:\\users\\", "c:/users/", "/home/user", "/home/user", "USER", "jasons new site", "d:\\secrets"]
        .iter()
        .any(|k| lower.contains(k))
    {
        why.push("a private file path");
    }
    if hardcoded_secret(&lower) {
        why.push("a password or key written into the code");
    }
    why
}

fn has_email(text: &str) -> bool {
    text.split(|c: char| c.is_whitespace() || "\"'<>(),;`".contains(c)).any(|w| {
        let w = w.trim_matches(|c: char| !c.is_ascii_alphanumeric());
        match w.split_once('@') {
            Some((a, b)) => !a.is_empty() && b.contains('.') && !b.starts_with('.')
                && b.chars().all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-')
                && !b.ends_with("example.com") && !b.ends_with("example.org")
                && !b.ends_with("invalid"),
            None => false,
        }
    })
}

/// `password = "something"` style assignments with a literal value (not env lookups).
fn hardcoded_secret(lower: &str) -> bool {
    let keys = ["password", "passwd", "secret", "api_key", "apikey", "token"];
    for line in lower.lines() {
        for k in keys {
            if let Some(i) = line.find(k) {
                let after = line[i + k.len()..].trim_start();
                let after = after.trim_start_matches(|c: char| c == '_' || c.is_ascii_alphanumeric()).trim_start();
                if let Some(rest) = after.strip_prefix('=').or_else(|| after.strip_prefix(':')) {
                    let v = rest.trim_start();
                    if let Some(q) = v.chars().next().filter(|c| *c == '"' || *c == '\'') {
                        let inner: String = v[1..].chars().take_while(|c| *c != q).collect();
                        if inner.len() >= 6 && !inner.contains('{') && !inner.contains('$')
                            && !inner.contains("example") && !inner.contains("changeme")
                        {
                            return true;
                        }
                    }
                }
            }
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clean_text_passes() {
        assert!(blockers("import os\nKEY = os.environ['API_KEY']\nprint('hi')\n").is_empty());
    }

    #[test]
    fn findings() {
        assert!(blockers("x = 'ghp_abcdefghijklmnopqrstuvwxyz0123'").contains(&"an API key or token"));
        assert!(blockers("mail me at user@example.com").contains(&"an email address"));
        assert!(blockers("ssh server@192.168.1.86").contains(&"a home network address"));
        assert!(blockers(r"C:\Users\USER\Documents").contains(&"a private file path"));
        assert!(blockers("password = \"hunter22\"").contains(&"a password or key written into the code"));
        assert!(blockers("password = os.environ['PW']").is_empty());
    }
}
