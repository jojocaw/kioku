"""Kioku's web app: chat, memory, guard receipts, approvals, usage and the daily rhythm on one page.

    python -m kioku.web --demo demo/seed     # public demo: each visitor gets a private copy of the fictional persona
    python -m kioku.web                      # your own Kioku on http://127.0.0.1:8700 (KIOKU_DATA_DIR)

Standard library only. Demo mode: no login; a cookie names the visitor's sandbox (a copy of the persona's data in a
temp folder, removed after two idle hours); per-sandbox rate limits and token budget; one shared daily budget for all
visitors, so the demo can never spend more than a set amount per day. The approval buttons are the owner's controls:
they are plain HTTP calls from the page, never something the model can reach.
"""
from __future__ import annotations

import argparse
import json
import secrets
import shutil
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from . import scheduler, skills
from .agent import Kioku
from .config import Settings
from .llm import CreditsExhausted, LLMError
from .memory import week_id

STATIC = Path(__file__).parent / 'static'
MAX_TEXT = 1000            # characters per chat message
CHATS_PER_HOUR = 30        # per visitor
JOBS_PER_HOUR = 6          # journal / briefing / review runs per visitor
SANDBOX_IDLE_S = 2 * 3600  # a sandbox nobody touched for this long is deleted
MAX_SANDBOXES = 300
NEW_PER_IP_HOUR = 12       # new sandboxes per network per hour (a reload keeps its sandbox through the cookie)


class TooMany(Exception):
    pass


class Meter:
    """What all visitors together spent today (UTC calendar day). The demo stops answering at the limit."""

    def __init__(self, limit_usd: float):
        self.limit, self.day, self.spent, self.lock = limit_usd, '', 0.0, threading.Lock()

    def _roll(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        if today != self.day:
            self.day, self.spent = today, 0.0

    def ok(self) -> bool:
        with self.lock:
            self._roll()
            return self.spent < self.limit

    def add(self, usd: float) -> None:
        with self.lock:
            self._roll()
            self.spent += usd


class Space:
    """One Kioku (an owner's, or a visitor's sandbox) with its own lock and rate limits."""

    def __init__(self, kioku: Kioku, folder: Path | None = None):
        self.k, self.folder = kioku, folder
        self.lock = threading.Lock()
        self.touched = time.time()
        self.chats: deque = deque()
        self.jobs: deque = deque()

    def allow(self, kind: str, per_hour: int) -> bool:
        q = self.chats if kind == 'chat' else self.jobs
        now = time.time()
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= per_hour:
            return False
        q.append(now)
        return True

    def left(self, kind: str, per_hour: int) -> int:
        q = self.chats if kind == 'chat' else self.jobs
        return max(0, per_hour - sum(1 for t in q if time.time() - t <= 3600))


class App:
    def __init__(self, settings: Settings, seed: Path | None, daily_usd: float, sandbox_root: Path | None = None):
        self.settings, self.seed = settings, seed
        self.meter = Meter(daily_usd)
        self.spaces: dict[str, Space] = {}
        self.lock = threading.Lock()
        self.persona = json.loads((seed / 'persona.json').read_text(encoding='utf-8')) if seed else {}
        # The recall check's results for this memory (demo/eval_recall.py --publish), shown on the Models & cost tab.
        recall = seed / 'recall.json' if seed else None
        self.recall = json.loads(recall.read_text(encoding='utf-8')) if recall and recall.exists() else None
        if seed:
            self.root = Path(sandbox_root or tempfile.mkdtemp(prefix='kioku-sandboxes-'))
            self.new_by_ip: dict[str, deque] = {}
            # Looking costs nothing: until a visitor changes something, pages read the seed itself (never written to).
            self.viewer = self._space_over(seed, writable=False)
            threading.Thread(target=self._sweeper, daemon=True).start()
        else:
            self.owner = Space(Kioku(settings))
            threading.Thread(target=self._rhythm, daemon=True).start()

    def _rhythm(self) -> None:
        """Owner mode: run the habits on time (journal, briefing, reviews) while the server is up; catches up after a pause."""
        while True:
            try:
                with self.owner.lock:
                    scheduler.run_due(self.owner.k)
            except (LLMError, ValueError) as e:
                sys.stderr.write(f'rhythm: {type(e).__name__}: {e}\n')
            time.sleep(60)

    # ---------------------------------------------------------------- sandboxes
    def space(self, sid: str | None, create: bool = False, ip: str = '') -> tuple[Space, str]:
        """The visitor's sandbox. Without one, reading is served from the seed; the first change makes a private copy."""
        if not self.seed:
            return self.owner, ''
        with self.lock:
            if sid and sid in self.spaces:
                s = self.spaces[sid]
                s.touched = time.time()
                return s, sid
            if not create:
                return self.viewer, ''
            q = self.new_by_ip.setdefault(ip, deque())
            while q and time.time() - q[0] > 3600:
                q.popleft()
            if len(q) >= NEW_PER_IP_HOUR:
                raise TooMany()
            q.append(time.time())
            if len(self.spaces) >= MAX_SANDBOXES:
                self._drop(min(self.spaces, key=lambda k: self.spaces[k].touched))
            sid = secrets.token_urlsafe(18)
            self.spaces[sid] = self._new_sandbox(sid)
            return self.spaces[sid], sid

    def _new_sandbox(self, sid: str) -> Space:
        folder = self.root / sid
        shutil.copytree(self.seed, folder, ignore=shutil.ignore_patterns('seed_log.jsonl', 'seed_state.json', 'persona.json', 'recall.json'))
        return self._space_over(folder, writable=True)

    def _space_over(self, folder: Path, writable: bool) -> Space:
        p = self.persona
        tz = ZoneInfo(p.get('timezone', 'UTC'))
        start, born = datetime.fromisoformat(p['now']).replace(tzinfo=tz), time.time()
        settings = replace(self.settings, data_dir=folder, timezone=p.get('timezone', 'UTC'),
                           private_terms=tuple(p.get('private_terms', ())), max_payment=float(p.get('max_payment', 200)))
        if writable:
            clock = lambda: start + timedelta(seconds=time.time() - born)  # noqa: E731  the persona's day moves with real time
        else:
            clock = lambda: start  # noqa: E731
        return Space(Kioku(settings, clock=clock), folder if writable else None)

    def _drop(self, sid: str) -> None:
        s = self.spaces.pop(sid, None)
        if s and s.folder:
            shutil.rmtree(s.folder, ignore_errors=True)

    def reset(self, sid: str) -> None:
        with self.lock:
            self._drop(sid)

    def _sweeper(self) -> None:
        while True:
            time.sleep(300)
            with self.lock:
                for sid in [k for k, s in self.spaces.items() if time.time() - s.touched > SANDBOX_IDLE_S]:
                    self._drop(sid)

    # ---------------------------------------------------------------- what the page asks for
    def state(self, s: Space) -> dict:
        k, m = s.k, s.k.memory
        rows = k.audit.rows()
        return dict(
            mode='demo' if self.seed else 'owner',
            persona={key: self.persona.get(key) for key in ('name', 'business', 'started', 'about')} if self.seed else {},
            suggestions=self.persona.get('suggestions', []),
            now=k.clock().isoformat(timespec='minutes'),
            counts=dict(days=len(m.files('daily')), weekly=len(m.files('weekly')), monthly=len(m.files('monthly')),
                        topics=len(m.files('topics')), receipts=len(rows),
                        blocked=sum(r['decision'] == 'block' for r in rows),
                        redacted=sum(r['rule'] == 'privacy.redact' for r in rows),
                        asked=sum(r['decision'] == 'ask' for r in rows),
                        pending=len(k.approvals.list())),
            limits=dict(chats_left=s.left('chat', CHATS_PER_HOUR), jobs_left=s.left('job', JOBS_PER_HOUR)),
        )

    def chat(self, s: Space, text: str) -> tuple[int, dict]:
        text = (text or '').strip()
        if not text:
            return 400, dict(error='Empty message.')
        if len(text) > MAX_TEXT:
            return 400, dict(error=f'Please keep a message under {MAX_TEXT} characters.')
        if self.seed and not s.allow('chat', CHATS_PER_HOUR):
            return 429, dict(error=f'Demo limit: {CHATS_PER_HOUR} messages an hour per visitor. Please come back a little later.')
        if self.seed and not self.meter.ok():
            return 503, dict(error="The demo's shared budget for today is used up. It resets at 00:00 UTC.")
        with s.lock:
            try:
                r = s.k.chat(text)
            except CreditsExhausted:
                return 503, dict(error='The model credits for this demo are used up.')
            except LLMError:
                return 502, dict(error='The model did not answer. Please try again.')
        self.meter.add(sum(c['cost_usd'] for c in r.calls))
        return 200, dict(text=r.text, seen=r.seen, blocked=r.blocked, calls=r.calls,
                         events=[dict(tool=e.tool, decision=e.decision, rule=e.rule, detail=e.detail, receipt=e.receipt)
                                 for e in r.events])

    def run(self, s: Space, job: str) -> tuple[int, dict]:
        if job not in ('briefing', 'journal', 'weekly'):
            return 400, dict(error='Unknown job.')
        if self.seed and not s.allow('job', JOBS_PER_HOUR):
            return 429, dict(error=f'Demo limit: {JOBS_PER_HOUR} runs an hour per visitor.')
        if self.seed and not self.meter.ok():
            return 503, dict(error="The demo's shared budget for today is used up. It resets at 00:00 UTC.")
        k = s.k
        today = k.clock().date()
        before = sum(r.get('cost_usd', 0) for r in k.ledger.rows())
        with s.lock:
            try:
                if job == 'briefing':
                    skills.morning_briefing(k, today)
                    path = k.memory.daily(today)
                elif job == 'journal':
                    path = skills.daily_journal(k, today)
                else:
                    path = skills.weekly_review(k, week_id(today - timedelta(days=today.isoweekday())))  # last full week
            except CreditsExhausted:
                return 503, dict(error='The model credits for this demo are used up.')
            except (LLMError, ValueError):
                return 502, dict(error='The model did not answer properly. Please try again.')
        self.meter.add(sum(r.get('cost_usd', 0) for r in k.ledger.rows()) - before)
        if not path:
            return 200, dict(path=None, text='', note='Nothing to write yet — talk to Kioku first.')
        return 200, dict(path=path, text=k.memory.read(path))

    @staticmethod
    def memory_tree(s: Space) -> dict:
        m = s.k.memory
        top = [p for p in ('index.md', 'profile.md', 'tasks.md') if m.exists(p)]
        return dict(groups=[('Index', top), ('Topics', m.files('topics')), ('Monthly', m.files('monthly')[::-1]),
                            ('Weekly', m.files('weekly')[::-1]), ('Daily', m.files('daily')[::-1])])

    def usage(self, s: Space) -> dict:
        rows = s.k.ledger.rows()
        return dict(rows=s.k.ledger.summary(), recall=self.recall, totals=dict(
            calls=len(rows), tokens=sum(r.get('prompt_tokens', 0) + r.get('completion_tokens', 0) for r in rows),
            cost_usd=round(sum(r.get('cost_usd', 0) for r in rows), 5)))


# -------------------------------------------------------------------- HTTP

def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'Kioku'
        sys_version = ''

        def log_message(self, fmt, *args):  # request lines only — never message content
            sys.stderr.write(f'{self.log_date_time_string()} {fmt % args}\n')

        # ------------------------------------------------ plumbing
        def _sid(self) -> str | None:
            for part in (self.headers.get('Cookie') or '').split(';'):
                name, _, value = part.strip().partition('=')
                if name == 'kioku_sid':
                    return value
            return None

        def _ip(self) -> str:
            forwarded = (self.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
            return forwarded or self.client_address[0]

        def _send(self, code: int, body: bytes, ctype: str, sid: str = '') -> None:
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy',
                             "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                             "font-src https://fonts.gstatic.com; img-src 'self' data:; frame-ancestors 'none'")
            if sid:
                secure = '; Secure' if self.headers.get('X-Forwarded-Proto') == 'https' else ''
                self.send_header('Set-Cookie', f'kioku_sid={sid}; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400{secure}')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def _json(self, code: int, data: dict, sid: str = '') -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8', sid)

        def _body(self) -> dict | None:
            if 'application/json' not in (self.headers.get('Content-Type') or ''):
                return None  # also stops plain cross-site form posts
            n = int(self.headers.get('Content-Length') or 0)
            if n > 16_000:
                return None
            try:
                data = json.loads(self.rfile.read(n) or b'{}')
            except json.JSONDecodeError:
                return None
            return data if isinstance(data, dict) else None

        # ------------------------------------------------ routes
        def do_GET(self):
            url = urlparse(self.path)
            if url.path == '/healthz':
                return self._send(200, b'ok', 'text/plain')
            if url.path in ('/', '/index.html'):
                return self._send(200, (STATIC / 'index.html').read_bytes(), 'text/html; charset=utf-8')
            if url.path.startswith('/static/'):
                f = (STATIC / url.path[len('/static/'):]).resolve()
                if STATIC.resolve() in f.parents and f.is_file():
                    ctype = {'.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml',
                             '.png': 'image/png'}.get(f.suffix, 'application/octet-stream')
                    return self._send(200, f.read_bytes(), ctype)
                return self._json(404, dict(error='Not found.'))
            if not url.path.startswith('/api/'):
                return self._json(404, dict(error='Not found.'))
            s, _ = app.space(self._sid())
            new_sid = ''
            q = parse_qs(url.query)
            k = s.k
            if url.path == '/api/state':
                return self._json(200, app.state(s), new_sid)
            if url.path == '/api/memory':
                return self._json(200, app.memory_tree(s), new_sid)
            if url.path == '/api/note':
                try:
                    path = q.get('path', [''])[0]
                    return self._json(200, dict(path=path, text=k.memory.read(path)), new_sid)
                except ValueError:
                    return self._json(400, dict(error='That note is outside the memory folder.'), new_sid)
            if url.path == '/api/audit':
                rows = k.audit.rows()[::-1][:400]
                return self._json(200, dict(rows=rows, summary=k.audit.summary()), new_sid)
            if url.path == '/api/approvals':
                return self._json(200, dict(items=k.approvals.list(None)[::-1]), new_sid)
            if url.path == '/api/usage':
                return self._json(200, app.usage(s), new_sid)
            if url.path == '/api/history':  # today's conversation, so a reload does not look like a reset
                turns = [t for t in k.memory.log_for(k.clock().date()) if t.get('role') in ('owner', 'kioku')]
                return self._json(200, dict(turns=turns[-40:]), new_sid)
            return self._json(404, dict(error='Not found.'), new_sid)

        def do_HEAD(self):  # uptime checks and link previews: the same headers as GET, no body
            self.do_GET()

        def do_POST(self):
            url = urlparse(self.path)
            body = self._body()
            if body is None:
                return self._json(400, dict(error='Send JSON.'))
            old = self._sid()
            if url.path == '/api/reset' and app.seed:
                if old:
                    app.reset(old)
                return self._json(200, dict(ok=True))
            try:
                s, sid = app.space(old, create=True, ip=self._ip())
            except TooMany:
                return self._json(429, dict(error='Too many new demo sessions from your network. Please try again later.'))
            new_sid = sid if sid != old else ''
            if url.path == '/api/chat':
                code, data = app.chat(s, str(body.get('text', '')))
                return self._json(code, data, new_sid)
            if url.path == '/api/run':
                code, data = app.run(s, str(body.get('job', '')))
                return self._json(code, data, new_sid)
            if url.path == '/api/approvals/decide':  # the owner's control — a button on the page, not a tool
                try:
                    with s.lock:
                        result = s.k.decide(str(body.get('id', '')), bool(body.get('approve')))
                    return self._json(200, dict(result=result), new_sid)
                except (KeyError, ValueError) as e:
                    return self._json(400, dict(error=str(e)), new_sid)
            return self._json(404, dict(error='Not found.'), new_sid)

    return Handler


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog='python -m kioku.web')
    ap.add_argument('--demo', metavar='SEED', help='serve visitor sandboxes copied from this seed folder')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8700)
    ap.add_argument('--daily-usd', type=float, default=1.0, help='demo: what all visitors may spend per day, in total')
    a = ap.parse_args(argv)
    seed = Path(a.demo).resolve() if a.demo else None
    overrides = dict(daily_token_budget=150_000, daily_usd_budget=0.05) if seed else {}
    settings = Settings.from_env(**overrides)
    if not settings.api_key:
        sys.exit('NEBIUS_API_KEY is not set (.env)')
    app = App(settings, seed, a.daily_usd)
    server = ThreadingHTTPServer((a.host, a.port), make_handler(app))
    print(f'Kioku on http://{a.host}:{a.port}  ({"demo: " + str(seed) if seed else "owner: " + str(settings.data_dir)})',
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
