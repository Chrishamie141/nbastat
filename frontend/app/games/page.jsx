"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import SubscriptionGuard from "@/components/auth/SubscriptionGuard";
import GlowCard from "@/components/ui/GlowCard";
import NflMatchup from "@/components/games/NflMatchup";
import { api } from "@/lib/api";

export default function Games() {
  return (
    <SubscriptionGuard>
      <Board />
    </SubscriptionGuard>
  );
}

function Board() {
  const [week, setWeek] = useState(1),
    [season, setSeason] = useState(null),
    [seasonType, setSeasonType] = useState("regular"),
    [ready, setReady] = useState(false);
  const [data, setData] = useState(null),
    [error, setError] = useState("");
  const minWeek = seasonType === "preseason" ? 0 : 1,
    maxWeek = seasonType === "preseason" ? 3 : 18;
  useEffect(() => {
    api.nfl
      .context()
      .then((context) => {
        setSeason(context.season);
        setWeek(context.week);
        setSeasonType(context.seasonType);
      })
      .catch(() => {})
      .finally(() => setReady(true));
  }, []);
  useEffect(() => {
    if (!ready) return;
    setData(null);
    setError("");
    api.nfl
      .week({ season, week, seasonType })
      .then(setData)
      .catch((e) => setError(e.message));
  }, [ready, season, week, seasonType]);
  function changeSeason(value) {
    setSeasonType(value);
    setWeek(1);
  }
  return (
    <main className="mx-auto min-h-screen max-w-7xl px-4 pb-24 pt-10 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-5">
        <div>
          <p className="text-sm font-bold uppercase tracking-[.2em] text-cyan-300">
            All game winners
          </p>
          <h1 className="mt-2 text-4xl font-black tracking-[-.06em] sm:text-5xl">
            {data?.weekLabel ||
              (seasonType === "preseason" && week === 0
                ? "Hall of Fame Game"
                : (seasonType === "preseason" ? "Preseason Week " : "Week ") +
                  week)}
          </h1>
          <p className="mt-3 max-w-2xl text-slate-300">
            SmartBetSports&apos; independent winner lean for every game. A
            likely winner is not automatically a good bet at the current price.
          </p>
        </div>
        <Link
          href={`/parlays?season=${season}&seasonType=${seasonType}&week=${week}`}
          className="btn btn-primary"
        >
          Build picks
        </Link>
      </div>
      <GlowCard className="mt-6 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex gap-2">
            {[
              ["preseason", "Preseason"],
              ["regular", "Regular season"],
            ].map(([id, label]) => (
              <button
                key={id}
                onClick={() => changeSeason(id)}
                aria-pressed={seasonType === id}
                className={`rounded-full px-4 py-2 text-sm font-bold ${seasonType === id ? "bg-cyan-300 text-slate-950" : "bg-white/10 text-slate-200"}`}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <button
              className="btn btn-glass"
              disabled={week === minWeek}
              onClick={() => setWeek((w) => w - 1)}
            >
              Previous
            </button>
            <strong className="min-w-32 text-center">
              {seasonType === "preseason" && week === 0
                ? "Hall of Fame"
                : `Week ${week}`}
            </strong>
            <button
              className="btn btn-glass"
              disabled={week === maxWeek}
              onClick={() => setWeek((w) => w + 1)}
            >
              Next
            </button>
          </div>
        </div>
      </GlowCard>
      {data?.methodology && (
        <p className="mt-4 text-sm text-slate-400">
          Method: {data.methodology}
        </p>
      )}
      {error && (
        <GlowCard className="mt-6 border-red-400/40 p-5 text-red-100">
          <b>Unable to load verified data.</b>
          <p className="mt-1">{error}</p>
        </GlowCard>
      )}
      {!data && !error ? (
        <div className="mt-6 h-80 animate-pulse rounded-3xl bg-white/10" />
      ) : data?.items?.length ? (
        <div className="mt-7 grid gap-5 lg:grid-cols-2">
          {data.items.map((game) => (
            <PredictionCard
              key={game.game_id}
              game={game}
              season={season}
              week={week}
              seasonType={seasonType}
            />
          ))}
        </div>
      ) : (
        !error && (
          <GlowCard className="mt-6 p-8 text-center text-slate-300">
            No verified games are listed for this week.
          </GlowCard>
        )
      )}
    </main>
  );
}

function PredictionCard({ game, season, week, seasonType }) {
  const final = game.status === "final";
  const hasPick = ["available", "pregame_snapshot"].includes(
    game.predictionStatus,
  );
  const pickOdds =
    game.winner === game.home_team
      ? game.market?.homeOdds
      : game.market?.awayOdds;
  return (
    <article className="flex flex-col rounded-3xl border border-white/10 bg-white/[.04] p-5 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="tag">
          {new Date(game.kickoff_time).toLocaleString()}
        </span>
        <span
          className={`rounded-full px-3 py-1 text-xs font-bold ${final ? "bg-slate-400/15 text-slate-200" : game.recommendedBet ? "bg-emerald-400/15 text-emerald-200" : "bg-amber-300/10 text-amber-100"}`}
        >
          {final
            ? "Final"
            : game.recommendedBet
              ? "Bet threshold met"
              : "Winner lean only"}
        </span>
      </div>
      <NflMatchup game={game} size={50} className="mt-5" />
      {final && (
        <p className="mt-3 text-center text-lg font-bold">
          Final: {game.away_team} {game.away_score} – {game.home_team}{" "}
          {game.home_score}
        </p>
      )}
      {hasPick ? (
        <div className="mt-5 rounded-2xl bg-cyan-300/10 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-sm text-slate-300">SmartBetSports pick</p>
              <p className="mt-1 text-2xl font-black text-cyan-100">
                {game.winner} moneyline
              </p>
              <p className="mt-1 font-semibold">
                {(game.winProbability * 100).toFixed(1)}% model win probability
              </p>
            </div>
            <div className="text-right text-sm">
              <p>{game.riskLevel || "Unclassified"} risk</p>
              <p>
                {game.rating
                  ? `${game.rating}/10 rating`
                  : "Rating unavailable"}
              </p>
              <p>
                {pickOdds == null
                  ? "Verified price unavailable"
                  : `${pickOdds > 0 ? "+" : ""}${pickOdds}`}
              </p>
            </div>
          </div>
          {game.reasons?.length > 0 && (
            <div className="mt-4">
              <p className="text-xs font-bold uppercase tracking-wide text-slate-400">
                Why
              </p>
              <ul className="mt-2 space-y-1 text-sm text-slate-200">
                {game.reasons.map((reason) => (
                  <li key={reason}>• {reason}</li>
                ))}
              </ul>
            </div>
          )}
          {game.mainRisk && (
            <p className="mt-4 border-t border-white/10 pt-3 text-sm text-amber-100">
              <b>Main risk:</b> {game.mainRisk}
            </p>
          )}
          <p className="mt-3 text-xs text-slate-400">
            Evidence quality: {game.evidenceScore ?? "unavailable"}/100 (not a
            probability)
            {game.edge == null
              ? " · market edge unavailable"
              : ` · model edge ${(game.edge * 100).toFixed(1)} points`}
          </p>
          {final && game.predictionResult && (
            <p className="mt-3 font-bold uppercase">
              Stored pregame result: {game.predictionResult}
            </p>
          )}
        </div>
      ) : (
        <div className="mt-5 rounded-2xl bg-white/5 p-4 text-sm text-slate-300">
          <b>No trustworthy pregame pick shown.</b>
          <p className="mt-1">
            {game.unavailableReason ||
              "The required verified inputs are unavailable."}
          </p>
        </div>
      )}
      <Link
        href={`/games/${game.game_id}?season=${season}&week=${week}&seasonType=${seasonType}`}
        className="mt-5 text-sm font-bold text-cyan-200"
      >
        View matchup details →
      </Link>
    </article>
  );
}
