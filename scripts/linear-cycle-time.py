"""
Compute real cycle time from Linear issueHistory, bypassing Linear's broken `startedAt`.

Linear's `startedAt` is system-managed (auto-set on transition to a `started`-type
state) and the API does not allow direct writes. For tickets created retrospectively
or moved Triage -> Done without passing through In Progress, `startedAt` is null
and cycle time analysis breaks.

This script walks `issueHistory` for each closed ticket, finds the first transition
to any `started`-type state (work_started) and the first transition to any
`completed`-type state (work_done), and computes cycle = work_done - work_started.

Output: JSON with {identifier, title, cycle_hours, started_at, completed_at,
priority, has_real_transitions} per ticket. Tickets created and closed without
ever transitioning through "started" are flagged `has_real_transitions=false`
and excluded from reference-class statistics.

Usage:
    $env:LINEAR_API_KEY = (Get-Content D:/keys/.env | Select-String '^LINEAR_API_KEY=' | ForEach-Object { $_.Line.Split('=',2)[1] })
    python scripts/linear-cycle-time.py --team XAR --since 2026-03-01 --out research/linear-cycle-2026-05-20.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

LINEAR_API = "https://api.linear.app/graphql"

TEAM_IDS = {
    "XAR": "d87f5bad-c6df-4eb1-8fa8-2f16558ac0c7",
}


def gql(key: str, query: str, variables: dict | None = None) -> dict:
    r = requests.post(
        LINEAR_API,
        headers={"Authorization": key, "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}},
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    if "errors" in body:
        raise RuntimeError(json.dumps(body["errors"], indent=2))
    return body["data"]


def list_closed_issues(key: str, team_id: str, since_iso: str) -> list[dict]:
    """Pull all closed/canceled issues since `since_iso`."""
    query = """
    query($teamId: String!, $since: DateTimeOrDuration!, $after: String) {
      team(id: $teamId) {
        issues(first: 100, after: $after, filter: { completedAt: { gt: $since } }) {
          pageInfo { hasNextPage endCursor }
          nodes {
            id identifier title priority createdAt completedAt startedAt
            state { type name }
          }
        }
      }
    }
    """
    out: list[dict] = []
    after = None
    while True:
        data = gql(key, query, {"teamId": team_id, "since": since_iso, "after": after})
        page = data["team"]["issues"]
        out.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    return out


def get_transitions(key: str, issue_id: str) -> list[dict]:
    query = """
    query($id: String!) {
      issue(id: $id) {
        history(first: 100) {
          nodes { createdAt toState { name type } }
        }
      }
    }
    """
    data = gql(key, query, {"id": issue_id})
    nodes = data["issue"]["history"]["nodes"]
    return [
        {"at": n["createdAt"], "state_name": n["toState"]["name"], "state_type": n["toState"]["type"]}
        for n in nodes
        if n.get("toState")
    ]


def compute_real_cycle(transitions: list[dict]) -> tuple[str | None, str | None]:
    """Return (work_started_at, work_done_at) from transition history.

    work_started_at = first transition to a 'started'-type state (In Progress, In Review)
    work_done_at = first transition to a 'completed'-type state (Done)
    """
    sorted_t = sorted(transitions, key=lambda t: t["at"])
    work_started = next((t["at"] for t in sorted_t if t["state_type"] == "started"), None)
    work_done = next((t["at"] for t in sorted_t if t["state_type"] == "completed"), None)
    return work_started, work_done


def hours_between(a: str, b: str) -> float:
    da = datetime.fromisoformat(a.replace("Z", "+00:00"))
    db = datetime.fromisoformat(b.replace("Z", "+00:00"))
    return round((db - da).total_seconds() / 3600, 2)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(len(s) * p))
    return s[idx]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default="XAR")
    ap.add_argument("--since", default="2026-03-01", help="ISO date")
    ap.add_argument("--out", required=True, help="Output JSON path")
    ap.add_argument("--limit", type=int, default=None, help="Cap issues processed (for testing)")
    args = ap.parse_args()

    key = os.environ.get("LINEAR_API_KEY")
    if not key:
        print("LINEAR_API_KEY env var missing. Source from D:/keys/.env first.", file=sys.stderr)
        return 1

    team_id = TEAM_IDS.get(args.team)
    if not team_id:
        print(f"Unknown team: {args.team}", file=sys.stderr)
        return 1

    print(f"Pulling closed issues for {args.team} since {args.since}...")
    issues = list_closed_issues(key, team_id, args.since + "T00:00:00.000Z")
    print(f"  found {len(issues)} closed issues")

    if args.limit:
        issues = issues[: args.limit]

    print(f"Walking issueHistory for {len(issues)}...")
    enriched = []
    for i, issue in enumerate(issues, 1):
        try:
            transitions = get_transitions(key, issue["id"])
            work_started, work_done = compute_real_cycle(transitions)
            has_real = work_started is not None and work_done is not None

            entry = {
                "id": issue["identifier"],
                "title": issue["title"][:80],
                "priority": issue["priority"],
                "created_at": issue["createdAt"],
                "completed_at": issue["completedAt"],
                "linear_started_at": issue["startedAt"],
                "history_work_started_at": work_started,
                "history_work_done_at": work_done,
                "has_real_transitions": has_real,
                "real_cycle_hours": (
                    hours_between(work_started, work_done) if has_real else None
                ),
                "calendar_age_hours": hours_between(issue["createdAt"], issue["completedAt"]),
            }
            enriched.append(entry)
        except Exception as exc:
            print(f"  ERROR {issue['identifier']}: {exc}", file=sys.stderr)
        if i % 10 == 0:
            print(f"  progress: {i}/{len(issues)}")
        time.sleep(0.1)  # rate limit

    real = [e for e in enriched if e["has_real_transitions"]]
    real_hours = [e["real_cycle_hours"] for e in real]

    summary = {
        "team": args.team,
        "since": args.since,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "totals": {
            "issues_total": len(enriched),
            "with_real_transitions": len(real),
            "logged_after_the_fact": len(enriched) - len(real),
        },
        "real_cycle_distribution_hours": {
            "n": len(real_hours),
            "p25": percentile(real_hours, 0.25),
            "p50": percentile(real_hours, 0.50),
            "p75": percentile(real_hours, 0.75),
            "p95": percentile(real_hours, 0.95),
            "min": min(real_hours) if real_hours else 0,
            "max": max(real_hours) if real_hours else 0,
        },
        "by_priority": {},
        "issues": enriched,
    }

    by_p: dict[int, list[float]] = {}
    for e in real:
        by_p.setdefault(e["priority"], []).append(e["real_cycle_hours"])
    for p, vals in sorted(by_p.items()):
        summary["by_priority"][str(p)] = {
            "n": len(vals),
            "p50": percentile(vals, 0.50),
            "p95": percentile(vals, 0.95),
        }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n=== Summary ===")
    print(f"  total closed:           {summary['totals']['issues_total']}")
    print(f"  with real transitions:  {summary['totals']['with_real_transitions']}")
    print(f"  logged after the fact:  {summary['totals']['logged_after_the_fact']}")
    rd = summary["real_cycle_distribution_hours"]
    print(f"  real cycle hours: n={rd['n']} p25={rd['p25']} p50={rd['p50']} p75={rd['p75']} p95={rd['p95']}")
    print(f"  by priority:")
    for p, stats in summary["by_priority"].items():
        print(f"    P{p}: n={stats['n']} p50={stats['p50']}h p95={stats['p95']}h")
    print(f"\n  written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
