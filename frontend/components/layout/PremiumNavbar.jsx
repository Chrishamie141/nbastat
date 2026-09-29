"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { LogOut, Menu } from "lucide-react";
import { useAuth } from "@/components/auth/AuthProvider";
import SmartBetSportsLogo from "@/components/branding/SmartBetSportsLogo";
export default function PremiumNavbar() {
  const path = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const [loggingOut, setLoggingOut] = useState(false);
  useEffect(() => setMenuOpen(false), [path]);
  async function signOut() {
    setLoggingOut(true);
    setLogoutError("");
    try { await logout(); } catch { setLogoutError("Could not sign out. Please try again."); }
    finally { setLoggingOut(false); }
  }
  const { isAuthenticated, user, logout } = useAuth();
  const authed = [
    "dashboard",
    "games",
    "parlays",
    "fantasy",
    "history",
    "performance",
    "account",
  ];
  const ownerPortal = Boolean(user?.isInternal && (path.startsWith("/internal") || path.startsWith("/command-center")));
  const links = ownerPortal
    ? ["dashboard", "command-center"]
    : user?.isInternal
      ? [...authed, "command-center"]
      : authed;
  const authPage = [
    "/login",
    "/register",
    "/forgot-password",
    "/reset-password",
    "/setup",
  ].includes(path);
  return (
    <header className="sticky top-0 z-40 border-b border-white/10 bg-[#061225]/85 backdrop-blur">
      <nav className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 sm:px-6">
        <Link href="/" aria-label="SmartBetSports home">
          <SmartBetSportsLogo size={30} />
        </Link>
        {isAuthenticated ? (
          <div className="hidden gap-1 md:flex">
            {links.map((l) => (
              <Link
                key={l}
                href={`/${l}`}
                aria-current={path === `/${l}` || path.startsWith(`/${l}/`) ? "page" : undefined}
                className={`rounded-xl px-2 py-2 text-sm capitalize transition ${(path === `/${l}` || path.startsWith(`/${l}/`)) ? "bg-cyan-400/12 text-white" : "text-slate-300 hover:text-white"}`}
              >
                {l === "command-center" ? "Command Center" : l === "dashboard" && ownerPortal ? "User Dashboard" : l}
              </Link>
            ))}
          </div>
        ) : authPage ? (
          <div className="hidden items-center gap-2 md:flex">
            <Link
              href="/"
              className="rounded-xl px-3 py-2 text-sm text-slate-300 hover:text-white"
            >
              Back to Home
            </Link>
            {path !== "/login" && <Link href="/login" className="btn btn-glass px-4 py-2">Log In</Link>}
          </div>
        ) : (
          <div className="hidden items-center gap-2 md:flex">
            <Link
              href="/#how"
              className="rounded-xl px-3 py-2 text-sm text-slate-300 hover:text-white"
            >
              How It Works
            </Link>
            <Link
              href="/login"
              className="rounded-xl px-3 py-2 text-sm text-slate-300 hover:text-white"
            >
              Log In
            </Link>
            <Link href="/register" className="btn btn-primary px-4 py-2">
              Get Started
            </Link>
          </div>
        )}
        {isAuthenticated ? (
          <button
            onClick={signOut}
            disabled={loggingOut}
            aria-label="Log out"
            className="hidden items-center gap-2 rounded-xl bg-white/10 px-3 py-2 text-sm text-slate-300 hover:text-white md:flex"
          >
            <span className="max-w-24 truncate">{user?.name}</span>
            <LogOut size={16} />
          </button>
        ) : null}
        <button type="button" className="flex min-h-11 min-w-11 items-center justify-center rounded-xl border border-white/15 md:hidden"
          aria-label={menuOpen ? "Close menu" : "Open menu"} aria-expanded={menuOpen} aria-controls="mobile-primary-menu"
          onClick={() => setMenuOpen((open) => !open)}
          onKeyDown={(event) => { if (event.key === "Escape") setMenuOpen(false); }}>
          <Menu aria-hidden="true" />
        </button>
      </nav>
      {menuOpen && <nav id="mobile-primary-menu" aria-label="Mobile primary" className="max-h-[70vh] overflow-y-auto border-t border-white/10 px-4 pb-4 md:hidden"
        onKeyDown={(event) => { if (event.key === "Escape") setMenuOpen(false); }}>
        {(isAuthenticated ? links.map((item) => [`/${item}`, item.replaceAll("-", " ")]) : [["/", "Home"], ["/#how", "How it works"], ["/login", "Log in"], ["/register", "Get started"]]).map(([href, label]) =>
          <Link key={href} href={href} onClick={() => setMenuOpen(false)}
            aria-current={path === href || (href !== "/" && path.startsWith(`${href}/`)) ? "page" : undefined}
            className="block rounded-xl px-3 py-3 capitalize text-slate-200 hover:bg-white/10">{label}</Link>)}
        {isAuthenticated && <button className="btn btn-glass mt-2 w-full" onClick={signOut} disabled={loggingOut}>{loggingOut ? "Signing out…" : "Log out"}</button>}
      </nav>}
      {logoutError && <p role="alert" className="px-4 pb-3 text-sm text-red-200">{logoutError}</p>}
    </header>
  );
}
