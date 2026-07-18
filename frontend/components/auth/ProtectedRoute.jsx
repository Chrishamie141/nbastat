'use client';
import {useEffect} from 'react';import {useRouter} from 'next/navigation';import {useAuth} from './AuthProvider';
export default function ProtectedRoute({children}){const{ready,isAuthenticated}=useAuth();const router=useRouter();useEffect(()=>{if(ready&&!isAuthenticated)router.replace('/login')},[ready,isAuthenticated,router]);if(!ready)return <main className="mx-auto min-h-screen max-w-5xl px-6 pt-36"><div className="glass rounded-3xl p-8 text-gray-300">Loading secure workspace…</div></main>;if(!isAuthenticated)return null;return children}
