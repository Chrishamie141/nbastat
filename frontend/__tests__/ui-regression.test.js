const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');

test('dashboard hides public mode/disclaimer/watch score UI', () => {
  const page = fs.readFileSync('app/dashboard/page.jsx','utf8');
  assert.doesNotMatch(page, /modeLabel|Editorial watchability|Watch \{/);
  assert.match(page, /items-start/);
  assert.match(page, /Featured game unavailable/);
});

test('game card does not duplicate abbreviation before full team name or watch score', () => {
  const card = fs.readFileSync('components/games/UpcomingGameCard.jsx','utf8');
  assert.doesNotMatch(card, /Watch \{/);
  assert.doesNotMatch(card, /team\?\.abbreviation.*team\?\.name/);
  assert.match(card, /National broadcast/);
});

test('analyze page uses custom risk and team selector defaults', () => {
  const page = fs.readFileSync('app/analyze/page.jsx','utf8');
  assert.match(page, /difficulty:'BALANCED'/);
  assert.match(page, /RiskLevelSelector/);
  assert.match(page, /TeamSelector/);
  assert.doesNotMatch(page, /Optional team abbreviation|<select/);
});

test('production API requests use the same-origin Vercel backend rewrite', () => {
  const api = fs.readFileSync('lib/api.js','utf8');
  const config = fs.readFileSync('next.config.mjs','utf8');
  assert.match(api, /NEXT_PUBLIC_API_URL\|\|''/);
  assert.doesNotMatch(api, /localhost:8000/);
  assert.match(config, /source: '\/api\/:path\*'/);
  assert.match(config, /smartbetsports-api\.vercel\.app/);
});

test('fantasy builder submits league settings and renders a dedicated draft board', () => {
  const page = fs.readFileSync('app/analyze/page.jsx','utf8');
  const api = fs.readFileSync('lib/api.js','utf8');
  const result = fs.readFileSync('components/results/AnalysisResult.jsx','utf8');
  assert.match(page, /scoring:'PPR'.*position:'ALL'.*limit:25/);
  assert.match(page, /Build Draft Board/);
  assert.match(page, /api\.nfl\.fantasy\(opts\)/);
  assert.match(api, /fantasy:\(body\).*method:'POST'/);
  assert.match(result, /FantasyResult/);
  assert.match(result, /2025 PPG/);
});
