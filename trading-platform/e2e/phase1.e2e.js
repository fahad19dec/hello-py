// End-to-end check of Phase 1 in a real browser, against a running API and dashboard.
//
// It registers a user, confirms the emailed link, signs in, turns on two-factor,
// signs in again with a code, creates a super admin from the server, suspends
// and reinstates the user, resets the password by email and checks the phone
// layout. Screenshots are written to the output folder.
//
//   node e2e/phase1.e2e.js <screenshot-dir> <backend-log-file> <backend-dir>
//
// Requirements: the API started with EMAIL_BACKEND=console and its output
// redirected to <backend-log-file> (the script reads the emailed links from
// it), the dashboard on http://localhost:3000, and Playwright with Chromium.
// See docs/testing.md.
const { chromium } = require('playwright');
const crypto = require('crypto');
const fs = require('fs');
const { execFileSync } = require('child_process');

const BASE = 'http://localhost:3000';
const OUT = process.argv[2];
const BACKEND_LOG = process.argv[3];
const BACKEND_DIR = process.argv[4];
const stamp = Date.now();
const USER = `asha.${stamp}@example.com`;
const ADMIN = `root.${stamp}@example.com`;
const PASSWORD = 'correct horse battery staple';
const ADMIN_PASSWORD = 'operator-passphrase-2026';

function totp(secret, stepOffset = 0) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const ch of secret.replace(/=+$/, '')) bits += alphabet.indexOf(ch).toString(2).padStart(5, '0');
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000) + stepOffset));
  const mac = crypto.createHmac('sha1', key).update(counter).digest();
  const o = mac[mac.length - 1] & 15;
  return String((mac.readUInt32BE(o) & 0x7fffffff) % 1e6).padStart(6, '0');
}

function emailedLink(email, page) {
  const log = fs.readFileSync(BACKEND_LOG, 'utf8');
  const blocks = log.split('----- EMAIL to=').filter((b) => b.startsWith(email) && b.includes(`/${page}?token=`));
  if (!blocks.length) throw new Error(`no ${page} email for ${email} in the backend log`);
  return blocks[blocks.length - 1].match(new RegExp(`http://localhost:3000/${page}\\?token=[\\w-]+`))[0];
}

const problems = [];
const checks = [];
function check(name, ok, detail = '') {
  checks.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
}

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1360, height: 900 }, deviceScaleFactor: 2 });
  const page = await context.newPage();
  const expected4xx = /Failed to load resource: the server responded with a status of 4\d\d/;
  page.on('console', (m) => { if (m.type() === 'error' && !expected4xx.test(m.text())) problems.push(`console: ${m.text()}`); });
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  const shot = (name, p = page) => p.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  global.__page = page;

  // 1. signed-out visitors are sent to sign in
  await page.goto(`${BASE}/`);
  await page.waitForURL(/\/login/);
  check('signed-out visitor is redirected to /login', true);
  await shot('01-login');

  // 2. register
  await page.click('role=link[name="Create account"]');
  await page.waitForURL(/\/register/);
  await page.fill('#email', USER);
  await page.fill('#password', 'short');
  await page.fill('#confirm', 'short');
  await page.evaluate(() => document.querySelector('#password').removeAttribute('minlength'));
  await page.click('button[type=submit]');
  await page.waitForSelector('p[role=alert]');
  check('weak password is refused with a reason', (await page.textContent('p[role=alert]')).includes('at least 10'));
  await page.fill('#password', PASSWORD);
  await page.fill('#confirm', PASSWORD);
  await page.click('button[type=submit]');
  await page.waitForSelector('text=Check your email');
  check('registration shows the neutral "check your email" state', true);

  // 3. sign-in is blocked until the address is confirmed
  await page.goto(`${BASE}/login`);
  await page.fill('#email', USER);
  await page.fill('#password', PASSWORD);
  await page.click('button[type=submit]');
  await page.waitForSelector('text=Confirm your email address before signing in');
  check('unconfirmed account cannot sign in', true);

  // 4. confirm through the emailed link
  await new Promise((r) => setTimeout(r, 300));
  await page.goto(emailedLink(USER, 'verify-email'));
  await page.waitForSelector('text=Email confirmed');
  check('emailed link confirms the address', true);
  check('token is removed from the address bar', !page.url().includes('token='), page.url());

  // 5. sign in and land on the overview
  await page.click('role=link[name="Sign in"]');
  await page.waitForURL(/\/login/);
  await page.fill('#email', USER);
  await page.fill('#password', 'not the password');
  await page.click('button[type=submit]');
  await page.waitForSelector('text=Email or password is incorrect');
  check('wrong password gives a generic error', true);
  await page.fill('#password', PASSWORD);
  await page.click('button[type=submit]');
  await page.waitForURL(/\/overview/);
  await page.waitForSelector('text=Recent account activity');
  await page.waitForSelector('text=Signed in');
  check('sign-in lands on the overview with real activity', true);
  check('live trading is shown as off', await page.locator('text=Live trading off').first().isVisible());
  await shot('02-overview');

  const cookies = await context.cookies();
  const session = cookies.find((c) => c.name === 'tp_session');
  check('session cookie is HttpOnly and SameSite=Lax', session && session.httpOnly && session.sameSite === 'Lax');
  check('page scripts cannot read the session cookie', !(await page.evaluate(() => document.cookie)).includes('tp_session'));

  // 6. a section that is not built says so and shows no data
  await page.click('nav >> text=Strategies');
  await page.waitForSelector('text=Not built yet');
  await shot('03-planned-section');
  check('unbuilt section is labelled as not built', true);

  // 7. profile
  await page.goto(`${BASE}/settings`);
  await page.fill('#display_name', 'Asha Rao');
  await page.selectOption('#timezone', 'Asia/Kolkata');
  await page.click('role=button[name="Save changes"]');
  await page.waitForSelector('text=Profile saved');
  check('profile saves', true);

  // 8. two-factor setup
  await page.click('role=tab[name="Security"]');
  await page.click('role=button[name="Set up two-factor"]');
  await page.fill('[role=dialog] #password', PASSWORD);
  await page.click('[role=dialog] button[type=submit]');
  await page.waitForSelector('text=Scan this code');
  const secret = (await page.textContent('[role=dialog] span.font-mono')).trim();
  await shot('04-two-factor-setup');
  await page.fill('[role=dialog] #code', '000000');
  await page.click('[role=dialog] button[type=submit]');
  await page.waitForSelector('[role=dialog] p[role=alert]');
  check('wrong setup code is refused', true);
  await page.fill('[role=dialog] #code', totp(secret, -1));
  await page.click('[role=dialog] button[type=submit]');
  await page.waitForSelector('text=Two-factor authentication is on');
  const codes = await page.locator('[role=dialog] ul.font-mono li').allTextContents();
  check('ten recovery codes are shown once', codes.length === 10);
  await shot('05-recovery-codes');
  await page.click('role=button[name="I have saved them"]');
  await page.waitForSelector('text=10 of 10 recovery codes unused');

  // 9. sessions tab
  await page.click('role=tab[name="Sessions"]');
  await page.waitForSelector('text=This device');
  await shot('06-sessions');
  check('sessions tab lists this device', true);

  // 10. sign out; sign in now needs the second factor
  await page.click('button[aria-label="Account menu"]');
  await page.click('role=menuitem[name="Sign out"]');
  await page.waitForURL(/\/login/);
  await page.goto(`${BASE}/overview`);
  await page.waitForURL(/\/login/);
  check('after sign-out the dashboard is unreachable', true);
  await page.fill('#email', USER);
  await page.fill('#password', PASSWORD);
  await page.click('button[type=submit]');
  await page.waitForSelector('text=Two-factor authentication');
  await shot('07-second-factor');
  await page.fill('#code', '000000');
  await page.click('button[type=submit]');
  await page.waitForSelector('text=That code is not valid');
  await page.fill('#code', totp(secret, 0));
  await page.click('button[type=submit]');
  await page.waitForURL(/\/overview/);
  await page.waitForSelector('text=Recent account activity');
  check('sign-in completes with the authenticator code', true);

  // 11. a regular user has no admin area
  await page.goto(`${BASE}/admin/users`);
  await page.waitForSelector('text=Page not found');
  const adminNav = await page.locator('nav >> text=Audit Log').count();
  check('regular user sees no admin area', adminNav === 0);

  // 12. operator creates the first super admin from the server
  execFileSync(`${BACKEND_DIR}/.venv/bin/python`, ['-m', 'app.cli', 'create-admin', '--email', ADMIN], {
    cwd: BACKEND_DIR, env: { ...process.env, ADMIN_PASSWORD }, stdio: 'pipe',
  });
  const adminContext = await browser.newContext({ viewport: { width: 1360, height: 900 }, deviceScaleFactor: 2 });
  const admin = await adminContext.newPage();
  admin.on('console', (m) => { if (m.type() === 'error' && !expected4xx.test(m.text())) problems.push(`console(admin): ${m.text()}`); });
  admin.on('pageerror', (e) => problems.push(`pageerror(admin): ${e.message}`));
  await admin.goto(`${BASE}/login`);
  await admin.fill('#email', ADMIN);
  await admin.fill('#password', ADMIN_PASSWORD);
  await admin.click('button[type=submit]');
  await admin.waitForURL(/\/overview/);
  await admin.goto(`${BASE}/admin/users`);
  await admin.waitForSelector('text=Two-factor sign-in required');
  check('admin area refuses a staff session without two-factor', true);
  await shot('08-admin-needs-two-factor', admin);

  await admin.click('role=link[name="Open security settings"]');
  await admin.click('role=button[name="Set up two-factor"]');
  await admin.fill('[role=dialog] #password', ADMIN_PASSWORD);
  await admin.click('[role=dialog] button[type=submit]');
  await admin.waitForSelector('text=Scan this code');
  const adminSecret = (await admin.textContent('[role=dialog] span.font-mono')).trim();
  await admin.fill('[role=dialog] #code', totp(adminSecret, 0));
  await admin.click('[role=dialog] button[type=submit]');
  await admin.waitForSelector('text=Two-factor authentication is on');
  await admin.click('role=button[name="I have saved them"]');

  // 13. suspend the user from the admin area
  await admin.click('nav >> text=Users');
  await admin.waitForSelector(`text=${USER}`);
  await shot('09-admin-users', admin);
  await admin.click(`button[aria-label="Actions for ${USER}"]`);
  await admin.click('role=menuitem[name="Suspend"]');
  await admin.fill('#reason', 'End-to-end test of suspension');
  await admin.click('role=button[name="Suspend account"]');
  await admin.waitForSelector('text=suspended and signed out');
  check('admin can suspend a user', true);

  await page.goto(`${BASE}/settings`);
  await page.waitForURL(/\/login/);
  check('the suspended user is signed out immediately', true);
  await page.fill('#email', USER);
  await page.fill('#password', PASSWORD);
  await page.click('button[type=submit]');
  await page.waitForSelector('text=This account is suspended');
  check('the suspended user cannot sign in', true);

  await admin.click('nav >> text=Audit Log');
  await admin.waitForSelector('text=admin.user.suspended');
  await shot('10-admin-audit', admin);
  check('the suspension is in the audit log', true);

  await admin.click('nav >> text=Users');
  await admin.click(`button[aria-label="Actions for ${USER}"]`);
  await admin.click('role=menuitem[name="Reinstate"]');
  await admin.waitForSelector('text=reinstated');
  check('admin can reinstate the user', true);

  // 14. password reset by email, which must not remove two-factor
  await page.click('role=link[name="Forgot password?"]');
  await page.waitForURL(/\/forgot-password/);
  await page.waitForSelector('text=Reset your password');
  await page.fill('#email', USER);
  await page.click('button[type=submit]');
  await page.waitForSelector('text=Check your email');
  await new Promise((r) => setTimeout(r, 300));
  await page.goto(emailedLink(USER, 'reset-password'));
  await page.fill('#password', 'a brand new passphrase 77');
  await page.fill('#confirm', 'a brand new passphrase 77');
  await page.click('button[type=submit]');
  await page.waitForSelector('text=Password changed');
  await page.click('role=link[name="Sign in"]');
  await page.waitForURL(/\/login/);
  await page.fill('#email', USER);
  await page.fill('#password', 'a brand new passphrase 77');
  await page.click('button[type=submit]');
  await page.waitForSelector('text=Two-factor authentication');
  check('password reset works and still asks for the second factor', true);
  await page.click('role=button[name="Use a recovery code instead"]');
  await page.fill('#code', codes[0]);
  await page.click('button[type=submit]');
  await page.waitForURL(/\/overview/);
  check('a recovery code completes sign-in', true);

  // 15. phone layout
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  await phone.addCookies(await context.cookies());
  const mobile = await phone.newPage();
  await mobile.goto(`${BASE}/overview`);
  await mobile.waitForSelector('text=Recent account activity');
  const overflow = await mobile.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check('phone layout has no sideways scroll', overflow <= 0, `overflow ${overflow}px`);
  await shot('11-mobile-overview', mobile);
  await mobile.click('button[aria-label="Open menu"]');
  await mobile.waitForSelector('[role=dialog] >> text=Broker Accounts');
  await mobile.screenshot({ path: `${OUT}/12-mobile-menu.png` });

  await browser.close();
  const failed = checks.filter((c) => !c.ok);
  console.log(`\n${checks.length - failed.length}/${checks.length} checks passed`);
  console.log(problems.length ? `Browser problems:\n  ${[...new Set(problems)].join('\n  ')}` : 'No browser console errors or page errors.');
  process.exit(failed.length || problems.length ? 1 : 0);
})().catch(async (e) => { try { await global.__page.screenshot({ path: `${OUT}/failure.png` }); console.log('failure url:', global.__page.url()); } catch {} console.error('E2E aborted:', e.message.split('\n')[0]); console.log(problems.join('\n')); process.exit(2); });
