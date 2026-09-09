'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { api } from '@/lib/api';

const AuthContext = createContext(null);
const protectedRoutes = ['/dashboard', '/analyze', '/history', '/performance', '/account', '/parlays', '/fantasy', '/players', '/games', '/predictions', '/internal', '/command-center', '/nfl'];
const ownerRoutes = ['/internal', '/command-center'];

const matches = (path, prefixes) => prefixes.some((route) => path === route || path?.startsWith(`${route}/`));

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);
  const [authError, setAuthError] = useState('');
  const router = useRouter();
  const path = usePathname();

  useEffect(() => {
    api.auth.me()
      .then((data) => { setUser(data.user); setAuthError(''); })
      .catch((error) => {
        if (error?.status === 401) setUser(null);
        else setAuthError('Account service is temporarily unavailable. Please try again.');
      })
      .finally(() => setReady(true));
  }, []);

  useEffect(() => {
    if (!ready || authError) return;
    if (!user && matches(path, protectedRoutes)) {
      const next = matches(path, ownerRoutes) ? '/command-center' : path;
      router.replace(`/login?next=${encodeURIComponent(next)}`);
    } else if (user && !user.isInternal && matches(path, ownerRoutes)) {
      router.replace('/dashboard');
    }
  }, [authError, path, ready, router, user]);

  const register = useCallback(async (form) => {
    if (form.password !== form.confirm) throw new Error('Passwords do not match.');
    if ((form.password || '').length < 8) throw new Error('Password must contain at least 8 characters.');
    const data = await api.auth.register({ name: form.name, email: form.email, password: form.password });
    setUser(data.user);
    return data;
  }, []);

  const login = useCallback(async (form) => {
    const data = await api.auth.login({ email: form.email, password: form.password });
    setUser(data.user);
    return data;
  }, []);

  const ownerLogin = useCallback(async (form) => {
    const data = await api.auth.ownerLogin({ email: form.email, password: form.password });
    setUser(data.user);
    return data;
  }, []);

  const logout = useCallback(async () => {
    try { await api.auth.logout(); }
    finally { setUser(null); router.replace('/login'); }
  }, [router]);

  const value = useMemo(() => ({ user, ready, loading: !ready, authError, isAuthenticated: Boolean(user), register, login, ownerLogin, logout }), [authError, login, logout, ownerLogin, ready, register, user]);
  const protectedPath = matches(path, protectedRoutes);
  const ownerPath = matches(path, ownerRoutes);
  let content = children;
  if (protectedPath && !ready) content = <main className="mx-auto min-h-screen max-w-3xl px-6 pt-20 text-slate-200">Checking your session…</main>;
  else if (protectedPath && authError) content = <main className="mx-auto min-h-screen max-w-3xl px-6 pt-20"><h1 className="text-2xl font-bold">Account service unavailable</h1><p className="mt-3 text-slate-300">We could not verify your session. Please retry shortly.</p></main>;
  else if (protectedPath && (!user || (ownerPath && !user.isInternal))) content = <main className="mx-auto min-h-screen max-w-3xl px-6 pt-20 text-slate-200">Redirecting to a safe page…</main>;
  return <AuthContext.Provider value={value}>{content}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
}
