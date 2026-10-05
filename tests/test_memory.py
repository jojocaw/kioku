from datetime import date, datetime

import pytest

from kioku.memory import Memory, add_to_section, section, set_section, shift_month, slugify, tokens, week_days, week_id


def test_notes_and_topics(tmp_path):
    m = Memory(tmp_path)
    m.save_note('Mill Co raised flour 8%', datetime(2026, 9, 12, 10, 5), topic='Flour prices')
    m.save_note('Asked two other mills for quotes', datetime(2026, 9, 12, 15, 0), topic='Flour prices')
    daily = m.read('daily/2026-09-12.md')
    assert '- 10:05 Mill Co raised flour 8% [[flour-prices]]' in daily
    assert section(daily, 'Notes').count('\n') == 1
    topic = m.read('topics/flour-prices.md')
    assert topic.startswith('# Flour prices') and '- [[2026-09-12]] Asked two other mills for quotes' in topic
    assert m.topics() == ['flour-prices']


def test_search_ranks_the_right_note(tmp_path):
    m = Memory(tmp_path)
    m.write('daily/2026-09-12.md', '# 2026-09-12\n\n## Notes\n- Mill Co raised the flour price by 8%')
    m.write('daily/2026-09-13.md', '# 2026-09-13\n\n## Notes\n- Record Saturday: sold 412 croissants')
    m.write('daily/2026-09-14.md', '# 2026-09-14\n\n## Notes\n- 小麦粉の値上げについて別の製粉所に相談した')
    assert m.search('when did flour prices go up')[0].path == 'daily/2026-09-12.md'
    assert m.search('croissant sales record')[0].path == 'daily/2026-09-13.md'
    assert m.search('小麦粉 値上げ')[0].path == 'daily/2026-09-14.md'
    assert m.search('the and of') == []


def test_on_this_day(tmp_path):
    m = Memory(tmp_path)
    m.write('daily/2026-09-28.md', '# 2026-09-28\n\n## Key points\n- Started the autumn menu')
    m.write('daily/2026-09-05.md', '# 2026-09-05\n\n## Key points\n- Oven repair')
    found = dict(m.on_this_day(date(2026, 10, 5)))
    assert 'Started the autumn menu' in found['a week ago (2026-09-28)']
    assert 'Oven repair' in found['a month ago (2026-09-05)']


def test_paths_stay_inside_memory(tmp_path):
    m = Memory(tmp_path / 'memory')
    with pytest.raises(ValueError):
        m.read('../secret.txt')
    with pytest.raises(ValueError):
        m.write('/etc/x.md', 'no')


def test_helpers():
    assert week_id(date(2026, 10, 5)) == '2026-W41'
    assert week_days('2026-W41')[0] == date(2026, 10, 5) and len(week_days('2026-W41')) == 7
    assert shift_month(date(2026, 3, 31), -1) == date(2026, 2, 28)
    assert slugify('Flour prices / Mill Co') == 'flour-prices-mill-co' and slugify('小麦粉の値段') == '小麦粉の値段'
    assert 'flour' in tokens('Flours!') and '小麦' in tokens('小麦粉')
    body = set_section('# Day\n\n## A\n- one\n\n## B\n- two\n', 'A', '- uno')
    assert section(body, 'A') == '- uno' and section(body, 'B') == '- two'
    assert section(add_to_section(body, 'C', '- three'), 'C') == '- three'


def test_catch_all_topics_are_not_topics(tmp_path):
    from kioku.memory import Memory, real_topic
    m = Memory(tmp_path)
    assert real_topic('Daily notes') is None and real_topic(' ') is None and real_topic('Flour prices') == 'Flour prices'
    m.save_note('230 croissants', datetime(2026, 7, 10, 16, 5), topic='daily-notes')
    assert m.topics() == [] and '230 croissants' in m.read('daily/2026-07-10.md')


def test_similar_topic_names_share_one_note(tmp_path):
    m = Memory(tmp_path)
    m.save_note('Launched at ¥320', datetime(2026, 7, 17, 16, 0), topic='Lemon cream bun')
    m.save_note('80 sold', datetime(2026, 7, 18, 16, 0), topic='lemon buns')
    m.save_note('Thermostat broken', datetime(2026, 8, 3, 9, 0), topic='ovens')
    m.save_note('Repaired for ¥48,000', datetime(2026, 8, 7, 13, 0), topic='Oven repair')
    m.save_note('Mill Co +8%', datetime(2026, 9, 12, 12, 0), topic='Flour prices')
    m.save_note('Asked Kitano Mills', datetime(2026, 9, 15, 10, 0), topic='Flour supplier')
    assert m.topics() == ['flour-prices', 'flour-supplier', 'lemon-cream-bun', 'ovens']
    assert '80 sold' in m.read('topics/lemon-cream-bun.md') and 'Repaired' in m.read('topics/ovens.md')


def test_long_names_are_not_topics():
    from kioku.memory import real_topic
    assert real_topic('Kitchen reached 27 C at 4 am causing dough to soften') is None
    assert real_topic('Hoshino Dairy deliveries') == 'Hoshino Dairy deliveries'


def test_merge_topics_keeps_history_and_links(tmp_path):
    m = Memory(tmp_path)
    m.save_note('500 flyers printed', datetime(2026, 8, 18, 9, 30), topic='Flyer coupon')
    m.save_note('Handed out flyers', datetime(2026, 8, 18, 16, 20), topic='Flyer distribution')
    m.save_note('9 coupons used', datetime(2026, 8, 22, 16, 40), topic='Coupon returns')
    assert m.merge_topics('flyer-coupon', ['flyer-distribution', 'coupon-returns', 'missing']) == ['flyer-distribution', 'coupon-returns']
    assert m.topics() == ['flyer-coupon']
    history = section(m.read('topics/flyer-coupon.md'), 'History').splitlines()
    assert [h.split(']] ')[1] for h in history] == ['500 flyers printed', 'Handed out flyers', '9 coupons used']
    assert '[[flyer-distribution]]' not in m.read('daily/2026-08-18.md') and '[[flyer-coupon]]' in m.read('daily/2026-08-18.md')
    assert m.merge_topics('flyer-coupon', ['flyer-coupon']) == []


def test_complete_task(tmp_path):
    m = Memory(tmp_path)
    m.add_task('Order the Obon closure sign and put it up', due='2026-08-06')
    m.add_task('Book oven maintenance', due='2027-08')
    assert m.complete_task('the Obon closure sign is up', datetime(2026, 8, 9, 15, 30)).startswith('Order the Obon')
    assert m.open_tasks() == ['Book oven maintenance (due 2027-08)']
    assert '- [x] Order the Obon closure sign and put it up (due 2026-08-06) (done 2026-08-09)' in m.read('tasks.md')
    assert m.complete_task('pay the electricity bill', datetime(2026, 8, 9)) is None


@pytest.mark.parametrize('a,b', [('quotes', 'quote'), ('prices', 'price'), ('ordered', 'order'), ('priced', 'price'),
                                 ('deliveries', 'delivery'), ('batches', 'batch'), ('croissants', 'croissant')])
def test_stem_meets_singulars(a, b):
    from kioku.memory import stem
    assert stem(a) == stem(b)
