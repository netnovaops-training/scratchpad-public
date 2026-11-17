//! Client for the LiteLLM router -- the Rust twin of `shared/router.py`.
//!
//! Nothing here ever names a model. You ask for a **job** (`fast`, `chat`,
//! `coding`, `reasoning`, `phone`) and `litellm/config.yaml` decides what
//! answers it. Change one line there and every app -- Python or Rust, browser
//! or phone -- follows, with no rebuild.
//!
//! Timeouts are long on purpose. A local 8B model on a 4070 taking 30 seconds
//! over a hard question is normal behaviour, not a hang, and a 30-second client
//! timeout turns "slow answer" into "mystery error".

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::time::Duration;

#[derive(Clone)]
pub struct RouterClient {
    http: reqwest::Client,
    base: String,
    key: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub role: String,
    pub content: String,
}

impl Message {
    pub fn system(c: impl Into<String>) -> Self {
        Self { role: "system".into(), content: c.into() }
    }
    pub fn user(c: impl Into<String>) -> Self {
        Self { role: "user".into(), content: c.into() }
    }
    pub fn assistant(c: impl Into<String>) -> Self {
        Self { role: "assistant".into(), content: c.into() }
    }
}

#[derive(Serialize)]
struct ChatRequest<'a> {
    model: &'a str,
    messages: &'a [Message],
    #[serde(skip_serializing_if = "Option::is_none")]
    temperature: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    max_tokens: Option<u32>,
}

#[derive(Deserialize)]
struct ChatResponse {
    choices: Vec<Choice>,
}

#[derive(Deserialize)]
struct Choice {
    message: Message,
}

#[derive(Deserialize)]
struct ModelList {
    data: Vec<ModelEntry>,
}

#[derive(Deserialize)]
struct ModelEntry {
    id: String,
}

impl RouterClient {
    /// Build from the environment: `LITELLM_BASE` and `LITELLM_MASTER_KEY`.
    pub fn from_env() -> Result<Self> {
        let base = crate::env_or("LITELLM_BASE", "http://litellm:4000")
            .trim_end_matches('/')
            .to_string();
        let key = crate::env_required("LITELLM_MASTER_KEY")?;

        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(300))
            .connect_timeout(Duration::from_secs(5))
            .build()
            .context("could not build the HTTP client")?;

        Ok(Self { http, base, key })
    }

    /// Ask a job a question. Returns the assistant's text.
    pub async fn chat(
        &self,
        job: &str,
        messages: &[Message],
        temperature: Option<f32>,
        max_tokens: Option<u32>,
    ) -> Result<String> {
        let body = ChatRequest { model: job, messages, temperature, max_tokens };

        let resp = self
            .http
            .post(format!("{}/v1/chat/completions", self.base))
            .bearer_auth(&self.key)
            .json(&body)
            .send()
            .await
            .with_context(|| format!("could not reach the router at {}", self.base))?;

        let status = resp.status();
        if !status.is_success() {
            let detail = resp.text().await.unwrap_or_default();
            // 400 from LiteLLM almost always means the job name isn't in
            // config.yaml, so say so rather than echoing a wall of JSON.
            anyhow::bail!(
                "router refused job '{job}' ({status}): {}",
                detail.chars().take(400).collect::<String>()
            );
        }

        let parsed: ChatResponse = resp.json().await.context("router sent something unparseable")?;
        parsed
            .choices
            .into_iter()
            .next()
            .map(|c| c.message.content)
            .context("router returned no choices")
    }

    /// Which jobs the router currently offers. Useful for a health page.
    pub async fn jobs(&self) -> Result<Vec<String>> {
        let resp = self
            .http
            .get(format!("{}/v1/models", self.base))
            .bearer_auth(&self.key)
            .timeout(Duration::from_secs(10))
            .send()
            .await
            .context("could not reach the router")?
            .error_for_status()
            .context("router returned an error")?;

        let list: ModelList = resp.json().await.context("unparseable model list")?;
        Ok(list.data.into_iter().map(|m| m.id).collect())
    }

    /// Cheap liveness check that does not load a model.
    pub async fn healthy(&self) -> bool {
        self.http
            .get(format!("{}/health/liveliness", self.base))
            .timeout(Duration::from_secs(3))
            .send()
            .await
            .map(|r| r.status().is_success())
            .unwrap_or(false)
    }
}
