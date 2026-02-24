from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path("~/Desktop/debris/fisk_db.sqlite").expanduser()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    key            TEXT PRIMARY KEY,
    name           TEXT,
    board_ids      TEXT,
    last_synced_at TEXT
);

INSERT OR IGNORE INTO projects (key, name) VALUES
    ('FED', 'Front End Dev'),
    ('DIP', 'Data & Integrations Platform'),
    ('SUP', 'Engineering Support'),
    ('OOT', 'Onboarding and Originations Team'),
    ('SSJ', 'SSJ Project');

CREATE TABLE IF NOT EXISTS issues (
    issue_key         TEXT PRIMARY KEY,
    project_key       TEXT NOT NULL,
    summary           TEXT,
    issue_type        TEXT,
    status            TEXT,
    priority          TEXT,
    assignee          TEXT,
    assignee_email    TEXT,
    reporter          TEXT,
    created_at        TEXT,
    updated_at        TEXT,
    resolved_at       TEXT,
    story_points      REAL,
    cycle_time_days   REAL,
    created_iso_week  TEXT,
    resolved_iso_week TEXT,
    components        TEXT,
    labels            TEXT,
    synced_at         TEXT,
    FOREIGN KEY (project_key) REFERENCES projects (key)
);

CREATE INDEX IF NOT EXISTS idx_issues_project   ON issues (project_key);
CREATE INDEX IF NOT EXISTS idx_issues_assignee  ON issues (assignee);
CREATE INDEX IF NOT EXISTS idx_issues_resolved  ON issues (resolved_iso_week);
CREATE INDEX IF NOT EXISTS idx_issues_created   ON issues (created_iso_week);

CREATE TABLE IF NOT EXISTS sprints (
    sprint_id   TEXT PRIMARY KEY,
    board_id    INTEGER,
    board_name  TEXT,
    project_key TEXT,
    name        TEXT,
    state       TEXT,
    start_date  TEXT,
    end_date    TEXT,
    goal        TEXT,
    FOREIGN KEY (project_key) REFERENCES projects (key)
);

CREATE TABLE IF NOT EXISTS sprint_issues (
    sprint_id         TEXT,
    issue_key         TEXT,
    story_points      REAL,
    completed         INTEGER,
    added_after_start INTEGER,
    PRIMARY KEY (sprint_id, issue_key)
);

CREATE TABLE IF NOT EXISTS stage_durations (
    issue_key     TEXT,
    stage         TEXT,
    duration_days REAL,
    PRIMARY KEY (issue_key, stage)
);

CREATE TABLE IF NOT EXISTS sync_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key     TEXT,
    sync_type       TEXT,
    started_at      TEXT,
    completed_at    TEXT,
    status          TEXT,
    error_msg       TEXT,
    records_fetched INTEGER
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA_SQL)


def get_cycle_time_by_week(project_key: str, weeks: int) -> list[dict]:
    sql = """
        SELECT resolved_iso_week AS week,
               AVG(cycle_time_days) AS avg_days,
               COUNT(*) AS total_issues,
               COUNT(CASE WHEN cycle_time_days > 0 THEN 1 END) AS nonzero_issues
        FROM issues
        WHERE project_key = ?
          AND resolved_iso_week IS NOT NULL
          AND resolved_at >= date('now', ? || ' days')
        GROUP BY resolved_iso_week
        ORDER BY resolved_iso_week
    """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, (project_key, f"-{weeks * 7}")).fetchall()]


def get_volume_by_week(project_key: str, weeks: int) -> list[dict]:
    sql = """
        SELECT created_iso_week AS week, COUNT(*) AS ticket_count
        FROM issues
        WHERE project_key = ?
          AND created_iso_week IS NOT NULL
          AND created_at >= date('now', ? || ' days')
        GROUP BY created_iso_week
        ORDER BY created_iso_week
    """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, (project_key, f"-{weeks * 7}")).fetchall()]


def get_contributors(project_key: str, weeks: int) -> list[dict]:
    sql = """
        SELECT assignee,
               COUNT(*) AS tickets_resolved,
               AVG(cycle_time_days) AS avg_cycle_time,
               SUM(COALESCE(story_points, 0)) AS total_points
        FROM issues
        WHERE project_key = ?
          AND resolved_at IS NOT NULL
          AND resolved_at >= date('now', ? || ' days')
          AND assignee != 'Unassigned'
        GROUP BY assignee
        ORDER BY tickets_resolved DESC
    """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, (project_key, f"-{weeks * 7}")).fetchall()]


def get_issues_for_week(project_key: str, iso_week: str, filter_type: str) -> list[dict]:
    if filter_type == "resolved":
        sql = """
            SELECT issue_key, summary, assignee, status, priority, issue_type,
                   cycle_time_days, created_at, resolved_at
            FROM issues
            WHERE project_key = ? AND resolved_iso_week = ?
            ORDER BY cycle_time_days DESC NULLS LAST
        """
    else:
        sql = """
            SELECT issue_key, summary, assignee, status, priority, issue_type,
                   cycle_time_days, created_at, resolved_at
            FROM issues
            WHERE project_key = ? AND created_iso_week = ?
            ORDER BY created_at DESC
        """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, (project_key, iso_week)).fetchall()]


def get_all_engineers() -> list[str]:
    sql = "SELECT DISTINCT assignee FROM issues WHERE assignee != 'Unassigned' ORDER BY assignee"
    with get_conn() as conn:
        return [r[0] for r in conn.execute(sql).fetchall()]


def get_engineer_stats(name: str, weeks: int) -> dict:
    days = f"-{weeks * 7}"
    by_project_sql = """
        SELECT project_key,
               COUNT(*) AS tickets,
               AVG(cycle_time_days) AS avg_cycle_time,
               SUM(COALESCE(story_points, 0)) AS total_points,
               MIN(cycle_time_days) AS min_cycle,
               MAX(cycle_time_days) AS max_cycle
        FROM issues
        WHERE assignee = ?
          AND resolved_at IS NOT NULL
          AND resolved_at >= date('now', ? || ' days')
        GROUP BY project_key
    """
    weekly_sql = """
        SELECT resolved_iso_week AS week, COUNT(*) AS tickets
        FROM issues
        WHERE assignee = ?
          AND resolved_at IS NOT NULL
          AND resolved_at >= date('now', ? || ' days')
        GROUP BY resolved_iso_week
        ORDER BY resolved_iso_week
    """
    avg_sprint_sql = """
        SELECT AVG(sprint_count) AS avg_per_sprint
        FROM (
            SELECT si.sprint_id, COUNT(*) AS sprint_count
            FROM sprint_issues si
            JOIN issues i ON i.issue_key = si.issue_key
            JOIN sprints s ON s.sprint_id = si.sprint_id
            WHERE i.assignee = ?
              AND si.completed = 1
              AND s.end_date >= date('now', ? || ' days')
            GROUP BY si.sprint_id
        )
    """
    with get_conn() as conn:
        by_project = [dict(r) for r in conn.execute(by_project_sql, (name, days)).fetchall()]
        by_week = [dict(r) for r in conn.execute(weekly_sql, (name, days)).fetchall()]
        row = conn.execute(avg_sprint_sql, (name, days)).fetchone()
        avg_per_sprint = round(row["avg_per_sprint"], 1) if row["avg_per_sprint"] is not None else None
    return {"by_project": by_project, "by_week": by_week, "avg_per_sprint": avg_per_sprint}


def get_sprint_tickets(sprint_id: str) -> list[dict]:
    sql = """
        SELECT si.issue_key,
               COALESCE(i.summary, '') AS summary,
               COALESCE(i.assignee, '') AS assignee,
               COALESCE(i.status, '') AS status,
               COALESCE(i.priority, '') AS priority,
               si.completed,
               i.cycle_time_days,
               i.resolved_at
        FROM sprint_issues si
        LEFT JOIN issues i ON i.issue_key = si.issue_key
        WHERE si.sprint_id = ?
        ORDER BY si.completed DESC, i.cycle_time_days DESC NULLS LAST
    """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, (sprint_id,)).fetchall()]


def get_engineer_week_issues(name: str, week: str) -> list[dict]:
    sql = """
        SELECT issue_key, summary, project_key, status, priority, issue_type,
               cycle_time_days, created_at, resolved_at
        FROM issues
        WHERE assignee = ? AND resolved_iso_week = ?
        ORDER BY cycle_time_days DESC NULLS LAST
    """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, (name, week)).fetchall()]


def get_sprint_velocity(project_key: str, weeks: int = 8) -> list[dict]:
    sql = """
        SELECT s.sprint_id, s.name, s.start_date, s.end_date,
               COALESCE(COUNT(si.issue_key), 0) AS committed,
               COALESCE(SUM(CASE WHEN si.completed = 1 THEN 1 ELSE 0 END), 0) AS completed
        FROM sprints s
        LEFT JOIN sprint_issues si ON si.sprint_id = s.sprint_id
        WHERE s.project_key = ?
          AND s.state = 'closed'
          AND s.end_date >= date('now', ? || ' days')
        GROUP BY s.sprint_id
        ORDER BY s.start_date DESC
    """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, (project_key, f"-{weeks * 7}")).fetchall()]


def get_stage_durations(project_key: str, weeks: int = 12) -> list[dict]:
    sql = """
        SELECT sd.stage, AVG(sd.duration_days) AS avg_days, COUNT(*) AS issue_count
        FROM stage_durations sd
        JOIN issues i ON i.issue_key = sd.issue_key
        WHERE i.project_key = ?
          AND i.resolved_at >= date('now', ? || ' days')
        GROUP BY sd.stage
        ORDER BY avg_days DESC
    """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, (project_key, f"-{weeks * 7}")).fetchall()]


def get_stage_issues(project_key: str, stage: str, weeks: int) -> list[dict]:
    sql = """
        SELECT i.issue_key, i.summary, i.assignee, i.status, i.priority,
               sd.duration_days AS stage_days,
               i.created_at, i.resolved_at
        FROM stage_durations sd
        JOIN issues i ON i.issue_key = sd.issue_key
        WHERE i.project_key = ?
          AND sd.stage = ?
          AND i.resolved_at >= date('now', ? || ' days')
        ORDER BY sd.duration_days DESC
    """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, (project_key, stage, f"-{weeks * 7}")).fetchall()]
