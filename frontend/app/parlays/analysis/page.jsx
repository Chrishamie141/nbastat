"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import SubscriptionGuard from "@/components/auth/SubscriptionGuard";
import RiskLevelSelector from "@/components/analyze/RiskLevelSelector";
import GlowCard from "@/components/ui/GlowCard";
import { useParlayTicket } from "@/context/ParlayTicketContext";
import { api } from "@/lib/api";
import { formatPercent as percent, formatDateTime as dateTime } from "@/lib/display-values.mjs";

const STORAGE_KEY = "smartbets:parlay-analysis";
const MARKET_LABELS = { PASS_YDS: "Passing yards", PASS_TD: "Passing TDs", PASS_INT: "Interceptions", RUSH_YDS: "Rushing yards", REC_YDS: "Receiving yards", RECEPTIONS: "Receptions", TD: "Anytime touchdown" };
const price = (value) => value == null || !Number.isFinite(Number(value)) ? "—" : `${Number(value) > 0 ? "+" : ""}${value}`;

export default function ParlayAnalysisPage() { return <SubscriptionGuard><AnalysisWorkspace /></SubscriptionGuard>; }

function AnalysisWorkspace() {
  const { ticket, preferences, addLeg, removeLeg, replaceConflict, updatePreferences } = useParlayTicket();
  const [setup, setSetup] = useState(null), [profile, setProfile] = useState("BALANCED"), [analysis, setAnalysis] = useState(null);
  const [loading, setLoading] = useState(false), [error, setError] = useState(""), [message, setMessage] = useState("");
  const [team, setTeam] = useState("ALL"), [market, setMarket] = useState("ALL"), [book, setBook] = useState("ALL"), [query, setQuery] = useState("");
  const [eligibleOnly, setEligibleOnly] = useState(false);

  useEffect(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "null");
      if (Array.isArray(saved?.selections) && saved.selections.length && saved.selections.every((item) => item?.gameId)) {
        setSetup(saved); setProfile(["SAFE", "BALANCED", "AGGRESSIVE"].includes(saved.profile) ? saved.profile : preferences.strategy);
      }
    } catch { /* Session storage is optional. */ }
  }, [preferences.strategy]);

  async function loadAnalysis() {
    if (!setup) return;
    setLoading(true); setError(""); setMessage("");
    try {
      const next = await api.nfl.parlayAnalysis({ ...setup, profile });
      setAnalysis(next); setTeam("ALL"); setMarket("ALL");
      const preferred = next.propBoards?.flatMap((board) => board.rows || []).some((row) => row.bookmaker === preferences.sportsbook);
      setBook(preferred ? preferences.sportsbook : "ALL");
    } catch (exc) { setError(exc.message); }
    finally { setLoading(false); }
  }

  const rows = useMemo(() => analysis?.propBoards?.flatMap((board) => board.rows || []) || [], [analysis]);
  const teams = useMemo(() => [...new Set(rows.map((row) => row.team))].sort(), [rows]);
  const markets = useMemo(() => [...new Set(rows.map((row) => row.market))].sort(), [rows]);
  const books = useMemo(() => [...new Set(rows.map((row) => row.bookmaker).filter(Boolean))].sort(), [rows]);
  const filteredIds = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return new Set(rows.filter((row) => (team === "ALL" || row.team === team) && (market === "ALL" || row.market === market) && (book === "ALL" || row.bookmaker === book) && (!eligibleOnly || row.profileEligible) && (!needle || `${row.player} ${row.team} ${MARKET_LABELS[row.market] || row.market}`.toLowerCase().includes(needle))).map((row) => `${row.gameId}:${row.rowId}`));
  }, [rows, team, market, book, query, eligibleOnly]);
  const isSelected = (row) => ticket.legs.some((leg) => leg.rowId === row.rowId && leg.gameId === row.gameId);

  function toggleLeg(row, board) {
    if (isSelected(row)) { removeLeg(row.rowId, row.gameId); setMessage("Leg removed from the pending ticket."); return; }
    const leg = { ...row, matchup: `${board.awayTeam} @ ${board.homeTeam}`, marketLabel: MARKET_LABELS[row.market] || row.market };
    const meta = { ticketType: setup.mode === "same_game" ? "SAME_GAME_PARLAY" : "MULTI_GAME_PARLAY", sportsbook: row.bookmaker, strategy: profile, season: setup.season, seasonType: setup.seasonType, week: setup.week };
    const result = addLeg(leg, meta);
    if (result.ok) setMessage("Leg added to your pending ticket.");
    else if (result.reason === "OPPOSING_SELECTION" && window.confirm("Replace the opposite side already in your ticket?")) { replaceConflict(result.conflict, leg, meta); setMessage("Conflicting leg replaced."); }
    else setMessage({ DUPLICATE: "That leg is already selected.", SGP_GAME_CONFLICT: "Finish or clear the current same-game ticket before choosing another matchup.", SPORTSBOOK_CONFLICT: "All legs on one ticket must use the same sportsbook." }[result.reason] || "This leg could not be added.");
  }

  if (!setup) return <main className="mx-auto min-h-screen max-w-4xl px-4 py-14 sm:px-6"><GlowCard className="p-8 text-center"><h1 className="text-3xl font-black">Choose your matchups first</h1><p className="mt-3 text-slate-300">This workspace starts with the games you select on the Parlays page.</p><Link href="/parlays" className="btn btn-primary mt-6">Back to Parlays</Link></GlowCard></main>;

  return <main className="mx-auto min-h-screen max-w-[1500px] px-4 pb-28 pt-10 sm:px-6">
    <div className="flex flex-wrap items-end justify-between gap-4"><div><p className="text-sm font-bold uppercase tracking-[.22em] text-cyan-300">Parlay analysis workspace</p><h1 className="mt-2 text-4xl font-black tracking-[-.04em] md:text-6xl">Research. Select. Build.</h1><p className="mt-3 max-w-3xl text-slate-300">Add verified lines without leaving the board. Your pending ticket persists as you change markets and filters. Nothing on this page creates a wager; confirmation only saves a private tracking record.</p></div><div className="flex gap-2"><Link href="/parlays/tickets" className="btn btn-glass">Ticket History</Link><Link href="/parlays" className="btn btn-secondary">Change matchups</Link></div></div>
    <GlowCard className="mt-7 p-5 md:p-6"><div className="grid items-end gap-5 lg:grid-cols-[1fr_auto]"><div><h2 className="text-xl font-black">Choose an analysis profile</h2><p className="mt-1 text-sm text-slate-400">The profile changes the confidence floor. It does not hide the complete verified market board.</p><fieldset disabled={loading} className="mt-4 max-w-2xl"><RiskLevelSelector value={profile} onChange={(next) => { setProfile(next); setAnalysis(null); updatePreferences({ strategy: next }); }} /></fieldset></div><button className="btn btn-primary min-w-56" onClick={loadAnalysis} disabled={loading}>{loading ? "Loading verified markets…" : analysis ? "Refresh analysis" : "Load verified analysis"}</button></div></GlowCard>
    {error && <div role="alert"><GlowCard className="mt-5 border-red-400/40 p-5 text-red-100"><b>Analysis unavailable.</b><p className="mt-1 text-sm">{error}</p></GlowCard></div>}
    {analysis && <>
      <section className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><Metric label="Matchups" value={analysis.matchups.length}/><Metric label="Verified market rows" value={rows.length}/><Metric label={`${profile} eligible`} value={rows.filter((row) => row.profileEligible).length}/><Metric label="Pending ticket legs" value={ticket.legs.length}/></section>
      <GlowCard className="mt-5 overflow-hidden"><div className="border-b border-white/10 p-5"><h2 className="text-xl font-black">Game-level model view</h2><p className="mt-1 text-sm text-slate-400">Winner probabilities come from the original weekly prediction board; stored prices appear only when available.</p></div><div className="overflow-x-auto" role="region" aria-label="Matchup comparison"><table className="w-full min-w-[920px] text-left text-sm"><thead className="bg-white/5 text-xs uppercase tracking-wider text-slate-400"><tr><Th>Matchup</Th><Th>Selected side</Th><Th>Model pick</Th><Th>Selected win estimate</Th><Th>Stored price</Th><Th>Kickoff</Th><Th>Model generated</Th></tr></thead><tbody>{analysis.matchups.map((game) => <tr key={game.gameId} className="border-t border-white/10"><Td><b>{game.awayTeam}</b> at <b>{game.homeTeam}</b></Td><Td>{game.selectedTeam || "Analysis only"}</Td><Td>{game.modelWinner || "Unavailable"}</Td><Td>{percent(game.selectedTeamProbability, "fraction")}</Td><Td>{price(game.selectedTeamOdds)}</Td><Td>{dateTime(game.kickoffTime)}</Td><Td>{dateTime(game.modelGeneratedAt)}</Td></tr>)}</tbody></table></div></GlowCard>
      <GlowCard className="mt-5 overflow-hidden"><div className="border-b border-white/10 p-5"><div className="flex flex-wrap items-end justify-between gap-4"><div><h2 className="text-xl font-black">Player and team market spreadsheet</h2><p className="mt-1 text-sm text-slate-400">Every live, verified prop returned for the selected matchups.</p></div><span className="tag">{filteredIds.size} rows shown</span></div><div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5"><label className="text-xs font-bold uppercase tracking-wide text-slate-400">Player search<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Josh Allen" className="mt-2 w-full rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-sm text-white"/></label><Filter label="Team" value={team} onChange={setTeam} options={teams}/><Filter label="Market" value={market} onChange={setMarket} options={markets} render={(value) => MARKET_LABELS[value] || value}/><Filter label="Sportsbook" value={book} onChange={(value) => { setBook(value); if (value !== "ALL") updatePreferences({ sportsbook: value }); }} options={books}/><label className="flex items-end gap-2 rounded-xl border border-white/10 p-3 text-sm"><input type="checkbox" checked={eligibleOnly} onChange={(event) => setEligibleOnly(event.target.checked)}/> Show {profile} eligible only</label></div></div>
        {rows.length ? <div className="overflow-x-auto"><table className="w-full min-w-[1380px] text-left text-sm"><thead className="sticky top-0 bg-slate-950 text-xs uppercase tracking-wider text-slate-400"><tr><Th>Ticket</Th><Th>Player</Th><Th>Team / Pos</Th><Th>Market</Th><Th>Side</Th><Th>Line</Th><Th>Projection</Th><Th>Model estimate</Th><Th>Recent hit rate</Th><Th>Sample</Th><Th>Price</Th><Th>Book</Th><Th>Recommendation</Th></tr></thead><tbody>{analysis.propBoards.flatMap((board) => (board.rows || []).filter((row) => filteredIds.has(`${row.gameId}:${row.rowId}`)).map((row) => <tr key={`${row.gameId}:${row.rowId}`} className={`border-t border-white/10 hover:bg-white/[.035] ${isSelected(row) ? "bg-cyan-300/10" : ""}`}><Td><button aria-pressed={isSelected(row)} onClick={() => toggleLeg(row, board)} className={`rounded-lg px-3 py-2 text-xs font-bold ${isSelected(row) ? "bg-cyan-300 text-slate-950" : "bg-white/10 text-cyan-100"}`}>{isSelected(row) ? "Selected" : "Add to Ticket"}</button></Td><Td><b>{row.player}</b></Td><Td>{row.team}<span className="ml-2 text-xs text-slate-500">{row.position || "—"}</span></Td><Td>{MARKET_LABELS[row.market] || row.market}</Td><Td><span className={row.side === row.modelSide ? "text-emerald-300" : "text-slate-400"}>{row.side}</span></Td><Td>{row.line}</Td><Td>{row.projection == null ? "—" : Number(row.projection).toFixed(1)}</Td><Td><b>{percent(row.modelLikelihood)}</b></Td><Td>{row.recentSample ? percent(row.recentHitRate) : "No prior results at this line"}</Td><Td>n={row.recentSample || 0}{row.recentPushes ? ` · ${row.recentPushes} push` : ""}</Td><Td>{price(row.odds)}</Td><Td>{row.bookmaker || "—"}</Td><Td>{row.profileEligible ? <span className="text-emerald-300">SmartBet recommended</span> : row.side === row.modelSide ? <span className="text-amber-200">SmartBet lean</span> : <span className="text-slate-400">Manual selection</span>}</Td></tr>))}</tbody></table></div> : <div className="p-8 text-center text-slate-300"><b>No verified player markets are available.</b><p className="mt-2 text-sm text-slate-400">No sample props were substituted.</p></div>}
      </GlowCard>
      {message && <p role="status" className="mt-4 rounded-xl border border-cyan-300/20 bg-cyan-300/10 p-3 text-sm text-cyan-100">{message}</p>}
      <GlowCard className="mt-5 p-5 text-sm text-slate-300"><h2 className="font-black text-white">Probability policy</h2><p className="mt-2">The displayed model estimate is the existing heuristic confidence for that offered side, not a guaranteed calibrated probability. SmartBets does not multiply leg estimates or present them as a true joint SGP probability.</p></GlowCard>
    </>}
  </main>;
}

function Metric({ label, value }) { return <GlowCard className="p-5"><p className="text-xs font-bold uppercase tracking-wider text-slate-400">{label}</p><p className="mt-2 text-3xl font-black">{value}</p></GlowCard>; }
function Filter({ label, value, onChange, options, render = (item) => item }) { return <label className="text-xs font-bold uppercase tracking-wide text-slate-400">{label}<select value={value} onChange={(event) => onChange(event.target.value)} className="mt-2 w-full rounded-xl border border-white/10 bg-slate-950 px-3 py-2 text-sm text-white"><option value="ALL">All</option>{options.map((option) => <option key={option} value={option}>{render(option)}</option>)}</select></label>; }
function Th({ children }) { return <th className="whitespace-nowrap px-4 py-3 font-bold">{children}</th>; }
function Td({ children }) { return <td className="whitespace-nowrap px-4 py-3">{children}</td>; }
