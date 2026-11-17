//! Who may sign in to /mama and /casey, and what their password is.
//!
//! ONE FILE, TWO PROCESSES, AND WHY THAT IS THE WHOLE DESIGN
//! `admin` writes this file and `auth` reads it. Neither talks to the other,
//! there is no database, and the file is plain JSON in a folder you can open.
//! That means "who can get in" is answerable by looking, and recoverable by
//! editing, without either service running.
//!
//! It also means the two must agree byte for byte on the format, which is why
//! this lives in `common` rather than being written twice. A drift between the
//! writer and the reader here does not look like a bug -- it looks like nobody
//! can log in.
//!
//! WHY THE PASSWORD IS A HASH AND NOT A PASSWORD
//! argon2id, deliberately slow and memory-hard. Reading this file gives you the
//! list of who has an account; it does not give you their passwords, and
//! guessing at them costs real time and real RAM per attempt.

use argon2::password_hash::{phc::PasswordHash, PasswordHasher, PasswordVerifier};
use argon2::Argon2;
use serde::{Deserialize, Serialize};
use std::path::Path;

/// Every app whose door this file can be checked against. A page not on this
/// list (the admin panel itself, /auth, /dee, /legacy) is not part of the
/// per-app toggle at all -- being on the sign-in list is still the only gate
/// there, same as before this existed.
///
/// "recipe" added 2026-08-25, Jason's own request: he wants a way to let
/// somebody outside the household (not one of the four regulars) into
/// /recipe specifically, without handing them /kitchen, /mama, /casey or /kb
/// too. Before this, /recipe was not on the list at all, which meant the
/// per-app toggle could not narrow it -- anyone who could sign in at all
/// could reach it (see app_for_uri() in the auth service). The admin's
/// People page needs no changes for this: it already renders one checkbox
/// per ALL_APPS entry generically (see admin's render_people()).
///
/// "status" and "logs" added 2026-08-27: the NUC homelab's Uptime Kuma and
/// Dozzle, reverse-proxied from the existing Caddyfile (see
/// share/nuc-homelab/caddyfile-additions.txt). These are ops-only tools --
/// there is no separate "default off" tier in this file, so the new boxes
/// default UNRESTRICTED like every app here (apps_unrestricted defaults
/// true). Go narrow each non-Jason person on /admin/people after deploying
/// this if they shouldn't see these two.
///
/// "git", "tools", "pdf" added same day, same NUC batch: Gitea, IT-Tools,
/// Stirling-PDF. "git"/"tools" are arguably ops-only like status/logs
/// above; "pdf" is more family-facing per README.md's "Proposed layout" --
/// same manual narrowing story either way, nothing enforced here.
///
/// "search" (SearXNG), "links" (Linkding) and "home" (Home Assistant) were
/// all added then REMOVED same day (2026-08-28), Jason's call -- search
/// because SearXNG only federates external engines (he wanted his own
/// content searched), links and home-assistant with no reason given beyond
/// wanting them gone. See share/nuc-homelab/README.md.
/// "portainer" added 2026-08-28: the Docker UI. The most ops-only entry on
/// this list by some distance -- it holds the Docker socket. Narrow it to
/// Jason alone on /admin/people; it defaults unrestricted like every other
/// app here until someone does that by hand.
/// "photos" added 2026-08-30: Immich, the photo library.
///
/// THIS ONE IS DIFFERENT FROM EVERY OTHER ENTRY HERE and the difference is
/// worth stating, because reading the list would otherwise mislead you.
/// Every other app is gated by Caddy's forward_auth, which asks this file
/// before letting a request through. Immich is NOT: it lives on its own
/// hostname and authenticates through Authentik directly, because a phone app
/// cannot complete a browser sign-in against a forward_auth gate.
///
/// So this grant controls whether the CARD APPEARS on somebody's start page,
/// not whether they can reach Immich. Access there is decided by whether
/// Authentik lets them in and what Immich's own accounts say. Removing this
/// grant tidies their page; it does not lock the door.
///
/// "scratch" added 2026-08-28: the travel scratch-off map on the NUC. Family
/// -facing, not ops -- but note it is the first app here that holds a record
/// of the household's PHYSICAL movements, so "who can see this" is a different
/// question from "who can see the recipe box". It has no login of its own, so
/// this list is the whole of its access control.
pub const ALL_APPS: &[&str] = &[
    // "grab" added 2026-09-11: the video fetcher Jason and Sachin submit
    // URLs to. EXPLICIT_ONLY below, so it is never inherited -- see there.
    "grab",
    "mama", "kitchen", "kb", "casey", "recipe", "status", "logs", "git", "tools", "pdf",
    "portainer", "grafana", "paperless", "dashboard", "scratch", "special", "photos",
    // "tribute" and "studio" added 2026-08-31: The First Voice -- the Pee-Wee
    // Harris tribute -- and its workbench. CARD-ONLY, like "photos" above:
    // both live on their own subdomains outside the forward_auth gate (the
    // tribute is public by design, the Studio unwalled at Jason's explicit
    // instruction), so these grants control whose start page shows the card,
    // not who can reach the site.
    // ("tribute", NOT "legacy": app keys derive gates for matching paths in
    // app_for_uri, and /legacy is the OLD family album with its own password,
    // deliberately outside this toggle. The auth tests caught the collision.)
    "tribute", "studio",
    // "engine" added 2026-08-31: the tachometer dial page (/engine). Gates
    // the path AND shows the card, like "connect" below.
    "engine",
    // "connect" added 2026-08-31: the link-your-Google-Photos page (/connect).
    // Unlike legacy/studio above this one DOES gate a real path behind the
    // forward_auth wall, and shows a card, both from this one grant.
    "connect",
    // "find" added 2026-09-07: the household search (/find). Gates the path
    // and shows the card.
    //
    // THE GRANT IS NOT WHAT KEEPS SEARCH PRIVATE, and that is worth being
    // clear about. Every result is already filtered to the address the gate
    // supplies, so granting this to somebody shows them their OWN recipes,
    // documents and workouts and nothing of anyone else's. This decides who
    // gets a search box, not who gets whose things.
    "find",
    // Added 2026-09-10, all four for Sachin's studying and the household
    // records that came with it. Three gate a real path AND show a card;
    // "homebox" is card-only, like "tribute" and "studio" above, because it
    // lives on its own hostname.
    //
    // "read" is Kavita and "audiobookshelf" is the player. THE KEYS MATCH
    // THE PATHS, which is what app_for_uri needs -- it gates /{app}. That
    // is also why the player's key is not "listen": /listen is a redirect
    // to /audiobookshelf (the only prefix Kavita's... no, Audiobookshelf's
    // client will accept), and gating a redirect gates nothing.
    "study", "read", "audiobookshelf", "homebox",
    // "money" added 2026-09-10: MoneyMatter, self-hosted. Card-only,
    // like "homebox" -- it lives on its own hostname.
    "money",
    // "music" added 2026-09-15: Navidrome on the Pi 5. Card-only, like
    // "money" and "homebox" -- it lives on music.home.example.net,
    // so this grant decides whose start page shows the card; the subdomain's
    // own forward_auth decides who gets in.
    //
    // The library was reachable ONLY at http://192.168.1.69:4533 on the LAN
    // until now: no route, no card, 117,321 files nobody could play from a
    // phone. Navidrome had been configured for header auth all along
    // (ND_EXTAUTH_USERHEADER=X-Auth-Email) and simply never wired to anything.
    "music",
    // --- 2026-10-29: paths confirmed gated by forward_auth in the live Caddyfile
    // but missing from this list. All carry a TODO so every path gets a second
    // look when the app behind it is better understood.

    // "ability" -- /ability* -- fitness / ability tracker. Gated.
    // TODO: SSO validation -- confirm the app behind this and whether card-only or full gate.
    "ability",

    // "dl" -- /dl/* -- private file downloads served directly by Caddy. Gated.
    // TODO: SSO validation -- Jason-only? revisit before granting to guests.
    "dl",

    // "files" -- /files/* -- file manager. Gated.
    // TODO: SSO validation -- confirm which service and default access level.
    "files",

    // "home" -- /home* -- the household home/dashboard page. Previously removed
    // from this list intentionally; re-added 2026-10-29 at Jason's request.
    // TODO: SSO validation -- recheck why it was removed and whether that reason still applies.
    "home",

    // "looks" -- /looks* -- the start-page look picker. Gated so the service
    // knows who is asking when writing to the looks store.
    // TODO: SSO validation -- this may be better as an automatic grant for all
    // authenticated accounts rather than a toggleable permission.
    "looks",
    // "backlog", "tickets", "todo" added 2026-10-30: these were already in
    // EXPLICIT_ONLY below (so an unrestricted account never silently
    // inherited them) but were MISSING from ALL_APPS itself, which meant
    // app_for_uri() never recognized these paths and the per-person toggle
    // had no effect on them at all -- ANY signed-in account could reach
    // them regardless of apps/apps_unrestricted. Jason: "I have a static
    // version of them so they should be able to access them... if I assign
    // those pages to them... they don't need access to all apps." Adding
    // them here is what actually turns the toggle on; Jason's own account
    // needed "backlog","tickets","todo" added to its explicit apps list in
    // people.json in the SAME change, or his own unrestricted account would
    // have lost access the moment this shipped (EXPLICIT_ONLY ignores
    // apps_unrestricted by design -- see the doc comment below).
    // "reviews" and "secrets" deliberately NOT added here -- Jason
    // confirmed these stay owner-only, unlike the other three.
    "backlog", "tickets", "todo",
    // "projects" added 2026-10-31: /projects, the live view of the Proton Pass
    // "Current Project" note. NOT in EXPLICIT_ONLY on purpose: the guest view only
    // shows "Working on" and "Next step" (Caddy serves the whole-note owner.html
    // only to the owner's email), so an unrestricted account inheriting it is safe.
    // Without this entry app_for_uri() would not recognise the path and ANY
    // signed-in account could reach it, ignoring the per-person toggle.
    "projects",
    // "portfolio" added 2026-10-31: gates /portfolio/completed (the auto-generated
    // finished-work list). The public /portfolio itself has NO forward_auth, so this key is
    // only consulted for the sub-path. Not EXPLICIT_ONLY: the guest view is scrubbed of
    // names, hosts and credential-shaped entries, and only the owner email gets the full list.
    "portfolio",
];

/// Apps that an "unrestricted" account still does NOT get. Only an explicit
/// grant in that person's own list opens these.
///
/// WHY THIS EXISTS. "Unrestricted" is a convenience meaning "everything",
/// written when everything was a recipe box and a PDF tool. Then /special was
/// added, and /special is a live camera in a bedroom -- so "everything"
/// quietly started including that, for every unrestricted account, without
/// anyone deciding it should.
///
/// That is exactly what had happened: user@example.com is unrestricted,
/// has no name against it, and could watch the camera. Not through a bug --
/// through the rule working as written.
///
/// Editing that one account would have fixed today and not tomorrow, because
/// the next unrestricted account would inherit the same access the moment it
/// was created, silently. Listing the app here inverts the default for it: the
/// blast radius of "give this person everything" no longer includes the
/// bedroom, and adding a sensitive app in future is one line rather than an
/// audit of every account.
pub const EXPLICIT_ONLY: &[&str] = &[
    "special", "grab",
    // Admin / owner tools -- 2026-10-29. These are personal-dashboard pages
    // that carry no guest-facing value and should never land on an unrestricted
    // account by default. Each has a forward_auth gate in the Caddyfile already;
    // EXPLICIT_ONLY makes sure "give this person everything" excludes them.
    // TODO: SSO validation -- revisit before sharing any of these with a guest.
    "backlog", "reviews", "secrets", "tickets", "todo",
];
// "grab" added 2026-09-11. Jason: "i want this only to be avaible to the
// sso for me and him". It downloads to a shared and a PRIVATE library, so
// the default has to be closed: an unrestricted account must not inherit
// it the way it inherits everything else. Same reasoning as "special",
// and the same one-line fix rather than auditing every account.

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Person {
    pub email: String,
    /// argon2id. EMPTY means the account exists but has no password yet, and
    /// `verify` refuses it. That state is reachable and normal -- it is what an
    /// account looks like between being added and being given a password -- so
    /// it must fail closed rather than fail open.
    #[serde(default)]
    pub hash: String,
    /// Set when you choose the password for somebody else. They are made to
    /// pick their own on the next sign-in, so the one you typed and possibly
    /// said out loud stops being the password.
    #[serde(default)]
    pub must_change: bool,
    /// Unix seconds. Only for showing "added 3 days ago" on the admin page.
    #[serde(default)]
    pub added: u64,
    /// True until somebody explicitly narrows this account through the
    /// admin's "Which pages" control. Defaults true via serde for every
    /// account that existed before this field did, and for a freshly added
    /// one, so this feature never silently locked out somebody who could
    /// already get in. `apps` below is meaningless while this is true.
    ///
    /// A SEPARATE FIELD, NOT "apps IS EMPTY", ON PURPOSE. Empty and
    /// unrestricted look the same on the wire but mean opposite things --
    /// "every box ticked" and "every box unticked" both produce an empty
    /// checked set from the form. Collapsing both into one Vec<String>
    /// makes "revoke everything" indistinguishable from "never restricted
    /// at all", which is the one mistake an access-control toggle cannot
    /// afford to make quietly.
    #[serde(default = "default_true")]
    pub apps_unrestricted: bool,
    /// Which of ALL_APPS this person may reach. Read only when
    /// apps_unrestricted is false. Saving through the admin form always
    /// sets both fields together -- see admin's people_apps().
    #[serde(default)]
    pub apps: Vec<String>,
    /// Which look their start page wears. One of THEMES below; empty means
    /// the plain one.
    ///
    /// A FIELD, NOT A TEMPLATE PER PERSON. The first version of this hardcoded
    /// one HTML file per email address -- mom.html, casey.html, robin.html -- with
    /// the addresses in environment variables. That works for three people and
    /// stops working at four: every new account needed a new file, a code
    /// change and a rebuild, and inviting somebody to look at one recipe was
    /// not worth any of that.
    ///
    /// Themes are reusable and unrelated to permissions. Two people can wear
    /// the same one; changing somebody's theme changes nothing about what they
    /// can reach. What is ON their page still comes from `apps` above.
    #[serde(default)]
    pub theme: String,
    /// What to call them on their own page. Empty falls back to the part of
    /// the address before the @.
    ///
    /// THE FALLBACK IS WHY THIS EXISTS. Deriving a name from an address gives
    /// you "davetta1" and "kopoboi85" -- which is fine in a log and unkind on
    /// a page that greets somebody by it. A page made for a person should know
    /// their name.
    #[serde(default)]
    pub name: String,
}

/// The start-page looks that can be chosen in /admin/people.
///
/// `key` is stored on the account; `label` is what the admin form shows;
/// `blurb` says who it suits, because a name alone does not tell you whether
/// "Meadow" is the green one or the blue one.
/// One start-page look: (key, group, label, blurb).
///
/// GROUPED because fifteen options in a flat dropdown is a list nobody reads
/// to the bottom of. The group is a plain string rather than an enum on
/// purpose -- adding "Weather" later should be typing a word, not editing a
/// type and every match arm that touches it.
///
/// The blurb is not decoration. "Meadow" does not tell you whether it is the
/// green one or the blue one, and somebody picking a look for their mother
/// should not have to try six to find out.
pub const THEMES: &[(&str, &str, &str, &str)] = &[
    // --- Outdoors ---------------------------------------------------------
    ("garden",  "Outdoors", "Garden",        "Green and lush, moving in the wind."),
    ("beach",   "Outdoors", "Beach",         "Aqua and sand, waves rolling at the foot."),
    ("sky",     "Outdoors", "Open sky",      "Breezy blue, jets and flowers."),
    ("sunset",  "Outdoors", "Sunset",        "Warm and dark. The calm one for evenings."),

    // --- Places -----------------------------------------------------------
    ("kitchen", "Places",   "Sunny kitchen", "A pot on the boil and a road. Big, easy text."),
    ("park",    "Places",   "Theme park",    "Big wheel, striped tents, string lights."),
    ("western", "Places",   "Western",       "Desert dusk, mesas and a low sun."),
    ("travel",  "Places",   "Travel",        "Stamps, tickets and a route across a map."),

    // --- At work ------------------------------------------------------------
    // "cooking" is NOT a duplicate of "kitchen" above. Kitchen is a sunny room
    // with big friendly text -- built for Mom, and deliberately gentle.
    // Cooking is the working surface: dark slate, chopping board, the tools
    // out. Same subject, opposite mood, which is the point of having both.
    ("cooking", "At work",  "Cooking",       "Slate worktop, board and knife. The busy one."),
    ("office",  "At work",  "Office",        "Paper, ruled lines and a desk lamp. Sober."),

    // --- After dark -------------------------------------------------------
    ("space",   "After dark", "Space",       "Stars, a planet, and a great deal of quiet."),
    ("disco",   "After dark", "Disco",       "Mirror ball and a lit floor. Loud on purpose."),

    // --- Machines ---------------------------------------------------------
    ("chip",    "Machines", "Circuit board", "Traces and solder mask. The one Jason uses."),
    ("",        "Machines", "Plain",         "The house style. Dark, quiet, suits anyone."),

    // --- Just a colour ----------------------------------------------------
    // ONE TEMPLATE, FIVE KEYS. These share theme_solid.html and differ only
    // in the hue handed to it -- see solid_hue(). Somebody who wants a calm
    // background and no scenery should not have to accept a beach.
    ("solid-slate",  "Just a colour", "Slate",  "Cool grey-blue. Quietest of the lot."),
    ("solid-teal",   "Just a colour", "Teal",   "Deep blue-green."),
    ("solid-moss",   "Just a colour", "Moss",   "Soft deep green."),
    ("solid-plum",   "Just a colour", "Plum",   "Muted purple."),
    ("solid-clay",   "Just a colour", "Clay",   "Warm terracotta brown."),
];

/// The groups, in the order they should be shown. Derived from THEMES rather
/// than written twice, so a new group cannot appear in one list and not the
/// other.
pub fn theme_groups() -> Vec<&'static str> {
    let mut out: Vec<&str> = Vec::new();
    for (_, group, _, _) in THEMES {
        if !out.contains(group) {
            out.push(group);
        }
    }
    out
}

/// The background for a "Just a colour" theme. Returns None for anything
/// else, which is how the dispatch knows whether to reach for theme_solid.
pub fn solid_hue(key: &str) -> Option<(&'static str, &'static str)> {
    // (ground, accent) -- both dark enough to carry the light text the
    // template sets, checked against it rather than picked from a wheel.
    Some(match key {
        "solid-slate" => ("#2b3440", "#8fb3d9"),
        "solid-teal"  => ("#17383c", "#6fd0c4"),
        "solid-moss"  => ("#22352a", "#8fcf9a"),
        "solid-plum"  => ("#33253f", "#c39ce0"),
        "solid-clay"  => ("#3b2a24", "#e0a180"),
        _ => return None,
    })
}

/// True if `key` is a theme we actually ship. Anything else falls back to
/// plain rather than failing -- a theme that was renamed or removed should
/// leave somebody with a boring page, never a broken one.
pub fn theme_exists(key: &str) -> bool {
    THEMES.iter().any(|(k, _, _, _)| *k == key)
}

fn default_true() -> bool {
    true
}

impl Person {
    pub fn new(email: &str) -> Self {
        Person {
            email: normalise(email),
            hash: String::new(),
            must_change: false,
            added: now(),
            apps_unrestricted: true,
            apps: Vec::new(),
            theme: String::new(),
            name: String::new(),
        }
    }
    pub fn has_password(&self) -> bool {
        !self.hash.trim().is_empty()
    }
    /// Whether this person may reach `app` (one of ALL_APPS).
    ///
    /// Note the order: EXPLICIT_ONLY apps are checked BEFORE the unrestricted
    /// flag, so being unrestricted does not reach them. See EXPLICIT_ONLY.
    pub fn can_use(&self, app: &str) -> bool {
        self.level_for(app).is_some()
    }

    /// How MUCH of `app` this person gets: None (no access), "view", or
    /// "full".
    ///
    /// GRANULAR PERMISSIONS, added 2026-08-31 at Jason's direction. A grant
    /// in `apps` is either a bare name ("recipe" = full access, which is
    /// what every pre-existing grant means, so nothing already saved changes
    /// meaning) or "name:level" ("recipe:view"). The admin panel writes
    /// these; the auth gate enforces them; apps can read the level from the
    /// X-Auth-Level header when they want finer behaviour of their own.
    ///
    /// EXPLICIT_ONLY apps still ignore the unrestricted flag, same as
    /// can_use always did. An unrestricted account gets "full" everywhere
    /// else -- unrestricted has always meant "the owner's own account".
    pub fn level_for(&self, app: &str) -> Option<&str> {
        for a in &self.apps {
            if a == app {
                return Some("full");
            }
            if let Some(rest) = a.strip_prefix(app) {
                if let Some(level) = rest.strip_prefix(':') {
                    return Some(if level.is_empty() { "full" } else { level });
                }
            }
        }
        if !EXPLICIT_ONLY.contains(&app) && self.apps_unrestricted {
            return Some("full");
        }
        None
    }
}

/// The levels the admin panel offers, in display order. "none" is the
/// absence of a grant rather than a stored value.
pub const LEVELS: &[(&str, &str)] = &[
    ("full", "Full"),
    ("view", "View only"),
];

pub fn now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Lowercased and trimmed. Mail addresses are case-insensitive in practice, and
/// "user@example.com" not matching "user@example.com" is the kind of bug
/// that gets diagnosed as "the site is broken".
pub fn normalise(email: &str) -> String {
    email.trim().to_lowercase()
}

/// Read the list, tolerating the older shape.
///
/// The first version of this file was a plain list of addresses, because
/// sign-in was by emailed link and there were no passwords to store. Those
/// files still exist. Rather than make somebody migrate by hand, read them:
/// each address becomes an account with no password, which the admin shows as
/// "no password set" and `verify` refuses. Nobody is locked out silently and
/// nobody is let in silently.
pub fn read(path: &Path) -> Vec<Person> {
    let Ok(bytes) = std::fs::read(path) else {
        return Vec::new();
    };
    if let Ok(list) = serde_json::from_slice::<Vec<Person>>(&bytes) {
        return list;
    }
    if let Ok(list) = serde_json::from_slice::<Vec<String>>(&bytes) {
        return list.iter().map(|e| Person::new(e)).collect();
    }
    Vec::new()
}

/// Write the list atomically.
///
/// Temp file then rename, because `auth` reads this on every single request it
/// checks. A plain overwrite gives it a window to read a half-written file and
/// conclude that nobody is allowed in.
pub fn write(path: &Path, list: &[Person]) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let mut json = serde_json::to_vec_pretty(list)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    json.push(b'\n');
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, &json)?;
    std::fs::rename(&tmp, path)
}

pub fn find<'a>(list: &'a [Person], email: &str) -> Option<&'a Person> {
    let want = normalise(email);
    list.iter().find(|p| p.email == want)
}

/// Hash a password for storage.
pub fn hash_password(password: &str) -> anyhow::Result<String> {
    Argon2::default()
    .hash_password(password.as_bytes())
        .map(|h| h.to_string())
        .map_err(|e| anyhow::anyhow!("could not hash the password: {e}"))
}

/// Check a password against a stored hash.
///
/// Returns false for an empty hash and for a hash that does not parse, rather
/// than erroring. Both mean "this account cannot be signed into", and there is
/// no caller for whom the distinction changes what happens next.
pub fn verify(hash: &str, password: &str) -> bool {
    if hash.trim().is_empty() {
        return false;
    }
    match PasswordHash::new(hash) {
        Ok(parsed) => Argon2::default()
            .verify_password(password.as_bytes(), &parsed)
            .is_ok(),
        Err(_) => false,
    }
}

/// The shortest password worth allowing.
///
/// Eight, matching what /mama already asks for. A number chosen to be the same
/// as the one next door beats a number chosen to be marginally safer: two
/// different rules for two doors in the same house is how people end up writing
/// passwords down.
pub const MIN_PASSWORD: usize = 8;

/// A handful of passwords that are never acceptable, however long they are.
///
/// NOT A DICTIONARY, AND NOT PRETENDING TO BE. A real blocklist is millions
/// of entries and belongs in a file; this is the short list of things people
/// actually type when a form demands a password, and it exists so that
/// "Password1!" cannot satisfy a rule written to stop exactly that.
const NEVER: &[&str] = &[
    "password", "passw0rd", "qwerty", "letmein", "welcome", "admin",
    "changeme", "iloveyou", "monkey", "dragon", "football", "abc123",
];

/// Why this password is not acceptable, or None if it is.
///
/// FOUR CHARACTER CLASSES, at Jason's request 2026-09-24: an uppercase, a
/// lowercase, a number and a symbol.
///
/// THE LENGTH RULE STILL DOES MOST OF THE WORK. Character classes are worth
/// less than people think -- "Passw0rd!" satisfies every one of them and is
/// among the first thousand guesses any attacker makes -- which is why the
/// obvious words are refused outright and why the minimum length is checked
/// first. The classes are here because they were asked for and they do raise
/// the floor; they are not the reason this is safe.
///
/// THE COMPLAINT NAMES ONLY WHAT IS MISSING. Listing every rule every time
/// teaches people to read past the message.
pub fn password_problem(password: &str) -> Option<String> {
    let n = password.chars().count();
    if n < MIN_PASSWORD {
        return Some(format!("Use at least {MIN_PASSWORD} characters."));
    }
    // A cap, because argon2 hashes whatever it is given and a megabyte of
    // password is a way to make the server do a megabyte of work per guess.
    if password.len() > 200 {
        return Some("That is longer than 200 characters.".into());
    }

    let lower = password.to_lowercase();
    if NEVER.iter().any(|w| lower == *w || lower.trim_end_matches(
        |c: char| c.is_ascii_digit() || c == '!' || c == '?') == *w)
    {
        return Some("That is one of the most-guessed passwords. Pick another.".into());
    }

    let mut missing = Vec::new();
    if !password.chars().any(|c| c.is_uppercase()) {
        missing.push("an uppercase letter");
    }
    if !password.chars().any(|c| c.is_lowercase()) {
        missing.push("a lowercase letter");
    }
    if !password.chars().any(|c| c.is_numeric()) {
        missing.push("a number");
    }
    // Anything that is not a letter, a number or a space counts. Naming a
    // specific set of symbols would refuse the ones on somebody's keyboard
    // layout and not others.
    if !password
        .chars()
        .any(|c| !c.is_alphanumeric() && !c.is_whitespace())
    {
        missing.push("a symbol");
    }
    if missing.is_empty() {
        return None;
    }
    Some(format!("Add {}.", join_and(&missing)))
}

fn join_and(items: &[&str]) -> String {
    match items {
        [] => String::new(),
        [a] => a.to_string(),
        [a, b] => format!("{a} and {b}"),
        [rest @ .., last] => format!("{}, and {last}", rest.join(", ")),
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn levels_parse_and_default_to_full() {
        let mut p = super::Person::new("a@b.c");
        p.apps_unrestricted = false; // Person::new defaults open; grants only
        p.apps = vec!["recipe".into(), "special:view".into(), "kb:".into()];
        assert_eq!(p.level_for("recipe"), Some("full"));
        assert_eq!(p.level_for("special"), Some("view"));
        assert_eq!(p.level_for("kb"), Some("full"));
        assert_eq!(p.level_for("git"), None);
        assert!(p.can_use("special"));
        assert!(!p.can_use("git"));
        // "scratch" must not match a grant for "scratch2"
        p.apps = vec!["scratch2:view".into()];
        assert_eq!(p.level_for("scratch"), None);
    }

    #[test]
    fn unrestricted_is_full_except_explicit_only() {
        let mut p = super::Person::new("a@b.c");
        p.apps_unrestricted = true;
        assert_eq!(p.level_for("recipe"), Some("full"));
        for e in super::EXPLICIT_ONLY {
            assert_eq!(p.level_for(e), None);
        }
    }

    use super::*;

    /// The camera is not covered by "everything".
    ///
    /// This is the regression test for a real exposure: an unrestricted
    /// account with nobody's name on it could reach the bedroom camera,
    /// because unrestricted meant every app and /special is an app. Fixing the
    /// account would have fixed one day; this fixes the rule.
    #[test]
    fn unrestricted_does_not_include_the_camera() {
        let mut everything = Person::new("everything@example.test");
        everything.apps_unrestricted = true;
        everything.apps = vec![];

        // Unrestricted still opens the ordinary apps...
        assert!(everything.can_use("recipe"));
        assert!(everything.can_use("scratch"));
        // ...and specifically does NOT open the ones that need asking for.
        assert!(
            !everything.can_use("special"),
            "an unrestricted account must not reach the camera without an explicit grant"
        );

        // An explicit grant is what opens it, unrestricted or not.
        let mut granted = Person::new("granted@example.test");
        granted.apps_unrestricted = false;
        granted.apps = vec!["special".to_string()];
        assert!(granted.can_use("special"));

        // And a grant to something else does not leak across.
        let mut other = Person::new("other@example.test");
        other.apps = vec!["recipe".to_string()];
        assert!(!other.can_use("special"));
    }

    /// Every EXPLICIT_ONLY entry must be a real app, or it silently guards
    /// nothing -- a typo here would read as "locked down" while locking
    /// nothing down.
    #[test]
    fn explicit_only_apps_are_real_apps() {
        for app in EXPLICIT_ONLY {
            assert!(
                ALL_APPS.contains(app),
                "EXPLICIT_ONLY lists {app:?}, which is not in ALL_APPS"
            );
        }
    }

    #[test]
    fn a_password_verifies_and_a_wrong_one_does_not() {
        let h = hash_password("correct horse battery").unwrap();
        assert!(verify(&h, "correct horse battery"));
        assert!(!verify(&h, "Correct horse battery"));
        assert!(!verify(&h, ""));
    }

    #[test]
    fn an_account_with_no_password_can_never_be_signed_into() {
        let p = Person::new("nobody@example.com");
        assert!(!p.has_password());
        assert!(!verify(&p.hash, ""), "an empty password must not match an empty hash");
        assert!(!verify(&p.hash, "anything"));
    }

    #[test]
    fn a_corrupt_hash_fails_closed() {
        assert!(!verify("$argon2id$not-a-real-hash", "anything"));
        assert!(!verify("hunter2", "hunter2"), "a plaintext password in the hash field must not work");
    }

    #[test]
    fn addresses_are_matched_without_case() {
        let list = vec![Person::new("user@example.com")];
        assert!(find(&list, "user@example.com").is_some());
        assert!(find(&list, "  user@example.com  ").is_some());
        assert!(find(&list, "someone@else.com").is_none());
    }

    #[test]
    fn the_old_list_of_addresses_still_reads() {
        let dir = std::env::temp_dir().join("people-compat-test");
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("people.json");
        std::fs::write(&p, br#"["a@b.com","c@d.com"]"#).unwrap();

        let list = read(&p);
        assert_eq!(list.len(), 2);
        assert_eq!(list[0].email, "a@b.com");
        assert!(!list[0].has_password(), "an upgraded account must not be signable-into");
        std::fs::remove_file(&p).ok();
    }

    #[test]
    fn a_fresh_account_is_unrestricted_except_where_it_must_be_asked_for() {
        let p = Person::new("a@b.com");
        assert!(p.apps_unrestricted);

        // Still the original promise: adding somebody does not lock them out
        // of anything they could previously reach. That is what keeps this
        // toggle from breaking accounts that predate it.
        for app in ALL_APPS {
            if EXPLICIT_ONLY.contains(app) {
                continue;
            }
            assert!(p.can_use(app), "a fresh account must allow {app}");
        }
        assert!(p.can_use("anything-not-even-a-real-app"));

        // The deliberate exception, and the reason this test changed: a new
        // account must not arrive holding the bedroom camera. Somebody has to
        // hand it over.
        for app in EXPLICIT_ONLY {
            assert!(
                !p.can_use(app),
                "a fresh account must NOT arrive with {app}; it has to be granted"
            );
        }
    }

    #[test]
    fn a_narrowed_apps_list_refuses_everything_else() {
        let mut p = Person::new("a@b.com");
        p.apps_unrestricted = false;
        p.apps = vec!["mama".to_string()];
        assert!(p.can_use("mama"));
        assert!(!p.can_use("kitchen"));
        assert!(!p.can_use("casey"));
        assert!(!p.can_use("kb"));
    }

    #[test]
    fn revoking_every_app_actually_refuses_every_app() {
        // THE BUG THIS FIELD EXISTS TO PREVENT: an empty apps list must not
        // silently mean "unrestricted" once somebody has deliberately
        // unticked every box. apps_unrestricted is what makes that an
        // unambiguous, separate state from "never configured".
        let mut p = Person::new("a@b.com");
        p.apps_unrestricted = false;
        p.apps = Vec::new();
        for app in ALL_APPS {
            assert!(!p.can_use(app), "{app} must be refused once access is revoked");
        }
    }

    #[test]
    fn an_upgraded_old_style_account_stays_unrestricted() {
        let dir = std::env::temp_dir().join("people-apps-upgrade-test");
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("people.json");
        // The pre-`apps` shape: a real record with no "apps" key at all.
        std::fs::write(
            &p,
            br#"[{"email":"a@b.com","hash":"","must_change":false,"added":1}]"#,
        )
        .unwrap();
        let list = read(&p);
        assert_eq!(list.len(), 1);
        assert!(list[0].can_use("mama"), "a record from before apps existed must not be locked out");
        std::fs::remove_file(&p).ok();
    }

    #[test]
    fn a_round_trip_keeps_everything() {
        let dir = std::env::temp_dir().join("people-round-trip-test");
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("people.json");

        let mut a = Person::new("a@b.com");
        a.hash = hash_password("a good password").unwrap();
        a.must_change = true;
        a.apps_unrestricted = false;
        a.apps = vec!["mama".to_string(), "kitchen".to_string()];
        write(&p, &[a.clone()]).unwrap();

        let back = read(&p);
        assert_eq!(back.len(), 1);
        assert!(back[0].must_change);
        assert!(!back[0].apps_unrestricted);
        assert_eq!(back[0].apps, vec!["mama", "kitchen"]);
        assert!(verify(&back[0].hash, "a good password"));
        std::fs::remove_file(&p).ok();
    }
}
