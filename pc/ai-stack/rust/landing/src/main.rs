//! example.org — the landing page.
//!
//! A straight port of the old `index.php`. Same markup, same CSS, same live
//! canvas background, byte for byte where it matters — this replaces the PHP
//! entirely, so the `php` container can go.
//!
//! Two things are genuinely better than the PHP was, and both come free with
//! the language rather than from trying harder:
//!
//!   * **The template is checked at compile time.** Askama turns
//!     `templates/index.html` into Rust code during the build. A typo in a
//!     field name, a `{% for %}` you forgot to close, a variable that doesn't
//!     exist — none of those can reach the server, because the build fails.
//!     PHP would have served you a blank page at 2am instead.
//!
//!   * **The page is rendered once, at startup, into a String.** It has no
//!     inputs — no query params, no database, no session — so re-rendering it
//!     per request was always pure waste. Requests now just hand back bytes
//!     that already exist.
//!
//! Adding a section is still a one-place change: add an entry to `sections()`.

mod brand;
mod looks;

use std::path::PathBuf;
use std::sync::Arc;

use askama::Template;
use axum::{
    extract::State,
    http::{header, HeaderMap, HeaderValue, StatusCode},
    response::{Html, IntoResponse, Redirect},
    routing::get,
    Router,
};
use tower_http::{compression::CompressionLayer, services::ServeDir, set_header::SetResponseHeaderLayer};

/// One card on the page.
#[derive(Clone)]
struct Section {
    name: &'static str,
    /// The small uppercase line above the title.
    sub: &'static str,
    /// String rather than &'static str since 2026-08-31: the Engine Bay
    /// card's description is a LIVE drive-usage bar rebuilt every render,
    /// and one dynamic card means the field carries owned text for all.
    desc: String,
    href: &'static str,
    accent: &'static str,
    /// Inner SVG for the icon. Trusted, hand-written, rendered unescaped.
    icon: &'static str,
}

/// One card on somebody's own home page, tagged with the app that gates it.
///
/// THE TAG IS THE POINT. `app` is a key from common::people::ALL_APPS -- the
/// same key the Caddyfile gates on and the same one /admin/people toggles. So
/// the page cannot drift from the permissions: a card appears exactly when its
/// owner can actually open it.
///
/// WHAT THIS IS NOT. Hiding a card is presentation, not protection. Every one
/// of these paths is still gated in Caddy, and would still refuse somebody who
/// typed the URL directly. If this filter were removed tomorrow the site would
/// be untidy, not insecure -- which is the correct order for those two to fail
/// in, and worth keeping that way.
struct HomeCard {
    app: &'static str,
    /// The shelf this card lives on (2026-09-01): a busy page is laid out
    /// category by category -- Projects, Family & house, Dashboards,
    /// Administrative -- with the category stamped into the eyebrow. A
    /// page with only a few cards stays plain; headings over three cards
    /// would be organisation theatre.
    group: &'static str,
    section: Section,
}

/// Category order on a busy page. Projects first -- the things he BUILT
/// outrank the things he merely runs.
const GROUP_ORDER: [&str; 4] = [
    "Projects",
    "Family & house",
    "Dashboards",
    "Administrative",
];

// The real marks of the software behind the cards, fetched from the running
// containers rather than drawn from memory. See src/brand.rs.
//
// A CARD WEARS A BRAND MARK ONLY WHEN SOMEBODY ELSE'S SOFTWARE IS BEHIND IT.
// Jason, 2026-09-07: "every card should have a svg of the docker icon it was
// made from so recipes should have tandoor". Eight cards are somebody else's
// application -- Recipes is Tandoor, Documents is Paperless, Photos is Immich
// -- and those now show that project's own logo. The rest are things he WROTE,
// and they keep the hand-drawn glyph, because inventing a logo for his own
// application would be inventing a brand that does not exist. The split is
// readable at a glance and it is true: a mark means "this is software I run",
// a glyph means "this is software I built".
use brand::{
    BRAND_GITEA, BRAND_GRAFANA, BRAND_HOMEPAGE, BRAND_IMMICH, BRAND_ITTOOLS,
    BRAND_OPENGYM, BRAND_PAPERLESS, BRAND_TANDOOR,
};

const ICON_SIGN: &str = r#"<path d="M8 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.1 2.1 0 0 1 3 3L13 14l-4 1 1-4z"/>"#;

// Added 2026-08-28 for the dashboard. ONLY THE THREE THAT DID NOT ALREADY
// EXIST -- the first pass also redefined ICON_PULSE, ICON_TERMINAL, ICON_CODE,
// ICON_WRENCH and ICON_DOC, which are declared further down this file and made
// the crate fail to compile. Reused rather than redrawn.
const ICON_CHART: &str = r#"<path d="M3 3v18h18"/><path d="m7 14 3-4 3 3 5-7"/>"#;
const ICON_BOX: &str = r#"<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="m3 8 9 5 9-5M12 13v8"/>"#;
const ICON_PAGES: &str = r#"<rect x="7" y="3" width="13" height="16" rx="2"/><path d="M4 7v12a2 2 0 0 0 2 2h10"/>"#;

const ICON_AWARD: &str =
    r#"<circle cx="12" cy="9" r="5"/><path d="M8.5 13.5 7 21l5-2.5L17 21l-1.5-7.5"/>"#;
const ICON_HELP: &str = r#"<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.2 2.4c-.6.2-1 .8-1 1.4v.4"/><circle cx="12" cy="17" r=".6" fill="currentColor"/>"#;
const ICON_TEA: &str = r#"<path d="M4 8h13v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z"/><path d="M17 9h2a2 2 0 0 1 0 4h-2"/><path d="M7 3v2M10 3v2M13 3v2"/>"#;
const ICON_POT: &str = r#"<path d="M6 13a3.6 3.6 0 1 1 1.5-6.9 4 4 0 0 1 9 0A3.6 3.6 0 1 1 18 13z"/><path d="M6.4 13v5a2 2 0 0 0 2 2h7.2a2 2 0 0 0 2-2v-5"/><path d="M6.6 16.4h10.8"/>"#;
const ICON_PHOTOS: &str = r#"<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8.5" cy="10" r="1.5"/><path d="m21 16-5-5-6 6"/>"#;
const ICON_CODE: &str =
    r#"<path d="m8 8-4 4 4 4"/><path d="m16 8 4 4-4 4"/><path d="M13.5 6 10.5 18"/>"#;

// --- NUC homelab icons, added 2026-08-28 -- same hand-drawn feather-icon
// style as the ones above (thin stroke, rounded caps, 24x24 viewBox).
const ICON_PULSE: &str = r#"<path d="M3 12h4l2-7 4 14 2-7h6"/>"#;
// An old tabletop radio: case, aerial, dial line, one knob.
const ICON_RADIO: &str = r#"<rect x="3" y="8" width="18" height="12" rx="2"/><path d="m7 8 10-5"/><path d="M7 12h7"/><circle cx="17.5" cy="14" r="1.6"/><path d="M7 16h4"/>"#;
const ICON_TERMINAL: &str =
    r#"<path d="m5 7 5 5-5 5"/><path d="M13 17h6"/><rect x="2" y="3" width="20" height="18" rx="2"/>"#;
const ICON_GIT: &str = r#"<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="9" r="2.5"/><path d="M6 8.5v7"/><path d="M6 8.5A6 6 0 0 0 18 11.5"/>"#;
const ICON_WRENCH: &str = r#"<path d="M14.7 6.3a4 4 0 0 0-5.6 5.1L4 16.5V20h3.5l5.1-5.1a4 4 0 0 0 5.1-5.6l-3 3-2-2 3-3Z"/>"#;
const ICON_GARDEN: &str = r#"<path d="M12 21v-8"/><path d="M12 13c0-3.5 2.5-6 5.5-6C17.5 10.5 15 13 12 13Z"/><path d="M12 15c0-3-2-5.5-4.5-5.5C7.5 12.5 9.5 15 12 15Z"/><path d="M5 21h14"/>"#;

const ICON_GLOBE: &str = r#"<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18Z"/>"#;
const ICON_ANDROID: &str = r#"<path d="M6 10.5a6 6 0 0 1 12 0v5.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 16Z"/><path d="M8 6 6.7 3.9M16 6l1.3-2.1"/><circle cx="9.7" cy="11.5" r=".6"/><circle cx="14.3" cy="11.5" r=".6"/><path d="M4 11.5v4M20 11.5v4M10 17.5v2.2M14 17.5v2.2"/>"#;

// --- 2026-09-10, for the study/reading/records batch. Same feather style
// as everything above: thin stroke, rounded caps, 24x24.
const ICON_MUSIC: &str = r#"<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>"#;
const ICON_STUDY: &str = r#"<path d="M3 6.5 12 3l9 3.5-9 3.5z"/><path d="M7 9.5V15c0 1.4 2.2 2.5 5 2.5s5-1.1 5-2.5V9.5"/><path d="M21 6.5V13"/>"#;
const ICON_READER: &str = r#"<path d="M12 6.5C10.5 5 8.5 4.5 6 4.5H3v13h3c2.5 0 4.5.5 6 2"/><path d="M12 6.5C13.5 5 15.5 4.5 18 4.5h3v13h-3c-2.5 0-4.5.5-6 2"/><path d="M12 6.5v13"/>"#;
const ICON_HEADPHONES: &str = r#"<path d="M4 14v-2a8 8 0 0 1 16 0v2"/><rect x="2.5" y="13.5" width="4.5" height="6.5" rx="1.6"/><rect x="17" y="13.5" width="4.5" height="6.5" rx="1.6"/>"#;
const ICON_WALLET: &str = r#"<rect x="2.5" y="6" width="19" height="13" rx="2.5"/><path d="M2.5 10h19"/><circle cx="17.5" cy="14.5" r="1.2"/><path d="M6 3.5h11a2 2 0 0 1 2 2V6"/>"#;
const ICON_RECEIPT: &str = r#"<path d="M6 2.5h12v19l-2-1.4-2 1.4-2-1.4-2 1.4-2-1.4-2 1.4z"/><path d="M9.5 7.5h5M9.5 11h5M9.5 14.5h3"/>"#;

const ICON_DOC: &str = r#"<path d="M7 3h7l4 4v14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z"/><path d="M14 3v4h4"/><path d="M9 13h6M9 16.5h6"/>"#;

/// The cards, in the order they appear.
///
/// NOTE the Mama link is `/mama/`, not `/mom/` as the PHP had it. The Caddyfile
/// has always routed that app at `/mama` — inherited from the Pi — so the old
/// link was a 404 waiting to be clicked. Caddy now also redirects `/mom` to
/// `/mama` for anything still pointing at the old path.
fn sections() -> Vec<Section> {
    vec![
        Section {
            name: "Casey",
            sub: "Awards & career portal",
            desc: "Voice notes in, Air Force award statements out.".into(),
            href: "/casey/",
            accent: "#5aa2e8",
            icon: ICON_AWARD,
        },
        Section {
            name: "Mama",
            sub: "Tech & cyber help",
            desc: "Ask anything about your phone or computer.".into(),
            href: "/mama/",
            accent: "#4fc79b",
            icon: ICON_HELP,
        },
        Section {
            name: "Ms Dee",
            sub: "Tea & company",
            desc: "Book a visit, a ride, or a hand with the day.".into(),
            href: "/dee/",
            accent: "#e8a35a",
            icon: ICON_TEA,
        },
        // CAUGHT LIVE (2026-08-24): this used to point straight at Mealie's
        // own port, from back when Mealie was the front door. That's no
        // longer true -- Kitchen absorbed the library entirely (see
        // ai-stack/TANDOOR_MIGRATION_STATUS.md) and Mealie is stopped, so
        // this card was quietly sending people to a dead port. /kitchen is
        // the one real front door now, same relative-path convention every
        // other card on this page already uses.
        // CAUGHT LIVE AGAIN (2026-08-28), one door further along. The comment
        // above fixed a card pointing at a dead Mealie port by sending it to
        // /kitchen -- correct at the time. It stopped being correct when
        // /recipe was built: browse, one recipe, random pick, meal planning
        // and the pantry picker on a single page, which is exactly what the
        // words on this card describe.
        //
        // /kitchen is still real and still useful, but it is the IMPORTER --
        // paste a link, get a recipe. That is a thing you do occasionally,
        // not the front door to the library, and clicking "Recipes" was
        // landing people in it (and onward into Tandoor's own UI, which
        // /recipe exists specifically to avoid -- see the Caddyfile block).
        Section {
            name: "Recipes",
            sub: "Cook & keep",
            desc: "Your recipes, meal plans, and shopping list. Paste a link to add a new one.".into(),
            href: "/recipe/",
            accent: "#e0705f",
            icon: ICON_POT,
        },
        Section {
            name: "Legacy",
            sub: "PeeWee family photos",
            desc: "The albums, kept somewhere they won't be lost.".into(),
            href: "/legacy/",
            accent: "#d98ab0",
            icon: ICON_PHOTOS,
        },
        Section {
            name: "Jason",
            sub: "Projects & lab",
            desc: "Builds, experiments, and everything in progress.".into(),
            // /jason/cards/, not /jason/: since 2026-11-03 bare /jason opens Jason's
            // private notes page (Caddy on the NUC); the project cards moved here.
            href: "/jason/cards/",
            accent: "#c78af0",
            icon: ICON_CODE,
        },
    ]
}

/// The cards on `/jason/` -- the NUC homelab tools, added 2026-08-28. See
/// share/nuc-homelab/README.md. A card here is just a link, not a grant --
/// who can actually get past sign-in for each of these is still controlled
/// per-person on /admin/people (common::people::ALL_APPS).
/// The monitoring and maintenance tools, gathered in one place.
///
/// THESE CARDS LINK OUT; THEY DO NOT MOVE ANYTHING. Jason asked for these
/// "under /dashboard", and the cheap reading of that -- re-pathing each app to
/// /dashboard/status, /dashboard/logs and so on -- would mean changing Gitea's
/// ROOT_URL, Grafana's root_url, Portainer's --base-url, Dozzle's DOZZLE_BASE,
/// Paperless's FORCE_SCRIPT_NAME, and REBUILDING IT-Tools from source, because
/// its Vue router bakes the base path in at build time (that rebuild took a
/// day the first time). Every one of those is a chance to break a working
/// door, and the benefit is that the address bar reads differently.
///
/// So the grouping is a page, not a prefix. Same convenience, none of the
/// churn. If the addresses themselves ever need to move, that is a separate
/// job worth planning rather than a side effect of wanting a menu.
fn dashboard_sections() -> Vec<Section> {
    vec![
        Section {
            name: "Uptime",
            sub: "Is it up",
            desc: "Every site and service, checked on a timer, with the history of when something last went down.".into(),
            href: "/status/",
            accent: "#4ade80",
            icon: ICON_PULSE,
        },
        Section {
            name: "Live logs",
            sub: "Right now",
            desc: "Dozzle: what every container on the NUC is saying, as it says it. Best when something is broken this minute.".into(),
            href: "/logs/",
            accent: "#60a5fa",
            icon: ICON_TERMINAL,
        },
        Section {
            name: "Log history",
            sub: "Search back",
            desc: "Grafana and Loki: both machines' logs, kept and searchable. Best when something broke last week.".into(),
            href: "/grafana/",
            accent: "#f59e0b",
            icon: ICON_CHART,
        },
        Section {
            name: "Containers",
            sub: "Docker",
            desc: "Portainer: start, stop, inspect and update anything on either box. Keeps its own password on top of this sign-in.".into(),
            href: "/portainer/",
            accent: "#38bdf8",
            icon: ICON_BOX,
        },
        Section {
            name: "Documents",
            sub: "Paperless",
            desc: "Scan something in and it becomes searchable text. Drop files into the consume folder and they file themselves.".into(),
            href: "/paperless/",
            accent: "#a78bfa",
            icon: ICON_DOC,
        },
        Section {
            name: "Code",
            sub: "Gitea",
            desc: "The repositories, and the history of every change made to this house's software.".into(),
            href: "/git/",
            accent: "#fb923c",
            icon: ICON_GIT,
        },
        Section {
            name: "Utilities",
            sub: "IT-Tools",
            desc: "The small conversions and generators you only ever need in the middle of fixing something else.".into(),
            href: "/tools/",
            accent: "#2dd4bf",
            icon: ICON_WRENCH,
        },
        Section {
            name: "PDF tools",
            sub: "Stirling",
            desc: "Merge, split, rotate, compress. Runs here, so nothing is uploaded to a stranger's website.".into(),
            href: "/pdf/",
            accent: "#f472b6",
            icon: ICON_PAGES,
        },
    ]
}

fn jason_sections() -> Vec<Section> {
    vec![
        // First on the page on purpose: the rest of this list is
        // infrastructure Jason runs, this is something he actually built.
        Section {
            name: "Android",
            sub: "Apps I've built",
            desc: "The Android apps, with what each one does and a download.".into(),
            href: "/jason/android",
            accent: "#3ddc84", // Android's own green
            icon: ICON_ANDROID,
        },
        // (Robin's Garden card removed 2026-11-04: off Jason's site; Robin's own site and the
        // GitHub repository are untouched.)
        Section {
            name: "Status",
            sub: "Uptime monitor",
            desc: "What's up, what's down, right now.".into(),
            href: "/status/",
            accent: "#2dd4bf",
            icon: ICON_PULSE,
        },
        Section {
            name: "Logs",
            sub: "Container logs",
            desc: "Live logs from everything running, in one place.".into(),
            href: "/logs/",
            accent: "#94a3b8",
            icon: ICON_TERMINAL,
        },
        Section {
            name: "Git",
            sub: "Version control",
            desc: "This household's own repos, self-hosted.".into(),
            href: "/git/",
            accent: "#6366f1",
            icon: ICON_GIT,
        },
        Section {
            name: "Tools",
            sub: "Dev & IT utilities",
            desc: "JSON formatting, subnet math, and the rest of the toolbox.".into(),
            href: "/tools/",
            accent: "#a3e635",
            icon: ICON_WRENCH,
        },
        // DocuSeal, on its own PORT rather than a path.
        //
        // It cannot be served from a subpath: it ignores
        // RAILS_RELATIVE_URL_ROOT (tested -- /pdf-sign/ returned its own 404)
        // and stripping the prefix breaks its root-absolute assets instead.
        // At the root of a port there is no prefix to cope with.
        //
        // A TLS certificate is issued per HOSTNAME, not per port, so Caddy's
        // existing certificate for example.org covers this with no
        // new DNS entry and no paid domain -- which matters, because the
        // No-IP account is the free tier. 9444 was Otobo's until it was
        // retired earlier the same day, so even the router forward already
        // existed.
        // ("Been there" scratch map removed 2026-11-04: retired, Jason's request.)
        Section {
            name: "Sign a PDF",
            sub: "DocuSeal",
            desc: "Send a document for signature, or sign one yourself. The signed copy stays here rather than on someone else's service.".into(),
            href: "https://example.org:9444/",
            accent: "#c084fc",
            icon: ICON_SIGN,
        },
        Section {
            name: "PDF",
            sub: "Document tools",
            desc: "Merge, split, compress, and edit PDFs.".into(),
            href: "/pdf/",
            accent: "#22d3ee",
            icon: ICON_DOC,
        },
    ]
}

#[derive(Template)]
#[template(path = "welcome.html")]
struct WelcomeTemplate {
    sections: Vec<Section>,
    year: i32,
    /// Where the admin lives, or empty to show no link at all.
    ///
    /// A FOOTER LINK, NOT A FOURTH CARD, and not by accident. The three cards
    /// are for the people the sites are FOR; the admin is for exactly one
    /// person and putting it beside them invites everyone else to try it.
    ///
    /// It also has a real cost worth knowing: this link publishes the admin's
    /// URL in the page source. If ADMIN_PATH was set to something unguessable
    /// to keep scanners off it, this link hands the path straight back to
    /// them. Set LANDING_ADMIN_LINK=false to hide it and bookmark the URL
    /// instead - the choice is convenience against quiet.
    admin_href: String,
}

/// One Android app on `/jason/android`.
///
/// `size` is written out rather than computed from the file: these are
/// served from a mounted folder that this process does not stat at
/// startup, and a wrong-but-confident number is worse than a stale one.
/// Re-check it when an APK is replaced.
struct AndroidApp {
    name: &'static str,
    package: &'static str,
    version: &'static str,
    size: &'static str,
    built: &'static str,
    /// Filename under /jason/apk/ -- see the ServeDir mount in run().
    ///
    /// EMPTY for a project that has never produced a build. The template
    /// shows an "in progress" note instead of a download button in that
    /// case, rather than a link that 404s.
    file: &'static str,
    accent: &'static str,
    /// Short pitch, then the longer detail paragraphs.
    tagline: &'static str,
    detail: &'static [&'static str],
}

impl AndroidApp {
    fn downloadable(&self) -> bool {
        !self.file.is_empty()
    }
}

/// Every project in ~/AndroidStudioProjects, built ones first.
///
/// `jasonIT` and `zeeawards` are listed WITHOUT a download and clearly
/// marked unfinished, because that is what they are: both are untouched
/// Android Studio "Empty Compose Activity" templates -- 43 lines each,
/// still rendering the stock `Greeting("Android")` Hello World, no custom
/// code and no APK ever built. Describing them as though they did
/// something would be inventing a product out of a project folder. What
/// each is FOR is inferred only from its package name and the matching
/// app already on this domain, and is written as intent, not as fact
/// about working software.
///
/// The Juns Garden APK served here is the 66MB 2026-06-18 build that the
/// garden site itself links, NOT the 11.8MB January build sitting in
/// AndroidStudioProjects -- that one is a 20-line template stub with no
/// permissions and no functionality. Same app name, very different thing.
fn android_apps() -> Vec<AndroidApp> {
    vec![
        // (Juns Garden app card removed 2026-11-04 with the rest of the garden.)
        AndroidApp {
            name: "Clean My Phone",
            package: "com.example.cleanmyphone",
            version: "1.0",
            size: "6 MB",
            built: "23 October 2025",
            file: "clean-my-phone.apk",
            accent: "#5aa2e8",
            tagline: "A shortcut to the storage cleanup Android already has.",
            detail: &[
                "Android's own \"free up space\" screen is genuinely useful and                  genuinely buried. This puts one button in front of it and                  hands you off to the system's own cleanup rather than                  pretending to be a cleaner itself.",
                "Deliberately does NOT delete anything on its own. The apps                  that claim to are mostly selling you something; the real                  tool is already on the phone.",
            ],
        },
        AndroidApp {
            name: "Casey Awards",
            package: "Jason.Casey.Dee",
            version: "—",
            size: "—",
            built: "Started July 2026",
            file: "",
            accent: "#5aa2e8",
            tagline: "A phone companion for the awards portal. Not written yet.",
            detail: &[
                "The intended pairing for /casey — dictating an award bullet                  from a phone rather than sitting down at a desk to type it.                  The package name is the only thing linking it there so far.",
                "Right now this is an empty Android Studio project: the                  default Compose template, unmodified. Nothing to install.",
            ],
        },
        AndroidApp {
            name: "jasonIT",
            package: "jason.casey.jasonit",
            version: "—",
            size: "—",
            built: "Started July 2026",
            file: "",
            accent: "#94a3b8",
            tagline: "An IT-help app in name only, so far.",
            detail: &[
                "Created alongside Casey Awards in the same sitting. The name                  suggests the same ground /mama covers on the web — tech and                  cyber help — but nothing has been built against it.",
                "Also still the stock Compose template. Listed here for                  completeness rather than because there is anything to try.",
            ],
        },
    ]
}

/// `/jason/` — the NUC homelab's tools, added 2026-08-28.
///
/// The old `Jason/index.php` was the stock "PHP is working perfectly!" test
/// page, then a placeholder saying nothing was published yet. Same
/// `Section`/card shape as the front page (see `jason_sections()`).
#[derive(Template)]
#[template(path = "jason.html")]
struct JasonTemplate {
    sections: Vec<Section>,
}

#[derive(Template)]
#[template(path = "dashboard.html")]
struct DashboardTemplate {
    sections: Vec<Section>,
}

#[derive(Template)]
#[template(path = "android.html")]
struct AndroidTemplate {
    apps: Vec<AndroidApp>,
}

#[derive(Template)]
#[template(path = "garden.html")]
struct GardenTemplate;

/// Both pages, rendered once and shared.
struct AppState {
    welcome: String,
    jason: String,
    dashboard: String,
    android: String,
    garden: String,
}

/// The page that used to be at the root. Kept because it is still a good
/// menu for the household, and because throwing away working work to make
/// room for new work is rarely necessary.
async fn welcome(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    Html(state.welcome.clone())
}

async fn jason(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    Html(state.jason.clone())
}

async fn dashboard(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    Html(state.dashboard.clone())
}

async fn android(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    Html(state.android.clone())
}

/// /jason/garden and /garden showed the Robin's Garden guide page. Taken off Jason's site
/// 2026-11-04 ("remove it on my website ... don't remove it on the GitHub repository"):
/// both answer 404 now. Robin's own site (garden.example.org) is untouched.
async fn garden(State(_state): State<Arc<AppState>>) -> impl IntoResponse {
    (StatusCode::NOT_FOUND, Html("<h1>404</h1><p>Nothing here.</p>"))
}

async fn healthz() -> &'static str {
    "ok"
}

async fn not_found() -> impl IntoResponse {
    (StatusCode::NOT_FOUND, Html("<h1>404</h1><p>Nothing here.</p>"))
}

/// Written out longhand instead of `#[tokio::main]` on purpose: the local
/// timezone offset has to be read before any thread exists. `common` explains
/// why at length.
fn main() -> anyhow::Result<()> {
    common::init_local_offset();
    common::runtime()?.block_on(run())
}

async fn run() -> anyhow::Result<()> {
    common::init_tracing("landing");

    // Render at startup. If the template is broken we find out now, on a
    // container that is about to be marked unhealthy — not on the first
    // visitor's request.
    // Read the admin's real path rather than hardcoding /admin, so the link
    // keeps working after ADMIN_PATH is rolled to something unguessable. A
    // footer link pointing at a stale path is worse than no link: it looks
    // broken, and it still tells a scanner where to knock.
    let admin_href = if common::env_bool("LANDING_ADMIN_LINK", true) {
        let p = common::env_or("ADMIN_PATH", "/admin");
        // handle_path in Caddy strips the prefix, and the admin's own links
        // are relative - so the trailing slash is what keeps them resolving
        // against the right base.
        if p.ends_with('/') { p } else { format!("{p}/") }
    } else {
        String::new()
    };

    let welcome_html = WelcomeTemplate {
        sections: sections(),
        year: common::current_year(),
        admin_href,
    }
    .render()
    .map_err(|e| anyhow::anyhow!("the landing template failed to render: {e}"))?;
    let garden_html = GardenTemplate
        .render()
        .map_err(|e| anyhow::anyhow!("the garden template failed to render: {e}"))?;
    let android_html = AndroidTemplate { apps: android_apps() }
        .render()
        .map_err(|e| anyhow::anyhow!("the android template failed to render: {e}"))?;
    let jason_html = JasonTemplate { sections: jason_sections() }
        .render()
        .map_err(|e| anyhow::anyhow!("the jason template failed to render: {e}"))?;
    let dashboard_html = DashboardTemplate { sections: dashboard_sections() }
        .render()
        .expect("dashboard template");

    tracing::info!("rendered {} bytes of welcome page", welcome_html.len());

    let state = Arc::new(AppState {
        welcome: welcome_html,
        jason: jason_html,
        dashboard: dashboard_html,
        android: android_html,
        garden: garden_html,
    });

    let app = Router::new()
        // THE PORTFOLIO IS THE FRONT PAGE as of 2026-08-28. What used to be
        // here was a three-card menu of the household apps -- useful to the
        // five people who live here and meaningless to anyone else, which is
        // backwards for the one URL strangers actually arrive at. The
        // household now signs in and lands on /home, which is better than
        // the old page was: it shows each person only their own things.
        //
        // The old page is still built and still reachable at /welcome, so
        // nothing was thrown away and reverting is a one-line change.
        .route("/", get(index))
        .route("/privacy", get(privacy))
        .route("/looks.css", get(looks_css))
        .route("/welcome", get(welcome))
        // Somebody's own page. Gated in Caddy, which is what supplies the
        // identity -- see the handler for why a missing header shows nothing.
        // Public. No forward_auth -- see the handler.
        // The portfolio IS the index now; these keep any shared link working.
        .route("/portfolio", get(index))
        .route("/portfolio/", get(index))
        .route("/all", get(all))
        .route("/all/", get(all))
        .route("/home", get(home))
        // Self-service look picker. Gated like /home -- it needs to know who
        // is asking, and it writes their choice.
        .route("/looks", get(looks_page).post(looks_save))
        .route("/looks/", get(looks_page).post(looks_save))
        .route("/home/", get(home))
        // /dashboard IS NOW A REDIRECT, NOT A PAGE (2026-09-06). Every card
        // it held lives on /home, so the page had become a second copy of
        // cards he was already looking at -- Jason: "I don't need a
        // separate page for all these cards". A redirect rather than a
        // removal because the URL is bookmarked and written down in notes.
        .route("/dashboard", get(dashboard_moved))
        .route("/dashboard/", get(dashboard_moved))
        .route("/jason", get(jason))
        .route("/jason/", get(jason))
        .route("/jason/garden", get(garden))
        // ALSO AT /garden, without the /jason prefix. This page is about
        // Robin's site and is linked from Robin's own home page, so sending him
        // through a path named after somebody else read as borrowing it.
        // Both paths serve the same page -- the old one still works, because
        // it may already be bookmarked or linked.
        .route("/garden", get(garden))
        .route("/garden/", get(garden))
        .route("/jason/android", get(android))
        .route("/jason/android/", get(android))
        // The APK files themselves, from a mounted folder (see the volume
        // on `landing` in docker-compose.web.yml). Read-only: this page
        // hands out builds, it never receives them.
        //
        // Served under /jason/apk, NOT /jason/android: a nest_service on
        // the same prefix as the exact `/jason/android` route above is a
        // routing conflict, and axum panics on those at startup rather
        // than silently picking one.
        .nest_service(
            "/jason/apk",
            ServeDir::new(common::env_or("ANDROID_APK_DIR", "/data/apk")),
        )
        .route("/healthz", get(healthz))
        .fallback(not_found)
        // WAS "public, max-age=60", on the reasoning that the page is identical
        // for everyone. That stopped being true the moment these pages became
        // per-person: they now carry the viewer's name, only the cards their
        // account is granted, and the theme they picked.
        //
        // Two things were wrong with it, one annoying and one not.
        //
        // The annoying one: pick a new theme, land back on your page, and for
        // the next sixty seconds you are served the old one out of cache. It
        // reads exactly like a setting that did not save -- so the natural
        // response is to change it again, which resets the minute.
        //
        // The one that matters: "public" invites any shared cache between here
        // and the reader to keep a copy and hand it to somebody else. These
        // pages differ per account, and Jason's has admin cards on it that
        // nobody else is meant to see. Nothing in front is caching today, which
        // is the only reason this was never observed -- the header was still
        // giving permission for it.
        //
        // "private" means the viewer's own browser only, never a shared cache.
        // "no-cache" means it may keep a copy but must check with us before
        // using it, so a theme change shows up on the very next load.
        .layer(SetResponseHeaderLayer::overriding(
            header::CACHE_CONTROL,
            HeaderValue::from_static("private, no-cache"),
        ))
        .layer(CompressionLayer::new())
        .with_state(state);

    let port = common::env_u16("APP_PORT", 8010);
    common::serve(app, port).await
}

/// Every card the household might see, each tagged with the app that gates it.
///
/// ONE LIST, FILTERED PER PERSON -- not a page written per person. Jason asked
/// for "a different page for each", and this is that, arrived at differently:
/// a page per person means five pages to keep in step, and the one that gets
/// forgotten is the one somebody is looking at. Filtering one list means
/// granting an app on /admin/people makes its card appear by itself.
///
/// The order here is the order they appear. Everyday things first; the
/// ops-only entries fall to the bottom and only Jason ever sees them.
fn home_cards() -> Vec<HomeCard> {
    vec![
        // --- 2026-09-10 ----------------------------------------------------
        HomeCard { app: "read", group: "Family & house", section: Section {
            name: "Reader", sub: "Books & documents",
            desc: "Read EPUBs and PDFs in the browser, and it remembers where you stopped.".into(),
            href: "/read/", accent: "#7dc98f", icon: ICON_READER,
        }},
        // The card points at /listen, not /audiobookshelf, even though the
        // GRANT is keyed on the latter. /listen is the name Jason asked for
        // and 308s straight to the app; the ugly prefix is forced by the
        // client's compiled-in base path and does not have to be the name
        // anybody reads.
        HomeCard { app: "audiobookshelf", group: "Family & house", section: Section {
            name: "Listen", sub: "Documents read aloud",
            desc: "Anything sent from Find, narrated in one of the house voices.".into(),
            href: "/listen", accent: "#c78af0", icon: ICON_HEADPHONES,
        }},
        HomeCard { app: "money", group: "Administrative", section: Section {
            name: "Money", sub: "Budget & statements",
            desc: "Paystubs, bank statements and where it all actually goes. Self-hosted, so it stays here.".into(),
            href: "https://money.home.example.net/", accent: "#4fc79b", icon: ICON_WALLET,
        }},
        HomeCard { app: "homebox", group: "Administrative", section: Section {
            name: "Warranties", sub: "Receipts & renewals",
            desc: "What we own, what it cost, and when the cover on it runs out.".into(),
            href: "https://homebox.home.example.net/", accent: "#e8a35a", icon: ICON_RECEIPT,
        }},
        HomeCard { app: "recipe", group: "Family & house", section: Section {
            name: "Recipes", sub: "The recipe box",
            desc: "Everything we cook, searchable, with the ones we actually make marked.".into(),
            href: "/recipe/", accent: "#e8894a", icon: BRAND_TANDOOR,
        }},
        // Kitchen's card was withdrawn 2026-09-07 at Jason's request ("hide
        // kitchen"). THE IMPORTER IS STILL RUNNING and /kitchen still works
        // for anyone who has the URL -- this removes the front door, not the
        // room. Its repository is also the one thing deliberately kept OFF
        // Gitea, so hiding the card keeps the site and the pushed code
        // telling the same story.
        HomeCard { app: "tribute", group: "Projects", section: Section {
            name: "The First Voice", sub: "Pee-Wee Harris",
            desc: "The radio tribute to Edward \u{201c}Pee-Wee\u{201d} Harris. Switch it on and tune the dial.".into(),
            href: "https://peewee.home.example.net/", accent: "#e8a33d", icon: ICON_RADIO,
        }},
        HomeCard { app: "studio", group: "Projects", section: Section {
            name: "Pee-Wee Studio", sub: "Manage the tribute",
            desc: "Add people, edit their stories, import photos, publish to the site.".into(),
            href: "https://studio.home.example.net/", accent: "#c8452c", icon: ICON_SIGN,
        }},
        HomeCard { app: "engine", group: "Dashboards", section: Section {
            name: "Engine Bay", sub: "SuperNova + the NUC",
            desc: drive_bars(),
            href: "/engine", accent: "#e8a33d", icon: ICON_PULSE,
        }},
        // ("Is everything up?" card removed 2026-11-04: uptime is on Debug Found, the wall.)
        // ("Connect Google Photos" card removed 2026-11-04: retired, Immich data already wiped.)
        // ("Photos" card removed 2026-11-04: retired, Immich data already wiped.)
        // ("Workouts" card removed 2026-11-04, Jason's request; the gym app is retired.)
        // ("Tech help" card removed 2026-11-04, Jason's request; /mama redirects to /mom/.)
        // ("Career / Casey" card removed 2026-11-04, Jason's request. Casey lands on /casey/ after sign-in.)
        // Beside Casey's on purpose -- they are the same machinery pointed at
        // two people, and reading as a pair is the honest description.
        // Jason, 2026-09-07: "zees Career is there not mine."
        // ("My record" -> /ability/ removed 2026-11-04, Jason's request.)
        // "find" and "kb" cards removed 2026-11-03 (Jason: "Remove kb, find ...
        // freeing resources and removing unnecessary buttons"). Only he could
        // see either. /find/ itself still works and its index is what Hermes
        // quotes books from, so only the button went; /mama/kb still exists
        // for Mama's page. The search bar (_search.html) is unaffected.
        HomeCard { app: "pdf", group: "Administrative", section: Section {
            name: "PDF tools", sub: "Documents",
            desc: "Merge, split, compress and sign. Files stay on the machine here.".into(),
            href: "/pdf/", accent: "#e86a6a", icon: ICON_DOC,
        }},
        HomeCard { app: "tools", group: "Administrative", section: Section {
            name: "Utilities", sub: "Odds and ends",
            desc: "Converters, formatters, generators. Small tools, no accounts.".into(),
            href: "/tools/", accent: "#8fa3b8", icon: BRAND_ITTOOLS,
        }},
        HomeCard { app: "git", group: "Administrative", section: Section {
            name: "Code", sub: "The source",
            desc: "Everything running here, and the history of every change.".into(),
            href: "/git/", accent: "#7f9cf5", icon: BRAND_GITEA,
        }},
        HomeCard { app: "paperless", group: "Administrative", section: Section {
            name: "Documents", sub: "Paperless",
            desc: "Scanned household paperwork, searchable by its text.".into(),
            href: "/paperless/", accent: "#b0885f", icon: BRAND_PAPERLESS,
        }},
        // 2026-09-01: the cards the sub-pages held, folded into /home so
        // one page shows everything (Jason: "I don't want to have to click
        // on more than one page to see all the cards").
        // A second Uptime card lived here from 2026-09-06 until later the
        // same day, brought across when /dashboard was folded in. It pointed
        // at /status/, which is a 308 to status.home.example.net --
        // the exact destination the "Is everything up?" card above already
        // had. Same service, two cards, different names. Removed rather than
        // renamed: one door is the point.
        HomeCard { app: "dashboard", group: "Dashboards", section: Section {
            name: "Debug Found", sub: "Everything at once",
            desc: "Live widgets for both machines: what is running, what it \
                   is using, and what it is costing.".into(),
            href: "https://dashboard.home.example.net/",
            accent: "#5aa2e8", icon: BRAND_HOMEPAGE,
        }},
        // ("Live logs" / Dozzle card removed 2026-11-04: Dozzle retired; Grafana + Loki is the log place.)
        HomeCard { app: "grafana", group: "Dashboards", section: Section {
            name: "Log history", sub: "Search back",
            desc: "Grafana and Loki: both machines' logs, kept and \
                   searchable. Best when something broke last week. Runs \
                   on the NUC, so it survives the PC going down.".into(),
            href: "/grafana/", accent: "#f59e0b", icon: BRAND_GRAFANA,
        }},
        HomeCard { app: "portainer", group: "Administrative", section: Section {
            name: "Containers", sub: "Docker",
            desc: "Portainer: start, stop, inspect and update anything on \
                   either box. Keeps its own password on top of this \
                   sign-in.".into(),
            href: "/portainer/", accent: "#38bdf8", icon: ICON_BOX,
        }},
        // DocuSeal's card was here until 2026-09-06. Jason: "I don't need
        // the PDF signing since I have the one thats wired into career."
        // Nothing was ever signed with it -- its database had no tables --
        // and the only traffic in its last week was five foreign IPs
        // scanning the dashboard. Container stopped, hostname withdrawn.
        HomeCard { app: "git", group: "Projects", section: Section {
            name: "Android", sub: "Apps I've built",
            desc: "The Android apps, with what each one does and a \
                   download.".into(),
            href: "/jason/android", accent: "#3ddc84", icon: ICON_ANDROID,
        }},
    ]
}

#[derive(Template)]
#[template(path = "theme_plain.html")]
struct PlainTemplate {
    /// What to call them. Falls back to the part before the @ when the
    /// account has no name, which is friendlier than showing a bare address.
    who: String,
    sections: Vec<Section>,
    /// The same cards, shelved (2026-09-01): a busy page renders these with
    /// visible category headings; a small page's single shelf renders
    /// heading-free. Themed looks still read `sections` flat.
    shelves: Vec<Shelf>,
    year: i32,
}

/// One shelf on /all. Distinct from `Shelf` only in carrying a slug, which
/// the jump links need and which nothing else does.
struct AllShelf {
    name: &'static str,
    slug: String,
    sections: Vec<Section>,
}

/// Every door in the house on one page.
///
/// Jason, 2026-09-10: "make a section called /all that has all the cards and
/// links to everything".
///
/// FILTERED BY THE SAME GRANTS AS /home, and that is not a hedge. A page
/// listing doors this account cannot open would be a catalogue of things to
/// go and be refused by -- worse than useless, because it reads as broken
/// rather than as private. "Everything" means everything that is yours.
///
/// It differs from /home in one way only: /home collapses to a single
/// unlabelled shelf when there are few cards, because headings over three
/// cards is organisation theatre. This page is FOR the structure, so it
/// always shows the headings.
#[derive(Template)]
#[template(path = "all.html")]
struct AllTemplate {
    who: String,
    shelves: Vec<AllShelf>,
    total: usize,
    year: i32,
}

struct Shelf {
    name: &'static str,
    sections: Vec<Section>,
}

/// Somebody's own page, after signing in.
///
/// IDENTITY COMES FROM THE GATE, NOT FROM THE REQUEST. Caddy runs forward_auth
/// against the auth service before this handler is reached and copies the
/// result in as X-Auth-Email. A browser cannot set it: whatever a client sends
/// is replaced. That is the same arrangement scratch-map relies on to keep
/// three maps apart.
///
/// IF THE HEADER IS MISSING, SHOW NOTHING rather than showing everything. That
/// only happens if this route is ever mounted without its forward_auth -- a
/// mistake worth making obvious immediately, instead of quietly serving the
/// full menu to whoever arrived.
/// /dashboard, which is no longer somewhere to go.
///
/// 308 and not 302: the move is permanent, and a permanent redirect is the
/// one browsers and bookmarks actually update themselves to.
async fn dashboard_moved() -> impl IntoResponse {
    Redirect::permanent("/home")
}

/// GET /all -- every card, grouped, never collapsed.
async fn all(headers: HeaderMap) -> impl IntoResponse {
    let email = headers
        .get("X-Auth-Email")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .trim()
        .to_string();

    let people_file = PathBuf::from(common::env_or(
        "LANDING_PEOPLE_FILE",
        "/data/auth/people.json",
    ));
    let people = common::people::read(&people_file);
    let person = common::people::find(&people, &email);

    let mine: Vec<HomeCard> = match person {
        Some(p) => home_cards()
            .into_iter()
            .filter(|c| p.can_use(c.app))
            .collect(),
        None => Vec::new(),
    };
    let total = mine.len();

    let mut shelves: Vec<AllShelf> = GROUP_ORDER
        .iter()
        .map(|g| AllShelf {
            name: g,
            // Lowercased with spaces and ampersands turned into hyphens, so
            // "Family & house" gives a usable fragment id.
            slug: g
                .to_lowercase()
                .chars()
                .map(|c| if c.is_alphanumeric() { c } else { '-' })
                .collect::<String>()
                .trim_matches('-')
                .to_string(),
            sections: mine
                .iter()
                .filter(|c| c.group == *g)
                .map(|c| c.section.clone())
                .collect(),
        })
        .filter(|s| !s.sections.is_empty())
        .collect();

    // A card whose group is not in GROUP_ORDER would silently vanish --
    // exactly the bug a page called "everything" must not have. Anything
    // unaccounted for lands here rather than nowhere.
    let placed: usize = shelves.iter().map(|s| s.sections.len()).sum();
    if placed < total {
        let known: Vec<&str> = GROUP_ORDER.to_vec();
        shelves.push(AllShelf {
            name: "Other",
            slug: "other".to_string(),
            sections: mine
                .iter()
                .filter(|c| !known.contains(&c.group))
                .map(|c| c.section.clone())
                .collect(),
        });
    }

    let who = person
        .map(|p| p.name.trim().to_string())
        .filter(|n| !n.is_empty())
        .unwrap_or_else(|| email.split('@').next().unwrap_or("").to_string());

    match (AllTemplate { who, shelves, total, year: common::current_year() }).render() {
        Ok(html) => Html(html).into_response(),
        Err(e) => {
            tracing::error!("/all failed to render: {e}");
            (StatusCode::INTERNAL_SERVER_ERROR, "could not build that page")
                .into_response()
        }
    }
}

async fn home(headers: HeaderMap) -> impl IntoResponse {
    let email = headers
        .get("X-Auth-Email")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .trim()
        .to_string();

    if email.is_empty() {
        tracing::warn!("/home reached with no X-Auth-Email -- is forward_auth wired up?");
    }

    let people_file = PathBuf::from(common::env_or(
        "LANDING_PEOPLE_FILE",
        "/data/auth/people.json",
    ));
    let people = common::people::read(&people_file);
    let person = common::people::find(&people, &email);

    let mine: Vec<HomeCard> = match person {
        Some(p) => home_cards()
            .into_iter()
            .filter(|c| p.can_use(c.app))
            .collect(),
        None => Vec::new(),
    };
    // The shelves: category order, headings shown by the plain theme when
    // more than one shelf has cards. A small page collapses to one
    // unlabelled shelf -- headings over three cards is noise.
    let mut shelves: Vec<Shelf> = if mine.len() > 8 {
        GROUP_ORDER
            .iter()
            .map(|g| Shelf {
                name: g,
                sections: mine
                    .iter()
                    .filter(|c| c.group == *g)
                    .map(|c| c.section.clone())
                    .collect(),
            })
            .filter(|s| !s.sections.is_empty())
            .collect()
    } else {
        vec![Shelf {
            name: "",
            sections: mine.iter().map(|c| c.section.clone()).collect(),
        }]
    };
    // Themed looks still take the flat list, category-ordered with the
    // shelf stamped in the eyebrow -- their templates have no headings.
    let sections: Vec<Section> = if mine.len() > 8 {
        shelves
            .iter()
            .flat_map(|sh| {
                sh.sections.iter().map(|sec| Section {
                    sub: Box::leak(
                        format!("{} \u{00b7} {}", sh.name, sec.sub).into_boxed_str(),
                    ),
                    ..sec.clone()
                })
            })
            .collect()
    } else {
        mine.into_iter().map(|c| c.section).collect()
    };

    // Their name if the account carries one, otherwise the part of the
    // address before the @ -- which reads as "davetta1" and is why the name
    // field exists. Set it in /admin/people.
    let who = person
        .map(|p| p.name.trim().to_string())
        .filter(|n| !n.is_empty())
        .unwrap_or_else(|| email.split('@').next().unwrap_or("").to_string());

    let year = common::current_year();

    // WHICH LOOK, FROM THE ACCOUNT -- not from a list of email addresses.
    //
    // This used to be three `if email == ...` branches with the addresses in
    // environment variables and one HTML file per person. That works for three
    // people and stops working at four: a new account meant a new template, a
    // code change and a rebuild. Inviting somebody to look at one recipe was
    // not worth any of that.
    //
    // Now the theme is a field on the account, set from a dropdown in
    // /admin/people, and the same theme can be worn by any number of people.
    // An unknown or removed theme falls through to plain -- a boring page,
    // never a broken one.
    //
    // THEMES ARE NOT PERMISSIONS. What a page LOOKS like and what is ON it are
    // separate decisions: the cards still come from `apps`, checked the same
    // way for everyone. Changing somebody's theme grants and revokes nothing.
    // Their own choice first, the admin's setting second. Resolved by the
    // same function /looks uses, so the picker and the page cannot disagree
    // about which look is current.
    let theme = effective_theme(&common::people::normalise(&email), person);

    // The Robin's Garden cards (the website + "How it works") were removed 2026-11-04:
    // Jason no longer runs garden.example.org ("I no longer have control of this
    // website"). All garden cards and pages are off his site; Robin's own site and the
    // GitHub repository stay as they are.
    let cards = sections;

    match theme.as_str() {
        "kitchen" => render_themed(KitchenTemplate { who, sections: cards, year }.render(), "kitchen", &theme),
        "sky"     => render_themed(SkyTemplate     { who, sections: cards, year }.render(), "sky", &theme),
        "garden"  => render_themed(GardenThemeTemplate { who, sections: cards, year }.render(), "garden", &theme),
        "beach"   => render_themed(BeachTemplate   { who, sections: cards, year }.render(), "beach", &theme),
        "sunset"  => render_themed(SunsetTemplate  { who, sections: cards, year }.render(), "sunset", &theme),
        "space"   => render_themed(SpaceTemplate   { who, sections: cards, year }.render(), "space", &theme),
        "disco"   => render_themed(DiscoTemplate   { who, sections: cards, year }.render(), "disco", &theme),
        "park"    => render_themed(ParkTemplate    { who, sections: cards, year }.render(), "park", &theme),
        "chip"    => render_themed(ChipTemplate    { who, sections: cards, year }.render(), "chip", &theme),
        "western" => render_themed(WesternTemplate { who, sections: cards, shelves, year }.render(), "western", &theme),
        "travel"  => render_themed(TravelTemplate  { who, sections: cards, year }.render(), "travel", &theme),
        "cooking" => render_themed(CookingTemplate { who, sections: cards, year }.render(), "cooking", &theme),
        "office"  => render_themed(OfficeTemplate  { who, sections: cards, year }.render(), "office", &theme),
        // The colour themes share theme_solid.html; only the hue differs.
        other => match common::people::solid_hue(other) {
            Some((ground, accent)) => render_themed(
                SolidTemplate { who, sections: cards, year, ground, accent }.render(), "solid", &theme),
            None => render_themed(PlainTemplate { who, sections: cards, shelves, year }.render(), "plain", &theme),
        },
    }
}


/// A themed page, with the look-picker put on it.
///
/// WHY THE PICKER IS INJECTED HERE AND NOT WRITTEN INTO THE TEMPLATES. There
/// are fifteen theme templates and each is a complete, standalone page -- no
/// shared layout, on purpose, because that is what lets a theme be genuinely
/// different rather than a colour swap. Putting the picker in each of them
/// would be fifteen copies of the same markup, and the one that gets forgotten
/// is the one somebody is looking at.
///
/// So it is inserted after <body> on the way out: one definition, every theme,
/// including any theme added later without anyone remembering this exists.
///
/// "LIVE" MEANS ONE ROUND TRIP, NOT ZERO, AND THAT IS HONEST. Each theme is a
/// different document -- different fonts, different animations, different
/// structure -- so there is no set of styles to swap in place. The picker saves
/// the choice and reloads, which now lands immediately because these pages
/// stopped being cached. It is instant to look at; it just is not pretending to
/// be something it is not.
/// A one-line footer every themed page gets alongside the picker: the
/// privacy policy, reachable from anywhere someone is actually signed in.
const PRIVACY_FOOT: &str = r#"<div style="text-align:center;padding:18px 0 24px;opacity:.6;font-size:.85rem"><a href="/privacy" style="color:inherit">How this site treats your data</a></div>"#;

fn with_picker(html: String, current: &str) -> String {
    // Grouped by asking for the groups and then filtering, NOT by walking
    // THEMES and starting a new <optgroup> whenever the group changes. The
    // walking version is shorter and works right up until somebody appends a
    // theme whose group already appeared earlier in the list -- then that
    // group is rendered twice, and the picker quietly grows a second
    // "Places" heading. Depending on the order of a const to stay correct is
    // a trap for whoever adds the next theme, which is exactly who will not
    // know about it.
    let mut opts = String::new();
    for group in common::people::theme_groups() {
        opts.push_str(&format!("<optgroup label=\"{group}\">"));
        for (key, g, label, _blurb) in common::people::THEMES {
            if *g != group {
                continue;
            }
            let sel = if *key == current { " selected" } else { "" };
            opts.push_str(&format!("<option value=\"{key}\"{sel}>{label}</option>"));
        }
        opts.push_str("</optgroup>");
    }

    // Deliberately self-contained: its own colours, its own font stack, an id
    // rather than class names that could collide with a theme's stylesheet. A
    // picker that inherits the page it sits on is a picker that disappears on
    // half the themes.
    let widget = format!(
        r##"<div id="lookbar"><label for="lookpick">Look</label><select id="lookpick">{opts}</select><span id="looknote" aria-live="polite"></span></div>
<style>
#lookbar{{position:fixed;top:10px;right:12px;z-index:2147483000;display:flex;align-items:center;gap:8px;
padding:7px 11px;border-radius:999px;background:rgba(18,18,20,.82);backdrop-filter:blur(9px);
-webkit-backdrop-filter:blur(9px);box-shadow:0 2px 14px rgba(0,0,0,.32);
font:500 13px/1.2 system-ui,-apple-system,"Segoe UI",sans-serif;color:#f2f2f0}}
#lookbar label{{opacity:.72;letter-spacing:.02em}}
#lookbar select{{font:inherit;color:#f2f2f0;background:rgba(255,255,255,.10);
border:1px solid rgba(255,255,255,.18);border-radius:999px;padding:4px 26px 4px 10px;
cursor:pointer;appearance:none;-webkit-appearance:none;
background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='7'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%23fff' stroke-width='1.6' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
background-repeat:no-repeat;background-position:right 9px center}}
#lookbar select:focus-visible{{outline:2px solid #fff;outline-offset:2px}}
/* The dropdown list is drawn by the operating system in its own colours, so
   these two keep it readable instead of white text on white. */
#lookbar option,#lookbar optgroup{{background:#1b1b1e;color:#f2f2f0}}
#looknote{{font-size:12px;opacity:.75}}
@media print{{#lookbar{{display:none}}}}
@media (max-width:520px){{#lookbar{{top:auto;bottom:10px;right:10px}}}}
</style>
<script>
(function(){{
  var sel=document.getElementById('lookpick'),note=document.getElementById('looknote');
  if(!sel)return;
  sel.addEventListener('change',function(){{
    sel.disabled=true; note.textContent='saving…';
    fetch('/looks',{{method:'POST',
      headers:{{'Content-Type':'application/x-www-form-urlencoded'}},
      body:'theme='+encodeURIComponent(sel.value),credentials:'same-origin'}})
      .then(function(r){{ if(!r.ok) throw new Error(r.status); location.reload(); }})
      .catch(function(){{ sel.disabled=false; note.textContent='could not save'; }});
  }});
}})();
</script>"##
    );

    // Insert after the opening <body ...> tag. A template with no <body> is a
    // broken template rather than a themed page, so it is returned untouched
    // instead of having markup stapled to the front of it.
    match html.find("<body").and_then(|i| html[i..].find('>').map(|j| i + j + 1)) {
        Some(at) => {
            let mut out = String::with_capacity(html.len() + widget.len());
            out.push_str(&html[..at]);
            out.push_str(&widget);
            out.push_str(&html[at..]);
            out
        }
        None => html,
    }
}

/// render(), plus the look-picker. Only the personal pages use it -- the
/// portfolio and the sign-in page are not somebody's own page and have no
/// business carrying a personal setting.
fn render_themed(
    result: Result<String, askama::Error>,
    name: &str,
    current: &str,
) -> axum::response::Response {
    match result {
        Ok(html) => {
            let mut page = with_picker(html, current);
            // Before </body> so it sits at the true foot of every theme.
            if let Some(i) = page.rfind("</body>") {
                page.insert_str(i, PRIVACY_FOOT);
            }
            Html(page).into_response()
        }
        Err(e) => {
            tracing::error!("{name} template: {e}");
            (StatusCode::INTERNAL_SERVER_ERROR, "template error").into_response()
        }
    }
}

/// One place that turns a render result into a response, so a broken template
/// produces a logged 500 rather than a panic -- and so the three personas
/// cannot drift in how they fail.
fn render(result: Result<String, askama::Error>, name: &str) -> axum::response::Response {
    match result {
        Ok(html) => Html(html).into_response(),
        Err(e) => {
            tracing::error!("{name} template: {e}");
            (StatusCode::INTERNAL_SERVER_ERROR, "template error").into_response()
        }
    }
}

// The four added 2026-08-29. Each is its own full page rather than a palette
// swap -- see the note at the top of each template for what it is trying to
// be. They take the same three fields as every other theme, so adding one is
// a template plus four lines here and nothing else.
#[derive(Template)]
#[template(path = "theme_western.html")]
struct WesternTemplate {
    who: String,
    sections: Vec<Section>,
    shelves: Vec<Shelf>,
    year: i32,
}

#[derive(Template)]
#[template(path = "theme_travel.html")]
struct TravelTemplate {
    who: String,
    sections: Vec<Section>,
    year: i32,
}

#[derive(Template)]
#[template(path = "theme_cooking.html")]
struct CookingTemplate {
    who: String,
    sections: Vec<Section>,
    year: i32,
}

#[derive(Template)]
#[template(path = "theme_office.html")]
struct OfficeTemplate {
    who: String,
    sections: Vec<Section>,
    year: i32,
}

#[derive(Template)]
#[template(path = "theme_kitchen.html")]
struct KitchenTemplate {
    who: String,
    sections: Vec<Section>,
    year: i32,
}

#[derive(Template)]
#[template(path = "theme_garden.html")]
struct GardenThemeTemplate {
    who: String,
    sections: Vec<Section>,
    year: i32,
}

#[derive(Template)]
#[template(path = "theme_sky.html")]
struct SkyTemplate {
    who: String,
    sections: Vec<Section>,
    year: i32,
}

#[derive(Template)]
#[template(path = "index.html")]
struct IndexTemplate;

/// The privacy page. PUBLIC like the portfolio: it exists precisely for
/// someone who has not signed in and wants to know what signing in means.
#[derive(Template)]
#[template(path = "privacy.html")]
struct PrivacyTemplate {
    year: i32,
}

async fn privacy() -> impl IntoResponse {
    let year = common::current_year();
    render(PrivacyTemplate { year }.render(), "privacy")
}

/// The portfolio. PUBLIC, and that is the whole point -- it exists to be read
/// by someone who has never signed in and never will. It is rendered per
/// request rather than cached at startup only because it costs nothing; it
/// takes no inputs and could be hoisted into AppState if that ever changes.
async fn index() -> impl IntoResponse {
    render(IndexTemplate.render(), "index")
}

#[derive(Template)]
#[template(path = "theme_beach.html")]
struct BeachTemplate {
    who: String,
    sections: Vec<Section>,
    year: i32,
}

#[derive(Template)]
#[template(path = "theme_sunset.html")]
struct SunsetTemplate {
    who: String,
    sections: Vec<Section>,
    year: i32,
}

// ===========================================================================
//  Choosing your own look
// ===========================================================================

#[derive(Template)]
#[template(path = "theme_space.html")]
struct SpaceTemplate { who: String, sections: Vec<Section>, year: i32 }

#[derive(Template)]
#[template(path = "theme_disco.html")]
struct DiscoTemplate { who: String, sections: Vec<Section>, year: i32 }

#[derive(Template)]
#[template(path = "theme_park.html")]
struct ParkTemplate { who: String, sections: Vec<Section>, year: i32 }

#[derive(Template)]
#[template(path = "theme_chip.html")]
struct ChipTemplate { who: String, sections: Vec<Section>, year: i32 }

/// The five "just a colour" themes share this. `ground` and `accent` are the
/// only difference between them -- see common::people::solid_hue.
#[derive(Template)]
#[template(path = "theme_solid.html")]
struct SolidTemplate {
    who: String,
    sections: Vec<Section>,
    year: i32,
    ground: &'static str,
    accent: &'static str,
}

struct LookOption {
    /// Stable per-render id for the radio's `for`/`id` pair. The theme key is
    /// not usable there: the plain theme's key is the empty string.
    id: usize,
    key: &'static str,
    label: &'static str,
    blurb: &'static str,
    current: bool,
    /// A CSS background showing the theme's actual colours, so the choice can
    /// be made by looking rather than by reading a name.
    swatch: &'static str,
}

struct LookGroup {
    name: &'static str,
    themes: Vec<LookOption>,
}

#[derive(Template)]
#[template(path = "looks.html")]
struct LooksTemplate {
    groups: Vec<LookGroup>,
    saved: bool,
}

/// A swatch per theme. Hand-written rather than derived, because a theme's
/// character is not its first colour -- Beach is aqua AND sand, and a single
/// flat square would make half of these look identical.
fn swatch_for(key: &str) -> &'static str {
    match key {
        "garden" => "linear-gradient(135deg,#dff0e4,#5d9455 55%,#2f5733)",
        "beach"  => "linear-gradient(180deg,#7ecbe0 45%,#2fa3ab 62%,#f2e0bd)",
        "sky"    => "linear-gradient(135deg,#6bb8e0,#a8d8f0 55%,#f2789f)",
        "sunset" => "linear-gradient(180deg,#2b1740,#b8425c 55%,#ffb864)",
        "kitchen"=> "linear-gradient(135deg,#8ecae6,#ffe8c9 55%,#e8604c)",
        "park"   => "linear-gradient(135deg,#131f3a,#2a3d66 50%,#ffd77a)",
        "space"  => "linear-gradient(135deg,#070b1a,#141c3a 60%,#7fd8ff)",
        "disco"  => "linear-gradient(135deg,#150c26,#ff5fa2 45%,#4fd6ff)",
        "chip"   => "linear-gradient(135deg,#0e1220,#1f6b52 55%,#ffb347)",
        "solid-slate" => "#2b3440",
        "solid-teal"  => "#17383c",
        "solid-moss"  => "#22352a",
        "solid-plum"  => "#33253f",
        "solid-clay"  => "#3b2a24",
        _ => "linear-gradient(135deg,#0e1220,#26304a)",
    }
}

/// Who is asking, and what they may see. Shared by /home and /looks so the
/// two cannot disagree about identity.

/// The Engine Bay card's text IS the gauge: one block-character fuel bar
/// per drive, 0% empty to 100% full, read fresh from the host-metrics
/// feed on every render. Text, not markup, so all nineteen themes show it
/// without a single template change -- the card system only carries
/// strings, and a string can still be a bar chart.
fn drive_bars() -> String {
    let fallback = "Dials for the machine: processor, memory, video card, network.";
    let path = std::path::PathBuf::from(common::env_or(
        "LANDING_HOST_METRICS",
        "/host-metrics/host-metrics.json",
    ));
    let Ok(raw) = std::fs::read_to_string(&path) else {
        return fallback.into();
    };
    let Ok(v) = serde_json::from_str::<serde_json::Value>(&raw) else {
        return fallback.into();
    };
    // Grouped by machine, because the NUC's disks arrived on this card in
    // September 2026 and six unlabelled bars in a row do not tell anyone
    // which box is the one filling up. Order is stable -- SuperNova, then
    // the NUC, then anything new -- so the card does not reshuffle
    // between refreshes.
    let mut groups: Vec<(String, Vec<String>)> = Vec::new();
    for d in v["drives"].as_array().map(|a| a.as_slice()).unwrap_or(&[]) {
        let name = d["name"].as_str().unwrap_or("?");
        let machine = d["machine"].as_str().unwrap_or("SuperNova");
        let state = d["state"].as_str().unwrap_or("ok");
        // A LOCKED VAULT GETS WORDS, NOT A BAR. It measures as 0% used,
        // an empty bar reads as plenty of room, and what it actually
        // means is that nothing is being backed up tonight.
        let cell = if state == "locked" {
            format!("{name} \u{2014} LOCKED, nothing is backing up")
        } else {
            let pct = d["pct_used"].as_u64().unwrap_or(0).min(100) as usize;
            let filled = (pct + 5) / 10;
            let bar: String = std::iter::repeat('\u{2588}')
                .take(filled)
                .chain(std::iter::repeat('\u{2591}').take(10 - filled))
                .collect();
            let free = d["free_gb"].as_f64().unwrap_or(0.0);
            format!("{name} {bar} {pct}% \u{b7} {free:.0}GB free")
        };
        match groups.iter_mut().find(|(m, _)| m == machine) {
            Some((_, cells)) => cells.push(cell),
            None => groups.push((machine.to_string(), vec![cell])),
        }
    }
    if groups.is_empty() {
        return fallback.into();
    }
    groups
        .into_iter()
        .map(|(m, cells)| format!("{m}:  {}", cells.join("   ")))
        .collect::<Vec<_>>()
        .join("   |   ")
}

fn viewer(headers: &HeaderMap) -> (String, Option<common::people::Person>) {
    let email = headers
        .get("X-Auth-Email")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .trim()
        .to_string();
    let file = PathBuf::from(common::env_or(
        "LANDING_PEOPLE_FILE",
        "/data/auth/people.json",
    ));
    let people = common::people::read(&file);
    let found = common::people::find(&people, &email).cloned();
    (common::people::normalise(&email), found)
}

fn looks_file() -> PathBuf {
    PathBuf::from(common::env_or("LANDING_LOOKS_FILE", "/data/looks/looks.json"))
}

/// The look this person actually gets: their own choice first, the admin's
/// setting second, plain last. See looks.rs for why the two live apart.

/// The person's theme, as a stylesheet ANY of our apps can link.
///
/// This is how one choice follows somebody everywhere. The picker on the
/// start page saves a theme against the account; every app that links
/// /looks.css gets that theme back as CSS custom properties plus a light
/// base skin, chosen by the SAME lookup the start page uses. Change the
/// dropdown once and every page you open after is yours.
///
/// DELIBERATELY GENTLE. The full themes are whole pages -- a beach has
/// waves, the disco has a mirror ball. A stylesheet dropped into somebody
/// else's app cannot be a whole page without breaking that app, so this
/// carries the palette, not the scenery: background, ink, accent, and a
/// themed line across the top of the page so the choice is visible at a
/// glance. Apps that want more can build on the --look-* variables.
///
/// PRIVATE AND UNCACHEABLE, the same lesson the theme pages learned on
/// 2026-08-29: this response differs per person, and a shared cache serving
/// one person's look to another is a privacy bug, not a styling bug.
async fn looks_css(headers: HeaderMap) -> axum::response::Response {
    let (email, person) = viewer(&headers);
    let theme = effective_theme(&email, person.as_ref());

    // bg, surface, ink, accent -- the four every theme can be reduced to.
    let (bg, surface, ink, accent) = match theme.as_str() {
        "garden" => ("#101b10", "#18271a", "#e7f0e2", "#7dc98f"),
        "beach" => ("#0b1c22", "#12303a", "#eaf6f6", "#59c2c9"),
        "sky" => ("#0d1726", "#152238", "#eef3fb", "#6aa5e8"),
        "sunset" => ("#1a1020", "#28172e", "#f6e8dc", "#e8875c"),
        "kitchen" => ("#fff8ec", "#ffffff", "#33291a", "#d98e2b"),
        "park" => ("#131022", "#1e1834", "#f3eefc", "#e8619a"),
        "western" => ("#1d130c", "#2c1d12", "#f3e6d5", "#d97f3e"),
        "travel" => ("#101418", "#1a2027", "#ece9e2", "#c9a227"),
        "cooking" => ("#161a1c", "#20262a", "#e9edee", "#d96c4f"),
        "office" => ("#f4f2ec", "#ffffff", "#2a2a26", "#3f6bb0"),
        "space" => ("#070a14", "#101527", "#e6e9f5", "#8b5cf6"),
        "disco" => ("#120716", "#221030", "#f7e9fb", "#e85cc0"),
        "chip" => ("#0a140e", "#12241a", "#dff0e4", "#3fd97f"),
        "solid-slate" => ("#232a33", "#2c3540", "#e8ecf1", "#8fa7c2"),
        "solid-teal" => ("#0f2a2a", "#153736", "#e4f2f0", "#4fb3a8"),
        "solid-moss" => ("#1a2418", "#233020", "#e8efe4", "#8fb573"),
        "solid-plum" => ("#241826", "#302033", "#f0e8f1", "#b285b8"),
        "solid-clay" => ("#291c15", "#36251b", "#f2e9e2", "#c98960"),
        _ => ("#101215", "#191d22", "#e8e8e6", "#7aa2f7"),
    };

    let css = format!(
        ":root{{--look-bg:{bg};--look-surface:{surface};--look-ink:{ink};--look-accent:{accent};--look-theme:'{theme}'}}\n\
body{{background-color:var(--look-bg);color:var(--look-ink);\n\
  border-top:4px solid var(--look-accent)}}\n\
a{{color:var(--look-accent)}}\n"
    );

    (
        [
            (header::CONTENT_TYPE, "text/css; charset=utf-8"),
            // Per-person response. public would hand your look to the family.
            (header::CACHE_CONTROL, "private, no-cache"),
        ],
        css,
    )
        .into_response()
}

fn effective_theme(email: &str, person: Option<&common::people::Person>) -> String {
    let chosen = looks::read(&looks_file()).get(email).cloned();
    let theme = chosen
        .or_else(|| person.map(|p| p.theme.clone()))
        .unwrap_or_default();
    if common::people::theme_exists(&theme) {
        theme
    } else {
        String::new()
    }
}

async fn looks_page(headers: HeaderMap) -> impl IntoResponse {
    let (email, person) = viewer(&headers);
    let current = effective_theme(&email, person.as_ref());
    render(build_looks(&current, false).render(), "looks")
}

async fn looks_save(headers: HeaderMap, body: String) -> impl IntoResponse {
    let (email, person) = viewer(&headers);
    if email.is_empty() {
        // Only reachable if this route were ever mounted without its gate.
        return (StatusCode::FORBIDDEN, "Not signed in.").into_response();
    }

    // Whatever arrived is validated against the shipped list before it is
    // stored -- a form field is whatever the sender typed.
    let raw = form_value(&body, "theme");
    let theme = if common::people::theme_exists(&raw) { raw } else { String::new() };

    if let Err(e) = looks::set(&looks_file(), &email, &theme) {
        tracing::error!("could not save look for {email}: {e}");
        return render(build_looks(&theme, false).render(), "looks");
    }
    tracing::info!(email = %email, theme = %theme, "look changed");

    let _ = person;
    render(build_looks(&theme, true).render(), "looks")
}

fn build_looks(current: &str, saved: bool) -> LooksTemplate {
    let mut groups: Vec<LookGroup> = Vec::new();
    let mut id = 0usize;
    for name in common::people::theme_groups() {
        let themes: Vec<LookOption> = common::people::THEMES
            .iter()
            .filter(|(_, g, _, _)| *g == name)
            .map(|(key, _, label, blurb)| {
                id += 1;
                LookOption {
                    id,
                    key,
                    label,
                    blurb,
                    current: *key == current,
                    swatch: swatch_for(key),
                }
            })
            .collect();
        groups.push(LookGroup { name, themes });
    }
    LooksTemplate { groups, saved }
}

/// One field out of an application/x-www-form-urlencoded body.
fn form_value(body: &str, key: &str) -> String {
    for pair in body.split('&') {
        let mut it = pair.splitn(2, '=');
        if it.next() == Some(key) {
            let raw = it.next().unwrap_or("").replace('+', " ");
            return percent_decode(&raw);
        }
    }
    String::new()
}

fn percent_decode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' && i + 2 < b.len() {
            if let Ok(v) = u8::from_str_radix(&s[i + 1..i + 3], 16) {
                out.push(v);
                i += 3;
                continue;
            }
        }
        out.push(b[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}
