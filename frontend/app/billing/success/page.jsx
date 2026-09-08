'use client';

import Link from 'next/link';
import { Suspense, useCallback, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import GlowCard from '@/components/ui/GlowCard';
import { useSubscription } from '@/context/SubscriptionProvider';
import { safeNextPath } from '@/lib/safe-next-path';

function BillingSuccessContent() {
  const { confirmCheckout } = useSubscription();
  const [tries, setTries] = useState(0);
  const [active, setActive] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState('');
  const router = useRouter();
  const params = useSearchParams();
  const destination = safeNextPath(params.get('next'));
  const sessionId = params.get('session_id');

  const confirm = useCallback(async () => {
    setChecking(true);
    setError('');
    try {
      const entitlement = await confirmCheckout(sessionId);
      if (entitlement?.hasFullAccess) {
        setActive(true);
        router.replace(destination);
        return true;
      }
      return false;
    } catch (confirmationError) {
      setError(confirmationError.message || 'Unable to confirm payment yet.');
      return false;
    } finally {
      setChecking(false);
    }
  }, [confirmCheckout, destination, router, sessionId]);

  useEffect(() => {
    let alive = true;
    async function poll() {
      for (let index = 0; index < 8; index += 1) {
        const confirmed = await confirm();
        if (!alive || confirmed) return;
        setTries(index + 1);
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
    }
    poll();
    return () => { alive = false; };
  }, [confirm]);

  return (
    <main className="mx-auto min-h-screen max-w-3xl px-6 pt-24">
      <GlowCard className="p-8 text-center">
        <h1 className="text-3xl font-black">Confirming your SmartBetSports membership…</h1>
        <p className="mt-4 text-slate-300">Your payment was received. We’re securely linking it to your account.</p>
        <p className="mt-2 text-sm text-slate-500">Attempt {tries}/8</p>
        {error && <p className="mt-4 rounded-xl bg-red-500/10 p-3 text-sm text-red-100">{error}</p>}
        {!active && (
          <div className="mt-6 flex justify-center gap-3">
            <button disabled={checking} className="btn btn-primary disabled:opacity-60" onClick={confirm}>
              {checking ? 'Checking…' : 'Retry confirmation'}
            </button>
            <Link href="/account" className="btn btn-glass">Account</Link>
          </div>
        )}
      </GlowCard>
    </main>
  );
}

export default function BillingSuccess() {
  return (
    <Suspense fallback={<main className="mx-auto min-h-screen max-w-3xl px-6 pt-24">Confirming your SmartBetSports membership…</main>}>
      <BillingSuccessContent />
    </Suspense>
  );
}
