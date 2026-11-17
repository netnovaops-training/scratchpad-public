//! =========================================================================
//!  The cookie bridge -- one household sign-in, even across domains.
//!
//!  THE PROBLEM THIS SOLVES. The session cookie belongs to
//!  example.org, and browsers will never send it to a
//!  *.home.example.net hostname -- different registrable domain,
//!  end of story. Until now that meant every duckdns subdomain either ran
//!  its own login (Kuma, Immich) or none (the tribute, the Studio). Jason
//!  wants the one login everywhere, so the auth service now walks the
//!  cookie across the gap itself:
//!
//!    1. A request hits a bridged subdomain with no cookie. Caddy's
//!       forward_auth asks /verify, which sees the foreign host and
//!       redirects the browser to the MAIN site's sign-in with the full
//!       absolute destination as ?next=.
//!    2. Sign-in happens where the cookie lives (or is skipped, if one is
//!       already there). safe_next() routes an absolute trusted next
//!       through /sso/bridge instead of redirecting off-site blind.
//!    3. /sso/bridge (cookie present, by construction) mints a SINGLE-USE,
//!       60-second code bound to the destination host, and redirects to
//!       https://<host>/__sso?code=...&next=<path>.
//!    4. The subdomain routes /__sso back to this service, which redeems
//!       the code and sets the SAME session cookie -- host-scoped to the
//!       subdomain this time -- then lands the visitor where they were
//!       going. From then on /verify works there like anywhere else.
//!
//!  WHAT KEEPS IT FROM BEING AN OPEN DOOR:
//!    * Only hosts matching  <label>.home.example.net  (one label,
//!      [a-z0-9-]) are bridgeable. Everything else is a plain "/" next.
//!    * Codes are removed on redemption -- single use even under a race --
//!      and die after sixty seconds.
//!    * The code carries the exact host it was minted for; redeeming it is
//!      only possible through that host's own /__sso route, and the cookie
//!      it sets is host-only, so one subdomain's session says nothing
//!      about another's.
//! =========================================================================

use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use axum::{
    extract::{Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Redirect, Response},
};
use rand::Rng;
use std::sync::Arc;

use crate::{cookie_email, issue_session, urlencode, AppState};

const CODE_TTL: u64 = 60;
const DOMAIN_SUFFIX: &str = ".home.example.net";

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

pub struct BridgeCode {
    pub email: String,
    pub host: String,
    pub expires: u64,
}

pub type BridgeStore = Mutex<HashMap<String, BridgeCode>>;

/// Is this absolute URL one of ours to bridge to? Returns (host, path+query)
/// if so. Deliberately strict: https only, exactly one subdomain label of
/// [a-z0-9-], nothing clever.
pub fn trusted_absolute(next: &str) -> Option<(String, String)> {
    let rest = next.strip_prefix("https://")?;
    let (host, path) = match rest.find('/') {
        Some(i) => (&rest[..i], &rest[i..]),
        None => (rest, "/"),
    };
    let label = host.strip_suffix(DOMAIN_SUFFIX)?;
    if label.is_empty()
        || !label
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
    {
        return None;
    }
    // A path that tried to smuggle another authority ("//evil") is refused
    // the same way safe_next refuses it.
    if path.starts_with("//") {
        return None;
    }
    Some((host.to_string(), path.to_string()))
}

fn random_code() -> String {
    let mut b = [0u8; 24];
    rand::rand_core::UnwrapErr(rand::rngs::SysRng).fill_bytes(&mut b);
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// GET /sso/bridge?next=https://<sub>.home.example.net/...
/// Runs on the MAIN host, where the cookie lives.
pub async fn bridge(
    State(state): State<Arc<AppState>>,
    Query(q): Query<HashMap<String, String>>,
    headers: HeaderMap,
) -> Response {
    let next = q.get("next").cloned().unwrap_or_default();
    let Some((host, path)) = trusted_absolute(&next) else {
        return (StatusCode::BAD_REQUEST, "that is not one of our doors").into_response();
    };
    let Some(email) = cookie_email(&state, &headers) else {
        // Not signed in yet: through the front door first, then back here.
        return Redirect::to(&format!(
            "/auth/?next={}",
            urlencode(&format!("/sso/bridge?next={}", urlencode(&next)))
        ))
        .into_response();
    };

    let code = random_code();
    if let Ok(mut m) = state.bridge.lock() {
        let t = now();
        m.retain(|_, c| c.expires > t); // sweep the dead while we are here
        m.insert(
            code.clone(),
            BridgeCode {
                email,
                host: host.clone(),
                expires: t + CODE_TTL,
            },
        );
    }
    Redirect::to(&format!(
        "https://{host}/__sso?code={code}&next={}",
        urlencode(&path)
    ))
    .into_response()
}

/// GET /__sso?code=...&next=/path
/// Arrives THROUGH THE SUBDOMAIN (Caddy routes its /__sso here), which is
/// what lets the Set-Cookie land on that host.
pub async fn redeem(
    State(state): State<Arc<AppState>>,
    Query(q): Query<HashMap<String, String>>,
    headers: HeaderMap,
) -> Response {
    let code = q.get("code").cloned().unwrap_or_default();
    let next = q.get("next").cloned().unwrap_or_else(|| "/".into());
    let next = if next.starts_with('/') && !next.starts_with("//") {
        next
    } else {
        "/".into()
    };

    // Which host is redeeming? Caddy passes the real one.
    let host = headers
        .get("X-Forwarded-Host")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string();

    let entry = state.bridge.lock().ok().and_then(|mut m| m.remove(&code));
    let Some(entry) = entry else {
        return expired();
    };
    if entry.expires < now() || entry.host != host {
        return expired();
    }
    // Same cookie, same signing, same revocation story as the main site --
    // issue_session sets no Domain attribute, so the browser scopes it to
    // exactly this subdomain.
    issue_session(&state, &entry.email, &next)
}

fn expired() -> Response {
    (
        StatusCode::UNAUTHORIZED,
        "That sign-in link expired (they only live a minute). Go back and try again.",
    )
        .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn trust_is_narrow() {
        assert!(trusted_absolute("https://status.home.example.net/").is_some());
        assert!(trusted_absolute("https://status.home.example.net/dash?x=1").is_some());
        assert!(trusted_absolute("https://evil.example/").is_none());
        assert!(trusted_absolute("http://status.home.example.net/").is_none());
        assert!(trusted_absolute("https://a.b.home.example.net/").is_none());
        assert!(trusted_absolute("https://home.example.net/").is_none());
        assert!(trusted_absolute("https://statusevil.example/?home.example.net").is_none());
        assert!(trusted_absolute("https://status.home.example.net//evil").is_none());
    }

    #[test]
    fn host_and_path_split() {
        let (h, p) = trusted_absolute("https://status.home.example.net/dashboard?a=b").unwrap();
        assert_eq!(h, "status.home.example.net");
        assert_eq!(p, "/dashboard?a=b");
    }
}
