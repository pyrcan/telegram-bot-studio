#!/usr/bin/env python3
"""Owner-chat setup; saved sealed card is terminal, never an app password form."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from owner import call
from setup_spec import TITLE, DESCRIPTION, consumer_command


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','status','activate','pause']);p.add_argument('assistant');p.add_argument('--epoch');args=p.parse_args()
    prefix=f'assistants/{args.assistant}/telegram/'
    if args.action=='prepare':
        # Validate the real top-level installation-owner run before reserve.
        from connect_telegram import authoring_owner, installation
        owner_run=authoring_owner()
        app_id=call('owner/catalog')['app_id']
        current=installation(app_id)
        if Path(__file__).resolve()!=current['runtime']/'setup_telegram.py':
            raise RuntimeError('Run only the accepted owner setup source')
        sys.path.insert(0,'/data/platform/backend')
        from app.app_python_env import resolve_env,python_for
        interpreter=python_for(resolve_env(current['data'],app_id,current['runtime']))
        if authoring_owner()!=owner_run:raise RuntimeError('Owner run changed')
        installation(app_id,current['nonce'],current['revision'])
        row=call(prefix+'reserve',{})
        if row['state']!='reserved':print(json.dumps(row));return
        installation(app_id,current['nonce'],current['revision'])
        if authoring_owner()!=owner_run:raise RuntimeError('Owner run changed')
        command=consumer_command(interpreter,current['runtime']/'connect_telegram.py',app_id,
                                 current['nonce'],current['revision'],args.assistant,row['epoch'],row['slot'],
                                 os.environ['API_BASE_URL'],owner_run)
        helper=[sys.executable,'/data/platform/backend/scripts/secure-input.py','run',
            '--title',TITLE,'--description',DESCRIPTION,
            '--field','bot_token:password:Telegram bot key','--',*command]
        sys.exit(subprocess.run(helper,cwd=current['runtime']).returncode)
    elif args.action=='status':print(json.dumps(call(prefix+'connection'),indent=2))
    elif args.action=='pause':print(json.dumps(call(prefix+'pause',{})))
    else:
        if not args.epoch:p.error('--epoch must match the separately approved connection')
        print(json.dumps(call(prefix+'activate',{'epoch':args.epoch})))
        # Scheduled supervisor is the only listener; no outbound test sent here.

if __name__=='__main__':
    try:main()
    except Exception:print('Owner setup could not finish; review the current non-secret status.',file=sys.stderr);sys.exit(1)
