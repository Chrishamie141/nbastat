'use client';
import { useEffect } from 'react';
import Lenis from '@studio-freight/lenis';
export default function SmoothScroll(){useEffect(()=>{if(matchMedia('(prefers-reduced-motion: reduce)').matches)return;const lenis=new Lenis({duration:.9,smoothWheel:true,wheelMultiplier:.9});let id;const raf=(t)=>{lenis.raf(t);id=requestAnimationFrame(raf)};id=requestAnimationFrame(raf);return()=>{cancelAnimationFrame(id);lenis.destroy()}},[]);return null}
