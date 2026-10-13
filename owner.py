#!/usr/bin/env python3
"""Owner-agent client for Studio. Uses live service operations, never SQLite edits."""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from urllib.parse import quote

BASE='/api/services/assistant-studio/'

def api(path,body=None,method=None):
    if not os.environ.get('AGENT_TOKEN') or os.environ.get('APP_TOKEN'):raise RuntimeError('Actual owner chat required')
    request=urllib.request.Request(os.environ['API_BASE_URL'].rstrip('/')+path, data=json.dumps(body).encode() if body is not None else None,method=method,headers={'Authorization':'Bearer '+os.environ['AGENT_TOKEN'],'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=25) as response:
        raw=response.read();return json.loads(raw) if raw else None


def call(path, body=None):
    token=os.environ.get('AGENT_TOKEN')
    if not token or os.environ.get('APP_TOKEN') or os.environ.get('APP_OWNER_ACTION'): raise RuntimeError('Run this helper from an actual owner Möbius chat, not an app/service/job.')
    request=urllib.request.Request(os.environ['API_BASE_URL'].rstrip('/')+BASE+path,
      data=json.dumps(body).encode() if body is not None else None,
      headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=25) as response: return json.load(response)
    except urllib.error.HTTPError as exc:
        try: message=json.load(exc)
        except Exception: message={'error':f'Studio request failed ({exc.code}).'}
        raise RuntimeError(str(message.get('error') or message.get('detail') or 'Studio request failed.')) from None

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['action','customize','costs','save-costs','context','list','inspect','create','draft','identity','publish','export','reconnect-plan','reconnect'])
    p.add_argument('assistant',nargs='?')
    p.add_argument('--revision',help='Revision inspected and approved by the owner.')
    p.add_argument('--source',help='Deleted source for exact approved reconnect.')
    args=p.parse_args()
    if args.action=='action':
        from owner_action import execute
        result=execute(json.load(sys.stdin))
    elif args.action=='customize':
        if not args.assistant:p.error('Assistant ID required')
        from owner_action import execute
        result=execute({'action':'customize-bot','context':{'assistant':args.assistant}})
    elif args.action=='costs':
        data=call('owner/dashboard')
        result={key:data[key] for key in ('assumptions','cost_summary','cost_history')}
        result['bots']=[{key:bot[key] for key in ('id','name','uses_model','actual_model','runner_provider','cost')} for bot in data['bots']]
    elif args.action=='save-costs':
        if not args.revision:p.error('--revision is required; use costs first.')
        result=call('owner/cost-assumptions',{**json.load(sys.stdin),'expected_revision':args.revision})
    elif args.action=='context':
        chat=os.environ.get('CHAT_ID')
        matches=[item for item in call('owner/catalog')['assistants'] if chat and (item.get('editing_chat') or {}).get('id')==chat]
        result={'assistant':matches[0] if len(matches)==1 else None}
        if len(matches)==1:result['state']=call('assistants/'+matches[0]['id']+'/admin/state')
    elif args.action=='list':result=call('owner/catalog')
    elif args.action=='create':result=call('owner/create',json.load(sys.stdin))
    else:
        if not args.assistant:p.error('Assistant ID required; use list first.')
        prefix='assistants/'+quote(args.assistant,safe='')+'/admin/'
        if args.action=='identity':
            if not args.revision:p.error('--revision is required; inspect the display revision in list first.')
            patch=json.load(sys.stdin)
            result=call('assistants/'+quote(args.assistant,safe='')+'/identity',{**patch,'expected_revision':args.revision})
        elif args.action=='reconnect-plan':
            if not args.source:p.error('--source is required')
            result=call('assistants/'+quote(args.assistant,safe='')+'/telegram/reconnect-plan',{'source':args.source})
        elif args.action=='reconnect':
            from telegram_reconnect import reconnect
            result=reconnect(args.assistant,json.load(sys.stdin),call,api)
        elif args.action in ('inspect','export'):result=call(prefix+('state' if args.action=='inspect' else 'export'))
        elif args.action=='draft':
            if not args.revision:p.error('--revision is required to avoid overwriting another edit.')
            patch=json.load(sys.stdin)
            if not isinstance(patch,dict) or set(patch)-{'name','description','instructions','model','runtime'}:p.error('Draft input supports name, description, instructions, model and runtime only.')
            state=call(prefix+'state')
            if state['revision']!=args.revision:raise RuntimeError('The draft changed. Inspect it again before applying your edit.')
            definition={**state['draft'],**patch,'expected_revision':args.revision}
            result=call(prefix+'draft',definition)
        else:
            if not args.revision:p.error('--revision is required for the exact reviewed draft.')
            result=call(prefix+'publish',{'expected_revision':args.revision})
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    try:main()
    except (RuntimeError,ValueError) as exc:
        print(str(exc),file=sys.stderr);sys.exit(1)
