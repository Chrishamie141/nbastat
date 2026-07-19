import logging
import os
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from backend.app.database import get_db_connection, initialize_billing_database
from backend.app.services.auth_service import current_user
from backend.app.services.entitlement_service import entitlement_for_user, current_entitlement_for_user_id
try:
    import stripe
except ImportError:
    stripe = None

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/billing", tags=["billing"])
ACTIVE={"active","trialing"}

def _now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def _stripe():
    if stripe is None: raise HTTPException(503, detail="Stripe is not installed.")
    if not os.getenv("STRIPE_SECRET_KEY"): raise HTTPException(503, detail="Unable to start checkout.")
    stripe.api_key=os.getenv("STRIPE_SECRET_KEY"); return stripe

def _get(obj, key, default=None):
    if obj is None: return default
    if isinstance(obj, dict): return obj.get(key, default)
    try: return obj.get(key, default)
    except AttributeError: return getattr(obj, key, default)

def _metadata(obj):
    meta = _get(obj, "metadata", {}) or {}
    return meta if isinstance(meta, dict) else dict(meta)

def _safe_int(value):
    try: return int(value) if value not in (None, "") else None
    except (TypeError, ValueError): return None

def _ts(v):
    try: return datetime.fromtimestamp(int(v), timezone.utc).replace(microsecond=0).isoformat() if v else None
    except (TypeError, ValueError, OSError, OverflowError): return None

def _source(sub): return "stripe_promotion" if _get(sub, "discount") else "stripe_paid"

def _plan(obj): return _metadata(obj).get("plan") or "founding"

def _update(conn, uid, **kw):
    if not uid or not conn.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone(): return False
    now=_now(); fields=["subscription_updated_at=?","subscription_created_at=COALESCE(subscription_created_at, ?)"]; vals=[now,now]
    mp={"customer":"stripe_customer_id","subscription":"stripe_subscription_id","status":"subscription_status","period_end":"subscription_current_period_end","source":"access_source","plan":"subscription_plan"}
    for k,col in mp.items():
        if kw.get(k) is not None: fields.append(f"{col}=?"); vals.append(kw[k])
    if "cancel_at_period_end" in kw: fields.append("subscription_cancel_at_period_end=?"); vals.append(1 if kw.get("cancel_at_period_end") else 0)
    if kw.get("status") is not None and kw.get("plan") is None: fields.append("subscription_plan=?"); vals.append("none" if kw["status"]=="inactive" else "founding")
    vals.append(uid); conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id=?", vals); return True

def _uid_for_sub(conn, sub):
    uid=_safe_int(_metadata(sub).get("user_id"))
    if uid and conn.execute("SELECT 1 FROM users WHERE id=?",(uid,)).fetchone(): return uid
    sid=_get(sub, "id")
    if sid:
        row=conn.execute("SELECT id FROM users WHERE stripe_subscription_id=?",(sid,)).fetchone()
        if row: return row["id"]
    customer=_get(sub, "customer")
    if customer:
        row=conn.execute("SELECT id FROM users WHERE stripe_customer_id=?",(customer,)).fetchone()
        if row: return row["id"]
    return None

def _apply_sub(conn, sub):
    uid=_uid_for_sub(conn, sub)
    if uid:
        return _update(conn, uid, customer=_get(sub,"customer"), subscription=_get(sub,"id"), status=_get(sub,"status") or "inactive", plan=_plan(sub), period_end=_ts(_get(sub,"current_period_end")), cancel_at_period_end=bool(_get(sub,"cancel_at_period_end")), source=_source(sub))
    logger.info("Stripe subscription event ignored: no matching user for subscription=%s customer=%s", _get(sub,"id"), _get(sub,"customer"))
    return False

def _invoice_period_end(invoice):
    data=_get(_get(invoice, "lines"), "data", []) or []
    first=data[0] if data else None
    return _ts(_get(_get(first, "period", {}), "end"))

def _uid_for_invoice(conn, invoice):
    sid=_get(invoice,"subscription")
    if sid:
        row=conn.execute("SELECT id FROM users WHERE stripe_subscription_id=?",(sid,)).fetchone()
        if row: return row["id"]
    customer=_get(invoice,"customer")
    if customer:
        row=conn.execute("SELECT id FROM users WHERE stripe_customer_id=?",(customer,)).fetchone()
        if row: return row["id"]
    return _safe_int(_metadata(invoice).get("user_id"))

@router.get("/subscription")
def subscription(request: Request): return {"subscription": entitlement_for_user(current_user(request))}
@router.get("/entitlements")
def entitlements(request: Request): return entitlement_for_user(current_user(request))
@router.post("/create-checkout-session")
def create_checkout_session(request: Request):
    user=current_user(request); initialize_billing_database(); ent=entitlement_for_user(user)
    if ent["status"] in ACTIVE: return {"alreadyActive":True,"message":"You already have an active membership."}
    price=os.getenv("STRIPE_FOUNDING_MONTHLY_PRICE_ID")
    if not price: raise HTTPException(503, detail="Unable to start checkout.")
    params={"mode":"subscription","line_items":[{"price":price,"quantity":1}],"allow_promotion_codes":True,"client_reference_id":str(user["id"]),"success_url":os.getenv("STRIPE_SUCCESS_URL","http://localhost:3000/billing/success"),"cancel_url":os.getenv("STRIPE_CANCEL_URL","http://localhost:3000/subscribe"),"subscription_data":{"metadata":{"user_id":str(user["id"]),"plan":"founding"}},"metadata":{"user_id":str(user["id"]),"plan":"founding"}}
    if "stripe_customer_id" in user.keys() and user["stripe_customer_id"]: params["customer"]=user["stripe_customer_id"]
    else: params["customer_email"]=user["email"]
    try: return {"url": _stripe().checkout.Session.create(**params).url}
    except Exception as exc:
        logger.exception("Unable to create Stripe checkout session")
        raise HTTPException(400, detail="Unable to start checkout.") from exc
@router.post("/create-portal-session")
def portal(request: Request):
    user=current_user(request); customer=user["stripe_customer_id"] if "stripe_customer_id" in user.keys() else None
    if not customer: raise HTTPException(400, detail="Unable to open billing portal.")
    try: return {"url": _stripe().billing_portal.Session.create(customer=customer, return_url=os.getenv("FRONTEND_ORIGIN","http://localhost:3000")+"/account").url}
    except Exception as exc:
        logger.exception("Unable to create Stripe portal session for customer %s", customer)
        raise HTTPException(400, detail="Unable to open billing portal.") from exc
@router.post("/refresh")
def refresh(request: Request):
    user=current_user(request); sub=user["stripe_subscription_id"] if "stripe_subscription_id" in user.keys() else None
    if sub:
        try:
            with get_db_connection() as conn: _apply_sub(conn, _stripe().Subscription.retrieve(sub)); conn.commit()
        except Exception: logger.exception("Unable to refresh Stripe subscription %s", sub)
    return current_entitlement_for_user_id(user["id"])
@router.post("/webhook")
async def webhook(request: Request):
    initialize_billing_database(); secret=os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret: raise HTTPException(503, detail="Webhook is not configured.")
    payload=await request.body(); sig=request.headers.get("stripe-signature")
    try: event=_stripe().Webhook.construct_event(payload, sig, secret)
    except Exception as exc: raise HTTPException(400, detail="Invalid webhook signature.") from exc
    eid=_get(event,"id"); etype=_get(event,"type"); obj=_get(_get(event,"data",{}),"object",{})
    try:
        with get_db_connection() as conn:
            if conn.execute("SELECT 1 FROM stripe_webhook_events WHERE stripe_event_id=?",(eid,)).fetchone(): return {"received":True,"duplicate":True}
            if etype=="checkout.session.completed":
                uid=_safe_int(_metadata(obj).get("user_id")) or _safe_int(_get(obj,"client_reference_id"))
                _update(conn, uid, customer=_get(obj,"customer"), subscription=_get(obj,"subscription"))
            elif etype in {"customer.subscription.created","customer.subscription.updated","customer.subscription.deleted"}:
                if etype=="customer.subscription.deleted": obj={**dict(obj),"status":"canceled"} if isinstance(obj, dict) else {"id":_get(obj,"id"),"customer":_get(obj,"customer"),"status":"canceled","metadata":_metadata(obj),"current_period_end":_get(obj,"current_period_end"),"cancel_at_period_end":_get(obj,"cancel_at_period_end"),"discount":_get(obj,"discount")}
                _apply_sub(conn, obj)
            elif etype in {"invoice.paid","invoice.payment_failed"}:
                uid=_uid_for_invoice(conn, obj)
                if uid: _update(conn,uid,customer=_get(obj,"customer"),subscription=_get(obj,"subscription"),status="active" if etype=="invoice.paid" else "past_due",plan="founding",period_end=_invoice_period_end(obj),source="stripe_paid")
            conn.execute("INSERT INTO stripe_webhook_events(stripe_event_id,event_type,processed_at) VALUES(?,?,?)",(eid,etype,_now())); conn.commit()
    except Exception:
        logger.exception("Unexpected Stripe webhook failure for event_id=%s event_type=%s", eid, etype)
        raise HTTPException(500, detail="Webhook processing failed.")
    return {"received":True}
