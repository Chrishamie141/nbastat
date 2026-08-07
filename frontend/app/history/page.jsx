'use client';
import { useEffect, useState } from 'react';
import SubscriptionGuard from '@/components/auth/SubscriptionGuard';
import GlowCard from '@/components/ui/GlowCard';
import { api } from '@/lib/api';

const tabs = ['All', 'NFL', 'NBA', 'Predictions', 'Parlays', 'Graded', 'Ungraded'];
export default function History() { return <SubscriptionGuard><Inner /></SubscriptionGuard>; }
function Inner() {
  const [tab, setTab] = useState('All');
  const [rows, setRows] = useState([]);
  const [state, setState] = useState('loading');
  useEffect(() => { setState('loading'); api.history(tab === 'All' ? '' : `?tab=${tab}`).then((data) => { setRows(data.items || []); setState('ready'); }).catch(() => { setRows([]); setState('unavailable'); }); }, [tab]);
  return <main className="mx-auto min-h-screen max-w-6xl px-6 pt-32"><h1 className="text-5xl font-black tracking-[-.06em]">History</h1><div className="mt-6 flex flex-wrap gap-2">{tabs.map((item) => <button key={item} disabled={state === 'loading'} onClick={() => setTab(item)} className={`rounded-full px-4 py-2 disabled:opacity-60 ${tab === item ? 'bg-violet-500 text-white' : 'bg-white/10 text-gray-300'}`}>{item}</button>)}</div><GlowCard className="mt-6 p-6">{state === 'loading' ? <p className="text-gray-400">Loading stored records…</p> : state === 'unavailable' ? <p role="alert" className="text-red-200">History service unavailable. Your records may still exist; retry after the server reconnects.</p> : rows.length ? <div className="grid gap-3">{rows.map((row, index) => <div key={index} className="rounded-2xl bg-white/5 p-4"><div className="flex flex-wrap justify-between gap-3"><b>{row.date} · {row.sport} · {row.action}</b><span className="tag">{row.resultStatus}</span></div><p className="mt-2 text-gray-400">{row.summary}</p><p className="mt-2 text-sm text-gray-500">Data mode: {row.dataMode}</p></div>)}</div> : <p className="text-gray-400">No stored records found for this filter.</p>}</GlowCard></main>;
}
