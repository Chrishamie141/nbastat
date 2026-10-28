'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import GlowCard from '@/components/ui/GlowCard';
import TeamLogo from '@/components/teams/TeamLogo';
import { api } from '@/lib/api';

const FINAL_STATUSES = new Set(['final', 'final-ot', 'completed']);
const NBA_ALIASES = { GS: 'GSW', NO: 'NOP', NY: 'NYK', SA: 'SAS', UTAH: 'UTA' };
const NBA_TEAM_NAMES = {
  ATL: 'Atlanta Hawks', BOS: 'Boston Celtics', BKN: 'Brooklyn Nets', CHA: 'Charlotte Hornets',
  CHI: 'Chicago Bulls', CLE: 'Cleveland Cavaliers', DAL: 'Dallas Mavericks', DEN: 'Denver Nuggets',
  DET: 'Detroit Pistons', GSW: 'Golden State Warriors', HOU: 'Houston Rockets', IND: 'Indiana Pacers',
  LAC: 'LA Clippers', LAL: 'Los Angeles Lakers', MEM: 'Memphis Grizzlies', MIA: 'Miami Heat',
  MIL: 'Milwaukee Bucks', MIN: 'Minnesota Timberwolves', NOP: 'New Orleans Pelicans', NYK: 'New York Knicks',
  OKC: 'Oklahoma City Thunder', ORL: 'Orlando Magic', PHI: 'Philadelphia 76ers', PHX: 'Phoenix Suns',
  POR: 'Portland Trail Blazers', SAC: 'Sacramento Kings', SAS: 'San Antonio Spurs', TOR: 'Toronto Raptors',
  UTA: 'Utah Jazz', WSH: 'Washington Wizards',
};

function title(value) {
  return String(value || '')
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function localDate(iso) {
  const value = new Date(iso);
  return Number.isNaN(value.getTime())
    ? 'Tipoff TBD'
    : new Intl.DateTimeFormat(undefined, {
        dateStyle: 'full',
        timeStyle: 'short',
      }).format(value);
}

function teamDetails(abbreviation) {
  const displayAbbreviation = String(abbreviation || 'TBD').toUpperCase();
  const canonicalAbbreviation = NBA_ALIASES[displayAbbreviation] || displayAbbreviation;
  return {
    league: 'nba',
    abbreviation: canonicalAbbreviation,
    name: NBA_TEAM_NAMES[canonicalAbbreviation] || displayAbbreviation,
  };
}

function probabilityNumber(value) {
  if (value == null || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function predictionOutlook(game, prediction) {
  const probability = probabilityNumber(prediction?.modelProbability);
  if (probability == null) return null;
  const opponent = prediction.predictedWinner === game.homeTeam
    ? game.awayTeam
    : game.homeTeam;
  return {
    probability,
    opponent,
    advantage: Math.max(0, (probability - (1 - probability)) * 100),
  };
}

function Meta({ label, value }) {
  return <div>
    <p className="text-xs font-bold uppercase tracking-wide text-slate-500">{label}</p>
    <p className="mt-1 font-semibold text-slate-200">{value}</p>
  </div>;
}

function HeaderTeam({ abbreviation, side, probability, score, align = 'left' }) {
  const team = teamDetails(abbreviation);
  const probabilityValue = probabilityNumber(probability);
  return <div className={`flex items-center gap-4 ${align === 'right' ? 'md:flex-row-reverse md:text-right' : ''}`}>
    <TeamLogo team={team} size={76} />
    <div className="min-w-0">
      <p className="text-xs font-bold uppercase tracking-[.2em] text-slate-500">{side}</p>
      <h2 className="mt-1 text-2xl font-black tracking-[-.03em] text-slate-50 lg:text-3xl">{team.name}</h2>
      {score != null
        ? <p className="mt-1 text-2xl font-black text-cyan-100">{score}</p>
        : probabilityValue != null
          ? <p className="mt-1 text-sm font-semibold text-slate-400">{(probabilityValue * 100).toFixed(1)}% win probability</p>
          : null}
    </div>
  </div>;
}

function ProbabilityCard({ abbreviation, probability }) {
  const team = teamDetails(abbreviation);
  const value = probabilityNumber(probability);
  return <GlowCard className="p-5">
    <div className="flex items-center gap-3">
      <TeamLogo team={team} size={48} />
      <div>
        <p className="text-sm font-semibold text-slate-400">{team.name}</p>
        <p className="mt-1 text-2xl font-black text-slate-100">
          {value != null ? `${(value * 100).toFixed(1)}%` : 'Unavailable'}
        </p>
      </div>
    </div>
  </GlowCard>;
}

export default function NbaGameBreakdown({ gameId, season }) {
  const [game, setGame] = useState(null);
  const [error, setError] = useState('');
  const load = useCallback(() => {
    setError('');
    api.nba.game(gameId, season).then(setGame).catch((exception) => setError(exception.message));
  }, [gameId, season]);

  useEffect(() => { load(); }, [load]);

  if (error) return <main className="mx-auto min-h-screen max-w-4xl px-6 py-16">
    <GlowCard className="p-6 text-red-100">
      <b>NBA game detail unavailable.</b>
      <p className="mt-2">{error}</p>
      <button onClick={load} className="btn btn-glass mt-4">Try again</button>
    </GlowCard>
  </main>;
  if (!game) return <main className="mx-auto min-h-screen max-w-7xl px-6 py-16">
    <div className="h-80 animate-pulse rounded-3xl bg-white/10" />
  </main>;

  const prediction = game.prediction;
  const outlook = predictionOutlook(game, prediction);
  const status = String(game.status || '').toLowerCase();
  const final = FINAL_STATUSES.has(status);
  const predictedProbability = probabilityNumber(prediction?.modelProbability);
  const storedAwayProbability = probabilityNumber(prediction?.awayWinProbability);
  const storedHomeProbability = probabilityNumber(prediction?.homeWinProbability);
  const homeProbability = storedHomeProbability ?? (
    predictedProbability == null
      ? null
      : prediction?.predictedWinner === game.homeTeam
        ? predictedProbability
        : 1 - predictedProbability
  );
  const awayProbability = storedAwayProbability ?? (
    predictedProbability == null ? null : 1 - homeProbability
  );

  return <main className="mx-auto min-h-screen max-w-7xl px-6 py-12 md:py-16">
    <div className="mb-5">
      <Link href="/games?league=nba" className="text-sm font-semibold text-cyan-200 hover:text-cyan-100">← NBA games</Link>
    </div>

    <GlowCard className="overflow-hidden p-6 md:p-8">
      <div className="flex flex-wrap gap-2">
        <span className="rounded-full bg-cyan-300 px-3 py-1 text-xs font-black uppercase tracking-wide text-slate-950">
          {final ? 'Final' : title(game.status)}
        </span>
        <span className="tag capitalize">NBA {title(game.seasonPhase || 'Regular season')} · {game.season}</span>
      </div>

      <div className="mt-8 grid items-center gap-6 md:grid-cols-[1fr_auto_1fr]">
        <HeaderTeam
          abbreviation={game.awayTeam}
          side="Away"
          probability={awayProbability}
          score={final ? game.awayScore : null}
        />
        <div className="text-center">
          <p className="text-xs font-bold uppercase tracking-[.25em] text-slate-500">{final ? 'Score' : 'Matchup'}</p>
          <p className="mt-2 text-xl font-black text-slate-300">AT</p>
        </div>
        <HeaderTeam
          abbreviation={game.homeTeam}
          side="Home"
          probability={homeProbability}
          score={final ? game.homeScore : null}
          align="right"
        />
      </div>

      <div className="mt-8 grid gap-4 border-t border-white/10 pt-5 text-sm text-slate-300 sm:grid-cols-2 lg:grid-cols-4">
        <Meta label="Local tipoff" value={localDate(game.kickoffTime)} />
        <Meta label="Season" value={`${game.season} ${title(game.seasonPhase || 'Regular season')}`} />
        <Meta label="SmartBetSports pick" value={prediction?.predictedWinner || 'Not available'} />
        <Meta label="Win probability" value={outlook ? `${(outlook.probability * 100).toFixed(1)}%` : 'Not available'} />
      </div>
    </GlowCard>

    <section className="mt-8" aria-labelledby="nba-matchup-outlook">
      <p className="text-xs font-black uppercase tracking-[.24em] text-cyan-300">MATCHUP OUTLOOK</p>
      <h2 id="nba-matchup-outlook" className="mt-2 text-3xl font-black tracking-[-.04em]">SmartBetSports prediction</h2>
      <p className="mt-2 text-slate-400">Pregame win probabilities for both teams, shown together for a clear matchup comparison.</p>

      {prediction && outlook ? <>
        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <ProbabilityCard abbreviation={game.awayTeam} probability={awayProbability} />
          <ProbabilityCard abbreviation={game.homeTeam} probability={homeProbability} />
        </div>
        <div className="mt-4 rounded-2xl bg-cyan-300/10 p-4 text-sm text-cyan-50">
          <b>{teamDetails(prediction.predictedWinner).name}</b> has a {outlook.advantage.toFixed(1)}-point probability advantage over {teamDetails(outlook.opponent).name}.
          {final && prediction.settlementStatus && prediction.settlementStatus !== 'UNRESOLVED'
            ? <span className="ml-2 font-bold">Prediction result: {title(prediction.settlementStatus)}.</span>
            : null}
        </div>
      </> : <GlowCard className="mt-5 p-5 text-slate-300">
        A verified pregame pick is not available for this matchup.
      </GlowCard>}
    </section>
  </main>;
}
