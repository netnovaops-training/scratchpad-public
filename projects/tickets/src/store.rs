//! Ticket storage: one pretty-printed JSON file per ticket in DATA_DIR/tickets/T-011.json.
//!
//! Plain files on purpose: easy to read, back up and fix by hand. Writes go to a temp file and
//! are renamed into place, so a crash never leaves half a ticket. One lock serializes writes
//! (a household's worth of tickets needs nothing faster).

use serde_json::{Map, Value};
use std::path::{Path, PathBuf};
use tokio::sync::Mutex;

pub const KINDS: [&str; 3] = ["fix", "note", "project"];

/// Fields only the server writes. A save from the page never changes them, so an old copy of
/// a ticket open in another tab can't wipe out its GitHub/Drive links.
pub const SERVER_FIELDS: [&str; 14] = [
    "id", "num", "kind", "created", "createdBy", "updated", "updatedBy",
    "githubUrl", "githubRepo", "githubAt", "driveId", "driveUrl", "driveAt", "driveSyncedAt",
];

/// "T-" and 3 to 6 digits. Everything that builds a file path from an id checks this first.
pub fn valid_id(id: &str) -> bool {
    id.strip_prefix("T-")
        .is_some_and(|n| (3..=6).contains(&n.len()) && n.bytes().all(|b| b.is_ascii_digit()))
}

pub fn now() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
}

pub struct Store {
    dir: PathBuf,
    first_number: u64,
    lock: Mutex<()>,
}

impl Store {
    pub fn open(dir: &Path, first_number: u64) -> std::io::Result<Self> {
        std::fs::create_dir_all(dir.join("tickets"))?;
        std::fs::create_dir_all(dir.join("deleted"))?;
        Ok(Self { dir: dir.to_path_buf(), first_number, lock: Mutex::new(()) })
    }

    fn path(&self, id: &str) -> PathBuf {
        self.dir.join("tickets").join(format!("{id}.json"))
    }

    pub async fn list(&self) -> std::io::Result<Vec<Value>> {
        let mut out = Vec::new();
        let mut rd = tokio::fs::read_dir(self.dir.join("tickets")).await?;
        while let Some(e) = rd.next_entry().await? {
            let p = e.path();
            let name = p.file_name().and_then(|n| n.to_str()).unwrap_or_default();
            if !name.ends_with(".json") || !valid_id(name.trim_end_matches(".json")) {
                continue;
            }
            if let Ok(v) = tokio::fs::read(&p).await.map(|b| serde_json::from_slice::<Value>(&b)) {
                match v {
                    Ok(v) => out.push(v),
                    Err(e) => tracing::warn!("skipping unreadable {name}: {e}"),
                }
            }
        }
        Ok(out)
    }

    pub async fn get(&self, id: &str) -> Option<Value> {
        if !valid_id(id) {
            return None;
        }
        let b = tokio::fs::read(self.path(id)).await.ok()?;
        serde_json::from_slice(&b).ok()
    }

    async fn write(&self, id: &str, v: &Value) -> std::io::Result<()> {
        let tmp = self.dir.join("tickets").join(format!(".{id}.tmp"));
        tokio::fs::write(&tmp, serde_json::to_vec_pretty(v)?).await?;
        tokio::fs::rename(&tmp, self.path(id)).await
    }

    /// The highest number ever handed out, so a deleted ticket's number is never reused.
    async fn last_number(&self) -> u64 {
        tokio::fs::read_to_string(self.dir.join("last-number"))
            .await
            .ok()
            .and_then(|s| s.trim().parse().ok())
            .unwrap_or(0)
    }

    pub async fn create(&self, kind: &str, mut body: Map<String, Value>, by: &str) -> std::io::Result<Value> {
        let _guard = self.lock.lock().await;
        let highest = self.list().await?.iter().filter_map(|t| t.get("num").and_then(Value::as_u64)).max().unwrap_or(0);
        let num = (highest.max(self.last_number().await) + 1).max(self.first_number);
        let id = format!("T-{num:03}");
        for k in SERVER_FIELDS {
            body.remove(k);
        }
        let now = now();
        body.insert("id".into(), id.clone().into());
        body.insert("num".into(), num.into());
        body.insert("kind".into(), kind.into());
        body.insert("created".into(), now.clone().into());
        body.insert("createdBy".into(), by.into());
        body.insert("updated".into(), now.into());
        body.insert("updatedBy".into(), by.into());
        let v = Value::Object(body);
        self.write(&id, &v).await?;
        tokio::fs::write(self.dir.join("last-number"), num.to_string()).await?;
        Ok(v)
    }

    /// Replaces the page-owned fields; server-owned fields keep their stored values.
    pub async fn save(&self, id: &str, mut body: Map<String, Value>, by: &str) -> std::io::Result<Option<Value>> {
        let _guard = self.lock.lock().await;
        let Some(Value::Object(old)) = self.get(id).await else { return Ok(None) };
        for k in SERVER_FIELDS {
            body.remove(k);
            if let Some(v) = old.get(k) {
                body.insert(k.into(), v.clone());
            }
        }
        body.insert("updated".into(), now().into());
        body.insert("updatedBy".into(), by.into());
        let v = Value::Object(body);
        self.write(id, &v).await?;
        Ok(Some(v))
    }

    /// Sets server-owned fields (GitHub and Drive links) without touching the rest.
    pub async fn set_fields(&self, id: &str, fields: Map<String, Value>) -> std::io::Result<Option<Value>> {
        let _guard = self.lock.lock().await;
        let Some(Value::Object(mut t)) = self.get(id).await else { return Ok(None) };
        t.extend(fields);
        let v = Value::Object(t);
        self.write(id, &v).await?;
        Ok(Some(v))
    }

    /// Moves the ticket to DATA_DIR/deleted/ rather than erasing it: a mis-click is recoverable.
    pub async fn delete(&self, id: &str) -> std::io::Result<bool> {
        let _guard = self.lock.lock().await;
        if !valid_id(id) || tokio::fs::metadata(self.path(id)).await.is_err() {
            return Ok(false);
        }
        let stamp = chrono::Utc::now().format("%Y%m%dT%H%M%S");
        tokio::fs::rename(self.path(id), self.dir.join("deleted").join(format!("{id}-{stamp}.json"))).await?;
        Ok(true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn temp_store(first: u64) -> (Store, PathBuf) {
        let dir = std::env::temp_dir().join(format!("tickets-test-{}", rand::random::<u64>()));
        (Store::open(&dir, first).unwrap(), dir)
    }

    fn obj(v: Value) -> Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    #[test]
    fn ids_must_be_t_and_digits() {
        assert!(valid_id("T-011"));
        assert!(valid_id("T-1234"));
        assert!(!valid_id("T-11"));
        assert!(!valid_id("B-F001"));
        assert!(!valid_id("T-../../etc"));
        assert!(!valid_id("T-0111111"));
    }

    #[tokio::test]
    async fn numbers_start_at_first_number_and_never_repeat() {
        let (s, dir) = temp_store(11);
        let a = s.create("fix", obj(json!({"title": "a"})), "me@x").await.unwrap();
        assert_eq!(a["id"], "T-011");
        let b = s.create("note", obj(json!({"title": "b"})), "me@x").await.unwrap();
        assert_eq!(b["id"], "T-012");
        assert!(s.delete("T-012").await.unwrap());
        let c = s.create("project", obj(json!({})), "me@x").await.unwrap();
        assert_eq!(c["id"], "T-013", "a deleted number is not handed out again");
        assert_eq!(s.list().await.unwrap().len(), 2);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[tokio::test]
    async fn saves_keep_server_fields() {
        let (s, dir) = temp_store(1);
        s.create("fix", obj(json!({"title": "a", "id": "T-999", "num": 999})), "me@x").await.unwrap();
        s.set_fields("T-001", obj(json!({"driveId": "abc", "githubUrl": "https://g"}))).await.unwrap();
        let saved = s
            .save("T-001", obj(json!({"title": "new", "driveId": "evil", "kind": "note"})), "you@x")
            .await
            .unwrap()
            .unwrap();
        assert_eq!(saved["title"], "new");
        assert_eq!(saved["driveId"], "abc");
        assert_eq!(saved["githubUrl"], "https://g");
        assert_eq!(saved["kind"], "fix");
        assert_eq!(saved["num"], 1);
        assert_eq!(saved["updatedBy"], "you@x");
        assert_eq!(saved["createdBy"], "me@x");
        assert!(s.save("T-404", obj(json!({})), "me@x").await.unwrap().is_none());
        let _ = std::fs::remove_dir_all(dir);
    }
}
