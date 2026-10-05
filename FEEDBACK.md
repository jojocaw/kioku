# Feedback on Nebius Token Factory and NVIDIA Nemotron

Notes from building Kioku (October 2026). Everything below was observed in this project; numbers come from
`usage.jsonl`, the ledger Kioku writes for every call.

## What worked well

- **Drop-in OpenAI-compatible API.** The whole client is ~170 lines of standard-library Python (`urllib`); chat,
  native tool calling (`tools` / `tool_choice`) and parallel tool calls worked on the first try.
- **Nemotron 3.5 Lightning is fast and cheap enough to run an agent's every turn.** A chat turn with a tool call is
  two calls, about 3,000–7,000 tokens, roughly $0.0002–0.0004 and 1.5–3 seconds. Thirteen weeks of a simulated
  owner's life — 104 messages plus a journal every night and a briefing every morning — cost $0.05 on Lightning
  (328 calls); the 25 weekly and monthly reviews on Super added $0.17, for $0.23 in total (353 calls, about 1 M tokens).
- **On our recall check Lightning matched Super.** 22 questions about the quarter: 21 correct on each, but Lightning
  cost about a sixth as much ($0.0075 vs $0.0433 for all 22) and answered faster (2.0 s vs 3.2 s on average), so
  everyday chat stays on Lightning.
- **Nemotron 3 Super writes good weekly reviews** when given room: it finds patterns across days and separates real
  decisions from plans better than a smaller model.
- **Zero data retention as an account setting** made the privacy story simple to explain.

## Friction we hit (with what we did about it)

1. **Lightning puts its thinking into `content` by default.** Without a switch the answer starts with a thinking
   process and often hits `max_tokens` before answering. `chat_template_kwargs: {"enable_thinking": false}` (or
   `reasoning_effort: "none"`) fixes it; system-prompt switches such as `/no_think` or "detailed thinking off" did
   not. *Suggestion:* say this on the model card and in the playground's code sample, or default to thinking off when
   `tools` are present.
2. **Super's reasoning counts against `max_tokens`, and when it runs out the answer is empty.** With
   `max_tokens: 3000`, 12 of 12 weekly-review calls (about 2,500 prompt tokens each) ended with
   `finish_reason: "length"` and no usable JSON; the reasoning had used the whole budget. At 8,000 they finished
   (3,800 and 6,400 completion tokens). *Suggestion:* a separate reasoning budget (`max_reasoning_tokens`), or a
   clear note that reasoning tokens count toward `max_tokens`. We now retry a cut-off answer with twice the room.
3. **Placeholders lose their brackets.** Kioku replaces private data with placeholders such as `[NAME_2]` before a
   call. Super sometimes wrote `NAME_2` without brackets in its reviews, which broke our restore step until we
   matched both forms. Worth knowing for anyone doing redaction around these models.
4. **"Saved!" without saving.** Lightning (thinking off) sometimes replies "Saved" or "I'll remind you next August"
   without calling the tool. We added an honesty check in code that compares the reply's claims with the tools that
   actually ran — saving a note or task is then done through the same guard rules, and "sent" / "paid" claims are
   corrected in front of the owner.
5. **Mental arithmetic.** Lightning (thinking off) answered "8% on 28 bags a month is about ¥1,904" — the right
   figure is ¥14,000. Because the journal then quoted the reply, the wrong number reached the monthly review and the
   index. We added a `calculate` tool and stopped journals from reading the assistant's own replies.
6. **Nemotron 3 Nano 30B cut tool arguments short** at around 200 tokens in our first test, so we did not use it for
   tool calls.
7. **No NVIDIA embedding model in the catalogue** (we saw only `Qwen/Qwen3-Embedding-8B`). Kioku uses keyword search
   (BM25) instead, which also keeps everything local.
8. **Spend control is on us.** We built a per-day token and dollar budget with a hard stop, and a shared daily cap for
   the public demo. *Suggestion:* a per-key spend limit in the console and a usage endpoint that a program can read,
   so apps can show remaining credit and stop before HTTP 402.
