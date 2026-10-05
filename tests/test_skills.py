import json
from datetime import date, datetime

import pytest

from conftest import TZ
from kioku import scheduler, skills
from kioku.memory import section

JOURNAL = json.dumps({
    'key_points': ['Flour price went up 8%', 'Record Saturday planned'],
    'what_happened': ['09:30 Talked about the flour price'],
    'owner_words': ['We keep the croissant at 380 yen'],
    'insights': ['Mill Co raises prices every autumn'],
    'open_questions': ['Switch mills?'],
    'tomorrow': ['Ask two mills for quotes'],
    'topics': [{'topic': 'Flour prices', 'update': 'Mill Co +8% from October'}],
    'mood': 'worried',
})


def chat_day(k, text='Mill Co raised flour 8%. We keep the croissant at 380 yen.'):
    now = k.clock()
    k.memory.append_log('owner', text, now)
    k.memory.save_note('Mill Co invoice due 10/31', now, topic='Flour prices')


def test_daily_journal_keeps_notes_and_feeds_topics(make_kioku):
    k, fake = make_kioku(JOURNAL, JOURNAL)
    chat_day(k)
    rel = skills.daily_journal(k, date(2026, 10, 5))
    body = k.memory.read(rel)
    assert section(body, 'Key points').startswith('- Flour price went up 8%')
    assert '> We keep the croissant at 380 yen' in body
    assert 'Mill Co invoice due 10/31' in section(body, 'Notes')        # saved notes survive the rewrite
    assert '[[flour-prices]] Mill Co +8% from October' in body
    topic = k.memory.read('topics/flour-prices.md')
    assert '- [[2026-10-05]] Mill Co +8% from October' in topic
    skills.daily_journal(k, date(2026, 10, 5))                            # running twice adds nothing twice
    assert k.memory.read('topics/flour-prices.md').count('Mill Co +8% from October') == 1
    assert skills.similar('Friday rush at noon: 230 croissants sold', 'Friday noon rush sold 230 croissants')
    assert not skills.similar('Mill Co invoice due 10/31', 'Mill Co +8% from October')
    assert fake.sent[0]['task'] == 'journal'


def test_nothing_happened_means_no_note_and_no_call(make_kioku):
    k, fake = make_kioku()
    assert skills.daily_journal(k, date(2026, 10, 4)) is None and fake.sent == []


def test_json_retry(make_kioku):
    k, fake = make_kioku('Sure! Here is the journal: key points...', JOURNAL)
    chat_day(k)
    assert skills.daily_journal(k, date(2026, 10, 5))
    assert 'not valid' in fake.sent[1]['messages'][-1]['content']


WEEKLY = json.dumps({
    'summary': 'A week of rising costs and a record Saturday.',
    'highlights': ['412 croissants on Saturday'], 'patterns': ['Costs rise every autumn'],
    'decisions': ['Keep the croissant at 380 yen'], 'didnt_work': ['Discount flyers'],
    'next_week': ['Get mill quotes'],
    'topics': [{'topic': 'Flour prices', 'current_state': ['Mill Co is 8% higher; two quotes pending'],
                'decision': 'Keep prices until quotes arrive', 'didnt_work': ''}],
    'merge_topics': [{'into': 'flour-prices', 'from': ['flour-quotes', 'no-such-topic']}],
    'index_facts': ['Owns a small bakery', 'Signature item: croissant at 380 yen'],
})


def test_weekly_review_updates_topics_and_index(make_kioku):
    k, fake = make_kioku(JOURNAL, WEEKLY)
    chat_day(k)
    skills.daily_journal(k, date(2026, 10, 5))
    k.memory.write('profile.md', '- Runs a bakery with two staff')
    k.memory.save_note('Asked Kitano Mills for a quote', k.clock(), topic='Flour quotes')
    rel = skills.weekly_review(k, '2026-W41')
    assert 'flour-quotes' not in k.memory.topics() and 'flour-quotes → [[flour-prices]]' in k.memory.read(rel)
    assert 'Asked Kitano Mills' in k.memory.read('topics/flour-prices.md')
    assert '[[flour-quotes]]' not in k.memory.read('daily/2026-10-05.md')            # links follow the merge
    assert rel == 'weekly/2026-W41.md' and fake.sent[1]['task'] == 'weekly_review'
    assert '412 croissants' in k.memory.read(rel)
    topic = k.memory.read('topics/flour-prices.md')
    assert 'Mill Co is 8% higher' in section(topic, 'Current state')
    assert 'Keep prices until quotes arrive' in section(topic, 'Decisions')
    index = k.memory.read('index.md')
    assert 'Runs a bakery with two staff' in section(index, 'About the owner')
    assert 'Signature item: croissant at 380 yen' in section(index, 'Lasting facts')
    assert '[[flour-prices]] — Mill Co is 8% higher' in section(index, 'Topics')
    assert index.index('## Lasting facts') < index.index('## About the owner')
    skills.refresh_index(k.memory)                                                  # refreshing twice keeps one label
    again = k.memory.read('index.md')
    assert again.count('_Written by the owner') == 1 and again.count('_Kept current') == 1
    assert 'Signature item: croissant at 380 yen' in section(again, 'Lasting facts')


def test_monthly_and_briefing(make_kioku):
    monthly = json.dumps({'summary': 'October: costs up, sales up.', 'numbers': ['Sales ¥2.1M'], 'trends': ['Saturday sales grew'],
                          'decisions': [], 'lessons': ['Quote early'], 'next_month': ['Christmas orders']})
    k, fake = make_kioku(monthly, 'Good morning. Ask two mills for quotes today.')
    k.memory.write('weekly/2026-W41.md', '# 2026-W41\n\n## Summary\nBusy week.')
    k.memory.write('daily/2026-10-30.md', '# 2026-10-30\n\n## Key points\n- Halloween cookies sold out')  # W44 has no review yet
    assert skills.monthly_review(k, 2026, 10) == 'monthly/2026-10.md'
    sent = fake.sent[0]['messages'][1]['content']
    assert 'Busy week.' in sent and '2026-10-30:\n- Halloween cookies sold out' in sent
    assert '## Numbers\n- Sales ¥2.1M' in k.memory.read('monthly/2026-10.md')
    k.memory.write('daily/2026-10-04.md', '# 2026-10-04\n\n## Key points\n- Busy Sunday\n\n## Tomorrow\n- Ask two mills for quotes')
    text = skills.morning_briefing(k, date(2026, 10, 6))  # Monday 10-05 was a day off with no note
    assert 'Ask two mills' in text and 'Ask two mills' in section(k.memory.read('daily/2026-10-06.md'), 'Morning briefing')
    assert 'Ask two mills for quotes' in fake.sent[1]['messages'][1]['content']


def test_scheduler_matches_and_catches_up(make_kioku):
    assert scheduler.matches('43 23 * * *', datetime(2026, 10, 5, 23, 43))
    assert scheduler.matches('0 4 * * 0', datetime(2026, 10, 11, 4, 0))      # a Sunday
    assert not scheduler.matches('0 4 * * 0', datetime(2026, 10, 12, 4, 0))
    assert scheduler.parse_field('*/15', 0, 59) == {0, 15, 30, 45}
    k, fake = make_kioku(JOURNAL, now=datetime(2026, 10, 6, 8, 0, tzinfo=TZ))
    k.memory.append_log('owner', 'Mill Co raised flour 8%', datetime(2026, 10, 5, 21, 0, tzinfo=TZ))
    (k.settings.data_dir / 'scheduler.json').write_text(json.dumps({'daily_journal': '2026-10-05T12:00:00+09:00'}))
    done = scheduler.run_due(k, jobs={'daily_journal': '43 23 * * *'})
    assert done == [('daily_journal', '2026-10-05T23:43+09:00', 'daily/2026-10-05.md')]
    assert scheduler.run_due(k, jobs={'daily_journal': '43 23 * * *'}) == []  # never twice


def test_journal_never_writes_a_secret_back(make_kioku):
    leaky = JOURNAL.replace('Flour price went up 8%', 'Wifi password is [SECRET_1]')
    k, fake = make_kioku(leaky)
    k.memory.write('daily/2026-10-05.md', '# 2026-10-05\n\n## Notes\n- 09:00 old note: password: hunter2hunter2')
    skills.daily_journal(k, date(2026, 10, 5))
    assert 'hunter2hunter2' not in fake.seen_text()
    assert '- Wifi password is [withheld]' in k.memory.read('daily/2026-10-05.md')


def test_a_cut_off_answer_is_asked_again_with_more_room(make_kioku):
    from kioku.llm import LLMResult
    k, fake = make_kioku()
    answers = [LLMResult('{"key_points": ["half', [], 'm', 'journal', 10, 2048, 0, 1, finish_reason='length'),
               LLMResult(JOURNAL, [], 'm', 'journal', 10, 300, 0, 1, finish_reason='stop')]
    sizes = []
    k.llm.chat = lambda messages, **kw: (sizes.append(kw['max_tokens']), answers.pop(0))[1]
    chat_day(k)
    assert skills.daily_journal(k, date(2026, 10, 5)) and sizes == [2048, 4096]


def test_the_journal_is_built_from_the_owner_and_the_receipts_not_the_assistant(make_kioku):
    k, fake = make_kioku(JOURNAL)
    now = k.clock()
    k.memory.append_log('owner', 'Mill Co raises flour 8% from October: ¥6,300 → ¥6,800, about 28 bags a month.', now)
    k.memory.append_log('kioku', 'That is roughly ¥1,904 more a month.', now)          # a wrong sum by the assistant
    aid = k.approvals.request('make_payment', {'what': 'gift cards', 'amount': 30000, 'payee': 'Payment Support'}, 'r', 'R-1')
    k.decide(aid, False)
    k.audit.log('tool_call', 'block', 'spend.scam_pattern', 'looks like a known scam', tool='make_payment')
    skills.daily_journal(k, date(2026, 10, 5))
    sent = fake.sent[0]['messages'][1]['content']
    assert '¥6,800' in sent and '1,904' not in sent                               # the assistant's prose is left out
    assert 'the owner rejected A-001' in sent and 'Kioku Guard refused make_payment: spend.scam_pattern' in sent
