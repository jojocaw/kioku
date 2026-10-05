# Try the switches that keep Lightning's thinking out of the answer (the key is never printed)
import json, time, urllib.request, urllib.error, pathlib
env = dict(l.split('=', 1) for l in (pathlib.Path(__file__).resolve().parents[1] / '.env').read_text().splitlines() if '=' in l and not l.startswith('#'))
KEY = env['NEBIUS_API_KEY'].strip()
URL = 'https://api.tokenfactory.nebius.com/v1/chat/completions'
USER = "Summarize my week: Mon new croissant recipe test; Wed flour supplier price up 8%; Fri record sales 412 items; Sat staff Aiko sick."
variants = {
  "kwargs enable_thinking=false": ({"chat_template_kwargs": {"enable_thinking": False}}, "You are Kioku. Reply in 3 short bullet points."),
  "reasoning_effort=none": ({"reasoning_effort": "none"}, "You are Kioku. Reply in 3 short bullet points."),
  "system /no_think": ({}, "/no_think\nYou are Kioku. Reply in 3 short bullet points."),
  "system detailed thinking off": ({}, "detailed thinking off\nYou are Kioku. Reply in 3 short bullet points."),
}
for name, (extra, sysmsg) in variants.items():
    body = {"model": "nvidia/Nemotron-3_5-Lightning", "messages": [{"role": "system", "content": sysmsg}, {"role": "user", "content": USER}], "max_tokens": 400, "temperature": 0.2, **extra}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r: res = json.load(r)
        m = res["choices"][0]["message"]; c = m.get("content") or ""
        print(f"[{name}] {time.time()-t:.1f}s finish={res['choices'][0].get('finish_reason')} len={len(c)} reasoning_len={len(m.get('reasoning_content') or '')} tokens={res['usage']['completion_tokens']} head={c[:110]!r}")
    except urllib.error.HTTPError as e:
        print(f"[{name}] HTTP {e.code}: {e.read()[:160]!r}")
