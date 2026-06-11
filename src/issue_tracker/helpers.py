"""
Cloud-native helpers for Azure Functions — GitHub Issue Tracker.
All integrations via REST APIs — ZERO PATs required:
  - GitHub REST API    → GitHub App (auto-rotating installation tokens)
  - ADO REST API       → Managed Identity (Azure handles token lifecycle)
  - Microsoft Graph    → App Registration client-credentials
  - Azure Blob Storage → Connection string (from Function App settings)
"""

import json
import os
import logging
import base64
import re
import time
import threading
from datetime import datetime, timezone, timedelta
from urllib.parse import quote as urlquote

import jwt  # PyJWT — for GitHub App JWT signing
import requests

# Azure SDK imports — optional (only needed for Azure Functions, not GitHub Actions)
try:
    from azure.storage.blob import BlobServiceClient, ContentSettings
    from azure.identity import DefaultAzureCredential
    _HAS_AZURE_SDK = True
except ImportError:
    _HAS_AZURE_SDK = False

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# ENVIRONMENT + CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

def env(key, default=None):
    val = os.environ.get(key, default)
    if val is None:
        raise EnvironmentError(f"Missing required env var: {key}")
    return val


REPOS = [
    {"name": "Customer Chatbot", "owner": "microsoft",
     "repo": "customer-chatbot-solution-accelerator",
     "primary": {"name": "Ajit Padhi", "email": "v-padhiajit@microsoft.com"},
     "secondary": {"name": "Prajwal D C", "email": "v-dcprajwal@microsoft.com"}},
    {"name": "Code Modernization", "owner": "microsoft",
     "repo": "Modernize-your-code-solution-accelerator",
     "primary": {"name": "Priyanka Singhal", "email": "v-prisinghal@microsoft.com"},
     "secondary": {"name": "Shreyas Waikar", "email": "v-swaikar@microsoft.com"}},
    {"name": "Container Migration", "owner": "microsoft",
     "repo": "Container-Migration-Solution-Accelerator",
     "primary": {"name": "Shreyas Waikar", "email": "v-swaikar@microsoft.com"},
     "secondary": {"name": "Priyanka Singhal", "email": "v-prisinghal@microsoft.com"}},
    {"name": "Content Generation", "owner": "microsoft",
     "repo": "content-generation-solution-accelerator",
     "primary": {"name": "Ragini Chauragade", "email": "v-raginich@microsoft.com"},
     "secondary": {"name": "Pavan Kumar", "email": "v-kupavan@microsoft.com"}},
    {"name": "Content Processing", "owner": "microsoft",
     "repo": "content-processing-solution-accelerator",
     "primary": {"name": "Shreyas Waikar", "email": "v-swaikar@microsoft.com"},
     "secondary": {"name": "Ajit Padhi", "email": "v-padhiajit@microsoft.com"}},
    {"name": "CWYD", "owner": "Azure-Samples",
     "repo": "chat-with-your-data-solution-accelerator",
     "primary": {"name": "Ajit Padhi", "email": "v-padhiajit@microsoft.com"},
     "secondary": {"name": "Priyanka Singhal", "email": "v-prisinghal@microsoft.com"}},
    {"name": "Data & Agent Governance", "owner": "microsoft",
     "repo": "Data-and-Agent-Governance-and-Security-Accelerator",
     "primary": {"name": "Saswato Chatterjee", "email": "v-saswatoc@microsoft.com"},
     "secondary": {"name": "Yamini", "email": "v-yamini3@microsoft.com"}},
    {"name": "Deploy AI App", "owner": "microsoft",
     "repo": "Deploy-Your-AI-Application-In-Production",
     "primary": {"name": "Saswato Chatterjee", "email": "v-saswatoc@microsoft.com"},
     "secondary": {"name": "Yamini", "email": "v-yamini3@microsoft.com"}},
    {"name": "DKM", "owner": "microsoft",
     "repo": "Document-Knowledge-Mining-Solution-Accelerator",
     "primary": {"name": "Priyanka Singhal", "email": "v-prisinghal@microsoft.com"},
     "secondary": {"name": "Ajit Padhi", "email": "v-padhiajit@microsoft.com"}},
    {"name": "Agentic App", "owner": "microsoft",
     "repo": "agentic-applications-for-unified-data-foundation-solution-accelerator",
     "primary": {"name": "Ragini Chauragade", "email": "v-raginich@microsoft.com"},
     "secondary": {"name": "Pavan Kumar", "email": "v-kupavan@microsoft.com"}},
    {"name": "KM Generic", "owner": "microsoft",
     "repo": "Conversation-Knowledge-Mining-Solution-Accelerator",
     "primary": {"name": "Pavan Kumar", "email": "v-kupavan@microsoft.com"},
     "secondary": {"name": "Avijit Ghorui", "email": "v-aghorui@microsoft.com"}},
    {"name": "UDF", "owner": "microsoft",
     "repo": "unified-data-foundation-with-fabric-solution-accelerator",
     "primary": {"name": "Saswato Chatterjee", "email": "v-saswatoc@microsoft.com"},
     "secondary": {"name": "Yamini", "email": "v-yamini3@microsoft.com"}},
    {"name": "MACAE", "owner": "microsoft",
     "repo": "Multi-Agent-Custom-Automation-Engine-Solution-Accelerator",
     "primary": {"name": "Dhruvkumar Babariya", "email": "v-dbabariya@microsoft.com"},
     "secondary": {"name": "Abdul Mujeeb T A", "email": "v-amujeebta@microsoft.com"}},
    {"name": "RTI", "owner": "microsoft",
     "repo": "real-time-intelligence-operations-solution-accelerator",
     "primary": {"name": "Saswato Chatterjee", "email": "v-saswatoc@microsoft.com"},
     "secondary": {"name": "Yamini", "email": "v-yamini3@microsoft.com"}},
]


# ═══════════════════════════════════════════════════════════════════════════════
# STATE — Azure Blob Storage OR Local File (GitHub Actions)
# ═══════════════════════════════════════════════════════════════════════════════
# When STATE_FILE env var is set → uses file-based state (for GitHub Actions)
# Otherwise → uses Azure Blob Storage (for Azure Functions)

from pathlib import Path


def _blob_client():
    if not _HAS_AZURE_SDK:
        raise RuntimeError("azure-storage-blob not installed — set STATE_FILE for file-based state")
    conn_str  = env("AzureWebJobsStorage")
    container = env("STATE_CONTAINER", "tracker-state")
    blob_name = env("STATE_BLOB", "tracked_issues_state.json")
    svc = BlobServiceClient.from_connection_string(conn_str)
    try:
        svc.get_container_client(container).create_container()
    except Exception:
        pass
    return svc.get_blob_client(container=container, blob=blob_name)


def load_state():
    """Load tracker state — file-based (Actions) or Blob (Azure Functions)."""
    # File-based state for GitHub Actions
    state_file = os.environ.get("STATE_FILE", "")
    if state_file:
        p = Path(state_file)
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    # Azure Blob Storage for Azure Functions
    try:
        data = _blob_client().download_blob().readall()
        return json.loads(data)
    except Exception as e:
        log.warning(f"State load failed (starting fresh): {e}")
        return {}


def save_state(state):
    """Save tracker state — file-based (Actions) or Blob (Azure Functions)."""
    # File-based state for GitHub Actions
    state_file = os.environ.get("STATE_FILE", "")
    if state_file:
        p = Path(state_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        log.info(f"State saved to {p}")
        return

    # Azure Blob Storage for Azure Functions
    try:
        _blob_client().upload_blob(
            json.dumps(state, indent=2),
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json"),
        )
        log.info("State saved to blob storage")
    except Exception as e:
        log.error(f"State save failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# GITHUB REST API  —  GitHub App Auth (NO PAT — auto-rotating tokens)
# ═══════════════════════════════════════════════════════════════════════════════
#
# How it works:
#   1. Private key (stored in App Settings) signs a JWT  → valid 10 min
#   2. JWT is exchanged for an Installation Token        → valid 1 hour
#   3. Installation token is cached & auto-refreshed before expiry
#
# Setup (one-time):
#   1. Create GitHub App in your org with Issues:read permission
#   2. Generate a private key (.pem) → store contents in GITHUB_APP_PRIVATE_KEY
#   3. Install the app on microsoft + Azure-Samples orgs
#   4. Store App ID and Installation IDs in Function App Settings
#
# Required env vars:
#   GITHUB_APP_ID              — numeric App ID
#   GITHUB_APP_PRIVATE_KEY     — PEM private key (newlines as \n or literal)
#   GITHUB_APP_INSTALL_ID      — installation ID for the primary org
#   GITHUB_APP_INSTALL_ID_ALT  — (optional) installation ID for Azure-Samples org
#
# Fallback: if GITHUB_PAT is set, uses it instead (for local dev / migration)

_gh_token_cache = {"token": None, "expires": 0, "install_id": None, "lock": threading.Lock()}


def _generate_github_app_jwt():
    """Sign a short-lived JWT using the GitHub App private key."""
    app_id = env("GITHUB_APP_ID")
    private_key = env("GITHUB_APP_PRIVATE_KEY")
    # Handle escaped newlines from Azure App Settings
    private_key = private_key.replace("\\n", "\n")

    now = int(time.time())
    payload = {
        "iat": now - 60,       # issued 60s ago (clock skew buffer)
        "exp": now + 600,      # expires in 10 min (GitHub max)
        "iss": int(app_id),
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def _get_github_installation_token(install_id):
    """Exchange GitHub App JWT for an installation access token (valid 1 hour)."""
    jwt_token = _generate_github_app_jwt()
    r = requests.post(
        f"https://api.github.com/app/installations/{install_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return data["token"], data["expires_at"]


def _resolve_install_id(owner):
    """Get the GitHub App installation ID for the given org."""
    primary_id = env("GITHUB_APP_INSTALL_ID")
    alt_id = os.environ.get("GITHUB_APP_INSTALL_ID_ALT", "")
    # Azure-Samples uses the alt installation if provided
    if owner.lower() == "azure-samples" and alt_id:
        return alt_id
    return primary_id


def _gh_headers(owner="microsoft"):
    """Build GitHub API headers with auto-rotating token.
    Priority: GITHUB_TOKEN (Actions built-in) > GITHUB_PAT > GitHub App.
    """
    # Option 1: GitHub Actions built-in token (auto-provided, no secret needed)
    # Works for reading public repos with 1000 req/hr rate limit
    actions_token = os.environ.get("GITHUB_TOKEN", "")
    if actions_token:
        return {
            "Authorization": f"Bearer {actions_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    # Option 2: Explicit PAT (local dev)
    pat = os.environ.get("GITHUB_PAT", "")
    if pat:
        return {
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    # Option 3: GitHub App (Azure Functions deployment)
    app_id = os.environ.get("GITHUB_APP_ID", "")
    if app_id:
        install_id = _resolve_install_id(owner)
        now = time.time()
        with _gh_token_cache["lock"]:
            if (_gh_token_cache["token"]
                    and _gh_token_cache["install_id"] == install_id
                    and now < _gh_token_cache["expires"] - 300):
                token = _gh_token_cache["token"]
            else:
                log.info(f"  Refreshing GitHub App token for install {install_id}")
                token, expires_at = _get_github_installation_token(install_id)
                exp_ts = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp()
                _gh_token_cache["token"] = token
                _gh_token_cache["expires"] = exp_ts
                _gh_token_cache["install_id"] = install_id
        return {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    raise EnvironmentError("No GitHub auth: set GITHUB_TOKEN, GITHUB_PAT, or GITHUB_APP_ID")


def fetch_open_issues(owner, repo, retries=3):
    """Fetch all open issues (excluding PRs) with pagination."""
    for attempt in range(1, retries + 1):
        try:
            issues, page = [], 1
            while True:
                r = requests.get(
                    f"https://api.github.com/repos/{owner}/{repo}/issues",
                    params={"state": "open", "per_page": 100, "page": page},
                    headers=_gh_headers(owner), timeout=30,
                )
                r.raise_for_status()
                batch = r.json()
                if not batch:
                    break
                for item in batch:
                    if item.get("pull_request") is None:
                        issues.append({
                            "number": item["number"],
                            "title": item["title"],
                            "html_url": item["html_url"],
                            "created_at": item["created_at"],
                            "user": item["user"]["login"],
                            "labels": [l["name"] for l in item.get("labels", [])],
                            "body": item.get("body") or "",
                        })
                if len(batch) < 100:
                    break
                page += 1
            return issues
        except Exception as e:
            log.error(f"  GitHub {owner}/{repo} attempt {attempt}: {e}")
            if attempt < retries:
                time.sleep(5 * attempt)
    return None


def fetch_issue_comments(owner, repo, issue_number, retries=3):
    """Fetch comments for a specific issue."""
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(
                f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments",
                params={"per_page": 100},
                headers=_gh_headers(owner), timeout=30,
            )
            r.raise_for_status()
            return [{"user": c["user"]["login"], "created_at": c["created_at"]}
                    for c in r.json()]
        except Exception as e:
            log.error(f"  Comments {owner}/{repo}#{issue_number} attempt {attempt}: {e}")
            if attempt < retries:
                time.sleep(5 * attempt)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# ADO REST API  —  Managed Identity Auth (NO PAT — Azure handles everything)
# ═══════════════════════════════════════════════════════════════════════════════
#
# How it works:
#   1. Function App has System-Assigned Managed Identity enabled
#   2. DefaultAzureCredential requests a token for ADO resource
#   3. Azure auto-handles token lifecycle (acquire, cache, refresh)
#
# Setup (one-time):
#   1. Enable Managed Identity on the Function App:
#      az functionapp identity assign -g Github_Auto -n <FUNC_NAME>
#   2. Add the identity to ADO org as a user:
#      dev.azure.com/CSACTOSOL → Org Settings → Users → Add
#      Paste the Object ID, assign Basic access level
#   3. Grant the identity Contributor on the project
#
# Fallback: if ADO_PAT is set, uses it instead (for local dev)

_ado_token_cache = {"header": None, "expires": 0, "lock": threading.Lock()}
_ado_credential = None  # Lazy-init DefaultAzureCredential


def _ado_auth():
    """Get ADO Authorization header.
    Priority: AZURE_ADO_TOKEN (pre-acquired via OIDC) > Managed Identity > PAT.
    """
    global _ado_credential

    # Option 1: Pre-acquired token from GitHub Actions OIDC step
    # The workflow uses azure/login → az account get-access-token → sets this env var
    oidc_token = os.environ.get("AZURE_ADO_TOKEN", "")
    if oidc_token:
        return f"Bearer {oidc_token}"

    # Option 2: PAT fallback (local dev)
    pat = os.environ.get("ADO_PAT", "")
    if pat:
        return "Basic " + base64.b64encode(f":{pat}".encode()).decode()

    # Option 3: Managed Identity (Azure Functions deployment)
    if not _HAS_AZURE_SDK:
        raise EnvironmentError(
            "ADO auth failed — no AZURE_ADO_TOKEN, no ADO_PAT, "
            "and azure-identity not installed for Managed Identity"
        )

    now = time.time()
    with _ado_token_cache["lock"]:
        if _ado_token_cache["header"] and now < _ado_token_cache["expires"] - 120:
            return _ado_token_cache["header"]

        try:
            if _ado_credential is None:
                _ado_credential = DefaultAzureCredential()

            token = _ado_credential.get_token("499b84ac-1321-427f-aa17-267ca6975798/.default")
            header = f"Bearer {token.token}"
            _ado_token_cache["header"] = header
            _ado_token_cache["expires"] = token.expires_on
            log.info("  ADO token acquired via Managed Identity")
            return header
        except Exception as e:
            raise EnvironmentError(
                f"ADO auth failed — no AZURE_ADO_TOKEN, no ADO_PAT, "
                f"and Managed Identity error: {e}"
            )


def get_current_iteration():
    """Resolve the active sprint iteration path from ADO."""
    org     = env("ADO_ORG")
    project = env("ADO_PROJECT")
    try:
        r = requests.get(
            f"https://dev.azure.com/{org}/{urlquote(project)}"
            "/_apis/wit/classificationnodes/Iterations?$depth=5&api-version=7.0",
            headers={"Authorization": _ado_auth()}, timeout=30,
        )
        r.raise_for_status()
        tree = r.json()
        now = datetime.now(timezone.utc)
        best, best_depth = None, -1

        def walk(node, path="", depth=0):
            nonlocal best, best_depth
            name = node.get("name", "")
            cur = f"{path}\\{name}" if path else name
            attrs = node.get("attributes") or {}
            s, f = attrs.get("startDate"), attrs.get("finishDate")
            if s and f:
                sd = datetime.fromisoformat(s.replace("Z", "+00:00"))
                fd = datetime.fromisoformat(f.replace("Z", "+00:00"))
                if sd <= now <= fd and depth > best_depth:
                    best, best_depth = cur, depth
            for child in node.get("children", []):
                walk(child, cur, depth + 1)

        walk(tree)
        if best:
            log.info(f"  ADO iteration: {best}")
        return best
    except Exception as e:
        log.error(f"  ADO iteration failed: {e}")
        return None


def _md_to_html(text):
    if not text:
        return ""
    h = text
    h = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<img src="\2" alt="\1" style="max-width:100%;">', h)
    h = re.sub(r'\[([^\]]*)\]\(([^)]+)\)', r'<a href="\2">\1</a>', h)
    h = re.sub(r'^### (.+)$', r'<h4>\1</h4>', h, flags=re.MULTILINE)
    h = re.sub(r'^## (.+)$',  r'<h3>\1</h3>', h, flags=re.MULTILINE)
    h = re.sub(r'^# (.+)$',   r'<h2>\1</h2>', h, flags=re.MULTILINE)
    h = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', h)
    h = re.sub(r'__(.+?)__',     r'<b>\1</b>', h)
    h = re.sub(r'`([^`]+)`', r'<code>\1</code>', h)
    h = re.sub(r'```\w*\n(.*?)```', r'<pre><code>\1</code></pre>', h, flags=re.DOTALL)
    h = h.replace("\n", "<br>\n")
    return h


def _extract_images(text):
    if not text:
        return []
    imgs = []
    for m in re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)', text):
        imgs.append({"alt": m.group(1), "url": m.group(2)})
    for m in re.finditer(r'<img[^>]+src=["\']([^"\'>]+)["\']', text):
        if not any(i["url"] == m.group(1) for i in imgs):
            imgs.append({"alt": "", "url": m.group(1)})
    return imgs


def create_ado_bug(issue, accel, repo_url, primary_email, iteration_path):
    """Create a Bug work item in Azure DevOps."""
    org       = env("ADO_ORG")
    project   = env("ADO_PROJECT")
    area_path = env("ADO_AREA_PATH")
    parent_id = env("ADO_PARENT_US_ID")

    labels_str = ", ".join(issue.get("labels", [])) or "None"
    body_html  = _md_to_html(issue.get("body", ""))
    images     = _extract_images(issue.get("body", ""))

    description = (
        "<h3>Summary</h3>"
        f"<p><b>[GitHub #{issue['number']}][{accel}] - {issue['title']}</b></p>"
        "<table style='border-collapse:collapse;margin:8px 0;'>"
        f"<tr><td style='padding:4px 12px;'><b>Accelerator</b></td><td>{accel}</td></tr>"
        f"<tr><td style='padding:4px 12px;'><b>GitHub Issue</b></td>"
        f"<td><a href='{issue['html_url']}'>#{issue['number']} - {issue['title']}</a></td></tr>"
        f"<tr><td style='padding:4px 12px;'><b>Reported By</b></td><td>{issue.get('user','N/A')}</td></tr>"
        f"<tr><td style='padding:4px 12px;'><b>Created On</b></td><td>{issue['created_at'][:10]}</td></tr>"
        f"<tr><td style='padding:4px 12px;'><b>Labels</b></td><td>{labels_str}</td></tr>"
        f"<tr><td style='padding:4px 12px;'><b>Repository</b></td><td><a href='{repo_url}'>{repo_url}</a></td></tr>"
        "</table>"
        "<hr><p style='color:#888;font-size:11px;'><i>Auto-created by GitHub Issue Tracker (Azure Function).</i></p>"
    )

    repro = (
        "<h3>Repro Steps</h3>"
        f"<p><b>GitHub Issue:</b> <a href='{issue['html_url']}'>{issue['html_url']}</a></p><hr>"
        "<h4>Issue Details</h4>"
        f"<div style='padding:8px;border:1px solid #ddd;border-radius:4px;background:#f9f9f9;'>"
        f"{body_html or '<i>No description provided.</i>'}</div>"
    )
    if images:
        repro += "<h4>Screenshots / Attachments</h4>"
        for img in images:
            alt = img["alt"] or "Screenshot"
            repro += (f"<p><b>{alt}:</b><br>"
                      f"<img src='{img['url']}' alt='{alt}' "
                      "style='max-width:100%;border:1px solid #ddd;margin:4px 0;'></p>")
    repro += (f"<hr><p style='color:#888;font-size:11px;'>"
              f"<i>Source: <a href='{issue['html_url']}'>{issue['html_url']}</a></i></p>")

    title = f"[GitHub #{issue['number']}][{accel}] - {issue['title']}"

    payload = json.dumps([
        {"op": "add", "path": "/fields/System.Title", "value": title},
        {"op": "add", "path": "/fields/System.AreaPath", "value": area_path},
        {"op": "add", "path": "/fields/System.IterationPath", "value": iteration_path},
        {"op": "add", "path": "/fields/System.State", "value": "New"},
        {"op": "add", "path": "/fields/Microsoft.VSTS.Common.Priority", "value": 3},
        {"op": "add", "path": "/fields/System.Description", "value": description},
        {"op": "add", "path": "/fields/Microsoft.VSTS.TCM.ReproSteps", "value": repro},
        {"op": "add", "path": "/fields/System.AssignedTo", "value": primary_email},
        {"op": "add", "path": "/fields/System.Tags",
         "value": f"GitHub;Persistent;Maintenance;{accel}"},
        {"op": "add", "path": "/relations/-", "value": {
            "rel": "System.LinkTypes.Hierarchy-Reverse",
            "url": f"https://dev.azure.com/{org}/_apis/wit/workitems/{parent_id}",
            "attributes": {"name": "Parent"},
        }},
    ])

    try:
        r = requests.post(
            f"https://dev.azure.com/{org}/{urlquote(project)}/_apis/wit/workitems/$Bug?api-version=7.0",
            headers={"Authorization": _ado_auth(),
                     "Content-Type": "application/json-patch+json"},
            data=payload, timeout=30,
        )
        r.raise_for_status()
        result = r.json()
        bug_id  = result.get("id")
        bug_url = result.get("_links", {}).get("html", {}).get("href", "")
        log.info(f"    ADO Bug #{bug_id} -> {bug_url}")
        return {"id": bug_id, "url": bug_url}
    except requests.exceptions.HTTPError as e:
        log.error(f"    ADO Bug HTTP {e.response.status_code}: {e.response.text[:300]}")
    except Exception as e:
        log.error(f"    ADO Bug error: {e}")
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# EMAIL + TEAMS VIA POWER AUTOMATE WEBHOOKS  (No admin consent needed!)
# ═══════════════════════════════════════════════════════════════════════════════
#
# How it works:
#   1. Create 2 Power Automate flows with "When an HTTP request is received" trigger
#   2. Flow 1: receives JSON → sends email via Office 365 Outlook connector
#   3. Flow 2: receives JSON → posts Teams message via Teams connector
#   4. Store the webhook URLs as env vars / GitHub secrets
#   5. Python just POSTs JSON to the webhook URLs — done!
#
# Setup:
#   Go to https://make.powerautomate.com → Create → Instant cloud flow
#
#   FLOW 1 — "Send Email Webhook":
#     Trigger:  "When an HTTP request is received"
#               Request Body JSON Schema:
#               {
#                 "type": "object",
#                 "properties": {
#                   "to":      { "type": "string" },
#                   "cc":      { "type": "string" },
#                   "subject": { "type": "string" },
#                   "body":    { "type": "string" }
#                 }
#               }
#     Action:   "Send an email (V2)" — Office 365 Outlook
#               To: @{triggerBody()?['to']}
#               CC: @{triggerBody()?['cc']}
#               Subject: @{triggerBody()?['subject']}
#               Body: @{triggerBody()?['body']}
#               Importance: High
#     → Save → Copy the HTTP POST URL
#
#   FLOW 2 — "Teams Message Webhook":
#     Trigger:  "When an HTTP request is received"
#               Request Body JSON Schema:
#               {
#                 "type": "object",
#                 "properties": {
#                   "chatId":  { "type": "string" },
#                   "message": { "type": "string" }
#                 }
#               }
#     Action:   "Post message in a chat or channel" — Microsoft Teams
#               Post in: Group chat
#               Group chat: (select your chat, or use chatId from trigger)
#               Message: @{triggerBody()?['message']}
#     → Save → Copy the HTTP POST URL
#
# Required env vars:
#   POWER_AUTOMATE_EMAIL_URL  — webhook URL from Flow 1
#   POWER_AUTOMATE_TEAMS_URL  — webhook URL from Flow 2


def send_email(to_list, cc_list, subject, html_body):
    """Send email via Power Automate webhook."""
    webhook_url = os.environ.get("POWER_AUTOMATE_EMAIL_URL", "")
    if not webhook_url:
        log.warning("  Email skipped — POWER_AUTOMATE_EMAIL_URL not set")
        return False

    try:
        payload = {
            "to": "; ".join(e for e in to_list if e),
            "cc": "; ".join(e for e in cc_list if e),
            "subject": subject,
            "body": html_body,
        }
        r = requests.post(webhook_url, json=payload, timeout=30)
        r.raise_for_status()
        log.info(f"  Email sent via Power Automate -> {to_list}")
        return True
    except Exception as e:
        log.error(f"  Email failed: {e}")
        return False


def send_teams_message(html_content):
    """Post Teams message via Power Automate webhook."""
    webhook_url = os.environ.get("POWER_AUTOMATE_TEAMS_URL", "")
    if not webhook_url:
        log.warning("  Teams skipped — POWER_AUTOMATE_TEAMS_URL not set")
        return False

    try:
        chat_id = os.environ.get("TEAMS_CHAT_ID", "")
        payload = {
            "chatId": chat_id,
            "message": html_content,
        }
        r = requests.post(webhook_url, json=payload, timeout=30)
        r.raise_for_status()
        log.info("  Teams message sent via Power Automate")
        return True
    except Exception as e:
        log.error(f"  Teams failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# EMAIL / TEAMS BODY BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def new_issue_email(accel, repo_url, issues):
    rows = ""
    for i in issues:
        labels = ", ".join(i.get("labels", [])) or "None"
        ado = (f'<a href="{i["ado_bug_url"]}">Bug #{i["ado_bug_id"]}</a>'
               if i.get("ado_bug_id") else "\u2014")
        rows += (
            "<tr>"
            f'<td style="border:1px solid #ddd;padding:8px;text-align:center;">'
            f'<a href="{i["html_url"]}">#{i["number"]}</a></td>'
            f'<td style="border:1px solid #ddd;padding:8px;">{i["title"]}</td>'
            f'<td style="border:1px solid #ddd;padding:8px;">{i.get("user","N/A")}</td>'
            f'<td style="border:1px solid #ddd;padding:8px;">{i["created_at"][:10]}</td>'
            f'<td style="border:1px solid #ddd;padding:8px;">{labels}</td>'
            f'<td style="border:1px solid #ddd;padding:8px;text-align:center;">{ado}</td>'
            "</tr>"
        )
    return (
        '<html><body style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#333;">'
        f'<h2 style="color:#0078D4;">New GitHub Issue(s) \u2014 {accel}</h2>'
        f'<p><b>Repository:</b> <a href="{repo_url}">{repo_url}</a></p>'
        f'<p><b>{len(issues)} new issue(s)</b> detected:</p>'
        '<table style="border-collapse:collapse;width:100%;margin-top:10px;">'
        '<thead><tr style="background:#0078D4;color:white;">'
        '<th style="border:1px solid #ddd;padding:8px;">Issue #</th>'
        '<th style="border:1px solid #ddd;padding:8px;">Title</th>'
        '<th style="border:1px solid #ddd;padding:8px;">Reported By</th>'
        '<th style="border:1px solid #ddd;padding:8px;">Created</th>'
        '<th style="border:1px solid #ddd;padding:8px;">Labels</th>'
        '<th style="border:1px solid #ddd;padding:8px;">ADO Bug</th>'
        f'</tr></thead><tbody>{rows}</tbody></table><br>'
        '<p style="color:#666;font-size:12px;">Automated notification from GitHub Issue Tracker (Azure Function).</p>'
        '</body></html>'
    )


def new_issue_teams(accel, repo_url, issues, primary, secondary):
    items = ""
    for i in issues:
        ado = (f' | <a href="{i["ado_bug_url"]}">ADO #{i["ado_bug_id"]}</a>'
               if i.get("ado_bug_id") else "")
        items += (f'<li><a href="{i["html_url"]}">#{i["number"]}</a> '
                  f'{i["title"]} (by {i.get("user","N/A")}){ado}</li>')
    owners = (f'<p><b>Primary:</b> <at>{primary["email"]}</at> ({primary["name"]}) | '
              f'<b>Secondary:</b> <at>{secondary["email"]}</at> ({secondary["name"]})</p>')
    return (f'<h3>New GitHub Issue(s) \u2014 {accel}</h3>'
            f'<p><b>Repo:</b> <a href="{repo_url}">{repo_url}</a></p>'
            f'{owners}<ul>{items}</ul>'
            '<p><i>Please respond within the SLA timeline.</i></p>')


def followup_email(accel, repo_url, overdue, deadline_days):
    rows = ""
    for i in overdue:
        rows += (
            "<tr>"
            f'<td style="border:1px solid #ddd;padding:8px;text-align:center;">'
            f'<a href="{i["html_url"]}">#{i["number"]}</a></td>'
            f'<td style="border:1px solid #ddd;padding:8px;">{i["title"]}</td>'
            f'<td style="border:1px solid #ddd;padding:8px;">{i.get("user","N/A")}</td>'
            f'<td style="border:1px solid #ddd;padding:8px;">{i.get("created_at","N/A")[:10]}</td>'
            f'<td style="border:1px solid #ddd;padding:8px;color:#D83B01;font-weight:bold;">'
            f'{i["age_days"]} days</td>'
            "</tr>"
        )
    return (
        '<html><body style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#333;">'
        f'<h2 style="color:#D83B01;">Follow-up Reminder \u2014 {accel}</h2>'
        f'<p><b>Repository:</b> <a href="{repo_url}">{repo_url}</a></p>'
        f'<p>The following <b>{len(overdue)} issue(s)</b> have had '
        f'<b style="color:#D83B01;">NO follow-up comment</b> for more than '
        f'<b>{deadline_days} days</b>. Please respond ASAP:</p>'
        '<table style="border-collapse:collapse;width:100%;margin-top:10px;">'
        '<thead><tr style="background:#D83B01;color:white;">'
        '<th style="border:1px solid #ddd;padding:8px;">Issue #</th>'
        '<th style="border:1px solid #ddd;padding:8px;">Title</th>'
        '<th style="border:1px solid #ddd;padding:8px;">Reported By</th>'
        '<th style="border:1px solid #ddd;padding:8px;">Created</th>'
        '<th style="border:1px solid #ddd;padding:8px;">Overdue By</th>'
        f'</tr></thead><tbody>{rows}</tbody></table><br>'
        f'<p style="color:#666;font-size:12px;">Automated reminder. Follow-up within '
        f'{deadline_days} days per SLA. Reminders repeat every 2 hours.</p>'
        '</body></html>'
    )


def followup_teams(accel, repo_url, overdue, primary, secondary):
    items = ""
    for i in overdue:
        items += (f'<li><a href="{i["html_url"]}">#{i["number"]}</a> '
                  f'{i["title"]} \u2014 <b>{i["age_days"]} days overdue</b></li>')
    owners = (f'<p><b>Primary:</b> <at>{primary["email"]}</at> ({primary["name"]}) | '
              f'<b>Secondary:</b> <at>{secondary["email"]}</at> ({secondary["name"]})</p>')
    return (f'<h3>Follow-up Overdue \u2014 {accel}</h3>'
            f'<p><b>Repo:</b> <a href="{repo_url}">{repo_url}</a></p>'
            f'{owners}<p>{len(overdue)} issue(s) with <b>NO comment</b> for 2+ days:</p>'
            f'<ul>{items}</ul><p><i>Please respond ASAP.</i></p>')


def summary_email(total_new, total_ado, total_overdue, errors):
    IST = timezone(timedelta(hours=5, minutes=30))
    ts = datetime.now(IST).strftime("%d-%b-%Y %I:%M %p IST")
    icon = "OK" if errors == 0 else "WARN"
    subj = f"[{icon}] Issue Tracker Run \u2014 {total_new} new, {total_overdue} overdue, {errors} errors \u2014 {ts}"
    body = (
        '<html><body style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#333;">'
        '<h3>GitHub Issue Tracker \u2014 Run Summary (Azure Function)</h3>'
        '<table style="border-collapse:collapse;margin-top:8px;">'
        f'<tr><td style="padding:4px 12px;"><b>Time</b></td><td>{ts}</td></tr>'
        f'<tr><td style="padding:4px 12px;"><b>New Issues</b></td><td>{total_new}</td></tr>'
        f'<tr><td style="padding:4px 12px;"><b>ADO Bugs Created</b></td><td>{total_ado}</td></tr>'
        f'<tr><td style="padding:4px 12px;"><b>Overdue Follow-ups</b></td><td>{total_overdue}</td></tr>'
        f'<tr><td style="padding:4px 12px;"><b>Errors</b></td>'
        f'<td style="color:{"red" if errors else "green"};">{errors}</td></tr>'
        '</table></body></html>'
    )
    return subj, body
