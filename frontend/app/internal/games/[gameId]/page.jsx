import NflGameBreakdown from '@/components/games/NflGameBreakdown';

export default function OwnerNflGamePage({ params }) {
  return <NflGameBreakdown gameId={params.gameId} ownerMode />;
}
