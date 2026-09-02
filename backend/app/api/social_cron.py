"""Authenticated production scheduler; publishing remains off by default."""
import hmac
import os
from fastapi import APIRouter, HTTPException, Request
from backend.app.services import social_marketing

router=APIRouter()


@router.get('/api/cron/social-daily')
def social_daily(request:Request):
    secret=os.getenv('CRON_SECRET','')
    if not secret or not hmac.compare_digest(request.headers.get('authorization',''),f'Bearer {secret}'):
        raise HTTPException(401,'Cron authentication required')
    if os.getenv('SOCIAL_SCHEDULER_ENABLED','false').lower()!='true':
        return {'status':'DISABLED','published':False}
    try:
        return social_marketing.daily()
    except Exception:
        # Never echo OAuth, database URLs, source payloads or provider error bodies.
        raise HTTPException(503,'Social job blocked; check configuration and verified-source freshness') from None
