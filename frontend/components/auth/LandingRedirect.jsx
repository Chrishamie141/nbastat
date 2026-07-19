'use client';
import {useEffect} from 'react';
import {useRouter} from 'next/navigation';
import {useAuth} from '@/components/auth/AuthProvider';
import {useSubscription} from '@/hooks/useSubscription';
import {brand} from '@/lib/brand';

export default function LandingRedirect({children}){const{ready,isAuthenticated}=useAuth();const{loading,hasFullAccess,subscription}=useSubscription();const router=useRouter();const active=hasFullAccess||['active','trialing'].includes(subscription?.status);useEffect(()=>{if(ready&&isAuthenticated&&!loading&&active)router.replace('/dashboard')},[ready,isAuthenticated,loading,active,router]);if(!ready||(isAuthenticated&&loading))return <main className="mx-auto min-h-[55vh] max-w-5xl px-6 pt-28"><div className="glass rounded-2xl p-6 text-gray-300">Loading {brand.name}…</div></main>;if(isAuthenticated&&active)return null;return children}
