"""Participant-owned display preferences and deterministic note views.

No model is needed to browse, group or filter. Task options are snapshots, not
live preferences, so changing the menu cannot change a queued digest.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import urlparse
from assistant_store import Problem

DEFAULTS={'timezone':'UTC','period':'all','type':'all','theme':'','query':'','digest_period':'7','group':'date'}
PERIODS={'all':'All time','today':'Today','7':'Last 7 days','30':'Last 30 days'}
TYPES={'all':'All types','note':'Text notes','video':'Videos','link':'Web links'}
GROUPS={'date':'Date','type':'Type','theme':'Theme'}

def timezone(value):
    try:return ZoneInfo(value)
    except (ZoneInfoNotFoundError,ValueError,TypeError):raise Problem('Use a timezone such as Europe/London, America/New_York or UTC.')

def preferences(store,participant,patch=None):
    store.active(participant)
    key='note-view:'+participant
    current={**DEFAULTS,**store.config(key,{})}
    if patch is not None:
        if set(patch)-set(DEFAULTS):raise Problem('Unknown note preference.')
        new={**current,**patch}
        timezone(new['timezone'])
        if new['period'] not in PERIODS or new['digest_period'] not in PERIODS or new['type'] not in TYPES or new['group'] not in GROUPS:raise Problem('Choose one of the available views.')
        if not all(isinstance(new[k],str) and len(new[k])<=200 for k in ('theme','query')):raise Problem('Use a shorter filter.')
        current=new;store.set_config(key,current)
    return current

def kind(note):
    if not note.get('url'):return 'note'
    host=(urlparse(note['url']).hostname or '').lower()
    return 'video' if host in ('youtube.com','www.youtube.com','m.youtube.com','youtu.be','vimeo.com','www.vimeo.com') else 'link'

def saved_at(note,zone='UTC',exact=False):
    dt=datetime.fromtimestamp(note['created'],timezone(zone))
    return dt.strftime('%d %b %Y · %H:%M')+' '+zone if exact else dt.strftime('%d %b %Y')

def cutoff(period,zone,now):
    if period=='all':return 0
    if period=='today':return datetime.fromtimestamp(now,timezone(zone)).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    return now-int(period)*86400

def filter_notes(rows,opts,now):
    after=cutoff(opts.get('period','all'),opts.get('timezone','UTC'),now)
    query=opts.get('query','').casefold().strip();theme=opts.get('theme','').casefold()
    return [n for n in rows if n['created']>=after and
      (opts.get('type','all')=='all' or kind(n)==opts['type']) and
      (not theme or theme in n['tags']) and
      (not query or query in (n['title']+' '+n['original']+' '+str(n['metadata'])+' '+' '.join(n['tags'])).casefold())]

def group_key(note,group,zone):
    if group=='type':return TYPES[kind(note)]
    if group=='theme':return ', '.join(note['tags']) or 'Uncategorised'
    return saved_at(note,zone)

def digest_options(raw):
    raw=raw or {};period=raw.get('period','7');group=raw.get('group','date');zone=raw.get('timezone','UTC')
    if period not in PERIODS or group not in GROUPS:raise Problem('Invalid digest view.')
    timezone(zone)
    return {'period':period,'group':group,'timezone':zone}

def digest_notes(store,participant,options,at):
    opts=digest_options(options)
    rows=filter_notes(store.notes(participant),opts,at)
    total=len(rows);rows=rows[:40]  # Select the latest bounded subset BEFORE grouping.
    if opts['group']!='date':rows.sort(key=lambda n:(group_key(n,opts['group'],opts['timezone']),-n['created'],n['id']))
    return rows,total
