'use client';
import {useEffect} from 'react';import {useRouter} from 'next/navigation';import {useAuth} from './AuthProvider';import {brand} from '@/lib/brand';
export default function ProtectedRoute({children}){const{ready,isAuthenticated}=useAuth();const router=useRouter();useEffect(()=>{if(ready&&!isAuthenticated)router.replace('/login')},[ready,isAuthenticated,router]);if(!ready)return <main className="mx-auto min-h-[55vh] max-w-5xl px-6 pt-28"><div className="glass rounded-2xl p-6 text-gray-300">Loading {brand.name}…</div></main>;if(!isAuthenticated)return null;return children}
