"""The daily rhythm: when each habit runs. A tiny cron (minute hour day-of-month month day-of-week).

    23:43 every day     daily journal (for today)
    07:00 every day     morning briefing
    04:00 Sunday        weekly review (for the week that just ended)
    05:00 on the 1st    monthly review (for the month that just ended)

run_due() catches up on anything missed while the machine was off (up to 3 days back) and remembers what ran
in scheduler.json, so nothing runs twice. replay() runs the same rhythm over past dates (used to build the demo).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from . import skills
from .memory import week_id

JOBS = {
    'daily_journal': '43 23 * * *',
    'morning_briefing': '0 7 * * *',
    'weekly_review': '0 4 * * 0',
    'monthly_review': '0 5 1 * *',
}


def parse_field(text: str, lo: int, hi: int) -> set[int]:
    values: set[int] = set()
    for part in text.split(','):
        step = 1
        if '/' in part:
            part, s = part.split('/')
            step = int(s)
        if part == '*':
            a, b = lo, hi
        elif '-' in part:
            a, b = (int(x) for x in part.split('-'))
        else:
            a = b = int(part)
        values.update(range(a, b + 1, step))
    return values


def matches(expr: str, t: datetime) -> bool:
    minute, hour, dom, month, dow = expr.split()
    return (t.minute in parse_field(minute, 0, 59) and t.hour in parse_field(hour, 0, 23)
            and t.day in parse_field(dom, 1, 31) and t.month in parse_field(month, 1, 12)
            and (t.isoweekday() % 7) in parse_field(dow, 0, 6))  # cron: 0 = Sunday


def due_times(expr: str, after: datetime, until: datetime) -> list[datetime]:
    """Every minute in (after, until] that the expression matches."""
    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    out = []
    while t <= until:
        if matches(expr, t):
            out.append(t)
        t += timedelta(minutes=1)
    return out


def run_job(k, name: str, at: datetime) -> str | None:
    d = at.date()
    if name == 'daily_journal':
        return skills.daily_journal(k, d)
    if name == 'morning_briefing':
        return skills.morning_briefing(k, d) and k.memory.daily(d)
    if name == 'weekly_review':
        return skills.weekly_review(k, week_id(d - timedelta(days=1)))  # Sunday 04:00 → the week that just ended
    if name == 'monthly_review':
        prev = d.replace(day=1) - timedelta(days=1)
        return skills.monthly_review(k, prev.year, prev.month)
    raise KeyError(name)


def run_due(k, now: datetime | None = None, jobs: dict = JOBS) -> list[tuple[str, str, str | None]]:
    now = now or k.clock()
    state_path = Path(k.settings.data_dir) / 'scheduler.json'
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}
    done = []
    for name, expr in jobs.items():
        last = datetime.fromisoformat(state[name]) if name in state else now - timedelta(minutes=1)
        last = max(last, now - timedelta(days=3))
        for at in due_times(expr, last, now):
            done.append((name, at.isoformat(timespec='minutes'), run_job(k, name, at)))
            state[name] = at.isoformat()
            state_path.write_text(json.dumps(state, indent=1))
        state.setdefault(name, now.isoformat())
    state_path.write_text(json.dumps(state, indent=1))
    return done


def replay(k, start: date, end: date) -> list[tuple[str, str, str | None]]:
    """Run the rhythm for every day in [start, end] in order — journal each night, reviews on their days."""
    done = []
    d = start
    while d <= end:
        done.append(('daily_journal', d.isoformat(), skills.daily_journal(k, d)))
        if d.isoweekday() == 7:
            done.append(('weekly_review', week_id(d), skills.weekly_review(k, week_id(d))))
        if (d + timedelta(days=1)).day == 1:
            done.append(('monthly_review', f'{d.year}-{d.month:02d}', skills.monthly_review(k, d.year, d.month)))
        d += timedelta(days=1)
    return done
