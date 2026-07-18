export function mappedLogoUrl(league, abbreviation) {
  if (!league || !abbreviation) return null;
  const path = league.toLowerCase() === 'nfl' ? 'nfl' : 'nba';
  return `https://a.espncdn.com/i/teamlogos/${path}/500/${abbreviation.toLowerCase()}.png`;
}
