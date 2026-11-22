//! The admin — upload JSON data files without rebuilding anything.
//!
//! WHAT IT IS FOR
//! Ms Dee's location data, and whatever `/mama` and `/casey` need, live in JSON
//! files that those apps read. This lets you replace one of those files from a
//! browser: you build the JSON however you like (an API, a script, by hand),
//! upload it here, and the site is using it seconds later. No rebuild, no
//! `docker compose up`, no editing files over the device bridge.
//!
//! WHY IT IS ITS OWN CONTAINER RATHER THAN A ROUTE ON `landing`
//! This is the only thing in the stack that can WRITE site data. Keeping it
//! separate means the public landing page — the thing scanners actually hit —
//! has no write capability at all, not even a disabled one. If landing is ever
//! compromised, it still cannot change what Ms Dee's site says.
//!
//! HOW IT IS PROTECTED, AND WHAT THAT IS AND IS NOT WORTH
//!   * A password, stored as an argon2 hash. The .env holds the hash, never
//!     the password.
//!   * Failed attempts are counted and lock the door with growing delays.
//!   * Sessions expire twice over: on idle, and absolutely.
//!   * SameSite=Strict cookies plus a CSRF token on every write.
//!   * Uploads may only land on an allowlisted filename. There is no path the
//!     browser can influence, so `../../.env` is not expressible.
//!   * Every overwrite keeps a timestamped backup first.
//!
//! THE HONEST LIMITATION: the lockout is GLOBAL, not per-visitor.
//! Docker Desktop on Windows NATs every connection, so the real client IP never
//! reaches this process — the Caddyfile documents this and warns against
//! building anything that depends on it. Per-IP limiting would therefore be a
//! lie: it would count every attacker on earth as the same visitor anyway.
//! Global counting is the honest version of the same protection, and the cost
//! is real: someone hammering the login can lock YOU out for up to 15 minutes.
//! Restarting the container clears it instantly. For a one-person admin that
//! trade is worth it; for a multi-user system it would not be.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use hmac::{Hmac, KeyInit, Mac};
use sha2::Sha256;

use argon2::password_hash::{phc::PasswordHash, PasswordHasher, PasswordVerifier};
use argon2::Argon2;
use askama::Template;
use axum::{
    body::Body,
    extract::{Multipart, State},
    http::{header, HeaderMap, StatusCode},
    response::{Html, IntoResponse, Redirect, Response},
    routing::{get, post},
    Router,
};
use rand::rngs::SysRng;
use rand::rand_core::UnwrapErr;
use rand::Rng;

// -----------------------------------------------------------------------------
//  What may be uploaded, and where it lands.
//
//  A FIXED ALLOWLIST, on purpose. The browser sends a *key* from this table,
//  never a filename and never a path. That means directory traversal is not
//  something this code has to defend against by sanitising — it is simply not
//  expressible. `../../../ai-stack/.env` is not a key in this list, so it
//  cannot be selected, so no amount of cleverness in the request reaches it.
//
//  Adding a new data file is a one-line change here plus a rebuild, which is
//  the right amount of friction for "this process may now overwrite that".
// -----------------------------------------------------------------------------
struct Target {
    key: &'static str,
    file: &'static str,
    label: &'static str,
    sub: &'static str,
    hint: &'static str,
    accent: &'static str,
    icon: &'static str,

    // -------------------------------------------------------------------------
    //  IS THERE A BETTER TOOL THAN THIS PAGE?
    //
    //  For most of these files, uploading JSON is the whole job. For two of
    //  them it is now the WORST way in: the knowledge base has a page that
    //  takes a link, reads it with a local model and writes the article for
    //  you, and hand-editing that file instead is doing by hand what is
    //  already automatic.
    //
    //  This page cannot know that on its own, and somebody looking for the
    //  importer will keep arriving HERE -- because this is where the data
    //  lives and that is a perfectly reasonable place to look. So when a
    //  better tool exists, this page says so, at the top, before the upload
    //  box. Empty means there is no better tool and the page is the answer.
    tool_href: &'static str,
    tool_label: &'static str,
    tool_why: &'static str,

    // -------------------------------------------------------------------------
    //  SLIPSTREAM: is this file a list that grows?
    //
    //  Two of these files are directories that get one more entry every few
    //  weeks - a new place, a fix you just worked out. Replacing the whole file
    //  is the wrong shape for those: it means the only way to add one entry is
    //  to hold the entire current contents somewhere and send it back with the
    //  entry appended, and the day that copy is a week stale, everything added
    //  in that week is gone with no error and nothing on screen to notice.
    //
    //  So a list file takes a PATCH instead. You name what to add and what to
    //  remove; everything you did not name is left exactly as it was.
    //
    //  `list_key` empty means the old behaviour: what you send becomes the
    //  file. That is still right for the files you rewrite in one go.
    // -------------------------------------------------------------------------
    /// The top-level key holding the list, e.g. `articles`. Empty = replace-only.
    list_key: &'static str,
    /// The field an entry's id is derived from when it carries no `id` of its
    /// own - `name` for places, `title` for the knowledge base.
    name_key: &'static str,
    /// What one entry is called on screen. Only used for wording.
    noun: &'static str,
}

// Icons are inline SVG path data, hand-written and trusted, rendered
// unescaped. Same approach and same reasoning as the landing page.
const I_TEA: &str = r#"<path d="M4 8h13v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z"/><path d="M17 9h2a2 2 0 0 1 0 4h-2"/><path d="M7 3v2M10 3v2M13 3v2"/>"#;
const I_HELP: &str = r#"<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.2 2.4c-.6.2-1 .8-1 1.4v.4"/><circle cx="12" cy="17" r=".6" fill="currentColor"/>"#;
const I_PHOTOS: &str = r#"<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8.5" cy="10" r="1.5"/><path d="m21 16-5-5-6 6"/>"#;
const I_CHART: &str = r#"<path d="M3 3v18h18"/><path d="M7 15l3-4 3 3 5-7"/>"#;
const I_CODE: &str = r#"<path d="m9 8-5 4 5 4"/><path d="m15 8 5 4-5 4"/>"#;
const I_PIN: &str = r#"<path d="M12 21s7-6.2 7-11a7 7 0 1 0-14 0c0 4.8 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/>"#;
const I_FIXED: &str = r#"<path d="M4 5h11l5 5v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"/><path d="M14 5v5h6"/><path d="m7.5 15 2 2 4.5-4.5"/>"#;

// -----------------------------------------------------------------------------
//  ONE ENTRY PER SITE FOLDER under www, plus the shared places directory.
//  ai-stack is deliberately absent: it is the code that runs everything, not a
//  site with content to upload, and it holds .env. It must never be a
//  destination anything here can write to.
// -----------------------------------------------------------------------------
const TARGETS: &[Target] = &[
    Target {
        key: "booking",
        file: "booking.json",
        label: "Tea with Ms Dee",
        sub: "Booking",
        hint: "Location, service area, hours and rates. Read by the booking site and by the phone line when it answers.",
        accent: "#e8a35a",
        icon: I_TEA,
        list_key: "",
        name_key: "",
        noun: "",
        tool_href: "",
        tool_label: "",
        tool_why: "",
    },
    Target {
        key: "mom",
        file: "mom.json",
        label: "Mama",
        sub: "Tech & cyber help",
        hint: "Content and settings for /mama.",
        accent: "#4fc79b",
        icon: I_HELP,
        list_key: "",
        name_key: "",
        noun: "",
        tool_href: "/mama/kb",
        tool_label: "Mama's guides",
        tool_why: "The published guides as she sees them, and the page that imports new ones from a link.",
    },
    Target {
        key: "mom-kb",
        file: "mom-kb.json",
        label: "Known fixes",
        sub: "Mama's knowledge base",
        hint: "Problems already solved. When one matches what she asks, /mama answers straight from here - instantly, in your words, without waking the desktop or asking a model anything.",
        accent: "#7fb4f5",
        icon: I_FIXED,
        // kb.py reads `articles`, and falls back to a bare list. Writing the
        // object form keeps room for top-level notes without breaking it.
        list_key: "articles",
        name_key: "title",
        noun: "article",
        tool_href: "/mama/kb/manage",
        tool_label: "Import a page instead",
        tool_why: "Paste a link and one of your own models reads the page and writes the article for you -- symptoms in her words, numbered steps, and pictures you pick off the page. It lands as a draft only you can see. Solved tickets arrive there the same way.",
    },
    Target {
        key: "legacy",
        file: "legacy.json",
        label: "Legacy",
        sub: "Family photos",
        hint: "Which photo folders are published. A folder not listed here has no URL at all.",
        accent: "#d98ab0",
        icon: I_PHOTOS,
        list_key: "",
        name_key: "",
        noun: "",
        tool_href: "",
        tool_label: "",
        tool_why: "",
    },
    Target {
        key: "dashboard",
        file: "dashboard.json",
        label: "Dashboard",
        sub: "Everything under /dashboard",
        hint: "Panels, sources and layout for the dashboard.",
        accent: "#8ad9c7",
        icon: I_CHART,
        list_key: "",
        name_key: "",
        noun: "",
        tool_href: "",
        tool_label: "",
        tool_why: "",
    },
    Target {
        key: "jason",
        file: "jason.json",
        label: "Jason",
        sub: "Projects & lab",
        hint: "Project list and write-ups for /jason.",
        accent: "#c78af0",
        icon: I_CODE,
        list_key: "",
        name_key: "",
        noun: "",
        tool_href: "",
        tool_label: "",
        tool_why: "",
    },
    Target {
        key: "places",
        file: "places.json",
        label: "Places",
        sub: "Shared directory",
        hint: "Somewhere-to-be listings: names, addresses, phones, hours. Added to from a browser, on top of the researched directory in places.yaml.",
        accent: "#e8695a",
        icon: I_PIN,
        list_key: "places",
        name_key: "name",
        noun: "place",
        tool_href: "",
        tool_label: "",
        tool_why: "",
    },
];

fn target_for(key: &str) -> Option<&'static Target> {
    TARGETS.iter().find(|t| t.key == key)
}

// -----------------------------------------------------------------------------
//  Where this app is mounted, and why every link is absolute
//
//  THE BUG THIS EXISTS TO KILL. Caddy strips the prefix before proxying, so the
//  app sees "/people" while the browser sees "/admin/people". Relative links
//  looked like the tidy answer to that -- the app need not know where it lives.
//  They are not, and the failure is nasty:
//
//      GET  /admin/people          form action "people/add"  -> /admin/people/add   OK
//      POST /admin/people/add      renders the page again, browser URL is now
//                                  /admin/people/add
//      the SAME form's action "people/password" now resolves against
//      /admin/people/  ->          /admin/people/people/password        404
//
//  So the first submit works and the second 404s, which reads as "special
//  characters broke it" rather than "the URL doubled". Two fixes, both applied,
//  because either alone leaves a way back in:
//
//    1. Every link and form action is ABSOLUTE, built from this prefix. There
//       is no relative resolution left to get wrong.
//    2. Every POST redirects to its page instead of rendering it (below), so
//       the browser's URL is always the canonical one and a refresh does not
//       re-submit the form.
//
//  The prefix comes from Caddy (X-Forwarded-Prefix) so that changing ADMIN_PATH
//  moves the app without the app being rebuilt. The env var is the fallback for
//  running it directly, and "/admin" the fallback for that.
// -----------------------------------------------------------------------------

fn base_path(headers: &HeaderMap) -> String {
    let raw = headers
        .get("X-Forwarded-Prefix")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string())
        .unwrap_or_else(|| common::env_or("ADMIN_PATH", "/admin"));
    let trimmed = raw.trim().trim_end_matches('/');
    if trimmed.is_empty() {
        String::new()
    } else if trimmed.starts_with('/') {
        trimmed.to_string()
    } else {
        format!("/{trimmed}")
    }
}

// -----------------------------------------------------------------------------
//  Sessions
// -----------------------------------------------------------------------------

struct Session {
    csrf: String,
    created: Instant,
    last_seen: Instant,
}

/// The failed-login gate. See the honest limitation in the module docs above.
struct Gate {
    fails: u32,
    locked_until: Option<Instant>,
}

impl Gate {
    /// How long the door stays shut after `fails` failures.
    ///
    /// Nothing happens for the first few — fat fingers are not an attack. After
    /// that it doubles, because the only thing that makes a slow guess
    /// worthwhile is being allowed to try often.
    fn lockout(fails: u32) -> Option<Duration> {
        match fails {
            0..=4 => None,
            5..=6 => Some(Duration::from_secs(60)),
            7..=8 => Some(Duration::from_secs(300)),
            _ => Some(Duration::from_secs(900)),
        }
    }
}

struct AppState {
    password_hash: String,
    /// The sign-in allowlist, shared with the auth service through www\Admin.
    ///
    /// The auth service re-reads this on EVERY check, so adding or removing
    /// somebody here takes effect on their next request - including cutting
    /// off a six-month session that is already live. That is what makes a long
    /// session safe to hand out in the first place.
    people_file: PathBuf,
    /// Invite codes and who has redeemed one. Lives beside people.json in the
    /// auth service's data directory, and is read fresh on every request for
    /// the same reason people.json is: switching a code off has to take effect
    /// on the next redemption, not on the next restart.
    trials_file: PathBuf,
    /// Guest invites this console has minted -- see common::guest_invite.
    /// Lives beside people.json/trials.json in the auth service's data dir.
    guest_invites_file: PathBuf,
    /// The auth service's own passkeys.json. Admin does not know that
    /// file's Rust type (it lives in the auth binary and pulls in
    /// webauthn-rs, which this binary has no reason to depend on) -- see
    /// `patch_guest_entry` below for why this is read and written as plain
    /// JSON instead, touching only the handful of top-level fields an
    /// invite needs to set.
    auth_passkeys_file: PathBuf,
    /// The public base URL guests' invite links are built from, e.g.
    /// https://example.org/auth -- same idea as auth's own
    /// AUTH_PASSKEY_LINK_BASE.
    invite_link_base: String,
    /// The auth service's revocation list: email -> "everything issued for
    /// this person before T is void". Written from here so that signing
    /// somebody out does not need an API between the two containers -- auth
    /// re-reads this file on every token check, exactly as it re-reads
    /// people.json.
    auth_revoked_file: PathBuf,
    data_dir: PathBuf,
    backup_dir: PathBuf,
    cookie_secure: bool,
    idle_timeout: Duration,
    absolute_timeout: Duration,
    max_upload: usize,
    sessions: Mutex<HashMap<String, Session>>,
    /// Signs the session cookie. See "The signed session cookie" below.
    ///
    /// IT IS THE PASSWORD HASH, not a separate secret, and that is the whole
    /// trick. The hash is already secret, already stable across restarts,
    /// already in .env, and already the thing that changes when you change the
    /// admin password -- so using it as the signing key means a password
    /// change invalidates every outstanding cookie for free, and there is no
    /// second secret to generate, store, or forget to set.
    secret: Vec<u8>,
    /// One unix timestamp. Any cookie issued before it is refused.
    ///
    /// This is what makes Sign out mean something once sessions live in the
    /// cookie rather than in memory: dropping the map entry no longer helps,
    /// because the cookie can rebuild it. Writing the current time here does.
    revoke_file: PathBuf,
    gate: Mutex<Gate>,
    /// One pending message per session, for Post/Redirect/Get. See set_flash.
    flash: Mutex<HashMap<String, (String, bool)>>,
    /// YOUR OWN addresses. Protected from the two mistakes that are easy to
    /// make on a page full of Remove buttons and impossible to undo from a
    /// phone: removing yourself, and being locked out of your own sites.
    owners: Vec<String>,

    // -------------------------------------------------------------------------
    //  The picture backlog page -- see PicturesTemplate / pictures_page below.
    //  Talks to Kitchen and kb over the internal Docker network (their
    //  container hostname, never the public domain) and to Ollama/imagine
    //  for the GPU control.
    // -------------------------------------------------------------------------
    http: reqwest::Client,
    kitchen_url: String,
    kitchen_secret: String,
    kb_url: String,
    kb_secret: String,
    ollama_url: String,
    imagine_url: String,
    /// Where approved items are recorded, so they stay off the list once
    /// you're done with them. Same shape as everything else here: a plain
    /// JSON file under data_dir, read fresh on every load.
    approved_file: PathBuf,
}

fn is_owner(state: &AppState, email: &str) -> bool {
    let e = common::people::normalise(email);
    state.owners.iter().any(|o| o == &e)
}


// -----------------------------------------------------------------------------
//  Flash messages
//
//  Post/Redirect/Get needs somewhere to put "that worked" between the POST and
//  the GET it redirects to. Kept in memory, keyed by session token, and taken
//  out on read - so it shows exactly once and never survives a restart, which
//  is the correct lifetime for a sentence about something that just happened.
//
//  NOT a query parameter. The message names files and email addresses, and a
//  URL is the one place text reliably ends up in a log, a bookmark and a
//  referrer header.
// -----------------------------------------------------------------------------

/// Leave a message and send the browser to the page that shows it.
///
/// This is the Post/Redirect/Get half of the fix described at base_path: the
/// browser ends up on the canonical URL, so the next form on that page resolves
/// against the right thing, and a refresh re-runs a GET rather than re-posting
/// the form.
/// Same shape as render_people, but flashes and redirects instead.
/// Keeping the argument order identical is what made converting every call
/// site a search-and-replace rather than a chance to get one of them wrong.
fn flash_people(state: &AppState, base: &str, token: &str, msg: &str, ok: bool) -> Response {
    flash_to(state, token, msg, ok, &format!("{base}/people"))
}

fn flash_site(state: &AppState, base: &str, token: &str, t: &Target, msg: &str, ok: bool) -> Response {
    flash_to(state, token, msg, ok, &format!("{base}/site/{}", t.key))
}

/// Minimal percent-encoding for one query-string value -- no `url`/
/// `percent-encoding` crate in this binary's dependency tree, and this is
/// the only place that needs it (building a redirect URL server-side;
/// every other query param here comes from a browser's own form GET,
/// which encodes itself).
fn url_encode_query(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.as_bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => out.push(*b as char),
            _ => out.push_str(&format!("%{:02X}", b)),
        }
    }
    out
}

fn flash_to(state: &AppState, token: &str, msg: &str, ok: bool, to: &str) -> Response {
    set_flash(state, token, msg, ok);
    Redirect::to(to).into_response()
}

fn set_flash(state: &AppState, token: &str, msg: &str, ok: bool) {
    if let Ok(mut f) = state.flash.lock() {
        f.insert(token.to_string(), (msg.to_string(), ok));
    }
}

fn take_flash(state: &AppState, token: &str) -> (String, bool) {
    state
        .flash
        .lock()
        .ok()
        .and_then(|mut f| f.remove(token))
        .unwrap_or_else(|| (String::new(), true))
}

fn random_token() -> String {
    let mut bytes = [0u8; 32];
    UnwrapErr(SysRng).fill_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

// -----------------------------------------------------------------------------
//  Templates
// -----------------------------------------------------------------------------

#[derive(Template)]
#[template(path = "login.html")]
struct LoginTemplate {
    base: String,

    error: String,
    year: i32,
}

struct TargetView {
    key: String,
    label: String,
    sub: String,
    hint: String,
    file: String,
    accent: String,
    icon: String,
    status: String,
    /// True when a file has actually been uploaded. The card shows a dot for
    /// this, so "which of these have I actually done" is answerable at a
    /// glance instead of by reading seven lines of text.
    has_file: bool,
    /// True when this file is a list you patch rather than a file you replace.
    patch: bool,
    /// "place" / "article". Empty when this is not a list.
    noun: String,
    /// A better tool than this page, when one exists. See the note on
    /// Target::tool_href. Empty means this page is the answer.
    tool_href: String,
    tool_label: String,
    tool_why: String,
}

/// One row in the "what is in the file now" list.
struct EntryView {
    id: String,
    name: String,
    note: String,
    direct: bool,
}

#[derive(Template)]
#[template(path = "dashboard.html")]
struct DashboardTemplate {
    /// People out of questions with no account yet -- the only thing on this
    /// dashboard that is time-sensitive, so it gets a number rather than a
    /// link somebody has to think to click.
    trials_waiting: usize,
    trial_codes: usize,
    base: String,

    targets: Vec<TargetView>,
    /// How many people can ACTUALLY sign in - an account with no password yet
    /// is not one of them. Counting all accounts here would put a reassuring
    /// number on the card for a state where nobody can get in, which is the
    /// one case the card exists to make visible.
    people: usize,
    /// Accounts still waiting for a password.
    people_pending: usize,
    /// How many items (recipes + kb guides, not yet approved) still need a
    /// picture -- see the "picture backlog" section further down.
    pictures_missing: usize,
    year: i32,
}

#[derive(Template)]
#[template(path = "people.html")]
struct PeopleTemplate {
    base: String,

    csrf: String,
    flash: String,
    flash_ok: bool,
    people: Vec<PersonView>,
    /// Every app, each starting at "none" -- the fresh "Which pages" control
    /// for the "Invite a guest" form, same AppCheck shape the per-person
    /// control already uses (so the same {{ a.name }}/{{ a.level }} template
    /// loop and the same apps_from_levels parser both work unchanged).
    fresh_app_checks: Vec<AppCheck>,
    /// The looks a start page can wear, from common::people::THEMES.
    themes: Vec<ThemeChoice>,
    /// The shortest password the form will take. Rendered rather than written
    /// into the HTML by hand, so the number on screen cannot disagree with the
    /// number enforced.
    min: usize,
    year: i32,
}

#[derive(Template)]
#[template(path = "trials.html")]
struct TrialsTemplate {
    base: String,
    csrf: String,
    flash: String,
    flash_ok: bool,
    /// How many people are out of questions and do not yet have an account --
    /// the number that answers "is there anything for me to do here".
    waiting: usize,
    codes: Vec<CodeView>,
    trials: Vec<TrialView>,
    /// Offered in the "new code" box so there is something to accept rather
    /// than something to invent. Regenerated per page load, and only used if
    /// the field is left as it is.
    suggested: String,
    default_quota: u32,
    year: i32,
}

struct CodeView {
    code: String,
    label: String,
    quota: u32,
    active: bool,
    max_uses: u32,
    /// How many people have redeemed it. The reason a code exists is to be
    /// handed out, so "has anyone used it" is the first thing to show.
    redeemed: usize,
}

struct TrialView {
    email: String,
    code: String,
    used: u32,
    quota: u32,
    remaining: u32,
    /// True once the questions are gone. Drives the row's emphasis, because
    /// this is the list Jason is actually looking at: who is waiting.
    finished: bool,
    /// Already has a household account, so the trial is moot. Shown rather
    /// than hidden -- a leftover row is how you notice you already let them
    /// in and forgot to say so.
    now_a_member: bool,
    started: String,
    last: String,
}

/// One row of the People page. Deliberately does NOT carry the hash: a template
/// cannot print what it was never given.
struct PersonView {
    email: String,
    has_password: bool,
    must_change: bool,
    /// One of your own addresses. Shown as a badge and refused a Remove button.
    owner: bool,
    /// One row per app on the toggle -- whether this person can currently
    /// reach it, all true when Person::apps was empty (unrestricted). Built
    /// here, not in the template, so the template stays a plain loop with no
    /// membership-testing logic of its own to get subtly wrong.
    app_checks: Vec<AppCheck>,
    /// Which look their start page currently wears, for preselecting the
    /// dropdown. Empty means the plain one.
    theme: String,
    /// What to call them on their own page; empty means the address stem.
    name: String,
    /// How many of app_checks are currently allowed, for the "(3/4)" count
    /// on the summary -- computed here for the same reason as app_checks.
    apps_allowed: usize,
    /// "never", or how long ago this person was last signed out of
    /// everything. Stated rather than left blank: "never" is a fact worth
    /// seeing next to a button that changes it.
    signed_out: String,
    /// Non-empty when they ALSO hold a trial. Somebody who redeemed a code
    /// before being given an account keeps that row, and without this it
    /// reads on the Trials page as a second identity rather than as a
    /// leftover their account already overrides.
    trial: String,
    /// From the auth service's own passkeys.json (not people.json -- see
    /// guest_emails below for why this is read separately). Used ONLY to
    /// hide the synthetic placeholder email on a guest's row in the
    /// template; it changes nothing about what that row can do.
    guest: bool,
    /// What to show as this person's identifier on the People page. For
    /// guests this is their label; for regular users it is p.name when
    /// set, else the stem of the email address (before '@'). The raw email
    /// is deliberately NOT shown in the accounts list -- only the owner
    /// sees their own email via the "you" badge.
    display_name: String,
    /// Registered passkeys pulled from the auth service's passkeys.json.
    /// Empty means no passkey yet (they cannot sign in with one).
    passkeys: Vec<KeyView>,
    /// "YYYY-MM-DD" when a guest account ends; empty for regular users.
    account_expires_label: String,
    /// "Invited YYYY-MM-DD[, redeemed YYYY-MM-DD]" for guest accounts;
    /// empty for regular users or when no matching invite record exists.
    invited_label: String,
}

/// Which people.json emails belong to a guest account, read once from the
/// auth service's passkeys.json rather than per-row -- see
/// patch_new_guest_entry's own doc comment for why this binary reads that
/// file as plain JSON instead of the typed UserKeys struct it has no
/// business depending on. Any problem reading the file means "no guests
/// found," which only means the email column is NOT hidden anywhere -- the
/// safe direction to fail, since this is cosmetic, not access control.
fn guest_emails(path: &std::path::Path) -> std::collections::HashSet<String> {
    let Ok(text) = std::fs::read_to_string(path) else {
        return std::collections::HashSet::new();
    };
    let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) else {
        return std::collections::HashSet::new();
    };
    v["users"]
        .as_array()
        .map(|users| {
            users
                .iter()
                .filter(|u| u["guest"].as_bool().unwrap_or(false))
                .filter_map(|u| u["email"].as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

/// Format a Unix timestamp as "YYYY-MM-DD". Returns empty string for 0.
/// Uses the `time` crate already in scope (workspace dep).
fn unix_date(ts: u64) -> String {
    if ts == 0 {
        return String::new();
    }
    use time::OffsetDateTime;
    let dt = OffsetDateTime::from_unix_timestamp(ts as i64)
        .unwrap_or(OffsetDateTime::UNIX_EPOCH);
    format!("{:04}-{:02}-{:02}", dt.year(), dt.month() as u8, dt.day())
}

/// Per-user passkey data read from the auth service's passkeys.json.
/// Same raw-JSON approach as guest_emails() -- admin has no business
/// depending on auth's typed structs, and a parse failure returns an
/// empty map (safe direction: missing passkey info is cosmetic, not
/// access-control).
fn passkey_info_map(path: &std::path::Path) -> HashMap<String, (Vec<KeyView>, u64)> {
    let Ok(text) = std::fs::read_to_string(path) else {
        return HashMap::new();
    };
    let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) else {
        return HashMap::new();
    };
    let Some(users) = v["users"].as_array() else {
        return HashMap::new();
    };
    let mut map = HashMap::new();
    for u in users {
        let Some(email) = u["email"].as_str() else { continue };
        let account_expires = u["account_expires"].as_u64().unwrap_or(0);
        let keys: Vec<KeyView> = u["keys"]
            .as_array()
            .map(|ks| {
                ks.iter()
                    .filter_map(|k| {
                        let id = k["id"].as_str()?.to_string();
                        let name = k["name"].as_str().unwrap_or("unnamed").to_string();
                        let expires = k["expires"].as_u64().unwrap_or(0);
                        Some(KeyView { id, name, expires_label: unix_date(expires) })
                    })
                    .collect()
            })
            .unwrap_or_default();
        map.insert(email.to_string(), (keys, account_expires));
    }
    map
}

/// Per-guest invite record from guest_invites.json, keyed by target_email.
/// Returns a formatted string like "Invited 2026-10-29" or
/// "Invited 2026-10-29, redeemed 2026-10-29". Empty map on any read error.
fn invite_info_map(path: &std::path::Path) -> HashMap<String, String> {
    let store = common::guest_invite::read(path);
    store
        .invites
        .iter()
        .map(|inv| {
            let mut label = format!("Invited {}", unix_date(inv.created));
            if inv.redeemed {
                label.push_str(&format!(", redeemed {}", unix_date(inv.redeemed_at)));
            }
            (inv.target_email.clone(), label)
        })
        .collect()
}

struct AppCheck {
    name: &'static str,
    allowed: bool,
    /// "none", "view" or "full" -- preselects the dropdown.
    level: String,
}

/// One option in the start-page look dropdown. Built here rather than in the
/// template so the template stays a plain loop -- same reasoning as AppCheck.
struct ThemeChoice {
    key: &'static str,
    label: &'static str,
    blurb: &'static str,
}

/// One passkey row on the People page -- id for the revoke form, display
/// name, and expiry label (empty for a lasting passkey, "YYYY-MM-DD" for a
/// loaner that will stop working on that date).
#[derive(Clone)]
struct KeyView {
    id: String,
    name: String,
    /// Empty string means the passkey never expires (lasting). "YYYY-MM-DD"
    /// means it is a loaner that stops working on that date.
    expires_label: String,
}

#[derive(Template)]
#[template(path = "site.html")]
struct SiteTemplate {
    base: String,

    csrf: String,
    flash: String,
    flash_ok: bool,
    t: TargetView,
    entries: Vec<EntryView>,
    backups: Vec<String>,
    year: i32,
}

/// One row on the picture backlog page.
struct PictureRowView {
    source: String,
    key: String,
    title: String,
    missing: u32,
    url: String,
}

/// One row on the ingredient-warnings panel of the same page -- Kitchen's
/// three-tier ingredient matcher (tandoor.py's resolve_ingredients())
/// landing here whenever it could not confidently place a line anywhere,
/// even after reading the recipe's own context. Jason's own words
/// (2026-08-24): "I don't want this link here [pointing out to Kitchen's
/// own /warnings page] I want to manage it in my admin panel" -- so this
/// is a real second panel, not a link out.
struct WarningRowView {
    id: String,
    title: String,
    line: String,
    why: String,
    url: String,
}

/// One row on the food/tool-merge panel -- Phi's tier-2 proposals from
/// Kitchen's dedupe.py, ones it was not mechanically certain enough about
/// to merge on its own. Jason's own words (2026-08-24): "conservative
/// auto-merge, review only the unsure ones" -- nothing here has touched
/// Tandoor yet; Approve is the first thing that actually merges anything.
/// `usage_url` points back into THIS admin app (not straight at Kitchen),
/// same reasoning as pictures.html's own links -- Jason's other explicit
/// ask this same conversation: "let me see the recipe where it comes
/// from... open up the actual recipe and read it for myself" before
/// deciding.
struct MergeRowView {
    id: String,
    kind: String,
    item_name: String,
    target_name: String,
    reason: String,
    usage_url: String,
}

/// One row on the nutrition-review panel -- usda_local.audit()'s output:
/// a food whose cached nutrition and the local USDA match disagree by
/// more than 2x. Jason's own request (2026-08-24), after seeing the
/// comparison: "both" -- auto-fix the obvious ones (done directly, see
/// TANDOOR_MIGRATION_STATUS.md), queue the rest here. NO auto-fix tier
/// survived in the end -- two rounds of it produced two different kinds
/// of wrong match (see usda_local.py's own comment), so everything past
/// the obvious cases lands here for a real decision.
struct NutritionRowView {
    id: String,
    name: String,
    cached_name: String,
    cached_cal: String,
    local_name: String,
    local_cal: String,
}

#[derive(Template)]
#[template(path = "pictures.html")]
struct PicturesTemplate {
    base: String,

    csrf: String,
    flash: String,
    flash_ok: bool,
    rows: Vec<PictureRowView>,
    total: usize,
    warning_rows: Vec<WarningRowView>,
    warning_total: usize,
    merge_rows: Vec<MergeRowView>,
    merge_total: usize,
    nutrition_rows: Vec<NutritionRowView>,
    nutrition_total: usize,
    /// What Ollama currently has loaded, as "name (N.N GB)" -- already
    /// formatted here so the template stays a plain loop.
    loaded_models: Vec<String>,
    gpu_reachable: bool,
    gpu_room: bool,
    gpu_free_mb: i64,
    gpu_need_mb: i64,
    year: i32,
}

// -----------------------------------------------------------------------------
//  Cookie helpers
// -----------------------------------------------------------------------------

const COOKIE: &str = "admin_session";

fn session_cookie(token: &str, secure: bool, max_age: u64) -> String {
    // HttpOnly  - JavaScript cannot read it, so an XSS bug cannot steal it.
    // SameSite=Strict - the browser refuses to send this cookie on a request
    //                   started by another site, which is what stops CSRF at
    //                   the door. The CSRF token below is the second lock.
    // Secure    - never sent over plain http. Off only for local testing.
    let mut c = format!("{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}");
    if secure {
        c.push_str("; Secure");
    }
    c
}

fn clear_cookie(secure: bool) -> String {
    let mut c = format!("{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0");
    if secure {
        c.push_str("; Secure");
    }
    c
}

// -----------------------------------------------------------------------------
//  The signed session cookie
//
//  WHY THIS EXISTS
//  ---------------
//  Sessions used to live only in a HashMap in this process. That is fine until
//  the process stops, and this process stops often: every `docker compose
//  build admin`, every `update.cmd`, every restart of the stack. Each one
//  silently signed you out of the admin, which is the page you were most
//  likely in the middle of using when you rebuilt something.
//
//  Now the cookie carries enough to rebuild the session, signed so it cannot
//  be forged. The map is still there and still does the work -- it is what
//  enforces the idle timeout -- but it is now a CACHE of something the cookie
//  can reconstruct, rather than the only copy.
//
//  WHAT IS IN IT:   <hex(issued|csrf)>.<hmac>
//
//  The CSRF token rides inside the signed payload rather than being looked up
//  server-side, so a rehydrated session has the same CSRF token it had before
//  the restart -- otherwise every form open in a tab would fail its check
//  after a rebuild, which is a confusing way to be told the server bounced.
//
//  THREE THINGS END A SESSION, and all three survive a restart:
//    * the absolute timeout, from `issued` in the payload
//    * changing the admin password, because the hash IS the signing key
//    * Sign out, via the revoke file -- see revoked_at() below
//
//  The idle timeout is the one thing that does NOT survive, by construction:
//  it lives in the map, so a restart resets everyone to "just seen". That is
//  the honest trade for not writing session state to disk, and it is the
//  weakest of the four because the absolute timeout still caps the session at
//  ADMIN_SESSION_HOURS regardless.
// -----------------------------------------------------------------------------

type HmacSha256 = Hmac<Sha256>;

fn unix_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn unhex(s: &str) -> Option<Vec<u8>> {
    if s.len() % 2 != 0 {
        return None;
    }
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).ok())
        .collect()
}

/// Compare without leaking where two strings first differ.
fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

fn sign(state: &AppState, payload: &str) -> String {
    let mut mac = HmacSha256::new_from_slice(&state.secret).expect("hmac accepts any key length");
    mac.update(payload.as_bytes());
    hex(&mac.finalize().into_bytes())
}

fn mint_token(state: &AppState, csrf: &str) -> String {
    let payload = hex(format!("{}|{}", unix_now(), csrf).as_bytes());
    let sig = sign(state, &payload);
    format!("{payload}.{sig}")
}

/// (issued, csrf) if the cookie is genuine, unexpired and not signed out.
fn read_token(state: &AppState, raw: &str) -> Option<(u64, String)> {
    let (payload, sig) = raw.split_once('.')?;

    // Verify BEFORE parsing. Anything else means acting on attacker-controlled
    // bytes, however briefly.
    let expected = sign(state, payload);
    if !constant_time_eq(expected.as_bytes(), sig.as_bytes()) {
        return None;
    }

    let decoded = String::from_utf8(unhex(payload)?).ok()?;
    let (issued, csrf) = decoded.split_once('|')?;
    let issued: u64 = issued.parse().ok()?;

    let now = unix_now();
    if now.saturating_sub(issued) >= state.absolute_timeout.as_secs() {
        return None;
    }
    if issued < revoked_at(state) {
        return None;
    }
    Some((issued, csrf.to_string()))
}

/// The moment Sign out was last pressed. 0 if it never has been.
///
/// A whole file for one number, and it is worth it. Without it, Sign out can
/// only forget the session on THIS side -- and the cookie in the browser can
/// rebuild it on the next request, so the button would do nothing at all. A
/// missing or unreadable file reads as 0, which fails OPEN. That is the right
/// way round here: the alternative locks you out of your own admin because a
/// file went missing, and Sign out on a single-person admin is a convenience
/// rather than the thing standing between an attacker and the door.
fn revoked_at(state: &AppState) -> u64 {
    std::fs::read_to_string(&state.revoke_file)
        .ok()
        .and_then(|s| s.trim().parse().ok())
        .unwrap_or(0)
}

fn revoke_all(state: &AppState) {
    if let Some(dir) = state.revoke_file.parent() {
        std::fs::create_dir_all(dir).ok();
    }
    if let Err(e) = std::fs::write(&state.revoke_file, unix_now().to_string()) {
        // Not fatal, but say so loudly: it means Sign out did not stick, and
        // silently pretending it did would be worse than a log line.
        tracing::warn!(error = %e, path = %state.revoke_file.display(),
                       "admin: could not write the revoke file - sign out will not hold");
    }
}

fn cookie_value(headers: &HeaderMap) -> Option<String> {
    let raw = headers.get(header::COOKIE)?.to_str().ok()?;
    for part in raw.split(';') {
        let part = part.trim();
        if let Some(v) = part.strip_prefix(&format!("{COOKIE}=")) {
            if !v.is_empty() {
                return Some(v.to_string());
            }
        }
    }
    None
}

/// Returns the session's CSRF token if the request carries a live session.
///
/// Also does the expiry sweep: an expired session is removed here rather than
/// left to linger, so "logged out" is a fact about the server's memory and not
/// just about what the browser was told.
/// The session's token AND its CSRF value.
///
/// The token is what the flash is filed under, so a redirect can find the
/// message the POST left for it.
fn touch_session_full(state: &AppState, headers: &HeaderMap) -> Option<(String, String)> {
    let token = cookie_value(headers)?;
    let csrf = touch_session(state, headers)?;
    Some((token, csrf))
}

fn touch_session(state: &AppState, headers: &HeaderMap) -> Option<String> {
    let token = cookie_value(headers)?;

    // THE COOKIE IS CHECKED FIRST, ALWAYS.
    //
    // It would be faster to look in the map and only verify on a miss, and it
    // would also be wrong: a signed-out or expired cookie whose entry happens
    // to still be in the map would be honoured. The signature, the absolute
    // timeout and the sign-out check are the real gate; the map only adds the
    // idle timeout on top.
    let (issued, csrf) = read_token(state, &token)?;

    let mut sessions = state.sessions.lock().ok()?;
    let now = Instant::now();

    // Sweep everything that has aged out, not only the one being looked up.
    sessions.retain(|_, s| {
        now.duration_since(s.last_seen) < state.idle_timeout
            && now.duration_since(s.created) < state.absolute_timeout
    });

    if let Some(s) = sessions.get_mut(&token) {
        s.last_seen = now;
        return Some(s.csrf.clone());
    }

    // Not in the map, but the cookie is good: this process restarted under a
    // browser that was already signed in. Rebuild the entry rather than
    // sending them to a login page they do not need.
    //
    // `created` is wound back to when the cookie was actually issued, so the
    // absolute timeout counts from the real sign-in rather than restarting at
    // every rebuild -- otherwise ADMIN_SESSION_HOURS would be trivially
    // extended by anyone who could make this container restart.
    let age = Duration::from_secs(unix_now().saturating_sub(issued));
    sessions.insert(
        token.clone(),
        Session {
            csrf: csrf.clone(),
            created: now.checked_sub(age).unwrap_or(now),
            last_seen: now,
        },
    );
    Some(csrf)
}

// -----------------------------------------------------------------------------
//  Handlers
// -----------------------------------------------------------------------------

/// The endpoint Caddy's forward_auth calls before proxying /admin/tables.
///
/// WHY THE TABLES EDITOR HAS NO PASSWORD OF ITS OWN. It is a Streamlit app, and
/// giving it a second password would mean a second thing to get right, a second
/// thing to forget, and a second login page that looks nothing like this one.
/// Instead Caddy asks here first: 2xx and it proxies, anything else and the
/// visitor gets whatever this returns, which is a redirect to the admin's own
/// sign-in. So the argon2 password on this service is the only door, and the
/// editor cannot be reached without having already come through it.
///
/// Deliberately does NOT extend the session. `touch_session` does that as a
/// side effect, which is correct here - a person actively using the editor is
/// making these calls - but it means a browser tab left open on the editor
/// keeps the admin session alive. That is the same behaviour as leaving a tab
/// open on the admin itself, so it is at least not a surprise.
async fn verify(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    let base = base_path(&headers);
    match touch_session(&state, &headers) {
        Some(_) => (StatusCode::OK, "ok").into_response(),
        None => {
            // NOT the original URI (Caddy sends it as X-Forwarded-Uri): the
            // tables editor is a single-page app whose deep links are not
            // meaningful to return to, and the admin root is one click from
            // everything.
            Redirect::to(&format!("{base}/")).into_response()
        }
    }
}

async fn healthz() -> &'static str {
    "ok"
}

fn render_login(base: &str, error: &str, code: StatusCode) -> Response {
    let html = LoginTemplate {
        base: base.to_string(),
        error: error.to_string(),
        year: common::current_year(),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    (code, Html(html)).into_response()
}

fn view_of(state: &AppState, t: &'static Target) -> TargetView {
    let path = state.data_dir.join(t.file);
    // Say plainly whether the file is there, how big, and how old. "Not
    // uploaded yet" is a legitimate state, not an error - it means that site is
    // running on whatever defaults it ships with.
    let (status, has_file) = match std::fs::metadata(&path) {
        Ok(m) => {
            let kb = m.len() as f64 / 1024.0;
            let size = if m.len() < 1024 {
                format!("{} bytes", m.len())
            } else {
                format!("{kb:.1} KB")
            };
            (size, true)
        }
        Err(_) => ("not uploaded yet".to_string(), false),
    };
    TargetView {
        key: t.key.to_string(),
        label: t.label.to_string(),
        sub: t.sub.to_string(),
        hint: t.hint.to_string(),
        file: t.file.to_string(),
        accent: t.accent.to_string(),
        icon: t.icon.to_string(),
        status,
        has_file,
        patch: !t.list_key.is_empty(),
        tool_href: t.tool_href.into(),
            tool_label: t.tool_label.into(),
            tool_why: t.tool_why.into(),
            noun: t.noun.to_string(),
    }
}

/// How many items are missing a picture and not yet approved -- the
/// dashboard card's count. Best effort, same as the pictures page itself:
/// either service being unreachable just means a smaller number here, not a
/// broken dashboard.
async fn count_missing_pictures(state: &AppState) -> usize {
    let approved = read_approved(state);
    let (kitchen_rows, kb_rows) = tokio::join!(fetch_kitchen_missing(state), fetch_kb_missing(state));
    kitchen_rows
        .iter()
        .chain(kb_rows.iter())
        .filter(|r| !approved.contains(&r.key))
        .count()
}

async fn render_dashboard(state: &AppState, base: &str) -> Response {
    let targets = TARGETS.iter().map(|t| view_of(state, t)).collect();
    let html = DashboardTemplate {
        base: base.to_string(),
        targets,
        people: read_people(state).iter().filter(|p| p.has_password()).count(),
        people_pending: read_people(state).iter().filter(|p| !p.has_password()).count(),
        trials_waiting: {
            // Somebody out of questions who has since been given an account is
            // NOT waiting -- that is the case already dealt with, and counting
            // it would leave a badge that never clears.
            let store = read_trials(state);
            let people = read_people(state);
            store
                .trials
                .iter()
                .filter(|t| t.remaining() == 0 && common::people::find(&people, &t.email).is_none())
                .count()
        },
        trial_codes: read_trials(state).invites.iter().filter(|i| i.active).count(),
        pictures_missing: count_missing_pictures(state).await,
        year: common::current_year(),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    Html(html).into_response()
}

/// Backups for one target, newest first, as display strings.
fn backups_for(state: &AppState, t: &Target) -> Vec<String> {
    let Ok(entries) = std::fs::read_dir(&state.backup_dir) else {
        return Vec::new();
    };
    let mut names: Vec<String> = entries
        .filter_map(|e| e.ok())
        .filter_map(|e| e.file_name().into_string().ok())
        .filter(|n| n.ends_with(&format!("-{}", t.file)))
        .collect();
    names.sort();
    names.reverse();
    names
        .into_iter()
        .map(|n| {
            // 20260821-143355-casey.json -> "21 Aug 2026, 14:33"
            let stamp = n.split('-').next().unwrap_or("").to_string();
            let time = n.split('-').nth(1).unwrap_or("").to_string();
            if stamp.len() == 8 && time.len() == 6 {
                format!(
                    "{}-{}-{} at {}:{}",
                    &stamp[0..4],
                    &stamp[4..6],
                    &stamp[6..8],
                    &time[0..2],
                    &time[2..4]
                )
            } else {
                n
            }
        })
        .collect()
}

fn render_site(
    state: &AppState,
    base: &str,
    csrf: &str,
    t: &'static Target,
    flash: &str,
    ok: bool,
) -> Response {
    let html = SiteTemplate {
        base: base.to_string(),
        csrf: csrf.to_string(),
        flash: flash.to_string(),
        flash_ok: ok,
        t: view_of(state, t),
        entries: entries_for(state, t),
        backups: backups_for(state, t),
        year: common::current_year(),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    Html(html).into_response()
}

async fn index(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    let base = base_path(&headers);
    match touch_session(&state, &headers) {
        Some(_) => render_dashboard(&state, &base).await,
        None => render_login(&base, "", StatusCode::OK),
    }
}

async fn site(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    axum::extract::Path(key): axum::extract::Path<String>,
) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    let (flash, ok) = take_flash(&state, &token);
    match target_for(&key) {
        Some(t) => render_site(&state, &base, &csrf, t, &flash, ok),
        None => (StatusCode::NOT_FOUND, Html("<h1>404</h1><p>No such site.</p>")).into_response(),
    }
}

async fn login(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let base = base_path(&headers);
    // Check the gate BEFORE looking at the password. Verifying argon2 is
    // deliberately expensive; doing it while locked out would let someone
    // exhaust the CPU even though they can never succeed.
    {
        let mut gate = match state.gate.lock() {
            Ok(g) => g,
            Err(_) => return render_login(&base, "Internal error.", StatusCode::INTERNAL_SERVER_ERROR),
        };
        if let Some(until) = gate.locked_until {
            if Instant::now() < until {
                let secs = until.duration_since(Instant::now()).as_secs() + 1;
                return render_login(
                    &base,
                    &format!("Too many failed attempts. Try again in {secs} seconds."),
                    StatusCode::TOO_MANY_REQUESTS,
                );
            }
            gate.locked_until = None;
        }
    }

    let password = form_field(&body, "password").unwrap_or_default();
    if password.is_empty() {
        return render_login(&base, "Enter the password.", StatusCode::BAD_REQUEST);
    }

    let ok = verify_password(&state.password_hash, &password);

    let mut gate = match state.gate.lock() {
        Ok(g) => g,
        Err(_) => return render_login(&base, "Internal error.", StatusCode::INTERNAL_SERVER_ERROR),
    };

    if !ok {
        gate.fails += 1;
        if let Some(d) = Gate::lockout(gate.fails) {
            gate.locked_until = Some(Instant::now() + d);
        }
        tracing::warn!(fails = gate.fails, "admin: failed login");
        // Deliberately vague. "Wrong password" and "no such user" being
        // different messages is how someone learns which half they got right.
        return render_login(&base, "That did not work.", StatusCode::UNAUTHORIZED);
    }

    gate.fails = 0;
    gate.locked_until = None;
    drop(gate);

    let csrf = random_token();
    // The token is no longer random: it is a signed statement of when this
    // session began and what its CSRF value is. See "The signed session
    // cookie" above for why.
    let token = mint_token(&state, &csrf);
    let now = Instant::now();
    if let Ok(mut sessions) = state.sessions.lock() {
        sessions.insert(
            token.clone(),
            Session {
                csrf,
                created: now,
                last_seen: now,
            },
        );
    }
    tracing::info!("admin: signed in");

    let max_age = state.absolute_timeout.as_secs();
    let mut headers = HeaderMap::new();
    if let Ok(v) = session_cookie(&token, state.cookie_secure, max_age).parse() {
        headers.insert(header::SET_COOKIE, v);
    }
    // {base}/ and NOT "/". Caddy strips the prefix before this app sees the
    // request, so a bare "/" is the LANDING PAGE from the browser's point of
    // view -- signing in would bounce you out to the public site.
    (headers, Redirect::to(&format!("{base}/"))).into_response()
}

async fn logout(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    let base = base_path(&headers);
    if let Some(token) = cookie_value(&headers) {
        if let Ok(mut sessions) = state.sessions.lock() {
            sessions.remove(&token);
        }
    }
    // FORGETTING IS NO LONGER ENOUGH. The cookie can rebuild its own session,
    // so dropping the map entry above would leave Sign out doing nothing at
    // all past the next request. This is the line that actually ends it.
    //
    // It ends EVERY admin session, not just this browser's. For a one-person
    // admin that is the behaviour you want from a button you press because
    // you are worried -- "sign out here" and "sign out everywhere" being
    // different buttons is a distinction worth having on a family site with
    // several people, and not on this one.
    revoke_all(&state);
    let mut out = HeaderMap::new();
    if let Ok(v) = clear_cookie(state.cookie_secure).parse() {
        out.insert(header::SET_COOKIE, v);
    }
    (out, Redirect::to(&format!("{base}/"))).into_response()
}

/// Hand the current file back, so you can edit what is actually live rather
/// than what you think is live.
async fn download(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    axum::extract::Path(key): axum::extract::Path<String>,
) -> Response {
    let base = base_path(&headers);
    if touch_session(&state, &headers).is_none() {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    }
    let Some(t) = target_for(&key) else {
        return (StatusCode::NOT_FOUND, "no such target").into_response();
    };
    match std::fs::read(state.data_dir.join(t.file)) {
        Ok(bytes) => (
            [
                (header::CONTENT_TYPE, "application/json"),
                (
                    header::CONTENT_DISPOSITION,
                    // A fixed name from the allowlist, never anything the
                    // request supplied.
                    Box::leak(format!("attachment; filename=\"{}\"", t.file).into_boxed_str()),
                ),
            ],
            Body::from(bytes),
        )
            .into_response(),
        Err(_) => (StatusCode::NOT_FOUND, "nothing uploaded yet").into_response(),
    }
}

async fn upload(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    mut multipart: Multipart,
) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };

    let mut got_csrf = String::new();
    let mut key = String::new();
    let mut mode = String::new();
    let mut bytes: Vec<u8> = Vec::new();
    let mut pasted = String::new();
    let mut too_big = false;

    while let Ok(Some(field)) = multipart.next_field().await {
        match field.name().unwrap_or("").to_string().as_str() {
            "csrf" => got_csrf = field.text().await.unwrap_or_default(),
            "target" => key = field.text().await.unwrap_or_default(),
            "mode" => mode = field.text().await.unwrap_or_default(),
            "text" => pasted = field.text().await.unwrap_or_default(),
            "file" => match field.bytes().await {
                Ok(b) => {
                    if b.len() > state.max_upload {
                        too_big = true;
                    } else {
                        bytes = b.to_vec();
                    }
                }
                Err(_) => {}
            },
            _ => {}
        }
    }

    // The paste box and the file picker are the same input by the time it gets
    // here. A file wins if both are somehow filled, because picking a file is
    // the more deliberate act of the two.
    if bytes.is_empty() && !pasted.trim().is_empty() {
        bytes = pasted.trim().as_bytes().to_vec();
    }

    // Resolve the target FIRST so every failure below can be reported on that
    // site's own page, next to the form that produced it - rather than bouncing
    // you back to the dashboard to work out which card you were on.
    let Some(t) = target_for(&key) else {
        return (
            StatusCode::BAD_REQUEST,
            Html("<h1>Unknown site</h1><p>Nothing was changed.</p>"),
        )
            .into_response();
    };

    // Constant-ish comparison is overkill for a CSRF token, but mismatched
    // lengths short-circuiting is the kind of thing that turns into a talk at a
    // conference, so compare the whole thing.
    if got_csrf.is_empty() || got_csrf.as_bytes() != csrf.as_bytes() {
        return flash_site(&state, &base, &token, t,
            "That request did not carry a valid form token. Reload the page and try again.",
            false,
        );
    }

    if too_big {
        let mb = state.max_upload / (1024 * 1024);
        return flash_site(&state, &base, &token, t,
            &format!("That file is larger than the {mb} MB limit. Nothing was changed."),
            false,
        );
    }

    if bytes.is_empty() {
        return flash_site(&state, &base, &token, t,
            "Nothing was pasted and no file was attached.",
            false,
        );
    }

    // VALIDATE BEFORE WRITING. A file that is not JSON must never reach the
    // apps: they would fail to parse it at the worst possible moment, on a live
    // page, with no clue where the bad data came from. Refusing here means the
    // only way to break the site is to upload valid-but-wrong JSON, which is a
    // much smaller target.
    let parsed: serde_json::Value = match serde_json::from_slice(&bytes) {
        Ok(v) => v,
        Err(e) => {
            return flash_site(&state, &base, &token, t,
                &format!(
                    "That is not valid JSON, so nothing was changed.\nLine {}, column {}: {}",
                    e.line(),
                    e.column(),
                    e
                ),
                false,
            );
        }
    };

    // A bare number or string is valid JSON but is never what a data file
    // should be, and is a strong sign the wrong file was picked.
    if !parsed.is_object() && !parsed.is_array() {
        return flash_site(&state, &base, &token, t,
            "That parsed as JSON but is not an object or a list. Nothing was changed.",
            false,
        );
    }

    // SLIPSTREAM, when this file is a list and the form did not explicitly ask
    // to replace the whole thing. Defaulting to patch is the safe direction: a
    // patch sent to a file that wanted replacing loses nothing, while a replace
    // sent to a file that wanted patching loses everything not in the upload.
    if !t.list_key.is_empty() && mode != "replace" {
        let patch = match parse_patch(t, parsed) {
            Ok(p) => p,
            Err(e) => return flash_site(&state, &base, &token, t, &e, false),
        };
        let (shell, mut entries) = read_list(&state, t);
        let n_before = entries.len();
        let r = apply_patch(t, &mut entries, patch);
        let out = serialize_list(t, shell, entries);

        return match write_with_backup(&state, t, &out) {
            Ok(backup) => {
                tracing::info!(
                    target = t.key,
                    added = r.added,
                    updated = r.updated,
                    removed = r.removed,
                    "admin: patched"
                );
                let mut said: Vec<String> = Vec::new();
                if r.added > 0 {
                    said.push(format!("{} added", r.added));
                }
                if r.updated > 0 {
                    said.push(format!("{} updated", r.updated));
                }
                if r.removed > 0 {
                    said.push(format!("{} removed", r.removed));
                }
                if said.is_empty() {
                    said.push("nothing changed".into());
                }
                let mut msg = format!(
                    "{}: {}. The file now holds {} (it held {}).",
                    t.label,
                    said.join(", "),
                    n_pl(entries_for(&state, t).len(), t.noun),
                    n_before
                );
                if !r.missing.is_empty() {
                    msg.push_str(&format!(
                        "\nNothing was removed for: {} - no entry has that id.",
                        r.missing.join(", ")
                    ));
                }
                if let Some(name) = backup {
                    msg.push_str(&format!("\nPrevious version kept as {name}."));
                }
                flash_site(&state, &base, &token, t, &msg, true)
            }
            Err(e) => flash_site(&state, &base, &token, t,
                &format!("Could not write the file, so nothing was changed.\n{e}"),
                false,
            ),
        };
    }

    match write_with_backup(&state, t, &bytes) {
        Ok(backup) => {
            tracing::info!(target = t.key, bytes = bytes.len(), "admin: uploaded");
            let note = match backup {
                Some(name) => format!("Previous version kept as {name}."),
                None => "This was the first upload, so there was nothing to back up.".to_string(),
            };
            flash_site(&state, &base, &token, t,
                &format!(
                    "{} updated - {} bytes are live now.\n{}",
                    t.label,
                    bytes.len(),
                    note
                ),
                true,
            )
        }
        Err(e) => flash_site(&state, &base, &token, t,
            &format!("Could not write the file, so nothing was changed.\n{e}"),
            false,
        ),
    }
}

/// Back up what is there, then write the new file atomically.
///
/// ATOMIC MATTERS HERE. Writing straight over the live file means a reader can
/// catch it half-written and get a parse error on a file that is about to be
/// perfectly fine. Writing to a temp file and renaming means readers see either
/// the whole old file or the whole new one, never a torn one.
fn write_with_backup(
    state: &AppState,
    t: &Target,
    bytes: &[u8],
) -> Result<Option<String>, std::io::Error> {
    std::fs::create_dir_all(&state.data_dir)?;
    let live = state.data_dir.join(t.file);

    let backup_name = if live.exists() {
        std::fs::create_dir_all(&state.backup_dir)?;
        // format_description! is checked at COMPILE time, so a typo in the
        // pattern is a build error rather than a runtime failure that only
        // shows up on the one path that matters - the backup, right before an
        // overwrite.
        let fmt = time::macros::format_description!(
            "[year][month][day]-[hour][minute][second]"
        );
        let stamp = common::now_local()
            .format(&fmt)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e.to_string()))?;
        // THE SUFFIX IS NOT DECORATION. The stamp is only accurate to the
        // second, so two saves inside one second produce the same filename and
        // the second silently overwrites the first - destroying the very
        // version somebody would want back. A double-clicked Save gets there.
        let mut name = format!("{stamp}-{}", t.file);
        let mut n = 1;
        while state.backup_dir.join(&name).exists() {
            name = format!("{stamp}-{n}-{}", t.file);
            n += 1;
        }
        std::fs::copy(&live, state.backup_dir.join(&name))?;
        prune_backups(&state.backup_dir, t.file, 10);
        Some(name)
    } else {
        None
    };

    let tmp = state.data_dir.join(format!(".{}.tmp", t.file));
    std::fs::write(&tmp, bytes)?;
    std::fs::rename(&tmp, &live)?;
    Ok(backup_name)
}

/// Keep the last `keep` backups of one file; delete the rest.
///
/// Unbounded backups of a file you might upload daily is a slow disk leak, and
/// the tenth-most-recent version of a config file has never once been the one
/// anybody wanted.
fn prune_backups(dir: &Path, file: &str, keep: usize) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    let mut mine: Vec<PathBuf> = entries
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .map(|n| n.ends_with(&format!("-{file}")))
                .unwrap_or(false)
        })
        .collect();
    // Names start with a sortable timestamp, so lexical order is time order.
    mine.sort();
    if mine.len() > keep {
        for old in &mine[..mine.len() - keep] {
            let _ = std::fs::remove_file(old);
        }
    }
}

// -----------------------------------------------------------------------------
//  Slipstream: patching a list file instead of replacing it
//
//  THE ONE RULE THAT MAKES THIS SAFE TO USE CASUALLY: every entry has an id,
//  and sending the same id twice UPDATES that entry rather than adding a second
//  copy. Found a better phone number? Paste the whole entry again with the
//  number fixed. There is no "edit" mode to remember to use and no way to end
//  up with two Walnut Recreation Centers.
//
//  The id is written INTO the entry the first time it is stored, rather than
//  re-derived from the name on every read. That is what lets a name be changed
//  later without the entry being treated as a new one - which matters, because
//  the alternative silently turns "fix a typo in the name" into "add a
//  duplicate and orphan the original".
// -----------------------------------------------------------------------------

/// `"Walnut Recreation Center"` -> `"walnut-recreation-center"`.
fn slug(s: &str) -> String {
    let mut out = String::new();
    let mut dash = false;
    for c in s.chars().flat_map(|c| c.to_lowercase()) {
        if c.is_ascii_alphanumeric() {
            out.push(c);
            dash = false;
        } else if !dash && !out.is_empty() {
            out.push('-');
            dash = true;
        }
    }
    while out.ends_with('-') {
        out.pop();
    }
    out.chars().take(60).collect()
}

/// "3 places" / "1 article". Counting is the whole point of the message, so
/// getting the plural wrong makes it read like a template that was not finished.
fn n_pl(n: usize, noun: &str) -> String {
    if n == 1 {
        format!("1 {noun}")
    } else {
        format!("{n} {noun}s")
    }
}

/// The id for one entry: its own `id` if it has one, else a slug of its name.
fn entry_id(t: &Target, e: &serde_json::Value) -> String {
    let explicit = e.get("id").and_then(|v| v.as_str()).unwrap_or("").trim();
    if !explicit.is_empty() {
        return slug(explicit);
    }
    slug(e.get(t.name_key).and_then(|v| v.as_str()).unwrap_or(""))
}

#[derive(Debug)]
struct Patch {
    add: Vec<serde_json::Value>,
    remove: Vec<String>,
}

/// Work out what a pasted blob is asking for.
///
/// DELIBERATELY GENEROUS ABOUT SHAPE. These files are written by hand on a
/// phone or pasted out of a model, and `{"add": [...]}`, a bare list, and a
/// single bare entry are all completely reasonable things to send. Refusing two
/// of those three would mean the difference between working and not working is
/// a pair of brackets nobody can see a reason for.
///
/// STRICT ABOUT CONTENT, though: an entry that cannot produce an id is refused
/// by name and position, and the whole patch is abandoned. Storing an entry
/// with an empty id would make it un-removable and would collide with the next
/// nameless one.
fn parse_patch(t: &Target, v: serde_json::Value) -> Result<Patch, String> {
    let mut add: Vec<serde_json::Value> = Vec::new();
    let mut remove: Vec<String> = Vec::new();

    // "remove" accepts a string, a list of strings, or a list of whole entries
    // (so a downloaded entry can be pasted straight back to delete it).
    let mut take_remove = |v: &serde_json::Value| match v {
        serde_json::Value::String(s) => remove.push(slug(s)),
        serde_json::Value::Object(_) => remove.push(entry_id(t, v)),
        serde_json::Value::Array(a) => {
            for i in a {
                match i {
                    serde_json::Value::String(s) => remove.push(slug(s)),
                    serde_json::Value::Object(_) => remove.push(entry_id(t, i)),
                    _ => {}
                }
            }
        }
        _ => {}
    };

    match v {
        serde_json::Value::Array(items) => add = items,
        serde_json::Value::Object(ref map) => {
            let has_verbs = map.contains_key("add")
                || map.contains_key("remove")
                || map.contains_key(t.list_key);
            if has_verbs {
                for key in ["add", t.list_key] {
                    match map.get(key) {
                        Some(serde_json::Value::Array(a)) => add.extend(a.clone()),
                        Some(o @ serde_json::Value::Object(_)) => add.push(o.clone()),
                        _ => {}
                    }
                }
                if let Some(r) = map.get("remove") {
                    take_remove(r);
                }
            } else {
                // No verbs and no list: this is one entry on its own.
                add.push(v.clone());
            }
        }
        _ => return Err("That is not an entry, a list of entries, or a patch.".into()),
    }

    if add.is_empty() && remove.is_empty() {
        return Err(
            "Nothing to add and nothing to remove, so nothing was changed.\n\
             A patch looks like {\"add\": [ ... ], \"remove\": [\"an-id\"]}."
                .into(),
        );
    }

    for (i, e) in add.iter().enumerate() {
        if !e.is_object() {
            return Err(format!(
                "Entry {} is not an object, so nothing was changed.",
                i + 1
            ));
        }
        if entry_id(t, e).is_empty() {
            return Err(format!(
                "Entry {} has no \"id\" and no \"{}\", so there is no way to tell \
                 it apart from the next one. Nothing was changed.",
                i + 1,
                t.name_key
            ));
        }
    }

    Ok(Patch { add, remove })
}

/// The live file, split into the list and everything else at the top level.
///
/// "Everything else" is preserved on write. Somebody's hand-written `_note` at
/// the top of the file surviving a patch is the difference between this being a
/// file you own and a file the admin owns.
fn read_list(
    state: &AppState,
    t: &Target,
) -> (serde_json::Map<String, serde_json::Value>, Vec<serde_json::Value>) {
    let path = state.data_dir.join(t.file);
    let Ok(bytes) = std::fs::read(&path) else {
        return (serde_json::Map::new(), Vec::new());
    };
    match serde_json::from_slice::<serde_json::Value>(&bytes) {
        // A bare list on disk is legitimate - kb.py accepts one, and a hand
        // edit is likely to produce one.
        Ok(serde_json::Value::Array(a)) => (serde_json::Map::new(), a),
        Ok(serde_json::Value::Object(mut m)) => {
            let list = match m.remove(t.list_key) {
                Some(serde_json::Value::Array(a)) => a,
                _ => Vec::new(),
            };
            (m, list)
        }
        _ => (serde_json::Map::new(), Vec::new()),
    }
}

struct Applied {
    added: usize,
    updated: usize,
    removed: usize,
    missing: Vec<String>,
}

fn apply_patch(t: &Target, entries: &mut Vec<serde_json::Value>, p: Patch) -> Applied {
    let mut out = Applied {
        added: 0,
        updated: 0,
        removed: 0,
        missing: Vec::new(),
    };

    for mut e in p.add {
        let id = entry_id(t, &e);
        // Stamp the id in, so a later rename does not orphan the entry.
        if let Some(map) = e.as_object_mut() {
            map.insert("id".into(), serde_json::Value::String(id.clone()));
        }
        match entries.iter().position(|x| entry_id(t, x) == id) {
            // Replace IN PLACE. Keeping the position means the file does not
            // reshuffle itself every time you fix a phone number.
            Some(i) => {
                entries[i] = e;
                out.updated += 1;
            }
            None => {
                entries.push(e);
                out.added += 1;
            }
        }
    }

    for id in p.remove {
        let before = entries.len();
        entries.retain(|x| entry_id(t, x) != id);
        if entries.len() == before {
            // Say so rather than reporting a success that removed nothing -
            // "I deleted it and it is still there" is the worst outcome here.
            out.missing.push(id);
        } else {
            out.removed += before - entries.len();
        }
    }

    out
}

fn serialize_list(
    t: &Target,
    mut shell: serde_json::Map<String, serde_json::Value>,
    entries: Vec<serde_json::Value>,
) -> Vec<u8> {
    shell.insert(t.list_key.into(), serde_json::Value::Array(entries));
    let mut bytes = serde_json::to_vec_pretty(&serde_json::Value::Object(shell))
        .unwrap_or_else(|_| b"{}".to_vec());
    bytes.push(b'\n');
    bytes
}

/// What is in the file now, for the page that lists it with Remove buttons.
fn entries_for(state: &AppState, t: &Target) -> Vec<EntryView> {
    if t.list_key.is_empty() {
        return Vec::new();
    }
    let (_, list) = read_list(state, t);
    list.iter()
        .map(|e| EntryView {
            id: entry_id(t, e),
            name: e
                .get(t.name_key)
                .and_then(|v| v.as_str())
                .unwrap_or("(no name)")
                .to_string(),
            note: e
                .get("category")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            // Only meaningful for the knowledge base, and harmless elsewhere:
            // it is the flag that decides whether an article can answer on its
            // own, so it is worth seeing without opening the file.
            direct: e.get("direct").and_then(|v| v.as_bool()).unwrap_or(false),
        })
        .collect()
}

/// The Remove button next to one entry in the list.
///
/// A plain form post, not JSON and not a DELETE, because it has to work with
/// nothing but a browser - and because the CSRF token and the session cookie
/// are then handled by exactly the same code as every other write here.
async fn entry_remove(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };

    let key = form_field(&body, "target").unwrap_or_default();
    let Some(t) = target_for(&key) else {
        return (
            StatusCode::BAD_REQUEST,
            Html("<h1>Unknown site</h1><p>Nothing was changed.</p>"),
        )
            .into_response();
    };
    if t.list_key.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Html("<h1>Not a list</h1><p>Nothing was changed.</p>"),
        )
            .into_response();
    }

    let got = form_field(&body, "csrf").unwrap_or_default();
    if got.is_empty() || got.as_bytes() != csrf.as_bytes() {
        return flash_site(&state, &base, &token, t,
            "That request did not carry a valid form token. Reload the page and try again.",
            false,
        );
    }

    let id = slug(&form_field(&body, "id").unwrap_or_default());
    if id.is_empty() {
        return flash_site(&state, &base, &token, t, "No entry was named.", false);
    }

    let (shell, mut entries) = read_list(&state, t);
    let before = entries.len();
    entries.retain(|e| entry_id(t, e) != id);
    if entries.len() == before {
        return flash_site(&state, &base, &token, t,
            &format!("Nothing here has the id \"{id}\", so nothing was changed."),
            false,
        );
    }

    let out = serialize_list(t, shell, entries);
    match write_with_backup(&state, t, &out) {
        Ok(backup) => {
            tracing::info!(target = t.key, id = %id, "admin: entry removed");
            let note = match backup {
                Some(name) => format!(" Previous version kept as {name}."),
                None => String::new(),
            };
            flash_site(&state, &base, &token, t,
                &format!("Removed \"{id}\".{note}"),
                true,
            )
        }
        Err(e) => flash_site(&state, &base, &token, t,
            &format!("Could not write the file, so nothing was changed.\n{e}"),
            false,
        ),
    }
}

// -----------------------------------------------------------------------------
//  Who can sign in
// -----------------------------------------------------------------------------

fn read_people(state: &AppState) -> Vec<common::people::Person> {
    common::people::read(&state.people_file)
}

fn write_people(state: &AppState, list: &[common::people::Person]) -> std::io::Result<()> {
    common::people::write(&state.people_file, list)
}

/// Good enough to catch a typo, not a full RFC 5321 validator.
///
/// A wrong address here does not grant anyone access - it just means an account
/// nobody can use. So the cost of being slightly permissive is low, and the
/// cost of rejecting a legitimate odd address is a person who cannot sign in
/// and does not know why.
fn looks_like_email(e: &str) -> bool {
    let e = e.trim();
    match e.split_once('@') {
        Some((user, domain)) => {
            !user.is_empty()
                && domain.contains('.')
                && !domain.starts_with('.')
                && !domain.ends_with('.')
                && !e.contains(' ')
                && e.len() <= 254
        }
        None => false,
    }
}

async fn people_page(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    let (flash, ok) = take_flash(&state, &token);
    render_people(&state, &base, &csrf, &flash, ok)
}

fn render_people(state: &AppState, base: &str, csrf: &str, flash: &str, ok: bool) -> Response {
    let trials = read_trials(state);
    let guests = guest_emails(&state.auth_passkeys_file);
    let pk_info = passkey_info_map(&state.auth_passkeys_file);
    let inv_info = invite_info_map(&state.guest_invites_file);
    let mut people: Vec<PersonView> = read_people(state)
        .iter()
        .map(|p| {
            let app_checks: Vec<AppCheck> = common::people::ALL_APPS
                .iter()
                .map(|&a| AppCheck {
                    name: a,
                    allowed: p.can_use(a),
                    level: p.level_for(a).unwrap_or("none").to_string(),
                })
                .collect();
            let apps_allowed = app_checks.iter().filter(|a| a.allowed).count();
            let is_guest = guests.contains(&p.email);
            let (passkeys, account_expires): (Vec<KeyView>, u64) = match pk_info.get(&p.email) {
                Some((ks, exp)) => (ks.clone(), *exp),
                None => (Vec::new(), 0),
            };
            // Display name: name field if set, else the email stem before '@',
            // so no raw email address ever appears on the row itself.
            let display_name = if !p.name.is_empty() {
                p.name.clone()
            } else {
                p.email.split('@').next().unwrap_or(&p.email).to_string()
            };
            let account_expires_label = if is_guest { unix_date(account_expires) } else { String::new() };
            let invited_label = if is_guest {
                inv_info.get(&p.email).cloned().unwrap_or_default()
            } else {
                String::new()
            };
            PersonView {
                email: p.email.clone(),
                has_password: p.has_password(),
                must_change: p.must_change,
                owner: is_owner(state, &p.email),
                app_checks,
                apps_allowed,
                theme: p.theme.clone(),
                name: p.name.clone(),
                signed_out: revoked_label(state, &p.email),
                trial: trial_label(&trials, &p.email),
                guest: is_guest,
                display_name,
                passkeys,
                account_expires_label,
                invited_label,
            }
        })
        .collect();
    people.sort_by(|a, b| a.email.cmp(&b.email));
    let fresh_app_checks: Vec<AppCheck> = common::people::ALL_APPS
        .iter()
        .map(|&a| AppCheck { name: a, allowed: false, level: "none".to_string() })
        .collect();
    let html = PeopleTemplate {
        base: base.to_string(),
        fresh_app_checks,
        themes: common::people::THEMES
            .iter()
            .map(|(key, _group, label, blurb)| ThemeChoice { key, label, blurb })
            .collect(),
        csrf: csrf.to_string(),
        flash: flash.to_string(),
        flash_ok: ok,
        people,
        min: common::people::MIN_PASSWORD,
        year: common::current_year(),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    Html(html).into_response()
}

/// When this person was last signed out of everything.
fn revoked_label(state: &AppState, email: &str) -> String {
    let map: std::collections::HashMap<String, u64> = std::fs::read(&state.auth_revoked_file)
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or_default();
    match map.get(email) {
        Some(&t) if t > 0 => ago(t),
        _ => "never".into(),
    }
}

/// "3 of 8 used" when this person also holds a trial, empty otherwise.
fn trial_label(store: &common::trial::Store, email: &str) -> String {
    store
        .trials
        .iter()
        .find(|t| t.email == email)
        .map(|t| format!("{} of {} used", t.used, t.quota))
        .unwrap_or_default()
}

/// Sign one person out of everything: the website and both phone apps.
///
/// WRITES A TIMESTAMP, DOES NOT DELETE ANYTHING. The auth service records when
/// each cookie and each OIDC token was minted and refuses any that predates
/// this mark, so one number voids every credential the person holds without
/// this process needing to reach into the other one's memory. It also survives
/// a restart, which an in-memory revocation would not.
///
/// IT DOES NOT REMOVE THE ACCOUNT. They sign in again and carry on -- this is
/// the "their phone was stolen" button, not the "they no longer live here"
/// button, and the two should not be one control.
async fn people_signout(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_people(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }

    let email = common::people::normalise(&form_field(&body, "email").unwrap_or_default());
    let people = read_people(&state);
    if common::people::find(&people, &email).is_none() {
        return flash_people(&state, &base, &token, "No such account.", false);
    }

    let mut map: std::collections::HashMap<String, u64> =
        std::fs::read(&state.auth_revoked_file)
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok())
            .unwrap_or_default();
    map.insert(email.clone(), common::people::now());

    if let Some(dir) = state.auth_revoked_file.parent() {
        std::fs::create_dir_all(dir).ok();
    }
    let written = serde_json::to_vec_pretty(&map).ok().and_then(|j| {
        // Written beside the target and renamed, so a crash mid-write cannot
        // leave a truncated revocation list -- which would fail OPEN.
        let tmp = state.auth_revoked_file.with_extension("tmp");
        std::fs::write(&tmp, j).ok()?;
        std::fs::rename(&tmp, &state.auth_revoked_file).ok()
    });

    match written {
        Some(_) => {
            tracing::info!(%email, "admin: signed out everywhere");
            flash_people(&state, &base, &token,
                &format!("{email} is signed out of the website and both apps. \
                          They can sign in again whenever they like."),
                true)
        }
        None => flash_people(&state, &base, &token, "Could not write the revocation list.", false),
    }
}

/// Remove one passkey from a person's entry in the auth service's passkeys.json.
/// The key is found by the short random `id` field (not the credential id),
/// same field shown in the revoke form. Safe to call for regular users and
/// guests alike -- it only removes a key, never the account.
async fn people_passkey_revoke(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_people(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }

    let email = common::people::normalise(&form_field(&body, "email").unwrap_or_default());
    let key_id = form_field(&body, "key_id").unwrap_or_default();
    if email.is_empty() || key_id.is_empty() {
        return flash_people(&state, &base, &token, "Missing email or key id.", false);
    }

    let path = &state.auth_passkeys_file;
    let result: Result<(), String> = (|| {
        let text = std::fs::read_to_string(path)
            .map_err(|e| format!("Could not read passkeys.json: {e}"))?;
        let mut root: serde_json::Value = serde_json::from_str(&text)
            .map_err(|e| format!("passkeys.json does not parse: {e}"))?;
        let users = root["users"]
            .as_array_mut()
            .ok_or_else(|| "passkeys.json has no users array".to_string())?;
        let user = users
            .iter_mut()
            .find(|u| u["email"].as_str() == Some(&email))
            .ok_or_else(|| format!("No passkey entry for {email}"))?;
        let keys = user["keys"]
            .as_array_mut()
            .ok_or_else(|| "User has no keys array".to_string())?;
        let before = keys.len();
        keys.retain(|k| k["id"].as_str() != Some(&key_id));
        if keys.len() == before {
            return Err("Passkey not found (already removed?).".to_string());
        }
        let out = serde_json::to_vec_pretty(&root)
            .map_err(|e| format!("Could not serialise passkeys.json: {e}"))?;
        let tmp = path.with_extension("json.tmp");
        std::fs::write(&tmp, &out)
            .map_err(|e| format!("Could not write temp file: {e}"))?;
        std::fs::rename(&tmp, path)
            .map_err(|e| format!("Could not rename temp file: {e}"))
    })();

    match result {
        Ok(_) => {
            tracing::info!(%email, %key_id, "admin: passkey revoked");
            flash_people(&state, &base, &token, "Passkey revoked.", true)
        }
        Err(msg) => {
            tracing::warn!(%email, %key_id, error = %msg, "admin: passkey revoke failed");
            flash_people(&state, &base, &token, &msg, false)
        }
    }
}

// ===========================================================================
//  Guest invites: resend (fresh link) and extend (2026-11-11, Jason's ask:
//  "can the invite links go into auth for the people so i can resend them
//  and have an option to renew them").
//
//  RESEND MAKES A NEW LINK, never re-shows the old one: codes are stored only
//  as hashes (common::guest_invite), so whoever can read the file cannot
//  sign in as a guest. Jason picked this over storing the plain link.
//  Every older unused code for the same account is retired, so only the
//  newest link works.
//
//  EXTEND moves the guest's account_expires out and carries their passkey's
//  expiry along with it (an invite passkey's expiry IS account_expires, see
//  auth's invite_start), so the guest keeps signing in with the same key.
// ===========================================================================

/// The raw passkeys.json entry for `email`, only if it is a guest.
fn read_guest_entry(path: &std::path::Path, email: &str) -> Result<serde_json::Value, String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("Could not read passkeys.json: {e}"))?;
    let root: serde_json::Value =
        serde_json::from_str(&text).map_err(|e| format!("passkeys.json does not parse: {e}"))?;
    let user = root["users"]
        .as_array()
        .and_then(|us| us.iter().find(|u| u["email"].as_str() == Some(email)))
        .ok_or_else(|| "That person has no guest entry.".to_string())?;
    if user["guest"].as_bool() != Some(true) {
        return Err("That is not a guest account.".to_string());
    }
    Ok(user.clone())
}

/// Read passkeys.json raw, let `f` change the one guest entry for `email`,
/// write it back atomically. Raw JSON for the same reason as
/// patch_new_guest_entry: this crate does not know the Passkey type.
fn edit_guest_entry(
    path: &std::path::Path,
    email: &str,
    f: impl FnOnce(&mut serde_json::Value) -> Result<(), String>,
) -> Result<(), String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("Could not read passkeys.json: {e}"))?;
    let mut root: serde_json::Value =
        serde_json::from_str(&text).map_err(|e| format!("passkeys.json does not parse: {e}"))?;
    let user = root["users"]
        .as_array_mut()
        .ok_or_else(|| "passkeys.json has no users array".to_string())?
        .iter_mut()
        .find(|u| u["email"].as_str() == Some(email))
        .ok_or_else(|| "That person has no guest entry.".to_string())?;
    if user["guest"].as_bool() != Some(true) {
        return Err("That is not a guest account.".to_string());
    }
    f(user)?;
    let out = serde_json::to_vec_pretty(&root).map_err(|e| format!("Could not serialise passkeys.json: {e}"))?;
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, &out).map_err(|e| format!("Could not write temp file: {e}"))?;
    std::fs::rename(&tmp, path).map_err(|e| format!("Could not save passkeys.json: {e}"))
}

async fn people_invite_resend(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_people(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let email = common::people::normalise(&form_field(&body, "email").unwrap_or_default());
    let now = common::people::now();

    // Guest, not ended, no passkey yet (a guest may hold one, so a new link
    // would only be refused at the door by auth's enrol_allowed_invite).
    let check = read_guest_entry(&state.auth_passkeys_file, &email).and_then(|u| {
        let ends = u["account_expires"].as_u64().unwrap_or(0);
        if ends != 0 && now >= ends {
            return Err("That guest account has ended. Extend it first, then make a new link.".to_string());
        }
        if u["keys"].as_array().map(|k| !k.is_empty()).unwrap_or(false) {
            return Err("They already have a passkey. Revoke it first if they need a new one.".to_string());
        }
        Ok(())
    });
    if let Err(m) = check {
        return flash_people(&state, &base, &token, &m, false);
    }

    let label = read_people(&state)
        .iter()
        .find(|p| p.email == email)
        .map(|p| p.name.clone())
        .filter(|n| !n.is_empty())
        .unwrap_or_else(|| "Guest".to_string());
    let code = common::guest_invite::new_code();
    let mut store = common::guest_invite::read(&state.guest_invites_file);
    let mut retired = 0;
    for inv in store.invites.iter_mut().filter(|i| i.target_email == email && !i.redeemed) {
        inv.redeemed = true; // retired, not used: peek() refuses it from now on
        inv.redeemed_at = now;
        retired += 1;
    }
    let days = store.invites.iter().rev().find(|i| i.target_email == email).map(|i| i.days).unwrap_or(0);
    store.invites.push(common::guest_invite::Invite {
        code_hash: common::guest_invite::code_hash(&code),
        target_email: email.clone(),
        label: label.clone(),
        days,
        created: now,
        redeemed: false,
        redeemed_at: 0,
    });
    if let Err(e) = common::guest_invite::write(&state.guest_invites_file, &store) {
        return flash_people(&state, &base, &token, &format!("Could not save the new link: {e}"), false);
    }
    tracing::info!(label = %label, retired, "admin: guest invite re-sent (new code)");
    let link = format!("{}/invite?c={}", state.invite_link_base.trim_end_matches('/'), code);
    flash_people(&state, &base, &token,
        &format!("New invite link for \"{label}\" -- it works once, and any older link is now off: {link}"),
        true)
}

async fn people_guest_extend(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_people(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let email = common::people::normalise(&form_field(&body, "email").unwrap_or_default());
    let days: u64 = form_field(&body, "days").unwrap_or_default().trim().parse().unwrap_or(0);
    let now = common::people::now();
    // From today if it already ended, else from the current end date; never
    // more than 365 days from today (same ceiling as guest_expiry).
    let mut new_end = 0;
    let r = edit_guest_entry(&state.auth_passkeys_file, &email, |u| {
        if !(1..=365).contains(&days) {
            return Err("Pick 1 to 365 days.".to_string());
        }
        let ends = u["account_expires"].as_u64().unwrap_or(0);
        new_end = (ends.max(now) + days * 86400).min(now + 365 * 86400);
        u["account_expires"] = serde_json::json!(new_end);
        if let Some(keys) = u["keys"].as_array_mut() {
            for k in keys.iter_mut().filter(|k| k["expires"].as_u64().unwrap_or(0) != 0) {
                k["expires"] = serde_json::json!(new_end);
            }
        }
        Ok(())
    });
    match r {
        Ok(()) => {
            tracing::info!(%email, days, "admin: guest account extended");
            flash_people(&state, &base, &token, &format!("Extended. Their access now ends {}.", unix_date(new_end)), true)
        }
        Err(m) => flash_people(&state, &base, &token, &m, false),
    }
}

// ===========================================================================
//  Trials -- invite codes, and who has redeemed one
// ===========================================================================

fn read_trials(state: &AppState) -> common::trial::Store {
    common::trial::read(&state.trials_file)
}

fn flash_trials(state: &AppState, base: &str, token: &str, msg: &str, ok: bool) -> Response {
    flash_to(state, token, msg, ok, &format!("{base}/trials"))
}

/// Unix seconds as something a person can read. Deliberately coarse: the exact
/// minute somebody redeemed a code is noise, and "3 days ago" is the question
/// actually being asked.
fn ago(then: u64) -> String {
    if then == 0 {
        return "never".into();
    }
    let secs = common::people::now().saturating_sub(then);
    match secs {
        0..=90 => "just now".into(),
        s if s < 3600 => format!("{} min ago", s / 60),
        s if s < 172_800 => format!("{} hr ago", s / 3600),
        s => format!("{} days ago", s / 86_400),
    }
}

async fn trials_page(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    let (flash, ok) = take_flash(&state, &token);
    render_trials(&state, &base, &csrf, &flash, ok)
}

fn render_trials(state: &AppState, base: &str, csrf: &str, flash: &str, ok: bool) -> Response {
    let store = read_trials(state);
    let people = read_people(state);

    let codes: Vec<CodeView> = store
        .invites
        .iter()
        .map(|i| CodeView {
            code: i.code.clone(),
            label: i.label.clone(),
            quota: i.quota,
            active: i.active,
            max_uses: i.max_uses,
            redeemed: store.trials.iter().filter(|t| t.code == i.code).count(),
        })
        .collect();

    let mut trials: Vec<TrialView> = store
        .trials
        .iter()
        .map(|t| TrialView {
            email: t.email.clone(),
            code: t.code.clone(),
            used: t.used,
            quota: t.quota,
            remaining: t.remaining(),
            finished: t.remaining() == 0,
            now_a_member: common::people::find(&people, &t.email).is_some(),
            started: ago(t.started),
            last: ago(t.last),
        })
        .collect();
    // The people waiting to hear back come first -- that is the whole reason
    // to open this page -- then alphabetically.
    trials.sort_by(|a, b| b.finished.cmp(&a.finished).then(a.email.cmp(&b.email)));

    let html = TrialsTemplate {
        base: base.to_string(),
        csrf: csrf.to_string(),
        flash: flash.to_string(),
        flash_ok: ok,
        waiting: trials.iter().filter(|t| t.finished && !t.now_a_member).count(),
        codes,
        trials,
        suggested: common::trial::new_invite_code(),
        default_quota: common::trial::DEFAULT_QUOTA,
        year: common::current_year(),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    Html(html).into_response()
}

/// Mint a code.
async fn trials_add(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_trials(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }

    let code = common::trial::normalise_code(&form_field(&body, "code").unwrap_or_default());
    if code.len() < 4 {
        return flash_trials(&state, &base, &token,
            "A code needs at least four letters or digits.", false);
    }

    let quota: u32 = form_field(&body, "quota")
        .unwrap_or_default()
        .trim()
        .parse()
        .unwrap_or(common::trial::DEFAULT_QUOTA);
    let max_uses: u32 = form_field(&body, "max_uses")
        .unwrap_or_default()
        .trim()
        .parse()
        .unwrap_or(0);

    let mut store = read_trials(&state);
    if store.invites.iter().any(|i| i.code == code) {
        return flash_trials(&state, &base, &token, &format!("{code} already exists."), false);
    }

    store.invites.push(common::trial::Invite {
        code: code.clone(),
        label: form_field(&body, "label").unwrap_or_default().trim().to_string(),
        quota: quota.clamp(1, 1000),
        active: true,
        created: common::people::now(),
        max_uses,
    });

    match common::trial::write(&state.trials_file, &store) {
        Ok(_) => {
            tracing::info!(%code, quota, "admin: invite code created");
            flash_trials(&state, &base, &token,
                &format!("{code} is live. Send it with the app link."), true)
        }
        Err(e) => flash_trials(&state, &base, &token, &format!("Could not save: {e}"), false),
    }
}

/// Switch a code on or off, or delete it.
///
/// NEITHER TOUCHES THE TRIALS ALREADY REDEEMED ON IT. Those people hold tokens
/// with counters of their own; the code only ever gated getting one. Ending
/// somebody's access is the other form below, which is a different decision
/// and should look like one.
async fn trials_code(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_trials(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }

    let code = common::trial::normalise_code(&form_field(&body, "code").unwrap_or_default());
    let action = form_field(&body, "action").unwrap_or_default();
    let mut store = read_trials(&state);

    let message = match action.as_str() {
        "delete" => {
            let before = store.invites.len();
            store.invites.retain(|i| i.code != code);
            if store.invites.len() == before {
                return flash_trials(&state, &base, &token, "No such code.", false);
            }
            format!("{code} deleted. Anyone already on it keeps the questions they have.")
        }
        "toggle" => {
            let Some(invite) = store.invites.iter_mut().find(|i| i.code == code) else {
                return flash_trials(&state, &base, &token, "No such code.", false);
            };
            invite.active = !invite.active;
            if invite.active {
                format!("{code} is live again.")
            } else {
                format!("{code} is off. Nobody new can redeem it.")
            }
        }
        _ => return flash_trials(&state, &base, &token, "Unknown action.", false),
    };

    match common::trial::write(&state.trials_file, &store) {
        Ok(_) => {
            tracing::info!(%code, %action, "admin: invite code changed");
            flash_trials(&state, &base, &token, &message, true)
        }
        Err(e) => flash_trials(&state, &base, &token, &format!("Could not save: {e}"), false),
    }
}

/// A password somebody can read off a screen and type on a phone.
///
/// Grouped with dashes and drawn from an alphabet with no O/0 or I/1, because
/// this one gets dictated down a phone or pasted into a message. It is
/// temporary by construction -- the account is created with must_change set,
/// so it stops being the password the first time they use it.
fn readable_password() -> String {
    (0..3)
        .map(|_| common::trial::new_invite_code().chars().take(4).collect::<String>())
        .collect::<Vec<_>>()
        .join("-")
}

/// Turn a trial into a real account, in one click.
///
/// WHY THIS EXISTS. The steps were: read the address off the Trials page, go
/// to People, retype it, invent a password, type it twice, then come back and
/// end the trial. Six steps, one of them retyping an address that was already
/// on screen -- and the whole point of the trial is that this moment happens
/// often and should be easy. This is the button that was missing when the
/// trial feature shipped.
///
/// THE NEW ACCOUNT GETS NO WEBSITE PAGES. `apps_unrestricted` is false and
/// `apps` is empty, so they can use the assistant in the phone app -- which is
/// what they were trialling and what they asked to keep -- and see none of the
/// household's pages until somebody ticks a box on the People page. Granting
/// "everything" from a button that exists to be clicked quickly is how a
/// stranger ends up with the camera; see EXPLICIT_ONLY in common::people for
/// the time that reasoning was learned the hard way.
///
/// IT REMOVES THE TRIAL ROW. Its only job was to meter somebody who had no
/// account, and they have one now. Leaving it reads as a second identity --
/// which is exactly the confusion that prompted this.
async fn trials_grant(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_trials(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }

    let email = common::people::normalise(&form_field(&body, "email").unwrap_or_default());
    if !looks_like_email(&email) {
        return flash_trials(&state, &base, &token, "That does not look like an email address.", false);
    }

    let mut store = read_trials(&state);
    let had_trial = store.trials.iter().any(|t| t.email == email);
    let mut people = read_people(&state);

    // ALREADY A MEMBER: this is the leftover-row case, not a mistake. Clear
    // the row and say so, rather than refusing and leaving it on screen.
    if common::people::find(&people, &email).is_some() {
        if !had_trial {
            return flash_trials(&state, &base, &token, "No such trial.", false);
        }
        store.trials.retain(|t| t.email != email);
        return match common::trial::write(&state.trials_file, &store) {
            Ok(_) => {
                tracing::info!(%email, "admin: cleared a trial for an existing account");
                flash_trials(&state, &base, &token,
                    &format!("{email} is on their full account now, and the trial row is gone. \
                              The account already gave them the assistant with no question \
                              limit; pick their pages on People."),
                    true)
            }
            Err(e) => flash_trials(&state, &base, &token, &format!("Could not save: {e}"), false),
        };
    }

    let password = readable_password();
    let hash = match common::people::hash_password(&password) {
        Ok(h) => h,
        Err(e) => return flash_trials(&state, &base, &token, &format!("Could not hash that: {e}"), false),
    };

    let mut person = common::people::Person::new(&email);
    person.hash = hash;
    // They pick their own the first time. The one above has been on a screen
    // and will be in a message, so it should stop working as soon as it has
    // done its job.
    person.must_change = true;
    person.apps_unrestricted = false;
    person.apps = Vec::new();
    people.push(person);

    if let Err(e) = write_people(&state, &people) {
        return flash_trials(&state, &base, &token, &format!("Could not save the account: {e}"), false);
    }

    // ORDER MATTERS. The account is written first, so a failure here leaves
    // somebody with an account AND a spent trial -- harmless, and recoverable
    // with one more click. The other order could delete their trial and fail
    // to give them anything.
    store.trials.retain(|t| t.email != email);
    if let Err(e) = common::trial::write(&state.trials_file, &store) {
        tracing::error!(%email, "account created but the trial row could not be cleared: {e}");
    }

    // The password is NEVER logged. This line lands in Loki and stays there.
    tracing::info!(%email, "admin: trial promoted to an account");
    flash_trials(&state, &base, &token,
        &format!(
            "{email} has an account. Send them this password -- it is shown once and \
             they will be asked to choose their own the first time they use it:  {password}"
        ),
        true)
}

/// Act on one person's trial: give them more questions, or end it now.
async fn trials_end(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_trials(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }

    let email = common::people::normalise(&form_field(&body, "email").unwrap_or_default());
    let action = form_field(&body, "action").unwrap_or_default();
    let mut store = read_trials(&state);

    let message = match action.as_str() {
        "revoke" => {
            let before = store.trials.len();
            store.trials.retain(|t| t.email != email);
            if store.trials.len() == before {
                return flash_trials(&state, &base, &token, "No such trial.", false);
            }
            // Their token stops working on the very next request, because the
            // auth service reads this file per call rather than caching it.
            format!("{email}'s trial ended. Their app will ask them to sign in.")
        }
        "more" => {
            let Some(trial) = store.trials.iter_mut().find(|t| t.email == email) else {
                return flash_trials(&state, &base, &token, "No such trial.", false);
            };
            trial.quota += common::trial::DEFAULT_QUOTA;
            // Cleared so the "their trial ran out" email can fire again when
            // these are gone too. Leaving it set would mean a second silence.
            trial.notified = false;
            format!(
                "{email} has {} questions again.",
                trial.quota.saturating_sub(trial.used)
            )
        }
        _ => return flash_trials(&state, &base, &token, "Unknown action.", false),
    };

    match common::trial::write(&state.trials_file, &store) {
        Ok(_) => {
            tracing::info!(%email, %action, "admin: trial changed");
            flash_trials(&state, &base, &token, &message, true)
        }
        Err(e) => flash_trials(&state, &base, &token, &format!("Could not save: {e}"), false),
    }
}

/// Shared by "add somebody" and "set a new password", because the rules about
/// what makes an acceptable password must not be able to differ between the two
/// doors that set one.
fn check_password(pw: &str, again: &str) -> Result<String, String> {
    if pw.chars().count() < common::people::MIN_PASSWORD {
        return Err(format!(
            "Use at least {} characters.",
            common::people::MIN_PASSWORD
        ));
    }
    if pw != again {
        return Err("The two passwords don't match.".into());
    }
    common::people::hash_password(pw).map_err(|e| format!("Could not hash that: {e}"))
}

async fn people_add(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_people(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let email = common::people::normalise(&form_field(&body, "email").unwrap_or_default());
    if !looks_like_email(&email) {
        return flash_people(&state, &base, &token, "That does not look like an email address.", false);
    }
    let pw = form_field(&body, "password").unwrap_or_default();
    let again = form_field(&body, "confirm").unwrap_or_default();
    let hash = match check_password(&pw, &again) {
        Ok(h) => h,
        Err(e) => return flash_people(&state, &base, &token, &e, false),
    };

    let mut list = read_people(&state);
    if list.iter().any(|p| p.email == email) {
        return flash_people(&state, &base, &token,
            &format!("{email} already has an account. Use \"Set a new password\" instead."),
            false,
        );
    }
    let mut person = common::people::Person::new(&email);
    person.hash = hash;
    // OPT IN, and off by default. It used to be forced, which meant the
    // password you set stopped being the password the moment they first used
    // it - the opposite of "I set it and it stays set". Tick the box when you
    // want them to pick their own; leave it and yours is the password until
    // you change it here.
    //
    // Never for one of your own addresses: being made to choose a new password
    // for your own account, on a page you already signed in to, is nonsense.
    person.must_change = form_field(&body, "must_change").is_some() && !is_owner(&state, &email);
    let must_change_note = person.must_change;
    list.push(person);

    match write_people(&state, &list) {
        Ok(_) => {
            // The password is NEVER logged, here or anywhere. This line lands in
            // Loki and stays there.
            tracing::info!(email = %email, "admin: account created");
            flash_people(&state, &base, &token,
                &if must_change_note {
                    format!(
                        "{email} can sign in now. Give them that password - they will be \
                         asked to choose their own the first time they use it."
                    )
                } else {
                    format!(
                        "{email} can sign in now. That password stays theirs until you \
                         change it on this page."
                    )
                },
                true,
            )
        }
        Err(e) => flash_people(&state, &base, &token, &format!("Could not save: {e}"), false),
    }
}

/// Set a new password for somebody who already has an account.
///
/// This is the "they forgot it" path, and it is the ONLY one - there is no
/// reset email, because there is no mail service. That is a deliberate trade:
/// the thing that resets a password is you, in person or on the phone, which is
/// a stronger check than an inbox anyway.
async fn people_password(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_people(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let email = common::people::normalise(&form_field(&body, "email").unwrap_or_default());
    let pw = form_field(&body, "password").unwrap_or_default();
    let again = form_field(&body, "confirm").unwrap_or_default();
    let hash = match check_password(&pw, &again) {
        Ok(h) => h,
        Err(e) => return flash_people(&state, &base, &token, &e, false),
    };

    let mut list = read_people(&state);
    let Some(i) = list.iter().position(|p| p.email == email) else {
        return flash_people(&state, &base, &token, "Nobody here has that address.", false);
    };
    list[i].hash = hash;
    list[i].must_change = form_field(&body, "must_change").is_some() && !is_owner(&state, &email);
    let must_change_note = list[i].must_change;

    match write_people(&state, &list) {
        Ok(_) => {
            tracing::info!(email = %email, "admin: password reset");
            flash_people(&state, &base, &token,
                &if must_change_note {
                    format!(
                        "New password set for {email}. The old one no longer works, and \
                         they will be asked to choose their own."
                    )
                } else {
                    format!(
                        "New password set for {email}. The old one no longer works, and \
                         any session it created is dead on their next click."
                    )
                },
                true,
            )
        }
        Err(e) => flash_people(&state, &base, &token, &format!("Could not save: {e}"), false),
    }
}

/// The submitted checkbox names -> the ALL_APPS-ordered, de-duped,
/// bogus-value-dropped list to store in Person::apps. Saving through this
/// form is always what flips Person::apps_unrestricted to false -- ticking
/// every box here is a DIFFERENT, later, narrower thing than an account that
/// was simply never touched, even though the visible checkboxes look
/// identical either way. See Person::apps_unrestricted's own doc comment.
/// The dropdowns come back as lvl_<app>=none|view|full. Anything unknown --
/// an app not shipped, a level not offered -- is dropped, same posture as
/// the old checkbox parser: the form is input, ALL_APPS and LEVELS are truth.
/// "full" stores the bare name, so files written today read identically to
/// files written before levels existed.
// -----------------------------------------------------------------------------
//  Guest invites: pre-build an account (label, which pages, how long) and
//  hand out a short link instead of an email address and a typed password.
//  See common::guest_invite for the code storage this writes, and
//  auth's passkeys.rs "Guest invites" section for how the link is redeemed.
// -----------------------------------------------------------------------------

/// Makes "Brandon" into "Brandon", a second "Brandon" into "Brandon-2", and so
/// on -- so Jason never has to think about it when he reuses a label (six
/// throwaway test accounts can all just be called "Test"). Checked against
/// every existing Person's name AND every invite already minted (redeemed or
/// not), so two invites sent the same afternoon do not collide either.
fn disambiguate_label(state: &AppState, wanted: &str) -> String {
    let wanted = wanted.trim();
    let people = read_people(state);
    let invites = common::guest_invite::read(&state.guest_invites_file);
    let taken = |candidate: &str| {
        people.iter().any(|p| p.name.eq_ignore_ascii_case(candidate))
            || invites.invites.iter().any(|i| i.label.eq_ignore_ascii_case(candidate))
    };
    if !taken(wanted) {
        return wanted.to_string();
    }
    let mut n = 2u32;
    loop {
        let candidate = format!("{wanted}-{n}");
        if !taken(&candidate) {
            return candidate;
        }
        n += 1;
    }
}

/// A 256-character random password, hashed and stored exactly like any other
/// account's -- see Person::hash -- but never shown to Jason or the guest.
/// Its only job is satisfying issue_session's existing requirement that a
/// person have SOME password on file for a passkey sign-in to validate (see
/// the big comment at the top of auth's passkeys.rs); the guest only ever
/// authenticates with their passkey. 256 characters, not 256 bits, is Jason's
/// own explicit choice -- far beyond anything brute-forceable either way, but
/// this is the length he asked for.
fn random_guest_password() -> String {
    use rand::Rng as _;
    const ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    let mut bytes = [0u8; 256];
    rand::rand_core::UnwrapErr(rand::rngs::SysRng).fill_bytes(&mut bytes);
    bytes.iter().map(|b| ALPHABET[*b as usize % ALPHABET.len()] as char).collect()
}

/// A placeholder "email" for a guest who was never asked for one.
/// people.json is keyed by email everywhere (see common::people::find) --
/// this exists purely to satisfy that, is never shown to the guest or typed
/// by anyone, and is built from the label plus random characters so two
/// invites with the same label never collide even before disambiguation is
/// considered (disambiguate_label handles the HUMAN-visible collision; this
/// handles the MACHINE key, independently, belt and suspenders).
fn synthetic_guest_email(label: &str) -> String {
    use rand::Rng as _;
    let slug: String = label
        .to_ascii_lowercase()
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '-' })
        .collect();
    let slug: String = slug.chars().take(24).collect();
    let mut suffix = [0u8; 4];
    rand::rand_core::UnwrapErr(rand::rngs::SysRng).fill_bytes(&mut suffix);
    let suffix: String = suffix.iter().map(|b| format!("{b:02x}")).collect();
    format!("guest-{slug}-{suffix}@invite.local")
}

/// Sets `guest`, `account_expires` and an empty `keys` list on a brand-new
/// entry in the auth service's passkeys.json, WITHOUT the strongly-typed
/// UserKeys/PasskeyFile structs those live as in auth/src/passkeys.rs.
///
/// WHY RAW JSON, NOT THE REAL TYPE. This binary and the auth binary are
/// separate crates with no shared library between them beyond `common` (see
/// rust/Cargo.toml's workspace members) -- importing auth's PasskeyFile here
/// would mean depending on the `auth` binary crate as a library AND pulling
/// in webauthn-rs (which stores a real Passkey object inside every existing
/// key), purely to write four fields on a user who starts with zero keys.
/// The alternative this function takes -- read the file as a generic
/// serde_json::Value, touch only the fields a brand-new guest entry needs,
/// leave every existing user's `keys` array completely untouched because it
/// is never parsed, just carried through -- changes nothing about the
/// file's real shape or any OTHER account in it, and does not require this
/// crate to understand what a Passkey object looks like at all.
///
/// Only ever called for a BRAND NEW account this same request just created
/// in people.json, so there is never an existing entry to merge against;
/// this always inserts fresh.
fn patch_new_guest_entry(
    path: &std::path::Path,
    email: &str,
    label: &str,
    account_expires: u64,
) -> std::io::Result<()> {
    let text = std::fs::read_to_string(path).unwrap_or_default();
    let mut root: serde_json::Value = if text.trim().is_empty() {
        serde_json::json!({ "users": [] })
    } else {
        serde_json::from_str(&text).map_err(|e| {
            std::io::Error::new(std::io::ErrorKind::InvalidData, format!("passkeys.json does not parse: {e}"))
        })?
    };
    let users = root
        .get_mut("users")
        .and_then(|u| u.as_array_mut())
        .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::InvalidData, "passkeys.json has no \"users\" array"))?;
    users.push(serde_json::json!({
        "email": email,
        "user_id": "",
        "label": label,
        "keys": [],
        "passkey_only": false,
        "offer_snooze_until": 0,
        "offer_never": false,
        "cap": 0,
        "guest": true,
        "account_expires": account_expires
    }));
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let out = serde_json::to_vec_pretty(&root)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, out)?;
    std::fs::rename(&tmp, path)
}

/// Any whole number of days from 1 to 365 -- free text, Jason's own request,
/// rather than the fixed 1/7/30 picker. The underlying guest-account system
/// (see auth's exec_guest/enrol_allowed) still always requires SOME end
/// date -- a guest account never has true no-expiration -- so this validates
/// a count, not a date, same "must be a real positive number, with a sane
/// ceiling so a typo cannot create an effectively-permanent account" spirit
/// as exec_guest's own parse_end_of_day check, just for a day COUNT instead
/// of a calendar date.
///
/// 365 is the ceiling, not a magic number: the one passkey a guest account
/// may hold is capped at a 30-day loaner internally (see auth's
/// enrol_allowed/fit_loaner_days) regardless of how long the ACCOUNT itself
/// lasts, so a very long custom expiry (anything over 30 days) means the
/// guest's one passkey will need remaking once it hits that 30-day loaner
/// ceiling even though the account stays valid -- worth knowing, not a bug.
fn guest_expiry(days: u64, now: u64) -> Result<u64, &'static str> {
    match days {
        1..=365 => Ok(now + days * 86400),
        0 => Err("Pick at least 1 day."),
        _ => Err("365 days is the most an invite can run for."),
    }
}

async fn people_invite_add(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_people(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let raw_label = form_field(&body, "label").unwrap_or_default();
    let raw_label = raw_label.trim();
    if raw_label.is_empty() {
        return flash_people(&state, &base, &token, "Give the invite a label -- a name for your own reference.", false);
    }
    if raw_label.chars().count() > 40 {
        return flash_people(&state, &base, &token, "Keep the label to 40 characters or fewer.", false);
    }
    let days: u64 = form_field(&body, "days").unwrap_or_default().parse().unwrap_or(0);
    let now = common::people::now();
    let expires = match guest_expiry(days, now) {
        Ok(e) => e,
        Err(m) => return flash_people(&state, &base, &token, m, false),
    };

    let label = disambiguate_label(&state, raw_label);
    let email = synthetic_guest_email(&label);
    let apps = apps_from_levels(|k| form_field(&body, k));
    if apps.is_empty() {
        return flash_people(&state, &base, &token,
            "Pick at least one page for this guest -- an invite with no pages would sign them in to nothing.", false);
    }

    let hash = match common::people::hash_password(&random_guest_password()) {
        Ok(h) => h,
        Err(e) => return flash_people(&state, &base, &token, &format!("Could not set that up: {e}"), false),
    };
    let mut person = common::people::Person::new(&email);
    person.hash = hash;
    person.must_change = false;
    person.apps_unrestricted = false;
    person.apps = apps;
    person.name = label.clone();

    let mut people = read_people(&state);
    people.push(person);
    if let Err(e) = write_people(&state, &people) {
        return flash_people(&state, &base, &token, &format!("Could not save the account: {e}"), false);
    }

    if let Err(e) = patch_new_guest_entry(&state.auth_passkeys_file, &email, &label, expires) {
        tracing::error!(email = %email, "could not set up the guest passkey entry: {e}");
        return flash_people(&state, &base, &token,
            &format!("The account was created but the guest/passkey settings could not be saved: {e}. \
                      Remove {email} on this page before trying again."),
            false);
    }

    let code = common::guest_invite::new_code();
    let mut store = common::guest_invite::read(&state.guest_invites_file);
    store.invites.push(common::guest_invite::Invite {
        code_hash: common::guest_invite::code_hash(&code),
        target_email: email.clone(),
        label: label.clone(),
        days,
        created: now,
        redeemed: false,
        redeemed_at: 0,
    });
    if let Err(e) = common::guest_invite::write(&state.guest_invites_file, &store) {
        tracing::error!(email = %email, "could not save the invite code: {e}");
        return flash_people(&state, &base, &token,
            &format!("The account was created but the invite link could not be saved: {e}. \
                      Remove {email} on this page before trying again."),
            false);
    }

    tracing::info!(label = %label, days, "admin: guest invite minted");
    let link = format!("{}/invite?c={}", state.invite_link_base.trim_end_matches('/'), code);
    flash_people(&state, &base, &token,
        &format!(
            "Invite ready for \"{label}\" (expires in {days} day{}). Send this link -- it works once: {link}",
            if days == 1 { "" } else { "s" }
        ),
        true)
}

fn apps_from_levels(get: impl Fn(&str) -> Option<String>) -> Vec<String> {
    let mut out = Vec::new();
    for &a in common::people::ALL_APPS {
        match get(&format!("lvl_{a}")).as_deref() {
            Some("full") => out.push(a.to_string()),
            Some(l) if common::people::LEVELS.iter().any(|(k, _)| *k == l) => {
                out.push(format!("{a}:{l}"))
            }
            _ => {}
        }
    }
    out
}

/// Which pages a person may reach through the shared sign-in -- see
/// common::people::Person::apps and app_for_uri() in the auth service.
async fn people_apps(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_people(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let email = common::people::normalise(&form_field(&body, "email").unwrap_or_default());

    // An owner reaches everything regardless of this list -- is_owner() is
    // checked ahead of it everywhere that matters -- so narrowing it here
    // would look like it does something it does not.
    if is_owner(&state, &email) {
        return flash_people(&state, &base, &token,
            &format!("{email} is your own account, so access here is not narrowed by this."), false);
    }

    let apps = apps_from_levels(|k| form_field(&body, k));

    let mut list = read_people(&state);
    let Some(i) = list.iter().position(|p| p.email == email) else {
        return flash_people(&state, &base, &token, "Nobody here has that address.", false);
    };
    // Touching Save is the explicit narrowing action -- see
    // apps_unrestricted's own doc comment for why this can never again be
    // inferred from apps being empty.
    list[i].apps_unrestricted = false;
    list[i].apps = apps.clone();

    // The start-page look, from the same form and the same Save button.
    //
    // VALIDATED AGAINST THE SHIPPED LIST, not trusted. This arrives as a form
    // field and a form field is whatever the sender typed; an unknown value
    // falls back to plain rather than being stored, so a renamed or removed
    // theme leaves somebody with a boring page instead of a broken one.
    //
    // It grants nothing -- see Person::theme. Appearance and access are
    // separate decisions that happen to share a button.
    // Their name, from the same form. Trimmed and length-capped: this is
    // rendered on their page, and an unbounded form field is not something to
    // paste into HTML on trust. Askama escapes it on the way out as well.
    let name = form_field(&body, "name").unwrap_or_default();
    let name = name.trim();
    list[i].name = name.chars().take(40).collect();

    let theme = form_field(&body, "theme").unwrap_or_default();
    list[i].theme = if common::people::theme_exists(&theme) {
        theme
    } else {
        String::new()
    };
    let theme_label = common::people::THEMES
        .iter()
        .find(|(k, _, _, _)| *k == list[i].theme)
        .map(|(_, _, label, _)| *label)
        .unwrap_or("Plain");

    match write_people(&state, &list) {
        Ok(_) => {
            tracing::info!(email = %email, apps = ?apps, "admin: access changed");
            let msg = if apps.is_empty() {
                format!("{email} can no longer reach anything on the list. \
                        Tick at least one page, or Remove them if that is what you meant.")
            } else if apps.len() == common::people::ALL_APPS.len() {
                format!("{email} can reach everything -- ticking every box here is still \
                        narrower than never touching this at all, so a page added later \
                        will need its own box ticked for them.")
            } else {
                format!("{email} can now reach: {}. Start page: {}.", apps.join(", "), theme_label)
            };
            flash_people(&state, &base, &token, &msg, true)
        }
        Err(e) => flash_people(&state, &base, &token, &format!("Could not save: {e}"), false),
    }
}

async fn people_remove(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_people(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let email = common::people::normalise(&form_field(&body, "email").unwrap_or_default());
    // AN OWNER CANNOT BE REMOVED. This page is a column of Remove buttons and
    // the one next to your own address does nothing you would ever want. There
    // is no confirmation dialog here and there should not be one - a refusal
    // that cannot be clicked through is better than a question that can.
    if is_owner(&state, &email) {
        return flash_people(&state, &base, &token,
            &format!("{email} is your own account, so it cannot be removed here. \
                      Edit ADMIN_OWNERS in .env if that is really what you want."), false);
    }

    let mut list = read_people(&state);
    let before = list.len();
    list.retain(|p| p.email != email);
    if list.len() == before {
        return flash_people(&state, &base, &token, "That address was not on the list.", false);
    }
    // REFUSE TO EMPTY THE LIST. An empty allowlist means nobody can sign in to
    // anything, including you - and the only way back is editing a file on the
    // PC. Easy to do by accident when tidying up; impossible to undo remotely.
    if list.is_empty() {
        return flash_people(&state, &base, &token,
            "That is the last person on the list. Removing them would lock everyone \
             out, including you, so it was not saved. Add someone else first.", false);
    }
    match write_people(&state, &list) {
        Ok(_) => {
            tracing::info!(email = %email, "admin: removed from the sign-in list");
            flash_people(&state, &base, &token,
                &format!("{email} removed. Any session they had is dead on their next click."), true)
        }
        Err(e) => flash_people(&state, &base, &token, &format!("Could not save: {e}"), false),
    }
}

// -----------------------------------------------------------------------------
//  The picture backlog
//
//  WHAT THIS IS FOR. Kitchen and kb each already have a real way to find or
//  draw a picture for something missing one -- Kitchen's guide page has a
//  "Draw pictures" button, kb's article page has "Find a photo" / upload /
//  generate, all per-item. What neither has is a single place that says
//  "here is everything, across both, still waiting on one" -- so this page
//  is deliberately just an AGGREGATE, a LINK, and an APPROVE button. It does
//  not draw or fetch a single picture itself; that stays where it already
//  works, one click away.
//
//  THE GPU CONTROL exists because of one thing caught live while building
//  this: on a 12GB card, Ollama routinely leaves 9+ GB pinned to whatever
//  chat model was last asked something (expires_at years in the future, not
//  Ollama's own 10-minute default -- something keeps re-triggering it before
//  it lapses), which leaves imagine (the actual picture-drawing service, 8.2GB
//  needed) with no room. "Free the GPU" unloads whatever Ollama currently
//  has loaded via its own HTTP API (POST /api/generate with keep_alive: 0,
//  Ollama's documented immediate-unload call) so a click-through to draw a
//  picture actually has somewhere to run. Nothing here changes
//  OLLAMA_KEEP_ALIVE or any other config -- it is a live, on-demand action,
//  and whatever was unloaded reloads itself the next time anything asks it a
//  question, so there is no separate "turn back on" step.
// -----------------------------------------------------------------------------

/// One row, from either service, normalised to what the table actually shows.
struct PictureRow {
    source: &'static str,
    /// "kitchen:<slug>" / "kb:<id>" -- what gets recorded on Approve, and
    /// what filters an already-approved row back out on the next load.
    key: String,
    title: String,
    missing: u32,
    url: String,
}

/// Best-effort GET+parse against one of Kitchen/kb's internal endpoints.
/// Down, slow, or answering nonsense all read the same way here: an empty
/// list, never a broken page. The GPU status card and the row count are
/// enough to notice something is wrong without a stack trace on screen.
/// The network call. Kept to just fetch-and-hand-off so parse_missing below
/// (the part actually worth getting right) can be unit tested without a
/// mock server -- a malformed or partial JSON body is the realistic failure
/// mode here, not a connection error, and that's what parse_missing covers.
async fn fetch_missing(
    client: &reqwest::Client,
    url: &str,
    secret_header: &'static str,
    secret: &str,
    source: &'static str,
    id_key: &'static str,
) -> Vec<PictureRow> {
    let Ok(resp) = client.get(url).header(secret_header, secret).send().await else {
        return Vec::new();
    };
    let Ok(items): Result<Vec<serde_json::Value>, _> = resp.json().await else {
        return Vec::new();
    };
    parse_missing(items, source, id_key)
}

/// One malformed row (a missing field, wrong type) is dropped rather than
/// failing the whole batch -- 181 real rows beat zero rows because the
/// 182nd was odd.
///
/// CAUGHT LIVE (2026-08-25), Jason's own report browsing /pictures: "I
/// don't see food items" -- the id field was required to be a JSON
/// STRING (`.as_str()`), which Kitchen's recipe/kb ids (slugs, string
/// ids) satisfy, but Tandoor's real food/tool ids are plain integers
/// (confirmed live: every one of 1017 food rows and all 114 tool rows
/// from Kitchen's own /internal/foods-missing-pictures and
/// /internal/tools-missing-pictures came back as a JSON number) -- so
/// `.as_str()` returned None and `filter_map` silently dropped every
/// single one of them, not just foods. `id_as_string` below accepts
/// either shape.
fn id_as_string(v: &serde_json::Value) -> Option<String> {
    v.as_str().map(str::to_string).or_else(|| v.as_i64().map(|n| n.to_string()))
}

fn parse_missing(items: Vec<serde_json::Value>, source: &'static str, id_key: &str) -> Vec<PictureRow> {
    items
        .into_iter()
        .filter_map(|v| {
            let title = v.get("title")?.as_str()?.to_string();
            let id = id_as_string(v.get(id_key)?)?;
            let missing = v.get("missing")?.as_u64()? as u32;
            let url = v.get("url")?.as_str()?.to_string();
            Some(PictureRow {
                source,
                key: format!("{source}:{id}"),
                title,
                missing,
                url,
            })
        })
        .collect()
}

/// Kitchen's ingredient-warnings queue. Same fetch-and-forgive shape as
/// fetch_missing above (a down/slow/nonsense response is just an empty
/// list, never a broken page), but a different JSON shape -- a warning has
/// a `line` and a `why`, not a `missing` count -- so it gets its own
/// fetch/parse pair rather than being forced through parse_missing.
async fn fetch_food_warnings(state: &AppState) -> Vec<WarningRowView> {
    let Ok(resp) = state
        .http
        .get(format!("{}/internal/food-warnings", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .send()
        .await
    else {
        return Vec::new();
    };
    let Ok(items): Result<Vec<serde_json::Value>, _> = resp.json().await else {
        return Vec::new();
    };
    parse_warnings(items)
}

fn parse_warnings(items: Vec<serde_json::Value>) -> Vec<WarningRowView> {
    items
        .into_iter()
        .filter_map(|v| {
            let id = v.get("id")?.as_u64()?.to_string();
            let title = v.get("title")?.as_str()?.to_string();
            let line = v.get("line")?.as_str()?.to_string();
            let why = v.get("why")?.as_str()?.to_string();
            let url = v.get("url")?.as_str()?.to_string();
            Some(WarningRowView { id, title, line, why, url })
        })
        .collect()
}

/// Same shape/gate pattern as fetch_food_warnings above, for Kitchen's
/// dedupe.py output. Takes `base` (the request's own site-prefix, same
/// as everywhere else in this file that builds a link) since the row's
/// usage_url needs it and this function has no other way to know it.
async fn fetch_merge_proposals(state: &AppState, base: &str) -> Vec<MergeRowView> {
    let Ok(resp) = state
        .http
        .get(format!("{}/internal/merge-proposals", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .send()
        .await
    else {
        return Vec::new();
    };
    let Ok(items): Result<Vec<serde_json::Value>, _> = resp.json().await else {
        return Vec::new();
    };
    parse_merge_proposals(base, items)
}

fn parse_merge_proposals(base: &str, items: Vec<serde_json::Value>) -> Vec<MergeRowView> {
    items
        .into_iter()
        .filter_map(|v| {
            let id = v.get("id")?.as_u64()?.to_string();
            let kind = v.get("kind")?.as_str()?.to_string();
            let item_id = v.get("item_id")?.as_u64()?.to_string();
            let item_name = v.get("item_name")?.as_str()?.to_string();
            let target_name = v.get("target_name")?.as_str()?.to_string();
            let reason = v.get("reason")?.as_str()?.to_string();
            let usage_url = format!("{base}/pictures/usage/{kind}/{item_id}");
            Some(MergeRowView { id, kind, item_name, target_name, reason, usage_url })
        })
        .collect()
}

/// Same shape/gate pattern as fetch_merge_proposals above.
async fn fetch_nutrition_review(state: &AppState) -> Vec<NutritionRowView> {
    let Ok(resp) = state
        .http
        .get(format!("{}/internal/nutrition-review", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .send()
        .await
    else {
        return Vec::new();
    };
    let Ok(items): Result<Vec<serde_json::Value>, _> = resp.json().await else {
        return Vec::new();
    };
    parse_nutrition_review(items)
}

fn parse_nutrition_review(items: Vec<serde_json::Value>) -> Vec<NutritionRowView> {
    items
        .into_iter()
        .filter_map(|v| {
            let id = v.get("id")?.as_u64()?.to_string();
            let name = v.get("name")?.as_str()?.to_string();
            let cached_name = v.get("cached_name").and_then(|s| s.as_str()).unwrap_or("?").to_string();
            let local_name = v.get("local_name").and_then(|s| s.as_str()).unwrap_or("?").to_string();
            let cached_cal = v
                .get("cached_cal")
                .and_then(|x| x.as_f64())
                .map(|x| format!("{x:.0}"))
                .unwrap_or_else(|| "?".into());
            let local_cal = v
                .get("local_cal")
                .and_then(|x| x.as_f64())
                .map(|x| format!("{x:.0}"))
                .unwrap_or_else(|| "?".into());
            Some(NutritionRowView { id, name, cached_name, cached_cal, local_name, local_cal })
        })
        .collect()
}

/// Every source's rows, minus whatever's already approved, worst first.
/// Pulled out of render_pictures so it's testable without a real HTTP round
/// trip. Takes a Vec-of-Vecs rather than one param per source so a fifth
/// source is one more entry in the call site's list, not a new parameter
/// threaded through every caller.
fn build_rows(sources: Vec<Vec<PictureRow>>, approved: &HashSet<String>) -> Vec<PictureRowView> {
    let mut rows: Vec<PictureRowView> = sources
        .into_iter()
        .flatten()
        .filter(|r| !approved.contains(&r.key))
        .map(|r| PictureRowView {
            source: r.source.to_string(),
            key: r.key,
            title: r.title,
            missing: r.missing,
            url: r.url,
        })
        .collect();
    rows.sort_by(|a, b| b.missing.cmp(&a.missing));
    rows
}

async fn fetch_kitchen_missing(state: &AppState) -> Vec<PictureRow> {
    fetch_missing(
        &state.http,
        &format!("{}/internal/missing-pictures", state.kitchen_url),
        "X-Internal-Secret",
        &state.kitchen_secret,
        "kitchen",
        "slug",
    )
    .await
}

async fn fetch_kb_missing(state: &AppState) -> Vec<PictureRow> {
    fetch_missing(
        &state.http,
        &format!("{}/internal/missing-pictures", state.kb_url),
        "X-KB-Secret",
        &state.kb_secret,
        "kb",
        "id",
    )
    .await
}

/// Foods and Tools both live in Kitchen (see Kitchen's foodimages.py /
/// toolimages.py) and share its secret -- same reasoning as
/// fetch_kitchen_missing, just a different pair of internal routes.
async fn fetch_food_missing(state: &AppState) -> Vec<PictureRow> {
    fetch_missing(
        &state.http,
        &format!("{}/internal/foods-missing-pictures", state.kitchen_url),
        "X-Internal-Secret",
        &state.kitchen_secret,
        "food",
        "id",
    )
    .await
}

async fn fetch_tool_missing(state: &AppState) -> Vec<PictureRow> {
    fetch_missing(
        &state.http,
        &format!("{}/internal/tools-missing-pictures", state.kitchen_url),
        "X-Internal-Secret",
        &state.kitchen_secret,
        "tool",
        "id",
    )
    .await
}

/// One model Ollama currently holds in VRAM.
struct LoadedModel {
    name: String,
    gb: f64,
}

async fn ollama_loaded(state: &AppState) -> Vec<LoadedModel> {
    let Ok(resp) = state
        .http
        .get(format!("{}/api/ps", state.ollama_url))
        .send()
        .await
    else {
        return Vec::new();
    };
    let Ok(v): Result<serde_json::Value, _> = resp.json().await else {
        return Vec::new();
    };
    v.get("models")
        .and_then(|m| m.as_array())
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .filter_map(|m| {
            let name = m.get("name")?.as_str()?.to_string();
            // size_vram is what is actually resident; size is the file on
            // disk. They're usually the same number but size_vram is the
            // one that means "this is what the card is holding right now".
            let bytes = m
                .get("size_vram")
                .or_else(|| m.get("size"))?
                .as_f64()?;
            Some(LoadedModel { name, gb: bytes / 1_000_000_000.0 })
        })
        .collect()
}

struct ImagineStatus {
    reachable: bool,
    room: bool,
    free_mb: i64,
    need_mb: i64,
}

async fn imagine_status(state: &AppState) -> ImagineStatus {
    let unreachable = ImagineStatus { reachable: false, room: false, free_mb: 0, need_mb: 0 };
    let Ok(resp) = state
        .http
        .get(format!("{}/health", state.imagine_url))
        .send()
        .await
    else {
        return unreachable;
    };
    let Ok(v): Result<serde_json::Value, _> = resp.json().await else {
        return unreachable;
    };
    ImagineStatus {
        reachable: true,
        room: v.get("room").and_then(|x| x.as_bool()).unwrap_or(false),
        free_mb: v.get("free_mb").and_then(|x| x.as_i64()).unwrap_or(0),
        need_mb: v.get("need_mb").and_then(|x| x.as_i64()).unwrap_or(0),
    }
}

/// Unload every model Ollama currently has loaded. Returns the names it
/// actually asked Ollama to drop -- a model that was never loaded is not in
/// this list, and neither is one whose unload request failed to send.
async fn free_gpu(state: &AppState) -> Vec<String> {
    let loaded = ollama_loaded(state).await;
    let mut unloaded = Vec::new();
    for m in &loaded {
        let sent = state
            .http
            .post(format!("{}/api/generate", state.ollama_url))
            .json(&serde_json::json!({"model": m.name, "keep_alive": 0}))
            .send()
            .await;
        if sent.is_ok() {
            unloaded.push(m.name.clone());
        }
    }
    unloaded
}

fn read_approved(state: &AppState) -> HashSet<String> {
    std::fs::read_to_string(&state.approved_file)
        .ok()
        .and_then(|s| serde_json::from_str::<Vec<String>>(&s).ok())
        .map(|v| v.into_iter().collect())
        .unwrap_or_default()
}

fn write_approved(state: &AppState, set: &HashSet<String>) -> std::io::Result<()> {
    let mut v: Vec<&String> = set.iter().collect();
    v.sort();
    let json = serde_json::to_string_pretty(&v).unwrap_or_else(|_| "[]".to_string());
    if let Some(dir) = state.approved_file.parent() {
        std::fs::create_dir_all(dir).ok();
    }
    std::fs::write(&state.approved_file, json)
}

async fn pictures_page(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    let (flash, ok) = take_flash(&state, &token);
    render_pictures(&state, &base, &csrf, &flash, ok).await
}

async fn render_pictures(state: &AppState, base: &str, csrf: &str, flash: &str, ok: bool) -> Response {
    let approved = read_approved(state);
    let (kitchen_rows, kb_rows, food_rows, tool_rows, warning_rows, merge_rows, nutrition_rows, loaded, imagine) = tokio::join!(
        fetch_kitchen_missing(state),
        fetch_kb_missing(state),
        fetch_food_missing(state),
        fetch_tool_missing(state),
        fetch_food_warnings(state),
        fetch_merge_proposals(state, base),
        fetch_nutrition_review(state),
        ollama_loaded(state),
        imagine_status(state),
    );

    let rows = build_rows(vec![kitchen_rows, kb_rows, food_rows, tool_rows], &approved);

    let loaded_models: Vec<String> = loaded
        .iter()
        .map(|m| format!("{} ({:.1} GB)", m.name, m.gb))
        .collect();

    let html = PicturesTemplate {
        base: base.to_string(),
        csrf: csrf.to_string(),
        flash: flash.to_string(),
        flash_ok: ok,
        total: rows.len(),
        rows,
        warning_total: warning_rows.len(),
        warning_rows,
        merge_total: merge_rows.len(),
        merge_rows,
        nutrition_total: nutrition_rows.len(),
        nutrition_rows,
        loaded_models,
        gpu_reachable: imagine.reachable,
        gpu_room: imagine.room,
        gpu_free_mb: imagine.free_mb,
        gpu_need_mb: imagine.need_mb,
        year: common::current_year(),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    Html(html).into_response()
}

fn flash_pictures(state: &AppState, base: &str, token: &str, msg: &str, ok: bool) -> Response {
    flash_to(state, token, msg, ok, &format!("{base}/pictures"))
}

/// Dismiss ONE ingredient warning -- calls back into Kitchen's own
/// /internal/food-warnings/{id}/clear (secret-gated, same as the GET),
/// which really deletes the row. Unlike Approve above, there is no local
/// "already seen" file for these -- Kitchen's own database is the one
/// true list, so there's nothing here to fall out of sync with it.
async fn pictures_dismiss_warning(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_pictures(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let id = form_field(&body, "id").unwrap_or_default();
    if id.is_empty() {
        return flash_pictures(&state, &base, &token, "Nothing to dismiss.", false);
    }
    let sent = state
        .http
        .post(format!("{}/internal/food-warnings/{id}/clear", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .send()
        .await;
    match sent {
        Ok(r) if r.status().is_success() => {
            flash_pictures(&state, &base, &token, "Dismissed.", true)
        }
        _ => flash_pictures(&state, &base, &token, "Could not reach Kitchen to dismiss it.", false),
    }
}

/// Approve ONE merge proposal -- this is the first point anything Phi
/// proposed actually touches Tandoor. Kitchen's own
/// /internal/merge-proposals/{id}/approve does the real merge (through
/// Tandoor's own native merge endpoint, see tandoor.py's merge_food/
/// merge_tool) and only clears the row if that succeeded.
async fn pictures_merge_approve(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_pictures(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let id = form_field(&body, "id").unwrap_or_default();
    if id.is_empty() {
        return flash_pictures(&state, &base, &token, "Nothing to approve.", false);
    }
    let sent = state
        .http
        .post(format!("{}/internal/merge-proposals/{id}/approve", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .send()
        .await;
    // Same real bug as llm_findings_approve's own copy of this call --
    // see that function's comment. Kitchen answers HTTP 200 either way;
    // the actual outcome only lives in the JSON body's "ok" field.
    match sent {
        Ok(r) if r.status().is_success() => {
            let data: serde_json::Value = r.json().await.unwrap_or_default();
            if data.get("ok").and_then(|b| b.as_bool()).unwrap_or(false) {
                flash_pictures(&state, &base, &token, "Merged.", true)
            } else {
                let err = data.get("error").and_then(|s| s.as_str())
                    .unwrap_or("Kitchen could not complete that merge.");
                flash_pictures(&state, &base, &token, err, false)
            }
        }
        _ => flash_pictures(&state, &base, &token, "Could not reach Kitchen to merge it.", false),
    }
}

async fn pictures_merge_reject(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_pictures(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let id = form_field(&body, "id").unwrap_or_default();
    if id.is_empty() {
        return flash_pictures(&state, &base, &token, "Nothing to reject.", false);
    }
    let sent = state
        .http
        .post(format!("{}/internal/merge-proposals/{id}/reject", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .send()
        .await;
    match sent {
        Ok(r) if r.status().is_success() => {
            flash_pictures(&state, &base, &token, "Rejected.", true)
        }
        _ => flash_pictures(&state, &base, &token, "Could not reach Kitchen to reject it.", false),
    }
}

/// Jason's own request (2026-08-25): "can the llm match all the food
/// items to other items really quick and give me a /llm-findings page,
/// I would like a notice when it's done and a link on from telegram."
/// A dedicated page for exactly this workflow -- trigger the run, walk
/// away, come back (or just wait for the Telegram message) to review
/// what it found. Reuses the SAME merge_proposals data /pictures already
/// shows (fetch_merge_proposals, unchanged) rather than a second copy of
/// that fetch/parse logic.
#[derive(Template)]
#[template(path = "llm-findings.html")]
struct LlmFindingsTemplate {
    base: String,
    csrf: String,
    flash: String,
    flash_ok: bool,
    rows: Vec<MergeRowView>,
    total: usize,
    running: bool,
    year: i32,
}

async fn llm_findings_page(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    let (flash, flash_ok) = take_flash(&state, &token);
    let rows = fetch_merge_proposals(&state, &base).await;
    let running = match state
        .http
        .get(format!("{}/internal/dedupe-status", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => r
            .json::<serde_json::Value>()
            .await
            .ok()
            .and_then(|v| v.get("running").and_then(|b| b.as_bool()))
            .unwrap_or(false),
        _ => false,
    };
    let html = LlmFindingsTemplate {
        base: base.to_string(),
        csrf,
        flash,
        flash_ok,
        total: rows.len(),
        rows,
        running,
        year: common::current_year(),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    Html(html).into_response()
}

fn flash_findings(state: &AppState, base: &str, token: &str, msg: &str, ok: bool) -> Response {
    flash_to(state, token, msg, ok, &format!("{base}/llm-findings"))
}

async fn llm_findings_run(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_findings(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let sent = state
        .http
        .post(format!("{}/internal/dedupe-run", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .send()
        .await;
    match sent {
        Ok(r) if r.status().is_success() => {
            let d: serde_json::Value = r.json().await.unwrap_or_default();
            if d.get("already_running").and_then(|b| b.as_bool()).unwrap_or(false) {
                flash_findings(&state, &base, &token, "Already running.", true)
            } else {
                flash_findings(&state, &base, &token,
                    "Started -- you'll get a Telegram message when it's done.", true)
            }
        }
        _ => flash_findings(&state, &base, &token, "Could not reach Kitchen to start it.", false),
    }
}

async fn llm_findings_tidy(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_findings(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let sent = state
        .http
        .post(format!("{}/internal/tidy-names-run", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .timeout(Duration::from_secs(120))
        .send()
        .await;
    match sent {
        Ok(r) if r.status().is_success() => {
            let d: serde_json::Value = r.json().await.unwrap_or_default();
            let renamed = d.get("renamed").and_then(|n| n.as_u64()).unwrap_or(0);
            let merged = d.get("merged").and_then(|n| n.as_u64()).unwrap_or(0);
            flash_findings(&state, &base, &token,
                &format!("Tidied {renamed} name(s), merged {merged} into an existing item."), true)
        }
        _ => flash_findings(&state, &base, &token, "Could not reach Kitchen to run it.", false),
    }
}

async fn llm_findings_approve(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_findings(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let id = form_field(&body, "id").unwrap_or_default();
    if id.is_empty() {
        return flash_findings(&state, &base, &token, "Nothing to approve.", false);
    }
    let sent = state
        .http
        .post(format!("{}/internal/merge-proposals/{id}/approve", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .send()
        .await;
    // CAUGHT LIVE (2026-08-25), Jason's own report: "the changes don't
    // stick." Kitchen's own route always answers HTTP 200 -- even when
    // the merge itself failed -- and reports the REAL outcome only in
    // the JSON body's "ok" field (see internal_merge_proposal_approve's
    // own docstring: it only clears the proposal `if ok:`). This only
    // ever checked r.status().is_success(), which is true either way, so
    // a failed merge still flashed "Merged." and the un-cleared proposal
    // just sat there looking untouched on the next page load -- exactly
    // "doesn't stick." Now reads the body's own "ok" field.
    match sent {
        Ok(r) if r.status().is_success() => {
            let data: serde_json::Value = r.json().await.unwrap_or_default();
            if data.get("ok").and_then(|b| b.as_bool()).unwrap_or(false) {
                flash_findings(&state, &base, &token, "Merged.", true)
            } else {
                let err = data.get("error").and_then(|s| s.as_str())
                    .unwrap_or("Kitchen could not complete that merge.");
                flash_findings(&state, &base, &token, err, false)
            }
        }
        _ => flash_findings(&state, &base, &token, "Could not reach Kitchen to approve it.", false),
    }
}

async fn llm_findings_reject(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_findings(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let id = form_field(&body, "id").unwrap_or_default();
    if id.is_empty() {
        return flash_findings(&state, &base, &token, "Nothing to reject.", false);
    }
    let sent = state
        .http
        .post(format!("{}/internal/merge-proposals/{id}/reject", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .send()
        .await;
    match sent {
        Ok(r) if r.status().is_success() => flash_findings(&state, &base, &token, "Rejected.", true),
        _ => flash_findings(&state, &base, &token, "Could not reach Kitchen to reject it.", false),
    }
}

/// One decision on a nutrition-review row -- `choice` is "local" (write
/// the local USDA match as the new baseline), "cached" (leave the
/// existing cache alone), or "skip" -- either way the row leaves the
/// queue once Jason has actually looked at it.
async fn pictures_nutrition_apply(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_pictures(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let id = form_field(&body, "id").unwrap_or_default();
    let choice = form_field(&body, "choice").unwrap_or_default();
    if id.is_empty() {
        return flash_pictures(&state, &base, &token, "Nothing to apply.", false);
    }
    let sent = state
        .http
        .post(format!("{}/internal/nutrition-review/{id}/apply", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .form(&[("choice", choice.as_str())])
        .send()
        .await;
    match sent {
        Ok(r) if r.status().is_success() => {
            flash_pictures(&state, &base, &token, "Saved.", true)
        }
        _ => flash_pictures(&state, &base, &token, "Could not reach Kitchen.", false),
    }
}

/// One row on the usage page -- one recipe that uses the food/tool being
/// reviewed. `original_text` is empty for a tool (a tool is a recipe-level
/// tag, not an ingredient line, so there is no line to quote).
struct UsageRowView {
    title: String,
    original_text: String,
    guide_url: String,
    edit_url: String,
}

#[derive(Template)]
#[template(path = "usage.html")]
struct UsageTemplate {
    base: String,
    kind: String,
    recipes: Vec<UsageRowView>,
    year: i32,
}

/// Jason's own words (2026-08-24), asked for the SAME turn he asked for
/// conservative auto-merge: "let me see the recipe where it comes from...
/// open up the actual recipe and read it for myself" before deciding
/// whether a proposed merge makes sense, and "if it's an error, let me
/// change how it is in the recipe" -- guide_url reads it, edit_url fixes
/// it, both real Kitchen pages, not something reimplemented here.
async fn pictures_usage(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    axum::extract::Path((kind, item_id)): axum::extract::Path<(String, String)>,
) -> Response {
    let base = base_path(&headers);
    let Some(_) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    let path = if kind == "tool" {
        format!("/internal/tool-usage/{item_id}")
    } else {
        format!("/internal/food-usage/{item_id}")
    };
    let items: Vec<serde_json::Value> = match state
        .http
        .get(format!("{}{path}", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .send()
        .await
    {
        Ok(r) => r.json().await.unwrap_or_default(),
        Err(_) => Vec::new(),
    };
    let recipes: Vec<UsageRowView> = items
        .into_iter()
        .filter_map(|v| {
            let title = v.get("title")?.as_str()?.to_string();
            let original_text = v.get("original_text").and_then(|s| s.as_str()).unwrap_or("").to_string();
            let guide_url = v.get("guide_url")?.as_str()?.to_string();
            let edit_url = v.get("edit_url")?.as_str()?.to_string();
            Some(UsageRowView { title, original_text, guide_url, edit_url })
        })
        .collect();
    let html = UsageTemplate {
        base: base.to_string(),
        kind,
        recipes,
        year: common::current_year(),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    Html(html).into_response()
}

/// One food or tool on the items-management page -- Jason's own request
/// (2026-08-24): "I would like to manage all the tools and food items
/// there with .json files." Covers EVERY food/tool now, not just
/// picture-having ones -- CAUGHT LIVE: his own example was a just-added
/// "coriander" that had no picture yet and so was invisible here under
/// the old, narrower scoping.
struct ItemRowView {
    id: String,
    name: String,
    picture: String,
    photo_url: String,
    used_in: Vec<String>,
    used_in_more: usize,
    info_json: String,
    edited: bool,
}

/// The where-to-find/how-to-use/history/fun-fact card as one pretty JSON
/// blob -- Jason's own request (2026-08-24): "input a Json file by
/// exporting the fields to put to an llm like google and paste it back
/// into to use back into the page." One textarea to copy out, paste into
/// Gemini, paste the edited result back into, instead of four.
///
/// `id`/`name` ride along IN the JSON, not just in the page around it --
/// Jason's own follow-up (2026-08-24): "will it help... to add a pinch of
/// salt for export and import when doing this in bulk." Once several of
/// these are copied out together for one LLM conversation, the page's own
/// heading is gone; without a name inside the blob itself Gemini has four
/// blank fields and no idea what it's writing about. `id` is never read
/// back from THIS single-item form (the hidden `id` field still drives
/// the save target -- see items_save), only from the bulk form below.
fn item_info_json(id: &str, name: &str, where_to_find: &str, how_to_use: &str, history: &str, fun_fact: &str) -> String {
    serde_json::to_string_pretty(&serde_json::json!({
        "id": id,
        "name": name,
        "where_to_find": where_to_find,
        "how_to_use": how_to_use,
        "history": history,
        "fun_fact": fun_fact,
    }))
    .unwrap_or_default()
}

#[derive(Template)]
#[template(path = "items.html")]
struct ItemsTemplate {
    base: String,
    csrf: String,
    flash: String,
    flash_ok: bool,
    kind: String,
    items: Vec<ItemRowView>,
    total: usize,
    bulk_json: String,
    year: i32,
}

async fn items_page(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    axum::extract::Query(params): axum::extract::Query<HashMap<String, String>>,
) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    let (flash, ok) = take_flash(&state, &token);
    let kind = match params.get("kind").map(|s| s.as_str()) {
        Some("tool") => "tool",
        _ => "food",
    };
    // A 60s override, not the shared client's 15s default -- CAUGHT LIVE:
    // once Kitchen's own /internal/item-info-list stopped skipping
    // no-picture items (1300+ foods instead of a few hundred), it started
    // taking ~20s to answer (a usage lookup per item, run concurrently
    // Kitchen-side but still real network time), same shape as the
    // reparse-draw route's own override just above.
    let items: Vec<serde_json::Value> = match state
        .http
        .get(format!("{}/internal/item-info-list/{kind}", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .timeout(Duration::from_secs(60))
        .send()
        .await
    {
        Ok(r) => r.json().await.unwrap_or_default(),
        Err(_) => Vec::new(),
    };
    let mut bulk: Vec<serde_json::Value> = Vec::new();
    let rows: Vec<ItemRowView> = items
        .into_iter()
        .filter_map(|v| {
            let id = v.get("id")?.as_u64()?.to_string();
            let name = v.get("name")?.as_str()?.to_string();
            let where_to_find = v.get("where_to_find").and_then(|s| s.as_str()).unwrap_or("").to_string();
            let how_to_use = v.get("how_to_use").and_then(|s| s.as_str()).unwrap_or("").to_string();
            let history = v.get("history").and_then(|s| s.as_str()).unwrap_or("").to_string();
            let fun_fact = v.get("fun_fact").and_then(|s| s.as_str()).unwrap_or("").to_string();
            let all_used: Vec<String> = v
                .get("used_in")
                .and_then(|u| u.as_array())
                .map(|arr| arr.iter().filter_map(|s| s.as_str().map(str::to_string)).collect())
                .unwrap_or_default();
            let used_in_more = all_used.len().saturating_sub(4);
            bulk.push(serde_json::json!({
                "id": id, "name": name,
                "where_to_find": where_to_find, "how_to_use": how_to_use,
                "history": history, "fun_fact": fun_fact,
            }));
            Some(ItemRowView {
                info_json: item_info_json(&id, &name, &where_to_find, &how_to_use, &history, &fun_fact),
                id,
                name,
                picture: v.get("picture").and_then(|s| s.as_str()).unwrap_or("").to_string(),
                photo_url: v.get("photo_url").and_then(|s| s.as_str()).unwrap_or("").to_string(),
                used_in: all_used.into_iter().take(4).collect(),
                used_in_more,
                edited: v.get("edited").and_then(|b| b.as_bool()).unwrap_or(false),
            })
        })
        .collect();
    // CAUGHT LIVE (2026-08-25), Jason's own report: with no sort at all,
    // rows rendered in whatever order Kitchen's own /internal/item-info-
    // list happened to return them, which put a batch of the newest
    // (and, that day, still messiest) entries at the very top -- "the
    // first few entries are like that." A-to-Z, case-insensitive so
    // "amount" and "Amount" land together instead of splitting on case.
    let mut rows = rows;
    rows.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    let bulk_json = serde_json::to_string_pretty(&bulk).unwrap_or_default();
    let html = ItemsTemplate {
        base: base.to_string(),
        csrf,
        flash,
        flash_ok: ok,
        kind: kind.to_string(),
        total: rows.len(),
        items: rows,
        bulk_json,
        year: common::current_year(),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    Html(html).into_response()
}

fn flash_items(state: &AppState, base: &str, token: &str, kind: &str, msg: &str, ok: bool) -> Response {
    flash_to(state, token, msg, ok, &format!("{base}/items?kind={kind}"))
}

async fn items_save(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    let kind = form_field(&body, "kind").unwrap_or_else(|| "food".into());
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_items(&state, &base, &token, &kind, "Bad form token. Reload and try again.", false);
    }
    let id = form_field(&body, "id").unwrap_or_default();
    if id.is_empty() {
        return flash_items(&state, &base, &token, &kind, "Nothing to save.", false);
    }
    // The one JSON textarea replaces the old four-textarea form -- see
    // item_info_json()'s own comment. Parsed here, not trusted as-is, so
    // a stray edit that breaks the JSON (an extra comma, a missing quote)
    // fails loudly instead of silently blanking every field.
    let raw = form_field(&body, "info_json").unwrap_or_default();
    let parsed: serde_json::Value = match serde_json::from_str(&raw) {
        Ok(v @ serde_json::Value::Object(_)) => v,
        _ => {
            return flash_items(
                &state, &base, &token, &kind,
                "That doesn't look like valid JSON -- nothing was saved.", false,
            );
        }
    };
    let field = |k: &str| parsed.get(k).and_then(|s| s.as_str()).unwrap_or("").to_string();
    let form = [
        ("where_to_find", field("where_to_find")),
        ("how_to_use", field("how_to_use")),
        ("history", field("history")),
        ("fun_fact", field("fun_fact")),
    ];
    let sent = state
        .http
        .post(format!("{}/internal/item-info/{kind}/{id}", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .form(&form)
        .send()
        .await;
    match sent {
        Ok(r) if r.status().is_success() => flash_items(&state, &base, &token, &kind, "Saved.", true),
        _ => flash_items(&state, &base, &token, &kind, "Could not reach Kitchen to save it.", false),
    }
}

/// Renaming a food/tool from the condensed items list -- Jason's own
/// request (2026-08-24): "I would like to be able change the name of the
/// food item... when I update it update the recipe." Every recipe holds
/// a reference to the one shared Tandoor row, not a copy of its name, so
/// nothing on the recipe side needs a separate update (see Kitchen's own
/// tandoor.rename_food/rename_tool docstrings).
async fn items_rename(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    let kind = form_field(&body, "kind").unwrap_or_else(|| "food".into());
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_items(&state, &base, &token, &kind, "Bad form token. Reload and try again.", false);
    }
    let id = form_field(&body, "id").unwrap_or_default();
    let name = form_field(&body, "name").unwrap_or_default();
    if id.is_empty() || name.trim().is_empty() {
        return flash_items(&state, &base, &token, &kind, "A name is required.", false);
    }
    let sent = state
        .http
        .post(format!("{}/internal/item-rename/{kind}/{id}", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .form(&[("name", name.as_str())])
        .send()
        .await;
    match sent {
        Ok(r) if r.status().is_success() => flash_items(&state, &base, &token, &kind, "Renamed.", true),
        // Kitchen's own precheck caught a real Food/Keyword already at
        // that exact name -- Jason's own request (2026-08-24): "I would
        // like instead to get a notification to merge them rather than
        // an error message." Straight to "find a match", pre-filled with
        // the name that just collided, instead of a flat error -- the
        // colliding item shows up as the first (often only) result.
        Ok(r) if r.status().as_u16() == 409 => {
            let existing_name = r
                .json::<serde_json::Value>()
                .await
                .ok()
                .and_then(|v| v.get("existing_name").and_then(|s| s.as_str()).map(str::to_string))
                .unwrap_or_else(|| name.clone());
            set_flash(
                &state, &token,
                &format!("\"{name}\" already exists as \"{existing_name}\" -- merge below."),
                true,
            );
            Redirect::to(&format!(
                "{base}/items/match/{kind}/{id}?q={}",
                url_encode_query(&name)
            ))
            .into_response()
        }
        _ => flash_items(&state, &base, &token, &kind, "Tandoor would not rename it.", false),
    }
}

/// Many cards at once -- Jason's own request (2026-08-24): "can we add
/// this in bulk," building on the single-item JSON textarea now carrying
/// `id`/`name` inside the blob itself so a whole page's worth of these
/// can be copied out together, handed to one LLM conversation, and pasted
/// back in as one array without losing track of which item is which.
async fn items_bulk_save(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    let kind = form_field(&body, "kind").unwrap_or_else(|| "food".into());
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_items(&state, &base, &token, &kind, "Bad form token. Reload and try again.", false);
    }
    let raw = form_field(&body, "bulk_json").unwrap_or_default();
    let parsed: serde_json::Value = match serde_json::from_str(&raw) {
        Ok(v @ serde_json::Value::Array(_)) => v,
        _ => {
            return flash_items(
                &state, &base, &token, &kind,
                "That doesn't look like a valid JSON array -- nothing was saved.", false,
            );
        }
    };
    let sent = state
        .http
        .post(format!("{}/internal/item-info-bulk/{kind}", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .timeout(Duration::from_secs(60))
        .json(&parsed)
        .send()
        .await;
    match sent {
        Ok(r) if r.status().is_success() => {
            let saved = r
                .json::<serde_json::Value>()
                .await
                .ok()
                .and_then(|v| v.get("saved").and_then(|n| n.as_u64()))
                .unwrap_or(0);
            flash_items(&state, &base, &token, &kind, &format!("Saved {saved} item(s)."), true)
        }
        _ => flash_items(&state, &base, &token, &kind, "Could not reach Kitchen to save it.", false),
    }
}

#[derive(Template)]
#[template(path = "nutrition.html")]
struct NutritionTemplate {
    base: String,
    csrf: String,
    flash: String,
    flash_ok: bool,
    food_id: String,
    item_name: String,
    nutrition_json: String,
    portion_unit: String,
    portion_grams: String,
    portion_source: String,
    year: i32,
}

/// Jason's own request (2026-08-25): "is it possible in the admin panel
/// to upload... nutritional information... since that's publicly
/// available" and, on the same food, "I just want a baseline" for a
/// countable serving size. One page for both, reached from each food
/// row on /items -- not folded into that list itself, which already
/// fetches 1300+ rows on its own and does not need two more calls per
/// row just to pre-fill a page most rows will never visit.
async fn items_nutrition_page(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    axum::extract::Path(food_id): axum::extract::Path<String>,
) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    let (flash, flash_ok) = take_flash(&state, &token);

    let nutrition: serde_json::Value = match state
        .http
        .get(format!("{}/internal/food-nutrition/{food_id}", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => r.json().await.unwrap_or_default(),
        _ => serde_json::Value::Null,
    };
    let item_name = nutrition.get("name").and_then(|s| s.as_str()).unwrap_or("").to_string();
    let nutrition_json = nutrition
        .get("baseline")
        .filter(|v| !v.is_null())
        .and_then(|v| serde_json::to_string_pretty(v).ok())
        .unwrap_or_default();

    let portion: serde_json::Value = match state
        .http
        .get(format!("{}/internal/food-portion/{food_id}", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => r.json().await.unwrap_or_default(),
        _ => serde_json::Value::Null,
    };
    let portion_unit = portion.get("unit").and_then(|s| s.as_str()).unwrap_or("").to_string();
    let portion_grams = portion.get("grams").map(|v| v.to_string()).unwrap_or_default();
    let portion_source = portion.get("source").and_then(|s| s.as_str()).unwrap_or("").to_string();

    let html = NutritionTemplate {
        base: base.to_string(),
        csrf,
        flash,
        flash_ok,
        food_id,
        item_name,
        nutrition_json,
        portion_unit,
        portion_grams,
        portion_source,
        year: common::current_year(),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    Html(html).into_response()
}

fn flash_nutrition(state: &AppState, base: &str, token: &str, food_id: &str, msg: &str, ok: bool) -> Response {
    flash_to(state, token, msg, ok, &format!("{base}/items/nutrition/{food_id}"))
}

async fn items_nutrition_save(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    let food_id = form_field(&body, "food_id").unwrap_or_default();
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_nutrition(&state, &base, &token, &food_id, "Bad form token. Reload and try again.", false);
    }
    let raw = form_field(&body, "nutrition_json").unwrap_or_default();
    let parsed: serde_json::Value = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(_) => {
            return flash_nutrition(&state, &base, &token, &food_id,
                "That doesn't look like valid JSON -- nothing was saved.", false);
        }
    };
    let sent = state
        .http
        .post(format!("{}/internal/food-nutrition/{food_id}", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .json(&parsed)
        .send()
        .await;
    match sent {
        Ok(r) if r.status().is_success() => flash_nutrition(&state, &base, &token, &food_id, "Nutrition saved.", true),
        Ok(r) => {
            let msg = r.text().await.unwrap_or_else(|_| "Kitchen refused that.".into());
            flash_nutrition(&state, &base, &token, &food_id, &msg, false)
        }
        _ => flash_nutrition(&state, &base, &token, &food_id, "Could not reach Kitchen.", false),
    }
}

async fn items_portion_save(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    let food_id = form_field(&body, "food_id").unwrap_or_default();
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_nutrition(&state, &base, &token, &food_id, "Bad form token. Reload and try again.", false);
    }
    let unit = form_field(&body, "unit").unwrap_or_default();
    let grams = form_field(&body, "grams").unwrap_or_default();
    let sent = state
        .http
        .post(format!("{}/internal/food-portion/{food_id}", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .form(&[("unit", unit.as_str()), ("grams", grams.as_str())])
        .send()
        .await;
    match sent {
        Ok(r) if r.status().is_success() => flash_nutrition(&state, &base, &token, &food_id, "Baseline saved.", true),
        Ok(r) => {
            let msg = r.text().await.unwrap_or_else(|_| "Kitchen refused that.".into());
            flash_nutrition(&state, &base, &token, &food_id, &msg, false)
        }
        _ => flash_nutrition(&state, &base, &token, &food_id, "Could not reach Kitchen.", false),
    }
}

async fn items_portions_import(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_items(&state, &base, &token, "food", "Bad form token. Reload and try again.", false);
    }
    let sent = state
        .http
        .post(format!("{}/internal/food-portions-import", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .timeout(Duration::from_secs(120))
        .send()
        .await;
    match sent {
        Ok(r) if r.status().is_success() => {
            let d: serde_json::Value = r.json().await.unwrap_or_default();
            let written = d.get("written").and_then(|n| n.as_u64()).unwrap_or(0);
            let no_match = d.get("no_match").and_then(|n| n.as_u64()).unwrap_or(0);
            flash_items(&state, &base, &token, "food",
                &format!("Imported {written} baseline(s) from USDA; {no_match} had no match (hand-type those)."), true)
        }
        _ => flash_items(&state, &base, &token, "food", "Could not reach Kitchen.", false),
    }
}

/// JSON proxy for Kitchen's own /internal/food-search -- Jason's own
/// request (2026-08-25): merge "inline" on /items itself, not a click-
/// through to the separate search page. Same session gate as every
/// other admin route (checked here, not left to Kitchen, which only
/// knows the shared internal secret and nothing about who's asking).
async fn items_search_json(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    axum::extract::Path((kind, item_id)): axum::extract::Path<(String, String)>,
    axum::extract::Query(params): axum::extract::Query<HashMap<String, String>>,
) -> Response {
    if touch_session_full(&state, &headers).is_none() {
        return (StatusCode::UNAUTHORIZED, "signed out").into_response();
    }
    let q = params.get("q").cloned().unwrap_or_default();
    if q.trim().is_empty() {
        return axum::Json(Vec::<serde_json::Value>::new()).into_response();
    }
    let sent = state
        .http
        .get(format!("{}/internal/food-search", state.kitchen_url))
        .query(&[("kind", kind.as_str()), ("q", q.as_str()), ("exclude", item_id.as_str())])
        .header("X-Internal-Secret", &state.kitchen_secret)
        .send()
        .await;
    match sent {
        Ok(r) if r.status().is_success() => {
            let items: Vec<serde_json::Value> = r.json().await.unwrap_or_default();
            axum::Json(items).into_response()
        }
        _ => axum::Json(Vec::<serde_json::Value>::new()).into_response(),
    }
}

/// One candidate on the manual-match search page.
struct MatchCandidateView {
    id: String,
    name: String,
}

#[derive(Template)]
#[template(path = "match.html")]
struct MatchTemplate {
    base: String,
    csrf: String,
    flash: String,
    flash_ok: bool,
    kind: String,
    item_id: String,
    item_name: String,
    q: String,
    candidates: Vec<MatchCandidateView>,
    year: i32,
}

/// Jason's own request (2026-08-24): "I would like in the admin panel to
/// be able to match manually to a like item... if I can't find the
/// match I'd like to make a match for it." Search-then-merge, entirely
/// separate from dedupe.py's own automatic tier-2 proposals -- this is
/// Jason picking the target himself, reached from a "find a match" link
/// on each row of /items.
async fn items_match(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    axum::extract::Path((kind, item_id)): axum::extract::Path<(String, String)>,
    axum::extract::Query(params): axum::extract::Query<HashMap<String, String>>,
) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    let (flash, flash_ok) = take_flash(&state, &token);
    let q = params.get("q").cloned().unwrap_or_default();

    // The item's own current name -- ONE cheap Tandoor lookup, not the
    // whole item-info-list (1300+ items, each with its own usage query)
    // just to read one name off it. CAUGHT LIVE (2026-08-24): that used
    // to be the only way to get it, back when item-info-list was still
    // scoped to a few hundred picture-having items and cheap to fetch
    // wholesale; it stopped being cheap the moment that scoping was lifted.
    let item_name: String = match state
        .http
        .get(format!("{}/internal/item-name/{kind}/{item_id}", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => r
            .json::<serde_json::Value>()
            .await
            .ok()
            .and_then(|v| v.get("name").and_then(|s| s.as_str()).map(str::to_string))
            .unwrap_or_default(),
        _ => String::new(),
    };

    let mut candidates: Vec<MatchCandidateView> = Vec::new();
    if !q.trim().is_empty() {
        let sent = state
            .http
            .get(format!("{}/internal/food-search", state.kitchen_url))
            .query(&[("kind", kind.as_str()), ("q", q.as_str()), ("exclude", item_id.as_str())])
            .header("X-Internal-Secret", &state.kitchen_secret)
            .send()
            .await;
        if let Ok(r) = sent {
            if let Ok(items) = r.json::<Vec<serde_json::Value>>().await {
                candidates = items
                    .into_iter()
                    .filter_map(|v| {
                        let id = v.get("id")?.as_u64()?.to_string();
                        let name = v.get("name")?.as_str()?.to_string();
                        Some(MatchCandidateView { id, name })
                    })
                    .collect();
            }
        }
    }

    let html = MatchTemplate {
        base: base.to_string(),
        csrf,
        flash,
        flash_ok,
        kind,
        item_id,
        item_name,
        q,
        candidates,
        year: common::current_year(),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    Html(html).into_response()
}

async fn items_match_merge(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    let kind = form_field(&body, "kind").unwrap_or_default();
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_items(&state, &base, &token, &kind, "Bad form token. Reload and try again.", false);
    }
    let item_id = form_field(&body, "item_id").unwrap_or_default();
    let target_id = form_field(&body, "target_id").unwrap_or_default();
    if item_id.is_empty() || target_id.is_empty() {
        return flash_items(&state, &base, &token, &kind, "Nothing to match.", false);
    }
    let sent = state
        .http
        .post(format!("{}/internal/manual-merge", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .form(&[("kind", kind.as_str()), ("item_id", item_id.as_str()), ("target_id", target_id.as_str())])
        .send()
        .await;
    match sent {
        Ok(r) if r.status().is_success() => flash_items(&state, &base, &token, &kind, "Matched.", true),
        _ => flash_items(&state, &base, &token, &kind, "Could not reach Kitchen to match it.", false),
    }
}

/// One row on the reparse done/undone list.
struct ReparseRowView {
    tandoor_id: String,
    title: String,
    done: bool,
    has_source: bool,
}

#[derive(Template)]
#[template(path = "reparse.html")]
struct ReparseTemplate {
    base: String,
    csrf: String,
    flash: String,
    flash_ok: bool,
    rows: Vec<ReparseRowView>,
    done_count: usize,
    total: usize,
    year: i32,
}

/// Jason's own words (2026-08-24): "I'll parse the rest manually just
/// give me the ability to do so in /admin with a done and undone." One
/// button per recipe, each one a real fetch-and-parse call against
/// Kitchen (see reparse.py) -- slow on purpose, one at a time, exactly
/// what he asked for instead of the earlier bulk background run.
async fn reparse_page(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    let (flash, ok) = take_flash(&state, &token);
    render_reparse(&state, &base, &csrf, &flash, ok).await
}

async fn render_reparse(state: &AppState, base: &str, csrf: &str, flash: &str, ok: bool) -> Response {
    let items: Vec<serde_json::Value> = match state
        .http
        .get(format!("{}/internal/reparse-status", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .send()
        .await
    {
        Ok(r) => r.json().await.unwrap_or_default(),
        Err(_) => Vec::new(),
    };
    let rows: Vec<ReparseRowView> = items
        .into_iter()
        .filter_map(|v| {
            let tandoor_id = v.get("tandoor_id")?.as_str()?.to_string();
            let title = v.get("title")?.as_str()?.to_string();
            let done = v.get("done").and_then(|b| b.as_bool()).unwrap_or(false);
            let has_source = v.get("has_source").and_then(|b| b.as_bool()).unwrap_or(true);
            Some(ReparseRowView { tandoor_id, title, done, has_source })
        })
        .collect();
    let done_count = rows.iter().filter(|r| r.done).count();
    let html = ReparseTemplate {
        base: base.to_string(),
        csrf: csrf.to_string(),
        flash: flash.to_string(),
        flash_ok: ok,
        total: rows.len(),
        done_count,
        rows,
        year: common::current_year(),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    Html(html).into_response()
}

fn flash_reparse(state: &AppState, base: &str, token: &str, msg: &str, ok: bool) -> Response {
    flash_to(state, token, msg, ok, &format!("{base}/reparse"))
}

async fn reparse_one(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_reparse(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let id = form_field(&body, "id").unwrap_or_default();
    if id.is_empty() {
        return flash_reparse(&state, &base, &token, "Nothing to re-parse.", false);
    }
    // The shared client's own default timeout (15s) is nowhere near
    // enough for a real fetch-plus-Phi-4 call, especially a cold model
    // load -- overridden per-request rather than raised for everyone
    // else's much faster calls.
    let sent = state
        .http
        .post(format!("{}/internal/reparse/{id}", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .timeout(Duration::from_secs(180))
        .send()
        .await;
    match sent {
        Ok(r) => {
            let data: serde_json::Value = r.json().await.unwrap_or_default();
            if data.get("ok").and_then(|b| b.as_bool()).unwrap_or(false) {
                flash_reparse(&state, &base, &token, "Re-parsed.", true)
            } else {
                let err = data.get("error").and_then(|s| s.as_str()).unwrap_or("Could not re-parse it.");
                flash_reparse(&state, &base, &token, err, false)
            }
        }
        Err(_) => flash_reparse(&state, &base, &token, "Could not reach Kitchen.", false),
    }
}

/// Draw a recipe's missing step pictures -- Jason's own request
/// (2026-08-24), right after asking for the manual reparse control:
/// "then we can go to the next step... generate images [for] the steps
/// for the recipes too... and evict the models as such as needed."
/// Frees whatever Ollama has loaded FIRST (same free_gpu() the
/// /pictures page's own button already uses), then asks Kitchen to draw
/// -- one click instead of two, no separate trip to "free the GPU"
/// before every drawing run.
async fn reparse_draw(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_reparse(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let id = form_field(&body, "id").unwrap_or_default();
    if id.is_empty() {
        return flash_reparse(&state, &base, &token, "Nothing to draw for.", false);
    }
    free_gpu(&state).await;
    let sent = state
        .http
        .post(format!("{}/internal/draw-missing/{id}", state.kitchen_url))
        .header("X-Internal-Secret", &state.kitchen_secret)
        .timeout(Duration::from_secs(180))
        .send()
        .await;
    match sent {
        Ok(r) => {
            let data: serde_json::Value = r.json().await.unwrap_or_default();
            let made = data.get("made").and_then(|x| x.as_i64()).unwrap_or(0);
            if made > 0 {
                flash_reparse(&state, &base, &token, &format!("Drew {made} step picture(s)."), true)
            } else {
                let err = data.get("error").and_then(|s| s.as_str()).unwrap_or("Nothing to draw.");
                flash_reparse(&state, &base, &token, err, false)
            }
        }
        Err(_) => flash_reparse(&state, &base, &token, "Could not reach Kitchen.", false),
    }
}

async fn pictures_approve(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_pictures(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let key = form_field(&body, "key").unwrap_or_default();
    if key.is_empty() {
        return flash_pictures(&state, &base, &token, "Nothing to approve.", false);
    }
    let mut approved = read_approved(&state);
    approved.insert(key.clone());
    match write_approved(&state, &approved) {
        Ok(_) => {
            tracing::info!(key = %key, "admin: picture approved, off the list");
            flash_pictures(&state, &base, &token, &format!("Marked done: {key}"), true)
        }
        Err(e) => flash_pictures(&state, &base, &token, &format!("Could not save: {e}"), false),
    }
}

async fn pictures_free_gpu(State(state): State<Arc<AppState>>, headers: HeaderMap, body: String) -> Response {
    let base = base_path(&headers);
    let Some((token, csrf)) = touch_session_full(&state, &headers) else {
        return render_login(&base, "Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    if form_field(&body, "csrf").unwrap_or_default() != csrf {
        return flash_pictures(&state, &base, &token, "Bad form token. Reload and try again.", false);
    }
    let unloaded = free_gpu(&state).await;
    let msg = if unloaded.is_empty() {
        "Nothing was loaded, or nothing could be reached -- the card may already have room, \
         or Ollama did not answer.".to_string()
    } else {
        format!(
            "Unloaded: {}. They come back on their own the next time anything asks them a \
             question -- there is nothing to switch back on.",
            unloaded.join(", ")
        )
    };
    flash_pictures(&state, &base, &token, &msg, true)
}

// -----------------------------------------------------------------------------
//  Small helpers
// -----------------------------------------------------------------------------

/// Pull one field out of an application/x-www-form-urlencoded body.
///
/// Hand-rolled rather than pulling in a form-parsing crate: this reads exactly
/// one field from one form, and the decoding rules are small enough to see.
fn form_field(body: &str, name: &str) -> Option<String> {
    for pair in body.split('&') {
        let mut it = pair.splitn(2, '=');
        let k = it.next()?;
        if k != name {
            continue;
        }
        let v = it.next().unwrap_or("");
        return Some(url_decode(v));
    }
    None
}

/// Every value for a repeated form field -- a checkbox list all sharing one
/// `name`, which form_field() above cannot see past the first of.
fn form_fields(body: &str, name: &str) -> Vec<String> {
    body.split('&')
        .filter_map(|pair| {
            let mut it = pair.splitn(2, '=');
            let k = it.next()?;
            if k != name {
                return None;
            }
            Some(url_decode(it.next().unwrap_or("")))
        })
        .collect()
}

fn url_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b'%' if i + 2 < bytes.len() => {
                let hi = (bytes[i + 1] as char).to_digit(16);
                let lo = (bytes[i + 2] as char).to_digit(16);
                match (hi, lo) {
                    (Some(h), Some(l)) => {
                        out.push((h * 16 + l) as u8);
                        i += 3;
                    }
                    _ => {
                        out.push(bytes[i]);
                        i += 1;
                    }
                }
            }
            b => {
                out.push(b);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn verify_password(hash: &str, password: &str) -> bool {
    match PasswordHash::new(hash) {
        Ok(parsed) => Argon2::default()
            .verify_password(password.as_bytes(), &parsed)
            .is_ok(),
        Err(e) => {
            tracing::error!("ADMIN_PASSWORD_HASH is not a valid argon2 hash: {e}");
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

// -----------------------------------------------------------------------------

fn main() -> anyhow::Result<()> {
    // `admin hash "some password"` prints a hash and exits.
    //
    // This lives in the same binary so there is nothing extra to install and no
    // website that wants your password in a text box. Run it through Docker:
    //   docker compose -f docker-compose.web.yml run --rm admin hash "your password"
    let args: Vec<String> = std::env::args().collect();
    if args.len() >= 2 && args[1] == "hash" {
        // TWO WAYS IN, and the quiet one is the better one.
        //
        //   admin hash "the password"   <- convenient, but the password is
        //                                  now in the process list, in shell
        //                                  history, and in `docker ps` output
        //                                  for as long as it runs.
        //   echo "the password" | admin hash   <- nothing to read anywhere.
        //
        // admin-password.ps1 uses the second form, which is why it can prompt
        // for a password without ever putting it on a command line.
        let pw = match args.get(2) {
            Some(p) if !p.is_empty() => p.clone(),
            _ => {
                let mut line = String::new();
                if std::io::stdin().read_line(&mut line).is_err() {
                    eprintln!("usage: admin hash \"your password\"");
                    eprintln!("   or: echo \"your password\" | admin hash");
                    std::process::exit(2);
                }
                line.trim_end_matches(['\r', '\n']).to_string()
            }
        };
        if pw.is_empty() {
            eprintln!("No password given. Nothing to hash.");
            std::process::exit(2);
        }
        println!("{}", hash_password(&pw)?);
        eprintln!();
        eprintln!("Put that whole line in ai-stack\\.env as:");
        eprintln!("  ADMIN_PASSWORD_HASH=<the line above>");
        eprintln!();
        eprintln!("It contains $ characters. In docker-compose they must be");
        eprintln!("doubled ($$) OR the value must be single-quoted in .env.");
        return Ok(());
    }

    common::init_local_offset();
    common::init_tracing("admin");
    common::runtime()?.block_on(run())
}

async fn run() -> anyhow::Result<()> {
    // A MISCONFIGURATION IS NOT A CRASH, so it does not get a crash's output.
    //
    // Returning these as anyhow errors printed a stack backtrace above the
    // message, which makes "you forgot to set a password" look like a panic and
    // buries the one line that tells you what to do. Print and exit instead.
    let password_hash = match common::env_required("ADMIN_PASSWORD_HASH") {
        Ok(h) if !h.trim().is_empty() => h,
        _ => {
            eprintln!("ADMIN_PASSWORD_HASH is not set, so there would be no password at all.");
            eprintln!("Refusing to start rather than serving an unprotected admin.");
            eprintln!();
            eprintln!("Generate one with:");
            eprintln!("  docker compose -f docker-compose.web.yml run --rm admin hash \"your password\"");
            eprintln!();
            eprintln!("Then put it in .env, SINGLE-QUOTED:");
            eprintln!("  ADMIN_PASSWORD_HASH='$argon2id$v=19$...'");
            std::process::exit(1);
        }
    };

    // Check the hash parses at STARTUP, not at the first login attempt. A hash
    // that does not parse means nobody can ever sign in, and discovering that
    // from a phone at 11pm is worse than discovering it here.
    if PasswordHash::new(&password_hash).is_err() {
        eprintln!("ADMIN_PASSWORD_HASH is set but is not a valid argon2 hash.");
        eprintln!();
        eprintln!("It should start with $argon2id$ and end with a long base64 blob.");
        eprintln!("The usual cause: it was put in .env unquoted, so docker compose");
        eprintln!("expanded the $ sections away as if they were variables.");
        eprintln!("Fix by single-quoting the whole value in .env.");
        eprintln!();
        eprintln!("What this process actually received:");
        // Show ONLY the shape, never the value - this lands in logs.
        eprintln!("  {} characters, starts with {:?}",
            password_hash.len(),
            password_hash.chars().take(9).collect::<String>());
        std::process::exit(1);
    }

    // Cloned BEFORE the move into the struct. Reading it back out of `state`
    // would work too, but this keeps the signing key's provenance on one line.
    let password_hash_for_secret = password_hash.clone();

    let state = Arc::new(AppState {
        password_hash,
        data_dir: PathBuf::from(common::env_or("ADMIN_DATA_DIR", "/data/site")),
        people_file: PathBuf::from(common::env_or("ADMIN_PEOPLE_FILE", "/data/auth/people.json")),
        trials_file: PathBuf::from(common::env_or("ADMIN_TRIALS_FILE", "/data/auth/trials.json")),
        guest_invites_file: PathBuf::from(
            common::env_or("ADMIN_GUEST_INVITES_FILE", "/data/auth/guest-invites.json")),
        auth_passkeys_file: PathBuf::from(
            common::env_or("ADMIN_AUTH_PASSKEYS_FILE", "/data/auth/passkeys.json")),
        invite_link_base: common::env_or(
            "ADMIN_INVITE_LINK_BASE", "https://example.org/auth"),
        auth_revoked_file: PathBuf::from(
            common::env_or("ADMIN_AUTH_REVOKED_FILE", "/data/auth/revoked.json")),
        backup_dir: PathBuf::from(common::env_or("ADMIN_BACKUP_DIR", "/data/backups")),
        cookie_secure: common::env_bool("ADMIN_COOKIE_SECURE", true),
        idle_timeout: Duration::from_secs(
            common::env_or("ADMIN_IDLE_MINUTES", "20").parse::<u64>().unwrap_or(20) * 60,
        ),
        absolute_timeout: Duration::from_secs(
            common::env_or("ADMIN_SESSION_HOURS", "8").parse::<u64>().unwrap_or(8) * 3600,
        ),
        max_upload: common::env_or("ADMIN_MAX_UPLOAD_MB", "8")
            .parse::<usize>()
            .unwrap_or(8)
            * 1024
            * 1024,
        sessions: Mutex::new(HashMap::new()),
        flash: Mutex::new(HashMap::new()),
        secret: password_hash_for_secret.into_bytes(),
        // Lives beside people.json, on the Windows folder that is already
        // bind-mounted and already backed up -- not in a Docker volume, for
        // the same reason people.json is not.
        revoke_file: PathBuf::from(common::env_or("ADMIN_PEOPLE_FILE", "/data/auth/people.json"))
            .parent()
            .unwrap_or(Path::new("/data/auth"))
            .join("admin-revoked"),
        // YOUR addresses. Comma separated in .env if they ever change.
        owners: common::env_or("ADMIN_OWNERS", "user1@example.com")
            .split(',')
            .map(common::people::normalise)
            .filter(|e| !e.is_empty())
            .collect(),
        gate: Mutex::new(Gate {
            fails: 0,
            locked_until: None,
        }),
        http: reqwest::Client::builder()
            .timeout(Duration::from_secs(15))
            .build()
            .expect("reqwest client with a fixed timeout always builds"),
        kitchen_url: common::env_or("PICTURES_KITCHEN_URL", "http://kitchen:8003"),
        kitchen_secret: common::env_or("KITCHEN_INTERNAL_SECRET", ""),
        kb_url: common::env_or("PICTURES_KB_URL", "http://kb:8014"),
        kb_secret: common::env_or("KB_TICKET_SECRET", ""),
        ollama_url: common::env_or("PICTURES_OLLAMA_URL", "http://ollama:11434"),
        imagine_url: common::env_or("PICTURES_IMAGINE_URL", "http://imagine:8020"),
        approved_file: PathBuf::from(common::env_or("ADMIN_DATA_DIR", "/data/site"))
            .join("approved-pictures.json"),
    });

    std::fs::create_dir_all(&state.data_dir).ok();
    std::fs::create_dir_all(&state.backup_dir).ok();

    tracing::info!(
        data_dir = %state.data_dir.display(),
        idle_min = state.idle_timeout.as_secs() / 60,
        session_h = state.absolute_timeout.as_secs() / 3600,
        secure_cookie = state.cookie_secure,
        "admin ready"
    );

    let app = Router::new()
        .route("/", get(index))
        .route("/site/{key}", get(site))
        .route("/people", get(people_page))
        .route("/people/add", post(people_add))
        .route("/people/password", post(people_password))
        .route("/people/apps", post(people_apps))
        .route("/people/invite", post(people_invite_add))
        .route("/people/invite-resend", post(people_invite_resend))
        .route("/people/guest-extend", post(people_guest_extend))
        .route("/people/remove", post(people_remove))
        .route("/people/signout", post(people_signout))
        .route("/people/passkey-revoke", post(people_passkey_revoke))
        .route("/trials", get(trials_page))
        .route("/trials/add", post(trials_add))
        .route("/trials/code", post(trials_code))
        .route("/trials/end", post(trials_end))
        .route("/trials/grant", post(trials_grant))
        .route("/pictures", get(pictures_page))
        .route("/pictures/approve", post(pictures_approve))
        .route("/pictures/dismiss-warning", post(pictures_dismiss_warning))
        .route("/pictures/merge-approve", post(pictures_merge_approve))
        .route("/pictures/merge-reject", post(pictures_merge_reject))
        .route("/pictures/nutrition-apply", post(pictures_nutrition_apply))
        .route("/pictures/usage/{kind}/{item_id}", get(pictures_usage))
        .route("/pictures/free-gpu", post(pictures_free_gpu))
        .route("/llm-findings", get(llm_findings_page))
        .route("/llm-findings/run", post(llm_findings_run))
        .route("/llm-findings/tidy", post(llm_findings_tidy))
        .route("/llm-findings/approve", post(llm_findings_approve))
        .route("/llm-findings/reject", post(llm_findings_reject))
        .route("/items", get(items_page))
        .route("/items/save", post(items_save))
        .route("/items/rename", post(items_rename))
        .route("/items/bulk-save", post(items_bulk_save))
        .route("/items/nutrition/{food_id}", get(items_nutrition_page))
        .route("/items/nutrition/save", post(items_nutrition_save))
        .route("/items/portion/save", post(items_portion_save))
        .route("/items/portions-import", post(items_portions_import))
        .route("/items/search-json/{kind}/{item_id}", get(items_search_json))
        .route("/items/match/{kind}/{item_id}", get(items_match))
        .route("/items/match/merge", post(items_match_merge))
        .route("/reparse", get(reparse_page))
        .route("/reparse/one", post(reparse_one))
        .route("/reparse/draw", post(reparse_draw))
        .route("/login", post(login))
        .route("/logout", post(logout))
        .route("/upload", post(upload))
        .route("/entry/remove", post(entry_remove))
        .route("/download/{key}", get(download))
        .route("/verify", get(verify))
        .route("/healthz", get(healthz))
        .with_state(state);

    common::serve(app, common::env_u16("APP_PORT", 8011)).await
}

// -----------------------------------------------------------------------------
//  Tests for the patch logic
//
//  These cover the ways a patch can quietly do the WRONG thing rather than fail
//  - duplicating instead of updating, removing the wrong row, dropping a key
//  somebody hand-wrote. A patch that errors is visible on screen; a patch that
//  succeeds and loses an entry is not, and that is the whole reason this file
//  is patched rather than replaced.
// -----------------------------------------------------------------------------
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn guest_emails_finds_only_guest_rows() {
        let dir = std::env::temp_dir().join("admin-guest-emails-test");
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("passkeys.json");
        std::fs::write(
            &p,
            br#"{"users":[
                {"email":"jason@example.com","guest":false},
                {"email":"guest-test-ab12@invite.local","guest":true},
                {"email":"guest-brandon-9f01@invite.local","guest":true}
            ]}"#,
        )
        .unwrap();

        let set = guest_emails(&p);
        assert_eq!(set.len(), 2);
        assert!(set.contains("guest-test-ab12@invite.local"));
        assert!(set.contains("guest-brandon-9f01@invite.local"));
        assert!(!set.contains("jason@example.com"));
        std::fs::remove_file(&p).ok();
    }

    #[test]
    fn guest_emails_fails_safe_to_empty_on_a_missing_or_bad_file() {
        let missing = std::env::temp_dir().join("admin-guest-emails-does-not-exist.json");
        assert!(guest_emails(&missing).is_empty());

        let dir = std::env::temp_dir().join("admin-guest-emails-bad-test");
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("passkeys.json");
        std::fs::write(&p, b"not valid json at all").unwrap();
        assert!(guest_emails(&p).is_empty());
        std::fs::remove_file(&p).ok();
    }


    #[test]
    fn guest_expiry_accepts_any_day_count_from_1_to_365() {
        let now = 1_000_000u64;
        assert_eq!(guest_expiry(1, now), Ok(now + 86400));
        assert_eq!(guest_expiry(2, now), Ok(now + 2 * 86400));
        assert_eq!(guest_expiry(7, now), Ok(now + 7 * 86400));
        assert_eq!(guest_expiry(30, now), Ok(now + 30 * 86400));
        assert_eq!(guest_expiry(365, now), Ok(now + 365 * 86400));
        assert!(guest_expiry(0, now).is_err(), "zero days must still be refused");
        assert!(guest_expiry(366, now).is_err(), "the 365-day ceiling must still be enforced");
        assert!(guest_expiry(10_000, now).is_err(), "a wildly large typo must still be refused");
    }

    #[test]
    fn random_guest_password_is_256_characters_and_differs_each_time() {
        let a = random_guest_password();
        let b = random_guest_password();
        assert_eq!(a.chars().count(), 256);
        assert!(a.chars().all(|c| c.is_ascii_alphanumeric()));
        assert_ne!(a, b, "two calls must not produce the same password");
    }

    #[test]
    fn guest_entry_helpers_only_touch_guests_and_keep_other_users() {
        let dir = std::env::temp_dir().join("admin-guest-edit-test");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("passkeys.json");
        std::fs::write(&path, r#"{"users":[
            {"email":"owner@x","guest":false,"keys":[{"id":"a","passkey":{"opaque":1}}]},
            {"email":"g@invite.local","guest":true,"account_expires":100,"keys":[{"id":"b","expires":100}]}
        ]}"#).unwrap();

        assert!(read_guest_entry(&path, "owner@x").is_err(), "a non-guest must be refused");
        assert!(edit_guest_entry(&path, "owner@x", |_| Ok(())).is_err());
        edit_guest_entry(&path, "g@invite.local", |u| {
            u["account_expires"] = serde_json::json!(500);
            u["keys"][0]["expires"] = serde_json::json!(500);
            Ok(())
        })
        .unwrap();
        let g = read_guest_entry(&path, "g@invite.local").unwrap();
        assert_eq!(g["account_expires"], 500);
        assert_eq!(g["keys"][0]["expires"], 500);
        // The other user's key is carried through untouched.
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(text.contains("\"opaque\": 1"));
    }

    #[test]
    fn synthetic_guest_email_looks_like_an_email_and_never_collides() {
        let a = synthetic_guest_email("Brandon");
        let b = synthetic_guest_email("Brandon");
        assert!(a.contains('@'), "must satisfy looks_like_email's basic shape");
        assert!(a.starts_with("guest-brandon-"));
        assert_ne!(a, b, "same label, two invites: must not produce the same placeholder email");
    }

    #[test]
    fn synthetic_guest_email_strips_characters_that_are_not_alphanumeric() {
        let e = synthetic_guest_email("Robin's Garden!!");
        assert!(!e.contains('\''));
        assert!(!e.contains('!'));
        assert!(looks_like_email(&e));
    }

    #[test]
    fn patch_new_guest_entry_creates_the_file_and_preserves_other_users() {
        let dir = std::env::temp_dir().join("admin-guest-patch-test");
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("passkeys.json");
        std::fs::write(&p, br#"{"users":[{"email":"jason@example.com","user_id":"abc","keys":[{"id":"k1"}]}]}"#).unwrap();

        patch_new_guest_entry(&p, "guest-test-ab12@invite.local", "Test-1", 999999).unwrap();

        let text = std::fs::read_to_string(&p).unwrap();
        let v: serde_json::Value = serde_json::from_str(&text).unwrap();
        let users = v["users"].as_array().unwrap();
        assert_eq!(users.len(), 2, "the existing user must survive untouched");
        assert_eq!(users[0]["email"], "jason@example.com");
        assert_eq!(users[0]["keys"][0]["id"], "k1", "an existing user's keys must not be rewritten");
        let guest = users.iter().find(|u| u["email"] == "guest-test-ab12@invite.local").unwrap();
        assert_eq!(guest["guest"], true);
        assert_eq!(guest["account_expires"], 999999);
        assert_eq!(guest["label"], "Test-1");
        assert_eq!(guest["keys"].as_array().unwrap().len(), 0);
        std::fs::remove_file(&p).ok();
    }


    const KB: Target = Target {
        key: "mom-kb",
        file: "mom-kb.json",
        label: "Known fixes",
        sub: "",
        hint: "",
        accent: "",
        icon: "",
        list_key: "articles",
        name_key: "title",
        noun: "article",
        tool_href: "",
        tool_label: "",
        tool_why: "",
    };

    fn apply(entries: &mut Vec<serde_json::Value>, patch: serde_json::Value) -> Applied {
        let p = parse_patch(&KB, patch).expect("patch should parse");
        apply_patch(&KB, entries, p)
    }

    #[test]
    fn form_fields_collects_every_value_for_a_repeated_checkbox_name() {
        assert_eq!(
            form_fields("apps=mama&apps=kitchen&email=a%40b.com", "apps"),
            vec!["mama".to_string(), "kitchen".to_string()],
        );
        assert_eq!(form_fields("email=a%40b.com", "apps"), Vec::<String>::new());
        assert_eq!(form_fields("", "apps"), Vec::<String>::new());
    }

    /// The form as apps_from_levels sees it: lvl_<app> -> the dropdown's value.
    fn levels(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let m: std::collections::HashMap<String, String> =
            pairs.iter().map(|(a, l)| (format!("lvl_{a}"), l.to_string())).collect();
        move |k| m.get(k).cloned()
    }

    #[test]
    fn choosing_a_subset_of_apps_stores_exactly_that_subset() {
        assert_eq!(apps_from_levels(levels(&[("mama", "full")])), vec!["mama".to_string()]);
    }

    #[test]
    fn choosing_full_for_every_app_stores_all_of_them_explicitly() {
        // NOT collapsed to empty. Person::apps_unrestricted, set to false by
        // the caller (people_apps) whenever this helper runs at all, is what
        // separates "every app chosen" from "never touched" -- see that
        // field's own doc comment for the bug this avoids: an empty Vec here
        // would be indistinguishable from a person nobody has ever narrowed.
        let all: Vec<(&str, &str)> = common::people::ALL_APPS.iter().map(|a| (*a, "full")).collect();
        let want: Vec<String> = common::people::ALL_APPS.iter().map(|a| a.to_string()).collect();
        assert_eq!(apps_from_levels(levels(&all)), want);
    }

    #[test]
    fn choosing_nothing_stores_an_empty_list() {
        // Paired with apps_unrestricted = false (set by the caller), this is
        // what "revoke everything" actually looks like on disk -- see
        // revoking_every_app_actually_refuses_every_app in common::people.
        assert_eq!(apps_from_levels(levels(&[])), Vec::<String>::new());
    }

    #[test]
    fn an_unknown_app_or_level_is_silently_dropped() {
        // Nothing on the page can submit these, but the form body is still
        // attacker-editable HTML -- a bogus app name or level must not get stored.
        let bogus = levels(&[("not-a-real-app", "full"), ("mama", "admin-please")]);
        assert_eq!(apps_from_levels(bogus), Vec::<String>::new());
    }

    #[test]
    fn id_is_derived_from_the_name_field() {
        assert_eq!(slug("Walnut Recreation Center"), "walnut-recreation-center");
        assert_eq!(slug("  Printer says OFFLINE!  "), "printer-says-offline");
        assert_eq!(slug("---"), "");
        assert_eq!(slug("a".repeat(90).as_str()).len(), 60);
    }

    #[test]
    fn the_same_entry_twice_updates_and_never_duplicates() {
        let mut e = Vec::new();
        apply(&mut e, json!({"title": "Printer offline", "answer": "one"}));
        let r = apply(&mut e, json!({"title": "Printer offline", "answer": "two"}));

        assert_eq!(e.len(), 1, "a second send must not add a second copy");
        assert_eq!(r.updated, 1);
        assert_eq!(r.added, 0);
        assert_eq!(e[0]["answer"], "two", "the newer version wins");
    }

    #[test]
    fn an_explicit_id_survives_a_rename() {
        let mut e = Vec::new();
        apply(&mut e, json!({"id": "printer", "title": "Printer offline"}));
        apply(&mut e, json!({"id": "printer", "title": "Printer says it is offline"}));

        assert_eq!(e.len(), 1, "renaming must not orphan the original");
        assert_eq!(e[0]["title"], "Printer says it is offline");
    }

    #[test]
    fn the_id_is_written_in_so_a_later_rename_still_matches() {
        let mut e = Vec::new();
        apply(&mut e, json!({"title": "Printer offline"}));
        assert_eq!(e[0]["id"], "printer-offline");

        // Now rename it, addressing it by the id it was given.
        apply(&mut e, json!({"id": "printer-offline", "title": "Printer is offline"}));
        assert_eq!(e.len(), 1);
        assert_eq!(e[0]["title"], "Printer is offline");
    }

    #[test]
    fn an_update_keeps_its_position() {
        let mut e = Vec::new();
        apply(&mut e, json!([{"title": "A"}, {"title": "B"}, {"title": "C"}]));
        apply(&mut e, json!({"title": "B", "answer": "new"}));

        let order: Vec<&str> = e.iter().map(|x| x["title"].as_str().unwrap()).collect();
        assert_eq!(order, vec!["A", "B", "C"], "the file must not reshuffle");
    }

    #[test]
    fn remove_takes_only_the_named_id() {
        let mut e = Vec::new();
        apply(&mut e, json!([{"title": "A"}, {"title": "B"}, {"title": "C"}]));
        let r = apply(&mut e, json!({"remove": ["b"]}));

        assert_eq!(r.removed, 1);
        let left: Vec<&str> = e.iter().map(|x| x["title"].as_str().unwrap()).collect();
        assert_eq!(left, vec!["A", "C"]);
    }

    #[test]
    fn removing_something_that_is_not_there_says_so() {
        let mut e = vec![json!({"title": "A", "id": "a"})];
        let r = apply(&mut e, json!({"remove": ["not-here"]}));

        assert_eq!(r.removed, 0);
        assert_eq!(r.missing, vec!["not-here".to_string()]);
        assert_eq!(e.len(), 1, "a miss must not touch anything");
    }

    #[test]
    fn add_and_remove_in_one_patch() {
        let mut e = vec![json!({"title": "Old", "id": "old"})];
        let r = apply(&mut e, json!({"add": [{"title": "New"}], "remove": "old"}));

        assert_eq!((r.added, r.removed), (1, 1));
        assert_eq!(e.len(), 1);
        assert_eq!(e[0]["title"], "New");
    }

    #[test]
    fn a_bare_list_and_a_bare_entry_are_both_accepted() {
        let mut e = Vec::new();
        apply(&mut e, json!([{"title": "A"}, {"title": "B"}]));
        apply(&mut e, json!({"title": "C"}));
        assert_eq!(e.len(), 3);
    }

    #[test]
    fn a_whole_file_shape_is_accepted_as_a_patch() {
        // Pasting back a downloaded file must add its entries, not be refused
        // for having the wrong wrapper.
        let mut e = Vec::new();
        apply(&mut e, json!({"articles": [{"title": "A"}], "_note": "mine"}));
        assert_eq!(e.len(), 1);
    }

    #[test]
    fn an_entry_with_no_title_and_no_id_is_refused_by_position() {
        let err = parse_patch(&KB, json!([{"title": "A"}, {"answer": "orphan"}]))
            .expect_err("must refuse");
        assert!(err.contains("Entry 2"), "should name which one: {err}");
        assert!(err.contains("title"), "should name the field: {err}");
    }

    #[test]
    fn an_empty_patch_is_refused_rather_than_reported_as_a_success() {
        assert!(parse_patch(&KB, json!({"add": [], "remove": []})).is_err());
        assert!(parse_patch(&KB, json!({"_readme": "just a note"})).is_err());
    }

    #[test]
    fn hand_written_top_level_keys_survive_a_patch() {
        let mut shell = serde_json::Map::new();
        shell.insert("_note".into(), json!("do not lose me"));
        shell.insert("version".into(), json!(3));

        let mut e = vec![json!({"title": "A", "id": "a"})];
        apply(&mut e, json!({"add": [{"title": "B"}]}));

        let out = serialize_list(&KB, shell, e);
        let back: serde_json::Value = serde_json::from_slice(&out).unwrap();
        assert_eq!(back["_note"], "do not lose me");
        assert_eq!(back["version"], 3);
        assert_eq!(back["articles"].as_array().unwrap().len(), 2);
    }

    #[test]
    fn the_readme_key_from_an_example_file_is_not_copied_into_the_live_file() {
        let mut e = Vec::new();
        apply(&mut e, json!({"_readme": ["a long note"], "add": [{"title": "A"}]}));

        let out = serialize_list(&KB, serde_json::Map::new(), e);
        let back: serde_json::Value = serde_json::from_slice(&out).unwrap();
        assert!(back.get("_readme").is_none(), "the example's own notes must not land in the live file");
        assert_eq!(back["articles"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn the_output_is_what_kb_py_expects() {
        // kb.py reads raw["articles"] when the file is a dict. If this shape
        // ever drifts, /mama silently loads zero articles.
        let mut e = Vec::new();
        apply(&mut e, json!({"title": "Printer offline", "steps": ["one", "two"], "direct": true}));
        let out = serialize_list(&KB, serde_json::Map::new(), e);
        let back: serde_json::Value = serde_json::from_slice(&out).unwrap();

        let arts = back["articles"].as_array().expect("must be under 'articles'");
        assert_eq!(arts[0]["title"], "Printer offline");
        assert_eq!(arts[0]["direct"], true);
        assert_eq!(arts[0]["steps"][1], "two");
        assert!(out.ends_with(b"\n"), "a trailing newline keeps git and editors happy");
    }

    // ---- the example files shipped in Admin\ must paste in as-is ----------
    //
    // These are the files the README tells you to start from. If one of them
    // stops being a valid patch, the first thing anybody tries fails, and the
    // failure looks like the feature is broken rather than the example.
    //
    // The fixtures are copies of Admin\*.example.json. If you edit those, copy
    // them here too - a test against a stale copy proves nothing.

    fn count_articles(src: &str, t: &Target) -> usize {
        let v: serde_json::Value = serde_json::from_str(src).expect("example must be valid JSON");
        let p = parse_patch(t, v).expect("example must be a valid patch");
        let mut e = Vec::new();
        apply_patch(t, &mut e, p);
        e.len()
    }

    #[test]
    fn the_knowledge_base_example_pastes_in_as_is() {
        let n = count_articles(include_str!("../tests/fixtures/mom-kb.example.json"), &KB);
        assert!(n >= 2, "expected the example's articles, got {n}");
    }

    #[test]
    fn the_places_example_pastes_in_as_is() {
        const PLACES: Target = Target {
            key: "places", file: "places.json", label: "Places", sub: "", hint: "",
            accent: "", icon: "", list_key: "places", name_key: "name", noun: "place",
            tool_href: "", tool_label: "", tool_why: "",
        };
        let n = count_articles(include_str!("../tests/fixtures/places.example.json"), &PLACES);
        assert!(n >= 2, "expected the example's places, got {n}");
    }

    // -------------------------------------------------------------------------
    //  The picture backlog: parsing what the two services hand back, and the
    //  approved-state filter that keeps a checked-off item off the list.
    // -------------------------------------------------------------------------

    #[test]
    fn parse_missing_reads_kitchen_and_kb_shapes() {
        let kitchen = json!([
            {"title": "Peach Cobbler", "slug": "peach-cobbler", "missing": 11, "url": "/kitchen/r/abc"}
        ]);
        let rows = parse_missing(kitchen.as_array().unwrap().clone(), "kitchen", "slug");
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].key, "kitchen:peach-cobbler");
        assert_eq!(rows[0].missing, 11);
        assert_eq!(rows[0].url, "/kitchen/r/abc");

        let kb = json!([
            {"title": "Solid Red Light on Cox Gateway", "id": "solid-red-light-on-cox-gateway",
             "missing": 5, "url": "/mama/kb/a/solid-red-light-on-cox-gateway"}
        ]);
        let rows = parse_missing(kb.as_array().unwrap().clone(), "kb", "id");
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].key, "kb:solid-red-light-on-cox-gateway");
    }

    #[test]
    fn parse_missing_drops_one_bad_row_rather_than_failing_the_batch() {
        // Second entry is missing "missing" entirely -- a service bug or a
        // future field rename should not blank the whole page.
        let items = json!([
            {"title": "Good", "slug": "good", "missing": 2, "url": "/kitchen/r/1"},
            {"title": "Bad", "slug": "bad", "url": "/kitchen/r/2"}
        ]);
        let rows = parse_missing(items.as_array().unwrap().clone(), "kitchen", "slug");
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].key, "kitchen:good");
    }

    #[test]
    fn parse_missing_on_an_empty_list_is_an_empty_list_not_a_panic() {
        let rows = parse_missing(Vec::new(), "kitchen", "slug");
        assert!(rows.is_empty());
    }

    fn row(source: &'static str, id: &str, title: &str, missing: u32) -> PictureRow {
        PictureRow {
            source,
            key: format!("{source}:{id}"),
            title: title.to_string(),
            missing,
            url: format!("/{source}/{id}"),
        }
    }

    #[test]
    fn build_rows_merges_all_sources_worst_first() {
        let kitchen = vec![row("kitchen", "a", "A", 2), row("kitchen", "b", "B", 9)];
        let kb = vec![row("kb", "c", "C", 5)];
        let food = vec![row("food", "d", "Onion", 1)];
        let tool = vec![row("tool", "e", "Dutch oven", 1)];
        let out = build_rows(vec![kitchen, kb, food, tool], &HashSet::new());
        let missing: Vec<u32> = out.iter().map(|r| r.missing).collect();
        assert_eq!(missing, vec![9, 5, 2, 1, 1], "expected worst-first across every source");
        assert_eq!(out.len(), 5);
    }

    #[test]
    fn build_rows_drops_approved_items() {
        let kitchen = vec![row("kitchen", "a", "A", 2), row("kitchen", "b", "B", 9)];
        let mut approved = HashSet::new();
        approved.insert("kitchen:b".to_string());
        let out = build_rows(vec![kitchen, Vec::new()], &approved);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].key, "kitchen:a");
    }

    #[test]
    fn build_rows_on_an_empty_source_list_is_empty_not_a_panic() {
        let out: Vec<PictureRowView> = build_rows(Vec::new(), &HashSet::new());
        assert!(out.is_empty());
    }

    #[test]
    fn parse_missing_reads_food_and_tool_rows_the_same_shape_as_kitchen_and_kb() {
        let items = json!([
            {"title": "Onion", "id": "food-1", "missing": 1, "url": "/kitchen/foods/food-1/photos"}
        ]);
        let rows = parse_missing(items.as_array().unwrap().clone(), "food", "id");
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].key, "food:food-1");
        assert_eq!(rows[0].source, "food");
    }
}
