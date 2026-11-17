//! HTML for the scratchpad. Hand-written strings rather than templates: three small pages, and
//! every value that came from a file goes through `esc` (code is shown, never interpreted).

use crate::scan::Script;
use crate::share::Share;

pub fn esc(s: &str) -> String {
    let mut o = String::with_capacity(s.len() + 16);
    for c in s.chars() {
        match c {
            '&' => o.push_str("&amp;"),
            '<' => o.push_str("&lt;"),
            '>' => o.push_str("&gt;"),
            '"' => o.push_str("&quot;"),
            '\'' => o.push_str("&#39;"),
            _ => o.push(c),
        }
    }
    o
}

/// For query strings: keep [A-Za-z0-9-._~/] and percent-encode the rest.
pub fn qs(s: &str) -> String {
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' | b'/' => (b as char).to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}

pub fn date(ts: i64) -> String {
    time::OffsetDateTime::from_unix_timestamp(ts)
        .map(|d| format!("{:04}-{:02}-{:02}", d.year(), d.month() as u8, d.day()))
        .unwrap_or_default()
}

const STYLE: &str = r#"
:root{--bg:#fbfbf8;--ink:#1d2a20;--muted:#5d6b60;--line:#dde3dc;--card:#fff;--accent:#2f6f4f;--code:#f4f6f2}
@media (prefers-color-scheme:dark){:root{--bg:#121512;--ink:#e6ece6;--muted:#9aa79c;--line:#2a322b;--card:#181c18;--accent:#7fc49b;--code:#0e110e}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:20px 16px 48px}a{color:var(--accent)}
header{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;justify-content:space-between}
h1{font-size:1.35rem;margin:0}h2{font-size:1.05rem;margin:22px 0 8px}.muted{color:var(--muted);font-size:.85rem}
form.search{display:flex;gap:8px;margin:14px 0}input[type=search]{flex:1;min-width:0;padding:9px 11px;font-size:15px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink)}
button,.btn{padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);font-size:14px;cursor:pointer;text-decoration:none;display:inline-block}
button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}.chips a{font-size:.8rem;padding:3px 9px;border:1px solid var(--line);border-radius:99px;text-decoration:none}
.chips a.on{background:var(--accent);color:#fff;border-color:var(--accent)}
ul.list{list-style:none;margin:0;padding:0}ul.list li{border-bottom:1px solid var(--line);padding:9px 2px}
ul.list a.name{font-family:ui-monospace,Consolas,monospace;font-size:.92rem;word-break:break-all}
.sum{color:var(--muted);font-size:.86rem;margin-top:2px}.tag{font-size:.72rem;border:1px solid var(--line);border-radius:5px;padding:0 5px;margin-left:6px;color:var(--muted)}
.doc,.notes{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;white-space:pre-wrap;font-size:.9rem;overflow-wrap:anywhere}
.code{background:var(--code);border:1px solid var(--line);border-radius:10px;overflow:auto;font:13px/1.5 ui-monospace,Consolas,monospace}
.code table{border-collapse:collapse;width:100%}.code td{padding:0 10px;vertical-align:top;white-space:pre}
.code td.n{color:var(--muted);text-align:right;user-select:none;border-right:1px solid var(--line);width:1%}
.bar{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}#msg{font-size:.88rem;margin:6px 0;overflow-wrap:anywhere}
.snip{font-family:ui-monospace,Consolas,monospace;font-size:.8rem;color:var(--muted);white-space:pre-wrap;overflow-wrap:anywhere}
"#;

pub fn layout(title: &str, body: &str) -> String {
    format!(
        "<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content=\"width=device-width,initial-scale=1\">\
         <meta name=robots content=\"noindex,nofollow\"><title>{}</title><style>{STYLE}</style></head>\
         <body><div class=wrap>{body}</div></body></html>",
        esc(title)
    )
}

fn summary(s: &Script) -> String {
    let first = s.doc.lines().find(|l| !l.trim().is_empty()).unwrap_or("");
    if first.is_empty() { "No header documentation yet.".into() } else { first.chars().take(160).collect() }
}

pub fn index(base: &str, scripts: &[&Script], total: usize, sources: &[(String, usize)],
             q: &str, src: &str, shares: usize) -> String {
    let mut b = format!(
        "<header><h1>Scratchpad</h1><span class=muted>{total} scripts · <a href=\"{base}/shares\">{shares} shared links</a> · \
         <a href=\"{base}/?rescan=1\">rescan</a></span></header>\
         <form class=search method=get action=\"{base}/\"><input type=search name=q value=\"{}\" placeholder=\"Search names, notes and code (all words must match)\" autofocus>\
         {}<button class=primary>Search</button></form><div class=chips><a href=\"{base}/?q={}\" class=\"{}\">all</a>",
        esc(q),
        if src.is_empty() { String::new() } else { format!("<input type=hidden name=src value=\"{}\">", esc(src)) },
        qs(q),
        if src.is_empty() { "on" } else { "" }
    );
    for (name, n) in sources {
        b += &format!(
            "<a href=\"{base}/?src={}&amp;q={}\" class=\"{}\">{} ({n})</a>",
            qs(name), qs(q), if src == name { "on" } else { "" }, esc(name)
        );
    }
    b += "</div>";
    b += &format!("<p class=muted>{} shown</p><ul class=list>", scripts.len());
    let first_term = q.split_whitespace().next().unwrap_or("").to_lowercase();
    for s in scripts {
        b += &format!(
            "<li><a class=name href=\"{base}/view?id={}\">{}</a><span class=tag>{}</span><span class=tag>{} lines</span><span class=tag>{}</span>\
             <div class=sum>{}</div>",
            qs(&s.id), esc(&s.id), s.lang, s.lines, date(s.modified), esc(&summary(s))
        );
        if !first_term.is_empty() {
            if let Some((n, l)) = s.text.lines().enumerate().find(|(_, l)| l.to_lowercase().contains(&first_term)) {
                b += &format!("<div class=snip>{}: {}</div>", n + 1, esc(&l.trim().chars().take(160).collect::<String>()));
            }
        }
        b += "</li>";
    }
    b += "</ul>";
    layout("Scratchpad", &b)
}

fn code_table(text: &str) -> String {
    let mut t = String::from("<div class=code><table>");
    for (i, l) in text.lines().enumerate() {
        t += &format!("<tr><td class=n>{}</td><td>{}</td></tr>", i + 1, esc(l));
    }
    t + "</table></div>"
}

/// The owner's view: docs, code, copy / download / share.
pub fn view(base: &str, s: &Script, blockers: &[&str]) -> String {
    let notes = s.notes.as_deref().map(|n| format!("<h2>Notes (docs/code)</h2><div class=notes>{}</div>", esc(n))).unwrap_or_default();
    let share = if blockers.is_empty() {
        "<button class=primary id=share>Share</button>".to_string()
    } else {
        format!("<button disabled title=\"Not shareable\">Share</button><span class=muted>Not shareable: it contains {}.</span>", esc(&blockers.join(", ")))
    };
    let body = format!(
        "<header><h1 style=\"word-break:break-all\">{}</h1><a href=\"{base}/\">all scripts</a></header>\
         <p class=muted>{} · {} lines · changed {}</p>\
         <h2>What it does</h2><div class=doc>{}</div>{notes}\
         <div class=bar><button id=copy>Copy code</button><a class=btn href=\"{base}/raw?id={}\" download>Download</a>{share}</div><div id=msg></div>\
         <h2>Code</h2>{}<script>{}</script>",
        esc(&s.id), s.lang, s.lines, date(s.modified),
        esc(if s.doc.is_empty() { "No header documentation in this file yet." } else { &s.doc }),
        qs(&s.id), code_table(&s.text), owner_js(base, &s.id)
    );
    layout(&s.id, &body)
}

fn owner_js(base: &str, id: &str) -> String {
    let id_json = serde_json::to_string(id).unwrap_or_default();
    format!(
        r#"const ID={id_json},msg=document.getElementById('msg');
document.getElementById('copy').onclick=async()=>{{const r=await fetch('{base}/raw?id='+encodeURIComponent(ID));await navigator.clipboard.writeText(await r.text());msg.textContent='Code copied.';}};
const sh=document.getElementById('share');if(sh)sh.onclick=async()=>{{
 const r=await fetch('{base}/api/share',{{method:'POST',headers:{{'content-type':'application/json','x-scratchpad':'1'}},body:JSON.stringify({{id:ID}})}});
 const j=await r.json();if(!r.ok){{msg.textContent=j.error||'Could not share.';return;}}
 msg.innerHTML='';const a=document.createElement('a');a.href=j.url;a.textContent=j.url;msg.append('Read-only link (30 days): ',a);
 try{{await navigator.clipboard.writeText(j.url);msg.append(' — copied.');}}catch(e){{}}
 if(navigator.share){{try{{await navigator.share({{title:ID,url:j.url}});}}catch(e){{}}}}
}};"#
    )
}

/// What someone with a share link sees: no navigation, no other scripts.
pub fn shared(base: &str, s: &Script, sh: &Share) -> String {
    let body = format!(
        "<header><h1 style=\"word-break:break-all\">{}</h1><span class=muted>shared read-only · link ends {}</span></header>\
         <h2>What it does</h2><div class=doc>{}</div>\
         <div class=bar><a class=btn href=\"{base}/shared/{}/raw\" download>Download</a></div><h2>Code</h2>{}",
        esc(s.id.rsplit('/').next().unwrap_or(&s.id)), date(sh.expires),
        esc(if s.doc.is_empty() { "No header documentation in this file." } else { &s.doc }),
        esc(&sh.token), code_table(&s.text)
    );
    layout(s.id.rsplit('/').next().unwrap_or("script"), &body)
}

pub fn shares(base: &str, list: &[(Share, bool)]) -> String {
    let mut b = format!("<header><h1>Shared links</h1><a href=\"{base}/\">all scripts</a></header><ul class=list>");
    if list.is_empty() {
        b += "<li class=muted>Nothing shared.</li>";
    }
    for (s, live) in list {
        b += &format!(
            "<li><a class=name href=\"{base}/shared/{}\">{}</a><span class=tag>made {}</span><span class=tag>ends {}</span>{}\
             <div class=bar><button data-t=\"{}\" class=rv>Revoke</button></div></li>",
            esc(&s.token), esc(&s.id), date(s.created), date(s.expires),
            if *live { "" } else { "<span class=tag>not working (script gone or changed)</span>" }, esc(&s.token)
        );
    }
    b += &format!(
        "</ul><script>document.querySelectorAll('.rv').forEach(b=>b.onclick=async()=>{{await fetch('{base}/api/unshare',{{method:'POST',headers:{{'content-type':'application/json','x-scratchpad':'1'}},body:JSON.stringify({{token:b.dataset.t}})}});location.reload();}});</script>"
    );
    layout("Shared links", &b)
}
