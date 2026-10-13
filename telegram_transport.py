"""Supervised private-bot listener. Intake never waits for model generation."""
import json
import re
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from assistant_store import Store, Problem
from catalog import Catalog
import telegram_store as tg
from events import changed
import private_testing as testing

from telegram_http import TelegramError, call_saved

def call(slot,method,params=None):
    if type(slot) is not int or not 1 <= slot <= tg.MAX_CONNECTIONS:
        raise TelegramError(reason='invalid-request')
    return call_saved(f'tg-{slot}',method,params,app_id=os.environ['APP_ID'],
        bearer=os.environ['APP_TOKEN'],api_base=os.environ['API_BASE_URL'])

def deliver(store,root,identity,send=None,*,lane=None,stop=None,live=None):
    send=send or call
    live=live or store
    # One durable outbox, separate transport lanes. Panel ordering and atomic
    # claims still live in their existing owners; no bypass for destructive work.
    predicate={None:'1', 'ack':"method='answerCallbackQuery'",
               'panel':"purpose IN ('panel','status')",
               'background':"method!='answerCallbackQuery' AND purpose NOT IN ('panel','status')"}[lane]
    progress=0
    for row in store.db.execute("SELECT * FROM tg_deliveries WHERE status='pending' AND retry_at<=? AND ("+predicate+") ORDER BY created,id LIMIT 12",(time.time(),)).fetchall():
        if stop is not None and stop.is_set():return progress
        conn=tg.connection(root,identity)
        if not conn or conn['state'] not in ('active','testing'):return progress
        if not testing.deliverable(store,live,conn,row['chat_id']):
            with store.db:store.db.execute("UPDATE tg_deliveries SET status='blocked' WHERE id=?",(row['id'],))
            progress+=1;continue
        membership=store.db.execute('SELECT p.active FROM tg_members m JOIN participants p ON p.id=m.participant WHERE m.chat_id=?',(row['chat_id'],)).fetchone()
        if membership and not membership[0]:
            with store.db:store.db.execute("UPDATE tg_deliveries SET status='blocked' WHERE id=?",(row['id'],))
            progress+=1;continue
        task_match=re.fullmatch(r't([a-f0-9]{32}):[0-9]+',row['id'])
        if task_match:
            from note_management import sources_available
            task=store.db.execute('SELECT participant,sources FROM tasks WHERE id=?',(task_match[1],)).fetchone()
            if task and not sources_available(store,task['participant'],json.loads(task['sources'])):
                with store.db:store.db.execute("UPDATE tg_deliveries SET status='blocked' WHERE id=?",(row['id'],))
                progress+=1;continue
        import telegram_notebook as notebook
        from bot_behaviors import behavior
        impl=behavior(store.definition()) if store.config('deployed') else None
        presentation=impl if hasattr(impl,'prepare_delivery') else notebook
        with store.db:
            store.db.execute('BEGIN IMMEDIATE')
            current=store.db.execute("SELECT * FROM tg_deliveries WHERE id=? AND status='pending'",(row['id'],)).fetchone()
            if not current:continue
            if store.config('bot_deleted',False):
                store.db.execute("UPDATE tg_deliveries SET status='blocked' WHERE id=?",(row['id'],));continue
            try:prepared=presentation.prepare_delivery(store,current)
            except Problem:prepared=None
            if prepared=='wait':continue
            if prepared is None:
                store.db.execute("UPDATE tg_deliveries SET status='blocked' WHERE id=?",(row['id'],));progress+=1;continue
            row=prepared
            claimed=store.db.execute("UPDATE tg_deliveries SET status='sending' WHERE id=? AND status='pending'",(row['id'],)).rowcount
        if not claimed:continue
        progress+=1
        send_started=time.monotonic();outbox_ms=round(max(0,time.time()-row['created'])*1000)
        try:
            body=testing.payload(store,json.loads(row['body']))
            result=send(conn['slot'],row['method'],body)
        except TelegramError as exc:
            if row['purpose'] in ('panel','status') and row['method']=='editMessageText' and exc.code==400:
                with store.db:
                    if exc.reason=='not-modified':
                        changed=store.db.execute("UPDATE tg_deliveries SET status='sent' WHERE id=? AND status='sending'",(row['id'],)).rowcount
                        if changed:presentation.delivered(store,row,json.loads(row['body']))
                        continue
                    if exc.reason=='edit-unavailable' and row['purpose']=='panel':
                        participant=json.loads(row['context'])['participant'];message_id=json.loads(row['body'])['message_id']
                        store.db.execute('UPDATE tg_members SET notebook_panel=NULL WHERE participant=? AND notebook_panel=?',(participant,message_id))
                        body=json.loads(row['body']);body.pop('message_id',None)
                        context=json.loads(row['context']);context['target']=None
                        store.db.execute("UPDATE tg_deliveries SET status='pending',method='sendMessage',body=?,context=? WHERE id=? AND status='sending'",(json.dumps(body),json.dumps(context),row['id']))
                        continue
            # A definite rate-limit rejection is safe to retry. Other uncertain
            # transport outcomes are surfaced, not automatically duplicated.
            status='pending' if exc.code==429 else 'failed' if exc.code in (400,401,403,404) else 'uncertain'
            with store.db:store.db.execute("UPDATE tg_deliveries SET status=?,retry_at=? WHERE id=? AND status='sending'",(status,time.time()+max(1,exc.retry),row['id']))
            if status!='pending':
                from analytics import observe
                observe(live,'delivery-failed',row['id'],{'telegram_failed':1},
                        mode='test' if store.config('test_identity') else 'live')
            handled=False
            failed=getattr(presentation,'delivery_failed',None)
            if failed:
                with store.db:handled=failed(store,row,status,exc)
            if row['method']=='sendMessage':
                with root.db:root.db.execute('UPDATE telegram_connections SET error=? WHERE assistant=?',('Some replies were not confirmed. Users can send /latest to retrieve an answer.',identity))
            if exc.code==429:return 0
        else:
            with store.db:
                changed=store.db.execute("UPDATE tg_deliveries SET status='sent',message_id=? WHERE id=? AND status='sending'",(result.get('message_id') if isinstance(result,dict) else None,row['id'])).rowcount
                if changed:
                    presentation.delivered(store,row,result)
            if changed:
                from analytics import observe
                observe(live,'delivery',row['id'],
                        {'telegram_confirmed':1,'send_ms':round((time.monotonic()-send_started)*1000)},
                        mode='test' if store.config('test_identity') else 'live')
        finally:
            try:tg.record_timing(root,identity,'delivery',{'outbox_ms':outbox_ms,'send_ms':round((time.monotonic()-send_started)*1000)})
            except Exception:pass  # Do not change a delivery outcome for diagnostics.
    return progress

def delivery_worker(root_path,identity,stop,signals,lane,refresh):
    """One bounded lane with its own SQLite connection. Events drive new work;
    the bounded wait services durable retry/deletion deadlines and pause checks.
    """
    root=Store(root_path);signal=signals[lane]
    try:
        with Catalog(root).open(identity,include_deleted=True) as store:
            tg.schema(store)
            while not stop.is_set():
                signal.clear()
                conn=tg.connection(root,identity)
                if not conn or conn['state'] not in ('active','testing'):return
                progress=0
                with testing.runtime_stores(store) as stores:
                    for target in stores:
                        if lane=='background':tg.collect_answers(target)
                        progress+=deliver(target,root,identity,lane=lane,stop=stop,live=store)
                if lane=='background' and refresh.is_set():
                    refresh.clear();changed()
                if progress:
                    # Receipts can create panels; panel success can unlock cleanup.
                    if lane=='background':signals['panel'].set()
                    elif lane=='panel':signals['background'].set()
                    continue
                signal.wait(1)
    finally:root.db.close()

def poll_succeeded(root, identity, health=None):
    # Connection recovery must retire its own warning, not unrelated delivery
    # failures which still need attention. This never activates/pauses a bot.
    with root.db:
        root.db.execute("""UPDATE telegram_connections SET last_seen=?,
            error=CASE WHEN error LIKE 'Telegram connection failed (HTTP %). Check the bot connection in Studio.'
            THEN NULL ELSE error END WHERE assistant=?""", (time.time(),identity))
        if health is not None:
            root.db.execute("UPDATE telegram_connections SET poll_health=? WHERE assistant=?",(json.dumps(health),identity))


def listen(root_path,identity,stop,wake,background_signal=None):
    root=Store(root_path);local_stop=threading.Event()
    signals={'ack':threading.Event(),'panel':threading.Event(),'background':background_signal or threading.Event()}
    refresh=threading.Event();workers=[]
    try:
        with Catalog(root).open(identity,include_deleted=True) as store:
            tg.schema(store)
            for lane,signal in signals.items():
                worker=threading.Thread(target=delivery_worker,args=(root_path,identity,local_stop,signals,lane,refresh),name='telegram-'+lane,daemon=True)
                workers.append(worker);worker.start()
            while not stop.is_set():
                conn=tg.connection(root,identity)
                if not conn or conn['state'] not in ('active','testing'):return
                from telegram_menu import sync as sync_menu
                sync_menu(store,conn,call)
                if any(not worker.is_alive() for worker in workers):
                    raise RuntimeError('Telegram delivery lane stopped; renewing listener')
                requested_offset=store.config('telegram_offset',0)
                poll_started=time.monotonic()
                try:updates=call(conn['slot'],'getUpdates',{'offset':requested_offset,'timeout':5,'limit':20,'allowed_updates':['message','callback_query']})
                except TelegramError as exc:
                    error='Another connection is using this bot. Pause it there before resuming here.' if exc.code==409 else f'Telegram connection failed (HTTP {exc.code}). Check the bot connection in Studio.'
                    with root.db:
                        root.db.execute("UPDATE telegram_connections SET error=?,state=CASE WHEN ? IN (401,403,404,409) THEN 'paused' ELSE state END WHERE assistant=?",(error,exc.code,identity))
                    if exc.code in (401,403,404,409):return
                    if stop.wait(max(2,min(60,exc.retry or 5))):return
                    continue
                poll_seconds=round(time.monotonic()-poll_started,3)
                conn=tg.connection(root,identity)
                if conn['state'] not in ('active','testing'):return
                joined=testing.owner_joined(store,conn['owner_user_id'])
                intake_started=time.monotonic()
                tg.ingest(store,updates if isinstance(updates,list) else [],connection=conn)
                if testing.consume(store,conn):
                    wake.set()
                    # Participant navigation is not Studio management state.
                    # Only first owner admission needs its join-status refresh.
                    if not joined and testing.owner_joined(store,conn['owner_user_id']):refresh.set()
                    for signal in signals.values():signal.set()
                # Transport shape/timing only: never persist updates, identifiers,
                # message text, URLs or participant-level usage in management.
                health={'seconds':poll_seconds,'result_type':type(updates).__name__,
                        'received_updates':bool(updates) if isinstance(updates,list) else False,
                        'offset_advanced':store.config('telegram_offset',0)>requested_offset}
                poll_succeeded(root,identity,health)
                if isinstance(updates,list) and updates:
                    dates=[u['message']['date'] for u in updates if isinstance(u,dict) and isinstance(u.get('message'),dict) and type(u['message'].get('date')) is int]
                    metrics={'intake_ms':round((time.monotonic()-intake_started)*1000)}
                    if dates:metrics['receive_lag_ms']=round(max(0,time.time()-max(dates))*1000)
                    try:tg.record_timing(root,identity,'receive',metrics)
                    except Exception:pass  # Intake already committed; diagnostics are optional.
    finally:
        local_stop.set()
        for signal in signals.values():signal.set()
        for worker in workers:worker.join()
        root.db.close()

def listen_pairing(root_path,identity,stop,wake):
    """Identity-only intake: no participant store, menu, delivery or model work."""
    root=Store(root_path)
    try:
        with Catalog(root).open(identity,include_deleted=True) as store:
            tg.schema(store)
            while not stop.is_set():
                conn=tg.connection(root,identity)
                if not conn or conn['state']!='pairing':return
                offset=store.config('telegram_offset',0)
                try:updates=call(conn['slot'],'getUpdates',{'offset':offset,'timeout':5,'limit':20,'allowed_updates':['message']})
                except TelegramError as exc:
                    error='Another connection is using this bot. Stop it there, then try pairing again.' if exc.code==409 else f'Telegram connection failed (HTTP {exc.code}). Check the bot connection in Studio.'
                    with root.db:root.db.execute('UPDATE telegram_connections SET error=? WHERE assistant=? AND epoch=? AND state=?',(error,identity,conn['epoch'],'pairing'))
                    if stop.wait(max(2,min(60,exc.retry or 5))):return
                    continue
                valid=[u for u in updates if isinstance(u,dict) and type(u.get('update_id')) is int] if isinstance(updates,list) else []
                if valid:
                    # Advance the normal bot offset before admitting the owner.
                    # Pairing commands must never appear in participant history.
                    store.set_config('telegram_offset',max(u['update_id'] for u in valid)+1)
                    for update in valid:
                        if tg.accept_pairing(root,identity,conn['epoch'],update):
                            changed();wake.set();return
                poll_succeeded(root,identity)
    finally:root.db.close()

def recover_deliveries(store):
    """An interrupted network send is uncertain, including its presentation."""
    from bot_behaviors import behavior
    impl=behavior(store.definition()) if store.config('deployed') else None
    failed=getattr(impl,'delivery_failed',None)
    with store.db:
        rows=store.db.execute("SELECT * FROM tg_deliveries WHERE status='sending'").fetchall()
        store.db.execute("UPDATE tg_deliveries SET status='uncertain' WHERE status='sending'")
        if failed:
            for row in rows:
                try:failed(store,row,'uncertain',TelegramError())
                except Problem:pass  # A revoked task must not regain presentation access.


def work_one(root_path,identity,process_one,enrich_one,delivery_signal=None,wake=None,stop=None):
    """One bounded pool turn for one bot; a slow bot holds only its own turn."""
    if stop and stop.is_set():return False
    root=Store(root_path)
    try:
        with Catalog(root).open(identity,include_deleted=True) as selected:
            tg.schema(selected)
            conn=tg.connection(root,identity)
            worked=False
            with testing.runtime_stores(selected) as stores:
                for target in stores:
                    if stop and stop.is_set():break
                    permitted=conn and conn['state'] in ('active','testing') and (target is not selected or conn['state']=='active')
                    if conn and not permitted:
                        with target.db:target.db.execute("UPDATE tasks SET status='cancelled' WHERE status='queued' AND id IN (SELECT task_id FROM tg_requests)")
                    enrich_started=time.monotonic()
                    enriched=enrich_one(target)
                    if enriched:
                        try:tg.record_timing(root,identity,'enrichment',{'duration_ms':round((time.monotonic()-enrich_started)*1000)})
                        except Exception:pass
                    def measured(metrics):tg.record_timing(root,identity,'work',metrics)
                    processed=process_one(target,on_timing=measured,metrics_store=selected,cost_root=root,cost_identity=identity)
                    if enriched or processed:worked=True
                    if permitted:
                        tg.collect_answers(target)
                        if delivery_signal:delivery_signal.set()
            if worked and wake:wake.set()
            return worked
    finally:root.db.close()


def run(root,process_one,enrich_one,duration=1800):
    """Supervise intake/delivery and bounded, fair cross-bot background turns."""
    from concurrent.futures import ThreadPoolExecutor
    tg.schema(root);catalog=Catalog(root)
    for identity in catalog.ids():
        with catalog.open(identity,include_deleted=True) as selected:
            tg.schema(selected)
            with testing.runtime_stores(selected) as stores:
                for target in stores:recover_deliveries(target)
    stop=threading.Event();wake=threading.Event();listeners={};work={}
    deadline=time.monotonic()+duration;last_runtime=0;runtime=None;delivery_signals={}
    # At most one background turn per bot and at most the reviewed connection
    # capacity plus Pocket Notes in parallel. Unconnected drafts queue fairly.
    with ThreadPoolExecutor(max_workers=tg.MAX_CONNECTIONS+1,thread_name_prefix='studio-bot') as pool:
        try:
            while time.monotonic()<deadline:
                active=[r[0] for r in root.db.execute("SELECT assistant FROM telegram_connections WHERE state IN ('active','testing','pairing')")]
                for identity in active:
                    if identity not in listeners or not listeners[identity].is_alive():
                        conn=tg.connection(root,identity)
                        if conn['state']=='pairing':
                            thread=threading.Thread(target=listen_pairing,args=(root.root,identity,stop,wake),daemon=True)
                        else:
                            signal=delivery_signals.setdefault(identity,threading.Event())
                            thread=threading.Thread(target=listen,args=(root.root,identity,stop,wake,signal),daemon=True)
                        listeners[identity]=thread;thread.start()
                worked=False;running=False
                # A large set of unconnected drafts cannot occupy every lane
                # ahead of bots that are currently receiving Telegram updates.
                ids=list(dict.fromkeys(active+catalog.ids()))
                for identity in ids:
                    future=work.get(identity)
                    if future and not future.done():
                        running=True;continue
                    if future:
                        result=future.result()
                        if result:worked=True
                        elif not active:continue  # This bot scanned idle; no live listener can add work.
                    signal=delivery_signals.setdefault(identity,threading.Event())
                    work[identity]=pool.submit(work_one,root.root,identity,process_one,enrich_one,signal,wake,stop)
                    running=True
                for identity in list(work):
                    if identity not in ids and work[identity].done():
                        work[identity].result();del work[identity]
                if not active and not running and not worked:break
                # Accepted-source changes yield normally; in-flight bot turns
                # finish before this single-flight job exits, avoiding overlap.
                if active and time.monotonic()-last_runtime>60:
                    req=urllib.request.Request(f"{os.environ['API_BASE_URL']}/api/apps/{os.environ['APP_ID']}/job-context",headers={'Authorization':'Bearer '+os.environ['APP_TOKEN']})
                    try:
                        with urllib.request.urlopen(req,timeout=10) as response:current=json.load(response).get('runtime_dir')
                        if runtime is not None and current!=runtime:break
                        runtime=current
                    except Exception:pass
                    last_runtime=time.monotonic()
                if not worked:wake.wait(1);wake.clear()
        finally:
            stop.set()
            for thread in listeners.values():thread.join(timeout=30)
            for future in work.values():future.result()
