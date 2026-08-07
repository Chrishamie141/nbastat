import { redirect } from 'next/navigation';

export default function LegacyGamePage({ params }) {
  if (/^(?:espn-)?\d{6,18}$/.test(params.slug)) redirect(`/nfl/games/${params.slug.replace('espn-', '')}`);
  redirect('/dashboard');
}
