import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

SRC_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = SRC_DIR.parents[0]
sys.path.insert(0, str(SRC_DIR))

from issue_tracker.helpers import REPOS  # noqa: E402

STATE_FILE = Path(os.environ.get("SECURITY_STATE_FILE", ROOT_DIR / "state" / "security_state.json"))
API_BASE = "https://api.github.com"
TIMEOUT = 30

DEPENDABOT_SEVERITIES = ("critical", "high", "medium", "low")
CODE_SCANNING_SEVERITIES = ("critical", "high", "medium", "low", "warning", "note", "error")


def gh_headers():
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_PAT", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def parse_github_datetime(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def wait_for_rate_limit(response):
    remaining = response.headers.get("X-RateLimit-Remaining")
    reset = response.headers.get("X-RateLimit-Reset")
    if remaining == "0" and reset:
        sleep_for = max(int(reset) - int(time.time()) + 2, 2)
        print(f"Rate limit reached, sleeping for {sleep_for}s...")
        time.sleep(sleep_for)
        return True
    return False


def github_get_paginated(url, params=None):
    params = dict(params or {})
    params.setdefault("per_page", 100)
    page = 1
    items = []
    while True:
        request_params = dict(params, page=page)
        response = requests.get(url, headers=gh_headers(), params=request_params, timeout=TIMEOUT)

        if response.status_code == 403 and wait_for_rate_limit(response):
            continue
        if response.status_code == 401:
            return {"status": "error", "items": [], "error": "Authentication required"}
        if response.status_code == 404:
            return {"status": "unavailable", "items": [], "error": "GHAS or endpoint not enabled"}
        if response.status_code == 451:
            return {"status": "unavailable", "items": [], "error": "Access restricted"}
        if response.status_code >= 400:
            return {
                "status": "error",
                "items": [],
                "error": f"{response.status_code}: {response.text[:300]}",
            }

        batch = response.json()
        if not isinstance(batch, list):
            return {"status": "error", "items": [], "error": "Unexpected API payload"}

        items.extend(batch)
        if len(batch) < params["per_page"]:
            break
        page += 1
    return {"status": "ok", "items": items, "error": None}


def normalize_dependabot_severity(alert):
    severity = (
        (alert.get("security_vulnerability") or {}).get("severity")
        or (alert.get("security_advisory") or {}).get("severity")
        or "unknown"
    )
    severity = str(severity).lower()
    return severity if severity in DEPENDABOT_SEVERITIES else "unknown"


def normalize_code_scanning_severity(alert):
    rule = alert.get("rule") or {}
    severity = (
        rule.get("security_severity_level")
        or rule.get("severity")
        or alert.get("severity")
        or alert.get("rule", {}).get("problem", {}).get("severity")
        or "note"
    )
    severity = str(severity).lower()
    if severity == "error":
        return "high"
    return severity if severity in CODE_SCANNING_SEVERITIES else "note"


def detect_code_scanning_category(alert):
    haystack = " ".join(
        str(value)
        for value in [
            alert.get("tool", {}).get("name"),
            alert.get("tool", {}).get("guid"),
            alert.get("rule", {}).get("id"),
            alert.get("rule", {}).get("name"),
            alert.get("rule", {}).get("description"),
            " ".join(alert.get("rule", {}).get("tags", []) or []),
        ]
        if value
    ).lower()
    custom_terms = ("microsoft", "custom", "ai", "copilot")
    return "microsoft_pack" if any(term in haystack for term in custom_terms) else "standard"


def repo_risk_level(severity_counts):
    if severity_counts.get("critical", 0):
        return "critical"
    if severity_counts.get("high", 0):
        return "high"
    if severity_counts.get("medium", 0) or severity_counts.get("warning", 0):
        return "medium"
    return "low"


def summarize_dependabot(owner, repo):
    result = github_get_paginated(
        f"{API_BASE}/repos/{owner}/{repo}/dependabot/alerts",
        params={"state": "open"},
    )
    alerts = result["items"]
    severity_counts = Counter({key: 0 for key in DEPENDABOT_SEVERITIES})
    ecosystem_counts = Counter()
    details = []

    for alert in alerts:
        severity = normalize_dependabot_severity(alert)
        if severity in severity_counts:
            severity_counts[severity] += 1
        package = (alert.get("dependency") or {}).get("package") or {}
        ecosystem = package.get("ecosystem") or "unknown"
        ecosystem_counts[ecosystem] += 1
        advisory = alert.get("security_advisory") or {}
        details.append(
            {
                "number": alert.get("number"),
                "html_url": alert.get("html_url"),
                "severity": severity,
                "summary": advisory.get("summary") or advisory.get("description") or "Dependabot alert",
                "package_name": package.get("name") or "unknown",
                "ecosystem": ecosystem,
                "manifest_path": (alert.get("dependency") or {}).get("manifest_path"),
                "created_at": alert.get("created_at"),
                "updated_at": alert.get("updated_at"),
                "advisory_ghsa_id": advisory.get("ghsa_id"),
            }
        )

    details.sort(key=lambda item: (item["severity"], item.get("created_at") or ""), reverse=True)
    return {
        "available": result["status"] == "ok",
        "status": result["status"],
        "error": result["error"],
        "total_open": len(alerts),
        "severity_counts": dict(severity_counts),
        "ecosystem_counts": dict(sorted(ecosystem_counts.items(), key=lambda item: (-item[1], item[0]))),
        "alerts": details,
        "risk_level": repo_risk_level(severity_counts),
    }


def summarize_code_scanning(owner, repo):
    result = github_get_paginated(
        f"{API_BASE}/repos/{owner}/{repo}/code-scanning/alerts",
        params={"state": "open"},
    )
    alerts = result["items"]
    severity_counts = Counter({key: 0 for key in CODE_SCANNING_SEVERITIES})
    category_counts = Counter({"standard": 0, "microsoft_pack": 0})
    details = []

    for alert in alerts:
        severity = normalize_code_scanning_severity(alert)
        category = detect_code_scanning_category(alert)
        if severity in severity_counts:
            severity_counts[severity] += 1
        category_counts[category] += 1
        instance = alert.get("most_recent_instance") or {}
        location = instance.get("location") or {}
        details.append(
            {
                "number": alert.get("number"),
                "html_url": alert.get("html_url"),
                "severity": severity,
                "category": category,
                "state": alert.get("state"),
                "rule_id": (alert.get("rule") or {}).get("id"),
                "rule_name": (alert.get("rule") or {}).get("description") or (alert.get("rule") or {}).get("name"),
                "tool_name": (alert.get("tool") or {}).get("name"),
                "path": location.get("path"),
                "start_line": location.get("start_line"),
                "created_at": alert.get("created_at"),
                "updated_at": alert.get("updated_at"),
            }
        )

    details.sort(key=lambda item: (item["severity"], item.get("created_at") or ""), reverse=True)
    return {
        "available": result["status"] == "ok",
        "status": result["status"],
        "error": result["error"],
        "total_open": len(alerts),
        "severity_counts": dict(severity_counts),
        "category_counts": dict(category_counts),
        "alerts": details,
        "risk_level": repo_risk_level(severity_counts),
    }


def deduped_repos():
    seen = set()
    merged = []
    for cfg in REPOS:
        key = f"{cfg['owner']}/{cfg['repo']}"
        if key not in seen:
            seen.add(key)
            merged.append(cfg)
    return merged


def build_state():
    started_at = datetime.now(timezone.utc)
    repos = []
    total_dep = Counter({key: 0 for key in DEPENDABOT_SEVERITIES})
    total_code = Counter({key: 0 for key in CODE_SCANNING_SEVERITIES})
    total_ecosystems = Counter()
    total_categories = Counter({"standard": 0, "microsoft_pack": 0})

    for cfg in deduped_repos():
        owner = cfg["owner"]
        repo = cfg["repo"]
        repo_key = f"{owner}/{repo}"
        print(f"Scanning security data for {repo_key}")

        dependabot = summarize_dependabot(owner, repo)
        code_scanning = summarize_code_scanning(owner, repo)

        for key, value in dependabot["severity_counts"].items():
            total_dep[key] += value
        for key, value in dependabot["ecosystem_counts"].items():
            total_ecosystems[key] += value
        for key, value in code_scanning["severity_counts"].items():
            total_code[key] += value
        for key, value in code_scanning["category_counts"].items():
            total_categories[key] += value

        repos.append(
            {
                "name": cfg["name"],
                "owner": owner,
                "repo": repo,
                "repo_key": repo_key,
                "repo_url": f"https://github.com/{repo_key}",
                "primary_owner": cfg["primary"]["name"],
                "secondary_owner": cfg["secondary"]["name"],
                "dependabot": dependabot,
                "code_scanning": code_scanning,
            }
        )

    completed_at = datetime.now(timezone.utc)
    state = {
        "generated_at": completed_at.isoformat(),
        "scan_started_at": started_at.isoformat(),
        "scan_duration_seconds": round((completed_at - started_at).total_seconds(), 2),
        "repo_count": len(repos),
        "repos": repos,
        "totals": {
            "dependabot_open": sum(repo["dependabot"]["total_open"] for repo in repos),
            "code_scanning_open": sum(repo["code_scanning"]["total_open"] for repo in repos),
            "all_open_findings": sum(repo["dependabot"]["total_open"] + repo["code_scanning"]["total_open"] for repo in repos),
            "dependabot_severity_counts": dict(total_dep),
            "code_scanning_severity_counts": dict(total_code),
            "ecosystem_counts": dict(sorted(total_ecosystems.items(), key=lambda item: (-item[1], item[0]))),
            "code_scanning_category_counts": dict(total_categories),
        },
    }
    return state


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)


if __name__ == "__main__":
    save_state(build_state())
    print(f"Security state saved to {STATE_FILE}")
