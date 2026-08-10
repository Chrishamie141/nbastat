const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');

test('dashboard hides public mode/disclaimer/watch score UI', () => {
  const page = fs.readFileSync('app/dashboard/page.jsx','utf8');
  assert.doesNotMatch(page, /modeLabel|Editorial watchability|Watch \{/);
  assert.match(page, /NFL game board/);
  assert.match(page, /NflMatchup/);
});

test('game card does not duplicate abbreviation before full team name or watch score', () => {
  const card = fs.readFileSync('components/games/UpcomingGameCard.jsx','utf8');
  assert.doesNotMatch(card, /Watch \{/);
  assert.doesNotMatch(card, /team\?\.abbreviation.*team\?\.name/);
  assert.match(card, /National broadcast/);
});

test('analyze page routes NFL winner choices and keeps the NBA team selector', () => {
  const page = fs.readFileSync('app/analyze/page.jsx','utf8');
  assert.match(page, /Game Winners/);
  assert.match(page, /WinnerViewChoice/);
  assert.match(page, /All Weekly Picks/);
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

test('fantasy builder loads and persists a dedicated depth chart', () => {
  const page = fs.readFileSync('app/fantasy/page.jsx','utf8');
  const api = fs.readFileSync('lib/api.js','utf8');
  assert.match(page, /Build your depth chart/);
  assert.match(page, /complete 2025 game production/);
  assert.match(page, /api\.nfl\.depthCharts/);
  assert.match(page, /api\.nfl\.saveDepthChart/);
  assert.match(api, /depthCharts:\(scoring='PPR'\)/);
});

test('NFL game surfaces share logos and complementary probability layout', () => {
  const matchup = fs.readFileSync('components/games/NflMatchup.jsx','utf8');
  const dashboard = fs.readFileSync('app/dashboard/page.jsx','utf8');
  const games = fs.readFileSync('app/games/page.jsx','utf8');
  const parlays = fs.readFileSync('app/parlays/page.jsx','utf8');
  assert.match(matchup, /TeamLogo/);
  assert.match(matchup, /homeWinProbability/);
  assert.match(matchup, /awayWinProbability/);
  assert.match(matchup, /Math\.abs\(explicitHome \+ explicitAway - 1\)/);
  assert.match(dashboard, /NflMatchup/);
  assert.match(games, /NflMatchup/);
  assert.match(parlays, /NflMatchup/);
});
