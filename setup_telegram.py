#!/usr/bin/env python3
"""Owner setup helper. Credentials are requested only by the trusted sealed card."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from owner import call

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','status','activate','pause']);p.add_argument('assistant');p.add_argument('--epoch');args=p.parse_args()
 prefix=f'assistants/{args.assistant}/telegram/'
 if args.action=='prepare':
  row=call(prefix+'reserve',{})
  if row['state']!='reserved':print(json.dumps(row));return
  # This subprocess publishes the terminal card. Print nothing after receipt.
  command=[sys.executable,'/data/platform/backend/scripts/secure-input.py','run',
   '--title','Connect your Telegram bot','--description','Verifies your separate specialist bot and stores its key encrypted. It will not read messages, send replies or activate the bot yet. Use a bot not running elsewhere.',
   '--field','bot_token:password:Telegram bot key','--',sys.executable,str(Path(__file__).with_name('connect_telegram.py')),args.assistant,row['epoch'],'--api-base',os.environ['API_BASE_URL']]
  result=subprocess.run(command);sys.exit(result.returncode)
 elif args.action=='status':print(json.dumps(call(prefix+'connection'),indent=2))
 elif args.action=='pause':print(json.dumps(call(prefix+'pause',{})))
 else:
  if not args.epoch:p.error('--epoch must match the separately approved connection')
  print(json.dumps(call(prefix+'activate',{'epoch':args.epoch})))
  # Normal supervised activation, not an outbound test.
  from connect_telegram import api
  app_id=call('owner/catalog')['app_id'];api(f'/api/apps/{app_id}/run-job',{},'POST')

if __name__=='__main__':
 try:main()
 except RuntimeError as exc:print(str(exc),file=sys.stderr);sys.exit(1)
