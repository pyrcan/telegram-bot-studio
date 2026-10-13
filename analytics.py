"""Forward-only owner aggregate operations; never participant/content reporting.

Daily counters live in each bot's domain store, with separate live/test buckets.
Receipts hash internal operation IDs only, never user IDs, and prevent replay.
This is operational evidence, NOT an invoice: crashes can leave partial usage.
"""
import calendar
import datetime as dt
import hashlib
import json
import time
from assistant_store import Problem
import cost_settings as costs

FIELDS = ('interactions','callbacks','tasks','task_errors','task_cancelled',
          'process_ms','queue_ms','worker_cpu_ms','ai_calls','metered_ai_calls',
          'input_tokens','cached_input_tokens','output_tokens',
          'telegram_confirmed','telegram_failed','send_ms')

def schema(store):
    if getattr(store,'_analytics_ready',False):return
    store.db.execute('CREATE TABLE IF NOT EXISTS studio_daily(day TEXT NOT NULL, mode TEXT NOT NULL, metric TEXT NOT NULL, value INTEGER NOT NULL, PRIMARY KEY(day,mode,metric))')
    store.db.execute('CREATE TABLE IF NOT EXISTS studio_receipts(hash TEXT PRIMARY KEY, created REAL NOT NULL)')
    with store.db:
        store.db.execute('INSERT OR IGNORE INTO config VALUES (?,?)',('analytics_since',json.dumps(time.time())))
    costs.usage_schema(store)
    store._analytics_ready=True

def record(store, kind, receipt, values, *, mode='live', now=None, cost_context=None):
    """One short transaction; writes fixed numeric columns plus opaque receipts."""
    if mode not in ('live','test'):raise ValueError('Unknown usage bucket')
    if any(field not in FIELDS for field in values):raise ValueError('Unknown numeric measurement')
    if any(type(n) is not int or not 0<=n<=10**15 for n in values.values()):
        raise ValueError('Measurements must be bounded nonnegative integers')
    schema(store)
    now=time.time() if now is None else now
    day=dt.datetime.fromtimestamp(now,dt.timezone.utc).date().isoformat()
    digest=hashlib.sha256(f'{mode}:{kind}:{receipt}'.encode()).hexdigest()
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        inserted=store.db.execute('INSERT OR IGNORE INTO studio_receipts VALUES (?,?)',(digest,now)).rowcount
        if not inserted:return False
        for field,n in values.items():
            store.db.execute('INSERT INTO studio_daily VALUES (?,?,?,?) ON CONFLICT(day,mode,metric) DO UPDATE SET value=value+excluded.value',(day,mode,field,n))
        if cost_context is not None:costs.record_usage(store,cost_context,day,mode)
        # Daily totals last one year; operational dedupe receipts last 90 days.
        # The domain's permanent update/task receipts still prevent old execution.
        last=store.config('analytics_cleanup_day')
        if last!=day:
            store.db.execute('DELETE FROM studio_receipts WHERE created<?',(now-90*86400,))
            cutoff=(dt.date.fromisoformat(day)-dt.timedelta(days=365)).isoformat()
            store.db.execute('DELETE FROM studio_daily WHERE day<?',(cutoff,))
            store.db.execute('DELETE FROM studio_ai_costs WHERE day<?',(cutoff,))
            store.db.execute('INSERT OR REPLACE INTO config VALUES (?,?)',('analytics_cleanup_day',json.dumps(day)))
    return True

def observe(*args, **kwargs):
    """Diagnostics must never change an intake, task or Telegram outcome."""
    try:return record(*args,**kwargs)
    except Exception:
        try:args[0].set_config('analytics_incomplete',True)
        except Exception:pass
        return False

def totals(store,start,end):
    schema(store)
    buckets={mode:{field:0 for field in FIELDS} for mode in ('live','test')}
    daily={}
    for row in store.db.execute('SELECT day,mode,metric,value FROM studio_daily WHERE day>=? AND day<?',(start,end)):
        if (row['metric'] not in FIELDS) or row['mode'] not in buckets:continue
        bucket=buckets[row['mode']]
        bucket[row['metric']]=bucket.get(row['metric'],0)+row['value']
        day=daily.setdefault(row['day'],{'day':row['day'],'live_interactions':0,'test_interactions':0})
        if row['metric']=='interactions':day[row['mode']+'_interactions']+=row['value']
    return buckets, sorted(daily.values(),key=lambda r:r['day'])

def dashboard(catalog, *, now=None):
    import telegram_store as tg
    from bot_behaviors import behavior
    now=time.time() if now is None else now
    today=dt.datetime.fromtimestamp(now,dt.timezone.utc).date()
    start=today.replace(day=1).isoformat()
    end=(today.replace(day=1)+dt.timedelta(days=calendar.monthrange(today.year,today.month)[1])).isoformat()
    settings=costs.read(catalog.root)
    bots=[]
    for identity in catalog.ids():
        connection=tg.connection(catalog.root,identity) or {}
        with catalog.open(identity) as store:
            tg.schema(store)
            buckets,daily=totals(store,start,end)
            # Existing identity/access metadata only; no task/content history mined.
            states={state:0 for state in ('allowed','pending','blocked')}
            owner=connection.get('owner_user_id')
            rows=store.db.execute('SELECT user_id FROM tg_members UNION SELECT user_id FROM tg_access_requests')
            for row in rows:
                user=tg.access_user(store,row[0],owner)
                if user and row[0]!=owner:states[user['state']]+=1
            joined=store.db.execute('SELECT count(*) FROM tg_members m JOIN participants p ON p.id=m.participant WHERE p.active=1 AND m.user_id!=?',(owner if owner is not None else -1,)).fetchone()[0]
            definition=store.definition() if store.config('deployed') else store.config('draft')
            impl=behavior(definition)
            uses_model=impl is None or bool(getattr(impl,'uses_model',False))
            both={field:sum(b[field] for b in buckets.values()) for field in FIELDS}
            bots.append({'id':identity,'name':store.config('draft')['name'],
                         'display':__import__('bot_identity').read(catalog.root,identity,store.config('draft')),
                         'users':{**states,'joined':joined},'usage':buckets,'combined':both,
                         'daily':daily,'since':store.config('analytics_since'),
                         'incomplete':store.config('analytics_incomplete',False),'uses_model':uses_model,
                         'actual_model':definition['model'] if uses_model else None,
                         'runner_provider':'OpenAI Codex' if uses_model else None,
                         'cost':{**costs.recorded_cost(store,start,end,settings['currency'],both['ai_calls'],uses_model),
                                 'measured_bill':None,'unmetered_ai_calls':both['ai_calls']-both['metered_ai_calls']},
                         'connected':connection.get('state') in ('active','testing','paused','verified')})
    summary=costs.budgets(bots,settings)
    return {'period':{'start':start,'end':end,'timezone':'UTC'},'bots':bots,'assumptions':settings,
            'cost_summary':summary,'cost_history':costs.history(catalog.root),
            'users':{key:sum(bot['users'][key] for bot in bots) for key in ('allowed','pending','blocked','joined')},
            'usage':{mode:{field:sum(bot['usage'][mode][field] for bot in bots) for field in FIELDS} for mode in ('live','test')},
            'disclosure':'Current non-deleted bots; users are bot memberships, not distinct people across bots. Owner accounts excluded. Activity is forward-only; live includes owner use outside private tests. Operational usage is not a billing ledger and may be incomplete after interruption. Daily totals retained one year; replay receipts 90 days. No content or individual usage profiles.',
            'allocation':'Current monthly planning assumptions: hosting is divided equally across connected bots, including paused ones. Each shared subscription/local-model budget is counted once and divided across its assigned bots; unassigned paid pools remain in the overall budget. Token usage estimates retain the rates and currency pinned at task start, including private tests. Earlier unpriced usage is not backfilled; no automatic currency conversion. Labels do not change the serving provider. No provider bill, child-process CPU, full egress or storage invoice is measured.'}
