/* Hash-routed, no build step -- #/ (browse), #/r/{id} (one recipe),
   #/plan (this week). Hash routing rather than the History API on
   purpose: it needs nothing from Caddy beyond serving index.html once,
   no server-side "catch every path and hand back index.html" rule to
   keep in sync as routes are added. */
(function () {
  var ROOT = window.ROOT;
  var KITCHEN_ROOT = window.KITCHEN_ROOT;
  var app = document.getElementById("app");

  function esc(s) {
    // textContent -> innerHTML escapes < > &, and NOT quotes -- which is
    // fine for a text node and wrong for an attribute value, which is
    // where this is mostly used: src="' + esc(url) + '". A recipe image
    // URL comes off a scraped page, so a quote in it would close the
    // attribute and everything after is markup. Found by semgrep,
    // 2026-09-04. Quotes escaped explicitly; the rest still by the DOM.
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function api(path, opts) {
    return fetch(ROOT + "/api" + path, opts).then(function (r) {
      // READ THE BODY AS TEXT FIRST, THEN TRY JSON. This called r.json()
      // unconditionally, so a 500 -- whose body is the plain words "Internal
      // Server Error" -- threw a parse error instead, and the person saw
      //     Unexpected token 'I', "Internal S"... is not valid JSON
      // which says nothing about what failed and points at the wrong layer
      // entirely. The real status was already in `r` and was being discarded.
      return r.text().then(function (body) {
        var data = null;
        try { data = body ? JSON.parse(body) : null; } catch (e) { /* not JSON */ }
        if (!r.ok) {
          throw new Error(
            (data && data.error) ||
            // A non-JSON error body is usually a stack trace or a proxy page;
            // the first line of it is the useful part, the rest is noise.
            (body ? body.split("\n")[0].slice(0, 200) : "") ||
            ("The server answered " + r.status + "."));
        }
        if (data === null && body) {
          throw new Error("The server sent something this page could not read.");
        }
        return data;
      });
    });
  }

  // --- browse: sticky search + pills, infinite-scroll cards ------------
  // The search box and pill row live OUTSIDE the part that grows on
  // every fetch (#results-grid) -- typing a letter must never rebuild
  // the input it lives in, or the field loses focus mid-keystroke.
  // Jason's own request (2026-08-24): "make it lazy load" -- pages
  // accumulate into one grid as a bottom sentinel scrolls into view,
  // instead of a Prev/Next click replacing the page each time.
  function renderBrowse(filters) {
    filters = filters || { q: "", course: "", label: "", recent: "" };
    // Jason's own request (2026-08-26): "add a NEW pill to show recently
    // imported recipes." `recent` is "" or "1", same string-flag shape
    // as course/label above -- not a real boolean -- specifically so the
    // ONE generic pill click handler below (current[kind] = current[kind]
    // === value ? "" : value) keeps working unchanged for this pill too,
    // rather than needing its own special-cased toggle.
    var current = { q: filters.q || "", course: filters.course || "", label: filters.label || "",
                    recent: filters.recent || "" };
    // Jason's own request (2026-08-25): "on page load can random cards
    // of recipes be loaded" -- then (2026-08-26), refining it: "can
    // randomness just be only when someone visits after an hour. if
    // they click the back button, I want them to see the last recipes
    // that were there for them." A fresh seed every time this function
    // ran (including on the BACK button, which re-enters this same
    // function from scratch -- there is no saved DOM to return to in a
    // hash-routed SPA) meant "go look at a recipe, hit back" reshuffled
    // the whole grid out from under you, which reads as losing your
    // place rather than as a feature. Persisted in localStorage now,
    // keyed by TIME rather than by visit: the same seed survives any
    // number of navigations for an hour, then a genuinely fresh one
    // is drawn -- so back-button always shows what was there, and
    // "random on page load" still means something once you have
    // actually been away a while.
    var SEED_KEY = "recipeapp_browse_seed";
    var SEED_MAX_AGE = 60 * 60 * 1000; // one hour
    var seed;
    try {
      var saved = JSON.parse(localStorage.getItem(SEED_KEY) || "null");
      if (saved && saved.seed && (Date.now() - saved.at) < SEED_MAX_AGE) {
        seed = saved.seed;
      }
    } catch (e) { /* fall through to drawing a fresh one */ }
    if (!seed) {
      seed = Math.floor(Math.random() * 1000000000) + 1;
      try { localStorage.setItem(SEED_KEY, JSON.stringify({ seed: seed, at: Date.now() })); } catch (e) {}
    }

    app.innerHTML =
      '<div class="searchbar"><input type="search" id="search-box" ' +
      'placeholder="Search recipes&hellip;" value="' + esc(current.q) + '"></div>' +
      '<div id="pills-wrap"></div>' +
      '<div class="grid" id="results-grid"></div>' +
      '<div class="scroll-sentinel" id="scroll-sentinel">Loading&hellip;</div>';

    var searchBox = document.getElementById("search-box");
    var grid = document.getElementById("results-grid");
    var sentinel = document.getElementById("scroll-sentinel");
    var debounceTimer;
    var nextPage = 1;
    var totalPages = 1;
    var loading = false;

    function syncUrl() {
      var p = new URLSearchParams();
      if (current.q) p.set("q", current.q);
      if (current.course) p.set("course", current.course);
      if (current.label) p.set("label", current.label);
      if (current.recent) p.set("recent", "1");
      var qs = p.toString();
      // replaceState, not location.hash= -- a hash assignment fires
      // hashchange, which re-enters route() and rebuilds this whole
      // screen (and the search box with it) on every keystroke.
      history.replaceState(null, "", location.pathname + "#/" + (qs ? "?" + qs : ""));
    }

    function pill(kind, value, label, count, active) {
      return '<button type="button" class="pill' + (active ? " on" : "") + '" ' +
        'data-pillkind="' + kind + '" data-pillvalue="' + esc(value) + '">' +
        esc(label) + (count == null ? "" : '<span class="n">' + count + '</span>') + '</button>';
    }

    function cardHtml(r) {
      var img = r.image ? '<img src="' + esc(r.image) + '" alt="" loading="lazy">'
                        : '<div class="noimg"></div>';
      // "Made this" -- Jason's own request (2026-08-25): "add checkboxes
      // to the recipes." A label, not a bare input, so the tap target is
      // the whole pill (easier to hit than a small square) -- and its
      // own click is stopped from bubbling to the card's <a>, or
      // checking it would also navigate into the recipe.
      // r.id goes into two attributes, so it is escaped like everything
      // else -- ids are numeric today, and that is a property of the
      // data source rather than of this code.
      var made = '<label class="madecheck" onclick="event.stopPropagation()">' +
        '<input type="checkbox" data-made-id="' + esc(r.id) + '"><span>Made this</span></label>';
      return '<a class="rcard" href="' + ROOT + '/#/r/' + encodeURIComponent(r.id) + '" data-link>' +
        img + made + '<div class="body"><h3>' + esc(r.title) + '</h3>' +
        (r.course ? '<div class="course">' + esc(r.course) + '</div>' : '') +
        '</div></a>';
    }

    function madeKey(recipeId) { return "recipeapp_made_" + recipeId; }

    function wireMadeChecks() {
      grid.querySelectorAll("[data-made-id]").forEach(function (box) {
        var rid = box.getAttribute("data-made-id");
        var on;
        try { on = localStorage.getItem(madeKey(rid)) === "1"; } catch (e) { on = false; }
        box.checked = on;
        box.parentElement.classList.toggle("on", on);
        box.onchange = function () {
          box.parentElement.classList.toggle("on", box.checked);
          try {
            if (box.checked) localStorage.setItem(madeKey(rid), "1");
            else localStorage.removeItem(madeKey(rid));
          } catch (e) {}
        };
      });
    }

    function loadMore(isFirst) {
      if (loading || nextPage > totalPages) return;
      loading = true;
      sentinel.textContent = "Loading…";
      api("/recipes?page=" + nextPage +
        "&q=" + encodeURIComponent(current.q) +
        "&course=" + encodeURIComponent(current.course) +
        "&label=" + encodeURIComponent(current.label) +
        "&recent=" + (current.recent ? "1" : "0") +
        "&seed=" + seed
      ).then(function (data) {
        loading = false;
        totalPages = Math.max(1, Math.ceil(data.total / data.per_page));

        if (isFirst) {
          var pillsWrap = document.getElementById("pills-wrap");
          // NEW -- Jason's own request (2026-08-26): "can they be moved
          // in and out on a bi weekly schedule[?] new ones that get
          // added have a 14 day timer then get moved to the appropriate
          // pill and out of NEW." A real filter now (Kitchen's own
          // kept_at, 14 days), not just a re-sort -- this pill's own
          // count is exactly how many are in that window right now, so
          // it shrinks and the pill can disappear entirely on its own
          // as recipes age out, with nothing here that has to notice.
          var recentPill = data.new_count
            ? pill("recent", "1", "New", data.new_count, current.recent === "1")
            : "";
          var coursePills = (data.courses || []).map(function (c) {
            return pill("course", c[0], c[0], c[1], current.course === c[0]);
          }).join("");
          var labelPills = (data.labels || []).map(function (l) {
            return pill("label", l.name, l.name, l.count, current.label === l.name);
          }).join("");
          pillsWrap.innerHTML = (recentPill || coursePills || labelPills)
            ? '<div class="pills">' + recentPill + coursePills + labelPills + '</div>' : "";
          pillsWrap.querySelectorAll("[data-pillkind]").forEach(function (btn) {
            btn.onclick = function () {
              var kind = btn.getAttribute("data-pillkind");
              var value = btn.getAttribute("data-pillvalue");
              current[kind] = (current[kind] === value) ? "" : value;
              reset();
            };
          });
        }

        if (!data.recipes.length && isFirst) {
          grid.innerHTML = '<p class="muted">Nothing matches yet.</p>';
        } else {
          grid.insertAdjacentHTML("beforeend", data.recipes.map(cardHtml).join(""));
          wireMadeChecks();
        }
        nextPage++;
        sentinel.textContent = nextPage > totalPages
          ? (data.total ? "That's every recipe here." : "") : "";
      }).catch(function (e) {
        loading = false;
        sentinel.textContent = "Could not load more: " + e.message;
      });
    }

    function reset() {
      syncUrl();
      grid.innerHTML = "";
      nextPage = 1;
      totalPages = 1;
      loadMore(true);
    }

    new IntersectionObserver(function (entries) {
      if (entries[0].isIntersecting) loadMore(false);
    }, { rootMargin: "600px 0px" }).observe(sentinel);

    searchBox.addEventListener("input", function () {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () {
        current.q = searchBox.value;
        reset();
      }, 300);
    });

    reset();
  }

  // --- one recipe -------------------------------------------------------
  function renderRecipe(id, serves) {
    app.innerHTML = '<p class="muted">Loading&hellip;</p>';
    api("/recipes/" + id + (serves ? "?serves=" + serves : "")).then(function (r) {
      var hero = r.image ? '<img class="rhero" src="' + esc(r.image) + '" alt="">' : '';
      var meta = [];
      if (r.prep) meta.push('Prep ' + esc(r.prep));
      if (r.cook) meta.push('Cook ' + esc(r.cook));
      if (r.total) meta.push('Total ' + esc(r.total));
      if (r.yields) meta.push(esc(r.yields));

      // Jason's own request (2026-08-25): "with the models i'd like to
      // also suggest substitutions with tools and food items." A swap
      // link per ingredient/tool, expanding IN PLACE -- see wireSwaps()
      // below, called once the whole screen is in the DOM.
      // Jason's own request (2026-08-25): "I would still like to go back
      // and edit saved pictures already made... only as person1@example.com
      // can I do this... allow person1@example.com to directly add images...
      // like on /kitchen in /recipe." Only rendered when r.owner is true
      // (Kitchen's own _can_edit_images() check -- his one specific
      // address, forwarded through recipe-app's real X-Auth-Email, see
      // api_recipe_detail()'s own comment). A button, not a link -- see
      // wirePhotoEdit() below for the inline search/draw panel it opens.
      function editPicBadge(kind, itemId) {
        if (!r.owner || !itemId) return "";
        return '<button type="button" class="picedit" data-pic-kind="' + esc(kind) +
          '" data-pic-id="' + esc(itemId) + '" title="Change this picture">&#9998;</button>';
      }
      // Jason's own report (2026-08-26): "recipe 30 is missing one
      // ingredient... I would like to be able to go back in and edit
      // those kinds of ingredients." What was actually missing was the
      // PICTURE -- an ingredient nobody had ever given one to used to
      // get no icon row at all, so there was nowhere to put the pencil
      // either. Kitchen now sends an icon entry with url:null for
      // exactly that case (a real food id, just no photo yet); shown
      // here as a dashed placeholder so it still gets the same "add a
      // picture" badge as one that already has an image.
      // Jason's own request (2026-08-26), Tandoor's own screen shown as
      // the reference: "I wanted all recipes to have this layout[,] the
      // food items[,] neatly stacked[,] and converted with the ability
      // to adjust the portions." structured_ingredients (amount/unit/
      // name as real separate fields, from Tandoor's own parsed
      // ingredient data) lines up in real CSS-grid columns instead of
      // one plain sentence per row -- see .itemrow's own comment in
      // style.css. Falls back to the old plain-text line for anything
      // that somehow has no structured data (should not happen for a
      // real kept recipe, but a row with SOMETHING beats one silently
      // dropped).
      var structured = r.structured_ingredients || [];
      // Jason's own request (2026-08-26): "can everyone have a checkbox
      // for the food item[,] or add it to a shopping cart so they can
      // make sure they have the ingredients." A checkbox (personal,
      // localStorage -- see wireIngredientChecks() below, same pattern
      // as the step checkboxes) for "I already have this", and a small
      // cart button (shared, server-side -- see wireShopAdd()) for "I
      // don't, put it on the list". Not gated to r.owner -- his own
      // word was "everyone", unlike the picture-editing controls.
      var ingredients = (structured.length ? structured : (r.ingredients || []).map(function (i) {
          return { name: i.text, icon: i.icon, plain: true };
        })).map(function (ing, idx) {
        if (ing.header) {
          return '<div class="ingheader">' + esc(ing.header) + '</div>';
        }
        var icon = ing.icon
          ? '<span class="iconwrap' + (ing.icon.url ? '' : ' iconwrap-empty') + '">' +
            (ing.icon.url ? '<img src="' + esc(ing.icon.url) + '" alt="">'
                         : '<span class="iconplaceholder">+</span>') +
            editPicBadge(ing.icon.kind, ing.icon.id) + '</span>'
          : '<span></span>';
        var nameText = ing.plain
          ? esc(ing.name)
          : esc(ing.name) + (ing.note ? ' <span class="ingnote">(' + esc(ing.note) + ')</span>' : '');
        var shopText = ing.plain ? ing.name
          : [ing.amount, ing.unit, ing.name].filter(Boolean).join(" ");
        return '<div class="itemrow">' +
          '<label class="ingcheckwrap"><input type="checkbox" class="ingcheck" ' +
          'data-ing-idx="' + idx + '"></label>' +
          icon +
          '<span class="ingamt">' + esc(ing.amount || '') + '</span>' +
          '<span class="ingunit">' + esc(ing.unit || '') + '</span>' +
          '<span class="itemtext">' + nameText +
          ' <button type="button" class="cartlink" data-cart-text="' + esc(shopText) +
          '" title="Add to the shopping list">&#128722;</button>' +
          ' <button type="button" class="swaplink" data-swap-kind="ingredient" ' +
          'data-swap-name="' + esc(ing.name || "") + '">swap</button>' +
          '<div class="swapresult" id="swap-ing-' + idx + '" hidden></div></span></div>';
      }).join("");

      // Jason's own request (2026-08-26): "converted with the ability to
      // adjust the portions" -- same Fraction arithmetic Kitchen's own
      // portion buttons use (scale.py, never a model -- see its own
      // docstring), just triggered here instead of there. Re-fetches
      // and re-renders the whole screen at the new count rather than
      // trying to rescale numbers already on the page, so it can never
      // drift from what the server actually computed.
      var portions = r.servings_base
        ? '<div class="portions">' +
          '<button type="button" class="portion-btn" id="portion-minus" ' +
          (r.servings_now <= 1 ? 'disabled' : '') + ' aria-label="Fewer servings">&minus;</button>' +
          '<span class="portion-count">' + r.servings_now + ' serving' +
          (r.servings_now === 1 ? '' : 's') + '</span>' +
          '<button type="button" class="portion-btn" id="portion-plus" aria-label="More servings">+</button>' +
          (r.servings_now !== r.servings_base
            ? '<button type="button" class="portion-reset" id="portion-reset">Reset to ' +
              r.servings_base + '</button>'
            : '') +
          '</div>'
        : '';

      // Timer/temp/measurement call-outs -- Jason's own request
      // (2026-08-25): "when a timer needs to be set... setting a temp
      // to 400 degrees... illustrations of measurements... just popup
      // the image of this." diagram.badges_for() draws these server-
      // side (code, not AI -- always numerically exact); this just
      // shows the small version inline and the full one on tap. Same
      // .stepicon size as a food/tool step icon, so all three read as
      // one row rather than two different scales.
      // Step checkboxes -- Jason's own request (2026-08-25): "add
      // checkboxes to the recipes and steps per user." Kept per-browser
      // in localStorage (see stepDoneKey()/wireStepChecks() below), not
      // synced through Kitchen -- "per user" is naturally true already
      // since this household's own members each use their own phone,
      // and a step you've checked off mid-cook is throwaway progress
      // for tonight, not something worth a server round trip to persist.
      // The step's own illustration (a photo you took, or one drawn for
      // it) -- Jason's own report (2026-08-25): "I made the photos. they
      // don't appear with the recipe." They were always in the API
      // response (s.photo/s.svg, straight from Kitchen's own build_guide)
      // -- this page just never rendered them, only the small food/tool
      // call-out icons below. Same "photo wins, diagram is the fallback"
      // rule guide.html itself uses.
      function stepArt(s) {
        if (s.photo) {
          return '<div class="stepart">' +
            '<img src="' + esc(s.photo) + '" alt="' + esc(s.alt || "") + '" loading="lazy">' +
            (s.drawn ? '<p class="drawnote muted">a drawing, not a photo</p>' : '') +
            '</div>';
        }
        if (s.svg) {
          return '<div class="stepart stepart-diagram">' + s.svg + '</div>';
        }
        return "";
      }
      // WHAT THE WHOLE RECIPE KNOWS ABOUT ONE STEP (2026-09-04). Only on
      // the Gemini twin. Fetched AFTER the page is drawn and one step at
      // a time, so a slow or failed call costs a note and never the
      // recipe -- the page must work exactly as /recipe does if this
      // never answers.
      function geminiNotes(recipeId, count) {
        if (!window.GEMINI) return;
        for (var i = 1; i <= count; i++) {
          (function (n) {
            api("/step-notes/" + encodeURIComponent(recipeId) + "/" + n)
              .then(function (d) {
                var box = document.querySelector('[data-gnote="' + n + '"]');
                if (!box || !d) return;
                var bits = [];
                if (d.ready) bits.push('<b>Already done:</b> ' + esc(d.ready));
                if (d.watch) bits.push('<b>Watch:</b> ' + esc(d.watch));
                if (d.looks) bits.push('<b>Done when:</b> ' + esc(d.looks));
                if (bits.length) box.innerHTML = bits.join('<br>');
              })
              .catch(function () { /* a missing note is not a broken page */ });
          })(i);
        }
      }

      var steps = (r.steps || []).map(function (s, idx) {
        var foodIcons = (s.icons || []).map(function (ic) {
          return '<span class="stepicon picwrap"><img src="' + esc(ic.url) + '" alt="">' +
            editPicBadge(ic.kind, ic.id) + esc(ic.name) + '</span>';
        }).join("");
        var badgeIcons = (s.badges || []).map(function (b) {
          return '<span class="stepicon badge-trigger" data-badge-label="' + esc(b.label) +
            '" data-badge-svg="' + esc(b.svg) + '" role="button" tabindex="0">' +
            b.svg + esc(b.label) + '</span>';
        }).join("");
        var icons = foodIcons + badgeIcons;
        // Jason's own request (2026-08-25): the same cached "Listen"
        // button Kitchen's own guide page already has, here too. Only
        // rendered when r.listen_enabled (a voice is actually configured
        // -- see Kitchen's tts.enabled()); wired in wireListen() below.
        var listenBtn = r.listen_enabled
          ? '<button type="button" class="listenbtn" data-listen="' + (idx + 1) +
            '">&#128266; Listen</button>'
          : '';
        return '<div class="step" data-step-idx="' + idx + '">' +
          stepArt(s) +
          (icons ? '<div class="stepicons">' + icons + '</div>' : '') +
          '<label class="stepline">' +
          '<input type="checkbox" class="stepcheck" data-step-idx="' + idx + '">' +
          '<span class="n">' + (idx + 1) + '.</span>' +
          '<span class="steptext">' + esc(s.text) + '</span></label>' +
          listenBtn +
          // The Gemini twin only: a slot the notes drop into once they
          // arrive. Empty on /recipe, so the two pages are otherwise
          // identical and the comparison is about one thing.
          (window.GEMINI ? '<div class="gnotes" data-gnote="' + (idx + 1) +
            '"></div>' : '') + '</div>';
      }).join("");

      // Tools get a picture too, same as ingredients -- Jason's own
      // request (2026-08-24): "The items and tools needed it'd be
      // larger." A tool the icon-matcher never found a picture for
      // still shows as a plain chip rather than disappearing.
      var tools = (r.tools || []).filter(function (t) { return t.name; }).map(function (t, idx) {
        var chip = t.url
          ? '<span class="toolchip picwrap"><img src="' + esc(t.url) + '" alt="">' +
            editPicBadge("tool", t.id) + esc(t.name) + '</span>'
          : '<span class="chip">' + esc(t.name) + '</span>';
        return '<span class="toolwrap">' + chip +
          '<button type="button" class="swaplink" data-swap-kind="tool" ' +
          'data-swap-name="' + esc(t.name) + '">swap</button>' +
          '<div class="swapresult" id="swap-tool-' + idx + '" hidden></div></span>';
      }).join(" ");

      // USDA breakdown, two ways -- Jason's own request (2026-08-24): "a
      // usda breakdown per one serving... and a serving for the whole
      // recipe as the recipe calls." Same recipe_nutrition.daily_values()
      // numbers guide.html's own %DV chart uses, just tabbed instead of
      // both shown at once.
      // Traffic-light coloring by %DV -- same convention as UK/EU front-
      // of-pack labels: green under a fifth of a day's worth, amber up
      // to half, red past that. Only meaningful per SERVING (%DV is a
      // per-day reference); the whole-recipe table never shows a %,
      // so it never gets a color either.
      function pctClass(pct) {
        if (pct == null) return "";
        if (pct < 20) return "good";
        if (pct <= 50) return "warn";
        return "bad";
      }
      function nutriTable(rows, showPct) {
        if (!rows || !rows.length) {
          return '<p class="muted">No USDA data for this one yet.</p>';
        }
        return '<table>' + rows.map(function (n) {
          var cls = showPct ? pctClass(n.pct) : "";
          return '<tr><td>' + esc(n.label) + '</td>' +
            '<td class="amt' + (cls ? " " + cls : "") + '">' + n.amount + ' ' + esc(n.unit) + '</td>' +
            (showPct ? '<td class="pct">' + (n.pct != null ? n.pct + '%' : '') + '</td>' : '') +
            '</tr>';
        }).join("") + '</table>';
      }
      var hasNutrition = (r.nutrition_per_serving && r.nutrition_per_serving.length) ||
        (r.nutrition_whole && r.nutrition_whole.length);
      var nutrition = hasNutrition
        ? '<div class="rsection nutri"><h2>USDA nutrition</h2>' +
          '<div class="nutri-tabs">' +
          '<button type="button" class="on" data-nutri="serving">Per serving</button>' +
          '<button type="button" data-nutri="whole">Whole recipe' +
          (r.yields ? ' &mdash; ' + esc(r.yields) : '') + '</button>' +
          '</div><div id="nutri-body">' + nutriTable(r.nutrition_per_serving, true) + '</div></div>'
        : "";

      // Jason's own request (2026-08-26): "use the original source as a
      // link to get back to the recipes. at the top. say source so that
      // if someone doesn't want to use my recipe they can see the
      // original." External, so it opens the source site itself rather
      // than navigating this app away from the recipe.
      var sourceLink = r.source
        ? ' &middot; <a href="' + esc(r.source) + '" target="_blank" rel="noreferrer">Source</a>'
        : '';

      // Jason's own request (2026-08-26): "I want to add these features
      // back into /recipe" -- see wireMissingCard() for what wires each
      // of these up. Ingredient <option>s use the STRUCTURED list's own
      // full text (amount + unit + name), matching what "I already have
      // something specific" needs -- subs.convert() wants the recipe's
      // own line WITH its quantity, not a bare ingredient name.
      var swapOptions = (r.structured_ingredients || [])
        .filter(function (ing) { return !ing.header; })
        .map(function (ing) {
          var full = [ing.amount, ing.unit, ing.name].filter(Boolean).join(" ");
          return '<option value="' + esc(full) + '">' + esc(full) + '</option>';
        }).join("");
      var quickSwaps = (r.quick_swaps || []).map(function (s) {
        return '<button type="button" class="small ghost" data-quick-swap="' +
          esc(s.key) + '">' + esc(s.key) + '</button>';
      }).join("");
      var missingCard =
        '<div class="rsection swapcard"><h2>Missing something?</h2>' +
        (quickSwaps
          ? '<p class="small muted">These are already written down &mdash; the answers ' +
            'come back instantly, with no model involved:</p>' +
            '<div class="quickswaps" id="quick-swaps">' + quickSwaps + '</div>'
          : '') +
        '<form id="swap-else-form">' +
        '<label>Something else<span class="hint">Name the ingredient you have not got.</span></label>' +
        '<input type="text" name="ingredient" required placeholder="e.g. star anise">' +
        '<label>Why? <span class="hint">Optional &mdash; but &ldquo;dairy-free&rdquo; and ' +
        '&ldquo;the shop had none&rdquo; have different answers.</span></label>' +
        '<input type="text" name="why" placeholder="e.g. dairy-free">' +
        '<button class="ghost" type="submit">Find a substitute</button></form>' +
        '<div class="swapresult" id="something-else-result" hidden></div>' +
        (swapOptions
          ? '<form id="swap-amount-form" style="margin-top:1.1rem">' +
            '<label>I already have something specific<span class="hint">Pick what the ' +
            'recipe calls for, then say what you have instead.</span></label>' +
            '<select name="ingredient" required>' + swapOptions + '</select>' +
            '<label style="margin-top:.5rem">What you have instead</label>' +
            '<input type="text" name="have" required placeholder="e.g. stevia">' +
            '<button class="ghost" type="submit">How much do I need?</button></form>' +
            '<div class="swapresult" id="swap-amount-result" hidden></div>'
          : '') +
        '</div>' +
        '<div class="rsection swapcard"><h2>Don&rsquo;t have the equipment?</h2>' +
        '<form id="swap-equipment-form">' +
        '<label>What are you missing?<span class="hint">e.g. a stand mixer, a Dutch oven, ' +
        'a meat thermometer.</span></label>' +
        '<input type="text" name="item" required placeholder="e.g. stand mixer">' +
        '<button class="ghost" type="submit">Find a workaround</button></form>' +
        '<div class="swapresult" id="swap-equipment-result" hidden></div></div>';

      var today = new Date().toISOString().slice(0, 10);
      app.innerHTML =
        '<a href="' + ROOT + '/#/" data-link class="muted">&larr; Browse</a>' + sourceLink +
        hero + '<h1>' + esc(r.title) + '</h1>' +
        '<div class="rmeta">' + meta.join(' &middot; ') + '</div>' +
        // Cooking mode -- Jason's own request (2026-08-25): "a cooking
        // mode that keeps the phone on while making a recipe." See
        // wireCookingMode() below, called once this screen is in the
        // DOM -- the button itself is inert markup until then.
        '<button type="button" id="cooking-mode-btn" class="cookbtn">' +
        '<span class="dot"></span> Cooking mode</button>' +
        // Ingredients before Tools -- Jason's own request (2026-08-25):
        // "I would like to add tools under the ingredients."
        '<div class="rsection"><h2>Ingredients</h2>' + portions + ingredients + '</div>' +
        (tools ? '<div class="rsection"><h2>Tools</h2><div class="chips">' + tools + '</div></div>' : '') +
        missingCard +
        '<div class="rsection"><h2>Steps</h2>' +
        // Jason's own request (2026-08-25): "like in /kitchen when I can
        // generate a photo for step by step... for a recipe in /recipe."
        // r.owner is Kitchen's own _can_edit_images() check (person1@example.com
        // only), forwarded as-is -- see api_recipe_detail()'s comment.
        (r.owner ? '<button type="button" id="draw-steps-btn" class="drawbtn">' +
          'Generate step photos</button><p class="muted" id="draw-steps-status"></p>' : '') +
        steps + '</div>' +
        nutrition +
        '<div class="rsection"><h2>Add to this week\'s plan</h2>' +
        '<form class="planform" id="plan-form">' +
        '<span><label>Date</label><input type="date" name="date" value="' + today + '" required></span>' +
        '<span><label>Meal</label><select name="meal_type">' +
        '<option value="breakfast">Breakfast</option><option value="lunch">Lunch</option>' +
        '<option value="dinner" selected>Dinner</option><option value="side">Side</option>' +
        '</select></span><button type="submit">Add</button>' +
        '<span class="muted" id="plan-msg"></span></form></div>';

      document.getElementById("plan-form").onsubmit = function (e) {
        e.preventDefault();
        var fd = new FormData(e.target);
        fd.set("recipe_id", id);
        api("/plan", { method: "POST", body: fd }).then(function (res) {
          document.getElementById("plan-msg").textContent = res.ok ? "Added." : "Could not add it.";
        });
      };

      if (hasNutrition) {
        var nutriTabs = app.querySelectorAll("[data-nutri]");
        nutriTabs.forEach(function (btn) {
          btn.onclick = function () {
            nutriTabs.forEach(function (b) { b.classList.remove("on"); });
            btn.classList.add("on");
            var whole = btn.getAttribute("data-nutri") === "whole";
            document.getElementById("nutri-body").innerHTML =
              nutriTable(whole ? r.nutrition_whole : r.nutrition_per_serving, !whole);
          };
        });
      }

      wireSwaps(id);
      wireMissingCard(id);
      wireBadges();
      // Gemini twin only, and only after the page is already usable --
      // a slow or failed note must never delay the recipe itself.
      geminiNotes(r.id, (r.steps || []).length);
      wireStepChecks(id);
      wireIngredientChecks(id);
      wireShopAdd(id);
      wireCookingMode();
      wirePhotoEdit();
      wireListen(id);
      var portionMinus = document.getElementById("portion-minus");
      var portionPlus = document.getElementById("portion-plus");
      var portionReset = document.getElementById("portion-reset");
      if (portionMinus) portionMinus.onclick = function () {
        renderRecipe(id, Math.max(1, r.servings_now - 1));
      };
      if (portionPlus) portionPlus.onclick = function () {
        renderRecipe(id, r.servings_now + 1);
      };
      if (portionReset) portionReset.onclick = function () {
        renderRecipe(id, r.servings_base);
      };
      var drawStepsBtn = document.getElementById("draw-steps-btn");
      if (drawStepsBtn) {
        drawStepsBtn.onclick = function () {
          drawStepsBtn.disabled = true;
          var status = document.getElementById("draw-steps-status");
          status.textContent = "Drawing… this can take a few minutes.";
          api("/recipes/" + id + "/draw-missing", { method: "POST" }).then(function (res) {
            if (res.made) { route(); return; }
            drawStepsBtn.disabled = false;
            status.textContent = res.error || "Nothing left to draw.";
          }).catch(function (e) {
            drawStepsBtn.disabled = false;
            status.textContent = "Could not draw pictures: " + e.message;
          });
        };
      }
    }).catch(function (e) {
      app.innerHTML = '<p class="muted">Could not load that recipe: ' + esc(e.message) + '</p>';
    });
  }

  // --- step checkboxes ---------------------------------------------------
  function stepDoneKey(recipeId) { return "recipeapp_steps_done_" + recipeId; }

  function loadDoneSteps(recipeId) {
    try {
      var raw = localStorage.getItem(stepDoneKey(recipeId));
      return raw ? JSON.parse(raw) : [];
    } catch (e) { return []; }
  }

  function wireStepChecks(recipeId) {
    var done = loadDoneSteps(recipeId);
    app.querySelectorAll(".stepcheck").forEach(function (box) {
      var idx = Number(box.getAttribute("data-step-idx"));
      var stepEl = box.closest(".step");
      if (done.indexOf(idx) !== -1) {
        box.checked = true;
        if (stepEl) stepEl.classList.add("done");
      }
      box.addEventListener("change", function () {
        var current = loadDoneSteps(recipeId);
        var at = current.indexOf(idx);
        if (box.checked) {
          if (at === -1) current.push(idx);
          if (stepEl) stepEl.classList.add("done");
        } else {
          if (at !== -1) current.splice(at, 1);
          if (stepEl) stepEl.classList.remove("done");
        }
        try { localStorage.setItem(stepDoneKey(recipeId), JSON.stringify(current)); } catch (e) {}
      });
    });
  }

  // --- ingredient checkboxes and "add to shopping list" -----------------
  // Jason's own request (2026-08-26): "can everyone have a checkbox for
  // the food item[,] or add it to a shopping cart so they can make sure
  // they have the ingredients." Checkbox is personal (localStorage, same
  // per-browser lifetime as the step ticks above -- "do I have this on
  // my counter right now" is throwaway progress for tonight, not
  // household state). The cart button is the opposite: shared, so
  // whoever's actually going to the shop sees the whole household's list.
  function ingDoneKey(recipeId) { return "recipeapp_ing_done_" + recipeId; }

  function loadDoneIngredients(recipeId) {
    try {
      var raw = localStorage.getItem(ingDoneKey(recipeId));
      return raw ? JSON.parse(raw) : [];
    } catch (e) { return []; }
  }

  function wireIngredientChecks(recipeId) {
    var done = loadDoneIngredients(recipeId);
    app.querySelectorAll(".ingcheck").forEach(function (box) {
      var idx = Number(box.getAttribute("data-ing-idx"));
      var row = box.closest(".itemrow");
      if (done.indexOf(idx) !== -1) {
        box.checked = true;
        if (row) row.classList.add("done");
      }
      box.addEventListener("change", function () {
        var current = loadDoneIngredients(recipeId);
        var at = current.indexOf(idx);
        if (box.checked) {
          if (at === -1) current.push(idx);
          if (row) row.classList.add("done");
        } else {
          if (at !== -1) current.splice(at, 1);
          if (row) row.classList.remove("done");
        }
        try { localStorage.setItem(ingDoneKey(recipeId), JSON.stringify(current)); } catch (e) {}
      });
    });
  }

  function wireShopAdd(recipeId) {
    app.querySelectorAll(".cartlink").forEach(function (btn) {
      btn.onclick = function () {
        var text = btn.getAttribute("data-cart-text");
        if (!text || btn.disabled) return;
        btn.disabled = true;
        var fd = new FormData();
        fd.set("text", text);
        api("/recipes/" + recipeId + "/shop-add", { method: "POST", body: fd })
          .then(function (res) {
            if (res.ok) {
              btn.innerHTML = res.added ? "&#10003;" : "&#10003; already there";
              btn.title = res.added ? "Added to the shopping list" : "Already on the shopping list";
            } else {
              btn.disabled = false;
              alert(res.error || "Could not add that to the shopping list.");
            }
          })
          .catch(function (e) {
            btn.disabled = false;
            alert("Could not add that to the shopping list: " + e.message);
          });
      };
    });
  }

  // --- cooking mode: keep the screen on while following a recipe --------
  // Jason's own request (2026-08-25): "a cooking mode that keeps the
  // phone on while making a recipe." The Screen Wake Lock API is the
  // real mechanism; released automatically by the browser the moment
  // the tab is hidden (switching apps to check a timer, the phone
  // auto-locking first), so it re-acquires on visibilitychange rather
  // than assuming one request lasts the whole cook.
  var wakeLock = null;
  var cookingModeOn = false;

  async function acquireWakeLock() {
    // {ok, why} not a bare bool -- Jason's own report (2026-08-26):
    // "when clicking cooking mode nothing happens." The button's click
    // handler WAS running; a rejected/unsupported wakeLock.request() just
    // had nowhere to say so, so a real failure looked identical to
    // nothing happening at all. Re-acquiring on visibilitychange (see
    // below) still fails silently on purpose -- that path fires with no
    // click to react to, and a background retry throwing an alert every
    // time you switch back to the tab would be worse than the failure.
    if (!("wakeLock" in navigator)) return { ok: false, why: "Not supported in this browser." };
    try {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", function () { wakeLock = null; });
      return { ok: true };
    } catch (e) {
      return { ok: false, why: (e && e.message) ? e.message : String(e) };
    }
  }

  function wireCookingMode() {
    var btn = document.getElementById("cooking-mode-btn");
    if (!btn) return;
    // NOT disabled when the Wake Lock API is missing -- CAUGHT reasoning
    // through Jason's own report (2026-08-26) about bigger text: this
    // used to disable the WHOLE button (bigger text included) over only
    // the wake lock half being unsupported, so a browser without it got
    // no cooking mode at all, silently. See the click handler below for
    // why the two are no longer gated on each other.
    btn.onclick = async function () {
      if (cookingModeOn) {
        cookingModeOn = false;
        document.body.classList.remove("cooking");
        if (wakeLock) { try { await wakeLock.release(); } catch (e) {} wakeLock = null; }
        btn.classList.remove("on");
        return;
      }
      // BIGGER TEXT AND THE WAKE LOCK ARE TWO SEPARATE THINGS, TOGGLED
      // TOGETHER BUT NOT GATED ON EACH OTHER. Jason's own request
      // (2026-08-26): "the text steps to prepare should be made
      // bigger... the same size as the food items text" -- Kitchen's own
      // cooking mode (guide.html/base.html) already does exactly this,
      // unconditionally, for the same reason: readable step text from
      // arm's length doesn't need the screen to actually stay awake to
      // be worth having, and a wake lock failure (unsupported browser,
      // denied) used to mean cooking mode did not visibly turn on AT
      // ALL -- which is also the earlier "clicking cooking mode nothing
      // happens" report. Text size is now unconditional; the wake lock
      // is still attempted, best effort, with its own separate message
      // if it specifically fails.
      cookingModeOn = true;
      document.body.classList.add("cooking");
      btn.classList.add("on");
      var res = await acquireWakeLock();
      if (!res.ok) {
        alert("Bigger text is on, but your screen may still lock itself: " + res.why);
      }
    };
  }
  // Registered once, not inside wireCookingMode() -- that runs on every
  // recipe render, which would otherwise stack up one listener per
  // visit for the life of the tab.
  document.addEventListener("visibilitychange", function () {
    if (cookingModeOn && document.visibilityState === "visible" && !wakeLock) {
      acquireWakeLock();
    }
  });

  // A step read aloud in your own cloned voice -- Jason's own request
  // (2026-08-25): the same Listen button Kitchen's own guide page
  // already has, here too, cached server-side (see Kitchen's
  // tts.cached_speak()) so only the FIRST press of a given step is slow.
  // Same one-shared-<audio>-element, toggle-to-stop pattern as
  // Kitchen's own guide.html script; the whole point is that it plays
  // right here and never leaves the page.
  var listenAudio = null;
  var listenBtn = null;
  function wireListen(recipeId) {
    if (!listenAudio) {
      listenAudio = new Audio();
      listenAudio.addEventListener("ended", function () { resetListenBtn(); });
      listenAudio.addEventListener("error", function () { resetListenBtn(); });
    }
    app.querySelectorAll(".listenbtn").forEach(function (btn) {
      btn.onclick = async function () {
        if (listenBtn === btn) {
          listenAudio.pause();
          resetListenBtn();
          return;
        }
        if (listenBtn) { resetListenBtn(); }
        listenBtn = btn;
        btn.disabled = true;
        btn.innerHTML = "Reading&hellip;";
        var n = btn.getAttribute("data-listen");
        try {
          var r = await fetch(ROOT + "/api/recipes/" + recipeId + "/step/" + n + "/listen");
          if (!r.ok) {
            var why = await r.text();
            alert(why || "Could not read that step aloud.");
            resetListenBtn();
            return;
          }
          var blob = await r.blob();
          listenAudio.src = URL.createObjectURL(blob);
          await listenAudio.play();
          btn.disabled = false;
          btn.innerHTML = "&#9209; Stop";
        } catch (e) {
          alert("Could not read that step aloud.");
          resetListenBtn();
        }
      };
    });
  }
  function resetListenBtn() {
    if (listenBtn) {
      listenBtn.disabled = false;
      listenBtn.innerHTML = "&#128266; Listen";
    }
    listenBtn = null;
  }

  // Tap a temp/timer/measurement call-out -- pop the same reusable
  // overlay install-modal already uses (badge-modal in index.html),
  // filled with THIS badge's own SVG, drawn full size.
  function wireBadges() {
    var modal = document.getElementById("badge-modal");
    var body = document.getElementById("badge-modal-body");
    var closeBtn = document.getElementById("badge-close");
    if (!modal || !body) return;
    function open(svg, label) {
      body.innerHTML = svg + '<p class="sub" style="text-align:center;margin-top:.6rem">' +
        esc(label) + '</p>';
      modal.hidden = false;
    }
    function close() { modal.hidden = true; }
    app.querySelectorAll(".badge-trigger").forEach(function (el) {
      el.onclick = function () {
        open(el.getAttribute("data-badge-svg"), el.getAttribute("data-badge-label"));
      };
      el.onkeydown = function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.click(); }
      };
    });
    closeBtn.onclick = close;
    modal.onclick = function (e) { if (e.target === modal) close(); };
  }

  // Jason's own request (2026-08-25): "allow person1@example.com to directly
  // add images for items for recipes like on /kitchen in /recipe" --
  // his own explicit choice (AskUserQuestion) was to embed the existing
  // search/draw flow inline here rather than link out to Kitchen's own
  // photo page. Every badge rendered by editPicBadge() above only exists
  // when r.owner is already true, so no extra check is needed client-side
  // -- the real gate is server-side on every search/keep/draw call
  // regardless (_recipe_app_image_gate() in Kitchen's main.py).
  function wirePhotoEdit() {
    var modal = document.getElementById("photo-modal");
    var body = document.getElementById("photo-modal-body");
    var closeBtn = document.getElementById("photo-close");
    if (!modal || !body) return;

    function close() { modal.hidden = true; body.innerHTML = ""; }
    closeBtn.onclick = close;
    modal.onclick = function (e) { if (e.target === modal) close(); };

    function renderPanel(kind, itemId, data) {
      var results = (data.results || []).map(function (img) {
        return '<button type="button" class="photo-result" data-url="' + esc(img.url) +
          '" data-title="' + esc(img.title) + '" title="' + esc(img.title) + '">' +
          '<img src="' + esc(img.thumbnail || img.url) + '" alt="' + esc(img.title) + '"></button>';
      }).join("");
      body.innerHTML =
        '<h2>Change picture' + (data.item_name ? ': ' + esc(data.item_name) : '') + '</h2>' +
        (data.current && data.current.url ? '<div class="photo-current"><img src="' +
          esc(data.current.url) + '" alt="' + esc(data.current.alt || "") +
          '"><span class="muted">Current picture</span></div>' : '') +
        '<form class="photo-search-form" id="photo-search-form">' +
        '<input type="text" name="q" placeholder="Search for a picture&hellip;" ' +
        'value="' + esc(data.item_name || "") + '" required>' +
        '<button type="submit">Search</button></form>' +
        (data.error ? '<p class="muted">' + esc(data.error) + '</p>' : '') +
        (results ? '<div class="photo-results">' + results + '</div>' : '') +
        '<button type="button" id="photo-draw-btn"' + (data.room_ok ? '' : ' disabled') +
        '>Draw one with AI instead</button>' +
        (!data.room_ok && data.room_why ? '<p class="muted">' + esc(data.room_why) + '</p>' : '') +
        '<p class="muted" id="photo-status"></p>';

      var status = document.getElementById("photo-status");

      document.getElementById("photo-search-form").onsubmit = function (e) {
        e.preventDefault();
        var q = e.target.q.value.trim();
        if (!q) return;
        status.textContent = "Searching…";
        api("/items/" + kind + "/" + itemId + "/photo-search?q=" + encodeURIComponent(q))
          .then(function (res) { renderPanel(kind, itemId, res); })
          .catch(function (e) { status.textContent = "Could not search: " + e.message; });
      };

      body.querySelectorAll(".photo-result").forEach(function (btn) {
        btn.onclick = function () {
          status.textContent = "Saving…";
          var fd = new FormData();
          fd.set("url", btn.getAttribute("data-url"));
          fd.set("title", btn.getAttribute("data-title"));
          api("/items/" + kind + "/" + itemId + "/photo-keep", { method: "POST", body: fd })
            .then(function (res) {
              if (res.ok) { close(); route(); }
              else status.textContent = res.error || "Could not save that picture.";
            })
            .catch(function (e) { status.textContent = "Could not save that picture: " + e.message; });
        };
      });

      var drawBtn = document.getElementById("photo-draw-btn");
      drawBtn.onclick = function () {
        drawBtn.disabled = true;
        status.textContent = "Drawing… this can take a minute.";
        api("/items/" + kind + "/" + itemId + "/photo-draw", { method: "POST" })
          .then(function (res) {
            if (res.ok) { close(); route(); }
            else {
              status.textContent = res.error || "Could not draw a picture.";
              drawBtn.disabled = false;
            }
          })
          .catch(function (e) {
            status.textContent = "Could not draw a picture: " + e.message;
            drawBtn.disabled = false;
          });
      };
    }

    app.querySelectorAll(".picedit").forEach(function (el) {
      el.onclick = function () {
        var kind = el.getAttribute("data-pic-kind");
        var itemId = el.getAttribute("data-pic-id");
        if (!kind || !itemId) return;
        body.innerHTML = '<p class="muted">Loading&hellip;</p>';
        modal.hidden = false;
        api("/items/" + kind + "/" + itemId + "/photo-search").then(function (res) {
          renderPanel(kind, itemId, res);
        }).catch(function (e) {
          body.innerHTML = '<p class="muted">Could not load that: ' + esc(e.message) + '</p>';
        });
      };
    });
  }

  // Shared between wireSwaps() (the per-ingredient/tool "swap" link) and
  // the "Missing something?" card's own three forms below -- all four
  // hit the same table-first-then-model shape (subs.suggest()/
  // suggest_equipment()), so there is exactly one place that knows how
  // to draw the answer.
  function renderSwapParts(res) {
    var parts = [];
    if (res.table) {
      parts.push('<div class="src">From the table</div><ul>' +
        res.table.options.map(function (o) { return "<li>" + esc(o) + "</li>"; }).join("") + "</ul>");
    }
    if (res.options && res.source === "written down") {
      parts.push('<div class="src">From the table</div><ul>' +
        res.options.map(function (o) { return "<li>" + esc(o) + "</li>"; }).join("") + "</ul>");
    }
    if (res.model) {
      parts.push('<div class="src">Worked out</div><ul>' +
        res.model.options.map(function (o) { return "<li>" + esc(o) + "</li>"; }).join("") +
        "</ul>" + (res.model.note ? "<p>" + esc(res.model.note) + "</p>" : ""));
    }
    if (res.options && res.source === "worked out") {
      parts.push('<div class="src">Worked out</div><ul>' +
        res.options.map(function (o) { return "<li>" + esc(o) + "</li>"; }).join("") + "</ul>" +
        (res.note ? "<p>" + esc(res.note) + "</p>" : ""));
    }
    if (res.warning) parts.push('<p class="warn-note">' + esc(res.warning) + "</p>");
    return parts.join("") || '<span class="muted">Nothing found.</span>';
  }

  // Jason's own request (2026-08-25): "with the models i'd like to also
  // suggest substitutions with tools and food items." subs.py already
  // has both (table first, the model for anything not written down) --
  // this just reaches it from /recipe. One result box per click, closed
  // again on a second click rather than piling up.
  function wireSwaps(recipeId) {
    app.querySelectorAll(".swaplink").forEach(function (btn) {
      btn.onclick = function () {
        var box = btn.nextElementSibling;
        if (!box.hidden) { box.hidden = true; return; }
        var kind = btn.getAttribute("data-swap-kind");
        var name = btn.getAttribute("data-swap-name");
        box.hidden = false;
        // Jason's own report (2026-08-26): "swap in a recipe does
        // nothing" -- it was working, just taking up to a minute (a
        // real model call, every time, by design -- see subs.suggest()'s
        // own docstring) with nothing on screen to say so. A spinner and
        // honest timing instead of static text that looks abandoned.
        box.innerHTML = '<span class="muted"><span class="swap-spinner"></span>' +
          'Thinking&hellip; this can take up to a minute.</span>';
        var fd = new FormData();
        fd.set("tandoor_id", recipeId);
        fd.set(kind === "tool" ? "item" : "ingredient", name);
        api(kind === "tool" ? "/swap-equipment" : "/swap", { method: "POST", body: fd })
          .then(function (res) { box.innerHTML = renderSwapParts(res); })
          .catch(function (e) {
            box.innerHTML = '<span class="muted">' + esc(e.message) + "</span>";
          });
      };
    });
  }

  // Jason's own request (2026-08-26): "I want to add these features back
  // into /recipe" -- Kitchen's own "Missing something?" card has THREE
  // separate forms (guide.html): quick pre-known swaps (subs.scan(),
  // instant, no model), a free-text "something else" (subs.suggest()),
  // and "I already have something specific" (subs.convert() -- the
  // REVERSE question: given what you already have, how much of it do
  // you need). Plus a separate equipment form, same shape. /recipe only
  // ever got a per-ingredient "swap" link (wireSwaps() above); this is
  // the rest of the card.
  function wireMissingCard(recipeId) {
    var quickWrap = document.getElementById("quick-swaps");
    if (quickWrap) {
      quickWrap.querySelectorAll("[data-quick-swap]").forEach(function (btn) {
        btn.onclick = function () {
          var out = document.getElementById("something-else-result");
          out.hidden = false;
          out.innerHTML = '<span class="muted"><span class="swap-spinner"></span>Thinking&hellip;</span>';
          var fd = new FormData();
          fd.set("tandoor_id", recipeId);
          fd.set("ingredient", btn.getAttribute("data-quick-swap"));
          api("/swap", { method: "POST", body: fd })
            .then(function (res) { out.innerHTML = renderSwapParts(res); })
            .catch(function (e) { out.innerHTML = '<span class="muted">' + esc(e.message) + "</span>"; });
        };
      });
    }

    var elseForm = document.getElementById("swap-else-form");
    if (elseForm) {
      elseForm.onsubmit = function (e) {
        e.preventDefault();
        var out = document.getElementById("something-else-result");
        out.hidden = false;
        out.innerHTML = '<span class="muted"><span class="swap-spinner"></span>' +
          'Thinking&hellip; this can take up to a minute.</span>';
        var fd = new FormData(elseForm);
        fd.set("tandoor_id", recipeId);
        api("/swap", { method: "POST", body: fd })
          .then(function (res) { out.innerHTML = renderSwapParts(res); })
          .catch(function (e) { out.innerHTML = '<span class="muted">' + esc(e.message) + "</span>"; });
      };
    }

    var amountForm = document.getElementById("swap-amount-form");
    if (amountForm) {
      amountForm.onsubmit = function (e) {
        e.preventDefault();
        var out = document.getElementById("swap-amount-result");
        out.hidden = false;
        out.innerHTML = '<span class="muted"><span class="swap-spinner"></span>' +
          'Thinking&hellip; this can take up to a minute.</span>';
        var fd = new FormData(amountForm);
        fd.set("tandoor_id", recipeId);
        api("/swap-amount", { method: "POST", body: fd })
          .then(function (res) {
            if (res.error) { out.innerHTML = '<span class="muted">' + esc(res.error) + '</span>'; return; }
            out.innerHTML = '<div class="src">Worked out</div><p><strong>' + esc(res.amount) +
              '</strong>' + (res.note ? ' &mdash; ' + esc(res.note) : '') + '</p>' +
              (res.warning ? '<p class="warn-note">' + esc(res.warning) + '</p>' : '');
          })
          .catch(function (e) { out.innerHTML = '<span class="muted">' + esc(e.message) + "</span>"; });
      };
    }

    var equipForm = document.getElementById("swap-equipment-form");
    if (equipForm) {
      equipForm.onsubmit = function (e) {
        e.preventDefault();
        var out = document.getElementById("swap-equipment-result");
        out.hidden = false;
        out.innerHTML = '<span class="muted"><span class="swap-spinner"></span>' +
          'Thinking&hellip; this can take up to a minute.</span>';
        var fd = new FormData(equipForm);
        fd.set("tandoor_id", recipeId);
        api("/swap-equipment", { method: "POST", body: fd })
          .then(function (res) { out.innerHTML = renderSwapParts(res); })
          .catch(function (e) { out.innerHTML = '<span class="muted">' + esc(e.message) + "</span>"; });
      };
    }
  }

  // --- add a recipe: paste a link, review, save ------------------------
  // Jason's own request (2026-08-24): "want to add the import part to
  // the /recipe as well." The common case only -- paste a link, check
  // the title/ingredients/steps look right, save it. Deeper editing
  // (step pictures, tools, nutrition review) still lives on Kitchen's
  // own guide page; this covers "paste a link and save it" without ever
  // leaving /recipe for that part.
  function renderImport() {
    app.innerHTML =
      '<h1>Add a recipe</h1>' +
      '<p class="muted">Paste a link to a recipe page, or paste the recipe text itself.</p>' +
      '<form class="importform" id="import-form">' +
      '<input type="url" name="url" placeholder="https://&hellip;">' +
      '<textarea name="text" placeholder="&hellip;or paste the recipe text here instead"></textarea>' +
      '<button type="submit">Import</button>' +
      '</form>' +
      '<div id="import-status"></div>' +
      '<div id="import-review"></div>';

    document.getElementById("import-form").onsubmit = function (e) {
      e.preventDefault();
      var fd = new FormData(e.target);
      var status = document.getElementById("import-status");
      var review = document.getElementById("import-review");
      var btn = e.target.querySelector("button");
      review.innerHTML = "";
      btn.disabled = true;
      // Jason's own request (2026-08-25): "let the user know it's
      // waiting to be imported" -- a quick, non-blocking check before
      // the real (much slower) import call, so someone who submits
      // while something else has the parser sees why it's taking a
      // moment instead of wondering if anything is happening at all.
      status.innerHTML = '<p class="muted">Checking&hellip;</p>';
      fetch(ROOT + "/api/parser-busy").then(function (r) { return r.json(); }).then(function (b) {
        status.innerHTML = b && b.busy
          ? '<p class="muted">Something else is using the parser right now &mdash; ' +
            "this recipe is queued and will start as soon as it's free.</p>"
          : '<p class="muted">Reading the recipe&hellip; this can take a little while.</p>';
      }).catch(function () {
        status.innerHTML = '<p class="muted">Reading the recipe&hellip; this can take a little while.</p>';
      });
      api("/import", { method: "POST", body: fd }).then(function (draft) {
        status.innerHTML = "";
        btn.disabled = false;
        var ingredients = (draft.ingredients || []).map(function (i) {
          return "<li>" + esc(typeof i === "string" ? i : (i.text || i.original_text || "")) + "</li>";
        }).join("");
        var steps = (draft.steps || []).map(function (s) {
          return "<li>" + esc(typeof s === "string" ? s : (s.text || "")) + "</li>";
        }).join("");
        review.innerHTML =
          '<div class="importreview">' +
          (draft.image ? '<img class="rhero" src="' + esc(draft.image) + '" alt="">' : "") +
          "<h2>" + esc(draft.title) + "</h2>" +
          (draft.yields ? '<p class="muted">' + esc(draft.yields) + "</p>" : "") +
          (ingredients ? '<div class="rsection"><h2>Ingredients</h2><ul>' + ingredients + "</ul></div>" : "") +
          (steps ? '<div class="rsection"><h2>Steps</h2><ol>' + steps + "</ol></div>" : "") +
          '<div class="actions">' +
          '<button type="button" id="save-btn">Save to library</button>' +
          '<a class="secondary" href="' + KITCHEN_ROOT + "/r/" + draft.draft_id + '" target="_blank" rel="noopener">Fine-tune in Kitchen</a>' +
          "</div><p id=\"save-msg\"></p></div>";
        document.getElementById("save-btn").onclick = function () {
          var saveBtn = this;
          saveBtn.disabled = true;
          api("/import/" + draft.draft_id + "/keep", { method: "POST" }).then(function (res) {
            if (res.ok && res.tandoor_id) {
              location.hash = "/r/" + res.tandoor_id;
            } else {
              saveBtn.disabled = false;
              document.getElementById("save-msg").innerHTML =
                '<span class="err">' + esc(res.error || "Could not save it.") + "</span>";
            }
          }).catch(function (err) {
            saveBtn.disabled = false;
            document.getElementById("save-msg").innerHTML = '<span class="err">' + esc(err.message) + "</span>";
          });
        };
      }).catch(function (err) {
        btn.disabled = false;
        status.innerHTML = '<p class="err">' + esc(err.message) + "</p>";
      });
    };
  }

  // --- this week's plan ---------------------------------------------------
  function weekDates(startIso) {
    var start = startIso ? new Date(startIso + "T00:00:00") : new Date();
    var day = start.getDay();
    start.setDate(start.getDate() - day);
    var out = [];
    for (var i = 0; i < 7; i++) {
      var d = new Date(start);
      d.setDate(start.getDate() + i);
      out.push(d.toISOString().slice(0, 10));
    }
    return out;
  }

  function renderPlan(startIso) {
    var dates = weekDates(startIso);
    app.innerHTML = '<p class="muted">Loading&hellip;</p>';
    api("/plan?start=" + dates[0] + "&end=" + dates[6]).then(function (entries) {
      var todayIso = new Date().toISOString().slice(0, 10);
      var byDate = {};
      dates.forEach(function (d) { byDate[d] = []; });
      entries.forEach(function (e) { if (byDate[e.date]) byDate[e.date].push(e); });

      var prevStart = new Date(dates[0] + "T00:00:00");
      prevStart.setDate(prevStart.getDate() - 7);
      var nextStart = new Date(dates[0] + "T00:00:00");
      nextStart.setDate(nextStart.getDate() + 7);

      var days = dates.map(function (d) {
        var dt = new Date(d + "T00:00:00");
        var label = dt.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
        var rows = byDate[d].map(function (e) {
          return '<div class="planentry"><span><span class="muted">' + esc(e.meal_type) + '</span> &mdash; ' +
            (e.recipe_id ? '<a href="' + ROOT + '/#/r/' + e.recipe_id + '" data-link>' + esc(e.title) + '</a>' : esc(e.title)) +
            '</span><button data-remove="' + e.id + '" title="Remove">&times;</button></div>';
        }).join("") || '<p class="muted" style="margin:.3rem 0 0">Nothing planned.</p>';
        return '<div class="day' + (d === todayIso ? ' today' : '') + '"><h3>' + label + '</h3>' + rows + '</div>';
      }).join("");

      app.innerHTML =
        '<div class="weeknav"><a href="#" id="prev-week">&larr; Previous week</a>' +
        '<a href="#" id="next-week">Next week &rarr;</a></div>' + days;

      document.getElementById("prev-week").onclick = function (e) {
        e.preventDefault(); renderPlan(prevStart.toISOString().slice(0, 10));
      };
      document.getElementById("next-week").onclick = function (e) {
        e.preventDefault(); renderPlan(nextStart.toISOString().slice(0, 10));
      };
      app.querySelectorAll("[data-remove]").forEach(function (btn) {
        btn.onclick = function () {
          api("/plan/" + btn.getAttribute("data-remove") + "/remove", { method: "POST" })
            .then(function () { renderPlan(dates[0]); });
        };
      });
    }).catch(function (e) {
      app.innerHTML = '<p class="muted">Could not load the plan: ' + esc(e.message) + '</p>';
    });
  }

  // --- pantry: what can I make from what I already have -------------------
  function renderPantry(haveText) {
    haveText = haveText || "";
    app.innerHTML =
      '<h1>What can I make?</h1>' +
      '<p class="muted">List what you already have, one thing per line or ' +
      'separated by commas. Salt, pepper, oil, water and other basics don\'t ' +
      'count against you.</p>' +
      '<form id="pantry-form">' +
      '<textarea name="have" rows="4" style="width:100%;max-width:32rem" ' +
      'placeholder="chicken thighs, rice, broccoli">' + esc(haveText) + '</textarea><br>' +
      '<button type="submit" style="margin-top:.5rem">Find recipes</button>' +
      '</form><div id="pantry-results" class="rsection"></div>';

    function search(text) {
      var results = document.getElementById("pantry-results");
      if (!text.trim()) { results.innerHTML = ""; return; }
      results.innerHTML = '<p class="muted">Looking&hellip;</p>';
      api("/pantry?have=" + encodeURIComponent(text)).then(function (matches) {
        if (!matches.length) {
          results.innerHTML = '<p class="muted">Nothing close yet with just that.</p>';
          return;
        }
        results.innerHTML = matches.map(function (m) {
          var missing = m.missing.length
            ? '<p class="muted">Still need: ' + m.missing.map(esc).join(", ") + '</p>'
            : '<p class="muted">You have everything.</p>';
          return '<div class="itemrow" style="align-items:flex-start;flex-direction:column;gap:.2rem">' +
            '<a href="' + ROOT + '/#/r/' + m.id + '" data-link><strong>' + esc(m.title) + '</strong></a>' +
            '<span class="muted">' + m.got + ' of ' + m.total + ' ingredients</span>' +
            missing + '</div>';
        }).join("");
      });
    }

    document.getElementById("pantry-form").onsubmit = function (e) {
      e.preventDefault();
      search(new FormData(e.target).get("have"));
    };
    if (haveText) search(haveText);
  }

  // --- router -------------------------------------------------------------
  // Released on every navigation, not just when leaving a recipe --
  // simplest correct rule: cooking mode is scoped to one viewing of one
  // recipe screen, and turns back off (screen can sleep again) the
  // moment you go anywhere else, including re-opening the same recipe,
  // rather than a wake lock silently surviving in the background with
  // no visible control left on the page to turn it off.
  function releaseCookingMode() {
    cookingModeOn = false;
    document.body.classList.remove("cooking");
    if (wakeLock) { wakeLock.release().catch(function () {}); wakeLock = null; }
  }

  function route() {
    releaseCookingMode();
    var raw = location.hash.replace(/^#/, "") || "/";
    var qIdx = raw.indexOf("?");
    var hash = qIdx === -1 ? raw : raw.slice(0, qIdx);
    var params = new URLSearchParams(qIdx === -1 ? "" : raw.slice(qIdx + 1));
    var m;
    if (hash === "/" || hash === "") {
      renderBrowse({
        q: params.get("q") || "", course: params.get("course") || "", label: params.get("label") || "",
        recent: params.get("recent") || "",
      });
    } else if ((m = hash.match(/^\/r\/(.+)$/))) {
      renderRecipe(m[1]);
    } else if (hash === "/import") {
      renderImport();
    } else if (hash === "/plan") {
      renderPlan();
    } else if (hash === "/pantry") {
      renderPantry();
    } else {
      app.innerHTML = '<p class="muted">Not sure what that is.</p>';
    }
    window.scrollTo(0, 0);
  }

  window.addEventListener("hashchange", route);
  document.addEventListener("click", function (e) {
    var a = e.target.closest("[data-link]");
    if (!a) return;
    // Same-origin hash links only -- let anything else behave normally.
    if (a.getAttribute("href").indexOf(ROOT + "/#/") !== 0) return;
    // No preventDefault needed: changing location.hash already avoids a
    // full navigation, and the hashchange listener repaints #app.
  });

  document.getElementById("surprise-btn").addEventListener("click", function () {
    api("/random").then(function (r) {
      if (r.id) location.hash = "/r/" + r.id;
      else app.innerHTML = '<p class="muted">Nothing to pick from yet.</p>';
    });
  });

  // --- install as an app --------------------------------------------------
  // Jason's own request (2026-08-24): "a pop-up... to check to see if
  // they would like to install the website as a desktop app... once
  // they install it... the website don't check again." Chrome/Edge
  // (desktop AND Android) fire beforeinstallprompt once a manifest +
  // registered service worker exist (see main.py's own new routes) --
  // this captures that event instead of letting the browser show its
  // own mini-infobar, so the ask matches this app's own look, and
  // remembers the outcome so it never nags after a real install.
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register(ROOT + "/sw.js", { scope: ROOT + "/" }).catch(function () {});
  }

  (function () {
    var INSTALLED_KEY = "recipeapp_installed"; // fast local cache of the server's own answer
    var ASKED_KEY = "recipeapp_install_asked"; // sessionStorage: at most one auto-popup per visit
    var linkBtn = document.getElementById("install-link-btn");

    function isStandalone() {
      return window.matchMedia("(display-mode: standalone)").matches ||
        window.navigator.standalone === true; // iOS home-screen, no beforeinstallprompt there
    }
    function isIOS() {
      return /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
    }
    function markInstalled() {
      try { localStorage.setItem(INSTALLED_KEY, "1"); } catch (e) {}
      // Best effort -- Jason's own request (2026-08-26): "if the desktop/
      // mobile app is downloaded check for it and don't run again for
      // that person." Recorded against the SIGNED-IN PERSON on Kitchen's
      // side (see install-status/install-mark in main.py), not just this
      // one browser -- so installing on the phone means the laptop never
      // asks either, once it is signed in as the same person.
      api("/install-mark", { method: "POST" }).catch(function () {});
    }

    // RUNNING INSTALLED RIGHT NOW is the one unambiguous signal for BOTH
    // platforms -- Chrome/Edge's own appinstalled event fires reliably,
    // but iOS has no equivalent at all; the only way to know "Add to
    // Home Screen" actually happened is that the page is now running in
    // standalone mode. Checked on every load specifically so that iOS
    // install gets recorded server-side too, not only the Chrome path.
    if (isStandalone()) {
      markInstalled();
      return; // nothing to install, nothing to offer
    }

    var localDone;
    try { localDone = localStorage.getItem(INSTALLED_KEY) === "1"; } catch (e) { localDone = false; }
    if (localDone) return; // fast path -- skip the network round trip entirely

    api("/install-status").then(function (res) {
      if (res && res.installed) {
        try { localStorage.setItem(INSTALLED_KEY, "1"); } catch (e) {}
        return; // installed on a different device, signed in as the same person
      }
      offerInstall();
    }).catch(function () {
      offerInstall(); // can't reach Kitchen -- fail open to still offering, not silently skip it
    });

    function offerInstall() {
      var deferred = null;
      var overlay, yesBtn, noBtn;

      // ALWAYS REACHABLE, not just a popup that shows itself once.
      // Jason's own report (2026-08-25): "the install to desktop link is
      // still not working." The popup-only approach depended entirely on
      // beforeinstallprompt firing -- which Safari on iOS NEVER does at
      // all (Apple does not implement it), and which Chrome/Edge gate
      // behind their own engagement heuristics -- so there was often
      // nothing to click, ever, and no way to ask for it directly. This
      // button is always visible (until actually installed) and works
      // either way: the real native prompt if one is ready, manual
      // instructions if not.
      if (linkBtn) {
        linkBtn.hidden = false;
        linkBtn.onclick = function () {
          if (deferred) { showPrompt(); return; }
          showInstallHelp();
        };
      }

      window.addEventListener("beforeinstallprompt", function (e) {
        e.preventDefault();
        deferred = e;
        if (askedAlready()) return;
        setTimeout(showPrompt, 1500); // let the page settle first, not an instant popup
      });

      window.addEventListener("appinstalled", function () {
        markInstalled();
        hidePrompt();
        if (linkBtn) linkBtn.hidden = true;
      });

      // Jason's own request (2026-08-26): "I want something to popup to
      // ask to install" -- on iOS there is no beforeinstallprompt to
      // wait for at all, so without this an iOS visitor got the button
      // and nothing else, ever, unless they happened to notice it. Same
      // once-per-visit throttle as the Chrome/Edge popup above.
      if (isIOS() && !askedAlready()) {
        setTimeout(showInstallHelp, 1500);
      }

      function askedAlready() {
        try { return sessionStorage.getItem(ASKED_KEY) === "1"; } catch (e) { return false; }
      }
      function markAsked() {
        try { sessionStorage.setItem(ASKED_KEY, "1"); } catch (e) {}
      }

      function showPrompt() {
        if (!deferred) return;
        markAsked();
        overlay = document.getElementById("install-modal");
        yesBtn = document.getElementById("install-yes");
        noBtn = document.getElementById("install-no");
        var closeBtn = document.getElementById("install-close");
        if (!overlay) return;
        overlay.hidden = false;
        yesBtn.onclick = function () {
          hidePrompt();
          if (!deferred) return;
          var prompt = deferred;
          deferred = null;
          prompt.prompt();
          prompt.userChoice.then(function (choice) {
            if (choice.outcome === "accepted") {
              markInstalled();
              if (linkBtn) linkBtn.hidden = true;
            }
          });
        };
        noBtn.onclick = hidePrompt;
        // CAUGHT LIVE (2026-08-25): neither of these existed before --
        // the only way out was the "Not now" button, and a separate CSS
        // bug (see style.css's own note) meant even THAT didn't visibly
        // work. Clicking the dimmed backdrop (not the panel itself) and
        // a real X button both dismiss it now.
        if (closeBtn) closeBtn.onclick = hidePrompt;
        overlay.onclick = function (e) {
          if (e.target === overlay) hidePrompt();
        };
      }
      function hidePrompt() {
        if (overlay) overlay.hidden = true;
      }

      // The manual fallback -- reached automatically on iOS (see above)
      // and from the persistent button any time there is no captured
      // beforeinstallprompt to trigger directly.
      function showInstallHelp() {
        markAsked();
        var modal = document.getElementById("install-help-modal");
        var body = document.getElementById("install-help-body");
        if (!modal || !body) return;
        body.innerHTML = isIOS()
          ? "<p>Tap the Share icon in Safari&rsquo;s toolbar (the square with an " +
            "arrow pointing up), then choose <strong>Add to Home Screen</strong>.</p>"
          : "<p>Look for an install icon in your browser&rsquo;s address bar, or open " +
            "the browser&rsquo;s menu and look for <strong>Install app</strong> or " +
            "<strong>Add to Home screen</strong>.</p>" +
            "<p class=\"small muted\">The popup version of this only shows up once " +
            "your browser decides you visit often enough &mdash; the menu option " +
            "works right away.</p>";
        modal.hidden = false;
        var closeBtn = document.getElementById("install-help-close");
        if (closeBtn) closeBtn.onclick = function () { modal.hidden = true; };
        modal.onclick = function (e) { if (e.target === modal) modal.hidden = true; };
      }
    }
  })();

  route();
})();
