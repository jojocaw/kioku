"""Command line: python -m kioku <command>

    chat                      talk to Kioku (type 'quit' to leave)
    say "text"                one message
    journal [--date D]        write the daily journal (default: today)
    briefing [--date D]       write this morning's briefing
    weekly [--week 2026-W40]  weekly review (default: last week)
    monthly [--month 2026-09] monthly review (default: last month)
    schedule [--loop]         run whatever is due now (or keep running every minute)
    approvals                 list actions waiting for the owner
    approve ID / reject ID    the owner's decision
    audit [--last N]          the guard's receipts
    usage                     model calls per task: calls, tokens, cost, latency
    search "words"            search the memory
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from . import scheduler, skills
from .agent import Kioku
from .config import Settings
from .memory import week_id


def show_reply(r) -> None:
    for e in r.events:
        mark = {'allow': '✓', 'ask': '?', 'block': '✗'}[e.decision]
        print(f'   {mark} {e.tool} [{e.rule}] {e.detail}  ({e.receipt})')
    print(f'\nKioku: {r.text}\n')
    if r.calls:
        tokens = sum(c['tokens'] for c in r.calls)
        cost = sum(c['cost_usd'] for c in r.calls)
        ms = sum(c['latency_ms'] for c in r.calls)
        print(f'   [{len(r.calls)} call(s) · {tokens:,} tokens · ${cost:.5f} · {ms / 1000:.1f}s · {r.calls[0]["model"]}]')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='kioku', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', help='data folder (default: KIOKU_DATA_DIR or ./data/default)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('chat')
    p = sub.add_parser('say'); p.add_argument('text')
    for name in ('journal', 'briefing'):
        p = sub.add_parser(name); p.add_argument('--date', type=date.fromisoformat)
    p = sub.add_parser('weekly'); p.add_argument('--week')
    p = sub.add_parser('monthly'); p.add_argument('--month')
    p = sub.add_parser('schedule'); p.add_argument('--loop', action='store_true')
    sub.add_parser('approvals')
    for name in ('approve', 'reject'):
        p = sub.add_parser(name); p.add_argument('id')
    p = sub.add_parser('audit'); p.add_argument('--last', type=int, default=20)
    sub.add_parser('usage')
    p = sub.add_parser('search'); p.add_argument('words')
    a = ap.parse_args(argv)

    overrides = {'data_dir': Path(a.data)} if a.data else {}
    settings = Settings.from_env(Path.cwd(), **overrides)
    needs_model = a.cmd in ('chat', 'say', 'journal', 'briefing', 'weekly', 'monthly', 'schedule')
    if needs_model and not settings.api_key:
        print('NEBIUS_API_KEY is not set (put it in .env or the environment).', file=sys.stderr)
        return 2
    k = Kioku(settings)
    today = k.clock().date()

    if a.cmd == 'chat':
        print('Kioku is listening. Type "quit" to leave.\n')
        while True:
            try:
                text = input('you: ').strip()
            except (EOFError, KeyboardInterrupt):
                break
            if text.lower() in ('quit', 'exit'):
                break
            if text:
                show_reply(k.chat(text))
    elif a.cmd == 'say':
        show_reply(k.chat(a.text))
    elif a.cmd == 'journal':
        print(skills.daily_journal(k, a.date or today) or 'Nothing to write for that day.')
    elif a.cmd == 'briefing':
        print(skills.morning_briefing(k, a.date or today))
    elif a.cmd == 'weekly':
        print(skills.weekly_review(k, a.week or week_id(today - timedelta(days=7))) or 'No daily notes in that week.')
    elif a.cmd == 'monthly':
        if a.month:
            y, mth = (int(x) for x in a.month.split('-'))
        else:
            prev = today.replace(day=1) - timedelta(days=1)
            y, mth = prev.year, prev.month
        print(skills.monthly_review(k, y, mth) or 'No weekly reviews in that month.')
    elif a.cmd == 'schedule':
        while True:
            for name, at, out in scheduler.run_due(k):
                print(f'{at} {name}: {out}')
            if not a.loop:
                break
            time.sleep(60)
    elif a.cmd == 'approvals':
        items = k.approvals.list()
        for it in items:
            print(f'{it["id"]}  {it["tool"]}  {it["reason"]}  (receipt {it["receipt"]})')
        print(f'{len(items)} waiting.' if items else 'Nothing is waiting for approval.')
    elif a.cmd in ('approve', 'reject'):
        print(k.decide(a.id, a.cmd == 'approve'))
    elif a.cmd == 'audit':
        for r in k.audit.rows(a.last):
            print(f'{r["id"]} {r["ts"][11:19]} {r["event"]:<10} {r["decision"]:<8} {r["rule"]:<20} {r["reason"]}')
        print('\n' + '\n'.join(f'{n:>4}  {key}' for key, n in k.audit.summary().items()))
    elif a.cmd == 'usage':
        print(f'{"task":<16}{"model":<36}{"calls":>6}{"tokens":>10}{"cost $":>11}{"avg ms":>8}')
        for g in k.ledger.summary():
            print(f'{g["task"]:<16}{g["model"]:<36}{g["calls"]:>6}{g["tokens"]:>10,}{g["cost_usd"]:>11.5f}{g["avg_latency_ms"]:>8}')
    elif a.cmd == 'search':
        for h in k.memory.search(a.words, k=8):
            print(f'{h.score:6.2f}  {h.path} › {h.heading}\n        {h.text[:160]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
