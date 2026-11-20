//! Google Drive, through a small Google Apps Script web app in Jason's own account
//! (apps-script/Code.gs). No Google Cloud project, OAuth client or billing: the script runs as
//! him with the drive.file scope (only files it made), and every call carries a shared secret.
//!
//! One Google Doc per ticket in a "Tickets" folder. A ticket goes up as Markdown and Drive turns
//! it into a real Doc; "Pull from Doc" exports the Doc back as Markdown for the page to read.

use serde::Deserialize;
use serde_json::{json, Value};
use tokio::sync::Mutex;

#[derive(Deserialize, Debug, Clone)]
#[serde(rename_all = "camelCase")]
pub struct Meta {
    pub id: String,
    #[serde(default)]
    pub modified_time: String,
    #[serde(default)]
    pub web_view_link: String,
}

pub struct Drive {
    url: String,
    secret: String,
    folder_url: Mutex<Option<String>>,
}

impl Drive {
    pub fn new(cfg: &crate::api::Config) -> Self {
        Self { url: cfg.apps_script_url.clone(), secret: cfg.apps_script_secret.clone(), folder_url: Mutex::new(None) }
    }

    pub fn configured(&self) -> bool {
        self.url.starts_with("https://script.google.com/") && self.secret.len() >= 32
    }

    /// One call to the script. Apps Script answers a POST with a redirect to the result, which
    /// reqwest follows as a GET (the way Google expects).
    async fn call(&self, http: &reqwest::Client, action: &str, mut body: Value) -> Result<Value, String> {
        if !self.configured() {
            return Err("Google Drive isn't set up yet (Settings).".into());
        }
        body["action"] = json!(action);
        body["secret"] = json!(self.secret);
        let r = http
            .post(&self.url)
            .json(&body)
            .send()
            .await
            .map_err(|e| format!("Couldn't reach the Drive script: {e}"))?;
        let status = r.status();
        let text = r.text().await.map_err(|e| e.to_string())?;
        let v: Value = serde_json::from_str(&text).map_err(|_| {
            if status.is_success() {
                "The Drive script sent back a web page, not data. Check it is deployed as a Web app with access \"Anyone\".".to_string()
            } else {
                format!("The Drive script answered {status}.")
            }
        })?;
        match v["error"].as_str() {
            Some("bad secret") => Err("The Drive script refused the secret. APPS_SCRIPT_SECRET in tickets.env must match the one setup() made.".into()),
            Some(e) => Err(format!("Google Drive: {e}")),
            None => Ok(v),
        }
    }

    pub async fn folder_url(&self, http: &reqwest::Client) -> Option<String> {
        if let Some(u) = self.folder_url.lock().await.clone() {
            return Some(u);
        }
        let u = self.call(http, "folder", json!({})).await.ok()?["url"].as_str()?.to_string();
        *self.folder_url.lock().await = Some(u.clone());
        Some(u)
    }

    /// The Doc's details, or None when it was deleted or is in the trash.
    pub async fn meta(&self, http: &reqwest::Client, id: &str) -> Result<Option<Meta>, String> {
        let v = self.call(http, "meta", json!({ "id": id })).await?;
        if v["missing"].as_bool() == Some(true) {
            return Ok(None);
        }
        serde_json::from_value(v).map(Some).map_err(|e| e.to_string())
    }

    /// Replaces the Doc's text (or makes a new Doc in the Tickets folder) from Markdown.
    pub async fn upload(&self, http: &reqwest::Client, existing: Option<&str>, name: &str, markdown: &str) -> Result<Meta, String> {
        let v = self.call(http, "upload", json!({ "id": existing, "title": name, "markdown": markdown })).await?;
        serde_json::from_value(v).map_err(|e| e.to_string())
    }

    pub async fn export_markdown(&self, http: &reqwest::Client, id: &str) -> Result<String, String> {
        let v = self.call(http, "export", json!({ "id": id })).await?;
        v["markdown"].as_str().map(String::from).ok_or_else(|| "The Drive script sent no text.".into())
    }
}
