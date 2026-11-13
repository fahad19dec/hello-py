// End-to-end check of broker accounts, webhooks, risk management and order placement,
// in a real browser against the running API, worker and dashboard.
//
//   node e2e/trading.e2e.js <screenshot-dir> <backend-log-file>
//
// Needs the API (EMAIL_BACKEND=console, output in <backend-log-file>), the worker
// (python -m app.workers.run) and the dashboard on http://localhost:3000.
// Alerts are sent the way TradingView sends them: a POST of the message text to the webhook URL.
const { chromium } = require('playwright');
const fs = require('fs');

const BASE = 'http://localhost:3000';
const [OUT, LOG] = process.argv.slice(2);
const EMAIL = `trader.${Date.now()}@example.com`;
const PASSWORD = 'correct horse battery staple';
const checks = [];
const problems = [];
const check = (name, ok, detail = '') => { checks.push(ok); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function emailedLink(email, page) {
  const blocks = fs.readFileSync(LOG, 'utf8').split('----- EMAIL to=').filter((b) => b.startsWith(email) && b.includes(`/${page}?token=`));
  return blocks[blocks.length - 1].match(new RegExp(`http://localhost:3000/${page}\\?token=[\\w-]+`))[0];
}

async function sendAlert(url, fields) {
  const body = { symbol: 'NSE:RELIANCE', action: 'BUY', price: 2500, timestamp: new Date().toISOString(), ...fields };
  const res = await fetch(url, { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'text/plain' } });
  return { status: res.status, body: await res.json() };
}

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1360, height: 900 }, deviceScaleFactor: 2 });
  const page = await context.newPage();
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error' && !/status of 4\d\d/.test(m.text())) problems.push(`console: ${m.text()}`); });
  global.__page = page;
  const shot = (name) => page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  const go = async (path, text) => { await page.goto(`${BASE}${path}`); await page.waitForSelector(`text=${text}`); };

  // sign up and sign in
  await page.goto(`${BASE}/register`);
  await page.fill('#email', EMAIL); await page.fill('#password', PASSWORD); await page.fill('#confirm', PASSWORD);
  await page.click('button[type=submit]'); await page.waitForSelector('text=Check your email'); await sleep(300);
  await page.goto(emailedLink(EMAIL, 'verify-email')); await page.waitForSelector('text=Email confirmed');
  await page.goto(`${BASE}/login`); await page.fill('#email', EMAIL); await page.fill('#password', PASSWORD);
  await page.click('button[type=submit]'); await page.waitForURL(/\/overview/);

  // 1. broker accounts
  await go('/broker-accounts', 'No accounts yet');
  await page.click('role=button[name="Add account"]');
  await page.fill('[role=dialog] #label', 'Paper INR');
  await page.fill('[role=dialog] #slippage_pct', '0');
  await page.click('[role=dialog] button[type=submit]');
  await page.waitForSelector('text=Account added');
  await page.click('role=button[name="Add account"]');
  await page.selectOption('[role=dialog] #broker', 'kite');
  await page.fill('[role=dialog] #label', 'Zerodha main');
  await page.click('[role=dialog] button[type=submit]');
  await page.waitForSelector('role=button[name="Add API key"]');
  await page.click('role=button[name="Add account"]');
  await page.selectOption('[role=dialog] #broker', 'mt5');
  await page.fill('[role=dialog] #label', 'MT5 demo');
  await page.fill('[role=dialog] #symbol_suffix', 'm');
  await page.click('[role=dialog] button[type=submit]');
  await page.waitForSelector('role=button[name="Pair terminal"]');
  check('paper, Zerodha and MetaTrader accounts can be added', true);
  await page.click('role=button[name="Pair terminal"]');
  await page.waitForSelector('text=Copy the token now');
  const deviceToken = (await page.locator('[role=dialog] code').first().textContent()).trim();
  check('pairing shows a device token once', deviceToken.startsWith('tbd_'));
  await shot('20-pairing');
  await page.keyboard.press('Escape');
  // the terminal (simulated here) connects with the token
  const poll = await fetch(`${BASE}/api/v1/bridge/poll`, { method: 'POST', headers: { 'X-Device-Token': deviceToken, 'Content-Type': 'application/x-www-form-urlencoded' },
    body: 'login=5001&server=Demo&currency=USD&balance=10000&equity=10000&margin_free=10000' });
  check('the terminal connects with the device token', (await poll.text()).includes('type=idle'));
  await page.reload(); await page.waitForSelector('text=MT5 demo');
  await shot('21-broker-accounts');

  // 2. strategy linked to the paper account, fixed size 5
  await go('/strategies', 'No strategies yet');
  await page.click('role=button[name="New strategy"]');
  await page.fill('[role=dialog] #code', 'RELIANCE_TREND'); await page.fill('[role=dialog] #name', 'Reliance trend');
  await page.click('[role=dialog] button[type=submit]');
  await page.waitForSelector('text=No accounts linked');
  await page.click('role=button[name="Link account"]');
  await page.selectOption('[role=dialog] #account', { label: 'Paper INR (paper)' });
  await page.selectOption('[role=dialog] #sizing_mode', 'fixed');
  await page.fill('[role=dialog] #sizing_value', '5');
  await page.click('[role=dialog] button[type=submit]');
  await page.waitForSelector('text=Fixed size: 5');
  check('a strategy is linked to the paper account with a fixed size', true);
  await shot('22-strategies');

  // 3. webhook
  await go('/webhooks', 'No webhooks yet');
  await page.click('role=button[name="New webhook"]');
  await page.fill('[role=dialog] #name', 'TradingView 15m');
  await page.click('[role=dialog] button[type=submit]');
  await page.waitForSelector('text=Shown once');
  const hookUrl = (await page.locator('[role=dialog] code').first().textContent()).trim();
  check('the webhook URL is shown once', hookUrl.includes('/api/v1/hooks/'));
  await shot('23-webhook-created');
  await page.keyboard.press('Escape');
  await page.click('role=button[name="Test"]');
  await page.click('role=button[name="Check message"]');
  await page.waitForSelector('text=Valid BUY alert');
  check('the dry-run tester validates a message without trading', true, await page.locator('[role=dialog] p.text-muted-foreground').first().textContent());
  await page.keyboard.press('Escape');

  // 4. place an order: TradingView posts the alert
  const sent = await sendAlert(hookUrl, { signal_id: 'e2e-1' });
  check('the alert is accepted', sent.status === 202, JSON.stringify(sent.body));
  const dup = await sendAlert(hookUrl, { signal_id: 'e2e-1' });
  check('the same signal again is a duplicate', dup.body.status === 'duplicate');
  await sleep(2500);
  await go('/orders', 'Orders');
  await page.selectOption('select[aria-label="Filter by state"]', '');
  await page.waitForSelector('tbody >> text=FILLED');
  check('the worker filled the paper order', true);
  await shot('24-orders');
  await page.locator('tbody tr').first().click();
  await page.waitForSelector('[role=dialog] >> text=History');
  check('order history shows each state', (await page.locator('[role=dialog] li').count()) >= 4);
  await shot('25-order-detail');
  await page.keyboard.press('Escape');
  await go('/positions', 'Open positions');
  await page.waitForSelector('td:has-text("RELIANCE")');
  check('the position is open', (await page.locator('td:has-text("RELIANCE")').count()) === 1);
  await go('/signals', 'Signal log');
  await page.waitForSelector('text=1 duplicate(s) ignored');
  check('the signal log shows the duplicate was ignored', true);
  await shot('26-signals');

  // 5. risk: a limit rejects an order with the rule
  await go('/risk', 'Emergency kill switch');
  await page.locator('role=button[name="Edit limits"]').first().click();
  await page.fill('[role=dialog] #max_order_quantity', '2');
  await page.click('[role=dialog] button[type=submit]');
  await page.waitForSelector('text=Max order quantity: 2');
  await sendAlert(hookUrl, { signal_id: 'e2e-2' });
  await sleep(2500);
  await go('/orders', 'Orders');
  await page.selectOption('select[aria-label="Filter by state"]', 'REJECTED');
  await page.waitForSelector('text=Max order quantity:');
  check('a risk limit rejects the order and says which rule', true);
  await shot('27-rejected-by-risk');

  // 6. kill switch
  await go('/risk', 'Emergency kill switch');
  await page.locator('role=button[name="Edit limits"]').first().click();
  await page.fill('[role=dialog] #max_order_quantity', '');
  await page.click('[role=dialog] button[type=submit]');
  await sleep(500);
  page.once('dialog', (d) => d.accept('e2e test'));
  await page.click('role=button[name="Stop all automated trading"]');
  await page.waitForSelector('text=Resume automated trading');
  await shot('28-kill-switch');
  await sendAlert(hookUrl, { signal_id: 'e2e-3' });
  await sleep(2500);
  await go('/orders', 'Orders');
  await page.selectOption('select[aria-label="Filter by state"]', 'REJECTED');
  await page.waitForSelector('text=Your kill switch:');
  check('the kill switch stops the next order', true);
  await go('/risk', 'Emergency kill switch');
  await page.click('role=button[name="Resume automated trading"]');
  await page.waitForSelector('text=Stop all automated trading');

  // 7. semi-automatic: the order waits for approval
  await go('/strategies', 'Reliance trend');
  await page.click('role=button[name="Edit"]');
  await page.check('[role=dialog] input[name=approval_required]');
  await page.click('[role=dialog] button[type=submit]');
  await page.waitForSelector('text=you approve each order');
  await sendAlert(hookUrl, { signal_id: 'e2e-4', action: 'SELL', price: 2510 });
  await sleep(2500);
  await go('/orders', 'Orders');
  await page.waitForSelector('tbody >> text=AWAITING APPROVAL');
  check('a semi-automatic order waits for approval', true);
  await shot('29-awaiting-approval');
  await page.click('role=button[name="Approve"]');
  await page.waitForSelector('text=Approved');
  await sleep(2500);
  await page.selectOption('select[aria-label="Filter by state"]', 'FILLED');
  await page.waitForFunction(() => document.querySelectorAll('tbody tr').length >= 2);
  check('an approved semi-automatic order executes', true);

  // 8. overview and paper pages show stored figures
  await go('/overview', 'Trading today');
  await page.waitForSelector('text=Filled or applied');
  await shot('30-overview');
  await go('/paper-trading', 'Simulated positions');
  await shot('31-paper-trading');
  await go('/trades', 'Trade history');
  check('trade history lists the fills', (await page.locator('tbody tr').count()) >= 2);
  await go('/api-docs', 'Alert message format');

  // 9. phone layout
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  await phone.addCookies(await context.cookies());
  const mobile = await phone.newPage();
  for (const path of ['/orders', '/broker-accounts', '/risk', '/webhooks']) {
    await mobile.goto(`${BASE}${path}`); await mobile.waitForLoadState('networkidle');
    const overflow = await mobile.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(`phone layout of ${path} has no sideways scroll`, overflow <= 0, `overflow ${overflow}px`);
  }
  await mobile.screenshot({ path: `${OUT}/32-mobile-orders.png`, fullPage: true });

  await browser.close();
  const failed = checks.filter((ok) => !ok).length;
  console.log(`\n${checks.length - failed}/${checks.length} checks passed`);
  console.log(problems.length ? `Browser problems:\n  ${[...new Set(problems)].join('\n  ')}` : 'No browser console errors or page errors.');
  process.exit(failed || problems.length ? 1 : 0);
})().catch(async (e) => {
  try { await global.__page.screenshot({ path: `${OUT}/failure.png`, fullPage: true }); console.log('failure url:', global.__page.url()); } catch {}
  console.error('E2E aborted:', e.message.split('\n')[0]); console.log(problems.join('\n')); process.exit(2);
});
