"""Nebius Token Factory client (OpenAI-compatible chat completions) and the usage ledger.

- Routes each task to a model (see config.ROUTES) and switches Nemotron's thinking on or off.
  Lightning writes its thinking into `content` unless `chat_template_kwargs.enable_thinking` is false
  (checked 2026-10-04; system-prompt switches such as /no_think do not work).
- Records every call (model, task, tokens, cost, latency) so routing can be judged on real numbers.
- Standard library only.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import MODELS, Settings


class LLMError(Exception):
    pass


class CreditsExhausted(LLMError):
    """HTTP 402: the Token Factory balance is used up."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResult:
    content: str
    tool_calls: list[ToolCall]
    model: str
    task: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    finish_reason: str = ''
    reasoning: str = ''


def parse_tool_calls(raw: list | None) -> list[ToolCall]:
    calls = []
    for i, c in enumerate(raw or []):
        fn = c.get('function') or {}
        args = fn.get('arguments') or '{}'
        try:
            parsed = json.loads(args) if isinstance(args, str) else dict(args)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = {'_unparsed': str(args)[:500]}
        calls.append(ToolCall(id=c.get('id') or f'call_{i}', name=fn.get('name', ''), arguments=parsed))
    return calls


class UsageLedger:
    """Append-only record of model calls (usage.jsonl). Never stores message content."""

    def __init__(self, path: Path, clock=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def record(self, **row) -> None:
        row.setdefault('ts', self.clock().isoformat(timespec='seconds'))
        with self.path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')

    def rows(self) -> list[dict]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding='utf-8').splitlines() if line.strip()]

    def totals_since(self, since: datetime) -> tuple[int, float]:
        tokens, usd = 0, 0.0
        for r in self.rows():
            if datetime.fromisoformat(r['ts']) >= since:
                tokens += r.get('prompt_tokens', 0) + r.get('completion_tokens', 0)
                usd += r.get('cost_usd', 0.0)
        return tokens, usd

    def summary(self) -> list[dict]:
        """Per (task, model): calls, tokens, cost, average latency — the numbers behind the routing choice."""
        groups: dict[tuple, dict] = {}
        for r in self.rows():
            g = groups.setdefault((r.get('task'), r.get('model')), dict(task=r.get('task'), model=r.get('model'),
                                                                    calls=0, errors=0, tokens=0, cost_usd=0.0, latency_ms=0))
            g['calls'] += 1
            g['errors'] += 0 if r.get('ok', True) else 1
            g['tokens'] += r.get('prompt_tokens', 0) + r.get('completion_tokens', 0)
            g['cost_usd'] += r.get('cost_usd', 0.0)
            g['latency_ms'] += r.get('latency_ms', 0)
        for g in groups.values():
            g['avg_latency_ms'] = round(g.pop('latency_ms') / max(1, g['calls']))
            g['cost_usd'] = round(g['cost_usd'], 6)
        return sorted(groups.values(), key=lambda g: (-g['calls'], g['task'] or ''))


def http_post(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


@dataclass
class TokenFactory:
    settings: Settings
    ledger: UsageLedger
    post: callable = field(default=http_post, repr=False)  # replaceable in tests
    timeout: float = 120.0
    retries: int = 3

    def chat(self, messages: list[dict], *, task: str, tools: list[dict] | None = None, tool_choice: str = 'auto',
             max_tokens: int = 1024, temperature: float | None = None) -> LLMResult:
        model_key, thinking = self.settings.routes.get(task, self.settings.routes['chat'])
        model = MODELS[model_key]
        payload = {'model': model.id, 'messages': messages, 'max_tokens': max_tokens,
                   'temperature': 0.6 if temperature is None else temperature}
        if model.thinking_switch:
            payload['chat_template_kwargs'] = {'enable_thinking': thinking}
        if tools:
            payload['tools'] = tools
            payload['tool_choice'] = tool_choice
        headers = {'Authorization': f'Bearer {self.settings.api_key}', 'Content-Type': 'application/json'}
        url = self.settings.base_url.rstrip('/') + '/chat/completions'

        started = time.monotonic()
        last_error = ''
        for attempt in range(self.retries):
            try:
                res = self.post(url, payload, headers, self.timeout)
                break
            except urllib.error.HTTPError as e:
                body = e.read()[:300].decode('utf-8', 'replace') if hasattr(e, 'read') else ''
                last_error = f'HTTP {e.code}: {body}'
                if e.code == 402:
                    self._record(task, model.id, started, ok=False, error='HTTP 402')
                    raise CreditsExhausted('Token Factory balance is exhausted (HTTP 402)') from None
                if e.code not in (408, 409, 429, 500, 502, 503, 504):
                    self._record(task, model.id, started, ok=False, error=f'HTTP {e.code}')
                    raise LLMError(last_error) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_error = f'{type(e).__name__}: {e}'
            time.sleep(min(8, 2 ** attempt))
        else:
            self._record(task, model.id, started, ok=False, error=last_error[:80])
            raise LLMError(f'Token Factory did not answer after {self.retries} attempts ({last_error})')

        choice = (res.get('choices') or [{}])[0]
        msg = choice.get('message') or {}
        usage = res.get('usage') or {}
        p_tok, c_tok = int(usage.get('prompt_tokens', 0)), int(usage.get('completion_tokens', 0))
        cost = p_tok / 1e6 * model.usd_in_per_m + c_tok / 1e6 * model.usd_out_per_m
        latency = int((time.monotonic() - started) * 1000)
        self.ledger.record(task=task, model=model.id, ok=True, prompt_tokens=p_tok, completion_tokens=c_tok,
                           cost_usd=round(cost, 8), latency_ms=latency, finish_reason=choice.get('finish_reason', ''))
        return LLMResult(content=(msg.get('content') or '').strip(), tool_calls=parse_tool_calls(msg.get('tool_calls')),
                         model=model.id, task=task, prompt_tokens=p_tok, completion_tokens=c_tok, cost_usd=cost,
                         latency_ms=latency, finish_reason=choice.get('finish_reason') or '',
                         reasoning=msg.get('reasoning_content') or '')

    def _record(self, task: str, model_id: str, started: float, ok: bool, error: str) -> None:
        self.ledger.record(task=task, model=model_id, ok=ok, prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                           latency_ms=int((time.monotonic() - started) * 1000), error=error)


def result_dict(r: LLMResult) -> dict:
    d = asdict(r)
    d.pop('reasoning', None)
    return d
