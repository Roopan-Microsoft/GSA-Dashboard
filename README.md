# GSA Dashboard

Unified GitHub Pages dashboard for portfolio issue tracking and GitHub security posture across the Solution Accelerator repository set.

## Repository layout

```text
GSA-Dashboard/
├── .github/workflows/
├── docs/dashboard.html
├── src/dashboard/generate_dashboard.py
├── src/issue_tracker/{helpers.py,run_tracker.py}
├── src/security_scanner/scan_security.py
└── state/{tracked_issues_state.json,security_state.json}
```

## What it does

- Tracks open GitHub issues for the shared accelerator repo list
- Creates ADO bugs and sends email / Teams notifications for new issue activity
- Scans Dependabot and code scanning alerts through GitHub REST APIs
- Generates a single Fluent-styled HTML dashboard with **Overview**, **Issue Tracking**, and **Security & Code Quality** tabs
- Deploys the dashboard to GitHub Pages

## Tracked repositories

The unified repo list is the union of the original issue tracker and security dashboard inputs. Today that resolves to the 14 repositories already defined in `src/issue_tracker/helpers.py`.

## Local setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python src\dashboard\generate_dashboard.py
```

The generated dashboard is written to `docs/dashboard.html` by default.

## Environment variables

### Issue tracker

| Variable | Required | Purpose |
| --- | --- | --- |
| `GITHUB_TOKEN` | Yes in Actions | Reads GitHub issues/comments |
| `ADO_PAT` | Yes for ADO creation | Creates Azure DevOps bugs when new issue labels allow it |
| `ADO_ORG` | Yes | Azure DevOps organization |
| `ADO_PROJECT` | Yes | Azure DevOps project |
| `ADO_AREA_PATH` | Yes | Area path for new bugs |
| `ADO_PARENT_US_ID` | Yes | Parent user story id |
| `POWER_AUTOMATE_EMAIL_URL` | Optional | Email notification webhook |
| `POWER_AUTOMATE_TEAMS_URL` | Optional | Teams notification webhook |
| `TEAMS_CHAT_ID` | Optional | Teams target chat id |
| `SENDER_EMAIL` | Optional | Summary email sender |
| `CC_EMAILS` | Optional | Semicolon-separated CC list |
| `FOLLOWUP_DEADLINE_DAYS` | Optional | Overdue follow-up threshold, default `2` |
| `REMINDER_RESEND_HOURS` | Optional | Reminder resend interval, default `2` |
| `STATE_FILE` | Optional | Issue state output path |

### Security scanner

| Variable | Required | Purpose |
| --- | --- | --- |
| `GITHUB_TOKEN` | Yes in Actions | Calls Dependabot and code scanning APIs |
| `SECURITY_GITHUB_TOKEN` | Recommended in Actions | Optional PAT secret with explicit security access if the default Actions token is insufficient |
| `GITHUB_PAT` | Optional for local runs | Fine-grained or classic token when not using Actions |
| `SECURITY_STATE_FILE` | Optional | Security state output path |

> The workflow grants `security-events: read`. If your org requires a stronger token for Dependabot or code scanning APIs, set a `SECURITY_GITHUB_TOKEN` repository secret and the workflow will prefer it.

### Dashboard generator

| Variable | Required | Purpose |
| --- | --- | --- |
| `ISSUE_STATE_FILE` | Optional | Input issue state JSON |
| `SECURITY_STATE_FILE` | Optional | Input security state JSON |
| `OUTPUT_HTML` | Optional | Output HTML path |
| `FOLLOWUP_DEADLINE_DAYS` | Optional | Overdue threshold used for status coloring |

## GitHub Actions

- `issue_tracker.yml` — runs every 30 minutes and updates `state/tracked_issues_state.json`
- `security_scanner.yml` — runs every 6 hours and updates `state/security_state.json`
- `deploy_dashboard.yml` — rebuilds and deploys GitHub Pages after either upstream workflow succeeds

## Notes

- Security endpoints may return `404` when GHAS is not enabled for a repository. The scanner treats those repositories as unavailable instead of failing the full run.
- No secrets or tokens are stored in the repository.
