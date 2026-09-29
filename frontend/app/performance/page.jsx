"use client";

import { useCallback, useEffect, useState } from "react";
import { LineChart, Line, ResponsiveContainer, XAxis, YAxis, Tooltip } from "recharts";
import SubscriptionGuard from "@/components/auth/SubscriptionGuard";
import GlowCard from "@/components/ui/GlowCard";
import { api } from "@/lib/api";

export default function Performance() {
  return <SubscriptionGuard><Inner /></SubscriptionGuard>;
}

function Inner() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try { setData(await api.performance()); }
    catch { setError("Performance data could not be loaded. Please retry; this is not an empty prediction history."); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const metrics = data?.metrics || {};
  const names = ["NFL record", "NFL accuracy", "NFL graded predictions", "NBA accuracy", "Safe parlay performance", "Balanced parlay performance", "Aggressive parlay performance"];
  return <main className="mx-auto min-h-screen max-w-6xl px-6 pb-28 pt-20">
    <h1 className="text-4xl font-black tracking-[-.06em] sm:text-5xl">Performance & grading</h1>
    <p className="mt-2 text-gray-400">Grade saved predictions automatically from verified final results. Frozen winner predictions and tracked wagers are reported separately.</p>
    {loading && <p role="status" className="mt-6 text-cyan-200">Loading performance…</p>}
    {error && <div role="alert" className="mt-6 rounded-xl border border-amber-300/30 p-5"><p>{error}</p><button className="btn btn-primary mt-3" onClick={load} disabled={loading}>Retry performance</button></div>}
    {!error && !loading && data && <>
      <div className="mt-6 grid gap-4 md:grid-cols-3">{names.map((name) => <GlowCard key={name} className="p-5"><p className="text-sm text-gray-400">{name}</p><p className="mt-2 text-2xl font-black">{metrics[name] ?? (name.toLowerCase().includes("parlay") ? "No graded account parlays yet." : "No graded account predictions yet.")}</p></GlowCard>)}</div>
      <GlowCard className="mt-6 p-6"><h2 className="text-2xl font-bold">Accuracy over time</h2>
        {data.series?.length ? <div className="mt-4 h-64"><ResponsiveContainer><LineChart data={data.series}><XAxis dataKey="date" /><YAxis /><Tooltip /><Line dataKey="accuracy" stroke="#67e8f9" /></LineChart></ResponsiveContainer></div> : <p className="mt-4 text-gray-400">Weekly trend appears after multiple graded slates. Available graded records are shown above.</p>}
      </GlowCard>
    </>}
  </main>;
}
