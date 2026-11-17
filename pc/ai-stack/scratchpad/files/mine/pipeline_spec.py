import sys; sys.path.insert(0,".")
from cins import apply
S = [
(17, """//!
//! Rust ideas used often in this file (explained once here):
//!   - `async fn` / `.await`: a function that may wait (for the database or
//!     the disk). `.await` pauses this function until the wait is over.
//!   - `String` is text the code owns; `&str` is a borrowed look at text.
//!   - `Option<T>` is "a value or nothing" (`Some(x)` / `None`).
//!   - `Result<T, E>` is "worked" (`Ok(x)`) or "failed" (`Err(e)`).
//!   - `?` after a call means: if it failed or is empty, return that from
//!     this function right now; otherwise carry on with the good value.
//!   - `match` picks one branch by the shape of a value.
//!   - `Map<String, Value>` is one JSON object (a plant row: field -> value).
//!     `Value` is any JSON value (text, number, true/false, list, object).
//!   - `h.push_str(..)` adds text to the end of the page text `h`.
//! HTML is built as plain text with `format!`. `{}` in the text is filled
//! with the next argument, and `{name}` with the variable `name`."""),
(35, """/// The names of the six inner tabs, in order (`&sub=1` is the first).
/// `[&str; 6]` is a fixed-size array holding exactly 6 text values."""),
(39, """/// Reads field `k` of a plant row as plain text ("" when missing or null).
/// Short name because it is used everywhere in these files."""),
(44, """/// Takes an optional JSON value; gives back true when it counts as empty.
/// Empty means: missing, null, an empty list, or placeholder text such as
/// "unknown" or a row of dots (an unset month code)."""),
(46, "// Nothing there at all, or an explicit null."),
(47, "// A list is empty when it has no items."),
(48, "// Anything else: compare its text (trimmed, lower case) with the placeholders."),
(53, """/// Takes a JSON value; gives back text shaped the way Python would print it
/// (None, True, False, ['a', 'b']) so the screens look like the Streamlit app."""),
(62, """/// Where the pipeline's saved progress lives: data/orchestrator-ai/pipeline.json.
/// `PathBuf` is an owned file path. `state.data` is the app's data folder."""),
(66, """/// Reads the saved pipeline progress. Gives back an empty map when the file
/// is missing or broken, so the pages still draw."""),
(67, """// read file -> bytes -> JSON. `.ok()` turns a failure into "nothing",
// and `.and_then(..)` runs the next step only if the earlier one worked."""),
(73, """/// Saves the pipeline progress. It writes to a temporary file and then
/// renames it, so a crash half way never leaves a broken pipeline.json.
/// Failures are ignored (`let _ =`): losing a progress note is not serious."""),
(75, "// Make sure the folder exists."),
(76, "// Same name with the extension changed to .tmp."),
(82, "/// Reads a true/false setting `k` from the saved progress. Missing counts as false."),
(86, """/// Loads the progress file, sets one value, and saves it again.
/// Used by the "Load" and "Reload" buttons to remember what was pressed."""),
(92, """/// Where an uploaded JSON file is kept: upload-<token>.json.
/// Gives back `None` unless the token is only letters and digits. This
/// matters for safety: it stops a token such as "../x" from reaching other
/// folders. `.then(|| ..)` builds the path only when the test is true."""),
(98, """/// Takes the token from the page's hidden field. Gives back `None` when the
/// token is bad, the file is gone, or the file is not a JSON list."""),
(99, "// Each `?` here gives up with `None` if that step fails."),
(100, "// Keep only the list items that are objects; copy them (`cloned`)."),
(104, """/// Takes the file picked in the upload form. Gives back the new token, or an
/// error message to show the user."""),
(105, "// The form field is named `json`. `let .. else` leaves early when no file came."),
(106, "// Parse the bytes as JSON; `map_err` changes the error into a friendly message."),
(107, "// The Streamlit rule: the file must be a list of plant objects."),
(110, "// 8 random bytes written as 16 hex characters make a hard-to-guess token."),
(112, "// Build the path (this also re-checks the token is safe)."),
(113, "// Create the folder, then write the file; any error becomes a text message."),
(118, """/// Fields an import never writes: ids, helper fields added by the pipeline,
/// and fields owned by other tools (pictures, quantity, approvals).
/// Any field starting with `_` is also skipped (see `diff`)."""),
(120, """/// The result of comparing an uploaded record with the database row:
/// (new fills as field -> value, overwrites as (field, db value, json value))."""),
(123, """/// This is the LOCK RULE: a value already in the database is never replaced
/// unless the user approves it."""),
(124, "// `new` = fields empty in the DB but filled in the file; `ow` = changed values."),
(125, "// Look at every field of the uploaded record."),
(126, "// Skip fields the import must not touch."),
(129, "// The database value for this field (null when the column is missing)."),
(130, "// DB empty and file has a value: a safe new fill."),
(132, "// Both have values: it is an overwrite only if they differ."),
(133, """// If both look like numbers, compare them as numbers so "8.990" equals 8.99.
// Otherwise compare the trimmed text."""),
(142, "// Give back both lists."),
(146, """/// A list such as [5, 6] means May and June; the code is "....MJ......".
/// Gives `None` when the value is not a month list."""),
(147, "// Get a list of month numbers from either a real list or text that looks like one."),
(152, "// Month numbers may be numbers or text; `opts::format_m` makes the 12 letters."),
(156, """/// Draws a field's value for the review screens. Month fields become a row of
/// coloured letters (green for grow, pink for bloom); all other fields are
/// shown as escaped text cut to 200 characters."""),
(157, """// A small helper (closure) that makes safe, shortened text.
// `he` turns characters such as < and & into HTML-safe forms."""),
(161, "// Turn the value into a 12-letter code; else accept a ready 12-character string."),
(165, "// Green for growing months, pink for blooming months."),
(166, "// First letter of each month."),
(167, "// One small box per month; a dot in the code means \"not active\" (grey dot)."),
(176, "// One table row per new field, with a green background."),
(182, """/// Returns how many columns were written, or an error message.
/// `table` is data_source or future_plants. The SQL is built from column
/// names that were checked against the real table, and the values are sent
/// separately with `.bind` so they can never be run as SQL."""),
(183, """// Ask the database which columns this table really has (S-13 in DEVIATIONS.md).
// DATABASE() means the database this connection uses."""),
(185, "// Keep only real columns, never id or date_added; fix up each value."),
(186, "// Month fields are stored as the 12-letter code."),
(187, """// Lists become "a, b, c" text, objects become JSON text, and for the
// wishlist an empty string becomes NULL (the `blank_null` rule)."""),
(195, "// Nothing valid to write."),
(198, "// Build: UPDATE `table` SET `a` = ?, `b` = ? WHERE id = ?  (the ? are filled in below)."),
(200, "// Bind each value in order, using the type that matches the JSON value."),
(209, "// Last bind is the id for WHERE; then run it."),
(210, "// The home-page widgets cache plant data, so tell them it is out of date."),
(219, "/// A date-time stamp like 20261007_142530, used in download file names."),
(225, "// Adds `_missing_fields` to the row: the list of fields that need filling."),
(232, """// `order` is extra SQL text such as " ORDER BY plant_name ASC" (or empty).
// It is only ever a fixed string written in this file, never user input."""),
(236, "/// How many fields a row says are missing (0 if the list is absent)."),
(241, """/// Draws a multi-pick box whose values are plant ids. `max` limits how many can
/// be chosen (0 means no limit). `st.js` adds the chip display and the limit."""),
(242, "// `data-ms` tells st.js to turn this <select> into the chip box."),
(249, """/// Wraps `body` in a POST form that sends `act` (which button), `tab=11` and `sub`.
/// `extra` is more hidden inputs. `enctype=multipart` allows file uploads."""),
(253, "/// A hidden form input, or nothing when the value is empty."),
(264, """/// Turns "3, 5,x" into ["3", "5"]: comma-separated, trimmed, digits only."""),
(270, """/// Draws the whole JSON Pipeline tab. `req` holds `sub` (which inner tab).
/// Gives back HTML text. It loads the saved progress once, then asks the
/// right inner-tab function to draw itself."""),
(271, "// Page heading and intro box."),
(273, "// Inner tab number, forced into the range 1..6 (default 1)."),
(274, "// The row of inner-tab links; the current one gets class \"on\"."),
(279, "// Saved progress (loaded flags, done list, enrich results)."),
(280, "// Page so far + the chosen inner tab. `match sub` picks one branch."),
(290, """/// Phase 1: export ONE incomplete plant at a time. Shows a progress bar,
/// a plant picker, the missing fields, a download link and a "Mark as Done"
/// tick box. `st` is the saved progress. Gives back HTML."""),
(293, "// \"Reload\" clears the done list; \"Load\" turns the tab on. Nothing is queried until Load."),
(298, "// Ids already marked done this session."),
(299, "// The queue: incomplete plants that are not done yet."),
(300, "// Percent done for the progress bar (whole-number maths)."),
(308, "// Choices for the drop-down: text shown, and the id as its value."),
(309, "// The chosen plant (`p` in the address), or the first in the queue."),
(313, "// Names of this plant's missing fields."),
(319, "// The download link is answered by `export` below (kind jp_p1)."),
(320, "// Ticking the box submits at once (onchange) and adds the id to the done list."),
(323, "// A preview table of all current values; internal helper fields are hidden."),
(330, """/// Phase 2 (export): pick up to 5 incomplete plants, see exactly which fields
/// are missing, and download them as one JSON file. Gives back HTML."""),
(343, "// Filter box text, lower case."),
(344, "// Plants whose name, botanical name or family contains the filter text."),
(346, "// The ids ticked in the picker, at most 5 (`take(5)`)."),
(350, "// Look the picked ids up in the shown list."),
(356, "// One expander per chosen plant, listing its missing fields."),
(365, "// File name for the download, with a time stamp."),
(367, "// The link is answered by `export` below (kind jp_p2e)."),
(373, """/// Phase 2 (enrich): choose plants, press Start, and the free AI fills blanks in
/// the background. The page shows a progress bar and reloads every 4 seconds
/// while `running` is true. When `done` it lists results and a download link.
/// Gives back HTML."""),
(376, "// `p2` is the saved Enrich job: loaded / running / done / results / errors."),
(379, "// `form` collects the controls; `chosen` is how many plants are selected."),
(381, "// Before a run: either offer the Load button or show the plant picker."),
(389, "// Every incomplete plant is pre-selected."),
(397, "// Web search tick box and the worker-count slider (1 to 16, default 8)."),
(401, "// The Start button; disabled (by editing its HTML) if running, done or nothing chosen."),
(405, "// Start and Reset buttons side by side."),
(407, "// While running: progress bar, spinner, and a script that reloads after 4000 ms."),
(413, "// When finished: summary, any errors, a results table and the download link."),
(437, """/// Takes shared app state (`Arc` is a counted shared pointer, so the
/// background task can keep using it), the plants, whether to use web
/// search, and the worker count. It runs in the background, started by
/// `tokio::spawn` in `action`."""),
(438, "// Brings the `.next()` helper for streams into scope for this function."),
(439, "// A second shared pointer that is moved into the closure below."),
(440, """// A stream of one async job per plant, run `workers` at a time.
// `buffer_unordered` yields each result as soon as it is ready."""),
(443, "// Per plant: optional web context, Perenual plant data, then ask the AI."),
(449, "// At least 1 worker, even if 0 was asked for."),
(450, "// Handle each finished plant as it arrives."),
(451, "// Reload the saved job, add this result, and save again."),
(454, "// AI worked: fill only fields that are empty (the lock rule)."),
(467, "// AI failed: record the error so the page lists it (S-14 in DEVIATIONS.md)."),
(469, "// Count how many plants have finished."),
(474, "// All plants handled: mark the job as finished."),
(482, "/// Adds `v` to the list stored under key `k` in map `m`, creating the list if needed."),
(489, """/// Phase 3: upload a JSON file, compare each record with the database, and
/// let the user commit new fills and chosen overwrites. Nothing is written
/// until a commit button is pressed. Gives back HTML."""),
(493, "// `up` is the upload's token, kept in the address between page loads."),
(494, "// No file yet: show a hint and stop."),
(499, "// All current plants by id, for comparing."),
(500, "// Records that would change something: (diff, record)."),
(501, "// \"Approve ALL\" button, only when there is something to commit."),
(502, "// Totals of new fills (tn) and overwrites (to) across all plants."),
(511, "// One expander per uploaded record, with its review inside."),
(525, """/// Takes the diff, the record's position (`idx`) in the upload, the upload
/// token, the plant name, the inner-tab number and the action name (which
/// differs for the wishlist). Gives back HTML. The wishlist reuses this."""),
(526, "// Nothing to show for this record."),
(530, "// Green table of new fills."),
(535, "// Amber table of overwrites, then one tick box per overwrite."),
(552, "// Button label with a plural \"s\" when needed."),
(555, "// The form with the tick boxes, Commit and Skip buttons."),
(560, "/// Uses the file name `f` only if it has safe characters; otherwise `fallback`."),
(564, """/// Builds a download. `kind` says which one (jp_p1, jp_p2e, jp_p2, jp_schema, jp_wl);
/// `q` holds the address settings. Gives back (file text, file name) or `None`
/// when the plant does not exist. Called from `page` in mod.rs."""),
(565, "// Small helper: get an address setting as text (\"\" if missing)."),
(567, "// Phase 1: one plant, named after the plant."),
(573, "// Phase 2 export: up to 5 chosen plants as a JSON list."),
(579, "// Phase 2 enrich: the saved results."),
(584, "// The VS Code schema from pipeline_ref.rs."),
(585, "// Wishlist export from pipeline_wl.rs."),
(592, """/// Handles every button on this tab (POST). `act` names the button; `f` is the
/// submitted form. Gives back (messages to show, extra address settings so the
/// redirect lands on the same inner tab and upload). Called from `action` in mod.rs."""),
(593, "// Which inner tab to return to."),
(594, "// Settings carried over to the next page load."),
(600, "// `msgs` is the list of result messages; each branch gives back a list."),
(601, "// Remember Phase 1 was loaded."),
(603, "// Mark a plant done in Phase 1: add its id to the done list."),
(613, "// Enrich: Load button."),
(614, "// Enrich: Reset button (refused while a job is running)."),
(622, "// Enrich: Start. Save the job state first, then start the background task."),
(630, "// `tokio::spawn` starts the job in the background; this request does not wait for it."),
(634, "// A file was uploaded: store it and carry its token to the next page."),
(638, "// Approve ALL: commit every record's new fills (and all overwrites if ticked)."),
(658, "// Commit or skip one record; only ticked overwrites (`acc[]`) are applied."),
(683, "// Wishlist buttons are handled in pipeline_wl.rs."),
(689, "// Tests: run with `cargo test`; they are left out of the real program."),
(693, "/// The lock rule: filled DB values are kept unless they differ and are approved."),
(703, "/// Month lists become the 12-letter code; Python-style text matches Streamlit."),
]
apply("pipeline.rs", S)
