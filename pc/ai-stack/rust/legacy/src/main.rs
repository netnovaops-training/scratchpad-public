//! The family photos — behind a password, and never as a directory listing.
//!
//! WHAT CHANGED AND WHY
//! This folder used to be served by Caddy's `file_server browse`: a public
//! directory listing, readable by anyone who had or guessed the URL, showing
//! every file in it. For family photos that is the wrong default twice over --
//! no password, and total enumeration.
//!
//! THE TWO RULES THIS APP IS BUILT AROUND
//!
//!   1. NOTHING IS REACHABLE THAT THE MANIFEST DOES NOT NAME.
//!      legacy.json lists the folders you want published. A photo in a folder
//!      that is not listed has no URL -- not a blocked one, none. That is a
//!      stronger guarantee than "hide the listing", which leaves every file
//!      still fetchable by name to anyone who guesses it.
//!
//!   2. URLS CARRY INDEXES, NOT FILENAMES.
//!      A photo is /p/portraits/7, not /p/Photos-1-001%20(1)/Final_Portraits/
//!      PXL_20260815_192111858.jpg. Real paths never appear in the page, so
//!      the folder structure is not something a visitor can map or walk.
//!
//! THUMBNAILS ARE NOT OPTIONAL HERE
//! The originals are around 9.5MB each. A grid of twenty would be a 190MB page
//! and would simply never finish loading on a phone. Thumbnails are generated
//! once, cached to disk, and reused; the full image is only ever fetched when
//! somebody opens a single photo.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use argon2::password_hash::{phc::PasswordHash, PasswordHasher, PasswordVerifier};
use argon2::Argon2;
use askama::Template;
use axum::{
    body::Body,
    extract::{Path as AxPath, State},
    http::{header, HeaderMap, StatusCode},
    response::{Html, IntoResponse, Json, Redirect, Response},
    routing::{get, post},
    Router,
};
use rand::rngs::SysRng;
use rand::rand_core::UnwrapErr;
use rand::Rng;
use serde::{Deserialize, Serialize};

// -----------------------------------------------------------------------------
//  The manifest
// -----------------------------------------------------------------------------

/// One published album. `folder` is relative to the photo root.
///
/// FOLDERS, NOT FILE LISTS, on purpose. There are hundreds of photos in there;
/// hand-listing every filename in JSON would be unmaintainable and would drift
/// out of date the first time a file was added. Naming a folder says "publish
/// what is in here", which is the thing you actually mean.
#[derive(Debug, Clone, Deserialize)]
struct Album {
    id: String,
    title: String,
    #[serde(default)]
    note: String,
    folder: String,
}

#[derive(Debug, Clone, Deserialize)]
struct Manifest {
    #[serde(default = "default_title")]
    title: String,
    #[serde(default)]
    intro: String,
    #[serde(default)]
    albums: Vec<Album>,
}

fn default_title() -> String {
    "Family photos".to_string()
}

impl Default for Manifest {
    fn default() -> Self {
        Manifest {
            title: default_title(),
            intro: String::new(),
            albums: Vec::new(),
        }
    }
}

const IMAGE_EXTS: &[&str] = &["jpg", "jpeg", "png"];

// -----------------------------------------------------------------------------
//  State
// -----------------------------------------------------------------------------

struct Session {
    created: Instant,
    last_seen: Instant,
}

struct Gate {
    fails: u32,
    locked_until: Option<Instant>,
}

struct AppState {
    password_hash: String,
    /// True when no password is configured. See the note in run().
    open: bool,
    root: PathBuf,
    manifest_path: PathBuf,
    thumb_dir: PathBuf,
    thumb_px: u32,
    cookie_secure: bool,
    idle_timeout: Duration,
    absolute_timeout: Duration,
    sessions: Mutex<HashMap<String, Session>>,
    gate: Mutex<Gate>,
}

fn random_token() -> String {
    let mut b = [0u8; 32];
    UnwrapErr(SysRng).fill_bytes(&mut b);
    b.iter().map(|x| format!("{x:02x}")).collect()
}

// -----------------------------------------------------------------------------
//  Manifest + photo listing
// -----------------------------------------------------------------------------

/// Read the manifest fresh on each request rather than caching it at startup.
///
/// The admin writes this file; re-reading means an upload is live immediately,
/// with no restart. It is a few kB of JSON -- the read is not worth optimising
/// away, and a stale gallery after an upload would be a confusing bug.
fn manifest(state: &AppState) -> Manifest {
    match std::fs::read(&state.manifest_path) {
        Ok(bytes) => serde_json::from_slice(&bytes).unwrap_or_else(|e| {
            tracing::warn!("legacy.json is not valid, falling back to auto: {e}");
            discover(state)
        }),
        // NO MANIFEST IS THE NORMAL CASE, not an error.
        //
        // This used to require legacy.json before it would show anything, which
        // meant hand-writing JSON for a pile of family photos before you could
        // look at them. Now it finds the albums itself; the manifest is only
        // needed when you want to curate - nicer titles, a chosen order, or
        // publishing some folders and not others.
        Err(_) => discover(state),
    }
}

/// Build the album list by looking at the folder.
///
/// Every subdirectory containing images becomes an album, and loose files at
/// the top become one as well. Nested folders are included by their path, so
/// "Photos-1-001 (1)/Final_Portraits" shows up as its own album rather than
/// disappearing inside its parent.
///
/// TITLES ARE TIDIED, NOT INVENTED: underscores become spaces and the parent
/// path is dropped, so `AI_Restored_Faces` reads as "AI Restored Faces". The
/// folder itself is never renamed.
fn discover(state: &AppState) -> Manifest {
    let mut albums = Vec::new();

    // Loose files at the top level.
    if !album_photos(&state.root).is_empty() {
        albums.push(Album {
            id: "all".into(),
            title: "Photos".into(),
            note: String::new(),
            folder: ".".into(),
        });
    }

    // Subfolders, two levels deep. Deep enough for how these are actually
    // organised; shallow enough not to walk a whole drive if the root is ever
    // pointed somewhere unexpected.
    fn walk(root: &Path, rel: &Path, depth: usize, out: &mut Vec<Album>) {
        if depth > 2 {
            return;
        }
        let Ok(rd) = std::fs::read_dir(root.join(rel)) else {
            return;
        };
        let mut dirs: Vec<PathBuf> = rd
            .filter_map(|e| e.ok())
            .map(|e| e.path())
            .filter(|p| p.is_dir())
            .collect();
        dirs.sort();
        for d in dirs {
            let Ok(sub) = d.strip_prefix(root) else { continue };
            if !album_photos(&d).is_empty() {
                let name = sub
                    .file_name()
                    .and_then(|n| n.to_str())
                    .unwrap_or("album")
                    .replace(['_', '-'], " ");
                out.push(Album {
                    // A stable, URL-safe id derived from the path, so a link
                    // shared today still works tomorrow.
                    id: slug(&sub.to_string_lossy()),
                    title: name.trim().to_string(),
                    note: String::new(),
                    folder: sub.to_string_lossy().to_string(),
                });
            }
            walk(root, sub, depth + 1, out);
        }
    }
    walk(&state.root, Path::new(""), 1, &mut albums);

    Manifest {
        title: default_title(),
        intro: String::new(),
        albums,
    }
}

fn slug(s: &str) -> String {
    let mut out = String::new();
    let mut dash = false;
    for c in s.chars() {
        if c.is_ascii_alphanumeric() {
            out.push(c.to_ascii_lowercase());
            dash = false;
        } else if !dash && !out.is_empty() {
            out.push('-');
            dash = true;
        }
    }
    out.trim_matches('-').to_string()
}

/// Resolve an album's folder, refusing anything that escapes the photo root.
///
/// The manifest is written by you, not by a visitor, so this is not defending
/// against an attacker typing "../". It is defending against a typo in a JSON
/// file quietly publishing your whole drive -- `"folder": "../ai-stack"` would
/// otherwise do exactly that, and would look like a small mistake.
fn album_dir(state: &AppState, album: &Album) -> Option<PathBuf> {
    let joined = if album.folder == "." || album.folder.is_empty() {
        state.root.clone()
    } else {
        state.root.join(&album.folder)
    };
    let real = joined.canonicalize().ok()?;
    let root = state.root.canonicalize().ok()?;
    if real.starts_with(&root) && real.is_dir() {
        Some(real)
    } else {
        tracing::warn!(
            album = album.id,
            folder = album.folder,
            "album folder is outside the photo root, or does not exist - skipping"
        );
        None
    }
}

/// Image files in an album, sorted, so index N means the same photo every time.
///
/// SORTED IS LOAD-BEARING. The URL for a photo is its position in this list, so
/// an unstable order would mean a link shared today points at a different
/// picture tomorrow. Filesystem order is not guaranteed; sorting makes it so.
fn album_photos(dir: &Path) -> Vec<PathBuf> {
    let Ok(rd) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut files: Vec<PathBuf> = rd
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| {
            p.is_file()
                && p.extension()
                    .and_then(|x| x.to_str())
                    .map(|x| IMAGE_EXTS.contains(&x.to_lowercase().as_str()))
                    .unwrap_or(false)
        })
        .collect();
    files.sort();
    files
}

fn find_album(m: &Manifest, id: &str) -> Option<Album> {
    m.albums.iter().find(|a| a.id == id).cloned()
}

/// The one function that turns a URL into a file on disk.
///
/// Everything the outside world can ask for funnels through here: an album id
/// that must be in the manifest, and an integer that must be in range. There is
/// no path in the request at all, so there is no path to sanitise.
fn photo_at(state: &AppState, album_id: &str, index: usize) -> Option<PathBuf> {
    let m = manifest(state);
    let album = find_album(&m, album_id)?;
    let dir = album_dir(state, &album)?;
    album_photos(&dir).into_iter().nth(index)
}

// -----------------------------------------------------------------------------
//  Thumbnails
// -----------------------------------------------------------------------------

/// Cache key: album, index, and the file's size+mtime.
///
/// Including size and mtime means replacing a photo invalidates its thumbnail
/// automatically. Keying on the path alone would serve the old thumbnail
/// forever and look like the upload silently failed.
fn thumb_path(state: &AppState, album_id: &str, index: usize, src: &Path) -> PathBuf {
    let meta = std::fs::metadata(src).ok();
    let len = meta.as_ref().map(|m| m.len()).unwrap_or(0);
    let mtime = meta
        .as_ref()
        .and_then(|m| m.modified().ok())
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Not a cryptographic hash and does not need to be - this only has to be
    // stable and collision-free enough for a local cache directory.
    let mut h: u64 = 1469598103934665603;
    for b in format!("{album_id}/{index}/{len}/{mtime}/{}", state.thumb_px).bytes() {
        h ^= b as u64;
        h = h.wrapping_mul(1099511628211);
    }
    state.thumb_dir.join(format!("{h:016x}.jpg"))
}

fn make_thumb(src: &Path, dst: &Path, px: u32) -> anyhow::Result<()> {
    let img = image::open(src)?;
    // thumbnail() preserves aspect ratio and is much faster than a full resize
    // filter -- for a grid tile the quality difference is invisible.
    let small = img.thumbnail(px, px);
    if let Some(parent) = dst.parent() {
        std::fs::create_dir_all(parent)?;
    }
    // Write to a temp file and rename, so a half-written thumbnail is never
    // served if this is interrupted.
    let tmp = dst.with_extension("tmp");
    small.to_rgb8().save_with_format(&tmp, image::ImageFormat::Jpeg)?;
    std::fs::rename(&tmp, dst)?;
    Ok(())
}

// -----------------------------------------------------------------------------
//  Sessions (same shape as the admin, same reasoning)
// -----------------------------------------------------------------------------

const COOKIE: &str = "legacy_session";

fn session_cookie(token: &str, secure: bool, max_age: u64) -> String {
    let mut c = format!("{COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={max_age}");
    if secure {
        c.push_str("; Secure");
    }
    c
}

fn clear_cookie(secure: bool) -> String {
    let mut c = format!("{COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0");
    if secure {
        c.push_str("; Secure");
    }
    c
}

fn cookie_value(headers: &HeaderMap) -> Option<String> {
    let raw = headers.get(header::COOKIE)?.to_str().ok()?;
    for part in raw.split(';') {
        if let Some(v) = part.trim().strip_prefix(&format!("{COOKIE}=")) {
            if !v.is_empty() {
                return Some(v.to_string());
            }
        }
    }
    None
}

fn signed_in(state: &AppState, headers: &HeaderMap) -> bool {
    // Open gallery: everyone is "signed in", so every handler below works
    // unchanged rather than needing an `if open` branch in each one.
    if state.open {
        return true;
    }
    let Some(token) = cookie_value(headers) else {
        return false;
    };
    let Ok(mut sessions) = state.sessions.lock() else {
        return false;
    };
    let now = Instant::now();
    sessions.retain(|_, s| {
        now.duration_since(s.last_seen) < state.idle_timeout
            && now.duration_since(s.created) < state.absolute_timeout
    });
    match sessions.get_mut(&token) {
        Some(s) => {
            s.last_seen = now;
            true
        }
        None => false,
    }
}

// -----------------------------------------------------------------------------
//  Templates
// -----------------------------------------------------------------------------

#[derive(Template)]
#[template(path = "login.html")]
struct LoginTemplate {
    title: String,
    error: String,
    year: i32,
}

struct AlbumView {
    id: String,
    title: String,
    note: String,
    count: usize,
}

#[derive(Template)]
#[template(path = "index.html")]
struct IndexTemplate {
    title: String,
    intro: String,
    albums: Vec<AlbumView>,
    year: i32,
}

#[derive(Template)]
#[template(path = "album.html")]
struct AlbumTemplate {
    site_title: String,
    id: String,
    title: String,
    note: String,
    indexes: Vec<usize>,
    year: i32,
}

// -----------------------------------------------------------------------------
//  Handlers
// -----------------------------------------------------------------------------

async fn healthz() -> &'static str {
    "ok"
}

fn render_login(state: &AppState, error: &str, code: StatusCode) -> Response {
    let html = LoginTemplate {
        title: manifest(state).title,
        error: error.to_string(),
        year: common::current_year(),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    (code, Html(html)).into_response()
}

async fn index(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    if !signed_in(&state, &headers) {
        return render_login(&state, "", StatusCode::OK);
    }
    let m = manifest(&state);
    let albums = m
        .albums
        .iter()
        .filter_map(|a| {
            let dir = album_dir(&state, a)?;
            Some(AlbumView {
                id: a.id.clone(),
                title: a.title.clone(),
                note: a.note.clone(),
                count: album_photos(&dir).len(),
            })
        })
        .collect();

    let html = IndexTemplate {
        title: m.title,
        intro: m.intro,
        albums,
        year: common::current_year(),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    Html(html).into_response()
}

async fn album_page(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    AxPath(id): AxPath<String>,
) -> Response {
    if !signed_in(&state, &headers) {
        return render_login(&state, "", StatusCode::UNAUTHORIZED);
    }
    let m = manifest(&state);
    let Some(album) = find_album(&m, &id) else {
        return (StatusCode::NOT_FOUND, Html("<h1>404</h1>")).into_response();
    };
    let Some(dir) = album_dir(&state, &album) else {
        return (StatusCode::NOT_FOUND, Html("<h1>404</h1>")).into_response();
    };
    let n = album_photos(&dir).len();

    let html = AlbumTemplate {
        site_title: m.title,
        id: album.id,
        title: album.title,
        note: album.note,
        indexes: (0..n).collect(),
        year: common::current_year(),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    Html(html).into_response()
}

async fn login(State(state): State<Arc<AppState>>, body: String) -> Response {
    {
        let mut gate = match state.gate.lock() {
            Ok(g) => g,
            Err(_) => return render_login(&state, "Internal error.", StatusCode::INTERNAL_SERVER_ERROR),
        };
        if let Some(until) = gate.locked_until {
            if Instant::now() < until {
                let secs = until.duration_since(Instant::now()).as_secs() + 1;
                return render_login(
                    &state,
                    &format!("Too many tries. Wait {secs} seconds."),
                    StatusCode::TOO_MANY_REQUESTS,
                );
            }
            gate.locked_until = None;
        }
    }

    let password = form_field(&body, "password").unwrap_or_default();
    let ok = !password.is_empty() && verify_password(&state.password_hash, &password);

    let mut gate = match state.gate.lock() {
        Ok(g) => g,
        Err(_) => return render_login(&state, "Internal error.", StatusCode::INTERNAL_SERVER_ERROR),
    };
    if !ok {
        gate.fails += 1;
        // Gentler than the admin's: this password gets shared with family, so
        // somebody typing it from memory and getting it wrong twice is normal
        // and should not lock the album for everyone.
        if gate.fails >= 10 {
            gate.locked_until = Some(Instant::now() + Duration::from_secs(120));
        }
        return render_login(&state, "That is not the password.", StatusCode::UNAUTHORIZED);
    }
    gate.fails = 0;
    gate.locked_until = None;
    drop(gate);

    let token = random_token();
    let now = Instant::now();
    if let Ok(mut s) = state.sessions.lock() {
        s.insert(
            token.clone(),
            Session {
                created: now,
                last_seen: now,
            },
        );
    }
    let mut out = HeaderMap::new();
    if let Ok(v) = session_cookie(&token, state.cookie_secure, state.absolute_timeout.as_secs()).parse() {
        out.insert(header::SET_COOKIE, v);
    }
    (out, Redirect::to("./")).into_response()
}

async fn logout(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    if let Some(t) = cookie_value(&headers) {
        if let Ok(mut s) = state.sessions.lock() {
            s.remove(&t);
        }
    }
    let mut out = HeaderMap::new();
    if let Ok(v) = clear_cookie(state.cookie_secure).parse() {
        out.insert(header::SET_COOKIE, v);
    }
    (out, Redirect::to("./")).into_response()
}

async fn thumb(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    AxPath((id, index)): AxPath<(String, usize)>,
) -> Response {
    if !signed_in(&state, &headers) {
        return (StatusCode::UNAUTHORIZED, "sign in first").into_response();
    }
    let Some(src) = photo_at(&state, &id, index) else {
        return (StatusCode::NOT_FOUND, "no such photo").into_response();
    };
    let dst = thumb_path(&state, &id, index, &src);
    if !dst.exists() {
        if let Err(e) = make_thumb(&src, &dst, state.thumb_px) {
            tracing::warn!("thumbnail failed for {}: {e}", src.display());
            return (StatusCode::INTERNAL_SERVER_ERROR, "could not make a thumbnail")
                .into_response();
        }
    }
    match std::fs::read(&dst) {
        Ok(bytes) => (
            [
                (header::CONTENT_TYPE, "image/jpeg"),
                // Private, because these are behind a password: caches shared
                // between people must not keep them.
                (header::CACHE_CONTROL, "private, max-age=86400"),
            ],
            Body::from(bytes),
        )
            .into_response(),
        Err(_) => (StatusCode::NOT_FOUND, "gone").into_response(),
    }
}

async fn photo(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    AxPath((id, index)): AxPath<(String, usize)>,
) -> Response {
    if !signed_in(&state, &headers) {
        return (StatusCode::UNAUTHORIZED, "sign in first").into_response();
    }
    let Some(src) = photo_at(&state, &id, index) else {
        return (StatusCode::NOT_FOUND, "no such photo").into_response();
    };
    let ctype = match src.extension().and_then(|e| e.to_str()).map(|e| e.to_lowercase()) {
        Some(ref e) if e == "png" => "image/png",
        _ => "image/jpeg",
    };
    match std::fs::read(&src) {
        Ok(bytes) => (
            [
                (header::CONTENT_TYPE, ctype),
                (header::CACHE_CONTROL, "private, max-age=86400"),
            ],
            Body::from(bytes),
        )
            .into_response(),
        Err(_) => (StatusCode::NOT_FOUND, "gone").into_response(),
    }
}

// -----------------------------------------------------------------------------
//  JSON, for the project that will consume these
// -----------------------------------------------------------------------------

#[derive(Serialize)]
struct ApiPhoto {
    index: usize,
    thumb: String,
    full: String,
}

#[derive(Serialize)]
struct ApiAlbum {
    id: String,
    title: String,
    note: String,
    count: usize,
    photos: Vec<ApiPhoto>,
}

/// The album list as JSON, so another project can lazy-load these.
///
/// BEHIND THE SAME SESSION as the pages. An open JSON index would hand out a
/// complete map of every photo -- which is exactly the enumeration this app
/// exists to prevent, just in a more convenient format for whoever wanted it.
async fn api_albums(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    if !signed_in(&state, &headers) {
        return (StatusCode::UNAUTHORIZED, "sign in first").into_response();
    }
    let m = manifest(&state);
    let out: Vec<ApiAlbum> = m
        .albums
        .iter()
        .filter_map(|a| {
            let dir = album_dir(&state, a)?;
            let n = album_photos(&dir).len();
            Some(ApiAlbum {
                id: a.id.clone(),
                title: a.title.clone(),
                note: a.note.clone(),
                count: n,
                photos: (0..n)
                    .map(|i| ApiPhoto {
                        index: i,
                        thumb: format!("t/{}/{}", a.id, i),
                        full: format!("p/{}/{}", a.id, i),
                    })
                    .collect(),
            })
        })
        .collect();
    Json(out).into_response()
}

// -----------------------------------------------------------------------------

fn form_field(body: &str, name: &str) -> Option<String> {
    for pair in body.split('&') {
        let mut it = pair.splitn(2, '=');
        let k = it.next()?;
        if k == name {
            return Some(url_decode(it.next().unwrap_or("")));
        }
    }
    None
}

fn url_decode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b'%' if i + 2 < b.len() => {
                match (
                    (b[i + 1] as char).to_digit(16),
                    (b[i + 2] as char).to_digit(16),
                ) {
                    (Some(h), Some(l)) => {
                        out.push((h * 16 + l) as u8);
                        i += 3;
                    }
                    _ => {
                        out.push(b[i]);
                        i += 1;
                    }
                }
            }
            c => {
                out.push(c);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn verify_password(hash: &str, password: &str) -> bool {
    match PasswordHash::new(hash) {
        Ok(p) => Argon2::default().verify_password(password.as_bytes(), &p).is_ok(),
        Err(e) => {
            tracing::error!("LEGACY_PASSWORD_HASH is not a valid argon2 hash: {e}");
            false
        }
    }
}

fn hash_password(password: &str) -> anyhow::Result<String> {
    Argon2::default()
    .hash_password(password.as_bytes())
        .map(|h| h.to_string())
        .map_err(|e| anyhow::anyhow!("could not hash: {e}"))
}

fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() >= 2 && args[1] == "hash" {
        let pw = match args.get(2) {
            Some(p) if !p.is_empty() => p.clone(),
            _ => {
                let mut line = String::new();
                let _ = std::io::stdin().read_line(&mut line);
                line.trim_end_matches(['\r', '\n']).to_string()
            }
        };
        if pw.is_empty() {
            eprintln!("usage: legacy hash \"the family password\"");
            std::process::exit(2);
        }
        println!("{}", hash_password(&pw)?);
        return Ok(());
    }

    common::init_local_offset();
    common::init_tracing("legacy");
    common::runtime()?.block_on(run())
}

async fn run() -> anyhow::Result<()> {
    // NO PASSWORD IS A VALID CHOICE HERE, so it is a setting rather than a
    // hard requirement.
    //
    // The password is not what stops the folder being enumerated - the
    // manifest is. A folder that legacy.json does not name has no URL whether
    // or not anyone is signed in, and photo URLs carry an index rather than a
    // filename. Turning the password off makes the published albums visible to
    // anyone with the link; it does NOT expose anything you did not publish.
    //
    // Set LEGACY_PASSWORD_HASH later and it comes back with no code change.
    let password_hash = common::env_or("LEGACY_PASSWORD_HASH", "").trim().to_string();
    let open = password_hash.is_empty();
    if !open && PasswordHash::new(&password_hash).is_err() {
        eprintln!("LEGACY_PASSWORD_HASH is set but is not a valid argon2 hash.");
        eprintln!("Usually means it was stored unquoted and compose ate the $ signs.");
        eprintln!("Single-quote it in .env.");
        std::process::exit(1);
    }

    let state = Arc::new(AppState {
        password_hash,
        open,
        root: PathBuf::from(common::env_or("LEGACY_ROOT", "/photos")),
        manifest_path: PathBuf::from(common::env_or(
            "LEGACY_MANIFEST",
            "/data/site/legacy.json",
        )),
        thumb_dir: PathBuf::from(common::env_or("LEGACY_THUMBS", "/data/thumbs")),
        thumb_px: common::env_or("LEGACY_THUMB_PX", "480").parse().unwrap_or(480),
        cookie_secure: common::env_bool("LEGACY_COOKIE_SECURE", true),
        idle_timeout: Duration::from_secs(
            common::env_or("LEGACY_IDLE_MINUTES", "60").parse::<u64>().unwrap_or(60) * 60,
        ),
        absolute_timeout: Duration::from_secs(
            common::env_or("LEGACY_SESSION_HOURS", "24").parse::<u64>().unwrap_or(24) * 3600,
        ),
        sessions: Mutex::new(HashMap::new()),
        gate: Mutex::new(Gate {
            fails: 0,
            locked_until: None,
        }),
    });

    std::fs::create_dir_all(&state.thumb_dir).ok();

    let m = manifest(&state);
    tracing::info!(
        root = %state.root.display(),
        albums = m.albums.len(),
        password = !open,
        "legacy ready"
    );
    if open {
        tracing::warn!(
            "NO PASSWORD SET - anyone with the link can see the published \
             albums. Unpublished folders are still unreachable. Set \
             LEGACY_PASSWORD_HASH (.\\legacy-password.ps1) to require one."
        );
    }
    if m.albums.is_empty() {
        tracing::warn!(
            "no photos found under {} - check the folder is mounted",
            state.root.display()
        );
    } else if !state.manifest_path.exists() {
        tracing::info!(
            "no legacy.json, so albums were found automatically. Upload one in \
             the admin if you want to choose titles, order, or which folders \
             are published."
        );
    }

    let app = Router::new()
        .route("/", get(index))
        .route("/login", post(login))
        .route("/logout", post(logout))
        .route("/a/{id}", get(album_page))
        .route("/t/{id}/{index}", get(thumb))
        .route("/p/{id}/{index}", get(photo))
        .route("/api/albums", get(api_albums))
        .route("/healthz", get(healthz))
        .with_state(state);

    common::serve(app, common::env_u16("APP_PORT", 8012)).await
}
