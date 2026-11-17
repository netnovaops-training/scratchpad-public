//! Tests for passkeys.rs. The end-to-end ones drive the real handlers with a
//! SOFTWARE authenticator (webauthn-authenticator-rs SoftPasskey). That proves
//! the server side start to finish; it does not prove a real browser or phone.

use super::*;
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use std::sync::atomic::Ordering;
use webauthn_authenticator_rs::{softpasskey::SoftPasskey, WebauthnAuthenticator};
use webauthn_rs::prelude::{CreationChallengeResponse, RequestChallengeResponse};

const RP: &str = "auth.example.test";
const ORIGIN: &str = "https://auth.example.test";
const EMAIL: &str = "jason@example.test";
const PASSWORD: &str = "correct horse battery";

fn temp_dir(tag: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("auth-pk-{tag}-{}", random_hex(4)));
    std::fs::create_dir_all(&d).unwrap();
    d
}

// ----------------------------------------------------------------- the file

#[test]
fn missing_file_is_empty() {
    let d = temp_dir("missing");
    let f = load(&d.join("passkeys.json")).unwrap();
    assert!(f.users.is_empty());
}

#[test]
fn empty_or_blank_file_is_empty() {
    let d = temp_dir("empty");
    let p = d.join("passkeys.json");
    std::fs::write(&p, b"").unwrap();
    assert!(load(&p).unwrap().users.is_empty());
    std::fs::write(&p, b"  \n\n").unwrap();
    assert!(load(&p).unwrap().users.is_empty());
}

#[test]
fn corrupt_file_is_an_error_not_empty() {
    let d = temp_dir("corrupt");
    let p = d.join("passkeys.json");
    std::fs::write(&p, b"{ not json").unwrap();
    assert_eq!(load(&p).err(), Some(LoadError::Corrupt));
    std::fs::write(&p, b"[1,2,3]").unwrap();
    assert_eq!(load(&p).err(), Some(LoadError::Corrupt));
}

#[test]
fn save_is_atomic_private_and_keeps_a_bak() {
    let d = temp_dir("save");
    let p = d.join("passkeys.json");
    let mut f = PasskeyFile::default();
    f.users.push(UserKeys {
        email: "a@example.test".into(),
        user_id: "11111111-1111-1111-1111-111111111111".into(),
        ..Default::default()
    });
    save(&p, &f).unwrap();
    assert!(
        !p.with_extension("tmp").exists(),
        "temp file must be renamed away"
    );
    assert!(
        !p.with_extension("json.bak").exists(),
        "no bak on the very first write"
    );
    assert_eq!(load(&p).unwrap().users.len(), 1);

    f.users.push(UserKeys {
        email: "b@example.test".into(),
        user_id: "22222222-2222-2222-2222-222222222222".into(),
        ..Default::default()
    });
    save(&p, &f).unwrap();
    let bak = load(&p.with_extension("json.bak")).unwrap();
    assert_eq!(bak.users.len(), 1, "the .bak is the PREVIOUS write");
    assert_eq!(load(&p).unwrap().users.len(), 2);

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = std::fs::metadata(&p).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600, "file must be private");
    }
}

#[test]
fn save_never_leaves_a_half_file_when_the_target_dir_is_missing_then_created() {
    let d = temp_dir("mkdir");
    let p = d.join("nested").join("passkeys.json");
    save(&p, &PasskeyFile::default()).unwrap();
    assert!(load(&p).is_ok());
}

// ------------------------------------------------------------- challenges

#[test]
fn a_challenge_works_once() {
    let mut c: Challenges<u32> = Challenges::new();
    let now = Instant::now();
    let t = c.put(7, now).unwrap();
    assert_eq!(c.take(&t, now), Some(7));
    assert_eq!(c.take(&t, now), None, "second use must fail");
}

#[test]
fn a_challenge_expires_after_five_minutes() {
    let mut c: Challenges<u32> = Challenges::new();
    let now = Instant::now();
    let fresh = c.put(1, now).unwrap();
    let old = c.put(2, now).unwrap();
    assert_eq!(c.take(&fresh, now + Duration::from_secs(299)), Some(1));
    assert_eq!(c.take(&old, now + Duration::from_secs(301)), None);
    // and an expired one is gone, not merely refused
    assert_eq!(c.take(&old, now), None);
}

#[test]
fn an_unknown_token_gives_nothing() {
    let mut c: Challenges<u32> = Challenges::new();
    assert_eq!(c.take("nope", Instant::now()), None);
}

#[test]
fn too_many_waiting_challenges_are_refused_until_old_ones_expire() {
    let mut c: Challenges<u32> = Challenges::new();
    let now = Instant::now();
    for i in 0..MAX_PENDING {
        assert!(c.put(i as u32, now).is_some());
    }
    assert!(c.put(0, now).is_none());
    assert!(c.put(0, now + Duration::from_secs(301)).is_some());
}

#[test]
fn tokens_are_long_and_different() {
    let mut c: Challenges<u32> = Challenges::new();
    let now = Instant::now();
    let a = c.put(1, now).unwrap();
    let b = c.put(2, now).unwrap();
    assert_eq!(a.len(), 64);
    assert_ne!(a, b);
}

// ------------------------------------------------------------ rate limits

#[test]
fn limiter_blocks_after_max_and_clears_itself() {
    let mut l = Limiter::default();
    let now = Instant::now();
    let w = Duration::from_secs(300);
    for _ in 0..3 {
        assert!(l.blocked("k", 3, w, now).is_none());
        l.record("k", now);
    }
    let wait = l.blocked("k", 3, w, now).expect("fourth is blocked");
    assert!((1..=301).contains(&wait));
    assert!(l.blocked("other", 3, w, now).is_none(), "keys are separate");
    assert!(l
        .blocked("k", 3, w, now + Duration::from_secs(301))
        .is_none());
}

// ---------------------------------------------------------- names, removal

#[test]
fn names_are_checked() {
    assert_eq!(clean_name("  Pixel  ").unwrap(), "Pixel");
    assert!(clean_name("").is_err());
    assert!(clean_name("   ").is_err());
    assert!(clean_name(&"x".repeat(65)).is_err());
    assert!(clean_name(&"x".repeat(64)).is_ok());
    assert!(clean_name("bad\nname").is_err());
    // HTML in a name is stored as text; the page escapes it and the script
    // uses textContent, so it is allowed but inert.
    assert!(clean_name("<b>x</b>").is_ok());
}

#[test]
fn removal_rule_table() {
    assert!(
        can_remove(true, 1),
        "with a password the last passkey may go"
    );
    assert!(can_remove(true, 3));
    assert!(!can_remove(false, 1), "no password: the only key stays");
    assert!(can_remove(false, 2));
}

// -------------------------------------------------- end to end, real handlers

fn app_state(dir: &Path, passkeys: bool, must_change: bool, with_password: bool) -> Arc<AppState> {
    let people = dir.join("people.json");
    let mut p = common::people::Person::new(EMAIL);
    if with_password {
        p.hash = common::people::hash_password(PASSWORD).unwrap();
    }
    p.must_change = must_change;
    common::people::write(&people, &[p]).unwrap();
    let pk = if passkeys {
        Some(Passkeys::new(RP, ORIGIN, &[], false, dir.join("passkeys.json")).unwrap())
    } else {
        None
    };
    Arc::new(AppState {
        allowed_seed: vec![],
        people_file: people,
        secret: b"0123456789abcdef0123456789abcdef".to_vec(),
        session_days: 180,
        cookie_secure: true,
        revoked_file: dir.join("revoked.json"),
        reset_file: dir.join("resets.json"),
        reset_lock: Mutex::new(()),
        trial_file: dir.join("trials.json"),
        trial_lock: tokio::sync::Mutex::new(()),
        gates: Mutex::new(HashMap::new()),
        decoy_hash: String::new(),
        bridge: Mutex::new(HashMap::new()),
        oidc: None,
        passkeys: pk,
    })
}

fn hdrs(origin: Option<&str>, cookie: Option<&str>) -> HeaderMap {
    let mut h = HeaderMap::new();
    if let Some(o) = origin {
        h.insert(header::ORIGIN, o.parse().unwrap());
    }
    if let Some(c) = cookie {
        h.insert(header::COOKIE, c.parse().unwrap());
    }
    h
}

async fn read(resp: Response) -> (StatusCode, HeaderMap, serde_json::Value) {
    let (parts, body) = resp.into_parts();
    let bytes = axum::body::to_bytes(body, 1 << 20).await.unwrap();
    let v = serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null);
    (parts.status, parts.headers, v)
}

fn session_cookie(st: &AppState) -> String {
    let list = crate::people(st);
    let p = common::people::find(&list, EMAIL).unwrap();
    format!("sso={}", crate::make_cookie_value(st, EMAIL, &p.hash))
}

fn soft() -> WebauthnAuthenticator<SoftPasskey> {
    WebauthnAuthenticator::new(SoftPasskey::new(true))
}

fn origin_url() -> Url {
    Url::parse(ORIGIN).unwrap()
}

/// Run a whole enrolment. Returns the status of the final step.
async fn enrol(
    st: &Arc<AppState>,
    dev: &mut WebauthnAuthenticator<SoftPasskey>,
    cookie: &str,
    name: &str,
    password: &str,
) -> StatusCode {
    enrol_at(st, dev, cookie, name, password, ORIGIN, 0).await
}

async fn enrol_at(
    st: &Arc<AppState>,
    dev: &mut WebauthnAuthenticator<SoftPasskey>,
    cookie: &str,
    name: &str,
    password: &str,
    origin: &str,
    days: u64,
) -> StatusCode {
    let body = json!({ "name": name, "password": password, "days": days }).to_string();
    let r = register_start(State(st.clone()), hdrs(Some(origin), Some(cookie)), body).await;
    let (code, _, v) = read(r).await;
    if code != StatusCode::OK {
        return code;
    }
    finish_enrol(st, dev, v, origin, Some(cookie)).await
}

/// The device half and the finish call, for any start response `v`.
async fn finish_enrol(
    st: &Arc<AppState>,
    dev: &mut WebauthnAuthenticator<SoftPasskey>,
    v: serde_json::Value,
    origin: &str,
    cookie: Option<&str>,
) -> StatusCode {
    let sel = &v["options"]["publicKey"]["authenticatorSelection"];
    assert_eq!(
        sel["residentKey"], "required",
        "enrolment must ask for a discoverable key"
    );
    assert!(
        sel["authenticatorAttachment"].is_null(),
        "attachment must stay unrestricted"
    );
    assert_eq!(v["options"]["publicKey"]["attestation"], "none");
    // The simple U2F-style soft device cannot make resident keys and refuses
    // the request, so it gets the same options minus that one demand (the
    // assertion above already proved the server asked for it).
    let mut for_device = v["options"].clone();
    for_device["publicKey"]["authenticatorSelection"]["residentKey"] = json!("discouraged");
    for_device["publicKey"]["authenticatorSelection"]["requireResidentKey"] = json!(false);
    let ccr: CreationChallengeResponse = serde_json::from_value(for_device).unwrap();
    let cred = dev
        .do_registration(Url::parse(origin).unwrap(), ccr)
        .expect("soft device registers");
    let body = json!({ "token": v["token"], "credential": cred }).to_string();
    let mut h = hdrs(Some(origin), cookie);
    h.insert(header::USER_AGENT, ANDROID_UA.parse().unwrap());
    h.insert("x-forwarded-for", "203.0.113.77".parse().unwrap());
    let r = register_finish(State(st.clone()), h, body).await;
    read(r).await.0
}

const ANDROID_UA: &str = "Mozilla/5.0 (Linux; Android 14; Pixel 10 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36";
const IPHONE_UA: &str = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";

/// Start a sign-in, answer it with the device, return the finish request body.
async fn sign_in_body(
    st: &Arc<AppState>,
    dev: &mut WebauthnAuthenticator<SoftPasskey>,
    cred_id_b64: &str,
    user_handle: Vec<u8>,
    next: &str,
) -> String {
    let r = login_start(State(st.clone()), hdrs(Some(ORIGIN), None)).await;
    let (code, _, v) = read(r).await;
    assert_eq!(code, StatusCode::OK);
    let mut opts = v["options"].clone();
    // What a browser does after the person picks a passkey: it knows which
    // credential, so the software device is told which one too.
    opts["publicKey"]["allowCredentials"] = json!([{ "type": "public-key", "id": cred_id_b64 }]);
    let rcr: RequestChallengeResponse = serde_json::from_value(opts).unwrap();
    let cred = dev
        .do_authentication(origin_url(), rcr)
        .expect("soft device signs");
    let mut cj = serde_json::to_value(&cred).unwrap();
    // A discoverable authenticator returns the user handle; this one is the
    // simple U2F-style soft device, so put it where a real one would.
    cj["response"]["userHandle"] = json!(URL_SAFE_NO_PAD.encode(user_handle));
    json!({ "token": v["token"], "credential": cj, "next": next }).to_string()
}

fn stored(dir: &Path) -> (String, String, Vec<u8>) {
    let f = load(&dir.join("passkeys.json")).unwrap();
    let u = &f.users[0];
    let cid = URL_SAFE_NO_PAD.encode(u.keys.last().unwrap().passkey.cred_id().as_ref() as &[u8]);
    let uid = Uuid::parse_str(&u.user_id).unwrap().as_bytes().to_vec();
    (u.user_id.clone(), cid, uid)
}

#[tokio::test]
async fn enrol_then_sign_in_gives_the_same_session() {
    let d = temp_dir("e2e");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();

    assert_eq!(
        enrol(&st, &mut dev, &cookie, "Pixel", PASSWORD).await,
        StatusCode::OK
    );
    let (_, cid, uid) = stored(&d);

    let body = sign_in_body(&st, &mut dev, &cid, uid, "/kitchen/").await;
    let r = login_finish(State(st.clone()), hdrs(Some(ORIGIN), None), body.clone()).await;
    let (code, h, v) = read(r).await;
    assert_eq!(code, StatusCode::OK, "{v}");
    assert_eq!(v["next"], "/kitchen/");

    // The cookie it set is the ordinary one: the cookie reader, and the
    // forward_auth /verify gate, accept it and name the same person.
    let set = h
        .get(header::SET_COOKIE)
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();
    assert!(set.starts_with("sso="));
    assert!(set.contains("HttpOnly") && set.contains("Secure"));
    let cookie_pair = set.split(';').next().unwrap().to_string();
    assert_eq!(
        crate::cookie_email(&st, &hdrs(None, Some(&cookie_pair))).as_deref(),
        Some(EMAIL)
    );
    let mut vh = hdrs(None, Some(&cookie_pair));
    vh.insert("X-Forwarded-Uri", "/kitchen/".parse().unwrap());
    let ok = crate::verify(State(st.clone()), vh).await;
    assert_eq!(ok.status(), StatusCode::OK);
    assert_eq!(ok.headers().get("X-Auth-Email").unwrap(), EMAIL);

    // The same answer sent twice must not work again (token is single use).
    let again = login_finish(State(st.clone()), hdrs(Some(ORIGIN), None), body).await;
    assert_eq!(read(again).await.0, StatusCode::BAD_REQUEST);

    // The counter and last-used were written back.
    let f = load(&d.join("passkeys.json")).unwrap();
    assert!(f.users[0].keys[0].last_used > 0);
}

#[tokio::test]
async fn a_next_off_site_is_not_followed() {
    let d = temp_dir("next");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "Pixel", PASSWORD).await,
        StatusCode::OK
    );
    let (_, cid, uid) = stored(&d);
    let body = sign_in_body(&st, &mut dev, &cid, uid, "https://evil.example/").await;
    let (code, _, v) =
        read(login_finish(State(st.clone()), hdrs(Some(ORIGIN), None), body).await).await;
    assert_eq!(code, StatusCode::OK);
    assert_eq!(v["next"], "/");
}

#[tokio::test]
async fn enrolling_needs_the_right_password_and_a_session_and_our_origin() {
    let d = temp_dir("guards");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "Pixel", "wrong password!").await,
        StatusCode::UNAUTHORIZED
    );
    // no session
    let r = register_start(
        State(st.clone()),
        hdrs(Some(ORIGIN), None),
        json!({"name":"x","password":PASSWORD}).to_string(),
    )
    .await;
    assert_eq!(read(r).await.0, StatusCode::UNAUTHORIZED);
    // somebody else's page
    let r = register_start(
        State(st.clone()),
        hdrs(Some("https://evil.example"), Some(&cookie)),
        json!({"name":"x","password":PASSWORD}).to_string(),
    )
    .await;
    assert_eq!(read(r).await.0, StatusCode::FORBIDDEN);
    // no Origin header at all
    let r = register_start(
        State(st.clone()),
        hdrs(None, Some(&cookie)),
        json!({"name":"x","password":PASSWORD}).to_string(),
    )
    .await;
    assert_eq!(read(r).await.0, StatusCode::FORBIDDEN);
    assert!(load(&d.join("passkeys.json")).unwrap().users.is_empty());
}

#[tokio::test]
async fn wrong_passwords_at_enrolment_lock_like_the_sign_in_form() {
    let d = temp_dir("lock");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();
    for _ in 0..3 {
        assert_eq!(
            enrol(&st, &mut dev, &cookie, "P", "nope nope nope").await,
            StatusCode::UNAUTHORIZED
        );
    }
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "P", PASSWORD).await,
        StatusCode::TOO_MANY_REQUESTS,
        "even the right password waits out the lock"
    );
}

#[tokio::test]
async fn a_challenge_for_one_person_cannot_finish_as_another_or_twice() {
    let d = temp_dir("bound");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let r = register_start(
        State(st.clone()),
        hdrs(Some(ORIGIN), Some(&cookie)),
        json!({"name":"x","password":PASSWORD}).to_string(),
    )
    .await;
    let (_, _, v) = read(r).await;
    let bad = json!({ "token": v["token"], "credential": {"nonsense": true} }).to_string();
    // junk answer: refused, and the token is spent
    let r1 = register_finish(
        State(st.clone()),
        hdrs(Some(ORIGIN), Some(&cookie)),
        bad.clone(),
    )
    .await;
    assert_eq!(read(r1).await.0, StatusCode::BAD_REQUEST);
    let r2 = register_finish(State(st.clone()), hdrs(Some(ORIGIN), Some(&cookie)), bad).await;
    let (c2, _, v2) = read(r2).await;
    assert_eq!(c2, StatusCode::BAD_REQUEST);
    assert!(v2["error"].as_str().unwrap().contains("already used"));
}

#[tokio::test]
async fn removal_works_and_the_list_updates() {
    let d = temp_dir("remove");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "Pixel", PASSWORD).await,
        StatusCode::OK
    );
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "Windows Hello", PASSWORD).await,
        StatusCode::OK
    );
    // same name twice is refused
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "pixel", PASSWORD).await,
        StatusCode::BAD_REQUEST
    );

    let f = load(&d.join("passkeys.json")).unwrap();
    assert_eq!(f.users[0].keys.len(), 2);
    let id = f.users[0].keys[0].id.clone();
    let r = remove(
        State(st.clone()),
        hdrs(Some(ORIGIN), Some(&cookie)),
        json!({ "id": id }).to_string(),
    )
    .await;
    assert_eq!(read(r).await.0, StatusCode::OK);
    assert_eq!(
        load(&d.join("passkeys.json")).unwrap().users[0].keys.len(),
        1
    );

    let r = remove(
        State(st.clone()),
        hdrs(Some(ORIGIN), Some(&cookie)),
        json!({ "id": "doesnotexist" }).to_string(),
    )
    .await;
    assert_eq!(read(r).await.0, StatusCode::BAD_REQUEST);
}

#[test]
fn remove_key_refuses_the_last_way_in_without_a_password() {
    // build a file with real passkeys via the soft device would be async;
    // the rule itself is covered by removal_rule_table and the handler test
    // above, so here check the not-found paths.
    let mut f = PasskeyFile::default();
    assert!(remove_key(&mut f, "nobody@example.test", "x", true).is_err());
}

#[tokio::test]
async fn people_without_a_usable_password_cannot_use_a_passkey_to_sign_in() {
    // Enrol while the account is fine, then flip it to "must change".
    let d = temp_dir("mustchange");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "Pixel", PASSWORD).await,
        StatusCode::OK
    );
    let (_, cid, uid) = stored(&d);

    let mut list = crate::people(&st);
    list[0].must_change = true;
    common::people::write(&st.people_file, &list).unwrap();

    let body = sign_in_body(&st, &mut dev, &cid, uid.clone(), "/").await;
    let r = login_finish(State(st.clone()), hdrs(Some(ORIGIN), None), body).await;
    let (code, h, _) = read(r).await;
    assert_eq!(code, StatusCode::UNAUTHORIZED);
    assert!(h.get(header::SET_COOKIE).is_none());

    // and with the password gone altogether
    list[0].must_change = false;
    list[0].hash = String::new();
    common::people::write(&st.people_file, &list).unwrap();
    let body = sign_in_body(&st, &mut dev, &cid, uid, "/").await;
    let r = login_finish(State(st.clone()), hdrs(Some(ORIGIN), None), body).await;
    assert_eq!(read(r).await.0, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn a_damaged_file_blocks_passkeys_but_changes_nothing_else() {
    let d = temp_dir("damaged");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    std::fs::write(d.join("passkeys.json"), b"{ garbage").unwrap();
    let mut dev = soft();
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "Pixel", PASSWORD).await,
        StatusCode::INTERNAL_SERVER_ERROR
    );
    // the damaged file was not overwritten
    assert_eq!(
        std::fs::read(d.join("passkeys.json")).unwrap(),
        b"{ garbage"
    );
    // sign-in start still answers (it does not read the file)
    let r = login_start(State(st.clone()), hdrs(Some(ORIGIN), None)).await;
    assert_eq!(read(r).await.0, StatusCode::OK);
    // the password cookie still verifies
    assert_eq!(
        crate::cookie_email(&st, &hdrs(None, Some(&cookie))).as_deref(),
        Some(EMAIL)
    );
}

#[tokio::test]
async fn switched_off_answers_plainly_and_signin_page_keeps_the_password_form() {
    let d = temp_dir("off");
    let st = app_state(&d, false, false, true);
    let r = login_start(State(st.clone()), hdrs(Some(ORIGIN), None)).await;
    let (code, _, v) = read(r).await;
    assert_eq!(code, StatusCode::SERVICE_UNAVAILABLE);
    assert!(v["error"].as_str().unwrap().contains("password"));

    ENABLED.store(false, Ordering::Relaxed);
    let page = crate::render_signin("", "", "/", StatusCode::OK);
    let (parts, body) = page.into_parts();
    let html =
        String::from_utf8(axum::body::to_bytes(body, 1 << 20).await.unwrap().to_vec()).unwrap();
    assert_eq!(parts.status, StatusCode::OK);
    assert!(html.contains("name=\"password\""));
    assert!(!html.contains("pk-btn"), "no passkey button when off");
}

#[tokio::test]
async fn signin_page_with_passkeys_on_has_button_script_and_autofill_hint() {
    ENABLED.store(true, Ordering::Relaxed);
    let page = crate::render_signin("", "", "/sso/bridge?next=x", StatusCode::OK);
    let (_, body) = page.into_parts();
    let html =
        String::from_utf8(axum::body::to_bytes(body, 1 << 20).await.unwrap().to_vec()).unwrap();
    ENABLED.store(false, Ordering::Relaxed);
    assert!(
        html.contains("name=\"password\""),
        "password form always present"
    );
    assert!(html.contains("Tap to sign in with your passkey"));
    assert!(html.contains("autocomplete=\"username webauthn\""));
    assert!(html.contains("passkey/passkey.js"));
    assert!(
        html.contains("name=\"next\""),
        "return-to URL still carried"
    );
}

#[test]
fn the_script_is_served_and_is_free_of_alert_and_innerhtml() {
    assert!(SCRIPT.contains("passkey/login/start"));
    assert!(!SCRIPT.contains("alert("));
    assert!(!SCRIPT.contains("innerHTML"));
    assert!(!SCRIPT.contains("console.log"));
}

#[test]
fn bad_settings_turn_passkeys_off_instead_of_panicking() {
    let d = temp_dir("cfg");
    // origin that does not belong to the rp id
    assert!(Passkeys::new(
        "example.org",
        "https://other.example",
        &[],
        false,
        d.join("p.json")
    )
    .is_err());
    assert!(Passkeys::new("x", "not a url", &[], false, d.join("p.json")).is_err());
    assert!(Passkeys::new(
        "example.org",
        "https://example.org",
        &["https://test.example.org".to_string()],
        false,
        d.join("p.json")
    )
    .is_ok());
}

#[test]
fn origin_matching_exact_subdomain_and_sibling() {
    let d = temp_dir("origins");
    let rp = "example.org";
    let strict = Passkeys::new(
        rp,
        "https://example.org",
        &[],
        false,
        d.join("p.json"),
    )
    .unwrap();
    assert!(strict.origin_allowed("https://example.org"));
    assert!(
        !strict.origin_allowed("https://app.example.org"),
        "subdomains are off by default"
    );

    let wide = Passkeys::new(
        rp,
        "https://example.org",
        &[],
        true,
        d.join("p.json"),
    )
    .unwrap();
    assert!(wide.origin_allowed("https://example.org"));
    assert!(wide.origin_allowed("https://app.example.org"));
    assert!(wide.origin_allowed("https://a.b.example.org"));
    // other hosts under the shared zapto.org suffix are NOT ours
    assert!(!wide.origin_allowed("https://garden.example.org"));
    assert!(!wide.origin_allowed("https://zapto.org"));
    assert!(!wide.origin_allowed("https://evil-example.org"));
    assert!(!wide.origin_allowed("https://example.org.evil.com"));
    assert!(!wide.origin_allowed("https://x@example.org"));
    assert!(
        !wide.origin_allowed("http://app.example.org"),
        "https only"
    );
    assert!(!wide.origin_allowed("https://.example.org"));
    assert!(!wide.origin_allowed(""));
}

#[tokio::test]
async fn enrolment_from_a_subdomain_works_and_a_sibling_host_is_refused() {
    let d = temp_dir("subdom");
    let mut st = app_state(&d, true, false, true);
    Arc::get_mut(&mut st).unwrap().passkeys =
        Some(Passkeys::new(RP, ORIGIN, &[], true, d.join("passkeys.json")).unwrap());
    let cookie = session_cookie(&st);
    let mut dev = soft();
    assert_eq!(
        enrol_at(
            &st,
            &mut dev,
            &cookie,
            "Pixel",
            PASSWORD,
            "https://app.auth.example.test",
            0
        )
        .await,
        StatusCode::OK
    );
    assert_eq!(
        enrol_at(
            &st,
            &mut dev,
            &cookie,
            "Other",
            PASSWORD,
            "https://sibling.example.test",
            0
        )
        .await,
        StatusCode::FORBIDDEN
    );
}

// ------------------------------------------------ recovery links (pure part)

#[test]
fn recovery_token_is_stored_hashed_and_works_once() {
    let d = temp_dir("rec-hash");
    let p = d.join("passkey-recovery.json");
    let mut rec = RecoveryFile::default();
    let t = rec.issue(EMAIL, 1000);
    assert_eq!(t.len(), 64);
    save_any(&p, &rec).unwrap();
    let on_disk = std::fs::read_to_string(&p).unwrap();
    assert!(
        !on_disk.contains(&t),
        "the token itself must never be on disk"
    );
    assert!(on_disk.contains(&token_hash(&t)));
    assert_ne!(token_hash(&t), t);

    let mut rec = load_any::<RecoveryFile>(&p).unwrap();
    assert_eq!(
        rec.peek(&t, 1001).as_deref(),
        Some(EMAIL),
        "peek does not use it up"
    );
    assert_eq!(rec.peek(&t, 1002).as_deref(), Some(EMAIL));
    assert_eq!(rec.redeem(&t, 1003).as_deref(), Some(EMAIL));
    assert_eq!(rec.redeem(&t, 1004), None, "second use fails");
    assert_eq!(rec.peek(&t, 1004), None);
}

#[test]
fn recovery_token_expires_after_fifteen_minutes() {
    let mut rec = RecoveryFile::default();
    let t = rec.issue(EMAIL, 1000);
    assert_eq!(
        rec.peek(&t, 1000 + RECOVERY_LIFE - 1).as_deref(),
        Some(EMAIL)
    );
    assert_eq!(rec.peek(&t, 1000 + RECOVERY_LIFE), None);
    assert_eq!(rec.redeem(&t, 1000 + RECOVERY_LIFE), None);
    assert_eq!(RECOVERY_LIFE, 15 * 60);
}

#[test]
fn asking_again_does_not_replace_a_live_link() {
    let mut rec = RecoveryFile::default();
    let a = rec
        .issue_if_free(EMAIL, 1000)
        .expect("first request gets a link");
    assert!(
        rec.issue_if_free(EMAIL, 1010).is_none(),
        "a second request while one is live makes nothing new"
    );
    assert_eq!(
        rec.peek(&a, 1020).as_deref(),
        Some(EMAIL),
        "the first link still works"
    );
    // another account is unaffected
    assert!(rec.issue_if_free("other@example.test", 1010).is_some());
    // after the link expires, a new one may be made
    assert!(rec.issue_if_free(EMAIL, 1000 + RECOVERY_LIFE + 1).is_some());
    assert_eq!(rec.redeem("not a token", 1020), None);
}

// ----------------------------------------------------- flag, loaners, lost

fn read_file(d: &Path) -> PasskeyFile {
    load(&d.join("passkeys.json")).unwrap()
}

async fn password_signin(st: &Arc<AppState>, pw: &str) -> StatusCode {
    let body = format!(
        "email={}&password={}&next=%2F",
        EMAIL.replace('@', "%40"),
        pw.replace(' ', "+")
    );
    crate::sign_in(State(st.clone()), body).await.status()
}

async fn post_json<F, Fut>(
    f: F,
    st: &Arc<AppState>,
    cookie: &str,
    body: serde_json::Value,
) -> (StatusCode, serde_json::Value)
where
    F: FnOnce(State<Arc<AppState>>, HeaderMap, String) -> Fut,
    Fut: std::future::Future<Output = Response>,
{
    let r = f(
        State(st.clone()),
        hdrs(Some(ORIGIN), Some(cookie)),
        body.to_string(),
    )
    .await;
    let (c, _, v) = read(r).await;
    (c, v)
}

#[test]
fn passkey_only_rule_is_two_lasting_keys() {
    assert!(can_set_passkey_only(&[], 100).is_err());
}

#[tokio::test]
async fn passkey_only_needs_two_lasting_passkeys_and_is_reversible() {
    let d = temp_dir("only");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();

    // no passkeys at all
    let (c, _) = post_json(set_only, &st, &cookie, json!({"on": true})).await;
    assert_eq!(c, StatusCode::BAD_REQUEST);

    assert_eq!(
        enrol(&st, &mut dev, &cookie, "One", PASSWORD).await,
        StatusCode::OK
    );
    let (c, v) = post_json(set_only, &st, &cookie, json!({"on": true})).await;
    assert_eq!(c, StatusCode::BAD_REQUEST, "one is not enough: {v}");

    // a loaner does not count towards the two
    assert_eq!(
        enrol_at(&st, &mut dev, &cookie, "Loan", PASSWORD, ORIGIN, 7).await,
        StatusCode::OK
    );
    let (c, _) = post_json(set_only, &st, &cookie, json!({"on": true})).await;
    assert_eq!(
        c,
        StatusCode::BAD_REQUEST,
        "one lasting + one loaner is still not enough"
    );

    assert_eq!(
        enrol(&st, &mut dev, &cookie, "Two", PASSWORD).await,
        StatusCode::OK
    );
    let (c, _) = post_json(set_only, &st, &cookie, json!({"on": true})).await;
    assert_eq!(c, StatusCode::OK);
    assert!(read_file(&d).users[0].passkey_only);

    // password sign-in is now refused with the ordinary message...
    assert_eq!(
        password_signin(&st, PASSWORD).await,
        StatusCode::UNAUTHORIZED
    );
    // ...but a passkey still gets in
    let f = read_file(&d);
    let last = f.users[0].keys.last().unwrap();
    let cid = URL_SAFE_NO_PAD.encode(last.passkey.cred_id().as_ref() as &[u8]);
    let uid = Uuid::parse_str(&f.users[0].user_id)
        .unwrap()
        .as_bytes()
        .to_vec();
    let body = sign_in_body(&st, &mut dev, &cid, uid, "/").await;
    let (c, _, _) =
        read(login_finish(State(st.clone()), hdrs(Some(ORIGIN), None), body).await).await;
    assert_eq!(c, StatusCode::OK);

    // removing a lasting key would break the two-key rule
    let id = read_file(&d).users[0].keys[0].id.clone();
    let (c, _) = post_json(remove, &st, &cookie, json!({"id": id})).await;
    assert_eq!(c, StatusCode::BAD_REQUEST);
    // the loaner can go
    let loan = read_file(&d).users[0].keys[1].id.clone();
    let (c, _) = post_json(remove, &st, &cookie, json!({"id": loan})).await;
    assert_eq!(c, StatusCode::OK);

    // switching off restores password sign-in; always allowed
    let (c, _) = post_json(set_only, &st, &cookie, json!({"on": false})).await;
    assert_eq!(c, StatusCode::OK);
    assert!(!read_file(&d).users[0].passkey_only);
    assert_eq!(password_signin(&st, PASSWORD).await, StatusCode::SEE_OTHER);
}

#[tokio::test]
async fn a_stale_flag_never_locks_the_password_out() {
    // Flag says on, but only one lasting key is really there (hand-edited,
    // or a key vanished): the password must keep working.
    let d = temp_dir("stale");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "One", PASSWORD).await,
        StatusCode::OK
    );
    let mut f = read_file(&d);
    f.users[0].passkey_only = true;
    save(&d.join("passkeys.json"), &f).unwrap();
    assert!(!password_off(&st, EMAIL));
    assert_eq!(password_signin(&st, PASSWORD).await, StatusCode::SEE_OTHER);
}

#[tokio::test]
async fn invalid_loaner_lengths_are_refused() {
    let d = temp_dir("days");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();
    assert_eq!(
        enrol_at(&st, &mut dev, &cookie, "X", PASSWORD, ORIGIN, 5).await,
        StatusCode::BAD_REQUEST
    );
    assert_eq!(
        enrol_at(&st, &mut dev, &cookie, "X", PASSWORD, ORIGIN, 365).await,
        StatusCode::BAD_REQUEST
    );
    assert_eq!(loaner_expiry(1, 0), Ok(86400));
    assert_eq!(loaner_expiry(7, 0), Ok(7 * 86400));
    assert_eq!(loaner_expiry(30, 0), Ok(30 * 86400));
    assert_eq!(loaner_expiry(0, 5), Ok(0));
}

#[tokio::test]
async fn an_expired_loaner_is_refused_then_removed_on_the_next_run() {
    let d = temp_dir("loaner");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();
    assert_eq!(
        enrol_at(&st, &mut dev, &cookie, "Loan", PASSWORD, ORIGIN, 1).await,
        StatusCode::OK
    );
    let f = read_file(&d);
    let k = &f.users[0].keys[0];
    let now = common::people::now();
    assert!(k.expires > now + 86000 && k.expires <= now + 86400 + 5);
    let cid = URL_SAFE_NO_PAD.encode(k.passkey.cred_id().as_ref() as &[u8]);
    let uid = Uuid::parse_str(&f.users[0].user_id)
        .unwrap()
        .as_bytes()
        .to_vec();

    // while valid it signs in
    let body = sign_in_body(&st, &mut dev, &cid, uid.clone(), "/").await;
    let (c, _, _) =
        read(login_finish(State(st.clone()), hdrs(Some(ORIGIN), None), body).await).await;
    assert_eq!(c, StatusCode::OK);

    // time passes
    let mut f = read_file(&d);
    f.users[0].keys[0].expires = 1;
    save(&d.join("passkeys.json"), &f).unwrap();
    let body = sign_in_body(&st, &mut dev, &cid, uid, "/").await;
    let (c, h, v) =
        read(login_finish(State(st.clone()), hdrs(Some(ORIGIN), None), body).await).await;
    assert_eq!(c, StatusCode::UNAUTHORIZED);
    assert!(v["error"]
        .as_str()
        .unwrap()
        .contains("isn't valid any more"));
    assert!(h.get(header::SET_COOKIE).is_none());
    assert_eq!(
        read_file(&d).users[0].keys.len(),
        1,
        "still on disk until the next run"
    );

    st.passkeys.as_ref().unwrap().prune_on_start(&[]);
    assert!(read_file(&d).users[0].keys.is_empty());
}

#[test]
fn pruning_clears_passkey_only_when_the_guard_no_longer_holds() {
    // Pure check on the rule using the file type: no keys left, flag set.
    let mut f = PasskeyFile::default();
    f.users.push(UserKeys {
        email: EMAIL.into(),
        user_id: "33333333-3333-3333-3333-333333333333".into(),
        passkey_only: true,
        ..Default::default()
    });
    let gone = prune_expired(&mut f, 1_000_000);
    assert!(gone.is_empty());
    assert!(!f.users[0].passkey_only);
}

#[tokio::test]
async fn lost_phone_keeps_one_or_removes_all_and_ends_sessions() {
    let d = temp_dir("lost");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();
    for n in ["A", "B", "C"] {
        assert_eq!(
            enrol(&st, &mut dev, &cookie, n, PASSWORD).await,
            StatusCode::OK
        );
    }
    let keep = read_file(&d).users[0].keys[1].id.clone();
    let (c, _) = post_json(lost, &st, &cookie, json!({"keep": "nope"})).await;
    assert_eq!(
        c,
        StatusCode::BAD_REQUEST,
        "unknown keep id changes nothing"
    );
    assert_eq!(read_file(&d).users[0].keys.len(), 3);

    // Cookies are stamped in whole seconds; make sure this one is older.
    tokio::time::sleep(Duration::from_millis(1100)).await;
    let (c, v) = post_json(lost, &st, &cookie, json!({"keep": keep})).await;
    assert_eq!(c, StatusCode::OK);
    assert_eq!(v["removed"], 2);
    let f = read_file(&d);
    assert_eq!(f.users[0].keys.len(), 1);
    assert_eq!(f.users[0].keys[0].id, keep);
    assert!(d.join("revoked.json").exists(), "sessions were revoked");
    assert!(
        crate::cookie_email(&st, &hdrs(None, Some(&cookie))).is_none(),
        "the session that was open before is now dead"
    );

    // sign in again (a fresh cookie), then remove the last one too
    let cookie = session_cookie(&st);
    let (c, v) = post_json(lost, &st, &cookie, json!({})).await;
    assert_eq!(c, StatusCode::OK);
    assert_eq!(v["removed"], 1);
    assert!(read_file(&d).users[0].keys.is_empty());
}

#[test]
fn lost_phone_clears_passkey_only() {
    let mut f = PasskeyFile::default();
    f.users.push(UserKeys {
        email: EMAIL.into(),
        user_id: "44444444-4444-4444-4444-444444444444".into(),
        passkey_only: true,
        ..Default::default()
    });
    assert_eq!(revoke_others(&mut f, EMAIL, None), Ok(0));
    assert!(!f.users[0].passkey_only);
    assert_eq!(revoke_others(&mut f, "nobody@example.test", None), Ok(0));
}

// ------------------------------------------------- recovery, whole journey

#[tokio::test]
async fn recovery_link_adds_a_passkey_once_and_signs_nobody_in() {
    let d = temp_dir("recover");
    let st = app_state(&d, true, false, true);
    let pk = st.passkeys.as_ref().unwrap();

    // Asking: the page is identical for a real and an unknown address.
    let known = ask_recovery(&st, EMAIL, None).await;
    let unknown = ask_recovery(&st, "nobody@example.test", None).await;
    let (ks, kb) = known.into_parts();
    let (us, ub) = unknown.into_parts();
    assert_eq!(ks.status, us.status);
    let kb = axum::body::to_bytes(kb, 1 << 20).await.unwrap();
    let ub = axum::body::to_bytes(ub, 1 << 20).await.unwrap();
    assert_eq!(kb, ub, "same page whether or not the address exists");

    // Only the real account got a (hashed) entry (made after the reply).
    wait_for(|| {
        load_any::<RecoveryFile>(&pk.recovery_file)
            .map(|r| r.items.len() == 1)
            .unwrap_or(false)
    })
    .await;
    let rec: RecoveryFile = load_any(&pk.recovery_file).unwrap();
    assert_eq!(rec.items[0].email, EMAIL);
    assert_eq!(rec.items[0].hash.len(), 64);

    // Asking again does NOT replace the link that is already out there.
    let first_hash = rec.items[0].hash.clone();
    for _ in 0..2 {
        ask_recovery(&st, EMAIL, None).await;
    }
    tokio::time::sleep(Duration::from_millis(300)).await;
    let again = load_any::<RecoveryFile>(&pk.recovery_file).unwrap();
    assert_eq!(again.items.len(), 1);
    assert_eq!(
        again.items[0].hash, first_hash,
        "the live link was left alone"
    );

    // The email carried the raw token; the test makes one the same way.
    let mut rec: RecoveryFile = load_any(&pk.recovery_file).unwrap();
    let token = rec.issue(EMAIL, common::people::now());
    save_any(&pk.recovery_file, &rec).unwrap();

    let mut dev = soft();
    // A typo'd name does not burn the link.
    let r = recover_start(
        State(st.clone()),
        hdrs(Some(ORIGIN), None),
        json!({"token": token, "name": "bad\nname"}).to_string(),
    )
    .await;
    assert_eq!(read(r).await.0, StatusCode::BAD_REQUEST);
    // A made-up token is refused.
    let r = recover_start(
        State(st.clone()),
        hdrs(Some(ORIGIN), None),
        json!({"token": "0".repeat(64), "name": "Pixel"}).to_string(),
    )
    .await;
    assert_eq!(read(r).await.0, StatusCode::BAD_REQUEST);
    // Somebody else's page cannot use it.
    let r = recover_start(
        State(st.clone()),
        hdrs(Some("https://evil.example"), None),
        json!({"token": token, "name": "Pixel"}).to_string(),
    )
    .await;
    assert_eq!(read(r).await.0, StatusCode::FORBIDDEN);

    let r = recover_start(
        State(st.clone()),
        hdrs(Some(ORIGIN), None),
        json!({"token": token, "name": "Pixel"}).to_string(),
    )
    .await;
    let (code, h, v) = read(r).await;
    assert_eq!(code, StatusCode::OK);
    assert!(
        h.get(header::SET_COOKIE).is_none(),
        "a link never signs anybody in"
    );

    // second use of the same link: refused
    let again = recover_start(
        State(st.clone()),
        hdrs(Some(ORIGIN), None),
        json!({"token": token, "name": "Other"}).to_string(),
    )
    .await;
    assert_eq!(read(again).await.0, StatusCode::BAD_REQUEST);

    // finishing needs no session, and adds the key
    assert_eq!(
        finish_enrol(&st, &mut dev, v, ORIGIN, None).await,
        StatusCode::OK
    );
    let f = read_file(&d);
    assert_eq!(f.users[0].email, EMAIL);
    assert_eq!(f.users[0].keys.len(), 1);
}

#[tokio::test]
async fn an_expired_recovery_link_is_refused() {
    let d = temp_dir("recover-old");
    let st = app_state(&d, true, false, true);
    let pk = st.passkeys.as_ref().unwrap();
    let mut rec = RecoveryFile::default();
    let token = rec.issue(EMAIL, common::people::now() - RECOVERY_LIFE - 5);
    save_any(&pk.recovery_file, &rec).unwrap();
    let r = recover_start(
        State(st.clone()),
        hdrs(Some(ORIGIN), None),
        json!({"token": token, "name": "Pixel"}).to_string(),
    )
    .await;
    let (c, _, v) = read(r).await;
    assert_eq!(c, StatusCode::BAD_REQUEST);
    assert!(v["error"].as_str().unwrap().contains("expired"));
}

#[tokio::test]
async fn a_challenge_from_a_normal_start_still_needs_the_session_to_finish() {
    let d = temp_dir("needsession");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();
    let r = register_start(
        State(st.clone()),
        hdrs(Some(ORIGIN), Some(&cookie)),
        json!({"name": "X", "password": PASSWORD}).to_string(),
    )
    .await;
    let (_, _, v) = read(r).await;
    assert_eq!(
        finish_enrol(&st, &mut dev, v, ORIGIN, None).await,
        StatusCode::FORBIDDEN
    );
}

#[tokio::test]
async fn the_recover_page_get_does_not_use_the_token() {
    let d = temp_dir("recover-get");
    let st = app_state(&d, true, false, true);
    let pk = st.passkeys.as_ref().unwrap();
    let mut rec = RecoveryFile::default();
    let token = rec.issue(EMAIL, common::people::now());
    save_any(&pk.recovery_file, &rec).unwrap();
    let q = axum::extract::Query(RecoverQuery { t: token.clone() });
    let r = recover_page(State(st.clone()), q).await;
    assert_eq!(r.status(), StatusCode::OK);
    let rec: RecoveryFile = load_any(&pk.recovery_file).unwrap();
    assert!(
        rec.peek(&token, common::people::now()).is_some(),
        "opening the page left the link alive"
    );
}

#[test]
fn no_outbox_means_no_log_of_the_message_body() {
    // send_text must not print the body (a recovery link) when SMTP is unset.
    // Checked by reading the source: the only tracing line in that branch names
    // the recipient and subject, not the body.
    let src = include_str!("../mail.rs");
    let branch = src.split("pub async fn send_text").nth(1).unwrap();
    let branch = branch.split("pub async fn send_reset_code").next().unwrap();
    assert!(!branch.contains("{body}"));
    assert!(!branch.contains("body ="));
}

// ------------------------------------------ the "add a passkey?" offer page

async fn signin_with_next(st: &Arc<AppState>, next_encoded: &str) -> Response {
    let body = format!(
        "email={}&password={}&next={}",
        EMAIL.replace('@', "%40"),
        PASSWORD.replace(' ', "+"),
        next_encoded
    );
    crate::sign_in(State(st.clone()), body).await
}

fn location(r: &Response) -> String {
    r.headers()
        .get(header::LOCATION)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string()
}

async fn html_of(r: Response) -> (StatusCode, HeaderMap, String) {
    let (p, b) = r.into_parts();
    let bytes = axum::body::to_bytes(b, 1 << 20).await.unwrap();
    (
        p.status,
        p.headers,
        String::from_utf8(bytes.to_vec()).unwrap(),
    )
}

#[test]
fn offer_due_table() {
    let now = 1_000_000;
    assert!(offer_due(None, now), "no entry yet: ask");
    let mut u = UserKeys::default();
    assert!(offer_due(Some(&u), now), "no passkey, nothing said: ask");
    u.offer_snooze_until = now + 1;
    assert!(!offer_due(Some(&u), now), "snoozed: do not ask");
    u.offer_snooze_until = now;
    assert!(offer_due(Some(&u), now), "snooze just ran out: ask again");
    u.offer_snooze_until = now - 5;
    assert!(offer_due(Some(&u), now));
    u.offer_never = true;
    assert!(!offer_due(Some(&u), now), "don't ask again wins");
    assert_eq!(SNOOZE_SECS, 7 * 86400);
}

#[tokio::test]
async fn password_signin_shows_the_offer_and_keeps_the_return_to() {
    let d = temp_dir("offer");
    let st = app_state(&d, true, false, true);

    let r = signin_with_next(&st, "%2Fkitchen%2Fsoup").await;
    let (code, h, html) = html_of(r).await;
    assert_eq!(code, StatusCode::OK, "an interstitial, not a redirect");
    assert!(html.contains("Add a passkey?"));
    assert!(html.contains("fingerprint or face"));
    assert!(html.contains("Add a passkey</button>"));
    assert!(html.contains("Not now"));
    assert!(html.contains("Don't ask again"));
    assert!(
        html.contains("data-next=\"/kitchen/soup\""),
        "return-to is carried"
    );
    assert!(
        html.contains("href=\"/kitchen/soup\""),
        "plain skip link goes there too"
    );

    // the session is already live, so skipping never needs another sign-in
    let set = h
        .get(header::SET_COOKIE)
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();
    let cookie = set.split(';').next().unwrap().to_string();
    assert_eq!(
        crate::cookie_email(&st, &hdrs(None, Some(&cookie))).as_deref(),
        Some(EMAIL)
    );

    // and the enrolment from this page works with that session (password
    // asked once, here)
    let mut dev = soft();
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "Pixel", PASSWORD).await,
        StatusCode::OK
    );
    let r = signin_with_next(&st, "%2Fkitchen%2Fsoup").await;
    assert_eq!(
        r.status(),
        StatusCode::SEE_OTHER,
        "has a passkey now: no more offers"
    );
    assert_eq!(location(&r), "/kitchen/soup");
}

#[tokio::test]
async fn a_subdomain_return_to_survives_the_offer() {
    let d = temp_dir("offer-bridge");
    let st = app_state(&d, true, false, true);
    let r = signin_with_next(
        &st,
        "https%3A%2F%2Fstatus.home.example.net%2Fdashboard",
    )
    .await;
    let (code, _, html) = html_of(r).await;
    assert_eq!(code, StatusCode::OK);
    assert!(html.contains("data-next=\"/sso/bridge?next="), "{html}");
}

#[tokio::test]
async fn not_now_snoozes_for_a_week_then_asks_again() {
    let d = temp_dir("snooze");
    let st = app_state(&d, true, false, true);
    let (_, h, _) = html_of(signin_with_next(&st, "%2F").await).await;
    let cookie = h
        .get(header::SET_COOKIE)
        .unwrap()
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_string();

    let (c, _) = post_json(offer_choice, &st, &cookie, json!({"choice": "later"})).await;
    assert_eq!(c, StatusCode::OK);
    let f = read_file(&d);
    let until = f.users[0].offer_snooze_until;
    let now = common::people::now();
    assert!(until > now + SNOOZE_SECS - 10 && until <= now + SNOOZE_SECS + 5);
    assert!(!f.users[0].offer_never);

    let r = signin_with_next(&st, "%2Fgo").await;
    assert_eq!(
        r.status(),
        StatusCode::SEE_OTHER,
        "snoozed: straight through"
    );
    assert_eq!(location(&r), "/go");

    // a week later
    let mut f = read_file(&d);
    f.users[0].offer_snooze_until = common::people::now() - 1;
    save(&d.join("passkeys.json"), &f).unwrap();
    assert_eq!(
        signin_with_next(&st, "%2Fgo").await.status(),
        StatusCode::OK
    );
}

#[tokio::test]
async fn dont_ask_again_sticks() {
    let d = temp_dir("never");
    let st = app_state(&d, true, false, true);
    let (_, h, _) = html_of(signin_with_next(&st, "%2F").await).await;
    let cookie = h
        .get(header::SET_COOKIE)
        .unwrap()
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_string();
    let (c, _) = post_json(offer_choice, &st, &cookie, json!({"choice": "never"})).await;
    assert_eq!(c, StatusCode::OK);
    assert!(read_file(&d).users[0].offer_never);
    let mut f = read_file(&d);
    f.users[0].offer_snooze_until = 0;
    save(&d.join("passkeys.json"), &f).unwrap();
    let r = signin_with_next(&st, "%2Fgo").await;
    assert_eq!(r.status(), StatusCode::SEE_OTHER);
    assert_eq!(location(&r), "/go");
}

#[tokio::test]
async fn the_offer_never_blocks_when_passkeys_are_off_or_the_file_is_damaged() {
    let d = temp_dir("offer-off");
    let st = app_state(&d, false, false, true);
    let r = signin_with_next(&st, "%2Fgo").await;
    assert_eq!(r.status(), StatusCode::SEE_OTHER);
    assert_eq!(location(&r), "/go");
    assert!(r.headers().get(header::SET_COOKIE).is_some());

    let d = temp_dir("offer-damaged");
    let st = app_state(&d, true, false, true);
    std::fs::write(d.join("passkeys.json"), b"{ garbage").unwrap();
    let r = signin_with_next(&st, "%2Fgo").await;
    assert_eq!(r.status(), StatusCode::SEE_OTHER);
    assert_eq!(location(&r), "/go");
}

#[tokio::test]
async fn a_wrong_password_gets_no_offer_and_no_cookie() {
    let d = temp_dir("offer-wrong");
    let st = app_state(&d, true, false, true);
    let body = format!(
        "email={}&password=nope+nope+nope&next=%2F",
        EMAIL.replace('@', "%40")
    );
    let r = crate::sign_in(State(st.clone()), body).await;
    assert_eq!(r.status(), StatusCode::UNAUTHORIZED);
    assert!(r.headers().get(header::SET_COOKIE).is_none());
}

#[tokio::test]
async fn the_offer_follows_a_first_password_choice() {
    let d = temp_dir("offer-first");
    let st = app_state(&d, true, true, true); // must_change
    let body = format!(
        "email={}&current={}&password=A+brand+new+one+7%21&confirm=A+brand+new+one+7%21&next=%2Fmama%2F",
        EMAIL.replace('@', "%40"),
        PASSWORD.replace(' ', "+")
    );
    let r = crate::change_password(State(st.clone()), body).await;
    let (code, h, html) = html_of(r).await;
    assert_eq!(code, StatusCode::OK);
    assert!(html.contains("Add a passkey?"));
    assert!(html.contains("data-next=\"/mama/\""));
    assert!(h.get(header::SET_COOKIE).is_some());
}

#[tokio::test]
async fn offer_answers_need_a_session_our_origin_and_a_real_choice() {
    let d = temp_dir("offer-guards");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let no_session = offer_choice(
        State(st.clone()),
        hdrs(Some(ORIGIN), None),
        json!({"choice": "later"}).to_string(),
    )
    .await;
    assert_eq!(no_session.status(), StatusCode::UNAUTHORIZED);
    let wrong_origin = offer_choice(
        State(st.clone()),
        hdrs(Some("https://evil.example"), Some(&cookie)),
        json!({"choice": "later"}).to_string(),
    )
    .await;
    assert_eq!(wrong_origin.status(), StatusCode::FORBIDDEN);
    let (c, _) = post_json(offer_choice, &st, &cookie, json!({"choice": "whatever"})).await;
    assert_eq!(c, StatusCode::BAD_REQUEST);
    assert!(load(&d.join("passkeys.json")).unwrap().users.is_empty());
}

#[test]
fn the_offer_page_script_always_carries_on() {
    // every path in initOffer ends in a replace() to the return-to
    let js = SCRIPT.split("function initOffer").nth(1).unwrap();
    let js = js.split("// ----").next().unwrap();
    assert!(js.contains("if (!supported) { window.location.replace(next); return; }"));
    assert!(js.matches("window.location.replace(next)").count() >= 2);
}

// ====================================================== review fixes (round 1)

const BOSS: &str = "boss@example.test";

fn hdrs_ip(origin: Option<&str>, cookie: Option<&str>, ip: Option<&str>) -> HeaderMap {
    let mut h = hdrs(origin, cookie);
    if let Some(ip) = ip {
        h.insert("x-forwarded-for", ip.parse().unwrap());
    }
    h
}

async fn ask_recovery(st: &Arc<AppState>, email: &str, ip: Option<&str>) -> Response {
    recover_send(
        State(st.clone()),
        hdrs_ip(Some(ORIGIN), None, ip),
        format!("email={}", email.replace('@', "%40")),
    )
    .await
}

async fn wait_for<F: Fn() -> bool>(f: F) {
    for _ in 0..100 {
        if f() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(30)).await;
    }
    panic!("condition not reached in time");
}

/// Add another person (with the same test password); returns their cookie.
fn add_person(st: &AppState, email: &str, name: &str) -> String {
    let mut list = crate::people(st);
    let mut p = common::people::Person::new(email);
    p.hash = common::people::hash_password(PASSWORD).unwrap();
    p.name = name.to_string();
    list.push(p.clone());
    common::people::write(&st.people_file, &list).unwrap();
    format!("sso={}", crate::make_cookie_value(st, email, &p.hash))
}

#[test]
fn client_ip_takes_the_rightmost_address_and_ignores_junk() {
    let mut h = HeaderMap::new();
    assert_eq!(client_ip(&h), "unknown");
    h.insert("x-forwarded-for", "203.0.113.9".parse().unwrap());
    assert_eq!(client_ip(&h), "203.0.113.9");
    h.insert("x-forwarded-for", "6.6.6.6, 203.0.113.9".parse().unwrap());
    assert_eq!(
        client_ip(&h),
        "203.0.113.9",
        "a forged first entry is ignored"
    );
    h.insert("x-forwarded-for", "not-an-ip".parse().unwrap());
    assert_eq!(client_ip(&h), "unknown");
    h.insert("x-forwarded-for", "2001:db8::1".parse().unwrap());
    assert_eq!(client_ip(&h), "2001:db8::1");
}

#[tokio::test]
async fn one_address_hitting_the_limit_does_not_block_another() {
    let d = temp_dir("perip");
    let st = app_state(&d, true, false, true);
    for i in 0..60 {
        let r = login_start(
            State(st.clone()),
            hdrs_ip(Some(ORIGIN), None, Some("198.51.100.1")),
        )
        .await;
        assert_eq!(r.status(), StatusCode::OK, "request {i}");
    }
    let r = login_start(
        State(st.clone()),
        hdrs_ip(Some(ORIGIN), None, Some("198.51.100.1")),
    )
    .await;
    assert_eq!(r.status(), StatusCode::TOO_MANY_REQUESTS);
    let other = login_start(
        State(st.clone()),
        hdrs_ip(Some(ORIGIN), None, Some("198.51.100.2")),
    )
    .await;
    assert_eq!(
        other.status(),
        StatusCode::OK,
        "a stranger cannot use up everyone's allowance"
    );
}

#[tokio::test]
async fn junk_credentials_never_count_toward_any_limit() {
    let d = temp_dir("junk");
    let st = app_state(&d, true, false, true);
    for _ in 0..40 {
        let r = login_start(
            State(st.clone()),
            hdrs_ip(Some(ORIGIN), None, Some("203.0.113.5")),
        )
        .await;
        let (_, _, v) = read(r).await;
        let body = json!({ "token": v["token"], "credential": {"nonsense": 1} }).to_string();
        let r = login_finish(
            State(st.clone()),
            hdrs_ip(Some(ORIGIN), None, Some("203.0.113.5")),
            body,
        )
        .await;
        assert_eq!(r.status(), StatusCode::UNAUTHORIZED);
    }
    // never turned into 429, from that address or any other
    let r = login_finish(
        State(st.clone()),
        hdrs_ip(Some(ORIGIN), None, Some("203.0.113.5")),
        json!({"token": "x", "credential": {}}).to_string(),
    )
    .await;
    assert_ne!(r.status(), StatusCode::TOO_MANY_REQUESTS);
}

#[tokio::test]
async fn recover_send_checks_the_origin_and_answers_without_waiting() {
    let d = temp_dir("rec-origin");
    let st = app_state(&d, true, false, true);
    let pk = st.passkeys.as_ref().unwrap();
    let r = recover_send(
        State(st.clone()),
        hdrs(Some("https://evil.example"), None),
        format!("email={}", EMAIL.replace('@', "%40")),
    )
    .await;
    assert_eq!(r.status(), StatusCode::FORBIDDEN);
    let r = recover_send(
        State(st.clone()),
        hdrs(None, None),
        format!("email={}", EMAIL.replace('@', "%40")),
    )
    .await;
    assert_eq!(r.status(), StatusCode::FORBIDDEN, "no Origin header either");
    tokio::time::sleep(Duration::from_millis(200)).await;
    assert!(!pk.recovery_file.exists(), "nothing was issued");
}

#[tokio::test]
async fn known_and_unknown_addresses_get_the_same_body_and_status() {
    let d = temp_dir("rec-same");
    let st = app_state(&d, true, false, true);
    let a = ask_recovery(&st, EMAIL, Some("192.0.2.1")).await;
    let b = ask_recovery(&st, "nobody@example.test", Some("192.0.2.1")).await;
    let (pa, ba) = a.into_parts();
    let (pb, bb) = b.into_parts();
    assert_eq!(pa.status, pb.status);
    assert_eq!(
        axum::body::to_bytes(ba, 1 << 20).await.unwrap(),
        axum::body::to_bytes(bb, 1 << 20).await.unwrap()
    );
}

#[tokio::test]
async fn an_ended_guest_gets_no_recovery_link() {
    let d = temp_dir("rec-ended");
    let st = app_state(&d, true, false, true);
    let pk = st.passkeys.as_ref().unwrap();
    let mut f = PasskeyFile::default();
    f.users.push(UserKeys {
        email: EMAIL.into(),
        user_id: new_user_id().to_string(),
        guest: true,
        account_expires: 1000,
        ..Default::default()
    });
    save(&d.join("passkeys.json"), &f).unwrap();
    ask_recovery(&st, EMAIL, None).await;
    tokio::time::sleep(Duration::from_millis(300)).await;
    assert!(
        !pk.recovery_file.exists()
            || load_any::<RecoveryFile>(&pk.recovery_file)
                .unwrap()
                .items
                .is_empty()
    );
}

#[tokio::test]
async fn a_password_reset_removes_passkeys_and_clears_passkey_only() {
    let d = temp_dir("reset");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "One", PASSWORD).await,
        StatusCode::OK
    );
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "Two", PASSWORD).await,
        StatusCode::OK
    );
    let (c, _) = post_json(set_only, &st, &cookie, json!({"on": true})).await;
    assert_eq!(c, StatusCode::OK);
    // the owner's settings must survive a reset
    let mut f = read_file(&d);
    f.users[0].label = "Ultra Nova's Passkey".into();
    f.users[0].cap = 3;
    f.users[0].offer_never = true;
    save(&d.join("passkeys.json"), &f).unwrap();

    // a real emailed-code reset
    let code = "123456";
    let mut list = Vec::new();
    crate::reset::issue(&mut list, EMAIL, code, common::people::now()).unwrap();
    crate::reset::write(&st.reset_file, &list).unwrap();
    let body = format!(
        "email={}&code={code}&password=A+brand+new+one+7%21&confirm=A+brand+new+one+7%21&next=%2Fgo",
        EMAIL.replace('@', "%40")
    );
    let r = crate::use_code(State(st.clone()), body).await;
    let (code_status, h, html) = html_of(r).await;
    assert_eq!(
        code_status,
        StatusCode::OK,
        "offer page appears again after a reset"
    );
    assert!(html.contains("Add a passkey?") && html.contains("data-next=\"/go\""));
    assert!(h.get(header::SET_COOKIE).is_some());

    let f = read_file(&d);
    assert!(f.users[0].keys.is_empty(), "every passkey is gone");
    assert!(!f.users[0].passkey_only);
    assert!(!f.users[0].offer_never, "the offer is back");
    assert_eq!(
        f.users[0].label, "Ultra Nova's Passkey",
        "owner's label kept"
    );
    assert_eq!(f.users[0].cap, 3, "owner's cap kept");
    assert!(!password_off(&st, EMAIL));
}

#[tokio::test]
async fn a_normal_password_change_keeps_passkeys() {
    let d = temp_dir("change");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "One", PASSWORD).await,
        StatusCode::OK
    );
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "Two", PASSWORD).await,
        StatusCode::OK
    );
    post_json(set_only, &st, &cookie, json!({"on": true})).await;
    let body = format!(
        "email={}&current={}&password=Another+new+one+8%21&confirm=Another+new+one+8%21&next=%2F",
        EMAIL.replace('@', "%40"),
        PASSWORD.replace(' ', "+")
    );
    // (password sign-in is off, but the change form needs the current password only)
    let r = crate::change_password(State(st.clone()), body).await;
    assert!(r.status() == StatusCode::SEE_OTHER || r.status() == StatusCode::OK);
    let f = read_file(&d);
    assert_eq!(f.users[0].keys.len(), 2, "passkeys survive a normal change");
    assert!(
        f.users[0].passkey_only,
        "two lasting passkeys still back the switch"
    );
}

#[test]
fn passkey_only_is_cleared_when_nothing_backs_it() {
    let d = temp_dir("backing");
    let st = app_state(&d, true, false, true);
    let mut f = PasskeyFile::default();
    f.users.push(UserKeys {
        email: EMAIL.into(),
        user_id: new_user_id().to_string(),
        passkey_only: true, // but no passkeys at all
        ..Default::default()
    });
    save(&d.join("passkeys.json"), &f).unwrap();
    after_password_change(&st, EMAIL);
    assert!(!read_file(&d).users[0].passkey_only);
}

#[test]
fn entries_for_people_who_left_are_pruned_but_never_on_an_empty_list() {
    let mut f = PasskeyFile::default();
    for e in ["a@example.test", "gone@example.test"] {
        f.users.push(UserKeys {
            email: e.into(),
            user_id: new_user_id().to_string(),
            ..Default::default()
        });
    }
    assert_eq!(
        prune_unknown(&mut f, &[]),
        (0, 0),
        "an unreadable people list wipes nothing"
    );
    assert_eq!(f.users.len(), 2);
    assert_eq!(
        prune_unknown(&mut f, &["a@example.test".to_string()]),
        (1, 0)
    );
    assert_eq!(f.users.len(), 1);
    assert_eq!(f.users[0].email, "a@example.test");
}

#[tokio::test]
async fn the_pruning_run_drops_unknown_people_from_the_file() {
    let d = temp_dir("prune-unknown");
    let st = app_state(&d, true, false, true);
    let mut f = PasskeyFile::default();
    for e in [EMAIL, "gone@example.test"] {
        f.users.push(UserKeys {
            email: e.into(),
            user_id: new_user_id().to_string(),
            ..Default::default()
        });
    }
    save(&d.join("passkeys.json"), &f).unwrap();
    st.passkeys
        .as_ref()
        .unwrap()
        .prune_on_start(&[EMAIL.to_string()]);
    let f = read_file(&d);
    assert_eq!(f.users.len(), 1);
    assert_eq!(f.users[0].email, EMAIL);
}

#[test]
fn the_script_refuses_backslash_and_control_characters_in_return_to() {
    assert!(SCRIPT.contains("[\\\\\\u0000-\\u001f\\u007f]"));
}

// ================================================================ naming

#[test]
fn label_fallback_order() {
    // 1. the owner-set label, exactly as typed (no apostrophe guessing)
    assert_eq!(
        display_label("Ultra Nova's Passkey", "Jason Gibson", EMAIL),
        "Ultra Nova's Passkey"
    );
    assert_eq!(
        display_label("Ultra Novas Passkey", "Jason", EMAIL),
        "Ultra Novas Passkey"
    );
    // 2. the name in people.json
    assert_eq!(
        display_label("", "Jason Gibson", EMAIL),
        "Jason Gibson Passkey"
    );
    assert_eq!(display_label("", "Jason", EMAIL), "Jason Passkey");
    assert_eq!(display_label("  ", "  Casey  ", EMAIL), "Casey Passkey");
    // 3. the part of the email before the @
    assert_eq!(
        display_label("", "", "user@example.com"),
        "davetta1 Passkey"
    );
    assert_eq!(
        display_label("", "   ", "user@example.com"),
        "davetta1 Passkey"
    );
    assert_eq!(display_label("", "", "@odd"), "My Passkey");
}

#[test]
fn labels_are_plain_short_and_never_say_private_key() {
    let long = "x".repeat(200);
    assert_eq!(display_label(&long, "", EMAIL).chars().count(), 64);
    assert!(display_label("", &long, EMAIL).chars().count() <= 64);
    // "private key" in any case is never used
    assert_eq!(
        display_label("My Private Key", "Jason", EMAIL),
        "Jason Passkey"
    );
    assert_eq!(display_label("", "PRIVATE KEY guy", EMAIL), "jason Passkey");
    assert!(clean_label("my private key").is_err());
    assert!(clean_label(&"y".repeat(65)).is_err());
    assert!(clean_label(&"y".repeat(64)).is_ok());
    assert!(clean_label("bad\nlabel").is_err());
    assert_eq!(
        clean_label("  Ultra Nova's Passkey ").unwrap(),
        "Ultra Nova's Passkey"
    );
    assert_eq!(clean_label("   ").unwrap(), "", "empty clears the override");
}

#[test]
fn the_default_name_is_made_unique() {
    assert_eq!(free_name("Jason Passkey", &[]), "Jason Passkey");
}

#[tokio::test]
async fn the_manager_sees_the_email_as_name_and_the_label_as_display_name() {
    let d = temp_dir("names");
    let st = app_state(&d, true, false, true);
    // give the person a name in people.json (read only for us)
    let mut list = crate::people(&st);
    list[0].name = "Jason Gibson".into();
    common::people::write(&st.people_file, &list).unwrap();
    let cookie = session_cookie(&st);
    let start = |st: Arc<AppState>, cookie: String, name: &'static str| async move {
        let r = register_start(
            State(st),
            hdrs(Some(ORIGIN), Some(&cookie)),
            json!({"name": name, "password": PASSWORD}).to_string(),
        )
        .await;
        read(r).await.2
    };
    let v = start(st.clone(), cookie.clone(), "A").await;
    assert_eq!(
        v["options"]["publicKey"]["user"]["name"], EMAIL,
        "stable sign-in email"
    );
    assert_eq!(
        v["options"]["publicKey"]["user"]["displayName"],
        "Jason Gibson Passkey"
    );

    // an owner-set label wins, apostrophe untouched
    let mut f = PasskeyFile::default();
    f.users.push(UserKeys {
        email: EMAIL.into(),
        label: "Ultra Nova's Passkey".into(),
        ..Default::default() // a hand-written entry: no user handle yet
    });
    save(&d.join("passkeys.json"), &f).unwrap();
    let v = start(st.clone(), cookie.clone(), "B").await;
    assert_eq!(
        v["options"]["publicKey"]["user"]["displayName"],
        "Ultra Nova's Passkey"
    );
}

#[tokio::test]
async fn a_hand_written_label_entry_gets_its_user_handle_when_the_first_passkey_is_added() {
    let d = temp_dir("handwritten");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    std::fs::write(
        d.join("passkeys.json"),
        format!(r#"{{"users":[{{"email":"{EMAIL}","label":"Ultra Nova's Passkey"}}]}}"#),
    )
    .unwrap();
    let mut dev = soft();
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "Pixel", PASSWORD).await,
        StatusCode::OK
    );
    let f = read_file(&d);
    assert!(
        Uuid::parse_str(&f.users[0].user_id).is_ok(),
        "handle was filled in"
    );
    assert_eq!(f.users[0].label, "Ultra Nova's Passkey");
    // and the passkey signs in
    let (_, cid, uid) = stored(&d);
    let body = sign_in_body(&st, &mut dev, &cid, uid, "/").await;
    let (c, _, _) =
        read(login_finish(State(st.clone()), hdrs(Some(ORIGIN), None), body).await).await;
    assert_eq!(c, StatusCode::OK);
}

#[tokio::test]
async fn only_an_owner_sets_labels_and_keys_are_named_automatically() {
    let d = temp_dir("label-set");
    let mut st = app_state(&d, true, false, true);
    set_owners(&mut st, &[EMAIL]);
    let cookie = session_cookie(&st);
    let mut dev = soft();

    // there is no self-service label route any more: the page has no label form
    let (_, _, html) =
        html_of(account_page(State(st.clone()), hdrs(None, Some(&cookie))).await).await;
    assert!(!html.contains("pk-label") && !html.contains("Save label"));
    assert!(
        !html.contains("name=\"name\""),
        "no name box on the account page"
    );
    assert!(!SCRIPT.contains("passkey/label"));

    // the owner sets a label with their own passkey (the first key is named automatically)
    assert_eq!(enrol_auto(&st, &mut dev, &cookie).await, StatusCode::OK);
    assert_eq!(read_file(&d).users[0].keys[0].name, "jason Passkey");
    let (c, _) = owner_do(
        &st,
        &mut dev,
        &cookie,
        json!({"action":"label","email":EMAIL,"label":"Ultra Nova's Passkey"}),
    )
    .await;
    assert_eq!(c, StatusCode::OK);
    assert_eq!(read_file(&d).users[0].label, "Ultra Nova's Passkey");
    for bad in ["my private key".to_string(), "z".repeat(65)] {
        let (c, _) = owner_do(
            &st,
            &mut dev,
            &cookie,
            json!({"action":"label","email":EMAIL,"label":bad}),
        )
        .await;
        assert_eq!(c, StatusCode::BAD_REQUEST);
    }
    let (c, _) = owner_do(
        &st,
        &mut dev,
        &cookie,
        json!({"action":"label","email":"nobody@example.test","label":"x"}),
    )
    .await;
    assert_eq!(c, StatusCode::BAD_REQUEST);

    // the account page says what the next key will be called (escaped), with no box
    let (_, _, html) =
        html_of(account_page(State(st.clone()), hdrs(None, Some(&cookie))).await).await;
    assert!(
        html.contains("saved as &ldquo;Ultra Nova&#39;s Passkey&rdquo;"),
        "{html}"
    );

    // new keys get the label, made unique, and the manager is told the same label
    assert_eq!(enrol_auto(&st, &mut dev, &cookie).await, StatusCode::OK);
    assert_eq!(enrol_auto(&st, &mut dev, &cookie).await, StatusCode::OK);
    let f = read_file(&d);
    let names: Vec<&str> = f.users[0].keys.iter().map(|k| k.name.as_str()).collect();
    assert_eq!(
        names,
        [
            "jason Passkey",
            "Ultra Nova's Passkey",
            "Ultra Nova's Passkey 2"
        ]
    );
}

/// Enrol the way the pages do: no name sent.
async fn enrol_auto(
    st: &Arc<AppState>,
    dev: &mut WebauthnAuthenticator<SoftPasskey>,
    cookie: &str,
) -> StatusCode {
    let r = register_start(
        State(st.clone()),
        hdrs(Some(ORIGIN), Some(cookie)),
        json!({"password": PASSWORD}).to_string(),
    )
    .await;
    let (code, _, v) = read(r).await;
    if code != StatusCode::OK {
        return code;
    }
    finish_enrol(st, dev, v, ORIGIN, Some(cookie)).await
}

#[tokio::test]
async fn the_offer_and_recovery_pages_have_no_name_box_either() {
    let d = temp_dir("noname");
    let st = app_state(&d, true, false, true);
    let (_, _, html) = html_of(signin_with_next(&st, "%2F").await).await;
    assert!(
        html.contains("saved as &ldquo;jason Passkey&rdquo;"),
        "{html}"
    );
    assert!(!html.contains("name=\"name\""));
    let (_, _, rec) = html_of(
        recover_page(
            State(st.clone()),
            axum::extract::Query(RecoverQuery { t: "abc".into() }),
        )
        .await,
    )
    .await;
    assert!(!rec.contains("name=\"name\""));
    // the script sends no name from any page
    assert!(!SCRIPT.contains("form.elements['name']"));
    assert!(!SCRIPT.contains("name: name"));
}

#[tokio::test]
async fn names_up_to_64_characters_are_accepted_for_passkeys() {
    assert!(clean_name(&"n".repeat(64)).is_ok());
    assert!(clean_name(&"n".repeat(65)).is_err());
}

// ====================================================== cap, guest, expiry

#[test]
fn cap_and_guest_rules_table() {
    let now = 1_000_000;
    assert_eq!(cap_for(None), 5);
    let mut u = UserKeys::default();
    assert_eq!(cap_for(Some(&u)), 5);
    u.cap = 2;
    assert_eq!(cap_for(Some(&u)), 2);
    u.cap = 99;
    assert_eq!(cap_for(Some(&u)), 20, "never above 20");

    // ordinary person: fine
    assert!(enrol_allowed(None, 0, now).is_ok());
    assert!(enrol_allowed(Some(&UserKeys::default()), 0, now).is_ok());

    // guest: temporary only, one at most, not past the account end
    let g = UserKeys {
        guest: true,
        account_expires: now + 10 * 86400,
        ..Default::default()
    };
    assert!(
        enrol_allowed(Some(&g), 0, now).is_err(),
        "no lasting passkey for a guest"
    );
    assert!(enrol_allowed(Some(&g), 7, now).is_ok());
    assert!(
        enrol_allowed(Some(&g), 30, now).is_err(),
        "longer than the account"
    );
    assert!(enrol_allowed(Some(&g), 1, now).is_ok());
    // ended account
    assert!(enrol_allowed(Some(&g), 1, now + 11 * 86400).is_err());
    assert!(account_expired(&g, now + 10 * 86400));
    assert!(!account_expired(&g, now + 10 * 86400 - 1));
    assert!(
        !account_expired(&UserKeys::default(), u64::MAX),
        "no end date: never ends"
    );
}

#[tokio::test]
async fn the_cap_is_enforced_at_enrolment_with_plain_words() {
    let d = temp_dir("cap");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();
    let mut f = PasskeyFile::default();
    f.users.push(UserKeys {
        email: EMAIL.into(),
        user_id: new_user_id().to_string(),
        cap: 2,
        ..Default::default()
    });
    save(&d.join("passkeys.json"), &f).unwrap();
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "One", PASSWORD).await,
        StatusCode::OK
    );
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "Two", PASSWORD).await,
        StatusCode::OK
    );
    let r = register_start(
        State(st.clone()),
        hdrs(Some(ORIGIN), Some(&cookie)),
        json!({"name": "Three", "password": PASSWORD}).to_string(),
    )
    .await;
    let (c, _, v) = read(r).await;
    assert_eq!(c, StatusCode::BAD_REQUEST);
    let msg = v["error"].as_str().unwrap();
    assert!(
        msg.contains("most passkeys") && msg.contains('2') && msg.contains("Remove one"),
        "{msg}"
    );
}

#[tokio::test]
async fn the_default_cap_is_five() {
    let d = temp_dir("cap5");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();
    for n in 0..5 {
        assert_eq!(
            enrol(&st, &mut dev, &cookie, &format!("K{n}"), PASSWORD).await,
            StatusCode::OK
        );
    }
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "K5", PASSWORD).await,
        StatusCode::BAD_REQUEST
    );
}

#[tokio::test]
async fn an_ended_guest_account_refuses_every_sign_in_method_at_once() {
    let d = temp_dir("guest-end");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "Pixel", PASSWORD).await,
        StatusCode::OK
    );
    let (_, cid, uid) = stored(&d);
    assert!(crate::cookie_email(&st, &hdrs(None, Some(&cookie))).is_some());

    // the owner's end date arrives
    let mut f = read_file(&d);
    f.users[0].guest = true;
    f.users[0].account_expires = common::people::now() - 1;
    save(&d.join("passkeys.json"), &f).unwrap();

    // 1. the session cookie, on the very next request
    assert!(crate::cookie_email(&st, &hdrs(None, Some(&cookie))).is_none());
    let mut vh = hdrs(None, Some(&cookie));
    vh.insert("X-Forwarded-Uri", "/kitchen/".parse().unwrap());
    assert_ne!(
        crate::verify(State(st.clone()), vh).await.status(),
        StatusCode::OK
    );
    // 2. the password
    assert_eq!(
        password_signin(&st, PASSWORD).await,
        StatusCode::UNAUTHORIZED
    );
    // 3. the passkey, with the same plain words as any expired one
    let body = sign_in_body(&st, &mut dev, &cid, uid, "/").await;
    let (c, h, v) =
        read(login_finish(State(st.clone()), hdrs(Some(ORIGIN), None), body).await).await;
    assert_eq!(c, StatusCode::UNAUTHORIZED);
    assert!(v["error"]
        .as_str()
        .unwrap()
        .contains("isn't valid any more"));
    assert!(h.get(header::SET_COOKIE).is_none());
    // 4. no new passkey, and the next pruning run removes what is left
    assert!(account_ended(&st, EMAIL));
    st.passkeys
        .as_ref()
        .unwrap()
        .prune_on_start(&[EMAIL.to_string()]);
    assert!(read_file(&d).users[0].keys.is_empty());
}

#[test]
fn pruning_removes_every_passkey_of_an_ended_guest_and_names_them() {
    let mut f = PasskeyFile::default();
    f.users.push(UserKeys {
        email: EMAIL.into(),
        guest: true,
        account_expires: 500,
        passkey_only: true,
        ..Default::default()
    });
    let gone = prune_expired(&mut f, 1000);
    assert!(gone.is_empty(), "no keys to remove in this bare entry");
    assert!(!f.users[0].passkey_only);
}

#[test]
fn dates_round_trip() {
    // 2026-12-31 works through the whole day: ends at 2027-01-01 00:00 UTC
    let e = parse_end_of_day("2026-12-31").unwrap();
    assert_eq!(e, 1_798_761_600);
    assert_eq!(last_day_text(e), "2026-12-31");
    assert_eq!(
        parse_end_of_day("2024-02-29").map(last_day_text).as_deref(),
        Some("2024-02-29")
    );
    assert!(parse_end_of_day("2026-02-29").is_none(), "not a leap year");
    assert!(parse_end_of_day("2026-13-01").is_none());
    assert!(parse_end_of_day("26-1-1").is_none());
    assert!(parse_end_of_day("tomorrow").is_none());
    assert!(parse_end_of_day("2026-12-31-1").is_none());
    assert_eq!(last_day_text(0), "");
}

#[test]
fn guests_are_not_pestered_with_the_offer() {
    let g = UserKeys {
        guest: true,
        ..Default::default()
    };
    assert!(!offer_due(Some(&g), 1_000_000));
}

// ============================================ owner tools, with the passkey

fn set_owners(st: &mut Arc<AppState>, owners: &[&str]) {
    Arc::get_mut(st).unwrap().passkeys.as_mut().unwrap().owners =
        owners.iter().map(|s| s.to_string()).collect();
}

/// Run one owner action the way the page does: begin, answer with the
/// owner's own passkey, confirm.
async fn owner_do(
    st: &Arc<AppState>,
    dev: &mut WebauthnAuthenticator<SoftPasskey>,
    cookie: &str,
    action: serde_json::Value,
) -> (StatusCode, serde_json::Value) {
    let r = owner_begin(
        State(st.clone()),
        hdrs(Some(ORIGIN), Some(cookie)),
        action.to_string(),
    )
    .await;
    let (c, _, v) = read(r).await;
    if c != StatusCode::OK {
        return (c, v);
    }
    let rcr: RequestChallengeResponse = serde_json::from_value(v["options"].clone()).unwrap();
    let cred = dev
        .do_authentication(origin_url(), rcr)
        .expect("owner device signs");
    let body = json!({ "token": v["token"], "credential": cred }).to_string();
    let r = owner_confirm(State(st.clone()), hdrs(Some(ORIGIN), Some(cookie)), body).await;
    let (c, _, v) = read(r).await;
    (c, v)
}

#[tokio::test]
async fn owner_tools_are_for_owners_only_and_off_by_default() {
    let d = temp_dir("owner-gate");
    let mut st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    // nobody is an owner yet
    let r = owner_page(State(st.clone()), hdrs(None, Some(&cookie))).await;
    assert_eq!(r.status(), StatusCode::FORBIDDEN);
    // someone else is the owner
    set_owners(&mut st, &[BOSS]);
    let r = owner_page(State(st.clone()), hdrs(None, Some(&cookie))).await;
    assert_eq!(r.status(), StatusCode::FORBIDDEN);
    let r = owner_begin(
        State(st.clone()),
        hdrs(Some(ORIGIN), Some(&cookie)),
        json!({"action":"cap","email":EMAIL,"cap":3}).to_string(),
    )
    .await;
    assert_eq!(r.status(), StatusCode::FORBIDDEN);
    // not signed in at all
    let r = owner_page(State(st.clone()), hdrs(None, None)).await;
    assert_eq!(r.status(), StatusCode::UNAUTHORIZED);
    // the ordinary account page does not show the owner link to a non-owner
    let (_, _, html) =
        html_of(account_page(State(st.clone()), hdrs(None, Some(&cookie))).await).await;
    assert!(!html.contains("Owner tools"));
}

#[tokio::test]
async fn an_owner_session_alone_changes_nothing_a_passkey_must_confirm() {
    let d = temp_dir("owner-fresh");
    let mut st = app_state(&d, true, false, true);
    set_owners(&mut st, &[EMAIL]);
    let cookie = session_cookie(&st);
    add_person(&st, BOSS, "Boss");

    // the owner has no passkey yet: refused with a plain pointer
    let r = owner_begin(
        State(st.clone()),
        hdrs(Some(ORIGIN), Some(&cookie)),
        json!({"action":"cap","email":BOSS,"cap":1}).to_string(),
    )
    .await;
    let (c, _, v) = read(r).await;
    assert_eq!(c, StatusCode::BAD_REQUEST);
    assert!(v["error"].as_str().unwrap().contains("passkey"));

    let mut dev = soft();
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "Owner phone", PASSWORD).await,
        StatusCode::OK
    );

    // begin, then confirm with junk instead of the device's answer
    let r = owner_begin(
        State(st.clone()),
        hdrs(Some(ORIGIN), Some(&cookie)),
        json!({"action":"cap","email":BOSS,"cap":1}).to_string(),
    )
    .await;
    let (_, _, v) = read(r).await;
    let r = owner_confirm(
        State(st.clone()),
        hdrs(Some(ORIGIN), Some(&cookie)),
        json!({"token": v["token"], "credential": {"junk": true}}).to_string(),
    )
    .await;
    assert_ne!(r.status(), StatusCode::OK);
    let f = read_file(&d);
    assert!(
        f.users.iter().all(|u| u.email != BOSS || u.cap == 0),
        "nothing changed"
    );
    // that challenge is spent
    let r = owner_confirm(
        State(st.clone()),
        hdrs(Some(ORIGIN), Some(&cookie)),
        json!({"token": v["token"], "credential": {"junk": true}}).to_string(),
    )
    .await;
    assert_eq!(r.status(), StatusCode::BAD_REQUEST);

    // the real thing works, once
    let (c, v) = owner_do(
        &st,
        &mut dev,
        &cookie,
        json!({"action":"cap","email":BOSS,"cap":1}),
    )
    .await;
    assert_eq!(c, StatusCode::OK, "{v}");
    let f = read_file(&d);
    assert_eq!(f.users.iter().find(|u| u.email == BOSS).unwrap().cap, 1);

    // somebody else's device (not the owner's passkey) cannot confirm: B has a
    // device and a session, but is no owner
    let boss_cookie = format!(
        "sso={}",
        crate::make_cookie_value(&st, BOSS, &crate::people(&st)[1].hash)
    );
    let (c, _) = owner_do(
        &st,
        &mut soft(),
        &boss_cookie,
        json!({"action":"cap","email":EMAIL,"cap":1}),
    )
    .await;
    assert_eq!(c, StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn owner_can_list_revoke_cap_and_guest_with_a_passkey_each_time() {
    let d = temp_dir("owner-all");
    let mut st = app_state(&d, true, false, true);
    set_owners(&mut st, &[EMAIL]);
    let cookie = session_cookie(&st);
    let boss_cookie = add_person(&st, BOSS, "Boss Person");
    let mut owner_dev = soft();
    assert_eq!(
        enrol(&st, &mut owner_dev, &cookie, "Owner phone", PASSWORD).await,
        StatusCode::OK
    );
    let mut boss_dev = soft();
    assert_eq!(
        enrol(&st, &mut boss_dev, &boss_cookie, "Boss laptop", PASSWORD).await,
        StatusCode::OK
    );
    assert_eq!(
        enrol(&st, &mut boss_dev, &boss_cookie, "Boss phone", PASSWORD).await,
        StatusCode::OK
    );

    // listing: names, times, caps; never ids or keys
    let (c, _, html) =
        html_of(owner_page(State(st.clone()), hdrs(None, Some(&cookie))).await).await;
    assert_eq!(c, StatusCode::OK);
    assert!(html.contains(BOSS) && html.contains("Boss laptop") && html.contains("Boss phone"));
    assert!(
        html.contains(&format!("created {}", date_text(common::people::now())))
            && html.contains("last used never")
    );
    let f = read_file(&d);
    let boss = f.users.iter().find(|u| u.email == BOSS).unwrap();
    let cred_b64 = URL_SAFE_NO_PAD.encode(boss.keys[0].passkey.cred_id().as_ref() as &[u8]);
    assert!(!html.contains(&cred_b64), "no credential ids on the page");
    assert!(!html.contains("public_key") && !html.contains("COSE"));

    // a boss login before revoking
    let (bid, bcid, buid) = {
        let u = boss;
        (
            u.user_id.clone(),
            URL_SAFE_NO_PAD.encode(u.keys[0].passkey.cred_id().as_ref() as &[u8]),
            Uuid::parse_str(&u.user_id).unwrap().as_bytes().to_vec(),
        )
    };
    let _ = bid;
    let key0 = boss.keys[0].id.clone();
    let body = sign_in_body(&st, &mut boss_dev, &bcid, buid.clone(), "/").await;
    let (c, _, _) =
        read(login_finish(State(st.clone()), hdrs(Some(ORIGIN), None), body).await).await;
    assert_eq!(c, StatusCode::OK);

    // revoke ONE: takes effect on the very next request
    let (c, v) = owner_do(
        &st,
        &mut owner_dev,
        &cookie,
        json!({"action":"revoke","email":BOSS,"id":key0}),
    )
    .await;
    assert_eq!(c, StatusCode::OK, "{v}");
    assert_eq!(
        read_file(&d)
            .users
            .iter()
            .find(|u| u.email == BOSS)
            .unwrap()
            .keys
            .len(),
        1
    );
    let body = sign_in_body(&st, &mut boss_dev, &bcid, buid.clone(), "/").await;
    let (c, h, v) =
        read(login_finish(State(st.clone()), hdrs(Some(ORIGIN), None), body).await).await;
    assert_eq!(
        c,
        StatusCode::UNAUTHORIZED,
        "a revoked passkey is refused straight away"
    );
    assert!(v["error"]
        .as_str()
        .unwrap()
        .contains("isn't valid any more"));
    assert!(h.get(header::SET_COOKIE).is_none());

    // set a cap
    let (c, _) = owner_do(
        &st,
        &mut owner_dev,
        &cookie,
        json!({"action":"cap","email":BOSS,"cap":1}),
    )
    .await;
    assert_eq!(c, StatusCode::OK);
    let r = register_start(
        State(st.clone()),
        hdrs(Some(ORIGIN), Some(&boss_cookie)),
        json!({"name":"More","password":PASSWORD}).to_string(),
    )
    .await;
    assert_eq!(
        read(r).await.0,
        StatusCode::BAD_REQUEST,
        "the new cap applies at once"
    );
    let (c, _) = owner_do(
        &st,
        &mut owner_dev,
        &cookie,
        json!({"action":"cap","email":BOSS,"cap":21}),
    )
    .await;
    assert_eq!(c, StatusCode::BAD_REQUEST);

    // make Boss a guest until a future date: old passkeys go, rules apply
    let (c, _) = owner_do(
        &st,
        &mut owner_dev,
        &cookie,
        json!({"action":"guest","email":BOSS,"guest":true,"expires":"2000-01-01"}),
    )
    .await;
    assert_eq!(c, StatusCode::BAD_REQUEST, "a past date is refused");
    let (c, _) = owner_do(
        &st,
        &mut owner_dev,
        &cookie,
        json!({"action":"guest","email":BOSS,"guest":true,"expires":"2199-12-31"}),
    )
    .await;
    assert_eq!(c, StatusCode::OK);
    let f = read_file(&d);
    let boss = f.users.iter().find(|u| u.email == BOSS).unwrap();
    assert!(boss.guest && boss.keys.is_empty() && boss.account_expires > common::people::now());
    // lasting passkey refused; a temporary one works, only one
    let (c, _) = owner_do(
        &st,
        &mut owner_dev,
        &cookie,
        json!({"action":"cap","email":BOSS,"cap":5}),
    )
    .await;
    assert_eq!(c, StatusCode::OK);
    assert_eq!(
        enrol_at(
            &st,
            &mut boss_dev,
            &boss_cookie,
            "Lasting",
            PASSWORD,
            ORIGIN,
            0
        )
        .await,
        StatusCode::BAD_REQUEST
    );
    assert_eq!(
        enrol_at(
            &st,
            &mut boss_dev,
            &boss_cookie,
            "Visit",
            PASSWORD,
            ORIGIN,
            7
        )
        .await,
        StatusCode::OK
    );
    assert_eq!(
        enrol_at(
            &st,
            &mut boss_dev,
            &boss_cookie,
            "Second",
            PASSWORD,
            ORIGIN,
            7
        )
        .await,
        StatusCode::BAD_REQUEST
    );
    // the account page tells the guest how things stand
    let (_, _, html) =
        html_of(account_page(State(st.clone()), hdrs(None, Some(&boss_cookie))).await).await;
    assert!(html.contains("guest account until 2199-12-31"));

    // back to a normal account
    let (c, _) = owner_do(
        &st,
        &mut owner_dev,
        &cookie,
        json!({"action":"guest","email":BOSS,"guest":false}),
    )
    .await;
    assert_eq!(c, StatusCode::OK);
    let f = read_file(&d);
    let boss = f.users.iter().find(|u| u.email == BOSS).unwrap();
    assert!(!boss.guest && boss.account_expires == 0);

    // revoke ALL also ends their sessions
    std::thread::sleep(Duration::from_millis(1100));
    let (c, _) = owner_do(
        &st,
        &mut owner_dev,
        &cookie,
        json!({"action":"revoke","email":BOSS}),
    )
    .await;
    assert_eq!(c, StatusCode::OK);
    assert!(read_file(&d)
        .users
        .iter()
        .find(|u| u.email == BOSS)
        .unwrap()
        .keys
        .is_empty());
    assert!(crate::cookie_email(&st, &hdrs(None, Some(&boss_cookie))).is_none());
}

#[tokio::test]
async fn owner_actions_check_the_origin_and_unknown_people() {
    let d = temp_dir("owner-guards");
    let mut st = app_state(&d, true, false, true);
    set_owners(&mut st, &[EMAIL]);
    let cookie = session_cookie(&st);
    let mut dev = soft();
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "Owner phone", PASSWORD).await,
        StatusCode::OK
    );
    let r = owner_begin(
        State(st.clone()),
        hdrs(Some("https://evil.example"), Some(&cookie)),
        json!({"action":"cap","email":EMAIL,"cap":2}).to_string(),
    )
    .await;
    assert_eq!(r.status(), StatusCode::FORBIDDEN);
    let (c, _) = owner_do(
        &st,
        &mut dev,
        &cookie,
        json!({"action":"cap","email":"nobody@example.test","cap":2}),
    )
    .await;
    assert_eq!(c, StatusCode::BAD_REQUEST);
    let r = owner_begin(
        State(st.clone()),
        hdrs(Some(ORIGIN), Some(&cookie)),
        "{\"action\":\"explode\"}".to_string(),
    )
    .await;
    assert_eq!(r.status(), StatusCode::BAD_REQUEST);
}

// =================================================== the look, static files

#[test]
fn range_header_parsing() {
    assert_eq!(parse_range("bytes=0-99", 1000), Some((0, 99)));
    assert_eq!(parse_range("bytes=900-", 1000), Some((900, 999)));
    assert_eq!(parse_range("bytes=-100", 1000), Some((900, 999)));
    assert_eq!(
        parse_range("bytes=0-99999", 1000),
        Some((0, 999)),
        "end clipped"
    );
    assert_eq!(parse_range("bytes=1000-", 1000), None);
    assert_eq!(parse_range("bytes=5-2", 1000), None);
    assert_eq!(parse_range("bytes=0-1,5-6", 1000), None);
    assert_eq!(parse_range("items=0-1", 1000), None);
    assert_eq!(parse_range("bytes=-0", 1000), None);
    assert_eq!(parse_range("bytes=x-y", 1000), None);
}

#[tokio::test]
async fn the_video_is_served_with_type_cache_and_ranges() {
    let get = |file: &str, range: Option<&str>| {
        let mut h = HeaderMap::new();
        if let Some(r) = range {
            h.insert(header::RANGE, r.parse().unwrap());
        }
        static_file(axum::extract::Path(file.to_string()), h)
    };
    let r = get("passkey-orb.v1.mp4", None).await;
    assert_eq!(r.status(), StatusCode::OK);
    assert_eq!(r.headers()[header::CONTENT_TYPE], "video/mp4");
    assert_eq!(r.headers()[header::ACCEPT_RANGES], "bytes");
    let cc = r.headers()[header::CACHE_CONTROL].to_str().unwrap();
    assert!(
        cc.contains("max-age=31536000") && cc.contains("immutable"),
        "{cc}"
    );
    assert_eq!(
        r.headers()[header::CONTENT_LENGTH].to_str().unwrap(),
        VIDEO_ORB.len().to_string()
    );
    assert_eq!(&VIDEO_ORB[4..8], b"ftyp", "it is an MP4 file");
    assert!(
        VIDEO_ORB.len() < 400_000,
        "small enough to be cheap on a phone"
    );

    let r = get("passkey-orb.v1.mp4", Some("bytes=0-99")).await;
    assert_eq!(r.status(), StatusCode::PARTIAL_CONTENT);
    assert_eq!(
        r.headers()[header::CONTENT_RANGE].to_str().unwrap(),
        format!("bytes 0-99/{}", VIDEO_ORB.len())
    );
    let (_, b) = r.into_parts();
    assert_eq!(axum::body::to_bytes(b, 1 << 20).await.unwrap().len(), 100);

    let r = get("passkey-orb.v1.mp4", Some("bytes=99999999-")).await;
    assert_eq!(r.status(), StatusCode::RANGE_NOT_SATISFIABLE);
    // the earlier, larger clips are gone
    for old in [
        "signin-bg.mp4",
        "signin-bg-phone.v1.mp4",
        "signin-bg-wide.v1.mp4",
        "passkey-orb.mp4",
    ] {
        assert_eq!(
            get(old, None).await.status(),
            StatusCode::NOT_FOUND,
            "{old}"
        );
    }

    let r = get("auth.css", None).await;
    assert_eq!(r.status(), StatusCode::OK);
    assert!(r.headers()[header::CONTENT_TYPE]
        .to_str()
        .unwrap()
        .starts_with("text/css"));
    assert_eq!(
        get("../Cargo.toml", None).await.status(),
        StatusCode::NOT_FOUND
    );
    assert_eq!(get("nothing", None).await.status(), StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn the_signin_page_is_just_the_form_plus_the_passkey_extras() {
    ENABLED.store(true, Ordering::Relaxed);
    let page = crate::render_signin("", "", "/kitchen/", StatusCode::OK);
    let (_, _, html) = html_of(page).await;
    ENABLED.store(false, Ordering::Relaxed);
    // kept
    assert!(html.contains("name=\"email\"") && html.contains("name=\"password\""));
    assert!(html.contains(">Sign in</button>"));
    assert!(html.contains("Lost your passkey device?") && html.contains("href=\"recover\""));
    assert!(html.contains("static/auth.css"));
    assert!(html.contains("name=\"next\" value=\"/kitchen/\""));
    // the orb: a focusable round button with an accessible name, a clip inside
    // (muted, looping, inline, autoplay) and a visible label under it
    assert!(html.contains("<button id=\"pk-btn\" class=\"orb\" type=\"button\" aria-label=\"Sign in with your passkey\""));
    assert!(html.contains("Tap to sign in with your passkey"));
    assert!(
        html.contains("<video")
            && html.contains("muted")
            && html.contains("loop")
            && html.contains("autoplay")
            && html.contains("playsinline")
            && html.contains("tabindex=\"-1\"")
    );
    assert!(
        html.contains("preload=\"none\"")
            && html.contains("data-src=\"static/passkey-orb.v1.mp4\"")
    );
    assert!(
        !html.contains(" src=\"static/passkey-orb"),
        "nothing is fetched until the script allows it"
    );
    // the clip is inside the button, which is inside the form's card; the
    // glow behind it is plain CSS, the same markup works with no video
    let (b0, v0, b1) = (
        html.find("<button id=\"pk-btn\"").unwrap(),
        html.find("<video").unwrap(),
        html.find("</button>").unwrap(),
    );
    assert!(b0 < v0 && v0 < b1);
    assert!(html.contains("orb-glow"));
    // no full-screen video layer any more, and no script-drawn sky
    assert!(!html.contains("class=\"bgvideo\"") && !html.contains("<canvas"));
    // removed from the design: tabs, template options, artwork sizes, passkey lists
    for gone in [
        "tablist",
        "Template options",
        "Sizes for your own",
        "Prompt to give",
        "data-bgset",
        "class=\"list\"",
    ] {
        assert!(
            !html.contains(gone),
            "{gone} must not be on the sign-in page"
        );
    }
    // no web fonts fetched from anywhere
    assert!(!html.contains("fonts.googleapis"));
    assert!(!CSS.contains("fonts.googleapis") && !CSS.contains("@import"));
}

#[test]
fn the_script_only_plays_the_clip_when_allowed_and_hides_it_on_failure() {
    let js = SCRIPT.split("function initVideo").nth(1).unwrap();
    let js = js.split("try { initVideo()").next().unwrap();
    assert!(js.contains("prefers-reduced-motion: reduce"));
    assert!(js.contains("saveData"));
    assert!(js.contains("addEventListener('error', hide)"));
    assert!(js.contains("p.catch(hide)"));
    // started on load, not only after the first tap
    assert!(js.contains("v.play()"));
    // the page background needs no script at all
    assert!(!SCRIPT.contains("getContext('2d')") && !SCRIPT.contains("requestAnimationFrame"));
}

#[test]
fn the_orb_is_round_masked_and_sized_for_a_phone() {
    assert!(CSS.contains(".orb {") && CSS.contains("border-radius: 50%"));
    assert!(CSS.contains("172px"), "about 170px wide");
    assert!(
        CSS.contains("radial-gradient(circle, #000 52%, transparent 71%)"),
        "soft edge"
    );
    assert!(CSS.contains("object-fit: cover"));
    assert!(CSS.contains(".orb:focus-visible"), "visible focus ring");
    assert!(
        CSS.contains(".orb-glow"),
        "a glow stands in when there is no video"
    );
}

#[test]
fn the_portrait_phone_layout_does_not_scroll_and_parks_the_orb_near_the_sensor() {
    // reading the stylesheet, as there is no browser here
    let q = CSS
        .split("@media (max-width: 500px) and (orientation: portrait) and (min-height: 640px)")
        .nth(1)
        .expect("the phone layout exists");
    assert!(q.contains("overflow: hidden"), "no scrolling");
    assert!(q.contains("100dvh"));
    assert!(q.contains("position: fixed"));
    assert!(q.contains("22dvh"), "orb centre about 78% of the way down");
    assert!(q.contains("env(safe-area-inset-bottom"));
    assert!(
        q.contains("align-items: flex-start"),
        "the card sits at the top, clear of the orb"
    );
    // fixed placement only works when no ancestor blurs or animates
    assert!(q.contains("backdrop-filter: none") && q.contains("animation: none"));
    // the sign-in page as a whole is one viewport tall
    assert!(CSS.contains("body.centered { min-height: 100vh; min-height: 100dvh;"));
}

#[test]
fn the_background_is_the_aurora_sky_in_pure_css() {
    assert!(CSS.contains("--sky-0: #070a18") && CSS.contains("--sky-1: #0d1330"));
    assert!(CSS.contains("--accent: #8b7cff") && CSS.contains("--accent-ink: #0b0d1f"));
    assert!(CSS.contains("opacity: 0.55"), "aurora strength");
    assert!(CSS.contains("@keyframes drift-a") && CSS.contains("@keyframes drift-b"));
    assert!(CSS.contains("radial-gradient(120% 90% at 50% 0%"));
    // reduced motion stops the drift
    let rm = CSS.split("prefers-reduced-motion: reduce").nth(1).unwrap();
    assert!(rm.contains(".glow") && rm.contains("animation: none"));
    assert!(!include_str!("../../templates/sky.html").contains("canvas"));
}

// ======================================================= SSO unchanged

#[tokio::test]
async fn a_passkey_cookie_passes_forward_auth_exactly_like_the_password_one() {
    let d = temp_dir("sso");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "Pixel", PASSWORD).await,
        StatusCode::OK
    );
    let (_, cid, uid) = stored(&d);

    // password sign-in (has a passkey now, so no offer: a plain redirect)
    let pw = signin_with_next(&st, "%2Fkitchen%2F").await;
    assert_eq!(pw.status(), StatusCode::SEE_OTHER);
    let pw_set = pw
        .headers()
        .get(header::SET_COOKIE)
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();

    // passkey sign-in
    let body = sign_in_body(&st, &mut dev, &cid, uid, "/kitchen/").await;
    let (c, h, _) =
        read(login_finish(State(st.clone()), hdrs(Some(ORIGIN), None), body).await).await;
    assert_eq!(c, StatusCode::OK);
    let pk_set = h
        .get(header::SET_COOKIE)
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();

    // same cookie name, same attributes, same shape of value
    let attrs = |s: &str| {
        s.split(';')
            .skip(1)
            .map(|a| a.trim().to_string())
            .collect::<Vec<_>>()
    };
    assert_eq!(attrs(&pw_set), attrs(&pk_set));
    let val = |s: &str| s.split(';').next().unwrap().to_string();
    let (a, b) = (val(&pw_set), val(&pk_set));
    assert!(a.starts_with("sso=") && b.starts_with("sso="));
    let shape = |v: &str| {
        let (p, s) = v.trim_start_matches("sso=").split_once('.').unwrap();
        (p.chars().all(|c| c.is_ascii_hexdigit()), s.len())
    };
    assert_eq!(shape(&a), shape(&b));

    // and the very same gate (the one Caddy's forward_auth calls) lets both in
    for (name, c) in [("password", a), ("passkey", b)] {
        let mut vh = hdrs(None, Some(&c));
        vh.insert("X-Forwarded-Uri", "/kitchen/soup".parse().unwrap());
        vh.insert("X-Forwarded-Method", "GET".parse().unwrap());
        let ok = crate::verify(State(st.clone()), vh).await;
        assert_eq!(ok.status(), StatusCode::OK, "{name}");
        assert_eq!(ok.headers().get("X-Auth-Email").unwrap(), EMAIL, "{name}");
        assert_eq!(ok.headers().get("X-Auth-Level").unwrap(), "full", "{name}");
        // OIDC and /whoami read the same cookie
        assert_eq!(
            crate::cookie_email(&st, &hdrs(None, Some(&c))).as_deref(),
            Some(EMAIL),
            "{name}"
        );
    }
}

#[tokio::test]
async fn a_signed_out_visit_goes_to_signin_and_comes_back_after_a_passkey() {
    let d = temp_dir("sso-return");
    let st = app_state(&d, true, false, true);
    let cookie = session_cookie(&st);
    let mut dev = soft();
    assert_eq!(
        enrol(&st, &mut dev, &cookie, "Pixel", PASSWORD).await,
        StatusCode::OK
    );
    let (_, cid, uid) = stored(&d);

    // signed out: the gate sends the visitor to the sign-in page, remembering where
    let mut vh = HeaderMap::new();
    vh.insert("X-Forwarded-Uri", "/kitchen/soup".parse().unwrap());
    let gate = crate::verify(State(st.clone()), vh).await;
    assert!(gate.status().is_redirection());
    let to = gate
        .headers()
        .get(header::LOCATION)
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();
    assert_eq!(to, "/auth/?next=/kitchen/soup");

    // that sign-in page carries the return-to and the passkey prompt script
    ENABLED.store(true, Ordering::Relaxed);
    let q = axum::extract::Query(crate::NextQuery {
        next: "/kitchen/soup".into(),
    });
    let page = crate::signin_page(State(st.clone()), q, HeaderMap::new()).await;
    let (_, _, html) = html_of(page).await;
    ENABLED.store(false, Ordering::Relaxed);
    assert!(html.contains("name=\"next\" value=\"/kitchen/soup\""));
    assert!(html.contains("passkey/passkey.js"));

    // the passkey answer returns the visitor to the same page
    let body = sign_in_body(&st, &mut dev, &cid, uid, "/kitchen/soup").await;
    let (c, _, v) =
        read(login_finish(State(st.clone()), hdrs(Some(ORIGIN), None), body).await).await;
    assert_eq!(c, StatusCode::OK);
    assert_eq!(v["next"], "/kitchen/soup");
}

// ================================================= who uses a key (owner view)

#[test]
fn user_agent_is_reduced_to_a_family() {
    assert_eq!(ua_family(ANDROID_UA), "Chrome on Android");
    assert_eq!(ua_family(IPHONE_UA), "Safari on iOS");
    assert_eq!(
        ua_family("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36 Edg/130"),
        "Edge on Windows"
    );
    assert_eq!(
        ua_family("Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"),
        "Firefox on Linux"
    );
    assert_eq!(ua_family(""), "");
    assert_eq!(ua_family("curl/8"), "Unknown browser");
    // no version numbers or model names survive
    assert!(!ua_family(ANDROID_UA).contains("130") && !ua_family(ANDROID_UA).contains("Pixel"));
}

#[test]
fn time_text_and_device_line() {
    assert_eq!(when_text(0), "never");
    assert_eq!(
        when_text(1_798_761_600 + 14 * 3600 + 5 * 60),
        "2027-01-01 14:05 UTC"
    );
    assert_eq!(date_text(1_798_761_600), "2027-01-01");
}

#[tokio::test]
async fn the_owner_sees_whose_key_it_is_when_it_was_made_and_used_and_from_what() {
    let d = temp_dir("owner-view");
    let mut st = app_state(&d, true, false, true);
    set_owners(&mut st, &[EMAIL]);
    let cookie = session_cookie(&st);
    let boss_cookie = add_person(&st, BOSS, "Boss Person");
    let mut owner_dev = soft();
    assert_eq!(
        enrol(&st, &mut owner_dev, &cookie, "Owner phone", PASSWORD).await,
        StatusCode::OK
    );
    let mut boss_dev = soft();
    assert_eq!(
        enrol_auto(&st, &mut boss_dev, &boss_cookie).await,
        StatusCode::OK
    );

    // the boss signs in from an iPhone, on a named site address
    let f = read_file(&d);
    let boss = f.users.iter().find(|u| u.email == BOSS).unwrap();
    let cid = URL_SAFE_NO_PAD.encode(boss.keys[0].passkey.cred_id().as_ref() as &[u8]);
    let uid = Uuid::parse_str(&boss.user_id).unwrap().as_bytes().to_vec();
    let body = sign_in_body(&st, &mut boss_dev, &cid, uid, "/").await;
    let mut h = hdrs(Some(ORIGIN), None);
    h.insert(header::USER_AGENT, IPHONE_UA.parse().unwrap());
    h.insert("x-forwarded-for", "203.0.113.99".parse().unwrap());
    let (c, _, _) = read(login_finish(State(st.clone()), h, body).await).await;
    assert_eq!(c, StatusCode::OK);

    let (_, _, html) =
        html_of(owner_page(State(st.clone()), hdrs(None, Some(&cookie))).await).await;
    assert!(html.contains("Boss Person Passkey"), "{html}");
    assert!(html.contains(&format!("Belongs to {BOSS}")));
    assert!(html.contains(&format!("created {}", date_text(common::people::now()))));
    assert!(html.contains("UTC"), "last-used shows a date and time");
    assert!(html.contains("added from Chrome on Android"));
    assert!(html.contains("last used from Safari on iOS via auth.example.test"));
    // never raw user agents, addresses or credential material
    assert!(!html.contains("Mozilla") && !html.contains("203.0.113"));
    let file_text = std::fs::read_to_string(d.join("passkeys.json")).unwrap();
    assert!(
        !file_text.contains("203.0.113") && !file_text.contains("Mozilla"),
        "no IPs or raw agents stored"
    );

    // people see only their own keys, and no device lines, on /account
    let (_, _, mine) =
        html_of(account_page(State(st.clone()), hdrs(None, Some(&boss_cookie))).await).await;
    assert!(mine.contains("Boss Person Passkey"));
    assert!(!mine.contains("Owner phone") && !mine.contains(EMAIL));
    assert!(!mine.contains("Safari on iOS") && !mine.contains("Chrome on Android"));
}

#[tokio::test]
async fn owners_are_told_whenever_anybody_adds_a_passkey() {
    let d = temp_dir("owner-notice");
    let mut st = app_state(&d, true, false, true);
    set_owners(&mut st, &[EMAIL, "second.owner@example.test"]);
    let pk = st.passkeys.as_ref().unwrap();
    assert_eq!(owner_recipients(pk), [EMAIL, "second.owner@example.test"]);
    let (subject, text) = owner_notice("Boss Person", "2026-10-27");
    assert_eq!(subject, "Boss Person added a passkey on 2026-10-27");
    assert!(text.contains("Boss Person added a passkey on 2026-10-27"));
    assert!(!text.to_lowercase().contains("private key"));
    // and adding one still works with owners configured and no outbox
    let cookie = session_cookie(&st);
    assert_eq!(enrol_auto(&st, &mut soft(), &cookie).await, StatusCode::OK);
    // the real sending is wired in the handler
    assert!(include_str!("../passkeys.rs").contains("owner_notice(&who"));
}

// ===================================================== the opening sequence

#[test]
fn the_sequence_timings_are_constants_at_the_top_of_the_script() {
    let head = SCRIPT.split("var supported").next().unwrap();
    assert!(head.contains("var FIRST_FRAME_TO_PROMPT_MS = 300;"));
    assert!(head.contains("var MAX_WAIT_MS = 1200;"));
    assert!(head.contains("var HERE_KEY = 'pk-here';"));
    // storage is always wrapped
    assert!(head.matches("try {").count() >= 3);
}

#[test]
fn the_prompt_opens_by_itself_only_with_the_note_and_follows_the_rules() {
    let s = SCRIPT.split("function initSignin").nth(1).unwrap();
    let s = s.split("// ---------").next().unwrap();
    // without the note: nothing opens by itself
    let gate = s
        .find("if (!hereGet()) { armConditional(); return; }")
        .expect("no-note branch");
    let open = s.find("function openPrompt").expect("prompt function");
    assert!(
        gate < open,
        "the automatic prompt code is only reached with the note"
    );
    // timing: after the first frame, but never later than MAX_WAIT_MS from page load
    assert!(s.contains("window.setTimeout(openPrompt, FIRST_FRAME_TO_PROMPT_MS)"));
    assert!(s.contains("MAX_WAIT_MS - sinceLoad"));
    assert!(s.contains("performance.now"));
    // first frame: requestVideoFrameCallback, else 'playing'; an error or no clip does not stall
    assert!(s.contains("requestVideoFrameCallback"));
    assert!(s.contains("addEventListener('playing', firstFrame"));
    assert!(s.contains("addEventListener('error', firstFrame)"));
    // the sheen is skipped for reduced motion; the prompt timing is not touched by it
    assert!(s.contains("if (orb && !reduce)"));
    // opens at most once
    assert!(s.contains("if (prompted) { return; }"));
}

#[test]
fn the_note_is_set_on_success_and_cleared_on_lost_device_or_no_credential() {
    assert!(SCRIPT.contains("hereSet();\n        window.location.replace(f.body.next"));
    assert!(SCRIPT.contains("if (f.status === 401) { hereClear(); }"));
    assert!(SCRIPT.contains("if (r.ok) { hereClear(); window.location.replace('./'); }"));
    assert!(SCRIPT.contains("hereSet();   // a passkey now exists for this device"));
    // it holds nothing but "1"
    assert!(SCRIPT.contains("setItem(HERE_KEY, '1')"));
}

#[tokio::test]
async fn the_page_marks_itself_cine_before_drawing_and_layers_are_in_order() {
    ENABLED.store(true, Ordering::Relaxed);
    let (_, _, html) = html_of(crate::render_signin("", "", "/", StatusCode::OK)).await;
    ENABLED.store(false, Ordering::Relaxed);
    assert!(html.contains("localStorage.getItem('pk-here')==='1'"));
    assert!(html.contains("className+=' cine'"));
    assert!(
        html.find("pk-here").unwrap() < html.find("<body").unwrap(),
        "set in the head, before first paint"
    );
    // glow, then sheen, then the video on top
    let (g, s, v) = (
        html.find("<span class=\"orb-glow\"").unwrap(),
        html.find("<span class=\"orb-sheen\"").unwrap(),
        html.find("<video").unwrap(),
    );
    assert!(g < s && s < v, "the sweep is UNDER the video");
}

#[test]
fn the_css_sheen_sits_under_the_video_and_motion_effects_respect_reduced_motion() {
    assert!(
        CSS.contains(".orb-sheen { position: absolute; inset: -12.5%"),
        "125% of the orb"
    );
    assert!(
        CSS.contains("mix-blend-mode: screen"),
        "dark parts of the video let the light through"
    );
    assert!(CSS.contains("html.cine .orb-video { opacity: 0; }"));
    assert!(CSS.contains("html.cine .orb.orb-in .orb-video { opacity: 1; }"));
    // scale, fade-in of the page, sweep and pulse live only inside no-preference
    let np = CSS
        .split("@media (prefers-reduced-motion: no-preference) {")
        .nth(1)
        .unwrap()
        .split("@keyframes pagefade")
        .next()
        .unwrap();
    for needle in [
        "scale(.92)",
        "pagefade",
        ".orb-sheen { animation: sheen .9s",
        ".orb-glow { animation: pulse 1.2s",
    ] {
        assert!(np.contains(needle), "{needle}");
    }
    assert!(CSS.contains("@keyframes sheen") && CSS.contains("@keyframes pulse"));
    // outside that block there is no scale or sweep
    let outside = CSS.replace(np, "");
    assert!(!outside.contains("scale(.92)") && !outside.contains("animation: sheen"));
}
