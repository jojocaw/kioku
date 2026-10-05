import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import replace
from http.server import ThreadingHTTPServer

import pytest

from conftest import FakeLLM
from kioku import web
from kioku.config import Settings


@pytest.fixture
def demo(tmp_path):
    """A demo server over a tiny fictional seed; every sandbox gets a scripted model."""
    seed = tmp_path / 'seed'
    (seed / 'memory' / 'daily').mkdir(parents=True)
    (seed / 'memory' / 'index.md').write_text('# Index\n\n## About the owner\n- Runs a bakery\n')
    (seed / 'memory' / 'daily' / '2026-10-04.md').write_text('# 2026-10-04\n\n## Key points\n- 412 croissants\n')
    (seed / 'persona.json').write_text(json.dumps(dict(
        name='Rin', business='Komorebi Bakery', started='2026-07-06', now='2026-10-05T09:00', timezone='Asia/Tokyo',
        private_terms=['Aiko'], max_payment=100000, suggestions=[])))
    settings = Settings(api_key='test-key', data_dir=tmp_path / 'unused')
    app = web.App(settings, seed, daily_usd=1.0, sandbox_root=tmp_path / 'sandboxes')
    scripted = []
    original = app._new_sandbox

    def sandbox_with_fake_model(sid):
        space = original(sid)
        space.k.llm = FakeLLM(*scripted)
        return space

    app._new_sandbox = sandbox_with_fake_model
    server = ThreadingHTTPServer(('127.0.0.1', 0), web.make_handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app, f'http://127.0.0.1:{server.server_address[1]}', scripted
    server.shutdown()


def call(base, path, body=None, cookie=None, ctype='application/json'):
    data = None if body is None else (json.dumps(body).encode() if ctype == 'application/json' else body.encode())
    req = urllib.request.Request(base + path, data=data, method='POST' if data is not None else 'GET')
    if data is not None:
        req.add_header('Content-Type', ctype)
    if cookie:
        req.add_header('Cookie', cookie)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b'{}'), r.headers.get('Set-Cookie')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}'), None


def first_chat(base, scripted, reply='Hello Rin.'):
    """The first change a visitor makes creates their sandbox; returns the cookie."""
    scripted.append(reply)
    code, r, set_cookie = call(base, '/api/chat', {'text': 'Ask Aiko about Sunday'})
    assert code == 200 and set_cookie and 'HttpOnly' in set_cookie and 'SameSite=Lax' in set_cookie
    return set_cookie.split(';')[0], r


def test_looking_is_free_and_the_first_change_makes_a_private_copy(demo):
    app, base, scripted = demo
    code, state, set_cookie = call(base, '/api/state')
    assert code == 200 and state['mode'] == 'demo' and set_cookie is None and app.spaces == {}   # no copy yet
    assert call(base, '/api/note?path=daily/2026-10-04.md')[1]['text'].startswith('# 2026-10-04')
    cookie, r = first_chat(base, scripted)
    assert r['text'] == 'Hello Rin.' and r['seen'] == 'Ask [NAME_1] about Sunday'
    _, mine, _ = call(base, '/api/state', cookie=cookie)
    _, other, _ = call(base, '/api/state')
    assert mine['counts']['receipts'] > 0 and other['counts']['receipts'] == 0 and len(app.spaces) == 1
    assert mine['now'].startswith('2026-10-05T09:0')
    assert not (app.seed / 'audit.jsonl').exists()                           # the seed itself was never written


def test_the_server_refuses_what_it_should(demo):
    app, base, scripted = demo
    assert call(base, '/api/note?path=../persona.json')[0] == 400
    assert call(base, '/static/../web.py')[0] == 404
    assert call(base, '/api/chat', 'text=hi', ctype='application/x-www-form-urlencoded')[0] == 400
    cookie, _ = first_chat(base, scripted)
    assert call(base, '/api/chat', {'text': 'x' * (web.MAX_TEXT + 1)}, cookie)[0] == 400
    assert call(base, '/api/approvals/decide', {'id': 'A-999', 'approve': True}, cookie)[0] == 400
    assert call(base, '/api/run', {'job': 'rm -rf'}, cookie)[0] == 400


def test_the_recall_check_is_shown_but_not_copied_into_sandboxes(tmp_path, demo):
    app, base, scripted = demo
    assert call(base, '/api/usage')[1]['recall'] is None  # no results published for this seed
    app.recall = {'rows': [{'model': 'nvidia/Nemotron-3_5-Lightning', 'questions': 22, 'correct': 21,
                            'cost_usd': 0.0075, 'avg_latency_s': 2.0}]}
    assert call(base, '/api/usage')[1]['recall']['rows'][0]['correct'] == 21
    (app.seed / 'recall.json').write_text('{}')
    cookie, _ = first_chat(base, scripted)
    folder = app.spaces[cookie.split('=', 1)[1]].folder
    assert not (folder / 'recall.json').exists() and not (folder / 'persona.json').exists()


def test_limits(demo):
    app, base, scripted = demo
    cookie, _ = first_chat(base, scripted)
    space = next(iter(app.spaces.values()))
    space.chats.extend([time.time()] * web.CHATS_PER_HOUR)
    code, r, _ = call(base, '/api/chat', {'text': 'hi'}, cookie)
    assert code == 429 and 'an hour' in r['error']
    space.chats.clear()
    app.meter.add(5.0)                                                       # everyone's budget for today is gone
    code, r, _ = call(base, '/api/chat', {'text': 'hi'}, cookie)
    assert code == 503 and 'budget' in r['error']
    app.new_by_ip['127.0.0.1'].extend([time.time()] * web.NEW_PER_IP_HOUR)   # one network opening many sessions
    code, r, _ = call(base, '/api/chat', {'text': 'hi'})
    assert code == 429 and 'network' in r['error']


def test_approvals_are_decided_by_the_page_not_the_model(demo):
    app, base, scripted = demo
    cookie, _ = first_chat(base, scripted)
    space = next(iter(app.spaces.values()))
    space.k.llm.responses += [('', [('make_payment', {'what': 'flour', 'amount': 6664, 'currency': 'JPY', 'payee': 'Mill Co'})]),
                              'Queued for your approval.']
    code, r, _ = call(base, '/api/chat', {'text': 'Pay Mill Co for the flour'}, cookie)
    assert [e['decision'] for e in r['events']] == ['ask']
    _, a, _ = call(base, '/api/approvals', cookie=cookie)
    assert a['items'][0]['status'] == 'pending'
    code, d, _ = call(base, '/api/approvals/decide', {'id': a['items'][0]['id'], 'approve': True}, cookie)
    assert code == 200 and 'carried out' in d['result']
    assert (space.folder / 'payments.jsonl').exists()
