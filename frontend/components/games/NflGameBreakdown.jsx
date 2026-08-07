'use client';

import Link from 'next/link';
import { RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import GlowCard from '@/components/ui/GlowCard';
import TeamLogo from '@/components/teams/TeamLogo';
import { api } from '@/lib/api';

const LIVE_STATUSES = new Set(['live', 'halftime']);
const FINAL_STATUSES = new Set(['final', 'final-OT']);

function localDate(iso) {
  const value = new Date(iso);
  return Number.isNaN(value.getTime()) ? 'Kickoff TBD' : new Intl.DateTimeFormat(undefined, { dateStyle: 'full', timeStyle: 'short' }).format(value);
}

function formatNumber(value, digits = 1) {
  return typeof value === 'number' ? value.toFixed(digits).replace(/\.0$/, '') : '—';
}

function intervalLocation(item) {
  const low = item.quantiles?.p10;
  const high = item.quantiles?.p90;
  if (typeof low !== 'number' || typeof high !== 'number' || typeof item.actual !== 'number') return 'Interval unavailable';
  if (item.actual < low) return 'Below p10';
  if (item.actual > high) return 'Above p90';
  return 'Inside p10–p90';
}

function title(value) {
  return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export default function NflGameBreakdown({ gameId }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState('');
  const [refreshError, setRefreshError] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async ({ manual = false, silent = false } = {}) => {
    if (!silent) setRefreshing(true);
    setRefreshError('');
    try {
      const next = manual ? await api.nfl.refreshGame(gameId) : await api.nfl.game(gameId);
      setDetail(next);
      setError('');
    } catch (requestError) {
      const message = requestError.message || 'Game breakdown unavailable.';
      if (manual || silent) setRefreshError(message);
      else setError(message);
    } finally {
      if (!silent) setRefreshing(false);
    }
  }, [gameId]);

  useEffect(() => {
    load();
  }, [gameId]); // eslint-disable-line react-hooks/exhaustive-deps

  const status = detail?.game?.status;
  useEffect(() => {
    const interval = LIVE_STATUSES.has(status) ? 20000 : ['scheduled', 'pregame'].includes(status) ? 60000 : null;
    if (!interval) return undefined;
    const timer = window.setInterval(() => load({ silent: true }), interval);
    return () => window.clearInterval(timer);
  }, [load, status]);

  if (!detail && error) return <main className="mx-auto min-h-screen max-w-5xl px-6 py-16"><GlowCard className="p-8"><p className="text-lg font-semibold text-red-100">{error}</p><div className="mt-5 flex gap-3"><button className="btn btn-primary" onClick={() => load()}>Try again</button><Link className="btn btn-glass" href="/dashboard">Back to dashboard</Link></div></GlowCard></main>;
  if (!detail) return <GameSkeleton />;

  const { game, lifecycle, teamContext, predictions, actuals, comparison, sources, reconciliation } = detail;
  const hasScore = (LIVE_STATUSES.has(game.status) || FINAL_STATUSES.has(game.status)) && (game.awayScore != null || game.homeScore != null);
  return <main className="mx-auto min-h-screen max-w-7xl px-6 py-12 md:py-16">
    <div className="mb-5"><Link href="/dashboard" className="text-sm font-semibold text-cyan-200 hover:text-cyan-100">← Back to dashboard</Link></div>
    <GlowCard className="overflow-hidden p-6 md:p-8">
      <div className="flex flex-wrap items-start justify-between gap-4"><div className="flex flex-wrap gap-2"><StatusBadge status={game.status} /><span className="tag capitalize">NFL {title(game.seasonPhase)} · Week {game.week ?? 'TBD'}</span></div><button type="button" onClick={() => load({ manual: true })} disabled={refreshing} className="btn btn-glass disabled:cursor-wait disabled:opacity-60"><RefreshCw size={16} className={refreshing ? 'animate-spin' : ''} />{refreshing ? 'Refreshing…' : 'Refresh game'}</button></div>
      <div className="mt-8 grid items-center gap-6 md:grid-cols-[1fr_auto_1fr]">
        <HeaderTeam team={game.awayTeam} side="Away" score={hasScore ? game.awayScore : null} />
        <div className="text-center"><p className="text-xs font-bold uppercase tracking-[.25em] text-slate-500">{hasScore ? 'Score' : 'Matchup'}</p><p className="mt-2 text-xl font-black text-slate-300">AT</p></div>
        <HeaderTeam team={game.homeTeam} side="Home" score={hasScore ? game.homeScore : null} align="right" />
      </div>
      <div className="mt-8 grid gap-3 border-t border-white/10 pt-5 text-sm text-slate-300 md:grid-cols-2 lg:grid-cols-4"><Meta label="Local kickoff" value={localDate(game.startTimeUtc)} /><Meta label="Venue" value={[game.venue, game.city].filter(Boolean).join(' · ') || 'TBD'} /><Meta label="Last successful refresh" value={lifecycle.fetchedAt ? localDate(lifecycle.fetchedAt) : 'Unavailable'} /><Meta label="Data freshness" value={`${title(lifecycle.freshnessState)}${lifecycle.sourceAgeSeconds != null ? ` · source ${lifecycle.sourceAgeSeconds}s old` : ''}`} /></div>
      <p className="mt-4 text-xs text-slate-500">Canonical game ID: {game.canonicalId} · Source: {sources.scheduleStatusScore}</p>
      {refreshError ? <p role="alert" className="mt-4 rounded-xl border border-red-300/20 bg-red-300/10 p-3 text-sm text-red-100">Refresh failed: {refreshError}. Existing game data remains visible.</p> : null}
      {detail.refresh?.coalesced ? <p className="mt-4 rounded-xl bg-cyan-300/10 p-3 text-sm text-cyan-100">A recent refresh already completed; this request reused the latest server result.</p> : null}
      {reconciliation?.reasonCodes?.length ? <p className="mt-4 rounded-xl bg-amber-300/10 p-3 text-sm text-amber-100">Lifecycle audit: {reconciliation.reasonCodes.map(title).join(', ')}</p> : null}
    </GlowCard>

    <section className="mt-6" aria-labelledby="pregame-context"><SectionHeading id="pregame-context" eyebrow="PREGAME CONTEXT" title="Team context entering the game" description="Only information available before kickoff belongs here; final-game output is kept separate below." />{teamContext.fallbackUsed ? <p className="mt-4 rounded-xl border border-amber-200/20 bg-amber-200/10 p-3 text-sm text-amber-100">Current {teamContext.requestedSeason} sample is below the {teamContext.minimumSampleSize}-game minimum. Using completed {teamContext.contextSeasonUsed} regular-season context; game results are never backfilled.</p> : null}{teamContext.available ? <div className="mt-4 grid gap-4 md:grid-cols-2">{teamContext.teams.map((team) => <GlowCard key={team.team} className="p-5"><h3 className="text-xl font-bold">{team.team}</h3><p className="mt-1 text-xs text-slate-500">{team.games} prior games · {teamContext.source}</p><dl className="mt-4 grid grid-cols-2 gap-3">{Object.entries(team.metrics || {}).map(([label, value]) => <div key={label} className="rounded-xl bg-white/5 p-3"><dt className="text-xs text-slate-400">{title(label)}</dt><dd className="mt-1 text-lg font-bold">{formatNumber(value)}</dd></div>)}</dl></GlowCard>)}</div> : <Unavailable reason={teamContext.reason} />}</section>

    <section className="mt-10" aria-labelledby="system-a-predictions"><SectionHeading id="system-a-predictions" eyebrow="FROZEN BEFORE KICKOFF" title="System A player-stat predictions" description="Existing versioned outputs only. Refreshing the game never retrains or regenerates predictions." />{predictions.available ? <><div className="mt-4 grid gap-3 text-sm md:grid-cols-3"><MetaCard label="Model" value={predictions.modelVersion} /><MetaCard label="Prediction cutoff" value={localDate(predictions.predictionCutoff)} /><MetaCard label="Research policy" value={predictions.researchPolicyId} /></div><div className="mt-4 grid gap-4">{predictions.groups.map((group) => <PredictionGroup key={group.group} group={group} />)}</div></> : <Unavailable reason={predictions.reason} />}</section>

    <section className="mt-10" aria-labelledby="actual-result"><SectionHeading id="actual-result" eyebrow="ACTUAL GAME RESULT" title={FINAL_STATUSES.has(game.status) ? 'Final team and player statistics' : LIVE_STATUSES.has(game.status) ? 'Live team and player statistics' : 'Game statistics'} description="Provider box-score output is intentionally separated from pregame context and frozen predictions." />{actuals.available ? <div className="mt-4 grid gap-4"><TeamStats rows={actuals.teamStats} />{actuals.playerGroups.map((group) => <ActualGroup key={group.group} group={group} />)}</div> : <Unavailable reason="Official box-score statistics are not available yet." />}</section>

    <section className="mt-10" aria-labelledby="prediction-comparison"><SectionHeading id="prediction-comparison" eyebrow="FINAL REVIEW" title="Prediction versus actual" description="Comparable frozen projections are evaluated only after an authoritative final state." />{comparison.available ? <ComparisonTable items={comparison.items} /> : <Unavailable reason={comparison.reason} />}</section>

    <GlowCard className="mt-10 p-5 text-sm text-slate-400"><h2 className="font-bold text-slate-200">Data provenance</h2><ul className="mt-3 grid gap-2"><li>Schedule, lifecycle, score: {sources.scheduleStatusScore}</li><li>Actual statistics: {sources.boxScore}</li><li>Predictions: {sources.predictions}</li><li>Paid provider contacted: {sources.paidProviderContacted ? 'Yes' : 'No'}</li></ul></GlowCard>
  </main>;
}

function HeaderTeam({ team, side, score, align = 'left' }) {
  return <div className={`flex items-center gap-4 ${align === 'right' ? 'md:flex-row-reverse md:text-right' : ''}`}><TeamLogo team={team} size={76} /><div className="min-w-0"><p className="text-xs font-bold uppercase tracking-[.2em] text-slate-500">{side}</p><h1 className="mt-1 text-2xl font-black md:text-3xl">{team?.name || team?.abbreviation || 'TBD'}</h1>{team?.record ? <p className="mt-1 text-sm text-slate-400">Record {team.record}</p> : null}{score != null ? <p className="mt-1 text-4xl font-black text-cyan-100">{score}</p> : null}</div></div>;
}

function StatusBadge({ status }) {
  const color = LIVE_STATUSES.has(status) ? 'bg-red-400 text-slate-950' : FINAL_STATUSES.has(status) ? 'bg-emerald-300 text-slate-950' : 'bg-cyan-300 text-slate-950';
  return <span className={`rounded-full px-3 py-1 text-xs font-black uppercase tracking-wide ${color}`}>{status === 'final-OT' ? 'Final / OT' : title(status)}</span>;
}

function Meta({ label, value }) { return <div><p className="text-xs font-bold uppercase tracking-wide text-slate-500">{label}</p><p className="mt-1 font-semibold text-slate-200">{value}</p></div>; }
function MetaCard({ label, value }) { return <GlowCard className="p-4"><Meta label={label} value={value || 'Unavailable'} /></GlowCard>; }
function SectionHeading({ id, eyebrow, title: heading, description }) { return <div><p className="text-xs font-black uppercase tracking-[.24em] text-cyan-300">{eyebrow}</p><h2 id={id} className="mt-2 text-3xl font-black tracking-tight">{heading}</h2><p className="mt-2 max-w-3xl text-slate-400">{description}</p></div>; }
function Unavailable({ reason }) { return <GlowCard className="mt-4 p-5 text-slate-300">{reason || 'This panel is currently unavailable.'}</GlowCard>; }

function PredictionGroup({ group }) {
  if (!group.items?.length) return <GlowCard className="p-5"><h3 className="text-xl font-bold">{title(group.group)}</h3><p className="mt-2 text-sm text-slate-500">No frozen predictions in this group.</p></GlowCard>;
  return <GlowCard className="overflow-hidden p-0"><h3 className="px-5 pt-5 text-xl font-bold">{title(group.group)}</h3><div className="mt-3 overflow-x-auto"><table className="w-full min-w-[980px] text-left text-sm"><thead className="border-y border-white/10 bg-white/5 text-xs uppercase text-slate-400"><tr><th className="p-3">Player</th><th className="p-3">Market</th><th className="p-3">Mean</th><th className="p-3">P10 / P25 / P50 / P75 / P90</th><th className="p-3">Variance</th><th className="p-3">History</th><th className="p-3">Stability</th><th className="p-3">Flags</th></tr></thead><tbody>{group.items.map((item) => <tr key={`${item.playerId}-${item.market}`} className="border-b border-white/5"><td className="p-3 font-semibold">{item.playerName}<span className="ml-2 text-xs text-slate-500">{item.team}</span></td><td className="p-3">{title(item.market)}</td><td className="p-3">{formatNumber(item.mean)}</td><td className="p-3">{formatNumber(item.quantiles?.p10)} / {formatNumber(item.quantiles?.p25)} / {formatNumber(item.quantiles?.p50)} / {formatNumber(item.quantiles?.p75)} / {formatNumber(item.quantiles?.p90)}</td><td className="p-3">{formatNumber(item.variance)}</td><td className="p-3">{item.historyDepth ?? '—'} games</td><td className="p-3">{item.stability?.available ? `${title(item.stability.class)}${item.stability.score != null ? ` · ${formatNumber(item.stability.score)}` : ''}` : item.stability?.reason || 'Unavailable'}</td><td className="p-3">{item.unresolvedFlags?.length ? item.unresolvedFlags.join(', ') : 'None reported'}</td></tr>)}</tbody></table></div></GlowCard>;
}

function TeamStats({ rows }) {
  if (!rows?.length) return null;
  return <div className="grid gap-4 md:grid-cols-2">{rows.map((row) => <GlowCard key={row.team} className="p-5"><h3 className="text-xl font-bold">{row.team} team stats</h3><dl className="mt-3 grid grid-cols-2 gap-2">{row.statistics.slice(0, 12).map((stat) => <div key={stat.name} className="rounded-lg bg-white/5 p-2"><dt className="text-xs text-slate-500">{stat.name}</dt><dd className="font-bold">{stat.value ?? '—'}</dd></div>)}</dl></GlowCard>)}</div>;
}

function ActualGroup({ group }) {
  if (!group.items?.length) return null;
  const labels = [...new Set(group.items.flatMap((item) => Object.keys(item.stats || {})))];
  return <GlowCard className="overflow-hidden p-0"><h3 className="px-5 pt-5 text-xl font-bold">{title(group.group)}</h3><div className="mt-3 overflow-x-auto"><table className="w-full min-w-[650px] text-left text-sm"><thead className="border-y border-white/10 bg-white/5 text-xs uppercase text-slate-400"><tr><th className="p-3">Player</th>{labels.map((label) => <th key={label} className="p-3">{label}</th>)}</tr></thead><tbody>{group.items.map((item) => <tr key={`${group.group}-${item.playerId}-${item.team}`} className="border-b border-white/5"><td className="p-3 font-semibold">{item.playerName}<span className="ml-2 text-xs text-slate-500">{item.team} · {item.position}</span></td>{labels.map((label) => <td key={label} className="p-3">{item.stats?.[label] ?? '—'}</td>)}</tr>)}</tbody></table></div></GlowCard>;
}

function ComparisonTable({ items }) {
  return <GlowCard className="mt-4 overflow-hidden p-0"><div className="overflow-x-auto"><table className="w-full min-w-[800px] text-left text-sm"><thead className="border-b border-white/10 bg-white/5 text-xs uppercase text-slate-400"><tr><th className="p-3">Player</th><th className="p-3">Market</th><th className="p-3">Projected p50</th><th className="p-3">P10–P90 interval</th><th className="p-3">Actual</th><th className="p-3">Error vs p50</th><th className="p-3">Interval location</th></tr></thead><tbody>{items.map((item) => <tr key={`${item.playerId}-${item.market}`} className="border-b border-white/5"><td className="p-3 font-semibold">{item.playerName}</td><td className="p-3">{title(item.market)}</td><td className="p-3">{formatNumber(item.quantiles?.p50 ?? item.mean)}</td><td className="p-3">{formatNumber(item.quantiles?.p10)}–{formatNumber(item.quantiles?.p90)}</td><td className="p-3">{formatNumber(item.actual)}</td><td className={`p-3 font-bold ${item.error > 0 ? 'text-emerald-300' : item.error < 0 ? 'text-red-200' : ''}`}>{item.error > 0 ? '+' : ''}{formatNumber(item.error)}</td><td className="p-3">{intervalLocation(item)}</td></tr>)}</tbody></table></div></GlowCard>;
}

function GameSkeleton() {
  return <main className="mx-auto min-h-screen max-w-7xl px-6 py-16"><div className="h-80 animate-pulse rounded-3xl bg-white/10" /><div className="mt-6 grid gap-4 md:grid-cols-2"><div className="h-48 animate-pulse rounded-3xl bg-white/10" /><div className="h-48 animate-pulse rounded-3xl bg-white/10" /></div><p className="sr-only">Loading game breakdown</p></main>;
}
