"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import SubscriptionGuard from "@/components/auth/SubscriptionGuard";
import RiskLevelSelector from "@/components/analyze/RiskLevelSelector";
import GlowCard from "@/components/ui/GlowCard";
import { api } from "@/lib/api";

const STORAGE_KEY = "smartbets:parlay-analysis";
const MARKET_LABELS = {
  PASS_YDS: "Passing yards",
  PASS_TD: "Passing TDs",
  PASS_INT: "Interceptions",
  RUSH_YDS: "Rushing yards",
  REC_YDS: "Receiving yards",
  RECEPTIONS: "Receptions",
  TD: "Anytime touchdown",
};

const percent = (value) => {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const numeric = Number(value);
  return `${(Math.abs(numeric) <= 1 ? numeric * 100 : numeric).toFixed(1)}%`;
};
const price = (value) =>
  value == null ? "—" : `${Number(value) > 0 ? "+" : ""}${value}`;
const dateTime = (value) =>
  value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";

export default function ParlayAnalysisPage() {
  return (
    <SubscriptionGuard>
      <AnalysisWorkspace />
    </SubscriptionGuard>
  );
}

function AnalysisWorkspace() {
  const [setup, setSetup] = useState(null);
  const [profile, setProfile] = useState("BALANCED");
  const [analysis, setAnalysis] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [team, setTeam] = useState("ALL");
  const [market, setMarket] = useState("ALL");
  const [book, setBook] = useState("ALL");
  const [query, setQuery] = useState("");
  const [eligibleOnly, setEligibleOnly] = useState(false);
  const [shortlist, setShortlist] = useState({});

  useEffect(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "null");
      if (saved?.selections?.length) {
        setSetup(saved);
        setProfile(saved.profile || "BALANCED");
      }
    } catch {
      sessionStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  async function loadAnalysis() {
    if (!setup) return;
    setLoading(true);
    setError("");
    try {
      const next = await api.nfl.parlayAnalysis({ ...setup, profile });
      setAnalysis(next);
      setTeam("ALL");
      setMarket("ALL");
      setBook("ALL");
      setShortlist({});
    } catch (exc) {
      setError(exc.message);
    } finally {
      setLoading(false);
    }
  }

  const rows = useMemo(
    () => analysis?.propBoards?.flatMap((board) => board.rows || []) || [],
    [analysis],
  );
  const teams = useMemo(() => [...new Set(rows.map((row) => row.team))].sort(), [rows]);
  const markets = useMemo(() => [...new Set(rows.map((row) => row.market))].sort(), [rows]);
  const books = useMemo(() => [...new Set(rows.map((row) => row.bookmaker).filter(Boolean))].sort(), [rows]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return rows.filter(
      (row) =>
        (team === "ALL" || row.team === team) &&
        (market === "ALL" || row.market === market) &&
        (book === "ALL" || row.bookmaker === book) &&
        (!eligibleOnly || row.profileEligible) &&
        (!needle || `${row.player} ${row.team} ${MARKET_LABELS[row.market] || row.market}`.toLowerCase().includes(needle)),
    );
  }, [rows, team, market, book, query, eligibleOnly]);
  const shortlisted = rows.filter((row) => shortlist[row.rowId]);

  if (!setup)
    return (
      <main className="mx-auto min-h-screen max-w-4xl px-4 py-14 sm:px-6">
        <GlowCard className="p-8 text-center">
          <h1 className="text-3xl font-black">Choose your matchups first</h1>
          <p className="mt-3 text-slate-300">This workspace starts with the games you select on the Parlays page.</p>
          <Link href="/parlays" className="btn btn-primary mt-6">Back to Parlays</Link>
        </GlowCard>
      </main>
    );

  return (
    <main className="mx-auto min-h-screen max-w-[1500px] px-4 pb-28 pt-10 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm font-bold uppercase tracking-[.22em] text-cyan-300">Parlay analysis workspace</p>
          <h1 className="mt-2 text-4xl font-black tracking-[-.04em] md:text-6xl">Compare every verified leg.</h1>
          <p className="mt-3 max-w-3xl text-slate-300">
            Review the numbers, shortlist what fits your sportsbook, and make your own decision. Nothing on this page creates a wager.
          </p>
        </div>
        <Link href="/parlays" className="btn btn-secondary">Change matchups</Link>
      </div>

      <GlowCard className="mt-7 p-5 md:p-6">
        <div className="grid items-end gap-5 lg:grid-cols-[1fr_auto]">
          <div>
            <h2 className="text-xl font-black">1. Choose an analysis profile</h2>
            <p className="mt-1 text-sm text-slate-400">The profile changes the confidence floor. It does not hide the full verified market board.</p>
            <div className="mt-4 max-w-2xl"><RiskLevelSelector value={profile} onChange={setProfile} /></div>
          </div>
          <button className="btn btn-primary min-w-56" onClick={loadAnalysis} disabled={loading}>
            {loading ? "Loading verified markets…" : analysis ? "Refresh analysis" : "Load verified analysis"}
          </button>
        </div>
      </GlowCard>

      {error && <GlowCard className="mt-5 border-red-400/40 p-5 text-red-100"><b>Analysis unavailable.</b><p className="mt-1 text-sm">{error}</p></GlowCard>}
      {analysis && (
        <>
          <section className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Metric label="Matchups" value={analysis.matchups.length} />
            <Metric label="Verified market rows" value={rows.length} />
            <Metric label={`${profile} eligible`} value={rows.filter((row) => row.profileEligible).length} />
            <Metric label="Shortlisted" value={shortlisted.length} />
          </section>

          <GlowCard className="mt-5 overflow-hidden">
            <div className="border-b border-white/10 p-5"><h2 className="text-xl font-black">Game-level model view</h2><p className="mt-1 text-sm text-slate-400">Winner probabilities come from the original weekly prediction board; stored prices are shown only when available.</p></div>
            <div className="overflow-x-auto"><table className="w-full min-w-[920px] text-left text-sm"><thead className="bg-white/5 text-xs uppercase tracking-wider text-slate-400"><tr><Th>Matchup</Th><Th>Selected side</Th><Th>Model pick</Th><Th>Selected win estimate</Th><Th>Stored price</Th><Th>Kickoff</Th><Th>Model generated</Th></tr></thead><tbody>{analysis.matchups.map((game) => <tr key={game.gameId} className="border-t border-white/10"><Td><b>{game.awayTeam}</b> at <b>{game.homeTeam}</b></Td><Td>{game.selectedTeam || "Analysis only"}</Td><Td>{game.modelWinner || "Unavailable"}</Td><Td>{percent(game.selectedTeamProbability)}</Td><Td>{price(game.selectedTeamOdds)}</Td><Td>{dateTime(game.kickoffTime)}</Td><Td>{dateTime(game.modelGeneratedAt)}</Td></tr>)}</tbody></table></div>
          </GlowCard>

          <GlowCard className="mt-5 overflow-hidden">
            <div className="border-b border-white/10 p-5">
              <div className="flex flex-wrap items-end justify-between gap-4"><div><h2 className="text-xl font-black">Player and team market spreadsheet</h2><p className="mt-1 text-sm text-slate-400">Every live, verified prop returned for your selected matchup set.</p></div><span className="tag">{filtered.length} rows shown</span></div>
              <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                <label className="text-xs font-bold uppercase tracking-wide text-slate-400">Player search<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Josh Allen" className="mt-2 w-full rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-sm text-white" /></label>
                <Filter label="Team" value={team} onChange={setTeam} options={teams} />
                <Filter label="Market" value={market} onChange={setMarket} options={markets} render={(value) => MARKET_LABELS[value] || value} />
                <Filter label="Sportsbook" value={book} onChange={setBook} options={books} />
                <label className="flex items-end gap-2 rounded-xl border border-white/10 p-3 text-sm"><input type="checkbox" checked={eligibleOnly} onChange={(event) => setEligibleOnly(event.target.checked)} /> Show {profile} eligible only</label>
              </div>
            </div>
            {rows.length ? (
              <div className="overflow-x-auto"><table className="w-full min-w-[1320px] text-left text-sm"><thead className="sticky top-0 bg-slate-950 text-xs uppercase tracking-wider text-slate-400"><tr><Th>Keep</Th><Th>Player</Th><Th>Team / Pos</Th><Th>Market</Th><Th>Side</Th><Th>Line</Th><Th>Projection</Th><Th>Model estimate</Th><Th>Recent hit rate</Th><Th>Sample</Th><Th>Price</Th><Th>Book</Th><Th>Profile status</Th></tr></thead><tbody>{filtered.map((row) => <tr key={row.rowId} className="border-t border-white/10 hover:bg-white/[.035]"><Td><input aria-label={`Shortlist ${row.player} ${row.side} ${row.line}`} type="checkbox" checked={Boolean(shortlist[row.rowId])} onChange={() => setShortlist((current) => ({ ...current, [row.rowId]: !current[row.rowId] }))} /></Td><Td><b>{row.player}</b></Td><Td>{row.team}<span className="ml-2 text-xs text-slate-500">{row.position || "—"}</span></Td><Td>{MARKET_LABELS[row.market] || row.market}</Td><Td><span className={row.side === row.modelSide ? "text-emerald-300" : "text-slate-400"}>{row.side}</span></Td><Td>{row.line}</Td><Td>{row.projection == null ? "—" : Number(row.projection).toFixed(1)}</Td><Td><b>{percent(row.modelLikelihood)}</b></Td><Td>{row.recentSample ? percent(row.recentHitRate) : "Insufficient data"}</Td><Td>n={row.recentSample || 0}{row.recentPushes ? ` · ${row.recentPushes} push` : ""}</Td><Td>{price(row.odds)}</Td><Td>{row.bookmaker || "—"}</Td><Td>{row.profileEligible ? <span className="text-emerald-300">Meets {profile}</span> : <span className="text-amber-200">Review</span>}</Td></tr>)}</tbody></table></div>
            ) : <div className="p-8 text-center text-slate-300"><b>No verified player markets are available.</b><p className="mt-2 text-sm text-slate-400">No sample props were substituted. Try again when your sportsbook/provider has published markets.</p></div>}
          </GlowCard>

          {shortlisted.length > 0 && <GlowCard className="mt-5 p-5"><h2 className="text-xl font-black">My local shortlist</h2><p className="mt-1 text-sm text-slate-400">For comparison only; this is not saved or submitted as a bet.</p><div className="mt-4 grid gap-2 md:grid-cols-2 xl:grid-cols-3">{shortlisted.map((row) => <div key={row.rowId} className="rounded-xl bg-white/5 p-3 text-sm"><b>{row.player} {row.side} {row.line}</b><p className="mt-1 text-slate-400">{MARKET_LABELS[row.market] || row.market} · model estimate {percent(row.modelLikelihood)}</p></div>)}</div></GlowCard>}

          <GlowCard className="mt-5 p-5 text-sm text-slate-300"><h2 className="font-black text-white">How to read these numbers</h2><p className="mt-2"><b>Model estimate</b> is the existing SmartBets heuristic confidence for that offered side. It is not a guaranteed or fully calibrated sportsbook probability.</p><p className="mt-2"><b>Recent hit rate</b> is the share of available prior-game values that cleared this exact line. The sample size is always shown, and missing history stays unavailable.</p><p className="mt-2"><b>Market policy:</b> only live verified provider markets appear. No sample lines, invented prices, or unavailable injury claims are inserted.</p></GlowCard>
        </>
      )}
    </main>
  );
}

function Metric({ label, value }) { return <GlowCard className="p-5"><p className="text-xs font-bold uppercase tracking-wider text-slate-400">{label}</p><p className="mt-2 text-3xl font-black">{value}</p></GlowCard>; }
function Filter({ label, value, onChange, options, render = (item) => item }) { return <label className="text-xs font-bold uppercase tracking-wide text-slate-400">{label}<select value={value} onChange={(event) => onChange(event.target.value)} className="mt-2 w-full rounded-xl border border-white/10 bg-slate-950 px-3 py-2 text-sm text-white"><option value="ALL">All</option>{options.map((option) => <option key={option} value={option}>{render(option)}</option>)}</select></label>; }
function Th({ children }) { return <th className="whitespace-nowrap px-4 py-3 font-bold">{children}</th>; }
function Td({ children }) { return <td className="whitespace-nowrap px-4 py-3">{children}</td>; }
