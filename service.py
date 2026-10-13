"""Private owner management; no public content, files or stream endpoints."""
import json
import os
import sys
from assistant_store import Store, Problem
from events import changed
from catalog import Catalog
import telegram_store as tg

def dispatch(store, request):
    path = request.get('path','').strip('/')
    method = request.get('method')
    body = request.get('body') or {}
    if not isinstance(body, dict): raise Problem('Expected an object.')
    actor = request.get('actor') or {}
    # Public admission requires a separately approved deployment. Reject even
    # valid participant sessions arriving through an accidental public route.
    if request.get('public') or actor.get('scope') not in ('owner','app','agent') or actor.get('delegated') or actor.get('delegation_id') is not None:
        raise Problem('This local prototype is private to its owner.',403)
    # A guest's owner/app-shaped bearer is NOT installation-owner authority.
    # Only metadata-only catalog navigation is intentionally guest-readable;
    # every other management read/mutation is denied at this owning seam.
    if (actor.get('browser_grant_id') is not None or actor.get('browser_session_id') is not None) and not (path=='owner/catalog' and method=='GET'):
        raise Problem('Only the installation owner can manage this Studio.',403)
    own_id = int(os.environ.get('APP_ID','0'))
    foreign = actor.get('scope') == 'app' and actor.get('app_id') != own_id
    catalog = Catalog(store)
    if path.startswith('bridge-access/'):
        return bridge_access(store, request)
    if path == 'owner/access-bridge' and method == 'POST':
        if actor.get('scope') not in ('owner','agent'):raise Problem('Trusted owner confirmation required.',403)
        return connect_access_bridge(store, body)
    if foreign:
        allowed = store.config('owner_navigation_app_ids', [])
        if actor.get('app_id') not in allowed or method != 'GET' or path != 'owner/catalog':
            raise Problem('Other apps may read only explicitly connected owner navigation metadata.',403)
        return catalog.listing()
    if path == 'owner/dashboard' and method == 'GET':
        from analytics import dashboard
        return dashboard(catalog)
    if path == 'owner/cost-assumptions' and method == 'POST':
        from cost_settings import save
        result=save(catalog,body);changed();return result
    if path == 'owner/catalog' and method == 'GET': return catalog.listing()
    if path == 'owner/create' and method == 'POST': return catalog.create(body)
    if path == 'owner/navigation' and method == 'POST':
        if actor.get('scope') not in ('owner','agent'):
            raise Problem('Only the owner can connect navigation.',403)
        app_id=body.get('app_id')
        if not isinstance(app_id,int) or isinstance(app_id,bool) or app_id<=0: raise Problem('Valid app ID required.')
        store.set_config('owner_navigation_app_ids', sorted(set(store.config('owner_navigation_app_ids',[])+[app_id])))
        return {'ok':True}
    if path.startswith('assistants/'):
        parts=path.split('/',2)
        if len(parts)!=3: raise Problem('Action not found.',404)
        identity,action=parts[1:]
        if action in ('admin/delete','admin/restore') and method=='POST':
            if actor.get('scope') not in ('owner','agent'):raise Problem('Use the trusted owner confirmation for this action.',403)
            from bot_lifecycle import change
            return change(catalog,identity,body,restore=action=='admin/restore')
        if identity not in catalog.ids():raise Problem('Assistant not found.',404)
        if action in ('development/test','development/stop-test','development/release') and method=='POST':
            if actor.get('scope') not in ('owner','agent'):raise Problem('Use the trusted owner confirmation for development actions.',403)
            from private_testing import change
            return change(catalog,identity,body,action.split('/')[-1])
        if action=='identity' and method=='POST':
            from bot_identity import save
            result=save(catalog,identity,body);changed();return result
        if action=='access/users' and method=='GET':return tg.access_list(store,identity)
        if action=='access/change' and method=='POST':
            if actor.get('scope') not in ('owner','agent'):raise Problem('Use the trusted owner access confirmation.',403)
            return tg.change_access(store,identity,body)
        if action.startswith('telegram/'):
            if action=='telegram/state' and method=='GET':return tg.public_state(store,identity)
            if action=='telegram/pairing-code' and method=='POST':
                result=tg.issue_pairing(store,identity,body);changed();return result
            if action in ('telegram/prepare-identity','telegram/setup-error','telegram/reconnect-plan','telegram/reconnect') and method=='POST':
                if actor.get('scope') not in ('owner','agent'):raise Problem('Use the trusted owner setup flow.',403)
                if action=='telegram/prepare-identity':return tg.prepare_identity(store,identity,body)
                if action=='telegram/setup-error':return tg.setup_error(store,identity,body)
                from telegram_reconnect import plan,commit
                return (plan if action.endswith('reconnect-plan') else commit)(store,identity,body)
            if action in ('telegram/reserve','telegram/await-owner','telegram/activate','telegram/connection','telegram/begin-verify','telegram/reset-setup'):
                if actor.get('scope') not in ('owner','agent'):raise Problem('This step needs the owner’s secure setup flow.',403)
                if action=='telegram/begin-verify' and method=='POST':return tg.begin_verification(store,identity,body.get('epoch'))
                if action=='telegram/reset-setup' and method=='POST':return tg.reset_verification(store,identity,body.get('epoch'))
                if action=='telegram/connection' and method=='GET':return tg.connection(store,identity)
                if action=='telegram/reserve' and method=='POST':return tg.reserve(store,identity)
                if action=='telegram/await-owner' and method=='POST':return tg.await_owner(store,identity,body)
                if action=='telegram/activate' and method=='POST':return tg.activate(store,identity,body)
            if action=='telegram/pause' and method=='POST':return tg.pause(store,identity)
            if action=='telegram/invite' and method=='POST':raise Problem('Joining now needs owner approval. Share the bot link and review requests in Users.',409)
            raise Problem('Setup action not found.',404)
        with catalog.open(identity) as selected:
            result=domain_dispatch(selected, {**request,'path':action})
            if action=='admin/export':
                conn=tg.public_state(store,identity)
                result['deployment']['channels']=['telegram'] if conn.get('username') else []
            if action=='admin/state':
                state=tg.public_state(store,identity)['state'];result['deployment']['telegram']=state
                if state=='active':result['deployment']['access']='approval-only'
            return result
    if 'pocket-notes' not in catalog.ids():raise Problem('Assistant not found or deleted.',404)
    return domain_dispatch(store, request)

# One explicitly reviewed user-management scope, never owner-agent authority.
ACCESS_SCOPE='owned-bots-v1'

def connect_access_bridge(root, body):
    import re
    app_id=body.get('app_id');grant=body.get('grant')
    if type(app_id) is not int or app_id<=0 or not re.fullmatch(r'[a-f0-9]{12}',str(grant or '')):
        raise Problem('Review the Bridge connection first.')
    key=f'bridge-access:{grant}';current_key=f'bridge-access-owner:{app_id}'
    with root.db:
        root.db.execute('BEGIN IMMEDIATE')
        existing=root.config(key)
        if body.get('operation')=='disconnect':
            if not existing or existing.get('scope')!=ACCESS_SCOPE or existing['app_id']!=app_id:
                raise Problem('Connection unavailable.',409)
            if root.config(current_key)==grant:
                root.db.execute('UPDATE config SET value=? WHERE key=?',('null',current_key))
            return {'ok':True}
        # Old per-bot approvals cannot be replayed into broader authority.
        if (body.get('scope')!=ACCESS_SCOPE or type(body.get('owner_user_id')) is not int
            or body['owner_user_id']<=0 or not re.fullmatch(r'[a-f0-9]{64}',str(body.get('binding') or ''))):
            raise Problem('Review the current-and-future owned-bot permission explicitly.')
        value={k:body[k] for k in ('scope','app_id','grant','owner_user_id','binding')}
        if existing and existing!=value:raise Problem('Connection identity already used.',409)
        for k,v in ((key,value),(current_key,grant)):
            root.db.execute('INSERT OR REPLACE INTO config(key,value) VALUES (?,?)',(k,json.dumps(v)))
    return {'ok':True}


def access_bot_handle(grant, identity, epoch, generation):
    import hashlib
    return hashlib.sha256(json.dumps([grant,identity,epoch,generation]).encode()).hexdigest()[:12]


def access_bots(root,grant):
    from bot_lifecycle import generation
    catalog=Catalog(root);tg.schema(root);bots=[]
    for identity in catalog.ids():
        conn=root.db.execute('SELECT * FROM telegram_connections WHERE assistant=?',(identity,)).fetchone()
        if not conn or conn['owner_user_id']!=grant['owner_user_id']:continue
        with catalog.open(identity) as selected:
            if not selected.config('deployed'):continue
            name=selected.config('draft')['name']
        gen=generation(root,identity)
        bots.append({'assistant':identity,'name':name,'connection':grant['grant'],
                     'grant':access_bot_handle(grant['grant'],identity,conn['epoch'],gen),
                     'epoch':conn['epoch'],'generation':gen,
                     'notifications':root.config(f"bridge-notify:{grant['app_id']}:{grant['owner_user_id']}:{identity}",True)})
    return bots


def access_bridge_binding(grant):
    import urllib.request
    # Studio's ordinary service/job credential only. The Bridge endpoint checks
    # the calling Studio app, grant id, live pairing and enabled state itself.
    url=os.environ['API_BASE_URL'].rstrip('/')+'/api/services/telegram-bridge/access-binding/'+grant['grant']
    request=urllib.request.Request(url,headers={'Authorization':'Bearer '+os.environ['APP_TOKEN']})
    try:
        with urllib.request.urlopen(request,timeout=5) as response:result=json.load(response)
    except Exception:raise Problem('The owner Bridge connection is unavailable. Reconnect in Möbius.',403) from None
    if result.get('binding')!=grant['binding'] or result.get('app_id')!=grant['app_id']:
        raise Problem('The owner Bridge pairing changed. Reconnect in Möbius.',403)


def bridge_access(root, request):
    import re
    from bot_lifecycle import generation,deleted
    actor=request.get('actor') or {};parts=request['path'].strip('/').split('/')
    if len(parts) not in (3,4) or not re.fullmatch(r'[a-f0-9]{12}',parts[1]):raise Problem('Access connection unavailable.',403)
    grant=root.config('bridge-access:'+parts[1]);bot=None
    def authorize():
        if (actor.get('scope')!='app' or actor.get('delegated') or not grant
            or grant.get('scope')!=ACCESS_SCOPE or actor.get('app_id')!=grant['app_id']
            or root.config(f"bridge-access-owner:{grant['app_id']}")!=grant['grant']):
            raise Problem('Access connection unavailable.',403)
        if bot:
            conn=root.db.execute('SELECT * FROM telegram_connections WHERE assistant=?',(bot['assistant'],)).fetchone()
            if (not conn or conn['owner_user_id']!=grant['owner_user_id'] or deleted(root,bot['assistant'])
                or access_bot_handle(grant['grant'],bot['assistant'],conn['epoch'],generation(root,bot['assistant']))!=bot['grant']):
                raise Problem('This bot changed. Reopen My bots.',409)
    authorize();access_bridge_binding(grant)
    bots=access_bots(root,grant)
    if parts[2:]==['bots'] and request['method']=='GET':return {'bots':bots}
    if len(parts)!=4:raise Problem('Only bot user management is connected.',403)
    bot=next((b for b in bots if b['grant']==parts[2]),None)
    if not bot:raise Problem('This bot changed or is not owned by the paired user. Reopen My bots.',409)
    operation=parts[3];method=request['method'];body=request.get('body') or {}
    if method=='GET' and operation=='users':
        state=tg.access_list(root,bot['assistant']);authorize()
        return {**state,'assistant':bot['assistant'],'notifications':bot['notifications']}
    if method=='POST' and operation=='notifications':
        if type(body.get('enabled')) is not bool:raise Problem('Choose on or off.')
        with root.db:
            root.db.execute('BEGIN IMMEDIATE');authorize()
            root.db.execute('INSERT OR REPLACE INTO config(key,value) VALUES (?,?)',
                (f"bridge-notify:{grant['app_id']}:{grant['owner_user_id']}:{bot['assistant']}",json.dumps(body['enabled'])))
        return {'ok':True,'notifications':body['enabled']}
    if method=='POST' and operation=='change':
        if body.get('epoch')!=bot['epoch'] or body.get('generation')!=bot['generation']:
            raise Problem('Review this bot again.',409)
        return tg.change_access(root,bot['assistant'],body,authorize=authorize)
    raise Problem('Only user access and request notifications are connected.',403)


def domain_dispatch(store, request):
    path=request.get('path','').strip('/')
    method=request.get('method');body=request.get('body') or {}
    actor=request.get('actor') or {}
    # Defense in depth for callers of the domain seam directly. Do not turn a
    # guest/delegation origin into a clean owner actor in a nested dispatch.
    if request.get('public') or actor.get('delegated') or actor.get('delegation_id') is not None or actor.get('browser_grant_id') is not None or actor.get('browser_session_id') is not None:
        raise Problem('Only the installation owner can manage this Studio.',403)
    if path in ('admin/publish','admin/apply') and method=='POST' and (request.get('actor') or {}).get('scope') not in ('owner','agent'):
        raise Problem('Publishing needs a trusted owner confirmation.',403)
    if path in ('admin/draft','admin/publish','admin/apply') and method=='POST':
        if not isinstance(body.get('expected_revision'),str): raise Problem('Read the current draft revision before changing it.',409)
    # Studio is a management plane, not a participant impersonation surface.
    # Reject old bookmarks/sessions too; hiding controls is not a privacy boundary.
    if path.startswith(('participant/','admin/notes')) or path in ('admin/session','admin/participants','admin/refresh-links'):
        raise Problem('Personal notebooks are available only to their participants in Telegram.',403)
    if path=='admin/editing/handoff' and method=='POST':
        from editing_chat import handoff
        return handoff(store,body)
    if path in ('admin/editing/prepare','admin/editing/ready') and method=='POST':
        if (request.get('actor') or {}).get('scope') not in ('owner','agent'):
            raise Problem('Opening an owner editing chat needs trusted owner approval.',403)
        from editing_chat import prepare,ready
        return (prepare if path.endswith('/prepare') else ready)(store,body)
    if path == 'admin/state'  and method == 'GET': return {**store.state(), 'worker_ready': True, 'model_connection': store.config('model_connection')}
    if path == 'admin/check-connection' and method == 'POST':
        store.set_config('connection_check_requested', True)
        return {'status':'queued', 'model_calls':0}
    if path == 'admin/apply' and method == 'POST': return store.apply_definition(body,body['expected_revision'])
    if path == 'admin/draft' and method == 'POST': return store.save_draft(body, body['expected_revision'])
    if path == 'admin/publish' and method == 'POST': return store.publish(body['expected_revision'])
    if path == 'admin/revoke' and method == 'POST': raise Problem('Use the trusted Users access confirmation.',403)
    if path == 'admin/export' and method == 'GET':
        return {'format':'mobius-assistant-v1','definition':store.config('draft'),'published':store.definition() if store.config('deployed') else None,'deployment':{'target':'local','version':store.config('deployed'),'channels':[]},'contains_user_data':False}
    raise Problem('Action not found.',404)

def main():
    store = Store(os.environ['APP_STORAGE_DIR'])
    try:
        request = json.load(sys.stdin)
        result = dispatch(store,request)
        if request.get('method') == 'POST' and request.get('path','').split('/')[-1] in ('draft','publish','apply','participants','revoke','save','ask','stop','tags','create','confirm','delete','restore','test','stop-test','release','reconnect','prepare-identity','setup-error','change'):
            changed()
        print(json.dumps({'body':result,'headers':{'Cache-Control':'no-store'}}))
    except Problem as exc:
        print(json.dumps({'status':exc.status,'body':{'error':str(exc)}}))
    except Exception:
        print(json.dumps({'status':500,'body':{'error':'The operation failed. Your existing collection has not been replaced.'}}))
    finally: store.db.close()

if __name__ == '__main__': main()
