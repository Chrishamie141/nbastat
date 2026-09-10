"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import SubscriptionGuard from "@/components/auth/SubscriptionGuard";
import RiskLevelSelector from "@/components/analyze/RiskLevelSelector";
import GlowCard from "@/components/ui/GlowCard";
import NflMatchup from "@/components/games/NflMatchup";
import { api } from "@/lib/api";

const MODES = [
  ["winners", "Game Winners", "Inspect every independent winner lean."],
  [
    "same_game",
    "Same Game Parlay",
    "Use verified markets from one matchup only.",
  ],
  [
    "multi_game",
    "Multi-Game Parlay",
    "Combine verified moneylines across distinct games.",
  ],
];
const DAYS = ["ALL", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY", "MONDAY"];

export default function Parlays() {
  return (
    <SubscriptionGuard>
      <Builder />
    </SubscriptionGuard>
  );
}
function Builder() {
  const [mode, setMode] = useState("winners"),
    [profile, setProfile] = useState("BALANCED"),
    [season, setSeason] = useState(null),
    [week, setWeek] = useState(1),
    [seasonType, setSeasonType] = useState("regular"),
    [day, setDay] = useState("ALL"),
    [ready, setReady] = useState(false);
  const [data, setData] = useState(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [picks, setPicks] = useState({}),
    [result, setResult] = useState(null),
    [selectedGame, setSelectedGame] = useState(null);
  const initialBoardLoaded = useRef(false);
  const minWeek = seasonType === "preseason" ? 0 : 1,
    maxWeek = seasonType === "preseason" ? 3 : 18;
  useEffect(() => {
    const query = new URLSearchParams(window.location.search),
      requestedMode = query.get("mode"),
      requestedDay = query.get("day"),
      requestedType = query.get("seasonType"),
      requestedSeason = Number(query.get("season")),
      requestedWeek = Number(query.get("week"));
    if (MODES.some(([id]) => id === requestedMode)) setMode(requestedMode);
    if (DAYS.includes(requestedDay)) setDay(requestedDay);
    if (["preseason", "regular"].includes(requestedType)) {
      if (query.has("season") && Number.isFinite(requestedSeason))
        setSeason(requestedSeason);
      setSeasonType(requestedType);
      if (query.has("week") && Number.isFinite(requestedWeek))
        setWeek(requestedWeek);
      setReady(true);
    } else
      api.nfl
        .currentWeek({
          profile: "BALANCED",
          day: DAYS.includes(requestedDay) ? requestedDay : "ALL",
        })
        .then(({ context, board }) => {
          setSeason(context.season);
          setWeek(context.week);
          setSeasonType(context.seasonType);
          setData(board);
          setLoading(false);
          initialBoardLoaded.current = true;
        })
        .catch((exc) => setError(exc.message))
        .finally(() => setReady(true));
  }, []);
  useEffect(() => {
    if (!ready) return;
    if (initialBoardLoaded.current) {
      initialBoardLoaded.current = false;
      return;
    }
    setLoading(true);
    setError("");
    setResult(null);
    api.nfl
      .week({ season, week, profile, day, seasonType })
      .then((board) => {
        setData(board);
        setPicks((current) =>
          Object.fromEntries(
            Object.entries(current).filter(([id]) =>
              board.items.some((game) => game.game_id === id),
            ),
          ),
        );
        setSelectedGame((current) =>
          current &&
          board.items.some((game) => game.game_id === current.game_id)
            ? current
            : null,
        );
      })
      .catch((exc) => setError(exc.message))
      .finally(() => setLoading(false));
  }, [ready, season, week, profile, day, seasonType]);
  const games = useMemo(() => data?.items || [], [data]),
    selections = useMemo(
      () =>
        games.flatMap((game) =>
          picks[game.game_id]
            ? [
                {
                  game,
                  team: picks[game.game_id],
                  odds:
                    picks[game.game_id] === game.home_team
                      ? game.market.homeOdds
                      : game.market.awayOdds,
                },
              ]
            : [],
        ),
      [games, picks],
    );
  function switchMode(value) {
    setMode(value);
    setSelectedGame(null);
    setResult(null);
  }
  function changeSeason(value) {
    setSeasonType(value);
    setWeek(1);
    setPicks({});
    setSelectedGame(null);
  }
  function choose(game, team) {
    if (game.status !== "scheduled") return;
    setPicks((current) => ({
      ...current,
      [game.game_id]: current[game.game_id] === team ? undefined : team,
    }));
  }
  function recommended() {
    setPicks(
      Object.fromEntries(
        games
          .filter((game) => game.status === "scheduled" && game.winner)
          .map((game) => [game.game_id, game.winner]),
      ),
    );
  }
  async function generateSameGame() {
    if (!selectedGame) return;
    setResult(null);
    setError("");
    try {
      setResult(
        await api.nfl.parlay({
          mode: "same_game",
          difficulty: profile,
          gameId: selectedGame.game_id,
          season,
          week,
          seasonType,
          homeTeam: selectedGame.home_team,
          awayTeam: selectedGame.away_team,
        }),
      );
    } catch (exc) {
      setError(exc.message);
    }
  }
  async function generateMulti() {
    setResult(null);
    setError("");
    try {
      setResult(
        await api.nfl.multiGameParlay({
          season,
          week,
          seasonType,
          profile,
          selections: selections.map(({ game, team }) => ({
            gameId: game.game_id,
            team,
          })),
        }),
      );
    } catch (exc) {
      setError(exc.message);
    }
  }
  return (
    <main className="mx-auto min-h-screen max-w-7xl px-4 pb-28 pt-10 sm:px-6 md:pt-14">
      <p className="text-sm font-bold uppercase tracking-[.22em] text-cyan-300">
        NFL week center
      </p>
      <h1 className="mt-2 text-4xl font-black tracking-[-.05em] md:text-6xl">
        Pick the slate, then the risk.
      </h1>
      <p className="mt-3 max-w-3xl text-slate-300">
        Winner probability, betting value, and evidence quality are shown
        separately. Missing markets are never replaced with sample legs.
      </p>
      <div className="mt-7 grid gap-3 md:grid-cols-3">
        {MODES.map(([id, title, copy]) => (
          <button
            key={id}
            aria-pressed={mode === id}
            onClick={() => switchMode(id)}
            className={`rounded-3xl border p-5 text-left transition hover:-translate-y-1 focus:outline focus:outline-2 focus:outline-cyan-300 ${mode === id ? "border-cyan-300 bg-cyan-300/15" : "border-white/10 bg-white/5 hover:bg-white/10"}`}
          >
            <b className="text-xl">{title}</b>
            <span className="mt-1 block text-sm text-slate-300">{copy}</span>
          </button>
        ))}
      </div>
      <GlowCard className="mt-5 p-5">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="flex gap-2">
              {[
                ["preseason", "Preseason"],
                ["regular", "Regular season"],
              ].map(([id, label]) => (
                <button
                  key={id}
                  onClick={() => changeSeason(id)}
                  className={`rounded-full px-4 py-2 text-sm font-bold ${seasonType === id ? "bg-cyan-300 text-slate-950" : "bg-white/10 text-slate-200"}`}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="mt-3 flex items-center gap-2">
              <button
                className="btn btn-glass"
                disabled={week === minWeek}
                onClick={() => setWeek((value) => value - 1)}
              >
                Previous
              </button>
              <strong className="min-w-32 text-center text-xl">
                {seasonType === "preseason" && week === 0
                  ? "Hall of Fame Game"
                  : `Week ${week}`}
              </strong>
              <button
                className="btn btn-glass"
                disabled={week === maxWeek}
                onClick={() => setWeek((value) => value + 1)}
              >
                Next
              </button>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {DAYS.map((value) => (
              <button
                key={value}
                onClick={() => setDay(value)}
                className={`rounded-full px-3 py-2 text-xs font-bold ${day === value ? "bg-cyan-300 text-slate-950" : "bg-white/10 text-slate-200"}`}
              >
                {value === "ALL"
                  ? "All days"
                  : value[0] + value.slice(1).toLowerCase()}
              </button>
            ))}
          </div>
        </div>
      </GlowCard>
      {error && (
        <GlowCard className="mt-5 border-red-400/40 p-5 text-red-100">
          <b>Unable to complete this request.</b>
          <p className="mt-1">{error}</p>
        </GlowCard>
      )}
      {loading ? (
        <LoadingCards />
      ) : games.length ? (
        <div className="mt-5 grid gap-4 lg:grid-cols-[1fr_350px]">
          <section className="grid content-start gap-4 md:grid-cols-2">
            {games.map((game) => (
              <GameCard
                key={game.game_id}
                game={game}
                mode={mode}
                picked={picks[game.game_id]}
                selected={selectedGame?.game_id === game.game_id}
                choose={choose}
                selectGame={setSelectedGame}
              />
            ))}
          </section>
          <aside className="lg:sticky lg:top-24 lg:self-start">
            <SelectionPanel
              mode={mode}
              profile={profile}
              setProfile={setProfile}
              selectedGame={selectedGame}
              selections={selections}
              recommended={recommended}
              generateSameGame={generateSameGame}
              generateMulti={generateMulti}
            />
            {result && <ParlayResult result={result} />}
          </aside>
        </div>
      ) : (
        !error && (
          <GlowCard className="mt-5 p-8 text-center text-slate-300">
            No games match this week and day filter.
          </GlowCard>
        )
      )}
    </main>
  );
}
function LoadingCards() {
  return (
    <div className="mt-5 grid gap-4 md:grid-cols-2">
      {[1, 2, 3, 4].map((value) => (
        <div
          key={value}
          className="h-64 animate-pulse rounded-3xl bg-white/10"
        />
      ))}
    </div>
  );
}
function SelectionPanel({
  mode,
  profile,
  setProfile,
  selectedGame,
  selections,
  recommended,
  generateSameGame,
  generateMulti,
}) {
  if (mode === "same_game")
    return (
      <GlowCard className="p-5">
        <h2 className="text-xl font-black">Same-game setup</h2>
        {selectedGame ? (
          <>
            <p className="mt-2 rounded-2xl bg-cyan-300/10 p-3 font-bold">
              {selectedGame.away_team} at {selectedGame.home_team}
            </p>
            <div className="mt-4">
              <RiskLevelSelector value={profile} onChange={setProfile} />
            </div>
            <button
              className="btn btn-primary mt-4 w-full"
              onClick={generateSameGame}
              disabled={selectedGame.status !== "scheduled"}
            >
              Build verified same-game parlay
            </button>
            {selectedGame.status !== "scheduled" && (
              <p className="mt-3 text-sm text-amber-200">This result is final and remains available for review only. New pregame parlays are locked.</p>
            )}
          </>
        ) : (
          <p className="mt-3 text-sm text-slate-400">
            Choose one upcoming matchup. Final games cannot be used.
          </p>
        )}
      </GlowCard>
    );
  return (
    <GlowCard className="p-5">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-xl font-black">
          {mode === "winners" ? "Independent winners" : "Multi-game selections"}
        </h2>
        <button className="text-sm text-cyan-200" onClick={recommended}>
          Use model winners
        </button>
      </div>
      {selections.length ? (
        <div className="mt-4 grid gap-3">
          {selections.map(({ game, team, odds }) => (
            <div className="rounded-2xl bg-white/5 p-3" key={game.game_id}>
              <b>{team} moneyline</b>
              <p className="text-sm text-slate-400">
                {game.away_team} at {game.home_team} ·{" "}
                {odds == null
                  ? "Price unavailable"
                  : odds > 0
                    ? `+${odds}`
                    : odds}
              </p>
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-4 text-sm text-slate-400">
          Select at most one team per upcoming game. Final games are read-only.
        </p>
      )}
      {mode === "multi_game" && (
        <>
          <div className="mt-5 border-t border-white/10 pt-4">
            <RiskLevelSelector value={profile} onChange={setProfile} />
          </div>
          <button
            disabled={!selections.length}
            onClick={generateMulti}
            className="btn btn-primary mt-4 w-full"
          >
            Validate and build parlay
          </button>
        </>
      )}
      <p className="mt-4 border-t border-white/10 pt-4 text-xs text-slate-500">
        A parlay is withheld when prices, model support, or enough eligible legs
        are unavailable.
      </p>
    </GlowCard>
  );
}
function ParlayResult({ result }) {
  const scoreLabel =
    result.parlayMode === "same_game"
      ? "recommendation score"
      : "model probability";
  return (
    <GlowCard className="mt-4 p-5">
      <h2 className="font-black">
        {result.parlayMode === "same_game" ? "Same-game" : "Multi-game"} result
      </h2>
      {result.legs?.length ? (
        <div className="mt-3 grid gap-2">
          {result.legs.map((leg, index) => (
            <div
              key={`${leg.prediction}-${index}`}
              className="rounded-xl bg-white/5 p-3 text-sm"
            >
              <b>{leg.prediction}</b>
              <p className="mt-1 text-slate-400">
                {leg.odds == null
                  ? "Price unavailable"
                  : `${leg.odds > 0 ? "+" : ""}${leg.odds}`}{" "}
                · {leg.confidence}% {scoreLabel}
              </p>
              <p className="mt-1 text-xs text-slate-500">{leg.notes}</p>
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-2 text-sm text-slate-300">
          {result.message ||
            "No verified legs met this profile. No sample legs were substituted."}
        </p>
      )}
      {result.estimatedOdds != null && (
        <p className="mt-3 font-bold">
          Estimated combined odds: {result.estimatedOdds > 0 ? "+" : ""}
          {result.estimatedOdds}
        </p>
      )}
      {result.rejectedSelections?.length > 0 && (
        <details className="mt-3 text-xs text-amber-100">
          <summary>
            {result.rejectedSelections.length} rejected selection(s)
          </summary>
          <ul className="mt-2 space-y-1">
            {result.rejectedSelections.map((row, index) => (
              <li key={`${row.gameId}-${index}`}>
                {row.team || "Unknown"}: {row.reason.replaceAll("_", " ")}
              </li>
            ))}
          </ul>
        </details>
      )}
      {result.correlationWarning && (
        <p className="mt-3 text-xs text-amber-200">
          {result.correlationWarning}
        </p>
      )}
    </GlowCard>
  );
}
function GameCard({ game, mode, picked, selected, choose, selectGame }) {
  const final = game.status === "final",
    available = game.predictionStatus === "available";
  return (
    <article
      className={`flex min-h-[430px] flex-col rounded-3xl border p-5 ${selected ? "border-cyan-300 bg-cyan-300/10" : "border-white/10 bg-white/[.04]"}`}
    >
      <div className="flex min-h-8 items-center justify-between gap-3">
        <span className="tag">
          {new Intl.DateTimeFormat(undefined, {
            weekday: "short",
            hour: "numeric",
            minute: "2-digit",
          }).format(new Date(game.kickoff_time))}
        </span>
        <span className="text-xs text-slate-400">
          {final
            ? "Final"
            : game.recommendedBet
              ? "Bet threshold met"
              : "Winner lean"}
        </span>
      </div>
      <NflMatchup game={game} size={46} className="mt-5" />
      {final && (
        <p className="mt-3 text-center font-bold">
          Final: {game.away_score} – {game.home_score}
        </p>
      )}
      {available ? (
        <div className="mt-4 rounded-2xl bg-cyan-300/10 p-4">
          <p className="text-sm text-slate-300">SmartBetSports pick</p>
          <p className="mt-1 text-2xl font-black text-cyan-100">
            {game.winner} · {(game.winProbability * 100).toFixed(1)}%
          </p>
          <p className="mt-2 text-xs leading-relaxed text-slate-400">
            {game.riskLevel} · evidence {game.evidenceScore}/100 (not
            probability)
            {game.edge == null
              ? " · market edge unavailable"
              : ` · edge ${(game.edge * 100).toFixed(1)} pts`}
          </p>
          <p className="mt-2 text-xs text-amber-100">{game.mainRisk}</p>
        </div>
      ) : (
        <p className="mt-4 rounded-2xl bg-white/5 p-4 text-sm text-slate-300">
          {game.unavailableReason ||
            "Prediction unavailable; no estimate was invented."}
        </p>
      )}
      <div className="mt-auto pt-4">
        {mode === "same_game" ? (
          <button
            disabled={!available || final}
            onClick={() => selectGame(game)}
            className="btn btn-primary w-full"
          >
            {selected ? "Selected matchup" : "Choose this matchup"}
          </button>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            {[
              [game.away_team, game.market?.awayOdds],
              [game.home_team, game.market?.homeOdds],
            ].map(([team, odds]) => (
              <button
                key={team}
                disabled={final || !available}
                aria-pressed={picked === team}
                onClick={() => choose(game, team)}
                className={`rounded-2xl border p-3 font-bold disabled:cursor-not-allowed disabled:opacity-50 ${picked === team ? "border-cyan-300 bg-cyan-300/20" : "border-white/10 bg-white/5"}`}
              >
                {team}
                <span className="block text-xs font-normal text-slate-400">
                  {odds == null
                    ? "Price unavailable"
                    : odds > 0
                      ? `+${odds}`
                      : odds}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}
