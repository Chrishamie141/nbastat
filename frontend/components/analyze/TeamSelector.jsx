'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import TeamLogo from '@/components/teams/TeamLogo';
import { api } from '@/lib/api';

export default function TeamSelector({ league = 'nfl', value = '', onChange }) {
  const [teams, setTeams] = useState([]);
  const [loadState, setLoadState] = useState('loading');
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const box = useRef(null);
  useEffect(() => {
    setLoadState('loading');
    api.teams(league).then((response) => { setTeams(response.teams || []); setLoadState('ready'); }).catch(() => { setTeams([]); setLoadState('unavailable'); });
  }, [league]);
  useEffect(() => {
    const closeOutside = (event) => { if (box.current && !box.current.contains(event.target)) setOpen(false); };
    document.addEventListener('mousedown', closeOutside);
    return () => document.removeEventListener('mousedown', closeOutside);
  }, []);
  const selected = teams.find((team) => team.abbreviation === value);
  const filtered = useMemo(() => {
    const normalized = query.toLowerCase().trim();
    return teams.filter((team) => !normalized || [team.name, team.city, team.nickname, team.abbreviation].some((field) => (field || '').toLowerCase().includes(normalized))).slice(0, 12);
  }, [teams, query]);
  function choose(team) { onChange(team?.abbreviation || ''); setQuery(''); setOpen(false); }
  function keydown(event) {
    if (!open && (event.key === 'ArrowDown' || event.key === 'Enter')) { setOpen(true); return; }
    if (event.key === 'ArrowDown') { event.preventDefault(); setActive((index) => Math.min(index + 1, filtered.length - 1)); }
    if (event.key === 'ArrowUp') { event.preventDefault(); setActive((index) => Math.max(index - 1, 0)); }
    if (event.key === 'Enter' && filtered[active]) { event.preventDefault(); choose(filtered[active]); }
    if (event.key === 'Escape') setOpen(false);
  }
  return <div ref={box} className="relative"><label className="mb-2 block text-sm font-semibold text-slate-200" htmlFor="team-filter">Team filter</label><div className="flex items-center gap-2 rounded-2xl border border-white/10 bg-white/5 px-3 py-2 focus-within:outline focus-within:outline-2 focus-within:outline-cyan-300">{selected && <TeamLogo team={selected} size={32} />}<input id="team-filter" role="combobox" aria-expanded={open} aria-controls="team-filter-list" aria-autocomplete="list" disabled={loadState !== 'ready'} placeholder={loadState === 'loading' ? 'Loading teams…' : loadState === 'unavailable' ? 'Team search unavailable' : selected ? `${selected.name} — ${selected.abbreviation}` : 'All teams'} value={query} onFocus={() => setOpen(true)} onKeyDown={keydown} onChange={(event) => { setQuery(event.target.value); setOpen(true); setActive(0); }} className="min-w-0 flex-1 bg-transparent py-2 text-slate-100 placeholder:text-slate-400 focus:outline-none disabled:cursor-not-allowed" />{(selected || query) && <button type="button" onClick={() => choose(null)} className="rounded-full px-2 py-1 text-sm text-slate-300 hover:bg-white/10">Clear</button>}</div>{loadState === 'unavailable' && <p role="alert" className="mt-2 text-sm text-red-200">Team search service unavailable. Try again after the server reconnects.</p>}{open && loadState === 'ready' && <div id="team-filter-list" role="listbox" className="absolute z-30 mt-2 max-h-72 w-full overflow-auto rounded-2xl border border-white/10 bg-slate-950 p-2 shadow-2xl"><button type="button" role="option" aria-selected={!value} onClick={() => choose(null)} className="w-full rounded-xl px-3 py-2 text-left text-slate-100 hover:bg-white/10">All teams</button>{filtered.map((team, index) => <button type="button" key={team.abbreviation} role="option" aria-selected={index === active} onMouseEnter={() => setActive(index)} onClick={() => choose(team)} className={`flex w-full items-center gap-3 rounded-xl px-3 py-2 text-left ${index === active ? 'bg-cyan-300/15' : 'hover:bg-white/10'}`}><TeamLogo team={team} size={32} /><span><span className="block text-sm font-semibold text-slate-100">{team.name}</span><span className="text-xs text-slate-400">{team.abbreviation}</span></span></button>)}{query && !filtered.length && <p className="px-3 py-2 text-sm text-slate-400">No matching teams.</p>}</div>}</div>;
}
