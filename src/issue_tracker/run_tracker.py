"""
Standalone runner for GitHub Actions — invokes the same tracker logic
as the Azure Function but without the azure.functions dependency.
"""

import sys
import os
import logging

# Ensure helpers is importable from same directory
sys.path.insert(0, os.path.dirname(__file__))

from helpers import (
    env, REPOS, load_state, save_state,
    fetch_open_issues, fetch_issue_comments,
    get_current_iteration, create_ado_bug,
    send_email, send_teams_message,
    new_issue_email, new_issue_teams,
    followup_email, followup_teams,
    summary_email,
)
from datetime import datetime, timezone, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("tracker")


def run_tracker():
    log.info("=" * 60)
    log.info("GitHub Issue Tracker — GitHub Actions Run")
    log.info("=" * 60)

    state      = load_state()
    now        = datetime.now(timezone.utc)
    cc_emails  = [e.strip() for e in env("CC_EMAILS", "").split(";") if e.strip()]
    total_new  = 0
    total_ado  = 0
    total_over = 0
    errors     = 0

    followup_days = int(env("FOLLOWUP_DEADLINE_DAYS", "2"))
    reminder_hrs  = int(env("REMINDER_RESEND_HOURS", "2"))

    # Resolve ADO sprint once
    iteration = get_current_iteration()
    if not iteration:
        log.warning("Could not resolve ADO sprint — using fallback")
        iteration = f"{env('ADO_PROJECT')}\\FY26"

    for cfg in REPOS:
        accel    = cfg["name"]
        owner    = cfg["owner"]
        repo     = cfg["repo"]
        repo_key = f"{owner}/{repo}"
        repo_url = f"https://github.com/{repo_key}"

        log.info(f"Checking: {accel} ({repo_key})")

        issues = fetch_open_issues(owner, repo)
        if issues is None:
            errors += 1
            continue

        if repo_key not in state:
            state[repo_key] = {
                "seen_issues": [], "issue_details": {},
                "open_issues_snapshot": [],
                "last_checked": now.isoformat(), "last_new_count": 0,
            }
        if "issue_details" not in state[repo_key]:
            state[repo_key]["issue_details"] = {}
        if "open_issues_snapshot" not in state[repo_key]:
            state[repo_key]["open_issues_snapshot"] = []

        seen    = set(state[repo_key].get("seen_issues", []))
        details = state[repo_key]["issue_details"]

        # Detect new issues
        new_issues = [i for i in issues if i["number"] not in seen]

        if new_issues:
            log.info(f"  -> {len(new_issues)} NEW issue(s)!")
            total_new += len(new_issues)

            for issue in new_issues:
                lbls = [l.lower() for l in issue.get("labels", [])]
                if lbls and "bug" not in lbls:
                    log.info(f"    Skip ADO for #{issue['number']} ({', '.join(lbls)})")
                    continue
                result = create_ado_bug(issue, accel, repo_url,
                                        cfg["primary"]["email"], iteration)
                if result:
                    issue["ado_bug_id"]  = result["id"]
                    issue["ado_bug_url"] = result["url"]
                    total_ado += 1

            to = [cfg["primary"]["email"], cfg["secondary"]["email"]]
            subj = f"[GitHub Alert] {len(new_issues)} New Issue(s) — {accel}"
            send_email(to, cc_emails, subj, new_issue_email(accel, repo_url, new_issues))
            send_teams_message(new_issue_teams(
                accel, repo_url, new_issues, cfg["primary"], cfg["secondary"]))

            for issue in new_issues:
                details[str(issue["number"])] = {
                    "title": issue["title"],
                    "html_url": issue["html_url"],
                    "created_at": issue["created_at"],
                    "user": issue.get("user", "N/A"),
                    "first_seen": now.isoformat(),
                    "followup_found": False,
                    "ado_bug_id": issue.get("ado_bug_id"),
                    "ado_bug_url": issue.get("ado_bug_url"),
                }
        else:
            log.info(f"  -> No new issues")

        # Update seen_issues: UNION merge to prevent false notifications
        # when the GitHub API intermittently returns empty/partial results.
        # A response is "trustworthy" only when it returns results or when
        # we have no prior tracked issues. Empty responses require 3 consecutive
        # occurrences before we trust that a repo truly has 0 open issues.
        current_nums = {i["number"] for i in issues}
        trustworthy = True
        if not current_nums and seen:
            empty_streak = state[repo_key].get("_empty_streak", 0) + 1
            state[repo_key]["_empty_streak"] = empty_streak
            if empty_streak < 3:
                log.warning(f"  ⚠ API returned 0 issues but {len(seen)} tracked "
                            f"(streak {empty_streak}/3) — keeping existing state")
                trustworthy = False
            else:
                log.info(f"  ℹ 3 consecutive empty responses — clearing state")
                state[repo_key]["_empty_streak"] = 0
        else:
            state[repo_key]["_empty_streak"] = 0

        if trustworthy:
            state[repo_key]["seen_issues"] = list(seen | current_nums)
            state[repo_key]["open_issues_snapshot"] = issues
            # Purge closed issues from follow-up tracking
            open_nums = {str(i["number"]) for i in issues}
            for k in [k for k in details if k not in open_nums]:
                del details[k]

        state[repo_key]["last_checked"]  = now.isoformat()
        state[repo_key]["last_new_count"] = len(new_issues)

        # Follow-up SLA check (skip if API snapshot was untrustworthy)
        overdue_issues = []
        if not trustworthy:
            log.info(f"  ⏭ Skipping follow-up check — API snapshot untrustworthy")
        else:
            for num_str, det in details.items():
                if det.get("followup_found"):
                    continue
                first_seen = datetime.fromisoformat(det["first_seen"])
                age = now - first_seen
                if age < timedelta(days=followup_days):
                    continue
                last_rem = det.get("last_reminder_sent")
                if last_rem:
                    since = now - datetime.fromisoformat(last_rem)
                    if since < timedelta(hours=reminder_hrs):
                        continue
                comments = fetch_issue_comments(owner, repo, int(num_str))
                if comments is None:
                    continue
                if len(comments) > 0:
                    det["followup_found"] = True
                    det["followup_at"] = comments[0]["created_at"]
                    log.info(f"    Follow-up found: {accel} #{num_str}")
                    continue
                overdue_issues.append({
                    "number": int(num_str),
                    "title": det.get("title", "N/A"),
                    "html_url": f"{repo_url}/issues/{num_str}",
                    "created_at": det.get("created_at", "N/A"),
                    "user": det.get("user", "N/A"),
                    "age_days": age.days,
                })
                det["last_reminder_sent"] = now.isoformat()

        if overdue_issues:
            log.warning(f"  {accel}: {len(overdue_issues)} overdue!")
            total_over += len(overdue_issues)
            to = [cfg["primary"]["email"], cfg["secondary"]["email"]]
            subj = f"[REMINDER] {len(overdue_issues)} Issue(s) Awaiting Follow-up — {accel}"
            send_email(to, cc_emails, subj,
                       followup_email(accel, repo_url, overdue_issues, followup_days))
            send_teams_message(followup_teams(
                accel, repo_url, overdue_issues, cfg["primary"], cfg["secondary"]))

    save_state(state)

    log.info(f"\nRun complete: {total_new} new | {total_ado} ADO bugs | {total_over} overdue | {errors} errors")
    log.info("=" * 60)

    subj, body = summary_email(total_new, total_ado, total_over, errors)
    send_email([env("SENDER_EMAIL", "v-riteshmate@microsoft.com")], [], subj, body)


if __name__ == "__main__":
    run_tracker()
