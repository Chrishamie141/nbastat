import { notFound } from 'next/navigation';
import SubscriptionGuard from '@/components/auth/SubscriptionGuard';
import NbaGameBreakdown from '@/components/games/NbaGameBreakdown';

export default async function NbaGamePage({ params, searchParams }) {
  params = await params;
  searchParams = await searchParams;
  if (!/^espn-\d{6,18}$/.test(params.gameId)) notFound();
  return <SubscriptionGuard><NbaGameBreakdown gameId={params.gameId} season={searchParams?.season || ''} /></SubscriptionGuard>;
}
