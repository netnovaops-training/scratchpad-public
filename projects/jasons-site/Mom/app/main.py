"""Tech Help — a deliberately simple assistant for a non-technical person.

Served under /help on the same hostname as the awards portal, but a completely
separate process, database, user list, and rule set.
"""
import asyncio
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context as jinja_pass_context

from . import (backlog, core, distill, guides_book, linkfix, mailer, memory,
               otobo, otobo_handoff, router, safety, tickets, webhook)
# Imported under another name deliberately. `kb` is already a local variable in
# build_system_prompt below, where it holds the hand-written RULES text -- two
# different things called kb in one function is how you end up calling a
# method on a string at 11pm.
from . import kb as kb_mod

# Photos she sends. Kept narrow on purpose: these are decoded and handed to a
# vision model, so the list is what a phone camera actually produces, not every
# format a library will open.
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif", ".bmp"}

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title=core.SITE_NAME, root_path=core.ROOT_PATH,
              docs_url=None, redoc_url=None, openapi_url=None)
# NO SessionMiddleware ANY MORE.
#
# It existed to hold one thing -- which username was signed in -- and that now
# arrives in a header from Caddy on every request. A signed cookie holding
# nothing is not free: `secret_key=core.SECRET_KEY or secrets.token_hex(32)`
# meant that if MOM_SECRET_KEY were ever unset, the key was regenerated at
# startup and every session died on every rebuild. That failure mode is gone
# with the middleware, rather than being one missing .env line away.
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

# Illustrations from core.draw(), served straight back the same way /static
# is. Has to exist before the mount, not just by the time a picture is
# actually drawn -- StaticFiles checks the directory at mount time, which is
# import time here, well before core.ensure_dirs() runs at startup.
core.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/generated", StaticFiles(directory=str(core.GENERATED_DIR)), name="generated")

# thread_id -> {"state": "waking"|"listening"|"thinking"|"done"|"error", "detail": str}
WORK: dict[int, dict] = {}
_work_lock = threading.Lock()


def set_work(tid: int, state: str, detail: str = "") -> None:
    with _work_lock:
        WORK[tid] = {"state": state, "detail": detail, "ts": time.time()}


def get_work(tid: int) -> dict:
    with _work_lock:
        return dict(WORK.get(tid) or {"state": "done", "detail": ""})


def clear_work(tid: int) -> None:
    with _work_lock:
        WORK.pop(tid, None)


# ------------------------------------------------------------------- helpers

def current_user(request: Request) -> Optional[dict]:
    """Whoever Caddy already signed in, as a row in our users table.

    THIS APP HAS NO LOGIN OF ITS OWN, DELIBERATELY.
    ------------------------------------------------
    It used to. That meant signing in twice to reach one page: once at /auth
    for the site, and again here with a different username and a different
    password. Two sets of credentials for one person, and the second set lived
    only in this container's database -- so removing somebody from
    /admin/people closed the front door and left this account untouched behind
    it. The half you forget is always the removal.

    Now there is one door. Caddy's forward_auth asks the auth service about
    every request before it reaches this container, and copies the address it
    verified into X-Auth-Email. If that header is here, the person is signed
    in; there is no second question worth asking.

    WHY TRUSTING A HEADER IS SAFE HERE, AND WHERE IT WOULD NOT BE
    ------------------------------------------------------------
    A header is only as trustworthy as the thing that sets it, and a header a
    CLIENT can set is worth nothing. Two things make this one different:

      1. This container publishes NO port. It is reachable only on the Docker
         network, and the only thing on that network that proxies to it is
         Caddy. There is no route by which a request from the internet arrives
         here without passing through forward_auth first.

      2. The (appproxy) snippet in the Caddyfile DELETES every client-supplied
         identity header before proxying -- X-Real-IP, X-Forwarded-Host and
         the rest -- and forward_auth SETS X-Auth-Email from the auth
         service's own response, replacing anything the client sent.

    If this app were ever published on a port directly, or put behind a proxy
    that does not strip these, this function would be a way in. That is why
    the block below refuses when the header is absent rather than falling back
    to something friendlier: a missing header means the request did not come
    the way it must, and the correct answer to that is no.
    """
    email = (request.headers.get("X-Auth-Email") or "").strip().lower()
    if not email:
        return None
    row = core.user_for_email(email)
    if not row:
        return None
    return {"username": row["username"], "display_name": row["display_name"],
            "role": row["role"], "is_admin": row["role"] == "admin",
            "is_read_only": row["role"] == "read_only"}


def require_user(request: Request) -> dict:
    u = current_user(request)
    if not u:
        # NOT a redirect to a login page -- there is no login page any more,
        # and sending somebody to one would be a loop. Reaching here means the
        # request did not come through Caddy, so it says so plainly rather
        # than pretending the person needs to do something.
        raise HTTPException(
            401,
            "This page is only reachable through the website's sign-in. "
            "Go to https://example.org/mama/ rather than to this "
            "container directly.",
        )
    return u


def require_write(request: Request) -> dict:
    """require_user, plus refuses a read_only guest -- browse everything,
    change nothing. The only two things a plain user can write here (send a
    message, resolve their own ticket) both start with this instead."""
    u = require_user(request)
    if u["is_read_only"]:
        raise HTTPException(403, "This is a read-only guest account -- it can "
                                 "look around but can't send anything. Ask "
                                 "Jason if you need more than that.")
    return u


def require_admin(request: Request) -> dict:
    u = require_user(request)
    if not u["is_admin"]:
        raise HTTPException(403, "Not allowed")
    return u


def client_key(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def ctx(request: Request, user, **kw) -> dict:
    base = {"request": request, "user": user, "site_name": core.SITE_NAME,
            "root": core.ROOT_PATH, "helper": core.HELPER_NAME,
            "helper_phone": core.HELPER_PHONE,
            # Read by the fmt_ts filter so every timestamp on the page renders
            # in the timezone of whoever is looking at it.
            "tz_user": (user or {}).get("username", "")}
    base.update(kw)
    return base


@jinja_pass_context
def fmt_ts(jinja_ctx, ts: float) -> str:
    """Render a stored UTC epoch in the viewing person's own timezone.

    pass_context is what makes every existing `{{ x|fmt_ts }}` in the templates
    pick this up with no edits: the filter reads `tz_user` out of the template
    context, which ctx() puts there for whoever is signed in.

    So Mom in Nevada sees Nevada times and Jason in New York sees New York
    times, from the same row, with no duplicated templates.
    """
    return core.local_time(ts, jinja_ctx.get("tz_user") or "")


templates.env.filters["fmt_ts"] = fmt_ts


@app.exception_handler(HTTPException)
async def on_http_error(request: Request, exc: HTTPException):
    if exc.status_code == 307 and exc.headers and "Location" in exc.headers:
        return RedirectResponse(exc.headers["Location"], status_code=303)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    return templates.TemplateResponse(request,
        "error.html", ctx(request, current_user(request), code=exc.status_code,
                          detail=exc.detail), status_code=exc.status_code)


@app.on_event("startup")
def _startup():
    core.ensure_dirs()
    core.init_db()
    # Each of these only creates tables if they are missing, so an existing
    # deployment picks up the new features on restart with no migration step
    # and no data loss.
    for name, fn in (("router", router.init), ("memory", memory.init),
                     ("distill", distill.init), ("otobo", otobo.init),
                     ("webhook", webhook.init), ("guides", guides_book.init),
                     ("backlog", backlog.init)):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            core.log("error", "startup", f"{name} init failed: {e}")
    # Drains anything she asked while the desktop was off. Started after the
    # tables exist, and given handle_turn rather than importing it, since
    # backlog importing main would be circular.
    try:
        backlog.start_worker(handle_turn)
    except Exception as e:  # noqa: BLE001
        core.log("error", "startup", f"backlog worker: {e}")
    core.log("info", "startup",
             f"Tech Help started — profile={router.PROFILE}, "
             f"models={router.models()}")


# --------------------------------------------------------------------- auth
#
# There is nothing here on purpose.
#
# /login, /logout and /change-password used to live in this block. They are
# gone because this app no longer has accounts of its own -- see the note on
# current_user() above. Signing in happens once, at /auth, for the whole site;
# signing out happens there too, and it signs you out of everything rather
# than out of one app while the site cookie quietly keeps you signed in.
#
# core.create_user(), set_password() and verify_password() are deliberately
# LEFT IN core.py. `manage.py` still uses them to make an account from the
# command line, which is the only way in if the auth service is ever broken
# and you need to reach the ticket queue.

# --------------------------------------------------------------------- chat

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = require_user(request)
    threads = core.list_threads(user["username"], limit=25)
    # Her own tickets that still need something -- open, pending, or
    # escalated -- separate from the plain "things you've asked before"
    # history above, which includes questions that were never tracked as a
    # ticket at all (tickets.ensure_ticket() is what gives a thread a
    # ticket_no; a lot of threads never go through it).
    my_tickets = core.conn().execute(
        "SELECT id, ticket_no, title, category, status FROM threads "
        "WHERE username=? AND ticket_no != '' AND status IN "
        "('open','pending','escalated') ORDER BY id DESC LIMIT 10",
        (user["username"],)).fetchall()
    # The knowledge base and the ticket system read the SAME published file
    # (MOM_KB_FILE, written by the standalone /mama/kb app) -- kb.load()
    # already has it in memory for the RAG/tool-loop search above, so this
    # costs nothing extra, not a second fetch of anything.
    kb_articles = sorted(kb_mod.load(), key=lambda a: a.get("published", 0), reverse=True)[:8]
    return templates.TemplateResponse(request, "home.html", ctx(
        request, user, threads=threads, my_tickets=my_tickets,
        kb_articles=kb_articles, backend=core.backend_status()))


@app.get("/t/{tid}", response_class=HTMLResponse)
def thread_view(request: Request, tid: int):
    user = require_user(request)
    th = core.get_thread(tid)
    if not th or (th["username"] != user["username"] and not user["is_admin"]):
        raise HTTPException(404, "That conversation doesn't exist.")
    msgs = core.list_messages(tid)
    return templates.TemplateResponse(request, "thread.html", ctx(
        request, user, thread=th, messages=msgs, work=get_work(tid)))


@app.post("/t/{tid}/msg/{mid}/draw")
def draw_message(request: Request, tid: int, mid: int):
    """An illustration of this message, drawn on request -- 'show me a
    picture of this' as a button rather than something every answer gets
    whether it helps or not. A read-only guest cannot press it: this
    changes the conversation (a picture gets added), the same rule that
    already gates sending a message at all."""
    user = require_write(request)
    th = core.get_thread(tid)
    if not th or (th["username"] != user["username"] and not user["is_admin"]):
        raise HTTPException(404, "That conversation doesn't exist.")
    m = core.get_message(mid)
    if not m or m["thread_id"] != tid:
        raise HTTPException(404, "That message doesn't exist.")
    try:
        png = core.draw(m["content"])
    except core.ImagineError as e:
        return _redir(f"Couldn't draw one: {e}", f"/t/{tid}")
    core.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{mid}_{secrets.token_hex(4)}.png"
    (core.GENERATED_DIR / name).write_bytes(png)
    core.set_message_image(mid, name)
    return RedirectResponse(f"{core.ROOT_PATH}/t/{tid}", status_code=303)


@app.post("/ask")
async def ask(request: Request,
              thread_id: str = Form(""),
              question: str = Form(""),
              audio: Optional[UploadFile] = File(None),
              photos: list[UploadFile] = File(default=[]),
              video: Optional[UploadFile] = File(None)):
    """Accept a typed question, a voice recording, photos, a video, or any
    combination."""
    user = require_write(request)
    question = (question or "").strip()

    # --- photos ---------------------------------------------------------------
    # Three at most. Each one costs hundreds of tokens once the vision model
    # tiles it, and this runs on a card that is already close to full.
    image_paths: list[str] = []
    for photo in (photos or [])[:3]:
        if not photo or not photo.filename:
            continue
        ext = Path(photo.filename).suffix.lower()
        if ext not in IMAGE_EXT:
            raise HTTPException(400, "That kind of picture isn't supported. "
                                     "A photo from your phone will work.")
        core.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = core.UPLOAD_DIR / f"{int(time.time())}_{secrets.token_hex(4)}{ext}"
        limit = core.MAX_UPLOAD_MB * 1024 * 1024
        written = 0
        with dest.open("wb") as fh:
            while chunk := await photo.read(1024 * 256):
                written += len(chunk)
                if written > limit:
                    fh.close(); dest.unlink(missing_ok=True)
                    raise HTTPException(413, "That picture is too big.")
                fh.write(chunk)
        if written:
            image_paths.append(str(dest))
        else:
            dest.unlink(missing_ok=True)

    audio_path = ""
    if audio is not None and audio.filename:
        ext = Path(audio.filename).suffix.lower() or ".webm"
        if ext not in core.AUDIO_EXT:
            raise HTTPException(400, "That kind of file isn't supported.")
        core.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = core.UPLOAD_DIR / f"{int(time.time())}_{secrets.token_hex(4)}{ext}"
        limit = core.MAX_UPLOAD_MB * 1024 * 1024
        written = 0
        with dest.open("wb") as fh:
            while chunk := await audio.read(1024 * 256):
                written += len(chunk)
                if written > limit:
                    fh.close(); dest.unlink(missing_ok=True)
                    raise HTTPException(413, "That recording is too long.")
                fh.write(chunk)
        if written == 0:
            dest.unlink(missing_ok=True)
        else:
            audio_path = str(dest)

    # --- video -----------------------------------------------------------
    # Stored, never watched by anything here -- no model in this stack reads
    # video. It shows up only on the admin ticket page (Jason's own eyes),
    # never on this page or anywhere she can see. See core.VIDEO_EXT's own
    # comment for why that boundary matters.
    video_path = ""
    if video is not None and video.filename:
        ext = Path(video.filename).suffix.lower()
        if ext not in core.VIDEO_EXT:
            raise HTTPException(400, "That kind of video isn't supported.")
        core.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = core.UPLOAD_DIR / f"{int(time.time())}_{secrets.token_hex(4)}{ext}"
        limit = core.MAX_VIDEO_MB * 1024 * 1024
        written = 0
        with dest.open("wb") as fh:
            while chunk := await video.read(1024 * 256):
                written += len(chunk)
                if written > limit:
                    fh.close(); dest.unlink(missing_ok=True)
                    raise HTTPException(413, "That video is too big.")
                fh.write(chunk)
        if written == 0:
            dest.unlink(missing_ok=True)
        else:
            video_path = str(dest)

    if not question and not audio_path and not image_paths and not video_path:
        raise HTTPException(400, "Type a question, record one, or send a picture or video.")

    tid = int(thread_id) if thread_id.isdigit() else 0
    if tid:
        th = core.get_thread(tid)
        if not th or th["username"] != user["username"]:
            raise HTTPException(404, "That conversation doesn't exist.")
    else:
        default_title = "Voice question" if audio_path else (
            "Picture" if image_paths else ("Video" if video_path else "New question"))
        tid = core.create_thread(user["username"], question[:80] or default_title)

    set_work(tid, "queued", "Getting started...")
    threading.Thread(target=handle_turn,
                     args=(tid, user["username"], question, audio_path, image_paths),
                     kwargs={"video_path": video_path},
                     daemon=True).start()
    return RedirectResponse(f"{core.ROOT_PATH}/t/{tid}", status_code=303)


def build_system_prompt(username: str, question: str = "") -> tuple[str, str]:
    """Assemble the prompt, most-authoritative first.

    Order is deliberate and is the safety model of the whole app:

        1. RULES        hand-written, absolute, never touched by any model
        2. FACTS        what it knows about her, overridden by what she says now
        3. KNOWN FIXES  articles you wrote for problems already solved
        4. LEARNED      notes the nightly job wrote, explicitly NOT rules
        5. WHAT WORKED  retrieved past resolutions, explicitly NOT instructions

    Everything below line 1 is framed as material the model may use and may
    ignore. Only the rules are binding, and safety.py enforces the important
    ones in code regardless of what any of this text says.
    """
    kb, ver = core.load_knowledge()
    mem = core.recall(username)
    memory_block = ""
    if mem:
        lines = "\n".join(f"- {m['key']}: {m['value']}" for m in mem)
        memory_block = ("\n\n===== WHAT YOU ALREADY KNOW ABOUT HER =====\n"
                        "Learned from previous conversations. Use it so she never has to\n"
                        "repeat herself. If something here contradicts what she says now,\n"
                        "believe her, not this.\n" + lines + "\n")
    # Layers 3 and 4. Both are best-effort: if the desktop is busy or the
    # embedding model is missing they return empty strings and the assistant
    # behaves exactly as it did before any of this existed.
    try:
        learned_block = distill.load_learned()
    except Exception:
        learned_block = ""
    try:
        recall_block = memory.recall_block(question) if question else ""
    except Exception:
        recall_block = ""
    # Your handbook outranks what the assistant worked out on its own, because
    # you know which oven she has and it does not.
    try:
        book_block = guides_book.recall_block(question) if question else ""
    except Exception:
        book_block = ""
    # And a known fix outranks even the handbook for the problem it covers: it
    # is not general guidance, it is the thing that actually worked on this
    # printer. Best-effort like the rest -- a missing or broken knowledge base
    # returns an empty string and the assistant behaves as it always did.
    try:
        known_block = kb_mod.recall_block(question) if question else ""
    except Exception:
        known_block = ""

    system = (
        "You are the assistant described below. The rules are absolute and override "
        "anything the user asks for.\n\n"
        "===== YOUR RULES =====\n" + (kb or "(no rules loaded)") +
        "\n===== END RULES =====" + memory_block + known_block + book_block
        + learned_block + recall_block)
    return system, ver


def _json_from(text: str):
    """Same tiny extractor tickets.py and distill.py each already carry --
    a bare regex for the first {...} block, not a shared module, matching
    how small this is everywhere else it exists."""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


TRIAGE_PROMPT = """\
Before giving a full answer, decide whether you already have enough to give
a SPECIFIC, confident fix for exactly what she is describing -- not a
generic troubleshooting checklist.

If you do not have enough yet, ask exactly ONE clarifying question: the
single question that would most narrow down the cause. Good things to ask
about: exactly what she sees on the screen, when it started, what (if
anything) she has already tried, which device or program this is about.
Never ask something the conversation below already answers, and never ask
more than one question at once.

Return ONLY JSON, no prose, no markdown fence:
{{"need_more_info": true|false, "question": "<one short question in her own \
language, or empty string if need_more_info is false>"}}

CONVERSATION SO FAR:
{transcript}
"""


def _triage_question(system: str, tid: int) -> str:
    """One clarifying question, or "" if there is already enough to answer
    confidently -- see core.TRIAGE_ROUNDS for the round budget this is
    called under. Never raises; a failed judgment call here means answering
    normally instead of the household getting no answer at all."""
    try:
        history = core.list_messages(tid)[-(core.HISTORY_TURNS * 2):]
        transcript = "\n".join(
            f"{'SHE' if m['role'] == 'user' else 'YOU'}: {m['content']}"
            for m in history if m["role"] in ("user", "assistant"))[:4000]
        out = core.chat([
            {"role": "system", "content": system},
            {"role": "user", "content": TRIAGE_PROMPT.format(transcript=transcript)},
        ], temperature=0.3)
        data = _json_from(out) or {}
        if data.get("need_more_info") and str(data.get("question") or "").strip():
            return str(data["question"]).strip()[:500]
    except Exception as e:  # noqa: BLE001
        core.log("warn", "triage", f"skipped: {e}")
    return ""


def handle_turn(tid: int, username: str, question: str, audio_path: str,
                image_paths: Optional[list[str]] = None,
                from_backlog: bool = False, video_path: str = "") -> None:
    """Runs in a background thread so the browser never waits on a sleeping PC."""
    image_paths = image_paths or []
    channel = "photo" if image_paths else ("voice" if audio_path else "web")
    try:
        # 1. If she recorded or sent a photo, the desktop has to be up before we
        #    can do anything at all with it.
        if audio_path or image_paths:
            st = core.backend_status()
            if not st["ready"]:
                set_work(tid, "sleeping", "The computer is having a nap.")
                set_work(tid, "waking", "Waking up the computer...")
                st = core.ensure_awake()
                if not st["ready"]:
                    # Do NOT fail. Keep it and answer when the desktop next
                    # comes on. She is 2,500 miles away and has no idea whether
                    # a machine in New York is switched on; being told "the
                    # computer didn't wake up" teaches her the app is
                    # unreliable, and she stops using it.
                    if from_backlog:
                        raise RuntimeError("desktop still unreachable")
                    backlog.add(tid, username, question, audio_path, image_paths)
                    core.add_message(
                        tid, "assistant",
                        "## I've got it \u2014 I'll answer shortly\n\n"
                        "The computer that does the thinking is switched off "
                        "right now. **I have saved everything you sent**, "
                        "including your recording and pictures.\n\n"
                        "As soon as it comes back on I will work on this and "
                        "the answer will be here waiting for you. You do not "
                        "need to send it again.",
                        flags=["queued"])
                    set_work(tid, "queued_offline")
                    return

            # Hibernation empties the GPU, so after every wake the models have to
            # be loaded again. Start that now, in the background: transcription
            # below takes several seconds and does not touch them, so the load
            # happens in time that was going to be spent waiting anyway.
            router.prewarm()

        if audio_path:
            set_work(tid, "listening", "Listening to your recording...")
            question = (core.transcribe(Path(audio_path)) or "").strip()
            if not question and not image_paths:
                raise RuntimeError("I couldn't hear anything in that recording.")

        # Before anything else. Not after the safety screen, not after the
        # model -- an assistant that starts reading out first aid while someone
        # is not breathing is worse than no assistant at all.
        urgent = guides_book.emergency_check(question)
        if urgent:
            core.add_message(tid, "user", question[:2000])
            core.add_message(tid, "assistant", urgent, flags=["emergency"])
            core.raise_alert(username, "emergency",
                             f"Possible emergency. She said: {question[:400]}", tid)
            set_work(tid, "done")
            return

        clean, redacted = safety.redact(question)
        if redacted:
            core.raise_alert(username, "redacted",
                             "A number that looked like a card or code was removed "
                             "before saving.", tid)

        core.add_message(tid, "user", clean, audio_path=audio_path, video_path=video_path)
        th = core.get_thread(tid)
        if th and th["title"] in ("New question", "Voice question"):
            core.touch_thread(tid, title=clean[:80])

        # A ticket is opened by CODE, unconditionally, before the model is asked
        # anything. If every model call below fails, the record still exists.
        ticket_no = tickets.ensure_ticket(tid)

        # 2. Safety screen BEFORE the model sees anything.
        s = safety.screen_user(clean, helper=core.HELPER_NAME)
        if s.blocked:
            core.add_message(tid, "assistant", s.replacement or "", flags=s.flags)
            core.raise_alert(username, "scam_blocked",
                             f"Blocked ({s.reason}). She said: {clean[:400]}", tid)
            set_work(tid, "done")
            return
        if s.flags:
            core.raise_alert(username, "flagged",
                             f"{s.reason}: {clean[:300]}", tid)

        # 2b. ALREADY SOLVED? Answer from the knowledge base and stop here.
        #
        # This sits above the wake, not below it, and that placement is the
        # whole value of the feature. Below it, a written answer would still
        # cost a wake-from-hibernate and several seconds of staring at
        # "Waking up the computer..." for a question whose answer has been
        # sitting in a file since March. Above it, the answer is on screen
        # immediately and the desktop is never disturbed at all.
        #
        # Deliberately NOT taken when she sent a photo or a recording: the
        # picture is the question, and matching on the words around it would
        # answer something she did not ask. (A recording has been transcribed
        # by this point, so `question` is real text -- but the transcription
        # already required the desktop, so there is nothing left to save.)
        #
        # `direct_answer` requires BOTH a strong match and an article you have
        # explicitly marked as safe to send verbatim. See kb.py.
        if not image_paths and not audio_path:
            try:
                hit = kb_mod.direct_answer(clean)
            except Exception as e:  # noqa: BLE001
                # A knowledge base that cannot be read is a missing nicety, not
                # a reason she gets no answer. Fall through to the model.
                core.log("warn", "kb", f"lookup skipped: {e}")
                hit = None

            if hit:
                article = hit["article"]
                reply = kb_mod.render(article)
                # Screened on the way out exactly like a model's answer would
                # be. An article is hand-written and trusted, but "trusted" is
                # a claim about the day it was written, and the screen costs
                # nothing.
                a = safety.screen_assistant(reply, helper=core.HELPER_NAME)
                if a.blocked:
                    core.raise_alert(
                        username, "assistant_blocked",
                        f"A knowledge base article was blocked by the safety "
                        f"screen ({a.reason}): {article['title']}", tid)
                    core.log("warn", "kb", f"article blocked: {article['id']}")
                else:
                    core.add_message(tid, "assistant", reply,
                                     flags=(a.flags or []) + ["kb"],
                                     model=f"kb:{article['id']}")
                    core.log("info", "kb",
                             f"{ticket_no} answered from the knowledge base: "
                             f"{article['id']} (score {hit['score']:.2f}, no model call)")
                    set_work(tid, "done")
                    return
                # Blocked: fall through and let the model answer normally.

        # 3. Wake the desktop if we haven't already.
        st = core.backend_status()
        if not st["ready"]:
            set_work(tid, "waking", "Waking up the computer...")
            st = core.ensure_awake()
            if not st["ready"]:
                if from_backlog:
                    raise RuntimeError("desktop still unreachable")
                backlog.add(tid, username, clean, audio_path, image_paths)
                core.add_message(
                    tid, "assistant",
                    "## I've got it \u2014 I'll answer shortly\n\n"
                    "The computer that does the thinking is switched off right "
                    "now. **I have saved your question.** As soon as it comes "
                    "back on I will work on it and the answer will be here "
                    "waiting for you.",
                    flags=["queued"])
                set_work(tid, "queued_offline")
                return
            router.prewarm()

        # 3b. If she sent photos, something has to actually look at them first.
        #     The vision model only REPORTS what it sees; the reasoning model,
        #     which is the one holding the safety rules, decides what to do
        #     about it. See router.describe_image for why that split matters.
        seen = ""
        if image_paths:
            set_work(tid, "looking", "Looking at your picture...")
            seen = router.describe_image(image_paths, question, username, tid)
            if seen:
                question = (f"{question}\n\n[What is in the picture she sent, as "
                            f"described by the vision model:\n{seen}\n]").strip()
                core.add_message(tid, "user", f"_(sent {len(image_paths)} picture"
                                              f"{'s' if len(image_paths) > 1 else ''})_",
                                 flags=["photo"])
            else:
                question = (question or "").strip() or (
                    "I sent a picture but it could not be read.")

        system, ver = build_system_prompt(username, question)

        # 3c. TRIAGE: gather more before committing to a full answer -- up to
        # core.TRIAGE_ROUNDS clarifying questions, not "however many it
        # takes" (see that constant's own comment for why bounded). Skipped
        # entirely for a photo or a recording turn: the picture or the
        # recording already IS the detail, same reasoning "tools only on a
        # typed question" below already uses. Also skipped once a real
        # answer has already been given at least once in this thread --
        # this is about the FIRST answer being well-informed, not about
        # re-triaging every follow-up afterward.
        if channel == "web" and not seen:
            already_answered = any(
                m["role"] == "assistant" and "triage" not in core.jload(m["flags"] or "[]", [])
                for m in core.list_messages(tid))
            asked_so_far = sum(
                1 for m in core.list_messages(tid)
                if m["role"] == "assistant" and "triage" in core.jload(m["flags"] or "[]", []))
            if not already_answered and asked_so_far < core.TRIAGE_ROUNDS:
                set_work(tid, "thinking", "Working out what to ask...")
                q = _triage_question(system, tid)
                if q:
                    core.add_message(tid, "assistant", q, flags=["triage"])
                    set_work(tid, "done")
                    return

        # 4. Ask the model, with the recent thread for context.
        set_work(tid, "thinking", "Working out the answer...")
        # Classify once per ticket. A wrong label is cosmetic; a crash here must
        # not stop her getting an answer, so it is inside its own try.
        if not (th and th["category"] and th["category"] != "other"):
            try:
                meta = tickets.classify(tid, clean)
                if meta.get("urgent"):
                    core.raise_alert(username, "urgent",
                                     f"{ticket_no}: {meta.get('title', '')}", tid)
            except Exception as e:  # noqa: BLE001
                core.log("warn", "ticket", f"classify skipped: {e}")

        history = core.list_messages(tid)[-(core.HISTORY_TURNS * 2):]
        msgs = [{"role": "system", "content": system}]
        for m in history:
            if m["role"] in ("user", "assistant"):
                msgs.append({"role": m["role"], "content": m["content"]})
        if seen:
            msgs.append({"role": "user", "content": question})
        # TOOLS ONLY ON A TYPED QUESTION.
        #
        # Not on a photo or a recording. The picture is the question there, and
        # a tool loop would send the model off searching the words around it --
        # answering something she did not ask, slowly. Vision turns are also
        # already the slowest path in the app; adding two round trips to them
        # is the wrong place to spend twenty seconds.
        reply = router.run(msgs, role="reason", temperature=0.3,
                           username=username, thread_id=tid,
                           intent=(th["category"] if th else "") or "",
                           channel=channel,
                           use_tools=not image_paths and not audio_path)

        # 5. Safety screen the model's own output.
        a = safety.screen_assistant(reply, helper=core.HELPER_NAME)
        if a.blocked:
            core.raise_alert(username, "assistant_blocked",
                             f"The assistant {a.reason}. Reply was replaced. "
                             f"She had asked: {clean[:300]}", tid)
            reply = a.replacement or reply

        core.add_message(tid, "assistant", reply, flags=(a.flags or s.flags),
                         kb_version=ver, model=core.OLLAMA_MODEL)
        set_work(tid, "done")

        threading.Thread(target=learn_from_turn, args=(username, clean, reply),
                         daemon=True).start()

    except Exception as e:  # noqa: BLE001
        core.log("error", f"thread:{tid}", f"{type(e).__name__}: {e}")
        core.add_message(
            tid, "assistant",
            "## Sorry — something went wrong on my end\n\n"
            "This isn't anything you did. The computer that does the thinking "
            f"may have been asleep or busy.\n\n**Please try asking again.** "
            f"If it happens twice in a row, let {core.HELPER_NAME} know.",
            flags=["error"])
        set_work(tid, "error", str(e)[:200])


def learn_from_turn(username: str, question: str, answer: str) -> None:
    """Pull durable facts out of the exchange so she never repeats herself."""
    try:
        prompt = (
            "From the exchange below, extract ONLY durable facts about this person's "
            "devices, accounts, or setup that would be useful months from now.\n"
            "Good: phone model, computer make, printer model, browser, email provider, "
            "internet provider, recurring problem.\n"
            "Ignore: the specific problem being solved today, anything temporary, "
            "anything about how she feels, anything sensitive.\n"
            "Return ONLY a JSON object of short key/value strings, at most 4 keys. "
            "Return {} if there is nothing durable.\n\n"
            f"SHE SAID:\n{question[:1500]}\n\nYOU REPLIED:\n{answer[:1500]}")
        out = core.chat([{"role": "system",
                          "content": "You extract structured facts. Output JSON only."},
                         {"role": "user", "content": prompt}], temperature=0.1)
        import json
        s, e = out.find("{"), out.rfind("}")
        if s == -1 or e <= s:
            return
        facts = json.loads(out[s:e + 1])
        for k, v in list(facts.items())[:4]:
            if isinstance(v, str) and 0 < len(v) < 300 and len(str(k)) < 60:
                core.remember(username, str(k), v)
    except Exception:
        pass  # memory is a nicety, never worth surfacing an error for


def _close_ticket(tid: int, escalated: bool = False, sync_to_otobo: bool = True) -> None:
    """Document, publish an FAQ entry, index it, and email the record.

    `sync_to_otobo=False` is for the one caller where pushing the status
    back to OTOBO would be pointless and dangerous: /internal/otobo-status,
    which runs THIS function because OTOBO already changed. Pushing that
    status back to OTOBO would be a no-op at best and, if the Invoker that
    called us is also listening for OTOBO's own update event, a ping-pong
    loop at worst -- OTOBO tells Mom, Mom tells OTOBO, OTOBO tells Mom again.
    """
    try:
        tickets.document_and_close(tid, escalated=escalated)
        # The resolution becomes searchable memory the moment it is verified.
        # Both are best-effort by design: a failure here must never turn a ticket
        # that was successfully closed into an error she sees.
        try:
            memory.index_ticket(tid)
        except Exception as e:  # noqa: BLE001
            core.log("warn", f"ticket:{tid}", f"could not index: {e}")
        # UPDATES the ticket that was mirrored in when it opened -- does not
        # create a second one. See otobo.sync_close()'s own docstring; this
        # used to be the queue()+flush() pair that create a ticket, which was
        # correct before tickets.ensure_ticket() started mirroring at open
        # time too, and became a silent duplicate the moment it did.
        if sync_to_otobo:
            otobo.sync_close(tid, escalated)
        try:
            # Straight to your own machine, with the photos. Not woken for --
            # the desktop is usually asleep and the nightly job retries.
            webhook.queue(tid)
            webhook.flush(limit=5)
        except Exception as e:  # noqa: BLE001
            core.log("warn", f"ticket:{tid}", f"webhook deferred: {e}")
        if escalated:
            core.add_message(
                tid, "assistant",
                "## I've passed this to " + core.HELPER_NAME + "\n\n"
                "I couldn't sort it out, so I've sent him everything we tried "
                "so far. He'll have the whole conversation, so you won't need "
                "to explain it again.\n\n"
                "There's nothing else you need to do.", flags=["escalated"])
        else:
            core.add_message(
                tid, "assistant",
                "## All sorted\n\nI've written this one up and saved it, so if it "
                "happens again you can look it up instead of starting over. "
                "You'll find it under **Answers**.", flags=["closed"])
    except Exception as e:  # noqa: BLE001
        core.log("error", f"ticket:{tid}", f"close failed: {e}")
    finally:
        set_work(tid, "done")


@app.post("/t/{tid}/resolve")
def resolve(request: Request, tid: int, worked: str = Form("yes")):
    user = require_write(request)
    th = core.get_thread(tid)
    if not th or th["username"] != user["username"]:
        raise HTTPException(404, "That conversation doesn't exist.")
    if worked == "yes":
        set_work(tid, "closing", "Writing this up...")
        threading.Thread(target=_close_ticket, args=(tid,), daemon=True).start()
        return RedirectResponse(f"{core.ROOT_PATH}/t/{tid}", status_code=303)

    # Two failed attempts is enough. Escalate rather than loop her forever.
    attempts = sum(1 for m in core.list_messages(tid)
                   if m["role"] == "user" and "did not work" in m["content"].lower())
    if attempts >= 1:
        set_work(tid, "closing", "Passing this to " + core.HELPER_NAME + "...")
        threading.Thread(target=_close_ticket, args=(tid, True), daemon=True).start()
        return RedirectResponse(f"{core.ROOT_PATH}/t/{tid}", status_code=303)

    set_work(tid, "queued", "Trying a different way...")
    threading.Thread(
        target=handle_turn,
        args=(tid, user["username"],
              "That did not work. Please try explaining it a different way, and "
              "check whether I misunderstood something. Ask me what I saw on the "
              "screen if that would help.", ""),
        daemon=True).start()
    return RedirectResponse(f"{core.ROOT_PATH}/t/{tid}", status_code=303)


# ---------------------------------------------------------------------- api

@app.get("/api/thread/{tid}")
def api_thread(request: Request, tid: int):
    user = require_user(request)
    th = core.get_thread(tid)
    if not th or (th["username"] != user["username"] and not user["is_admin"]):
        raise HTTPException(404, "not found")
    w = get_work(tid)
    state = w.get("state", "done")
    out = {"state": state, "detail": w.get("detail", ""),
           "waking_seconds": core.waking_elapsed(),
           "messages": len(core.list_messages(tid))}
    if state == "queued_offline":
        out["queue_position"] = backlog.position(tid)
    return out


@app.get("/api/status")
def api_status(request: Request):
    require_user(request)
    return {"backend": core.backend_status(), "waking_seconds": core.waking_elapsed()}


@app.get("/manifest.webmanifest")
def manifest():
    """Lets Android/iOS install this as a real home-screen app with its own icon."""
    r = core.ROOT_PATH
    return JSONResponse({
        "name": core.SITE_NAME, "short_name": core.SITE_NAME,
        "start_url": f"{r}/", "scope": f"{r}/",
        "display": "standalone", "background_color": "#f4f6f5",
        "theme_color": "#0b6b4f",
        "icons": [{"src": f"{r}/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": f"{r}/static/icon-512.png", "sizes": "512x512", "type": "image/png",
                   "purpose": "any maskable"}],
    }, media_type="application/manifest+json")


@app.get("/healthz")
def healthz():
    return {"ok": True}


# -------------------------------------------------------------------- admin

@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, msg: str = ""):
    user = require_admin(request)
    _, kb_ver = core.load_knowledge()
    return templates.TemplateResponse(request, "admin.html", ctx(
        request, user, msg=msg,
        alerts=core.list_alerts(80), users=core.list_users(),
        events=core.recent_events(80), backend=core.backend_status(),
        kb_files=[(p.name, len(p.read_text(encoding="utf-8", errors="replace")))
                  for p in core.knowledge_files()],
        kb_version=kb_ver, models=core.available_models(),
        memories=core.recall(request.query_params.get("who", "mom")),
        who=request.query_params.get("who", "mom"),
        cfg={"desktop_ip": core.DESKTOP_IP, "desktop_mac": core.DESKTOP_MAC,
             "model": core.OLLAMA_MODEL, "root": core.ROOT_PATH}))


@app.post("/admin/alerts/seen")
def admin_alerts_seen(request: Request):
    require_admin(request)
    core.mark_alerts_seen()
    return RedirectResponse(f"{core.ROOT_PATH}/admin?msg=Marked+as+read", status_code=303)


@app.post("/admin/forget")
def admin_forget(request: Request, who: str = Form(...), key: str = Form(...)):
    require_admin(request)
    core.forget(who, key)
    return RedirectResponse(f"{core.ROOT_PATH}/admin?msg=Forgotten", status_code=303)


@app.post("/admin/user")
def admin_user(request: Request, username: str = Form(...), password: str = Form(...),
               display_name: str = Form(""), role: str = Form("user")):
    require_admin(request)
    username = core.normalize_username(username)
    if not core.valid_username(username):
        return RedirectResponse(f"{core.ROOT_PATH}/admin?msg=Bad+username", status_code=303)
    if len(password) < 8:
        return RedirectResponse(f"{core.ROOT_PATH}/admin?msg=Password+too+short",
                                status_code=303)
    core.create_user(username, password, display_name,
                     role if role in ("user", "admin") else "user", must_change=True)
    return RedirectResponse(f"{core.ROOT_PATH}/admin?msg=Saved", status_code=303)


@app.post("/admin/wake")
def admin_wake(request: Request):
    require_admin(request)
    core.send_wol()
    return RedirectResponse(f"{core.ROOT_PATH}/admin?msg=Wake+signal+sent", status_code=303)


# ======================================================= dashboard (Jason) ===
# Everything below is behind require_admin, which is your account. Mom's login
# is role 'user' and cannot reach any of it. She never sees the word "ticket".

def _redir(msg: str, where: str = "/admin") -> RedirectResponse:
    from urllib.parse import quote
    return RedirectResponse(f"{core.ROOT_PATH}{where}?msg={quote(msg)}",
                            status_code=303)


@app.get("/admin/tickets", response_class=HTMLResponse)
def admin_tickets(request: Request, q: str = "", status: str = "",
                  category: str = "", msg: str = ""):
    user = require_admin(request)
    rows = tickets.search_tickets(query=q, status=status, category=category)
    return templates.TemplateResponse(request, "admin_tickets.html", ctx(
        request, user, msg=msg, rows=rows, q=q, status=status, category=category,
        counts=tickets.ticket_counts(), categories=tickets.CATEGORIES))


@app.get("/admin/ticket/{tid}", response_class=HTMLResponse)
def admin_ticket(request: Request, tid: int, msg: str = ""):
    user = require_admin(request)
    th = core.get_thread(tid)
    if not th:
        raise HTTPException(404, "No such ticket.")
    return templates.TemplateResponse(request, "admin_ticket.html", ctx(
        request, user, msg=msg, t=th, messages=core.list_messages(tid),
        steps=core.jload(th["steps"] or "[]", []),
        categories=tickets.CATEGORIES,
        draft=DRAFTS.get(tid, ""),
        routes=core.conn().execute(
            "SELECT * FROM routing WHERE thread_id=? ORDER BY ts", (tid,)).fetchall()))


@app.get("/admin/ticket/{tid}/video/{mid}")
def admin_video(request: Request, tid: int, mid: int):
    """A video she attached, for Jason's own eyes only -- no route anywhere
    else in this app serves it, and this one is require_admin, not merely
    require_user. See core.VIDEO_EXT's own comment for why."""
    require_admin(request)
    m = core.get_message(mid)
    if not m or m["thread_id"] != tid or not m["video_path"]:
        raise HTTPException(404, "No video on that message.")
    path = Path(m["video_path"])
    if not path.is_file():
        raise HTTPException(404, "That video is gone.")
    return FileResponse(path)


def _reopen_ticket(tid: int) -> None:
    with core.tx() as c:
        c.execute("UPDATE threads SET status='open', resolved=0, closed_at=NULL "
                  "WHERE id=?", (tid,))
    # A reopened ticket is no longer a verified resolution, so it must stop
    # being offered as one. Leaving it indexed is how a fix you already know
    # did not work gets recommended again next week.
    try:
        memory.drop("ticket", tid)
    except Exception:
        pass


@app.post("/admin/ticket/{tid}/reopen")
def admin_reopen(request: Request, tid: int):
    require_admin(request)
    _reopen_ticket(tid)
    return _redir("Reopened and removed from memory", f"/admin/ticket/{tid}")


@app.post("/admin/ticket/{tid}/close")
def admin_close(request: Request, tid: int):
    require_admin(request)
    threading.Thread(target=_close_ticket, args=(tid, False), daemon=True).start()
    return _redir("Closing and documenting in the background",
                  f"/admin/ticket/{tid}")


@app.post("/admin/ticket/{tid}/pending")
def admin_pending(request: Request, tid: int):
    """Web twin of Telegram's pending button -- see internal_set_pending()'s
    own docstring for what 'pending' means here."""
    require_admin(request)
    th = core.get_thread(tid)
    if not th:
        return _redir("That ticket is gone.", "/admin/tickets")
    if th["status"] == "closed":
        return _redir("Closed -- reopen it first.", f"/admin/ticket/{tid}")
    with core.tx() as c:
        c.execute("UPDATE threads SET status='pending' WHERE id=?", (tid,))
    return _redir("Marked pending", f"/admin/ticket/{tid}")


# ---------------------------------------------------------- illustrations ---
#
# The admin twin of /t/{tid}/msg/{mid}/draw -- same core.draw(), but with an
# optional prompt override (Jason writing his own description rather than
# drawing the message text verbatim) and a way to take one back off, neither
# of which the plain reader-facing button needs.

@app.post("/admin/ticket/{tid}/msg/{mid}/draw")
def admin_draw(request: Request, tid: int, mid: int, prompt: str = Form("")):
    require_admin(request)
    m = core.get_message(mid)
    if not m or m["thread_id"] != tid:
        return _redir("That message is gone.", f"/admin/ticket/{tid}")
    try:
        png = core.draw(prompt.strip() or m["content"])
    except core.ImagineError as e:
        return _redir(f"Couldn't draw one: {e}", f"/admin/ticket/{tid}")
    core.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{mid}_{secrets.token_hex(4)}.png"
    (core.GENERATED_DIR / name).write_bytes(png)
    core.set_message_image(mid, name)
    return _redir("Drawn", f"/admin/ticket/{tid}")


@app.post("/admin/ticket/{tid}/msg/{mid}/draw/remove")
def admin_draw_remove(request: Request, tid: int, mid: int):
    require_admin(request)
    m = core.get_message(mid)
    if not m or m["thread_id"] != tid:
        return _redir("That message is gone.", f"/admin/ticket/{tid}")
    if m["image_path"]:
        (core.GENERATED_DIR / m["image_path"]).unlink(missing_ok=True)
        core.set_message_image(mid, "")
    return _redir("Picture removed", f"/admin/ticket/{tid}")


# ------------------------------------------------------- drafting a fix -----
#
# The web twin of Telegram's "🛠 Work on it" flow -- same underlying
# linkfix.py, same draft-then-send shape, so a ticket can be worked from
# whichever is in your hand. In memory only, same tradeoff as WORK above:
# a restart loses an unconfirmed draft, which costs pressing the button
# again, not data.
DRAFTS: dict[int, str] = {}


@app.post("/admin/ticket/{tid}/suggest")
def admin_suggest_fix(request: Request, tid: int, link: str = Form("")):
    require_admin(request)
    link = link.strip()
    try:
        text = linkfix.from_link(link, core.get_thread(tid)["title"]) if link \
            else linkfix.from_transcript(tid)
    except linkfix.LinkFixError as e:
        return _redir(f"Couldn't draft one: {e}", f"/admin/ticket/{tid}")
    DRAFTS[tid] = text
    return RedirectResponse(f"{core.ROOT_PATH}/admin/ticket/{tid}", status_code=303)


@app.post("/admin/ticket/{tid}/apply-fix")
def admin_apply_fix(request: Request, tid: int, also_close: str = Form("")):
    require_admin(request)
    text = DRAFTS.pop(tid, "")
    if not text:
        return _redir("That draft is gone -- ask again.", f"/admin/ticket/{tid}")
    core.add_message(tid, "assistant", text, flags=["from_jason"])
    if also_close:
        threading.Thread(target=_close_ticket, args=(tid, False), daemon=True).start()
        return _redir("Sent, and closing in the background.", f"/admin/ticket/{tid}")
    return _redir("Sent -- she'll see it next time she opens /mama.",
                  f"/admin/ticket/{tid}")


@app.post("/admin/ticket/{tid}/discard-fix")
def admin_discard_fix(request: Request, tid: int):
    require_admin(request)
    DRAFTS.pop(tid, None)
    return _redir("Discarded.", f"/admin/ticket/{tid}")


# ---------------------------------------------------------- reverse sync ----
#
# OTOBO -> Mom. The other direction (Mom -> OTOBO, when a ticket opens or
# closes here) is otobo.py's job. This is for the case OTOBO exists to solve
# in the first place: you resolve a ticket by clicking around in OTOBO's own
# UI, or an Invoker fires on some other status change, and Mama should hear
# about it back through /mama without you doing anything twice.
#
# PROTECTED DIFFERENTLY FROM /from-ticket ON THE KB SERVICE, not the same
# way. kb's Caddy block only matches /mama/kb/*, so a bare /from-ticket
# request never reaches it -- Mom's own block is `handle_path /mama/*`,
# which forwards everything under /mama, including this route in theory.
# In practice a request with no valid site session gets bounced by
# forward_auth before it ever reaches this container, same as every other
# page here -- but OTOBO's Invoker calls this directly on the Docker
# network (http://mom:8001/internal/otobo-status), the same way otobo.py
# calls OUT to OTOBO, which never touches Caddy or forward_auth either
# way. The shared secret is what stops another container on that same
# internal network from spoofing this call; refuses every request outright
# if it is not set, rather than defaulting open the way kb's
# TICKET_SECRET does.
OTOBO_SYNC_SECRET = os.getenv("MOM_OTOBO_SYNC_SECRET_KEY", "").strip()


@app.post("/internal/otobo-status")
async def otobo_status_webhook(request: Request):
    if not OTOBO_SYNC_SECRET:
        raise HTTPException(403, "MOM_OTOBO_SYNC_SECRET_KEY is not set")
    got = request.headers.get("X-Otobo-Secret", "")
    if not hmac.compare_digest(got, OTOBO_SYNC_SECRET):
        raise HTTPException(403, "no")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "that was not JSON")

    # Shape matches what OTOBO.md documents configuring the Invoker to send
    # -- checked against nothing live yet, since the Invoker itself is still
    # a manual setup step. If OTOBO's own Mapping ends up sending something
    # else, this is the one place to adjust to match it.
    remote_id = str(body.get("TicketID") or "")
    state = str(body.get("State") or "").strip().lower()
    tid = otobo.thread_for_remote(remote_id)
    if not tid:
        return {"ok": False, "why": "no local ticket mirrored to that OTOBO id"}

    th = core.get_thread(tid)
    if not th:
        return {"ok": False, "why": "local ticket record is gone"}

    if "closed" in state:
        if th["status"] in ("closed", "escalated"):
            return {"ok": True, "action": "none", "why": "already closed here"}
        # sync_to_otobo=False -- see _close_ticket()'s own docstring for why
        # pushing this status back to OTOBO here would be pointless at best
        # and a ping-pong loop at worst.
        threading.Thread(target=_close_ticket, args=(tid, False, False),
                         daemon=True).start()
        return {"ok": True, "action": "closing"}

    if state in ("open", "new") and th["status"] in ("closed", "escalated"):
        _reopen_ticket(tid)
        return {"ok": True, "action": "reopened"}

    return {"ok": True, "action": "none", "why": f"state {state!r} needs no change here"}


# --------------------------------------------------------- ticket API -------
#
# For Telegram's /tickets command and its resolve/close buttons -- a second,
# separate secret from OTOBO_SYNC_SECRET above, on purpose. Different caller,
# different container, no reason a leak of one has to mean rotating both.
# Same reachability story as /internal/otobo-status: nominally under
# /mama/*, but forward_auth bounces anything with no real site session
# before it gets here, and the actual caller (the telegram_bridge container)
# reaches this directly at http://mom:8001/internal/tickets/..., never
# through Caddy.
TICKET_API_SECRET = os.getenv("MOM_TICKET_API_SECRET_KEY", "").strip()


def _ticket_api_gate(request: Request) -> None:
    if not TICKET_API_SECRET:
        raise HTTPException(403, "MOM_TICKET_API_SECRET_KEY is not set")
    got = request.headers.get("X-Ticket-Api-Secret", "")
    if not hmac.compare_digest(got, TICKET_API_SECRET):
        raise HTTPException(403, "no")


@app.get("/internal/tickets/open")
def internal_open_tickets(request: Request):
    """Every open or escalated ticket, newest first -- what /tickets in
    Telegram actually lists. Kept intentionally small (id, number, title,
    category, status, who): this is a queue to pick from, not the full
    conversation, which stays one tap away at /admin/ticket/{tid}."""
    _ticket_api_gate(request)
    rows = core.conn().execute(
        "SELECT id, ticket_no, title, category, status, username "
        "FROM threads WHERE status IN ('open','escalated','pending') "
        "ORDER BY id DESC LIMIT 30").fetchall()
    return {"tickets": [dict(r) for r in rows]}


@app.post("/internal/tickets/{tid}/close")
def internal_close_ticket(request: Request, tid: int):
    _ticket_api_gate(request)
    th = core.get_thread(tid)
    if not th:
        return {"ok": False, "why": "no such ticket"}
    if th["status"] in ("closed", "escalated"):
        return {"ok": False, "why": f"already {th['status']}"}
    # sync_to_otobo=True (the default) here, unlike the reverse-sync webhook
    # above -- this close ORIGINATES here, in Telegram, so OTOBO genuinely
    # needs to be told, not echoed back to.
    threading.Thread(target=_close_ticket, args=(tid, False), daemon=True).start()
    return {"ok": True, "action": "closing", "title": th["title"]}


@app.post("/internal/tickets/{tid}/reopen")
def internal_reopen_ticket(request: Request, tid: int):
    """Back to 'open' from anything else -- closed, escalated, or pending.
    _reopen_ticket() drops the ticket from memory's resolved-fixes index,
    which is a safe no-op for a ticket that was never indexed (pending, or
    escalated-but-not-yet-closed), so one function covers all three."""
    _ticket_api_gate(request)
    th = core.get_thread(tid)
    if not th:
        return {"ok": False, "why": "no such ticket"}
    if th["status"] == "open":
        return {"ok": False, "why": "already open"}
    _reopen_ticket(tid)
    return {"ok": True, "action": "reopened", "title": th["title"]}


@app.post("/internal/tickets/{tid}/pending")
def internal_set_pending(request: Request, tid: int):
    """Waiting on something -- Jason, a part, a callback -- distinct from
    'open' (actively being worked) and 'closed' (done). Still counts as
    unresolved: internal_open_tickets() above lists it, and it is not
    touched by memory's resolved-fixes index (that only ever happens on a
    real close)."""
    _ticket_api_gate(request)
    th = core.get_thread(tid)
    if not th:
        return {"ok": False, "why": "no such ticket"}
    if th["status"] == "closed":
        return {"ok": False, "why": "closed -- reopen it first"}
    if th["status"] == "pending":
        return {"ok": False, "why": "already pending"}
    with core.tx() as c:
        c.execute("UPDATE threads SET status='pending' WHERE id=?", (tid,))
    return {"ok": True, "action": "pending", "title": th["title"]}


@app.post("/internal/tickets/{tid}/suggest")
async def internal_suggest_fix(request: Request, tid: int):
    """Draft a fix -- from a link Jason pasted, or from the ticket's own
    transcript if not. NEVER WRITES ANYTHING. This only drafts; the draft
    only reaches her once /internal/tickets/{tid}/apply-fix is called with
    the (possibly edited) text, which is Telegram's job, not this route's --
    Jason gets to read it first, same as every other draft-then-send shape
    already in this app (extras.py's suggestions, the ticket documentation
    itself before it publishes)."""
    _ticket_api_gate(request)
    th = core.get_thread(tid)
    if not th:
        return {"ok": False, "why": "no such ticket"}

    try:
        body = await request.json()
    except Exception:
        body = {}
    link = str(body.get("link") or "").strip()

    try:
        if link:
            text = await asyncio.to_thread(linkfix.from_link, link, th["title"])
        else:
            text = await asyncio.to_thread(linkfix.from_transcript, tid)
    except linkfix.LinkFixError as e:
        return {"ok": False, "why": str(e)}
    except Exception as e:  # noqa: BLE001
        core.log("error", f"ticket:{tid}", f"suggest failed: {e}")
        return {"ok": False, "why": f"{type(e).__name__}: {e}"}
    return {"ok": True, "text": text}


@app.post("/internal/tickets/{tid}/apply-fix")
async def internal_apply_fix(request: Request, tid: int):
    """Commit a drafted fix as a message in HER conversation -- the one and
    only way anything from this flow reaches Mama, and it reaches her
    exactly the way every other assistant reply does: through /mama, no
    second login, no separate inbox to check. Does not close the ticket;
    that stays a deliberate, separate action (the existing Close button)."""
    _ticket_api_gate(request)
    th = core.get_thread(tid)
    if not th:
        return {"ok": False, "why": "no such ticket"}

    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "why": "that was not JSON"}
    text = str(body.get("text") or "").strip()
    if not text:
        return {"ok": False, "why": "no text to send"}

    core.add_message(tid, "assistant", text, flags=["from_jason"])
    return {"ok": True, "title": th["title"]}


# ------------------------------------------------------------------- FAQ ----

@app.get("/admin/faq", response_class=HTMLResponse)
def admin_faq(request: Request, q: str = "", msg: str = ""):
    user = require_admin(request)
    return templates.TemplateResponse(request, "admin_faq.html", ctx(
        request, user, msg=msg, q=q,
        rows=tickets.list_faq(published_only=False, query=q),
        categories=tickets.CATEGORIES))


@app.post("/admin/faq/{fid}")
def admin_faq_save(request: Request, fid: int, question: str = Form(...),
                   answer: str = Form(...), category: str = Form("other"),
                   published: str = Form("")):
    require_admin(request)
    tickets.update_faq(fid, question, answer, category, bool(published))
    try:
        memory.index_faq(fid)          # keep the vector in step with the edit
    except Exception:
        pass
    return _redir("Saved", "/admin/faq")


@app.post("/admin/faq/{fid}/delete")
def admin_faq_delete(request: Request, fid: int):
    require_admin(request)
    tickets.delete_faq(fid)
    try:
        memory.drop("faq", fid)
    except Exception:
        pass
    return _redir("Deleted", "/admin/faq")


# ------------------------------------------------------- OTOBO hand-off -----

@app.get("/admin/otobo")
def admin_otobo_handoff(request: Request):
    """One click into OTOBO, already signed in. See otobo_handoff.py's own
    docstring for the how and why -- short version: it is you, and only
    you, because OTOBO has no way to provision anyone else automatically."""
    require_admin(request)
    site_cookie = request.cookies.get(otobo_handoff.SITE_COOKIE, "")
    try:
        set_cookie = otobo_handoff.mint(site_cookie)
    except otobo_handoff.HandoffError as e:
        return _redir(str(e), "/admin/models")
    response = RedirectResponse(
        f"{otobo_handoff.OTOBO_PUBLIC}{otobo_handoff.LOGIN_PATH}", status_code=303)
    response.headers.append("set-cookie", set_cookie)
    return response


# ---------------------------------------------------------------- models ----

@app.get("/admin/models", response_class=HTMLResponse)
def admin_models(request: Request, days: int = 7, msg: str = ""):
    user = require_admin(request)
    return templates.TemplateResponse(request, "admin_models.html", ctx(
        request, user, msg=msg, days=days,
        health=router.health(), usage=router.usage(days),
        recent=router.recent(60), backend=core.backend_status(),
        profile=router.PROFILE, mem=memory.stats(),
        pepper=otobo.status(), pepper_pending=otobo.pending(),
        runs=distill.history(10)))


@app.post("/admin/models/prewarm")
def admin_prewarm(request: Request):
    require_admin(request)
    if not core.backend_status()["ready"] and core.AUTO_WAKE:
        threading.Thread(target=core.ensure_awake, daemon=True).start()
    router.prewarm()
    return _redir("Loading the models in the background", "/admin/models")


@app.post("/admin/models/reindex")
def admin_reindex(request: Request):
    require_admin(request)
    threading.Thread(target=memory.reindex_all, daemon=True).start()
    return _redir("Rebuilding the memory index in the background", "/admin/models")


@app.post("/admin/models/otobo-sync")
def admin_pepper_sync(request: Request):
    require_admin(request)
    r = otobo.flush(limit=100)
    return _redir(f"OTOBO: {r['sent']} sent, {r['failed']} failed",
                  "/admin/models")


# ------------------------------------------------------------ known fixes ---

@app.get("/admin/kb", response_class=HTMLResponse)
def admin_kb(request: Request, q: str = "", ask: str = ""):
    """Read-only. The writing happens in the site admin's slipstream page.

    Two things this page is for, and neither is editing:

      1. proving the file arrived. "I added an article and nothing changed" has
         two causes — the mount and the match — and they need different fixes.
      2. the TRY IT box, which is the useful half. It runs the real matcher
         against a question you type and shows every article's score next to
         the two thresholds, so tuning a symptom line is a ten-second loop
         instead of guesswork against a live conversation.
    """
    user = require_admin(request)
    st = kb_mod.stats()
    articles = kb_mod.search(q) if q.strip() else kb_mod.load()

    tried = None
    if ask.strip():
        asked = kb_mod._words(ask)
        scored = sorted(((kb_mod.score(a, asked), a) for a in kb_mod.load()),
                        key=lambda p: -p[0])
        hit = kb_mod.direct_answer(ask)
        tried = {
            "question": ask,
            "scores": [{"article": a, "score": s} for s, a in scored[:8]],
            # What would ACTUALLY happen to this question, in the same words
            # the code uses, so the page cannot drift from the behaviour.
            "outcome": ("answered from the knowledge base, with no model call"
                        if hit else
                        "handed to the model, with the top articles as context"
                        if scored and scored[0][0] >= kb_mod.CONTEXT_SCORE else
                        "answered by the model alone — nothing here is relevant"),
            "direct_id": hit["article"]["id"] if hit else "",
        }

    return templates.TemplateResponse(request, "admin_kb.html", ctx(
        request, user, q=q, ask=ask, articles=articles, tried=tried, st=st,
        direct_score=kb_mod.DIRECT_SCORE, context_score=kb_mod.CONTEXT_SCORE))


# --------------------------------------------------------------- learned ----

@app.get("/admin/learned", response_class=HTMLResponse)
def admin_learned(request: Request, q: str = "", msg: str = ""):
    user = require_admin(request)
    return templates.TemplateResponse(request, "admin_learned.html", ctx(
        request, user, msg=msg, q=q, notes=distill.list_notes(q),
        runs=distill.history(15), mem=memory.stats(),
        learned_dir=str(distill.LEARNED_DIR)))


@app.post("/admin/learned/run")
def admin_distill_run(request: Request):
    require_admin(request)
    threading.Thread(target=distill.run_nightly, daemon=True).start()
    return _redir("Running now — this wakes the desktop and takes a few minutes",
                  "/admin/learned")


@app.post("/admin/learned/{nid}/toggle")
def admin_note_toggle(request: Request, nid: int):
    require_admin(request)
    with core.tx() as c:
        c.execute("UPDATE learned_notes SET approved = 1 - approved WHERE id=?", (nid,))
    distill.write_learned_files()
    return _redir("Updated", "/admin/learned")


@app.post("/admin/learned/{nid}/delete")
def admin_note_delete(request: Request, nid: int):
    require_admin(request)
    with core.tx() as c:
        c.execute("DELETE FROM learned_notes WHERE id=?", (nid,))
    try:
        memory.drop("learned", nid)
    except Exception:
        pass
    distill.write_learned_files()
    return _redir("Deleted", "/admin/learned")


@app.post("/admin/learned/reset")
def admin_learned_reset(request: Request, confirm: str = Form("")):
    require_admin(request)
    if confirm.strip().upper() != "RESET":
        return _redir("Not reset — type RESET to confirm", "/admin/learned")
    r = distill.reset_learned()
    return _redir(f"Reset: {r['notes']} notes, {r['vectors']} vectors, "
                  f"{r['files']} files removed", "/admin/learned")


# ================================================== her own past problems ====

@app.get("/history", response_class=HTMLResponse)
def history(request: Request, category: str = "", q: str = ""):
    """What has gone wrong before, and what fixed it.

    Hers, not yours: only her own closed problems, written the way she would
    say them, grouped by the kind of thing it was. She never sees the word
    ticket, a status, or anyone else's records.
    """
    user = require_user(request)
    rows = [r for r in tickets.search_tickets(query=q, category=category, limit=300)
            if r["username"] == user["username"]
            and r["status"] in ("closed", "resolved")]

    groups: dict = {}
    for r in rows:
        groups.setdefault(r["category"] or "other", []).append(r)
    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))

    counts = {k: len(v) for k, v in groups.items()}
    return templates.TemplateResponse(request, "history.html", ctx(
        request, user, groups=ordered, counts=counts, category=category, q=q,
        categories=tickets.CATEGORIES, total=len(rows)))


@app.get("/history/{tid}", response_class=HTMLResponse)
def history_item(request: Request, tid: int):
    user = require_user(request)
    th = core.get_thread(tid)
    if not th or th["username"] != user["username"]:
        raise HTTPException(404, "That isn't one of yours.")

    # "Pull others": the same retrieval the assistant uses, surfaced for her.
    related = []
    try:
        for h in memory.search(f"{th['title']} {th['summary'] or ''}",
                               kinds=("ticket",), k=4):
            if h["ref_id"] == tid:
                continue
            r = core.get_thread(h["ref_id"])
            if r and r["username"] == user["username"]:
                related.append({"t": r, "score": h["score"]})
    except Exception:
        pass

    return templates.TemplateResponse(request, "history_item.html", ctx(
        request, user, t=th, steps=core.jload(th["steps"] or "[]", []),
        messages=core.list_messages(tid), related=related[:3],
        categories=tickets.CATEGORIES))


@app.get("/admin/categories", response_class=HTMLResponse)
def admin_categories(request: Request, msg: str = ""):
    """Everything, grouped by kind, with what actually fixed each one."""
    user = require_admin(request)
    rows = tickets.search_tickets(limit=1000)
    groups: dict = {}
    for r in rows:
        groups.setdefault(r["category"] or "other", []).append(r)
    summary = []
    for key, label in tickets.CATEGORIES.items():
        items = groups.get(key, [])
        closed = [i for i in items if i["status"] in ("closed", "resolved")]
        summary.append({"key": key, "label": label, "items": items,
                        "n": len(items), "closed": len(closed),
                        "open": len(items) - len(closed)})
    summary.sort(key=lambda g: -g["n"])
    return templates.TemplateResponse(request, "admin_categories.html", ctx(
        request, user, msg=msg, groups=summary, total=len(rows)))


# =================================================== the handbook (for her) ==

@app.get("/guides", response_class=HTMLResponse)
def guides_index(request: Request, category: str = "", q: str = ""):
    user = require_user(request)
    rows = guides_book.listing(category=category, query=q)
    groups: dict = {}
    for r in rows:
        groups.setdefault(r["category"], []).append(r)
    return templates.TemplateResponse(request, "guides.html", ctx(
        request, user, rows=rows, groups=sorted(groups.items()),
        counts=guides_book.counts(), categories=guides_book.CATEGORIES,
        category=category, q=q, total=len(rows),
        recent=[r for r in tickets.search_tickets(status="closed", limit=6)
                if r["username"] == user["username"]][:5]))


@app.get("/guides/{gid}", response_class=HTMLResponse)
def guides_item(request: Request, gid: int):
    user = require_user(request)
    g = guides_book.get(gid)
    if not g or not g["published"]:
        raise HTTPException(404, "That page isn't here.")
    related = []
    try:
        for h in memory.search(f"{g['title']} {g['summary']}", kinds=("guide",), k=4):
            if h["ref_id"] != gid:
                o = guides_book.get(h["ref_id"])
                if o and o["published"]:
                    related.append(o)
    except Exception:
        pass
    return templates.TemplateResponse(request, "guide_item.html", ctx(
        request, user, g=g, related=related[:3],
        categories=guides_book.CATEGORIES,
        sensitive=g["category"] in guides_book.SENSITIVE))


# ================================================= the handbook (for you) ====

@app.get("/admin/guides", response_class=HTMLResponse)
def admin_guides(request: Request, q: str = "", category: str = "", msg: str = ""):
    user = require_admin(request)
    return templates.TemplateResponse(request, "admin_guides.html", ctx(
        request, user, msg=msg, q=q, category=category,
        rows=guides_book.listing(category=category, query=q, published_only=False),
        counts=guides_book.counts(published_only=False),
        categories=guides_book.CATEGORIES))


@app.post("/admin/guides/new")
def admin_guide_new(request: Request, title: str = Form(...), body: str = Form(...),
                    category: str = Form("other"), summary: str = Form(""),
                    tags: str = Form(""), published: str = Form(""),
                    pinned: str = Form("")):
    require_admin(request)
    if not title.strip() or not body.strip():
        return _redir("A title and some words are both needed", "/admin/guides")
    guides_book.create(category, title, body, summary, tags,
                       bool(published), bool(pinned))
    return _redir("Added", "/admin/guides")


@app.post("/admin/guides/{gid}")
def admin_guide_save(request: Request, gid: int, title: str = Form(...),
                     body: str = Form(...), category: str = Form("other"),
                     summary: str = Form(""), tags: str = Form(""),
                     published: str = Form(""), pinned: str = Form("")):
    require_admin(request)
    guides_book.update(gid, category, title, body, summary, tags,
                       bool(published), bool(pinned))
    return _redir("Saved", "/admin/guides")


@app.post("/admin/guides/{gid}/delete")
def admin_guide_delete(request: Request, gid: int):
    require_admin(request)
    guides_book.delete(gid)
    return _redir("Deleted", "/admin/guides")


@app.post("/admin/guides/import")
def admin_guide_import(request: Request, text: str = Form(...),
                       category: str = Form("other")):
    require_admin(request)
    n = guides_book.import_markdown(text, category)
    return _redir(f"Added {n} entr{'y' if n == 1 else 'ies'}", "/admin/guides")


@app.post("/admin/guides/reindex")
def admin_guide_reindex(request: Request):
    require_admin(request)
    n = guides_book.reindex_all()
    return _redir(f"Reindexed {n}", "/admin/guides")
