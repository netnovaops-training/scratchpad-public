//! Writes a ticket's Markdown to a GitHub repo through the Contents API: one commit per save,
//! skipped when the file on GitHub already has exactly that text.
//!
//! The token is a fine-grained personal access token limited to one repo with
//! "Contents: Read and write". It lives only in tickets.env on the NUC.

use base64::{engine::general_purpose::STANDARD as B64, Engine};
use reqwest::header::{HeaderMap, HeaderValue, ACCEPT, AUTHORIZATION, USER_AGENT};
use serde_json::{json, Value};

pub struct GitHub {
    pub token: String,
    pub repo: String,
    pub branch: String,
    pub dir: String,
}

impl GitHub {
    fn headers(&self) -> HeaderMap {
        let mut h = HeaderMap::new();
        if let Ok(v) = HeaderValue::from_str(&format!("Bearer {}", self.token)) {
            h.insert(AUTHORIZATION, v);
        }
        h.insert(ACCEPT, HeaderValue::from_static("application/vnd.github+json"));
        h.insert("X-GitHub-Api-Version", HeaderValue::from_static("2022-11-28"));
        h.insert(USER_AGENT, HeaderValue::from_static("tickets-nuc"));
        h
    }

    pub fn folder_url(&self) -> String {
        format!("https://github.com/{}/tree/{}/{}", self.repo, self.branch, self.dir)
    }

    /// Creates or updates `<dir>/<name>`; returns the file's page on github.com.
    pub async fn put_file(&self, http: &reqwest::Client, name: &str, content: &str, message: &str) -> Result<String, String> {
        let path = format!("{}/{}", self.dir.trim_matches('/'), name);
        let url = format!("https://api.github.com/repos/{}/contents/{}", self.repo, path);
        let page = format!("https://github.com/{}/blob/{}/{}", self.repo, self.branch, path);

        let found = http
            .get(&url)
            .query(&[("ref", &self.branch)])
            .headers(self.headers())
            .send()
            .await
            .map_err(|e| format!("Couldn't reach GitHub: {e}"))?;
        let sha = match found.status().as_u16() {
            200 => {
                let v: Value = found.json().await.unwrap_or_default();
                // GitHub wraps base64 at 60 characters; same text means nothing to commit
                let old = v["content"].as_str().unwrap_or_default().replace(['\n', '\r'], "");
                if B64.decode(old).ok().as_deref() == Some(content.as_bytes()) {
                    return Ok(page);
                }
                v["sha"].as_str().map(String::from)
            }
            404 => None,
            401 => return Err("GitHub refused the token (401). Make a new token and put it in tickets.env.".into()),
            s => return Err(format!("GitHub said {s} when looking up {path}.")),
        };

        let mut body = json!({ "message": message, "content": B64.encode(content), "branch": self.branch });
        if let Some(s) = sha {
            body["sha"] = json!(s);
        }
        let r = http
            .put(&url)
            .headers(self.headers())
            .json(&body)
            .send()
            .await
            .map_err(|e| format!("Couldn't reach GitHub: {e}"))?;
        let status = r.status();
        let v: Value = r.json().await.unwrap_or_default();
        if !status.is_success() {
            return Err(format!("GitHub said {status}: {}", v["message"].as_str().unwrap_or("no reason given")));
        }
        Ok(v["content"]["html_url"].as_str().map(String::from).unwrap_or(page))
    }
}
