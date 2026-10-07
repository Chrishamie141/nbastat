'use client';

const FINAL = new Set(['final', 'final-ot', 'completed']);
const LIVE = new Set(['live', 'halftime']);

const teamLabel = (team) => team?.abbreviation || team?.name || team || 'TBD';
const numericScore = (value) => value !== null && value !== undefined && Number.isFinite(Number(value));

export function scorePresentation(game) {
  const status = String(game?.status || '').toLowerCase();
  const final = FINAL.has(status);
  const live = LIVE.has(status);
  const awayScore = numericScore(game?.awayScore ?? game?.away_score) ? Number(game?.awayScore ?? game?.away_score) : null;
  const homeScore = numericScore(game?.homeScore ?? game?.home_score) ? Number(game?.homeScore ?? game?.home_score) : null;
  const available = awayScore !== null && homeScore !== null;
  const winner = !final || !available || awayScore === homeScore
    ? null
    : awayScore > homeScore ? 'away' : 'home';
  return {final, live, available, awayScore, homeScore, winner};
}

export default function GameScoreboard({game, modelPick = null, predictionResult = null, compact = false}) {
  const score = scorePresentation(game);
  if (!score.final && !score.live) return null;
  const away = game?.awayTeam || {abbreviation: game?.away_team, name: game?.away_name};
  const home = game?.homeTeam || {abbreviation: game?.home_team, name: game?.home_name};
  const label = score.final ? 'FINAL' : String(game.status).toUpperCase();
  return <div
    aria-label={`${label} score`}
    aria-live={score.live ? 'polite' : undefined}
    className={`${compact ? 'mt-3' : 'mt-4'} rounded-2xl border border-white/10 bg-slate-950/40 p-3`}
    role="group"
  >
    <p className="text-center text-[11px] font-black tracking-[.2em] text-slate-300">{label}</p>
    {score.available ? <div className="mt-2 grid grid-cols-[1fr_auto] gap-x-5 gap-y-1 text-sm tabular-nums">
      <span className={score.winner === 'away' ? 'font-black text-cyan-100' : 'font-semibold text-slate-300'}>{teamLabel(away)}</span>
      <strong className={score.winner === 'away' ? 'text-cyan-100' : 'text-white'}>{score.awayScore}</strong>
      <span className={score.winner === 'home' ? 'font-black text-cyan-100' : 'font-semibold text-slate-300'}>{teamLabel(home)}</span>
      <strong className={score.winner === 'home' ? 'text-cyan-100' : 'text-white'}>{score.homeScore}</strong>
    </div> : score.final ? <p className="mt-2 text-center text-sm text-amber-100">Score unavailable</p> : null}
    {modelPick && score.final && score.available && !predictionResult ? <p className="mt-2 text-center text-xs text-slate-400">Model pick: {modelPick}</p> : null}
    {predictionResult ? <p className="mt-2 text-center text-xs font-bold uppercase text-slate-300">Model result: {predictionResult}</p> : null}
  </div>;
}
