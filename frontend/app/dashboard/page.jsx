'use client';

import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import SubscriptionGuard from '@/components/auth/SubscriptionGuard';
import { useAuth } from '@/components/auth/AuthProvider';
import GlowCard from '@/components/ui/GlowCard';
import UpcomingGameCard from '@/components/games/UpcomingGameCard';
import { api } from '@/lib/api';
import NflMatchup from '@/components/games/NflMatchup';
import CatalogSearch from '@/components/search/CatalogSearch';

const GAME_FILTERS = ['All', 'Upcoming', 'Live', 'Final'];
const LIVE = new Set(['live', 'halftime']);
const FINAL = new Set(['final', 'final-OT']);
const EMPTY_GAMES = [];

export default function Dashboard() {
  return <SubscriptionGuard><DashboardInner /></SubscriptionGuard>;
}

function DashboardInner() {
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState('All');
  const [phase, setPhase] = useState('all');
  const [refreshState, setRefreshState] = useState('');
  const [refreshingGames, setRefreshingGames] = useState(false);

  useEffect(() => {
    api.dashboard().then(setData).catch((requestError) => setError(requestError.message || 'Dashboard data unavailable.'));
  }, []);

  const summary = data?.summary || {};
  const games = data?.upcomingGames || EMPTY_GAMES;
  const phases = useMemo(() => [...new Set(games.map((game) => game.seasonPhase).filter(Boolean))].sort(), [games]);
  const visibleGames = useMemo(() => {
    const rows = games.filter((game) => {
      if (phase !== 'all' && game.seasonPhase !== phase) return false;
      if (filter === 'Upcoming') return ['scheduled', 'pregame'].includes(game.status);
      if (filter === 'Live') return LIVE.has(game.status);
      if (filter === 'Final') return FINAL.has(game.status);
      return true;
    });
    return [...rows].sort((left, right) => {
      const leftRank = LIVE.has(left.status) ? 0 : FINAL.has(left.status) ? 2 : 1;
      const rightRank = LIVE.has(right.status) ? 0 : FINAL.has(right.status) ? 2 : 1;
      if (leftRank !== rightRank) return leftRank - rightRank;
      const direction = leftRank === 2 ? -1 : 1;
      return direction * (new Date(left.startTimeUtc) - new Date(right.startTimeUtc));
    });
  }, [filter, games, phase]);
  const hasActivity = (summary.savedAnalyses || 0) > 0;
  async function refreshDashboardGames() {
    if (refreshingGames) return;
    setRefreshingGames(true);
    setRefreshState('Refreshing game data…');
    try {
      const result = await api.refreshGames();
      const before = JSON.stringify(games.map((game) => [game.id, game.status, game.awayScore, game.homeScore]));
      const after = JSON.stringify(result.items.map((game) => [game.id, game.status, game.awayScore, game.homeScore]));
      setData((current) => {
        const currentFeaturedId = current?.featuredGame?.id;
        const featuredGame = result.items.find((game) => game.id === currentFeaturedId) || result.items.find((game) => !FINAL.has(game.status)) || null;
        return { ...current, upcomingGames: result.items, featuredGame, lastUpdated: result.lastUpdated };
      });
      setRefreshState(result.coalesced || before === after ? 'No change · latest server result is already shown.' : 'Updated with the latest server result.');
    } catch (requestError) {
      setRefreshState(`Refresh failed · ${requestError.message || 'schedule unavailable'}`);
    } finally {
      setRefreshingGames(false);
    }
  }

  return <main className="mx-auto min-h-screen max-w-7xl px-6 pt-12 md:pt-16">
    <div className="flex flex-wrap items-center justify-between gap-4"><div><h1 className="text-4xl font-black tracking-[-.06em] md:text-5xl">Welcome back, {user?.name}</h1><p className="mt-2 text-slate-300">Account-scoped activity excludes legacy records without ownership.</p></div><Link href="/analyze" className="btn btn-primary">Start New Analysis</Link></div>
    <WeeklyNflBoard />
    <CatalogSearch />
    {error ? <GlowCard className="mt-6 p-4 text-red-100">Dashboard metrics unavailable. <button onClick={() => location.reload()} className="ml-2 underline">Retry</button></GlowCard> : null}
    <div className="mt-8 grid gap-4 md:grid-cols-5">{[['Saved analyses', summary.savedAnalyses || 0], ['Individual predictions', summary.individualPredictions || 0], ['Graded predictions', summary.gradedPredictions || 0], ['Saved parlays', summary.savedParlays || 0], ['Accuracy', summary.overallAccuracy == null ? 'Not enough data' : `${summary.overallAccuracy}%`]].map(([label, value]) => <GlowCard key={label} className="p-5"><p className="text-sm text-slate-400">{label}</p><p className="mt-2 text-3xl font-black">{value}</p></GlowCard>)}</div>
    <section className="mt-6 grid items-start gap-4 lg:grid-cols-[.9fr_1.1fr]">
      <GlowCard className="h-auto min-h-0 self-start p-6"><h2 className="text-2xl font-bold">Game to Watch</h2>{data?.errors?.featuredGame ? <p className="mt-4 text-slate-300">Featured game unavailable. <button onClick={() => location.reload()} className="underline">Retry</button></p> : data?.featuredGame ? <div className="mt-4"><UpcomingGameCard game={data.featuredGame} featured /></div> : <p className="mt-4 text-slate-300">No featured matchup is available within the current supported schedule window.</p>}</GlowCard>
      <GlowCard className="p-6">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-2xl font-bold">NFL & NBA Games</h2>{data?.lastUpdated ? <p className="mt-1 text-xs text-slate-500">Updated {new Date(data.lastUpdated).toLocaleTimeString()}</p> : null}</div><div className="flex items-center gap-2">{phases.length > 1 ? <label className="text-sm text-slate-300">Phase <select value={phase} onChange={(event) => setPhase(event.target.value)} className="ml-2 rounded-xl border border-white/10 bg-slate-900 px-3 py-2"><option value="all">All</option>{phases.map((item) => <option key={item} value={item}>{item.replaceAll('_', ' ')}</option>)}</select></label> : null}<button type="button" onClick={refreshDashboardGames} disabled={refreshingGames} className="rounded-xl bg-white/10 px-3 py-2 text-sm font-semibold text-cyan-100 disabled:opacity-60">{refreshingGames ? 'Refreshing…' : 'Refresh'}</button></div></div>
        {refreshState ? <p role="status" className="mt-3 text-xs text-slate-400">{refreshState}</p> : null}
        <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label="Filter games by lifecycle">{GAME_FILTERS.map((item) => <button key={item} type="button" onClick={() => setFilter(item)} aria-pressed={filter === item} className={`rounded-full px-4 py-2 text-sm font-semibold ${filter === item ? 'bg-cyan-300 text-slate-950' : 'bg-white/10 text-slate-200 hover:bg-white/15'}`}>{item}</button>)}</div>
        {data?.errors?.upcomingGames ? <p className="mt-4 text-slate-300">Game schedule unavailable. <button onClick={() => location.reload()} className="underline">Retry</button></p> : !data && !error ? <div className="mt-4 grid gap-3">{[1, 2, 3].map((item) => <div key={item} className="h-28 animate-pulse rounded-3xl bg-white/10" />)}</div> : visibleGames.length ? <div className="mt-4 grid gap-3">{visibleGames.map((game) => <UpcomingGameCard key={`${game.league}-${game.id}`} game={game} />)}</div> : <p className="mt-4 text-slate-300">No games match the selected lifecycle and phase filters.</p>}
      </GlowCard>
    </section>
    {!hasActivity ? <GlowCard className="mt-6 p-8 text-center text-gray-300">No saved analyses yet. Start your first analysis.</GlowCard> : null}
    <div className="mt-6 grid gap-4 lg:grid-cols-2"><GlowCard className="p-6"><h2 className="text-2xl font-bold">Recent activity</h2>{data?.recent?.length ? <div className="mt-4 grid gap-3">{data.recent.map((row, index) => <p key={`${row.summary}-${index}`} className="rounded-xl bg-white/5 p-3 text-sm text-gray-300">{row.summary}</p>)}</div> : <p className="mt-4 text-gray-400">No recent account-owned predictions, parlays, or graded results.</p>}</GlowCard>{data?.series?.length ? <GlowCard className="p-6"><h2 className="text-2xl font-bold">Accuracy over time</h2><div className="mt-4 h-52"><ResponsiveContainer><LineChart data={data.series}><XAxis dataKey="date" /><YAxis /><Tooltip /><Line type="monotone" dataKey="accuracy" stroke="#06d6e8" /></LineChart></ResponsiveContainer></div></GlowCard> : <GlowCard className="p-6"><h2 className="text-2xl font-bold">Quick actions</h2><div className="mt-4 flex flex-wrap gap-3"><Link className="btn btn-glass" href="/analyze">Start New Analysis</Link><Link className="btn btn-glass" href="/history">View History</Link><Link className="btn btn-glass" href="/performance">View Performance</Link></div></GlowCard>}</div>
  </main>;
}

// Weekly prediction board complements, rather than replaces, live lifecycle controls.
function WeeklyNflBoard() {
  const [season, setSeason] = useState(null),
    [seasonType, setSeasonType] = useState("regular"),
    [week, setWeek] = useState(1),
    [ready, setReady] = useState(false),
    [board, setBoard] = useState(null),
    [error, setError] = useState("");
  const initialBoardLoaded = useRef(false);
  useEffect(() => {
    api.nfl
      .currentWeek()
      .then(({ context, board: currentBoard }) => {
        setSeason(context.season);
        setSeasonType(context.seasonType);
        setWeek(context.week);
        setBoard(currentBoard);
        initialBoardLoaded.current = true;
      })
      .catch((e) => setError(e.message))
      .finally(() => setReady(true));
  }, []);
  useEffect(() => {
    if (!ready) return;
    if (initialBoardLoaded.current) {
      initialBoardLoaded.current = false;
      return;
    }
    let active = true;
    setError("");
    setBoard(null);
    api.nfl
      .week({ season, week, seasonType })
      .then((data) => { if (active) setBoard(data); })
      .catch((e) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [ready, season, week, seasonType]);
  const minWeek = seasonType === "preseason" ? 0 : 1,
    maxWeek = seasonType === "preseason" ? 3 : 18;
  return (
      <section className="mt-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-sm font-bold uppercase tracking-[.2em] text-cyan-300">
              NFL game board
            </p>
            <h2 className="mt-1 text-3xl font-black">
              {board?.weekLabel || "Current NFL slate"}
            </h2>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              className="btn btn-glass"
              disabled={week === minWeek}
              onClick={() => setWeek((w) => w - 1)}
            >
              Previous
            </button>
            <button
              className="btn btn-glass"
              disabled={week === maxWeek}
              onClick={() => setWeek((w) => w + 1)}
            >
              Next
            </button>
            <Link className="btn btn-glass" href="/games">
              All games
            </Link>
          </div>
        </div>
        {error ? <p role="alert" className="mt-5 text-red-100">Weekly board unavailable: {error}</p> : !board ? (
          <div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[1, 2, 3].map((x) => (
              <div
                key={x}
                className="h-48 animate-pulse rounded-3xl bg-white/10"
              />
            ))}
          </div>
        ) : board.items?.length ? (
          <div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {board.items.slice(0, 6).map((game) => (
              <Link
                href={`/games/${game.game_id}?season=${season}&week=${week}&seasonType=${seasonType}`}
                key={game.game_id}
                className="flex min-h-48 flex-col rounded-3xl border border-white/10 bg-white/5 p-5 transition hover:-translate-y-0.5 hover:border-cyan-300/50"
              >
                <p className="text-xs text-slate-400">
                  {new Date(game.kickoff_time).toLocaleString()}
                </p>
                <NflMatchup game={game} size={40} className="mt-4" />
                <p className="mt-auto pt-4 text-sm font-semibold text-cyan-100">
                  {game.winner
                    ? `Model pick: ${game.winner} · ${(game.winProbability * 100).toFixed(1)}%`
                    : "Prediction unavailable"}
                </p>
              </Link>
            ))}
          </div>
        ) : (
          <GlowCard className="mt-5 p-6">
            No games are scheduled in this week.
          </GlowCard>
        )}
      </section>
  );
}
