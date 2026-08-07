const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8');

test('NFL game cards use a whole-card canonical game ID link', () => {
  const source = read('components/games/UpcomingGameCard.jsx');
  assert.match(source, /href={`\/nfl\/games\/\$\{game\.id\}`}/);
  assert.match(source, /aria-label=\{label\}/);
  assert.match(source, /focus-visible:ring-2/);
  assert.doesNotMatch(source, /<button/);
});

test('dashboard provides lifecycle filters and server-side manual refresh', () => {
  const source = read('app/dashboard/page.jsx');
  for (const label of ['All', 'Upcoming', 'Live', 'Final']) assert.ok(source.includes(label));
  assert.match(source, /api\.refreshGames\(\)/);
  assert.match(source, /No change/);
  assert.match(source, /Refresh failed/);
});

test('game detail separates pregame context, actual result, and final comparison', () => {
  const source = read('components/games/NflGameBreakdown.jsx');
  assert.match(source, /PREGAME CONTEXT/);
  assert.match(source, /ACTUAL GAME RESULT/);
  assert.match(source, /Prediction versus actual/);
  assert.match(source, /api\.nfl\.refreshGame/);
  assert.match(source, /sources\.paidProviderContacted/);
});

test('canonical NFL route includes loading and error boundaries', () => {
  for (const file of ['app/nfl/games/[gameId]/page.jsx', 'app/nfl/games/[gameId]/loading.jsx', 'app/nfl/games/[gameId]/error.jsx', 'app/nfl/games/[gameId]/not-found.jsx']) {
    assert.ok(fs.existsSync(path.join(root, file)), `${file} should exist`);
  }
});

test('game UI labels prior-season context and client requests are bounded', () => {
  assert.match(read('components/games/NflGameBreakdown.jsx'), /game results are never backfilled/);
  assert.match(read('lib/api.js'), /AbortController/);
  assert.match(read('lib/api.js'), /error\?\.message/);
});

test('search and history failures do not masquerade as empty results', () => {
  assert.match(read('components/analyze/TeamSelector.jsx'), /Team search service unavailable/);
  assert.match(read('components/search/CatalogSearch.jsx'), /This is not a zero-result search/);
  assert.match(read('app/history/page.jsx'), /History service unavailable/);
});

test('analysis action is contextual and duplicate restart control is removed', () => {
  const source = read('app/analyze/page.jsx');
  assert.match(source, /Analysis running…/);
  assert.match(source, /Retry Analysis/);
  assert.doesNotMatch(source, />Restart<\/button>/);
});
