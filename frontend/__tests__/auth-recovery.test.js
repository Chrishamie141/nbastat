const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = (file) => fs.readFileSync(path.join(__dirname, '..', file), 'utf8');

test('login, registration, and account surfaces expose password recovery', () => {
  assert.match(read('app/login/page.jsx'), /Forgot password\?/);
  assert.match(read('app/register/page.jsx'), /Forgot password\?/);
  assert.match(read('app/account/page.jsx'), /Reset Password/);
  assert.match(read('lib/api.js'), /forgot-password/);
  assert.match(read('lib/api.js'), /reset-password/);
});

test('recovery and setup forms keep identity and secrets in request bodies', () => {
  const files = ['app/forgot-password/page.jsx', 'app/reset-password/page.jsx', 'app/setup/page.jsx'].map(read).join('\n');
  assert.doesNotMatch(files, /router\.(push|replace)\([^)]*(email|code|token|setupCode)/);
  assert.match(read('lib/site-url.js'), /https:\/\/smartbetsports\.com/);
  assert.match(read('middleware.js'), /\.\.\.url\.searchParams\.keys/);
  assert.match(read('middleware.js'), /toLowerCase\(\).*replace/);
});

test('owner command center uses a dedicated owner-only login flow', () => {
  const login = read('app/login/page.jsx');
  const auth = read('components/auth/AuthProvider.jsx');
  assert.match(login, /ownerLogin/);
  assert.match(login, /Owner Login/);
  assert.match(login, /!ownerMode/);
  assert.match(auth, /'\/command-center'/);
  assert.match(read('lib/api.js'), /\/api\/auth\/owner-login/);
  assert.ok(fs.existsSync(path.join(__dirname, '../app/command-center/page.jsx')));
});

test('all redirect consumers use the shared safe path boundary', () => {
  assert.match(read('app/login/page.jsx'), /safeNextPath/);
  assert.match(read('app/subscribe/page.jsx'), /safeNextPath/);
  assert.match(read('app/billing/success/page.jsx'), /safeNextPath/);
  const helper = read('lib/safe-next-path.js');
  assert.match(helper, /startsWith\('\/\/'\)/);
  assert.match(helper, /parsed\.pathname/);
});
