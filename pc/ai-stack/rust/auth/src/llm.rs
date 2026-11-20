//! =========================================================================
//!  The phone app's way to the assistant.
//!
//!  THE PROBLEM. `llm.home.example.net` proxies straight to LiteLLM
//!  with no forward_auth in front of it -- LiteLLM's own master key is the
//!  only thing guarding it. A browser app can sit behind the household gate
//!  and never see that key. A PHONE APP CANNOT: anything compiled into an
//!  APK or IPA is readable by whoever holds the file, so shipping the key to
//!  the app would publish it, and it is the key to every model in the house.
//!
//!  That is the same reasoning that made the app a PUBLIC OIDC client with
//!  no secret (see oidc.rs). Having refused to ship one secret, shipping a
//!  different one through the back door would be worse than pointless.
//!
//!  WHAT THIS DOES INSTEAD. The app presents the OIDC access token it
//!  already has. This service already issued that token and still holds the
//!  issued-token map, so validating it is a lookup, not a network call and
//!  not a signature check. If it is good, the request is forwarded to
//!  LiteLLM with the master key attached HERE, server-side, where it stays.
//!
//!  WHY IT LIVES IN THE AUTH SERVICE rather than a new one. Because the
//!  token map is here. A separate proxy would have to call back to this
//!  service to validate every request, or re-verify signatures itself, and
//!  both are more moving parts than one route that can read a HashMap.
//!
//!  WHAT IT DELIBERATELY DOES NOT DO:
//!    * It does not touch LiteLLM's configuration. Hermes, Open WebUI,
//!      Telegram and the kitchen display all route through that service; a
//!      config change to enable JWT auth there would put every one of them
//!      at risk to serve one app.
//!    * It does not let the caller choose the upstream. The URL is fixed
//!      from configuration, so a request body cannot redirect the master key
//!      somewhere else.
//!    * It does not accept a model name it has not been told about. An open
//!      passthrough would let a signed-in phone spend GPU time on anything
//!      LiteLLM happens to expose, including routes meant for other apps.
//! =========================================================================

use std::sync::Arc;

use axum::{
    body::Body,
    extract::State,
    http::{header, HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use serde_json::Value;

use crate::{people, AppState};

/// Where LiteLLM lives, on the shared docker network. NOT taken from the
/// request: a caller-supplied upstream is a caller-supplied destination for
/// the master key.
fn upstream() -> String {
    std::env::var("LITELLM_URL").unwrap_or_else(|_| "http://litellm:4000".into())
}

/// Casey's career portal. Fixed from configuration for the same reason the
/// LLM upstream is: a caller-supplied destination is a caller-supplied place
/// to send somebody's identity header.
/// Hermes over HTTP. Fixed from configuration, never from the request.
fn hermes_upstream() -> String {
    std::env::var("HERMES_URL").unwrap_or_else(|_| "http://hermes-http:8014".into())
}

fn career_upstream() -> String {
    std::env::var("ZEE_URL").unwrap_or_else(|_| "http://casey:8000".into())
}

fn master_key() -> String {
    std::env::var("LITELLM_MASTER_KEY").unwrap_or_default()
}

/// The jobs a phone is allowed to ask for.
///
/// These are LiteLLM *job names* from its config.yaml, not model names --
/// "fast" and "chat" are routed to whatever model is right that week, so
/// this list keeps working when the model behind a job changes.
///
/// An allow-list rather than a passthrough because the alternative is that
/// any signed-in phone can drive any route LiteLLM exposes, including ones
/// wired to other apps' budgets and other apps' system prompts.
const ALLOWED_MODELS: &[&str] = &["fast", "chat", "reasoning", "coding", "vision"];

/// Images make a request enormously bigger than text, and axum's default body
/// limit (2MB) rejects a phone photo outright -- as a 413 with no explanation,
/// which reads to the app like the server being broken. A modern phone camera
/// produces 3-8MB, and base64 inflates it by a third again.
pub const MAX_CHAT_BODY: usize = 24 * 1024 * 1024;

/// Who is calling: a household member, or somebody on a trial.
///
/// THE DISTINCTION EXISTS TO BE CHECKED, not to be carried around. A trial is
/// a stranger who typed an address into a phone -- the address is unverified
/// and proves nothing -- so anything that reads household data must refuse
/// one. `Caller::member_email` is how a route says "members only", and it is
/// the DEFAULT SHAPE: every existing route already called `caller_email`, so
/// renaming it to this closed every one of them to trials at a stroke, and a
/// route written next year is closed until somebody opens it on purpose.
pub enum Caller {
    Member(String),
    /// Carries the token rather than a borrowed record, because the trial file
    /// is read fresh under a lock at the point of spending. Anything cached
    /// here would be a counter read before the decision that uses it.
    Trial { email: String, token: String },
}

impl Caller {
    pub fn email(&self) -> &str {
        match self {
            Caller::Member(e) => e,
            Caller::Trial { email, .. } => email,
        }
    }
}

fn bearer(headers: &HeaderMap) -> String {
    headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .unwrap_or("")
        .trim()
        .to_string()
}

/// Validate whatever the app presented: an OIDC access token, or a trial
/// token. Trial tokens are checked SECOND, so nothing about this path can
/// change the answer for a real member.
fn caller(state: &AppState, headers: &HeaderMap) -> Result<Caller, Response> {
    let token = bearer(headers);
    if token.is_empty() {
        return Err((StatusCode::UNAUTHORIZED, "Missing bearer token.").into_response());
    }

    // token_holder rather than access_email: it also re-reads the allowlist
    // and applies revoked.json, so a removed or signed-out person loses the
    // app at the same moment they lose the website.
    if let Some(email) = crate::token_holder(state, &token) {
        return Ok(Caller::Member(email));
    }

    // A trial token is opaque and random, so this lookup cannot collide with
    // an OIDC token by accident.
    let now = common::people::now();
    let store = common::trial::read(&state.trial_file);
    if let Some(t) = common::trial::peek(&store, &token, now) {
        return Ok(Caller::Trial {
            email: t.email.clone(),
            token,
        });
    }

    Err((StatusCode::UNAUTHORIZED, "Token is not valid.").into_response())
}

/// The email of a HOUSEHOLD MEMBER, refusing trials.
///
/// Every route that reads or writes anything belonging to the household uses
/// this. The refusal is a 403 with a sentence a person can act on rather than
/// a bare status, because the app shows it to them verbatim.
fn caller_email(state: &AppState, headers: &HeaderMap) -> Result<String, Response> {
    match caller(state, headers)? {
        Caller::Member(email) => Ok(email),
        Caller::Trial { email, .. } => {
            tracing::info!("trial {email} tried a members-only route");
            Err((
                StatusCode::FORBIDDEN,
                "That part needs a household account. Email user3@example.com                  if you would like one.",
            )
                .into_response())
        }
    }
}

/// `POST /app/chat` -- OpenAI-shaped chat completions, for the phone apps.
///
/// Streaming is passed through untouched when the body asks for it, so the
/// app gets tokens as they are produced rather than waiting for a paragraph.
pub async fn chat(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(mut body): Json<Value>,
) -> Response {
    // THE ONE ROUTE A TRIAL MAY USE, which is why it calls `caller` rather
    // than `caller_email`. It is a plain proxy to the model with no household
    // context injected anywhere, so a stranger reaching it learns nothing
    // about this house -- that property is what makes a trial safe at all,
    // and it is worth re-checking before anything is ever added here.
    let who = match caller(&state, &headers) {
        Ok(c) => c,
        Err(resp) => return resp,
    };
    let email = who.email().to_string();

    // Checked BEFORE the upstream call so an exhausted trial costs no GPU
    // time, and spent only after it succeeds -- see the charge below.
    if let Caller::Trial { token, .. } = &who {
        let now = common::people::now();
        let store = common::trial::read(&state.trial_file);
        let left = common::trial::peek(&store, token, now).map(|t| t.remaining());
        if left.unwrap_or(0) == 0 {
            return trial_exhausted(&state, token).await;
        }
    }

    let key = master_key();
    if key.is_empty() {
        tracing::error!("/app/chat: LITELLM_MASTER_KEY is not set; refusing to call upstream");
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            "The assistant is not configured.",
        )
            .into_response();
    }

    // The requested job must be one this route knows about. Checked BEFORE
    // the upstream call so a rejected model costs nothing.
    let model = body.get("model").and_then(|m| m.as_str()).unwrap_or("");
    if !ALLOWED_MODELS.contains(&model) {
        tracing::warn!("/app/chat: {email} asked for model {model:?}, which is not allowed");
        return (
            StatusCode::BAD_REQUEST,
            format!(
                "Unknown model {model:?}. Available: {}.",
                ALLOWED_MODELS.join(", ")
            ),
        )
            .into_response();
    }

    // Stamp the caller into the request. LiteLLM records this, so usage is
    // attributable to a person rather than to "the phone app" collectively.
    if let Some(obj) = body.as_object_mut() {
        obj.insert("user".into(), Value::String(email.clone()));
    }

    let streaming = body
        .get("stream")
        .and_then(|s| s.as_bool())
        .unwrap_or(false);

    let url = format!("{}/v1/chat/completions", upstream().trim_end_matches('/'));
    let client = reqwest::Client::new();

    let sent = client
        .post(&url)
        .bearer_auth(&key)
        .json(&body)
        .send()
        .await;

    let upstream_resp = match sent {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("/app/chat: upstream unreachable: {e}");
            return (
                StatusCode::BAD_GATEWAY,
                "Could not reach the assistant.",
            )
                .into_response();
        }
    };

    let status = StatusCode::from_u16(upstream_resp.status().as_u16())
        .unwrap_or(StatusCode::BAD_GATEWAY);

    // SPEND THE QUESTION HERE, and only here. The upstream has accepted the
    // request, so the person is getting an answer; charging any earlier would
    // let a model outage quietly eat one of eight.
    //
    // Under the lock, and re-reading the file inside it: the value peeked at
    // above was for failing fast, and treating it as still true here would be
    // a read-modify-write race on somebody's remaining questions.
    let mut remaining: Option<u32> = None;
    if let Caller::Trial { token, .. } = &who {
        if status.is_success() {
            let _guard = state.trial_lock.lock().await;
            let now = common::people::now();
            let mut store = common::trial::read(&state.trial_file);
            match common::trial::charge(&mut store, token, now) {
                common::trial::Charge::Ok { email, remaining: left } => {
                    if let Err(e) = common::trial::write(&state.trial_file, &store) {
                        // Logged loudly rather than failing the request: the
                        // answer is already on its way, and refusing it now
                        // would be worse than an uncounted question.
                        tracing::error!("could not record a trial question for {email}: {e}");
                    }
                    remaining = Some(left);
                }
                common::trial::Charge::Exhausted { .. } => {
                    let _ = common::trial::write(&state.trial_file, &store);
                    remaining = Some(0);
                }
                common::trial::Charge::Invalid => {}
            }
        }
    }

    // The upstream's own content type is preserved, because a streamed reply
    // is text/event-stream and a normal one is application/json, and the
    // client distinguishes them by exactly that.
    let content_type = upstream_resp
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or(if streaming {
            "text/event-stream"
        } else {
            "application/json"
        })
        .to_string();

    // Streamed straight through rather than buffered: buffering a chat reply
    // to hand it over in one piece is the difference between an app that
    // feels alive and one that feels hung.
    let body_stream = Body::from_stream(upstream_resp.bytes_stream());

    let mut out = Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, content_type)
        // Proxies and phones both buffer event streams unless told not to.
        .header("X-Accel-Buffering", "no")
        .header(header::CACHE_CONTROL, "no-cache");

    // A HEADER RATHER THAN A FIELD IN THE BODY. The body is either a streamed
    // event stream or an OpenAI-shaped object, and the app must be able to
    // parse it with an ordinary OpenAI client -- adding a key of our own to
    // that would be a fork of the format for the sake of one integer.
    if let Some(left) = remaining {
        out = out.header("X-Trial-Remaining", left.to_string());
    }

    out.body(body_stream)
        .unwrap_or_else(|e| {
            tracing::error!("/app/chat: could not build response: {e}");
            (StatusCode::INTERNAL_SERVER_ERROR, "Proxy error.").into_response()
        })
}

/// Ask the vision model to say what is in a picture, in words.
///
/// THE POINT OF THIS FUNCTION. Hermes has the memory, the shelf and the
/// tools, but its agent loop is text in and text out -- there is nowhere to
/// put an image. Rather than wait for that to change, or send photos down a
/// second path where none of the assistant's knowledge exists, the vision
/// model becomes an EYE for Hermes: it turns pixels into a description, and
/// Hermes reasons about that description with everything it normally has.
///
/// THE PROMPT ASKS FOR DETAIL, NOT A CAPTION. "A photo of a circuit board" is
/// useless to whatever has to answer "why will this not power on". Text,
/// numbers, labels, part numbers, connector states and visible damage are
/// what a troubleshooting answer is built from, so they are asked for by
/// name. It is also told to say what it CANNOT make out: a blurry serial
/// number reported as blurry is worth more than a confident guess at one.
async fn describe_image(key: &str, data_url: &str) -> Result<String, String> {
    let prompt = concat!(
        "Describe this image in thorough, literal detail for someone who ",
        "cannot see it and must act on your description. Include: every ",
        "piece of visible text exactly as written, including labels, ",
        "numbers, part numbers, error messages and readings; what objects ",
        "are present and their condition; how things are connected or ",
        "arranged; colours and indicator lights. If something is blurry, ",
        "cut off, or you are unsure, say so plainly rather than guessing. ",
        "Do not speculate about causes and do not give advice -- report ",
        "only what is visibly there."
    );

    let body = serde_json::json!({
        "model": "vision",
        "stream": false,
        "messages": [{
            "role": "user",
            "content": [
                { "type": "text", "text": prompt },
                { "type": "image_url", "image_url": { "url": data_url } }
            ]
        }]
    });

    let client = reqwest::Client::new();
    let resp = client
        .post(format!("{}/v1/chat/completions", upstream().trim_end_matches('/')))
        .bearer_auth(key)
        .json(&body)
        .timeout(std::time::Duration::from_secs(300))
        .send()
        .await
        .map_err(|e| format!("the vision model did not answer: {e}"))?;

    if !resp.status().is_success() {
        return Err(format!("the vision model refused ({})", resp.status().as_u16()));
    }

    let v: Value = resp
        .json()
        .await
        .map_err(|e| format!("the vision model sent something unreadable: {e}"))?;

    let text = v
        .get("choices")
        .and_then(|c| c.get(0))
        .and_then(|c| c.get("message"))
        .and_then(|m| m.get("content"))
        .and_then(|c| c.as_str())
        .unwrap_or("")
        .trim()
        .to_string();

    if text.is_empty() {
        // The documented failure of the previous vision model was a silent
        // empty reply. Naming it beats handing Hermes an empty description
        // and letting it answer as though no photo had been sent.
        return Err("the vision model returned an empty description".into());
    }
    Ok(text)
}

/// `POST /app/ask` -- the real assistant: memory, tools, and the person's
/// own history, rather than a bare model.
///
/// THE DIFFERENCE FROM /app/chat. That route proxies to LiteLLM, which is a
/// model and nothing else: it has never met you, remembers nothing, and can
/// look nothing up. This one reaches Hermes, which has the per-person memory
/// store and the household tools. Both exist because they answer different
/// questions -- "what does a model say about this" and "what does MY
/// assistant say about this" -- and collapsing them would quietly lose the
/// second.
///
/// IDENTITY COMES FROM THE TOKEN, as everywhere else here. Hermes trusts
/// X-Auth-Email and is published on no port, so the header this fills is the
/// only way in. Since its memory is per person, a caller able to choose that
/// header could read anything anybody has ever told the assistant.
pub async fn ask(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(mut body): Json<Value>,
) -> Response {
    let email = match caller_email(&state, &headers) {
        Ok(e) => e,
        Err(resp) => return resp,
    };

    let text = body
        .get("text")
        .and_then(|t| t.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if text.is_empty() {
        return (StatusCode::BAD_REQUEST, "Nothing to ask.").into_response();
    }

    // Strip anything the caller should not be able to assert. `history` and
    // `session` are theirs to send; an identity field is not, and leaving one
    // in the body would invite a future reader to think it meant something.
    if let Some(obj) = body.as_object_mut() {
        obj.remove("email");
        obj.remove("X-Auth-Email");
    }

    // A PHOTO BECOMES WORDS BEFORE HERMES SEES IT.
    let mut described: Option<String> = None;
    if let Some(data_url) = body
        .get("image")
        .and_then(|i| i.as_str())
        .map(|v| v.to_string())
        .filter(|v| v.starts_with("data:image/"))
    {
        let key = master_key();
        if key.is_empty() {
            return (StatusCode::SERVICE_UNAVAILABLE, "The assistant is not configured.")
                .into_response();
        }
        match describe_image(&key, &data_url).await {
            Ok(desc) => described = Some(desc),
            Err(why) => {
                tracing::warn!("/app/ask: could not describe image for {email}: {why}");
                // REFUSE rather than answer without it. Silently dropping the
                // photo and replying to the words alone is the failure that
                // looks like the assistant ignoring what it was shown.
                return (
                    StatusCode::BAD_GATEWAY,
                    format!("Could not read the photo: {why}"),
                )
                    .into_response();
            }
        }
    }

    if let Some(desc) = described.clone() {
        // Marked as a DESCRIPTION rather than passed off as the person's own
        // words, so the assistant knows it is reasoning about a report of a
        // photo and can honestly say "from the picture, ...".
        let combined = format!(
            "[The user sent a photo. This is what the vision model sees in it, \
             verbatim:]\n{desc}\n[End of photo description.]\n\nTheir question: {text}"
        );
        if let Some(obj) = body.as_object_mut() {
            obj.insert("text".into(), Value::String(combined));
            // THE PICTURE IS KEPT, NOT DROPPED -- changed 2026-09-24.
            //
            // It used to be removed here, on the reasoning that Hermes
            // cannot see images so the description was all it could use.
            // That was true of the MODEL and false of the TOOLS. The garden
            // service runs its own vision pass with the plant's record as
            // context and gives a far better answer about a leaf than any
            // general description; with the photo thrown away it had nothing
            // to work from, so "is this plant healthy" found no plant tool,
            // reached `house_status`, and reported that the servers were up.
            //
            // Hermes forwards this only to tools that take a picture, and
            // ignores anything that is not a data: image URL.
        }
    }

    let url = format!("{}/ask", hermes_upstream().trim_end_matches('/'));
    let client = reqwest::Client::new();

    let sent = client
        .post(&url)
        .header("X-Auth-Email", &email)
        .json(&body)
        // Tool use makes this slow: a shelf search plus a model turn can run
        // well past a default timeout, and cutting it off looks to the user
        // exactly like the assistant ignoring them.
        .timeout(std::time::Duration::from_secs(300))
        .send()
        .await;

    let resp = match sent {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("/app/ask: hermes unreachable: {e}");
            return (StatusCode::BAD_GATEWAY, "The assistant did not answer.")
                .into_response();
        }
    };

    let code = resp.status().as_u16();
    if !resp.status().is_success() {
        tracing::warn!("/app/ask: hermes returned {code} for {email}");
    }

    match resp.json::<Value>().await {
        Ok(mut v) => {
            // Hand the description back as well. When an answer is wrong
            // because the photo was misread, this is the only way to tell --
            // otherwise a bad description is indistinguishable from a bad
            // assistant.
            if let (Some(desc), Some(obj)) = (described, v.as_object_mut()) {
                obj.insert("saw".into(), Value::String(desc));
            }

            // THE FOLLOW-UP BUTTONS, and only after a turn that actually
            // touched the garden. Attaching them to every answer would put
            // plant questions at the end of a conversation about the
            // printer; attaching them after a garden turn is the moment
            // they are about what was just being discussed.
            let touched_garden = v
                .get("tools")
                .and_then(|t| t.as_array())
                .is_some_and(|names| {
                    names.iter().any(|n| {
                        n.as_str().is_some_and(|n| n.starts_with("garden_"))
                    })
                });
            if touched_garden && garden_people().contains(&email.to_lowercase()) {
                let waiting = garden_approvals(&email).await;
                if !waiting.is_empty() {
                    if let Some(obj) = v.as_object_mut() {
                        obj.insert("approvals".into(), Value::Array(waiting));
                    }
                }
            }
            (
                StatusCode::from_u16(code).unwrap_or(StatusCode::BAD_GATEWAY),
                Json(v),
            )
                .into_response()
        }
        Err(e) => {
            tracing::error!("/app/ask: hermes sent something unreadable: {e}");
            (StatusCode::BAD_GATEWAY, "The assistant sent no answer.").into_response()
        }
    }
}

/// The garden's ingest service, which owns the approval queue. On the NUC,
/// LAN-only, no published route -- the same header-trust arrangement as the
/// rest of the household, and only safe while that stays true.
fn garden_ingest_upstream() -> String {
    std::env::var("GARDEN_INGEST_URL").unwrap_or_else(|_| "http://192.168.1.86:8025".into())
}

/// Anything waiting on this person, shaped for a button.
///
/// FAILS SILENTLY AND ON PURPOSE. If the ingest service is down, the answer
/// still goes back -- losing the whole reply because an optional follow-up
/// could not be fetched would be a worse bug than the missing buttons.
async fn garden_approvals(email: &str) -> Vec<Value> {
    let url = format!("{}/api/pending", garden_ingest_upstream().trim_end_matches('/'));
    let got = reqwest::Client::new()
        .get(&url)
        .header("X-Auth-Email", email)
        .timeout(std::time::Duration::from_secs(8))
        .send()
        .await;
    let Ok(resp) = got else { return Vec::new() };
    if !resp.status().is_success() {
        return Vec::new();
    }
    let Ok(v) = resp.json::<Value>().await else {
        return Vec::new();
    };
    v.get("pending")
        .and_then(|p| p.as_array())
        .map(|rows| {
            rows.iter()
                // NOTHING TO SUGGEST MEANS NOTHING TO TAP. A yes/no button
                // for a field with no proposed value would be a button that
                // approves nothing, which is worse than no button.
                .filter(|r| r.get("suggested").and_then(|x| x.as_str()).is_some_and(|x| !x.is_empty()))
                .take(6)
                .map(|r| {
                    serde_json::json!({
                        "id": r.get("id"),
                        "field": r.get("field_name"),
                        "raw": r.get("raw_value"),
                        "suggested": r.get("suggested"),
                        "confidence": r.get("confidence"),
                        "source": r.get("source"),
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

/// `POST /app/garden/decide` -- what the Yes and No buttons call.
///
/// THE BODY CARRIES AN ID AND A YES OR NO, AND NOTHING ELSE THAT MATTERS.
/// The value being approved is read from the database, not from the
/// request, so a tampered body cannot approve a different word than the one
/// the button showed. The identity comes from the token, so it also cannot
/// approve as somebody else.
pub async fn garden_decide(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Response {
    let email = match caller_email(&state, &headers) {
        Ok(e) => e,
        Err(resp) => return resp,
    };
    if !garden_people().contains(&email.to_lowercase()) {
        return (StatusCode::FORBIDDEN, "Not your garden.").into_response();
    }
    let Some(id) = body.get("id").and_then(|i| i.as_i64()) else {
        return (StatusCode::BAD_REQUEST, "Which one?").into_response();
    };
    let approve = body.get("approve").and_then(|a| a.as_bool()).unwrap_or(true);

    let url = format!("{}/api/decide", garden_ingest_upstream().trim_end_matches('/'));
    match reqwest::Client::new()
        .post(&url)
        .header("X-Auth-Email", &email)
        .json(&serde_json::json!({"id": id, "approve": approve}))
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await
    {
        Ok(r) => {
            let code = StatusCode::from_u16(r.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
            match r.json::<Value>().await {
                Ok(v) => (code, Json(v)).into_response(),
                Err(_) => (StatusCode::BAD_GATEWAY, "The garden sent no answer.").into_response(),
            }
        }
        Err(e) => {
            tracing::error!("/app/garden/decide: {e}");
            (StatusCode::BAD_GATEWAY, "Could not reach the garden.").into_response()
        }
    }
}

/// Where the household index lives. It is on the NUC; this service is on the
/// PC, so unlike every other upstream here this one crosses machines.
fn find_upstream() -> String {
    std::env::var("FIND_URL").unwrap_or_else(|_| "http://192.168.1.86:8087".into())
}

/// `POST /app/upload` -- add a document to your own shelf, from the phone.
///
/// WHY A PROXY AND NOT A DIRECT CALL. The index's own /api/upload identifies
/// people by the X-Auth-Email header Caddy's forward_auth injects, which
/// works for a browser sitting behind the gate and not at all for a phone
/// holding a bearer token. This fills that header from the validated token,
/// so the two surfaces agree about who somebody is.
///
/// THE INDEX STILL DECIDES WHERE A FILE MAY GO. It checks the target against
/// a list DERIVED from INDEX_OWNERS -- the same configuration the search
/// filters on -- so "where may I put a file" and "what may I read" cannot
/// drift apart. This route deliberately does not second-guess that: it
/// forwards the target and lets the owner of the rule enforce it. Duplicating
/// the check here would create a second copy to fall out of step, and the
/// looser copy is the one nobody notices.
///
/// WHO CAN SEE WHAT IS UPLOADED: only the SSO members who own that shelf.
/// That is not enforced here either, and again on purpose -- it is the same
/// owner_of() the index applies to every search, so an uploaded file is
/// visible to exactly the people who could already search that folder.
pub async fn upload(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    mut multipart: axum::extract::Multipart,
) -> Response {
    let email = match caller_email(&state, &headers) {
        Ok(e) => e,
        Err(resp) => return resp,
    };

    let mut target = String::new();
    let mut files: Vec<(String, Vec<u8>)> = Vec::new();

    while let Ok(Some(field)) = multipart.next_field().await {
        let name = field.name().unwrap_or("").to_string();
        let filename = field.file_name().map(|f| f.to_string());
        match (name.as_str(), filename) {
            ("target", _) => {
                target = field.text().await.unwrap_or_default().trim().to_string();
            }
            ("files", Some(fname)) => {
                match field.bytes().await {
                    Ok(bytes) => files.push((fname, bytes.to_vec())),
                    Err(e) => {
                        tracing::warn!("/app/upload: {email} sent an unreadable part: {e}");
                        return (StatusCode::BAD_REQUEST, "That file could not be read.")
                            .into_response();
                    }
                }
            }
            _ => {}
        }
    }

    if target.is_empty() {
        return (StatusCode::BAD_REQUEST, "No shelf chosen.").into_response();
    }
    if files.is_empty() {
        return (StatusCode::BAD_REQUEST, "No files sent.").into_response();
    }

    let mut form = reqwest::multipart::Form::new().text("target", target.clone());
    for (fname, bytes) in files {
        let part = reqwest::multipart::Part::bytes(bytes).file_name(fname);
        form = form.part("files", part);
    }

    let url = format!("{}/api/upload", find_upstream().trim_end_matches('/'));
    let client = reqwest::Client::new();
    let sent = client
        .post(&url)
        .header("X-Auth-Email", &email)
        .multipart(form)
        // A textbook can be hundreds of megabytes and the NUC is across the
        // network; a short timeout here turns a slow upload into a mystery.
        .timeout(std::time::Duration::from_secs(600))
        .send()
        .await;

    let resp = match sent {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("/app/upload: index unreachable: {e}");
            return (StatusCode::BAD_GATEWAY, "Could not reach the index.").into_response();
        }
    };

    let code = StatusCode::from_u16(resp.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
    match resp.json::<Value>().await {
        Ok(v) => (code, Json(v)).into_response(),
        Err(e) => {
            tracing::error!("/app/upload: index sent something unreadable: {e}");
            (StatusCode::BAD_GATEWAY, "The index sent no answer.").into_response()
        }
    }
}

/// Where intake lives. The classifier and permission gate are there, not
/// here -- this route only proves who is asking.
fn intake_upstream() -> String {
    std::env::var("INTAKE_URL").unwrap_or_else(|_| "http://192.168.1.86:8100".into())
}

/// `POST /app/file` -- hand a file to intake and let it work out what it is.
///
/// THE DIFFERENCE FROM /app/upload. That one needs a shelf: the person
/// decides where it goes and it lands there. This one decides for them --
/// intake classifies the contents, maps the kind to the app grant that
/// governs it, and files it only if their SSO says they may WRITE to that
/// library. A recipe from somebody with `recipe:view` is held rather than
/// filed, and the reason is recorded.
///
/// NO SHELF PARAMETER, DELIBERATELY. Offering one would let the caller
/// choose a destination the classifier would not have chosen, which is the
/// permission check answered by the person it applies to.
///
/// The phone has a bearer token and intake sits behind a cookie gate, so
/// this is the same translation /app/garden does: verify the token, attach
/// the verified address, forward. The app never sees intake directly and
/// cannot set its own identity.
pub async fn app_file(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    mut multipart: axum::extract::Multipart,
) -> Response {
    let email = match caller_email(&state, &headers) {
        Ok(e) => e,
        Err(resp) => return resp,
    };

    let mut files: Vec<(String, Vec<u8>)> = Vec::new();
    while let Ok(Some(field)) = multipart.next_field().await {
        let fname = field.file_name().map(|f| f.to_string());
        if let Some(fname) = fname {
            match field.bytes().await {
                Ok(bytes) => files.push((fname, bytes.to_vec())),
                Err(e) => {
                    tracing::warn!("/app/file: {email} sent an unreadable part: {e}");
                    return (StatusCode::BAD_REQUEST, "That file could not be read.")
                        .into_response();
                }
            }
        }
    }
    if files.is_empty() {
        return (StatusCode::BAD_REQUEST, "No files sent.").into_response();
    }

    let url = format!("{}/upload", intake_upstream().trim_end_matches('/'));
    let client = reqwest::Client::new();
    let mut results: Vec<Value> = Vec::new();

    // ONE REQUEST PER FILE, because intake classifies one thing at a time
    // and its answer -- kind, destination, whether it was held and why --
    // is per file. Batching them would collapse four different outcomes
    // into one status.
    for (fname, bytes) in files {
        let part = reqwest::multipart::Part::bytes(bytes).file_name(fname.clone());
        let form = reqwest::multipart::Form::new().part("file", part);
        let sent = client
            .post(&url)
            .header("X-Auth-Email", &email)
            .multipart(form)
            .timeout(std::time::Duration::from_secs(600))
            .send()
            .await;
        match sent {
            Ok(r) => match r.json::<Value>().await {
                Ok(v) => results.push(serde_json::json!({"file": fname, "result": v})),
                Err(_) => results.push(serde_json::json!({
                    "file": fname, "error": "intake sent no answer"
                })),
            },
            Err(e) => {
                tracing::error!("/app/file: intake unreachable: {e}");
                results.push(serde_json::json!({
                    "file": fname, "error": "could not reach intake"
                }));
            }
        }
    }

    Json(serde_json::json!({ "files": results })).into_response()
}

/// `GET /app/upload-targets` -- the shelves this person may add to.
pub async fn upload_targets(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> Response {
    let email = match caller_email(&state, &headers) {
        Ok(e) => e,
        Err(resp) => return resp,
    };

    let url = format!("{}/api/upload-targets", find_upstream().trim_end_matches('/'));
    match reqwest::Client::new()
        .get(&url)
        .header("X-Auth-Email", &email)
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await
    {
        Ok(r) => match r.json::<Value>().await {
            Ok(v) => Json(v).into_response(),
            Err(_) => (StatusCode::BAD_GATEWAY, "The index sent no answer.").into_response(),
        },
        Err(e) => {
            tracing::error!("/app/upload-targets: index unreachable: {e}");
            (StatusCode::BAD_GATEWAY, "Could not reach the index.").into_response()
        }
    }
}

/// `GET /app/recipes` and `GET /app/recipe/{id}` -- the household recipes,
/// and one of them broken into steps that can be cooked from.
///
/// Both are thin proxies over the index, which does the real work: the same
/// owner filter every search uses, and the parsed-and-cached step list. The
/// identity comes from the token, as everywhere else here.
pub async fn recipes(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    axum::extract::Query(q): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> Response {
    let email = match caller_email(&state, &headers) {
        Ok(e) => e,
        Err(resp) => return resp,
    };
    let search = q.get("q").cloned().unwrap_or_default();
    let url = format!(
        "{}/api/recipes?limit=60&q={}",
        find_upstream().trim_end_matches('/'),
        urlencoding_light(&search)
    );
    proxy_get(&url, &email, 60).await
}

pub async fn recipe(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    axum::extract::Path(id): axum::extract::Path<String>,
) -> Response {
    let email = match caller_email(&state, &headers) {
        Ok(e) => e,
        Err(resp) => return resp,
    };
    // Ids come from the list this same person just fetched, but they arrive
    // in a URL, so they are constrained rather than trusted.
    if !id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_') || id.len() > 64 {
        return (StatusCode::BAD_REQUEST, "Not a recipe id.").into_response();
    }
    let url = format!("{}/api/recipe/{}", find_upstream().trim_end_matches('/'), id);
    // Generous: an unparsed recipe costs a model call, and the first person
    // to open one should not be the one who times out paying for it.
    proxy_get(&url, &email, 240).await
}

/// Percent-encode a query value. Deliberately minimal rather than a new
/// dependency: this escapes what a recipe search can actually contain.
fn urlencoding_light(s: &str) -> String {
    s.chars()
        .map(|c| match c {
            'A'..='Z' | 'a'..='z' | '0'..='9' | '-' | '_' | '.' | '~' => c.to_string(),
            ' ' => "+".to_string(),
            other => format!("%{:02X}", other as u32 as u8),
        })
        .collect()
}

async fn proxy_get(url: &str, email: &str, secs: u64) -> Response {
    match reqwest::Client::new()
        .get(url)
        .header("X-Auth-Email", email)
        .timeout(std::time::Duration::from_secs(secs))
        .send()
        .await
    {
        Ok(r) => {
            let code = StatusCode::from_u16(r.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
            match r.json::<Value>().await {
                Ok(v) => (code, Json(v)).into_response(),
                Err(_) => (StatusCode::BAD_GATEWAY, "The index sent no answer.").into_response(),
            }
        }
        Err(e) => {
            tracing::error!("proxy_get {url}: {e}");
            (StatusCode::BAD_GATEWAY, "Could not reach the index.").into_response()
        }
    }
}

/// `POST /app/support` -- feedback, or a request for help, emailed to the
/// household account.
///
/// The person's identity comes from the token, never from the body. Letting
/// a caller name the sender would turn this into a way for any signed-in
/// phone to send mail that appears to come from somebody else.
pub async fn support(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Response {
    let email = match caller_email(&state, &headers) {
        Ok(e) => e,
        Err(resp) => return resp,
    };

    let text = body
        .get("text")
        .and_then(|t| t.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if text.is_empty() {
        return (StatusCode::BAD_REQUEST, "Nothing to send.").into_response();
    }
    if text.len() > 20_000 {
        return (StatusCode::BAD_REQUEST, "That message is too long to send.")
            .into_response();
    }

    // Constrained to a known set so the subject line cannot be written by the
    // caller -- a free-text subject is a way to put anything into an inbox.
    let kind = match body.get("kind").and_then(|k| k.as_str()).unwrap_or("feedback") {
        "help" => "Help request",
        "bug" => "Bug report",
        _ => "Feedback",
    };

    // The display name comes from people.json, the same place every other
    // surface reads it, rather than from the request.
    let list = people(&state);
    let display = common::people::find(&list, &email)
        .map(|p| p.name.clone())
        .filter(|n| !n.is_empty())
        .unwrap_or_else(|| email.clone());

    match crate::mail::send_support(&email, &display, kind, &text).await {
        Ok(true) => Json(serde_json::json!({
            "sent": true,
            "note": "Sent. Jason will see it and can reply to you directly."
        }))
        .into_response(),
        Ok(false) => (
            StatusCode::SERVICE_UNAVAILABLE,
            "Email is not configured on the server, so that was not sent.",
        )
            .into_response(),
        Err(e) => {
            tracing::error!("/app/support: send failed for {email}: {e}");
            (StatusCode::BAD_GATEWAY, "That could not be sent. Try again.").into_response()
        }
    }
}

/// `GET /app/awards` -- the career options Casey can choose between.
///
/// Read-only on purpose. Setting progress writes into a promotion record and
/// is not something a phone should do by accident; that stays on the portal
/// until there is a reason and a confirmation step for it.
pub async fn awards(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    if let Err(resp) = caller_email(&state, &headers) {
        return resp;
    }

    let token = std::env::var("ZEE_ACTION_TOKEN").unwrap_or_default();
    if token.is_empty() {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            "The career portal's action API is not configured.",
        )
            .into_response();
    }

    // /actions is the router's prefix -- not /awards at the root, which is
    // what a reasonable guess produces and what returns 404.
    let url = format!("{}/actions/awards", career_upstream().trim_end_matches('/'));
    match reqwest::Client::new()
        .get(&url)
        .bearer_auth(&token)
        .timeout(std::time::Duration::from_secs(60))
        .send()
        .await
    {
        Ok(r) => {
            let code = StatusCode::from_u16(r.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
            match r.json::<Value>().await {
                Ok(v) => (code, Json(v)).into_response(),
                Err(_) => (StatusCode::BAD_GATEWAY, "The portal sent no answer.").into_response(),
            }
        }
        Err(e) => {
            tracing::error!("/app/awards: portal unreachable: {e}");
            (StatusCode::BAD_GATEWAY, "Could not reach the career portal.").into_response()
        }
    }
}

/// `POST /app/career` -- one question to Casey's career portal.
///
/// WHY THIS IS A SEPARATE ROUTE and not a model in the chat allow-list.
/// The career portal is not a model, it is a system of record: it KEEPS what
/// is said to it, filed under the person who said it. Routing career talk
/// through the general chat endpoint would mean a stray sentence about work
/// lands in a promotion file, or worse, that a career answer gets filed
/// under the wrong person. The Telegram bridge draws the same line with its
/// `/career` and `/house` commands, and for the same reason.
///
/// THE IDENTITY IS NOT THE CALLER'S TO CHOOSE. The portal trusts
/// `X-Auth-Email` the way every header-trusting app in this house does, so
/// whatever is put in that header IS the person. It is filled from the
/// validated access token here, server-side. A body field naming the person
/// would let any signed-in phone read and write anybody's career file, which
/// is the single worst thing this route could do.
pub async fn career(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Response {
    let email = match caller_email(&state, &headers) {
        Ok(e) => e,
        Err(resp) => return resp,
    };

    let text = body
        .get("text")
        .and_then(|t| t.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if text.is_empty() {
        return (StatusCode::BAD_REQUEST, "Nothing to ask.").into_response();
    }

    // A label so the portal can tell where a thought came from. The caller
    // may name its surface but nothing more -- it cannot name the person.
    let session = body
        .get("session")
        .and_then(|s| s.as_str())
        .filter(|s| s.len() <= 40 && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '-'))
        .unwrap_or("android");

    let url = format!("{}/api/ask", career_upstream().trim_end_matches('/'));
    let client = reqwest::Client::new();

    let sent = client
        .post(&url)
        .header("X-Auth-Email", &email)
        .form(&[("text", text.as_str()), ("session", session)])
        // The portal thinks before it answers; the Telegram bridge allows
        // 300s for the same call and that number was not arbitrary.
        .timeout(std::time::Duration::from_secs(300))
        .send()
        .await;

    let resp = match sent {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("/app/career: portal unreachable: {e}");
            return (StatusCode::BAD_GATEWAY, "The career portal did not answer.")
                .into_response();
        }
    };

    if !resp.status().is_success() {
        let code = resp.status().as_u16();
        tracing::warn!("/app/career: portal refused with {code} for {email}");
        return (
            StatusCode::BAD_GATEWAY,
            format!("The career portal refused that ({code}). Nothing was saved."),
        )
            .into_response();
    }

    match resp.json::<Value>().await {
        Ok(v) => Json(v).into_response(),
        Err(e) => {
            tracing::error!("/app/career: portal sent something unreadable: {e}");
            (StatusCode::BAD_GATEWAY, "The career portal sent no answer.").into_response()
        }
    }
}

/// `GET /app/models` -- what the app may ask for, so it does not have to
/// hard-code the list and drift from this one.
pub async fn models(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    if let Err(resp) = caller_email(&state, &headers) {
        return resp;
    }
    Json(serde_json::json!({ "models": ALLOWED_MODELS })).into_response()
}

// ===========================================================================
//  Sample prompts
// ===========================================================================

/// One suggestion, and the thing it needs in order to work.
///
/// TAGGED RATHER THAN FREE TEXT, because a suggestion the app cannot honour is
/// worse than no suggestion: somebody taps "what is in my china town garden",
/// gets told the assistant cannot see a garden, and reasonably concludes the
/// whole thing is broken. Every line here is filtered against what its reader
/// can actually reach before it is offered.
struct Suggestion {
    /// "" means everybody, including a trial. Otherwise the capability the
    /// person must have: an app grant name, or "garden".
    needs: &'static str,
    text: &'static str,
}

/// The pool. Written as things a person would actually say out loud, not as a
/// feature list -- "show me what you can do" is a demo, "is anything in my
/// beds toxic to the dog" is a reason to open the app twice.
const SUGGESTIONS: &[Suggestion] = &[
    // --- anybody, including a trial. Nothing here touches the household. ---
    Suggestion { needs: "", text: "Explain how a heat pump works, simply." },
    Suggestion { needs: "", text: "Help me write a polite note cancelling an appointment." },
    Suggestion { needs: "", text: "What is the difference between baking soda and baking powder?" },
    Suggestion { needs: "", text: "Give me a five minute stretch routine for a stiff back." },
    Suggestion { needs: "", text: "Talk me through changing a tyre." },

    // --- the household index: documents, manuals, books ------------------
    Suggestion { needs: "find", text: "Find the manual for the furnace and tell me the filter size." },
    Suggestion { needs: "find", text: "What do I have on file about the roof?" },
    Suggestion { needs: "find", text: "Search my documents for the car's service history." },
    Suggestion { needs: "study", text: "What does my textbook say about gram staining? Quote the page." },
    Suggestion { needs: "read", text: "Summarise a chapter from a book on my shelf." },

    // --- recipes ---------------------------------------------------------
    Suggestion { needs: "recipe", text: "What can I make with chicken, rice and whatever is usually in a cupboard?" },
    Suggestion { needs: "recipe", text: "Find me a recipe I have not cooked in a while." },
    Suggestion { needs: "recipe", text: "Scale my bread recipe to two loaves." },

    // --- the garden ------------------------------------------------------
    Suggestion { needs: "garden", text: "What is growing in my china town garden?" },
    Suggestion { needs: "garden", text: "Is anything in my beds toxic to a dog?" },
    Suggestion { needs: "garden", text: "Which of my plants attract bees?" },
    Suggestion { needs: "garden", text: "What is the soil moisture right now, and should I water?" },
    Suggestion { needs: "garden", text: "Show me a photo of the Sherwood Abelia and tell me how to prune it." },
];

/// Who gets the garden suggestions. READ FROM THE SAME ENV VAR HERMES USES,
/// so the list that decides whether the tool exists and the list that decides
/// whether it is advertised cannot drift apart and start disagreeing.
fn garden_people() -> Vec<String> {
    std::env::var("GARDEN_FOR")
        .unwrap_or_else(|_| "user6@example.com,user1@example.com,user3@example.com".into())
        .split(',')
        .map(|e| e.trim().to_lowercase())
        .filter(|e| !e.is_empty())
        .collect()
}

/// `GET /app/prompts` -- a handful of things worth asking, for this person.
///
/// A TRIAL GETS THE PLAIN ONES ONLY, which is not a consolation prize: those
/// five are the whole of what a trial can do, so showing them is showing the
/// truth rather than a menu with most of it greyed out.
pub async fn prompts(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    let who = match caller(&state, &headers) {
        Ok(c) => c,
        Err(resp) => return resp,
    };

    let mut have: Vec<&'static str> = vec![""];
    if let Caller::Member(email) = &who {
        let list = people(&state);
        if let Some(person) = common::people::find(&list, email) {
            for app in ["find", "recipe", "study", "read"] {
                if person.can_use(app) {
                    have.push(app);
                }
            }
        }
        if garden_people().iter().any(|g| g == &email.to_lowercase()) {
            have.push("garden");
        }
    }

    let mut pool: Vec<&'static str> = SUGGESTIONS
        .iter()
        .filter(|s| have.contains(&s.needs))
        .map(|s| s.text)
        .collect();

    // Shuffled so the same four are not on screen every launch -- the point of
    // a suggestion is to show something that had not occurred to the reader,
    // and a fixed list stops doing that on the second day.
    //
    // Fisher-Yates over the OS CSPRNG. Not because this needs to be
    // unguessable, but because `rand` is already here for the tokens and a
    // hand-rolled shuffle biases toward the front of the list.
    use rand::seq::SliceRandom;
    pool.shuffle(&mut rand::rand_core::UnwrapErr(rand::rngs::SysRng));
    pool.truncate(6);

    Json(serde_json::json!({ "prompts": pool })).into_response()
}

// ===========================================================================
//  Trial access
// ===========================================================================

/// The address a trial is told to write to. Configurable rather than compiled
/// in, so the person who fields these is a setting and not a code change.
fn trial_contact() -> String {
    std::env::var("TRIAL_CONTACT").unwrap_or_else(|_| "user3@example.com".into())
}

/// `POST /app/trial/start` -- redeem an invite code.
///
/// NO BEARER TOKEN, because the caller by definition has none yet. That makes
/// this the only unauthenticated route in this file, so it does two things
/// carefully: it says the same thing for a code that never existed as for one
/// that was switched off (see `StartError::message`), and it refuses to mint a
/// second trial for an address that already has one.
pub async fn trial_start(
    State(state): State<Arc<AppState>>,
    Json(body): Json<Value>,
) -> Response {
    let email = body.get("email").and_then(|v| v.as_str()).unwrap_or("").trim();
    let code = body.get("code").and_then(|v| v.as_str()).unwrap_or("").trim();

    let _guard = state.trial_lock.lock().await;
    let now = common::people::now();
    let mut store = common::trial::read(&state.trial_file);

    // ALREADY A MEMBER? Say so rather than handing them a worse account than
    // the one they have. Somebody who signed up before being added would
    // otherwise sit on a trial and wonder why nothing works.
    let people = people(&state);
    if common::people::find(&people, &common::people::normalise(email)).is_some() {
        return (
            StatusCode::CONFLICT,
            "You already have a household account -- sign in with that instead.",
        )
            .into_response();
    }

    match common::trial::start(&mut store, email, code, now) {
        Ok(trial) => {
            if let Err(e) = common::trial::write(&state.trial_file, &store) {
                tracing::error!("could not save the trial for {}: {e}", trial.email);
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "Could not start the trial. Try again in a moment.",
                )
                    .into_response();
            }
            tracing::info!(
                "trial started for {} on code {} ({} left)",
                trial.email, trial.code, trial.remaining()
            );
            Json(serde_json::json!({
                "token": trial.token,
                "email": trial.email,
                "quota": trial.quota,
                "used": trial.used,
                "remaining": trial.remaining(),
                "contact": trial_contact(),
            }))
            .into_response()
        }
        Err(e) => {
            tracing::warn!("trial code rejected for {email:?}: {e:?}");
            (StatusCode::FORBIDDEN, e.message()).into_response()
        }
    }
}

/// `GET /app/trial/status` -- how much is left.
///
/// Lets the app show the counter without spending a question to find out.
pub async fn trial_status(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> Response {
    let token = bearer(&headers);
    let now = common::people::now();
    let store = common::trial::read(&state.trial_file);

    match common::trial::peek(&store, &token, now) {
        Some(t) => Json(serde_json::json!({
            "email": t.email,
            "quota": t.quota,
            "used": t.used,
            "remaining": t.remaining(),
            "contact": trial_contact(),
        }))
        .into_response(),
        // Also the answer for a MEMBER's token, deliberately: "you are not on
        // a trial" and "that trial is over" are the same fact to the app,
        // which shows the counter only when there is one.
        None => (StatusCode::NOT_FOUND, "No trial on this token.").into_response(),
    }
}

/// The reply when the eighth question has already been asked.
///
/// 402 Payment Required, which is the closest honest status: the request was
/// understood and refused for a quota reason, not an authentication one. A 403
/// would make an app that retries on auth failure try to sign in again.
async fn trial_exhausted(state: &AppState, token: &str) -> Response {
    let contact = trial_contact();
    let mut who = String::new();

    // Marking `notified` is a write, so it happens under the lock with the
    // read that decides it -- otherwise two taps in the same second both
    // decide they are first and Jason gets two emails.
    {
        let _guard = state.trial_lock.lock().await;
        let now = common::people::now();
        let mut store = common::trial::read(&state.trial_file);
        if let common::trial::Charge::Exhausted { email, notify } =
            common::trial::charge(&mut store, token, now)
        {
            who = email.clone();
            if notify {
                if let Err(e) = common::trial::write(&state.trial_file, &store) {
                    tracing::error!("could not mark trial {email} as notified: {e}");
                }
                // Jason hears about it as well as being written to. He asked
                // for them to email him; this is so he knows somebody is
                // about to, and who.
                let body = format!(
                    "{email} has used all of their trial questions.\n\n\
                     They have been told to email you at {contact} if they \
                     would like to keep using it.\n\n\
                     To let them in: /admin/people, add {email}, then send \
                     them the password."
                );
                match crate::mail::send_support(&email, &email, "trial finished", &body).await {
                    Ok(true) => tracing::info!("told Jason that {email}'s trial ran out"),
                    Ok(false) => tracing::warn!("no outbox configured; {email}'s trial ran out"),
                    Err(e) => tracing::error!("could not email about {email}'s trial: {e}"),
                }
            }
        }
    }

    tracing::info!("trial exhausted for {who:?}");
    (
        StatusCode::PAYMENT_REQUIRED,
        format!(
            "That was the last of your free questions. Email {contact} if you \
             would like to keep using it -- mention the address you signed up \
             with and you will be set up properly."
        ),
    )
        .into_response()
}

/// The garden endpoints a photograph can be sent to, and nothing else.
///
/// AN ALLOWLIST, NOT A PASSTHROUGH. This route takes the action from the
/// URL, so without this list it would proxy anything the caller named --
/// `/api/bed-delete` included -- with a verified identity attached. The
/// whole value of proxying here is that the email cannot be forged; that
/// value disappears the moment the path cannot be forged either.
///
/// All four write nothing that is not reversible: identify writes nothing
/// at all, tag queues proposals for approval, receipt records a purchase,
/// diagnose records a checkup.
// `tag-queue` is the BULK path: many tags in one call, a receipt back,
// and the reading done afterwards by a worker. Jason, 2026-09-27: "there
// needs to be a bulk tag adder ... so that if someone wants to add 20 or
// 30 tags they can at once front and back without taking one picture at
// a time." It is allowlisted here like every other action rather than
// reached directly, because garden-ingest trusts X-Auth-Email absolutely
// and this route is the only thing entitled to set it.
const CAMERA_ACTIONS: [&str; 6] =
    ["identify", "diagnose", "tag", "receipt", "lookup", "tag-queue"];

/// A photograph taken in the garden, sent to the service that can read it.
///
/// WHY THE APP CANNOT CALL THE GARDEN DIRECTLY. garden-ingest decides who
/// you are from an `X-Auth-Email` header and trusts it completely, which is
/// only safe because nothing outside this machine can set it. The phone has
/// a bearer token instead, so this route is the translation: verify the
/// token, look up the real address, and attach it upstream. A phone that
/// could set that header itself could file a receipt as anybody.
///
/// GENEROUS TIMEOUT, ON PURPOSE. These calls run a vision model over a
/// photograph and then a catalogue lookup; thirty seconds is comfortably
/// too short and produces a failure that looks like a broken camera.
pub async fn garden_camera(
    State(state): State<Arc<AppState>>,
    axum::extract::Path(action): axum::extract::Path<String>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Response {
    let email = match caller_email(&state, &headers) {
        Ok(e) => e,
        Err(resp) => return resp,
    };
    if !garden_people().contains(&email.to_lowercase()) {
        return (StatusCode::FORBIDDEN, "Not your garden.").into_response();
    }
    if !CAMERA_ACTIONS.contains(&action.as_str()) {
        return (StatusCode::NOT_FOUND, "No such garden action.").into_response();
    }

    let url = format!(
        "{}/api/{}",
        garden_ingest_upstream().trim_end_matches('/'),
        action
    );
    match reqwest::Client::new()
        .post(&url)
        .header("X-Auth-Email", &email)
        .json(&body)
        .timeout(std::time::Duration::from_secs(300))
        .send()
        .await
    {
        Ok(r) => {
            let code = StatusCode::from_u16(r.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
            match r.json::<Value>().await {
                Ok(v) => (code, Json(v)).into_response(),
                Err(_) => (StatusCode::BAD_GATEWAY, "The garden sent no answer.").into_response(),
            }
        }
        Err(e) => {
            tracing::error!("/app/garden/{action}: {e}");
            (StatusCode::BAD_GATEWAY, "The garden did not answer.").into_response()
        }
    }
}
