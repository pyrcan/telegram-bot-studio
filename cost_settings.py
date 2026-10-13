"""Owner-supplied accounting, not provider routing or a billed-dollar ledger.

Shared monthly pools are counted once. Numerical AI estimates are pinned to
the settings revision at task start; later edits never reprice recorded usage.
"""
import copy
import json
import math
import re
import time
from assistant_store import Problem, uid

LEGACY_DEFAULT = {'currency':'GBP','monthly_hosting':None,'ai_mode':'unknown',
                  'monthly_ai':None,'input_per_million':None,
                  'cached_per_million':None,'output_per_million':None,'revision':'0'}
RATE_KEYS = ('input_per_million','cached_per_million','output_per_million')
PROFILE_KEYS = {'id','name','provider','model','kind','monthly_cost',*RATE_KEYS}
SETTING_KEYS = {'schema','currency','monthly_hosting','hosting_basis',
                'default_profile','profiles','assignments'}

def read(root):
    value=root.config('studio_costs',LEGACY_DEFAULT)
    if value.get('schema')==2:return copy.deepcopy(value)
    # Data migration only: preserve the old owner amounts and exact revision.
    kind={'allocation':'subscription','tokens':'tokens'}.get(value['ai_mode'],'unknown')
    profile={'id':'existing-ai','name':'Existing AI connection','provider':'',
             'model':'','kind':kind,'monthly_cost':value['monthly_ai'],
             **{key:value[key] for key in RATE_KEYS}}
    return {'schema':2,'currency':value['currency'],
            'monthly_hosting':value['monthly_hosting'],'hosting_basis':'allocation',
            'default_profile':profile['id'],'profiles':[profile],
            'assignments':{},'revision':value['revision']}

def amount(value):
    if value is not None and (type(value) not in (int,float) or
          not math.isfinite(value) or not 0<=value<=10**9):
        raise Problem('Enter a finite nonnegative cost, or leave it unknown.')
    return value

def text(value,limit,required=False):
    if not isinstance(value,str) or len(value)>limit or (required and not value.strip()):
        raise Problem('Enter text within the displayed limit.')
    return value.strip()

def validate(catalog,body):
    if set(body)-SETTING_KEYS-{'expected_revision'} or body.get('schema')!=2:
        raise Problem('Use the current Costs setup form.')
    if body.get('currency') not in ('GBP','USD','EUR'):
        raise Problem('Choose GBP, USD or EUR.')
    if body.get('hosting_basis') not in ('allocation','ceiling'):
        raise Problem('Choose the bots’ hosting share or a full-cost ceiling.')
    profiles=body.get('profiles')
    if not isinstance(profiles,list) or len(profiles)>32:
        raise Problem('Enter at most 32 billing profiles.')
    parsed=[];ids=set()
    for item in profiles:
        if not isinstance(item,dict) or set(item)!=PROFILE_KEYS:
            raise Problem('A billing profile has missing or unknown fields.')
        identity=item['id']
        if not isinstance(identity,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',identity) or identity in ids:
            raise Problem('Billing profile IDs must be unique.')
        ids.add(identity)
        if item['kind'] not in ('subscription','tokens','local','unknown'):
            raise Problem('Choose subscription, token rates, local model or unknown.')
        parsed.append({**item,'name':text(item['name'],80,True),
                       'provider':text(item['provider'],80),'model':text(item['model'],160),
                       **{key:amount(item[key]) for key in ('monthly_cost',*RATE_KEYS)}})
    default=body.get('default_profile')
    if default is not None and (not isinstance(default,str) or default not in ids):
        raise Problem('Choose an existing default billing profile.')
    assignments=body.get('assignments')
    if not isinstance(assignments,dict) or len(assignments)>128:
        raise Problem('Choose billing profiles for existing bots.')
    known=set(catalog.ids(include_deleted=True))
    for bot,profile in assignments.items():
        if bot not in known or (profile is not None and
                              (not isinstance(profile,str) or profile not in ids)):
            raise Problem('A bot or billing profile changed. Refresh Costs setup.')
    return {'schema':2,'currency':body['currency'],
            'monthly_hosting':amount(body.get('monthly_hosting')),
            'hosting_basis':body['hosting_basis'],'default_profile':default,
            'profiles':parsed,'assignments':assignments}

def history_schema(root):
    root.db.execute('CREATE TABLE IF NOT EXISTS studio_cost_versions(revision TEXT PRIMARY KEY,effective REAL NOT NULL,settings TEXT NOT NULL)')

def remember(root,value,now):
    root.db.execute('INSERT OR IGNORE INTO studio_cost_versions VALUES (?,?,?)',
                    (value['revision'],now,json.dumps(value)))

def save(catalog,body,*,now=None):
    value=validate(catalog,body)
    root=catalog.root;history_schema(root)
    now=time.time() if now is None else now
    with root.db:
        root.db.execute('BEGIN IMMEDIATE')
        current=read(root)
        if current['revision']!=body.get('expected_revision'):
            raise Problem('Cost assumptions changed. Refresh before saving.',409)
        remember(root,current,now)
        value['revision']=uid()
        remember(root,value,now)
        root.db.execute('INSERT OR REPLACE INTO config VALUES (?,?)',('studio_costs',json.dumps(value)))
    return value

def history(root):
    history_schema(root)
    return [{'revision':r['revision'],'effective':r['effective'],
             'settings':json.loads(r['settings'])} for r in
            root.db.execute('SELECT * FROM studio_cost_versions ORDER BY effective DESC,rowid DESC LIMIT 25')]

def profile_for(settings,identity,uses_model=True):
    chosen=settings['assignments'].get(identity,
              settings['default_profile'] if uses_model else None)
    return next((p for p in settings['profiles'] if p['id']==chosen),None)

def pin(root,identity):
    """Called once at task start, outside model/intake/delivery authority."""
    settings=read(root);history_schema(root)
    with root.db:remember(root,settings,time.time())
    return {'revision':settings['revision'],'currency':settings['currency'],
            'profile':profile_for(settings,identity),'calls':[]}

def usage_schema(store):
    store.db.execute('CREATE TABLE IF NOT EXISTS studio_ai_costs(day TEXT NOT NULL,mode TEXT NOT NULL,revision TEXT NOT NULL,profile TEXT NOT NULL,model TEXT NOT NULL,currency TEXT NOT NULL,priced_calls INTEGER NOT NULL,unknown_calls INTEGER NOT NULL,amount REAL NOT NULL,PRIMARY KEY(day,mode,revision,profile,model,currency))')

def token_estimate(profile,model,usage):
    if not profile or profile['kind']!='tokens' or not usage:
        return None
    if profile['model'] and profile['model']!=model:return None
    rates=[profile[k] for k in RATE_KEYS]
    if any(r is None for r in rates):return None
    return ((usage['input_tokens']-usage['cached_input_tokens'])*rates[0]+
            usage['cached_input_tokens']*rates[1]+usage['output_tokens']*rates[2])/1_000_000

def record_usage(store,context,day,mode):
    """Shares the task metrics transaction/dedupe; stores no prompt or user ID."""
    profile=context['profile']
    for call in context['calls']:
        value=token_estimate(profile,call['model'],call['usage'])
        store.db.execute('INSERT INTO studio_ai_costs VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(day,mode,revision,profile,model,currency) DO UPDATE SET priced_calls=priced_calls+excluded.priced_calls,unknown_calls=unknown_calls+excluded.unknown_calls,amount=amount+excluded.amount',
             (day,mode,context['revision'],profile['id'] if profile else '',
              call['model'],context['currency'],int(value is not None),
              int(value is None),value if value is not None else 0))

def recorded_cost(store,start,end,currency,ai_calls,uses_model):
    usage_schema(store)
    rows=store.db.execute('SELECT currency,sum(priced_calls) priced,sum(unknown_calls) unknown,sum(amount) amount FROM studio_ai_costs WHERE day>=? AND day<? GROUP BY currency',(start,end)).fetchall()
    priced=sum(r['priced'] for r in rows if r['currency']==currency)
    tracked=sum(r['priced']+r['unknown'] for r in rows)
    other=sum(r['priced']+r['unknown'] for r in rows if r['currency']!=currency)
    missing=max(0,ai_calls-tracked)
    unknown=missing+sum(r['unknown'] for r in rows if r['currency']==currency)
    value=sum(r['amount'] for r in rows if r['currency']==currency) if priced else 0 if not uses_model and ai_calls==0 else None
    estimates=[{'revision':r['revision'],'profile':r['profile'],'model':r['model'],
                'currency':r['currency'],'mode':r['mode'],'priced_ai_calls':r['priced'],
                'unknown_ai_calls':r['unknown'],
                'metered_ai_estimate':round(r['amount'],6) if r['priced'] else None}
               for r in store.db.execute('SELECT revision,profile,model,currency,mode,sum(priced_calls) priced,sum(unknown_calls) unknown,sum(amount) amount FROM studio_ai_costs WHERE day>=? AND day<? GROUP BY revision,profile,model,currency,mode ORDER BY revision,profile,model,currency,mode',(start,end))]
    return {'metered_ai_estimate':round(value,6) if value is not None else None,
            'average_metered_ai_call_estimate':round(value/priced,6) if priced else None,
            'priced_ai_calls':priced,'unknown_ai_calls':unknown,
            'other_currency_calls':other,'legacy_ai_calls':missing,
            'estimate_history':estimates}

def budgets(bots,settings):
    """Current planning allocation, explicitly distinct from pinned usage."""
    hosted=[b for b in bots if b['connected']]
    pools={p['id']:[] for p in settings['profiles']}
    for bot in bots:
        profile=profile_for(settings,bot['id'],bot['uses_model'] or bool(bot['combined']['ai_calls']))
        bot['cost'].update(profile_id=profile['id'] if profile else None,
                           profile_name=profile['name'] if profile else None)
        if profile:pools[profile['id']].append(bot)
        no_ai=not bot['uses_model'] and not bot['combined']['ai_calls']
        allocation=0 if no_ai and not profile else None
        if profile and profile['kind'] in ('subscription','local'):
            # Fill after the complete membership set is known.
            allocation=profile['monthly_cost']
        host=settings['monthly_hosting']
        hosting=None if host is None else host/len(hosted) if bot in hosted else 0
        bot['cost'].update(hosting_monthly_estimate=hosting,
                           ai_monthly_allocation_estimate=allocation)
    for profile in settings['profiles']:
        members=pools[profile['id']]
        if profile['kind'] in ('subscription','local') and members:
            for bot in members:
                value=profile['monthly_cost']
                bot['cost']['ai_monthly_allocation_estimate']=None if value is None else value/len(members)
    for bot in bots:
        cost=bot['cost'];h=cost['hosting_monthly_estimate'];a=cost['ai_monthly_allocation_estimate']
        cost['monthly_budget_estimate']=h+a if h is not None and a is not None else None
    fixed=[p for p in settings['profiles'] if p['kind'] in ('subscription','local')]
    # Unassigned paid pools are real owner-entered expenses, not free templates.
    unknown_pool=any(p['monthly_cost'] is None for p in fixed)
    variable_unknown=any(b['cost']['ai_monthly_allocation_estimate'] is None for b in bots)
    ai=None if unknown_pool else sum(p['monthly_cost'] for p in fixed)
    unassigned=[p for p in fixed if not pools[p['id']]]
    unallocated=None if any(p['monthly_cost'] is None for p in unassigned) else sum(p['monthly_cost'] for p in unassigned)
    host=settings['monthly_hosting']
    return {'hosting_monthly_estimate':host,'ai_monthly_allocation_estimate':ai,
            'monthly_budget_estimate':host+ai if host is not None and ai is not None and not variable_unknown else None,
            'unallocated_ai_monthly_estimate':unallocated,
            'variable_ai_cost_unknown':variable_unknown}
