const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");

test("ticket state prevents duplicates, conflicts, and cross-game SGP legs", async () => {
  const { addLegToTicket } = await import("../lib/parlay-ticket.mjs");
  const empty = { ticketType: null, sportsbook: "", legs: [] };
  const meta = { ticketType: "SAME_GAME_PARLAY", sportsbook: "DraftKings", strategy: "BALANCED" };
  const over = { gameId: "g1", rowId: "over", player: "A", market: "PASS_YDS", line: 231.5, side: "OVER", bookmaker: "DraftKings" };
  const first = addLegToTicket(empty, over, meta, "2026-09-27T12:00:00Z");
  assert.equal(first.result.ok, true);
  assert.equal(addLegToTicket(first.ticket, over, meta).result.reason, "DUPLICATE");
  assert.equal(addLegToTicket(first.ticket, { ...over, rowId: "under", side: "UNDER" }, meta).result.reason, "OPPOSING_SELECTION");
  assert.equal(addLegToTicket(first.ticket, { ...over, gameId: "g2", rowId: "other" }, meta).result.reason, "SGP_GAME_CONFLICT");
});

test("multi-game tickets allow distinct games but retain one sportsbook", async () => {
  const { addLegToTicket } = await import("../lib/parlay-ticket.mjs");
  const meta = { ticketType: "MULTI_GAME_PARLAY", sportsbook: "DraftKings", strategy: "BALANCED" };
  const empty = { ticketType: null, sportsbook: "", legs: [] };
  const first = addLegToTicket(empty, { gameId: "g1", rowId: "a", player: "A", market: "RUSH_YDS", line: 70, side: "OVER", bookmaker: "DraftKings" }, meta);
  assert.equal(addLegToTicket(first.ticket, { gameId: "g2", rowId: "b", player: "B", market: "REC_YDS", line: 60, side: "OVER", bookmaker: "DraftKings" }, meta).result.ok, true);
  assert.equal(addLegToTicket(first.ticket, { gameId: "g2", rowId: "c", player: "C", market: "REC_YDS", line: 50, side: "OVER", bookmaker: "FanDuel" }, meta).result.reason, "SPORTSBOOK_CONFLICT");
});

test("parlay workflow exposes persistent slip, review, confirm, history, and preferences", () => {
  const context = fs.readFileSync("context/ParlayTicketContext.jsx", "utf8");
  const analysis = fs.readFileSync("app/parlays/analysis/page.jsx", "utf8");
  const review = fs.readFileSync("app/parlays/review/page.jsx", "utf8");
  const api = fs.readFileSync("lib/api.js", "utf8");
  assert.match(context, /localStorage/);
  assert.match(context, /smartbets:parlay-preferences/);
  assert.match(analysis, /Add to Ticket/);
  assert.match(analysis, /Manual selection/);
  assert.match(review, /Confirm Ticket/);
  assert.match(review, /does not display a multiplied-leg win probability/);
  assert.match(api, /confirmParlayTicket/);
  assert.match(api, /parlayTickets/);
});
