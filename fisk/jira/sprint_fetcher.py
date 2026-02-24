from __future__ import annotations

import logging
from typing import Optional
import requests

from fisk.jira.utils import fetch_all_issues, parse_jira_datetime

logger = logging.getLogger(__name__)


def _parse_sprint_field(value) -> Optional[dict]:
    if not value:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    if not value:
        return None
    if isinstance(value, dict):
        return {
            "id": str(value.get("id", "")),
            "name": value.get("name", ""),
            "state": value.get("state", ""),
            "startDate": value.get("startDate", ""),
            "endDate": value.get("endDate", ""),
        }
    if isinstance(value, str) and "id=" in value:
        info = {}
        try:
            inner = value.split("[")[1].split("]")[0]
            for part in inner.split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    info[k.strip()] = v.strip()
        except (IndexError, ValueError):
            return None
        return {
            "id": info.get("id", ""),
            "name": info.get("name", ""),
            "state": info.get("state", "").lower(),
            "startDate": info.get("startDate", ""),
            "endDate": info.get("endDate", ""),
        }
    return None


def fetch_closed_sprints(
    session: requests.Session,
    base_url: str,
    board_ids: list[int],
) -> list[dict]:
    base = base_url.rstrip("/")
    sprints = []
    for board_id in board_ids:
        start_at = 0
        while True:
            url = f"{base}/rest/agile/1.0/board/{board_id}/sprint"
            resp = session.get(url, params={"state": "closed", "startAt": start_at, "maxResults": 50})
            if resp.status_code == 404:
                logger.warning(f"Board {board_id} not found or not accessible")
                break
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("values", [])
            for s in batch:
                sprints.append({
                    "sprint_id": str(s["id"]),
                    "board_id": board_id,
                    "name": s.get("name", ""),
                    "state": s.get("state", "").lower(),
                    "start_date": s.get("startDate", ""),
                    "end_date": s.get("endDate", ""),
                    "goal": s.get("goal", "") or "",
                })
            if data.get("isLast", True) or len(batch) < 50:
                break
            start_at += 50
    return sprints


def fetch_sprint_issues(
    session: requests.Session,
    base_url: str,
    sprint_id: str,
) -> list[dict]:
    jql = f"Sprint = {sprint_id}"
    fields = [
        "key", "summary", "status", "assignee", "issuetype", "priority",
        "created", "customfield_10016", "customfield_10020", "resolutiondate",
    ]
    return fetch_all_issues(
        session, base_url, jql, fields,
        expand="changelog",
    )


def compute_sprint_velocity(sprint: dict, issues: list[dict]) -> dict:
    done_categories = {"done"}
    committed = 0.0
    completed = 0.0
    sprint_id = sprint["sprint_id"]

    for issue in issues:
        f = issue.get("fields", {})
        points = f.get("customfield_10016") or 0.0

        sprint_field = f.get("customfield_10020") or []
        if isinstance(sprint_field, list):
            in_sprint = any(
                (str(s.get("id", "")) == sprint_id if isinstance(s, dict) else sprint_id in str(s))
                for s in sprint_field
            )
        else:
            in_sprint = sprint_id in str(sprint_field)

        if not in_sprint:
            continue

        committed += points
        status_cat = (f.get("status") or {}).get("statusCategory", {}).get("key", "").lower()
        if status_cat in done_categories:
            completed += points

    return {
        **sprint,
        "committed_points": round(committed, 1),
        "completed_points": round(completed, 1),
    }
