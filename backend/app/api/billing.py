import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from backend.app.database import get_db_connection, initialize_billing_database
from backend.app.services.auth_service import current_user
from backend.app.services.entitlement_service import (
    current_entitlement_for_user_id,
    entitlement_for_user,
)

try:
    import stripe
except ImportError:
    stripe = None


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])

ACTIVE = {"active", "trialing"}


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stripe():
    if stripe is None:
        raise HTTPException(503, detail="Stripe is not installed.")

    secret_key = os.getenv("STRIPE_SECRET_KEY")

    if not secret_key:
        raise HTTPException(503, detail="Unable to start checkout.")

    stripe.api_key = secret_key
    return stripe


def _get(obj, key, default=None):
    if obj is None:
        return default

    if isinstance(obj, dict):
        return obj.get(key, default)

    try:
        return obj.get(key, default)
    except (AttributeError, KeyError, TypeError):
        return getattr(obj, key, default)


def _metadata(obj):
    """
    Safely normalize Stripe metadata into a regular dictionary.

    Stripe may return metadata as:
    - a normal dict
    - a StripeObject
    - another mapping-like object
    - None
    """
    meta = _get(obj, "metadata", {}) or {}

    if isinstance(meta, dict):
        return meta

    if hasattr(meta, "to_dict"):
        try:
            result = meta.to_dict()
            return result if isinstance(result, dict) else {}
        except Exception:
            logger.exception("Unable to convert Stripe metadata using to_dict()")
            return {}

    if hasattr(meta, "_data"):
        try:
            data = meta._data
            return dict(data) if isinstance(data, dict) else {}
        except Exception:
            logger.exception("Unable to read Stripe metadata _data")
            return {}

    try:
        return {key: meta.get(key) for key in meta.keys()}
    except (AttributeError, KeyError, TypeError):
        return {}


def _safe_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _ts(value):
    try:
        if not value:
            return None

        return (
            datetime.fromtimestamp(int(value), timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        )
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _source(subscription):
    return (
        "stripe_promotion"
        if _get(subscription, "discount")
        else "stripe_paid"
    )


def _plan(obj):
    return _metadata(obj).get("plan") or "founding"


def _update(conn, uid, **values):
    if not uid:
        return False

    user_exists = conn.execute(
        "SELECT 1 FROM users WHERE id=?",
        (uid,),
    ).fetchone()

    if not user_exists:
        logger.info("Stripe update ignored: local user %s does not exist", uid)
        return False

    now = _now()

    fields = [
        "subscription_updated_at=?",
        "subscription_created_at=COALESCE(subscription_created_at, ?)",
    ]

    params = [now, now]

    column_map = {
        "customer": "stripe_customer_id",
        "subscription": "stripe_subscription_id",
        "status": "subscription_status",
        "period_end": "subscription_current_period_end",
        "source": "access_source",
        "plan": "subscription_plan",
    }

    for key, column in column_map.items():
        value = values.get(key)

        if value is not None:
            fields.append(f"{column}=?")
            params.append(value)

    if "cancel_at_period_end" in values:
        fields.append("subscription_cancel_at_period_end=?")
        params.append(1 if values.get("cancel_at_period_end") else 0)

    if values.get("status") is not None and values.get("plan") is None:
        fields.append("subscription_plan=?")

        params.append(
            "none"
            if values["status"] == "inactive"
            else "founding"
        )

    params.append(uid)

    conn.execute(
        f"""
        UPDATE users
        SET {", ".join(fields)}
        WHERE id=?
        """,
        params,
    )

    return True


def _uid_for_sub(conn, subscription):
    uid = _safe_int(_metadata(subscription).get("user_id"))

    if uid:
        user_exists = conn.execute(
            "SELECT 1 FROM users WHERE id=?",
            (uid,),
        ).fetchone()

        if user_exists:
            return uid

    subscription_id = _get(subscription, "id")

    if subscription_id:
        row = conn.execute(
            """
            SELECT id
            FROM users
            WHERE stripe_subscription_id=?
            """,
            (subscription_id,),
        ).fetchone()

        if row:
            return row["id"]

    customer_id = _get(subscription, "customer")

    if customer_id:
        row = conn.execute(
            """
            SELECT id
            FROM users
            WHERE stripe_customer_id=?
            """,
            (customer_id,),
        ).fetchone()

        if row:
            return row["id"]

    return None


def _apply_sub(conn, subscription):
    uid = _uid_for_sub(conn, subscription)

    if uid:
        return _update(
            conn,
            uid,
            customer=_get(subscription, "customer"),
            subscription=_get(subscription, "id"),
            status=_get(subscription, "status") or "inactive",
            plan=_plan(subscription),
            period_end=_ts(_get(subscription, "current_period_end")),
            cancel_at_period_end=bool(
                _get(subscription, "cancel_at_period_end")
            ),
            source=_source(subscription),
        )

    logger.info(
        (
            "Stripe subscription event ignored: "
            "no matching user for subscription=%s customer=%s"
        ),
        _get(subscription, "id"),
        _get(subscription, "customer"),
    )

    return False


def _invoice_period_end(invoice):
    lines = _get(invoice, "lines")
    data = _get(lines, "data", []) or []

    first_line = data[0] if data else None
    period = _get(first_line, "period", {})

    return _ts(_get(period, "end"))


def _invoice_subscription_id(invoice):
    """
    Supports older and newer Stripe invoice payload shapes.
    """
    subscription_id = _get(invoice, "subscription")

    if subscription_id:
        return subscription_id

    parent = _get(invoice, "parent")
    subscription_details = _get(parent, "subscription_details")

    return _get(subscription_details, "subscription")


def _uid_for_invoice(conn, invoice):
    subscription_id = _invoice_subscription_id(invoice)

    if subscription_id:
        row = conn.execute(
            """
            SELECT id
            FROM users
            WHERE stripe_subscription_id=?
            """,
            (subscription_id,),
        ).fetchone()

        if row:
            return row["id"]

    customer_id = _get(invoice, "customer")

    if customer_id:
        row = conn.execute(
            """
            SELECT id
            FROM users
            WHERE stripe_customer_id=?
            """,
            (customer_id,),
        ).fetchone()

        if row:
            return row["id"]

    metadata = _metadata(invoice)
    uid = _safe_int(metadata.get("user_id"))

    if uid:
        return uid

    parent = _get(invoice, "parent")
    subscription_details = _get(parent, "subscription_details")
    subscription_metadata = _metadata(subscription_details)

    return _safe_int(subscription_metadata.get("user_id"))


@router.get("/subscription")
def subscription(request: Request):
    user = current_user(request)

    return {
        "subscription": entitlement_for_user(user),
    }


@router.get("/entitlements")
def entitlements(request: Request):
    return entitlement_for_user(current_user(request))


@router.post("/create-checkout-session")
def create_checkout_session(request: Request):
    user = current_user(request)

    initialize_billing_database()

    entitlement = entitlement_for_user(user)

    if entitlement["status"] in ACTIVE:
        return {
            "alreadyActive": True,
            "message": "You already have an active membership.",
        }

    price_id = os.getenv("STRIPE_FOUNDING_MONTHLY_PRICE_ID")

    if not price_id:
        raise HTTPException(
            503,
            detail="Unable to start checkout.",
        )

    params = {
        "mode": "subscription",
        "line_items": [
            {
                "price": price_id,
                "quantity": 1,
            }
        ],
        "allow_promotion_codes": True,
        "client_reference_id": str(user["id"]),
        "success_url": os.getenv(
            "STRIPE_SUCCESS_URL",
            "http://localhost:3000/billing/success",
        ),
        "cancel_url": os.getenv(
            "STRIPE_CANCEL_URL",
            "http://localhost:3000/subscribe",
        ),
        "subscription_data": {
            "metadata": {
                "user_id": str(user["id"]),
                "plan": "founding",
            }
        },
        "metadata": {
            "user_id": str(user["id"]),
            "plan": "founding",
        },
    }

    if (
        "stripe_customer_id" in user.keys()
        and user["stripe_customer_id"]
    ):
        params["customer"] = user["stripe_customer_id"]
    else:
        params["customer_email"] = user["email"]

    try:
        checkout_session = _stripe().checkout.Session.create(**params)

        return {
            "url": checkout_session.url,
        }
    except Exception as exc:
        logger.exception("Unable to create Stripe checkout session")

        raise HTTPException(
            400,
            detail="Unable to start checkout.",
        ) from exc


@router.post("/create-portal-session")
def portal(request: Request):
    user = current_user(request)

    customer_id = (
        user["stripe_customer_id"]
        if "stripe_customer_id" in user.keys()
        else None
    )

    if not customer_id:
        raise HTTPException(
            400,
            detail="Unable to open billing portal.",
        )

    return_url = (
        os.getenv(
            "FRONTEND_ORIGIN",
            "http://localhost:3000",
        ).rstrip("/")
        + "/account"
    )

    try:
        portal_session = _stripe().billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )

        return {
            "url": portal_session.url,
        }
    except Exception as exc:
        logger.exception(
            "Unable to create Stripe portal session for customer %s",
            customer_id,
        )

        raise HTTPException(
            400,
            detail="Unable to open billing portal.",
        ) from exc


@router.post("/refresh")
def refresh(request: Request):
    user = current_user(request)

    subscription_id = (
        user["stripe_subscription_id"]
        if "stripe_subscription_id" in user.keys()
        else None
    )

    if subscription_id:
        try:
            stripe_subscription = _stripe().Subscription.retrieve(
                subscription_id
            )

            with get_db_connection() as conn:
                _apply_sub(conn, stripe_subscription)
                conn.commit()

        except Exception:
            logger.exception(
                "Unable to refresh Stripe subscription %s",
                subscription_id,
            )

    return current_entitlement_for_user_id(user["id"])


@router.post("/webhook")
async def webhook(request: Request):
    initialize_billing_database()

    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    if not webhook_secret:
        raise HTTPException(
            503,
            detail="Webhook is not configured.",
        )

    payload = await request.body()
    signature = request.headers.get("stripe-signature")

    try:
        event = _stripe().Webhook.construct_event(
            payload,
            signature,
            webhook_secret,
        )
    except Exception as exc:
        logger.warning(
            "Invalid Stripe webhook signature",
            exc_info=True,
        )

        raise HTTPException(
            400,
            detail="Invalid webhook signature.",
        ) from exc

    event_id = _get(event, "id")
    event_type = _get(event, "type")
    event_data = _get(event, "data", {})
    obj = _get(event_data, "object", {})

    try:
        with get_db_connection() as conn:
            duplicate = conn.execute(
                """
                SELECT 1
                FROM stripe_webhook_events
                WHERE stripe_event_id=?
                """,
                (event_id,),
            ).fetchone()

            if duplicate:
                return {
                    "received": True,
                    "duplicate": True,
                }

            if event_type == "checkout.session.completed":
                metadata = _metadata(obj)

                uid = (
                    _safe_int(metadata.get("user_id"))
                    or _safe_int(
                        _get(obj, "client_reference_id")
                    )
                )

                updated = _update(
                    conn,
                    uid,
                    customer=_get(obj, "customer"),
                    subscription=_get(obj, "subscription"),
                    plan=metadata.get("plan") or "founding",
                )

                if not updated:
                    logger.warning(
                        (
                            "Checkout completed but no local user "
                            "could be updated. event_id=%s user_id=%s"
                        ),
                        event_id,
                        uid,
                    )

            elif event_type in {
                "customer.subscription.created",
                "customer.subscription.updated",
                "customer.subscription.deleted",
            }:
                if event_type == "customer.subscription.deleted":
                    deleted_subscription = {
                        "id": _get(obj, "id"),
                        "customer": _get(obj, "customer"),
                        "status": "canceled",
                        "metadata": _metadata(obj),
                        "current_period_end": _get(
                            obj,
                            "current_period_end",
                        ),
                        "cancel_at_period_end": _get(
                            obj,
                            "cancel_at_period_end",
                        ),
                        "discount": _get(obj, "discount"),
                    }

                    _apply_sub(conn, deleted_subscription)
                else:
                    _apply_sub(conn, obj)

            elif event_type in {
                "invoice.paid",
                "invoice.payment_succeeded",
                "invoice.payment_failed",
            }:
                uid = _uid_for_invoice(conn, obj)

                if uid:
                    is_paid = event_type in {
                        "invoice.paid",
                        "invoice.payment_succeeded",
                    }

                    _update(
                        conn,
                        uid,
                        customer=_get(obj, "customer"),
                        subscription=_invoice_subscription_id(obj),
                        status="active" if is_paid else "past_due",
                        plan="founding",
                        period_end=_invoice_period_end(obj),
                        source="stripe_paid",
                    )
                else:
                    logger.info(
                        (
                            "Stripe invoice event ignored: "
                            "no matching user for invoice=%s customer=%s"
                        ),
                        _get(obj, "id"),
                        _get(obj, "customer"),
                    )

            conn.execute(
                """
                INSERT INTO stripe_webhook_events(
                    stripe_event_id,
                    event_type,
                    processed_at
                )
                VALUES (?, ?, ?)
                """,
                (
                    event_id,
                    event_type,
                    _now(),
                ),
            )

            conn.commit()

    except Exception:
        logger.exception(
            (
                "Unexpected Stripe webhook failure "
                "for event_id=%s event_type=%s"
            ),
            event_id,
            event_type,
        )

        raise HTTPException(
            500,
            detail="Webhook processing failed.",
        )

    return {
        "received": True,
    }