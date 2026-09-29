// Local-only browser regression checks. All API calls are mocked; no credentials,
// provider charges, database writes, email, checkout or social publication occur.
const assert = require("node:assert/strict");
const path = require("node:path");
const { default: AxeBuilder } = require("@axe-core/playwright");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");
const base = process.env.QA_BASE_URL || "http://127.0.0.1:3001";
if (!["127.0.0.1", "localhost"].includes(new URL(base).hostname)) throw new Error("QA must target localhost");

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.QA_BROWSER_PATH ? { executablePath: process.env.QA_BROWSER_PATH } : {}) });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  let auth = false, authFailure = false, membershipFailure = false, performanceFailure = false;
  const user = { id: 17, name: "QA member", email: "qa@example.invalid", isInternal: false };
  const errors = [];
  const accessibilityFailures = [];
  async function checkAccessibility(page, label) {
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    for (const violation of result.violations) accessibilityFailures.push({
      page: label, rule: violation.id, impact: violation.impact,
      nodes: violation.nodes.map((node) => ({ target: node.target, summary: node.failureSummary })),
    });
    console.log("Accessibility", label, result.violations.length, "violations");
  }
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith("/api/")) {
      let status = 200, body;
      if (url.pathname === "/api/auth/me") { status = authFailure ? 503 : auth ? 200 : 401; body = { user: auth ? user : null }; }
      else if (url.pathname === "/api/billing/entitlements") { status = membershipFailure ? 503 : 200; body = { hasFullAccess: true, status: "active", plan: "founding" }; }
      else if (url.pathname === "/api/performance") { status = performanceFailure ? 503 : 200; body = { metrics: { "NFL record": "2-1" }, series: [] }; }
      else if (url.pathname === "/api/nfl/parlays/analysis") body = {
        profile: "BALANCED",
        matchups: [{ gameId: "qa-game", homeTeam: "BUF", awayTeam: "MIA", selectedTeam: "BUF", modelWinner: "BUF", selectedTeamProbability: .64, kickoffTime: "2030-09-15T20:00:00Z" }],
        propBoards: [{ rows: [{ rowId: "qa-row", player: "QA Player", team: "BUF", market: "PASS_YDS", side: "OVER", line: 250.5, modelLikelihood: 1, recentHitRate: .5, recentSample: 200, bookmaker: "QA Book", profileEligible: false }] }],
      };
      else { status = 503; body = { detail: "QA provider unavailable" }; }
      return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    }
    if (url.origin !== new URL(base).origin) return route.abort();
    return route.continue();
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.goto(base);
    await checkAccessibility(page, "home mobile");
    await page.getByRole("button", { name: "Open menu" }).click();
    await page.getByRole("navigation", { name: "Mobile primary" }).waitFor();
    await page.getByRole("navigation", { name: "Mobile primary" }).getByRole("link", { name: "Log in", exact: true }).click();
    await page.waitForURL("**/login");
    assert.equal(await page.getByRole("navigation", { name: "Mobile primary" }).count(), 0);
    console.log("PASS guest mobile navigation");
    for (const route of ["/login", "/register", "/forgot-password", "/reset-password"]) {
      await page.goto(base + route);
      await page.waitForLoadState("networkidle");
      await checkAccessibility(page, route + " mobile");
    }

    auth = true; authFailure = true;
    await page.goto(base + "/account");
    await page.getByRole("heading", { name: "Account service unavailable" }).waitFor();
    authFailure = false;
    await page.getByRole("button", { name: "Retry session check" }).click();
    await page.getByRole("heading", { name: "Account & Billing" }).waitFor();
    console.log("PASS session recovery");

    membershipFailure = true;
    await page.goto(base + "/parlays/analysis");
    await page.getByRole("heading", { name: "Membership check unavailable" }).waitFor();
    assert.ok(page.url().endsWith("/parlays/analysis"));
    membershipFailure = false;
    await page.getByRole("button", { name: "Retry membership check" }).click();
    await page.getByRole("heading", { name: "Choose your matchups first" }).waitFor();
    console.log("PASS membership outage does not cause checkout redirect");

    await page.evaluate(() => sessionStorage.setItem("smartbets:parlay-analysis", JSON.stringify({
      mode: "same_game", profile: "BALANCED", season: 2030, week: 1, selections: [{ gameId: "qa-game", team: "BUF" }],
    })));
    await page.reload();
    await page.getByRole("button", { name: "Load verified analysis", exact: true }).click();
    await page.getByRole("cell", { name: "1.0%", exact: true }).waitFor();
    await page.getByRole("cell", { name: "0.5%", exact: true }).waitFor();
    await page.getByRole("cell", { name: "64.0%", exact: true }).waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.resolve(".runtime/review-parlays-mobile.png"), fullPage: true });
    await checkAccessibility(page, "parlay analysis mobile");
    const comparison = page.getByRole("region", { name: /^Matchup comparison/ });
    await comparison.focus();
    assert.equal(await comparison.evaluate((node) => node === document.activeElement), true);
    await page.keyboard.press("ArrowRight");
    await page.waitForTimeout(150);
    assert.ok(await comparison.evaluate((node) => node.scrollLeft > 0), "Keyboard users must be able to scroll matchup columns");
    await page.getByRole("radio", { name: /^Safe/ }).click();
    assert.equal(await page.getByRole("cell", { name: "QA Player", exact: true }).count(), 0);
    console.log("PASS explicit percentages, mobile layout and profile result invalidation");

    await page.getByRole("button", { name: "Open menu" }).click();
    await page.getByRole("navigation", { name: "Mobile primary" }).getByRole("link", { name: "performance", exact: true }).waitFor();
    console.log("PASS signed-in full mobile navigation");
    performanceFailure = true;
    await page.goto(base + "/performance");
    await page.getByRole("button", { name: "Retry performance" }).waitFor();
    performanceFailure = false;
    await page.getByRole("button", { name: "Retry performance" }).click();
    await page.getByText("2-1", { exact: true }).waitFor();
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.screenshot({ path: path.resolve(".runtime/review-performance-desktop.png"), fullPage: true });
    const font = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
    assert.match(font, /Geist/i);
    console.log("PASS performance recovery and Geist font:", font);
    await checkAccessibility(page, "performance desktop");

    for (const route of ["/dashboard", "/games", "/analyze", "/analyze/classic", "/parlays", "/fantasy", "/history", "/account", "/nfl/games/401872657"]) {
      await page.goto(base + route);
      await page.locator("main").first().waitFor();
      await page.waitForLoadState("networkidle");
      assert.ok((await page.locator("main").first().innerText()).trim().length > 20, route + " rendered no useful state");
      assert.equal(await page.getByRole("heading", { name: "This page could not load" }).count(), 0, route + " hit the root error boundary");
      console.log("PASS subscriber route / provider-unavailable state", route);
      await checkAccessibility(page, route);
    }
    user.isInternal = true;
    for (const route of ["/command-center", "/internal/operations/social", "/internal/experiments/week3"]) {
      await page.goto(base + route);
      await page.locator("main").first().waitFor();
      await page.waitForLoadState("networkidle");
      assert.ok((await page.locator("main").first().innerText()).trim().length > 20, route + " rendered no useful state");
      assert.equal(await page.getByRole("heading", { name: "This page could not load" }).count(), 0, route + " hit the root error boundary");
      console.log("PASS owner route / provider-unavailable state", route);
      await checkAccessibility(page, route);
    }

    await page.goto(base + "/not-a-real-page");
    await page.getByRole("heading", { name: "Page not found" }).waitFor();
    assert.deepEqual(errors, []);
    assert.deepEqual(accessibilityFailures, [], JSON.stringify(accessibilityFailures, null, 2));
    console.log("PASS 404 recovery; zero browser runtime exceptions");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
