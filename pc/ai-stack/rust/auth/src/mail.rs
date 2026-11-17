//! Sending the reset code.
//!
//! DELIBERATELY BEHIND ONE FUNCTION
//! `send_reset_code()` is the only thing the rest of the service knows about.
//! Which provider actually carries the mail is decided here and nowhere else,
//! so swapping providers later touches this file only.
//!
//! WHY PLAIN SMTP AND NOT A PROVIDER'S HTTP API
//! This was written against SendGrid's HTTP API first, and SendGrid removed
//! its free tier before the code ever sent a message. That is the argument for
//! SMTP in one sentence: every mail provider that has ever existed speaks it,
//! so when one of them changes its pricing the fix here is four environment
//! variables, not a rewrite. An HTTP API would have tied this file to whichever
//! company happened to be cheapest the week it was written.
//!
//! WHAT TO POINT IT AT
//! Gmail is the obvious choice for this house: the account already exists, an
//! app password takes two minutes to make, and the limit is 500 messages a day
//! against a real-world need of maybe a dozen a year.
//!
//!     SMTP_HOST=smtp.gmail.com
//!     SMTP_PORT=587
//!     SMTP_USER=<the gmail address>
//!     SMTP_PASSWORD=<a Google APP PASSWORD, not the account password>
//!     MAIL_FROM=<the same gmail address>
//!
//! An app password is made at myaccount.google.com -> Security -> 2-Step
//! Verification -> App passwords. It requires 2-Step Verification to be on.
//! Use one: Gmail rejects the real account password over SMTP, and pasting the
//! real one into a config file would hand this container the whole account
//! rather than the ability to send mail, which is all it needs.
//!
//! MAIL_FROM should match SMTP_USER. Gmail rewrites a mismatched From to the
//! authenticated account anyway, so a different value there does not do what it
//! looks like it does -- it just makes the logs disagree with reality.
//!
//! WHY IT STILL WORKS WITH NO PROVIDER CONFIGURED
//! With no SMTP host set, this does not fail -- it writes the code to the
//! container log at WARN and returns Ok. That is a real decision, not a stub:
//!
//!   * The reset flow is testable and usable from the day it ships. Jason can
//!     read the code out of `logs.cmd auth` and hand it to whoever is locked
//!     out, which is exactly what he would otherwise do by editing a file.
//!   * Returning an error instead would make "the outbox is not set up yet"
//!     look identical to "the mail failed", and the person resetting would be
//!     told to try again forever.
//!
//! It logs loudly BECAUSE a code in a logfile is a real, if small, exposure:
//! anyone who can read container logs can complete somebody's reset during
//! those 15 minutes. That is acceptable while the only reader is the person
//! who administers the box, and stops the moment SMTP is configured.

use base64::Engine as _;
use lettre::message::header::ContentType;
use lettre::transport::smtp::authentication::Credentials;
use lettre::{AsyncSmtpTransport, AsyncTransport, Message, Tokio1Executor};

// ── Gmail API sender ─────────────────────────────────────────────────────────

/// Try to send via the Gmail API using OAuth2. Returns Ok(true) on success,
/// Ok(false) if the Gmail env vars are absent (caller should fall through to
/// SMTP), Err on a real failure (bad token, network error, API rejection).
///
/// Env vars required:
///   GMAIL_CLIENT_ID      – OAuth2 client id from Google Cloud Console
///   GMAIL_CLIENT_SECRET  – OAuth2 client secret
///   GMAIL_REFRESH_TOKEN  – offline refresh token with gmail.send scope
///   GMAIL_SENDER         – the Gmail address (must match the token owner)
async fn try_gmail(to: &str, subject: &str, body: &str) -> anyhow::Result<bool> {
    let client_id     = common::env_or("GMAIL_CLIENT_ID",     "");
    let client_secret = common::env_or("GMAIL_CLIENT_SECRET", "");
    let refresh_token = common::env_or("GMAIL_REFRESH_TOKEN", "");
    let sender        = common::env_or("GMAIL_SENDER",        "");

    if client_id.is_empty() || client_secret.is_empty()
        || refresh_token.is_empty() || sender.is_empty() {
        return Ok(false); // not configured; caller tries SMTP
    }

    let http = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(20))
        .build()?;

    // Exchange the stored refresh token for a short-lived access token.
    let token_resp = http
        .post("https://oauth2.googleapis.com/token")
        .form(&[
            ("client_id",     client_id.as_str()),
            ("client_secret", client_secret.as_str()),
            ("refresh_token", refresh_token.as_str()),
            ("grant_type",    "refresh_token"),
        ])
        .send().await?
        .json::<serde_json::Value>().await?;

    let access_token = token_resp["access_token"]
        .as_str()
        .ok_or_else(|| anyhow::anyhow!("Gmail token refresh failed: {token_resp}"))?;

    // Gmail API expects a raw RFC 2822 message, base64url-encoded (no padding).
    let raw = format!(
        "From: {sender}\r\nTo: {to}\r\nSubject: {subject}\r\n\
         Content-Type: text/plain; charset=utf-8\r\n\r\n{body}"
    );
    let encoded = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .encode(raw.as_bytes());

    let resp = http
        .post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send")
        .bearer_auth(access_token)
        .json(&serde_json::json!({ "raw": encoded }))
        .send().await?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        anyhow::bail!("Gmail API {status}: {text}");
    }

    tracing::info!(%to, %subject, "sent via Gmail API");
    Ok(true)
}

/// Send a short plain-text message through the same SMTP settings.
///
/// Used by the passkey features (recovery link, "a passkey was added" notice).
/// With no outbox configured it logs ONLY that nothing was sent -- never the
/// body, because for a recovery link the body is a working credential and must
/// not land in a log file. `Ok(false)` means nothing was sent.
pub async fn send_text(to: &str, subject: &str, body: &str) -> anyhow::Result<bool> {
    // Gmail API takes priority; fall through to SMTP if not configured.
    if try_gmail(to, subject, body).await? {
        return Ok(true);
    }

    let host = common::env_or("SMTP_HOST", "");
    let user = common::env_or("SMTP_USER", "");
    let password = common::env_or("SMTP_PASSWORD", "");
    let from = common::env_or("MAIL_FROM", &user);
    if host.trim().is_empty() || user.trim().is_empty() || password.trim().is_empty() {
        tracing::warn!(%to, %subject, "no outbox configured (GMAIL_CLIENT_ID or SMTP_HOST, SMTP_USER, SMTP_PASSWORD); message not sent");
        return Ok(false);
    }
    let from_name = common::env_or("MAIL_FROM_NAME", "Jason Gibson");
    let message = Message::builder()
        .from(format!("{from_name} <{from}>").parse()?)
        .to(to.parse()?)
        .subject(subject)
        .header(ContentType::TEXT_PLAIN)
        .body(body.to_string())?;
    let mailer: AsyncSmtpTransport<Tokio1Executor> =
        AsyncSmtpTransport::<Tokio1Executor>::starttls_relay(&host)?
            .port(common::env_or("SMTP_PORT", "587").parse().unwrap_or(587))
            .credentials(Credentials::new(user, password))
            .timeout(Some(std::time::Duration::from_secs(20)))
            .build();
    mailer.send(message).await?;
    Ok(true)
}

/// Deliver a reset code. `Ok(true)` means a provider actually sent it;
/// `Ok(false)` means it was logged instead because no outbox is configured.
pub async fn send_reset_code(to: &str, code: &str) -> anyhow::Result<bool> {
    let subject = "Your password reset code";
    let body = format!(
        "Your code to reset your password is:\n\n    {code}\n\n\
         It works once, and expires in 15 minutes.\n\n\
         If you did not ask to reset your password, you can ignore this \
         message — nothing has changed, and whoever asked does not have \
         access to your account.\n"
    );

    // Gmail API takes priority; fall through to SMTP if not configured.
    if try_gmail(to, subject, &body).await? {
        return Ok(true);
    }

    let host = common::env_or("SMTP_HOST", "");
    let user = common::env_or("SMTP_USER", "");
    let password = common::env_or("SMTP_PASSWORD", "");
    let from = common::env_or("MAIL_FROM", &user);

    if host.trim().is_empty() || user.trim().is_empty() || password.trim().is_empty() {
        // See the module note above for why this is a warning and not an error.
        tracing::warn!(
            %to,
            "NO OUTBOX CONFIGURED (set SMTP_HOST, SMTP_USER, SMTP_PASSWORD). \
             Reset code for {to} is {code} — valid 15 minutes, single use."
        );
        return Ok(false);
    }

    let from_name = common::env_or("MAIL_FROM_NAME", "Jason Gibson");

    let message = Message::builder()
        .from(format!("{from_name} <{from}>").parse()?)
        .to(to.parse()?)
        .subject(subject)
        .header(ContentType::TEXT_PLAIN)
        .body(body)?;

    // STARTTLS on 587 rather than implicit TLS on 465. Both are fine and both
    // are encrypted; 587 is the submission port every provider documents first,
    // so it is the one whose settings a person can look up when this breaks.
    let mailer: AsyncSmtpTransport<Tokio1Executor> =
        AsyncSmtpTransport::<Tokio1Executor>::starttls_relay(&host)?
            .port(common::env_or("SMTP_PORT", "587").parse().unwrap_or(587))
            .credentials(Credentials::new(user, password))
            .timeout(Some(std::time::Duration::from_secs(20)))
            .build();

    mailer.send(message).await?;
    Ok(true)
}

/// Feedback or a request for help, from somebody using one of the apps.
///
/// WHO IT CLAIMS TO BE FROM. The envelope sender is always the household
/// account, never the person writing -- Gmail would rewrite a forged From
/// anyway, and an address that cannot be authenticated is the shape of a
/// phishing mail. Their identity goes in the SUBJECT and the body instead,
/// where it is information rather than a claim, and Reply-To is set to them
/// so hitting reply reaches the right person.
///
/// THE EMAIL ADDRESS IS NOT TAKEN FROM THE REQUEST. The caller supplies the
/// message; the auth service supplies who sent it, from the validated token.
/// Otherwise "support" becomes a way for any signed-in phone to send mail
/// that appears to come from somebody else.
pub async fn send_support(
    from_person: &str,
    display: &str,
    kind: &str,
    body_text: &str,
) -> anyhow::Result<bool> {
    let host = common::env_or("SMTP_HOST", "");
    let user = common::env_or("SMTP_USER", "");
    let password = common::env_or("SMTP_PASSWORD", "");
    let from = common::env_or("MAIL_FROM", &user);
    // Where support lands. Defaults to the sending account rather than to a
    // hardcoded address, so this works on a fresh setup without editing code.
    let to = common::env_or("SUPPORT_TO", &from);

    if host.trim().is_empty() || user.trim().is_empty() || password.trim().is_empty() {
        // Logged rather than lost: without this, a person is told their
        // message was sent and it simply was not.
        tracing::warn!(
            %from_person,
            "NO OUTBOX CONFIGURED -- support message from {from_person} ({kind}) \
             was NOT sent. Body: {body_text}"
        );
        return Ok(false);
    }

    let from_name = common::env_or("MAIL_FROM_NAME", "Jason Gibson");

    let body = format!(
        "{display} <{from_person}> sent this from the app.\n\n\
         Kind: {kind}\n\
         ----------------------------------------------------------------\n\n\
         {body_text}\n\n\
         ----------------------------------------------------------------\n\
         Reply to this message to answer them directly.\n"
    );

    let message = Message::builder()
        .from(format!("{from_name} <{from}>").parse()?)
        // Reply goes to the person who wrote, not to the household account.
        .reply_to(from_person.parse()?)
        .to(to.parse()?)
        .subject(format!("[Cut The Fluff] {kind} from {display}"))
        .header(ContentType::TEXT_PLAIN)
        .body(body)?;

    let mailer: AsyncSmtpTransport<Tokio1Executor> =
        AsyncSmtpTransport::<Tokio1Executor>::starttls_relay(&host)?
            .port(common::env_or("SMTP_PORT", "587").parse().unwrap_or(587))
            .credentials(Credentials::new(user, password))
            .timeout(Some(std::time::Duration::from_secs(20)))
            .build();

    mailer.send(message).await?;
    Ok(true)
}
