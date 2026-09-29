"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { addLegToTicket } from "@/lib/parlay-ticket.mjs";

const TicketContext = createContext(null);
export const TICKET_STORAGE_KEY = "smartbets:pending-parlay-ticket:v1";
export const PARLAY_PREFERENCES_KEY = "smartbets:parlay-preferences:v1";
const emptyTicket = { ticketType: null, sportsbook: "", strategy: "BALANCED", season: null, seasonType: "regular", week: null, legs: [] };

export function ParlayTicketProvider({ children }) {
  const [ticket, setTicket] = useState(emptyTicket);
  const [preferences, setPreferences] = useState({ sportsbook: "DraftKings", strategy: "BALANCED" });
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(TICKET_STORAGE_KEY) || "null");
      const prefs = JSON.parse(localStorage.getItem(PARLAY_PREFERENCES_KEY) || "null");
      if (saved && Array.isArray(saved.legs)) setTicket({ ...emptyTicket, ...saved });
      if (prefs) setPreferences((current) => ({ ...current, ...prefs }));
    } catch { /* Storage is optional. */ }
    setHydrated(true);
  }, []);
  useEffect(() => { if (hydrated) localStorage.setItem(TICKET_STORAGE_KEY, JSON.stringify(ticket)); }, [ticket, hydrated]);
  useEffect(() => { if (hydrated) localStorage.setItem(PARLAY_PREFERENCES_KEY, JSON.stringify(preferences)); }, [preferences, hydrated]);

  function updatePreferences(next) {
    setPreferences((current) => ({ ...current, ...next }));
  }
  function addLeg(leg, meta) {
    const next = addLegToTicket(ticket, leg, meta);
    if (next.result.ok) setTicket(next.ticket);
    return next.result;
  }
  const removeLeg = (rowId, gameId) => setTicket((current) => ({ ...current, legs: current.legs.filter((leg) => !(leg.rowId === rowId && leg.gameId === gameId)) }));
  const clearTicket = () => setTicket({ ...emptyTicket, sportsbook: preferences.sportsbook, strategy: preferences.strategy });
  const replaceConflict = (oldLeg, leg, meta) => setTicket((current) => ({ ...current, ...meta, sportsbook: leg.bookmaker || meta.sportsbook, legs: [...current.legs.filter((item) => !(item.rowId === oldLeg.rowId && item.gameId === oldLeg.gameId)), { ...leg, selectedAt: new Date().toISOString() }] }));
  const value = useMemo(() => ({ ticket, preferences, hydrated, addLeg, removeLeg, clearTicket, replaceConflict, updatePreferences }), [ticket, preferences, hydrated]);
  return <TicketContext.Provider value={value}>{children}</TicketContext.Provider>;
}

export function useParlayTicket() {
  const value = useContext(TicketContext);
  if (!value) throw new Error("useParlayTicket must be used inside ParlayTicketProvider");
  return value;
}
