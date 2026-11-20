# Projects

Jason's homelab projects, private. Two folders:

| Folder | What |
|---|---|
| `jasons-site/` | The PC side of example.org: site pages (Admin, Booking, Kitchen, Mom, Casey, recipe-app, peewee) and `ai-stack` (Caddy, Docker Compose stacks, Rust and Python services, scripts). |
| `tickets/` | The Tickets server: ticket editor behind the household sign-in that saves to GitHub and Google Drive. |
| `Tickets/` | Tickets saved from the Tickets page (one Markdown file each), on the `tickets` branch. |

## Branches

- **`tickets`**: all work, and where the Tickets page saves.
- **`main`**: published. Changes arrive by pull request from `tickets` (merge commit; `tickets` is never deleted).

## Checks

- **checks** (every push and pull request): gitleaks, shellcheck, PSScriptAnalyzer, actionlint, zizmor.
- **tickets-server** (when `tickets/` changes): `cargo test` and `cargo clippy`.
- **deep-scans** (pull requests into main, Sundays, by hand): semgrep, trivy, osv-scanner, cargo-audit.

The repo is private, so results show on each run's summary page rather than the Security tab.

## Kept out on purpose

- Secrets: every `.env`, key, certificate and token. Real values live in Proton Pass.
- People lists, sign-in state, uploads, databases and backups.
- Robin's Garden: handed over to Robin; it lives in his own repos.
- Personal data was scrubbed before the first commit. MAC addresses, public IPs, email addresses (and usernames made from them) and phone numbers are replaced with documentation or fictional values, and the device inventories, Gmail filters and places list are left out. The private Gitea copy on the NUC is scrubbed the same way. The real values are only on the PC and in the NAS backup.

Built with AI help (Claude); design choices and testing with Jason.
