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
