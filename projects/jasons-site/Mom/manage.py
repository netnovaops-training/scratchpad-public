#!/usr/bin/env python3
"""Command line admin for Tech Help.  Installed as: momhelp

  sudo -u momhelp momhelp adduser mom --name "Mom" --generate
  sudo -u momhelp momhelp adduser jason --admin --generate
  sudo -u momhelp momhelp users
  sudo -u momhelp momhelp status
  sudo -u momhelp momhelp alerts
  sudo -u momhelp momhelp memory mom
"""
import argparse, getpass, secrets, string, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import core  # noqa: E402


def genpw(n=16):
    words = "amber,anchor,basil,cedar,copper,daisy,ember,fable,garnet,harbor,indigo,jasper,kettle,lantern,meadow,nutmeg,olive,pepper,quartz,ribbon,saffron,timber,umber,velvet,willow,yarrow".split(",")
    return "-".join(secrets.choice(words) for _ in range(3)) + str(secrets.randbelow(90) + 10)


def main():
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("adduser"); a.add_argument("username"); a.add_argument("--name", default="")
    a.add_argument("--admin", action="store_true"); a.add_argument("--password", default="")
    a.add_argument("--generate", action="store_true")
    # She already has the password saved on her phone. Forcing a change on
    # first sign-in means she lands on a form she did not ask for, with a
    # password she cannot see, on a phone. --no-change skips that entirely.
    a.add_argument("--no-change", action="store_true",
                   help="do not force a password change on first sign-in")
    w = sub.add_parser("passwd"); w.add_argument("username"); w.add_argument("--generate", action="store_true")
    w.add_argument("--password", default="")
    sub.add_parser("users"); sub.add_parser("status"); sub.add_parser("alerts"); sub.add_parser("kb")
    m = sub.add_parser("memory"); m.add_argument("username")
    z = sub.add_parser("timezone")   # she is in Nevada, the Pi is in New York
    z.add_argument("username"); z.add_argument("tz")
    n = sub.add_parser("nochange")   # stop asking this person to change it
    n.add_argument("username")
    sub.add_parser("distill")        # run the nightly job now
    sub.add_parser("distill-reset")  # forget everything it taught itself
    sub.add_parser("reindex")        # rebuild the semantic memory from tickets
    sub.add_parser("models")         # which specialists are installed
    sub.add_parser("prewarm")        # load them into VRAM
    sub.add_parser("otobo")          # mirror status + flush the queue
    # WHY THIS EXISTS AS A COMMAND rather than a one-line docker exec.
    # The obvious way to read these is a python -c with a SQL string inside it,
    # which needs quotes inside quotes inside quotes -- and the bash escaping
    # for that is not the PowerShell escaping, so the line you copy off a page
    # fails on Windows for a reason that has nothing to do with the question
    # you were asking. A subcommand has no quoting at all.
    e = sub.add_parser("events")     # what the app has been deciding lately
    e.add_argument("source", nargs="?", default="",
                   help="choose | tools | router | kb | ticket | auth ...")
    e.add_argument("-n", type=int, default=20)
    sub.add_parser("webhook")        # push tickets to the desktop listener
    sub.add_parser("webhook-test")   # prove the link works
    args = p.parse_args()
    core.ensure_dirs(); core.init_db()

    if args.cmd == "adduser":
        pw = args.password or (genpw() if args.generate else getpass.getpass("Password: "))
        if len(pw) < 8: print("Too short.", file=sys.stderr); return 1
        core.create_user(args.username, pw, args.name, "admin" if args.admin else "user",
                         must_change=bool(args.generate) and not args.no_change)
        print(f"Created {args.username}" + (f"\nPassword: {pw}" if args.generate else ""))
        if args.generate: print("Readable on purpose -- she has to type it once on her phone.")
    elif args.cmd == "passwd":
        pw = args.password or (genpw() if args.generate else getpass.getpass("New password: "))
        core.set_password(args.username, pw); print(f"Updated." + (f" Password: {pw}" if args.generate else ""))
    elif args.cmd == "users":
        for r in core.list_users(): print(f"{r['username']:20} {r['role']:6} {r['display_name']}")
    elif args.cmd == "status":
        st = core.backend_status()
        print(f"desktop {core.DESKTOP_IP}  mac={core.DESKTOP_MAC}")
        print(f"  ollama  {'up' if st['ollama'] else 'down'}")
        print(f"  whisper {'up' if st['whisper'] else 'down'}")
        print(f"models: {', '.join(core.available_models()) or '(unreachable)'}")
    elif args.cmd == "alerts":
        rows = core.list_alerts(50)
        if not rows: print("No alerts.")
        for r in rows:
            print(f"[{'NEW' if not r['seen'] else '   '}] {r['kind']:18} {r['detail'][:110]}")
    elif args.cmd == "memory":
        for r in core.recall(args.username): print(f"{r['key']:28} {r['value']}")
    elif args.cmd == "nochange":
        with core.tx() as c:
            c.execute("UPDATE users SET must_change=0 WHERE username=?",
                      (core.normalize_username(args.username),))
        print(f"{args.username} will not be asked to change their password.")
    elif args.cmd == "timezone":
        if core.set_tz(args.username, args.tz):
            import time as _t
            print(f"{args.username} -> {args.tz}")
            print(f"Right now that reads as: {core.local_time(_t.time(), args.username)}")
        else:
            print(f"Unknown timezone {args.tz!r}. Try America/Los_Angeles.", file=sys.stderr)
            return 1
    elif args.cmd == "distill":
        from app import distill
        print(distill.run_nightly())
    elif args.cmd == "distill-reset":
        from app import distill
        r = distill.reset_learned()
        print(f"Removed {r['notes']} notes, {r['vectors']} vectors, {r['files']} files.")
        print("Your own knowledge/ files and every ticket are untouched.")
    elif args.cmd == "reindex":
        from app import memory
        print(memory.reindex_all())
    elif args.cmd == "events":
        sql = "SELECT ts, level, source, message FROM events"
        params = []
        if args.source:
            sql += " WHERE source=?"
            params.append(args.source)
        sql += " ORDER BY rowid DESC LIMIT ?"
        params.append(args.n)
        rows = core.conn().execute(sql, params).fetchall()
        if not rows:
            where = f" from {args.source}" if args.source else ""
            print(f"Nothing{where} yet.")
            # Naming the likely reason beats an empty list. "No events" reads
            # as broken; "it is switched off" reads as a thing to go and do.
            if args.source == "choose":
                print("MOM_CHOOSE_URL is probably unset, so this app is "
                      "picking its own models and never asks n8n.")
            return
        import datetime as _dt
        for r in reversed(rows):
            when = _dt.datetime.fromtimestamp(r["ts"]).strftime("%H:%M:%S")
            print(f"{when}  {r['level']:<5} {r['source']:<9} {r['message']}")

    elif args.cmd == "models":
        from app import router, memory
        print(f"profile: {router.PROFILE}")
        for h in router.health():
            print(f"  {h['role']:8} {h['model']:34} {'installed' if h['installed'] else 'MISSING'}")
        print(f"memory: {memory.stats()}")
    elif args.cmd == "prewarm":
        from app import router
        if not core.backend_status()["ready"]:
            print("waking the desktop..."); core.ensure_awake()
        router.prewarm(block=True); print("loaded")
    elif args.cmd == "otobo":
        from app import otobo
        print(otobo.status())
        ok, why = otobo.ping(); print(f"ping: {'ok' if ok else 'FAILED'} — {why}")
        print(otobo.flush(limit=100))
    elif args.cmd == "webhook":
        from app import webhook
        print(webhook.status()); print(webhook.flush(limit=100, wake=True))
    elif args.cmd == "webhook-test":
        from app import webhook
        ok, why = webhook.test()
        print(("ok: " if ok else "FAILED: ") + why)
        return 0 if ok else 1
    elif args.cmd == "kb":
        t, v = core.load_knowledge()
        for f in core.knowledge_files(): print(" ", f.name)
        print(f"version {v}  {len(t)} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
