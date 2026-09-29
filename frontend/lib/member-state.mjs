// Entitlements are authoritative. A transport error is never an inactive plan.
export function membershipState({ ready, isAuthenticated, loading, subscription, error }) {
  if (!ready) return "loading";
  if (!isAuthenticated) return "signed_out";
  if (loading) return "loading";
  if (error) return "error";
  if (!subscription) return "loading";
  return subscription.hasFullAccess === true ? "active" : "inactive";
}
