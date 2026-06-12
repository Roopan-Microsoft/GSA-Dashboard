"""Lightweight issue fetcher — collects open GitHub issues for dashboard display."""
import json, os, sys, time, requests
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

REPOS = [
    {"name": "Customer Chatbot", "owner": "microsoft", "repo": "customer-chatbot-solution-accelerator", "primary": "Ajit Padhi", "secondary": "Prajwal D C"},
    {"name": "Code Modernization", "owner": "microsoft", "repo": "Modernize-your-code-solution-accelerator", "primary": "Priyanka Singhal", "secondary": "Shreyas Waikar"},
    {"name": "Container Migration", "owner": "microsoft", "repo": "Container-Migration-Solution-Accelerator", "primary": "Shreyas Waikar", "secondary": "Priyanka Singhal"},
    {"name": "Content Generation", "owner": "microsoft", "repo": "content-generation-solution-accelerator", "primary": "Ragini Chauragade", "secondary": "Pavan Kumar"},
    {"name": "Content Processing", "owner": "microsoft", "repo": "content-processing-solution-accelerator", "primary": "Shreyas Waikar", "secondary": "Ajit Padhi"},
    {"name": "CWYD", "owner": "Azure-Samples", "repo": "chat-with-your-data-solution-accelerator", "primary": "Ajit Padhi", "secondary": "Priyanka Singhal"},
    {"name": "Data & Agent Governance", "owner": "microsoft", "repo": "Data-and-Agent-Governance-and-Security-Accelerator", "primary": "Saswato Chatterjee", "secondary": "Yamini"},
    {"name": "Deploy AI App", "owner": "microsoft", "repo": "Deploy-Your-AI-Application-In-Production", "primary": "Saswato Chatterjee", "secondary": "Yamini"},
    {"name": "DKM", "owner": "microsoft", "repo": "Document-Knowledge-Mining-Solution-Accelerator", "primary": "Priyanka Singhal", "secondary": "Ajit Padhi"},
    {"name": "Agentic App", "owner": "microsoft", "repo": "agentic-applications-for-unified-data-foundation-solution-accelerator", "primary": "Ragini Chauragade", "secondary": "Pavan Kumar"},
    {"name": "KM Generic", "owner": "microsoft", "repo": "Conversation-Knowledge-Mining-Solution-Accelerator", "primary": "Pavan Kumar", "secondary": "Avijit Ghorui"},
    {"name": "UDF", "owner": "microsoft", "repo": "unified-data-foundation-with-fabric-solution-accelerator", "primary": "Saswato Chatterjee", "secondary": "Yamini"},
    {"name": "MACAE", "owner": "microsoft", "repo": "Multi-Agent-Custom-Automation-Engine-Solution-Accelerator", "primary": "Dhruvkumar Babariya", "secondary": "Abdul Mujeeb T A"},
    {"name": "RTI", "owner": "microsoft", "repo": "real-time-intelligence-operations-solution-accelerator", "primary": "Saswato Chatterjee", "secondary": "Yamini"},
]

def headers():
    token = os.environ.get("GITHUB_TOKEN", "")
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h

def fetch_issues(owner, repo):
    issues = []
    page = 1
    while page <= 10:
        r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/issues",
                        params={"state": "open", "per_page": 100, "page": page},
                        headers=headers(), timeout=30)
        if r.status_code == 403:
            time.sleep(10); continue
        if r.status_code != 200:
            break
        batch = r.json()
        if not batch: break
        for item in batch:
            if "pull_request" not in item:
                issues.append(item)
        page += 1
    return issues

def main():
    state_file = os.environ.get("STATE_FILE", "state/tracked_issues_state.json")
    now = datetime.now(IST)
    
    all_issues = {}
    for cfg in REPOS:
        owner, repo = cfg["owner"], cfg["repo"]
        print(f"Fetching issues for {owner}/{repo}...")
        issues = fetch_issues(owner, repo)
        processed = []
        for iss in issues:
            created = datetime.fromisoformat(iss["created_at"].replace("Z", "+00:00"))
            age_days = (now - created).days
            processed.append({
                "number": iss["number"],
                "title": iss["title"],
                "url": iss["html_url"],
                "created_at": iss["created_at"],
                "age_days": age_days,
                "labels": [l["name"] for l in iss.get("labels", [])],
                "assignees": [a["login"] for a in iss.get("assignees", [])],
                "state": iss["state"],
            })
        all_issues[f"{owner}/{repo}"] = {
            "name": cfg["name"],
            "primary": cfg["primary"],
            "secondary": cfg["secondary"],
            "issues": processed,
            "open_count": len(processed),
        }
        print(f"  Found {len(processed)} open issues")

    state = {
        "last_updated": now.isoformat(),
        "repos": all_issues,
        "total_open": sum(r["open_count"] for r in all_issues.values()),
    }
    
    os.makedirs(os.path.dirname(state_file) if os.path.dirname(state_file) else ".", exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)
    print(f"\nSaved {state['total_open']} total open issues to {state_file}")

if __name__ == "__main__":
    main()
