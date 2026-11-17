//! Finding the scripts and their documentation.
//!
//! Everything under SCRATCHPAD_ROOT (default /src) is read-only input. Its first-level folders are
//! the sources (`pc`, `nuc`); a script's id is its path below the root, e.g.
//! `pc/ai-stack-scripts/sync_documents.py`. Ids are only ever looked up in the scanned list, never
//! joined onto a path from user input, so a crafted id cannot reach any other file.
//!
//! Left out on purpose (Jason, 2026-11-12):
//!   * anything that mentions Garden for All or Robin, in its path OR its text ("leave that untouched");
//!   * file names that look like secrets (.env, token, password, key files);
//!   * binaries and files over 512 KB.
//! Documentation shown for a script = its own header comment/docstring, plus the AI-written doc
//! from ai-stack/docs/code when the PC sync copied one (pc/_docs/<source>/<path>.md).

use std::path::{Path, PathBuf};

const EXTS: &[(&str, &str)] = &[
    ("py", "Python"), ("sh", "Shell"), ("bash", "Shell"), ("ps1", "PowerShell"),
    ("psm1", "PowerShell"), ("cmd", "Batch"), ("bat", "Batch"), ("rs", "Rust"),
    ("js", "JavaScript"), ("mjs", "JavaScript"), ("ts", "TypeScript"), ("sql", "SQL"),
    ("vbs", "VBScript"), ("pl", "Perl"), ("rb", "Ruby"), ("go", "Go"), ("lua", "Lua"),
];
const MAX_BYTES: u64 = 512 * 1024;

#[derive(Clone)]
pub struct Script {
    pub id: String,
    pub lang: &'static str,
    pub doc: String,
    pub notes: Option<String>,
    pub text: String,
    pub lines: usize,
    pub modified: i64,
}

/// True when text mentions Garden for All / Robin's Garden / Robin. Word check for "robin"/"juns" so
/// "june" or "junk" do not count. (No regex crate in the workspace; this is enough.)
pub fn mentions_garden(s: &str) -> bool {
    let l = s.to_lowercase();
    if ["garden", "gfa-", "for-robin", "robin-", "jun_"].iter().any(|k| l.contains(k)) {
        return true;
    }
    l.split(|c: char| !c.is_ascii_alphanumeric())
        .any(|w| w == "robin" || w == "juns")
}

fn secret_name(name: &str) -> bool {
    let l = name.to_lowercase();
    ["token", "secret", "password", "passwd", "credential", ".env", "id_rsa", "id_ed25519"]
        .iter()
        .any(|k| l.contains(k))
}

fn lang_of(path: &Path) -> Option<&'static str> {
    let ext = path.extension()?.to_str()?.to_lowercase();
    EXTS.iter().find(|(e, _)| *e == ext).map(|(_, l)| *l)
}

pub fn scan(root: &Path) -> Vec<Script> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(rd) = std::fs::read_dir(&dir) else { continue };
        for e in rd.flatten() {
            let p = e.path();
            let name = e.file_name().to_string_lossy().to_string();
            let Ok(ft) = e.file_type() else { continue };
            if ft.is_dir() {
                let skip = name.starts_with('.') || name == "_docs" || name == "node_modules"
                    || name == "__pycache__" || name == "target" || mentions_garden(&name);
                if !skip {
                    stack.push(p);
                }
                continue;
            }
            if let Some(s) = load(root, &p, &name) {
                out.push(s);
            }
        }
    }
    out.sort_by(|a, b| a.id.to_lowercase().cmp(&b.id.to_lowercase()));
    out
}

fn load(root: &Path, p: &PathBuf, name: &str) -> Option<Script> {
    let lang = lang_of(p)?;
    if secret_name(name) {
        return None;
    }
    let meta = std::fs::metadata(p).ok()?;
    if meta.len() > MAX_BYTES || meta.len() == 0 {
        return None;
    }
    let bytes = std::fs::read(p).ok()?;
    if bytes.contains(&0) {
        return None;
    }
    let text = String::from_utf8_lossy(&bytes).replace("\r\n", "\n");
    let id = p.strip_prefix(root).ok()?.to_string_lossy().replace('\\', "/");
    if mentions_garden(&id) || mentions_garden(&text) {
        return None;
    }
    let notes = companion_doc(root, &id).filter(|n| !mentions_garden(n));
    let modified = meta
        .modified()
        .ok()
        .and_then(|m| m.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    Some(Script {
        doc: header_doc(&text, lang),
        lines: text.lines().count(),
        id,
        lang,
        notes,
        text,
        modified,
    })
}

/// pc/<source>/<rel> -> pc/_docs/<source>/<rel>.md, written by scripts/sync_scratchpad.py.
fn companion_doc(root: &Path, id: &str) -> Option<String> {
    let mut parts = id.splitn(3, '/');
    let (host, src, rel) = (parts.next()?, parts.next()?, parts.next()?);
    let p = root.join(host).join("_docs").join(src).join(format!("{rel}.md"));
    std::fs::read_to_string(p).ok()
}

/// The script's own opening documentation: a docstring, or the comment block at the top.
pub fn header_doc(text: &str, lang: &str) -> String {
    let mut lines = text.lines().peekable();
    // skip shebang, encoding line, @echo off, blank lines
    while let Some(l) = lines.peek() {
        let t = l.trim();
        if t.is_empty() || t.starts_with("#!") || t.starts_with("# -*-") || t.eq_ignore_ascii_case("@echo off") {
            lines.next();
        } else {
            break;
        }
    }
    let rest: Vec<&str> = lines.collect();
    let first = rest.first().map(|l| l.trim()).unwrap_or("");
    let mut doc = Vec::new();
    // block docstrings / comments
    for (open, close) in [("\"\"\"", "\"\"\""), ("'''", "'''"), ("<#", "#>"), ("/*", "*/")] {
        if first.starts_with(open) {
            let mut body = first[open.len()..].to_string();
            if let Some(i) = body.find(close) {
                body.truncate(i);
                return clean(vec![body]);
            }
            doc.push(body);
            for l in rest.iter().skip(1) {
                if let Some(i) = l.find(close) {
                    doc.push(l[..i].to_string());
                    return clean(doc);
                }
                doc.push(l.to_string());
                if doc.len() > 60 {
                    break;
                }
            }
            return clean(doc);
        }
    }
    let prefixes: &[&str] = match lang {
        "Rust" => &["//!", "///", "//"],
        "JavaScript" | "TypeScript" | "Go" => &["//"],
        "Batch" => &["rem ", "rem", "::"],
        "SQL" | "Lua" => &["--"],
        "VBScript" => &["'"],
        _ => &["#"],
    };
    for l in rest {
        let t = l.trim_start();
        let lower = t.to_lowercase();
        match prefixes.iter().find(|p| lower.starts_with(*p)) {
            Some(p) => doc.push(t[p.len()..].to_string()),
            None => break,
        }
        if doc.len() > 60 {
            break;
        }
    }
    clean(doc)
}

fn clean(lines: Vec<String>) -> String {
    let joined: Vec<String> = lines
        .iter()
        .map(|l| l.strip_prefix(' ').unwrap_or(l).trim_end().to_string())
        .collect();
    joined.join("\n").trim_matches('\n').trim().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn garden_words() {
        assert!(mentions_garden("Robin's Garden"));
        assert!(mentions_garden("export-for-robin.sh"));
        assert!(mentions_garden("a note for Robin today"));
        assert!(!mentions_garden("June report, junk drawer"));
    }

    #[test]
    fn python_docstring() {
        let d = header_doc("#!/usr/bin/env python3\n\"\"\"Sync docs.\n\nMore.\"\"\"\nimport os\n", "Python");
        assert_eq!(d, "Sync docs.\n\nMore.");
    }

    #[test]
    fn shell_comments() {
        let d = header_doc("#!/bin/bash\n# Pull Drive.\n# Daily.\nset -e\n", "Shell");
        assert_eq!(d, "Pull Drive.\nDaily.");
    }

    #[test]
    fn batch_rem() {
        let d = header_doc("@echo off\nREM Start it all.\ncd x\n", "Batch");
        assert_eq!(d, "Start it all.");
    }
}
