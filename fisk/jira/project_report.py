from __future__ import annotations

import statistics
import argparse
import logging
from datetime import datetime
from collections import defaultdict
import requests

from fisk.jira.utils import (
    fetch_all_issues, create_jira_session, get_iso_week_label,
    get_date_range, classify_trend, parse_jira_datetime, load_config_unified,
)

logger = logging.getLogger(__name__)


def calculate_cycle_time_days(created: datetime, resolved: datetime) -> float:
    if not created or not resolved:
        return 0.0
    return (resolved - created).total_seconds() / 86400


def group_by_week(issues: list[dict], week_field: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for issue in issues:
        week = issue.get(week_field)
        if week:
            grouped[week].append(issue)
    return dict(grouped)


def weekly_cycle_time_stats(grouped: dict) -> list[tuple]:
    result = []
    for week, issues in sorted(grouped.items()):
        total = len(issues)
        times = [i["cycle_time_days"] for i in issues if i.get("cycle_time_days", 0) > 0]
        avg = sum(times) / len(times) if times else 0.0
        result.append((week, avg, total, len(times)))
    return result


def weekly_volume_counts(grouped: dict) -> list[tuple[str, int]]:
    return [(week, len(issues)) for week, issues in sorted(grouped.items())]


def build_cycle_time_summary(stats: list, start: str, end: str) -> list[str]:
    if not stats:
        return [f"Date range: {start} to {end}", "No resolved tickets found."]
    total = sum(count for _, _, count, _ in stats)
    avgs = [avg for _, avg, _, _ in stats if avg > 0]
    if not avgs:
        return [f"Date range: {start} to {end} // Tickets: {total}", "Avg: N/A"]
    overall_avg = sum(avgs) / len(avgs)
    median_weekly_avg = statistics.median(avgs)
    trend = classify_trend(avgs)
    mid = len(avgs) // 2
    if trend in ("growing", "shrinking") and len(avgs) >= 2:
        first = sum(avgs[:mid]) / mid
        second = sum(avgs[mid:]) / len(avgs[mid:])
        trend_detail = f"{trend.capitalize()} — {second:.1f}d recent vs {first:.1f}d earlier"
    elif trend == "highly variable":
        trend_detail = f"Highly variable — {min(avgs):.1f}d to {max(avgs):.1f}d"
    elif trend == "stable":
        trend_detail = f"Stable — consistent ~{overall_avg:.1f}d"
    else:
        trend_detail = trend.capitalize()
    return [
        f"Date range: {start} to {end} // Tickets resolved: {total}",
        f"Avg cycle time: {overall_avg:.1f}d // Median weekly avg: {median_weekly_avg:.1f}d",
        f"Trend: {trend_detail}",
    ]


def build_volume_summary(counts: list[tuple], start: str, end: str) -> list[str]:
    if not counts:
        return [f"Date range: {start} to {end}", "No tickets found."]
    vals = [c for _, c in counts]
    total = sum(vals)
    avg = total / len(vals)
    trend = classify_trend([float(v) for v in vals])
    mid = len(vals) // 2
    if trend in ("growing", "shrinking") and len(vals) >= 2:
        first = sum(vals[:mid]) / mid
        second = sum(vals[mid:]) / len(vals[mid:])
        trend_detail = f"{trend.capitalize()} — {second:.1f} recent vs {first:.1f} earlier"
    elif trend == "highly variable":
        trend_detail = f"Highly variable — {min(vals)} to {max(vals)} per week"
    elif trend == "stable":
        trend_detail = f"Stable — consistent ~{avg:.1f} tickets/week"
    else:
        trend_detail = trend.capitalize()
    return [
        f"Date range: {start} to {end} // Total tickets: {total}",
        f"Avg per week: {avg:.1f}",
        f"Trend: {trend_detail}",
    ]


def fetch_resolved_issues(
    session: requests.Session,
    base_url: str,
    project_key: str,
    start_date: str,
    end_date: str,
) -> list[dict]:
    jql = (
        f'project = {project_key} AND '
        f'status IN (Done, Resolved, Closed) AND '
        f'resolutiondate >= "{start_date}" AND '
        f'resolutiondate <= "{end_date}"'
    )
    return fetch_all_issues(
        session, base_url, jql,
        ["key", "summary", "created", "resolutiondate", "status",
         "assignee", "reporter", "customfield_10016", "issuetype", "priority"],
    )


def fetch_created_issues(
    session: requests.Session,
    base_url: str,
    project_key: str,
    start_date: str,
    end_date: str,
) -> list[dict]:
    jql = (
        f'project = {project_key} AND '
        f'created >= "{start_date}" AND '
        f'created <= "{end_date}"'
    )
    return fetch_all_issues(
        session, base_url, jql,
        ["key", "summary", "created", "status", "assignee", "reporter",
         "customfield_10016", "issuetype", "priority"],
    )


def process_resolved_issues(raw: list[dict]) -> list[dict]:
    out = []
    for issue in raw:
        f = issue.get("fields", {})
        created = parse_jira_datetime(f.get("created"))
        resolved = parse_jira_datetime(f.get("resolutiondate"))
        if not created or not resolved:
            continue
        assignee = f.get("assignee") or {}
        reporter = f.get("reporter") or {}
        out.append({
            "issue_key": issue["key"],
            "summary": f.get("summary", ""),
            "issue_type": (f.get("issuetype") or {}).get("name", ""),
            "status": (f.get("status") or {}).get("name", ""),
            "priority": (f.get("priority") or {}).get("name", ""),
            "assignee": assignee.get("displayName", "Unassigned"),
            "assignee_email": assignee.get("emailAddress", ""),
            "reporter": reporter.get("displayName", "Unknown"),
            "story_points": f.get("customfield_10016"),
            "created_at": created.isoformat(),
            "resolved_at": resolved.isoformat(),
            "cycle_time_days": round(calculate_cycle_time_days(created, resolved), 2),
            "resolved_iso_week": get_iso_week_label(resolved),
            "created_iso_week": get_iso_week_label(created),
        })
    return out


def process_created_issues(raw: list[dict]) -> list[dict]:
    out = []
    for issue in raw:
        f = issue.get("fields", {})
        created = parse_jira_datetime(f.get("created"))
        if not created:
            continue
        assignee = f.get("assignee") or {}
        reporter = f.get("reporter") or {}
        out.append({
            "issue_key": issue["key"],
            "summary": f.get("summary", ""),
            "issue_type": (f.get("issuetype") or {}).get("name", ""),
            "status": (f.get("status") or {}).get("name", ""),
            "priority": (f.get("priority") or {}).get("name", ""),
            "assignee": assignee.get("displayName", "Unassigned"),
            "assignee_email": assignee.get("emailAddress", ""),
            "reporter": reporter.get("displayName", "Unknown"),
            "story_points": f.get("customfield_10016"),
            "created_at": created.isoformat(),
            "created_iso_week": get_iso_week_label(created),
        })
    return out


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Jira project cycle-time and volume report")
    parser.add_argument("--project", required=True, help="Jira project key, e.g. SUP")
    parser.add_argument("--weeks", type=int, default=8)
    parser.add_argument("--config", default="/Users/michael@jaris.io/bin/jira.yaml")
    parser.add_argument("--output-dir", default="/Users/michael@jaris.io/Desktop/debris")
    args = parser.parse_args()

    config = load_config_unified(args.config)
    session = create_jira_session(config["base_url"], config["username"], config["api_token"])
    start_date, end_date = get_date_range(args.weeks)

    raw_resolved = fetch_resolved_issues(session, config["base_url"], args.project, start_date, end_date)
    resolved = process_resolved_issues(raw_resolved)
    grouped_r = group_by_week(resolved, "resolved_iso_week")
    stats = weekly_cycle_time_stats(grouped_r)
    summary = build_cycle_time_summary(stats, start_date, end_date)
    for line in summary:
        logger.info(line)

    raw_created = fetch_created_issues(session, config["base_url"], args.project, start_date, end_date)
    created = process_created_issues(raw_created)
    grouped_c = group_by_week(created, "created_iso_week")
    counts = weekly_volume_counts(grouped_c)
    vol_summary = build_volume_summary(counts, start_date, end_date)
    for line in vol_summary:
        logger.info(line)


if __name__ == "__main__":
    main()
