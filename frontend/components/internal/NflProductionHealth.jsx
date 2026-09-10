"use client";

import { useCallback, useEffect, useState } from "react";
import GlowCard from "@/components/ui/GlowCard";
import { api } from "@/lib/api";

const PROFILE_ORDER = ["SAFE", "BALANCED", "AGGRESSIVE"];
const value = (item) => item == null ? "—" : item;

export default function NflProductionHealth({ season = 2026, seasonType = "regular", week = 1 }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const load = useCallback(async () => {
    try {
      setError("");
      setData(await api.internal.nflProduction({ season, seasonType, week }));
    } catch (exception) {
      setError(exception.message);
    }
  }, [season, seasonType, week]);
  useEffect(() => { load(); }, [load]);
  async function audit() {
    setRunning(true);
    try {
      const report = await api.internal.runNflProductionAudit({ season, seasonType, week });
      setData((current) => ({ ...(current || {}), latestAudit: report }));
      setError("");
    } catch (exception) {
      setError(exception.message);
    } finally {
      setRunning(false);
    }
  }
  const auditReport = data?.latestAudit;
  const performance = data?.performance;
  return (
    <GlowCard id="nfl-production-health" className="mt-8 p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-black uppercase tracking-[.18em] text-cyan-300">NFL production health</p>
          <h2 className="mt-1 text-2xl font-black">Week {week} Settlement &amp; SGP Lab</h2>
          <p className="mt-2 text-sm text-slate-400">Frozen predictions and internal benchmark tickets only. Customer parlays are excluded.</p>
        </div>
        <button className="btn btn-primary px-4 py-2" onClick={audit} disabled={running}>
          {running ? "Running audit…" : "Run Production Audit"}
        </button>
      </div>
      {error && <p role="alert" className="mt-4 rounded-xl border border-rose-400/30 bg-rose-400/10 p-3 text-rose-100">{error}</p>}
      {auditReport ? (
        <>
          <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Audit" value={auditReport.result} />
            <Stat label="Final games" value={auditReport.counts?.finalGames} />
            <Stat label="Predictions settled" value={auditReport.counts?.finalPredictionsGraded} />
            <Stat label="Warnings" value={auditReport.warnings?.length} />
            <Stat label="Benchmark SGPs" value={auditReport.counts?.benchmarkSgps} />
            <Stat label="SGPs graded" value={auditReport.counts?.sgpsGraded} />
            <Stat label="SGPs pending" value={auditReport.counts?.sgpsPending} />
            <Stat label="NO BET" value={auditReport.counts?.noBet} />
          </div>
          <div className="mt-5 grid gap-2 md:grid-cols-2">
            {auditReport.checks?.map((check) => (
              <div key={check.name} className="flex items-center justify-between gap-3 rounded-xl bg-white/[.04] px-4 py-3">
                <div><p className="font-bold">{check.name}</p><p className="text-xs text-slate-500">{check.detail}</p></div>
                <span className={check.status === "PASS" ? "text-emerald-300" : check.status === "WARN" ? "text-amber-200" : "text-rose-300"}>{check.status}</span>
              </div>
            ))}
          </div>
          <p className="mt-4 text-xs text-slate-500">Last audit: {new Date(auditReport.createdAt).toLocaleString()}</p>
        </>
      ) : <p className="mt-5 text-slate-400">No production audit has been persisted yet.</p>}
      {performance && (
        <div className="mt-7 border-t border-white/10 pt-6">
          <div className="flex items-center justify-between gap-3"><h3 className="text-xl font-black">Same Game Parlay performance</h3><span className="text-xs font-bold text-amber-200">{performance.sampleStatus?.replaceAll("_", " ")}</span></div>
          <div className="mt-4 grid gap-3 lg:grid-cols-3">
            {PROFILE_ORDER.map((profile) => {
              const row = performance.byProfile?.[profile] || {};
              return <div key={profile} className="rounded-2xl border border-white/10 bg-white/[.03] p-4">
                <p className="text-sm font-black text-cyan-200">{profile}</p>
                <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
                  <Metric label="Tickets" value={row.generated} /><Metric label="No bet" value={row.noBet} />
                  <Metric label="Won / Lost" value={`${value(row.won)} / ${value(row.lost)}`} /><Metric label="Pending" value={row.pending} />
                  <Metric label="Ticket hit rate" value={row.hitRate == null ? "—" : `${row.hitRate}%`} /><Metric label="Leg hit rate" value={row.legHitRate == null ? "—" : `${row.legHitRate}%`} />
                </dl>
              </div>;
            })}
          </div>
        </div>
      )}
    </GlowCard>
  );
}

function Stat({ label, value: content }) {
  return <div className="rounded-2xl bg-white/[.05] p-4"><p className="text-xs uppercase tracking-wide text-slate-500">{label}</p><p className="mt-1 text-2xl font-black">{value(content)}</p></div>;
}

function Metric({ label, value: content }) {
  return <div><dt className="text-slate-500">{label}</dt><dd className="font-bold text-white">{value(content)}</dd></div>;
}
