import sys

sys.path.insert(0, ".")
from cins import apply

S = [
(14, """//! Used by: tabs 1 to 10 (via `tabs.rs`). The form posts to /streamlit/ with
//! `act=save_plant`, which `action` in mod.rs sends to `save` below. The
//! quantity logger (`qty_logger` / `save_qty_event`) sits under the editor.
//!
//! Rust ideas used often in this file (explained once here):
//!   - `fn name(..) -> String`: a function that gives back text it owns.
//!   - `&str` is a borrowed look at text; `String` is text the code owns.
//!   - `Map<String, Value>` is one JSON object: a plant row as field -> value.
//!   - `Option<T>` is "a value or nothing" (`Some(x)` / `None`).
//!   - `async fn` waits for the database; `.await` pauses until it answers.
//!   - `<'a>` after a name is a "lifetime": it says the borrowed data must
//!     stay alive as long as the struct that holds it.
//!   - `h.push_str(..)` adds text to the end of the page text `h`.
//!   - `vec![..]` makes a growable list; `.iter().map(..).collect()` builds
//!     a new list by changing each item."""),
(21, ""),
(25, "/// True when the editor is showing an AI result, so labels get AI badges."),
(26, "/// Names of fields the AI filled that were empty before (labelled \"New\")."),
(27, "/// Fields the AI changed: field name -> the old value (labelled \"Was\")."),
(34, "/// Reads field `k` of a plant row as plain text (\"\" when missing or null)."),
(38, ""),
(39, """/// Takes the form context, the data shown, the field name `f` and the plain
/// label `n`. Gives back the label text to display. Some fields never get a badge."""),
(40, "// Not an AI view, or a field the AI may not touch: plain label."),
(43, "// The AI filled this field and it was empty before."),
(46, "// The AI changed this field: show the old value, cut to 20 characters."),
(49, "// The AI left an existing value alone: show a short copy of it."),
(56, "/// The first `n` characters of `v` (by character, not byte, so emoji are safe)."),
(62, """/// Takes the drop-down's fixed `list` and the saved value `val`. Gives back the
/// options with the saved value first, so the select shows it as chosen."""),
(63, "// `trim` removes spaces at both ends."),
(64, "// No saved value: use the list as it is."),
(67, "// Find the saved value in the list, ignoring capital letters."),
(68, "// Not in the list: put the odd value first and keep every list entry after it."),
(69, "// In the list: put the list's own spelling first, then the others."),
(75, """/// Draws one drop-down for field `f`. `default` is used only when the plant
/// row has no such column at all. Gives back HTML."""),
(76, "// Current value; fall back to `default` only if the column is missing."),
(80, "// Make sure the current value is in the list and first."),
(85, "/// Draws a one-line text box for field `f`, filled with the plant's value."),
(89, "/// Draws a multi-line text box for field `f` (height 100)."),
(94, "/// Splits `raw` at the character `sep` into trimmed items, dropping empty items, none and null."),
(95, "// Remove list punctuation first: [ ] ' \" so \"['a','b']\" becomes \"a,b\"."),
(102, """/// Joins a fixed list and a list from the plant, sorted, with no repeats.
/// Used so a saved value that is not in the standard list still appears."""),
(104, "// `sort` orders the items; `dedup` removes neighbours that are equal."),
(109, "/// Splits a comma list of picture file names, dropping blanks and \"none\"/\"null\"/\"[]\"."),
(116, """/// Draws the coloured month bar (one box per month) with its 12-letter code.
/// `bitmap` is the code (a dot means not active), `color` the box colour,
/// `emoji` and `label` the heading. Gives back HTML."""),
(117, "// One letter per month, in order."),
(120, "// Go through the 12 months; `bits.get(i)` is the code's character for that month."),
(122, "// Active month: a coloured box with the month letter."),
(124, "// Inactive month: a grey dot."),
(128, "// Put the pieces together; `he` makes the code safe to show as HTML."),
(131, ""),
(134, "// Start the form. `data-plant-form` lets st.js find it.\n// It posts as multipart so photo uploads work."),
(135, "// Hidden fields: which action this is, and which plant."),
(136, "// More hidden fields so the browser comes back to the same tab and plant after Save."),
(140, "// The editor has 14 sections. Each one draws its inputs in columns."),
(166, "// Cost as a number (0 when blank or not a number)."),
(167, "// The newer `qty` column wins over the older `quantity` when it has a value."),
(168, "// Whole number, never below 0; default 1."),
(169, "// The year list: `year_planted` if set, otherwise `year_added`."),
(170, "// Years may be separated by \"/\" or \",\"."),
(176, "// A stored 0 means \"no year\", so show an empty box."),
(182, "// Keep only bed names that really exist; none left means \"To Be Sorted\"."),
(246, "// The calendars: two months-bar previews and a switch between a simple range and ticking months."),
(247, "// The toggle is edited so st.js can find it (`data-cal-multi`)."),
(248, "// `parse_m` turns the stored 12-letter code into month numbers 1..12."),
(251, "// `mname`: first or last month in the list as a short name, \"Jan\" if none."),
(255, "// `boxes`: twelve tick boxes named prefix0..prefix11, used by Multi-Period Mode."),
(272, "// A plant with these bloom colors is treated as flowerless."),
(292, "// Live preview bars, using the colours of the website card."),
(302, "// The Flag box is ticked when needs_review is 1 or true."),
(312, "// Collect the picture file names: `picture` first, then `additional_pictures`."),
(323, "// Show the thumbnail only if the file exists on disk; otherwise a red \"Missing File\" box."),
(328, "// The wipe button sends its position (`i`) as `purge_img`, which `save` reads."),
(344, "// Small helper: is this yes/no field switched on?"),
(354, ""),
(356, """/// Text columns copied straight from the form into the database when sent."""),
(366, ""),
(369, ""),
(375, """/// Reads the month numbers from the form. In Multi-Period Mode it reads the 12
/// tick boxes (`boxes` + 0..11); otherwise it reads the start and end drop-downs
/// named in `range` and fills every month between them."""),
(376, "// `f.has(name)` is true when the form sent that field."),
(379, "// Position of the chosen month name in the month list, as 1..12 (default 1)."),
(384, """/// A message shown at the top of the page after an action (a "flash").
/// The four kinds match Streamlit's success, info, warning and error boxes.
/// An `enum` is a type that is exactly one of several named choices; each
/// choice here carries the message text."""),
(391, ""),
(392, """/// Takes the database connection (inside `state`) and the submitted form `f`.
/// Gives back the messages to show. Steps: read the current gallery, handle a
/// wipe button, collect every column value, handle photo uploads, run one
/// UPDATE, then read the calendars back to confirm them."""),
(393, "// php_int turns text into a whole number the way PHP does (0 when not a number)."),
(397, "// Load the plant's current name and pictures."),
(401, "// `let Ok(Some(row)) = row else { .. }`: continue only if the query worked and found a row."),
(402, "// Brings `try_get` (read a column from a row) into scope."),
(403, "// A column may be NULL, so read it as Option and use \"\" when missing."),
(406, "// The gallery is `picture` followed by `additional_pictures`."),
(412, "// `purge_img` is the position of the photo to remove."),
(416, "// Save the first photo as `picture` and the rest as `additional_pictures`."),
(424, "// `sets` is the list of (column, new value) pairs for one UPDATE."),
(426, "// Text columns: write only those the form sent."),
(431, "// Set below when \"Does not bloom\" is ticked."),
(432, "// Pick-lists are always written, as \"a, b, c\" (empty when nothing is chosen)."),
(435, "// The editor's bed list is also kept in the older `flower_bed` column."),
(437, "// Cost as a number; bad text becomes 0."),
(439, "// Quantity: whole number, at least 0, default 1."),
(442, "// Years are stored as \"2023, 2024\" and also as \"2023 / 2024\" in year_planted."),
(446, "// Year died: digits only, otherwise NULL."),
(448, "// Tick boxes: only sent by the browser when ticked, so absent means 0."),
(451, "// Calendar months from the form."),
(452, "// \"Does not bloom\" clears the bloom months and sets bloom_color to \"Not ornamental\"."),
(462, "// Months are saved as the 12-letter code."),
(465, ""),
(467, "// Photo uploads from the multi-file box."),
(470, "// Seconds since 1970; used to make file names unique."),
(475, "// WebP files are kept as they are; others are converted to WebP at quality 87."),
(476, "// Skip a file that could not be converted."),
(481, "// Replace all, or add after the existing photos."),
(495, "// Build: UPDATE data_source SET `a` = ?, `b` = ? ... WHERE id = ?"),
(499, "// Bind each value in order (sqlx sends values apart from the SQL text, so they cannot change it)."),
(503, "// The last bind is the id; run it. On failure return an error message."),
(506, "// Home-page widgets keep a cache of plant data; mark it out of date."),
(513, "// A calendar is good when it has 12 characters and is not all dots."),
(514, "// Show NULL when the stored value is empty."),
(532, ""),
(533, """/// Takes today's date. Gives back 172 (label, Monday date) pairs, newest first.
/// 172 weeks is how far back the Streamlit picker went."""),
(534, "// Brings `.weekday()` into scope."),
(535, "// The Monday of this week."),
(541, "// Week 0 and 1 get friendly names; older weeks show the date range."),
(551, ""),
(552, """/// Takes the plant id and the hidden fields for coming back. Gives back HTML:
/// a small closed-by-default form for logging a purchase, death or removal."""),
(559, "// (value, label) pairs for the Event type drop-down."),
(566, "// Week choices; the value is the position in the list, the label is the date range."),
(575, """/// Handles the quantity form: writes a row in plant_quantity_log, changes the
/// plant's `qty`, and marks it dead when the quantity reaches 0 after a death
/// or removal. Gives back the messages to show."""),
(578, "// Only the four known event kinds are accepted; anything else is \"other\"."),
(582, "// At least 1 plant."),
(584, "// Week position, kept inside the list."),
(586, "// Week of the year, counting from 1 (the %W format starts at 0)."),
(588, "// The plant's name, for the confirmation message."),
(590, "// Insert the log row. The `?` marks are filled in by the `.bind(..)` calls below."),
(597, "// If the table is missing, tell the user to run the migration; else show the error."),
(605, "// A purchase adds to the quantity; anything else subtracts."),
(606, "// GREATEST(0, ..) keeps the quantity from going below zero."),
(607, "// Read the new quantity back."),
(608, "// If nothing is left after a death or removal, mark the plant as died."),
(615, "// Tests: run with `cargo test`; they are left out of the real program."),
(619, "/// The week list has 172 entries and its labels match the Streamlit picker."),
(628, "/// Plant names become file-safe: only ASCII letters and digits stay."),
(633, "/// A saved value that is not in the list is kept, first."),
(640, "/// List text with Python-style punctuation is split into clean items."),
]
S = [(a,t) for a,t in S if t]
apply("form.rs", S)
