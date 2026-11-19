"""
Hermes over HTTP, so a phone can talk to the same assistant the CLI does.

WHY THIS EXISTS. Hermes has been CLI-plus-Telegram since it was written, and
both of those run *inside* this container. A phone app cannot `docker exec`.
Without this, the Android app talks straight to a bare model through LiteLLM:
no memory, no tools, no idea who it is speaking to. That is a chatbot, not
this house's assistant.

WHO IS TALKING IS NOT THE CALLER'S TO CHOOSE. Identity arrives as
`X-Auth-Email`, the same header-trust pattern Gitea, Paperless and Casey's
career portal already use here. That is safe for exactly one reason: THIS
SERVICE IS NEVER EXPOSED PUBLICLY. It binds inside the docker network and
Caddy has no route to it. The only caller is the auth service, which fills
that header from a validated OIDC access token. If this port were ever
published, anyone could claim to be anyone, and the memory store is per
person -- so that would not be a leak of one thing, it would be a leak of
everything anybody ever told Hermes.

MEMORY IS PER PERSON, AND ABSENT RATHER THAN SHARED. agent.py is explicit
that `memory=None` means no memory at all rather than a common one, because
"a shared memory is worse than none: it puts one person's facts in front of
another with no way to tell." This honours that: an email with no entry in
people.json gets refused, not given a blank store to scribble in.

NO SHELL, NO WRITES, from this surface. The confirm hook refuses every
mutating tool with a sentence the model can repeat back. A phone is the
easiest device in the house to lose, and a lost phone that can run shell
commands on the PC is a different category of problem from a lost phone that
can read a recipe.
"""
from __future__ import annotations

import json
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from shared.identity import by_email
from shared.memory import MemoryStore
from shared.router import Router
from shared.transcript import Transcript

from app import threads

from . import garden_tools
from .agent import Agent

# Same default the rest of the stack uses. One directory per SSO identity.
MEMORY_ROOT = os.environ.get("HERMES_MEMORY_ROOT", "/memory")
PORT = int(os.environ.get("HERMES_HTTP_PORT", "8014"))
# Deliberately NOT 0.0.0.0-with-a-published-port. See the header: this is
# reachable on the docker network and nowhere else.
# Listens on every interface inside its own container; only the docker network and Caddy reach it (no published port).
BIND = os.environ.get("HERMES_HTTP_BIND", "0.0.0.0")  # nosec B104

# RAISED FROM 256 KiB ON 2026-09-24, when photographs started arriving.
# 256 KiB was ample for text and is smaller than a single phone photo: a
# shrunk 1600px JPEG is a few hundred kilobytes and base64 adds a third, so
# every photo question would have been refused with a bare HTTP 400 that
# said nothing about size. 12 MiB leaves room for a large one and still
# bounds the request.
MAX_BODY = 12 * 1024 * 1024

_router = Router()


def _refuse_mutations(name: str, args: dict):
    """Every mutating tool, refused with a reason the model can explain.

    A string (rather than False) so the assistant says WHY it did not do the
    thing, instead of silently not doing it and sounding evasive.

    THE GARDEN DECISION IS THE EXCEPTION, and deliberately so. This refusal
    exists to keep the phone from writing files and running commands on the
    house's machines. Answering "yes, that plant tag means Average" while
    standing in the garden with the tag in your hand is the phone's BEST
    use, not a thing to push onto a desktop -- and it is already guarded
    twice over: the decision is signed by whoever is signed in, and it is
    refused unless the exact suggested wording is passed back.
    """
    from . import garden_tools
    if name in garden_tools.MUTATING:
        return True
    return (
        f"'{name}' is not available from the phone app. Reading is allowed; "
        "writing files and running commands are not. Use the desktop for that."
    )


def _agent_for(email: str, display: str, surface: str) -> Agent:
    store = MemoryStore(MEMORY_ROOT, email, display)
    store.ensure()
    return Agent(
        router=_router,
        transcript=Transcript(surface=surface, title=f"{surface} {email}"),
        job=os.environ.get("DEFAULT_JOB", "chat"),
        auto_job=os.environ.get("AUTO_JOB", "true").lower() == "true",
        confirm=_refuse_mutations,
        memory=store,
        # Decided from the address the gate supplied, never from anything in
        # the request body -- the same rule the rest of this file follows.
        garden=garden_tools.allowed_for(email),
        identity_email=email,
    )


class Handler(BaseHTTPRequestHandler):
    # Quiet: the default logs every request to stderr in Apache format, which
    # buries the things worth reading in this container's log.
    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.rstrip("/") in ("/healthz", "/health"):
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "no such route"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/ask":
            self._send(404, {"error": "no such route"})
            return

        email = (self.headers.get("X-Auth-Email") or "").strip().lower()
        if not email:
            self._send(401, {"error": "no identity supplied"})
            return

        # The person must already exist in people.json. Inventing a memory
        # folder for an unknown address would make a typo look like a new
        # person, and quietly start a second memory for someone who has one.
        person = by_email(email)
        if person is None:
            self._send(403, {"error": f"{email} is not a member of this household"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, {"error": "bad Content-Length"})
            return
        if length <= 0 or length > MAX_BODY:
            # SAY WHICH. "empty or oversized" leaves a caller guessing, and
            # the two have opposite fixes.
            self._send(400, {"error": "empty request" if length <= 0 else
                             f"request too large ({length} bytes, "
                             f"limit {MAX_BODY})"})
            return

        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:  # noqa: BLE001
            self._send(400, {"error": "body is not JSON"})
            return

        text = (data.get("text") or "").strip()
        if not text:
            self._send(400, {"error": "nothing to ask"})
            return

        surface = data.get("session") or "app"
        if not isinstance(surface, str) or len(surface) > 40:
            surface = "app"

        agent = _agent_for(person.email, person.display, surface)

        # Prior turns. The caller's own history WINS when it sends any: the
        # app knows what is on its screen, and that is what the person can
        # actually see. Only role/content are accepted -- a caller-supplied
        # tool result would be a way to tell the model that something
        # happened which did not.
        #
        # WHEN IT SENDS NONE, the thread is restored from the server instead,
        # so closing the app does not end the conversation. `fresh` skips
        # that, for a deliberate new start.
        sent = []
        for turn in (data.get("history") or [])[-20:]:
            if not isinstance(turn, dict):
                continue
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and isinstance(content, str) and content:
                sent.append({"role": role, "content": content[:8000]})

        fresh = bool(data.get("fresh"))
        if fresh:
            threads.clear(person.email, surface)
        prior = sent if sent else ([] if fresh
                                   else threads.load(person.email, surface))
        resumed = bool(prior) and not sent
        for turn in prior:
            agent.messages.append(turn)

        # THE PICTURE, IF ONE CAME. The auth service has already turned it
        # into words and put those in `text`; this is the original, kept for
        # the tools that do their own, better-informed vision pass -- the
        # garden ones, which have the plant's own record to compare against.
        # Anything that is not a data: image URL is ignored rather than
        # forwarded, so a caller cannot use this field to make the service
        # fetch a URL of their choosing.
        photo = data.get("image")
        if not (isinstance(photo, str) and photo.startswith("data:image/")):
            photo = ""

        try:
            answer = agent.send(text, photo=photo)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._send(502, {"error": f"the assistant failed: {exc}"})
            return

        # The thread as it now stands, for the next time this person opens
        # the app. Saved AFTER a successful answer only -- storing a turn
        # whose reply failed would resume a conversation that never happened.
        threads.save(person.email, surface,
                     prior + [{"role": "user", "content": text},
                              {"role": "assistant", "content": answer}])

        self._send(
            200,
            {
                "answer": answer,
                # So an app can say "picking up where you left off" rather
                # than silently appearing to remember something it was not
                # shown.
                "resumed": resumed,
                # What it actually did, so the app can show "looked in your
                # shelf" rather than leaving a long pause unexplained.
                "tools": list(getattr(agent, "_tools_used", []) or []),
            },
        )


def main() -> None:
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"[hermes-http] listening on {BIND}:{PORT}, memory root {MEMORY_ROOT}",
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
