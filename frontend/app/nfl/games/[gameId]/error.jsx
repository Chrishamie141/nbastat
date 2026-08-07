'use client';

export default function Error({ reset }) {
  return <main className="mx-auto min-h-screen max-w-5xl px-6 py-16"><div className="rounded-3xl border border-red-300/20 bg-red-300/10 p-8"><h1 className="text-2xl font-black">Game breakdown unavailable</h1><p className="mt-2 text-slate-300">The route could not finish rendering. You can retry without leaving this game.</p><button type="button" onClick={reset} className="btn btn-primary mt-5">Try again</button></div></main>;
}
