'use client';

import {useEffect,useState} from 'react';
import {AnimatePresence,motion} from 'framer-motion';
import Link from 'next/link';
import {useRouter} from 'next/navigation';
import SubscriptionGuard from '@/components/auth/SubscriptionGuard';
import GlowCard from '@/components/ui/GlowCard';
import AnalysisResult from '@/components/results/AnalysisResult';
import TeamSelector from '@/components/analyze/TeamSelector';
import {api} from '@/lib/api';

const NFL_ANALYSES=[
  {id:'winners',title:'Game Winners',eyebrow:'Who will win?',description:'See SmartBetSports predictions for every game this week.'},
  {id:'same_game',title:'Same Game Parlay',eyebrow:'One matchup.',description:'Combine team, player, and game-market selections from one game.',href:'/parlays?mode=same_game'},
  {id:'multi_game',title:'Multi-Game Parlay',eyebrow:'Across the week.',description:'Combine team and player picks from multiple games.',href:'/parlays?mode=multi_game'},
  {id:'fantasy',title:'Fantasy Football',eyebrow:'Build your roster.',description:'Create, analyze, and save a custom depth chart.',href:'/fantasy'},
];
const NBA_ANALYSES=['Single Player Prediction','Default Roster Prediction','Team Auto-Roster Prediction','Best Bets Report','Auto Parlay Builder'];

export default function Analyze(){return <SubscriptionGuard><Flow/></SubscriptionGuard>}

function Flow(){
  const router=useRouter();
  const[step,setStep]=useState(1),[sport,setSport]=useState(''),[action,setAction]=useState('');
  const[opts,setOpts]=useState({team:'',player:''}),[result,setResult]=useState(null),[loading,setLoading]=useState(false),[error,setError]=useState(''),[sm,setSm]=useState(null);
  useEffect(()=>{api.sportsMode().then(value=>{setSm(value);if(value.mode==='nfl'||value.mode==='nba'){setSport(value.mode.toUpperCase());setStep(2)}}).catch(()=>{})},[]);
  const restart=()=>{setAction('');setResult(null);setError('');if(sm?.mode==='nfl'||sm?.mode==='nba'){setSport(sm.mode.toUpperCase());setStep(2)}else{setSport('');setStep(1)}};
  async function runNBA(){setLoading(true);setError('');try{let response;if(action.includes('Single'))response=await api.nba.player(opts);else if(action.includes('Default'))response=await api.nba.roster(opts);else if(action.includes('Team'))response=await api.nba.team(opts);else if(action.includes('Best'))response=await api.nba.bestBets();else response=await api.nba.parlay(opts);setResult(response);setStep(4)}catch(exc){setError(exc.message||'Analysis failed safely.')}finally{setLoading(false)}}
  return <main className="mx-auto min-h-screen max-w-5xl px-4 pb-28 pt-10 sm:px-6 md:pt-14">
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-4xl font-black tracking-[-.06em] md:text-5xl">Start New Analysis</h1><p className="mt-2 text-gray-400">{sport||'Choose a sport'}{action?` · ${action}`:''}</p></div><Link href="/dashboard" className="btn btn-glass">Cancel</Link></div>
    <GlowCard className="mb-5 p-4"><p className="text-sm font-semibold text-slate-300">Step {step} of 4</p><div className="mt-2 h-2 overflow-hidden rounded-full bg-white/10"><div className="h-full rounded-full bg-cyan-300 transition-all" style={{width:`${step*25}%`}}/></div></GlowCard>
    {sm?.mode==='offseason'&&step!==4?<GlowCard className="p-6"><h2 className="text-2xl font-bold">Supported analysis is paused</h2><p className="mt-3 text-slate-300">New NFL and NBA analysis is paused while both leagues are outside the supported window. Previous activity remains available.</p><div className="mt-5 flex gap-3"><Link className="btn btn-glass" href="/history">History</Link><Link className="btn btn-glass" href="/performance">Performance</Link></div></GlowCard>:<AnimatePresence mode="wait"><motion.div key={`${step}-${action}`} initial={{opacity:0,y:12}} animate={{opacity:1,y:0}} exit={{opacity:0,y:-12}}>
      {step===1&&<SportChoice onSelect={value=>{setSport(value);setStep(2)}} active={sm?.activeLeagues||[]}/>}
      {step===2&&<AnalysisChoice sport={sport} unavailable={Boolean(sm&&sport&&!sm.activeLeagues?.includes(sport.toLowerCase()))} onNFL={item=>{if(item.href)router.push(item.href);else{setAction('Game Winners');setStep(3)}}} onNBA={item=>{setAction(item);setStep(3)}} onBack={sm?.mode==='both'?()=>setStep(1):null}/>}
      {step===3&&sport==='NFL'&&action==='Game Winners'&&<WinnerViewChoice onBack={()=>setStep(2)} router={router}/>}
      {step===3&&sport==='NBA'&&<NBAOptions action={action} opts={opts} setOpts={setOpts} loading={loading} error={error} run={runNBA} back={()=>setStep(2)}/>}
      {step===4&&<AnalysisResult result={result} onRestart={restart}/>}
    </motion.div></AnimatePresence>}
    <Link href="/analyze/classic" className="btn btn-glass mt-6">Classic analysis tools &amp; fantasy draft board</Link>
  </main>
}

function SportChoice({onSelect,active}){return <GlowCard className="p-6"><h2 className="text-2xl font-bold">Select a sport</h2><div className="mt-5 grid gap-4 md:grid-cols-2">{[['NFL','Game winners · parlays · fantasy'],['NBA','Player · team · best bets · parlays']].filter(([name])=>!active.length||active.includes(name.toLowerCase())).map(([name,copy])=><button key={name} onClick={()=>onSelect(name)} className="glass rounded-3xl p-6 text-left hover:border-violet-400/60 focus:outline focus:outline-2 focus:outline-cyan-300"><span className="text-3xl font-black">{name}</span><span className="mt-3 block text-gray-300">{copy}</span></button>)}</div></GlowCard>}

function AnalysisChoice({sport,unavailable,onNFL,onNBA,onBack}){return <GlowCard className="p-6"><h2 className="text-2xl font-bold">Choose an analysis</h2><p className="mt-2 text-slate-400">Create something new. Previous activity and model evaluation remain in History and Performance.</p>{unavailable?<p className="mt-4 rounded-2xl bg-white/5 p-4 text-slate-300">{sport} analysis is currently paused while the league is outside the supported prediction window.</p>:sport==='NFL'?<div className="mt-6 grid gap-4 md:grid-cols-2">{NFL_ANALYSES.map(item=><button key={item.id} onClick={()=>onNFL(item)} className="group min-h-44 rounded-3xl border border-white/10 bg-gradient-to-br from-white/[.08] to-white/[.03] p-6 text-left transition hover:-translate-y-1 hover:border-cyan-300/60 hover:bg-cyan-300/10 focus:outline focus:outline-2 focus:outline-cyan-300"><span className="text-xs font-black uppercase tracking-[.18em] text-cyan-300">{item.eyebrow}</span><span className="mt-3 block text-2xl font-black text-white">{item.title}</span><span className="mt-2 block leading-6 text-slate-300">{item.description}</span><span className="mt-4 block text-sm font-bold text-cyan-100">Select analysis →</span></button>)}</div>:<div className="mt-5 grid gap-3 md:grid-cols-2">{NBA_ANALYSES.map(item=><button key={item} onClick={()=>onNBA(item)} className="rounded-2xl border border-white/10 bg-white/5 p-5 text-left hover:border-cyan-300/50 hover:bg-white/10 focus:outline focus:outline-2 focus:outline-cyan-300">{item}</button>)}</div>}{onBack&&<button onClick={onBack} className="btn btn-glass mt-5">Back</button>}</GlowCard>}

function WinnerViewChoice({onBack,router}){const choices=[['All Weekly Picks','Every NFL matchup, prediction, price, and available edge.','/parlays?mode=winners&day=ALL'],['Sunday Picks','Focus the board on the Sunday slate.','/parlays?mode=winners&day=SUNDAY'],['Select Individual Game','Open the weekly schedule and inspect one matchup.','/games']];return <GlowCard className="p-6"><h2 className="text-2xl font-bold">How do you want to view Game Winners?</h2><p className="mt-2 text-slate-400">Inspect predictions without creating a parlay. Winner selections are optional.</p><div className="mt-6 grid gap-4 md:grid-cols-3">{choices.map(([title,description,href])=><button key={title} onClick={()=>router.push(href)} className="rounded-3xl border border-white/10 bg-white/5 p-5 text-left transition hover:border-cyan-300/60 hover:bg-cyan-300/10 focus:outline focus:outline-2 focus:outline-cyan-300"><span className="block text-xl font-black">{title}</span><span className="mt-2 block text-sm leading-5 text-slate-300">{description}</span><span className="mt-4 block text-sm font-bold text-cyan-100">Continue →</span></button>)}</div><button onClick={onBack} className="btn btn-glass mt-6">Back</button></GlowCard>}

function NBAOptions({action,opts,setOpts,loading,error,run,back}){return <GlowCard className="mx-auto max-w-3xl p-6"><h2 className="text-2xl font-bold">{action}</h2><div className="mt-5 grid gap-5">{action.includes('Single')&&<input required aria-label="NBA player name" placeholder="NBA player name" value={opts.player} onChange={event=>setOpts({...opts,player:event.target.value})} className="rounded-2xl border border-white/10 bg-white/5 px-4 py-3"/>}{action.includes('Team')&&<TeamSelector league="nba" value={opts.team} onChange={team=>setOpts({...opts,team})}/>} {action.includes('Default')&&<p className="rounded-2xl bg-white/5 p-4 text-gray-300">This analysis uses the configured application roster.</p>}</div>{error&&<p className="mt-4 rounded-xl bg-red-500/10 p-3 text-red-200">{error}</p>}<div className="mt-6 flex flex-wrap gap-3 border-t border-white/10 pt-5"><button disabled={loading} onClick={back} className="btn btn-glass">Back</button><button disabled={loading} onClick={run} className="btn btn-primary">{loading?'Analysis running…':error?'Retry Analysis':action.includes('Player')?'Generate Player Prediction':action.includes('Team')||action.includes('Roster')?'Generate Team Analysis':action.includes('Best')?'Load Best Bets':'Generate Parlay'}</button></div></GlowCard>}
