"use client";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import SubscriptionGuard from "@/components/auth/SubscriptionGuard";
import GlowCard from "@/components/ui/GlowCard";
import NflMatchup from "@/components/games/NflMatchup";
import { api } from "@/lib/api";
export default function Game() {
  return (
    <SubscriptionGuard>
      <Suspense fallback={<p className="p-6">Loading matchup…</p>}><Detail /></Suspense>
    </SubscriptionGuard>
  );
}
function Detail() {
  const { slug } = useParams();
  const search = useSearchParams();
  const season = Number(search.get("season")) || undefined,
    week = Number(search.get("week") || 1),
    seasonType = search.get("seasonType") || "regular";
  const [game, setGame] = useState(null),
    [done, setDone] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    setDone(false);
    setError("");
    api.nfl
      .week({ season, week, seasonType })
      .then((data) =>
        setGame(data.items.find((row) => row.game_id === slug) || null),
      )
      .catch((exc) => setError(exc.message))
      .finally(() => setDone(true));
  }, [slug, season, week, seasonType]);
  if (!done)
    return (
      <main className="mx-auto max-w-4xl px-6 pt-16">
        <div className="h-80 animate-pulse rounded-3xl bg-white/10" />
      </main>
    );
  const hasPick =
      game && ["available", "pregame_snapshot"].includes(game.predictionStatus),
    pickOdds =
      game?.winner === game?.home_team
        ? game?.market?.homeOdds
        : game?.market?.awayOdds,
    closingObservation = game?.marketHistory?.closing,
    closingMarket = closingObservation?.market,
    closingPrice =
      game?.winner === game?.home_team
        ? closingMarket?.homeOdds
        : closingMarket?.awayOdds;
  return (
    <main className="mx-auto min-h-screen max-w-4xl px-4 pb-24 pt-12 sm:px-6">
      <Link href="/games" className="text-cyan-200">
        ← Back to weekly winners
      </Link>
      {/^(?:espn-)?\d{6,18}$/.test(slug) && <Link className="btn btn-glass ml-4" href={`/nfl/games/${slug.replace('espn-', '')}`}>Live breakdown, actuals &amp; refresh</Link>}
      {error ? (
        <GlowCard className="mt-6 border-red-400/40 p-6 text-red-100">
          {error}
        </GlowCard>
      ) : !game ? (
        <GlowCard className="mt-6 p-8">
          This game is not available in the selected {seasonType} week.
        </GlowCard>
      ) : (
        <>
          <p className="mt-8 text-sm text-slate-400">
            {game.week_label ||
              (seasonType === "preseason" && week === 0
                ? "Hall of Fame Game"
                : seasonType + " Week " + week)}{" "}
            · {new Date(game.kickoff_time).toLocaleString()}
          </p>
          <h1 className="sr-only">
            {game.away_team} at {game.home_team}
          </h1>
          <GlowCard className="mt-4 p-6 sm:p-8">
            <NflMatchup game={game} size={64} />
            <p className="mt-5 text-center text-slate-400">
              {game.venue || "Venue TBD"}
              {game.broadcast?.length ? ` · ${game.broadcast.join(", ")}` : ""}
            </p>
            {game.status === "final" && (
              <p className="mt-3 text-center text-xl font-black">
                Final: {game.away_team} {game.away_score} – {game.home_team}{" "}
                {game.home_score}
              </p>
            )}
          </GlowCard>
          {hasPick ? (
            <>
              <div className="mt-5 grid gap-4 sm:grid-cols-3">
                <GlowCard className="p-6 sm:col-span-2">
                  <p className="text-sm text-slate-400">SmartBetSports pick</p>
                  <p className="mt-2 text-4xl font-black text-cyan-100">
                    {game.winner} ML
                  </p>
                  <p className="mt-2 text-lg">
                    {(game.winProbability * 100).toFixed(1)}% model win
                    probability
                  </p>
                  <p className="mt-2 text-sm text-slate-400">
                    Projected score: {game.away_team} {game.projectedAwayScore}{" "}
                    – {game.home_team} {game.projectedHomeScore}
                  </p>
                </GlowCard>
                <GlowCard className="p-6">
                  <p className="text-sm text-slate-400">
                    Recommendation context
                  </p>
                  <p className="mt-2 text-2xl font-black">
                    {game.riskLevel || "Unclassified"}
                  </p>
                  <p className="mt-2">
                    {game.rating
                      ? `${game.rating}/10 rating`
                      : "Rating unavailable"}
                  </p>
                  <p>
                    {pickOdds == null
                      ? "Price unavailable"
                      : `${pickOdds > 0 ? "+" : ""}${pickOdds}`}
                  </p>
                  <p className="mt-2 text-xs text-slate-400">
                    Evidence {game.evidenceScore ?? "—"}/100, not a probability
                  </p>
                </GlowCard>
              </div>
              <GlowCard className="mt-4 p-6">
                <h2 className="text-xl font-black">
                  Why SmartBetSports likes it
                </h2>
                <ul className="mt-3 space-y-2 text-slate-200">
                  {game.reasons?.map((reason) => (
                    <li key={reason}>• {reason}</li>
                  ))}
                </ul>
                <h2 className="mt-6 text-xl font-black">Main risk</h2>
                <p className="mt-2 text-amber-100">{game.mainRisk}</p>
                {game.missingData?.length > 0 && (
                  <p className="mt-4 text-sm text-slate-400">
                    Missing verified inputs: {game.missingData.join(", ")}.
                  </p>
                )}
              </GlowCard>
              <GlowCard className="mt-4 p-6">
                <h2 className="text-xl font-black">Market vs model</h2>
                <p className="mt-2 text-slate-300">
                  {game.market?.sportsbook
                    ? `${game.market.sportsbook}: ${game.away_team} ${formatOdds(game.market.awayOdds)}, ${game.home_team} ${formatOdds(game.market.homeOdds)}.`
                    : "No verified live moneyline is attached. A betting-value recommendation is withheld without a price."}
                </p>
                <p className="mt-2">
                  {game.edge == null
                    ? "Model edge unavailable."
                    : `Model edge on the predicted winner: ${(game.edge * 100).toFixed(1)} percentage points.`}
                </p>
                <p className="mt-2 text-sm text-slate-400">
                  Current decision:{" "}
                  {game.recommendedBet
                    ? "BET — profile threshold met"
                    : "PASS — " +
                      (
                        game.recommendationReason || "threshold_not_met"
                      ).replaceAll("_", " ")}
                  .
                </p>
                <p className="mt-3 text-sm text-slate-500">
                  Model {game.modelVersion} · data through{" "}
                  {game.dataAsOf || "unavailable"}
                </p>
                <div className="mt-3 grid gap-1 text-xs text-slate-500">
                  <p>
                    Model generated:{" "}
                    {game.frozenModelGeneratedAt
                      ? new Date(game.frozenModelGeneratedAt).toLocaleString()
                      : "not stored"}
                  </p>
                  <p>
                    Odds updated:{" "}
                    {game.market?.marketTimestamp
                      ? new Date(game.market.marketTimestamp).toLocaleString()
                      : "unavailable"}
                  </p>
                  <p>
                    Market observations: {game.marketHistory?.count || 0}
                    {" · "}closing market:{" "}
                    {game.marketHistory?.closingStatus || "not captured"}
                  </p>
                  <p>
                    Closing price: {formatOdds(closingPrice)}
                    {closingObservation?.marketTimestamp
                      ? ` at ${new Date(closingObservation.marketTimestamp).toLocaleString()}`
                      : " · pending"}
                  </p>
                  <p>
                    Market source: {game.market?.provider || "the-odds-api"}
                    {game.market?.sportsbook
                      ? ` · ${game.market.sportsbook}`
                      : " · bookmaker unavailable"}
                  </p>
                </div>
              </GlowCard>
            </>
          ) : (
            <GlowCard className="mt-5 p-6">
              <h2 className="text-xl font-black">
                No trustworthy pregame pick
              </h2>
              <p className="mt-2 text-slate-300">
                {game.unavailableReason ||
                  "The model lacks enough verified pregame evidence."}
              </p>
            </GlowCard>
          )}
        </>
      )}
    </main>
  );
}
function formatOdds(value) {
  if (value == null) return "unavailable";
  return value > 0 ? `+${value}` : String(value);
}
