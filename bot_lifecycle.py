"""Recoverable bot deletion. No file removal, credential changes or outbound calls.

The catalog is the lifecycle authority. Local quiescence guards also stop a
worker which already holds the bot store. Root writer -> child writer is the
fixed lock order; child quiescence commits before the catalog mutation. A failed
catalog commit therefore fails closed, and retrying the same operation repairs
it. Restoring always leaves a connected bot paused and never replays old work.
"""
import json
import time
from contextlib import nullcontext
from assistant_store import Problem,uid


def generation(root, identity):
    row=root.db.execute('SELECT generation FROM assistant_lifecycle WHERE id=?',(identity,)).fetchone()
    return row[0] if row else 0


def deleted(root, identity):
    return bool(root.db.execute('SELECT 1 FROM assistant_lifecycle WHERE id=? AND deleted_at IS NOT NULL',(identity,)).fetchone())


def quiesce(store, removed):
    store.db.execute('INSERT OR REPLACE INTO config VALUES (?,?)',('bot_deleted',json.dumps(removed)))
    store.db.execute("UPDATE tasks SET status='cancelled',finished=? WHERE status IN ('queued','running')",(time.time(),))
    store.db.execute("UPDATE tg_deliveries SET status='blocked' WHERE status IN ('pending','sending')")
    store.db.execute('UPDATE tg_requests SET delivered=1 WHERE delivered=0')
    store.db.execute("UPDATE tg_updates SET status='done',body='{}' WHERE status='pending'")
    store.db.execute('UPDATE tg_invites SET used=1 WHERE used=0')
    store.db.execute('UPDATE tg_chat_cleanup SET expires=0')
    store.db.execute('UPDATE note_cleanup SET expires=0')
    store.db.execute('DELETE FROM sessions')
    store.db.execute("UPDATE tg_members SET mode=NULL,selection='[]' ")


def change(catalog, identity, body, *, restore=False):
    import telegram_store as tg
    root=catalog.root
    # Open/schema before taking writer locks; schema initialization may commit.
    with catalog.open(identity,include_deleted=True) as selected:
        tg.schema(root)
        if selected is not root:tg.schema(selected)
        with root.db:
            root.db.execute('BEGIN IMMEDIATE')
            current=generation(root,identity)
            was_deleted=deleted(root,identity)
            expected=body.get('generation')
            if type(expected) is not int:raise Problem('Reload the bot before confirming this action.',409)
            # Exact-operation retries are harmless; never replay through a later
            # restore/delete cycle, whose generation has advanced again.
            if expected==current-1 and was_deleted!=restore:return {'id':identity,'deleted':was_deleted,'unchanged':True}
            if expected!=current or was_deleted!=restore:raise Problem('This bot changed. Reopen its confirmation.',409)
            with selected.db if selected is not root else nullcontext():
                if selected is not root:selected.db.execute('BEGIN IMMEDIATE')
                name=selected.config('draft')['name']
                conn=root.db.execute('SELECT * FROM telegram_connections WHERE assistant=?',(identity,)).fetchone()
                if body.get('name')!=name:raise Problem('The bot name changed. Reopen its confirmation.',409)
                if not restore:
                    if body.get('revision')!=selected.draft_revision() or body.get('epoch')!=(conn['epoch'] if conn else None):
                        raise Problem('The bot changed. Review it again before deleting.',409)
                    if conn and conn['state']=='checking':raise Problem('Finish or cancel the in-progress bot connection before deleting it.',409)
                from private_testing import stop
                stop(selected)
                quiesce(selected,not restore)
                root.db.execute('INSERT OR REPLACE INTO assistant_lifecycle(id,generation,deleted_at,name) VALUES (?,?,?,?)',
                                (identity,current+1,None if restore else time.time(),name))
                if conn:
                    state=(('paused' if conn['owner_user_id'] else 'pairing') if conn['bot_id'] else 'reserved') if restore else 'deleted'
                    # Old activation/setup confirmations cannot reactivate it.
                    root.db.execute("UPDATE telegram_connections SET state=?,epoch=?,error=NULL,setup=CASE WHEN owner_user_id IS NULL THEN '{}' ELSE setup END WHERE assistant=?",(state,uid(),identity))
    return {'id':identity,'deleted':not restore,'telegram':('pairing' if conn and state=='pairing' else 'paused') if restore else 'stopped'}
