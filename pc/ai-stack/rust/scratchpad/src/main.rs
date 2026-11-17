//! scratchpad: Jason's code scratchpad. Browse, search, read the documentation of, and share his
//! scripts, from one page. Built 2026-11-12 (step 3 of that day's cleanup), in Rust as he asked.
//!
//! WHO SEES WHAT
//!   Everything except /shared/* is OWNER ONLY. The NUC's Caddy puts the household sign-in in front
//!   (forward_auth, owner check) and this app checks X-Auth-Email against SCRATCHPAD_OWNER again,
//!   failing closed when it is unset. /shared/<token> is public on purpose: one script, read-only,
//!   for 30 days, only if `share::blockers` found nothing private in it.
//!
//! WHAT IT READS
//!   SCRATCHPAD_ROOT (/src, read-only mount): `nuc/...` folders on the NUC itself, and `pc/...`, the
//!   PC's script folders copied over by ai-stack/scripts/sync_scratchpad.py. Garden for All / Robin
//!   material is skipped (scan.rs). SCRATCHPAD_DATA (/data) holds shares.json only.
//!
//! CSRF: the two POST routes need the header `x-scratchpad: 1`. A page on another site cannot add
//! a custom header to a cross-site request without a CORS preflight, and this app answers none.

mod page;
mod scan;
mod share;

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, RwLock};
use std::time::{Duration, Instant};

use axum::{
    extract::{Path as AxPath, Query, State},
    http::{header, HeaderMap, StatusCode},
    response::{Html, IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde_json::json;

use scan::Script;
use share::Share;

struct AppState {
    root: PathBuf,
    data: PathBuf,
    owner: String,
    base: String,       // URL prefix the pages live under, e.g. /scratchpad
    public_url: String, // full URL for share links, e.g. https://example.org/scratchpad
    scripts: RwLock<(Instant, Arc<Vec<Script>>)>,
    shares: Mutex<Vec<Share>>,
}

type St = State<Arc<AppState>>;
const RESCAN_AFTER: Duration = Duration::from_secs(120);

fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

impl AppState {
    fn scripts(&self, force: bool) -> Arc<Vec<Script>> {
        {
            let g = self.scripts.read().unwrap();
            if !force && g.0.elapsed() < RESCAN_AFTER && !g.1.is_empty() {
                return g.1.clone();
            }
        }
        let fresh = Arc::new(scan::scan(&self.root));
        *self.scripts.write().unwrap() = (Instant::now(), fresh.clone());
        fresh
    }

    fn is_owner(&self, h: &HeaderMap) -> bool {
        let who = h.get("x-auth-email").and_then(|v| v.to_str().ok()).unwrap_or("");
        !self.owner.is_empty() && who.eq_ignore_ascii_case(&self.owner)
    }
}

fn denied() -> Response {
    (StatusCode::FORBIDDEN, "Owner only.").into_response()
}

fn not_found() -> Response {
    (StatusCode::NOT_FOUND, Html(page::layout("Not found", "<h1>Not found</h1><p class=muted>This link does not exist, has ended or was revoked.</p>"))).into_response()
}

async fn index(State(st): St, h: HeaderMap, Query(q): Query<HashMap<String, String>>) -> Response {
    if !st.is_owner(&h) {
        return denied();
    }
    let all = st.scripts(q.contains_key("rescan"));
    let query = q.get("q").map(|s| s.trim().to_string()).unwrap_or_default();
    let src = q.get("src").cloned().unwrap_or_default();
    let terms: Vec<String> = query.to_lowercase().split_whitespace().map(String::from).collect();
    let mut sources: Vec<(String, usize)> = Vec::new();
    for s in all.iter() {
        let key = s.id.splitn(3, '/').take(2).collect::<Vec<_>>().join("/");
        match sources.iter_mut().find(|(k, _)| *k == key) {
            Some(e) => e.1 += 1,
            None => sources.push((key, 1)),
        }
    }
    let hits: Vec<&Script> = all
        .iter()
        .filter(|s| src.is_empty() || s.id.starts_with(&format!("{src}/")))
        .filter(|s| {
            if terms.is_empty() {
                return true;
            }
            let hay = format!("{}\n{}\n{}\n{}", s.id, s.doc, s.notes.as_deref().unwrap_or(""), s.text).to_lowercase();
            terms.iter().all(|t| hay.contains(t))
        })
        .take(400)
        .collect();
    let n_shares = st.shares.lock().unwrap().iter().filter(|s| s.expires > now()).count();
    Html(page::index(&st.base, &hits, all.len(), &sources, &query, &src, n_shares)).into_response()
}

fn find(st: &AppState, id: &str) -> Option<Script> {
    st.scripts(false).iter().find(|s| s.id == id).cloned()
}

async fn view(State(st): St, h: HeaderMap, Query(q): Query<HashMap<String, String>>) -> Response {
    if !st.is_owner(&h) {
        return denied();
    }
    match q.get("id").and_then(|id| find(&st, id)) {
        Some(s) => Html(page::view(&st.base, &s, &share::blockers(&s.text))).into_response(),
        None => not_found(),
    }
}

fn raw_response(s: &Script) -> Response {
    let name = s.id.rsplit('/').next().unwrap_or("script.txt").replace('"', "");
    (
        [
            (header::CONTENT_TYPE, "text/plain; charset=utf-8".to_string()),
            (header::CONTENT_DISPOSITION, format!("inline; filename=\"{name}\"")),
            (header::X_CONTENT_TYPE_OPTIONS, "nosniff".to_string()),
        ],
        s.text.clone(),
    )
        .into_response()
}

async fn raw(State(st): St, h: HeaderMap, Query(q): Query<HashMap<String, String>>) -> Response {
    if !st.is_owner(&h) {
        return denied();
    }
    match q.get("id").and_then(|id| find(&st, id)) {
        Some(s) => raw_response(&s),
        None => not_found(),
    }
}

fn csrf_ok(h: &HeaderMap) -> bool {
    h.get("x-scratchpad").and_then(|v| v.to_str().ok()) == Some("1")
}

async fn make_share(State(st): St, h: HeaderMap, Json(body): Json<serde_json::Value>) -> Response {
    if !st.is_owner(&h) || !csrf_ok(&h) {
        return denied();
    }
    let id = body.get("id").and_then(|v| v.as_str()).unwrap_or("");
    let Some(s) = find(&st, id) else { return not_found() };
    let why = share::blockers(&s.text);
    if !why.is_empty() {
        return (StatusCode::UNPROCESSABLE_ENTITY, Json(json!({"error": format!("Not shareable: it contains {}.", why.join(", "))}))).into_response();
    }
    let mut list = st.shares.lock().unwrap();
    list.retain(|x| x.expires > now());
    // One live link per script: sharing again hands back the same link.
    if let Some(x) = list.iter().find(|x| x.id == s.id) {
        return Json(json!({"url": format!("{}/shared/{}", st.public_url, x.token)})).into_response();
    }
    let t = share::token();
    list.push(Share { token: t.clone(), id: s.id.clone(), created: now(), expires: now() + share::DAYS * 86400 });
    if let Err(e) = share::save(&st.data, &list) {
        tracing::error!("could not save shares: {e}");
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"error": "Could not save the link."}))).into_response();
    }
    tracing::info!(id = %s.id, "shared");
    Json(json!({"url": format!("{}/shared/{t}", st.public_url)})).into_response()
}

async fn unshare(State(st): St, h: HeaderMap, Json(body): Json<serde_json::Value>) -> Response {
    if !st.is_owner(&h) || !csrf_ok(&h) {
        return denied();
    }
    let t = body.get("token").and_then(|v| v.as_str()).unwrap_or("");
    let mut list = st.shares.lock().unwrap();
    list.retain(|x| x.token != t);
    let _ = share::save(&st.data, &list);
    Json(json!({"ok": true})).into_response()
}

async fn shares_page(State(st): St, h: HeaderMap) -> Response {
    if !st.is_owner(&h) {
        return denied();
    }
    let list = st.shares.lock().unwrap().clone();
    let rows: Vec<(Share, bool)> = list
        .into_iter()
        .filter(|s| s.expires > now())
        .map(|s| {
            let live = find(&st, &s.id).map(|x| share::blockers(&x.text).is_empty()).unwrap_or(false);
            (s, live)
        })
        .collect();
    Html(page::shares(&st.base, &rows)).into_response()
}

/// The public side: re-checked on every open, so a script that changed and now holds something
/// private stops being served even though its link exists.
fn shared_script(st: &AppState, token: &str) -> Option<(Script, Share)> {
    let sh = st.shares.lock().unwrap().iter().find(|s| s.token == token && s.expires > now()).cloned()?;
    let s = find(st, &sh.id)?;
    share::blockers(&s.text).is_empty().then_some((s, sh))
}

async fn shared(State(st): St, AxPath(token): AxPath<String>) -> Response {
    match shared_script(&st, &token) {
        Some((s, sh)) => Html(page::shared(&st.base, &s, &sh)).into_response(),
        None => not_found(),
    }
}

async fn shared_raw(State(st): St, AxPath(token): AxPath<String>) -> Response {
    match shared_script(&st, &token) {
        Some((s, _)) => raw_response(&s),
        None => not_found(),
    }
}

async fn healthz() -> &'static str {
    "ok"
}

fn main() -> anyhow::Result<()> {
    common::init_local_offset();
    common::init_tracing("scratchpad");
    common::runtime()?.block_on(run())
}

async fn run() -> anyhow::Result<()> {
    let data = PathBuf::from(common::env_or("SCRATCHPAD_DATA", "/data"));
    let base = common::env_or("SCRATCHPAD_PREFIX", "/scratchpad").trim_end_matches('/').to_string();
    let state = Arc::new(AppState {
        root: PathBuf::from(common::env_or("SCRATCHPAD_ROOT", "/src")),
        shares: Mutex::new(share::load(&data)),
        data,
        owner: common::env_or("SCRATCHPAD_OWNER", "").trim().to_lowercase(),
        public_url: common::env_or("SCRATCHPAD_PUBLIC_URL", "https://example.org/scratchpad")
            .trim_end_matches('/').to_string(),
        base,
        scripts: RwLock::new((Instant::now(), Arc::new(Vec::new()))),
    });
    if state.owner.is_empty() {
        tracing::warn!("SCRATCHPAD_OWNER is not set: every owner page answers 403");
    }
    let n = state.scripts(true).len();
    tracing::info!(scripts = n, root = %state.root.display(), "scratchpad ready");

    let app = Router::new()
        .route("/", get(index))
        .route("/view", get(view))
        .route("/raw", get(raw))
        .route("/shares", get(shares_page))
        .route("/api/share", post(make_share))
        .route("/api/unshare", post(unshare))
        .route("/shared/{token}", get(shared))
        .route("/shared/{token}/raw", get(shared_raw))
        .route("/healthz", get(healthz))
        .with_state(state);
    common::serve(app, common::env_u16("APP_PORT", 8014)).await
}
