"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { ChevronDown, Trash2, X } from "lucide-react";
import { useParlayTicket } from "@/context/ParlayTicketContext";

export default function PendingBetSlip() {
  const { ticket, removeLeg, clearTicket, hydrated } = useParlayTicket();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  if (!hydrated || (!pathname.startsWith("/parlays") && !ticket.legs.length)) return null;
  return <div className="fixed bottom-20 right-3 z-50 sm:bottom-6 sm:right-6">
    {open && <section aria-label="Pending parlay ticket" className="mb-3 max-h-[72vh] w-[min(94vw,390px)] overflow-auto rounded-3xl border border-cyan-300/30 bg-slate-950/95 p-5 shadow-2xl backdrop-blur-xl">
      <div className="flex items-center justify-between gap-3"><div><p className="text-xs font-bold uppercase tracking-widest text-cyan-300">Bet Slip · {ticket.legs.length}</p><h2 className="mt-1 text-xl font-black">Pending Ticket</h2></div><button aria-label="Close bet slip" onClick={() => setOpen(false)} className="rounded-full p-2 hover:bg-white/10"><X size={18}/></button></div>
      {ticket.legs.length ? <><div className="mt-4 grid gap-3">{ticket.legs.map((leg) => <article key={`${leg.gameId}:${leg.rowId}`} className="rounded-2xl bg-white/5 p-3"><div className="flex justify-between gap-3"><div><p className="text-xs text-slate-400">{leg.matchup}</p><b>{leg.player}</b><p className="text-sm text-slate-300">{leg.side} {leg.line ?? ""} {leg.marketLabel || leg.market}</p></div><button aria-label={`Remove ${leg.player}`} onClick={() => removeLeg(leg.rowId, leg.gameId)} className="self-start rounded-full p-2 text-slate-400 hover:bg-red-500/10 hover:text-red-200"><Trash2 size={16}/></button></div></article>)}</div><div className="mt-5 flex gap-2"><button onClick={clearTicket} className="btn btn-glass flex-1">Clear</button><Link href="/parlays/review" onClick={() => setOpen(false)} className="btn btn-primary flex-1 text-center">Review Ticket</Link></div></> : <div className="py-10 text-center text-sm text-slate-400">Add verified lines while you research. Your pending selections stay here as filters and markets change.</div>}
    </section>}
    <button onClick={() => setOpen((value) => !value)} aria-expanded={open} className="flex items-center gap-3 rounded-full border border-cyan-300/40 bg-cyan-300 px-5 py-3 font-black text-slate-950 shadow-xl"><span>Bet Slip</span><span className="rounded-full bg-slate-950 px-2 py-0.5 text-sm text-cyan-200">{ticket.legs.length}</span><ChevronDown className={open ? "rotate-180" : ""} size={18}/></button>
  </div>;
}
