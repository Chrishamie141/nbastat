'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { api } from '@/lib/api';

const AuthContext = createContext(null);
const protectedRoutes = ['/dashboard', '/analyze', '/history', '/performance', '/account', '/parlays', '/fantasy', '/players', '/games', '/predictions'];

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);
  const router = useRouter();
  const path = usePathname();

  useEffect(() => {
    api.auth.me().then((data) => setUser(data.user)).catch(() => setUser(null)).finally(() => setReady(true));
  }, []);

  useEffect(() => {
    if (!ready) return;
    if (!user && protectedRoutes.some((route) => path?.startsWith(route))) router.replace('/login');
  }, [path, ready, router, user]);

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

  const logout = useCallback(async () => {
    try {
      await api.auth.logout();
    } finally {
      setUser(null);
      router.replace('/login');
    }
  }, [router]);

  const value = useMemo(() => ({ user, ready, loading: !ready, isAuthenticated: Boolean(user), register, login, logout }), [login, logout, ready, register, user]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
}
