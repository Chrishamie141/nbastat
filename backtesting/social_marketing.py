"""CLI for source synchronization, dry run, single publishing and daily X campaigns."""
import argparse
import json
import os
from pathlib import Path

from backend.app.services import social_marketing as social


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['init','sync-source','dry-run','publish-one','daily','report','reconcile'])
    p.add_argument('--week-db',type=Path)
    p.add_argument('--post-id')
    p.add_argument('--x-post-id')
    p.add_argument('--event',choices=list(social.EVENT_TEMPLATES))
    args=p.parse_args(argv)
    if args.action=='init': social.initialize();result={'status':'INITIALIZED'}
    elif args.action=='sync-source':
        if not args.week_db: p.error('--week-db is required')
        result=social.sync_source(args.week_db)
    elif args.action=='dry-run':
        # Never calls the publisher, even if environment switches are enabled.
        result=social.generate(event=args.event) if args.event else social.generate()
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


if __name__=='__main__': raise SystemExit(main())
