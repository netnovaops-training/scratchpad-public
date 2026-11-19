import sys

sys.path.insert(0, ".")
from cins import apply

S = [
(7, """//!
//! URL: /streamlit/?tab=11&sub=5 (tab 11 is "JSON Pipeline", inner tab 5).
//!
//! HOW TO READ THIS FILE. `render` draws the page and picks one of three
//! inner tabs: `add_form` (1), `export_tab` (2), `import_tab` (3). `export`
//! builds the download. `action` handles the POST buttons. The helpers it
//! uses (`diff`, `commit`, `review_body`, `post_form`...) live in pipeline.rs.
//!
//! Rust ideas used often in this file (explained once here):
//!   - `async fn` waits for the database; `.await` pauses until it answers.
//!   - `&str` is a borrowed look at text; `String` is text the code owns.
//!   - `Option<T>` is "a value or nothing" (`Some(x)` / `None`).
//!   - `Result<T, E>` is "worked" (`Ok(x)`) or "failed" (`Err(e)`).
//!   - `let Some(x) = .. else { .. }` continues only when the value is there.
//!   - `?` after a call means: if it is empty or failed, return that now.
//!   - `Map<String, Value>` is one JSON object (a row: field -> value).
//!   - `h.push_str(..)` adds text to the end of the page text `h`."""),
(20, "/// Names of the three inner tabs, in order (`&wsub=1` is the first)."),
(22, ""),
(23, """/// Fields a wishlist plant should have. A plant missing any of them is listed
/// for export. (`[&str; 20]` is a fixed-size list of exactly 20 texts.)"""),
(31, """/// Reads every wishlist plant and keeps those with at least one empty field from
/// `REQUIRED`. Each kept row gets `_missing_fields` (the names that are empty).
/// Takes the app state; gives back a list of rows."""),
(32, "// All wishlist rows, sorted by name."),
(33, "// `filter_map` keeps a row only when the closure gives `Some(row)`."),
(34, "// A field is missing when the column exists and its value is empty."),
(35, "// Nothing missing: drop this row."),
(43, """/// Draws the Wishlist tab. `req` holds the address settings (`wsub` = inner
/// tab); `st` is the saved pipeline progress. Gives back HTML text."""),
(45, "// The note that wishlist work never touches the main table."),
(46, "// Inner tab number forced into 1..3."),
(47, "// The row of inner-tab links; the current one gets class \"on\"."),
(52, "// Page so far + the chosen inner tab."),
(59, "/// Inner tab 1: a form with the basics for a new wishlist plant. Gives back HTML."),
(60, "// Drop-down choices as owned text, since the form helpers take lists of `String`."),
(62, "// Two columns of inputs, then the submit button."),
(69, "// The form posts `act=jp_wl_add`, handled by `action` below."),
(72, """/// Inner tab 2: pick up to 20 wishlist plants that still have blanks and download
/// them as one JSON file. Takes the app state, the request and the saved
/// progress. Gives back HTML."""),
(74, "// \"Reload\" button; the list is only read after \"Load\" has been pressed."),
(75, "// Not loaded yet: show only the Load and Reload buttons."),
(79, "// Plants that have missing fields."),
(80, "// None: everything is filled in."),
(84, "// Filter text (`wf`), lower case."),
(85, "// Keep plants whose name, company or botanical name contains it."),
(86, "// Picker choices: (id, label). The label shows id, name, company, and missing count."),
(91, "// The ids already picked (in the address), at most 20."),
(92, "// A GET form that reloads the page on any change, keeping the tab and inner tab."),
(95, "// The picked plants, looked up in the shown list."),
(96, "// None picked yet."),
(99, "// File name and ids for the download link, answered by `export` below."),
(106, """/// Builds the wishlist export. `ids` is a comma list of ids and `fname` the
/// wanted file name. Gives (file text, file name). Called from `export` in
/// pipeline.rs when the address says `export=jp_wl`."""),
(107, "// Only digit-only ids are kept."),
(109, "// Up to 20 chosen rows, without the large QR/barcode columns."),
(115, "// Use the wanted file name only if it has safe characters; else make one."),
(119, """/// Inner tab 3: upload a filled-in file, show what would change in each wishlist
/// row, and offer commit buttons. Takes the app state and the request. Gives HTML."""),
(120, "// `wup` is the uploaded file's token, kept in the address."),
(123, "// No file yet: show a hint."),
(127, "// All wishlist rows by id, for comparing."),
(128, "// Records that would change something: (position, diff, name, id)."),
(129, "// `?` here skips records whose id is not in the wishlist."),
(137, "// Totals: new fills (tn) and overwrites (to) across all records."),
(138, "// `waow=1` means the box \"Also accept ALL overwrites\" is ticked."),
(141, "// \"Approve ALL\" button (future_plants only)."),
(142, "// The tick box reloads the page with `waow` set."),
(146, "// One expander per record, with its review inside."),
(148, """// The review HTML comes from pipeline.rs; the text below is replaced so the
// forms use the wishlist's field names (`wup`, `wsub`) and wording."""),
(159, """/// Handles every wishlist button (POST). `act` names the button; `f` is the
/// submitted form. Gives back the messages to show. Called from `action` in
/// pipeline.rs for any button name that starts with `jp_wl_`."""),
(161, "// Remember whether the export list has been loaded."),
(163, "// Add a plant to the wishlist."),
(164, "// Only the name is required."),
(168, "// `out` collects the messages to show."),
(169, "// Warn (but do not block) if the garden already has this name."),
(172, "// `blank`: trimmed text, or None when empty (so the database stores NULL)."),
(173, "// Cost as a number; text that is not a number becomes NULL."),
(174, "// `?` marks are filled by `.bind(..)`, so the typed text can never change the SQL."),
(178, "// Show success with the new id, a friendly duplicate message, or the raw error."),
(185, "// Commit every uploaded record's new fills (and all overwrites if ticked)."),
(188, "// `ok` = plants saved, `n` = fields saved, `errs` = error messages."),
(190, "// Skip records not in the wishlist."),
(192, "// The tick box adds every overwrite too."),
(196, "// `true` = write empty strings as NULL (the wishlist rule)."),
(205, "// Commit or skip one record; only ticked overwrites (`acc[]`) are applied."),
(210, "// The position (`idx`) of the record in the uploaded file."),
(212, "// Re-read the row so the comparison is with the current data."),
(214, "// Names of the overwrites the user ticked."),
]
S = [(a,t) for a,t in S if t]
apply("pipeline_wl.rs", S)
