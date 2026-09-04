'use client';

import Link from 'next/link';
import { Suspense, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import GlowCard from '@/components/ui/GlowCard';
import { useAuth } from '@/components/auth/AuthProvider';
import { api } from '@/lib/api';
import { safeNextPath } from '@/lib/safe-next-path';
import SmartBetSportsLogo from '@/components/branding/SmartBetSportsLogo';

function LoginContent() {
  const { login, ownerLogin, ready, user } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNextPath(params.get('next'));
  const ownerMode = next === '/command-center';
  const [form, setForm] = useState({ email: '', password: '' });
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(false);
  const [show, setShow] = useState(false);

  useEffect(() => {
    if (ready && user?.isInternal) router.replace('/command-center');
    else if (ready && user && !ownerMode) router.replace('/dashboard');
  }, [ownerMode, ready, router, user]);

  async function submit(event) {
    event.preventDefault();
    setErr('');
    setLoading(true);
    try {
      const auth = await (ownerMode ? ownerLogin(form) : login(form));
      if (ownerMode || auth?.user?.isInternal) {
        router.replace('/command-center');
        return;
      }
      const entitlement = await api.billing.entitlements();
      const active = ['active', 'trialing'].includes(entitlement?.status) || Boolean(entitlement?.hasFullAccess);
      router.replace(active ? next : `/subscribe?next=${encodeURIComponent(next)}`);
    } catch (error) {
      if (error?.status === 401) setErr('Invalid email or password.');
      else setErr(error?.message || 'Login is temporarily unavailable. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return <main className="mx-auto flex min-h-[calc(100vh-140px)] max-w-md items-center px-6 py-8">
    <GlowCard className="w-full p-6">
      <SmartBetSportsLogo size={34} />
      <h1 className="mt-4 text-3xl font-black tracking-[-.05em]">{ownerMode ? 'Command Center' : 'Log in'}</h1>
      {ownerMode && <p className="mt-1 text-sm text-slate-400">Owner Login</p>}
      <form onSubmit={submit} className="mt-5 grid gap-4">
        <label className="grid gap-1 text-sm font-semibold">Email<input required type="email" autoComplete="email" className="focus-ring rounded-xl border border-white/10 bg-white/5 px-4 py-2.5" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
        <label className="grid gap-1 text-sm font-semibold">Password<span className="flex rounded-xl border border-white/10 bg-white/5 focus-within:outline focus-within:outline-2 focus-within:outline-cyan-300"><input required type={show ? 'text' : 'password'} autoComplete="current-password" className="min-w-0 flex-1 bg-transparent px-4 py-2.5 outline-none" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /><button type="button" className="px-3 text-xs text-cyan-200" onClick={() => setShow(!show)}>{show ? 'Hide' : 'Show'}</button></span><Link className="mt-1 text-right text-xs text-cyan-200" href="/forgot-password">Forgot password?</Link></label>
        {err && <p className="rounded-xl bg-red-500/10 p-3 text-sm text-red-200">{err}</p>}
        <button disabled={loading} className="btn btn-primary disabled:opacity-60">{loading ? 'Logging in…' : ownerMode ? 'Owner Login' : 'Log In'}</button>
      </form>
      {!ownerMode && <p className="mt-4 text-sm text-slate-400">Need an account? <Link className="text-cyan-200" href="/register">Create Account</Link></p>}
    </GlowCard>
  </main>;
}

export default function Login() {
  return <Suspense fallback={<main className="mx-auto flex min-h-[calc(100vh-140px)] max-w-md items-center px-6 py-8"><GlowCard className="w-full p-6">Loading login…</GlowCard></main>}><LoginContent /></Suspense>;
}
