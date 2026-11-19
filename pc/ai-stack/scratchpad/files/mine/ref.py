import sys

sys.path.insert(0, ".")
from cins import apply

S = [
(5, """//!
//! URL: /streamlit/?tab=11&sub=6 (tab 11 is "JSON Pipeline", inner tab 6).
//!
//! HOW TO READ THIS FILE. Start with `FIELD_DEFS` below: one line per plant
//! field. `render` turns that list into a filterable table plus lists of
//! the allowed values. `schema_json` turns the same list into a JSON Schema
//! file that VS Code can use to check a plant JSON file. Both functions
//! only read the list; nothing here touches the database.
//!
//! Rust idea: `use` brings a name into scope so it can be written short.
//! `super` means the parent folder module (`orchestrator`)."""),
(13, """/// One row of the field reference. It is a tuple (a fixed group of values
/// that is not named): (field name, type description, allowed values if the
/// field has a drop-down list, example text).
/// `&'static str` is text that lives for the whole run of the program, such
/// as a literal typed in the code. `Option<..>` means "a value or nothing";
/// `None` means the field has no fixed list of allowed values."""),
(15, """/// Allowed-value text for companion_plants. It is a hint, not a real list.
/// `&[&str]` is a borrowed list (a slice) of text pieces."""),
(16, "/// Hint text for grow_months (shown in place of a list of allowed values)."),
(17, "/// Hint text for bloom_months (same idea as `GROW`)."),
(19, """/// Every field a plant JSON file may hold, in the same order as the original
/// Streamlit FIELD_DEFS. The option lists (PLANT_TYPES and so on) come from
/// `opts.rs`, so the drop-down lists stay in one place.
/// A type text that contains "read-only" or "metadata" is shown but must
/// not be changed by hand. `schema_json` and `render` both check for those
/// words."""),
(90, """/// Walks through `FIELD_DEFS` one entry at a time.
/// Returns an iterator (something you can loop over) of references to rows.
/// `impl Iterator<..>` means "some type that can be looped over"; the
/// exact type is not important to the caller."""),
(94, """/// Makes the short "Allowed Values" cell text for the table.
/// Takes the optional list of allowed values. Gives back "—" when there is
/// no list; otherwise the first 6 values joined by commas, plus a count of
/// how many more there are. The full lists are shown lower on the page.
/// `let Some(a) = a else { .. }` means: if the value is there, name it `a`;
/// if not, run the `else` block, which must leave the function."""),
(95, "// No list for this field: show a dash."),
(96, "// Copy the values, dropping empty strings (some lists start with a blank choice)."),
(97, "// 6 is the most values shown in one table cell; more would make the row too tall."),
(104, """/// Draws the "Field Reference" page body as HTML text.
/// Takes the request (`req`, which holds the address's settings such as the
/// filter box text). Gives back the HTML as a `String` (text that this
/// function owns and can grow)."""),
(105, "// Page title and the intro paragraph."),
(107, """// The text typed in the filter box (`rf`), made lower case so that
// matching ignores capital letters."""),
(108, """// The filter box is a GET form: pressing Enter reloads this same tab and
// sub-tab with `rf=` in the address. The two hidden inputs keep the tab."""),
(110, """// Keep a row when the filter is empty, or when the filter text appears in
// the field name, the type text, or any allowed value. Each kept row
// becomes a list of table cells. `.map(..)` changes each item;
// `.filter(..)` keeps only the items for which the test is true;
// `.collect()` gathers the results into a list."""),
(112, "// Draw the table."),
(115, """// Fields that get a full list under the table: more than one allowed value,
// and not a read-only or metadata field."""),
(116, "// Two columns of expanders; `i % 2` sends items alternately to left and right."),
(118, """// One bullet per value. `he` turns characters such as < and & into safe
// HTML so that option text cannot break the page."""),
(121, "// Lay the two columns out side by side with equal width ([1, 1])."),
(123, "// Download section for the VS Code schema file."),
(125, "// This link is answered by `page` in mod.rs (`?export=jp_schema`)."),
(129, """/// Builds the JSON Schema text (draft-07) that VS Code uses to check a plant
/// JSON file. It is made from `FIELD_DEFS`, the way the Streamlit app did it.
/// Takes nothing; gives back the JSON as a `String`.
/// Every field is allowed to be null, so the checker does not complain
/// about empty values."""),
(131, "// `props` will hold one entry per field. `Map` is a list of name -> value pairs."),
(132, "// `for (..) in ..` runs the block once for every field definition."),
(133, "// `p` is the schema entry for this one field."),
(134, """// Pick the JSON type from the words in the type description.
// `json!([..])` builds a JSON value from a literal."""),
(138, "// Fields shown as \"0 or 1\" are whole numbers that may only be 0, 1 or null."),
(144, """// `if let Some(a) = a` runs only when the field has a list of allowed values."""),
(145, """// Only plain drop-down fields get a strict list. Read-only, metadata,
// comma-separated and month-string fields are free-form, so no list."""),
(147, "// null and the empty string are also accepted, because fields may be blank."),
(152, "// Show the type text as the hover description, and the example as a hint."),
(156, "// Wrap the properties in a schema: an array of plant objects."),
(163, """// `dumps` (in pipeline.rs) writes JSON text in the same style as Python's
// json.dumps, so the file matches what the Streamlit app produced."""),
(166, """// Tests run with `cargo test`; this module is left out of the real program."""),
(168, "    /// Checks that the drop-down lists are the same count and order as in Streamlit."),
(170, "// Collect the names of fields that would get a full option list."),
]
S = [(a, t.lstrip() if False else t) for a,t in S]
apply("pipeline_ref.rs", S)
