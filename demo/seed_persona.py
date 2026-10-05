"""Grow the fictional persona's memory by playing demo/persona/timeline.py through the real Kioku.

    .venv/bin/python demo/seed_persona.py               # → data/persona-rin/ (picks up where it stopped)
    .venv/bin/python demo/seed_persona.py --fresh       # start over
    .venv/bin/python demo/seed_persona.py --until 2026-07-12

Each day, on a simulated clock: morning briefing (open days) → the owner's messages at their times, with the
owner deciding approvals a few minutes later from the owner's controls → nightly journal → weekly review on
Sundays → monthly review on the last day of the month. Every call goes to Token Factory (Nemotron) through
Kioku Guard, so the audit log, usage ledger and approval queue fill up exactly as they would in real use.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'demo' / 'persona')]

import timeline as T  # noqa: E402

from kioku import skills  # noqa: E402
from kioku.agent import Kioku  # noqa: E402
from kioku.config import Settings  # noqa: E402
from kioku.llm import CreditsExhausted, LLMError  # noqa: E402
from kioku.memory import week_id  # noqa: E402

TZ = ZoneInfo('Asia/Tokyo')


class Clock:
    """The persona's clock. The seeder moves it; Kioku, the guard and the ledger all read it."""
    def __init__(self):
        self.now = datetime(2026, 7, 6, 0, 0, tzinfo=TZ)

    def __call__(self) -> datetime:
        return self.now


def at(d: date, hhmm: str) -> datetime:
    h, m = hhmm.split(':')
    return datetime(d.year, d.month, d.day, int(h), int(m), tzinfo=TZ)


def is_open(d: date) -> bool:
    return d.isoweekday() != 1 and d.isoformat() not in T.CLOSED


def retry(label: str, fn, tries: int = 3):
    for i in range(tries):
        try:
            return fn()
        except CreditsExhausted:
            raise
        except (ValueError, LLMError) as e:  # ValueError = no valid JSON twice in a row
            print(f'    ! {label}: {e} (try {i + 1}/{tries})', flush=True)
            time.sleep(3)
    return None


def run_day(settings: Settings, clock: Clock, d: date, log) -> dict:
    k = Kioku(settings, clock=clock)  # one session per day: a fresh placeholder vault and chat history
    stats = dict(msgs=0, events=[])
    if is_open(d):
        clock.now = at(d, T.BRIEFING_AT)
        retry('briefing', lambda: skills.morning_briefing(k, d))
    for item in T.DAYS.get(d.isoformat(), []):
        hhmm, text, opts = (item + ({},))[:3]
        clock.now = at(d, hhmm)
        if opts.get('reject_pending'):
            for a in k.approvals.list():
                k.decide(a['id'], False)
                stats['events'].append(f'{a["id"]} rejected')
        before = {a['id'] for a in k.approvals.list(None)}
        try:
            r = k.chat(text)
        except LLMError as e:
            if isinstance(e, CreditsExhausted):
                raise
            print(f'    ! chat {hhmm}: {e}', flush=True)
            continue
        stats['msgs'] += 1
        stats['events'] += [f'{e.tool}:{e.decision}' + (f'({e.rule})' if e.decision != 'allow' else '') for e in r.events]
        new = [a for a in k.approvals.list() if a['id'] not in before]
        log.write(json.dumps(dict(at=clock.now.isoformat(timespec='minutes'), owner=text, kioku=r.text,
                                  events=[(e.tool, e.decision, e.rule, e.detail) for e in r.events],
                                  calls=r.calls, blocked=r.blocked), ensure_ascii=False) + '\n')
        log.flush()
        if new and not opts.get('hold'):
            clock.now = at(d, hhmm) + timedelta(minutes=3)
            approve = not opts.get('reject')
            for a in new:
                k.decide(a['id'], approve)
                stats['events'].append(f'{a["id"]} {"approved" if approve else "rejected"}')
    clock.now = at(d, T.JOURNAL_AT)
    stats['journal'] = bool(retry('journal', lambda: skills.daily_journal(k, d)))
    if d.isoweekday() == 7:
        clock.now = at(d, T.WEEKLY_AT)
        stats['weekly'] = retry('weekly', lambda: skills.weekly_review(k, week_id(d)))
    if (d + timedelta(days=1)).day == 1:
        clock.now = at(d, T.MONTHLY_AT)
        stats['monthly'] = retry('monthly', lambda: skills.monthly_review(k, d.year, d.month))
    return stats


def write_persona(out: Path) -> None:
    """persona.json: what the demo page needs to know (who the visitor plays, the persona's clock, one-click prompts)."""
    (out / 'persona.json').write_text(json.dumps(T.PERSONA, ensure_ascii=False, indent=1), encoding='utf-8')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(ROOT / 'data' / 'persona-rin'))
    ap.add_argument('--fresh', action='store_true')
    ap.add_argument('--until', default=T.END)
    ap.add_argument('--persona-only', action='store_true', help='only (re)write persona.json for the demo page')
    ap.add_argument('--redo-weekly', metavar='WEEK', help='run one weekly review again (e.g. 2026-W40) at its own time')
    args = ap.parse_args()

    out = Path(args.out)
    if args.persona_only:
        return write_persona(out)
    if args.redo_weekly:
        settings = Settings.from_env(ROOT, data_dir=out, timezone='Asia/Tokyo', private_terms=T.PRIVATE_TERMS,
                                     max_payment=100_000)
        clock = Clock()
        sunday = date.fromisocalendar(int(args.redo_weekly[:4]), int(args.redo_weekly[-2:]), 7)
        clock.now = at(sunday, T.WEEKLY_AT)
        rel = retry('weekly', lambda: skills.weekly_review(Kioku(settings, clock=clock), args.redo_weekly))
        return print(f'{args.redo_weekly}: {rel}')
    if args.fresh and out.exists():
        shutil.rmtree(out)
    mem = out / 'memory'
    mem.mkdir(parents=True, exist_ok=True)
    settings = Settings.from_env(ROOT, data_dir=out, timezone='Asia/Tokyo', private_terms=T.PRIVATE_TERMS,
                                 max_payment=100_000)
    if not settings.api_key:
        sys.exit('NEBIUS_API_KEY is not set (.env)')

    clock = Clock()
    if not (mem / 'index.md').exists():  # the owner writes a profile on day one; the index starts from it
        (mem / 'profile.md').write_text((ROOT / 'demo' / 'persona' / 'profile.md').read_text(encoding='utf-8'), encoding='utf-8')
        clock.now = at(date.fromisoformat(T.START), '20:00')
        skills.refresh_index(Kioku(settings, clock=clock).memory, note='setup')

    state_path = out / 'seed_state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {'done': []}
    d, end = date.fromisoformat(T.START), date.fromisoformat(args.until)
    started = time.monotonic()
    with (out / 'seed_log.jsonl').open('a', encoding='utf-8') as log:
        while d <= end:
            if d.isoformat() not in state['done']:
                for rel in (f'log/{d.isoformat()}.jsonl', f'daily/{d.isoformat()}.md'):  # a half-finished day
                    (mem / rel).unlink(missing_ok=True)
                t0 = time.monotonic()
                try:
                    s = run_day(settings, clock, d, log)
                except CreditsExhausted as e:
                    sys.exit(f'stopped: {e}')
                state['done'].append(d.isoformat())
                state_path.write_text(json.dumps(state, indent=1))
                extra = ''.join(f' {key}={s[key]}' for key in ('weekly', 'monthly') if key in s)
                print(f'{d.strftime("%m-%d %a")}  msgs={s["msgs"]} journal={"y" if s["journal"] else "-"}{extra}  '
                      f'{time.monotonic() - t0:4.0f}s  {", ".join(s["events"])}', flush=True)
            d += timedelta(days=1)

    write_persona(out)
    from kioku.llm import UsageLedger
    rows = UsageLedger(out / 'usage.jsonl').rows()
    tokens = sum(r.get('prompt_tokens', 0) + r.get('completion_tokens', 0) for r in rows)
    print(f'done: {len(rows)} model calls, {tokens:,} tokens, ${sum(r.get("cost_usd", 0) for r in rows):.4f}, '
          f'{(time.monotonic() - started) / 60:.1f} min this run')


if __name__ == '__main__':
    main()
