#!/usr/bin/env python3
"""Narrow saved sealed connector, NOT an HTTP owner-action capability.

Only the pre-authored, accepted-runtime command in an actual owner's saved card
may run this local operation. CHAT_ID is a context signal, not authentication.
No owner bearer is read/minted. A five-minute own-app token is used ONLY to PUT
one encrypted slot via the public secret route and its lifecycle lock. All
privileged domain HTTP guards remain unchanged. Submitted values are stdin-only.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit
import urllib.request
from assistant_store import Store
import telegram_store as tg
from telegram_http import call as telegram, bot_identity, NoRedirect
from setup_spec import consumer_command, request_identity


class SetupBlocked(ValueError):pass

def installation(app_id, nonce=None, revision=None):
    if type(app_id) is not int or app_id <= 0:raise ValueError('Invalid installation')
    data=Path(os.environ.get('DATA_DIR','/data')).resolve()
    with sqlite3.connect(f'file:{data}/db/ultimate.db?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        row=db.execute('SELECT token_nonce,runtime_revision,capability_contract FROM apps WHERE id=? AND deleted_at IS NULL',(app_id,)).fetchone()
        if not row:raise ValueError('Installation unavailable')
        contract=json.loads(row['capability_contract'] or '{}')
        service=contract.get('service') or {}
        if service.get('id')!='assistant-studio' or service.get('entry')!='service.py' or sorted((contract.get('data') or {}).get('job_secret_read',[]))!=[f'tg-{i}' for i in range(1,9)]:raise ValueError('Wrong accepted installation')
        if (nonce is not None and row['token_nonce']!=nonce) or (revision is not None and row['runtime_revision']!=revision):raise ValueError('Installation changed')
        if not re.fullmatch(r'[a-f0-9]{32}',row['token_nonce'] or '') or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}',row['runtime_revision'] or ''):raise ValueError('No accepted runtime')
        owner=db.execute('SELECT username,token_epoch FROM owner').fetchall()
        if len(owner)!=1:raise ValueError('Owner unavailable')
    runtime=data/'app-runtime'/str(app_id)/row['runtime_revision']
    return {'data':data,'runtime':runtime,'nonce':row['token_nonce'],'revision':row['runtime_revision'],'owner':dict(owner[0])}


def authoring_owner():
    """Resolve a real current installation-owner run BEFORE any setup mutation.

    Imports protected platform auth only in this explicitly requested owner-chat
    procedure. A guest/delegated run cannot launder its origin into a saved card.
    """
    if (not os.environ.get('CHAT_ID') or not os.environ.get('AGENT_TOKEN')
            or any(os.environ.get(k) for k in ('APP_TOKEN','APP_OWNER_ACTION'))):
        raise ValueError('Actual installation owner chat required')
    sys.path.insert(0,'/data/platform/backend')
    from app import models
    from app.database import SessionLocal
    from app.deps import get_agent_run_principal, require_installation_owner_control
    with SessionLocal() as db:
        principal=get_agent_run_principal(os.environ['AGENT_TOKEN'],db)
        require_installation_owner_control(principal)
        chat=db.query(models.Chat).filter(models.Chat.id==os.environ['CHAT_ID'],models.Chat.deleted_at.is_(None)).first()
        origin=db.query(models.ChatRun).filter(models.ChatRun.id==principal.run_id,models.ChatRun.chat_id==os.environ['CHAT_ID']).first()
        if (not chat or principal.chat_id!=chat.id or not origin or origin.browser_grant_id is not None
                or db.query(models.Delegation).filter(models.Delegation.child_chat_id==chat.id).first()):
            raise ValueError('Current installation owner run required')
        return principal.run_id


def sealed_claim(current,app_id,nonce,revision,identity,epoch,slot,api_base,owner_run):
    """Require backend-owned consuming metadata for the reviewed owner-run card.

    CHAT_ID alone and an arbitrary matching argv are NOT authorization. No
    transcript/card answers/secret values are read; core persists no field values.
    """
    if not isinstance(owner_run,str) or not owner_run or len(owner_run)>64:
        raise ValueError('Exact authoring owner run required')
    command=consumer_command(sys.executable,Path(__file__).resolve(),app_id,nonce,revision,identity,epoch,slot,api_base,owner_run)
    chat_id=os.environ.get('CHAT_ID')
    exact_id=request_identity(chat_id,owner_run,command,str(current['runtime']))
    with sqlite3.connect(f"file:{current['data']}/db/ultimate.db?mode=ro",uri=True) as db:
        origin=db.execute('SELECT browser_grant_id FROM chat_runs WHERE id=? AND chat_id=?',(owner_run,chat_id)).fetchone()
        if (not origin or origin[0] is not None
                or db.execute('SELECT 1 FROM delegations WHERE child_chat_id=?',(chat_id,)).fetchone()):
            raise ValueError('Saved card was not authored by installation owner')
        rows=db.execute("SELECT s.command_json,s.cwd,s.action FROM saved_secure_inputs s JOIN chats c ON c.id=s.chat_id WHERE s.chat_id=? AND s.request_id=? AND s.status='consuming' AND c.deleted_at IS NULL AND c.pending_question_id=s.request_id",(chat_id,exact_id)).fetchall()
    if (len(rows)!=1 or rows[0][1]!=str(current['runtime']) or rows[0][2]!='run'
            or json.loads(rows[0][0])!=command):
        raise ValueError('No exact consuming reviewed sealed card')


def own_bearer(app_id,current):
    # Imported only inside the approved sealed operation. The core auth helper
    # uses protected signing material in RAM. Never import/call it in prep tests
    # without synthetic settings. No service/job/owner claims are fabricated.
    backend=Path('/data/platform/backend')
    if not (backend/'app/auth.py').is_file():raise ValueError('Public host auth unavailable')
    sys.path.insert(0,str(backend))
    from app.auth import create_app_token
    return create_app_token(app_id,current['owner']['username'],current['owner']['token_epoch'],
                            app_nonce=current['nonce'],expires_delta=timedelta(minutes=5))


def put_key(app_id,slot,key,api_base,bearer):
    parsed=urlsplit(api_base)
    if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
        raise ValueError('Local platform endpoint required')
    request=urllib.request.Request(api_base.rstrip('/')+f'/api/apps/{app_id}/secrets/tg-{slot}',
        data=json.dumps({'value':key}).encode(),method='PUT',
        headers={'Authorization':'Bearer '+bearer,'Content-Type':'application/json'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    try:
        with opener.open(request,timeout=15) as response:
            if response.status!=204:raise ValueError()
    except Exception:raise ValueError('Encrypted storage could not finish') from None


def connect(app_id,nonce,revision,identity,epoch,slot,key,api_base,owner_run):
    if (not os.environ.get('CHAT_ID') or any(os.environ.get(k) for k in ('APP_TOKEN','AGENT_TOKEN','APP_OWNER_ACTION'))
        or not re.fullmatch(r'[a-z0-9_-]{1,80}',identity) or not re.fullmatch(r'[a-f0-9]{32}',epoch)
        or type(slot) is not int or not 1<=slot<=tg.MAX_CONNECTIONS):raise ValueError('Saved sealed consumer required')
    current=installation(app_id,nonce,revision)
    if Path(__file__).resolve()!=current['runtime']/'connect_telegram.py':raise ValueError('Accepted consumer required')
    sealed_claim(current,app_id,nonce,revision,identity,epoch,slot,api_base,owner_run)
    # This core pin protects accepted bytes from pruning; explicit rechecks below
    # additionally reject Apply/uninstall/reinstall races. It is not an owner grant.
    sys.path.insert(0,'/data/platform/backend')
    from app.applied_app_runtime import hold_runtime
    with hold_runtime(app_id):
        installation(app_id,nonce,revision)
        sealed_claim(current,app_id,nonce,revision,identity,epoch,slot,api_base,owner_run)
        root=Store(current['data']/'apps'/str(app_id));prepared=False;phase='invalid-key'
        try:
            row=tg.connection(root,identity)
            if not row or row['state']!='reserved' or row['epoch']!=epoch or row['slot']!=slot:raise ValueError('Setup changed')
            if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]{20,150}',key):raise ValueError()
            phase='telegram-check';bot=bot_identity(telegram(key,'getMe'));hook=telegram(key,'getWebhookInfo')
            if not isinstance(hook,dict) or hook.get('url'):phase='webhook';raise ValueError()
            installation(app_id,nonce,revision)
            sealed_claim(current,app_id,nonce,revision,identity,epoch,slot,api_base,owner_run)
            phase='save-failed'
            if tg.prepare_identity(root,identity,{'epoch':epoch,'bot_id':bot['id'],'username':bot['username']}).get('status')!='ready':raise SetupBlocked('Identity unavailable')
            prepared=True
            # Fence domain reset/reassignment while the single encrypted write
            # is in flight. A failed PUT has an explicitly unknown outcome.
            with root.db:
                root.db.execute('BEGIN IMMEDIATE')
                pinned=root.db.execute('SELECT slot,state,epoch FROM telegram_connections WHERE assistant=?',(identity,)).fetchone()
                if not pinned or pinned['slot']!=slot or pinned['epoch']!=epoch or pinned['state']!='checking':raise ValueError('Setup changed')
                installation(app_id,nonce,revision)
                sealed_claim(current,app_id,nonce,revision,identity,epoch,slot,api_base,owner_run)
                bearer=own_bearer(app_id,current)
                try:put_key(app_id,slot,key,api_base,bearer)
                finally:bearer=None;key=None
                installation(app_id,nonce,revision)
                sealed_claim(current,app_id,nonce,revision,identity,epoch,slot,api_base,owner_run)
                # Existing owning seam rechecks epoch/state/empty owner/bot identity;
                # its final commit releases this fence after the transition.
                tg.await_owner(root,identity,{'epoch':epoch})
            # No polling, messages, run-job, activation, testing or publication.
            # The normal scheduled worker performs identity-only pairing intake.
        except SetupBlocked:
            # Preserve the owning seam's fixed duplicate/recovery diagnostic.
            # No key was written and no identity was claimed by this request.
            raise ValueError('Review the existing connection in Studio') from None
        except Exception:
            # If the installation changed do not mutate retained old storage.
            try:
                installation(app_id,nonce,revision)
                sealed_claim(current,app_id,nonce,revision,identity,epoch,slot,api_base,owner_run)
                tg.setup_error(root,identity,{'epoch':epoch,'code':phase})
                if prepared:tg.reset_verification(root,identity,epoch)
            except Exception:pass
            raise ValueError('Secure setup could not finish; inspect non-secret status') from None
        finally:root.db.close();key=None


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('app_id',type=int);p.add_argument('nonce');p.add_argument('revision')
    p.add_argument('assistant');p.add_argument('epoch');p.add_argument('slot',type=int)
    p.add_argument('--api-base',required=True);p.add_argument('--owner-run',required=True);args=p.parse_args()
    values={}
    try:
        raw=sys.stdin.read(16385)
        if len(raw)>16384:raise ValueError()
        values=json.loads(raw);raw=None
        if not isinstance(values,dict) or set(values)!={'bot_token'} or not isinstance(values['bot_token'],str):raise ValueError()
        key=values.pop('bot_token').strip();values.clear()
        connect(args.app_id,args.nonce,args.revision,args.assistant,args.epoch,args.slot,key,args.api_base,args.owner_run)
    finally:values.clear();key=None

if __name__=='__main__':
    try:main()
    except Exception:sys.exit(1)  # Never emit raw HTTP errors or secret values.
