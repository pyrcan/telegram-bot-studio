"""Owner-only bot editing chat identity, never participant content.

The app cannot mint owner chats. An approved operation in the actual owner
chat creates or reuses the canonical chat; the UI only opens that chat.
No model turn is submitted: the owner reviews the draft and presses Send.
"""
import uuid
from assistant_store import Problem


def prepare(store, body):
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        prior=store.config('editing_chat')
        if prior and body.get('replace_id')!=prior['id']:return prior
        current={'id':str(uuid.uuid4()),'needs_draft':True}
        store.db.execute('INSERT OR REPLACE INTO config VALUES (?,?)',('editing_chat',__import__('json').dumps(current)))
        return current


def ready(store, body):
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        current=store.config('editing_chat')
        if not current or current['id']!=body.get('id'):raise Problem('The editing chat changed. Open Customize bot again.',409)
        current['needs_draft']=current.get('needs_draft',False) and body.get('needs_draft') is True
        store.db.execute('INSERT OR REPLACE INTO config VALUES (?,?)',('editing_chat',__import__('json').dumps(current)))
        return current


def ensure(identity, call, api):
    """Idempotent owner API orchestration. Never seed Chat.messages directly."""
    from urllib.error import HTTPError
    prefix=f'assistants/{identity}/admin/'
    state=call(prefix+'state')
    current=call(prefix+'editing/prepare',{})
    for attempt in range(2):
        chat_id=current['id']
        try:
            chat=api(f'/api/chats/{chat_id}?limit=1&compact=1')
            if chat.get('deleted_at'):
                current=call(prefix+'editing/prepare',{'replace_id':chat_id});continue
        except HTTPError as exc:
            if exc.code not in (404,410):raise
            if exc.code==410:
                current=call(prefix+'editing/prepare',{'replace_id':chat_id});continue
            try:
                chat=api('/api/chats',{'id':chat_id,'title':'Customize '+state['draft']['name']})
            except HTTPError as creation:
                if creation.code!=409:raise
                current=call(prefix+'editing/prepare',{'replace_id':chat_id});continue
        call(prefix+'editing/ready',{'id':chat_id,'needs_draft':not chat.get('messages')})
        return
    raise Problem('Could not open the editing chat. Please try Customize bot again.',409)


def handoff(store, body):
    # Consume the initial composer suggestion once. Reopening must NEVER replace
    # the owner's unfinished draft. The durable bot/chat binding survives even
    # if navigation is interrupted before the suggestion reaches the composer.
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        current=store.config('editing_chat')
        if not current or current['id']!=body.get('id'):
            raise Problem('The editing chat changed. Open Customize bot again.',409)
        result=dict(current)
        current['needs_draft']=False
        store.db.execute('INSERT OR REPLACE INTO config VALUES (?,?)',('editing_chat',__import__('json').dumps(current)))
        return result
