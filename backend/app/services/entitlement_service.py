from datetime import datetime, timezone
from fastapi import Depends, HTTPException, Request
from backend.app.database import get_db_connection, initialize_billing_database
from backend.app.services.auth_service import current_user

ELIGIBLE_STATUSES = {"active", "trialing"}

def _iso(value):
    return value if value else None

def entitlement_for_user(user):
    initialize_billing_database()
    status = user["subscription_status"] if "subscription_status" in user.keys() and user["subscription_status"] else "inactive"
    plan = user["subscription_plan"] if "subscription_plan" in user.keys() and user["subscription_plan"] else "none"
    source = user["access_source"] if "access_source" in user.keys() and user["access_source"] else "none"
    return {
        "hasFullAccess": status in ELIGIBLE_STATUSES and plan == "founding",
        "entitlement": "full_access",
        "plan": plan,
        "status": status,
        "currentPeriodEnd": _iso(user["subscription_current_period_end"] if "subscription_current_period_end" in user.keys() else None),
        "cancelAtPeriodEnd": bool(user["subscription_cancel_at_period_end"] if "subscription_cancel_at_period_end" in user.keys() else 0),
        "accessSource": source,
    }

def current_entitlement_for_user_id(user_id: int):
    initialize_billing_database()
    with get_db_connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE id=? AND is_active=1", (user_id,)).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return entitlement_for_user(user)

def require_full_access(request: Request):
    user = current_user(request)
    ent = entitlement_for_user(user)
    if not ent["hasFullAccess"]:
        raise HTTPException(status_code=402, detail="Your subscription is not active.")
    return user
