# Scratchpad (public copy)

Code from a self-hosted home lab: a Rust sign-in and admin service (passkeys, OIDC), Python
helper services (voice, telephony, printing, metrics, a Telegram bot), Docker Compose files,
and PowerShell and shell maintenance scripts for a Windows PC, an Intel NUC and a Raspberry Pi.

This repo exists so GitHub's free security and quality checks can review the code.
It is a cleaned copy, not the running system:

- Names, email addresses, phone numbers, home addresses, domains and network addresses are
  replaced with placeholders (`example.org`, `home.example.net`, `192.168.1.x`, `555-01xx`).
- Secrets are never in the code. Services read them from environment variables, filled at run
  time from Proton Pass (`pass://` references).
- Personal document tools and session notes are left out.

## Checks

| Workflow | What it runs |
|---|---|
| `quick-checks` | gitleaks, ShellCheck, PSScriptAnalyzer, actionlint, zizmor |
| `deep-scans` | Semgrep, Trivy, OSV-Scanner, cargo-audit, Bandit, dependency review |
| `rust-clippy` | Rust lint on `pc/ai-stack/rust` |
| `mobile` | detekt (Android) and SwiftLint (Apple), when Kotlin or Swift files exist |
| `code-health` | shared code-health checks |
| `scorecard` | OpenSSF Scorecard |
| CodeQL, Dependabot, secret scanning | GitHub settings |

Known: the Rust workspace in this copy does not compile yet (a missing module and two
templates), so `rust-clippy` reports that until it is fixed.
