from __future__ import annotations

import os
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are an engineering metrics analyst for a software company.
You have access to Jira data for five projects: FED, DIP, SUP, OOT, SSJ.

Metrics available:
- Cycle time: average days to resolve a ticket, by week
- Ticket volume: tickets created per week
- Sprint velocity: story points committed vs completed per sprint
- Contributor stats: per-engineer tickets resolved, avg cycle time, story points
- Stage durations: average time in each Jira workflow state

When answering:
- Be specific and cite numbers from the provided data
- Call out trends (growing / shrinking / stable / highly variable)
- Flag concerning signals (high cycle time, declining velocity, stuck work)
- Use plain prose with bullet points where helpful
- If data is missing or the DB has not been synced yet, say so clearly

Current date: {date}
"""


def _gather_context(question: str) -> str:
    from fisk.api.db import (
        get_cycle_time_by_week, get_volume_by_week, get_contributors,
        get_sprint_velocity, get_all_engineers, get_engineer_stats,
    )
    from fisk.jira.utils import classify_trend

    PROJECTS = ["FED", "DIP", "SUP", "OOT", "SSJ"]
    q = question.lower()
    parts = []

    named = [p for p in PROJECTS if p.lower() in q]
    target_projects = named or PROJECTS

    for proj in target_projects:
        ct = get_cycle_time_by_week(proj, 8)
        vol = get_volume_by_week(proj, 8)
        contr = get_contributors(proj, 8)
        ct_values = [r["avg_days"] for r in ct if r.get("avg_days")]
        vol_values = [float(r["ticket_count"]) for r in vol]
        parts.append(
            f"\n=== {proj} — last 8 weeks ===\n"
            f"Cycle time (by week): {json.dumps(ct)}\n"
            f"CT trend: {classify_trend(ct_values)}\n"
            f"Volume (by week): {json.dumps(vol)}\n"
            f"Vol trend: {classify_trend(vol_values)}\n"
            f"Top contributors: {json.dumps(contr[:5])}\n"
        )

    for eng in get_all_engineers():
        if eng.lower() in q:
            stats = get_engineer_stats(eng, 12)
            parts.append(f"\n=== Engineer: {eng} ===\n{json.dumps(stats)}")

    if any(w in q for w in ["sprint", "velocity", "points", "committed", "completed"]):
        for proj in target_projects:
            vel = get_sprint_velocity(proj)
            parts.append(f"\n=== {proj} Velocity (last 6 sprints) ===\n{json.dumps(vel[:6])}")

    return "\n".join(parts) if parts else "No data available. The database may not have been synced yet."


def answer_question(question: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "Insights are unavailable: set the ANTHROPIC_API_KEY environment variable."
    if not question.strip():
        return "Please enter a question."
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        context = _gather_context(question)
        system = SYSTEM_PROMPT.format(date=datetime.now().strftime("%Y-%m-%d"))
        msg = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": f"DATA:\n{context}\n\nQUESTION: {question}"}],
        )
        return msg.content[0].text
    except Exception as e:
        logger.error(f"Insights error: {e}")
        return f"Error contacting Claude API: {e}"
