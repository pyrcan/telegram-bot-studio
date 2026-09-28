"""Private Telegram deployment state and durable intake/delivery boundaries.

Connection metadata is not a credential. Bot credentials live only in the
platform's encrypted app-secret store, read only by the approved app job/service.
"""
import hashlib
import json
import re
import secrets
import time
from assistant_store import Problem, uid, required
from catalog import Catalog

MAX_CONNECTIONS=8

def schema(store):
    store.db.executescript('''
      CREATE TABLE IF NOT EXISTS telegram_connections(assistant TEXT PRIMARY KEY,slot INTEGER UNIQUE NOT NULL,
        bot_id INTEGER UNIQUE,username TEXT,state TEXT NOT NULL,epoch TEXT NOT NULL,owner_user_id INTEGER,
        last_seen REAL,error TEXT);
      CREATE TABLE IF NOT EXISTS tg_members(user_id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL,
        participant TEXT NOT NULL UNIQUE REFERENCES participants(id), mode TEXT);
      CREATE TABLE IF NOT EXISTS tg_access_requests(user_id INTEGER PRIMARY KEY,name TEXT NOT NULL,
        username TEXT NOT NULL DEFAULT '',created REAL NOT NULL,state TEXT NOT NULL DEFAULT 'pending',epoch TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS tg_invites(hash TEXT PRIMARY KEY,expires REAL NOT NULL,used INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS tg_updates(id INTEGER PRIMARY KEY,body TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending');
      CREATE TABLE IF NOT EXISTS tg_deliveries(id TEXT PRIMARY KEY,chat_id INTEGER NOT NULL,body TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',created REAL NOT NULL,message_id INTEGER, retry_at REAL NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS tg_inputs(update_id INTEGER PRIMARY KEY, participant TEXT NOT NULL, text TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS tg_requests(task_id TEXT PRIMARY KEY,chat_id INTEGER NOT NULL,delivered INTEGER NOT NULL DEFAULT 0);
    ''')
    for table,column,declaration in (
        ('telegram_connections','setup',"TEXT NOT NULL DEFAULT '{}'"),
        ('telegram_connections','poll_health',"TEXT NOT NULL DEFAULT '{}'"),
        ('tg_updates','destination',"TEXT NOT NULL DEFAULT 'live'"),
        ('tg_deliveries','purpose',"TEXT NOT NULL DEFAULT ''"),
        ('tg_deliveries','context',"TEXT NOT NULL DEFAULT '{}'"),
        ('tg_members','notebook_since','REAL NOT NULL DEFAULT 0'),
        ('tg_members','notebook_panel','INTEGER'),
        ('tg_deliveries','method',"TEXT NOT NULL DEFAULT 'sendMessage'"),
        ('tg_members','controls_version','INTEGER NOT NULL DEFAULT 0'),
        ('tg_members','username',"TEXT NOT NULL DEFAULT ''"),
        ('tg_members','access_epoch',"TEXT NOT NULL DEFAULT ''"),
        ('tg_members','access_since','REAL NOT NULL DEFAULT 0'),
        ('tg_members','selection',"TEXT NOT NULL DEFAULT '[]'"),
    ):
        if column not in {row[1] for row in store.db.execute(f'PRAGMA table_info({table})')}:
            with store.db:
                store.db.execute('BEGIN IMMEDIATE')
                if column not in {row[1] for row in store.db.execute(f'PRAGMA table_info({table})')}:
                    store.db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {declaration}')

    from telegram_notebook import schema as notebook_schema
    notebook_schema(store)

def connection(root, identity):
    schema(root)
    row=root.db.execute('SELECT * FROM telegram_connections WHERE assistant=?',(identity,)).fetchone()
    return dict(row) if row else None

def public_state(root,identity):
    row=connection(root,identity)
    if not row:return {'state':'not-connected'}
    result={k:row[k] for k in ('state','username','last_seen','error','epoch')}
    result['reconnect']=json.loads(row['setup']).get('reconnect')
    result['poll_health']=json.loads(row['poll_health'])
    with Catalog(root).open(identity,include_deleted=True) as selected:
        schema(selected)
        from telegram_menu import contract
        result['menu_ready']=selected.config('telegram_menu')==contract(selected,row)
        from private_testing import owner_joined
        result['owner_joined']=owner_joined(selected,row['owner_user_id'])
    return result

def reserve(root,identity):
    if identity not in Catalog(root).ids():raise Problem('Bot not found.',404)
    schema(root)
    with root.db:
        root.db.execute('BEGIN IMMEDIATE')
        row=root.db.execute('SELECT * FROM telegram_connections WHERE assistant=?',(identity,)).fetchone()
        if row:return dict(row)
        used={r[0] for r in root.db.execute('SELECT slot FROM telegram_connections')}
        slot=next((i for i in range(1,MAX_CONNECTIONS+1) if i not in used),None)
        if not slot:raise Problem('This pilot supports eight Telegram connections.',409)
        root.db.execute('INSERT INTO telegram_connections(assistant,slot,state,epoch) VALUES (?,?,?,?)',(identity,slot,'reserved',uid()))
    return connection(root,identity)

def begin_verification(root,identity,epoch):
    schema(root)
    with root.db:
        n=root.db.execute("UPDATE telegram_connections SET state='checking' WHERE assistant=? AND epoch=? AND state='reserved'",(identity,epoch)).rowcount
    if not n:raise Problem('Another setup is already checking this bot. Finish it first.',409)
    return {'ok':True}

def reset_verification(root,identity,epoch):
    # Only pre-activation metadata is reset. An already verified/active bot and
    # its credential are never overwritten by a stale failed setup.
    with root.db:root.db.execute("UPDATE telegram_connections SET state='reserved',epoch=?,bot_id=NULL,username=NULL,owner_user_id=NULL WHERE assistant=? AND epoch=? AND state='checking'",(uid(),identity,epoch))
    return {'ok':True}

SETUP_ERRORS={
 'invalid-key':'That key format is not valid. Copy the bot API token from BotFather into the secure form.',
 'telegram-check':'Telegram could not verify this key. Check that it is current and try the secure form again.',
 'webhook':'This bot is connected to another service. Disconnect it there before connecting it here.',
 'owner-bridge':'Your personal Möbius bot cannot be used as a specialist bot.',
 'owner-check':'Could not verify your paired owner account. Check the personal Telegram Bridge connection.',
 'save-failed':'Connection setup did not finish. Any saved key remains encrypted; no replies were enabled. Retry the secure form.',
 'duplicate-active':'This Telegram bot is already connected to another Studio bot. Manage that entry instead.',
 'duplicate-deleted':'This Telegram bot belongs to a deleted Studio entry. Restore the original entry below, or ask Möbius to move its connection here. Old data stays separate.',
}

def setup_error(root,identity,body):
    code=body.get('code')
    if code not in SETUP_ERRORS:raise Problem('Unknown setup outcome.')
    with root.db:root.db.execute("UPDATE telegram_connections SET error=? WHERE assistant=? AND epoch=? AND state IN ('reserved','checking')",(SETUP_ERRORS[code],identity,body.get('epoch')))
    return {'ok':True}


def prepare_identity(root,identity,body):
    # Reserve the Telegram identity BEFORE saving a credential, including
    # deleted entries. SQLite uniqueness remains the final authority.
    catalog=Catalog(root);schema(root)
    if identity not in catalog.ids():raise Problem('Bot not found.',404)
    bot_id=body.get('bot_id');owner=body.get('owner_user_id');username=body.get('username','')
    if type(bot_id) is not int or bot_id<=0 or type(owner) is not int or owner<=0 or not re.fullmatch(r'[A-Za-z0-9_]{5,32}',username):raise Problem('Invalid bot identity.')
    with catalog.open(identity) as selected:
        with root.db:
            root.db.execute('BEGIN IMMEDIATE')
            row=root.db.execute('SELECT * FROM telegram_connections WHERE assistant=?',(identity,)).fetchone()
            if not row or row['state']!='reserved' or row['epoch']!=body.get('epoch'):raise Problem('This setup changed. Reopen it.',409)
            other=root.db.execute('SELECT * FROM telegram_connections WHERE bot_id=? AND assistant!=?',(bot_id,identity)).fetchone()
            if other:
                from bot_lifecycle import deleted
                retained=other['state']=='deleted' and deleted(root,other['assistant'])
                info={}
                if retained:
                    from telegram_reconnect import _plan,FIELDS
                    plan=_plan(root,identity,other['assistant'],selected)
                    info={'reconnect':{k:plan[k] for k in (*FIELDS,'source_name')}}
                code='duplicate-deleted' if retained else 'duplicate-active'
                root.db.execute('UPDATE telegram_connections SET error=?,setup=? WHERE assistant=?',(SETUP_ERRORS[code],json.dumps(info),identity))
                return {'status':'blocked','code':code}
            root.db.execute("UPDATE telegram_connections SET state='checking',bot_id=?,username=?,owner_user_id=?,error=NULL,setup='{}' WHERE assistant=?",(bot_id,username,owner,identity))
    return {'status':'ready'}

def verified(root,identity,body):
    row=connection(root,identity)
    if not row or row['state']!='checking' or body.get('epoch')!=row['epoch']:raise Problem('This setup step has changed. Reopen it.',409)
    bot_id=body.get('bot_id');owner=body.get('owner_user_id');username=body.get('username','')
    if not isinstance(bot_id,int) or bot_id<=0 or not isinstance(owner,int) or owner<=0 or not re.fullmatch(r'[A-Za-z0-9_]{5,32}',username):raise Problem('Invalid bot identity.')
    with root.db:
        if root.db.execute('SELECT 1 FROM telegram_connections WHERE bot_id=? AND assistant!=?',(bot_id,identity)).fetchone():raise Problem('This Telegram bot is already connected to another assistant.',409)
        root.db.execute("UPDATE telegram_connections SET bot_id=?,username=?,owner_user_id=?,state='verified',error=NULL WHERE assistant=? AND epoch=? AND state='checking'",(bot_id,username,owner,identity,row['epoch']))
    return connection(root,identity)

def activate(root,identity,body):
    row=connection(root,identity)
    if not row or row['state'] not in ('verified','paused','active') or body.get('epoch')!=row['epoch']:raise Problem('Check this bot connection before activating it.',409)
    with Catalog(root).open(identity) as store:
        store.definition()  # A reviewed local release must exist first.
        schema(store)
    with root.db:
        n=root.db.execute("UPDATE telegram_connections SET state='active',error=NULL WHERE assistant=? AND epoch=? AND state IN ('verified','paused','active')",(identity,row['epoch'])).rowcount
    if not n:raise Problem('This bot connection changed. Review it again.',409)
    return public_state(root,identity)

def pause(root,identity):
    if not connection(root,identity):raise Problem('Bot is not connected.',409)
    with root.db:root.db.execute("UPDATE telegram_connections SET state='paused' WHERE assistant=? AND state!='deleted'",(identity,))
    with Catalog(root).open(identity) as store:
        schema(store)
        from private_testing import stop
        with store.db:stop(store)
        with store.db:store.db.execute("UPDATE tasks SET status='cancelled',finished=? WHERE status IN ('queued','running') AND id IN (SELECT task_id FROM tg_requests)",(time.time(),))
    return public_state(root,identity)

def invite(store):
    schema(store);raw=secrets.token_urlsafe(24)
    with store.db:
        store.db.execute('DELETE FROM tg_invites WHERE expires<?',(time.time(),))
        store.db.execute('INSERT INTO tg_invites(hash,expires) VALUES (?,?)',(hashlib.sha256(raw.encode()).hexdigest(),time.time()+86400))
    return raw

def member(store,user_id,chat_id,owner_id,code=None,name='Guest'):
    """Existing approved members and the paired owner only. Links grant no access."""
    schema(store)
    if type(user_id) is not int or user_id<=0 or chat_id!=user_id:raise Problem('Use your private bot chat.',403)
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        row=store.db.execute('SELECT * FROM tg_members WHERE user_id=?',(user_id,)).fetchone()
        if row:
            store.active(row['participant'])
            if row['chat_id']!=chat_id:raise Problem('Use your private bot chat.',403)
            return dict(row)
        if user_id!=owner_id:raise Problem('Owner approval is required.',403)
        store.active('owner-test')
        store.db.execute('INSERT INTO tg_members(user_id,chat_id,participant,controls_version) VALUES (?,?,?,1)',(user_id,chat_id,'owner-test'))
    return dict(store.db.execute('SELECT * FROM tg_members WHERE user_id=?',(user_id,)).fetchone())


def access_user(store,user_id,owner_id):
    """Identity/access metadata only. Never select tasks, notes, sessions or counts."""
    row=store.db.execute("""SELECT m.user_id,p.name,m.username,p.created,
        CASE WHEN p.active=1 THEN 'allowed' ELSE 'blocked' END state,m.access_epoch epoch
        FROM tg_members m JOIN participants p ON p.id=m.participant WHERE m.user_id=?""",(user_id,)).fetchone()
    if not row:row=store.db.execute('SELECT user_id,name,username,created,state,epoch FROM tg_access_requests WHERE user_id=?',(user_id,)).fetchone()
    if not row:return None
    value=dict(row);value['owner']=user_id==owner_id
    value['review']=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()[:24]
    del value['epoch']
    return value


def access_list(root,identity):
    conn=connection(root,identity)
    with Catalog(root).open(identity) as store:
        schema(store)
        ids=[r[0] for r in store.db.execute('SELECT user_id FROM tg_members UNION SELECT user_id FROM tg_access_requests')]
        users=[access_user(store,i,(conn or {}).get('owner_user_id')) for i in ids]
    order={'pending':0,'allowed':1,'blocked':2}
    users.sort(key=lambda u:(order[u['state']],-u['created'],u['user_id']))
    return {'users':users,'policy':'owner-approval','epoch':(conn or {}).get('epoch'),
            'generation':__import__('bot_lifecycle').generation(root,identity)}


def request_access(store,user,owner_id):
    """Called only after the private Telegram sender/chat check and an explicit tap."""
    schema(store);identity=user['id']
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        existing=access_user(store,identity,owner_id)
        if existing:return existing['state']
        name=str(user.get('first_name') or 'Telegram user')[:80]
        username=str(user.get('username') or '')[:32]
        store.db.execute('INSERT INTO tg_access_requests(user_id,name,username,created,epoch) VALUES (?,?,?,?,?)',
                         (identity,name,username,time.time(),uid()))
    from events import changed
    changed()
    return 'pending'


def change_access(root,identity,body,*,authorize=None):
    """Exact-person, exact-state owner confirmation; retain all participant content."""
    from contextlib import nullcontext
    from bot_lifecycle import generation
    catalog=Catalog(root);conn=connection(root,identity)
    user_id=body.get('user_id');operation=body.get('operation')
    if type(user_id) is not int or user_id<=0 or type(body.get('generation')) is not int or operation not in ('allow','block'):raise Problem('Review a user and an access action first.')
    with catalog.open(identity) as store:
        schema(store)
        with root.db:
            root.db.execute('BEGIN IMMEDIATE')
            if authorize is not None:authorize()
            conn=root.db.execute('SELECT * FROM telegram_connections WHERE assistant=?',(identity,)).fetchone()
            if not conn or conn['epoch']!=body.get('epoch') or generation(root,identity)!=body.get('generation'):
                raise Problem('The bot connection changed. Refresh Users and review again.',409)
            if user_id==conn['owner_user_id']:raise Problem('The paired owner cannot be removed here.',403)
            with store.db if store is not root else nullcontext():
                if store is not root:store.db.execute('BEGIN IMMEDIATE')
                row=access_user(store,user_id,conn['owner_user_id'])
                if not row or row['review']!=body.get('review'):raise Problem('This user’s access changed. Refresh Users and review again.',409)
                if operation=='allow' and conn['state']!='active':raise Problem('Publish or resume this bot before allowing users.',409)
                if operation=='allow' and row['state']=='allowed' or operation=='block' and row['state']=='blocked':
                    raise Problem('This access change is already applied.',409)
                membership=store.db.execute('SELECT participant FROM tg_members WHERE user_id=?',(user_id,)).fetchone()
                participant=membership[0] if membership else None
                epoch=uid();now=time.time()
                # Clear queued traffic from the previous permission period. Already
                # dispatched external operations can finish; nothing is replayed on allow.
                store.db.execute("UPDATE tg_deliveries SET status='blocked' WHERE chat_id=? AND status IN ('pending','sending')",(user_id,))
                for update in store.db.execute("SELECT id,body FROM tg_updates WHERE status='pending'").fetchall():
                    data=json.loads(update['body']);msg=data.get('message') or (data.get('callback_query') or {}).get('message') or {}
                    if (msg.get('chat') or {}).get('id')==user_id:
                        store.db.execute("UPDATE tg_updates SET status='done',body='{}' WHERE id=?",(update['id'],))
                if participant:
                    store.db.execute('UPDATE participants SET active=? WHERE id=?',(int(operation=='allow'),participant))
                    store.db.execute('DELETE FROM sessions WHERE participant=?',(participant,))
                    store.db.execute("UPDATE tasks SET status='cancelled',finished=? WHERE participant=? AND status IN ('queued','running')",(now,participant))
                    store.db.execute('UPDATE tg_requests SET delivered=1 WHERE task_id IN (SELECT id FROM tasks WHERE participant=?)',(participant,))
                    store.db.execute("UPDATE tg_members SET mode=NULL,selection='[]',access_epoch=?,access_since=? WHERE user_id=?",(epoch,now,user_id))
                elif operation=='allow':
                    participant=uid()
                    store.db.execute('INSERT INTO participants(id,name,created) VALUES (?,?,?)',(participant,row['name'],now))
                    store.db.execute('INSERT INTO tg_members(user_id,chat_id,participant,controls_version,username,access_epoch,access_since) VALUES (?,?,?,1,?,?,?)',
                                     (user_id,user_id,participant,row['username'],epoch,now))
                    store.db.execute('DELETE FROM tg_access_requests WHERE user_id=?',(user_id,))
                else:
                    store.db.execute("UPDATE tg_access_requests SET state='blocked',epoch=? WHERE user_id=?",(epoch,user_id))
                if operation=='allow':
                    _insert_reply(store,'access:'+epoch,user_id,'Your access is approved. Send /start to begin. Your conversation is separate from other users.')
    return {'ok':True}


def queue_delivery(store,key,chat_id,body,method='sendMessage'):
    with store.db:
        store.db.execute('INSERT OR IGNORE INTO tg_deliveries(id,chat_id,body,created,method) VALUES (?,?,?,?,?)',(key,chat_id,json.dumps(body),time.time(),method))

def _insert_reply(store,key,chat_id,text,markup=None):
    """Insert within the caller's transaction (no nested context commits)."""
    chunks=[text[i:i+3500] for i in range(0,len(text),3500)] or ['Done.']
    for i,chunk in enumerate(chunks):
        body={'chat_id':chat_id,'text':chunk}
        if i==len(chunks)-1 and markup is not None:body['reply_markup']=markup
        store.db.execute('INSERT OR IGNORE INTO tg_deliveries(id,chat_id,body,created,method) VALUES (?,?,?,?,?)',
                         (f'{key}:{i}',chat_id,json.dumps(body),time.time(),'sendMessage'))

def reply(store,key,chat_id,text,markup=None):
    # Retain the durable receipt even when cached contents are later purged.
    with store.db: _insert_reply(store,key,chat_id,text,markup)


def purge_cached_content(store,participant):
    """Called only inside confirm_delete's atomic transaction."""
    member=store.db.execute('SELECT chat_id FROM tg_members WHERE participant=?',(participant,)).fetchone()
    if not member:return
    chat_id=member[0]
    store.db.execute("UPDATE tg_members SET mode=NULL,selection='[]' WHERE participant=?",(participant,))
    store.db.execute("UPDATE tg_inputs SET text='This saved preview was cleared. Open Notes again.' WHERE participant=?",(participant,))
    store.db.execute('UPDATE tg_requests SET delivered=1 WHERE task_id IN (SELECT id FROM tasks WHERE participant=?)',(participant,))
    store.db.execute("UPDATE tg_deliveries SET body='{}',status=CASE WHEN status IN ('pending','sending') THEN 'blocked' ELSE status END WHERE chat_id=? AND method!='answerCallbackQuery'",(chat_id,))
    # Callback updates include a copy of the bot's old message. Strip that
    # cached content but retain routing and the user's actual button action.
    for row in store.db.execute("SELECT id,body FROM tg_updates WHERE status='pending'").fetchall():
        update=json.loads(row['body']);callback=update.get('callback_query') or {}
        message=callback.get('message') or {}
        if (message.get('chat') or {}).get('id')==chat_id:
            callback['message']={k:message[k] for k in ('chat','message_id') if k in message}
            store.db.execute('UPDATE tg_updates SET body=? WHERE id=?',(json.dumps(update),row['id']))


def ingest(store,updates,connection=None):
    schema(store)
    valid=[u for u in updates if isinstance(u,dict) and isinstance(u.get('update_id'),int)]
    with store.db:
        for update in valid:
            from private_testing import route
            destination=route(store,update,connection) if connection else 'live'
            store.db.execute('INSERT OR IGNORE INTO tg_updates(id,body,destination) VALUES (?,?,?)',(update['update_id'],json.dumps(update),destination))
        if valid:
            # Checkpoint the batch actually returned by this serial poller.
            # Bot API queues can restart at lower IDs (e.g. local transport);
            # an all-time maximum then causes endless queue-head replays.
            # Durable INSERT OR IGNORE receipts still prevent duplicate work.
            offset=max(u['update_id'] for u in valid)+1
            store.db.execute('INSERT OR REPLACE INTO config VALUES (?,?)',('telegram_offset',json.dumps(offset)))

def wants_answer(text,command,mode):
    # Explicit commands win; a bare shared link is always capture. URL query
    # punctuation is not natural-language intent (e.g. YouTube watch?v=...).
    if command=='/ask':return True
    if command=='/save':return False
    if re.fullmatch(r'https?://[^\s<>]+',text,re.I):return False
    if mode in ('save','ask'):return mode=='ask'
    prose=re.sub(r'https?://[^\s<>]+','',text,flags=re.I).strip()
    return '?' in prose or bool(re.match(r'^(what|where|when|which|how|find|summari[sz]e|recall)\b',prose,re.I))

def handle_update(store,update,owner_id):
    if store.config('bot_deleted',False):return
    mid=update['update_id'];callback=update.get('callback_query')
    msg=(callback.get('message') if callback else update.get('message')) or {}
    chat=msg.get('chat') or {};user=(callback.get('from') if callback else msg.get('from')) or {}
    if chat.get('type')!='private' or not isinstance(user.get('id'),int) or user.get('is_bot'):return
    chat_id=chat.get('id');user_id=user['id']
    if not isinstance(chat_id,int) or chat_id!=user_id:return
    if callback and callback.get('data')=='access:request' and isinstance(callback.get('id'),str):
        queue_delivery(store,f'cb{mid}',chat_id,{'callback_query_id':callback['id']},'answerCallbackQuery')
        state=request_access(store,user,owner_id)
        message={'pending':'Request sent. The bot owner must approve your account before you can use this bot. You’ll receive a short message if approved.',
                 'allowed':'Your access is already approved. Send /start to begin.',
                 'blocked':'Access has not been approved. Contact the bot owner.'}[state]
        reply(store,f'u{mid}',chat_id,message);return
    from bot_behaviors import behavior
    impl=behavior(store.definition())
    if callback:
        text={'pn:save':'Save note','pn:ask':'Ask notes','pn:digest':'/notes'}.get(callback.get('data'))
        if not isinstance(callback.get('id'),str):return
        queue_delivery(store,f'cb{mid}',chat_id,{'callback_query_id':callback['id']},'answerCallbackQuery')
        if impl:text=''
        elif text is None:
            from telegram_notes import ACTIONS
            if str(callback.get('data',''))[3:].split(':')[0] not in ACTIONS or not str(callback.get('data','')).startswith('pn:'):return
            text='/notes'
    else:text=str(msg.get('text') or '').strip()
    parts=text.split(maxsplit=1);command=parts[0].split('@')[0].lower() if parts else ''
    code=parts[1] if command=='/start' and len(parts)==2 else None
    try:m=member(store,user_id,chat_id,owner_id,code,user.get('first_name') or 'Guest')
    except Problem:
        # No unauthorized note/model access, including for revoked participants.
        status=access_user(store,user_id,owner_id)
        if status:
            reply(store,f'u{mid}',chat_id,'Your access request is waiting for the owner’s approval.' if status['state']=='pending' else 'Access is not approved. Contact the bot owner.')
        else:
            reply(store,f'u{mid}',chat_id,'This is a private pilot. Request access to share your Telegram name, username and account ID with the bot owner. You cannot use the assistant until approved.',
                  {'inline_keyboard':[[{'text':'Request access','callback_data':'access:request'}]]})
        return
    # Do not import messages sent before this access period into a newly
    # approved/re-enabled conversation (Telegram timestamps have second precision).
    if type(msg.get('date')) in (int,float) and msg['date']<int(m['access_since']):return
    username=str(user.get('username') or '')[:32]
    if username!=m['username']:
        with store.db:store.db.execute('UPDATE tg_members SET username=? WHERE user_id=?',(username,user_id))
    participant=m['participant'];key=f'telegram:{mid}'
    if impl:
        impl.handle(store,participant,update,msg,callback,text,command)
        return
    import telegram_notebook as notebook
    if not callback:notebook.track_input(store,participant,msg)
    if store.db.execute('SELECT 1 FROM note_tombstones WHERE participant=? AND request_id=?',(participant,key)).fetchone():
        reply(store,f'u{mid}',chat_id,'This note was permanently deleted. Send a new message to save it again.');return
    if not m['controls_version']:
        # On the next owner/user interaction, retire the old replacement
        # keyboard once. New controls belong to messages, not the input area.
        with store.db:
            queue_delivery(store,f'controls:{user_id}',chat_id,{'chat_id':chat_id,'text':'Controls now sit below my messages, leaving your typing keyboard free.','reply_markup':{'remove_keyboard':True}})
            store.db.execute('UPDATE tg_members SET controls_version=1 WHERE user_id=?',(user_id,))
    if command in ('/ask','/save') and len(parts)==1:text='Ask notes' if command=='/ask' else 'Save note'
    if command in ('/digest','/settings','/privacy','/cancel') or text=='My digest':
        note_action='digest' if command=='/digest' or text=='My digest' else 'prefs' if command=='/settings' else 'privacy' if command=='/privacy' else 'n:0'
        command='/notes'
    else:note_action='n:0'
    if command=='/notes':
        from telegram_notes import render
        try:
            note_text,rows=render(store,participant,callback['data'][3:] if callback else note_action,request_id=key,message_id=msg.get('message_id') if callback else None)
            # Browsing replaces its own message rather than filling the chat.
            markup={'inline_keyboard':rows}
            notebook.panel(store,f'u{mid}:notes',participant,note_text,markup,target=msg.get('message_id') if callback else None)
        except Problem as exc:reply(store,f'u{mid}',chat_id,str(exc))
        return
    if command in ('/start','/help') or text.lower() in ('hi','hello','hey'):
        with store.db:store.db.execute('UPDATE tg_members SET mode=NULL WHERE user_id=?',(user_id,))
        from telegram_onboarding import welcome, help_text, has_saved_before
        name=store.definition()['name']
        introduction=help_text(name) if command=='/help' else welcome(name,has_saved_before(store,participant))
        reply(store,f'u{mid}',chat_id,introduction);return
    if not text:
        reply(store,f'u{mid}',chat_id,'I can save text and links for now. Send a short note or a link.');return
    receipt=store.db.execute('SELECT text FROM tg_inputs WHERE update_id=? AND participant=?',(mid,participant)).fetchone()
    if receipt:
        reply(store,f'u{mid}',chat_id,receipt[0]);return
    if not command.startswith('/') and m['mode'] and m['mode'].split(':')[0] in ('search','timezone','themeset'):
        from note_views import preferences
        from telegram_notes import render
        try:
            action,_,identity=m['mode'].partition(':')
            if action=='search':preferences(store,participant,{'query':text[:200]});result='Search updated. Open Notes to see the results.'
            elif action=='timezone':preferences(store,participant,{'timezone':text});result='Dates now use '+text+'.'
            else:
                store.tag(participant,{'id':identity,'tags':[] if text=='-' else text.split(',')});result='Themes updated.'
            with store.db:
                store.db.execute('INSERT OR IGNORE INTO tg_inputs VALUES (?,?,?)',(mid,participant,result))
                store.db.execute('UPDATE tg_members SET mode=NULL WHERE user_id=?',(user_id,))
            if action=='search':
                result,rows=render(store,participant,'n:0')
                # Save the exact text for crash replay; the usual home controls remain usable.
                with store.db:store.db.execute('UPDATE tg_inputs SET text=? WHERE update_id=?',(result,mid))
                reply(store,f'u{mid}',chat_id,result,{'inline_keyboard':rows})
            else:reply(store,f'u{mid}',chat_id,result)
        except Problem as exc:reply(store,f'u{mid}',chat_id,str(exc),{'inline_keyboard':[[{'text':'Cancel','callback_data':'pn:n:0'}]]})
        return
    if text in ('Save note','Ask notes'):
        with store.db:store.db.execute('UPDATE tg_members SET mode=? WHERE user_id=?',('save' if text=='Save note' else 'ask',user_id))
        prompt='Save mode · next message\nSend the note or link to save. Then normal capture resumes.' if text=='Save note' else 'Ask mode · next message\nWhat would you like to find in your notes? After your question, normal capture resumes. A bare link still saves.'
        notebook.panel(store,f'u{mid}:prompt',participant,prompt,{'inline_keyboard':[[{'text':'Cancel','callback_data':'pn:n:0'}]]},target=msg.get('message_id') if callback else None);return
    if command=='/latest':
        latest=store.db.execute("SELECT output,sources FROM tasks WHERE participant=? AND status IN ('done','error') ORDER BY created DESC LIMIT 1",(participant,)).fetchone()
        from note_management import sources_available
        reply(store,f'u{mid}',chat_id,latest[0] if latest and sources_available(store,participant,json.loads(latest[1])) else 'No current answer. Ask a question about your notes.');return
    if command.startswith('/') and command not in ('/save','/ask'):
        reply(store,f'u{mid}',chat_id,'Unknown command. Use the Telegram menu: Notes, Ask, Digest or Settings. Nothing was saved.');return
    # Replay must preserve the earlier interpretation even after its one-shot
    # Ask/Save mode was cleared. Domain request IDs are the durable receipt.
    prior=store.db.execute('SELECT id FROM tasks WHERE participant=? AND request_id=?',(participant,key)).fetchone()
    saved=store.db.execute('SELECT * FROM notes WHERE participant=? AND request_id=?',(participant,key)).fetchone()
    if prior or saved:
        if prior:
            with store.db:store.db.execute('INSERT OR IGNORE INTO tg_requests(task_id,chat_id) VALUES (?,?)',(prior[0],chat_id))
        if prior:reply(store,f'u{mid}',chat_id,'Looking through your notes…')
        else:
            from telegram_onboarding import saved_text
            confirmation=saved_text(store,participant,saved)
            if not notebook.saved(store,f'u{mid}',participant,dict(saved),msg,confirmation):
                reply(store,f'u{mid}',chat_id,confirmation)
        return
    kind='digest' if text=='My digest' or command=='/digest' else 'ask'
    asking=kind=='digest' or wants_answer(text,command,m['mode'])
    if command in ('/save','/ask'):text=parts[1] if len(parts)==2 else ''
    if kind=='digest':text='Give me a digest of my notes from the last seven days.'
    try:
        if asking:
            task=store.enqueue(participant,{'text':text,'kind':kind,'request_id':key})
            with store.db:store.db.execute('INSERT OR IGNORE INTO tg_requests(task_id,chat_id) VALUES (?,?)',(task['id'],chat_id))
            reply(store,f'u{mid}',chat_id,'Looking through your notes…')
        else:
            note=store.note(participant,{'text':text,'request_id':key})
            from telegram_onboarding import saved_text
            confirmation=saved_text(store,participant,note)
            if not notebook.saved(store,f'u{mid}',participant,note,msg,confirmation):
                reply(store,f'u{mid}',chat_id,confirmation)
        with store.db:store.db.execute('UPDATE tg_members SET mode=NULL WHERE user_id=?',(user_id,))
    except Problem as exc:reply(store,f'u{mid}',chat_id,str(exc))

def consume(store,owner_id):
    rows=store.db.execute("SELECT * FROM tg_updates WHERE status='pending' ORDER BY id LIMIT 20").fetchall()
    for row in rows:
        handle_update(store,json.loads(row['body']),owner_id)
        with store.db:store.db.execute("UPDATE tg_updates SET status='done',body='{}' WHERE id=?",(row['id'],))
    return bool(rows)

def collect_answers(store):
    # Re-read and enqueue under one writer transaction so a purge cannot be
    # followed by an old in-memory answer being copied back into the outbox.
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        rows=store.db.execute("SELECT r.*,t.output,t.sources,t.status,t.participant,t.version FROM tg_requests r JOIN tasks t ON t.id=r.task_id WHERE r.delivered=0 AND t.status IN ('done','error','cancelled')").fetchall()
        for row in rows:
            try:store.active(row['participant'])
            except Problem:
                store.db.execute('UPDATE tg_requests SET delivered=1 WHERE task_id=?',(row['task_id'],))
                continue
            from bot_behaviors import behavior
            impl=behavior(store.definition(row['version']))
            collect=getattr(impl,'collect_answer',None)
            if collect and collect(store,row):
                store.db.execute('UPDATE tg_requests SET delivered=1 WHERE task_id=?',(row['task_id'],))
                continue
            text=row['output'] or 'Stopped. Your saved notes are unchanged.'
            sources=json.loads(row['sources'])
            if sources:text+='\n\n'+ '\n'.join(f"[{i+1}] {s['title'][:180]}"+(f" · Saved {s['saved_local']}" if s.get('saved_local') else '')+(f"\n{s['url']}" if s.get('url') else '') for i,s in enumerate(sources))
            _insert_reply(store,'t'+row['task_id'],row['chat_id'],text)
            store.db.execute('UPDATE tg_requests SET delivered=1 WHERE task_id=?',(row['task_id'],))
