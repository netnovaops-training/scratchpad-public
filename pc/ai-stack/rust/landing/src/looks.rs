//! Where somebody's own choice of start-page look is stored.
//!
//! A SEPARATE FILE FROM people.json, AND THAT IS THE WHOLE POINT.
//!
//! people.json holds who may reach what. The landing service mounts it
//! READ-ONLY on purpose: landing is the container an attacker actually
//! reaches, and it should have no way to change anybody's access even if it is
//! compromised. Letting people pick their own theme means landing has to write
//! something, and writing it into people.json would have handed that same
//! process the ability to rewrite the permission list.
//!
//! So the cosmetic choice lives here instead, in its own file that landing owns
//! read-write. The worst a compromised landing can now do is change what
//! colour somebody's page is.
//!
//! WHICH WINS. A person's own choice beats the default set for them in
//! /admin/people. If they have never chosen, the admin's setting stands. That
//! ordering matters: an admin picking a look for somebody is a suggestion, and
//! the person looking at the page every day should be able to overrule it.
//!
//! The file is a flat map of email -> theme key. It is not authoritative about
//! anything: an address in here that no longer has an account is ignored, and
//! a theme key that no longer ships falls back to plain.

use std::collections::HashMap;
use std::path::Path;

/// Read the map. A missing or unreadable file is normal -- it does not exist
/// until the first person picks something -- and yields an empty map rather
/// than an error, because nobody's page should fail to load over a wallpaper.
pub fn read(path: &Path) -> HashMap<String, String> {
    match std::fs::read_to_string(path) {
        Ok(text) => serde_json::from_str(&text).unwrap_or_else(|e| {
            tracing::warn!("looks file at {} is not valid JSON ({e}); ignoring it", path.display());
            HashMap::new()
        }),
        Err(_) => HashMap::new(),
    }
}

/// Write one person's choice.
///
/// Whole-file rewrite through a temporary file and a rename, so a crash midway
/// leaves the old map intact rather than a half-written one. At household size
/// the cost of rewriting the whole thing is nothing, and it removes a class of
/// corruption entirely.
pub fn set(path: &Path, email: &str, theme: &str) -> std::io::Result<()> {
    let mut map = read(path);
    if theme.is_empty() {
        // An empty choice means "go back to whatever the admin set", which is
        // removing the override rather than storing a blank one.
        map.remove(email);
    } else {
        map.insert(email.to_string(), theme.to_string());
    }

    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let tmp = path.with_extension("json.tmp");
    let json = serde_json::to_string_pretty(&map)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    std::fs::write(&tmp, json)?;
    std::fs::rename(&tmp, path)
}
