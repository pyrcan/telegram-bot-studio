#!/usr/bin/env python3
"""Sealed consumer: verify a specialist token and store it encrypted, never activate.

Reads secrets ONLY from stdin, never logs them, never returns them. Run via the
platform secure-input helper. Runtime bot requests use their own app-scoped job credential.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

def owner_bearer():
    # Saved sealed consumers deliberately receive no expired agent-run bearer.
    # This owner-approved local consumer uses the existing local service
    # credential in RAM only, never copied into env, arguments or output.
    # Background app jobs must keep using their scoped APP_TOKEN instead.
    if os.environ.get('APP_TOKEN'):
        raise RuntimeError('App jobs cannot use owner setup authority.')
    if os.environ.get('AGENT_TOKEN'):
        return os.environ['AGENT_TOKEN']
    if not os.environ.get('CHAT_ID') or os.environ.get('APP_TOKEN'):
        raise RuntimeError('Owner sealed setup context required.')
    return (Path(os.environ.get('DATA_DIR','/data'))/'service-token.txt').read_text().strip()

def call(path,body=None):
    return api('/api/services/assistant-studio/'+path,body)

def api(path,body=None,method=None):
    request=urllib.request.Request(os.environ['API_BASE_URL'].rstrip('/')+path,
      data=json.dumps(body).encode() if body is not None else None,method=method,
      headers={'Authorization':'Bearer '+owner_bearer(),'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=20) as response:
        raw=response.read();return json.loads(raw) if raw else None

def telegram(token,method):
    from telegram_http import call
    if method not in ('getMe','getWebhookInfo'):raise ValueError()
    return call(token,method)

class SetupBlocked(ValueError):pass


def connect(identity,epoch,token):
    phase='invalid-key';prepared=False
    try:
        if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]{20,150}',token):raise ValueError()
        current=call(f'assistants/{identity}/telegram/connection')
        if not current or current['epoch']!=epoch or current['state']!='reserved':raise SetupBlocked()
        phase='telegram-check'
        bot=telegram(token,'getMe');hook=telegram(token,'getWebhookInfo')
        if not bot.get('is_bot'):raise ValueError()
        if hook.get('url'):phase='webhook';raise ValueError()
        phase='owner-check'
        bridges=api('/api/apps/')
        candidates=[a for a in bridges if ((a.get('capability_contract') or {}).get('service') or {}).get('id')=='telegram-bridge']
        if len(candidates)!=1:raise ValueError()
        bridge_id=candidates[0]['id']
        paired=api(f'/api/storage/apps/{bridge_id}/pairing.json')
        if (not paired or type(paired.get('userId')) is not int or paired['userId']<=0
            or paired.get('chatId')!=paired['userId']):raise ValueError()
        owner_bot=api(f'/api/apps/{bridge_id}/service/bot-info')
        if owner_bot['id']==bot['id']:phase='owner-bridge';raise ValueError()
        phase='save-failed'
        checked={'epoch':epoch,'bot_id':bot['id'],'username':bot['username'],'owner_user_id':paired['userId']}
        result=call(f'assistants/{identity}/telegram/prepare-identity',checked)
        if result.get('status')!='ready':raise SetupBlocked()
        prepared=True
        app_id=call('owner/catalog')['app_id'];secret=f"tg-{current['slot']}"
        api(f'/api/apps/{app_id}/secrets/{secret}',{'value':token},'PUT')
        call(f'assistants/{identity}/telegram/verified',checked)
    except SetupBlocked:
        # A duplicate has a server-authored, non-secret resolution in Studio.
        raise ValueError('Review connection status in Studio') from None
    except Exception:
        # Never persist arbitrary exception text: Telegram URLs contain keys.
        # A failed status write must not prevent releasing an identity claim.
        try:call(f'assistants/{identity}/telegram/setup-error',{'epoch':epoch,'code':phase})
        except Exception:pass
        if prepared:
            try:call(f'assistants/{identity}/telegram/reset-setup',{'epoch':epoch})
            except Exception:pass
        raise ValueError('Secure setup could not finish') from None

if __name__=='__main__':
    try:
        parser=argparse.ArgumentParser()
        parser.add_argument('assistant');parser.add_argument('epoch')
        parser.add_argument('--api-base',required=True)
        args=parser.parse_args()
        # Non-secret connection context belongs to the saved command; the
        # backend does not inherit the publishing chat's environment.
        os.environ['API_BASE_URL']=args.api_base
        values=json.load(sys.stdin);value=values.pop('bot_token','');values.clear()
        connect(args.assistant,args.epoch,value.strip());value=''
    except Exception:
        # The sealed runner reports a fixed failed receipt, never exception
        # text (HTTP errors can embed token-bearing URLs).
        sys.exit(1)
