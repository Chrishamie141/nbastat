const test = require("node:test");
const assert = require("node:assert/strict");

test("percentages have explicit units and preserve small percentages", async () => {
  const { formatPercent, formatDateTime } = await import("../lib/display-values.mjs");
  assert.equal(formatPercent(1), "1.0%");
  assert.equal(formatPercent(.5), "0.5%");
  assert.equal(formatPercent(1, "fraction"), "100.0%");
  assert.equal(formatPercent(.821, "fraction"), "82.1%");
  assert.equal(formatPercent(0), "0.0%");
  for (const value of [null, undefined, "", NaN, Infinity, -1, 101]) assert.equal(formatPercent(value), "—");
  assert.equal(formatDateTime("not-a-date"), "—");
});

test("membership transport failures never redirect an existing member to checkout", async () => {
  const { membershipState } = await import("../lib/member-state.mjs");
  const base = { ready: true, isAuthenticated: true, loading: false, subscription: null, error: "" };
  assert.equal(membershipState(base), "loading");
  assert.equal(membershipState({ ...base, error: "timeout" }), "error");
  assert.equal(membershipState({ ...base, subscription: { status: "active", hasFullAccess: false } }), "inactive");
  assert.equal(membershipState({ ...base, subscription: { hasFullAccess: true } }), "active");
  assert.equal(membershipState({ ...base, isAuthenticated: false }), "signed_out");
});

test("API rejects malformed bodies and preserves authentication status", async () => {
  const { createRequest } = await import("../lib/request.mjs");
  for (const status of [200, 401, 503]) {
    const request = createRequest("", async () => ({ ok: status === 200, status, json: async () => { throw new SyntaxError("html"); } }));
    await assert.rejects(request("/test"), (error) => error.status === status && !error.message.includes("html"));
  }
  const request = createRequest("", async () => ({ ok: false, status: 422, json: async () => ({ detail: [{ msg: "bad input" }] }) }));
  await assert.rejects(request("/test"), /check your inputs/);
});

test("request deadline covers a stalled body, not only response headers", async () => {
  const { createRequest } = await import("../lib/request.mjs");
  const request = createRequest("", async (_url, options) => ({
    ok: true, status: 200, json: () => new Promise((_resolve, reject) => options.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")))),
  }));
  await assert.rejects(request("/test", { timeoutMs: 10 }), /too long/);
});

test("API handles no-content and passes only configured request destination", async () => {
  const { createRequest } = await import("../lib/request.mjs");
  let observed;
  const request = createRequest("https://configured.example", async (...args) => { observed = args; return { ok: true, status: 204 }; });
  assert.equal(await request("/action", { method: "POST", body: { value: 0 } }), null);
  assert.equal(observed[0], "https://configured.example/action");
  assert.equal(observed[1].credentials, "include");
  assert.equal(observed[1].body, '{"value":0}');
});
