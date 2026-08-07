import Link from 'next/link';
import GlowCard from '@/components/ui/GlowCard';

export default function GameNotFound() {
  return <main className="mx-auto min-h-screen max-w-4xl px-6 py-16"><GlowCard className="p-8"><h1 className="text-3xl font-black">Game not found</h1><p className="mt-3 text-slate-300">This game ID is invalid or is no longer available from the schedule provider.</p><Link href="/dashboard" className="btn btn-primary mt-6">Back to dashboard</Link></GlowCard></main>;
}
