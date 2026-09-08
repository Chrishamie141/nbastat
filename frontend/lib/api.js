// Production uses the same-origin Vercel rewrite. A public override remains
// available for local development against a separately running API.
const API=process.env.NEXT_PUBLIC_API_BASE_URL||process.env.NEXT_PUBLIC_API_URL||'';
const REQUEST_TIMEOUT_MS = 12000;

async function request(path, { method = 'GET', body, timeoutMs = REQUEST_TIMEOUT_MS } = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(`${API}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
      cache: 'no-store',
      credentials: 'include',
      signal: controller.signal,
    });
  } catch (error) {
    if (error?.name === 'AbortError') throw new Error('The server took too long to respond. Please try again.');
    throw new Error('Unable to connect to the server.');
  } finally {
    clearTimeout(timeout);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = data?.error?.message || data?.detail || (typeof data?.error === 'string' ? data.error : '') || response.statusText;
    const error = new Error(message || `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return data;
}

export const api = {
  auth: {
    me: () => request("/api/auth/me"),
    register: (body) => request("/api/auth/register", { method: "POST", body }),
    login: (body) => request("/api/auth/login", { method: "POST", body }),
    ownerLogin: (body) => request("/api/auth/owner-login", { method: "POST", body }),
    logout: () => request("/api/auth/logout", { method: "POST" }),
    forgotPassword: (body) => request("/api/auth/forgot-password", { method: "POST", body }),
    resetPassword: (body) => request("/api/auth/reset-password", { method: "POST", body }),
    bootstrapAdmin: (body) => request("/api/auth/bootstrap-admin", { method: "POST", body }),
  },
  health: () => request("/api/health"),
  readiness: () => request('/api/readiness'),
  search: (query) => request(`/api/search?q=${encodeURIComponent(query)}`),
  refreshGames: () => request('/api/games/refresh', {method: 'POST'}),
  config: () => request("/api/config/status"),
  dashboard: () => request("/api/dashboard"),
  internal: {
    operations: () => request('/api/internal/operations'),
    operationsHealth: () => request('/api/internal/operations/health'),
    socialPosts: ({ limit = 25, offset = 0, status = 'ALL' } = {}) =>
      request(`/api/internal/operations/social-posts?limit=${limit}&offset=${offset}${status === 'ALL' ? '' : `&status=${encodeURIComponent(status)}`}`),
    operationsSearch: (query) => request(`/api/internal/operations/search?q=${encodeURIComponent(query)}`),
    operationGame: (gameId) => request(`/api/internal/operations/games/${encodeURIComponent(gameId)}`, { timeoutMs: 20000 }),
    refreshOperations: () => request('/api/internal/operations/refresh', { method: 'POST', timeoutMs: 20000 }),
    refreshOperationGame: (gameId) => request(`/api/internal/operations/games/${encodeURIComponent(gameId)}/refresh`, { method: 'POST', timeoutMs: 20000 }),
    reconcileOperationGame: (gameId) => request(`/api/internal/operations/games/${encodeURIComponent(gameId)}/reconcile`, { method: 'POST', timeoutMs: 30000 }),
    nflExperiment: ({ season, seasonType, week }) =>
      request(`/api/internal/nfl/experiments/${season}/${seasonType}/${week}`),
    gradeNflExperiment: ({ season, seasonType, week }) =>
      request(
        `/api/internal/nfl/experiments/${season}/${seasonType}/${week}/grade`,
        {
          method: "POST",
        },
      ),
  },
  sportsMode: () => request("/api/sports-mode"),
  teams: (league) => request(`/api/teams?league=${league}`),
  upcomingGames: () => request("/api/games/upcoming"),
  featuredGame: () => request("/api/games/featured"),
  history: (q = "") => request(`/api/history${q}`),
  performance: () => request("/api/performance"),
  billing: {
    entitlements: () => request("/api/billing/entitlements"),
    subscription: () => request("/api/billing/subscription"),
    checkout: () =>
      request("/api/billing/create-checkout-session", { method: "POST" }),
    portal: () =>
      request("/api/billing/create-portal-session", { method: "POST" }),
    refresh: () => request("/api/billing/refresh", { method: "POST" }),
    confirmCheckout: (sessionId) =>
      request("/api/billing/confirm-checkout", {
        method: "POST",
        body: { sessionId: sessionId || null },
        timeoutMs: 20000,
      }),
  },
  nfl: {
    game: (gameId) => request(`/api/nfl/games/${encodeURIComponent(gameId)}`),
    refreshGame: (gameId) => request(`/api/nfl/games/${encodeURIComponent(gameId)}/refresh`, {method: 'POST'}),
    context: (season) =>
      request("/api/nfl/context" + (season ? "?season=" + season : "")),
    week: ({
      season,
      week = 1,
      profile = "BALANCED",
      day = "ALL",
      seasonType = "regular",
    } = {}) =>
      request(
        "/api/nfl/week?" +
          (season ? "season=" + season + "&" : "") +
          "week=" +
          week +
          "&profile=" +
          profile +
          "&day=" +
          day +
          "&seasonType=" +
          seasonType,
      ),
    gameHistory: (season, seasonType = "regular") =>
      request(
        "/api/nfl/games/history?" +
          (season ? "season=" + season + "&" : "") +
          "seasonType=" +
          seasonType,
      ),
    weekPerformance: ({ season, week = 1, seasonType = "regular" } = {}) =>
      request(
        "/api/nfl/performance/week?" +
          (season ? "season=" + season + "&" : "") +
          "week=" +
          week +
          "&seasonType=" +
          seasonType,
      ),
    parlay: (body) =>
      request("/api/analyze/nfl/parlay", { method: "POST", body }),
    multiGameParlay: (body) =>
      request("/api/nfl/parlays/multi-game", { method: "POST", body }),
    fantasy: (body) =>
      request("/api/analyze/nfl/fantasy", { method: "POST", body }),
    depthCharts: (scoring = "PPR") =>
      request(`/api/nfl/fantasy/depth-charts?scoring=${scoring}`),
    saveDepthChart: (body) =>
      request("/api/nfl/fantasy/depth-charts", { method: "POST", body }),
    updateDepthChart: (id, body) =>
      request(`/api/nfl/fantasy/depth-charts/${id}`, { method: "PUT", body }),
    deleteDepthChart: (id) =>
      request(`/api/nfl/fantasy/depth-charts/${id}`, { method: "DELETE" }),
    history: (q = "") => request(`/api/analyze/nfl/history${q}`),
    grade: (body) =>
      request("/api/analyze/nfl/grade", { method: "POST", body }),
    performance: () => request("/api/analyze/nfl/performance"),
  },
  nba: {
    player: (body) =>
      request("/api/analyze/nba/player", { method: "POST", body }),
    roster: (body) =>
      request("/api/analyze/nba/roster", { method: "POST", body }),
    team: (body) => request("/api/analyze/nba/team", { method: "POST", body }),
    bestBets: () => request("/api/analyze/nba/best-bets"),
    parlay: (body) =>
      request("/api/analyze/nba/parlay", { method: "POST", body }),
    grade: (body) =>
      request("/api/analyze/nba/grade", { method: "POST", body }),
    history: () => request("/api/analyze/nba/history"),
    performance: () => request("/api/analyze/nba/performance"),
  },
};
