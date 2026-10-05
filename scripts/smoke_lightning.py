# Check Lightning / Super tool calls and that the answer is never empty (the key is never printed)
import json, time, urllib.request, pathlib
env = dict(l.split('=', 1) for l in (pathlib.Path(__file__).resolve().parents[1] / '.env').read_text().splitlines() if '=' in l and not l.startswith('#'))
KEY = env['NEBIUS_API_KEY'].strip()
URL = 'https://api.tokenfactory.nebius.com/v1/chat/completions'
TOOLS = [{"type": "function", "function": {"name": "save_memory", "description": "Save a durable fact about the user to long-term memory.",
          "parameters": {"type": "object", "properties": {"topic": {"type": "string"}, "fact": {"type": "string"}}, "required": ["topic", "fact"]}}}]
def call(model, messages, tools=None, max_tokens=800):
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2}
    if tools: body["tools"] = tools
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=120) as r: res = json.load(r)
    return res, time.time() - t
SYS = "You are Kioku, a private personal secretary. When the user shares a durable fact, call save_memory exactly once."
for model in ["nvidia/Nemotron-3_5-Lightning", "nvidia/nemotron-3-super-120b-a12b"]:
    try:
        res, dt = call(model, [{"role": "system", "content": SYS}, {"role": "user", "content": "Please remember: my bakery opens at 7am on weekdays and 8am on Saturdays, closed Sundays."}], TOOLS)
        m = res["choices"][0]["message"]; tc = m.get("tool_calls") or []
        args = [c["function"]["arguments"] for c in tc]
        ok = all(json.loads(a) for a in args) if args else False
        print(f"[{model}] tool {dt:.1f}s calls={len(tc)} valid_json={ok} args={args[:1]} content={(m.get('content') or '')[:80]!r} usage={res.get('usage')}")
        res, dt = call(model, [{"role": "system", "content": "You are Kioku. Reply in 3 short bullet points."}, {"role": "user", "content": "Summarize my week: Mon new croissant recipe test; Wed flour supplier price up 8%; Fri record sales 412 items; Sat staff Aiko sick."}], max_tokens=600)
        m = res["choices"][0]["message"]
        print(f"[{model}] text {dt:.1f}s finish={res['choices'][0].get('finish_reason')} content_len={len(m.get('content') or '')} reasoning_len={len(m.get('reasoning_content') or '')} head={(m.get('content') or '')[:120]!r}")
    except Exception as e:
        print(f"[{model}] ERROR {type(e).__name__}: {str(e)[:200]}")
