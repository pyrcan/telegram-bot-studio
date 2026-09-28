"""Explicit reassignment from a deleted bot. Metadata only; never read a secret.

The verified credential slot moves with its Telegram identity. The deleted entry
retains the spare slot and its own data, but no Telegram identity. Restoring it
cannot revive the transferred bot. No activation or cross-store content copy.
"""
import json
from contextlib import nullcontext
from assistant_store import Problem,uid
from catalog import Catalog
import telegram_store as tg

FIELDS=('assistant','source','source_epoch','source_generation','epoch','generation','revision','bot_id','bot')


def _plan(root,identity,source,selected):
    target=root.db.execute('SELECT * FROM telegram_connections WHERE assistant=?',(identity,)).fetchone()
    old=root.db.execute('SELECT * FROM telegram_connections WHERE assistant=?',(source,)).fetchone()
    tombstone=root.db.execute('SELECT * FROM assistant_lifecycle WHERE id=?',(source,)).fetchone()
    lifecycle=root.db.execute('SELECT * FROM assistant_lifecycle WHERE id=?',(identity,)).fetchone()
    if not old or not tombstone or tombstone['deleted_at'] is None or old['state']!='deleted' or not old['bot_id']:
        raise Problem('That connection is no longer held by a deleted bot.',409)
    if identity==source or (lifecycle and lifecycle['deleted_at'] is not None) or not target or target['state']!='reserved' or target['bot_id'] or selected.config('deployed'):
        raise Problem('Reconnect only to a disconnected, unpublished bot.',409)
    return {'assistant':identity,'source':source,'source_name':tombstone['name'],
      'source_epoch':old['epoch'],'source_generation':tombstone['generation'],
      'epoch':target['epoch'],'generation':lifecycle['generation'] if lifecycle else 0,
      'revision':selected.draft_revision(),'bot_id':old['bot_id'],'bot':old['username'],
      'owner_user_id':old['owner_user_id'],'source_slot':old['slot'],'target_slot':target['slot']}


def plan(root,identity,body):
    catalog=Catalog(root);tg.schema(root)
    with catalog.open(identity) as selected:return _plan(root,identity,body.get('source'),selected)


def commit(root,identity,body):
    catalog=Catalog(root);tg.schema(root)
    with catalog.open(identity) as selected:
        with root.db:
            root.db.execute('BEGIN IMMEDIATE')
            with selected.db if selected is not root else nullcontext():
                if selected is not root:selected.db.execute('BEGIN IMMEDIATE')
                current=_plan(root,identity,body.get('source'),selected)
                if any(body.get(k)!=current[k] for k in FIELDS):raise Problem('This reconnect changed. Review it again.',409)
                slot=body.get('verified_slot')
                if type(slot) is not int or slot not in (current['source_slot'],current['target_slot']):raise Problem('Verify the retained connection first.',409)
                spare=current['target_slot'] if slot==current['source_slot'] else current['source_slot']
                # Replace both associations in one SQLite transaction: no temporary
                # invalid slot, uniqueness relaxation, credential copy or live gap.
                root.db.execute('DELETE FROM telegram_connections WHERE assistant IN (?,?)',(identity,current['source']))
                root.db.execute("INSERT INTO telegram_connections(assistant,slot,state,epoch) VALUES (?,?,'deleted',?)",(current['source'],spare,uid()))
                root.db.execute("INSERT INTO telegram_connections(assistant,slot,bot_id,username,state,epoch,owner_user_id) VALUES (?,?,?,?,'verified',?,?)",(identity,slot,current['bot_id'],current['bot'],uid(),current['owner_user_id']))
    return tg.public_state(root,identity)


def reconnect(identity,context,call,api,inspect=None):
    """Trusted owner consumer; only read-only Telegram checks before commit."""
    current=call(f'assistants/{identity}/telegram/reconnect-plan',{'source':context.get('source')})
    if any(context.get(k)!=current[k] for k in FIELDS):raise ValueError('Reopen this reconnect confirmation')
    app_id=call('owner/catalog')['app_id']
    if inspect is None:
        import os
        from connect_telegram import owner_bearer
        from telegram_http import call_saved
        def inspect(slot,method):
            return call_saved(f'tg-{slot}',method,app_id=app_id,
                bearer=owner_bearer(),api_base=os.environ['API_BASE_URL'])
    valid=None
    for slot in dict.fromkeys((current['source_slot'],current['target_slot'])):
        try:
            bot=inspect(slot,'getMe')
        except Exception:
            # Provider failures can contain credential-bearing URLs. Never expose
            # or persist their text; only try the other already-stored slot.
            continue
        if not bot.get('is_bot') or bot.get('id')!=current['bot_id'] or bot.get('username','').lower()!=current['bot'].lower():continue
        hook=inspect(slot,'getWebhookInfo')
        if hook.get('url'):raise ValueError('Bot is managed elsewhere')
        valid=slot;break
    if valid is None:raise ValueError('The stored connection could not be verified')
    return call(f'assistants/{identity}/telegram/reconnect',{**{k:current[k] for k in FIELDS},'verified_slot':valid})
