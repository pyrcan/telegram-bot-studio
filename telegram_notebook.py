"""Participant-owned Telegram presentation; never deletes notebook records.

Destructive calls reuse the durable Telegram outbox. IDs come only from verified
incoming updates or successful sends, never from model text or guessed ranges.
"""
import json
import time
from assistant_store import Problem, uid

MAX_AGE=48*3600
GRACE=10

def schema(store):
    store.db.executescript('''
      CREATE TABLE IF NOT EXISTS tg_message_index(
        participant TEXT NOT NULL,message_id INTEGER NOT NULL,chat_id INTEGER NOT NULL,
        sent REAL NOT NULL,kind TEXT NOT NULL,removed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(participant,message_id));
      CREATE TABLE IF NOT EXISTS tg_chat_cleanup(
        id TEXT PRIMARY KEY,participant TEXT NOT NULL,items TEXT NOT NULL,
        expires REAL NOT NULL,confirmed INTEGER NOT NULL DEFAULT 0);
    ''')

def member(store,participant):
    store.active(participant)
    row=store.db.execute('SELECT * FROM tg_members WHERE participant=?',(participant,)).fetchone()
    if not row:raise Problem('Open your private Telegram bot first.',403)
    return row

def remember(store,participant,chat,message_id,sent,kind):
    if type(message_id) is not int or message_id<=0 or not isinstance(sent,(float,int)) or sent<=0:return
    store.db.execute('INSERT OR IGNORE INTO tg_message_index(participant,message_id,chat_id,sent,kind) VALUES (?,?,?,?,?)',
                     (participant,message_id,chat,sent,kind))

def track_input(store,participant,msg):
    # Text/links only. Dice and service messages have additional Telegram rules.
    if not isinstance(msg.get('text'),str):return
    with store.db:remember(store,participant,msg['chat']['id'],msg.get('message_id'),msg.get('date'),'incoming')

def enabled(store,participant):return bool(member(store,participant)['notebook_since'])

def set_enabled(store,participant,on):
    member(store,participant)
    with store.db:
        if on:
            store.db.execute('UPDATE tg_members SET notebook_since=? WHERE participant=? AND notebook_since=0',(time.time(),participant))
        else:
            store.db.execute('UPDATE tg_members SET notebook_since=0 WHERE participant=?',(participant,))
    return enabled(store,participant)

def enqueue(store,key,chat,body,purpose,context,method='sendMessage',due=0):
    # Caller owns transaction, including receipt-triggered cleanup creation.
    store.db.execute('INSERT OR IGNORE INTO tg_deliveries(id,chat_id,body,created,method,purpose,context,retry_at) VALUES (?,?,?,?,?,?,?,?)',
      (key,chat,json.dumps(body),time.time(),method,purpose,json.dumps(context),due))

def panel(store,key,participant,text,markup,target=None):
    m=member(store,participant)
    with store.db:enqueue(store,key,m['chat_id'],{'chat_id':m['chat_id'],'text':text,'reply_markup':markup},'panel',{'participant':participant,'target':target})

def saved(store,key,participant,note,msg,text):
    m=member(store,participant)
    if not m['notebook_since'] or note['created']<m['notebook_since'] or not isinstance(msg.get('date'),int) or msg['date']<int(m['notebook_since']) or type(msg.get('message_id')) is not int:
        return False
    # Both commit the note and receive a definite Telegram Saved receipt before
    # a single delete is eligible. The original stays on any ambiguous send.
    with store.db:
        enqueue(store,key+':0',m['chat_id'],{'chat_id':m['chat_id'],'text':text+'\nNotebook mode: this capture will leave the chat after saving; find it in Notes.'},'capture',
                {'participant':participant,'note':note['id'],'capture':msg['message_id'],'epoch':m['notebook_since']})
    return True

def queue_delete(store,key,participant,message_id,context,due=0):
    m=member(store,participant)
    enqueue(store,key,m['chat_id'],{'chat_id':m['chat_id'],'message_id':message_id},'delete',{'participant':participant,**context},'deleteMessage',due)

def prepare_clean(store,participant,exclude=None):
    m=member(store,participant);now=time.time()
    items=[r[0] for r in store.db.execute('SELECT message_id FROM tg_message_index WHERE participant=? AND chat_id=? AND removed=0 AND sent>? AND sent<=? ORDER BY sent,message_id',
                                        (participant,m['chat_id'],now-MAX_AGE+30,now)) if r[0] not in (exclude,m['notebook_panel'])]
    if not items:raise Problem('No tracked recent messages to clean. Saved notes are unchanged. Older history can be cleared in Telegram itself.')
    identity=uid()
    with store.db:
        store.db.execute('DELETE FROM tg_chat_cleanup WHERE expires<? AND confirmed=0',(now,))
        store.db.execute('INSERT INTO tg_chat_cleanup(id,participant,items,expires) VALUES (?,?,?,?)',(identity,participant,json.dumps(items),now+600))
    return identity,len(items)

def confirm_clean(store,participant,identity):
    member(store,participant)
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        row=store.db.execute('SELECT * FROM tg_chat_cleanup WHERE id=? AND participant=?',(identity,participant)).fetchone()
        if not row or row['expires']<time.time():raise Problem('This cleanup confirmation expired or was cancelled. Review again.',409)
        if not row['confirmed']:
            for message_id in json.loads(row['items']):queue_delete(store,'clean:'+identity+':'+str(message_id),participant,message_id,{'review':identity})
            store.db.execute('UPDATE tg_chat_cleanup SET confirmed=1 WHERE id=?',(identity,))
    return clean_status(store,participant,identity)

def cancel_clean(store,participant,identity):
    member(store,participant)
    with store.db:store.db.execute('DELETE FROM tg_chat_cleanup WHERE id=? AND participant=? AND confirmed=0',(identity,participant))

def clean_status(store,participant,identity):
    member(store,participant)
    row=store.db.execute('SELECT * FROM tg_chat_cleanup WHERE id=? AND participant=? AND confirmed=1',(identity,participant)).fetchone()
    if not row:raise Problem('Cleanup not found.',404)
    counts={r[0]:r[1] for r in store.db.execute('SELECT status,count(*) FROM tg_deliveries WHERE id LIKE ? GROUP BY status',('clean:'+identity+':%',))}
    return f"Chat cleanup · {len(json.loads(row['items']))} messages\nRemoved: {counts.get('sent',0)}\nWaiting: {counts.get('pending',0)+counts.get('sending',0)}\nNot removed: {counts.get('failed',0)+counts.get('blocked',0)}\nUnconfirmed: {counts.get('uncertain',0)}\n\nSaved notes and stored answers are unchanged. Telegram message deletion cannot be undone."

def prepare_delivery(store,row):
    """Under the transport claim transaction. Recheck consent at dispatch."""
    row=dict(row);purpose=row['purpose'];context=json.loads(row['context']);body=json.loads(row['body'])
    if not purpose:return row
    participant=context['participant'];m=member(store,participant)
    if m['chat_id']!=row['chat_id']:return None
    if purpose=='panel':
        if store.db.execute("SELECT 1 FROM tg_deliveries WHERE chat_id=? AND purpose='panel' AND status='sending'",(m['chat_id'],)).fetchone():return 'wait'
        # Never jump ahead of an earlier panel revision (including rate limits).
        earlier=store.db.execute("SELECT id FROM tg_deliveries WHERE chat_id=? AND purpose='panel' AND status='pending' ORDER BY created,id LIMIT 1",(m['chat_id'],)).fetchone()
        if earlier and earlier[0]!=row['id']:return 'wait'
        # Explicit commands start a fresh visible view. A callback owns only the
        # message that was tapped; background capture may reuse the last panel.
        target=context.get('target') if 'target' in context else m['notebook_panel']
        row['method']='editMessageText' if target else 'sendMessage'
        body.pop('message_id',None)
        if target:body['message_id']=target
    elif purpose=='delete':
        record=store.db.execute('SELECT * FROM tg_message_index WHERE participant=? AND message_id=? AND chat_id=?',
                               (participant,body.get('message_id'),m['chat_id'])).fetchone()
        if not record or record['removed'] or not 0<=time.time()-record['sent']<MAX_AGE-5 or body['message_id']==m['notebook_panel']:return None
        if context.get('auto'):
            if not m['notebook_since'] or m['notebook_since']!=context['epoch']:return None
            if not store.db.execute('SELECT 1 FROM notes WHERE id=? AND participant=?',(context['note'],participant)).fetchone():return None
            # Keep the original if no notebook panel was successfully displayed.
            if not m['notebook_panel']:
                pending=store.db.execute("SELECT 1 FROM tg_deliveries WHERE chat_id=? AND purpose='panel' AND status IN ('pending','sending')",(m['chat_id'],)).fetchone()
                return 'wait' if pending else None
        else:
            review=store.db.execute('SELECT items FROM tg_chat_cleanup WHERE id=? AND participant=? AND confirmed=1',(context.get('review'),participant)).fetchone()
            if not review or body['message_id'] not in json.loads(review[0]):return None
    row['body']=json.dumps(body)
    store.db.execute('UPDATE tg_deliveries SET method=?,body=? WHERE id=?',(row['method'],row['body'],row['id']))
    return row

def delivered(store,row,result):
    """In the same transaction as marking delivery sent; no lost side effects."""
    context=json.loads(row['context']);purpose=row['purpose'];body=json.loads(row['body'])
    m=store.db.execute('SELECT * FROM tg_members WHERE chat_id=?',(row['chat_id'],)).fetchone()
    if not m:return
    participant=m['participant'];now=time.time()
    message_id=(result.get('message_id') if isinstance(result,dict) else None) or body.get('message_id')
    if row['method']=='sendMessage' and isinstance(result,dict):
        remember(store,participant,row['chat_id'],message_id,result.get('date',now),purpose or 'reply')
    if purpose=='panel' and message_id:
        store.db.execute('UPDATE tg_members SET notebook_panel=? WHERE participant=?',(message_id,participant))
    if purpose=='delete':
        store.db.execute('UPDATE tg_message_index SET removed=1 WHERE participant=? AND message_id=?',(participant,body['message_id']))
    if purpose=='capture' and message_id and m['notebook_since']==context['epoch']:
        from telegram_notes import button
        enqueue(store,row['id']+':panel',row['chat_id'],{'chat_id':row['chat_id'],'text':'Pocket Notes\nYour notes are saved separately from this chat.','reply_markup':{'inline_keyboard':[[button('Notes','n:0')]]}},'panel',{'participant':participant})
        for identity in (context['capture'],message_id):
            queue_delete(store,row['id']+':delete:'+str(identity),participant,identity,{'auto':True,'epoch':context['epoch'],'note':context['note']},now+GRACE)

def view(store,participant,op,arg='',exclude=None):
    from telegram_notes import button,back_to_notes
    m=member(store,participant)
    if op=='notebookon':set_enabled(store,participant,True);op='notebook'
    if op=='notebookoff':set_enabled(store,participant,False);op='notebook'
    if op=='notebook':
        on=enabled(store,participant)
        return 'Notebook mode · '+('On' if on else 'Off')+'\n\nWhen enabled, NEW saved captures and their confirmations leave this chat after about 10 seconds. Notes stay in your notebook. Questions and answers stay in chat. Existing history is not swept. Turn off to stop future automatic cleanup. Telegram may refuse some removals; your notes remain safe.',[[button('Turn off' if on else 'Enable…','notebookoff' if on else 'notebookreview')],[button('Back to settings','prefs')]]
    if op=='notebookreview':return 'Enable Notebook mode for you?\n\nAfter each successful NEW save and confirmed Saved reply, remove the capture and temporary reply from Telegram after about 10 seconds. This cannot restore removed Telegram messages; your saved notes remain accessible. Questions, answers and existing history stay. You can turn this off at any time.',[[button('Not now','notebook')],[button('Enable Notebook mode','notebookon')]]
    if op=='chatclean':
        identity,count=prepare_clean(store,participant,exclude)
        return f'Remove {count} recent Telegram messages?\n\nIncludes your tracked messages and bot replies, including questions and answers. Saved notes and stored answers stay. Telegram deletion cannot be undone.\n\nOnly known messages under 48 hours old are included; older or untracked history stays. New messages after this review stay. The current notebook panel stays.',[[button('Cancel','chatcancel:'+identity)],[button(f'Remove {count} messages','chatconfirm:'+identity)]]
    if op=='chatcancel':cancel_clean(store,participant,arg);return 'Chat cleanup cancelled. Nothing queued for removal.',[back_to_notes()]
    if op=='chatconfirm':return confirm_clean(store,participant,arg),[[button('Refresh status','chatstatus:'+arg)],back_to_notes()]
    if op=='chatstatus':
        if arg:return clean_status(store,participant,arg),[[button('Refresh status','chatstatus:'+arg)],back_to_notes()]
        counts={r[0]:r[1] for r in store.db.execute("SELECT status,count(*) FROM tg_deliveries WHERE chat_id=? AND purpose='delete' GROUP BY status",(m['chat_id'],))}
        return f"Chat cleanup status\nRemoved: {counts.get('sent',0)}\nWaiting: {counts.get('pending',0)+counts.get('sending',0)}\nNot removed: {counts.get('failed',0)+counts.get('blocked',0)}\nUnconfirmed: {counts.get('uncertain',0)}\n\nTelegram may refuse older or already missing messages. No note is deleted by chat cleanup.",[[button('Refresh status','chatstatus'),button('Back to settings','prefs')]]
    raise Problem('Unknown notebook action.')

ACTIONS={'notebook','notebookreview','notebookon','notebookoff','chatclean','chatconfirm','chatcancel','chatstatus'}
