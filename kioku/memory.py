"""The owner's memory: plain Markdown files they can read, edit, export or delete (it opens as an Obsidian vault).

    memory/
      index.md                 what Kioku reads first: who the owner is, active topics, key facts (kept short)
      daily/2026-10-05.md      one note per day (journal + notes saved during the day)
      weekly/2026-W40.md       weekly review
      monthly/2026-10.md       monthly review
      topics/flour-prices.md   one note per topic: current state, history, decisions, what didn't work
      log/2026-10-05.jsonl     the day's raw conversation (input for the nightly journal)
      tasks.md                 open tasks

Search is keyword search (BM25) over headings and paragraphs — no embeddings, so nothing leaves the machine
to build an index, and every hit points at a file the owner can open.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def slugify(name: str) -> str:
    s = unicodedata.normalize('NFKC', name).strip().lower()
    s = re.sub(r'[^\w぀-ヿ㐀-鿿]+', '-', s).strip('-')
    return s[:60] or 'topic'


CATCH_ALL = {'daily-notes', 'daily-note', 'notes', 'note', 'daily', 'general', 'misc', 'other', 'journal', 'log', 'today',
             'memory', 'reminders', 'reminder', 'updates'}


def real_topic(name) -> str | None:
    """A topic name worth a note of its own, or None for catch-alls such as "daily notes"."""
    name = str(name or '').strip()
    if not name or slugify(name) in CATCH_ALL or len(slugify(name).split('-')) > 5:  # a sentence is not a topic
        return None
    return name


def topic_words(name: str) -> set[str]:
    """Words of a topic name, singular, for matching "lemon buns" to "lemon-cream-bun"."""
    words = re.findall(r'[a-z0-9]+|[぀-ヿ㐀-鿿]+', unicodedata.normalize('NFKC', name).lower().replace('-', ' '))
    out = set()
    for w in words:
        if w in STOP:
            continue
        if len(w) > 4 and w.endswith('es') and not w.endswith('ses'):
            w = w[:-2]
        elif len(w) > 3 and w.endswith('s') and not w.endswith('ss'):
            w = w[:-1]
        out.add(w)
    return out


def week_id(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f'{y}-W{w:02d}'


def week_days(wid: str) -> list[date]:
    y, w = wid.split('-W')
    monday = date.fromisocalendar(int(y), int(w), 1)
    return [monday + timedelta(days=i) for i in range(7)]


STOP = set('a an and are as at be but by for from had has have i in is it its me my of on or our so that the this '
           'to was we were will with you your us they them their there then than what which who whom whose when where '
           'why how did do does done much many again also just any some about into can could would should shall may '
           'might t s d m ll re ve don didn doesn isn wasn aren weren won wouldn couldn shouldn haven hasn hadn'.split())
# A few words people use for the same thing in questions and in notes ("best day" / "a new record").
SYNONYMS = {'best': ['record'], 'record': ['best'], 'cost': ['price', 'paid', 'charge'], 'price': ['cost', 'charge'],
            'paid': ['pay', 'cost'], 'pay': ['paid', 'cost'], 'decide': ['decision', 'decided'], 'decision': ['decide', 'decided'],
            'start': ['began', 'begin'], 'quote': ['quotes', 'quoted'], 'salary': ['wage'], 'wage': ['salary']}
EMPTY = {'- (none)', '(none)', '- (not reviewed yet)', '- (none yet)'}


def stem(w: str) -> str:
    """Light English stemming so "quotes"/"quote", "prices"/"price", "ordered"/"order" meet."""
    if len(w) > 4 and w.endswith('ies'):
        return w[:-3] + 'y'
    if len(w) > 4 and w.endswith('es') and w[:-2].endswith(('s', 'x', 'z', 'ch', 'sh')):
        return w[:-2]
    if len(w) > 3 and w.endswith('s') and not w.endswith('ss'):
        return w[:-1]
    if len(w) > 5 and w.endswith('ed'):
        return w[:-1] if w[:-2].endswith(('at', 'ic', 'iz', 'ur', 'ov', 'rc')) else w[:-2]  # priced→price, ordered→order
    if len(w) > 6 and w.endswith('ing'):
        return w[:-3]
    return w


def tokens(text: str) -> list[str]:
    """English words (lightly stemmed) plus character bigrams for Japanese/Chinese text."""
    text = unicodedata.normalize('NFKC', text).lower()
    words = [stem(w) for w in re.findall(r'[a-z0-9]+', text) if w not in STOP]
    cjk = re.findall(r'[぀-ヿ㐀-鿿]+', text)
    return words + [s[i:i + 2] for s in cjk for i in range(max(1, len(s) - 1))]


@dataclass
class Hit:
    path: str
    heading: str
    text: str
    score: float


class Memory:
    def __init__(self, root: Path):
        self.root = Path(root)
        for sub in ('daily', 'weekly', 'monthly', 'topics', 'log'):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # ---------- paths
    @staticmethod
    def daily(d: date) -> str:
        return f'daily/{d.isoformat()}.md'

    @staticmethod
    def weekly(wid: str) -> str:
        return f'weekly/{wid}.md'

    @staticmethod
    def monthly(y: int, m: int) -> str:
        return f'monthly/{y}-{m:02d}.md'

    @staticmethod
    def topic(name: str) -> str:
        return f'topics/{slugify(name)}.md'

    # ---------- files
    def _safe(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents and p != self.root.resolve():
            raise ValueError(f'outside the memory folder: {rel}')
        return p

    def read(self, rel: str) -> str:
        p = self._safe(rel)
        return p.read_text(encoding='utf-8') if p.is_file() else ''

    def write(self, rel: str, text: str) -> None:
        p = self._safe(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + '.tmp')
        tmp.write_text(text.rstrip() + '\n', encoding='utf-8')
        tmp.replace(p)

    def exists(self, rel: str) -> bool:
        return self._safe(rel).is_file()

    def files(self, sub: str = '') -> list[str]:
        base = self.root / sub
        return sorted(str(p.relative_to(self.root)) for p in base.rglob('*.md'))

    # ---------- conversation log
    def append_log(self, role: str, text: str, when: datetime) -> None:
        p = self.root / 'log' / f'{when.date().isoformat()}.jsonl'
        with p.open('a', encoding='utf-8') as f:
            f.write(json.dumps({'ts': when.isoformat(timespec='minutes'), 'role': role, 'text': text}, ensure_ascii=False) + '\n')

    def log_for(self, d: date) -> list[dict]:
        p = self.root / 'log' / f'{d.isoformat()}.jsonl'
        if not p.is_file():
            return []
        return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s.strip()]

    # ---------- notes written during the day
    def save_note(self, text: str, when: datetime, topic: str | None = None) -> str:
        """Append a dated note to today's daily file (and to the topic's history). Returns the path written."""
        rel = self.daily(when.date())
        topic = real_topic(topic)
        topic = self.resolve_topic(topic) if topic else None
        body = self.read(rel) or f'# {when.date().isoformat()}\n'
        line = f'- {when.strftime("%H:%M")} {text.strip()}' + (f' [[{slugify(topic)}]]' if topic else '')
        body = add_to_section(body, 'Notes', line)
        self.write(rel, body)
        if topic:
            self.add_topic_history(topic, when.date(), text.strip())
        return rel

    def add_topic_history(self, name: str, d: date, line: str) -> None:
        rel = self.topic(name)
        body = self.read(rel) or new_topic(name)
        self.write(rel, add_to_section(body, 'History', f'- [[{d.isoformat()}]] {line}'))

    def topics(self) -> list[str]:
        return [Path(p).stem for p in self.files('topics')]

    def merge_topics(self, into: str, sources: list[str]) -> list[str]:
        """Fold topic notes that cover the same subject into one. History, decisions and what didn't work are kept
        (in date order, without duplicates); links everywhere in the memory are pointed at the merged note.
        Returns the slugs that were merged away."""
        into = slugify(into)
        existing = set(self.topics())
        sources = [slugify(s) for s in sources if slugify(s) in existing and slugify(s) != into]
        if into not in existing or not sources:
            return []
        body = self.read(self.topic(into))
        for src in sources:
            other = self.read(self.topic(src))
            for name in ('History', 'Decisions', "What didn't work"):
                lines = section(body, name).splitlines() + section(other, name).splitlines()
                seen, merged = set(), []
                for line in sorted((s for s in lines if s.strip()), key=lambda s: (re.findall(r'\d{4}-\d{2}-\d{2}', s) or ['9'])[0]):
                    if line not in seen:
                        seen.add(line)
                        merged.append(line)
                body = set_section(body, name, '\n'.join(merged))
            self._safe(self.topic(src)).unlink()
        self.write(self.topic(into), body)
        pattern = re.compile(r'\[\[(' + '|'.join(map(re.escape, sources)) + r')\]\]')
        for rel in self.files():
            text = self.read(rel)
            if pattern.search(text):
                self.write(rel, pattern.sub(f'[[{into}]]', text))
        return sources

    def resolve_topic(self, name: str) -> str:
        """The existing topic a name means — same slug, or at least half the same words — else the name itself.
        Keeps "croissants", "croissant sales" and "Croissant-sales" in one note instead of three."""
        if slugify(name) in self.topics():
            return name
        words = topic_words(name)
        best, score = None, 0.0
        for slug in self.topics():
            other = topic_words(slug)
            j = len(words & other) / len(words | other) if words and other else 0.0
            if j > score:
                best, score = slug, j
        return best if best and score >= 0.5 else name

    # ---------- tasks
    def add_task(self, text: str, due: str | None = None) -> None:
        body = self.read('tasks.md') or '# Tasks\n'
        self.write('tasks.md', body.rstrip() + f'\n- [ ] {text.strip()}' + (f' (due {due})' if due else ''))

    def open_tasks(self) -> list[str]:
        return [s[6:] for s in self.read('tasks.md').splitlines() if s.startswith('- [ ] ')]

    def complete_task(self, text: str, when: datetime) -> str | None:
        """Tick the open task that best matches the words given (at least half of them). Returns it, or None."""
        lines = self.read('tasks.md').splitlines()
        want = set(tokens(text))
        best, score = None, 0.0
        for i, line in enumerate(lines):
            if line.startswith('- [ ] ') and want:
                have = set(tokens(line[6:]))
                s = len(want & have) / len(want) if have else 0.0
                if s > score:
                    best, score = i, s
        if best is None or score < 0.5:
            return None
        task = lines[best][6:]
        lines[best] = f'- [x] {task} (done {when.date().isoformat()})'
        self.write('tasks.md', '\n'.join(lines))
        return task

    # ---------- recall
    def chunks(self) -> list[tuple[str, str, str]]:
        """(path, heading, text) for every heading/paragraph block, the unit that search returns."""
        out = []
        for rel in self.files():
            heading = ''
            block: list[str] = []

            def flush():
                text = '\n'.join(block).strip()
                if text and text not in EMPTY:  # "What didn't work: (none)" is not an answer to anything
                    out.append((rel, heading, text[:900]))
                block.clear()

            for line in self.read(rel).splitlines():
                if line.startswith('#'):
                    flush()
                    heading = line.lstrip('#').strip()
                elif not line.strip():
                    flush()
                else:
                    block.append(line)
            flush()
        return out

    def search(self, query: str, k: int = 5, exclude: tuple[str, ...] = (), per_note: int = 1) -> list[Hit]:
        """Best-matching blocks, at most `per_note` from any one note, so one busy day cannot crowd out the answer."""
        words = tokens(query)
        q = Counter(words + [stem(s) for w in words for s in SYNONYMS.get(w, [])])
        if not q:
            return []
        docs = [(p, h, t, Counter(tokens(f'{h} {t} {Path(p).stem}'))) for p, h, t in self.chunks() if p not in exclude]
        if not docs:
            return []
        n = len(docs)
        avg = sum(sum(d[3].values()) for d in docs) / n
        df = Counter(term for d in docs for term in set(d[3]))
        hits = []
        for p, h, t, tf in docs:
            length = sum(tf.values())
            score = 0.0
            for term in q:
                if term in tf:
                    idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                    score += idf * tf[term] * 2.2 / (tf[term] + 1.2 * (0.25 + 0.75 * length / avg))
            if score > 0:
                hits.append(Hit(p, h, t, round(score, 3)))
        out, seen = [], Counter()
        for hit in sorted(hits, key=lambda x: -x.score):
            if seen[hit.path] < per_note:
                seen[hit.path] += 1
                out.append(hit)
                if len(out) == k:
                    break
        return out

    def on_this_day(self, d: date) -> list[tuple[str, str]]:
        """The same day last week, last month and last year — what the owner was doing then."""
        out = []
        for label, past in (('a week ago', d - timedelta(days=7)), ('a month ago', shift_month(d, -1)),
                            ('a year ago', shift_month(d, -12))):
            body = self.read(self.daily(past))
            if body:
                out.append((f'{label} ({past.isoformat()})', section(body, 'Key points') or body[:400]))
        return out


def shift_month(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    return date(y, m, 28)


# ---------- small Markdown helpers (sections are "## Name" blocks)

def section(body: str, name: str) -> str:
    m = re.search(rf'^## {re.escape(name)}\n(.*?)(?=^## |\Z)', body, re.M | re.S)
    return m.group(1).strip() if m else ''


def set_section(body: str, name: str, content: str) -> str:
    block = f'## {name}\n{content.strip()}\n\n'
    if re.search(rf'^## {re.escape(name)}\n', body, re.M):
        return re.sub(rf'^## {re.escape(name)}\n.*?(?=^## |\Z)', lambda m: block, body, count=1, flags=re.M | re.S)
    return body.rstrip() + '\n\n' + block


def add_to_section(body: str, name: str, line: str) -> str:
    current = section(body, name)
    return set_section(body, name, (current + '\n' + line).strip())


def new_topic(name: str) -> str:
    title = name.strip()
    if ' ' not in title and '-' in title:  # the model sometimes passes a slug ("flour-supplier")
        title = title.replace('-', ' ')
    title = title[:1].upper() + title[1:]
    return (f'# {title}\n\n## Current state\n- (not reviewed yet)\n\n## History\n\n## Decisions\n\n## What didn\'t work\n')


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
