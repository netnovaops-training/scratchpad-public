//! Slipstream — add and remove ONE entry at a time, without replacing the file.
//!
//! WHY THIS EXISTS ALONGSIDE THE WHOLE-FILE UPLOAD IN `main.rs`
//! The rest of the admin replaces a file wholesale. That is the right shape for
//! a settings file you rewrite in one go, and the wrong shape for a LIST you
//! keep adding to. Adding one place you found online should not mean:
//! download places.json, edit it, upload the whole thing back. That is three
//! chances to lose the other forty entries, and the most likely one is "the
//! copy you edited was three days old".
//!
//! So a slipstream upload is a PATCH, not a replacement:
//!
//!     { "add":    [ { ...an entry... }, { ...another... } ],
//!       "remove": [ "an-id", "another-id" ] }
//!
//! Anything not named is left exactly as it was, including hand-written keys at
//! the top of the file that are not part of the list at all.
//!
//! IDENTITY IS THE WHOLE DESIGN
//! Every entry has an `id`. If an upload does not carry one, it is derived from
//! the entry's own name or title. That single rule is what makes this safe to
//! use casually:
//!
//!     * uploading the same place twice UPDATES it, it does not duplicate it
//!     * you can hand an LLM "give me this cafe as JSON", paste the result in,
//!       and correct it later by pasting a better version over the top
//!     * removal is by id, so it never depends on array position — which is the
//!       thing that silently deletes the wrong row six months later
//!
//! WHAT IT DOES NOT DO
//! It does not validate the SHAPE of an entry beyond "it is a JSON object with
//! something to derive an id from". Deciding that a place needs an address, or
//! that a KB article needs steps, belongs to the app that reads the file — it
//! is the one that knows, and it is the one that has to cope when a field is
//! missing anyway. This layer's job is to not lose data.
//!
//! SAFETY IS INHERITED, NOT REBUILT
//! Session, CSRF, the upload size cap, the timestamped backup and the atomic
//! write all come from `main.rs` unchanged. There is no new door here — the
//! streams below are a fixed allowlist exactly like `TARGETS`, so a filename
//! still cannot be expressed by the browser.

use std::path::Path;
use std::sync::Arc;

use askama::Template;
use axum::{
    body::Body,
    extract::{Multipart, State},
    http::{header, HeaderMap, StatusCode},
    response::{Html, IntoResponse, Response},
};
use serde_json::{Map, Value};

use crate::{backups_for, form_field, render_login, touch_session, write_with_backup, AppState};

// -----------------------------------------------------------------------------
//  Icons. Same approach as main.rs: inline SVG path data, hand-written and
//  trusted, rendered unescaped.
// -----------------------------------------------------------------------------
const I_PIN: &str =
    r#"<path d="M12 21s7-6.2 7-11a7 7 0 1 0-14 0c0 4.8 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/>"#;
const I_BOOK: &str = r#"<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H19v3H6.5A2.5 2.5 0 0 1 4 20.5z"/><path d="M8 7.5h7M8 11h5"/>"#;

// -----------------------------------------------------------------------------
//  What may be streamed into, and where it lands.
//
//  A FIXED ALLOWLIST, for the same reason `TARGETS` is one: the browser sends a
//  key from this table, never a filename. `collection` is the property inside
//  the file that holds the list; everything else in the file is left alone.
// -----------------------------------------------------------------------------
pub(crate) struct Stream {
    pub key: &'static str,
    pub file: &'static str,
    /// The property holding the list. A file whose root is a bare array is also
    /// accepted and written back as a bare array — see `Doc`.
    pub collection: &'static str,
    pub label: &'static str,
    pub sub: &'static str,
    pub hint: &'static str,
    pub accent: &'static str,
    pub icon: &'static str,
    /// Where the entry's display name lives, and where its `id` comes from when
    /// the upload does not supply one.
    pub title_field: &'static str,
    /// A second field worth showing in the list, so entries are tellable apart
    /// at a glance. Missing is fine.
    pub sub_field: &'static str,
    /// Fields the reading app actually looks at. Shown on the page so you can
    /// write an entry — or tell a model what to write — without reading source.
    pub shape: &'static str,
    pub example: &'static str,
}

const EX_PLACES: &str = r#"{
  "add": [
    {
      "name": "Walnut Recreation Center",
      "category": "senior_centers",
      "address": "3075 N. Walnut Rd., Las Vegas, NV 89115",
      "phone": "555-0162",
      "hours": "Mon-Fri 8am-8pm, Sat 8am-4pm",
      "good_for": ["indoor", "cards", "classes", "summer_refuge"],
      "cost": "free",
      "notes": "Air conditioned all day. Good in July.",
      "source": "https://www.clarkcountynv.gov/residents/parks_recreation/"
    }
  ],
  "remove": ["a-place-that-closed"]
}"#;

const EX_MOMKB: &str = r#"{
  "add": [
    {
      "title": "Printer says offline but it is switched on",
      "category": "printer",
      "symptoms": ["printer says offline", "printer won't print",
                   "nothing comes out of the printer"],
      "keywords": ["printer", "offline", "hp", "not printing"],
      "steps": [
        "Turn the printer off at the switch on the back and count to ten.",
        "Turn it back on and wait for the lights to settle.",
        "On the computer, open Settings, then Printers, and set it as default."
      ],
      "answer": "It is almost always the printer having lost the Wi-Fi rather than anything you did.",
      "direct": true,
      "source": "Fixed 12 Aug 2026 — ticket TH-0042"
    }
  ],
  "remove": []
}"#;

pub(crate) const STREAMS: &[Stream] = &[
    Stream {
        key: "places",
        file: "places.json",
        collection: "places",
        label: "Places",
        sub: "/dee and the phone line",
        hint: "Somewhere-to-be listings. Add anything you find online and both \
               the phone agent and the booking site can answer from it — the \
               phone line picks up a change within a minute, with no restart.",
        accent: "#e8695a",
        icon: I_PIN,
        title_field: "name",
        sub_field: "address",
        shape: "name, category, address, phone, hours, good_for[], cost, notes, source",
        example: EX_PLACES,
    },
    Stream {
        key: "momkb",
        file: "mom-kb.json",
        collection: "articles",
        label: "Mama's knowledge base",
        sub: "/mama — known fixes",
        hint: "Answers to problems that are already solved. A strong match is \
               answered straight from here with no model call at all; a weaker \
               one is handed to the model as a known-good fix to follow.",
        accent: "#4fc79b",
        icon: I_BOOK,
        title_field: "title",
        sub_field: "category",
        shape: "title, category, symptoms[], keywords[], steps[], answer, direct, source",
        example: EX_MOMKB,
    },
];

pub(crate) fn stream_for(key: &str) -> Option<&'static Stream> {
    STREAMS.iter().find(|s| s.key == key)
}

// -----------------------------------------------------------------------------
//  The file, split into "the list" and "everything else"
// -----------------------------------------------------------------------------

/// One data file, opened for patching.
///
/// `rest` is the point. A places file may well grow a hand-written `meta` block
/// or a `warnings` list at the top; a patch that quietly dropped those would be
/// a data-loss bug that nobody notices until the day it matters.
struct Doc {
    /// True when the file's root is a bare array rather than an object. Written
    /// back in the shape it was found in, so this never rewrites someone's file
    /// into a different structure behind their back.
    bare: bool,
    rest: Map<String, Value>,
    items: Vec<Value>,
}

impl Doc {
    /// A missing file is an empty list, which is a normal first-run state.
    ///
    /// A file that is present but UNPARSEABLE is an error, deliberately. The
    /// tempting alternative — treat it as empty and carry on — would mean one
    /// bad hand-edit silently converts "forty places" into "no places" on the
    /// next upload, and the backup taken during that write would be of the
    /// broken file. Refusing leaves the damage exactly where it is, where it
    /// can still be read and repaired.
    fn load(path: &Path, collection: &str) -> Result<Doc, String> {
        let bytes = match std::fs::read(path) {
            Ok(b) => b,
            Err(_) => {
                return Ok(Doc {
                    bare: false,
                    rest: Map::new(),
                    items: Vec::new(),
                })
            }
        };
        let v: Value = serde_json::from_slice(&bytes).map_err(|e| {
            format!(
                "The file that is live right now is not valid JSON, so it cannot be \
                 patched safely — line {}, column {}: {e}. Nothing was changed. Fix it \
                 by hand in www\\Admin\\data, or replace the whole file from the site \
                 page, then come back.",
                e.line(),
                e.column()
            )
        })?;
        Doc::parse(v, collection)
    }

    /// Split an already-parsed file. Separate from `load` only so the tests at
    /// the bottom of this file can exercise the merge without touching a disk.
    fn parse(v: Value, collection: &str) -> Result<Doc, String> {
        match v {
            Value::Array(items) => Ok(Doc {
                bare: true,
                rest: Map::new(),
                items,
            }),
            Value::Object(mut o) => match o.remove(collection) {
                Some(Value::Array(items)) => Ok(Doc {
                    bare: false,
                    rest: o,
                    items,
                }),
                None => Ok(Doc {
                    bare: false,
                    rest: o,
                    items: Vec::new(),
                }),
                Some(_) => Err(format!(
                    "The live file has a \"{collection}\" key that is not a list, so \
                     this cannot patch it without guessing. Nothing was changed."
                )),
            },
            _ => Err("The live file is valid JSON but is not an object or a list. \
                      Nothing was changed."
                .to_string()),
        }
    }

    fn into_value(mut self, collection: &str) -> Value {
        if self.bare {
            return Value::Array(self.items);
        }
        // A human-readable stamp, so "when did this last change" is answerable
        // from the file itself rather than from a directory listing that a copy
        // or a restore would have reset.
        let fmt = time::macros::format_description!("[year]-[month]-[day] [hour]:[minute]");
        if let Ok(stamp) = common::now_local().format(&fmt) {
            self.rest
                .insert("updated".to_string(), Value::String(stamp));
        }
        self.rest
            .insert(collection.to_string(), Value::Array(self.items));
        Value::Object(self.rest)
    }
}

// -----------------------------------------------------------------------------
//  Identity
// -----------------------------------------------------------------------------

/// "Walnut Recreation Center" -> "walnut-recreation-center"
///
/// Capped at 60 characters because these are shown on a page and typed into a
/// remove box, not because anything downstream cares.
fn slug(s: &str) -> String {
    let mut out = String::new();
    let mut dash = false;
    for c in s.chars() {
        if c.is_ascii_alphanumeric() {
            out.push(c.to_ascii_lowercase());
            dash = false;
        } else if !out.is_empty() && !dash {
            out.push('-');
            dash = true;
        }
        if out.len() >= 60 {
            break;
        }
    }
    out.trim_matches('-').to_string()
}

fn str_field(item: &Value, name: &str) -> String {
    item.get(name)
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string()
}

/// Give an entry its `id`, and hand it back.
///
/// An explicit `id` always wins — that is how you rename a place without the
/// system treating it as a different one.
fn ensure_id(item: &mut Value, s: &Stream, index: usize) -> Result<String, String> {
    let obj = item.as_object_mut().ok_or_else(|| {
        format!("Entry {} is not a JSON object, so it was skipped.", index + 1)
    })?;

    let explicit = obj
        .get("id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let id = if !explicit.is_empty() {
        explicit
    } else {
        let title = obj
            .get(s.title_field)
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        let derived = slug(&title);
        if derived.is_empty() {
            return Err(format!(
                "Entry {} has neither an \"id\" nor a \"{}\", so there is no way to \
                 tell it apart from anything else. It was skipped.",
                index + 1,
                s.title_field
            ));
        }
        derived
    };
    obj.insert("id".to_string(), Value::String(id.clone()));
    Ok(id)
}

// -----------------------------------------------------------------------------
//  Reading a patch
// -----------------------------------------------------------------------------

/// Work out what the uploaded file is asking for.
///
/// DELIBERATELY GENEROUS about shape. The realistic way an entry gets written is
/// "ask a model for this cafe as JSON and paste what it gives you", and a model
/// asked that question returns a bare object about a third of the time, a bare
/// array another third, and the documented `{add, remove}` the rest. Refusing
/// two of those three teaches you to distrust the page rather than teaching the
/// model anything.
fn read_patch(upload: Value, s: &Stream) -> Result<(Vec<Value>, Vec<String>), String> {
    let mut adds: Vec<Value> = Vec::new();
    let mut removes: Vec<String> = Vec::new();

    match upload {
        // [ {...}, {...} ]  — the whole upload is things to add.
        Value::Array(a) => adds = a,

        Value::Object(o) => {
            let mut recognised = false;

            for key in ["add", "adds", "upsert", "items", "new"] {
                if let Some(Value::Array(a)) = o.get(key) {
                    adds.extend(a.iter().cloned());
                    recognised = true;
                }
            }
            for key in ["remove", "removes", "delete", "deleted", "drop"] {
                match o.get(key) {
                    Some(Value::Array(a)) => {
                        for v in a {
                            // Accept "an-id", or a whole entry, or {"id": "..."}
                            // — pasting back the thing you want gone is the
                            // obvious move and it should simply work.
                            let id = match v {
                                Value::String(t) => t.trim().to_string(),
                                Value::Object(_) => {
                                    let explicit = str_field(v, "id");
                                    if explicit.is_empty() {
                                        slug(&str_field(v, s.title_field))
                                    } else {
                                        explicit
                                    }
                                }
                                _ => String::new(),
                            };
                            if !id.is_empty() {
                                removes.push(id);
                            }
                        }
                        recognised = true;
                    }
                    Some(Value::String(t)) => {
                        if !t.trim().is_empty() {
                            removes.push(t.trim().to_string());
                            recognised = true;
                        }
                    }
                    _ => {}
                }
            }

            if !recognised {
                // { "places": [...] } — a whole file, or the relevant slice of
                // one. Treated as "make sure all of these are in there", which
                // is additive: it will NOT delete entries the upload omits.
                if let Some(Value::Array(a)) = o.get(s.collection) {
                    adds = a.clone();
                    recognised = true;
                }
            }

            if !recognised {
                // A single bare entry.
                let looks_like_entry =
                    !str_field(&Value::Object(o.clone()), s.title_field).is_empty()
                        || o.contains_key("id");
                if looks_like_entry {
                    adds.push(Value::Object(o));
                } else {
                    return Err(format!(
                        "That is valid JSON, but nothing in it looked like something to \
                         add or remove. Expected one of: a list of entries, an object \
                         with \"add\" and/or \"remove\", an object with a \"{}\" list, \
                         or a single entry with a \"{}\". Nothing was changed.",
                        s.collection, s.title_field
                    ));
                }
            }
        }

        _ => {
            return Err(
                "That parsed as JSON but is not an object or a list. Nothing was changed."
                    .to_string(),
            )
        }
    }

    if adds.is_empty() && removes.is_empty() {
        return Err("That file asked for nothing — no entries to add, none to remove. \
                    Nothing was changed."
            .to_string());
    }
    Ok((adds, removes))
}

// -----------------------------------------------------------------------------
//  Applying it
// -----------------------------------------------------------------------------

struct Report {
    added: usize,
    updated: usize,
    removed: usize,
    /// Ids the upload asked to remove that were not there. Reported rather than
    /// swallowed: it usually means a typo, and silence would let you believe
    /// something is gone when it is still live.
    not_found: Vec<String>,
    /// Entries that could not be given an id, with the reason.
    skipped: Vec<String>,
    total: usize,
}

fn apply(doc: &mut Doc, adds: Vec<Value>, removes: Vec<String>, s: &Stream) -> Report {
    let mut rep = Report {
        added: 0,
        updated: 0,
        removed: 0,
        not_found: Vec::new(),
        skipped: Vec::new(),
        total: 0,
    };

    for (i, mut item) in adds.into_iter().enumerate() {
        let id = match ensure_id(&mut item, s, i) {
            Ok(id) => id,
            Err(why) => {
                rep.skipped.push(why);
                continue;
            }
        };
        match doc
            .items
            .iter()
            .position(|e| str_field(e, "id") == id)
        {
            Some(pos) => {
                doc.items[pos] = item;
                rep.updated += 1;
            }
            None => {
                doc.items.push(item);
                rep.added += 1;
            }
        }
    }

    for id in removes {
        let before = doc.items.len();
        doc.items.retain(|e| str_field(e, "id") != id);
        let gone = before - doc.items.len();
        if gone == 0 {
            rep.not_found.push(id);
        } else {
            rep.removed += gone;
        }
    }

    rep.total = doc.items.len();
    rep
}

fn describe(rep: &Report, s: &Stream) -> String {
    let mut parts: Vec<String> = Vec::new();
    if rep.added > 0 {
        parts.push(format!("{} added", rep.added));
    }
    if rep.updated > 0 {
        parts.push(format!("{} updated", rep.updated));
    }
    if rep.removed > 0 {
        parts.push(format!("{} removed", rep.removed));
    }
    if parts.is_empty() {
        parts.push("nothing changed".to_string());
    }
    let mut out = format!(
        "{} — {}. {} entries live now in {}.",
        s.label,
        parts.join(", "),
        rep.total,
        s.file
    );
    if !rep.not_found.is_empty() {
        out.push_str(&format!(
            "\nAsked to remove, but not there: {}.",
            rep.not_found.join(", ")
        ));
    }
    for why in &rep.skipped {
        out.push('\n');
        out.push_str(why);
    }
    out
}

// -----------------------------------------------------------------------------
//  The page
// -----------------------------------------------------------------------------

/// Enough to see what is in the file; not so many that the page takes a second
/// to render on a phone. Anything past this is still live and still removable
/// by uploading a `remove` — it just is not listed.
const MAX_LISTED: usize = 400;

pub(crate) struct ItemView {
    pub id: String,
    pub title: String,
    pub sub: String,
}

/// One card on the dashboard.
pub(crate) struct StreamCard {
    pub key: String,
    pub label: String,
    pub sub: String,
    pub accent: String,
    pub icon: String,
    pub status: String,
    pub has_file: bool,
}

#[derive(Template)]
#[template(path = "stream.html")]
struct StreamTemplate {
    csrf: String,
    flash: String,
    flash_ok: bool,
    key: String,
    label: String,
    sub: String,
    hint: String,
    file: String,
    accent: String,
    icon: String,
    shape: String,
    example: String,
    collection: String,
    title_field: String,
    count: usize,
    listed: usize,
    items: Vec<ItemView>,
    backups: Vec<String>,
    has_file: bool,
    error: String,
    year: i32,
}

pub(crate) fn cards(state: &AppState) -> Vec<StreamCard> {
    STREAMS
        .iter()
        .map(|s| {
            let path = state.data_dir.join(s.file);
            let (status, has_file) = match Doc::load(&path, s.collection) {
                Ok(d) if path.exists() => (
                    format!(
                        "{} {}",
                        d.items.len(),
                        if d.items.len() == 1 { "entry" } else { "entries" }
                    ),
                    true,
                ),
                Ok(_) => ("nothing added yet".to_string(), false),
                // A broken live file must be visible from the dashboard. Showing
                // "0 entries" here would look identical to "not started".
                Err(_) => ("file needs attention".to_string(), true),
            };
            StreamCard {
                key: s.key.to_string(),
                label: s.label.to_string(),
                sub: s.sub.to_string(),
                accent: s.accent.to_string(),
                icon: s.icon.to_string(),
                status,
                has_file,
            }
        })
        .collect()
}

fn render(state: &AppState, csrf: &str, s: &'static Stream, flash: &str, ok: bool) -> Response {
    let path = state.data_dir.join(s.file);
    let has_file = path.exists();

    let (items, count, error) = match Doc::load(&path, s.collection) {
        Ok(d) => {
            let count = d.items.len();
            let items: Vec<ItemView> = d
                .items
                .iter()
                .take(MAX_LISTED)
                .map(|e| ItemView {
                    id: str_field(e, "id"),
                    title: {
                        let t = str_field(e, s.title_field);
                        if t.is_empty() {
                            str_field(e, "id")
                        } else {
                            t
                        }
                    },
                    sub: str_field(e, s.sub_field),
                })
                .collect();
            (items, count, String::new())
        }
        Err(e) => (Vec::new(), 0, e),
    };

    let listed = items.len();
    let html = StreamTemplate {
        csrf: csrf.to_string(),
        flash: flash.to_string(),
        flash_ok: ok,
        key: s.key.to_string(),
        label: s.label.to_string(),
        sub: s.sub.to_string(),
        hint: s.hint.to_string(),
        file: s.file.to_string(),
        accent: s.accent.to_string(),
        icon: s.icon.to_string(),
        shape: s.shape.to_string(),
        example: s.example.to_string(),
        collection: s.collection.to_string(),
        title_field: s.title_field.to_string(),
        count,
        listed,
        items,
        backups: backups_for(state, s.file),
        has_file,
        error,
        year: common::current_year(),
    }
    .render()
    .unwrap_or_else(|_| "<h1>template error</h1>".into());
    Html(html).into_response()
}

// -----------------------------------------------------------------------------
//  Handlers
// -----------------------------------------------------------------------------

pub(crate) async fn page(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    axum::extract::Path(key): axum::extract::Path<String>,
) -> Response {
    let Some(csrf) = touch_session(&state, &headers) else {
        return render_login("Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    match stream_for(&key) {
        Some(s) => render(&state, &csrf, s, "", true),
        None => (
            StatusCode::NOT_FOUND,
            Html("<h1>404</h1><p>No such stream.</p>"),
        )
            .into_response(),
    }
}

/// Hand back the whole current file, so an edit starts from what is actually
/// live rather than from what you remember uploading.
pub(crate) async fn download(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    axum::extract::Path(key): axum::extract::Path<String>,
) -> Response {
    if touch_session(&state, &headers).is_none() {
        return render_login("Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    }
    let Some(s) = stream_for(&key) else {
        return (StatusCode::NOT_FOUND, "no such stream").into_response();
    };
    match std::fs::read(state.data_dir.join(s.file)) {
        Ok(bytes) => (
            [
                (header::CONTENT_TYPE, "application/json"),
                (
                    header::CONTENT_DISPOSITION,
                    // A fixed name from the allowlist, never anything the
                    // request supplied.
                    Box::leak(format!("attachment; filename=\"{}\"", s.file).into_boxed_str()),
                ),
            ],
            Body::from(bytes),
        )
            .into_response(),
        Err(_) => (StatusCode::NOT_FOUND, "nothing added yet").into_response(),
    }
}

pub(crate) async fn upload(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    mut multipart: Multipart,
) -> Response {
    let Some(csrf) = touch_session(&state, &headers) else {
        return render_login("Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };

    let mut got_csrf = String::new();
    let mut key = String::new();
    let mut bytes: Vec<u8> = Vec::new();
    let mut pasted = String::new();
    let mut too_big = false;

    while let Ok(Some(field)) = multipart.next_field().await {
        match field.name().unwrap_or("").to_string().as_str() {
            "csrf" => got_csrf = field.text().await.unwrap_or_default(),
            "target" => key = field.text().await.unwrap_or_default(),
            // Typing or pasting JSON straight into the box is the common case
            // here — one place, forty lines — and making that require saving a
            // file first is friction with nothing on the other side of it.
            "paste" => pasted = field.text().await.unwrap_or_default(),
            "file" => {
                if let Ok(b) = field.bytes().await {
                    if b.len() > state.max_upload {
                        too_big = true;
                    } else {
                        bytes = b.to_vec();
                    }
                }
            }
            _ => {}
        }
    }

    let Some(s) = stream_for(&key) else {
        return (
            StatusCode::BAD_REQUEST,
            Html("<h1>Unknown stream</h1><p>Nothing was changed.</p>"),
        )
            .into_response();
    };

    if got_csrf.is_empty() || got_csrf.as_bytes() != csrf.as_bytes() {
        return render(
            &state,
            &csrf,
            s,
            "That request did not carry a valid form token. Reload the page and try again.",
            false,
        );
    }
    if too_big {
        let mb = state.max_upload / (1024 * 1024);
        return render(
            &state,
            &csrf,
            s,
            &format!("That file is larger than the {mb} MB limit. Nothing was changed."),
            false,
        );
    }

    // The file wins if both are given, because attaching a file is the more
    // deliberate act of the two.
    let raw: Vec<u8> = if !bytes.is_empty() {
        bytes
    } else if !pasted.trim().is_empty() {
        pasted.into_bytes()
    } else {
        return render(
            &state,
            &csrf,
            s,
            "Nothing was attached and nothing was pasted.",
            false,
        );
    };

    let parsed: Value = match serde_json::from_slice(&raw) {
        Ok(v) => v,
        Err(e) => {
            return render(
                &state,
                &csrf,
                s,
                &format!(
                    "That is not valid JSON, so nothing was changed.\nLine {}, column {}: {}",
                    e.line(),
                    e.column(),
                    e
                ),
                false,
            )
        }
    };

    let (adds, removes) = match read_patch(parsed, s) {
        Ok(v) => v,
        Err(msg) => return render(&state, &csrf, s, &msg, false),
    };

    let mut doc = match Doc::load(&state.data_dir.join(s.file), s.collection) {
        Ok(d) => d,
        Err(msg) => return render(&state, &csrf, s, &msg, false),
    };

    let rep = apply(&mut doc, adds, removes, s);

    // Nothing landed and everything was rejected: do not take a backup and
    // rewrite the file for a no-op.
    if rep.added == 0 && rep.updated == 0 && rep.removed == 0 {
        return render(&state, &csrf, s, &describe(&rep, s), false);
    }

    write_out(&state, &csrf, s, doc, rep)
}

pub(crate) async fn remove(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let Some(csrf) = touch_session(&state, &headers) else {
        return render_login("Session expired. Sign in again.", StatusCode::UNAUTHORIZED);
    };
    let key = form_field(&body, "target").unwrap_or_default();
    let Some(s) = stream_for(&key) else {
        return (
            StatusCode::BAD_REQUEST,
            Html("<h1>Unknown stream</h1><p>Nothing was changed.</p>"),
        )
            .into_response();
    };
    let got_csrf = form_field(&body, "csrf").unwrap_or_default();
    if got_csrf.is_empty() || got_csrf.as_bytes() != csrf.as_bytes() {
        return render(
            &state,
            &csrf,
            s,
            "That request did not carry a valid form token. Reload the page and try again.",
            false,
        );
    }
    let id = form_field(&body, "id").unwrap_or_default();
    if id.trim().is_empty() {
        return render(&state, &csrf, s, "No entry was named.", false);
    }

    let mut doc = match Doc::load(&state.data_dir.join(s.file), s.collection) {
        Ok(d) => d,
        Err(msg) => return render(&state, &csrf, s, &msg, false),
    };
    let rep = apply(&mut doc, Vec::new(), vec![id.trim().to_string()], s);
    if rep.removed == 0 {
        return render(
            &state,
            &csrf,
            s,
            &format!("There is no entry called \"{}\". Nothing was changed.", id.trim()),
            false,
        );
    }
    write_out(&state, &csrf, s, doc, rep)
}

/// The one place a stream writes to disk.
///
/// PRETTY-PRINTED, not compact. These files are meant to be opened in Notepad
/// and hand-edited — the README promises exactly that — and a 40KB single line
/// is not a file anyone can edit. The size cost is irrelevant at this scale.
fn write_out(
    state: &AppState,
    csrf: &str,
    s: &'static Stream,
    doc: Doc,
    rep: Report,
) -> Response {
    let value = doc.into_value(s.collection);
    let mut bytes = match serde_json::to_vec_pretty(&value) {
        Ok(b) => b,
        Err(e) => {
            return render(
                state,
                csrf,
                s,
                &format!("Could not build the new file, so nothing was changed.\n{e}"),
                false,
            )
        }
    };
    bytes.push(b'\n');

    match write_with_backup(state, s.file, &bytes) {
        Ok(backup) => {
            tracing::info!(
                stream = s.key,
                added = rep.added,
                updated = rep.updated,
                removed = rep.removed,
                total = rep.total,
                "admin: slipstream"
            );
            let note = match backup {
                Some(name) => format!("Previous version kept as {name}."),
                None => "This was the first change, so there was nothing to back up.".to_string(),
            };
            render(state, csrf, s, &format!("{}\n{}", describe(&rep, s), note), true)
        }
        Err(e) => render(
            state,
            csrf,
            s,
            &format!("Could not write the file, so nothing was changed.\n{e}"),
            false,
        ),
    }
}

// -----------------------------------------------------------------------------
//  Tests
//
//  The merge is the part worth testing, because every way it can be wrong is a
//  way to lose data quietly. A 500 you can see; forty places that turned into
//  one you may not notice for a month.
// -----------------------------------------------------------------------------
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const S: &Stream = &STREAMS[0]; // places: collection "places", title "name"

    fn doc(v: Value) -> Doc {
        Doc::parse(v, S.collection).expect("test fixture should parse")
    }

    fn patch(v: Value) -> (Vec<Value>, Vec<String>) {
        read_patch(v, S).expect("test patch should be understood")
    }

    fn names(d: &Doc) -> Vec<String> {
        d.items.iter().map(|e| str_field(e, "name")).collect()
    }

    #[test]
    fn id_is_derived_from_the_name_when_none_is_given() {
        let mut item = json!({ "name": "Walnut Recreation Center" });
        let id = ensure_id(&mut item, S, 0).unwrap();
        assert_eq!(id, "walnut-recreation-center");
        assert_eq!(item["id"], json!("walnut-recreation-center"));
    }

    #[test]
    fn an_explicit_id_wins_so_a_place_can_be_renamed() {
        let mut item = json!({ "id": "walnut", "name": "Walnut Rec Center (renamed)" });
        assert_eq!(ensure_id(&mut item, S, 0).unwrap(), "walnut");
    }

    #[test]
    fn an_entry_with_nothing_to_identify_it_is_refused_not_guessed() {
        let mut item = json!({ "address": "somewhere" });
        assert!(ensure_id(&mut item, S, 0).is_err());
    }

    #[test]
    fn slugs_do_not_run_punctuation_together() {
        assert_eq!(
            slug("Doolittle  Active-Adult Center!"),
            "doolittle-active-adult-center"
        );
        assert_eq!(slug("  --  "), "");
    }

    #[test]
    fn uploading_the_same_place_twice_updates_it_rather_than_duplicating_it() {
        let mut d = doc(json!({ "places": [ { "id": "walnut", "name": "Walnut", "phone": "old" } ] }));
        let (adds, removes) = patch(json!([{ "name": "Walnut", "phone": "new" }]));
        let rep = apply(&mut d, adds, removes, S);

        assert_eq!((rep.added, rep.updated, rep.total), (0, 1, 1));
        assert_eq!(d.items[0]["phone"], json!("new"));
    }

    #[test]
    fn a_patch_leaves_entries_it_does_not_name_alone() {
        let mut d = doc(json!({ "places": [
            { "id": "a", "name": "A" },
            { "id": "b", "name": "B" },
            { "id": "c", "name": "C" }
        ]}));
        let (adds, removes) = patch(json!({ "add": [{ "id": "d", "name": "D" }],
                                            "remove": ["b"] }));
        let rep = apply(&mut d, adds, removes, S);

        assert_eq!((rep.added, rep.removed, rep.total), (1, 1, 3));
        assert_eq!(names(&d), vec!["A", "C", "D"]);
    }

    #[test]
    fn a_removal_that_matches_nothing_is_reported_rather_than_swallowed() {
        let mut d = doc(json!({ "places": [{ "id": "a", "name": "A" }] }));
        let (adds, removes) = patch(json!({ "remove": ["typo"] }));
        let rep = apply(&mut d, adds, removes, S);

        assert_eq!(rep.removed, 0);
        assert_eq!(rep.not_found, vec!["typo"]);
        assert_eq!(rep.total, 1, "the real entry must still be there");
    }

    #[test]
    fn hand_written_keys_outside_the_list_survive_a_patch() {
        let mut d = doc(json!({
            "meta": { "region": "North Las Vegas", "verified": "2026-08-18" },
            "warnings": ["call ahead in summer"],
            "places": [{ "id": "a", "name": "A" }]
        }));
        let (adds, removes) = patch(json!([{ "name": "B" }]));
        apply(&mut d, adds, removes, S);
        let out = d.into_value(S.collection);

        assert_eq!(out["meta"]["region"], json!("North Las Vegas"));
        assert_eq!(out["warnings"][0], json!("call ahead in summer"));
        assert_eq!(out["places"].as_array().unwrap().len(), 2);
        assert!(
            out["updated"].is_string(),
            "a patch should stamp when it happened"
        );
    }

    #[test]
    fn a_file_whose_root_is_a_bare_list_is_written_back_as_one() {
        let mut d = doc(json!([{ "id": "a", "name": "A" }]));
        let (adds, removes) = patch(json!([{ "name": "B" }]));
        apply(&mut d, adds, removes, S);
        let out = d.into_value(S.collection);

        assert!(out.is_array(), "must not silently restructure someone's file");
        assert_eq!(out.as_array().unwrap().len(), 2);
    }

    #[test]
    fn a_live_file_that_is_not_valid_json_refuses_rather_than_starting_over() {
        // The dangerous alternative is treating it as empty: the patch would
        // then "succeed" and the backup taken during that write would be of the
        // broken file, so the real data would be gone from both places.
        assert!(Doc::parse(json!("just a string"), "places").is_err());
        assert!(Doc::parse(json!({ "places": "not a list" }), "places").is_err());
    }

    #[test]
    fn a_missing_key_is_an_empty_list_not_an_error() {
        let d = Doc::parse(json!({ "meta": {} }), "places").unwrap();
        assert!(d.items.is_empty());
    }

    // --- the shapes a patch may arrive in ------------------------------------

    #[test]
    fn a_bare_list_is_treated_as_things_to_add() {
        let (adds, removes) = patch(json!([{ "name": "A" }, { "name": "B" }]));
        assert_eq!(adds.len(), 2);
        assert!(removes.is_empty());
    }

    #[test]
    fn a_single_bare_entry_is_treated_as_one_thing_to_add() {
        let (adds, _) = patch(json!({ "name": "A", "address": "1 Some St" }));
        assert_eq!(adds.len(), 1);
    }

    #[test]
    fn a_whole_file_is_treated_as_additive_and_never_deletes_what_it_omits() {
        let mut d = doc(json!({ "places": [{ "id": "kept", "name": "Kept" }] }));
        let (adds, removes) = patch(json!({ "places": [{ "name": "New" }] }));
        let rep = apply(&mut d, adds, removes, S);

        assert_eq!(rep.total, 2, "omitting an entry must not remove it");
        assert!(names(&d).contains(&"Kept".to_string()));
    }

    #[test]
    fn pasting_back_a_whole_entry_removes_it() {
        let mut d = doc(json!({ "places": [{ "id": "a", "name": "A" }] }));
        let (adds, removes) = patch(json!({ "remove": [{ "name": "A" }] }));
        let rep = apply(&mut d, adds, removes, S);
        assert_eq!((rep.removed, rep.total), (1, 0));
    }

    #[test]
    fn removing_one_thing_may_be_written_as_a_plain_string() {
        let (_, removes) = patch(json!({ "remove": "a" }));
        assert_eq!(removes, vec!["a"]);
    }

    #[test]
    fn json_that_asks_for_nothing_is_refused_rather_than_silently_doing_nothing() {
        assert!(read_patch(json!({ "hello": "world" }), S).is_err());
        assert!(read_patch(json!({ "add": [], "remove": [] }), S).is_err());
        assert!(read_patch(json!(42), S).is_err());
    }

    #[test]
    fn one_bad_entry_does_not_stop_the_good_ones_landing() {
        let mut d = doc(json!({ "places": [] }));
        let (adds, removes) = patch(json!([{ "name": "Good" }, { "address": "no name" }]));
        let rep = apply(&mut d, adds, removes, S);

        assert_eq!((rep.added, rep.total), (1, 1));
        assert_eq!(rep.skipped.len(), 1, "and the skip is reported, not hidden");
    }

    #[test]
    fn the_kb_stream_identifies_entries_by_title() {
        let kb = stream_for("momkb").expect("the momkb stream should exist");
        let mut item = json!({ "title": "Printer says offline" });
        assert_eq!(ensure_id(&mut item, kb, 0).unwrap(), "printer-says-offline");
    }

    #[test]
    fn every_shipped_example_is_valid_json_that_this_code_understands() {
        // The example on each page is what people copy. One that does not parse
        // teaches them the page is broken.
        for s in STREAMS {
            let v: Value = serde_json::from_str(s.example)
                .unwrap_or_else(|e| panic!("{} example is not valid JSON: {e}", s.key));
            read_patch(v, s)
                .unwrap_or_else(|e| panic!("{} example is not understood: {e}", s.key));
        }
    }

    #[test]
    fn the_page_renders_for_every_stream() {
        // askama checks the template at COMPILE time, but `render()` still
        // returns a Result at run time. Without this, a page that cannot render
        // shows up as "<h1>template error</h1>" in a browser rather than as a
        // build failure.
        for s in STREAMS {
            let t = StreamTemplate {
                csrf: "token".into(),
                flash: "1 added, 1 removed.".into(),
                flash_ok: true,
                key: s.key.into(),
                label: s.label.into(),
                sub: s.sub.into(),
                hint: s.hint.into(),
                file: s.file.into(),
                accent: s.accent.into(),
                icon: s.icon.into(),
                shape: s.shape.into(),
                example: s.example.into(),
                collection: s.collection.into(),
                title_field: s.title_field.into(),
                count: 2,
                listed: 2,
                items: vec![
                    ItemView { id: "a".into(), title: "A place".into(), sub: "1 Some St".into() },
                    // An apostrophe in a name used to break the Remove button's
                    // confirm() dialogue. It is not interpolated any more; this
                    // is what keeps it that way.
                    ItemView { id: "b".into(), title: "O'Brien's Cafe".into(), sub: String::new() },
                ],
                backups: vec!["2026-08-21 at 14:33".into()],
                has_file: true,
                error: String::new(),
                year: 2026,
            };
            let html = t.render().unwrap_or_else(|e| panic!("{} page: {e}", s.key));
            // Not the label: "Mama's knowledge base" is escaped on the way out,
            // which is correct and would make a naive contains() fail.
            assert!(html.contains(s.file), "{} page lost its filename", s.key);
            assert!(html.contains(s.shape), "{} page lost the field list", s.key);
            assert!(!html.contains("O'Brien"),
                    "an apostrophe must be escaped, not written raw into the page");
            assert!(html.contains("O&#") && html.contains("Brien"),
                    "...but the name itself should still be there, escaped");
            assert!(!html.contains("confirm('Remove O"),
                    "an entry name must never reach the confirm() string");
        }
    }
}
