import asyncio, json, os, sys
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AUTH_SECRET", "test-secret")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_placeholder")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test")
os.environ.setdefault("STRIPE_FOUNDING_MONTHLY_PRICE_ID", "price_founder")
os.environ["DATABASE_URL"] = "sqlite:///./test_billing.db"

from backend.app.database import get_db_connection, initialize_billing_database, database_path
from backend.app.services.auth_service import create_token
from backend.app.services.entitlement_service import current_entitlement_for_user_id, require_full_access
from backend.app.api import billing

class Req:
    def __init__(self, uid=None, payload=b'{}', sig='sig'):
        self.cookies = {'sbs_session': create_token(uid)} if uid else {}
        self.headers = {'stripe-signature': sig}
        self._payload = payload
    async def body(self): return self._payload

def setup_function():
    p=database_path()
    if p.exists(): p.unlink()
    initialize_billing_database()
    with get_db_connection() as conn:
        conn.execute("INSERT INTO users(id,name,email,password_hash,created_at,updated_at,is_active) VALUES(1,'A','a@example.com','x','now','now',1)"); conn.commit()

def test_checkout_requires_authentication():
    with pytest.raises(Exception) as exc: billing.create_checkout_session(Req())
    assert exc.value.status_code == 401

def test_checkout_uses_authenticated_user_and_enables_promo(monkeypatch):
    calls={}
    class Session:
        @staticmethod
        def create(**kwargs): calls.update(kwargs); return SimpleNamespace(url='https://checkout.test/session')
    monkeypatch.setattr(billing, 'stripe', SimpleNamespace(api_key=None, checkout=SimpleNamespace(Session=Session)))
    assert billing.create_checkout_session(Req(1))['url'].startswith('https://checkout')
    assert calls['allow_promotion_codes'] is True
    assert calls['client_reference_id'] == '1'
    assert calls['customer_email'] == 'a@example.com'
    assert calls['subscription_data']['metadata']['user_id'] == '1'

def test_duplicate_active_subscriptions_prevented():
    with get_db_connection() as conn:
        conn.execute("UPDATE users SET subscription_status='active', subscription_plan='founding' WHERE id=1"); conn.commit()
    assert billing.create_checkout_session(Req(1))['alreadyActive'] is True

def test_webhook_signature_verification(monkeypatch):
    class Webhook:
        @staticmethod
        def construct_event(payload, sig, secret): raise ValueError('bad')
    monkeypatch.setattr(billing, 'stripe', SimpleNamespace(api_key=None, Webhook=Webhook))
    with pytest.raises(Exception) as exc: asyncio.run(billing.webhook(Req(1)))
    assert exc.value.status_code == 400

def event(eid, typ, obj): return {'id':eid,'object':'event','type':typ,'data':{'object':obj}}

def test_webhook_lifecycle_and_duplicate(monkeypatch):
    class Webhook:
        @staticmethod
        def construct_event(payload, sig, secret): return json.loads(payload)
    monkeypatch.setattr(billing, 'stripe', SimpleNamespace(api_key=None, Webhook=Webhook))
    events=[
      event('evt_checkout','checkout.session.completed',{'customer':'cus_1','subscription':'sub_1','client_reference_id':'1','metadata':{'user_id':'1'}}),
      event('evt_created','customer.subscription.created',{'id':'sub_1','customer':'cus_1','status':'active','current_period_end':2000000000,'cancel_at_period_end':False,'metadata':{'user_id':'1'}}),
      event('evt_updated','customer.subscription.updated',{'id':'sub_1','customer':'cus_1','status':'trialing','current_period_end':2000001000,'cancel_at_period_end':True,'discount':{'id':'di'},'metadata':{'user_id':'1'}}),
      event('evt_paid','invoice.paid',{'subscription':'sub_1','lines':{'data':[{'period':{'end':2000002000}}]}}),
      event('evt_failed','invoice.payment_failed',{'subscription':'sub_1','lines':{'data':[{'period':{'end':2000002000}}]}}),
      event('evt_deleted','customer.subscription.deleted',{'id':'sub_1','customer':'cus_1','status':'canceled','metadata':{'user_id':'1'}}),
    ]
    for e in events: assert asyncio.run(billing.webhook(Req(1, json.dumps(e).encode())))['received'] is True
    assert asyncio.run(billing.webhook(Req(1, json.dumps(events[-1]).encode())))['duplicate'] is True
    ent=current_entitlement_for_user_id(1)
    assert ent['hasFullAccess'] is False and ent['status'] == 'canceled'

def test_active_past_due_canceled_and_promotional_entitlements():
    with pytest.raises(Exception) as exc: require_full_access(Req(1))
    assert exc.value.status_code == 402
    with get_db_connection() as conn:
        conn.execute("UPDATE users SET subscription_status='past_due', subscription_plan='founding' WHERE id=1"); conn.commit()
    assert current_entitlement_for_user_id(1)['hasFullAccess'] is False
    with get_db_connection() as conn:
        conn.execute("UPDATE users SET subscription_status='active', subscription_plan='founding', access_source='stripe_promotion' WHERE id=1"); conn.commit()
    ent=current_entitlement_for_user_id(1)
    assert ent['hasFullAccess'] is True and ent['accessSource'] == 'stripe_promotion'

class AttrObj(SimpleNamespace):
    def get(self, key, default=None): return getattr(self, key, default)

def test_webhook_out_of_order_invoice_paid_and_missing_metadata(monkeypatch):
    class Webhook:
        @staticmethod
        def construct_event(payload, sig, secret): return json.loads(payload)
    monkeypatch.setattr(billing, 'stripe', SimpleNamespace(api_key=None, Webhook=Webhook))
    with get_db_connection() as conn:
        conn.execute("UPDATE users SET stripe_customer_id='cus_ooo' WHERE id=1"); conn.commit()
    e=event('evt_invoice_first','invoice.paid',{'customer':'cus_ooo','subscription':'sub_ooo','lines':{'data':[{'period':{'end':2000003000}}]}})
    assert asyncio.run(billing.webhook(Req(1, json.dumps(e).encode())))['received'] is True
    ent=current_entitlement_for_user_id(1)
    assert ent['hasFullAccess'] is True
    with get_db_connection() as conn:
        row=conn.execute("SELECT stripe_subscription_id, stripe_customer_id FROM users WHERE id=1").fetchone()
    assert row['stripe_subscription_id'] == 'sub_ooo'
    assert row['stripe_customer_id'] == 'cus_ooo'

def test_webhook_valid_event_missing_local_user_does_not_crash(monkeypatch):
    class Webhook:
        @staticmethod
        def construct_event(payload, sig, secret): return json.loads(payload)
    monkeypatch.setattr(billing, 'stripe', SimpleNamespace(api_key=None, Webhook=Webhook))
    e=event('evt_missing_user','customer.subscription.created',{'id':'sub_missing','customer':'cus_missing','status':'active','metadata':{'user_id':'999'}})
    assert asyncio.run(billing.webhook(Req(1, json.dumps(e).encode())))['received'] is True

def test_webhook_supports_object_like_stripe_payloads(monkeypatch):
    sub=AttrObj(id='sub_attr',customer='cus_attr',status='active',current_period_end=2000004000,cancel_at_period_end=False,metadata={'user_id':'1'},discount=None)
    ev=AttrObj(id='evt_attr',type='customer.subscription.created',data=AttrObj(object=sub))
    class Webhook:
        @staticmethod
        def construct_event(payload, sig, secret): return ev
    monkeypatch.setattr(billing, 'stripe', SimpleNamespace(api_key=None, Webhook=Webhook))
    assert asyncio.run(billing.webhook(Req(1, b'{}')))['received'] is True
    ent=current_entitlement_for_user_id(1)
    assert ent['hasFullAccess'] is True and ent['status'] == 'active'

def test_portal_creation_uses_customer_id(monkeypatch):
    with get_db_connection() as conn:
        conn.execute("UPDATE users SET stripe_customer_id='cus_portal' WHERE id=1"); conn.commit()
    calls={}
    class Session:
        @staticmethod
        def create(**kwargs): calls.update(kwargs); return SimpleNamespace(url='https://portal.test/session')
    monkeypatch.setattr(billing, 'stripe', SimpleNamespace(api_key=None, billing_portal=SimpleNamespace(Session=Session)))
    assert billing.portal(Req(1))['url'].startswith('https://portal')
    assert calls['customer'] == 'cus_portal'
