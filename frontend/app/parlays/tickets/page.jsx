"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import SubscriptionGuard from "@/components/auth/SubscriptionGuard";
import GlowCard from "@/components/ui/GlowCard";
import { api } from "@/lib/api";

export default function TicketHistoryPage() {
  return <SubscriptionGuard><TicketHistory /></SubscriptionGuard>;
}

function TicketHistory() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    api.nfl.parlayTickets().then((data) => setRows(data.items || []))
      .catch((exc) => setError(exc.message)).finally(() => setLoading(false));
  }, []);
  return <main className="mx-auto min-h-screen max-w-6xl px-4 pb-24 pt-12 sm:px-6">
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div><p className="text-sm font-bold uppercase tracking-widest text-cyan-300">My Parlays</p><h1 className="mt-2 text-5xl font-black">Ticket History</h1><p className="mt-2 text-slate-400">Confirmed lines and model evidence remain frozen exactly as saved.</p></div>
      <Link href="/parlays" className="btn btn-primary">Build Ticket</Link>
    </div>
    <GlowCard className="mt-7 p-5">
      {loading ? <div className="h-40 animate-pulse rounded-2xl bg-white/10" /> : error ? <p role="alert" className="text-red-200">{error}</p> : rows.length ? <div className="grid gap-3">{rows.map((ticket) => <Link key={ticket.ticket_id} href={`/parlays/tickets/${ticket.ticket_id}`} className="rounded-2xl border border-white/10 bg-white/5 p-4 transition hover:border-cyan-300/40">
        <div className="flex flex-wrap justify-between gap-3"><div><b>{ticket.sportsbook} · {ticket.ticket_type.replaceAll("_", " ")}</b><p className="mt-1 text-sm text-slate-400">{new Date(ticket.confirmed_at).toLocaleString()} · {ticket.number_of_legs} legs · {ticket.strategy}</p></div><span className="tag">{ticket.status}</span></div>
        <p className="mt-2 text-sm text-slate-300">{ticket.stake != null ? `Stake $${Number(ticket.stake).toFixed(2)}` : "Stake not recorded"}{ticket.combined_odds != null ? ` · Odds ${ticket.combined_odds > 0 ? "+" : ""}${ticket.combined_odds}` : " · Combined odds unavailable"}{ticket.potential_payout != null ? ` · Return $${Number(ticket.potential_payout).toFixed(2)}` : ""}</p>
        <div className="mt-3 flex flex-wrap gap-2">{ticket.legs.map((leg, index) => <span key={leg.ticket_leg_id} className="rounded-full bg-white/10 px-2.5 py-1 text-xs text-slate-300">Leg {index + 1}: {leg.leg_status}</span>)}</div>
      </Link>)}</div> : <p className="py-10 text-center text-slate-400">No confirmed tickets yet.</p>}
    </GlowCard>
  </main>;
}
