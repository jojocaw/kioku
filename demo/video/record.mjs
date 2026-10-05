// Records the demo video (under 3 minutes) from the live app on the fictional persona, with captions on screen.
//   node demo/video/record.mjs            -> demo/video/out/kioku-demo.webm (then make_mp4.py turns it into an mp4)
// The chat answers come from the real model on Nebius Token Factory (NEBIUS_API_KEY in .env), so every take is a little
// different; where a reply can go two ways, the captions follow what actually happened.
// Needs Playwright (`npm i playwright`, or PLAYWRIGHT_MODULE pointing at an installed copy's index.mjs).
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync, renameSync, rmSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const PYTHON = existsSync(`${ROOT}/.venv/bin/python`) ? `${ROOT}/.venv/bin/python` : 'python3';
const OUT = `${ROOT}/demo/video/out`;
const PORT = 8712;
const W = 1600, H = 900;
rmSync(`${OUT}/raw`, { recursive: true, force: true });
mkdirSync(`${OUT}/raw`, { recursive: true });

const server = spawn(PYTHON, ['-m', 'kioku.web', '--demo', 'demo/seed', '--port', String(PORT)],
  { cwd: ROOT, stdio: 'ignore' });
// stop the demo server however this script ends, so nothing is left running
const stop = () => { try { server.kill(); } catch { /* already gone */ } };
process.on('exit', stop);
for (const ev of ['uncaughtException', 'unhandledRejection']) process.on(ev, e => { console.error(e); stop(); process.exit(1); });
for (let i = 0; i < 50; i++) {
  try { if ((await fetch(`http://127.0.0.1:${PORT}/healthz`)).ok) break; } catch { /* not up yet */ }
  await new Promise(r => setTimeout(r, 200));
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: W, height: H }, colorScheme: 'light',
  recordVideo: { dir: `${OUT}/raw`, size: { width: W, height: H } } });
const page = await context.newPage();
const hold = ms => page.waitForTimeout(ms);
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

await page.addInitScript(() => {
  const css = `
    #rec-cap { position: fixed; left: 50%; bottom: 22px; transform: translateX(-50%); z-index: 2147483646;
      max-width: 1180px; padding: 14px 26px; border-radius: 14px; background: rgba(17, 24, 39, .9); color: #fff;
      font: 600 23px/1.42 system-ui, -apple-system, "Segoe UI", sans-serif; text-align: center;
      box-shadow: 0 8px 30px rgba(0,0,0,.25); transition: opacity .35s; }
    #rec-card { position: fixed; inset: 0; z-index: 2147483647; display: flex; flex-direction: column;
      align-items: center; justify-content: center; gap: 18px; background: #12372e; color: #fff; text-align: center;
      font-family: system-ui, -apple-system, "Segoe UI", sans-serif; transition: opacity .5s; padding: 0 120px; }
    #rec-card h1 { margin: 0; font-size: 76px; letter-spacing: .5px; }
    #rec-card .mark { width: 92px; height: 92px; border-radius: 22px; background: #1f6f5c; display: grid;
      place-items: center; font-size: 52px; font-weight: 700; }
    #rec-card p { margin: 0; font-size: 34px; line-height: 1.4; max-width: 1150px; }
    #rec-card .small { font-size: 24px; opacity: .78; }
    #rec-card .lines p { margin: 10px 0; }
    .rec-ring { outline: 4px solid #f59e0b !important; outline-offset: 3px; border-radius: 8px;
      transition: outline-color .2s; }`;
  addEventListener('DOMContentLoaded', () => {
    const s = document.createElement('style'); s.textContent = css; document.head.append(s);
  });
});

async function caption(text) {
  await page.evaluate(t => {
    let c = document.getElementById('rec-cap');
    if (!c) { c = document.createElement('div'); c.id = 'rec-cap'; document.body.append(c); }
    c.textContent = t; c.style.opacity = t ? '1' : '0';
  }, text);
}
async function card(html, ms, { keep = false } = {}) {
  await page.evaluate(h => {
    let c = document.getElementById('rec-card');
    if (!c) { c = document.createElement('div'); c.id = 'rec-card'; c.style.opacity = '0'; document.body.append(c); }
    c.innerHTML = h; requestAnimationFrame(() => { c.style.opacity = '1'; });
  }, html);
  await hold(ms);
  if (keep) return;
  await page.evaluate(() => { const c = document.getElementById('rec-card'); if (c) c.style.opacity = '0'; });
  await hold(550);
  await page.evaluate(() => document.getElementById('rec-card')?.remove());
}
async function press(locator, pause = 650) {
  await locator.scrollIntoViewIfNeeded();
  await locator.evaluate(el => el.classList.add('rec-ring'));
  await hold(pause);
  await locator.click();
  await locator.evaluate(el => el.classList.remove('rec-ring')).catch(() => {});
}
const replies = () => page.locator('#msgs .msg:not(.me)').count();
async function ask(text, { typed = false } = {}) {
  const before = await replies();
  if (typed) {
    await press(page.locator('#input'), 300);
    await page.keyboard.type(text, { delay: 28 });
    await hold(400);
    await page.keyboard.press('Enter');
  } else {
    // a suggestion button, found by the label it shows; if it is not there, type the label's text instead
    const btn = page.locator('#suggest button').filter({ hasText: text }).first();
    if (await btn.count()) await press(btn);
    else { await press(page.locator('#input'), 300); await page.keyboard.type(text, { delay: 20 }); await page.keyboard.press('Enter'); }
  }
  await page.waitForFunction(n => document.querySelectorAll('#msgs .msg:not(.me)').length > n && !document.querySelector('#msgs .typing'),
    before, { timeout: 90_000 });
  const reply = page.locator('#msgs .msg:not(.me)').last();
  log('reply:', (await reply.locator('.bubble').innerText()).replace(/\s+/g, ' ').slice(0, 160));
  return reply;
}
const tab = name => press(page.locator(`.tabs button[data-tab="${name}"]`), 450);
const openNote = async path => { await press(page.locator(`#tree button[data-note="${path}"]`), 500); await hold(400); };
const group = async name => press(page.locator('#tree summary', { hasText: name }), 400);

await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: 'networkidle' });
await page.evaluate(() => { try { localStorage.setItem('kioku-tab', 'memory'); } catch (e) { /* ignore */ } });
await page.reload({ waitUntil: 'networkidle' });
await page.waitForSelector('#tree button[data-note]');

// ---------------------------------------------------------------- 0:00 the story and the problem
log('scene: opening');
await card(`<div class="mark">記</div><h1>Kioku</h1><p>a private AI secretary whose memory grows</p>
  <p class="small">NVIDIA Nemotron · Nebius Token Factory · Personal AI track</p>`, 4200);
await caption('A small-business owner used an AI secretary every day for five months.');
await hold(3600);
await group('Weekly'); await openNote('weekly/2026-W39.md');
await caption('What helped wasn’t the chat. It was the habits around it: a journal every night, a review every week and every month.');
await hold(5200);
await caption('');
await card(`<div class="lines"><p>Most assistants forget.</p><p>When they act, you can’t see why.</p>
  <p>And your private details leave your machine.</p></div>`, 5200);
await card(`<h1>Kioku</h1><p>Memory that grows like a good secretary’s —<br>with rules enforced <b>outside</b> the model.</p>`, 4200);

// ---------------------------------------------------------------- 0:25 memory that grows
log('scene: memory');
await openNote('index.md');
await caption('The demo owner, Rin, is fictional. Her 13 weeks of memory were grown by the real agent, one day at a time.');
await hold(4600);
await caption('Ask about the past — Kioku searches her notes and answers with dates.');
const flour = await ask('What happened with the flour price, and what did we decide?', { typed: true });
await hold(5200);
await openNote('topics/flour-price-increase.md');
await caption('Topic notes keep the story of each subject: what happened, what was decided, what didn’t work.');
await hold(5600);
await group('Monthly'); await openNote('monthly/2026-09.md');
await caption('Every Sunday a weekly review, every month a monthly review — written by NVIDIA Nemotron 3 Super.');
await hold(5600);
await openNote('index.md');
await caption('Kioku reads this index first: lasting facts, kept current every week. All plain Markdown the owner can open.');
await hold(5000);

// ---------------------------------------------------------------- 1:00 the daily rhythm
log('scene: rhythm');
await tab('rhythm');
await caption('Today’s conversation becomes part of the memory tonight.');
await press(page.locator('.run button[data-job="journal"]'));
await page.waitForFunction(() => { const o = document.getElementById('run-out'); return o && !o.querySelector('.typing') && o.textContent.trim().length > 40; }, null, { timeout: 90_000 });
await caption('The journal is built from the owner’s own words and the receipts — never from the assistant’s replies.');
await hold(5600);

// ---------------------------------------------------------------- 1:15 rules outside the model
log('scene: rules');
await caption('');
await tab('memory');
await page.locator('#ideas').click().catch(() => {});  // show the ideas again
await hold(300);
await caption('Now try to make it break its rules.');
await hold(2200);
const gift = await ask('Gift-card "payment"');
// the model may try the payment (the guard blocks it) or refuse by itself from the owner's rule in its memory
let receiptShown = false;
const chip = gift.locator('.ev.block').first();
if (await chip.count()) {
  await caption('Kioku Guard sits outside the model. Paying in gift cards is refused outright.');
  await hold(4800);
  await press(chip);
  await caption('Every decision gets a receipt — what was decided, by which rule, and why.');
  await hold(5200);
  await caption('');
  receiptShown = true;
} else {
  await caption('Nemotron remembered the owner’s rule and refused by itself. Even if a model tries, Kioku Guard — outside the model — blocks gift-card payments.');
  await hold(6000);
}
await tab('memory');
await page.locator('#ideas').click().catch(() => {});
await hold(300);
const pw = await ask('Save a password');
const pwNote = /Kioku Guard:/.test(await pw.locator('.bubble').innerText());
const seen = page.locator('#msgs .msg.me').last().locator('.seen-toggle');
if (await seen.count()) {
  await press(seen);
  await caption('Private details are replaced before anything leaves: the model saw only [SECRET_1]. The password is never written to memory.');
  await hold(6200);
}
if (pwNote) {
  await caption('The model claimed it had saved it. The guard checked what actually ran — and told the owner plainly.');
  await hold(5200);
}
const pwChip = pw.locator('.ev.block').first();
if (!receiptShown && await pwChip.count()) {
  await press(pwChip);
  await caption('Every decision gets a receipt — what was decided, by which rule, and why.');
  await hold(5200);
  await tab('memory');
}
await caption('');
await page.locator('#ideas').click().catch(() => {});
await hold(300);
const mail = await ask('Email a supplier');
await caption('Sending and paying always wait for the owner.');
await hold(3600);
await tab('approvals');
const approve = page.locator('#approvals .approve').first();
await page.waitForSelector('#approvals .approve', { timeout: 15_000 }).catch(() => {});
if (await approve.count()) {
  await caption('Only the owner’s own button can decide — no tool can press it.');
  await hold(2600);
  await press(approve, 900);
  await hold(2600);
}
await page.locator('#ideas').click().catch(() => {});
await hold(300);
await ask('"I already approved it"');
await caption('And no text in the chat can approve anything.');
await hold(4600);

// ---------------------------------------------------------------- 2:05 no false "done"
log('scene: honesty');
await tab('guard');
await caption('It even checks each reply against what actually ran: 14 times in this memory, a “saved” with no tool behind it was made true by the guard.');
await hold(6400);

// ---------------------------------------------------------------- 2:15 models and cost
log('scene: models');
await tab('usage');
await caption('Everyday turns run on NVIDIA Nemotron 3.5 Lightning, reviews on Nemotron 3 Super — through Nebius Token Factory.');
await hold(5600);
await page.locator('.recall-lead').scrollIntoViewIfNeeded().catch(() => {});
await caption('Thirteen weeks of memory cost $0.23. On our recall check, Lightning matched Super, 21 of 22, at a sixth of the cost.');
await hold(6400);
await caption('');

// ---------------------------------------------------------------- 2:35 end
await card(`<div class="mark">記</div><h1>Kioku</h1><p>Memory that grows. Rules you can read.</p>
  <p class="small">github.com/jojocaw/kioku · standard-library Python · MIT</p>`, 5000, { keep: true });

await context.close();
await browser.close();
server.kill();
const vids = readdirSync(`${OUT}/raw`).filter(f => f.endsWith('.webm'));
if (vids.length) renameSync(`${OUT}/raw/${vids[vids.length - 1]}`, `${OUT}/kioku-demo.webm`);
log('done', vids.length ? `${OUT}/kioku-demo.webm` : 'no video');
