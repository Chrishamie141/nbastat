"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, Menu } from "lucide-react";
import { useAuth } from "@/components/auth/AuthProvider";
import SmartBetSportsLogo from "@/components/branding/SmartBetSportsLogo";
export default function PremiumNavbar() {
  const path = usePathname();
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
  const links = user?.isInternal
    ? [...authed, "internal/operations", "internal/experiments/week3"]
    : authed;
  const authPage = path === "/login" || path === "/register";
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
                className={`rounded-xl px-2 py-2 text-sm capitalize transition ${path.includes(l) ? "bg-cyan-400/12 text-white" : "text-slate-300 hover:text-white"}`}
              >
                {l === "internal/operations" ? "Command Center" : l.startsWith("internal") ? "Experiment" : l}
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
            <Link
              href={path === "/login" ? "/register" : "/login"}
              className="btn btn-glass px-4 py-2"
            >
              {path === "/login" ? "Create Account" : "Log In"}
            </Link>
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
            onClick={logout}
            aria-label="Log out"
            className="hidden items-center gap-2 rounded-xl bg-white/10 px-3 py-2 text-sm text-slate-300 hover:text-white md:flex"
          >
            <span className="max-w-24 truncate">{user?.name}</span>
            <LogOut size={16} />
          </button>
        ) : (
          <button className="md:hidden" aria-label="Open menu">
            <Menu />
          </button>
        )}
      </nav>
    </header>
  );
}
