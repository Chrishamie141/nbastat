"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import SubscriptionGuard from "@/components/auth/SubscriptionGuard";
import GlowCard from "@/components/ui/GlowCard";
import { api } from "@/lib/api";

const decimal = (value, digits = 1) => value == null ? "Unavailable" : Number(value).toFixed(digits);

export default function TicketDetailPage() {
  return <SubscriptionGuard><Detail /></SubscriptionGuard>;
}

function Detail() {
  const { ticketId } = useParams();
  const [ticket, setTicket] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (ticketId) api.nfl.parlayTicket(ticketId).then(setTicket).catch((exc) => setError(exc.message));
  }, [ticketId]);
  if (error) return <main className="mx-auto max-w-3xl px-4 py-14"><p role="alert" className="text-red-200">{error}</p></main>;
  if (!ticket) return <main className="mx-auto max-w-3xl px-4 py-14"><div className="h-48 animate-pulse rounded-3xl bg-white/10" /></main>;
  return <main className="mx-auto min-h-screen max-w-5xl px-4 pb-24 pt-12 sm:px-6">
    <Link href="/parlays/tickets" className="text-sm font-bold text-cyan-300">← Ticket History</Link>
    <div className="mt-5 flex flex-wrap justify-between gap-4"><div><p className="text-sm uppercase tracking-widest text-slate-400">{ticket.sportsbook} · {ticket.strategy}</p><h1 className="mt-2 text-4xl font-black">{ticket.ticket_type.replaceAll("_", " ")}</h1><p className="mt-2 text-slate-400">Confirmed {new Date(ticket.confirmed_at).toLocaleString()}</p><p className="mt-1 text-sm text-slate-400">{ticket.stake == null ? "Stake not recorded" : `Stake $${Number(ticket.stake).toFixed(2)}`} · {ticket.combined_odds == null ? "Combined odds unavailable" : `Odds ${ticket.combined_odds > 0 ? "+" : ""}${ticket.combined_odds}`}{ticket.potential_payout == null ? "" : ` · Potential return $${Number(ticket.potential_payout).toFixed(2)}`}</p></div><span className="tag h-fit">{ticket.status}</span></div>
    <div className="mt-7 grid gap-4">{ticket.legs.map((leg) => <GlowCard key={leg.ticket_leg_id} className="p-5"><div className="flex flex-wrap justify-between gap-3"><div><p className="text-xs uppercase tracking-wider text-slate-500">{leg.matchup}</p><h2 className="mt-1 text-xl font-black">{leg.player_name || leg.team}</h2><p className="mt-1 text-cyan-100">{leg.selection} {leg.line ?? ""} {leg.market} · {leg.odds == null ? "Price unavailable" : `${leg.odds > 0 ? "+" : ""}${leg.odds}`}</p></div><span className="tag">{leg.leg_status}</span></div><div className="mt-4 grid gap-2 text-sm sm:grid-cols-3"><p>Projection: <b>{decimal(leg.smartbet_projection)}</b></p><p>Confidence: <b>{leg.confidence == null ? "Unavailable" : `${decimal(leg.confidence)}%`}</b></p><p>Recommendation: <b>{leg.recommendation_state.replaceAll("_", " ")}</b></p><p>Model: <b>{leg.model_version || "Unavailable"}</b></p><p>Selected: <b>{new Date(leg.selected_at).toLocaleString()}</b></p><p>Edge: <b>{decimal(leg.edge)}</b></p><p>Final result: <b>{leg.final_result == null ? "Not graded" : decimal(leg.final_result)}</b></p></div></GlowCard>)}</div>
  </main>;
}
