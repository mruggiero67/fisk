from __future__ import annotations

import os
import logging
import statistics as stats_mod
from pathlib import Path

from fastapi import FastAPI, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from fisk.api.db import (
    init_db, get_conn,
    get_cycle_time_by_week, get_volume_by_week,
    get_contributors, get_all_engineers, get_engineer_stats,
    get_sprint_velocity, get_sprint_tickets,
    get_stage_durations, get_stage_issues,
    get_issues_for_week, get_engineer_week_issues,
)
from fisk.api.sync import trigger_sync, get_sync_status
from fisk.api.insights import answer_question
from fisk.jira.utils import classify_trend

logger = logging.getLogger(__name__)

CONFIG_PATH = os.environ.get(
    "JIRA_CONFIG",
    str(Path("~/bin/jira_epic_config.yaml").expanduser()),
)
STATIC_DIR = Path(__file__).parent.parent.parent / "static"

app = FastAPI(title="Fisk — Engineering Metrics Dashboard")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def startup():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        from fisk.jira.utils import load_config_unified
        import fisk.api.db as _db
        cfg = load_config_unified(CONFIG_PATH)
        if cfg.get("db_path"):
            _db.DB_PATH = Path(cfg["db_path"]).expanduser()
    except Exception as e:
        logger.warning(f"Could not read db_path from config: {e}")
    init_db()


@app.get("/")
def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/projects")
def list_projects():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY key").fetchall()]


@app.get("/api/projects/{project_key}/cycle-time")
def cycle_time(project_key: str, weeks: int = Query(8, ge=1, le=52)):
    rows = get_cycle_time_by_week(project_key, weeks)
    values = [r["avg_days"] for r in rows if r.get("avg_days")]
    return {"project": project_key, "weeks": rows, "trend": classify_trend(values)}


@app.get("/api/projects/{project_key}/volume")
def volume(project_key: str, weeks: int = Query(8, ge=1, le=52)):
    rows = get_volume_by_week(project_key, weeks)
    values = [float(r["ticket_count"]) for r in rows]
    return {"project": project_key, "weeks": rows, "trend": classify_trend(values)}


@app.get("/api/projects/{project_key}/velocity")
def velocity(project_key: str, weeks: int = Query(8, ge=1, le=52)):
    sprints = get_sprint_velocity(project_key, weeks)
    for s in sprints:
        committed = s.get("committed") or 0
        completed = s.get("completed") or 0
        s["completion_rate"] = round(completed / committed * 100, 1) if committed else 0
    vals = [s["completed"] for s in sprints]
    if len(vals) >= 2:
        velocity_stats = {
            "mean":    round(stats_mod.mean(vals), 1),
            "median":  round(stats_mod.median(vals), 1),
            "std_dev": round(stats_mod.stdev(vals), 1),
        }
    elif len(vals) == 1:
        velocity_stats = {"mean": vals[0], "median": vals[0], "std_dev": 0.0}
    else:
        velocity_stats = {"mean": None, "median": None, "std_dev": None}
    return {"project": project_key, "sprints": sprints, "stats": velocity_stats}


@app.get("/api/sprints/{sprint_id}/issues")
def sprint_issues_list(sprint_id: str):
    return {"sprint_id": sprint_id, "issues": get_sprint_tickets(sprint_id)}


@app.get("/api/projects/{project_key}/contributors")
def contributors(project_key: str, weeks: int = Query(8, ge=1, le=52)):
    return {"project": project_key, "contributors": get_contributors(project_key, weeks)}


@app.get("/api/projects/{project_key}/stage-durations")
def stage_durations(project_key: str, weeks: int = Query(12, ge=1, le=52)):
    return {"project": project_key, "stages": get_stage_durations(project_key, weeks)}


@app.get("/api/projects/{project_key}/stage-issues")
def stage_issues(
    project_key: str,
    stage: str = Query(...),
    weeks: int = Query(12, ge=1, le=52),
):
    issues = get_stage_issues(project_key, stage, weeks)
    return {"project": project_key, "stage": stage, "issues": issues}


@app.get("/api/config")
def config_info():
    from fisk.jira.utils import load_config_unified
    try:
        cfg = load_config_unified(CONFIG_PATH)
        return {"jira_base_url": cfg["base_url"], "projects": cfg.get("projects", {})}
    except Exception:
        return {"jira_base_url": "", "projects": {}}


@app.get("/api/projects/{project_key}/issues")
def project_issues(
    project_key: str,
    week: str = Query(..., description="ISO week label, e.g. 2026-W08"),
    type: str = Query("resolved", pattern="^(resolved|created)$"),
):
    issues = get_issues_for_week(project_key, week, type)
    return {"project": project_key, "week": week, "type": type, "issues": issues}


@app.get("/api/engineers")
def engineers():
    from_db = get_all_engineers()
    if from_db:
        return {"engineers": from_db}
    return {"engineers": _fetch_engineers_from_jira()}


def _fetch_engineers_from_jira() -> list[str]:
    from fisk.jira.utils import load_config_unified, create_jira_session, fetch_all_issues
    try:
        config = load_config_unified(CONFIG_PATH)
        session = create_jira_session(config["base_url"], config["username"], config["api_token"])
        projects_jql = " OR ".join(f"project = {p}" for p in ["FED", "DIP", "SUP", "OOT", "SSJ"])
        jql = f"({projects_jql}) AND status IN (Done, Resolved, Closed) AND resolutiondate >= -30d"
        issues = fetch_all_issues(session, config["base_url"], jql, ["assignee"], max_results=500)
        seen: set[str] = set()
        result = []
        for issue in issues:
            name = (issue.get("fields", {}).get("assignee") or {}).get("displayName")
            if name and name not in seen:
                seen.add(name)
                result.append(name)
        return sorted(result)
    except Exception as e:
        logger.error(f"Failed to fetch engineers from Jira: {e}")
        return []


@app.get("/api/engineers/{name}/stats")
def engineer_stats(name: str, weeks: int = Query(8, ge=1, le=52)):
    return {"engineer": name, "stats": get_engineer_stats(name, weeks)}


@app.get("/api/engineers/{name}/issues")
def engineer_week_issues(name: str, week: str = Query(...)):
    return {"engineer": name, "week": week, "issues": get_engineer_week_issues(name, week)}


@app.post("/api/sync")
def sync():
    started = trigger_sync(CONFIG_PATH)
    return {"started": started, "message": "Sync started" if started else "Sync already running"}


@app.get("/api/sync/status")
def sync_status():
    return get_sync_status()


class InsightsRequest(BaseModel):
    question: str


@app.post("/api/insights")
def insights(body: InsightsRequest):
    answer = answer_question(body.question)
    return {"question": body.question, "answer": answer}
