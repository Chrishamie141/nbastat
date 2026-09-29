import NflGameBreakdown from '@/components/games/NflGameBreakdown';

export default async function OwnerNflGamePage({ params }) {
  params = await params;
  return <NflGameBreakdown gameId={params.gameId} ownerMode />;
}
