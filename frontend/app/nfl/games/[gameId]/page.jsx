import SubscriptionGuard from '@/components/auth/SubscriptionGuard';
import NflGameBreakdown from '@/components/games/NflGameBreakdown';
import { notFound } from 'next/navigation';

export default function NflGamePage({ params }) {
  if (!/^\d{6,18}$/.test(params.gameId)) notFound();
  return <SubscriptionGuard><NflGameBreakdown gameId={params.gameId} /></SubscriptionGuard>;
}
