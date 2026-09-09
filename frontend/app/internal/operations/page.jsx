"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Database,
  ExternalLink,
  RefreshCw,
  Search,
  ShieldCheck,
  Siren,
  Workflow,
} from "lucide-react";
import GlowCard from "@/components/ui/GlowCard";
import { api } from "@/lib/api";
import { ownerStatus, ownerTerms } from "@/lib/ownerTerminology";

const EMPTY = "—";
const fmt = (value) => (value ? new Date(value).toLocaleString() : EMPTY);
const modelName = (value) => value ? value.replace("nfl_game_baseline_v", "NFL Game Model v").replaceAll("_", " ") : EMPTY;
const issueCopy = (issue) => {
  if (issue.category === "DATA") return ["Game data needs attention", "Some live game data is not updating yet."];
  if (issue.category === "DATABASE") return ["Database needs attention", "Some system data needs attention."];
  if (issue.category === "AUTOMATION") return ["Social posting is paused", "Posting safeguards are keeping X activity paused."];
  return [`${issue.category.replaceAll("_", " ")} issue`, issue.ownerSummary || issue.summary];
};

function Pill({ value }) {
  const text = String(value || "UNKNOWN").toUpperCase();
  const good = [
    "HEALTHY",
    "READY",
    "PASS",
    "CURRENT",
    "SUCCEEDED",
    "FINAL",
    "BLOCKED",
  ].includes(text);
  const bad = [
    "FAILED",
    "CRITICAL",
    "NOT_READY",
    "UNAVAILABLE",
    "STALE",
    "MISSING",
  ].includes(text);
  return (
    <span
      className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-black tracking-wide ${good ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-200" : bad ? "border-rose-400/30 bg-rose-400/10 text-rose-100" : "border-amber-300/30 bg-amber-300/10 text-amber-100"}`}
    >
      {text.replaceAll("_", " ")}
    </span>
  );
}

function Metric({ label, value, detail, icon: Icon }) {
  return (
    <GlowCard className="p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-black uppercase tracking-[.16em] text-slate-400">
            {label}
          </p>
          <div className="mt-3 text-3xl font-black tracking-tight text-white">
            {value ?? EMPTY}
          </div>
          {detail && <p className="mt-1 text-xs text-slate-400">{detail}</p>}
        </div>
        {Icon && <Icon className="text-cyan-300" size={20} />}
      </div>
    </GlowCard>
  );
}

function PanelState({ panel, retry }) {
  if (!panel || panel.status === "HEALTHY") return null;
  return (
    <div
      role="alert"
      className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-300/30 bg-amber-300/10 p-3 text-sm text-amber-100"
    >
      <span>
        {panel.error?.message || "This panel is temporarily unavailable."}
      </span>
      <button onClick={retry} className="font-bold underline">
        Retry panel
      </button>
    </div>
  );
}

function CheckRow({ label, value }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-white/10 py-2.5 last:border-0">
      <span className="text-sm text-slate-300">
        {ownerTerms[label] || label}
      </span>
      <Pill value={ownerStatus(value)} />
    </div>
  );
}

function HealthRow({ label, value, detail }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-white/10 py-3 last:border-0">
      <div>
        <p className="font-bold">{label}</p>
        {detail && <p className="mt-1 text-xs text-slate-400">{detail}</p>}
      </div>
      <Pill value={value} />
    </div>
  );
}

export default function OperationsPage() {
  const loadRequest = useRef(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState({});
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState(null);
  const [searching, setSearching] = useState(false);
  const [socialPosts, setSocialPosts] = useState({ items: [], total: 0, hasMore: false });
  const [socialStatus, setSocialStatus] = useState("ALL");
  const [socialLoading, setSocialLoading] = useState(true);
  const [socialError, setSocialError] = useState("");
  const [sport, setSport] = useState("ALL");

  const load = useCallback(async () => {
    const requestId = ++loadRequest.current;
    setLoading(true);
    setError("");
    try {
      const result = await api.internal.operations();
      if (requestId === loadRequest.current) setData(result);
    } catch (exception) {
      if (requestId === loadRequest.current) setError(exception.message);
    } finally {
      if (requestId === loadRequest.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 60000);
    return () => clearInterval(id);
  }, [load]);

  const loadSocialPosts = useCallback(async ({ append = false } = {}) => {
    setSocialLoading(true);
    setSocialError("");
    try {
      const offset = append ? socialPosts.items.length : 0;
      const result = await api.internal.socialPosts({ limit: 25, offset, status: socialStatus });
      setSocialPosts((current) => ({
        ...result,
        items: append ? [...current.items, ...result.items] : result.items,
      }));
    } catch (exception) {
      setSocialError(exception.message);
    } finally {
      setSocialLoading(false);
    }
  }, [socialPosts.items.length, socialStatus]);

  useEffect(() => {
    loadSocialPosts();
  }, [socialStatus]); // eslint-disable-line react-hooks/exhaustive-deps

  const runAction = useCallback(
    async (key, request) => {
      setAction((current) => ({ ...current, [key]: { state: "RUNNING" } }));
      try {
        const result = await request();
        setAction((current) => ({
          ...current,
          [key]: { state: "SUCCEEDED", at: result.completedAt },
        }));
        await load();
      } catch (exception) {
        setAction((current) => ({
          ...current,
          [key]: { state: "FAILED", error: exception.message },
        }));
      }
    },
    [load],
  );

  const submitSearch = useCallback(
    async (event) => {
      event.preventDefault();
      if (query.trim().length < 2) return;
      setSearching(true);
      try {
        setSearch(await api.internal.operationsSearch(query.trim()));
      } catch (exception) {
        setSearch({ error: exception.message });
      } finally {
        setSearching(false);
      }
    },
    [query],
  );

  const summary = data?.summary || {};
  const model = data?.modelOperations || {};
  const health = data?.dataHealth || {};
  const automation = data?.automation || {};
  const filteredGames = useMemo(
    () => (sport === "NBA" ? [] : data?.games || []),
    [data, sport],
  );

  if (loading && !data)
    return (
      <main
        className="mx-auto min-h-screen max-w-7xl px-6 py-16"
        aria-live="polite"
      >
        Loading Command Center…
      </main>
    );
  if (error && !data)
    return (
      <main className="mx-auto min-h-screen max-w-3xl px-6 py-16">
        <h1 className="text-4xl font-black">Command Center</h1>
        <div
          role="alert"
          className="mt-6 rounded-2xl border border-rose-400/30 bg-rose-400/10 p-5 text-rose-100"
        >
          {error}
        </div>
        <button onClick={load} className="btn btn-glass mt-4">
          Retry
        </button>
      </main>
    );

  return (
    <main className="mx-auto min-h-screen max-w-[1600px] px-4 pb-24 pt-10 sm:px-6">
      <header className="flex flex-wrap items-end justify-between gap-5">
        <div>
          <p className="text-sm font-black uppercase tracking-[.22em] text-cyan-300">
            SmartBetSports Operations
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <h1 className="text-4xl font-black tracking-[-.055em] md:text-6xl">
              Command Center
            </h1>
            <Pill value={ownerStatus(data?.systemReadiness?.status)} />
          </div>
          <p className="mt-3 max-w-3xl text-slate-300">
            Live games, predictions, results, system health, and social activity.
          </p>
          <p className="mt-2 text-xs text-slate-500">Last updated {fmt(data?.generatedAt)}</p>
          <div className="mt-4 flex flex-wrap gap-2" aria-label="Sport filter">
            {["ALL", "NFL", "NBA"].map((value) => (
              <button key={value} onClick={() => setSport(value)} className={`rounded-full px-3 py-1.5 text-xs font-black ${sport === value ? "bg-cyan-300 text-slate-950" : "bg-white/5 text-slate-300"}`}>{value}</button>
            ))}
            <span className="rounded-full bg-white/5 px-3 py-1.5 text-xs text-slate-400">
              {data?.context?.league} · {data?.context?.season} {data?.context?.seasonType} · Week {data?.context?.week}
            </span>
          </div>
        </div>
        <button
          onClick={() => runAction("all", api.internal.refreshOperations)}
          disabled={action.all?.state === "RUNNING"}
          className="btn btn-glass flex items-center gap-2"
        >
          <RefreshCw
            size={16}
            className={action.all?.state === "RUNNING" ? "animate-spin" : ""}
          />
          {action.all?.state === "RUNNING"
            ? "Refreshing…"
            : action.all?.state === "FAILED"
              ? "Retry refresh"
            : "Refresh data"}
        </button>
      </header>

      {error && (
        <div
          role="alert"
          className="mt-5 rounded-xl border border-amber-300/30 bg-amber-300/10 p-4 text-sm text-amber-100"
        >
          Latest dashboard refresh failed; last-known-good data remains visible.{" "}
          {error}
        </div>
      )}
      {action.all?.state === "FAILED" && (
        <div
          role="alert"
          className="mt-5 rounded-xl border border-rose-400/30 bg-rose-400/10 p-4 text-sm text-rose-100"
        >
          Refresh failed: {action.all.error}
        </div>
      )}

      <section
        aria-label="Operations summary"
        className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6"
      >
        <Metric
          label="Games"
          value={summary.gamesReady ?? 0}
          detail="active"
          icon={CheckCircle2}
        />
        <Metric
          label="Predictions"
          value={summary.predictionsReady ?? 0}
          detail="ready"
          icon={Workflow}
        />
        <Metric
          label="Final results"
          value={summary.finalResults ?? 0}
          detail="updated"
          icon={Clock3}
        />
        <Metric
          label="Issues"
          value={summary.needsAttention ?? 0}
          detail={summary.needsAttention === 1 ? "warning" : "warnings"}
          icon={Siren}
        />
        <Metric
          label="Database"
          value={<Pill value={ownerStatus(summary.databaseHealth)} />}
          detail="system data"
          icon={Database}
        />
        <Metric
          label="Data Provider"
          value={<Pill value={summary.providerHealth === "READY" ? "CONNECTED" : summary.providerHealth} />}
          detail="game updates"
          icon={Activity}
        />
      </section>

      <section
        id="readiness"
        className="mt-8 grid gap-4 xl:grid-cols-[.8fr_1.2fr]"
      >
        <GlowCard className="p-6">
          <h2 className="text-2xl font-black">System Readiness</h2>
          <p className="mt-1 text-sm text-slate-400">{summary.needsAttention ? `${summary.needsAttention} item needs attention` : "Everything is ready"}</p>
          <div className="mt-5">
            {Object.entries(data?.systemReadiness?.checks || {}).map(
              ([label, value]) => (
                <CheckRow key={label} label={label} value={value} />
              ),
            )}
          </div>
        </GlowCard>
        <GlowCard className="p-6">
          <div className="flex items-center gap-2">
            <AlertTriangle
              size={20}
              className={
                summary.needsAttention ? "text-amber-300" : "text-emerald-300"
              }
            />
            <h2 className="text-2xl font-black">Needs Attention</h2>
          </div>
          <div className="mt-5 space-y-3">
            {data?.issues?.length ? (
              data.issues.map((issue, index) => {
                const [title, message] = issueCopy(issue);
                return (
                <div
                  key={`${issue.entityId}-${issue.category}-${index}`}
                  className="rounded-xl border border-white/10 bg-white/[.035] p-4"
                >
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <p className="font-bold">{title}</p>
                    <Pill value={issue.severity} />
                  </div>
                  <p className="mt-2 text-sm text-slate-300">{message}</p>
                  <details className="mt-2 text-xs text-slate-500"><summary className="cursor-pointer">View details</summary><p className="mt-2">{issue.summary} · {fmt(issue.detectedAt)}</p></details>
                  <div className="mt-3 flex gap-3">
                    {issue.action === "RECONCILE" && (
                      <button
                        disabled={action[issue.entityId]?.state === "RUNNING"}
                        onClick={() =>
                          runAction(issue.entityId, () =>
                            api.internal.reconcileOperationGame(issue.entityId),
                          )
                        }
                        className="text-sm font-bold text-cyan-300"
                      >
                        {action[issue.entityId]?.state === "RUNNING"
                          ? "Reconciling…"
                          : "Reconcile game"}
                      </button>
                    )}
                    {issue.action === "VIEW_PREDICTION" && (
                      <Link
                        className="text-sm font-bold text-cyan-300"
                        href={`/internal/games/${issue.entityId}`}
                      >
                        Inspect prediction
                      </Link>
                    )}
                  </div>
                </div>
                );
              })
            ) : (
              <div className="rounded-2xl border border-emerald-400/20 bg-emerald-400/[.07] p-5">
                <p className="font-bold text-emerald-200">
                  No issues
                </p>
                <p className="mt-1 text-sm text-slate-300">
                  Games, predictions, and updates are running normally.
                </p>
              </div>
            )}
          </div>
        </GlowCard>
      </section>

      <section id="games" className="mt-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h2 className="text-3xl font-black">Games</h2>
            <p className="mt-1 text-sm text-slate-400">
              View predictions, scores, stats, and updates for the current slate.
            </p>
          </div>
          <form onSubmit={submitSearch} className="flex gap-2">
            <label className="relative">
              <Search
                className="absolute left-3 top-2.5 text-slate-500"
                size={16}
              />
              <input
                aria-label="Search games, teams, players, predictions, and issues"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="rounded-xl border border-white/10 bg-white/5 py-2 pl-9 pr-3 text-sm"
                placeholder="Search operations"
              />
            </label>
            <button
              disabled={searching || query.trim().length < 2}
              className="btn btn-glass px-4 py-2"
            >
              {searching ? "Searching…" : "Search"}
            </button>
          </form>
        </div>
        {search && (
          <div className="mt-4 rounded-xl border border-white/10 bg-white/[.035] p-4 text-sm">
            {search.error ? (
              <p role="alert" className="text-rose-200">
                Search failed: {search.error}
              </p>
            ) : (
              <>
                <p className="font-bold">Search results</p>
                <p className="mt-1 text-slate-400">
                  {search.games?.length || 0} games ·{" "}
                  {search.teams?.length || 0} teams ·{" "}
                  {search.players?.length || 0} players ·{" "}
                  {search.issues?.length || 0} issues
                </p>
                {search.games?.map((game) => (
                  <Link
                    key={game.id}
                    href={`/internal/games/${game.id}`}
                    className="mr-4 mt-2 inline-block font-bold text-cyan-300"
                  >
                    {game.awayTeam} at {game.homeTeam}
                  </Link>
                ))}
                {search.errors?.catalog && (
                  <p className="mt-2 text-amber-200">
                    Catalog search unavailable: {search.errors.catalog.message}
                  </p>
                )}
              </>
            )}
          </div>
        )}
        <PanelState panel={data?.panels?.week1} retry={load} />
        <div className="mt-4 overflow-x-auto rounded-2xl border border-white/10 bg-white/[.025]">
          <table className="w-full min-w-[1050px] text-left text-sm">
            <thead className="bg-white/[.04] text-xs uppercase tracking-wider text-slate-400">
              <tr>
                <th className="p-4">Matchup</th>
                <th className="p-4">Time</th>
                <th className="p-4">Status</th>
                <th className="p-4">Prediction</th>
                <th className="p-4">Stats</th>
                <th className="p-4">Updated</th>
                <th className="p-4">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredGames.map((game) => {
                const running = action[game.id]?.state === "RUNNING";
                const final = game.status === "FINAL";
                return (
                  <tr key={game.id} className="border-t border-white/10">
                    <td className="p-4">
                      <p className="font-black">
                        {game.awayTeam} at {game.homeTeam}
                      </p>
                      {game.warnings?.map((warning) => (
                        <p
                          key={warning}
                          className="mt-1 text-xs text-amber-200"
                        >
                          {warning}
                        </p>
                      ))}
                    </td>
                    <td className="p-4">{fmt(game.kickoff)}</td>
                    <td className="p-4">
                      <Pill value={game.status} />
                      {final && (
                        <p className="mt-2 font-bold">
                          {game.awayScore}–{game.homeScore}
                        </p>
                      )}
                    </td>
                    <td className="p-4">
                      <Pill value={game.predictionStatus} />
                      {game.prediction && <p className="mt-2 text-xs text-slate-400">{game.prediction.winner} · {(Number(game.prediction.probability) * 100).toFixed(1)}%</p>}
                    </td>
                    <td className="p-4">
                      <Pill value={game.statsStatus} />
                    </td>
                    <td className="p-4">
                      <Pill value={game.freshness} />
                      <p className="mt-2 text-xs text-slate-500">
                        {fmt(game.lastRefresh)}
                      </p>
                    </td>
                    <td className="p-4">
                      <div className="flex flex-wrap gap-3">
                        <Link
                          href={`/internal/games/${game.id}`}
                          className="font-bold text-cyan-300"
                        >
                          {final
                            ? game.grade
                              ? "View evaluation"
                              : "View final & comparison"
                            : game.predictionStatus === "READY" ? "View Prediction" : "View Game"}
                        </Link>
                        <button
                          disabled={running}
                          onClick={() =>
                            runAction(game.id, () =>
                              game.freshness === "STALE"
                                ? api.internal.reconcileOperationGame(game.id)
                                : api.internal.refreshOperationGame(game.id),
                            )
                          }
                          className="font-bold text-slate-200 disabled:text-slate-500"
                        >
                          {running
                            ? "Refreshing…"
                            : action[game.id]?.state === "FAILED"
                              ? "Retry"
                            : game.freshness === "STALE"
                              ? final ? "Sync result" : "Sync game"
                              : final ? "Refresh final stats" : "Refresh"}
                        </button>
                      </div>
                      {action[game.id]?.state === "FAILED" && (
                        <p role="alert" className="mt-2 text-xs text-rose-200">
                          {action[game.id].error}
                        </p>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!filteredGames.length && (
            <p className="p-6 text-sm text-slate-400">
              No active {sport === "ALL" ? "" : sport} games are available.
            </p>
          )}
        </div>
      </section>

      <section className="mt-8 grid gap-4 xl:grid-cols-2">
        <GlowCard id="model-operations" className="p-6">
          <div className="flex items-center justify-between">
            <h2 className="text-2xl font-black">Predictions</h2>
            <Pill value="ACTIVE" />
          </div>
          <dl className="mt-5 grid gap-4 sm:grid-cols-2">
            {[
              ["Current Model", modelName(model.productionModel)],
              ["Latest Run", fmt(model.lastRun)],
              ["Games Covered", model.gamesCovered],
              ["Predictions Ready", model.predictionsGenerated],
              ["Missing", model.missingPredictions],
              ["Errors", model.failedPredictions],
            ].map(([label, value]) => (
              <div key={label} className="rounded-xl bg-white/[.04] p-4">
                <dt className="text-xs uppercase tracking-wider text-slate-400">
                  {label}
                </dt>
                <dd className="mt-2 break-all font-bold">{value ?? EMPTY}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-5 flex flex-wrap gap-4">
            <a href="#games" className="font-bold text-cyan-300">
              View Predictions
            </a>
            {model.missingPredictions > 0 ? (
              <span className="text-sm text-amber-200">
                Some predictions are missing. Open the game list to review them.
              </span>
            ) : (
              <span className="text-sm text-emerald-200">
                Current predictions are ready.
              </span>
            )}
          </div>
        </GlowCard>
        <GlowCard id="data-health" className="p-6">
          <h2 className="text-2xl font-black">System Health</h2>
          <PanelState panel={data?.panels?.database} retry={load} />
          <div className="mt-4">
            <HealthRow
              label="Database"
              value={ownerStatus(health.database?.status)}
              detail="Connected"
            />
            <HealthRow
              label="Schedule"
              value={health.scheduleStore?.status}
              detail={`${health.scheduleStore?.gameCount ?? 0} active games`}
            />
            <HealthRow
              label="Game Updates"
              value={health.gameStatusService?.status}
            />
            <HealthRow
              label="Predictions"
              value={
                health.predictionStore?.data?.status ||
                health.predictionStore?.status
              }
              detail={model.missingPredictions ? `${model.missingPredictions} missing` : "Ready"}
            />
            <HealthRow
              label="Game Stats"
              value={health.playerStatsStore?.status}
              detail="Pending is expected before final games"
            />
            <HealthRow
              label="Background Jobs"
              value={health.worker?.status}
              detail={
                health.worker?.heartbeatAt
                  ? `${health.worker.scheduler === "SUPABASE_CRON" ? "Supabase Cron" : "Local worker"} · Updated ${fmt(health.worker.heartbeatAt)}`
                  : "Not available"
              }
            />
            <HealthRow
              label="Data Provider"
              value={health.provider?.status === "READY" ? "CONNECTED" : health.provider?.status}
            />
            <HealthRow
              label="Next Update"
              value={health.nextCheckpoint ? "SCHEDULED" : "NONE"}
              detail={health.nextCheckpoint ? fmt(new Date(health.nextCheckpoint.due * 1000).toISOString()) : "No pending capture"}
            />
          </div>
        </GlowCard>
      </section>

      <section className="mt-8 grid gap-4 xl:grid-cols-[.8fr_1.2fr]">
        <GlowCard id="automation" className="p-6">
          <div className="flex items-center justify-between">
            <h2 className="text-2xl font-black">Social Posting</h2>
            <Pill value={automation.publishingBlocked ? "PAUSED" : "READY"} />
          </div>
          <dl className="mt-5">
            <HealthRow
              label="X Account"
              value={automation.accountVerificationComplete ? "VERIFIED" : "NEEDS SETUP"}
            />
            <HealthRow
              label="Dry run"
              value={automation.dryRun ? "ON" : "OFF"}
            />
            <HealthRow
              label="Auto Posting"
              value={automation.autoPublish ? "ON" : "OFF"}
            />
            <HealthRow
              label="Status"
              value={automation.publishingBlocked ? "PAUSED" : "READY"}
            />
            <HealthRow label="Last Post" value={automation.lastAttemptStatus || "NONE"} detail={fmt(automation.lastPublishAttempt)} />
          </dl>
          <Link href="/internal/operations/social" className="btn btn-primary mt-5 inline-flex px-4 py-2">
            Open Social Operations
          </Link>
        </GlowCard>
        <GlowCard className="p-6">
          <div className="flex items-center justify-between">
            <h2 className="text-2xl font-black">Activity History</h2>
            <ShieldCheck className="text-cyan-300" />
          </div>
          <PanelState panel={data?.panels?.actionHistory} retry={load} />
          <div className="mt-5 space-y-3">
            {data?.actionHistory?.length ? (
              data.actionHistory.map((item) => (
                <div
                  key={item.action_id}
                  className="grid gap-2 rounded-xl bg-white/[.04] p-4 md:grid-cols-[1fr_auto]"
                >
                  <div>
                    <p className="font-bold">
                      {item.action.replaceAll("_", " ")}
                    </p>
                    <p className="mt-1 text-xs text-slate-400">
                      {item.actor} · started {fmt(item.started_at)} · completed{" "}
                      {fmt(item.completed_at)}
                    </p>
                    {item.error && (
                      <p className="mt-1 text-xs text-rose-200">{item.error}</p>
                    )}
                  </div>
                  <Pill value={item.result} />
                </div>
              ))
            ) : (
              <p className="rounded-xl bg-white/[.04] p-4 text-sm text-slate-400">
                No owner actions have been recorded yet.
              </p>
            )}
          </div>
        </GlowCard>
      </section>

      <section className="mt-8">
        <GlowCard className="p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h2 className="text-2xl font-black">X Post History</h2>
              <p className="mt-1 text-sm text-slate-400">
                Recent posts created by SmartBetSports.
              </p>
            </div>
            <label className="text-xs font-black uppercase tracking-wider text-slate-400">
              Status
              <select
                aria-label="Filter X post history by status"
                value={socialStatus}
                onChange={(event) => setSocialStatus(event.target.value)}
                className="ml-3 rounded-xl border border-white/10 bg-slate-950 px-3 py-2 text-sm text-white"
              >
                {['ALL', 'PUBLISHED', 'DRAFT', 'PUBLISHING', 'FAILED', 'UNKNOWN'].map((value) => (
                  <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="mt-5 flex items-center justify-between text-sm text-slate-400">
            <span>{socialPosts.total} SmartBets record{socialPosts.total === 1 ? '' : 's'}</span>
            <button onClick={() => loadSocialPosts()} disabled={socialLoading} className="font-bold text-cyan-300 disabled:text-slate-500">
              {socialLoading ? 'Loading…' : 'Refresh history'}
            </button>
          </div>
          {socialError && (
            <div role="alert" className="mt-4 rounded-xl border border-rose-400/30 bg-rose-400/10 p-4 text-sm text-rose-100">
              Social history unavailable: {socialError}{' '}
              <button onClick={() => loadSocialPosts()} className="font-bold underline">Retry</button>
            </div>
          )}
          <div className="mt-4 space-y-3">
            {socialPosts.items.map((post) => (
              <article key={post.post_id} className="rounded-xl border border-white/10 bg-white/[.035] p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="text-xs font-black uppercase tracking-wider text-slate-400">{post.category.replaceAll("_", " ")} · {post.day_key}</p>
                  <Pill value={post.status} />
                </div>
                <p className="mt-3 line-clamp-3 whitespace-pre-wrap text-sm leading-6 text-slate-200">{post.content}</p>
                <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-slate-500">
                  <span>{fmt(post.published_at || post.generated_at)}</span>
                  {post.failure_reason && <span className="text-rose-200">Post failed</span>}
                  {post.xUrl && (
                    <a href={post.xUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-bold text-cyan-300">
                      View on X <ExternalLink size={13} />
                    </a>
                  )}
                </div>
              </article>
            ))}
            {!socialLoading && !socialError && !socialPosts.items.length && (
              <p className="rounded-xl bg-white/[.04] p-4 text-sm text-slate-400">
                No SmartBets-managed posts match this status. Posts made manually on X are not imported.
              </p>
            )}
          </div>
          {socialPosts.hasMore && (
            <button onClick={() => loadSocialPosts({ append: true })} disabled={socialLoading} className="btn btn-glass mt-4 px-4 py-2">
              {socialLoading ? 'Loading…' : 'Load more'}
            </button>
          )}
        </GlowCard>
      </section>

    </main>
  );
}
