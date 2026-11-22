//! Sign-in for the handful of people who use these sites.
//!
//! PASSWORDS, SET BY JASON, NOT EMAILED LINKS
//! This used to send a link and a six digit code by email. That worked only as
//! well as the mail did, and the mail needed a paid sending service and a
//! domain nobody here owns -- so in practice it worked not at all: the code was
//! written to a log file that Davetta and Casey have no way to read.
//!
//! So the accounts are managed the way /mama already manages its own: Jason
//! adds a person in the admin and chooses their password, and hands it to them
//! however he likes. There is no mail to send, nothing to pay for, and nothing
//! that can silently fail between here and somebody else's spam filter.
//!
//! A password chosen for somebody else is marked `must_change`, and they are
//! made to pick their own the first time they sign in. That matters more than
//! it looks: the password Jason typed has been said out loud, sent in a text,
//! or written on paper, and it should stop being the password as soon as it has
//! done its one job.
//!
//! WHY IT IS A SEPARATE SERVICE AND NOT CODE IN EACH APP
//! /casey and /mama are working FastAPI apps. Adding login to both would mean
//! editing two codebases that are currently fine. Instead Caddy asks THIS
//! service "is this person allowed?" before it proxies anything (forward_auth),
//! so those apps need no changes at all and cannot be reached un-authenticated
//! even if they later grow a route that forgets to check.
//!
//! THE SESSION IS A SIGNED COOKIE, NOT SERVER STATE
//! Six months of sessions held in memory would vanish on every restart and grow
//! forever otherwise. The cookie carries the email and its expiry, signed with
//! HMAC-SHA256. It cannot be forged without the secret, needs no storage, and
//! survives restarts. The one thing a stateless cookie cannot do by itself is
//! be revoked -- so there is a small revocation file for that; see `revoked`.
//!
//! WHAT THIS IS HONESTLY WORTH
//! A password behind HTTPS, hashed with argon2id, with guesses slowed down. For
//! family photos and two small personal apps that is a fair trade. It would not
//! be for anything financial, and it is only as good as the passwords chosen --
//! which is why the minimum is enforced here rather than suggested.

mod bridge;
mod llm;
mod mail;
mod oidc;
mod passkeys;
mod reset;

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use askama::Template;
use axum::{
    extract::{Query, State},
    http::{header, HeaderMap, StatusCode},
    response::{Html, IntoResponse, Redirect, Response},
    routing::{get, post},
    Router,
};
use hmac::{Hmac, KeyInit, Mac};
use serde::Deserialize;
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

const COOKIE: &str = "sso";

// -----------------------------------------------------------------------------
//  State
// -----------------------------------------------------------------------------

/// How long a run of wrong passwords locks one account out.
///
/// PER EMAIL, NOT GLOBAL, and not per IP. Docker Desktop NATs every connection
/// so the real client address never reaches this process -- the Caddyfile spells
/// this out -- which makes per-IP limiting a lie that counts every attacker on
/// earth as one visitor. A global lock would be worse: anyone could shut all
/// three people out by guessing at one of them.
///
/// Per-email is the honest unit, and it does have a cost: somebody who knows
/// Davetta's address can keep Davetta locked out. Against that, the ceiling is
/// five minutes and it clears itself, which is a nuisance rather than a
/// lockout. For an unadvertised family site that trade is the right way round.
///
/// TIGHTENED 2026-08-28, Jason's call: three wrong passwords, then five
/// minutes. The previous ladder allowed four free guesses and only reached
/// five minutes after eleven, which is a lot of attempts at a six-character
/// minimum before anything slows down.
///
/// Flat rather than escalating. An escalating ladder rewards patience -- wait
/// out the thirty seconds and you get three more guesses at a lower cost --
/// and it makes the rule impossible to state in one sentence, which matters
/// when the person hitting it is family who mistyped.
fn lock_for(fails: u32) -> Option<Duration> {
    match fails {
        0..=2 => None,                              // three tries, no delay
        _ => Some(Duration::from_secs(300)),        // then five minutes, every time
    }
}

#[derive(Default)]
struct Gate {
    fails: u32,
    locked_until: Option<Instant>,
}

struct AppState {
    /// The starting list, from AUTH_ALLOWED_EMAILS. Used only when the file
    /// below does not exist yet - after that, the file is the truth. Accounts
    /// seeded this way have NO password and cannot be signed into until one is
    /// set in the admin, which is the safe direction to fail.
    allowed_seed: Vec<String>,
    /// Managed from the admin's People page. Read fresh on every check so
    /// adding or removing somebody takes effect immediately, with no restart
    /// and no redeploy - which is the whole point of having a console.
    people_file: PathBuf,
    secret: Vec<u8>,
    session_days: u64,
    cookie_secure: bool,
    /// email -> "every cookie issued before this timestamp is void".
    /// This is what makes a stateless cookie revocable.
    revoked_file: PathBuf,
    /// Pending one-time codes for "I forgot my password". A file, not memory,
    /// so a container restart does not silently kill a code somebody is
    /// halfway through using. See reset.rs for what is (and is not) stored.
    reset_file: PathBuf,
    /// Trial access: invite codes, and who has redeemed one. See trial.rs for
    /// why a trial reaches exactly one route and nothing else.
    trial_file: PathBuf,
    /// Held across the whole read-modify-write of the trial file, for the same
    /// reason as reset_lock below. Without it two questions sent together both
    /// read the same counter and only one of them is ever charged, which over
    /// a quota of eight is not a rounding error.
    trial_lock: tokio::sync::Mutex<()>,
    /// Held across the whole read-modify-write of the reset file. Without it,
    /// two requests arriving together could both read the same entry and both
    /// succeed with one code - which is exactly the single-use guarantee that
    /// makes a 6-digit code acceptable in the first place.
    reset_lock: Mutex<()>,
    /// Wrong-password counters, per email. In memory on purpose: restarting the
    /// container clears them, which is the escape hatch when somebody is locked
    /// out and standing next to the machine.
    gates: Mutex<HashMap<String, Gate>>,
    /// See the comment where it is built: it exists so an unknown address costs
    /// the same time as a known one.
    decoy_hash: String,
    /// Single-use codes that walk the session cookie onto our duckdns
    /// subdomains -- see bridge.rs for the whole story.
    pub(crate) bridge: bridge::BridgeStore,
    /// The OpenID Connect provider, when one is configured.
    ///
    /// OPTIONAL ON PURPOSE. Apps behind Caddy are gated by /verify and need
    /// nothing from this; OIDC exists only for clients that cannot be put
    /// behind forward_auth, which today means Immich and its phone app. When
    /// it is not configured those endpoints answer 503 and everything else
    /// carries on -- refusing to boot would take the sign-in page down with
    /// it, which is a far worse failure than one app losing its login.
    oidc: Option<oidc::Oidc>,
    /// Passkey sign-in, a second way in beside the password. Optional for the
    /// same reason as OIDC: if it cannot start, the password form must not go
    /// with it. See passkeys.rs and PASSKEYS.md.
    passkeys: Option<passkeys::Passkeys>,
}

/// Who may sign in, right now, with their password hashes.
///
/// The FILE wins when it exists. The env var only seeds the first run, so that
/// a fresh install has the three of you in it without anyone having to open a
/// console first - and so that removing somebody in the admin is not silently
/// undone by an old value still sitting in .env.
///
/// Read on every check rather than cached. It is a handful of lines of JSON,
/// and the alternative is that removing somebody does not actually remove them
/// until something restarts.
pub(crate) fn people(state: &AppState) -> Vec<common::people::Person> {
    if state.people_file.exists() {
        return common::people::read(&state.people_file);
    }
    state
        .allowed_seed
        .iter()
        .map(|e| common::people::Person::new(e))
        .collect()
}

fn now() -> u64 {
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

// -----------------------------------------------------------------------------
//  The signed cookie
// -----------------------------------------------------------------------------

fn sign(state: &AppState, payload: &str) -> String {
    let mut mac = HmacSha256::new_from_slice(&state.secret).expect("hmac accepts any key length");
    mac.update(payload.as_bytes());
    hex(&mac.finalize().into_bytes())
}

/// A short, non-reversible mark of which password this is.
///
/// WHY THE COOKIE CARRIES ONE. Changing a password has to end the sessions the
/// OLD password created, or a compromise that the change was meant to close
/// stays open for six months. Doing that through the revocation file works only
/// when whoever changed the password remembers to write to it - and the admin,
/// which is where a forgotten password gets reset, is a different process that
/// would have to be kept in step forever.
///
/// Carrying the fingerprint instead makes it automatic and impossible to
/// forget: a changed hash is a changed fingerprint, and every cookie minted
/// under the old one stops verifying. It is HMAC'd with the same secret rather
/// than being a plain hash of the hash, so the cookie does not hand out a
/// checkable fragment of the stored credential.
fn password_fingerprint(state: &AppState, hash: &str) -> String {
    sign(state, hash).chars().take(12).collect()
}

/// `<hex(email|issued|expires|fingerprint)>.<hmac>`
fn make_cookie_value(state: &AppState, email: &str, hash: &str) -> String {
    let issued = now();
    let expires = issued + state.session_days * 86400;
    let fp = password_fingerprint(state, hash);
    let payload = hex(format!("{email}|{issued}|{expires}|{fp}").as_bytes());
    let sig = sign(state, &payload);
    format!("{payload}.{sig}")
}

/// Returns the email if the cookie is genuine, unexpired and not revoked.
fn read_cookie_value(state: &AppState, raw: &str) -> Option<String> {
    let (payload, sig) = raw.split_once('.')?;

    // Verify BEFORE parsing. Anything else means acting on attacker-controlled
    // bytes, however briefly.
    let expected = sign(state, payload);
    if !constant_time_eq(expected.as_bytes(), sig.as_bytes()) {
        return None;
    }

    let decoded = String::from_utf8(unhex(payload)?).ok()?;
    let mut parts = decoded.split('|');
    let email = parts.next()?.to_string();
    let issued: u64 = parts.next()?.parse().ok()?;
    let expires: u64 = parts.next()?.parse().ok()?;
    // Absent on a cookie minted before fingerprints existed. Those are treated
    // as not matching, which signs those people out once. That is the right way
    // round: the alternative is honouring a session whose password may since
    // have been reset.
    let fp = parts.next().unwrap_or("").to_string();

    let t = now();
    if t >= expires {
        return None;
    }
    // Still on the list? Removing someone in the admin must lock them out on
    // their very next request, not in six months when their cookie lapses.
    // This is why a six-month session is safe to hand out.
    // A person with no password is treated as off the list too. That state
    // means "the account exists but is not usable yet", and a cookie from
    // before a password was cleared must not outlive the clearing.
    let list = people(state);
    match common::people::find(&list, &email) {
        Some(p) if p.has_password() => {
            if !constant_time_eq(
                password_fingerprint(state, &p.hash).as_bytes(),
                fp.as_bytes(),
            ) {
                // The password changed since this cookie was issued - here, or
                // in the admin. Either way this session is over.
                return None;
            }
        }
        _ => return None,
    }
    if issued < revoked_at(state, &email) {
        return None;
    }
    // A guest whose account has ended is out on their very next request
    // (checked fresh from passkeys.json each time, nothing cached).
    if passkeys::account_ended(state, &email) {
        return None;
    }
    Some(email)
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

// -----------------------------------------------------------------------------
//  Revocation - the one thing a stateless cookie cannot do alone
// -----------------------------------------------------------------------------

fn revoked_map(state: &AppState) -> HashMap<String, u64> {
    std::fs::read(&state.revoked_file)
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or_default()
}

pub(crate) fn revoked_at(state: &AppState, email: &str) -> u64 {
    revoked_map(state).get(email).copied().unwrap_or(0)
}

/// Who an OIDC access token belongs to, or None.
///
/// THE ONE PLACE THAT DECIDES whether a phone app's token is still good, so
/// /oidc/userinfo and every /app route give the same answer. Three checks,
/// and the last two are the ones that were missing:
///
///   1. the token exists and has not expired;
///   2. the person still has an account -- people.json is re-read here, so
///      removing somebody cuts off their app as fast as it cuts off the
///      website, rather than up to thirty days later;
///   3. the token predates no revocation -- "sign out everywhere" works on
///      the app as well as on the browser.
pub fn token_holder(state: &AppState, token: &str) -> Option<String> {
    let o = state.oidc.as_ref()?;
    let (email, issued) = o.access_record(token)?;
    if issued < revoked_at(state, &email) {
        return None;
    }
    if common::people::find(&people(state), &email).is_none() {
        return None;
    }
    Some(email)
}

/// "Sign out everywhere" for one person: void every cookie issued so far.
fn revoke_all(state: &AppState, email: &str) {
    let mut m = revoked_map(state);
    m.insert(email.to_string(), now());
    if let Some(p) = state.revoked_file.parent() {
        std::fs::create_dir_all(p).ok();
    }
    if let Ok(j) = serde_json::to_vec_pretty(&m) {
        let tmp = state.revoked_file.with_extension("tmp");
        if std::fs::write(&tmp, j).is_ok() {
            std::fs::rename(&tmp, &state.revoked_file).ok();
        }
    }
    // The file above is what makes this survive a restart; this drops the
    // tokens already in memory so nothing keeps working until the next sweep.
    if let Some(o) = state.oidc.as_ref() {
        let gone = o.forget(email);
        if gone > 0 {
            tracing::info!("revoked {gone} live app token(s) for {email}");
        }
    }
}

// -----------------------------------------------------------------------------
//  Pending magic links
#[derive(Template)]
#[template(path = "signin.html")]
struct SigninTemplate {
    error: String,
    email: String,
    /// For the hidden form field, where HTML escaping is the right escaping
    /// and the whole value arrives in the POST body.
    next: String,
    /// The SAME value, percent-encoded, for the "forgotten your password"
    /// LINK.
    ///
    /// A URL in a query parameter has to be percent-encoded, and HTML
    /// escaping is not that. `next` here is a whole OIDC authorize request
    /// -- `/auth/oidc/authorize?client_id=garden&redirect_uri=...` --
    /// and dropped raw into `forgot?next=...` the browser splits it at its
    /// first `&`. The link then carries only `client_id`, and everything
    /// after it arrives as separate query parameters of the forgot page.
    ///
    /// Robin hit this on 2026-09-25 setting his password: reset succeeded,
    /// sent him back to `/auth/oidc/authorize?client_id=garden` with no
    /// redirect_uri, and authorize rightly refused a request that was
    /// missing everything except the client. It looked like the app was
    /// broken; the app never sent that URL.
    next_q: String,
    /// Show the passkey button and load its script.
    passkeys: bool,
}

#[derive(Template)]
#[template(path = "forgot.html")]
struct ForgotTemplate {
    error: String,
    email: String,
    next: String,
    /// Percent-encoded, for the links. See SigninTemplate::next_q.
    next_q: String,
}

#[derive(Template)]
#[template(path = "reset.html")]
struct ResetTemplate {
    error: String,
    /// Shown after a code has just been sent. Separate from `error` so the
    /// page can say "we sent it" and "that code was wrong" in the right
    /// colours instead of one field doing both jobs badly.
    notice: String,
    email: String,
    next: String,
    /// Percent-encoded, for the links. See SigninTemplate::next_q.
    next_q: String,
}

#[derive(Template)]
#[template(path = "change.html")]
struct ChangeTemplate {
    error: String,
    email: String,
    next: String,
    /// True on the forced first sign-in, which changes the wording from "you
    /// may change this" to "you need to". Same form either way.
    forced: bool,
    min: usize,
}

// -----------------------------------------------------------------------------
//  Handlers
// -----------------------------------------------------------------------------

async fn healthz() -> &'static str {
    "ok"
}

#[derive(Deserialize)]
struct NextQuery {
    #[serde(default)]
    next: String,
}

/// Only ever redirect to a path on THIS site.
///
/// Without this, `?next=https://evil.example` turns a trusted sign-in page into
/// an open redirect - the classic way a phishing link gets to wear your domain
/// in the part of the URL people actually read.
fn safe_next(next: &str) -> String {
    // Browsers read "/\evil.com" as "//evil.com" (a backslash counts as a
    // slash), and control characters (tab, newline) are stripped inside URLs,
    // so "/\t/evil.com" becomes "//evil.com" too. Refuse both outright.
    if next.chars().any(|c| c == '\\' || c.is_control()) {
        return "/".to_string();
    }
    if next.starts_with('/') && !next.starts_with("//") {
        return next.to_string();
    }
    // One exception to paths-only, added with the cookie bridge: a full URL
    // on one of OUR duckdns subdomains routes through /sso/bridge, which is
    // itself a local path -- so this still never redirects off-site blind.
    if bridge::trusted_absolute(next).is_some() {
        return format!("/sso/bridge?next={}", urlencode(next));
    }
    "/".to_string()
}

fn render_signin(error: &str, email: &str, next: &str, code: StatusCode) -> Response {
    let html = SigninTemplate {
        error: error.to_string(),
        email: email.to_string(),
        next: next.to_string(),
        next_q: urlencode(next),
        passkeys: passkeys::ENABLED.load(std::sync::atomic::Ordering::Relaxed),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    (code, Html(html)).into_response()
}

fn render_change(error: &str, email: &str, next: &str, forced: bool, code: StatusCode) -> Response {
    let html = ChangeTemplate {
        error: error.to_string(),
        email: email.to_string(),
        next: next.to_string(),
        forced,
        min: common::people::MIN_PASSWORD,
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    (code, Html(html)).into_response()
}

async fn signin_page(
    State(state): State<Arc<AppState>>,
    Query(q): Query<NextQuery>,
    headers: HeaderMap,
) -> Response {
    // An existing session walks straight through -- safe_next routes
    // subdomain destinations over the cookie bridge. Showing the password
    // form to somebody already signed in is what made every bridged
    // subdomain feel like a second login (caught 2026-09-01).
    if cookie_email(&state, &headers).is_some() {
        return Redirect::to(&safe_next(&q.next)).into_response();
    }
    render_signin("", "", &safe_next(&q.next), StatusCode::OK)
}

/// Sign in with an email address and a password.
///
/// THE WRONG-PASSWORD MESSAGE NEVER SAYS WHICH HALF WAS WRONG. "No such
/// account" and "wrong password" as separate messages turn this page into a
/// tool for finding out who has an account here, which is the first thing
/// anybody probing it would want to know. One message, both cases.
///
/// The delay for a locked account is stated plainly rather than hidden, because
/// the person hitting it is almost always the actual owner mistyping, and
/// "wrong password" repeated forever with no explanation is how somebody
/// concludes the site is broken and stops trying.
async fn sign_in(State(state): State<Arc<AppState>>, body: String) -> Response {
    let email = common::people::normalise(&form_field(&body, "email").unwrap_or_default());
    let password = form_field(&body, "password").unwrap_or_default();
    let next = safe_next(&form_field(&body, "next").unwrap_or_default());

    if email.is_empty() || password.is_empty() {
        return render_signin(
            "Fill in both boxes.",
            &email,
            &next,
            StatusCode::BAD_REQUEST,
        );
    }

    // Locked out? Say so before spending time on the hash.
    if let Ok(gates) = state.gates.lock() {
        if let Some(g) = gates.get(&email) {
            if let Some(until) = g.locked_until {
                if Instant::now() < until {
                    let secs = (until - Instant::now()).as_secs() + 1;
                    return render_signin(
                        &format!(
                            "Too many wrong passwords. Try again in {} second{}.",
                            secs,
                            if secs == 1 { "" } else { "s" }
                        ),
                        &email,
                        &next,
                        StatusCode::TOO_MANY_REQUESTS,
                    );
                }
            }
        }
    }

    let list = people(&state);
    let found = common::people::find(&list, &email).cloned();

    // Verify EVEN WHEN THE ACCOUNT DOES NOT EXIST, against a throwaway hash.
    // argon2 is deliberately slow, so returning instantly for an unknown
    // address and slowly for a known one tells an attacker which addresses are
    // real -- from timing alone, without the message ever differing.
    let ok = match &found {
        Some(p) => common::people::verify(&p.hash, &password),
        None => {
            let _ = common::people::verify(&state.decoy_hash, &password);
            false
        }
    };
    // Passkey-only people have password sign-in switched off. They get the
    // ordinary "don't match" answer (and the same lockout count), so the page
    // does not say who has that switch on. The email link is their way back.
    let ok = ok && !passkeys::password_off(&state, &email);

    // A RETIRED PASSWORD EXPLAINS ITSELF. An account whose password was
    // withdrawn for being weak has an empty hash, so `verify` refuses every
    // password -- correctly, but the generic "that email and password don't
    // match" would send the owner round in circles retyping the password
    // that was the problem. Saying so costs nothing an attacker can use:
    // it reveals only that this address cannot be signed into with a
    // password at all, and the way forward is the same emailed code
    // anybody could already ask for.
    if !ok
        && found
            .as_ref()
            .map(|p| p.hash.trim().is_empty() && p.must_change)
            .unwrap_or(false)
    {
        tracing::info!(email = %email, "sign-in attempt on a retired password");
        return render_signin(
            "That password was retired because it was too easy to guess. Use the \"Forgot your password\" link below and we will email you a one-time code to set a new one.",
            &email,
            &next,
            StatusCode::UNAUTHORIZED,
        );
    }

    if !ok {
        let mut wait = None;
        if let Ok(mut gates) = state.gates.lock() {
            let g = gates.entry(email.clone()).or_default();
            g.fails += 1;
            if let Some(d) = lock_for(g.fails) {
                g.locked_until = Some(Instant::now() + d);
                wait = Some(d.as_secs());
            }
        }
        // The log says WHICH address was tried, because that is the difference
        // between "Davetta keeps mistyping" and "somebody is probing this".
        tracing::warn!(email = %email, "wrong password");
        let msg = match wait {
            Some(s) => format!(
                "That email and password don't match. Too many tries now - wait {s} seconds."
            ),
            None => "That email and password don't match.".to_string(),
        };
        return render_signin(&msg, &email, &next, StatusCode::UNAUTHORIZED);
    }

    if let Ok(mut gates) = state.gates.lock() {
        gates.remove(&email);
    }

    // A password somebody else chose has now done its one job.
    if found.map(|p| p.must_change).unwrap_or(false) {
        tracing::info!(email = %email, "signed in, must choose a new password");
        return render_change("", &email, &next, true, StatusCode::OK);
    }

    tracing::info!(email = %email, "signed in");
    // Same session as ever; may put a short "add a passkey?" page in front of
    // `next` (never in place of it). See passkeys::after_password.
    passkeys::after_password(&state, &email, &next)
}

/// Choose a new password.
///
/// Reached two ways: forced, straight after a first sign-in with a password
/// Jason chose, and voluntarily from /auth/change while already signed in.
/// BOTH require the current password. Without that, a borrowed unlocked laptop
/// is enough to lock the real owner out of their own account.
async fn change_password(State(state): State<Arc<AppState>>, body: String) -> Response {
    let email = common::people::normalise(&form_field(&body, "email").unwrap_or_default());
    let current = form_field(&body, "current").unwrap_or_default();
    let fresh = form_field(&body, "password").unwrap_or_default();
    let again = form_field(&body, "confirm").unwrap_or_default();
    let next = safe_next(&form_field(&body, "next").unwrap_or_default());

    let mut list = people(&state);
    let idx = list.iter().position(|p| p.email == email);
    let Some(idx) = idx else {
        return render_signin("Sign in again.", &email, &next, StatusCode::UNAUTHORIZED);
    };
    let forced = list[idx].must_change;

    if !common::people::verify(&list[idx].hash, &current) {
        tracing::warn!(email = %email, "wrong current password on a change");
        return render_change(
            "That isn't your current password.",
            &email,
            &next,
            forced,
            StatusCode::UNAUTHORIZED,
        );
    }
    if let Some(why) = common::people::password_problem(&fresh) {
        return render_change(&why, &email, &next, forced, StatusCode::BAD_REQUEST);
    }
    if fresh != again {
        return render_change(
            "The two new passwords don't match.",
            &email,
            &next,
            forced,
            StatusCode::BAD_REQUEST,
        );
    }
    if fresh == current {
        // Refused rather than accepted quietly: the whole point of the forced
        // change is that the old one stops working, and "changed it to the same
        // thing" would leave the person believing it had.
        return render_change(
            "That's the password you already have. Pick a different one.",
            &email,
            &next,
            forced,
            StatusCode::BAD_REQUEST,
        );
    }

    let Ok(hash) = common::people::hash_password(&fresh) else {
        return render_change(
            "Could not save that. Try again.",
            &email,
            &next,
            forced,
            StatusCode::INTERNAL_SERVER_ERROR,
        );
    };
    list[idx].hash = hash;
    list[idx].must_change = false;
    if let Err(e) = common::people::write(&state.people_file, &list) {
        tracing::error!(email = %email, "could not write the people file: {e}");
        return render_change(
            "Could not save that. Try again.",
            &email,
            &next,
            forced,
            StatusCode::INTERNAL_SERVER_ERROR,
        );
    }

    // Every session issued before now is void. Changing a password has to end
    // the sessions the OLD password created, or a compromise the change was
    // meant to close stays open for six months.
    revoke_all(&state, &email);
    // Passkeys are kept on a normal change; this only makes sure the
    // passkey-only switch is not left on without the passkeys to back it.
    passkeys::after_password_change(&state, &email);
    tracing::info!(email = %email, "password changed");
    // Includes the first password a new person chooses: the offer comes next.
    passkeys::after_password(&state, &email, &next)
}

/// The voluntary version of the page above, for somebody already signed in.
async fn change_page(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(q): Query<NextQuery>,
) -> Response {
    match cookie_email(&state, &headers) {
        Some(email) => render_change("", &email, &safe_next(&q.next), false, StatusCode::OK),
        None => render_signin("Sign in first.", "", &safe_next(&q.next), StatusCode::UNAUTHORIZED),
    }
}

/// Set the six month cookie and send them where they were going.
/// Shared by sign-in and by a password change so the two cannot drift apart.
pub(crate) fn issue_session(state: &AppState, email: &str, next: &str) -> Response {
    // Read the hash back rather than being handed it, so the cookie is stamped
    // with what is ON DISK at this moment. After a password change that is the
    // new one; getting this wrong would mint a cookie that immediately fails
    // its own fingerprint check and look like "the password change signed me
    // out and would not let me back in".
    let list = people(state);
    let hash = common::people::find(&list, email)
        .map(|p| p.hash.clone())
        .unwrap_or_default();
    let value = make_cookie_value(state, email, &hash);
    let max_age = state.session_days * 86400;
    let mut c = format!("{COOKIE}={value}; HttpOnly; SameSite=Lax; Path=/; Max-Age={max_age}");
    if state.cookie_secure {
        c.push_str("; Secure");
    }
    let mut out = HeaderMap::new();
    if let Ok(v) = c.parse() {
        out.insert(header::SET_COOKIE, v);
    }
    (out, Redirect::to(next)).into_response()
}

/// Which of common::people::ALL_APPS a forwarded request URI belongs to, or
/// None for anything not on the per-app toggle (the admin panel, /auth
/// itself, /dee, /legacy -- being on the sign-in list is still the only gate
/// there).
///
/// DERIVED FROM ALL_APPS, NOT HAND-WRITTEN. This used to be a chain of
/// `else if uri.starts_with("/git")` arms, one per app, which meant every
/// new app had to be added in THREE places that all had to agree: the
/// Caddyfile block, ALL_APPS (for the checkbox), and here (for the gate).
/// Miss this one and the app still asks for a login but the per-app toggle
/// silently does nothing -- it looks protected and is not. Jason hit exactly
/// that on 2026-08-28 and asked, reasonably, why this is not automatic.
///
/// Now there is one list. Adding a name to ALL_APPS gives it a checkbox AND
/// a gate; the only remaining manual step is the Caddyfile block that routes
/// it, and that one is not silent -- forget it and the app is unreachable
/// rather than unprotected, which is the right way round to fail.
///
/// LONGEST PREFIX WINS. `/mama/kb` has to beat `/mama`, the same ordering
/// the Caddyfile needs for the same reason. Sorting by length removes the
/// need to remember that: the old chain worked only because someone put the
/// arms in the right order by hand.
/// What "view only" refuses, per app. Pure so it can be tested as a table.
///
/// The base rule is METHODS: viewing is GET/HEAD/OPTIONS, everything else is
/// a change and is refused. On top of that, some apps have GET endpoints
/// that act or reveal too much, and those paths are refused for viewers too:
///
///   special (Frigate): /export (downloads recordings out of the house) and
///   /config (the whole camera setup) are not "viewing the cameras".
///
/// Growing this table is the intended way to tighten an app -- one line
/// here, no app changes, takes effect on the next request.
fn view_blocks(app: &str, method: &str, uri: &str) -> bool {
    let m = method.to_ascii_uppercase();
    if m != "GET" && m != "HEAD" && m != "OPTIONS" {
        return true;
    }
    let path = uri.split(['?', '#']).next().unwrap_or(uri);
    match app {
        "special" => {
            path.contains("/export") || path.contains("/config") || path.contains("/settings")
        }
        // Uptime Kuma. A viewer gets the STATUS PAGES and nothing else --
        // Kuma's admin surface is a websocket (/socket.io) where the gate
        // cannot see individual actions, so for view the whole socket is
        // off limits, along with the dashboard and settings. The status
        // pages themselves are plain pages with their own small API.
        "status" => {
            path.starts_with("/dashboard")
                || path.starts_with("/settings")
                || path.starts_with("/manage")
                || path.starts_with("/socket.io")
                || path.starts_with("/add")
                || (path.starts_with("/api/")
                    && !path.starts_with("/api/status-page")
                    && !path.starts_with("/api/entry-page")
                    && !path.starts_with("/api/badge"))
        }
        _ => false,
    }
}

fn app_for_uri(uri: &str) -> Option<&'static str> {
    // Path only -- a query string must never decide which app you are in.
    let path = uri.split(['?', '#']).next().unwrap_or(uri);

    let mut best: Option<&'static str> = None;
    let mut best_len = 0usize;
    for app in common::people::ALL_APPS {
        let prefix = format!("/{app}");
        // A prefix match must end at a path boundary, or "/statuspage" would
        // be gated as "status" and "/gitea-docs" as "git".
        let hit = path == prefix
            || path.strip_prefix(&prefix).is_some_and(|r| r.starts_with('/'));
        if hit && prefix.len() > best_len {
            best_len = prefix.len();
            best = Some(app);
        }
    }
    // "kb" lives UNDER /mama rather than at /kb, so the loop above cannot
    // find it by name. This is the one genuine exception, and it is here
    // rather than in the loop so that adding a normal app stays a one-line
    // change to ALL_APPS.
    if path == "/mama/kb" || path.starts_with("/mama/kb/") {
        return Some("kb");
    }
    best
}

/// The endpoint Caddy's forward_auth calls before proxying anything protected.
///
/// 2xx  -> Caddy proxies the request, with X-Auth-Email copied in.
/// 302  -> Caddy returns THAT to the browser, so the visitor lands on sign-in.
/// 403  -> signed in, but this door specifically is not theirs -- see
///         Person::can_use() and app_for_uri(). Not a redirect: sending them
///         back to sign in again would not change the answer, so it would
///         just be a loop.
async fn verify(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    let uri = headers
        .get("X-Forwarded-Uri")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("/")
        .to_string();

    if let Some(email) = cookie_email(&state, &headers) {
        let mut level_out = "full";
        // WHICH APP IS THIS? On the main site, the path decides. On a
        // bridged subdomain the path belongs to the app itself (Kuma's own
        // /dashboard must not read as the household 'dashboard' app), so
        // the subdomain's first label names the app instead -- when that
        // label is one we ship. status.home.example.net -> "status".
        let fwd_host = headers
            .get("X-Forwarded-Host")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        let host_app = fwd_host
            .strip_suffix(".home.example.net")
            .filter(|l| common::people::ALL_APPS.contains(l));
        if let Some(app) = host_app.or_else(|| app_for_uri(&uri)) {
            let list = people(&state);
            let level = common::people::find(&list, &email)
                .and_then(|p| p.level_for(app).map(|l| l.to_string()));
            let Some(level) = level else {
                return (
                    StatusCode::FORBIDDEN,
                    "Your account does not have access to this. Ask Jason if you think it should.",
                )
                    .into_response();
            };
            // GRANULAR LEVELS (2026-08-31, Jason's design): a "view" grant
            // is enforced HERE, at the one gate every request already
            // passes, so an app needs no changes of its own to become
            // view-only for somebody. The method arrives from Caddy the
            // same way the original URI does.
            let method = headers
                .get("X-Forwarded-Method")
                .and_then(|v| v.to_str().ok())
                .unwrap_or("GET");
            if level == "view" && view_blocks(app, method, &uri) {
                return (
                    StatusCode::FORBIDDEN,
                    "Your access here is view-only. Ask Jason if you need more.",
                )
                    .into_response();
            }
            level_out = if level == "view" { "view" } else { "full" };
        }
        let mut out = HeaderMap::new();
        if let Ok(v) = email.parse() {
            // The app behind Caddy can read this to know who it is serving.
            out.insert("X-Auth-Email", v);
        }
        if let Ok(v) = level_out.parse() {
            // ...and this to know how much of itself to offer them.
            out.insert("X-Auth-Level", v);
        }
        return (StatusCode::OK, out).into_response();
    }

    // Send them to sign in, and remember where they were headed. A request
    // from one of the bridged subdomains carries a host the cookie cannot
    // reach, so its sign-in must happen on the main site with the FULL
    // destination -- safe_next turns that into a /sso/bridge hop after the
    // password (or silently, when a session already exists).
    let host = headers
        .get("X-Forwarded-Host")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    if host.ends_with(".home.example.net") {
        let dest = format!("https://{host}{uri}");
        return Redirect::to(&format!(
            "https://example.org/auth/?next={}",
            urlencode(&dest)
        ))
        .into_response();
    }
    Redirect::to(&format!("/auth/?next={}", urlencode(&uri))).into_response()
}

pub(crate) fn cookie_email(state: &AppState, headers: &HeaderMap) -> Option<String> {
    let raw = headers.get(header::COOKIE)?.to_str().ok()?;
    for part in raw.split(';') {
        if let Some(v) = part.trim().strip_prefix(&format!("{COOKIE}=")) {
            return read_cookie_value(state, v);
        }
    }
    None
}

/// `GET /levels?email=…&apps=recipe,gym,paperless` -- what one person is
/// allowed to do with each of several apps.
///
/// WHY THIS EXISTS. `X-Auth-Level` tells an app how much of ITSELF to
/// offer, which is the right answer to "may they use the drop page". It is
/// the wrong answer to "may they file a RECIPE", because that is a grant on
/// a different app entirely. intake classifies a file as a recipe and then
/// has to know whether this person may write to recipes -- and the only
/// place that knows is here.
///
/// A SERVICE KEY, AND IT FAILS CLOSED. `/auth/*` is proxied straight off
/// the public hostname, so without this the whole family's permission map
/// would be one unauthenticated GET away. An unset key refuses everything
/// rather than defaulting open: the failure mode of a missing secret must
/// be "nothing works", never "everything is readable".
///
/// It answers only for the apps asked about, and says nothing about whether
/// the address is one it knows -- an unknown person and a person with no
/// grant both come back as null, so this cannot be used to enumerate the
/// household.
async fn levels(
    State(state): State<Arc<AppState>>,
    Query(q): Query<HashMap<String, String>>,
    headers: HeaderMap,
) -> Response {
    let expected = std::env::var("SERVICE_KEY").unwrap_or_default();
    let offered = headers
        .get("X-Service-Key")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    if expected.is_empty() || offered != expected {
        return (StatusCode::FORBIDDEN, "no").into_response();
    }

    let email = common::people::normalise(q.get("email").map(String::as_str).unwrap_or(""));
    let apps: Vec<String> = q
        .get("apps")
        .map(|s| {
            s.split(',')
                .map(|a| a.trim().to_lowercase())
                .filter(|a| !a.is_empty())
                .collect()
        })
        .unwrap_or_default();
    if email.is_empty() || apps.is_empty() {
        return (StatusCode::BAD_REQUEST, "email and apps are required").into_response();
    }

    let list = people(&state);
    let found = common::people::find(&list, &email);
    let mut out = serde_json::Map::new();
    for app in &apps {
        let level = found.and_then(|p| p.level_for(app));
        out.insert(
            app.clone(),
            match level {
                Some(l) => serde_json::Value::String(l.to_string()),
                None => serde_json::Value::Null,
            },
        );
    }
    axum::Json(serde_json::json!({
        "email": email,
        "levels": serde_json::Value::Object(out),
    }))
    .into_response()
}

async fn whoami(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    match cookie_email(&state, &headers) {
        Some(e) => e.into_response(),
        None => (StatusCode::UNAUTHORIZED, "not signed in").into_response(),
    }
}

async fn signout(State(state): State<Arc<AppState>>) -> Response {
    let mut c = format!("{COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0");
    if state.cookie_secure {
        c.push_str("; Secure");
    }
    let mut out = HeaderMap::new();
    if let Ok(v) = c.parse() {
        out.insert(header::SET_COOKIE, v);
    }
    (out, Redirect::to("/auth/")).into_response()
}

/// Sign out on every device. The only real answer to a lost phone when the
/// session lasts six months.
async fn signout_all(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    if let Some(email) = cookie_email(&state, &headers) {
        revoke_all(&state, &email);
        tracing::info!(email = %email, "signed out everywhere");
    }
    signout(State(state)).await
}

/// A real, visitable page at /auth/logout -- THE BUG THIS FIXES (found live,
/// 2026-10-29): this route did not exist at all. /signout above has existed
/// all along and genuinely works (it clears this browser's own session
/// cookie), but it is a POST-only form target for the in-app sign-out
/// button -- visiting /logout as a URL hit nothing, a bare 404, so NO
/// session code ran at all. That is not "sign-out is broken"; it is "there
/// was nothing here to sign anyone out at all."
///
/// SAME SCOPE AS /signout, DELIBERATELY: clears THIS browser's cookie only,
/// not every device (that is what the separate, stronger /signout-all is
/// for -- see revoke_all). This system's sessions are intentionally
/// independent per device/browser (see make_cookie_value's own doc comment),
/// so a plain sign-out legitimately leaves any OTHER already-open device
/// signed in; the page below says so rather than implying otherwise.
pub(crate) async fn logout_page(State(state): State<Arc<AppState>>) -> Response {
    let mut c = format!("{COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0");
    if state.cookie_secure {
        c.push_str("; Secure");
    }
    let mut out = HeaderMap::new();
    if let Ok(v) = c.parse() {
        out.insert(header::SET_COOKIE, v);
    }
    let html = LogoutTemplate {}
        .render()
        .unwrap_or_else(|_| "<h1>Signed out.</h1>".into());
    (out, Html(html)).into_response()
}

#[derive(Template)]
#[template(path = "logout.html")]
struct LogoutTemplate {}


// -----------------------------------------------------------------------------
//  Forgotten passwords
//
//  THE SHAPE OF THIS FLOW, AND WHY
//  Two pages: ask for the address, then enter the code and the new password.
//  The second page is reachable directly, so somebody who closes the tab and
//  comes back to the email an hour later still has somewhere to put the code.
//
//  NEITHER PAGE EVER SAYS WHETHER AN ADDRESS HAS AN ACCOUNT. Asking for a code
//  always shows the same "check your email" page, whether or not the address is
//  known. A form that answers that question honestly is a tool for listing who
//  lives here, and this site's user list is a household's names.
// -----------------------------------------------------------------------------

fn render_forgot(error: &str, email: &str, next: &str, code: StatusCode) -> Response {
    let html = ForgotTemplate {
        error: error.to_string(),
        email: email.to_string(),
        next: next.to_string(),
        next_q: urlencode(next),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    (code, Html(html)).into_response()
}

fn render_reset(error: &str, notice: &str, email: &str, next: &str, code: StatusCode) -> Response {
    let html = ResetTemplate {
        error: error.to_string(),
        notice: notice.to_string(),
        email: email.to_string(),
        next: next.to_string(),
        next_q: urlencode(next),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    (code, Html(html)).into_response()
}

async fn forgot_page(State(_s): State<Arc<AppState>>, Query(q): Query<NextQuery>) -> Response {
    render_forgot("", "", &safe_next(&q.next), StatusCode::OK)
}

async fn reset_page(State(_s): State<Arc<AppState>>, Query(q): Query<NextQuery>) -> Response {
    render_reset("", "", "", &safe_next(&q.next), StatusCode::OK)
}

/// Step one: somebody typed their address and asked for a code.
async fn send_code(State(state): State<Arc<AppState>>, body: String) -> Response {
    let email = common::people::normalise(&form_field(&body, "email").unwrap_or_default());
    let next = safe_next(&form_field(&body, "next").unwrap_or_default());

    if email.is_empty() {
        return render_forgot("Type your email address.", "", &next, StatusCode::BAD_REQUEST);
    }

    // The message shown NO MATTER WHAT HAPPENS BELOW. Written once here so it
    // cannot drift apart between the branches - the moment the known and
    // unknown cases word things differently, the enumeration is back.
    let sent_notice = "If that address has an account, a code is on its way. \
                       It works once and lasts 15 minutes.";

    let known = common::people::find(&people(&state), &email).is_some();
    if !known {
        // Deliberately still logged: a burst of these is somebody guessing at
        // addresses, and that is worth being able to see afterwards.
        tracing::info!(email = %email, "reset asked for an address with no account");
        return render_reset("", sent_notice, &email, &next, StatusCode::OK);
    }

    let code = {
        let _guard = state.reset_lock.lock();
        let now = common::people::now();
        let mut list = reset::read(&state.reset_file);
        reset::prune(&mut list, now);

        // THE COOLDOWN AND THE HOURLY CAP BOTH LIVE IN reset::issue NOW.
        //
        // A one-per-minute guard used to sit here, and it read the code's own
        // expiry to work out when the code was made -- so it stopped existing
        // the moment that code expired or burned through its guesses. That was
        // the hole: five wrong guesses destroyed the code, the guard went with
        // it, and the next request minted a fresh code with a fresh five
        // guesses. Unlimited tries at six digits, one button press at a time.
        //
        // The budget now belongs to the ADDRESS for an hour and survives the
        // code, so asking again does not refill it.
        let code = reset::new_code();
        match reset::issue(&mut list, &email, &code, now) {
            Err(e) => {
                tracing::error!(email = %email, "could not hash a reset code: {e}");
                return render_forgot(
                    "Could not do that right now. Try again in a moment.",
                    &email,
                    &next,
                    StatusCode::INTERNAL_SERVER_ERROR,
                );
            }
            // THE SAME NOTICE AS A SUCCESSFUL SEND, deliberately. Saying "wait
            // 40 seconds" here would confirm the address has an account, which
            // is precisely the enumeration the unknown-address branch above
            // goes to trouble to avoid. The log records what really happened.
            Ok(reset::Issued::TooSoon(secs)) => {
                tracing::info!(email = %email, secs, "reset asked again too soon; not resending");
                return render_reset("", sent_notice, &email, &next, StatusCode::OK);
            }
            Ok(reset::Issued::TooMany(secs)) => {
                tracing::warn!(email = %email, secs,
                               "reset send budget exhausted; not resending");
                return render_reset("", sent_notice, &email, &next, StatusCode::OK);
            }
            Ok(reset::Issued::Sent) => {}
        }
        if let Err(e) = reset::write(&state.reset_file, &list) {
            tracing::error!(email = %email, "could not write the reset file: {e}");
            return render_forgot(
                "Could not do that right now. Try again in a moment.",
                &email,
                &next,
                StatusCode::INTERNAL_SERVER_ERROR,
            );
        }
        code
    };

    // Sending happens AFTER the code is stored and the lock is released.
    // Storing second would mean a slow mail provider holds the file lock, and a
    // crash between the two would send a code that nothing can verify.
    match mail::send_reset_code(&email, &code).await {
        Ok(true) => tracing::info!(email = %email, "reset code emailed"),
        Ok(false) => {} // Already logged loudly by mail.rs - no outbox configured.
        Err(e) => {
            // The code is stored and valid; only delivery failed. Say nothing
            // different to the visitor (enumeration again), but make this the
            // loudest line in the log, because it is the one that strands
            // somebody who is locked out.
            tracing::error!(email = %email, "RESET CODE COULD NOT BE SENT: {e}");
        }
    }

    render_reset("", sent_notice, &email, &next, StatusCode::OK)
}

/// Step two: the code and a new password came back.
async fn use_code(State(state): State<Arc<AppState>>, body: String) -> Response {
    let email = common::people::normalise(&form_field(&body, "email").unwrap_or_default());
    let code = form_field(&body, "code").unwrap_or_default().trim().to_string();
    let fresh = form_field(&body, "password").unwrap_or_default();
    let again = form_field(&body, "confirm").unwrap_or_default();
    let next = safe_next(&form_field(&body, "next").unwrap_or_default());

    if email.is_empty() || code.is_empty() || fresh.is_empty() {
        return render_reset("Fill in every box.", "", &email, &next, StatusCode::BAD_REQUEST);
    }
    if fresh != again {
        return render_reset(
            "The two passwords don't match.",
            "",
            &email,
            &next,
            StatusCode::BAD_REQUEST,
        );
    }
    if let Some(why) = common::people::password_problem(&fresh) {
        return render_reset(
            &why,
            "",
            &email,
            &next,
            StatusCode::BAD_REQUEST,
        );
    }

    // Hash the new password BEFORE touching the reset file, so the slow part
    // happens outside the lock. Doing it inside would let a handful of
    // simultaneous resets queue up behind each other for seconds at a time.
    let Ok(hash) = common::people::hash_password(&fresh) else {
        return render_reset(
            "Could not save that. Try again.",
            "",
            &email,
            &next,
            StatusCode::INTERNAL_SERVER_ERROR,
        );
    };

    {
        let _guard = state.reset_lock.lock();
        let now = common::people::now();
        let mut list = reset::read(&state.reset_file);
        let verdict = reset::check(&mut list, &email, &code, now);
        // Write back whichever way it went: success consumed the entry, a wrong
        // guess incremented the counter. Skipping this on failure would make
        // the five-attempt cap decorative.
        if let Err(e) = reset::write(&state.reset_file, &list) {
            tracing::error!(email = %email, "could not write the reset file: {e}");
        }

        match verdict {
            reset::Check::Ok => {}
            reset::Check::Wrong => {
                tracing::warn!(email = %email, "wrong reset code");
                return render_reset(
                    "That code is not right.",
                    "",
                    &email,
                    &next,
                    StatusCode::BAD_REQUEST,
                );
            }
            reset::Check::None => {
                // One message for "never asked", "expired", "already used" and
                // "out of attempts". Telling them apart would say whether an
                // address had a code outstanding.
                return render_reset(
                    "That code has expired or has already been used. Ask for a new one.",
                    "",
                    &email,
                    &next,
                    StatusCode::BAD_REQUEST,
                );
            }
        }
    }

    // The code was right. Set the password.
    let mut list = people(&state);
    let Some(idx) = list.iter().position(|p| p.email == email) else {
        // The account was deleted in the 15 minutes since the code was issued.
        // Vanishingly rare, but the alternative is an index panic.
        return render_reset(
            "That account no longer exists.",
            "",
            &email,
            &next,
            StatusCode::BAD_REQUEST,
        );
    };
    list[idx].hash = hash;
    // Not forced to change again: they just chose this password themselves,
    // seconds ago. Making them do it twice reads as the site being broken.
    list[idx].must_change = false;
    if let Err(e) = common::people::write(&state.people_file, &list) {
        tracing::error!(email = %email, "could not write the people file: {e}");
        return render_reset(
            "Could not save that. Try again.",
            "",
            &email,
            &next,
            StatusCode::INTERNAL_SERVER_ERROR,
        );
    }

    // Same reasoning as a voluntary change, and more urgent here: a reset is
    // the flow somebody uses when they think their password is compromised, so
    // every session the old one created has to end.
    revoke_all(&state, &email);
    // And clear the wrong-password lockout - they have proved control of the
    // address, and leaving them locked out after a successful reset is the
    // single most confusing thing this page could do.
    if let Ok(mut gates) = state.gates.lock() {
        gates.remove(&email);
    }
    tracing::info!(email = %email, "password reset with a one-time code");
    // A reset means the mailbox (or the old password) may be the problem, so
    // every passkey for this person goes too, and passkey-only is cleared. They
    // add passkeys again, and the "add a passkey?" page comes back.
    passkeys::wipe_for_reset(&state, &email);
    passkeys::after_password(&state, &email, &next)
}

// -----------------------------------------------------------------------------

pub(crate) fn urlencode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' | b'/' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

fn form_field(body: &str, name: &str) -> Option<String> {
    for pair in body.split('&') {
        let mut it = pair.splitn(2, '=');
        if it.next()? == name {
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
            b'%' if i + 2 < b.len() => match (
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
            },
            c => {
                out.push(c);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn main() -> anyhow::Result<()> {
    common::init_local_offset();
    common::init_tracing("auth");
    common::runtime()?.block_on(run())
}

async fn run() -> anyhow::Result<()> {
    let seed: Vec<String> = common::env_or("AUTH_ALLOWED_EMAILS", "")
        .split(',')
        .map(|s| s.trim().to_lowercase())
        .filter(|s| !s.is_empty())
        .collect();
    if seed.is_empty() && !PathBuf::from(common::env_or("AUTH_PEOPLE_FILE", "/data/people.json")).exists() {
        eprintln!("AUTH_ALLOWED_EMAILS is empty and no people.json exists yet,");
        eprintln!("so nobody could ever sign in.");
        eprintln!("Set it in .env, comma separated:");
        eprintln!("  AUTH_ALLOWED_EMAILS=user1@example.com,user5@example.com,user4@example.com");
        std::process::exit(1);
    }

    let secret = common::env_or("AUTH_SECRET", "");
    if secret.len() < 32 {
        eprintln!("AUTH_SECRET is missing or too short (need 32+ characters).");
        eprintln!("It signs the session cookie - a weak one means anyone can forge a session.");
        eprintln!();
        eprintln!("Generate one:");
        eprintln!("  .\\auth-setup.ps1");
        std::process::exit(1);
    }

    let people_file = PathBuf::from(common::env_or("AUTH_PEOPLE_FILE", "/data/people.json"));

    // A hash of a password nobody has, verified against when the address does
    // not exist. Built once at startup because building it costs the same as
    // checking one, and doing it per request would show up as a timing
    // difference of its own.
    let decoy_hash = common::people::hash_password("this is not anybody's password")
        .unwrap_or_default();

    let state = Arc::new(AppState {
        allowed_seed: seed.clone(),
        people_file: people_file.clone(),
        secret: secret.into_bytes(),
        session_days: common::env_or("AUTH_SESSION_DAYS", "180").parse().unwrap_or(180),
        cookie_secure: common::env_bool("AUTH_COOKIE_SECURE", true),
        revoked_file: PathBuf::from(common::env_or("AUTH_REVOKED_FILE", "/data/revoked.json")),
        reset_file: PathBuf::from(common::env_or("AUTH_RESET_FILE", "/data/resets.json")),
        reset_lock: Mutex::new(()),
        trial_file: PathBuf::from(common::env_or("AUTH_TRIAL_FILE", "/data/trials.json")),
        // tokio's Mutex, not std's, unlike every other lock in this struct:
        // it is held across an .await (the "your trial ran out" email), and a
        // std guard held across a yield point is how an executor deadlocks.
        trial_lock: tokio::sync::Mutex::new(()),
        gates: Mutex::new(HashMap::new()),
        decoy_hash,
        // Keyed off the same directory the other state files live in, so the
        // signing key is persisted by whatever already persists those. A key
        // regenerated on every restart would invalidate tokens mid-flight and
        // present as intermittent, unexplainable sign-in failures.
        bridge: std::sync::Mutex::new(std::collections::HashMap::new()),
        oidc: oidc::Oidc::from_env(
            people_file.parent().unwrap_or_else(|| std::path::Path::new("/data")),
        ),
        passkeys: passkeys::Passkeys::from_env(
            people_file.parent().unwrap_or_else(|| std::path::Path::new("/data")),
        ),
    });
    passkeys::ENABLED.store(state.passkeys.is_some(), std::sync::atomic::Ordering::Relaxed);
    if let Some(p) = state.passkeys.as_ref() {
        let known: Vec<String> = people(&state).iter().map(|p| p.email.clone()).collect();
        p.prune_on_start(&known);
        // The pruning run repeats, so an expired loaner or an ended guest
        // account is cleaned up without waiting for a restart. (They are
        // refused the moment they expire either way.)
        let st = state.clone();
        tokio::spawn(async move {
            let mut tick = tokio::time::interval(Duration::from_secs(900));
            tick.tick().await;
            loop {
                tick.tick().await;
                if let Some(p) = st.passkeys.as_ref() {
                    let known: Vec<String> =
                        people(&st).iter().map(|p| p.email.clone()).collect();
                    p.prune_on_start(&known);
                }
            }
        });
    }

    // SEED ON FIRST RUN ONLY, and seeded accounts have no password. Writing the
    // file here rather than holding the seed in memory means the admin's People
    // page shows the three of them straight away, each plainly marked as unable
    // to sign in until a password is set - instead of an empty page that looks
    // like the setting did not work.
    if !people_file.exists() && !seed.is_empty() {
        let seeded: Vec<common::people::Person> =
            seed.iter().map(|e| common::people::Person::new(e)).collect();
        match common::people::write(&people_file, &seeded) {
            Ok(()) => tracing::info!(
                count = seeded.len(),
                path = %people_file.display(),
                "seeded the people file from AUTH_ALLOWED_EMAILS - none of them can sign in until \
                 a password is set in the admin"
            ),
            Err(e) => tracing::error!("could not write {}: {e}", people_file.display()),
        }
    }

    let list = people(&state);
    let without = list.iter().filter(|p| !p.has_password()).count();
    if without > 0 {
        // Loud, because this is the exact state that looks like a broken login.
        tracing::warn!(
            "{without} of {} people have no password yet and cannot sign in. \
             Set one in the admin: /admin/people",
            list.len()
        );
    }
    tracing::info!(
        people = list.len(),
        session_days = state.session_days,
        "auth ready"
    );

    let app = Router::new()
        .route("/", get(signin_page))
        .route("/signin", post(sign_in))
        .route("/change", get(change_page).post(change_password))
        .route("/forgot", get(forgot_page).post(send_code))
        .route("/reset", get(reset_page).post(use_code))
        .route("/verify", get(verify))
        // --- passkeys (see passkeys.rs) -----------------------------------
        .route("/account", get(passkeys::account_page))
        .route("/passkey/passkey.js", get(passkeys::script))
        .route("/passkey/register/start", post(passkeys::register_start))
        .route("/passkey/register/finish", post(passkeys::register_finish))
        .route("/passkey/remove", post(passkeys::remove))
        .route("/passkey/offer", post(passkeys::offer_choice))
        .route("/owner", get(passkeys::owner_page))
        .route("/owner/begin", post(passkeys::owner_begin))
        .route("/owner/confirm", post(passkeys::owner_confirm))
        .route("/static/{file}", get(passkeys::static_file))
        .route("/passkey/only", post(passkeys::set_only))
        .route("/passkey/lost", post(passkeys::lost))
        .route("/recover", get(passkeys::recover_page).post(passkeys::recover_send))
        .route("/passkey/recover/start", post(passkeys::recover_start))
        .route("/passkey/login/start", post(passkeys::login_start))
        .route("/passkey/login/finish", post(passkeys::login_finish))
        .route("/invite", get(passkeys::invite_page))
        .route("/passkey/invite/start", post(passkeys::invite_start))
        // --- the cookie bridge to our subdomains (see bridge.rs) ----------
        .route("/sso/bridge", get(bridge::bridge))
        .route("/__sso", get(bridge::redeem))
        // --- OpenID Connect ------------------------------------------------
        // These sit under /auth, which Caddy deliberately does NOT put behind
        // forward_auth -- it is where you go to become authenticated, so
        // requiring authentication to reach it would be a loop. That is
        // exactly what these need too: /token and /userinfo are called
        // server-to-server by the application, with no browser and no cookie,
        // and /authorize has to be reachable in order to redirect somebody to
        // the sign-in page in the first place.
        .route("/oidc/.well-known/openid-configuration", get(oidc::discovery))
        .route("/oidc/jwks.json", get(oidc::jwks))
        .route("/oidc/authorize", get(oidc::authorize))
        .route("/oidc/token", post(oidc::token))
        .route("/oidc/userinfo", get(oidc::userinfo).post(oidc::userinfo))
        // The phone apps' way to the assistant. Bearer-token authenticated
        // against the same issued-token map /oidc/userinfo uses, so no new
        // credential exists for a phone to leak. See llm.rs.
        .route(
            "/app/chat",
            // A photo from a phone is megabytes; the default 2MB cap rejects
            // it as a bare 413, which looks like a broken server rather than
            // a too-big picture.
            post(llm::chat).layer(axum::extract::DefaultBodyLimit::max(llm::MAX_CHAT_BODY)),
        )
        .route("/app/models", get(llm::models))
        .route("/app/prompts", get(llm::prompts))
        // THE ONLY UNAUTHENTICATED /app ROUTE. It has to be: whoever calls it
        // has no token yet. An invite code is what stands in for one, and
        // trial.rs says why that is a gate on who was sent the app rather
        // than an authentication check.
        .route("/app/trial/start", post(llm::trial_start))
        .route("/app/trial/status", get(llm::trial_status))
        .route("/app/upload-targets", get(llm::upload_targets))
        .route(
            "/app/upload",
            // Documents are far bigger than photos: a nursing textbook runs
            // to hundreds of megabytes, and the index itself accepts 400MB.
            post(llm::upload).layer(axum::extract::DefaultBodyLimit::max(420 * 1024 * 1024)),
        )
        // Classified and permission-checked by intake, rather than filed to
        // a shelf the caller names. See llm::app_file.
        .route(
            "/app/file",
            post(llm::app_file).layer(axum::extract::DefaultBodyLimit::max(420 * 1024 * 1024)),
        )
        // What the Yes/No buttons in the chat call. The value approved is
        // read from the database rather than the request, and the identity
        // from the token rather than the body -- so a tampered request can
        // change neither what is approved nor who approved it.
        .route("/app/garden/decide", post(llm::garden_decide))
        // A photograph from the garden. The body is an image, so it
        // needs the same room the chat uploads get rather than the
        // default 2 MB, which a phone camera clears on every shot.
        .route(
            "/app/garden/{action}",
            post(llm::garden_camera)
                .layer(axum::extract::DefaultBodyLimit::max(32 * 1024 * 1024)),
        )
        .route("/app/awards", get(llm::awards))
        .route("/app/support", post(llm::support))
        .route("/app/recipes", get(llm::recipes))
        .route("/app/recipe/{id}", get(llm::recipe))
        // Casey's career portal, identity supplied from the token.
        .route("/app/career", post(llm::career))
        // The real assistant: memory + tools, not a bare model.
        .route(
            "/app/ask",
            // Photos arrive here now too, so the same 24MB ceiling applies.
            post(llm::ask).layer(axum::extract::DefaultBodyLimit::max(llm::MAX_CHAT_BODY)),
        )
        .route("/whoami", get(whoami))
        // Internal only: guarded by SERVICE_KEY, see `levels`.
        .route("/levels", get(levels))
        .route("/signout", post(signout))
        .route("/signout-all", post(signout_all))
        .route("/logout", get(logout_page))
        .route("/healthz", get(healthz))
        .with_state(state);

    common::serve(app, common::env_u16("APP_PORT", 8013)).await
}

#[cfg(test)]
mod tests {
    #[test]
    fn safe_next_refuses_backslash_and_control_tricks() {
        use super::safe_next;
        assert_eq!(safe_next("/kitchen/soup?a=b"), "/kitchen/soup?a=b");
        assert_eq!(safe_next("//evil.example"), "/");
        assert_eq!(safe_next("/\\evil.example"), "/");
        assert_eq!(safe_next("/\\\\evil.example"), "/");
        assert_eq!(safe_next("\\evil.example"), "/");
        assert_eq!(safe_next("/a\tb"), "/");
        assert_eq!(safe_next("/\t/evil.example"), "/");
        assert_eq!(safe_next("/\n/evil.example"), "/");
        assert_eq!(safe_next("/a\u{7f}b"), "/");
        assert_eq!(safe_next("https://evil.example/"), "/");
        // a percent-encoded backslash stays a plain path character
        assert_eq!(safe_next("/%5Cevil.example"), "/%5Cevil.example");
        // the subdomain bridge still works
        assert!(safe_next("https://status.home.example.net/x").starts_with("/sso/bridge?next="));
    }

    #[test]
    fn view_only_rules() {
        use super::view_blocks;
        assert!(!view_blocks("recipe", "GET", "/recipe/soup"));
        assert!(view_blocks("recipe", "POST", "/recipe/save"));
        assert!(view_blocks("recipe", "DELETE", "/recipe/1"));
        assert!(!view_blocks("special", "GET", "/special/live"));
        assert!(view_blocks("special", "GET", "/special/export/clip"));
        assert!(view_blocks("special", "GET", "/special/config?x=1"));
        // the query string never decides
        assert!(!view_blocks("special", "GET", "/special/live?goto=/export"));
    }

    use super::*;

    #[test]
    fn app_for_uri_matches_each_apps_own_prefix() {
        assert_eq!(app_for_uri("/mama"), Some("mama"));
        assert_eq!(app_for_uri("/mama/t/8"), Some("mama"));
        assert_eq!(app_for_uri("/kitchen"), Some("kitchen"));
        assert_eq!(app_for_uri("/kitchen/r/some-recipe"), Some("kitchen"));
        assert_eq!(app_for_uri("/casey"), Some("casey"));
        assert_eq!(app_for_uri("/casey/dashboard"), Some("casey"));
        assert_eq!(app_for_uri("/recipe"), Some("recipe"));
        assert_eq!(app_for_uri("/recipe/r/28"), Some("recipe"));
        assert_eq!(app_for_uri("/status"), Some("status"));
        assert_eq!(app_for_uri("/status/dashboard"), Some("status"));
        assert_eq!(app_for_uri("/logs"), Some("logs"));
        assert_eq!(app_for_uri("/logs/nuc-homelab-dozzle-1"), Some("logs"));
        assert_eq!(app_for_uri("/git"), Some("git"));
        assert_eq!(app_for_uri("/git/jason/homelab"), Some("git"));
        assert_eq!(app_for_uri("/portainer"), Some("portainer"));
        assert_eq!(app_for_uri("/portainer/#!/endpoints"), Some("portainer"));
        assert_eq!(app_for_uri("/tools"), Some("tools"));
        assert_eq!(app_for_uri("/tools/json-formatter"), Some("tools"));
        assert_eq!(app_for_uri("/pdf"), Some("pdf"));
        assert_eq!(app_for_uri("/pdf/merge"), Some("pdf"));
    }

    #[test]
    fn three_wrong_passwords_then_five_minutes() {
        // Jason's rule, stated once here so a future edit to lock_for() has to
        // argue with a failing test rather than quietly change the policy.
        assert_eq!(lock_for(0), None);
        assert_eq!(lock_for(1), None);
        assert_eq!(lock_for(2), None, "the third try must still be allowed");
        assert_eq!(lock_for(3), Some(Duration::from_secs(300)),
                   "the fourth wrong password locks for five minutes");
        // Flat, not escalating: waiting one lockout out must not buy a cheaper
        // next round.
        assert_eq!(lock_for(9), Some(Duration::from_secs(300)));
        assert_eq!(lock_for(50), Some(Duration::from_secs(300)));
    }

    #[test]
    fn every_app_on_the_toggle_is_actually_gated() {
        // THE POINT OF DERIVING app_for_uri FROM ALL_APPS. Before, the two
        // were separate lists and an app could have a checkbox on
        // /admin/people while the gate ignored it -- protected-looking and
        // not protected. If this test ever fails, someone has reintroduced
        // that gap.
        for app in common::people::ALL_APPS {
            if *app == "kb" {
                continue; // lives under /mama/kb, covered by its own test
            }
            let root = format!("/{app}");
            let deep = format!("/{app}/something/deeper");
            assert_eq!(app_for_uri(&root), Some(*app), "{root} was not gated");
            assert_eq!(app_for_uri(&deep), Some(*app), "{deep} was not gated");
        }
    }

    #[test]
    fn a_prefix_must_end_on_a_path_boundary() {
        // Naive starts_with() would gate "/statuspage" as "status" and
        // "/gitea-mirror" as "git" -- handing those paths whichever
        // permission the unrelated app happens to have.
        assert_eq!(app_for_uri("/statuspage"), None);
        assert_eq!(app_for_uri("/gitea-mirror"), None);
        assert_eq!(app_for_uri("/toolsmith"), None);
        // and the query string must not decide it either
        assert_eq!(app_for_uri("/git?next=/portainer"), Some("git"));
    }

    #[test]
    fn a_share_link_is_not_on_the_toggle_at_all() {
        // /share/* has no forward_auth in front of it in the Caddyfile, so
        // `verify` is never called for it -- this just documents that even
        // if it somehow were, it would not collide with /recipe's own gate.
        assert_eq!(app_for_uri("/share/abc123"), None);
    }

    #[test]
    fn mama_kb_matches_kb_not_mama() {
        // Same ordering trap as the Caddyfile's own /mama/kb* block: the
        // shorter /mama prefix would swallow this if checked first.
        assert_eq!(app_for_uri("/mama/kb"), Some("kb"));
        assert_eq!(app_for_uri("/mama/kb/a/some-article"), Some("kb"));
    }

    #[test]
    fn pages_outside_the_toggle_are_not_restricted_here() {
        assert_eq!(app_for_uri("/admin/people"), None);
        assert_eq!(app_for_uri("/auth/"), None);
        assert_eq!(app_for_uri("/dee/whatever"), None);
        assert_eq!(app_for_uri("/legacy/old-site"), None);
        assert_eq!(app_for_uri("/"), None);
    }
}
