export const conflictKey = (leg) => `${leg.gameId}|${leg.playerId || leg.player || leg.team || ""}|${leg.market}|${leg.line ?? ""}`.toLowerCase();

export function addLegToTicket(ticket, leg, meta, selectedAt = new Date().toISOString()) {
  if (ticket.legs.length && ticket.ticketType === "SAME_GAME_PARLAY" &&
      (meta.ticketType !== "SAME_GAME_PARLAY" || ticket.legs[0].gameId !== leg.gameId))
    return { ticket, result: { ok: false, reason: "SGP_GAME_CONFLICT" } };
  if (ticket.legs.length && ticket.sportsbook && ticket.sportsbook !== leg.bookmaker)
    return { ticket, result: { ok: false, reason: "SPORTSBOOK_CONFLICT" } };
  if (ticket.legs.some((item) => item.rowId === leg.rowId && item.gameId === leg.gameId))
    return { ticket, result: { ok: false, reason: "DUPLICATE" } };
  const key = conflictKey(leg);
  const conflict = ticket.legs.find((item) => conflictKey(item) === key && item.side !== leg.side);
  if (conflict) return { ticket, result: { ok: false, reason: "OPPOSING_SELECTION", conflict } };
  return { ticket: { ...ticket, ...meta, sportsbook: leg.bookmaker || meta.sportsbook, legs: [...ticket.legs, { ...leg, selectedAt }] }, result: { ok: true } };
}
