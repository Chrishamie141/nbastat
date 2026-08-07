'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

export default function CatalogSearch() {
  const [query, setQuery] = useState('');
  const [state, setState] = useState('idle');
  const [result, setResult] = useState(null);
  useEffect(() => {
    const normalized = query.trim();
    if (normalized.length < 2) { setState('idle'); setResult(null); return undefined; }
    setState('loading');
    const timer = window.setTimeout(() => {
      api.search(normalized).then((data) => { setResult(data); setState('ready'); }).catch(() => { setResult(null); setState('unavailable'); });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  return <div className="relative mt-6"><label htmlFor="catalog-search" className="sr-only">Search teams, players, and games</label><input id="catalog-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search teams, players, or games" className="w-full rounded-2xl border border-white/10 bg-white/5 px-5 py-3 text-white placeholder:text-slate-400 focus:outline focus:outline-2 focus:outline-cyan-300" />{state !== 'idle' && <div className="absolute z-30 mt-2 max-h-80 w-full overflow-auto rounded-2xl border border-white/10 bg-slate-950 p-3 shadow-2xl">{state === 'loading' ? <p className="p-2 text-sm text-slate-400">Data still loading…</p> : state === 'unavailable' ? <p role="alert" className="p-2 text-sm text-red-200">Search service unavailable. This is not a zero-result search.</p> : result?.items?.length ? <><div className="grid gap-1">{result.items.map((item) => item.href ? <Link key={`${item.type}-${item.id}`} href={item.href} className="rounded-xl p-3 hover:bg-white/10"><b>{item.name}</b><span className="ml-2 text-xs uppercase text-slate-400">{item.type} · {item.status || item.league || ''}</span></Link> : <div key={`${item.type}-${item.id}`} className="rounded-xl p-3"><b>{item.name}</b><span className="ml-2 text-xs uppercase text-slate-400">{item.type} · {item.abbreviation || item.team || item.league || ''}</span></div>)}</div>{result.playerContext?.fallbackUsed && <p className="mt-2 border-t border-white/10 pt-2 text-xs text-amber-100">Player results use the {result.playerContext.contextSeasonUsed} completed-season index because the current-season index is not available.</p>}{result.errors?.games && <p className="mt-2 text-xs text-amber-100">{result.errors.games.message}</p>}</> : <p className="p-2 text-sm text-slate-400">No matching results.</p>}</div>}</div>;
}
