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
router = APIRouter(prefix="/api/billing", tags=["billing"])
ACTIVE={"active","trialing"}
def _now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def _stripe():
    if stripe is None: raise HTTPException(503, detail="Stripe is not installed.")
    if not os.getenv("STRIPE_SECRET_KEY"): raise HTTPException(503, detail="Unable to start checkout.")
    stripe.api_key=os.getenv("STRIPE_SECRET_KEY"); return stripe
def _ts(v): return datetime.fromtimestamp(v, timezone.utc).replace(microsecond=0).isoformat() if v else None
def _source(sub): return "stripe_promotion" if sub.get("discount") else "stripe_paid"
def _update(conn, uid, **kw):
    now=_now(); fields=["subscription_updated_at=?","subscription_created_at=COALESCE(subscription_created_at, ?)"]; vals=[now,now]
    mp={"customer":"stripe_customer_id","subscription":"stripe_subscription_id","status":"subscription_status","period_end":"subscription_current_period_end","source":"access_source"}
    for k,col in mp.items():
        if kw.get(k) is not None: fields.append(f"{col}=?"); vals.append(kw[k])
    if "cancel_at_period_end" in kw: fields.append("subscription_cancel_at_period_end=?"); vals.append(1 if kw.get("cancel_at_period_end") else 0)
    if kw.get("status") is not None: fields.append("subscription_plan=?"); vals.append("founding" if kw["status"]!="inactive" else "none")
    vals.append(uid); conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id=?", vals)
def _uid_for_sub(conn, sub):
    uid=(sub.get("metadata") or {}).get("user_id")
    if uid: return int(uid)
    row=conn.execute("SELECT id FROM users WHERE stripe_subscription_id=?",(sub.get("id"),)).fetchone()
    return row["id"] if row else None
def _apply_sub(conn, sub):
    uid=_uid_for_sub(conn, sub)
    if uid: _update(conn, uid, customer=sub.get("customer"), subscription=sub.get("id"), status=sub.get("status") or "inactive", period_end=_ts(sub.get("current_period_end")), cancel_at_period_end=bool(sub.get("cancel_at_period_end")), source=_source(sub))
    return bool(uid)
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
    except Exception: raise HTTPException(400, detail="Unable to start checkout.")
@router.post("/create-portal-session")
def portal(request: Request):
    user=current_user(request); customer=user["stripe_customer_id"] if "stripe_customer_id" in user.keys() else None
    if not customer: raise HTTPException(400, detail="Unable to open billing portal.")
    try: return {"url": _stripe().billing_portal.Session.create(customer=customer, return_url=os.getenv("FRONTEND_ORIGIN","http://localhost:3000")+"/account").url}
    except Exception: raise HTTPException(400, detail="Unable to open billing portal.")
@router.post("/refresh")
def refresh(request: Request):
    user=current_user(request); sub=user["stripe_subscription_id"] if "stripe_subscription_id" in user.keys() else None
    if sub:
        try:
            with get_db_connection() as conn: _apply_sub(conn, _stripe().Subscription.retrieve(sub)); conn.commit()
        except Exception: pass
    return current_entitlement_for_user_id(user["id"])
@router.post("/webhook")
async def webhook(request: Request):
    initialize_billing_database(); secret=os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret: raise HTTPException(503, detail="Webhook is not configured.")
    payload=await request.body(); sig=request.headers.get("stripe-signature")
    try: event=_stripe().Webhook.construct_event(payload, sig, secret)
    except Exception: raise HTTPException(400, detail="Invalid webhook signature.")
    eid=event["id"]; etype=event["type"]; obj=event["data"]["object"]
    with get_db_connection() as conn:
        if conn.execute("SELECT 1 FROM stripe_webhook_events WHERE stripe_event_id=?",(eid,)).fetchone(): return {"received":True,"duplicate":True}
        if etype=="checkout.session.completed":
            uid=int((obj.get("metadata") or {}).get("user_id") or obj.get("client_reference_id") or 0)
            if uid: _update(conn, uid, customer=obj.get("customer"), subscription=obj.get("subscription"))
        elif etype in {"customer.subscription.created","customer.subscription.updated","customer.subscription.deleted"}:
            if etype=="customer.subscription.deleted": obj={**obj,"status":"canceled"}
            _apply_sub(conn, obj)
        elif etype in {"invoice.paid","invoice.payment_failed"}:
            sid=obj.get("subscription"); row=conn.execute("SELECT id FROM users WHERE stripe_subscription_id=?",(sid,)).fetchone() if sid else None
            if row: _update(conn,row["id"],status="active" if etype=="invoice.paid" else "past_due",period_end=_ts(((obj.get("lines") or {}).get("data") or [{}])[0].get("period",{}).get("end")))
        conn.execute("INSERT INTO stripe_webhook_events(stripe_event_id,event_type,processed_at) VALUES(?,?,?)",(eid,etype,_now())); conn.commit()
    return {"received":True}
