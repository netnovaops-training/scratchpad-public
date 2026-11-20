//! Tickets: the household ticket editor.
//!
//! Serves one page (static/index.html) and a small JSON API under it. Tickets are kept on the
//! NUC as one JSON file each; "Save to GitHub + Drive" writes a Markdown copy to a GitHub repo
//! (Contents API) and a Google Doc (Drive API), and "Pull from Doc" reads the Doc back.
//!
//! Runs behind Caddy at https://example.org/tickets/ with the household SSO in
//! front (forward_auth). Caddy passes the signed-in address as X-Auth-Email; every route
//! except /api/health refuses a request without it. No new open ports: 127.0.0.1 only.
//!
//! Env (see tickets.env.example): DATA_DIR, PORT, BIND, FIRST_NUMBER, GITHUB_TOKEN, GITHUB_REPO,
//! GITHUB_BRANCH, GITHUB_DIR, APPS_SCRIPT_URL, APPS_SCRIPT_SECRET.

mod api;
mod drive;
mod github;
mod store;

use std::net::SocketAddr;
use std::sync::Arc;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(std::env::var("RUST_LOG").unwrap_or_else(|_| "tickets_server=info".into()))
        .init();
    let cfg = api::Config::from_env();
    let store = store::Store::open(&cfg.data_dir, cfg.first_number).expect("open data directory");
    let drive = drive::Drive::new(&cfg);
    let state = Arc::new(api::AppState::new(cfg, store, drive));
    let addr = SocketAddr::new(state.cfg.bind, state.cfg.port);
    let app = api::router(state);

    tracing::info!("tickets-server listening on {addr}");
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await
        .expect("serve");
}
