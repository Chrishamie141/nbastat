"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Activity, AlertTriangle, BarChart3, CheckCircle2, Clock3, ExternalLink, Radio, RefreshCw, Users } from "lucide-react";
import GlowCard from "@/components/ui/GlowCard";
import { api } from "@/lib/api";

const EMPTY="—";
const fmt=(value)=>value?new Date(value).toLocaleString():EMPTY;
const number=(value)=>Number(value||0).toLocaleString();

function Pill({value}){
  const text=String(value||"UNKNOWN");
  const good=["HEALTHY","VERIFIED","PUBLISHED","ACTIVE"].includes(text);
  const bad=["FAILED","UNKNOWN","ATTENTION"].includes(text);
  return <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-black tracking-wide ${good?"border-emerald-400/30 bg-emerald-400/10 text-emerald-200":bad?"border-rose-400/30 bg-rose-400/10 text-rose-100":"border-amber-300/30 bg-amber-300/10 text-amber-100"}`}>{text.replaceAll("_"," ")}</span>;
}

function Metric({label,value,detail,icon:Icon}){
  return <GlowCard className="p-5"><div className="flex items-start justify-between gap-3"><div><p className="text-xs font-black uppercase tracking-[.16em] text-slate-400">{label}</p><p className="mt-3 text-3xl font-black tracking-tight text-white">{value??EMPTY}</p>{detail&&<p className="mt-1 text-xs text-slate-400">{detail}</p>}</div>{Icon&&<Icon className="text-cyan-300" size={20}/>}</div></GlowCard>;
}

function Progress({label,value,total}){
  const pct=total?Math.min(100,100*value/total):0;
  return <div><div className="mb-2 flex justify-between text-sm"><span className="text-slate-300">{label}</span><span className="font-bold">{value}/{total}</span></div><div className="h-2 overflow-hidden rounded-full bg-white/10"><div className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-emerald-400" style={{width:`${pct}%`}}/></div></div>;
}

export default function OperationsPage(){
  const [data,setData]=useState(null);const [error,setError]=useState("");const [loading,setLoading]=useState(true);
  const load=useCallback(async()=>{setLoading(true);setError("");try{setData(await api.internal.operations());}catch(e){setError(e.message);}finally{setLoading(false);}},[]);
  useEffect(()=>{load();const id=setInterval(load,60000);return()=>clearInterval(id);},[load]);
  const regular=data?.experiments?.regular||{};const preseason=data?.experiments?.preseason||{};const social=data?.systems?.social||{};const buyers=data?.buyers||{};
  const record=regular.winner_record||{};const coverage=data?.experiments?.marketCoverage||{};
  const funnel=[
    ["Registered",buyers.registered||0],["Active accounts",buyers.active_accounts||0],["Members",buyers.members||0],["Paid members",buyers.paid_members||0]
  ];
  if(loading&&!data)return <main className="mx-auto min-h-screen max-w-7xl px-6 py-16" aria-live="polite">Loading SmartBets Command Center…</main>;
  if(error&&!data)return <main className="mx-auto min-h-screen max-w-3xl px-6 py-16"><h1 className="text-4xl font-black">Command Center</h1><div role="alert" className="mt-6 rounded-2xl border border-rose-400/30 bg-rose-400/10 p-5 text-rose-100">{error}</div></main>;
  return <main className="mx-auto min-h-screen max-w-[1600px] px-4 pb-24 pt-10 sm:px-6">
    <header className="flex flex-wrap items-end justify-between gap-5"><div><p className="text-sm font-black uppercase tracking-[.22em] text-cyan-300">Internal · Executive operations</p><div className="mt-2 flex flex-wrap items-center gap-3"><h1 className="text-4xl font-black tracking-[-.055em] md:text-6xl">SmartBets Command Center</h1><Pill value={data?.overallStatus}/></div><p className="mt-3 max-w-3xl text-slate-300">One view of model evidence, market coverage, X publishing, platform health, and the buyer funnel.</p></div><button onClick={load} disabled={loading} className="btn btn-glass flex items-center gap-2"><RefreshCw size={16} className={loading?"animate-spin":""}/>Refresh</button></header>

    {error&&<div role="alert" className="mt-5 rounded-xl border border-amber-300/30 bg-amber-300/10 p-4 text-sm text-amber-100">Latest refresh failed: {error}</div>}
    <section className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
      <Metric label="System" value={<Pill value={data?.overallStatus}/>} detail={`Updated ${fmt(data?.generatedAt)}`} icon={Activity}/>
      <Metric label="Regular record" value={`${record.WIN||0}-${record.LOSS||0}-${record.PUSH||0}`} detail={`${regular.graded||0} graded predictions`} icon={BarChart3}/>
      <Metric label="Market coverage" value={`${coverage.covered||0}/${coverage.total||0}`} detail="Verified pregame games" icon={Radio}/>
      <Metric label="X source age" value={`${social.sourceAgeMinutes??0}m`} detail={`Next post ${fmt(social.nextRunAt)}`} icon={Clock3}/>
      <Metric label="Members" value={number(buyers.members)} detail={`${number(buyers.paid_members)} paid · ${number(buyers.promotional_members)} promo`} icon={Users}/>
      <Metric label="X posts" value={number(social.postCounts?.PUBLISHED)} detail={`@SmartBetSports · ${social.autoPublish?"automatic":"manual"}`} icon={CheckCircle2}/>
    </section>

    <section className="mt-8 grid gap-4 xl:grid-cols-[1.15fr_.85fr]">
      <GlowCard className="p-6"><div className="flex items-center justify-between"><h2 className="text-2xl font-black">Operating pipeline</h2><Pill value={data?.systems?.api?.status}/></div><div className="mt-6 grid gap-3 md:grid-cols-5">{[
        ["1","Signed evidence",`${social.sourceAgeMinutes??0}m old`],["2","Frozen forecast",`${regular.predictions||0}/${regular.scheduled||0}`],["3","Pregame markets",`${coverage.covered||0}/${coverage.total||0}`],["4","X publishing",social.schedulerEnabled?"Scheduled":"Disabled"],["5","Buyer access",`${buyers.members||0} members`]
      ].map(([step,label,detail])=><div key={step} className="rounded-2xl border border-white/10 bg-white/[.035] p-4"><span className="text-xs font-black text-cyan-300">STEP {step}</span><p className="mt-2 font-bold">{label}</p><p className="mt-1 text-xs text-slate-400">{detail}</p></div>)}</div><div className="mt-6 space-y-4"><Progress label="Frozen regular-season predictions" value={regular.predictions||0} total={regular.scheduled||0}/><Progress label="Verified market coverage" value={coverage.covered||0} total={coverage.total||0}/><Progress label="Grading completion" value={regular.graded||0} total={regular.predictions||0}/></div></GlowCard>
      <GlowCard className="p-6"><div className="flex items-center gap-2"><AlertTriangle size={20} className={data?.alerts?.length?"text-amber-300":"text-emerald-300"}/><h2 className="text-2xl font-black">Attention queue</h2></div>{data?.alerts?.length?<div className="mt-5 space-y-3">{data.alerts.map((alert)=><div key={alert.code} className="rounded-xl border border-amber-300/20 bg-amber-300/[.07] p-4"><div className="flex items-center justify-between gap-3"><p className="font-bold">{alert.code.replaceAll("_"," ")}</p><Pill value={alert.severity}/></div><p className="mt-2 text-sm text-slate-300">{alert.message}</p></div>)}</div>:<div className="mt-5 rounded-2xl border border-emerald-400/20 bg-emerald-400/[.07] p-5"><p className="font-bold text-emerald-200">No active alerts</p><p className="mt-1 text-sm text-slate-300">Source freshness, publishing, and persisted membership state are within guardrails.</p></div>}</GlowCard>
    </section>

    <section className="mt-8 grid gap-4 xl:grid-cols-3">
      <GlowCard className="p-6"><h2 className="text-xl font-black">Experiment portfolio</h2><div className="mt-5 space-y-4"><div className="rounded-xl bg-white/[.04] p-4"><div className="flex justify-between"><b>2026 regular Week {regular.week}</b><Pill value="ACTIVE"/></div><p className="mt-2 text-sm text-slate-300">{regular.predictions||0} frozen · {regular.graded||0} graded · {record.WIN||0}-{record.LOSS||0}-{record.PUSH||0}</p></div><div className="rounded-xl bg-white/[.04] p-4"><div className="flex justify-between"><b>2026 preseason Week 3</b><Pill value="VERIFIED"/></div><p className="mt-2 text-sm text-slate-300">{preseason.predictions||0} frozen · {preseason.record?.WIN||0}-{preseason.record?.LOSS||0}-{preseason.record?.PUSH||0} · {preseason.qualified_wagers||0} wagers</p></div><Link href="/internal/experiments/week3" className="inline-flex items-center gap-2 text-sm font-bold text-cyan-300">Open Week 3 ledger <ExternalLink size={14}/></Link></div></GlowCard>
      <GlowCard className="p-6"><h2 className="text-xl font-black">Buyer funnel</h2><div className="mt-5 space-y-4">{funnel.map(([label,value],index)=><div key={label}><div className="flex justify-between text-sm"><span className="text-slate-300">{label}</span><b>{number(value)}</b></div><div className="mt-2 h-2 rounded-full bg-white/10"><div className="h-full rounded-full bg-cyan-400" style={{width:`${funnel[0][1]?Math.max(3,100*value/funnel[0][1]):0}%`,opacity:1-index*.15}}/></div></div>)}</div><div className="mt-5 grid grid-cols-2 gap-3 text-center text-sm"><div className="rounded-xl bg-white/[.04] p-3"><p className="text-slate-400">Past due</p><b className="text-lg">{number(buyers.past_due)}</b></div><div className="rounded-xl bg-white/[.04] p-3"><p className="text-slate-400">Canceling</p><b className="text-lg">{number(buyers.canceling)}</b></div></div></GlowCard>
      <GlowCard className="p-6"><h2 className="text-xl font-black">X automation</h2><dl className="mt-5 space-y-3 text-sm">{[["Account",social.account],["Scheduler",social.schedulerEnabled?"Enabled":"Disabled"],["Automatic publish",social.autoPublish?"Enabled":"Disabled"],["Dry run",social.dryRun?"On":"Off"],["Latest signed source",fmt(social.latestSourceAt)],["Next cloud run",fmt(social.nextRunAt)]].map(([label,value])=><div key={label} className="flex justify-between gap-4 border-b border-white/10 pb-3"><dt className="text-slate-400">{label}</dt><dd className="text-right font-bold">{value||EMPTY}</dd></div>)}</dl></GlowCard>
    </section>

    <section className="mt-8"><h2 className="text-2xl font-black">Unified activity feed</h2><div className="mt-4 overflow-hidden rounded-2xl border border-white/10 bg-white/[.025]">{data?.feed?.map((item,index)=><div key={`${item.type}-${item.at}-${index}`} className="grid gap-2 border-t border-white/10 p-4 first:border-0 md:grid-cols-[150px_1fr_auto]"><div><span className="text-xs font-black uppercase tracking-wider text-cyan-300">{item.type}</span><p className="mt-1 text-xs text-slate-500">{fmt(item.at)}</p></div><div><p className="font-bold">{item.title}</p><p className="mt-1 text-sm text-slate-300">{item.detail}</p></div><div className="flex items-start gap-3"><Pill value={item.status}/>{item.url&&<a href={item.url} target="_blank" rel="noreferrer" aria-label="Open post on X"><ExternalLink size={16}/></a>}</div></div>)}</div></section>
    <p className="mt-6 text-xs text-slate-500">Internal aggregate view. Member counts exclude identities; prediction accuracy and qualified-wager performance remain separate.</p>
  </main>;
}
