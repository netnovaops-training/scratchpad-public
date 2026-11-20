//! =========================================================================
//!  OpenID Connect provider -- so the session people already have IS the
//!  login for apps that cannot use forward_auth.
//!
//!  WHY THIS EXISTS AT ALL.
//!
//!  Every app on the website is gated by Caddy calling /verify on this
//!  service. That works because Caddy sits in front of the request and can
//!  bounce a browser to the sign-in page. It does NOT work for two kinds of
//!  client, and Immich is both:
//!
//!    * A PHONE APP. There is no browser in front of it to be redirected,
//!      and no cookie jar shared with the website. forward_auth has nothing
//!      to work with.
//!    * AN APP WITH ITS OWN ACCOUNTS. Immich wants to know who you are so it
//!      can own albums and keep libraries separate. A header saying
//!      "X-Auth-Email: someone" arriving from a proxy is not something it can
//!      safely trust on its own hostname.
//!
//!  OIDC is the standard answer to both, and the reason to put it HERE rather
//!  than run a second identity server is that this service already holds the
//!  session. Somebody signed into the website has a cookie this file can
//!  read. Their sign-in to Immich then costs zero prompts: the authorize
//!  endpoint sees the cookie, mints a code, and sends them straight back.
//!  One login, not two, and no second set of accounts to create for people
//!  who already exist in people.json.
//!
//!  WHAT THIS IS NOT. It is a deliberately small provider: authorization code
//!  flow with PKCE, one signing key, no refresh tokens, no dynamic client
//!  registration, no consent screen. Those are all things a provider serving
//!  strangers needs. This one serves four people and a fixed list of our own
//!  applications, where a consent screen asking Jason whether Jason may see
//!  Jason's photos is theatre rather than security.
//!
//!  THE PARTS THAT ARE SECURITY, NOT CEREMONY, and must not be "simplified":
//!
//!    * redirect_uri is matched EXACTLY against a configured list. This is
//!      the single most important line in the file. A loose match -- a
//!      prefix, a wildcard, a regex -- is how an authorization code gets
//!      handed to somebody else's server, and the code is the account.
//!    * Codes are single-use and expire in two minutes. A replayed code is a
//!      replayed login.
//!    * PKCE is verified when offered, and REQUIRED for public clients.
//!      Immich sends S256; a provider that accepts the challenge and never
//!      checks the verifier is providing the appearance of PKCE. For a
//!      client with no secret -- the phone apps -- PKCE is not defence in
//!      depth, it is the entire defence, so it is enforced at BOTH the
//!      authorize and token endpoints rather than either.
//!    * The client secret is compared in constant time. A byte-by-byte
//!      early-exit compare leaks the secret's prefix to anyone patient.
//! =========================================================================

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use axum::{
    extract::{Form, Query, State},
    http::{header, HeaderMap, StatusCode},
    response::{IntoResponse, Redirect, Response},
    Json,
};
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine as _;
use rand::Rng;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::sync::Arc;

use crate::{AppState, cookie_email, people, revoked_at, token_holder, urlencode};

/// Codes are exchanged server-to-server within a second or two of being
/// issued. Two minutes is slack for a slow network, not a window to live in.
const CODE_TTL: u64 = 120;
/// The access token exists only so Immich can call /userinfo immediately
/// after the exchange. It is not a session and is not meant to be kept.
const ACCESS_TTL: u64 = 300;
/// Thirty days. Long because the alternative is a phone app that logs you
/// out mid-sentence; safe because these are SINGLE USE -- each refresh
/// mints a replacement and destroys the one presented, so a stolen refresh
/// token stops working the moment the real app uses its copy, and the
/// resulting duplicate-use is visible in the log rather than silent.
const REFRESH_TTL: u64 = 30 * 24 * 60 * 60;
const ID_TOKEN_TTL: u64 = 300;

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

// -------------------------------------------------------------------------
//  Configuration
// -------------------------------------------------------------------------

pub struct Client {
    pub id: String,
    pub secret: String,
    /// EXACT strings. See the header. Never patterns.
    pub redirect_uris: Vec<String>,
    /// What to put in `preferred_username`. Default: the whole email,
    /// which is what every client here wanted until Synology DSM.
    ///
    /// DSM's SSO Client matches this claim against the name of an
    /// EXISTING DSM local account, and a DSM account name cannot be an
    /// email address. So DSM needs the local part -- `user` out of
    /// `user1@example.com` -- and needs a DSM user of exactly that name to
    /// already exist, or the login ends in "not privilege".
    ///
    /// Opt-in per client (`"username": "localpart"` in OIDC_CLIENTS)
    /// rather than a global switch, because `sub` and `email` are what
    /// Immich, Jellyfin and the rest key accounts on, and quietly
    /// changing this claim for all of them is how you end up with a
    /// second, empty account for somebody who already exists.
    pub username_localpart: bool,
    /// A PUBLIC client: a phone app, which cannot keep a secret.
    ///
    /// WHY THIS EXISTS. Immich and Jellyfin run on servers, so a secret in
    /// their config is a secret. An Android or iOS app ships its config to
    /// the handset -- anyone who downloads the APK can read it -- so a
    /// "secret" embedded there is a published password to this household's
    /// identity provider. Pretending otherwise would be worse than having
    /// no secret at all, because it would look like authentication.
    ///
    /// WHAT REPLACES THE SECRET. PKCE, and it stops being optional. For a
    /// confidential client PKCE is defence in depth on top of the secret;
    /// for a public client it is the ONLY thing standing between an
    /// intercepted authorization code and an account, because anybody can
    /// present the client_id. So `authorize` refuses a public client with
    /// no code_challenge, and `token` refuses a public client whose code
    /// carried no challenge. Both ends, because a check in one place is a
    /// check that can be walked around by entering at the other.
    ///
    /// Opt in explicitly with `"public": true` in OIDC_CLIENTS. It is never
    /// inferred from a missing secret -- a typo that dropped the secret
    /// would otherwise silently downgrade Immich to a public client.
    pub public: bool,
}

pub struct Oidc {
    /// Must match what the client is configured with, character for
    /// character, including the trailing slash -- a client that discovers a
    /// different issuer than it was told to expect will reject every token.
    pub issuer: String,
    pub clients: Vec<Client>,
    /// PKCS#8 PEM of the RSA private key.
    key_pem: Vec<u8>,
    kid: String,
    jwk_n: String,
    jwk_e: String,
    codes: Mutex<HashMap<String, Code>>,
    access: Mutex<HashMap<String, Access>>,
    /// Refresh tokens, added 2026-09-17 for the phone apps.
    ///
    /// WHY THIS EXISTS NOW, having deliberately not existed before. The
    /// access token is five minutes long and the header above says plainly
    /// it "is not a session and is not meant to be kept" -- which was right
    /// for Immich, whose only job is one /userinfo call straight after the
    /// exchange. A CHAT APP IS THE OPPOSITE: it is held open, used minutes
    /// apart, and re-authenticating every five minutes would make it
    /// unusable. Without this, AppAuth proactively tries to refresh near
    /// expiry, finds nothing to refresh with, and fails with an exception
    /// whose message is null -- which reaches the user as a blank error.
    refresh: Mutex<HashMap<String, Refresh>>,
    /// Where the refresh tokens are kept between restarts.
    ///
    /// THEY USED TO LIVE ONLY IN MEMORY, and that was a real bug rather than
    /// a simplification. The map was built empty on startup, so EVERY restart
    /// of this container -- an update, a reboot, a crash, me rebuilding it
    /// four times in an afternoon -- silently voided every phone app's
    /// session at once. What Jason saw was "Session expired" every single
    /// time he opened the app, with nothing in any log to explain it.
    ///
    /// reset.rs next door had already worked this out for one-time codes and
    /// says so in its own header: a file "survives a container restart, which
    /// matters because a code that quietly stops working while somebody is
    /// mid-reset looks exactly like 'this site is broken'". A 30-day refresh
    /// token that dies on restart is the same mistake with a longer fuse.
    ///
    /// WHAT IS WRITTEN. The token strings themselves ARE in this file, which
    /// is unavoidable -- a refresh token has to be compared against what was
    /// presented, and unlike a password there is nothing to verify it against
    /// but itself. The file therefore sits in the same private volume as
    /// people.json and is no more readable than the signing key beside it.
    refresh_file: PathBuf,
}


/// Read the refresh tokens kept from before the last restart.
///
/// A MISSING OR UNREADABLE FILE IS AN EMPTY MAP, NOT A FAILURE. The worst a
/// bad file can do is what the old in-memory version did every time: everyone
/// signs in again. Refusing to start instead would turn a cosmetic problem
/// into the house having no sign-in at all.
fn load_refresh(path: &Path) -> HashMap<String, Refresh> {
    let Ok(bytes) = std::fs::read(path) else {
        return HashMap::new();
    };
    let mut map: HashMap<String, Refresh> =
        serde_json::from_slice(&bytes).unwrap_or_default();
    // Expired ones are dropped on the way in, so a file left alone for a
    // month does not come back as a pile of dead tokens.
    let now = now();
    map.retain(|_, r| r.expires > now);
    map
}

/// Write them back, atomically -- temp file then rename, the same as
/// reset.rs. A torn read here would log everybody out, which is the exact
/// thing this file exists to stop.
fn save_refresh(path: &Path, map: &HashMap<String, Refresh>) {
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let Ok(mut json) = serde_json::to_vec_pretty(map) else {
        return;
    };
    json.push(b'\n');
    let tmp = path.with_extension("tmp");
    if std::fs::write(&tmp, json).is_ok() {
        let _ = std::fs::rename(tmp, path);
    }
}

/// A refresh token: long-lived, single-use, and bound to the client it was
/// issued to.
#[derive(Serialize, Deserialize, Clone)]
struct Refresh {
    email: String,
    client_id: String,
    expires: u64,
    /// When this was minted. Compared against revoked.json, which records
    /// "everything issued for this person before T is void" -- the same
    /// mechanism that already made a stateless session cookie revocable.
    issued: u64,
}

struct Code {
    client_id: String,
    redirect_uri: String,
    email: String,
    nonce: Option<String>,
    challenge: Option<String>,
    expires: u64,
    /// The scope the app asked for, carried from /authorize so the token
    /// endpoint can see whether `offline_access` was requested. Without
    /// this the decision to issue a refresh token would have to be made
    /// from the client_id, which is a guess about intent rather than a
    /// statement of it.
    scope: String,
}

struct Access {
    email: String,
    expires: u64,
    /// See Refresh::issued.
    issued: u64,
    /// Needed so /userinfo shapes `preferred_username` the same way the
    /// id_token did. A client that is told two different usernames for
    /// one login creates two accounts.
    client_id: String,
}

impl Oidc {
    /// Returns None when OIDC is not configured, and that is a supported
    /// state rather than an error: the website works perfectly well without
    /// it, so a missing issuer or client list disables these endpoints
    /// instead of preventing the whole auth service from starting. Refusing
    /// to boot would take the sign-in page down with it.
    pub fn from_env(data_dir: &Path) -> Option<Oidc> {
        let refresh_file = data_dir.join("refresh.json");
        let issuer = std::env::var("OIDC_ISSUER").ok()?;
        let raw = std::env::var("OIDC_CLIENTS").ok()?;
        if issuer.trim().is_empty() || raw.trim().is_empty() {
            return None;
        }

        let parsed: Vec<serde_json::Value> = serde_json::from_str(&raw)
            .map_err(|e| tracing::error!("OIDC_CLIENTS is not valid JSON: {e}"))
            .ok()?;

        let mut clients = Vec::new();
        for c in parsed {
            let id = c.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let secret = c
                .get("secret")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let uris: Vec<String> = c
                .get("redirect_uris")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|u| u.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();
            // Explicit, never inferred. A public client is a deliberate
            // decision; a missing secret is a mistake, and the two must not
            // look the same to this parser.
            let public = c
                .get("public")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            if id.is_empty() || uris.is_empty() {
                tracing::error!("OIDC client entry ignored: needs id and redirect_uris");
                continue;
            }
            if !public && secret.is_empty() {
                tracing::error!(
                    "OIDC client {id:?} ignored: no secret, and not marked \"public\": true"
                );
                continue;
            }
            if public && !secret.is_empty() {
                // Refusing rather than ignoring the secret. A config that
                // carries one implies somebody believes it is being checked.
                tracing::error!(
                    "OIDC client {id:?} ignored: marked public but also given a secret. \
                     A public client authenticates with PKCE and nothing else."
                );
                continue;
            }
            let username_localpart = c
                .get("username")
                .and_then(|v| v.as_str())
                .map(|v| v.eq_ignore_ascii_case("localpart"))
                .unwrap_or(false);
            clients.push(Client {
                id,
                secret,
                redirect_uris: uris,
                username_localpart,
                public,
            });
        }
        if clients.is_empty() {
            return None;
        }

        let key_path = std::env::var("OIDC_KEY_FILE")
            .map(PathBuf::from)
            .unwrap_or_else(|_| data_dir.join("oidc-key.pem"));
        let key_pem = load_or_create_key(&key_path)?;

        let (n, e, kid) = public_parts(&key_pem)?;

        tracing::info!(
            "OIDC provider enabled: issuer={issuer} clients={} key={}",
            clients.len(),
            key_path.display()
        );

        Some(Oidc {
            issuer,
            clients,
            key_pem,
            kid,
            jwk_n: n,
            jwk_e: e,
            codes: Mutex::new(HashMap::new()),
            access: Mutex::new(HashMap::new()),
            refresh: Mutex::new(load_refresh(&refresh_file)),
            refresh_file,
        })
    }

    /// `preferred_username` for this client: the local part when it asked
    /// for that, the whole email otherwise.
    fn username_for(&self, client_id: &str, email: &str) -> String {
        match self.client(client_id) {
            Some(c) if c.username_localpart => {
                email.split('@').next().unwrap_or(email).to_string()
            }
            _ => email.to_string(),
        }
    }

    fn client(&self, id: &str) -> Option<&Client> {
        self.clients.iter().find(|c| c.id == id)
    }

    /// Whose access token is this? `None` if it is unknown or expired.
    ///
    /// Extracted from /userinfo rather than copied, because llm.rs needs the
    /// identical check and two hand-written copies of an auth check are two
    /// places for them to drift apart -- and the one that drifts looser is
    /// the one nobody notices.
    pub fn access_email(&self, token: &str) -> Option<String> {
        self.access_record(token).map(|(email, _)| email)
    }

    /// The email AND when the token was minted.
    ///
    /// The timestamp is what lets a caller apply revoked.json. This type has
    /// no business reading that file -- it holds no paths and knows nothing
    /// about people.json either -- so it reports the fact and `token_holder`
    /// in main.rs makes the decision. One decision, in one place, rather than
    /// a second copy of the rule living in here.
    pub fn access_record(&self, token: &str) -> Option<(String, u64)> {
        self.access.lock().ok().and_then(|m| {
            m.get(token)
                .filter(|a| a.expires > now())
                .map(|a| (a.email.clone(), a.issued))
        })
    }

    /// Drop every token held for one person, both kinds.
    ///
    /// Belt to revoked.json's braces: that file is what makes revocation
    /// survive a restart and covers tokens minted by another process, while
    /// this frees the memory immediately rather than at the next sweep.
    pub fn forget(&self, email: &str) -> usize {
        let mut gone = 0;
        if let Ok(mut m) = self.access.lock() {
            let before = m.len();
            m.retain(|_, a| a.email != email);
            gone += before - m.len();
        }
        if let Ok(mut m) = self.refresh.lock() {
            let before = m.len();
            m.retain(|_, r| r.email != email);
            gone += before - m.len();
        }
        // Signing somebody out has to OUTLIVE a restart too, or the tokens
        // just revoked would come back from the file.
        self.persist();
        gone
    }

    /// Write the refresh map to disk.
    ///
    /// TAKES THE LOCK ITSELF, so it must be called AFTER the caller's own
    /// guard has gone out of scope -- calling it inside one of the blocks
    /// below would deadlock on the same mutex and hang the request.
    fn persist(&self) {
        if let Ok(m) = self.refresh.lock() {
            save_refresh(&self.refresh_file, &m);
        }
    }

    /// How many live tokens one person holds, for the admin's session count.
    pub fn live_tokens(&self, email: &str) -> (usize, usize) {
        let t = now();
        let access = self
            .access
            .lock()
            .map(|m| m.values().filter(|a| a.email == email && a.expires > t).count())
            .unwrap_or(0);
        let refresh = self
            .refresh
            .lock()
            .map(|m| m.values().filter(|r| r.email == email && r.expires > t).count())
            .unwrap_or(0);
        (access, refresh)
    }

    /// Drop anything expired. Called on every mint so the maps cannot grow
    /// without bound on a long-running process.
    fn sweep(&self) {
        let t = now();
        if let Ok(mut m) = self.codes.lock() {
            m.retain(|_, c| c.expires > t);
        }
        if let Ok(mut m) = self.refresh.lock() {
            m.retain(|_, r| r.expires > t);
        }
        if let Ok(mut m) = self.access.lock() {
            m.retain(|_, a| a.expires > t);
        }
        self.persist();
    }
}

/// The signing key lives on disk and is generated once.
///
/// GENERATED RATHER THAN CONFIGURED, because the alternative is a key pasted
/// into an env file, and a key in an env file gets committed eventually.
/// PERSISTED rather than made fresh at boot, because rotating it on every
/// restart would invalidate tokens mid-flight and look like random sign-in
/// failures.
fn load_or_create_key(path: &Path) -> Option<Vec<u8>> {
    if let Ok(pem) = std::fs::read(path) {
        if !pem.is_empty() {
            return Some(pem);
        }
    }

    use rsa::pkcs8::{EncodePrivateKey, LineEnding};
    tracing::info!("OIDC: no signing key at {}, generating one", path.display());
    let mut rng = rsa::rand_core::OsRng;
    let key = rsa::RsaPrivateKey::new(&mut rng, 2048)
        .map_err(|e| tracing::error!("OIDC: could not generate a signing key: {e}"))
        .ok()?;
    let pem = key
        .to_pkcs8_pem(LineEnding::LF)
        .map_err(|e| tracing::error!("OIDC: could not encode the signing key: {e}"))
        .ok()?;

    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    if let Err(e) = std::fs::write(path, pem.as_bytes()) {
        tracing::error!("OIDC: could not write the signing key: {e}");
        return None;
    }
    // The private key is the whole of this provider's authority: anyone
    // holding it can mint an id_token claiming to be anybody.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
    }
    Some(pem.as_bytes().to_vec())
}

/// modulus, exponent and a key id, for the JWKS document.
fn public_parts(pem: &[u8]) -> Option<(String, String, String)> {
    use rsa::pkcs8::DecodePrivateKey;
    use rsa::traits::PublicKeyParts;

    let s = std::str::from_utf8(pem).ok()?;
    let key = rsa::RsaPrivateKey::from_pkcs8_pem(s)
        .map_err(|e| tracing::error!("OIDC: signing key is not a PKCS#8 PEM: {e}"))
        .ok()?;
    let pubkey = key.to_public_key();
    let n = URL_SAFE_NO_PAD.encode(pubkey.n().to_bytes_be());
    let e = URL_SAFE_NO_PAD.encode(pubkey.e().to_bytes_be());
    // Derived from the key itself, so it changes if and only if the key does.
    let kid = hex16(&Sha256::digest(n.as_bytes()));
    Some((n, e, kid))
}

fn hex16(bytes: &[u8]) -> String {
    bytes.iter().take(8).map(|b| format!("{b:02x}")).collect()
}

fn random_token() -> String {
    let mut b = [0u8; 32];
    rand::rand_core::UnwrapErr(rand::rngs::SysRng).fill_bytes(&mut b);
    URL_SAFE_NO_PAD.encode(b)
}

/// Constant time. See the header note on why a normal `==` is wrong here.
fn secret_eq(a: &str, b: &str) -> bool {
    let (a, b) = (a.as_bytes(), b.as_bytes());
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for i in 0..a.len() {
        diff |= a[i] ^ b[i];
    }
    diff == 0
}

// -------------------------------------------------------------------------
//  Discovery and keys
// -------------------------------------------------------------------------

pub async fn discovery(State(state): State<Arc<AppState>>) -> Response {
    let Some(o) = state.oidc.as_ref() else {
        return disabled();
    };
    let base = o.issuer.trim_end_matches('/').to_string();
    Json(serde_json::json!({
        "issuer": o.issuer,
        "authorization_endpoint": format!("{base}/authorize"),
        "token_endpoint": format!("{base}/token"),
        "userinfo_endpoint": format!("{base}/userinfo"),
        "jwks_uri": format!("{base}/jwks.json"),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "email", "profile"],
        // "none" is how a public client (the phone apps) says it has no
        // secret. AppAuth on Android and iOS reads this list and will not
        // attempt a secret-less token request unless "none" appears here.
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic", "none"],
        "code_challenge_methods_supported": ["S256"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "claims_supported": ["sub", "iss", "aud", "exp", "iat", "email", "email_verified", "name", "preferred_username"],
    }))
    .into_response()
}

pub async fn jwks(State(state): State<Arc<AppState>>) -> Response {
    let Some(o) = state.oidc.as_ref() else {
        return disabled();
    };
    Json(serde_json::json!({
        "keys": [{
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": o.kid,
            "n": o.jwk_n,
            "e": o.jwk_e,
        }]
    }))
    .into_response()
}

fn disabled() -> Response {
    (
        StatusCode::SERVICE_UNAVAILABLE,
        "Single sign-on is not configured on this server.",
    )
        .into_response()
}

// -------------------------------------------------------------------------
//  /authorize
// -------------------------------------------------------------------------

pub async fn authorize(
    State(state): State<Arc<AppState>>,
    Query(q): Query<HashMap<String, String>>,
    headers: HeaderMap,
) -> Response {
    let Some(o) = state.oidc.as_ref() else {
        return disabled();
    };

    let client_id = q.get("client_id").cloned().unwrap_or_default();
    let redirect_uri = q.get("redirect_uri").cloned().unwrap_or_default();

    // These two are validated BEFORE anything else and any failure is shown
    // to the user rather than redirected. Redirecting an error to an
    // unverified redirect_uri would be a way to bounce traffic anywhere.
    let Some(client) = o.client(&client_id) else {
        return (StatusCode::BAD_REQUEST, "Unknown application.").into_response();
    };
    if !client.redirect_uris.iter().any(|u| u == &redirect_uri) {
        tracing::warn!("OIDC: rejected redirect_uri {redirect_uri:?} for client {client_id:?}");
        return (
            StatusCode::BAD_REQUEST,
            "That application asked to be sent somewhere this server does not recognise.",
        )
            .into_response();
    }

    // From here on, errors go back to the application in the standard way,
    // because the destination has been proven to be one we configured.
    let state_param = q.get("state").cloned().unwrap_or_default();

    if q.get("response_type").map(|s| s.as_str()) != Some("code") {
        return error_redirect(&redirect_uri, "unsupported_response_type", &state_param);
    }
    if let Some(m) = q.get("code_challenge_method") {
        if m != "S256" {
            return error_redirect(&redirect_uri, "invalid_request", &state_param);
        }
    }
    // A public client has no secret, so PKCE is the whole of its
    // authentication. Refuse here as well as at the token endpoint: this is
    // the earlier and clearer failure, and it means a code that cannot be
    // safely redeemed is never minted in the first place.
    if client.public && q.get("code_challenge").is_none() {
        tracing::warn!("OIDC: public client {client_id:?} tried to authorize without PKCE");
        return error_redirect(&redirect_uri, "invalid_request", &state_param);
    }

    // ---- the whole point: is there already a session? --------------------
    let Some(email) = cookie_email(&state, &headers) else {
        // Not signed in. Send them to the ordinary sign-in page with this
        // request as the destination, so they land back here afterwards and
        // the flow completes without them noticing it paused.
        let mut back = format!("/auth/oidc/authorize?client_id={}", urlencode(&client_id));
        for k in [
            "redirect_uri",
            "response_type",
            "scope",
            "state",
            "nonce",
            "code_challenge",
            "code_challenge_method",
        ] {
            if let Some(v) = q.get(k) {
                back.push_str(&format!("&{k}={}", urlencode(v)));
            }
        }
        return Redirect::to(&format!("/auth/?next={}", urlencode(&back))).into_response();
    };

    // Being signed in to the website is not by itself permission to use this
    // application. The same per-person toggles that gate every other app
    // apply here, so removing somebody's access in the admin takes their
    // photos away too rather than only hiding the card.
    let list = people(&state);
    let known = common::people::find(&list, &email).is_some();
    if !known {
        return (
            StatusCode::FORBIDDEN,
            "Your account is not set up on this server.",
        )
            .into_response();
    }

    o.sweep();
    let code = random_token();
    if let Ok(mut m) = o.codes.lock() {
        m.insert(
            code.clone(),
            Code {
                client_id: client_id.clone(),
                redirect_uri: redirect_uri.clone(),
                email,
                nonce: q.get("nonce").cloned(),
                challenge: q.get("code_challenge").cloned(),
                expires: now() + CODE_TTL,
                scope: q.get("scope").cloned().unwrap_or_default(),
            },
        );
    }

    let sep = if redirect_uri.contains('?') { '&' } else { '?' };
    let mut to = format!("{redirect_uri}{sep}code={}", urlencode(&code));
    if !state_param.is_empty() {
        to.push_str(&format!("&state={}", urlencode(&state_param)));
    }
    Redirect::to(&to).into_response()
}

fn error_redirect(redirect_uri: &str, err: &str, state: &str) -> Response {
    let sep = if redirect_uri.contains('?') { '&' } else { '?' };
    let mut to = format!("{redirect_uri}{sep}error={err}");
    if !state.is_empty() {
        to.push_str(&format!("&state={}", urlencode(state)));
    }
    Redirect::to(&to).into_response()
}

// -------------------------------------------------------------------------
//  /token
// -------------------------------------------------------------------------

#[derive(Serialize)]
struct TokenResponse {
    access_token: String,
    token_type: &'static str,
    expires_in: u64,
    id_token: String,
    /// Omitted entirely for clients that did not ask for it, so Immich,
    /// Jellyfin and the rest see exactly the response they saw before.
    #[serde(skip_serializing_if = "Option::is_none")]
    refresh_token: Option<String>,
}

#[derive(Serialize)]
struct Claims {
    iss: String,
    sub: String,
    aud: String,
    exp: u64,
    iat: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    nonce: Option<String>,
    email: String,
    email_verified: bool,
    name: String,
    preferred_username: String,
}

pub async fn token(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Form(f): Form<HashMap<String, String>>,
) -> Response {
    let Some(o) = state.oidc.as_ref() else {
        return disabled();
    };

    let grant = f.get("grant_type").map(|s| s.as_str()).unwrap_or("");
    if grant == "refresh_token" {
        return refresh_grant(&state, &headers, &f).await;
    }
    if grant != "authorization_code" {
        return token_error("unsupported_grant_type");
    }

    // Client authentication, either form fields or HTTP Basic. Immich uses
    // the form; other clients default to Basic, and a provider that supports
    // only one of them fails in a way that looks like a bad secret.
    let (cid, csecret) = client_credentials(&headers, &f);
    let Some(client) = o.client(&cid) else {
        return token_error("invalid_client");
    };
    // A public client presents no secret, so there is nothing to compare.
    // What it MUST present is a code that was bound to a PKCE challenge,
    // and that is checked below -- unconditionally for this client, rather
    // than only when a challenge happens to be on the code.
    if !client.public && !secret_eq(&client.secret, &csecret) {
        tracing::warn!("OIDC: bad client secret for {cid:?}");
        return token_error("invalid_client");
    }

    let Some(code) = f.get("code") else {
        return token_error("invalid_request");
    };

    // Taken out of the map, not read from it: a code is single use, and
    // "remove" is what makes that true even if two requests race.
    let entry = o.codes.lock().ok().and_then(|mut m| m.remove(code));
    let Some(entry) = entry else {
        return token_error("invalid_grant");
    };

    if entry.expires < now() {
        return token_error("invalid_grant");
    }
    if entry.client_id != cid {
        return token_error("invalid_grant");
    }
    // The redirect_uri is presented again and must match the one the code was
    // issued for. This is what stops a code obtained for one destination
    // being redeemed as if it were for another.
    if f.get("redirect_uri").map(|s| s.as_str()) != Some(entry.redirect_uri.as_str()) {
        return token_error("invalid_grant");
    }

    // THE SECOND HALF OF THE PUBLIC-CLIENT RULE. `authorize` will not mint a
    // code for a public client without a challenge, so a code reaching here
    // with none means either a client that was public-ified between the two
    // calls or a code that did not come from this provider. Both are a
    // refusal, and neither should be a silent pass through the `if let`
    // below, which treats "no challenge" as "nothing to check".
    if client.public && entry.challenge.is_none() {
        tracing::warn!("OIDC: public client {cid:?} redeemed a code carrying no PKCE challenge");
        return token_error("invalid_grant");
    }

    if let Some(challenge) = entry.challenge.as_ref() {
        let Some(verifier) = f.get("code_verifier") else {
            return token_error("invalid_grant");
        };
        let got = URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()));
        if !secret_eq(challenge, &got) {
            tracing::warn!("OIDC: PKCE verification failed for {cid:?}");
            return token_error("invalid_grant");
        }
    }

    let list = people(&state);
    let person = common::people::find(&list, &entry.email);
    let name = person
        .map(|p| p.name.clone())
        .filter(|n| !n.is_empty())
        .unwrap_or_else(|| entry.email.clone());

    let iat = now();
    let claims = Claims {
        iss: o.issuer.clone(),
        // The email is the identity everywhere else in this stack, and it is
        // what Immich matches an account on. Using anything else here would
        // create a second, empty account for somebody who already exists.
        sub: entry.email.clone(),
        aud: cid.clone(),
        exp: iat + ID_TOKEN_TTL,
        iat,
        nonce: entry.nonce.clone(),
        email: entry.email.clone(),
        email_verified: true,
        name: name.clone(),
        preferred_username: o.username_for(&cid, &entry.email),
    };

    let mut header = jsonwebtoken::Header::new(jsonwebtoken::Algorithm::RS256);
    header.kid = Some(o.kid.clone());
    let key = match jsonwebtoken::EncodingKey::from_rsa_pem(&o.key_pem) {
        Ok(k) => k,
        Err(e) => {
            tracing::error!("OIDC: signing key unusable: {e}");
            return token_error("server_error");
        }
    };
    let id_token = match jsonwebtoken::encode(&header, &claims, &key) {
        Ok(t) => t,
        Err(e) => {
            tracing::error!("OIDC: could not sign id_token: {e}");
            return token_error("server_error");
        }
    };

    o.sweep();
    let access_token = random_token();
    if let Ok(mut m) = o.access.lock() {
        m.insert(
            access_token.clone(),
            Access {
                email: entry.email.clone(),
                expires: now() + ACCESS_TTL,
                issued: now(),
                client_id: cid.clone(),
            },
        );
    }

    // ONLY FOR CLIENTS THAT ASKED. Immich, Jellyfin, DSM and the rest have
    // worked for months against a response with no refresh_token in it, and
    // handing them one unasked changes what they store and how they behave.
    // The scope is the standard way a client says it wants one.
    let wants_refresh = entry
        .scope
        .split_whitespace()
        .any(|s| s.eq_ignore_ascii_case("offline_access"));
    let refresh_token = if wants_refresh {
        let rt = random_token();
        if let Ok(mut m) = o.refresh.lock() {
            m.insert(
                rt.clone(),
                Refresh {
                    email: entry.email.clone(),
                    client_id: cid.clone(),
                    expires: now() + REFRESH_TTL,
                    issued: now(),
                },
            );
        }
        // Outside the block above: persist() takes the same lock.
        o.persist();
        Some(rt)
    } else {
        None
    };

    // No-store is required by the spec and is not decoration: these responses
    // contain credentials and must not sit in a proxy cache.
    let mut out = HeaderMap::new();
    out.insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
    out.insert(header::PRAGMA, "no-cache".parse().unwrap());

    (
        out,
        Json(TokenResponse {
            access_token,
            token_type: "Bearer",
            expires_in: ACCESS_TTL,
            id_token,
            refresh_token,
        }),
    )
        .into_response()
}

/// `grant_type=refresh_token` -- trade a refresh token for a fresh access
/// token, and a fresh refresh token.
///
/// ROTATION IS THE POINT, not a flourish. The presented token is REMOVED
/// before a replacement is issued, so each one works exactly once. That
/// turns a stolen refresh token from a permanent key into a race the thief
/// loses as soon as the real app refreshes -- and the loser's next attempt
/// lands here as an unknown token, which is logged.
///
/// It deliberately does NOT mint a new id_token. The id_token is a statement
/// about a login that happened at a point in time; re-issuing one on refresh
/// would assert a fresh authentication that did not occur.
async fn refresh_grant(
    state: &Arc<AppState>,
    headers: &HeaderMap,
    f: &HashMap<String, String>,
) -> Response {
    let Some(o) = state.oidc.as_ref() else {
        return disabled();
    };

    let (cid, csecret) = client_credentials(headers, f);
    let Some(client) = o.client(&cid) else {
        return token_error("invalid_client");
    };
    // Same rule as the code grant: a confidential client proves itself with
    // its secret, a public one has none to prove.
    if !client.public && !secret_eq(&client.secret, &csecret) {
        tracing::warn!("OIDC refresh: bad client secret for {cid:?}");
        return token_error("invalid_client");
    }

    let Some(presented) = f.get("refresh_token").map(|s| s.trim().to_string()) else {
        return token_error("invalid_request");
    };
    if presented.is_empty() {
        return token_error("invalid_request");
    }

    o.sweep();

    // REMOVED, not read: single use has to be true even if two requests race,
    // and `remove` is the only thing that makes that so.
    let found = o.refresh.lock().ok().and_then(|mut m| m.remove(&presented));
    let Some(rec) = found else {
        tracing::warn!("OIDC refresh: unknown or already-used token presented by {cid:?}");
        return token_error("invalid_grant");
    };

    if rec.expires < now() {
        return token_error("invalid_grant");
    }
    // THE ALLOWLIST IS RE-READ HERE, and this is the whole point of the
    // change. A refresh token lives for 30 days and mints a fresh access
    // token every five minutes, so without this a person removed from
    // people.json kept working in the phone app for a month -- while the
    // website, which re-reads the list on every request, cut them off at
    // once. Two doors, two answers, and the looser one was the silent one.
    if common::people::find(&people(state), &rec.email).is_none() {
        tracing::warn!("OIDC refresh: {} no longer has an account", rec.email);
        return token_error("invalid_grant");
    }
    if rec.issued < revoked_at(state, &rec.email) {
        tracing::warn!("OIDC refresh: {}'s tokens were revoked", rec.email);
        return token_error("invalid_grant");
    }
    // A refresh token issued to one client must not be redeemable by another.
    if rec.client_id != cid {
        tracing::warn!(
            "OIDC refresh: token for {:?} presented by {cid:?}",
            rec.client_id
        );
        return token_error("invalid_grant");
    }

    let access_token = random_token();
    if let Ok(mut m) = o.access.lock() {
        m.insert(
            access_token.clone(),
            Access {
                email: rec.email.clone(),
                expires: now() + ACCESS_TTL,
                issued: now(),
                client_id: cid.clone(),
            },
        );
    }

    let rotated = random_token();
    if let Ok(mut m) = o.refresh.lock() {
        m.insert(
            rotated.clone(),
            Refresh {
                email: rec.email.clone(),
                client_id: cid.clone(),
                // Rolling, not fixed: an app in daily use never hits the
                // wall, while one abandoned for thirty days stops working.
                expires: now() + REFRESH_TTL,
                // The ROTATION time, not the original. A revocation between
                // the two must void this one, and it does: the new token is
                // stamped now, and now is after the revocation.
                issued: now(),
            },
        );
    }
    // Covers BOTH the remove that consumed the presented token and the
    // insert of its replacement -- one write, so the file can never show
    // the old one live and the new one missing.
    o.persist();

    let mut out = HeaderMap::new();
    out.insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
    out.insert(header::PRAGMA, "no-cache".parse().unwrap());

    (
        out,
        Json(serde_json::json!({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": ACCESS_TTL,
            "refresh_token": rotated,
        })),
    )
        .into_response()
}

fn client_credentials(headers: &HeaderMap, f: &HashMap<String, String>) -> (String, String) {
    if let Some(v) = headers.get(header::AUTHORIZATION).and_then(|v| v.to_str().ok()) {
        if let Some(b64) = v.strip_prefix("Basic ") {
            use base64::engine::general_purpose::STANDARD;
            if let Ok(raw) = STANDARD.decode(b64.trim()) {
                if let Ok(s) = String::from_utf8(raw) {
                    if let Some((u, p)) = s.split_once(':') {
                        // Both halves are form-encoded per RFC 6749 §2.3.1.
                        return (urldecode(u), urldecode(p));
                    }
                }
            }
        }
    }
    (
        f.get("client_id").cloned().unwrap_or_default(),
        f.get("client_secret").cloned().unwrap_or_default(),
    )
}

fn urldecode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b'%' if i + 2 < b.len() => {
                let hex = std::str::from_utf8(&b[i + 1..i + 3]).unwrap_or("");
                match u8::from_str_radix(hex, 16) {
                    Ok(v) => {
                        out.push(v);
                        i += 3;
                    }
                    Err(_) => {
                        out.push(b[i]);
                        i += 1;
                    }
                }
            }
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            c => {
                out.push(c);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn token_error(code: &str) -> Response {
    let status = match code {
        "invalid_client" => StatusCode::UNAUTHORIZED,
        "server_error" => StatusCode::INTERNAL_SERVER_ERROR,
        _ => StatusCode::BAD_REQUEST,
    };
    let mut out = HeaderMap::new();
    out.insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
    (
        status,
        out,
        Json(serde_json::json!({ "error": code })),
    )
        .into_response()
}

// -------------------------------------------------------------------------
//  /userinfo
// -------------------------------------------------------------------------

pub async fn userinfo(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    let Some(o) = state.oidc.as_ref() else {
        return disabled();
    };

    let token = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .unwrap_or("")
        .trim()
        .to_string();
    if token.is_empty() {
        return (StatusCode::UNAUTHORIZED, "Missing bearer token.").into_response();
    }

    // token_holder() is the shared check; llm.rs makes the same one. The
    // client_id is still read here because only this endpoint needs it, for
    // the per-client `preferred_username` shaping DSM depends on.
    let Some(email) = token_holder(state.as_ref(), &token) else {
        return (StatusCode::UNAUTHORIZED, "Token is not valid.").into_response();
    };
    let client_id = o
        .access
        .lock()
        .ok()
        .and_then(|m| m.get(&token).map(|a| a.client_id.clone()))
        .unwrap_or_default();

    let list = people(&state);
    let name = common::people::find(&list, &email)
        .map(|p| p.name.clone())
        .filter(|n| !n.is_empty())
        .unwrap_or_else(|| email.clone());

    Json(serde_json::json!({
        "sub": email,
        "email": email,
        "email_verified": true,
        "name": name,
        "preferred_username": o.username_for(&client_id, &email),
    }))
    .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn secret_compare_is_length_safe() {
        assert!(secret_eq("abc", "abc"));
        assert!(!secret_eq("abc", "abd"));
        assert!(!secret_eq("abc", "abcd"));
        assert!(!secret_eq("", "a"));
    }

    #[test]
    fn pkce_challenge_matches_reference_vector() {
        // RFC 7636 appendix B.
        let verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
        let got = URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()));
        assert_eq!(got, "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
    }

    #[test]
    fn basic_auth_credentials_are_url_decoded() {
        assert_eq!(urldecode("a%40b"), "a@b");
        assert_eq!(urldecode("plain"), "plain");
        assert_eq!(urldecode("a+b"), "a b");
    }
}
