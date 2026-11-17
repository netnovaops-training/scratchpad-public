import sys; sys.path.insert(0,".")
from cins import apply
S = [
(17, """//! URL: /streamlit/?tab=1. `render` draws the page for the current step.
//! `action` handles the POST buttons (create a stub, ask the AI, create the
//! record, promote a wishlist plant). `edit_phase` shows the new record
//! with the full editor. `schema_json` and `missing_json` build downloads.
//!
//! Rust ideas used often in this file (explained once here):
//!   - `async fn` waits for the database; `.await` pauses until it answers.
//!   - `&str` is a borrowed look at text; `String` is text the code owns.
//!   - `Option<T>` is "a value or nothing" (`Some(x)` / `None`).
//!   - `Result<T, E>` is "worked" (`Ok(x)`) or "failed" (`Err(e)`).
//!   - `?` after a call means: if it failed or is empty, return that from
//!     this function now; otherwise carry on with the good value.
//!   - `let Some(x) = .. else { .. }` continues only when the value is there.
//!   - `|x| ..` is a small unnamed function (a closure).
//!   - `Map<String, Value>` is one JSON object (field -> value).
//!   - `h.push_str(..)` adds text to the end of the page text `h`.
//! The page is one long function that returns early (`return h + ..`) when
//! a step is not finished yet, so later steps only appear after earlier ones."""),
(29, """/// The text of the field reference guide (plant_schema_reference.txt), built into
/// the program at compile time by `include_str!`. Served as a download."""),
(31, """/// The two ways to add a plant, shown as radio buttons in Step 3.
/// `[&str; 2]` is a fixed-size list holding exactly two pieces of text."""),
(36, "/// Reads field `k` of a row as plain text (\"\" when missing or null)."),
(41, """/// Takes the request and a list of setting names. Gives back hidden form inputs
/// for each setting that has a value, so a form keeps them when it is sent."""),
(47, """/// Builds the address of this tab with the named settings kept, plus `extra`
/// (for example "&promote=5"). Values are URL-encoded so they are safe in an address."""),
(57, """/// Where an AI answer is kept while the user reviews it: add-<token>.json.
/// Gives `None` unless the token is only letters and digits, so a token such
/// as "../x" cannot point at some other file."""),
(64, """/// Reads the saved AI answer for `token`. Gives `None` if missing or not a JSON object."""),
(72, """/// A new random token: 8 random bytes written as 16 hex characters."""),
(77, """/// The plant-tag guide as markdown text: a table of tag words and their meaning.
/// Used when the photo plant_tag_guide.jpg is not on the server."""),
(81, "// The first line is the header row; the second is the |---| divider, which is skipped."),
(87, """/// Draws the Add Plant tab. `req` holds the address settings; each step stores
/// its answer there (name, bot, checked, ok, path, ai, new). Gives back HTML.
/// Steps: 1 names, 2 duplicate check, 3 choose a path, 4 create the record."""),
(89, "// Show the photo of the tag guide if it was saved on the server; else a text table."),
(96, "// `new` in the address means a record was just created: show its editor instead."),
(100, "// The two names typed in Step 1, trimmed."),
(102, "// Submits by itself once both boxes have text (onchange)."),
(108, "// Both names are required before Step 2 appears."),
(112, "// Step 2: search the garden and the wishlist for a similar plant."),
(115, "// Wait for the user to press the search button (it sets `checked=1`)."),
(118, "// `like`: wrap text in % signs; in SQL LIKE, % means \"anything\"."),
(119, """// Garden matches: the botanical name OR the common name contains the typed text.
// `?` marks are filled by `.bind(..)`; the typed text is never put into the SQL itself."""),
(121, "// Wishlist matches use the same search on the future_plants table."),
(123, "// Shows a dash for a missing value."),
(124, "// Garden matches: warn so a duplicate is not added by accident."),
(130, "// Wishlist matches: a tick box can promote one into the garden instead."),
(134, "// `ready` is true after the user ticked the promote box for this row (`promote=<id>`)."),
(135, "// The tick box is a link that toggles `promote` in the address."),
(140, "// Show the confirm button and stop here, so nothing below can run at the same time."),
(147, "// No matches: carry on. Matches in the garden: ask the user to confirm first."),
(158, "// `path=ai` chooses AI Enrich; anything else is Quick Create."),
(160, """// The radio buttons post back as `path=quick` or `path=ai`: the long label
// text is swapped for those short values."""),
(162, "// Hidden fields that keep all earlier answers in the next form."),
(164, "// Quick Create: ask for year, beds and quantity, then make a stub record."),
(166, "// Year choices: a blank first, then the known years."),
(180, "// AI Enrich path."),
(181, "// No saved AI answer yet (`ai` token missing): show the Fetch and Skip buttons."),
(182, "// Text shown while the page waits (data-busy is used by st.js)."),
(183, "// `post`: builds one small POST form for a button."),
(192, "// There is an answer: show what it contains. `data` = the values, `fields` = which were filled."),
(194, "// If the AI failed, the reason is shown (S-12 in DEVIATIONS.md)."),
(204, "// Step 4: create the record from the answer."),
(212, """/// Shows the page for a freshly created plant (`new=<id>` in the address):
/// a success message, an Add Another button, optional downloads, and the full
/// editor when the "Open full editor" switch is on. Gives back HTML."""),
(213, "// The new plant's id."),
(215, "// Load the record; if it is gone, say so and offer to start over."),
(219, "// `full=1` means the user switched on the full editor."),
(231, "// A wide button made from a link."),
(232, "// `qc=1` marks a Quick Create record: offer the schema and guide downloads."),
(239, "// Offer a download of the fields that are still empty."),
(248, "// Which fields the AI filled, so the editor can mark them with a star."),
(252, "// Hidden fields that bring Save back to this same view."),
(258, "// Context for the editor. `&ai_fields` borrows the list; `images_dir` is where photos live."),
(259, "// Draw the editor, then the quantity logger under it."),
(267, """/// Builds the JSON file for filling in a Quick Create plant. It lists every
/// field with a blank value (the name, years, beds and quantity are filled in).
/// Takes the plant id; gives (file text, file name) or `None` if not found."""),
(269, "// Every field that goes in the file, in order. `const` inside a function is local to it."),
(288, "// Keys starting with `_` are notes for the person; the import skips them."),
(293, "// `_missing_fields` lists the blanks that still need filling."),
(294, "// Fill each field: some from the row, `qty` as a number, everything else blank text."),
(307, "/// Takes the plant id; gives (file text, file name) or `None`. Includes every column of the row."),
(309, "// The helper fields go first, then every column of the row."),
(318, "/// The real column names of data_source, so an AI answer is only saved to columns that exist."),
(323, """/// Makes a new row with just a name (and the "needs review" flag on).
/// Gives back the new id, or the database error."""),
(330, """/// Takes the button name `act` and the submitted form `f`. Gives back
/// (messages to show, settings for the next address). Called by `action` in mod.rs."""),
(331, "// The names typed in Step 1 are carried in every form."),
(333, "// `step` = settings to carry to the next page: names plus any earlier answers."),
(340, "// Promote a wishlist plant into the garden (the work is in wishlist.rs)."),
(347, "// Quick Create: make the stub row, then fill the basics."),
(351, "// `match` on the result: `Ok(i)` gives the id; an error returns a message at once."),
(354, "// Fill in botanical name, year, beds and quantity. Empty text is stored as NULL."),
(358, "// Go to the new record's page with the download buttons shown (`qc=1`)."),
(360, "// \"add_ai\" asks the free AI; \"add_skip\" saves an empty answer so the user fills it in by hand."),
(361, "// `job` is the saved answer: `data` (values), `fields` (names filled), maybe `error`."),
(364, "// Start with only the two names, and ask the AI about the rest."),
(368, "// Weather and local context for the AI, and Perenual plant data."),
(372, "// Keep only answers that have a real value."),
(380, "// The AI could not answer; remember why so the page can show it."),
(383, "// The two typed names always win."),
(387, "// Save the answer to a file and carry its token to the next page."),
(395, "// Create the record, then write every AI value whose column exists."),
(401, "// Helper fields and the id are never written."),
(402, "// Keep real columns only. Month codes must be exactly 12 characters."),
(405, "// Build: UPDATE data_source SET `a` = ?, `b` = ? WHERE id = ?"),
(407, "// Bind each value in order, using the type that matches the JSON value."),
(416, "// Last bind is the id; if saving fails, the stub still exists, so say so."),
]
apply("add.rs", S)
