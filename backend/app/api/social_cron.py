"""Authenticated production scheduler; publishing remains off by default."""
import hmac
import os
from fastapi import APIRouter, HTTPException, Request
from backend.app.services import social_marketing

router=APIRouter()


def authorized(request:Request,name='CRON_SECRET'):
    secret=os.getenv(name,'')
    return bool(secret) and hmac.compare_digest(request.headers.get('authorization',''),f'Bearer {secret}')


@router.get('/api/cron/social-daily')
def social_daily(request:Request):
    if not authorized(request):
        raise HTTPException(401,'Cron authentication required')
    if os.getenv('SOCIAL_SCHEDULER_ENABLED','false').lower()!='true':
        return {'status':'DISABLED','published':False}
    try:
        return social_marketing.daily()
    except Exception:
        # Never echo OAuth, database URLs, source payloads or provider error bodies.
        raise HTTPException(503,'Social job blocked; check configuration and verified-source freshness') from None


@router.post('/api/cron/social-source')
async def social_source(request:Request):
    if not authorized(request,'SOCIAL_SYNC_SECRET'):
        raise HTTPException(401,'Social synchronization authentication required')
    if int(request.headers.get('content-length','0') or 0)>32768:
        raise HTTPException(413,'Source envelope too large')
    try:
        return social_marketing.accept_source_envelope(await request.json())
    except Exception:
        raise HTTPException(400,'Verified aggregate source rejected') from None


@router.get('/api/cron/social-status')
def social_status(request:Request):
    if not authorized(request,'SOCIAL_SYNC_SECRET'):
        raise HTTPException(401,'Social status authentication required')
    try:
        return social_marketing.campaign_report()
    except Exception:
        raise HTTPException(503,'Social status unavailable') from None
