"""Owner-only test deployments, separate from immutable published releases.

Each changed test snapshot gets a fresh isolated store. Previous test stores are
retained but quiesced; no data is copied from or into a real participant store.
Ingress pins the destination before acknowledging Telegram's update offset.
"""
import json
import re
import time
from contextlib import contextmanager, nullcontext
from assistant_store import Store, Problem, uid


def put(store,key,value):
    store.db.execute('INSERT OR REPLACE INTO config VALUES (?,?)',(key,json.dumps(value)))


def state(store):
    value=store.config('private_test',{})
    return {**value,'current':bool(value.get('enabled') and value.get('revision')==store.draft_revision())}


@contextmanager
def open_test(store,epoch=None):
    epoch=epoch or state(store).get('epoch')
    if not isinstance(epoch,str) or not re.fullmatch(r'[a-f0-9]{12}',epoch):raise Problem('No private test is available.',409)
    test=Store(store.root/'private-tests'/epoch)
    try:yield test
    finally:test.db.close()


def stop(store):
    """Caller holds the live writer lock; fail closed before changing routing."""
    import telegram_store as tg
    from bot_lifecycle import quiesce
    value=state(store)
    if value.get('epoch'):
        with open_test(store,value['epoch']) as test:
            tg.schema(test)
            with test.db:quiesce(test,True)
    put(store,'private_test',{**value,'enabled':False})


def quiesce_owner(live,owner):
    """Do not carry queued published owner work across a private-mode switch."""
    live.db.execute("UPDATE tasks SET status='cancelled',finished=? WHERE status IN ('queued','running') AND participant='owner-test' AND id IN (SELECT task_id FROM tg_requests)",(time.time(),))
    live.db.execute("UPDATE tg_requests SET delivered=1 WHERE chat_id=?",(owner,))
    live.db.execute("UPDATE tg_deliveries SET status='blocked' WHERE chat_id=? AND status IN ('pending','sending')",(owner,))


def change(catalog,identity,body,operation):
    import telegram_store as tg
    from bot_lifecycle import generation
    root=catalog.root
    with catalog.open(identity) as live:
        tg.schema(root);tg.schema(live)
        with root.db:
            root.db.execute('BEGIN IMMEDIATE')
            with live.db if live is not root else nullcontext():
                if live is not root:live.db.execute('BEGIN IMMEDIATE')
                conn=root.db.execute('SELECT * FROM telegram_connections WHERE assistant=?',(identity,)).fetchone()
                if not conn or conn['state'] not in ('verified','paused','active','testing'):
                    raise Problem('Connect Telegram first.',409)
                if body.get('epoch')!=conn['epoch'] or body.get('generation')!=generation(root,identity):
                    raise Problem('The connection changed. Review this step again.',409)
                live.check_revision(body.get('revision'))
                if not isinstance(body.get('revision'),str):raise Problem('Review the draft first.',409)
                old=state(live)
                if body.get('test_epoch')!=old.get('epoch'):
                    raise Problem('Your test changed. Refresh Studio and review again.',409)
                if operation=='test':
                    if old.get('current') and conn['state'] in ('testing','active'):
                        return old
                    stop(live)
                    quiesce_owner(live,conn['owner_user_id'])
                    epoch=uid()[:12]
                    with open_test(live,epoch) as test:
                        tg.schema(test)
                        definition=live.config('draft')
                        with test.db:
                            put(test,'draft',definition)
                            test.db.execute('INSERT INTO releases VALUES (1,?,?)',(json.dumps(definition),time.time()))
                            put(test,'deployed',1)
                            put(test,'test_identity',{'epoch':epoch,'owner':conn['owner_user_id']})
                        value={'enabled':True,'epoch':epoch,'revision':live.draft_revision(),'created':time.time()}
                        put(live,'private_test',value)
                    # Testing never resumes guest traffic on a paused bot.
                    root.db.execute("UPDATE telegram_connections SET state=CASE WHEN state='active' THEN state ELSE 'testing' END,error=NULL WHERE assistant=?",(identity,))
                elif operation=='stop-test':
                    stop(live)
                    root.db.execute("UPDATE telegram_connections SET state=CASE WHEN state='testing' THEN 'paused' ELSE state END WHERE assistant=?",(identity,))
                elif operation=='release':
                    if not old.get('current'):raise Problem('Load the latest draft with Try changes before publishing.',409)
                    if live.config('draft').get('runtime')=='blank-v1':raise Problem('Customize this blank bot before publishing.',409)
                    from bot_behaviors import require_publishable
                    require_publishable(live.config('draft'))
                    # Publish exactly the tested definition without nested transaction commits.
                    draft=live.config('draft')
                    if not live.config('deployed') or live.definition()!=draft:
                        version=live.db.execute('SELECT coalesce(max(version),0)+1 FROM releases').fetchone()[0]
                        live.db.execute('INSERT INTO releases VALUES (?,?,?)',(version,json.dumps(draft),time.time()))
                        put(live,'deployed',version)
                    # Owner returns to the live conversation; test history is not promoted.
                    stop(live)
                    root.db.execute("UPDATE telegram_connections SET state='active',error=NULL WHERE assistant=?",(identity,))
                else:raise Problem('Unknown development action.',404)
    return {'ok':True}


def private_user(update):
    callback=update.get('callback_query') or {}
    msg=(callback.get('message') if callback else update.get('message')) or {}
    user=(callback.get('from') if callback else msg.get('from')) or {}
    chat=msg.get('chat') or {}
    if chat.get('type')=='private' and type(user.get('id')) is int and user['id']==chat.get('id') and not user.get('is_bot'):
        return user['id']


def route(live,update,conn):
    preview=state(live);user=private_user(update)
    data=(update.get('callback_query') or {}).get('data','')
    if user==conn['owner_user_id'] and preview.get('enabled'):
        message=update.get('message') or {}
        # A newly connected/reassigned bot can have older queued messages. A
        # fresh private snapshot must not import them into its test conversation.
        stamp=message.get('date')
        if type(stamp) in (int,float) and stamp<int(preview['created']):return 'discard'
        if data and not data.startswith('p:'+preview['epoch']+':'):return 'discard'
        return preview['epoch']
    if data.startswith('p:'):return 'discard'
    return 'live' if conn['state']=='active' else 'discard'


def consume(live,conn):
    import telegram_store as tg
    rows=live.db.execute("SELECT * FROM tg_updates WHERE status='pending' ORDER BY id LIMIT 20").fetchall()
    for row in rows:
        target=row['destination'];update=json.loads(row['body'])
        current=state(live)
        handled=False
        if target=='live' and conn['state']=='active':
            # Never replay a previously queued owner message into a different mode.
            if not (current.get('enabled') and private_user(update)==conn['owner_user_id']):
                tg.handle_update(live,update,conn['owner_user_id']);handled=True
        elif target==current.get('epoch') and current.get('enabled') and private_user(update)==conn['owner_user_id']:
            with open_test(live,target) as test:
                tg.schema(test)
                if update.get('callback_query'):
                    update['callback_query']['data']=update['callback_query']['data'].removeprefix('p:'+target+':')
                tg.ingest(test,[update]);tg.consume(test,conn['owner_user_id'],metrics_store=live,metrics_mode='test');handled=True
        if not handled and update.get('callback_query') and private_user(update)==conn['owner_user_id']:
            # Old inline controls must neither act in another deployment nor
            # leave Telegram's spinner hanging. Fresh commands open this mode.
            body={'callback_query_id':update['callback_query'].get('id'),'text':'This control belongs to another version. Reopen from the bot menu.'}
            if current.get('enabled'):
                with open_test(live) as test:
                    tg.schema(test);tg.queue_delivery(test,'stale:'+str(row['id']),conn['owner_user_id'],body,'answerCallbackQuery')
            elif conn['state']=='active':tg.queue_delivery(live,'stale:'+str(row['id']),conn['owner_user_id'],body,'answerCallbackQuery')
        with live.db:live.db.execute("UPDATE tg_updates SET status='done',body='{}' WHERE id=?",(row['id'],))
    return bool(rows)


def payload(store,body):
    """Tag only transport payloads. Internal notebook/edit bookkeeping stays raw."""
    marker=store.config('test_identity')
    if not marker:return body
    body=json.loads(json.dumps(body))
    if isinstance(body.get('text'),str):body['text']='Private test · '+body['text']
    if isinstance(body.get('caption'),str):body['caption']='Private test · '+body['caption']
    for row in (body.get('reply_markup') or {}).get('inline_keyboard',[]):
        for button in row:
            if 'callback_data' in button:button['callback_data']='p:'+marker['epoch']+':'+button['callback_data']
    return body


def deliverable(store,live,conn,chat_id):
    marker=store.config('test_identity')
    current=state(live)
    if marker:
        return (current.get('enabled') and current.get('epoch')==marker['epoch'] and
                chat_id==marker['owner']==conn['owner_user_id'] and conn['state'] in ('active','testing'))
    return conn['state']=='active' and not (current.get('enabled') and chat_id==conn['owner_user_id'])


@contextmanager
def runtime_stores(live):
    current=state(live)
    if current.get('enabled'):
        with open_test(live,current['epoch']) as test:
            import telegram_store as tg
            tg.schema(test)
            yield [live,test]
    else:yield [live]


def owner_joined(live,owner):
    if live.db.execute('SELECT 1 FROM tg_members WHERE user_id=?',(owner,)).fetchone():return True
    if state(live).get('epoch'):
        with open_test(live) as test:
            import telegram_store as tg
            tg.schema(test)
            return bool(test.db.execute('SELECT 1 FROM tg_members WHERE user_id=?',(owner,)).fetchone())
    return False
