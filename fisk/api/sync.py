from __future__ import annotations

import threading
import logging
from datetime import datetime, timezone

from fisk.jira.utils import create_jira_session, load_config_unified, get_date_range, fetch_all_issues, parse_jira_datetime, get_iso_week_label
from fisk.jira.project_report import (
    fetch_resolved_issues, fetch_created_issues,
    process_resolved_issues, process_created_issues,
)
from fisk.jira.sprint_fetcher import fetch_closed_sprints, fetch_sprint_issues
from fisk.api.db import get_conn

logger = logging.getLogger(__name__)

PROJECTS = ["FED", "DIP", "SUP", "OOT", "SSJ"]

_sync_status: dict = {"running": False, "last_run": None, "last_error": None}
_lock = threading.Lock()


def get_sync_status() -> dict:
    with _lock:
        return dict(_sync_status)


def trigger_sync(config_path: str, weeks: int = 12) -> bool:
    with _lock:
        if _sync_status["running"]:
            return False
        _sync_status["running"] = True
    t = threading.Thread(target=_run_sync, args=(config_path, weeks), daemon=True)
    t.start()
    return True


def _run_sync(config_path: str, weeks: int):
    try:
        config = load_config_unified(config_path)
        session = create_jira_session(config["base_url"], config["username"], config["api_token"])
        start_date, end_date = get_date_range(weeks)
        for project_key in PROJECTS:
            try:
                _sync_project(session, config["base_url"], project_key, start_date, end_date)
            except Exception as e:
                logger.error(f"Failed syncing {project_key}: {e}")

        for project_key in PROJECTS:
            try:
                _sync_stage_durations(session, config["base_url"], project_key, start_date, end_date)
            except Exception as e:
                logger.error(f"Failed stage duration sync for {project_key}: {e}")

        sprint_boards = config.get("sprint_boards", {})
        for project_key, board_id in sprint_boards.items():
            try:
                _sync_sprints(session, config["base_url"], project_key, board_id)
            except Exception as e:
                logger.error(f"Failed sprint sync for {project_key}: {e}")
        with _lock:
            _sync_status["last_run"] = datetime.now(timezone.utc).isoformat()
            _sync_status["last_error"] = None
    except Exception as e:
        logger.error(f"Sync failed: {e}")
        with _lock:
            _sync_status["last_error"] = str(e)
    finally:
        with _lock:
            _sync_status["running"] = False


def _sync_project(session, base_url: str, project_key: str, start: str, end: str):
    logger.info(f"Syncing {project_key}...")
    raw_resolved = fetch_resolved_issues(session, base_url, project_key, start, end)
    resolved = process_resolved_issues(raw_resolved)

    raw_created = fetch_created_issues(session, base_url, project_key, start, end)
    created = process_created_issues(raw_created)

    merged: dict[str, dict] = {r["issue_key"]: r for r in created}
    for r in resolved:
        key = r["issue_key"]
        if key in merged:
            merged[key].update(r)
        else:
            merged[key] = r

    _upsert_issues(project_key, list(merged.values()))
    _update_sync_time(project_key)
    logger.info(f"  {project_key}: {len(merged)} issues upserted")


def _upsert_issues(project_key: str, issues: list[dict]):
    sql = """
        INSERT INTO issues (
            issue_key, project_key, summary, issue_type, status, priority,
            assignee, assignee_email, reporter, story_points,
            created_at, resolved_at, cycle_time_days,
            created_iso_week, resolved_iso_week, components, labels, synced_at
        ) VALUES (
            :issue_key, :project_key, :summary, :issue_type, :status, :priority,
            :assignee, :assignee_email, :reporter, :story_points,
            :created_at, :resolved_at, :cycle_time_days,
            :created_iso_week, :resolved_iso_week, :components, :labels, :synced_at
        )
        ON CONFLICT(issue_key) DO UPDATE SET
            status=excluded.status,
            story_points=excluded.story_points,
            resolved_at=excluded.resolved_at,
            cycle_time_days=excluded.cycle_time_days,
            resolved_iso_week=excluded.resolved_iso_week,
            synced_at=excluded.synced_at
    """
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for i in issues:
        row = {
            "components": "",
            "labels": "",
            "resolved_at": None,
            "cycle_time_days": None,
            "resolved_iso_week": None,
            **i,
            "project_key": project_key,
            "synced_at": now,
        }
        rows.append(row)
    with get_conn() as conn:
        conn.executemany(sql, rows)


def _update_sync_time(project_key: str):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE projects SET last_synced_at = ? WHERE key = ?",
            (now, project_key),
        )


def _stage_durations_from_changelog(issue_key: str, histories: list[dict]) -> list[dict]:
    transitions = []
    for history in histories:
        ts = parse_jira_datetime(history.get("created"))
        if not ts:
            continue
        for item in history.get("items", []):
            if item.get("field") == "status":
                transitions.append((ts, item.get("fromString", ""), item.get("toString", "")))
    transitions.sort(key=lambda x: x[0])
    totals: dict[str, float] = {}
    for i in range(len(transitions) - 1):
        to_status = transitions[i][2]
        duration = (transitions[i + 1][0] - transitions[i][0]).total_seconds() / 86400
        if to_status and duration > 0:
            totals[to_status] = totals.get(to_status, 0) + duration
    return [
        {"issue_key": issue_key, "stage": stage, "duration_days": round(total, 3)}
        for stage, total in totals.items()
    ]


def _sync_stage_durations(session, base_url: str, project_key: str, start: str, end: str):
    logger.info(f"Syncing stage durations for {project_key}...")
    jql = (
        f'project = {project_key} AND '
        f'status IN (Done, Resolved, Closed) AND '
        f'resolutiondate >= "{start}" AND '
        f'resolutiondate <= "{end}"'
    )
    issues = fetch_all_issues(session, base_url, jql, ["key"], expand="changelog")
    rows = []
    for issue in issues:
        histories = issue.get("changelog", {}).get("histories", [])
        rows.extend(_stage_durations_from_changelog(issue["key"], histories))
    if rows:
        sql = """
            INSERT INTO stage_durations (issue_key, stage, duration_days)
            VALUES (:issue_key, :stage, :duration_days)
            ON CONFLICT(issue_key, stage) DO UPDATE SET duration_days=excluded.duration_days
        """
        with get_conn() as conn:
            conn.executemany(sql, rows)
    logger.info(f"  {project_key}: {len(rows)} stage duration records upserted")


def _sync_sprints(session, base_url: str, project_key: str, board_id: int):
    logger.info(f"Syncing sprints for {project_key} (board {board_id})...")
    sprints = fetch_closed_sprints(session, base_url, [board_id])
    if not sprints:
        logger.info(f"  {project_key}: no closed sprints found")
        return

    sprint_rows = [{**s, "project_key": project_key} for s in sprints]
    _upsert_sprints(sprint_rows)

    for sprint in sprints:
        try:
            issues = fetch_sprint_issues(session, base_url, sprint["sprint_id"])
            _upsert_sprint_issues(sprint["sprint_id"], issues)
            _upsert_issue_stubs(project_key, issues)
        except Exception as e:
            logger.error(f"  Failed fetching issues for sprint {sprint['sprint_id']}: {e}")

    logger.info(f"  {project_key}: {len(sprints)} sprints synced")


def _upsert_sprints(sprints: list[dict]):
    sql = """
        INSERT INTO sprints (sprint_id, board_id, project_key, name, state, start_date, end_date, goal)
        VALUES (:sprint_id, :board_id, :project_key, :name, :state, :start_date, :end_date, :goal)
        ON CONFLICT(sprint_id) DO UPDATE SET
            state=excluded.state,
            end_date=excluded.end_date,
            goal=excluded.goal
    """
    with get_conn() as conn:
        conn.executemany(sql, sprints)


def _upsert_issue_stubs(project_key: str, raw_issues: list[dict]):
    """Insert basic issue metadata for sprint issues not already in the issues table."""
    now = datetime.now(timezone.utc).isoformat()
    sql = """
        INSERT OR IGNORE INTO issues (
            issue_key, project_key, summary, issue_type, status, priority,
            assignee, assignee_email, reporter, created_at, resolved_at,
            cycle_time_days, created_iso_week, resolved_iso_week,
            components, labels, synced_at
        ) VALUES (
            :issue_key, :project_key, :summary, :issue_type, :status, :priority,
            :assignee, :assignee_email, :reporter, :created_at, :resolved_at,
            :cycle_time_days, :created_iso_week, :resolved_iso_week,
            '', '', :synced_at
        )
    """
    rows = []
    for issue in raw_issues:
        f = issue.get("fields", {})
        assignee = f.get("assignee") or {}
        created = parse_jira_datetime(f.get("created"))
        resolved = parse_jira_datetime(f.get("resolutiondate"))
        cycle_time = None
        if created and resolved:
            cycle_time = round((resolved - created).total_seconds() / 86400, 2)
        rows.append({
            "issue_key": issue["key"],
            "project_key": project_key,
            "summary": f.get("summary", ""),
            "issue_type": (f.get("issuetype") or {}).get("name", ""),
            "status": (f.get("status") or {}).get("name", ""),
            "priority": (f.get("priority") or {}).get("name", ""),
            "assignee": assignee.get("displayName", "Unassigned"),
            "assignee_email": assignee.get("emailAddress", ""),
            "reporter": "",
            "created_at": created.isoformat() if created else None,
            "resolved_at": resolved.isoformat() if resolved else None,
            "cycle_time_days": cycle_time,
            "created_iso_week": get_iso_week_label(created) if created else None,
            "resolved_iso_week": get_iso_week_label(resolved) if resolved else None,
            "synced_at": now,
        })
    with get_conn() as conn:
        conn.executemany(sql, rows)


def _upsert_sprint_issues(sprint_id: str, raw_issues: list[dict]):
    DONE_CATEGORIES = {"done"}
    rows = []
    for issue in raw_issues:
        f = issue.get("fields", {})
        points = f.get("customfield_10016") or 0.0
        status_cat = (f.get("status") or {}).get("statusCategory", {}).get("key", "").lower()
        rows.append({
            "sprint_id": sprint_id,
            "issue_key": issue["key"],
            "story_points": points,
            "completed": 1 if status_cat in DONE_CATEGORIES else 0,
            "added_after_start": 0,
        })
    if not rows:
        return
    sql = """
        INSERT INTO sprint_issues (sprint_id, issue_key, story_points, completed, added_after_start)
        VALUES (:sprint_id, :issue_key, :story_points, :completed, :added_after_start)
        ON CONFLICT(sprint_id, issue_key) DO UPDATE SET
            story_points=excluded.story_points,
            completed=excluded.completed
    """
    with get_conn() as conn:
        conn.executemany(sql, rows)
