import html
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = SRC_DIR.parents[0]
sys.path.insert(0, str(SRC_DIR))

from issue_tracker.helpers import REPOS  # noqa: E402

ISSUE_STATE_FILE = Path(os.environ.get("ISSUE_STATE_FILE", ROOT_DIR / "state" / "tracked_issues_state.json"))
SECURITY_STATE_FILE = Path(os.environ.get("SECURITY_STATE_FILE", ROOT_DIR / "state" / "security_state.json"))
OUTPUT_HTML = Path(os.environ.get("OUTPUT_HTML", ROOT_DIR / "docs" / "dashboard.html"))
FOLLOWUP_DEADLINE_DAYS = int(os.environ.get("FOLLOWUP_DEADLINE_DAYS", "2"))
IST = timezone(timedelta(hours=5, minutes=30))

SEVERITY_ORDER = {"critical": 5, "high": 4, "medium": 3, "warning": 2, "low": 1, "note": 0, "error": 4}


def load_json(path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as handle:
            try:
                return json.load(handle)
            except json.JSONDecodeError:
                return default
    return default


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_ts(value):
    dt_value = parse_dt(value)
    if not dt_value:
        return "N/A"
    return dt_value.astimezone(IST).strftime("%d %b %Y · %I:%M %p IST")


def age_days(value, now):
    dt_value = parse_dt(value)
    if not dt_value:
        return 0
    return max((now - dt_value).days, 0)


def short_name(name):
    mapping = {
        "Customer Chatbot": "Chatbot",
        "Container Migration": "Container Mig",
        "Content Generation": "Content Gen",
        "Content Processing": "Content Proc",
        "Code Modernization": "Modernize",
        "Agentic App": "Agentic App",
        "Data & Agent Governance": "Governance",
        "Deploy AI App": "Deploy AI App",
        "KM Generic": "CKM",
        "Real-Time Intelligence": "RTI",
    }
    return mapping.get(name, name)


def escape(value):
    return html.escape(str(value if value is not None else ""))


def label_bucket(labels):
    lowered = [label.lower() for label in labels]
    if "bug" in lowered:
        return "bug"
    if "enhancement" in lowered:
        return "enhancement"
    return "other"


def severity_badge(value):
    css = "note"
    if value in ("critical", "high", "medium", "low"):
        css = value
    return f'<span class="sev-badge {css}">{escape(value.title())}</span>'


def risk_indicator(level):
    return (
        f'<span class="risk-indicator risk-{escape(level)}">'
        f'<span class="risk-dot"></span>{escape(level.title())}</span>'
    )


def build_issue_model(issue_state):
    now = datetime.now(timezone.utc)
    repo_rows = []
    all_issues = []
    owner_counts = Counter()
    totals = Counter({"open": 0, "bug": 0, "enhancement": 0, "other": 0, "overdue": 0})

    for cfg in REPOS:
        repo_key = f"{cfg['owner']}/{cfg['repo']}"
        repo_state = issue_state.get(repo_key, {})
        details = repo_state.get("issue_details", {})
        snapshot = repo_state.get("open_issues_snapshot") or []

        issue_map = {}
        for item in snapshot:
            issue_map[str(item.get("number"))] = {
                "number": item.get("number"),
                "title": item.get("title", "Untitled issue"),
                "html_url": item.get("html_url", f"https://github.com/{repo_key}/issues/{item.get('number')}"),
                "created_at": item.get("created_at"),
                "user": item.get("user", "N/A"),
                "labels": item.get("labels", []),
            }

        for num_str, item in details.items():
            issue_map.setdefault(
                num_str,
                {
                    "number": int(num_str),
                    "title": item.get("title", "Untitled issue"),
                    "html_url": item.get("html_url", f"https://github.com/{repo_key}/issues/{num_str}"),
                    "created_at": item.get("created_at"),
                    "user": item.get("user", "N/A"),
                    "labels": item.get("labels", []),
                },
            )

        repo_issue_rows = []
        for num_str, issue in issue_map.items():
            detail = details.get(str(num_str), {})
            labels = issue.get("labels", []) or detail.get("labels", [])
            bucket = label_bucket(labels)
            created_at = issue.get("created_at") or detail.get("created_at")
            item_age = age_days(created_at, now)
            followup_found = detail.get("followup_found", False)
            status = "Followed up" if followup_found else ("Overdue" if item_age >= FOLLOWUP_DEADLINE_DAYS else "Pending")
            repo_issue_rows.append(
                {
                    "repo_name": cfg["name"],
                    "repo_key": repo_key,
                    "repo_url": f"https://github.com/{repo_key}",
                    "number": issue["number"],
                    "title": issue["title"],
                    "html_url": issue["html_url"],
                    "created_at": created_at,
                    "age_days": item_age,
                    "reported_by": issue.get("user", "N/A"),
                    "labels": labels,
                    "bucket": bucket,
                    "status": status,
                    "followup_found": followup_found,
                    "primary_owner": cfg["primary"]["name"],
                    "secondary_owner": cfg["secondary"]["name"],
                    "ado_bug_url": detail.get("ado_bug_url"),
                    "ado_bug_id": detail.get("ado_bug_id"),
                }
            )

        repo_issue_rows.sort(key=lambda item: item["age_days"], reverse=True)
        bug_count = sum(1 for item in repo_issue_rows if item["bucket"] == "bug")
        enhancement_count = sum(1 for item in repo_issue_rows if item["bucket"] == "enhancement")
        other_count = sum(1 for item in repo_issue_rows if item["bucket"] == "other")
        overdue_count = sum(1 for item in repo_issue_rows if item["status"] == "Overdue")
        oldest_days = repo_issue_rows[0]["age_days"] if repo_issue_rows else 0

        totals["open"] += len(repo_issue_rows)
        totals["bug"] += bug_count
        totals["enhancement"] += enhancement_count
        totals["other"] += other_count
        totals["overdue"] += overdue_count
        owner_counts[cfg["primary"]["name"]] += len(repo_issue_rows)
        all_issues.extend(repo_issue_rows)

        repo_rows.append(
            {
                "name": cfg["name"],
                "short_name": short_name(cfg["name"]),
                "repo_key": repo_key,
                "repo_url": f"https://github.com/{repo_key}",
                "primary_owner": cfg["primary"]["name"],
                "secondary_owner": cfg["secondary"]["name"],
                "open_count": len(repo_issue_rows),
                "bug_count": bug_count,
                "enhancement_count": enhancement_count,
                "other_count": other_count,
                "overdue_count": overdue_count,
                "oldest_days": oldest_days,
                "last_checked": repo_state.get("last_checked"),
            }
        )

    all_issues.sort(key=lambda item: item["age_days"], reverse=True)
    owner_rows = [{"owner": owner, "count": count} for owner, count in owner_counts.most_common()]
    return {"repos": repo_rows, "issues": all_issues, "owners": owner_rows, "totals": totals}


def build_security_model(security_state):
    raw_repos = {
        item["repo_key"]: item
        for item in security_state.get("repos", [])
        if isinstance(item, dict) and item.get("repo_key")
    }
    repo_rows = []
    dependabot_details = []
    code_scanning_details = []
    dep_severity_totals = Counter({"critical": 0, "high": 0, "medium": 0, "low": 0})
    code_category_totals = Counter({"standard": 0, "microsoft_pack": 0})

    for cfg in REPOS:
        repo_key = f"{cfg['owner']}/{cfg['repo']}"
        repo_record = raw_repos.get(repo_key, {})
        dep = repo_record.get("dependabot", {})
        code = repo_record.get("code_scanning", {})
        dep_counts = dep.get("severity_counts", {})
        code_categories = code.get("category_counts", {})

        for sev in dep_severity_totals:
            dep_severity_totals[sev] += dep_counts.get(sev, 0)
        for category in code_category_totals:
            code_category_totals[category] += code_categories.get(category, 0)

        repo_rows.append(
            {
                "name": cfg["name"],
                "short_name": short_name(cfg["name"]),
                "repo_key": repo_key,
                "repo_url": f"https://github.com/{repo_key}",
                "dependabot_total": dep.get("total_open", 0),
                "dependabot_counts": dep_counts,
                "dependabot_risk": dep.get("risk_level", "low"),
                "dependabot_available": dep.get("available", False),
                "dependabot_error": dep.get("error"),
                "code_total": code.get("total_open", 0),
                "code_counts": code.get("severity_counts", {}),
                "code_categories": code_categories,
                "code_risk": code.get("risk_level", "low"),
                "code_available": code.get("available", False),
                "code_error": code.get("error"),
            }
        )

        for alert in dep.get("alerts", []):
            dependabot_details.append(
                {
                    "repo_name": cfg["name"],
                    "repo_key": repo_key,
                    "repo_url": f"https://github.com/{repo_key}",
                    "number": alert.get("number"),
                    "html_url": alert.get("html_url"),
                    "severity": alert.get("severity", "unknown"),
                    "package_name": alert.get("package_name", "unknown"),
                    "ecosystem": alert.get("ecosystem", "unknown"),
                    "summary": alert.get("summary", "Dependabot alert"),
                    "created_at": alert.get("created_at"),
                }
            )
        for alert in code.get("alerts", []):
            code_scanning_details.append(
                {
                    "repo_name": cfg["name"],
                    "repo_key": repo_key,
                    "repo_url": f"https://github.com/{repo_key}",
                    "number": alert.get("number"),
                    "html_url": alert.get("html_url"),
                    "severity": alert.get("severity", "note"),
                    "category": alert.get("category", "standard"),
                    "rule_id": alert.get("rule_id", "n/a"),
                    "rule_name": alert.get("rule_name", "Code scanning alert"),
                    "path": alert.get("path", ""),
                    "created_at": alert.get("created_at"),
                }
            )

    totals = security_state.get("totals", {})
    dependabot_details.sort(key=lambda item: (SEVERITY_ORDER.get(item["severity"], -1), item.get("created_at") or ""), reverse=True)
    code_scanning_details.sort(key=lambda item: (SEVERITY_ORDER.get(item["severity"], -1), item.get("created_at") or ""), reverse=True)
    return {
        "repos": repo_rows,
        "dependabot_details": dependabot_details,
        "code_scanning_details": code_scanning_details,
        "totals": {
            "dependabot_open": totals.get("dependabot_open", 0),
            "code_scanning_open": totals.get("code_scanning_open", 0),
            "all_open_findings": totals.get("all_open_findings", 0),
            "dependabot_severity_counts": totals.get("dependabot_severity_counts", dict(dep_severity_totals)),
            "code_scanning_severity_counts": totals.get("code_scanning_severity_counts", {}),
            "ecosystem_counts": totals.get("ecosystem_counts", {}),
            "code_scanning_category_counts": totals.get("code_scanning_category_counts", dict(code_category_totals)),
        },
        "generated_at": security_state.get("generated_at"),
    }


def build_health_rows(issue_model, security_model):
    security_map = {item["repo_key"]: item for item in security_model["repos"]}
    rows = []
    for repo in issue_model["repos"]:
        sec = security_map.get(repo["repo_key"], {})
        risk = "low"
        if repo["overdue_count"] > 0 or sec.get("dependabot_risk") == "critical":
            risk = "critical"
        elif repo["bug_count"] > 0 or sec.get("dependabot_risk") == "high" or sec.get("code_risk") == "high":
            risk = "high"
        elif repo["open_count"] > 0 or sec.get("dependabot_total", 0) > 0 or sec.get("code_total", 0) > 0:
            risk = "medium"
        rows.append(
            {
                "name": repo["name"],
                "repo_key": repo["repo_key"],
                "repo_url": repo["repo_url"],
                "issues": repo["open_count"],
                "overdue": repo["overdue_count"],
                "dependabot": sec.get("dependabot_total", 0),
                "code_scanning": sec.get("code_total", 0),
                "risk": risk,
                "primary_owner": repo["primary_owner"],
            }
        )
    rows.sort(key=lambda item: (SEVERITY_ORDER.get(item["risk"], 0), item["issues"] + item["dependabot"] + item["code_scanning"]), reverse=True)
    return rows


def render_issue_repo_rows(issue_model):
    rows = []
    for repo in issue_model["repos"]:
        overdue_cell = (
            risk_indicator("critical") if repo["overdue_count"] else '<span class="sev-badge clean">0</span>'
        )
        rows.append(
            "<tr>"
            f'<td><a class="repo-name" href="{escape(repo["repo_url"])}" target="_blank">{escape(repo["name"])}</a></td>'
            f'<td><strong>{repo["open_count"]}</strong></td>'
            f'<td>{repo["bug_count"]}</td>'
            f'<td>{repo["enhancement_count"]}</td>'
            f'<td>{repo["other_count"]}</td>'
            f"<td>{overdue_cell}</td>"
            f'<td>{repo["oldest_days"]}d</td>'
            f'<td>{escape(repo["primary_owner"])}</td>'
            f'<td>{escape(repo["secondary_owner"])}</td>'
            f'<td>{escape(fmt_ts(repo["last_checked"]))}</td>'
            "</tr>"
        )
    return "".join(rows)


def render_issue_detail_rows(issue_model):
    rows = []
    for issue in issue_model["issues"][:75]:
        labels = ", ".join(issue["labels"]) if issue["labels"] else "—"
        ado_html = (
            f'<a href="{escape(issue["ado_bug_url"])}" target="_blank">ADO #{escape(issue["ado_bug_id"])}</a>'
            if issue.get("ado_bug_url")
            else "—"
        )
        rows.append(
            "<tr>"
            f'<td><a class="repo-name" href="{escape(issue["html_url"])}" target="_blank">#{issue["number"]}</a></td>'
            f'<td>{escape(issue["repo_name"])}</td>'
            f'<td class="text-wrap">{escape(issue["title"])}</td>'
            f'<td>{escape(issue["reported_by"])}</td>'
            f'<td>{escape(labels)}</td>'
            f'<td>{issue["age_days"]}d</td>'
            f'<td>{severity_badge("medium" if issue["status"] == "Pending" else ("high" if issue["status"] == "Overdue" else "low"))}</td>'
            f'<td>{ado_html}</td>'
            "</tr>"
        )
    return "".join(rows)


def render_owner_rows(issue_model):
    rows = []
    max_count = max([row["count"] for row in issue_model["owners"]] or [1])
    for owner in issue_model["owners"]:
        width = round((owner["count"] / max_count) * 180) if max_count else 0
        rows.append(
            "<tr>"
            f"<td>{escape(owner['owner'])}</td>"
            f"<td><strong>{owner['count']}</strong></td>"
            f'<td><div class="bar-cell"><div class="severity-bar" style="width:{max(width, 4)}px"><div class="seg-brand" style="width:100%"></div></div></div></td>'
            "</tr>"
        )
    return "".join(rows)


def render_security_repo_rows(security_model):
    rows = []
    for repo in security_model["repos"]:
        dep_counts = repo["dependabot_counts"]
        code_categories = repo["code_categories"]
        dep_cell = (
            '<span class="sev-badge note">Unavailable</span>'
            if not repo["dependabot_available"] and repo["dependabot_total"] == 0
            else (
                f'<span class="sev-badge critical">{dep_counts.get("critical", 0)}</span> '
                f'<span class="sev-badge high">{dep_counts.get("high", 0)}</span> '
                f'<span class="sev-badge medium">{dep_counts.get("medium", 0)}</span> '
                f'<span class="sev-badge low">{dep_counts.get("low", 0)}</span>'
            )
        )
        code_cell = (
            '<span class="sev-badge note">Unavailable</span>'
            if not repo["code_available"] and repo["code_total"] == 0
            else (
                f'<span class="sev-badge high">{code_categories.get("standard", 0)}</span> '
                f'<span class="sev-badge note">{code_categories.get("microsoft_pack", 0)}</span>'
            )
        )
        rows.append(
            "<tr>"
            f'<td><a class="repo-name" href="{escape(repo["repo_url"])}" target="_blank">{escape(repo["name"])}</a></td>'
            f'<td><strong>{repo["dependabot_total"]}</strong></td>'
            f"<td>{dep_cell}</td>"
            f'<td>{risk_indicator(repo["dependabot_risk"])}</td>'
            f'<td><strong>{repo["code_total"]}</strong></td>'
            f"<td>{code_cell}</td>"
            f'<td>{risk_indicator(repo["code_risk"])}</td>'
            "</tr>"
        )
    return "".join(rows)


def render_dependabot_rows(security_model):
    rows = []
    for alert in security_model["dependabot_details"][:50]:
        rows.append(
            "<tr>"
            f'<td>{escape(alert["repo_name"])}</td>'
            f'<td><a class="repo-name" href="{escape(alert["html_url"])}" target="_blank">#{escape(alert["number"])}</a></td>'
            f'<td>{severity_badge(alert["severity"])}</td>'
            f'<td>{escape(alert["package_name"])}</td>'
            f'<td>{escape(alert["ecosystem"])}</td>'
            f'<td class="text-wrap">{escape(alert["summary"])}</td>'
            f'<td>{escape(fmt_ts(alert["created_at"]))}</td>'
            "</tr>"
        )
    return "".join(rows) or '<tr><td colspan="7">No Dependabot alerts available.</td></tr>'


def render_code_rows(security_model):
    rows = []
    for alert in security_model["code_scanning_details"][:50]:
        rows.append(
            "<tr>"
            f'<td>{escape(alert["repo_name"])}</td>'
            f'<td><a class="repo-name" href="{escape(alert["html_url"])}" target="_blank">#{escape(alert["number"])}</a></td>'
            f'<td>{severity_badge(alert["severity"])}</td>'
            f'<td>{severity_badge("note" if alert["category"] == "microsoft_pack" else "high")}</td>'
            f'<td>{escape(alert["rule_id"])}</td>'
            f'<td class="text-wrap">{escape(alert["rule_name"])}</td>'
            f'<td>{escape(alert["path"] or "—")}</td>'
            "</tr>"
        )
    return "".join(rows) or '<tr><td colspan="7">No code scanning alerts available.</td></tr>'


def render_health_rows(rows):
    html_rows = []
    for row in rows:
        html_rows.append(
            "<tr>"
            f'<td><a class="repo-name" href="{escape(row["repo_url"])}" target="_blank">{escape(row["name"])}</a></td>'
            f'<td>{row["issues"]}</td>'
            f'<td>{row["overdue"]}</td>'
            f'<td>{row["dependabot"]}</td>'
            f'<td>{row["code_scanning"]}</td>'
            f'<td>{risk_indicator(row["risk"])}</td>'
            f'<td>{escape(row["primary_owner"])}</td>'
            "</tr>"
        )
    return "".join(html_rows)


def generate_dashboard():
    issue_state = load_json(ISSUE_STATE_FILE, {})
    security_state = load_json(SECURITY_STATE_FILE, {})

    issue_model = build_issue_model(issue_state)
    security_model = build_security_model(security_state)
    health_rows = build_health_rows(issue_model, security_model)

    last_issue_update = max(
        [parse_dt(item.get("last_checked")) for item in issue_model["repos"] if item.get("last_checked")],
        default=None,
    )
    last_security_update = parse_dt(security_model.get("generated_at"))
    last_updated = max([item for item in [last_issue_update, last_security_update] if item], default=datetime.now(timezone.utc))

    overview_repo_labels = [repo["short_name"] for repo in issue_model["repos"]]
    overview_issue_counts = [repo["open_count"] for repo in issue_model["repos"]]
    overview_dependabot_counts = [
        next((sec["dependabot_total"] for sec in security_model["repos"] if sec["repo_key"] == repo["repo_key"]), 0)
        for repo in issue_model["repos"]
    ]
    overview_code_counts = [
        next((sec["code_total"] for sec in security_model["repos"] if sec["repo_key"] == repo["repo_key"]), 0)
        for repo in issue_model["repos"]
    ]
    owner_labels = [row["owner"] for row in issue_model["owners"]]
    owner_values = [row["count"] for row in issue_model["owners"]]
    dep_sev_counts = security_model["totals"]["dependabot_severity_counts"]
    eco_counts = security_model["totals"]["ecosystem_counts"]

    html_output = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GSA Unified Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
    <link rel="stylesheet" href="https://res.cdn.office.net/files/fabric-cdn-prod_20230815.002/css/fabric.min.css">
    <style>
        :root {{
            --colorNeutralBackground1: #FAF9F8;
            --colorNeutralBackground2: #FFFFFF;
            --colorNeutralBackground3: #F3F2F1;
            --colorNeutralBackground4: #EDEBE9;
            --colorNeutralForeground1: #242424;
            --colorNeutralForeground2: #424242;
            --colorNeutralForeground3: #616161;
            --colorNeutralForegroundDisabled: #A19F9D;
            --colorNeutralStroke1: #E1DFDD;
            --colorNeutralStroke2: #EDEBE9;
            --colorBrandBackground: #0078D4;
            --colorBrandBackgroundPressed: #005A9E;
            --colorBrandForeground1: #0078D4;
            --colorStatusDangerForeground1: #004E8C;
            --colorStatusDangerBackground1: #E8F1FA;
            --colorPaletteOrangeForeground: #0F548C;
            --colorPaletteOrangeBackground: #EBF3FC;
            --colorStatusWarningForeground1: #2B6CB0;
            --colorStatusWarningBackground1: #EFF6FC;
            --colorStatusSuccessForeground1: #107C10;
            --colorStatusSuccessBackground1: #F1FAF1;
            --colorNeutralForegroundNote: #4A8BC2;
            --colorNeutralBackgroundNote: #EFF6FC;
            --shadow2: 0 1px 2px rgba(0,0,0,0.06), 0 0 1px rgba(0,0,0,0.04);
            --shadow4: 0 2px 4px rgba(0,0,0,0.07), 0 0 1px rgba(0,0,0,0.05);
            --shadow8: 0 4px 8px rgba(0,0,0,0.08), 0 0 2px rgba(0,0,0,0.04);
            --borderRadiusLarge: 8px;
            --borderRadiusXLarge: 12px;
            --fontFamilyBase: 'Segoe UI', 'Segoe UI Web (West European)', -apple-system, BlinkMacSystemFont, Roboto, 'Helvetica Neue', sans-serif;
            --bg: var(--colorNeutralBackground1);
            --surface: var(--colorNeutralBackground2);
            --surface2: var(--colorNeutralBackground3);
            --border: var(--colorNeutralStroke1);
            --borderSubtle: var(--colorNeutralStroke2);
            --text: var(--colorNeutralForeground1);
            --textSecondary: var(--colorNeutralForeground2);
            --textTertiary: var(--colorNeutralForeground3);
            --accent: var(--colorBrandForeground1);
            --critical: var(--colorStatusDangerForeground1);
            --high: var(--colorPaletteOrangeForeground);
            --medium: var(--colorStatusWarningForeground1);
            --low: var(--colorStatusSuccessForeground1);
            --note: var(--colorNeutralForegroundNote);
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: var(--fontFamilyBase);
            background: var(--bg);
            color: var(--text);
            line-height: 1.5;
        }}
        .header {{
            background: linear-gradient(135deg, #0F2027 0%, #0078D4 50%, #00BCF2 100%);
        }}
        .header-band {{
            background: rgba(0,0,0,0.12);
            padding: 10px 28px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .ms-logo {{
            display: grid;
            grid-template-columns: 10px 10px;
            grid-template-rows: 10px 10px;
            gap: 2px;
        }}
        .ms-logo .sq1 {{ background: #F25022; }}
        .ms-logo .sq2 {{ background: #7FBA00; }}
        .ms-logo .sq3 {{ background: #00A4EF; }}
        .ms-logo .sq4 {{ background: #FFB900; }}
        .ms-logo span {{ display: block; }}
        .header-content {{
            max-width: 1480px;
            margin: 0 auto;
            padding: 26px 28px 30px;
            display: flex;
            gap: 20px;
            justify-content: space-between;
            align-items: flex-end;
        }}
        .header h1 {{
            color: #fff;
            font-size: 30px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 14px;
        }}
        .header-subtitle, .header-meta {{
            color: rgba(255,255,255,0.84);
            font-size: 13px;
        }}
        .header-meta {{ text-align: right; }}
        .container {{
            max-width: 1480px;
            margin: 0 auto;
            padding: 28px;
        }}
        .tabs {{
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            margin-bottom: 24px;
        }}
        .tab-button {{
            background: var(--surface);
            border: 1px solid var(--border);
            color: var(--textSecondary);
            padding: 10px 18px;
            border-radius: 999px;
            cursor: pointer;
            font-weight: 600;
            box-shadow: var(--shadow2);
        }}
        .tab-button.active {{
            background: var(--colorBrandBackground);
            color: #fff;
            border-color: var(--colorBrandBackground);
        }}
        .tab-panel {{ display: none; }}
        .tab-panel.active {{ display: block; }}
        .kpi-row {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
            margin-bottom: 22px;
        }}
        .kpi-card {{
            background: var(--surface);
            border: 1px solid var(--borderSubtle);
            border-radius: var(--borderRadiusXLarge);
            padding: 20px 22px;
            box-shadow: var(--shadow4);
        }}
        .kpi-value {{ font-size: 34px; font-weight: 700; }}
        .kpi-label {{
            color: var(--textTertiary);
            font-size: 12px;
            letter-spacing: 0.7px;
            text-transform: uppercase;
        }}
        .kpi-subtext {{ color: var(--textTertiary); font-size: 12px; margin-top: 6px; }}
        .charts-row {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 18px;
            margin-bottom: 18px;
        }}
        .chart-card, .table-card {{
            background: var(--surface);
            border: 1px solid var(--borderSubtle);
            border-radius: var(--borderRadiusXLarge);
            box-shadow: var(--shadow4);
            overflow: hidden;
        }}
        .chart-card h3, .table-header h3 {{
            padding: 18px 20px 0;
            font-size: 18px;
        }}
        .table-header {{
            padding: 0 20px 14px;
            border-bottom: 1px solid var(--borderSubtle);
        }}
        .chart-container {{
            height: 320px;
            padding: 16px 18px 18px;
        }}
        .section-title {{
            font-size: 22px;
            font-weight: 700;
            color: var(--text);
            margin: 18px 0 12px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .badge {{
            background: var(--surface2);
            border-radius: 999px;
            padding: 4px 10px;
            font-size: 12px;
            color: var(--textSecondary);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}
        th, td {{
            padding: 12px 14px;
            border-bottom: 1px solid var(--borderSubtle);
            text-align: left;
            vertical-align: top;
        }}
        thead th {{
            color: var(--textTertiary);
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            background: var(--surface);
            position: sticky;
            top: 0;
        }}
        tr:hover td {{ background: #FCFCFB; }}
        .repo-name {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
        .repo-name:hover {{ text-decoration: underline; }}
        .text-wrap {{ max-width: 420px; word-break: break-word; }}
        .sev-badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 600;
            margin-right: 4px;
            margin-bottom: 4px;
        }}
        .sev-badge.critical {{ background: var(--colorStatusDangerBackground1); color: var(--critical); }}
        .sev-badge.high {{ background: var(--colorPaletteOrangeBackground); color: var(--high); }}
        .sev-badge.medium {{ background: var(--colorStatusWarningBackground1); color: var(--medium); }}
        .sev-badge.low {{ background: var(--colorStatusSuccessBackground1); color: var(--low); }}
        .sev-badge.note {{ background: var(--colorNeutralBackgroundNote); color: var(--note); }}
        .sev-badge.clean {{ background: var(--colorStatusSuccessBackground1); color: var(--low); }}
        .risk-indicator {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 5px 11px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 700;
        }}
        .risk-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: currentColor;
            display: inline-block;
        }}
        .risk-indicator.risk-critical {{ background: var(--colorStatusDangerBackground1); color: var(--critical); }}
        .risk-indicator.risk-high {{ background: var(--colorPaletteOrangeBackground); color: var(--high); }}
        .risk-indicator.risk-medium {{ background: var(--colorStatusWarningBackground1); color: var(--medium); }}
        .risk-indicator.risk-low {{ background: var(--colorStatusSuccessBackground1); color: var(--low); }}
        .bar-cell {{ min-width: 200px; }}
        .severity-bar {{
            height: 10px;
            border-radius: 999px;
            background: #e8e8e8;
            overflow: hidden;
            display: flex;
        }}
        .seg-brand {{ background: linear-gradient(90deg, #0078D4, #00BCF2); }}
        .footer {{
            text-align: center;
            color: var(--textTertiary);
            font-size: 12px;
            padding: 24px 0 16px;
        }}
        @media (max-width: 900px) {{
            .header-content {{ flex-direction: column; align-items: flex-start; }}
            .header-meta {{ text-align: left; }}
            .container {{ padding: 18px; }}
            th, td {{ padding: 10px; }}
            .text-wrap {{ max-width: 260px; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="header-band">
            <div class="ms-logo">
                <span class="sq1"></span><span class="sq2"></span>
                <span class="sq3"></span><span class="sq4"></span>
            </div>
            <span style="color:white;font-size:13px;font-weight:600;">Microsoft</span>
        </div>
        <div class="header-content">
            <div>
                <h1><span style="font-size:30px;">📊</span> GSA Unified Portfolio Dashboard</h1>
                <div class="header-subtitle">Issue tracking, security posture, and cross-repo operational overview</div>
            </div>
            <div class="header-meta">
                <div>{len(REPOS)} repositories tracked</div>
                <div>Issue tracker state: {escape(fmt_ts(last_issue_update.isoformat() if last_issue_update else None))}</div>
                <div>Security state: {escape(fmt_ts(security_model.get("generated_at")))}</div>
                <div>Last updated: {escape(last_updated.astimezone(IST).strftime("%d %b %Y · %I:%M %p IST"))}</div>
            </div>
        </div>
    </div>

    <div class="container">
        <div class="tabs">
            <button class="tab-button active" data-tab="overview">Overview</button>
            <button class="tab-button" data-tab="issues">Issue Tracking</button>
            <button class="tab-button" data-tab="security">Security &amp; Code Quality</button>
        </div>

        <section class="tab-panel active" id="overview">
            <div class="kpi-row">
                <div class="kpi-card"><div class="kpi-value">{issue_model["totals"]["open"]}</div><div class="kpi-label">Open Issues</div><div class="kpi-subtext">{issue_model["totals"]["overdue"]} overdue follow-ups</div></div>
                <div class="kpi-card"><div class="kpi-value">{security_model["totals"]["dependabot_open"]}</div><div class="kpi-label">Dependabot Alerts</div><div class="kpi-subtext">{security_model["totals"]["code_scanning_open"]} code scanning alerts</div></div>
                <div class="kpi-card"><div class="kpi-value">{security_model["totals"]["all_open_findings"]}</div><div class="kpi-label">Total Security Findings</div><div class="kpi-subtext">{issue_model["totals"]["bug"]} bug-labeled issues</div></div>
                <div class="kpi-card"><div class="kpi-value">{len([row for row in health_rows if row["risk"] in ("critical", "high")])}</div><div class="kpi-label">High Attention Repos</div><div class="kpi-subtext">{len(REPOS)} total repositories</div></div>
            </div>

            <div class="charts-row">
                <div class="chart-card"><h3>Portfolio Activity by Repository</h3><div class="chart-container"><canvas id="overviewRepoChart"></canvas></div></div>
                <div class="chart-card"><h3>Owner Workload</h3><div class="chart-container"><canvas id="ownerWorkloadChart"></canvas></div></div>
            </div>

            <div class="charts-row">
                <div class="chart-card"><h3>Dependabot Ecosystem Mix</h3><div class="chart-container"><canvas id="ecosystemChart"></canvas></div></div>
                <div class="chart-card"><h3>Issue Mix</h3><div class="chart-container"><canvas id="issueTypeChart"></canvas></div></div>
            </div>

            <div class="section-title">Portfolio Health <span class="badge">{len(health_rows)} repositories</span></div>
            <div class="table-card">
                <div class="table-header"><h3>Combined Repository View</h3></div>
                <table>
                    <thead><tr><th>Repository</th><th>Issues</th><th>Overdue</th><th>Dependabot</th><th>Code Scanning</th><th>Risk</th><th>Primary Owner</th></tr></thead>
                    <tbody>{render_health_rows(health_rows)}</tbody>
                </table>
            </div>
        </section>

        <section class="tab-panel" id="issues">
            <div class="kpi-row">
                <div class="kpi-card"><div class="kpi-value">{issue_model["totals"]["open"]}</div><div class="kpi-label">Open Issues</div></div>
                <div class="kpi-card"><div class="kpi-value">{issue_model["totals"]["bug"]}</div><div class="kpi-label">Bug Labeled</div></div>
                <div class="kpi-card"><div class="kpi-value">{issue_model["totals"]["enhancement"]}</div><div class="kpi-label">Enhancements</div></div>
                <div class="kpi-card"><div class="kpi-value">{issue_model["totals"]["overdue"]}</div><div class="kpi-label">Overdue Follow-ups</div></div>
            </div>

            <div class="charts-row">
                <div class="chart-card"><h3>Open Issues by Repository</h3><div class="chart-container"><canvas id="issueRepoChart"></canvas></div></div>
                <div class="chart-card"><h3>Issue Classification</h3><div class="chart-container"><canvas id="issueTypeChartSecondary"></canvas></div></div>
            </div>

            <div class="section-title">Repository Summary <span class="badge">{issue_model["totals"]["open"]} open</span></div>
            <div class="table-card">
                <div class="table-header"><h3>Owner Assignment and Follow-up Status</h3></div>
                <table>
                    <thead><tr><th>Repository</th><th>Open</th><th>Bugs</th><th>Enhancements</th><th>Other</th><th>Overdue</th><th>Oldest</th><th>Primary Owner</th><th>Secondary Owner</th><th>Last Checked</th></tr></thead>
                    <tbody>{render_issue_repo_rows(issue_model)}</tbody>
                </table>
            </div>

            <div class="section-title">Open Issue Detail <span class="badge">Top 75 by age</span></div>
            <div class="table-card">
                <div class="table-header"><h3>Tracked Issues</h3></div>
                <table>
                    <thead><tr><th>Issue</th><th>Repository</th><th>Title</th><th>Reported By</th><th>Labels</th><th>Age</th><th>Status</th><th>ADO Bug</th></tr></thead>
                    <tbody>{render_issue_detail_rows(issue_model)}</tbody>
                </table>
            </div>

            <div class="section-title">Primary Owner Load</div>
            <div class="table-card">
                <div class="table-header"><h3>Open Issue Distribution</h3></div>
                <table>
                    <thead><tr><th>Owner</th><th>Open Issues</th><th>Relative Load</th></tr></thead>
                    <tbody>{render_owner_rows(issue_model)}</tbody>
                </table>
            </div>
        </section>

        <section class="tab-panel" id="security">
            <div class="kpi-row">
                <div class="kpi-card"><div class="kpi-value">{security_model["totals"]["all_open_findings"]}</div><div class="kpi-label">All Open Findings</div></div>
                <div class="kpi-card"><div class="kpi-value">{security_model["totals"]["dependabot_open"]}</div><div class="kpi-label">Dependabot Alerts</div></div>
                <div class="kpi-card"><div class="kpi-value">{security_model["totals"]["code_scanning_open"]}</div><div class="kpi-label">Code Scanning Alerts</div></div>
                <div class="kpi-card"><div class="kpi-value">{security_model["totals"]["dependabot_severity_counts"].get("critical", 0) + security_model["totals"]["dependabot_severity_counts"].get("high", 0)}</div><div class="kpi-label">Critical + High Dependabot</div></div>
            </div>

            <div class="charts-row">
                <div class="chart-card"><h3>Dependabot Severity Breakdown</h3><div class="chart-container"><canvas id="dependabotSeverityChart"></canvas></div></div>
                <div class="chart-card"><h3>Code Scanning Categories</h3><div class="chart-container"><canvas id="codeCategoryChart"></canvas></div></div>
            </div>

            <div class="section-title">Repository Security Summary <span class="badge">{security_model["totals"]["all_open_findings"]} findings</span></div>
            <div class="table-card">
                <div class="table-header"><h3>Dependabot and Code Scanning by Repository</h3></div>
                <table>
                    <thead><tr><th>Repository</th><th>Dependabot</th><th>Dependabot Severity</th><th>Dependabot Risk</th><th>Code Scanning</th><th>Code Categories</th><th>Code Risk</th></tr></thead>
                    <tbody>{render_security_repo_rows(security_model)}</tbody>
                </table>
            </div>

            <div class="section-title">Dependabot Detail <span class="badge">Top 50</span></div>
            <div class="table-card">
                <div class="table-header"><h3>Open Dependabot Alerts</h3></div>
                <table>
                    <thead><tr><th>Repository</th><th>Alert</th><th>Severity</th><th>Package</th><th>Ecosystem</th><th>Summary</th><th>Created</th></tr></thead>
                    <tbody>{render_dependabot_rows(security_model)}</tbody>
                </table>
            </div>

            <div class="section-title">Code Scanning Detail <span class="badge">Top 50</span></div>
            <div class="table-card">
                <div class="table-header"><h3>Open Code Scanning Alerts</h3></div>
                <table>
                    <thead><tr><th>Repository</th><th>Alert</th><th>Severity</th><th>Category</th><th>Rule Id</th><th>Rule</th><th>Path</th></tr></thead>
                    <tbody>{render_code_rows(security_model)}</tbody>
                </table>
            </div>
        </section>

        <div class="footer">Data sourced from issue tracker state and GitHub security APIs · Generated {escape(last_updated.astimezone(IST).strftime("%d %b %Y · %I:%M %p IST"))}</div>
    </div>

    <script>
        const fluentBrand = '#0078D4';
        const fluentBrandLight = '#83BDE6';
        const fluentDanger = '#004E8C';
        const fluentOrange = '#0F548C';
        const fluentWarning = '#2B6CB0';
        const fluentSuccess = '#107C10';
        const fluentNote = '#4A8BC2';
        const fluentTextMuted = '#616161';
        const fluentGridColor = 'rgba(0,0,0,0.06)';
        Chart.defaults.color = fluentTextMuted;
        Chart.defaults.borderColor = fluentGridColor;
        Chart.defaults.font = {{ family: "'Segoe UI', sans-serif" }};

        const charts = [];
        charts.push(new Chart(document.getElementById('overviewRepoChart'), {{
            type: 'bar',
            data: {{
                labels: {json.dumps(overview_repo_labels)},
                datasets: [
                    {{ label: 'Open Issues', data: {json.dumps(overview_issue_counts)}, backgroundColor: fluentBrand, borderRadius: 4 }},
                    {{ label: 'Dependabot', data: {json.dumps(overview_dependabot_counts)}, backgroundColor: fluentBrandLight, borderRadius: 4 }},
                    {{ label: 'Code Scanning', data: {json.dumps(overview_code_counts)}, backgroundColor: '#B4D5EE', borderRadius: 4 }}
                ]
            }},
            options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ position: 'bottom' }} }} }}
        }}));
        charts.push(new Chart(document.getElementById('ownerWorkloadChart'), {{
            type: 'bar',
            data: {{
                labels: {json.dumps(owner_labels)},
                datasets: [{{ label: 'Open Issues', data: {json.dumps(owner_values)}, backgroundColor: fluentBrand, borderRadius: 4 }}]
            }},
            options: {{ indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }} }}
        }}));
        charts.push(new Chart(document.getElementById('ecosystemChart'), {{
            type: 'doughnut',
            data: {{
                labels: {json.dumps(list(eco_counts.keys()) or ['n/a'])},
                datasets: [{{ data: {json.dumps(list(eco_counts.values()) or [1])}, backgroundColor: [fluentBrand, fluentBrandLight, '#B4D5EE', '#D7EBFA'] }}]
            }},
            options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ position: 'bottom' }} }} }}
        }}));
        const issueTypeData = {json.dumps([issue_model["totals"]["bug"], issue_model["totals"]["enhancement"], issue_model["totals"]["other"]])};
        charts.push(new Chart(document.getElementById('issueTypeChart'), {{
            type: 'doughnut',
            data: {{
                labels: ['Bug', 'Enhancement', 'Other'],
                datasets: [{{ data: issueTypeData, backgroundColor: [fluentDanger, fluentSuccess, fluentBrand] }}]
            }},
            options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ position: 'bottom' }} }} }}
        }}));
        charts.push(new Chart(document.getElementById('issueRepoChart'), {{
            type: 'bar',
            data: {{
                labels: {json.dumps(overview_repo_labels)},
                datasets: [{{ label: 'Open Issues', data: {json.dumps(overview_issue_counts)}, backgroundColor: fluentBrand, borderRadius: 4 }}]
            }},
            options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }} }}
        }}));
        charts.push(new Chart(document.getElementById('issueTypeChartSecondary'), {{
            type: 'pie',
            data: {{
                labels: ['Bug', 'Enhancement', 'Other'],
                datasets: [{{ data: issueTypeData, backgroundColor: [fluentDanger, fluentSuccess, fluentBrand] }}]
            }},
            options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ position: 'bottom' }} }} }}
        }}));
        charts.push(new Chart(document.getElementById('dependabotSeverityChart'), {{
            type: 'doughnut',
            data: {{
                labels: ['Critical', 'High', 'Medium', 'Low'],
                datasets: [{{
                    data: {json.dumps([dep_sev_counts.get("critical", 0), dep_sev_counts.get("high", 0), dep_sev_counts.get("medium", 0), dep_sev_counts.get("low", 0)])},
                    backgroundColor: [fluentDanger, fluentOrange, fluentWarning, fluentSuccess]
                }}]
            }},
            options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ position: 'bottom' }} }} }}
        }}));
        charts.push(new Chart(document.getElementById('codeCategoryChart'), {{
            type: 'bar',
            data: {{
                labels: {json.dumps(overview_repo_labels)},
                datasets: [
                    {{
                        label: 'Standard',
                        data: {json.dumps([next((sec["code_categories"].get("standard", 0) for sec in security_model["repos"] if sec["repo_key"] == repo["repo_key"]), 0) for repo in issue_model["repos"]])},
                        backgroundColor: fluentOrange,
                        borderRadius: 4
                    }},
                    {{
                        label: 'Microsoft Pack',
                        data: {json.dumps([next((sec["code_categories"].get("microsoft_pack", 0) for sec in security_model["repos"] if sec["repo_key"] == repo["repo_key"]), 0) for repo in issue_model["repos"]])},
                        backgroundColor: '#B4D5EE',
                        borderRadius: 4
                    }}
                ]
            }},
            options: {{ responsive: true, maintainAspectRatio: false, scales: {{ x: {{ stacked: true }}, y: {{ stacked: true }} }}, plugins: {{ legend: {{ position: 'bottom' }} }} }}
        }}));

        const tabButtons = document.querySelectorAll('.tab-button');
        const tabPanels = document.querySelectorAll('.tab-panel');
        tabButtons.forEach(button => {{
            button.addEventListener('click', () => {{
                tabButtons.forEach(item => item.classList.remove('active'));
                tabPanels.forEach(item => item.classList.remove('active'));
                button.classList.add('active');
                document.getElementById(button.dataset.tab).classList.add('active');
                setTimeout(() => charts.forEach(chart => chart.resize()), 50);
            }});
        }});
    </script>
</body>
</html>"""

    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as handle:
        handle.write(html_output)
    print(f"Dashboard written to {OUTPUT_HTML}")


if __name__ == "__main__":
    generate_dashboard()
