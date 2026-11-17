import sys; sys.path.insert(0,".")
from cins import apply
S = [
(18, """//! Tab numbers: 2 = Manual Review, 3 = Fill Missing Data, 10 = AI Missing Only.
//! The job file for tab N is data/orchestrator-ai/tab<N>.json. A job holds:
//!   queue    plants still waiting;   batch   finished answers;
//!   working  the plant being done now;   stage   a short progress note;
//!   done     (tab 10 only) true when no plant needs filling;
//!   last_id  (tab 10 only) the last plant looked at.
//!
//! Rust ideas used often in this file (explained once here):
//!   - `async fn` waits (for the database, the AI, the disk); `.await`
//!     pauses until the wait is over.
//!   - `tokio::spawn(..)` starts a task in the background; the request that
//!     started it does not wait for it.
//!   - `Arc<T>` is a shared pointer, so the page and the background task can
//!     both hold the app state. `Mutex<T>` lets only one thread at a time
//!     change a value; `.lock()` takes the turn.
//!   - `Option<T>` is "a value or nothing" (`Some(x)` / `None`).
//!   - `let Some(x) = .. else { .. }` continues only when the value is there.
//!   - `&str` is a borrowed look at text; `String` is text the code owns.
//!   - `Map<String, Value>` is one JSON object (field -> value).
//!   - `'a` is a "lifetime": it says a result borrows from an input."""),
(32, """/// The set of tab numbers whose background job is running right now.
/// A `static` lives for the whole run of the program. It is inside a Mutex so
/// two requests cannot change it at the same moment. It starts as `None`
/// (no set yet); `set_running` creates the set the first time it is needed."""),
(34, """/// True when the job for `tab` is running. If the lock cannot be taken, say false."""),
(38, """/// Marks the job for `tab` as running (`on` = true) or finished (false).
/// Gives back true if the set changed. Starting a second job on the same
/// tab gives false, which is how `action` avoids starting it twice."""),
(40, "// Create the set on first use."),
(44, """/// The job file for a tab: data/orchestrator-ai/tab<N>.json.
/// `PathBuf` is an owned file path. `state.data` is the app's data folder."""),
(48, """/// Reads a tab's job. Gives back an empty job when the file is missing or broken."""),
(49, """// read file -> bytes -> JSON. `.ok()` turns a failure into "nothing", and
// `.and_then(..)` runs the next step only if the earlier one worked."""),
(55, """/// Saves a tab's job. It writes a temporary file and then renames it, so a
/// reader never sees a half-written file."""),
(66, "/// Reads field `k` of a row as plain text (\"\" when missing or null)."),
(70, """/// Gets the list stored under key `k` as a list of objects (empty if absent).
/// The result borrows from `m`, which the `'a` marks show."""),
(74, "/// Gets the list stored under key `k` as a list of text values (empty if absent)."),
(78, """/// SQL for tab 10: a plant needs filling when any key field is NULL or blank
/// (TRIM removes spaces). The company field also counts "unknown" as blank.
/// It is joined into a SELECT in `action`; it holds no user input."""),
(93, ""),
(95, """/// Takes shared app state, the button (`act`) and the tab number. Gives back
/// messages to show. It picks the plants, saves them as a queue in the job
/// file, and starts `work` in the background."""),
(96, "// Only the three AI tabs are valid."),
(99, "// \"Check Again\": throw the old job away."),
(103, "// Do not start a second job on the same tab."),
(106, "// Which plants to pick depends on the tab."),
(107, "// Tab 2: up to 3 plants with no botanical name."),
(108, "// Tab 3: up to 3 plants missing size, spacing, soil pH, bloom colour or company."),
(116, """// Tab 10: ONE plant, the next one after `last_id`. If none is left after it,
// start again from the beginning."""),
(125, "// Start a fresh job."),
(126, "// Tab 10 with nothing to fill: remember that, so the page says it is finished."),
(131, "// Tab 10 remembers where it stopped, so \"Next Plant\" moves on (S-9 in DEVIATIONS.md)."),
(134, "// The queue is the picked plants; the batch (finished answers) starts empty."),
(137, "// `set_running` is true only if this tab was not already running."),
(138, "// Start the background worker. The page returns at once."),
(143, ""),
(144, """/// Takes shared app state and the tab. Loops until the queue is empty: take
/// the first plant, show it as "working", ask the AI, and save the answer in
/// the batch. Clears the running flag at the end."""),
(145, "// `loop` repeats until a `break`."),
(147, "// Take the first plant off the queue; if the queue is empty, stop the loop."),
(148, "// The queue as it is after removing that plant."),
(156, "// Ask the AI. The closure (small function) saves a progress note for the page."),
(161, "// Load again (the stage notes changed the file), then record the finished plant."),
(177, ""),
(182, ""),
(187, """/// Asks the AI about one plant and builds the finished answer for the batch.
/// Takes the app state, the tab, the plant row and `stage`, a function that is
/// called to report a progress note. Gives back an answer object holding the
/// row, the AI's values (`llm`), and extras for the tab: `new_fields` and
/// `changed_fields` (tab 3), `merged` and `ai_filled` (tab 10), or `error` and
/// `raw` when the AI failed. `impl Fn(&str)` means "any function that takes text"."""),
(188, "// Weather/location context and Perenual plant data are given to the AI."),
(192, "// Only tab 3 asks for the long version of the answer."),
(193, "// `Err` = the AI failed (S-14 in DEVIATIONS.md): keep the reason and raw text."),
(199, "// Tab 3: compare the AI's values with the row. Empty -> filled = \"new\"; different = \"changed\"."),
(213, "// Tab 10: the LOCK RULE. Only fields that are empty in the row are filled."),
(222, "// If companion plants are still empty, ask for them from the garden's own plant names."),
(238, "// Keep the original row beside the answer for the page."),
(242, ""),
(244, "/// Takes the plant snapshot from the job. Looks the plant up by id."),
(252, "/// A \"start\" button that posts `ai_start` for this tab. `label` is the button text."),
(256, "/// A spinner with a message."),
(260, ""),
(261, """/// Takes the job and the tab. Gives back HTML for the queue count, the status
/// message, and a script that reloads the page every 4000 ms while the queue
/// is not empty. Gives back nothing when the queue is empty."""),
(266, "// The plant being worked on, or the first in the queue."),
(268, "// Tab 10 shows one plant at a time, so no queue count there."),
(271, "// The status text depends on the tab (and on the companion stage for tab 10)."),
(281, """/// Draws the normal plant editor (form.rs) with the AI's values laid over the row.
/// `new` = fields the AI filled; `changed` = fields it would change (old values)."""),
(287, "/// A link button to download one AI result as HTML (`export` below)."),
(291, """/// Draws one of the three AI tabs. `req.tab` says which. Gives back HTML.
/// Tabs 2 and 3 show up to 3 results; tab 10 shows one."""),
(293, "// The job saved for this tab (may be empty)."),
(296, "// Tabs 2 and 3: a start button, one expander per finished plant, then the progress."),
(302, "// Number each result (`i`) so its export link can find it."),
(308, "// Expander title depends on the tab and on whether the AI failed."),
(313, "// Failed: show the error (tab 2 also shows the raw text)."),
(319, "// Worked: an export button and the editor with the AI's values."),
(326, "// Re-read the plant: photos or sign-offs may have changed since the AI looked."),
(334, "// Tab 10: strict mode, one plant at a time."),
(337, "// No plants left: say so and offer \"Check Again\"."),
(342, "// `busy` = a plant is still in the queue."),
(347, "// Nothing finished yet: stop after the button and progress."),
(356, "// `filled` = the fields the AI filled; `merged` = the row with those values added."),
(366, "// Show the freshest copy of the plant, with the AI's values and green marks."),
(373, ""),
(374, """/// Takes the tab and the position `i` of the result. Gives (HTML text, file
/// name), or `None` if there is no such result. Called from `page` in mod.rs."""),
(376, "// `*` copies the reference out of the list so `it` can be used on its own."),
(381, "// The page title differs per tab."),
(395, ""),
(396, """/// Handles "Fetch & Stage Image": finds a free web image for the plant, saves
/// it as WebP in images/Database and adds it to the plant's picture list at
/// once (S-10 in DEVIATIONS.md). Takes the app state and the submitted form;
/// gives back a message. Called from `action` in mod.rs."""),
(398, "// The plant's name and current picture list."),
(401, "// Search for a free image using the botanical name, the name and any search term."),
(404, "// Seconds since 1970, to make the file name unique."),
(409, "// Existing pictures as a clean list, then the new file is added at the end."),
(416, "// Tests: run with `cargo test`; they are left out of the real program."),
(420, "/// Each tab has its own idea of \"empty\": tab 10 treats \"None\" as a real answer."),
]
S = [(a,t) for a,t in S if t]
apply("ai_tabs.rs", S)
