from __future__ import annotations

import math
import logging
import requests
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Union
from requests.auth import HTTPBasicAuth
from dateutil import parser as date_parser

logger = logging.getLogger(__name__)


def get_iso_week_label(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def get_date_range(weeks_back: int) -> tuple[str, str]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(weeks=weeks_back)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def classify_trend(
    values: list[float],
    variability_threshold: float = 0.4,
    change_threshold: float = 0.1,
) -> str:
    if len(values) < 2:
        return "insufficient data"
    mean = sum(values) / len(values)
    if mean == 0:
        return "stable"
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    cv = math.sqrt(variance) / mean
    if cv > variability_threshold:
        return "highly variable"
    mid = len(values) // 2
    first_avg = sum(values[:mid]) / mid
    second_avg = sum(values[mid:]) / len(values[mid:])
    if first_avg == 0:
        return "growing" if second_avg > 0 else "stable"
    ratio = second_avg / first_avg
    if ratio > 1 + change_threshold:
        return "growing"
    if ratio < 1 - change_threshold:
        return "shrinking"
    return "stable"


def parse_jira_datetime(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        dt = date_parser.parse(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def load_config_unified(config_path: str) -> dict:
    import yaml
    with open(config_path) as f:
        raw = yaml.safe_load(f)

    if "jira" in raw and isinstance(raw["jira"], dict):
        j = raw["jira"]
        base_url = j.get("base_url", "")
        username = j.get("username", "")
        api_token = j.get("api_token", "")
    else:
        base_url = raw.get("url", "").replace("/rest/api/3/search/jql", "")
        username = raw.get("email", "")
        api_token = raw.get("api_token", "")

    sprint_boards_raw = raw.get("sprint_boards", {})
    if isinstance(sprint_boards_raw, list):
        sprint_boards = {k: v for d in sprint_boards_raw for k, v in d.items()}
    elif isinstance(sprint_boards_raw, dict):
        sprint_boards = sprint_boards_raw
    else:
        sprint_boards = {}

    fisk = raw.get("fisk", {})

    return {
        "base_url": base_url.rstrip("/"),
        "username": username,
        "api_token": api_token,
        "board_ids": raw.get("board_ids", []),
        "sprint_boards": sprint_boards,
        "done_statuses": raw.get("done_statuses", ["Done", "Closed", "Resolved"]),
        "output_directory": raw.get("output_directory", raw.get("directory", ".")),
        "db_path": fisk.get("db_path"),
        "projects": fisk.get("projects", {}),
        "_raw": raw,
    }


def create_jira_session(base_url: str, username: str, api_token: str) -> requests.Session:
    session = requests.Session()
    session.auth = HTTPBasicAuth(username, api_token)
    session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    test_url = f"{base_url.rstrip('/')}/rest/api/3/myself"
    response = session.get(test_url)
    response.raise_for_status()
    logger.info(f"Authenticated as: {response.json().get('displayName', username)}")
    return session


def fetch_all_issues(
    session: requests.Session,
    base_url: str,
    jql: str,
    fields: Union[str, List[str]],
    max_results: Optional[int] = None,
    expand: Optional[str] = None,
) -> List[Dict]:
    if isinstance(fields, str) and fields != "*all":
        fields = [f.strip() for f in fields.split(",")]

    all_issues: List[Dict] = []
    next_page_token = None
    page_size = min(100, max_results) if max_results else 100

    while True:
        params: Dict = {"jql": jql, "fields": fields, "maxResults": page_size}
        if expand:
            params["expand"] = expand
        if next_page_token:
            params["nextPageToken"] = next_page_token

        url = f"{base_url.rstrip('/')}/rest/api/3/search/jql"
        response = session.get(url, params=params)
        response.raise_for_status()
        result = response.json()

        issues = result.get("issues", [])
        if not issues:
            break
        all_issues.extend(issues)

        next_page_token = result.get("nextPageToken")
        if not next_page_token:
            break
        if max_results and len(all_issues) >= max_results:
            break
        if max_results:
            page_size = min(100, max_results - len(all_issues))

    return all_issues[:max_results] if max_results else all_issues
