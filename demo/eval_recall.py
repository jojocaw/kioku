"""Recall check: ask the persona's Kioku the EVAL questions and count the answers that carry every expected fact.

    .venv/bin/python demo/eval_recall.py                    # chat on Lightning (the default route)
    .venv/bin/python demo/eval_recall.py --chat-model super  # same questions, chat routed to Super
    .venv/bin/python demo/eval_recall.py --publish demo/seed # put the saved results on the demo page (no model calls)

Runs on a throwaway copy of the persona's data, so the persona's memory is not changed. Each question is a fresh
session (no chat history) on the persona's "today". Results go to demo/eval/recall-<model>.json.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'demo' / 'persona')]

import timeline as T  # noqa: E402

from kioku.agent import Kioku  # noqa: E402
from kioku.config import MODELS, ROUTES, Settings  # noqa: E402

TZ = ZoneInfo('Asia/Tokyo')


def check(text: str, facts: list[tuple[str, ...]]) -> list[bool]:
    low = ' '.join(text.replace('\u202f', ' ').replace('\u00a0', ' ').replace('\u2009', ' ').split()).lower()
    return [any(alt.lower() in low for alt in alts) for alts in facts]


def publish(seed: Path) -> None:
    """Copy the saved summaries into the seed; the web page's Models & cost tab reads them from there."""
    rows = [json.loads(p.read_text(encoding='utf-8'))['summary'] for p in sorted((ROOT / 'demo' / 'eval').glob('recall-*.json'))]
    rows.sort(key=lambda r: r['cost_usd'])
    (seed / 'recall.json').write_text(json.dumps(dict(rows=rows), indent=1), encoding='utf-8')
    for r in rows:
        print(f"{r['model']}: {r['correct']}/{r['questions']}, ${r['cost_usd']:.4f}, {r['avg_latency_s']} s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=str(ROOT / 'data' / 'persona-rin'))
    ap.add_argument('--chat-model', default='lightning', choices=sorted(MODELS))
    ap.add_argument('--only', type=int, nargs='*', help='question numbers (1-based) to run')
    ap.add_argument('--publish', metavar='SEED', help='copy the saved results into SEED/recall.json and stop')
    args = ap.parse_args()
    if args.publish:
        return publish(Path(args.publish))

    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / 'persona'
        shutil.copytree(args.data, data)
        routes = dict(ROUTES, chat=(args.chat_model, False))
        settings = Settings.from_env(ROOT, data_dir=data, timezone='Asia/Tokyo', private_terms=T.PRIVATE_TERMS,
                                     max_payment=100_000, routes=routes)
        start = datetime.combine(date.fromisoformat(T.TODAY), datetime.min.time(), TZ).replace(hour=9)
        results = []
        for i, (question, facts, source) in enumerate(T.EVAL, 1):
            if args.only and i not in args.only:
                continue
            now = start + timedelta(minutes=i)
            k = Kioku(settings, clock=lambda now=now: now)
            r = k.chat(question)
            hits = check(r.text, facts)
            ok = all(hits)
            results.append(dict(n=i, question=question, source=source, ok=ok, hits=hits, answer=r.text,
                                tools=[e.tool for e in r.events], calls=len(r.calls),
                                tokens=sum(c['tokens'] for c in r.calls), cost_usd=sum(c['cost_usd'] for c in r.calls),
                                latency_ms=sum(c['latency_ms'] for c in r.calls)))
            mark = 'OK  ' if ok else 'MISS'
            print(f'{i:2d} {mark} {question[:60]:60s} {"/".join("y" if h else "n" for h in hits):12s} '
                  f'{results[-1]["latency_ms"] / 1000:4.1f}s  tools={",".join(results[-1]["tools"]) or "-"}', flush=True)

    n_ok = sum(r['ok'] for r in results)
    summary = dict(model=MODELS[args.chat_model].id, questions=len(results), correct=n_ok,
                   tokens=sum(r['tokens'] for r in results), cost_usd=round(sum(r['cost_usd'] for r in results), 6),
                   avg_latency_s=round(sum(r['latency_ms'] for r in results) / max(1, len(results)) / 1000, 2))
    print(f'\n{n_ok}/{len(results)} answers carried every expected fact — {summary}')
    out = ROOT / 'demo' / 'eval' / f'recall-{args.chat_model}.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(dict(summary=summary, results=results), ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
