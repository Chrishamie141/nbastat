const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");

test("dashboard hides public mode/disclaimer/watch score UI", () => {
  const page = fs.readFileSync("app/dashboard/page.jsx", "utf8");
  assert.doesNotMatch(page, /modeLabel|Editorial watchability|Watch \{/);
  assert.match(page, /NFL game board/);
  assert.match(page, /NflMatchup/);
});

test("game card does not duplicate abbreviation before full team name or watch score", () => {
  const card = fs.readFileSync("components/games/UpcomingGameCard.jsx", "utf8");
  assert.doesNotMatch(card, /Watch \{/);
  assert.doesNotMatch(card, /team\?\.abbreviation.*team\?\.name/);
  assert.match(card, /National broadcast/);
});

test("analyze page routes NFL winner choices and keeps the NBA team selector", () => {
  const page = fs.readFileSync("app/analyze/page.jsx", "utf8");
  assert.match(page, /Game Winners/);
  assert.match(page, /WinnerViewChoice/);
  assert.match(page, /All Weekly Picks/);
  assert.match(page, /TeamSelector/);
  assert.doesNotMatch(page, /Optional team abbreviation|<select/);
});

test("production API requests use the same-origin Vercel backend rewrite", () => {
  const api = fs.readFileSync("lib/api.js", "utf8");
  const config = fs.readFileSync("next.config.mjs", "utf8");
  assert.match(api, /NEXT_PUBLIC_API_URL\s*\|\|\s*["']{2}/);
  assert.doesNotMatch(api, /localhost:8000/);
  assert.match(config, /source: '\/api\/:path\*'/);
  assert.match(config, /smartbetsports-api\.vercel\.app/);
});

test("local browser automation fails closed instead of targeting production", () => {
  const config = fs.readFileSync("next.config.mjs", "utf8");
  const api = fs.readFileSync("lib/api.js", "utf8");
  assert.match(config, /http:\/\/127\.0\.0\.1:8000/);
  assert.match(config, /ALLOW_LOCAL_PRODUCTION_MUTATIONS/);
  assert.match(
    config,
    /Local development is configured for the production API/,
  );
  assert.doesNotMatch(api, /season = 2026/);
});

test("fantasy builder loads and persists a dedicated depth chart", () => {
  const page = fs.readFileSync("app/fantasy/page.jsx", "utf8");
  const api = fs.readFileSync("lib/api.js", "utf8");
  assert.match(page, /Build your depth chart/);
  assert.match(page, /complete 2025 game production/);
  assert.match(page, /api\.nfl\.depthCharts/);
  assert.match(page, /api\.nfl\.saveDepthChart/);
  assert.match(api, /depthCharts:\s*\(scoring\s*=\s*["']PPR["']\)/);
});

test("NFL game surfaces share logos and complementary probability layout", () => {
  const matchup = fs.readFileSync("components/games/NflMatchup.jsx", "utf8");
  const dashboard = fs.readFileSync("app/dashboard/page.jsx", "utf8");
  const games = fs.readFileSync("app/games/page.jsx", "utf8");
  const parlays = fs.readFileSync("app/parlays/page.jsx", "utf8");
  assert.match(matchup, /TeamLogo/);
  assert.match(matchup, /homeWinProbability/);
  assert.match(matchup, /awayWinProbability/);
  assert.match(matchup, /Math\.abs\(explicitHome \+ explicitAway - 1\)/);
  assert.match(dashboard, /NflMatchup/);
  assert.match(games, /NflMatchup/);
  assert.match(parlays, /NflMatchup/);
});

test("weekly NFL surfaces distinguish preseason, probability, evidence, and value", () => {
  const games = fs.readFileSync("app/games/page.jsx", "utf8");
  const detail = fs.readFileSync("app/games/[slug]/page.jsx", "utf8");
  const api = fs.readFileSync("lib/api.js", "utf8");
  assert.match(games, /Preseason/);
  assert.match(games, /Hall of Fame Game/);
  assert.match(games, /maxWeek = seasonType === "preseason" \? 3 : 18/);
  assert.match(games, /model win probability/);
  assert.match(games, /Evidence quality/);
  assert.match(games, /not a\s+probability/);
  assert.match(games, /No trustworthy pregame pick/);
  assert.match(detail, /Market vs model/);
  assert.match(api, /seasonType/);
  assert.match(api, /weekPerformance/);
});

test("multi-game builder has a real generation action and explains rejected legs", () => {
  const page = fs.readFileSync("app/parlays/page.jsx", "utf8");
  const api = fs.readFileSync("lib/api.js", "utf8");
  assert.match(page, /generateMulti/);
  assert.match(page, /Validate and build parlay/);
  assert.match(page, /rejectedSelections/);
  assert.match(page, /No sample legs were substituted/);
  assert.match(api, /multiGameParlay/);
});

test("internal Week 3 experiment dashboard separates predictions from wagers", () => {
  const page = fs.readFileSync(
    "app/internal/experiments/week3/page.jsx",
    "utf8",
  );
  const api = fs.readFileSync("lib/api.js", "utf8");
  const auth = fs.readFileSync("components/auth/AuthProvider.jsx", "utf8");
  assert.match(page, /Experiment integrity/);
  assert.match(page, /Winner record/);
  assert.match(page, /Qualified record/);
  assert.match(page, /Expected SHA-256/);
  assert.match(page, /Frozen game ledger/);
  assert.match(page, /INSUFFICIENT_SAMPLE|data\.calibration\.status/);
  assert.match(api, /api\/internal\/nfl\/experiments/);
  assert.match(auth, /['"]\/internal['"]/);
});

test("internal command center is a plain-language multi-sport owner dashboard", () => {
  const page = fs.readFileSync("app/internal/operations/page.jsx", "utf8");
  const api = fs.readFileSync("lib/api.js", "utf8");
  const nav = fs.readFileSync("components/layout/PremiumNavbar.jsx", "utf8");
  const terms = fs.readFileSync("lib/ownerTerminology.js", "utf8");
  const footer = fs.readFileSync("components/layout/Footer.jsx", "utf8");
  assert.match(page, />\s*Command Center\s*</);
  assert.doesNotMatch(page, /Week 1 Command Center|Week 1 readiness|production · build/);
  assert.match(page, /System Readiness/);
  assert.match(page, /Needs Attention/);
  assert.match(page, />Games</);
  assert.match(page, />Predictions</);
  assert.match(page, /System Health/);
  assert.match(page, /Social Posting/);
  assert.match(page, /X Post History/);
  assert.match(page, /Posts made manually on X are not imported/);
  assert.match(page, /Activity History/);
  assert.doesNotMatch(page, /Buyer funnel|Members|Paid members|subscription/i);
  assert.doesNotMatch(page, /Manifest hash|Artifact hash|System A|Quota UNKNOWN|Expected X user ID|No artifact/);
  assert.doesNotMatch(page, /game\.id}\s*·/);
  assert.match(page, /\["ALL", "NFL", "NBA"\]/);
  assert.match(terms, /Current Slate/);
  assert.match(footer, /©.*brand\.name/);
  assert.doesNotMatch(footer, /Clear sports analysis/);
  assert.match(page, /setInterval\(load, 60000\)/);
  assert.match(api, /api\/internal\/operations/);
  assert.match(api, /operations\/search/);
  assert.match(api, /operations\/games/);
  assert.match(nav, /internal\/operations/);
  assert.match(nav, /ownerPortal/);
});

test("social operations is a responsive owner control room with safety controls", () => {
  const page = fs.readFileSync("app/internal/operations/social/page.jsx", "utf8");
  const queue = fs.readFileSync("components/internal/social/SocialQueue.jsx", "utf8");
  const settings = fs.readFileSync("components/internal/social/SocialSettings.jsx", "utf8");
  const performance = fs.readFileSync("components/internal/social/SocialPerformance.jsx", "utf8");
  const api = fs.readFileSync("lib/api.js", "utf8");
  assert.match(page, /Social Operations/);
  assert.match(page, /PAUSE ALL SOCIAL/);
  assert.match(page, /Content Studio/);
  assert.match(page, /Skipped Opportunities/);
  assert.match(page, /Media Library/);
  assert.match(performance, /Not available from current X API access/);
  assert.match(queue, /Post Now/);
  assert.match(queue, /Regenerate image/);
  assert.match(settings, /External auto replies/);
  assert.match(api, /operations\/social\/queue/);
  assert.match(api, /operations\/social\/metrics\/refresh/);
});

test("owner game flow exposes frozen prediction evidence and has no publish control", () => {
  const page = fs.readFileSync("app/internal/operations/page.jsx", "utf8");
  const game = fs.readFileSync("components/games/NflGameBreakdown.jsx", "utf8");
  assert.match(page, /internal\/games/);
  assert.match(page, /View evaluation/);
  assert.match(game, /ownerPrediction/);
  assert.match(game, /Artifact SHA-256/);
  assert.match(game, /Sportsbook prices and wager qualification remain separate/);
  assert.doesNotMatch(page, /publishOne|Publish now|Enable publishing/);
});
