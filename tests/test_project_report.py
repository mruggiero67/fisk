import statistics
from datetime import datetime, timezone

import pytest

from fisk.jira.utils import (
    get_iso_week_label,
    classify_trend,
    parse_jira_datetime,
)
from fisk.jira.project_report import (
    calculate_cycle_time_days,
    group_by_week,
    weekly_cycle_time_stats,
    build_cycle_time_summary,
    process_resolved_issues,
    process_created_issues,
)


# ── get_iso_week_label ──────────────────────────────────────────────

def test_iso_week_label_jan1_no_week_zero():
    # Jan 1 2023 is a Sunday — belongs to week 52 of 2022, not W00 of 2023
    dt = datetime(2023, 1, 1, tzinfo=timezone.utc)
    label = get_iso_week_label(dt)
    assert label == "2022-W52"
    assert "W00" not in label


def test_iso_week_label_jan2_2023():
    # Jan 2 2023 is a Monday — first day of 2023-W01
    dt = datetime(2023, 1, 2, tzinfo=timezone.utc)
    assert get_iso_week_label(dt) == "2023-W01"


def test_iso_week_label_same_week_monday_friday():
    monday = datetime(2026, 2, 16, tzinfo=timezone.utc)
    friday = datetime(2026, 2, 20, tzinfo=timezone.utc)
    assert get_iso_week_label(monday) == get_iso_week_label(friday)


def test_iso_week_label_format():
    dt = datetime(2026, 3, 9, tzinfo=timezone.utc)
    label = get_iso_week_label(dt)
    assert label.startswith("2026-W")
    week_num = int(label.split("W")[1])
    assert 1 <= week_num <= 53


# ── process_resolved_issues ─────────────────────────────────────────

def _make_raw_issue(key, created, resolved, assignee=None, points=None):
    return {
        "key": key,
        "fields": {
            "summary": f"Issue {key}",
            "created": created,
            "resolutiondate": resolved,
            "status": {"name": "Done"},
            "issuetype": {"name": "Story"},
            "priority": {"name": "Medium"},
            "assignee": {"displayName": assignee, "emailAddress": f"{assignee}@test.com"} if assignee else None,
            "reporter": {"displayName": "Reporter"},
            "customfield_10016": points,
        },
    }


def test_process_resolved_issues_cycle_time():
    raw = [_make_raw_issue(
        "SUP-1",
        "2026-01-01T00:00:00.000+0000",
        "2026-01-08T00:00:00.000+0000",
        assignee="Alice",
        points=3,
    )]
    result = process_resolved_issues(raw)
    assert len(result) == 1
    r = result[0]
    assert r["issue_key"] == "SUP-1"
    assert abs(r["cycle_time_days"] - 7.0) < 0.01
    assert r["status"] == "Done"
    assert r["assignee"] == "Alice"
    assert r["story_points"] == 3


def test_process_resolved_issues_skips_missing_dates():
    raw = [
        _make_raw_issue("SUP-2", None, "2026-01-08T00:00:00.000+0000"),
        _make_raw_issue("SUP-3", "2026-01-01T00:00:00.000+0000", None),
    ]
    result = process_resolved_issues(raw)
    assert result == []


def test_process_resolved_issues_iso_week():
    raw = [_make_raw_issue(
        "SUP-4",
        "2026-02-16T00:00:00.000+0000",
        "2026-02-20T00:00:00.000+0000",
    )]
    result = process_resolved_issues(raw)
    assert result[0]["resolved_iso_week"] == "2026-W08"
    assert result[0]["created_iso_week"] == "2026-W08"


# ── process_created_issues ──────────────────────────────────────────

def test_process_created_issues_iso_week():
    raw = [{
        "key": "FED-1",
        "fields": {
            "summary": "Feature",
            "created": "2026-01-05T12:00:00.000+0000",
            "status": {"name": "In Progress"},
            "issuetype": {"name": "Task"},
            "priority": {"name": "High"},
            "assignee": None,
            "reporter": {"displayName": "Bob"},
            "customfield_10016": None,
        },
    }]
    result = process_created_issues(raw)
    assert len(result) == 1
    assert result[0]["created_iso_week"] == "2026-W02"
    assert result[0]["assignee"] == "Unassigned"


# ── weekly_cycle_time_stats ─────────────────────────────────────────

def test_weekly_cycle_time_stats_separate_counts():
    grouped = {
        "2026-W01": [
            {"cycle_time_days": 5.0},
            {"cycle_time_days": 0.0},
            {"cycle_time_days": 10.0},
        ]
    }
    stats = weekly_cycle_time_stats(grouped)
    assert len(stats) == 1
    week, avg, total, nonzero = stats[0]
    assert week == "2026-W01"
    assert total == 3
    assert nonzero == 2
    assert abs(avg - 7.5) < 0.01


def test_weekly_cycle_time_stats_all_zero():
    grouped = {"2026-W02": [{"cycle_time_days": 0.0}, {"cycle_time_days": 0.0}]}
    stats = weekly_cycle_time_stats(grouped)
    _, avg, total, nonzero = stats[0]
    assert total == 2
    assert nonzero == 0
    assert avg == 0.0


# ── build_cycle_time_summary ────────────────────────────────────────

def test_build_cycle_time_summary_median_even_list():
    # Bug 2/3: for [2, 4, 6, 8], true median is 5.0, sorted[len//2] returns 6
    stats = [
        ("2026-W01", 2.0, 1, 1),
        ("2026-W02", 4.0, 1, 1),
        ("2026-W03", 6.0, 1, 1),
        ("2026-W04", 8.0, 1, 1),
    ]
    lines = build_cycle_time_summary(stats, "2026-01-01", "2026-02-01")
    median_line = lines[1]
    assert "5.0d" in median_line


def test_build_cycle_time_summary_label_median_weekly_avg():
    stats = [("2026-W01", 3.0, 1, 1), ("2026-W02", 5.0, 1, 1)]
    lines = build_cycle_time_summary(stats, "2026-01-01", "2026-02-01")
    assert "Median weekly avg" in lines[1]


def test_build_cycle_time_summary_empty():
    lines = build_cycle_time_summary([], "2026-01-01", "2026-02-01")
    assert "No resolved tickets found" in lines[1]


# ── classify_trend ──────────────────────────────────────────────────

def test_classify_trend_growing():
    # Low CV but clear upward trend: second-half avg is >10% above first-half avg
    assert classify_trend([3, 4, 4, 5, 6, 7, 7, 8]) == "growing"


def test_classify_trend_shrinking():
    # Low CV but clear downward trend
    assert classify_trend([8, 7, 7, 6, 5, 4, 4, 3]) == "shrinking"


def test_classify_trend_stable():
    assert classify_trend([5, 5, 5, 5, 5, 5]) == "stable"


def test_classify_trend_highly_variable():
    assert classify_trend([1, 100, 1, 100, 1, 100]) == "highly variable"


def test_classify_trend_insufficient_data():
    assert classify_trend([5]) == "insufficient data"
    assert classify_trend([]) == "insufficient data"


# ── parse_jira_datetime ─────────────────────────────────────────────

def test_parse_jira_datetime_tz_naive_becomes_utc():
    dt = parse_jira_datetime("2026-01-15T12:00:00.000")
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_jira_datetime_tz_aware_preserved():
    dt = parse_jira_datetime("2026-01-15T12:00:00.000+1000")
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_jira_datetime_none():
    assert parse_jira_datetime(None) is None
    assert parse_jira_datetime("") is None


def test_parse_jira_datetime_invalid():
    assert parse_jira_datetime("not-a-date") is None


# ── calculate_cycle_time_days ───────────────────────────────────────

def test_calculate_cycle_time_days_seven_days():
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    resolved = datetime(2026, 1, 8, tzinfo=timezone.utc)
    assert abs(calculate_cycle_time_days(created, resolved) - 7.0) < 0.001


def test_calculate_cycle_time_days_missing():
    assert calculate_cycle_time_days(None, datetime(2026, 1, 1, tzinfo=timezone.utc)) == 0.0
    assert calculate_cycle_time_days(datetime(2026, 1, 1, tzinfo=timezone.utc), None) == 0.0
