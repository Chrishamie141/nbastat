"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/components/auth/AuthProvider";
import { useSubscription } from "@/hooks/useSubscription";
import { brand } from "@/lib/brand";
import { membershipState } from "@/lib/member-state.mjs";

export default function SubscriptionGuard({ children }) {
  const { ready, isAuthenticated } = useAuth();
  const { loading, subscription, error, refreshSubscription } = useSubscription();
  const router = useRouter();
  const path = usePathname();
  const state = membershipState({ ready, isAuthenticated, loading, subscription, error });
  useEffect(() => {
    if (state === "signed_out") router.replace(`/login?next=${encodeURIComponent(path)}`);
    if (state === "inactive") router.replace(`/subscribe?next=${encodeURIComponent(path)}`);
  }, [state, router, path]);
  if (state === "error") return (
    <main className="mx-auto min-h-[55vh] max-w-5xl px-6 pt-28">
      <div className="glass rounded-2xl p-6" role="alert">
        <h1 className="text-xl font-bold">Membership check unavailable</h1>
        <p className="mt-2 text-slate-300">We could not confirm your access. This does not mean your membership has expired. Please retry before starting another checkout.</p>
        <button className="btn btn-primary mt-4" onClick={refreshSubscription}>Retry membership check</button>
      </div>
    </main>
  );
  if (state !== "active") return (
    <main className="mx-auto min-h-[55vh] max-w-5xl px-6 pt-28">
      <div className="glass rounded-2xl p-6 text-gray-300" role="status">Checking {brand.name} membership…</div>
    </main>
  );
  return children;
}
