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

function TeamSide({abbreviation,name,label,probability,winner,reverse,size}) {
  const team = {league: 'nfl', abbreviation, name: name || abbreviation};
  return <div className={`flex min-w-0 items-center gap-3 ${reverse ? 'flex-row-reverse text-right' : ''}`}>
    <TeamLogo team={team} size={size}/>
    <div className="min-w-0">
      <p className="text-[10px] font-bold uppercase tracking-[.16em] text-slate-300">{label}</p>
      <p className={`truncate text-lg font-black ${winner ? 'text-cyan-100' : 'text-slate-100'}`}>{abbreviation}</p>
      {probability != null ? <p className="text-xs tabular-nums text-slate-300">{(probability * 100).toFixed(1)}%</p> : null}
    </div>
  </div>;
}

export default function NflMatchup({game,size=46,showProbabilities=true,className=''}) {
  const probabilities = showProbabilities ? matchupProbabilities(game) : null;
  return <div className={`grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-3 ${className}`}>
    <TeamSide abbreviation={game.away_team} name={game.away_name} label="Away" probability={probabilities?.away} winner={game.winner===game.away_team} size={size}/>
    <span className="text-xs font-bold uppercase tracking-[.18em] text-slate-300">at</span>
    <TeamSide abbreviation={game.home_team} name={game.home_name} label="Home" probability={probabilities?.home} winner={game.winner===game.home_team} reverse size={size}/>
  </div>;
}
