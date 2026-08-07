import SubscriptionGuard from '@/components/auth/SubscriptionGuard';
import NflGameBreakdown from '@/components/games/NflGameBreakdown';

export default function NflGamePage({ params }) {
  return <SubscriptionGuard><NflGameBreakdown gameId={params.gameId} /></SubscriptionGuard>;
}
