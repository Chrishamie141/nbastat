'use client';

import TeamLogo from '@/components/teams/TeamLogo';

function validProbability(value) {
  const probability = Number(value);
  return Number.isFinite(probability) && probability >= 0 && probability <= 1
    ? probability
    : null;
}

export function matchupProbabilities(game) {
  const explicitHome = validProbability(game?.homeWinProbability);
  const explicitAway = validProbability(game?.awayWinProbability);
  if (explicitHome != null && explicitAway != null && Math.abs(explicitHome + explicitAway - 1) <= 0.001) {
    return {home: explicitHome, away: explicitAway};
  }

  const selected = validProbability(game?.winProbability);
  if (selected == null) return null;
  if (game?.winner === game?.home_team) return {home: selected, away: 1 - selected};
  if (game?.winner === game?.away_team) return {home: 1 - selected, away: selected};
  return null;
}

function numericScore(value) {
  if (value === null || value === undefined || value === '') return null;
  const score = Number(value);
  return Number.isFinite(score) ? score : null;
}

export function matchupResult(game) {
  const status = String(game?.status || '').toLowerCase();
  const final = ['final', 'final-ot', 'completed'].includes(status);
  const live = ['live', 'halftime'].includes(status);
  const awayScore = numericScore(game?.away_score ?? game?.awayScore);
  const homeScore = numericScore(game?.home_score ?? game?.homeScore);
  const available = awayScore !== null && homeScore !== null;
  const actualWinner = !final || !available || awayScore === homeScore
    ? null
    : awayScore > homeScore ? game?.away_team : game?.home_team;
  return {status, final, live, available, awayScore, homeScore, actualWinner};
}

function TeamSide({abbreviation,name,label,probability,score,winner,reverse,size}) {
  const team = {league: 'nfl', abbreviation, name: name || abbreviation};
  return <div className={`flex min-w-0 items-center gap-3 ${reverse ? 'flex-row-reverse text-right' : ''}`}>
    <TeamLogo team={team} size={size}/>
    <div className="min-w-0">
      <p className="text-[10px] font-bold uppercase tracking-[.16em] text-slate-300">{label}</p>
      <p className={`truncate text-lg font-black ${winner ? 'text-cyan-100' : 'text-slate-100'}`}>{abbreviation}</p>
      {score != null
        ? <p className={`text-2xl font-black tabular-nums ${winner ? 'text-cyan-100' : 'text-white'}`}>{score}</p>
        : probability != null
          ? <p className="text-xs tabular-nums text-slate-300">{(probability * 100).toFixed(1)}%</p>
          : null}
    </div>
  </div>;
}

export default function NflMatchup({game,size=46,showProbabilities=true,showFinalScore=false,className=''}) {
  const result = matchupResult(game);
  const showScore = showFinalScore && (result.final || result.live);
  const probabilities = showProbabilities && !showScore ? matchupProbabilities(game) : null;
  const awayWinner = showScore && result.available ? result.actualWinner === game.away_team : game.winner === game.away_team;
  const homeWinner = showScore && result.available ? result.actualWinner === game.home_team : game.winner === game.home_team;
  return <div className={className}>
    {showScore ? <p className="mb-2 text-center text-[11px] font-black uppercase tracking-[.2em] text-slate-300">
      {result.final ? 'FINAL' : result.status}
    </p> : null}
    <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-3">
      <TeamSide abbreviation={game.away_team} name={game.away_name} label="Away" probability={probabilities?.away} score={showScore ? result.awayScore : null} winner={awayWinner} size={size}/>
      <span className="text-xs font-bold uppercase tracking-[.18em] text-slate-300">at</span>
      <TeamSide abbreviation={game.home_team} name={game.home_name} label="Home" probability={probabilities?.home} score={showScore ? result.homeScore : null} winner={homeWinner} reverse size={size}/>
    </div>
    {showFinalScore && result.final && !result.available
      ? <p className="mt-3 text-center text-sm font-semibold text-amber-100">Final score unavailable</p>
      : null}
  </div>;
}
