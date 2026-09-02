"use client";

import { useCallback, useEffect, useState } from "react";
import GlowCard from "@/components/ui/GlowCard";
import { api } from "@/lib/api";

const EMPTY = "—";

function percent(value) {
  return value == null ? EMPTY : `${Number(value).toFixed(1)}%`;
}

function price(value) {
  if (value == null) return EMPTY;
  const numeric = Number(value);
  return numeric > 0 ? `+${numeric}` : String(numeric);
}

function timestamp(value) {
  return value ? new Date(value).toLocaleString() : EMPTY;
}

function Status({ value }) {
  const safe = String(value || "UNKNOWN");
  const good = ["PASS", "HEALTHY", "COMPLETE", "GRADED", "WIN", "BET"].includes(
    safe,
  );
  const bad = [
    "FAIL",
    "QUOTA_EXHAUSTED",
    "AUTH_ERROR",
    "LOSS",
    "BLOCKED_INTEGRITY",
  ].includes(safe);
  return (
    <span
      className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-bold ${
        good
          ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-200"
          : bad
            ? "border-red-400/40 bg-red-400/10 text-red-100"
            : "border-amber-300/30 bg-amber-300/10 text-amber-100"
      }`}
    >
      {safe.replaceAll("_", " ")}
    </span>
  );
}

function Metric({ label, value, detail }) {
  return (
    <GlowCard className="p-4">
      <p className="text-xs font-bold uppercase tracking-[.14em] text-slate-400">
        {label}
      </p>
      <p className="mt-2 break-words text-2xl font-black text-white">
        {value ?? EMPTY}
      </p>
      {detail ? <p className="mt-1 text-xs text-slate-400">{detail}</p> : null}
    </GlowCard>
  );
}

export default function Week3ExperimentPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(
        await api.internal.nflExperiment({
          season: 2026,
          seasonType: "preseason",
          week: 3,
        }),
      );
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return (
      <main
        className="mx-auto min-h-screen max-w-7xl px-4 py-16 sm:px-6"
        aria-live="polite"
      >
        Loading Week 3 experiment…
      </main>
    );
  }
  if (error) {
    return (
      <main className="mx-auto min-h-screen max-w-3xl px-4 py-16 sm:px-6">
        <h1 className="text-4xl font-black">Week 3 experiment</h1>
        <div
          role="alert"
          className="mt-6 rounded-2xl border border-red-400/30 bg-red-400/10 p-5 text-red-100"
        >
          {error}
        </div>
      </main>
    );
  }

  const experiment = data?.experiment || {};
  const metrics = data?.metrics || {};
  const record = metrics.qualifiedBetRecord || {};
  const provider = data?.providerHealth || {};
  const operations = data?.operations || {};
  const segmentRows = [
    ...Object.entries(data?.segments?.byProfile || {}),
    ...Object.entries(data?.segments?.byMarketPosition || {}),
    ...Object.entries(data?.segments?.byEdgeSign || {}),
  ];

  return (
    <main className="mx-auto min-h-screen max-w-[1600px] px-4 pb-28 pt-10 sm:px-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm font-bold uppercase tracking-[.2em] text-cyan-300">
            Internal · NFL experiment
          </p>
          <h1 className="mt-2 text-4xl font-black tracking-[-.05em] md:text-6xl">
            Week 3 integrity & performance
          </h1>
          <p className="mt-3 max-w-3xl text-slate-300">
            Prediction accuracy and price-qualified wagering performance are
            reported independently.
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          className="btn btn-glass focus-ring"
        >
          Refresh operational state
        </button>
      </header>

      <section aria-labelledby="integrity-heading" className="mt-8">
        <div className="flex flex-wrap items-center gap-3">
          <h2 id="integrity-heading" className="text-2xl font-black">
            Experiment integrity
          </h2>
          <Status value={experiment.experimentStatus} />
          <Status value={experiment.hashStatus} />
        </div>
        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
          <Metric label="Canonical week" value={experiment.canonicalWeek} />
          <Metric
            label="Baseline locked"
            value={experiment.baselineLocked ? "YES" : "NO"}
            detail={timestamp(experiment.baselineLockedAt)}
          />
          <Metric
            label="Frozen predictions"
            value={experiment.frozenPredictionCount}
          />
          <Metric label="Games final" value={metrics.finalGameCount} />
          <Metric
            label="Predictions graded"
            value={metrics.predictionsGraded}
          />
          <Metric
            label="Grading"
            value={<Status value={metrics.gradingStatus} />}
          />
        </div>
        <GlowCard className="mt-4 p-5">
          <dl className="grid gap-4 text-sm md:grid-cols-2">
            <div>
              <dt className="text-slate-400">Expected SHA-256</dt>
              <dd className="mt-1 break-all font-mono text-xs text-slate-100">
                {experiment.expectedHash}
              </dd>
            </div>
            <div>
              <dt className="text-slate-400">Current SHA-256</dt>
              <dd className="mt-1 break-all font-mono text-xs text-slate-100">
                {experiment.currentHash}
              </dd>
            </div>
          </dl>
        </GlowCard>
      </section>

      <section aria-labelledby="performance-heading" className="mt-8">
        <h2 id="performance-heading" className="text-2xl font-black">
          Performance
        </h2>
        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
          <Metric
            label="Winner record"
            value={`${metrics.correctPredictions || 0}-${metrics.incorrectPredictions || 0}-${metrics.pushes || 0}`}
            detail="All frozen winner predictions"
          />
          <Metric
            label="Winner accuracy"
            value={percent(metrics.winnerAccuracy)}
          />
          <Metric
            label="Qualified bets"
            value={metrics.qualifiedBetCount}
            detail="Legitimate pregame price required"
          />
          <Metric
            label="Qualified record"
            value={`${record.wins || 0}-${record.losses || 0}-${record.pushes || 0}`}
          />
          <Metric
            label="Net units"
            value={
              metrics.units == null ? EMPTY : Number(metrics.units).toFixed(2)
            }
            detail="1 flat unit risked per wager"
          />
          <Metric
            label="ROI"
            value={percent(metrics.roi)}
            detail="NO_BET excluded"
          />
          <Metric
            label="Market coverage"
            value={`${metrics.marketCoverage?.games || 0}/${metrics.marketCoverage?.total || 0}`}
          />
          <Metric
            label="Closing captured"
            value={metrics.closingPricesCaptured}
          />
        </div>
      </section>

      <section
        aria-labelledby="provider-heading"
        className="mt-8 grid gap-4 lg:grid-cols-2"
      >
        <GlowCard className="p-5">
          <div className="flex items-center justify-between gap-3">
            <h2 id="provider-heading" className="text-xl font-black">
              Odds provider
            </h2>
            <Status value={provider.state} />
          </div>
          <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-slate-400">Last attempt</dt>
              <dd>{timestamp(provider.lastAttempt)}</dd>
            </div>
            <div>
              <dt className="text-slate-400">Last successful retrieval</dt>
              <dd>{timestamp(provider.lastSuccessfulRetrieval)}</dd>
            </div>
            <div>
              <dt className="text-slate-400">Last market timestamp</dt>
              <dd>{timestamp(provider.lastMarketTimestamp)}</dd>
            </div>
            <div>
              <dt className="text-slate-400">Safe error code</dt>
              <dd>{provider.safeErrorCode || EMPTY}</dd>
            </div>
          </dl>
        </GlowCard>
        <GlowCard className="p-5">
          <h2 className="text-xl font-black">Schedule & grading operations</h2>
          <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-slate-400">Last schedule refresh</dt>
              <dd>{timestamp(operations.lastScheduleRefresh)}</dd>
            </div>
            <div>
              <dt className="text-slate-400">Refresh state</dt>
              <dd>{operations.scheduleRefreshState || EMPTY}</dd>
            </div>
            <div>
              <dt className="text-slate-400">Schedule error</dt>
              <dd>{operations.scheduleRefreshError || EMPTY}</dd>
            </div>
            <div>
              <dt className="text-slate-400">Grading error</dt>
              <dd>{operations.gradingError || EMPTY}</dd>
            </div>
          </dl>
        </GlowCard>
      </section>

      <section aria-labelledby="games-heading" className="mt-8">
        <h2 id="games-heading" className="text-2xl font-black">
          Frozen game ledger
        </h2>
        <p className="mt-2 text-sm text-slate-400">
          Horizontal scrolling is available on small screens. Prices are for the
          frozen winner side.
        </p>
        <div
          className="mt-4 overflow-x-auto rounded-2xl border border-white/10 bg-white/[.03] focus:outline focus:outline-2 focus:outline-cyan-300"
          role="region"
          aria-label="Scrollable Week 3 game ledger"
          tabIndex={0}
        >
          <table className="min-w-[1500px] w-full border-collapse text-left text-sm">
            <caption className="sr-only">
              Week 3 frozen predictions, market observations, outcomes, and
              grades
            </caption>
            <thead className="bg-white/[.06] text-xs uppercase tracking-wider text-slate-300">
              <tr>
                {[
                  "Matchup",
                  "Frozen winner",
                  "Probability",
                  "Rating",
                  "First price",
                  "Latest pregame",
                  "Closing",
                  "Edge",
                  "Decision",
                  "Actual score",
                  "Prediction",
                  "Qualified wager",
                  "Units",
                ].map((heading) => (
                  <th key={heading} scope="col" className="px-4 py-3">
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.games.map((game) => (
                <tr
                  key={game.gameId}
                  className="border-t border-white/10 align-top"
                >
                  <th scope="row" className="px-4 py-4 font-bold text-white">
                    {game.matchup || game.gameId}
                    <span className="mt-1 block text-xs font-normal text-slate-400">
                      {game.state}
                    </span>
                  </th>
                  <td className="px-4 py-4">{game.frozenWinner}</td>
                  <td className="px-4 py-4">
                    {percent(Number(game.frozenProbability) * 100)}
                  </td>
                  <td className="px-4 py-4">{game.frozenRating ?? EMPTY}</td>
                  <td className="px-4 py-4">{price(game.firstPrice)}</td>
                  <td className="px-4 py-4">
                    {price(game.latestPregamePrice)}
                  </td>
                  <td className="px-4 py-4">
                    {price(game.closingPrice)}
                    <span className="mt-1 block text-xs text-slate-400">
                      {game.marketHistory.closingStatus}
                    </span>
                  </td>
                  <td className="px-4 py-4">
                    {game.modelEdge == null
                      ? EMPTY
                      : percent(Number(game.modelEdge) * 100)}
                  </td>
                  <td className="px-4 py-4">
                    <Status value={game.betDecision} />
                  </td>
                  <td className="px-4 py-4">{game.actualScore || EMPTY}</td>
                  <td className="px-4 py-4">
                    <Status value={game.predictionResult} />
                  </td>
                  <td className="px-4 py-4">
                    <Status value={game.qualifiedWagerResult} />
                  </td>
                  <td className="px-4 py-4">
                    {game.units == null ? EMPTY : Number(game.units).toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section
        aria-labelledby="calibration-heading"
        className="mt-8 grid gap-4 xl:grid-cols-2"
      >
        <GlowCard className="p-5">
          <div className="flex flex-wrap items-center gap-3">
            <h2 id="calibration-heading" className="text-xl font-black">
              Probability buckets
            </h2>
            <Status value={data.calibration.status} />
          </div>
          <p className="mt-2 text-sm text-slate-400">{data.calibration.note}</p>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-slate-400">
                <tr>
                  <th className="py-2">Bucket</th>
                  <th>Predictions</th>
                  <th>W-L-P</th>
                  <th>Accuracy</th>
                </tr>
              </thead>
              <tbody>
                {data.calibration.buckets.map((bucket) => (
                  <tr key={bucket.range} className="border-t border-white/10">
                    <th className="py-3">{bucket.range}</th>
                    <td>{bucket.predictions}</td>
                    <td>
                      {bucket.wins}-{bucket.losses}-{bucket.pushes}
                    </td>
                    <td>{percent(bucket.accuracy)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlowCard>
        <GlowCard className="p-5">
          <h2 className="text-xl font-black">Evaluation definitions</h2>
          <dl className="mt-4 space-y-4 text-sm">
            {Object.entries(data.definitions).map(([key, value]) => (
              <div key={key}>
                <dt className="font-bold capitalize text-cyan-200">
                  {key.replace(/([A-Z])/g, " $1")}
                </dt>
                <dd className="mt-1 text-slate-300">{value}</dd>
              </div>
            ))}
          </dl>
          <h3 className="mt-6 text-base font-black">Diagnostic segments</h3>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-slate-400">
                <tr>
                  <th className="py-2">Segment</th>
                  <th>Predictions</th>
                  <th>W-L-P</th>
                  <th>Sample</th>
                </tr>
              </thead>
              <tbody>
                {segmentRows.map(([name, segment], index) => (
                  <tr
                    key={`${name}-${index}`}
                    className="border-t border-white/10"
                  >
                    <th className="py-3 capitalize">
                      {name.replace(/([A-Z])/g, " $1")}
                    </th>
                    <td>{segment.predictions}</td>
                    <td>
                      {segment.wins}-{segment.losses}-{segment.pushes}
                    </td>
                    <td>{segment.sampleStatus.replaceAll("_", " ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlowCard>
      </section>
    </main>
  );
}
