"""themes.py - the drop's look, chosen by drop.toml [brand] theme (T0942, 2026-11-21).

ATOMIC was CUT from atomic_ui.PAGE by patch_theme.py, so the Atomic look is exactly the old one.
Keys: root (the :root colour line of the sending page), theme_color (browser bar + manifest), svg (the
animated picture; must keep the classes atom, orbits, nucleus, glow that the page script drives).
"""
import dropconfig

ATOMIC = {
    "root": ':root{--bg:#0b1026;--card:#131a3a;--ink:#e8ecff;--dim:#9aa3c7;--cyan:#5ad1ff;--orange:#ff9b3d;--ok:#4ade80;--bad:#f87171;--spin:7s}',
    "theme_color": "#0b1026",
    "svg": '<svg class=atom viewBox="0 0 96 96" aria-hidden=true>\n <defs><radialGradient id=g><stop offset="0" stop-color="#ffb46b" stop-opacity=".55"/><stop offset=".45" stop-color="#5ad1ff" stop-opacity=".12"/><stop offset="1" stop-color="#5ad1ff" stop-opacity="0"/></radialGradient>\n  <path id=o0 d="M14,48a34,13 0 1,0 68,0a34,13 0 1,0 -68,0"/></defs>\n <circle class=glow cx=48 cy=48 r=30 fill="url(#g)"/>\n <g class=orbits fill=none stroke="#5ad1ff" stroke-width=3.2 stroke-linecap=round>\n  <g><use href="#o0"/><circle class=proton r=3.6 fill="#fff" stroke=none><animateMotion dur="1.1s" repeatCount=indefinite><mpath href="#o0"/></animateMotion></circle></g>\n  <g transform="rotate(60 48 48)"><use href="#o0"/><circle class=proton r=3.6 fill="#bdeeff" stroke=none><animateMotion dur="1.5s" begin="-.4s" repeatCount=indefinite keyPoints="1;0" keyTimes="0;1" calcMode=linear><mpath href="#o0"/></animateMotion></circle></g>\n  <g transform="rotate(120 48 48)"><use href="#o0"/><circle class=proton r=3.6 fill="#fff" stroke=none><animateMotion dur=".9s" begin="-.2s" repeatCount=indefinite><mpath href="#o0"/></animateMotion></circle></g>\n </g>\n <circle class=nucleus cx=48 cy=48 r=8 fill="#ff9b3d"/>\n</svg>',
}

# --- garden (T0942, 2026-11-21): Garden Drop for Robin.
# Jason 2026-11-21: "start with a seed turning into a mighty plant and reverse if it's going in reverse".
# The page script already writes the overall progress into #bar's aria-valuenow (paint(), every update). A small
# watcher in this theme's SVG turns that number into the plant: the stem draws upward, six leaves open one after
# another, a bud appears at 92 %; when the number drops (a retry, the next file starting again) the plant shrinks
# back the same way. When everything is filed (body.done) the bud opens into a flower; on an idle page with
# nothing sent only the seed shows. On an error (body.bad) the leaves wilt to the error colour.
# The upload engine is untouched: it still rotates `.orbits` (here an empty, invisible group, so the plant never
# spins) and pulses `.nucleus` (the seed) and `.glow`. Reduced motion: the plant still follows the number, without
# the animation. Colours: dark green night; contrast measured by test_themes.py.
GARDEN = {
    # The bar track colour sits in the page CSS (#5a679c for atomic, T0931 item 22: 3:1 against its sky), so the
    # garden theme overrides it right after its colour line; atomic's root stays exactly the old line.
    "root": ":root{--bg:#0c1f15;--card:#14301f;--ink:#e9f7ee;--dim:#a6c9b0;--cyan:#6fd38b;--orange:#ffb3c7;"
            "--ok:#86efac;--bad:#fca5a5;--spin:7s}\nhtml .bar{background:#4f7d5e}",
    # OUTCOME 2026-11-21 screenshot: a plain `.bar{...}` here lost to the page's own later `.bar` rule (same
    # specificity, later wins), so the garden bar stayed blue; `html .bar` is more specific and wins.
    "theme_color": "#0c1f15",
    # App icon (step 3): /icon.svg, /icon-192.png, /icon-512.png come from this module (made by gen_icons_garden.py).
    # Atomic has no key here, so share.py keeps serving its own ICON and icons_png exactly as before.
    "icon_module": "icons_png_garden",
    "svg": """<svg class=atom viewBox="0 0 96 96" aria-hidden=true>
 <style>
  .atom{--g:0;--bud:0}
  .atom .stem{stroke-dasharray:100 200;stroke-dashoffset:calc((1 - var(--g)) * 100);transition:stroke-dashoffset .7s ease}
  .atom .lf{transition:transform .5s cubic-bezier(.2,1.3,.4,1);transform-box:view-box}
  .atom .l1{transform-origin:48px 75px;transform:scale(var(--l1,0))}
  .atom .l2{transform-origin:47px 66px;transform:scale(var(--l2,0))}
  .atom .l3{transform-origin:48px 56px;transform:scale(var(--l3,0))}
  .atom .l4{transform-origin:46px 46px;transform:scale(var(--l4,0))}
  .atom .l5{transform-origin:49px 36px;transform:scale(var(--l5,0))}
  .atom .l6{transform-origin:46px 27px;transform:scale(var(--l6,0))}
  .atom .bud{transform-origin:48px 17px;transform:scale(var(--bud));transition:transform .5s ease}
  .atom .bloom{transform-origin:48px 17px;transform:scale(0);transition:transform .8s cubic-bezier(.2,1.4,.4,1)}
  body.done .atom .bloom{transform:scale(var(--bud))}
  body.bad .atom .lf,body.bad .atom .stem{fill:var(--bad);stroke:var(--bad)}
  @media (prefers-reduced-motion:reduce){.atom .stem,.atom .lf,.atom .bud,.atom .bloom{transition:none}}
 </style>
 <defs><radialGradient id=g><stop offset="0" stop-color="#d9f99d" stop-opacity=".35"/><stop offset=".55" stop-color="#6fd38b" stop-opacity=".1"/><stop offset="1" stop-color="#6fd38b" stop-opacity="0"/></radialGradient>
  <!-- dash 100 + gap 200, not plain 100: with a 100 gap the round cap drew a dot at the stem top at 0 %
       (11-21 idle screenshot) -->
  <path id=lf d="M0,0c6-5 15-4 19,0c-4,4-13,5-19,0z"/></defs>
 <circle class=glow cx=48 cy=52 r=38 fill="url(#g)"/>
 <g class=orbits></g>
 <ellipse cx=48 cy=87 rx=26 ry=5 fill="#3b2a1a"/>
 <!-- every value before the closing slash is QUOTED: in HTML an unquoted value swallows the slash (the stem's
      linecap was read as "round/"), the path stays open and every later shape becomes its invisible child
      (11-21 debug; test_themes.py now fails on that pattern) -->
 <path class="stem" d="M48,84C46,72 51,62 48,50C45,39 51,29 48,17" pathLength="100" fill="none" stroke="#4fbf74" stroke-width="3.4" stroke-linecap="round"/>
 <!-- left leaves: the mirror is on a wrapping <g>, because the CSS scale on .lf REPLACES an element's own
      transform attribute (a mirrored <use> would lose its mirror while growing) -->
 <use class="lf l1" href="#lf" x=48 y=75 fill="#6fd38b"/>
 <g transform="translate(96 0) scale(-1 1)"><use class="lf l2" href="#lf" x=47 y=66 fill="#4fbf74"/></g>
 <use class="lf l3" href="#lf" x=48 y=56 fill="#8fe3a8"/>
 <g transform="translate(96 0) scale(-1 1)"><use class="lf l4" href="#lf" x=46 y=46 fill="#6fd38b"/></g>
 <use class="lf l5" href="#lf" x=49 y=36 fill="#4fbf74"/>
 <g transform="translate(96 0) scale(-1 1)"><use class="lf l6" href="#lf" x=46 y=27 fill="#8fe3a8"/></g>
 <circle class=bud cx=48 cy=17 r=3.6 fill="#86efac"/>
 <ellipse class=nucleus cx=48 cy=84 rx=5 ry=4 fill="#c9a36b"/>
 <g class=bloom>
  <circle cx=48 cy=9 r=5.5 fill="#ffb3c7"/><circle cx=55.5 cy=14.5 r=5.5 fill="#ffc4d3"/><circle cx=52.5 cy=23 r=5.5 fill="#ffb3c7"/>
  <circle cx=43.5 cy=23 r=5.5 fill="#ffc4d3"/><circle cx=40.5 cy=14.5 r=5.5 fill="#ffb3c7"/><circle cx=48 cy=17 r=4.2 fill="#fde68a"/>
 </g>
 <script>
 // Grow with the upload, shrink if it goes back (T0942). Reads only #bar's aria-valuenow; writes CSS variables.
 addEventListener('DOMContentLoaded', () => {
   const bar = document.getElementById('bar'), svg = document.querySelector('.atom');
   if (!bar || !svg) return;
   const T = [.06, .2, .34, .48, .62, .76];
   const set = () => {
     const g = Math.max(0, Math.min(1, (+bar.getAttribute('aria-valuenow') || 0) / 100));
     svg.style.setProperty('--g', g.toFixed(3));
     T.forEach((t, i) => svg.style.setProperty('--l' + (i + 1), Math.max(0, Math.min(1, (g - t) / .12)).toFixed(3)));
     svg.style.setProperty('--bud', g >= .92 ? '1' : '0');
   };
   new MutationObserver(set).observe(bar, {attributes: true, attributeFilter: ['aria-valuenow']});
   set();
 });
 </script>
</svg>""",
}


# --- horizon (2026-11-22): a star collapses into an event horizon and pulls everything in.
# Jason 2026-11-21: "an animation that collapses from a star to an event horizon that pushes all of it into it".
# Same engine hooks as atomic and garden (atom, orbits, nucleus, glow, body.done/bad); a small watcher reads the
# overall progress the page writes into #bar's aria-valuenow and turns it into --g (0..1):
#   idle      a bright star (gradient disc, its core pulsing = .nucleus), a faint disk of debris around it
#   sending   the star shrinks and dims while a black disc with a thin bright photon ring grows inside it; the
#             debris (.orbits, spun by the page) is pulled inward and the accretion disk tightens and brightens
#   reverse   when the number drops (a retry, the next file) everything moves back the same way
#   done      the star is gone; one bright ring rushes inward and vanishes into the horizon (pulled in)
#   error     the photon ring turns the error colour
# Colours: the atomic night sky and palette (contrast already measured for atomic); only the picture differs.
# Every SVG attribute value before "/>" is QUOTED (an unquoted value swallows the slash: 11-21 garden debug).
HORIZON = {
    # 2026-11-23 evening (Jason: "purple and black ... and orange like an event horizon"): its own palette, was the
    # atomic navy + cyan. --cyan is the accent NAME the page uses; here it holds purple. "html .bar" beats the page's
    # own .bar track (same trick as garden): #7a62b3 is 3.4:1 on the sky (WCAG 1.4.11 wants 3).
    "root": ":root{--bg:#07050d;--card:#151024;--ink:#efe9ff;--dim:#ab9fcc;--cyan:#b388ff;--orange:#ff8a3d;--ok:#4ade80;--bad:#f87171;--spin:7s}\nhtml .bar{background:#7a62b3}",
    # Review 11-23 #3: the Drop page (portal.py) uses these tokens too, so it matches /sending and the splash.
    # Contrast on --bg #0b1026: ink 16:1, dim 7.6:1, accent 10:1; on-accent ink on the cyan button 10:1.
    "portal_root": "--ink:#efe9ff;--dim:#ab9fcc;--line:#2c2142;--bg:#07050d;--card:#151024;--accent:#b388ff;"
                   "--on-accent:#07050d;--accent-soft:#2a1b47;--warn:#ff8a3d;--warn-soft:#2a1a10;--ok:#4ade80;--bad:#f87171;color-scheme:dark",
    # The small mark beside the Drop page heading: the app icon's black hole and disk, simplified.
    "mark": '<svg class="mark" width="30" height="30" viewBox="0 0 96 96" aria-hidden="true">'
            '<ellipse cx="48" cy="48" rx="44" ry="12" fill="none" stroke="#a66bff" stroke-width="5" transform="rotate(-16 48 48)"/>'
            '<circle cx="48" cy="48" r="24" fill="#000"/><circle cx="48" cy="48" r="26" fill="none" stroke="#ff8a3d" stroke-width="4"/></svg>',
    "theme_color": "#07050d",
    # 2026-11-23 (Jason: the install sheet still showed the old logo): Novashare's own icon, a black hole with
    # its disk (gen_icons_horizon.py); without this key share.py served the atom.
    "icon_module": "icons_png_horizon",
    "svg": """<svg class=atom viewBox="0 0 96 96" aria-hidden=true>
 <style>
  .atom{--g:0}
  .atom .star{transform-origin:48px 48px;transform:scale(calc(1 - var(--g) * 0.85));opacity:calc(1 - var(--g) * 0.85);transition:transform .7s ease,opacity .7s ease}
  .atom .hole{transform-origin:48px 48px;transform:scale(var(--g));transition:transform .7s ease}
  .atom .disk{transform-origin:48px 48px;transform:scale(calc(1.3 - var(--g) * 0.5));opacity:calc(0.08 + var(--g) * 0.92);transition:transform .7s ease,opacity .7s ease}
  .atom .infall{transform-origin:48px 48px;transform:scale(calc(1 - var(--g) * 0.6));opacity:calc(1 - max(0, var(--g) - 0.9) * 10);transition:transform .7s ease,opacity .7s ease}
  .atom .gulp{transform-origin:48px 48px;opacity:0}
  /* hz-full: only when the upload really reached 100 % (an empty page is also body.done: keep the star) */
  body.done .atom.hz-full .star{transform:scale(0);opacity:0}
  body.done .atom .star .nucleus{fill:url(#hzstar)}
  /* review 11-23 #4: an error shows at ANY progress (the red ring sat in .hole, scaled to 0 at 0 %) */
  body.bad .atom .star .nucleus{fill:var(--bad)}
  body.bad .atom .hole{transform:scale(max(.35,var(--g)))}
  /* #9: finished stays visible after the gulp (and with reduced motion): the photon ring turns ok */
  body.done .atom.hz-full .ring{stroke:var(--ok);transition:stroke .6s ease}
  body.done .atom.hz-full .hole{transform:scale(1)}
  body.done .atom.hz-full .gulp{animation:hz-gulp 1.3s cubic-bezier(.5,0,.9,.4) 1 forwards}
  @keyframes hz-gulp{0%{transform:scale(2.2);opacity:0}25%{opacity:.9}100%{transform:scale(.45);opacity:0}}
  body.bad .atom .ring{stroke:var(--bad)}
  @media (prefers-reduced-motion:reduce){.atom .star,.atom .hole,.atom .disk,.atom .infall{transition:none}.atom .gulp{animation:none}}
 </style>
 <defs>
  <radialGradient id="hzstar"><stop offset="0" stop-color="#fffaf0"/><stop offset=".35" stop-color="#ffd27a"/><stop offset=".75" stop-color="#ff9b3d"/><stop offset="1" stop-color="#ff6a3d" stop-opacity="0"/></radialGradient>
  <radialGradient id="hzglow"><stop offset="0" stop-color="#ff8a3d" stop-opacity=".4"/><stop offset=".5" stop-color="#8a4dff" stop-opacity=".18"/><stop offset="1" stop-color="#8a4dff" stop-opacity="0"/></radialGradient>
 </defs>
 <circle class="glow" cx="48" cy="48" r="40" fill="url(#hzglow)"/>
 <g class="disk"><g transform="rotate(-16 48 48)">
  <ellipse cx="48" cy="48" rx="36" ry="9" fill="none" stroke="#ff8a3d" stroke-width="2.2" stroke-opacity=".8"/>
  <ellipse cx="48" cy="48" rx="28" ry="6.5" fill="none" stroke="#a66bff" stroke-width="1.4" stroke-opacity=".8"/>
 </g></g>
 <g class="infall"><g class="orbits">
  <circle cx="84" cy="48" r="1.8" fill="#ffb35c"/><circle cx="20" cy="40" r="1.5" fill="#d8c4ff"/>
  <circle cx="60" cy="16" r="1.3" fill="#fff"/><circle cx="34" cy="80" r="1.6" fill="#ff8a3d"/>
  <circle cx="76" cy="74" r="1.2" fill="#d8c4ff"/><circle cx="14" cy="62" r="1.2" fill="#fff"/>
  <circle cx="70" cy="26" r="1" fill="#ffb35c"/><circle cx="26" cy="22" r="1" fill="#d8c4ff"/>
 </g></g>
 <g class="star"><circle class="nucleus" cx="48" cy="48" r="22" fill="url(#hzstar)"/></g>
 <g class="hole">
  <circle cx="48" cy="48" r="13" fill="#000"/>
  <circle class="ring" cx="48" cy="48" r="14.6" fill="none" stroke="#ffb35c" stroke-width="1.8"/>
 </g>
 <circle class="gulp" cx="48" cy="48" r="16" fill="none" stroke="#b388ff" stroke-width="2"/>
 <script>
 // The collapse follows the upload; it reads only #bar's aria-valuenow and writes --g.
 addEventListener('DOMContentLoaded', () => {
   const bar = document.getElementById('bar'), svg = document.querySelector('.atom');
   if (!bar || !svg) return;
   const set = () => {
     const g = Math.max(0, Math.min(1, (+bar.getAttribute('aria-valuenow') || 0) / 100));
     svg.style.setProperty('--g', g.toFixed(3));
     svg.classList.toggle('hz-full', g >= 0.999);   // the end state needs a real 100 %
   };
   new MutationObserver(set).observe(bar, {attributes: true, attributeFilter: ['aria-valuenow']});
   set();
 });
 </script>
</svg>""",
}

THEMES = {"atomic": ATOMIC, "garden": GARDEN, "horizon": HORIZON}


def current() -> dict:
    """The theme named in drop.toml ([brand] theme), falling back to atomic; plus the configured name."""
    t = dict(THEMES.get(dropconfig.BRAND.get("theme", "atomic"), ATOMIC))
    t["name"] = dropconfig.BRAND.get("name", "Atomic Share")
    t["short_name"] = dropconfig.BRAND.get("short_name", t["name"])
    return t


def apply(page: str) -> str:
    """Fill the @@...@@ placeholders of a page with the current theme."""
    t = current()
    for key, val in (("@@ROOT@@", t["root"]), ("@@SVG@@", t["svg"]), ("@@NAME@@", t["name"]),
                     ("@@THEME_COLOR@@", t["theme_color"])):
        page = page.replace(key, val)
    return page
