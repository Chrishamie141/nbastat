"""CLI for source synchronization, dry run, single publishing and daily X campaigns."""
import argparse
import json
import os
from pathlib import Path
from datetime import datetime,timedelta

from backend.app.services import social_marketing as social


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['init','sync-source','dry-run','preview-week','publish-one','daily','report','reconcile'])
    p.add_argument('--week-db',type=Path)
    p.add_argument('--post-id')
    p.add_argument('--x-post-id')
    p.add_argument('--event',choices=list(social.EVENT_TEMPLATES))
    args=p.parse_args(argv)
    local_runtime=None
    if args.action in ('dry-run','preview-week') and not os.getenv('SOCIAL_DATABASE_URL'):
        from backtesting.week1_worker import configure_local, DB
        local_runtime=configure_local()
        social.initialize()
        social.sync_source(DB)
    if args.action=='init': social.initialize();result={'status':'INITIALIZED'}
    elif args.action=='sync-source':
        if not args.week_db: p.error('--week-db is required')
        result=social.sync_source(args.week_db)
    elif args.action=='dry-run':
        # Never calls the publisher, even if environment switches are enabled.
        result=social.generate(event=args.event) if args.event else social.generate()
    elif args.action=='preview-week':
        result=preview_week()
        if local_runtime:
            from backtesting.week1_worker import atomic_json
            atomic_json(local_runtime/'seven-day-previews.json',result)
    elif args.action=='publish-one':
        if not args.post_id: p.error('--post-id is required')
        result=social.publish_one(args.post_id)
    elif args.action=='reconcile':
        if not args.post_id or not args.x_post_id:p.error('--post-id and --x-post-id required')
        result=social.reconcile_published(args.post_id,args.x_post_id)
    elif args.action=='daily': result=social.daily()
    else: result=social.campaign_report()
    print(json.dumps(result,indent=2))
    return 0


def preview_week(clock=social.now):
    """Editorial previews only: current verified evidence, never future performance."""
    at=clock()
    start=datetime.fromisoformat(os.environ['SOCIAL_CAMPAIGN_START']).date()
    with social.connection() as c:
        source_id,source=social.load_source(c,clock=clock)
    previews=[]
    for offset in range(7):
        date=at.date()+timedelta(days=offset)
        index=(date-start).days
        if index not in range(14):
            previews.append(dict(date=date.isoformat(),status='OUTSIDE_APPROVED_CAMPAIGN'))
            continue
        category,content=social.render(index,source)
        previews.append(dict(date=date.isoformat(),category=category,content=content,
                             status='PREVIEW_ONLY',source_id=source_id,data_as_of=source['verified_at']))
    return dict(published=False,notice='Planning previews use current verified data, not future results. Regenerate on the actual day.',previews=previews)


if __name__=='__main__': raise SystemExit(main())
