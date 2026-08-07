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
  for (const file of ['app/nfl/games/[gameId]/page.jsx', 'app/nfl/games/[gameId]/loading.jsx', 'app/nfl/games/[gameId]/error.jsx']) {
    assert.ok(fs.existsSync(path.join(root, file)), `${file} should exist`);
  }
});
