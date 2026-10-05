"""Numbers for the README: what the seeded persona's memory holds and what it cost to grow it.

    .venv/bin/python demo/stats.py [data/persona-rin]
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kioku.config import MODELS  # noqa: E402

NAMES = {m.id: f'Nemotron {key.title()}' for key, m in MODELS.items()}
NAMES.update({'nvidia/Nemotron-3_5-Lightning': 'Nemotron 3.5 Lightning', 'nvidia/nemotron-3-super-120b-a12b': 'Nemotron 3 Super'})


def rows(path: Path) -> list[dict]:
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip()] if path.is_file() else []


def main() -> None:
    data = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'data' / 'persona-rin'
    mem = data / 'memory'
    count = lambda sub: len(list((mem / sub).glob('*.md')))  # noqa: E731
    log = rows(data / 'seed_log.jsonl')
    audit = rows(data / 'audit.jsonl')
    usage = rows(data / 'usage.jsonl')
    approvals = json.loads((data / 'approvals.json').read_text()) if (data / 'approvals.json').is_file() else []

    print('### Memory')
    print(f'- {len(log)} owner messages over {count("daily")} days with notes → {count("weekly")} weekly reviews, '
          f'{count("monthly")} monthly reviews, {count("topics")} topic notes')
    words = sum(len(p.read_text(encoding='utf-8').split()) for p in mem.rglob('*.md'))
    print(f'- {words:,} words of Markdown written by the agent and the owner')

    print('\n### Guard receipts')
    by = Counter((r['rule'], r['decision']) for r in audit)
    redacted = Counter()
    for r in audit:
        redacted.update(r.get('redacted') or {})
    print(f'- {len(audit):,} receipts; personal data hidden before {by[("privacy.redact", "allow")]:,} model calls '
          f'({", ".join(f"{k} ×{v:,}" for k, v in redacted.most_common())})')
    for (rule, decision), n in sorted(by.items()):
        if rule not in ('privacy.redact', 'budget.ok', 'tools.allow'):
            print(f'- `{rule}` → {decision}: {n}')
    print(f'- approvals: {len(approvals)} requested, '
          f'{sum(a["status"] == "approved" for a in approvals)} approved, {sum(a["status"] == "rejected" for a in approvals)} rejected '
          f'(by the owner\'s controls)')

    print('\n### Model calls (Nebius Token Factory)')
    print('| Job | Model | Calls | Tokens | Cost | Avg time |\n|---|---|--:|--:|--:|--:|')
    groups: dict[tuple, list] = {}
    for r in usage:
        groups.setdefault((r['task'], r['model']), []).append(r)
    for (task, model), rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        tok = sum(r.get('prompt_tokens', 0) + r.get('completion_tokens', 0) for r in rs)
        cost = sum(r.get('cost_usd', 0) for r in rs)
        avg = sum(r.get('latency_ms', 0) for r in rs) / len(rs) / 1000
        print(f'| {task} | {NAMES.get(model, model)} | {len(rs)} | {tok:,} | ${cost:.4f} | {avg:.1f} s |')
    tok = sum(r.get('prompt_tokens', 0) + r.get('completion_tokens', 0) for r in usage)
    print(f'| **total** | | **{len(usage)}** | **{tok:,}** | **${sum(r.get("cost_usd", 0) for r in usage):.4f}** | |')
    errors = [r for r in usage if not r.get('ok', True)]
    if errors:
        print(f'\n{len(errors)} failed calls: {Counter(r.get("error") for r in errors).most_common(3)}')


if __name__ == '__main__':
    main()
