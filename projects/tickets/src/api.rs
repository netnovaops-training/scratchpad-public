//! HTTP routes. Everything except /api/health needs the X-Auth-Email header that Caddy's
//! forward_auth sets after the household SSO lets someone in.
//!
//!   GET  /                          the page
//!   GET  /api/status                who you are, GitHub and Drive set-up
//!   GET  /api/tickets               all tickets
//!   POST /api/tickets               new ticket {kind, ...} -> numbered ticket
//!   GET|PUT|DELETE /api/tickets/{id}
//!   POST /api/tickets/{id}/publish  {markdown, title, force} -> GitHub commit + Google Doc
//!   POST /api/tickets/{id}/pull     -> the Google Doc as Markdown
//!   GET  /api/apps-script/{file}    the Drive script's code, for Settings' Copy buttons

use crate::{drive::Drive, github::GitHub, store, store::Store};
use axum::{
    extract::{DefaultBodyLimit, Path, Request, State},
    http::{header, StatusCode},
    middleware::{self, Next},
    response::{Html, IntoResponse, Response},
    routing::{get, post},
    Extension, Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use std::net::IpAddr;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

const PAGE: &str = include_str!("../static/index.html");
const SCRIPT_CODE: &str = include_str!("../apps-script/Code.gs");
const SCRIPT_MANIFEST: &str = include_str!("../apps-script/appsscript.json");

pub struct Config {
    pub data_dir: PathBuf,
    pub port: u16,
    pub bind: IpAddr,
    pub first_number: u64,
    /// The repos a ticket can be saved to (the page's dropdown); the first is the default.
    pub github: Vec<GitHub>,
    pub apps_script_url: String,
    pub apps_script_secret: String,
}

impl Config {
    pub fn from_env() -> Self {
        let var = |k: &str, d: &str| std::env::var(k).ok().filter(|v| !v.trim().is_empty()).unwrap_or_else(|| d.to_string());
        let token = var("GITHUB_TOKEN", "");
        Self {
            data_dir: var("DATA_DIR", "data").into(),
            port: var("PORT", "8050").parse().unwrap_or(8050),
            bind: var("BIND", "0.0.0.0").parse().unwrap_or([0, 0, 0, 0].into()),
            first_number: var("FIRST_NUMBER", "11").parse().unwrap_or(11),
            // GITHUB_REPOS="owner/a,owner/b" (GITHUB_REPO still works for a single repo)
            github: if token.is_empty() {
                Vec::new()
            } else {
                var("GITHUB_REPOS", &var("GITHUB_REPO", "netnovaops-training/Toolbox"))
                    .split(',')
                    .map(str::trim)
                    .filter(|r| r.split('/').count() == 2)
                    .map(|repo| GitHub {
                        token: token.clone(),
                        repo: repo.to_string(),
                        branch: var("GITHUB_BRANCH", "tickets"),
                        dir: var("GITHUB_DIR", "Tickets"),
                    })
                    .collect()
            },
            apps_script_url: var("APPS_SCRIPT_URL", ""),
            apps_script_secret: var("APPS_SCRIPT_SECRET", ""),
        }
    }
}

pub struct AppState {
    pub cfg: Config,
    store: Store,
    drive: Drive,
    http: reqwest::Client,
}

impl AppState {
    pub fn new(cfg: Config, store: Store, drive: Drive) -> Self {
        // Apps Script can take a few seconds to wake up and convert a Doc
        let http = reqwest::Client::builder().timeout(Duration::from_secs(60)).build().expect("http client");
        Self { cfg, store, drive, http }
    }
}

type S = Arc<AppState>;

/// The signed-in person, from Caddy.
#[derive(Clone)]
struct Who(String);

pub fn router(state: S) -> Router {
    let signed_in = Router::new()
        .route("/", get(page))
        .route("/api/status", get(status))
        .route("/api/tickets", get(list).post(create))
        .route("/api/tickets/{id}", get(one).put(save).delete(remove))
        .route("/api/tickets/{id}/publish", post(publish))
        .route("/api/tickets/{id}/pull", post(pull))
        .route("/api/apps-script/{file}", get(script_file))
        .route_layer(middleware::from_fn(require_sso));
    Router::new()
        .route("/api/health", get(|| async { "ok" }))
        .merge(signed_in)
        .layer(DefaultBodyLimit::max(512 * 1024))
        .with_state(state)
}

async fn require_sso(mut req: Request, next: Next) -> Response {
    let email = req
        .headers()
        .get("x-auth-email")
        .and_then(|v| v.to_str().ok())
        .map(str::trim)
        .filter(|v| !v.is_empty() && v.len() < 200)
        .map(String::from);
    match email {
        Some(e) => {
            req.extensions_mut().insert(Who(e));
            next.run(req).await
        }
        None => fail(StatusCode::UNAUTHORIZED, "Sign in first."),
    }
}

fn fail(code: StatusCode, msg: &str) -> Response {
    (code, Json(json!({ "error": msg }))).into_response()
}

fn io_fail(e: std::io::Error) -> Response {
    tracing::error!("storage error: {e}");
    fail(StatusCode::INTERNAL_SERVER_ERROR, "Couldn't save on the server. Try again in a moment.")
}

async fn page() -> Response {
    ([(header::CACHE_CONTROL, "no-cache")], Html(PAGE)).into_response()
}

async fn status(State(s): State<S>, Extension(Who(me)): Extension<Who>) -> Json<Value> {
    Json(json!({
        "me": me,
        "github": {
            "configured": !s.cfg.github.is_empty(),
            "repos": s.cfg.github.iter().map(|g| json!({
                "repo": g.repo, "branch": g.branch, "folderUrl": g.folder_url(),
            })).collect::<Vec<_>>(),
        },
        "drive": {
            "configured": s.drive.configured(),
            // asking the script for its folder also proves the URL and secret work
            "folderUrl": if s.drive.configured() { s.drive.folder_url(&s.http).await } else { None },
        },
    }))
}

async fn script_file(Path(file): Path<String>) -> Response {
    let body = match file.as_str() {
        "Code.gs" => SCRIPT_CODE,
        "appsscript.json" => SCRIPT_MANIFEST,
        _ => return fail(StatusCode::NOT_FOUND, "No such file."),
    };
    ([(header::CONTENT_TYPE, "text/plain; charset=utf-8")], body).into_response()
}

async fn list(State(s): State<S>) -> Response {
    match s.store.list().await {
        Ok(mut v) => {
            v.sort_by(|a, b| b["num"].as_u64().cmp(&a["num"].as_u64()));
            Json(json!({ "tickets": v })).into_response()
        }
        Err(e) => io_fail(e),
    }
}

async fn one(State(s): State<S>, Path(id): Path<String>) -> Response {
    match s.store.get(&id).await {
        Some(t) => Json(t).into_response(),
        None => fail(StatusCode::NOT_FOUND, "No such ticket."),
    }
}

fn body_object(v: Value) -> Option<Map<String, Value>> {
    match v {
        Value::Object(m) => Some(m),
        _ => None,
    }
}

fn not_an_object() -> Response {
    fail(StatusCode::BAD_REQUEST, "Send the ticket as a JSON object.")
}

async fn create(State(s): State<S>, Extension(Who(me)): Extension<Who>, Json(v): Json<Value>) -> Response {
    let kind = v["kind"].as_str().unwrap_or_default().to_string();
    if !store::KINDS.contains(&kind.as_str()) {
        return fail(StatusCode::BAD_REQUEST, "kind must be fix, note or project.");
    }
    let Some(body) = body_object(v) else { return not_an_object() };
    match s.store.create(&kind, body, &me).await {
        Ok(t) => (StatusCode::CREATED, Json(t)).into_response(),
        Err(e) => io_fail(e),
    }
}

async fn save(State(s): State<S>, Extension(Who(me)): Extension<Who>, Path(id): Path<String>, Json(v): Json<Value>) -> Response {
    let Some(body) = body_object(v) else { return not_an_object() };
    match s.store.save(&id, body, &me).await {
        Ok(Some(t)) => Json(t).into_response(),
        Ok(None) => fail(StatusCode::NOT_FOUND, "No such ticket."),
        Err(e) => io_fail(e),
    }
}

async fn remove(State(s): State<S>, Path(id): Path<String>) -> Response {
    match s.store.delete(&id).await {
        Ok(true) => StatusCode::NO_CONTENT.into_response(),
        Ok(false) => fail(StatusCode::NOT_FOUND, "No such ticket."),
        Err(e) => io_fail(e),
    }
}

#[derive(Deserialize)]
struct PublishReq {
    markdown: String,
    title: String,
    #[serde(default)]
    force: bool,
    /// "owner/name" from the page's dropdown; empty = the first configured repo
    #[serde(default)]
    repo: String,
}

/// Saves the Markdown to GitHub and Google Drive. Each side reports on its own, so one being
/// down never stops the other.
async fn publish(State(s): State<S>, Path(id): Path<String>, Json(req): Json<PublishReq>) -> Response {
    let Some(t) = s.store.get(&id).await else { return fail(StatusCode::NOT_FOUND, "No such ticket.") };
    if req.markdown.len() > 200_000 {
        return fail(StatusCode::PAYLOAD_TOO_LARGE, "That ticket is too long to save as one file.");
    }
    let title: String = req.title.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(150).collect();
    let mut fields = Map::new();

    let target = if req.repo.is_empty() {
        s.cfg.github.first()
    } else {
        s.cfg.github.iter().find(|g| g.repo == req.repo)
    };
    let github = match target {
        None if s.cfg.github.is_empty() => json!({ "skipped": true, "error": "GitHub isn't set up yet (GITHUB_TOKEN in tickets.env)." }),
        None => json!({ "ok": false, "error": format!("{} isn't one of the repos Tickets may save to.", req.repo) }),
        Some(gh) => match gh.put_file(&s.http, &format!("{id}.md"), &req.markdown, &format!("Tickets: {title}")).await {
            Ok(url) => {
                fields.insert("githubUrl".into(), url.clone().into());
                fields.insert("githubRepo".into(), gh.repo.clone().into());
                fields.insert("githubAt".into(), store::now().into());
                json!({ "ok": true, "url": url, "repo": gh.repo })
            }
            Err(e) => json!({ "ok": false, "error": e }),
        },
    };

    let drive = if !s.drive.configured() {
        json!({ "skipped": true, "error": "Google Drive isn't set up yet (Settings)." })
    } else {
        drive_publish(&s, &t, &title, &req, &mut fields).await
    };

    let ticket = if fields.is_empty() { Some(t) } else { s.store.set_fields(&id, fields).await.ok().flatten() };
    Json(json!({ "github": github, "drive": drive, "ticket": ticket })).into_response()
}

async fn drive_publish(s: &AppState, t: &Value, title: &str, req: &PublishReq, fields: &mut Map<String, Value>) -> Value {
    let mut existing = t["driveId"].as_str().map(String::from);
    if let Some(doc) = &existing {
        match s.drive.meta(&s.http, doc).await {
            Err(e) => return json!({ "ok": false, "error": e }),
            Ok(None) => existing = None, // deleted or trashed: make a fresh Doc
            Ok(Some(m)) => {
                let synced = t["driveSyncedAt"].as_str().unwrap_or_default();
                if !req.force && !synced.is_empty() && m.modified_time != synced {
                    return json!({ "ok": false, "conflict": true, "url": m.web_view_link,
                        "error": "The Doc was edited after the last save. Pull from Doc first, or overwrite it." });
                }
            }
        }
    }
    match s.drive.upload(&s.http, existing.as_deref(), title, &req.markdown).await {
        Ok(m) => {
            fields.insert("driveId".into(), m.id.clone().into());
            fields.insert("driveUrl".into(), m.web_view_link.clone().into());
            fields.insert("driveAt".into(), store::now().into());
            fields.insert("driveSyncedAt".into(), m.modified_time.into());
            json!({ "ok": true, "url": m.web_view_link })
        }
        Err(e) => json!({ "ok": false, "error": e }),
    }
}

async fn pull(State(s): State<S>, Path(id): Path<String>) -> Response {
    let Some(t) = s.store.get(&id).await else { return fail(StatusCode::NOT_FOUND, "No such ticket.") };
    let Some(doc) = t["driveId"].as_str() else {
        return fail(StatusCode::CONFLICT, "This ticket has no Google Doc yet. Save to GitHub + Drive first.");
    };
    let meta = match s.drive.meta(&s.http, doc).await {
        Ok(Some(m)) => m,
        Ok(None) => return fail(StatusCode::GONE, "The Google Doc was deleted or is in the trash."),
        Err(e) => return fail(StatusCode::BAD_GATEWAY, &e),
    };
    let markdown = match s.drive.export_markdown(&s.http, doc).await {
        Ok(m) => m,
        Err(e) => return fail(StatusCode::BAD_GATEWAY, &e),
    };
    let mut fields = Map::new();
    fields.insert("driveSyncedAt".into(), meta.modified_time.into());
    let ticket = s.store.set_fields(&id, fields).await.ok().flatten();
    Json(json!({ "markdown": markdown, "ticket": ticket })).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use http_body_util::BodyExt;
    use tower::ServiceExt;

    fn app() -> Router {
        let dir = std::env::temp_dir().join(format!("tickets-api-{}", rand::random::<u64>()));
        let cfg = Config {
            data_dir: dir.clone(),
            port: 0,
            bind: [127, 0, 0, 1].into(),
            first_number: 11,
            github: Vec::new(),
            apps_script_url: String::new(),
            apps_script_secret: String::new(),
        };
        let store = Store::open(&dir, 11).unwrap();
        let drive = Drive::new(&cfg);
        router(Arc::new(AppState::new(cfg, store, drive)))
    }

    fn req(method: &str, uri: &str, who: Option<&str>, body: Option<Value>) -> axum::http::Request<Body> {
        let mut b = axum::http::Request::builder().method(method).uri(uri);
        if let Some(w) = who {
            b = b.header("x-auth-email", w);
        }
        match body {
            Some(v) => b.header("content-type", "application/json").body(Body::from(v.to_string())).unwrap(),
            None => b.body(Body::empty()).unwrap(),
        }
    }

    async fn json_of(r: Response) -> Value {
        serde_json::from_slice(&r.into_body().collect().await.unwrap().to_bytes()).unwrap_or_default()
    }

    #[tokio::test]
    async fn everything_but_health_needs_sso() {
        let app = app();
        assert_eq!(app.clone().oneshot(req("GET", "/api/health", None, None)).await.unwrap().status(), 200);
        for uri in ["/", "/api/status", "/api/tickets", "/api/apps-script/Code.gs"] {
            assert_eq!(app.clone().oneshot(req("GET", uri, None, None)).await.unwrap().status(), 401, "{uri}");
        }
        assert_eq!(app.oneshot(req("GET", "/", Some("a@b"), None)).await.unwrap().status(), 200);
    }

    #[tokio::test]
    async fn create_save_and_publish_without_targets() {
        let app = app();
        let r = app.clone().oneshot(req("POST", "/api/tickets", Some("a@b"), Some(json!({"kind": "fix", "title": "x"})))).await.unwrap();
        assert_eq!(r.status(), 201);
        let t = json_of(r).await;
        assert_eq!(t["id"], "T-011");
        let r = app.clone().oneshot(req("PUT", "/api/tickets/T-011", Some("c@d"), Some(json!({"title": "y"})))).await.unwrap();
        assert_eq!(json_of(r).await["updatedBy"], "c@d");
        let bad = app.clone().oneshot(req("POST", "/api/tickets", Some("a@b"), Some(json!({"kind": "nope"})))).await.unwrap();
        assert_eq!(bad.status(), 400);
        let r = app
            .clone()
            .oneshot(req("POST", "/api/tickets/T-011/publish", Some("a@b"), Some(json!({"markdown": "# x", "title": "x"}))))
            .await
            .unwrap();
        let p = json_of(r).await;
        assert_eq!(p["github"]["skipped"], true);
        assert_eq!(p["drive"]["skipped"], true);
        let r = app.oneshot(req("POST", "/api/tickets/T-011/pull", Some("a@b"), None)).await.unwrap();
        assert_eq!(r.status(), 409, "no Doc yet");
    }
}
