"use client";
import Link from "next/link";

export default function PageError({ reset }) {
  return <main className="mx-auto min-h-[65vh] max-w-2xl px-6 py-20">
    <h1 className="text-3xl font-bold">This page could not load</h1>
    <p role="alert" className="mt-4 text-slate-300">Please try again. If the problem continues, return to the dashboard. Your saved history has not been changed.</p>
    <div className="mt-6 flex flex-wrap gap-3"><button onClick={reset} className="btn btn-primary">Try again</button><Link href="/dashboard" className="btn btn-glass">Back to dashboard</Link></div>
  </main>;
}
