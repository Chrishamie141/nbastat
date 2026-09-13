import { Clock3, Image as ImageIcon, Send, ShieldCheck, Video } from "lucide-react";
import GlowCard from "@/components/ui/GlowCard";

const value = (input) => input ?? "—";

export function StatusPill({ status }) {
  const text = String(status || "UNKNOWN").toUpperCase();
  const safe = ["READY", "ACTIVE", "PUBLISHED", "CONFIGURED", "DISCOVERY", "SUCCEEDED"].includes(text);
  const danger = ["FAILED", "UNKNOWN", "NEEDS_SETUP"].includes(text);
  return <span className={`rounded-full border px-2.5 py-1 text-xs font-black ${safe ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-200" : danger ? "border-rose-400/30 bg-rose-400/10 text-rose-100" : "border-amber-300/30 bg-amber-300/10 text-amber-100"}`}>{text.replaceAll("_", " ")}</span>;
}

export default function SocialStatus({ summary }) {
  const metrics = [
    ["Posts today", summary.postsToday, Send], ["Posts this week", summary.postsThisWeek, Send],
    ["Queued", summary.queued, Clock3],
    ["Generating", summary.generating, Clock3], ["Review", summary.reviewRequired, ShieldCheck],
    ["Images today", summary.imagesGeneratedToday, ImageIcon],
    ["Videos today", summary.videosGeneratedToday, Video], ["Failed", summary.failed, ShieldCheck],
  ];
  return <>
    <div className="flex flex-wrap items-center gap-3">
      <span className="text-xs font-black uppercase tracking-wider text-slate-500">X Automation</span>
      <StatusPill status={summary.engineState} />
      <span className="text-xs font-black uppercase tracking-wider text-slate-500">X API</span>
      <StatusPill status={summary.xAccountState} />
      <span className="text-xs font-black uppercase tracking-wider text-slate-500">Scheduler</span>
      <StatusPill status={summary.schedulerState} />
      <span className="text-xs font-black uppercase tracking-wider text-slate-500">Worker</span>
      <StatusPill status={summary.workerState} />
      {summary.publicWritesBlocked && <span className="text-sm font-bold text-amber-200">Public writes are blocked</span>}
    </div>
    <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8">
      {metrics.map(([label, metric, Icon]) => <GlowCard key={label} className="p-4">
        <div className="flex items-center justify-between"><p className="text-xs font-black uppercase tracking-wider text-slate-400">{label}</p><Icon size={16} className="text-cyan-300" /></div>
        <p className="mt-3 text-2xl font-black">{value(metric)}</p>
      </GlowCard>)}
    </div>
  </>;
}
