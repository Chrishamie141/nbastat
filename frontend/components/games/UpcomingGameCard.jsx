'use client';

import Link from 'next/link';
import TeamLogo from '@/components/teams/TeamLogo';

const FINISHED = new Set(['final', 'final-OT']);
const LIVE_STATUSES = new Set(['live', 'halftime']);

function formatGameTime(iso, status) {
  if (['postponed', 'canceled'].includes(status)) return status[0].toUpperCase() + status.slice(1);
  if (status === 'live') return 'Live';
  if (status === 'halftime') return 'Halftime';
  if (FINISHED.has(status)) return status === 'final-OT' ? 'Final / OT' : 'Final';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return 'Time TBD';
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const diff = Math.round((day - start) / 86400000);
  const dayLabel = diff === 0 ? 'Today' : diff === 1 ? 'Tomorrow' : new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric' }).format(date);
  const time = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }).format(date);
  return `${dayLabel} · ${time}`;
}

function phaseLabel(game) {
  const phase = (game.seasonPhase || '').replaceAll('_', ' ');
  const week = game.week ? ` · Week ${game.week}` : '';
  return `${(game.league || '').toUpperCase()} ${phase}${week}`.trim();
}

const meaningful = (reason) => reason && reason !== 'Schedule details available';

export { formatGameTime };

export default function UpcomingGameCard({ game, featured = false }) {
  if (!game) return null;
  const chips = (game.watchReasons || []).filter(meaningful).slice(0, 3);
  const isNfl = game.league === 'nfl';
  const showScore = LIVE_STATUSES.has(game.status) || FINISHED.has(game.status);
  const card = (
    <article className={`h-auto min-h-0 rounded-3xl border p-4 transition ${featured ? 'border-cyan-300/50 bg-cyan-300/10' : 'border-white/10 bg-white/[.04]'} ${isNfl ? 'group-hover:border-cyan-300/60 group-hover:bg-white/[.08]' : ''}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="tag w-fit capitalize">{phaseLabel(game)}</span>
        {game.nationalBroadcast ? <span className="rounded-full bg-white/10 px-2 py-1 text-xs text-slate-200">National broadcast</span> : null}
      </div>
      <div className={`mt-4 grid items-center gap-3 ${featured ? 'md:grid-cols-[1fr_auto_1fr]' : 'sm:grid-cols-[1fr_auto_1fr]'}`}>
        <TeamBlock label="Away" team={game.awayTeam} score={showScore ? game.awayScore : null} size={featured ? 58 : 46} />
        <span className="text-center text-sm font-semibold uppercase tracking-wide text-slate-500">at</span>
        <TeamBlock label="Home" team={game.homeTeam} score={showScore ? game.homeScore : null} size={featured ? 58 : 46} />
      </div>
      <div className="mt-4 border-t border-white/10 pt-3">
        <div className="flex items-center justify-between gap-3">
          <p className="font-semibold text-cyan-50">{formatGameTime(game.startTimeUtc, game.status)}</p>
          {isNfl ? <span className="text-xs font-semibold text-cyan-200">View breakdown →</span> : null}
        </div>
        {game.venue ? <p className="mt-1 text-sm text-slate-300">{game.venue}</p> : null}
        {game.broadcast?.length > 0 ? <p className="mt-1 text-sm text-slate-400">Broadcast: {game.broadcast.join(', ')}</p> : null}
      </div>
      {chips.length > 0 ? <ul className="mt-3 flex flex-wrap gap-2">{chips.map((reason) => <li key={reason} className="rounded-full bg-white/10 px-2 py-1 text-xs text-slate-200">{reason}</li>)}</ul> : null}
    </article>
  );
  if (!isNfl) return card;
  const label = `${game.awayTeam?.name || 'Away team'} at ${game.homeTeam?.name || 'Home team'} game breakdown`;
  return <Link className="group block rounded-3xl focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300" href={`/nfl/games/${game.id}`} aria-label={label}>{card}</Link>;
}

function TeamBlock({ label, team, score, size }) {
  return <div className="flex min-w-0 items-center gap-3"><TeamLogo team={team} size={size} /><div className="min-w-0"><div className="flex items-baseline gap-2"><p className="truncate font-bold text-slate-100">{team?.name || team?.abbreviation || 'TBD'}</p>{score != null ? <strong className="text-xl text-white">{score}</strong> : null}</div><p className="text-xs uppercase tracking-wide text-slate-500">{label}</p></div></div>;
}
