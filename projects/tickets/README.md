# Tickets

A ticket editor for the homelab: open a bookmark, fill in a form, and the ticket saves as you
type. One button saves a copy to GitHub (a Markdown file per ticket) and to Google Drive (a
Google Doc per ticket). Edits made in the Google Doc can be pulled back into the ticket.

Live at https://example.org/tickets/ behind the household sign-in (SSO).

## Three kinds of ticket

| Kind | For | Fields |
|---|---|---|
| Fix | Something broke | Problem, Fix, Learned (required to close); Symptoms, What I tried, Cause, One line; Work log; scrub check |
| Note | A quick todo, script, command or idea | Type, text, tags |
| Project | Something to build | Goal, Done when, numbered steps with checkboxes and notes, Work log |

Bookmarks: `/tickets/#fix`, `/tickets/#note`, `/tickets/#project`, `/tickets/#list`.
Numbers carry on from the Google Docs trial: T-011 is the first.

## How it fits together

```
browser ──> Caddy (forward_auth: household SSO) ──> tickets container 127.0.0.1:8050
                                                        │  /data/tickets/T-011.json
                                                        ├─> GitHub Contents API  (Toolbox, tickets branch, Tickets/)
                                                        └─> Apps Script web app  (Tickets folder, one Doc per ticket)
```

- **Storage:** one JSON file per ticket in the `tickets_data` volume. Deleted tickets move to
  `/data/deleted/`, so a mis-click can be undone by moving the file back.
- **Sign-in:** Caddy asks the SSO first and passes the signed-in address as `X-Auth-Email`.
  The server refuses any request without it and records who saved each ticket.
- **GitHub:** a fine-grained token that can only write that one repo's contents. A save that
  changes nothing makes no commit.
- **Google Drive:** a small Google Apps Script in your own account (`apps-script/`) does the
  Drive work, so no Google Cloud project or billing is needed. It runs with the `drive.file`
  scope (only files it made) and refuses calls without the shared secret. Saving updates the
  same Doc each time. If the Doc was edited since the last save, it stops and asks you to pull
  first (or overwrite). Setup steps are on the page under Settings.

## Run it

```
cargo test                       # store + API tests
bash deploy-nuc.sh               # on the NUC: build image, start container on 127.0.0.1:8050
bash deploy-nuc.sh undo          # stop it (data kept)
```

Settings live in `~/nuc-homelab/tickets.env` on the NUC (see `tickets.env.example`).

Built with AI help (Claude); design choices and testing with Jason.
