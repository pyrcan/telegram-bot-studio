"""Private Telegram notebook: deterministic views; explicit model requests only."""
import hashlib
import json
import time
import note_management as notes
from note_views import preferences, saved_at, kind, TYPES, PERIODS, GROUPS, digest_notes, group_key
from assistant_store import Problem


def button(label,action):return {'text':label,'callback_data':'pn:'+action}
def back_to_notes():return [button('Back to notes','n:0')]
def mode(store,participant,value):
    with store.db:store.db.execute('UPDATE tg_members SET mode=? WHERE participant=?',(value,participant))
def theme_id(value):return hashlib.sha256(value.encode()).hexdigest()[:12]

def note_text(n,prefs):
    tags=n['tags']
    text=n['title'][:200]+'\nSaved '+saved_at(n,prefs['timezone'],True)+'\n'+TYPES[kind(n)]
    if tags:text+=' · '+', '.join(tags)
    if n['original']!=n['title']:text+='\n\n'+n['original'][:2400]
    if n['metadata'].get('author'):text+='\nBy '+str(n['metadata']['author'])[:150]
    if n['metadata'].get('description'):text+='\n\n'+str(n['metadata']['description'])[:400]
    return text[:3300]

def reader(store,participant,prefs,page=0,identity=None,trash=False,suffix=''):
    chosen=notes.detail(store,participant,identity) if identity else None
    if chosen:trash=bool(chosen['deleted_at'])
    data=notes.browse(store,participant,{**({} if trash else prefs),'page':page,'trash':trash},page_size=1,focus=identity)
    n=chosen or (data['notes'][0] if data['notes'] else None)
    if not n:
        text='Trash is empty.' if trash else 'No notes here. Send a note or link to save it.'
        if not trash and any(prefs[k]!=v for k,v in [('period','all'),('type','all'),('theme',''),('query','')]):text+='\nFilters are active. Change them under More.'
        return suffix+text,[[button('More','trashmore' if trash else 'more')]]
    matched=bool(data['notes'] and data['notes'][0]['id']==n['id'])
    heading=('Trash' if trash else 'Notes')+(f" · {data['page']+1} of {data['total']}" if matched else ' · outside current filters')
    text=suffix+heading+'\nNewest first\n\n'+note_text(n,prefs)
    nav=[];prefix='trash' if trash else 'n'
    if matched and data['page']:nav.append(button('‹ Previous',f"{prefix}:{data['page']-1}"))
    if matched and data['page']+1<data['pages']:nav.append(button('Next ›',f"{prefix}:{data['page']+1}"))
    nav.append(button('More',('trashmore:' if trash else 'more:')+n['id']))
    return text[:3500],[nav]


def render(store,participant,action,request_id=None,message_id=None):
    op,_,arg=action.partition(':');prefs=preferences(store,participant);suffix=''
    # Navigating is an explicit exit from a one-shot input prompt.
    mode(store,participant,None)
    from telegram_notebook import ACTIONS as NOTEBOOK_ACTIONS,view as notebook_view
    if op in NOTEBOOK_ACTIONS:return notebook_view(store,participant,op,arg,message_id)
    if op in ('search','timezone','themeset'):
        if op=='themeset':notes.detail(store,participant,arg)
        mode(store,participant,op+(':'+arg if arg else ''))
        prompt={'search':'Search your notes. Send a title, word or theme.',
                'timezone':'Send your timezone, e.g. Europe/London, America/New_York or UTC. Dates will use it; original save times never change.',
                'themeset':'Send themes separated by commas, e.g. work, reading. Send - to remove themes. This changes labels only.'}[op]
        return prompt,[[button('Cancel','n:0')]]
    if op=='prefs':
        from telegram_notebook import enabled
        return 'Settings\nNotebook mode: '+('On' if enabled(store,participant) else 'Off')+'\nTimezone: '+prefs['timezone'],[[button('Notebook mode','notebook'),button('Timezone','timezone')],[button('Chat cleanup','chathelp'),button('Privacy & data','privacy')]]
    if op=='privacy':
        return ('Privacy & data\n\nYour notebook is separate from other users. Notes, links, metadata, questions and answers are stored on the operator’s Möbius server. The operator can technically access this data; it is not end-to-end encrypted.\n\nAI questions send note content and some earlier answers to the model provider. A small notebook may be included in full. Saved links are fetched from their websites.\n\nCleaning Telegram messages does not erase stored notes or answers. Trash keeps notes until you permanently delete them. Permanent note deletion also clears your saved AI answer history, but not Telegram, existing backups or provider-held copies. There is no automatic note expiry.',[[button('Back to settings','prefs')]])
    if op=='chathelp':
        return ('Clean Telegram chat\nSaved notes and stored answers stay.\n\nClean recent messages: review the exact number of tracked messages under 48 hours old, then confirm. Older or untracked messages are not included.\n\nFor ALL history, use Telegram’s own chat menu → Clear History (wording varies by device). The bot cannot erase the whole conversation for you. Reopen your notebook with /notes afterward.',[[button('Review recent cleanup','chatclean'),button('Cleanup status','chatstatus')],[button('Back to settings','prefs')]])
    if op in ('more','noteactions','trashmore'):
        n=notes.detail(store,participant,arg) if arg else None
        if n and bool(n['deleted_at'])!=(op=='trashmore'):
            return reader(store,participant,prefs,identity=arg)
        back='v:'+arg if n else 'trash:0' if op=='trashmore' else 'n:0'
        if op=='noteactions':
            if not n:raise Problem('Open a note first.')
            return n['title'][:200],[[button('Edit themes','themeset:'+arg),button('Move to Trash','d:'+arg)],[button('Back','more:'+arg)]]
        if op=='trashmore':
            rows=([[button('Restore note','restore:'+arg),button('Delete permanently','purge:'+arg)]] if n else [])
            rows += [[button('Trash options','trashmanage')],[button('Back',back)]]
            return 'Trash · More',rows
        rows=([[button('Note actions','noteactions:'+arg)]] if n else [])
        return 'Notes · More',rows+[[button('Find notes','filter'),button('Manage notes','manage')],[button('Back',back)]]
    if op=='trashmanage':
        data=notes.browse(store,participant,{'trash':True})
        rows=([[button('Restore all','restoreall'),button('Select notes','trashselect:0')],[button('Empty Trash','emptytrash')]] if data['total'] else [])
        return 'Trash options · '+str(data['total'])+' notes',rows+[[button('Back to Trash','trash:0'),button('Active notes','n:0')]]
    if op in ('filter','filterdates','filtertypes'):
        if op=='filterdates':return 'Date range',[[button(PERIODS[v],'period:'+v)] for v in ('all','today','7','30')]+[[button('Back','filter')]]
        if op=='filtertypes':return 'Note type',[[button(TYPES[v],'type:'+v)] for v in TYPES]+[[button('Back','filter')]]
        return f"Find notes\n{PERIODS[prefs['period']]} · {TYPES[prefs['type']]}\nTheme: {prefs['theme'] or 'All'}\nSearch: {prefs['query'] or 'None'}",[
          [button('Search','search'),button('Date','filterdates')],
          [button('Type','filtertypes'),button('Theme','themes')],
          [button('Reset filters','reset'),button('Back to notes','n:0')]]
    if op=='themes':
        themes=sorted({t for n in store.notes(participant) for t in n['tags']})
        page=max(0,int(arg or 0)) if str(arg or '0').isdigit() else 0
        rows=[[button(t,'theme:'+theme_id(t))] for t in themes[page*8:(page+1)*8]]
        if page:rows.append([button('← Previous','themes:'+str(page-1))])
        if (page+1)*8<len(themes):rows.append([button('Next →','themes:'+str(page+1))])
        return 'Themes\n'+('Choose a theme.' if themes else 'No themes yet. Open a note to add some.'),rows+[[button('All themes','theme:all'),button('Back','filter')]]
    if op in ('period','type','theme','reset'):
        patch={op:arg}
        if op=='theme':
            themes={theme_id(t):t for n in store.notes(participant) for t in n['tags']}
            if arg!='all' and arg not in themes:raise Problem('That theme changed. Open the theme list again.')
            patch={'theme':themes.get(arg,'')}
        if op=='reset':patch={'period':'all','type':'all','theme':'','query':''}
        prefs=preferences(store,participant,patch);op='n';arg='0'
    if op in ('digest','digestperiod','group','digestpage','generate'):
        if op=='digestperiod':prefs=preferences(store,participant,{'digest_period':arg})
        if op=='group':prefs=preferences(store,participant,{'group':arg})
        options={'period':prefs['digest_period'],'group':prefs['group'],'timezone':prefs['timezone']}
        if op=='generate':
            if not request_id:raise Problem('Reopen Digest to generate a summary.')
            task=store.enqueue(participant,{'kind':'digest','text':'Summarise my saved notes, grouped by '+GROUPS[prefs['group']]+'. Include save dates and citations. State if this is a limited subset.','options':options,'request_id':request_id})
            chat=store.db.execute('SELECT chat_id FROM tg_members WHERE participant=?',(participant,)).fetchone()[0]
            with store.db:store.db.execute('INSERT OR IGNORE INTO tg_requests(task_id,chat_id) VALUES (?,?)',(task['id'],chat))
            return 'Your summary is queued. You can keep saving notes while it runs.',[]
        rows,total=digest_notes(store,participant,options,time.time())
        page=max(0,min((len(rows)-1)//8,int(arg))) if op=='digestpage' and arg.isdigit() and rows else 0
        text=f"Digest · {PERIODS[prefs['digest_period']]}\nGrouped by {GROUPS[prefs['group']]} · {prefs['timezone']}\n{total} notes"+(' · showing latest 40' if total>40 else '')+'\n'
        last=None;buttons=[]
        for n in rows[page*8:(page+1)*8]:
            group=group_key(n,prefs['group'],prefs['timezone'])
            if group!=last:text+='\n'+group+'\n';last=group
            text+='• '+n['title'][:90]+(' · '+saved_at(n,prefs['timezone']) if prefs['group']!='date' else '')+'\n'

        if not rows:text+='\nNothing saved in this period. Try a wider date range.'
        nav=[]
        if page:nav.append(button('← Previous','digestpage:'+str(page-1)))
        if (page+1)*8<len(rows):nav.append(button('Next →','digestpage:'+str(page+1)))
        if nav:buttons.append(nav)
        if rows:buttons.append([button('AI summary','generate'),button('Options','digestoptions')])
        else:buttons.append([button('Options','digestoptions')])
        return text,buttons
    if op in ('digestoptions','digestdates','digestgroups'):
        if op=='digestdates':return 'Digest date range',[[button(PERIODS[v],'digestperiod:'+v)] for v in ('today','7','30','all')]+[[button('Back','digestoptions')]]
        if op=='digestgroups':return 'Group digest by',[[button(GROUPS[v],'group:'+v)] for v in GROUPS]+[[button('Back','digestoptions')]]
        return 'Digest options\n'+PERIODS[prefs['digest_period']]+' · '+GROUPS[prefs['group']],[[button('Date range','digestdates'),button('Group by','digestgroups')],[button('Back to digest','digest')]]
    if op=='manage':
        data=notes.browse(store,participant,{})
        return 'Manage notes\nCleanup moves notes to recoverable Trash. Telegram chat history stays unchanged.',[[button('Select notes','select:0'),button('Trash ('+str(data['trash_count'])+')','trash:0')],[button('Move all notes to Trash','clear')],[button('Back to notes','n:0')]]
    if op=='purgeconfirm':
        result=notes.confirm_delete(store,participant,{'confirmation':arg})
        suffix=f"Permanently deleted {result['deleted']} notes. This cannot be undone.\nSaved AI answers cleared; Telegram history is unchanged.\n\n";op='trash';arg='0'
    if op=='purgecancel':
        notes.cancel_delete(store,participant,arg);suffix='Nothing deleted.\n\n';op='trash';arg='0'
    if op in ('purge','emptytrash','purgeselected'):
        if op=='purgeselected':
            ids=json.loads(store.db.execute('SELECT selection FROM tg_members WHERE participant=?',(participant,)).fetchone()[0]);body={'ids':ids}
        else:body={'all':True} if op=='emptytrash' else {'ids':[arg]}
        result=notes.prepare_delete(store,participant,body);count=result['count'];identity=result['confirmation']
        return (f"Permanently delete {count} note"+('s' if count!=1 else '')+"?\n\nThis cannot be undone. Only your selected Trash entries are deleted, including their link metadata. Your other notes stay.\n\nAlso clears your saved AI answer history and cached previews, and stops pending requests. Telegram messages and existing backups are not erased. Notes added to Trash after this review stay.",
                [[button('Cancel','purgecancel:'+identity)],[button(f'Delete {count} permanently','purgeconfirm:'+identity)]])
    if op=='confirm':
        result=notes.confirm(store,participant,{'confirmation':arg});suffix=f"Moved {result['moved']} notes to Trash.\n\n";op='n';arg='0'
    if op in ('restore','restoreall'):
        result=notes.restore(store,participant,{'all':True} if op=='restoreall' else {'id':arg})
        suffix=f"Restored {result['restored']} notes.\n\n";op='trash';arg='0'
    if op in ('d','clear','selected'):
        if op=='selected':
            ids=json.loads(store.db.execute('SELECT selection FROM tg_members WHERE participant=?',(participant,)).fetchone()[0]);body={'ids':ids}
        else:body={'all':True} if op=='clear' else {'ids':[arg]}
        result=notes.prepare(store,participant,body)
        return result['message']+'\nNew notes saved after this review stay. Pending answers stop. Telegram history is unchanged.',[[button('Keep notes','n:0'),button(f"Move {result['count']} to Trash",'confirm:'+result['confirmation'])]]
    if op in ('n','trash','v'):
        return reader(store,participant,prefs,page=arg or 0,identity=arg if op=='v' else None,trash=op=='trash',suffix=suffix)
    if op in ('pick','trashpick'):
        parts=arg.split(':')
        if len(parts)!=3 or parts[2] not in ('0','1'):raise Problem('Reopen Notes and select again.')
        identity,page,wanted=parts;n=notes.detail(store,participant,identity)
        if bool(n['deleted_at'])!=(op=='trashpick'):raise Problem('That note moved. Reopen Notes or Trash.')
        selected=json.loads(store.db.execute('SELECT selection FROM tg_members WHERE participant=?',(participant,)).fetchone()[0]);selected=[i for i in selected if i!=identity]
        if wanted=='1':selected.append(identity)
        with store.db:store.db.execute('UPDATE tg_members SET selection=? WHERE participant=?',(json.dumps(selected),participant))
        op='trashselect' if op=='trashpick' else 'select';arg=page
    trash=op in ('trash','trashselect');select=op in ('select','trashselect')
    data=notes.browse(store,participant,{**({} if trash else prefs),'page':arg,'trash':trash})
    selected=json.loads(store.db.execute('SELECT selection FROM tg_members WHERE participant=?',(participant,)).fetchone()[0]) if select else []
    if select:
        active={r[0] for r in store.db.execute('SELECT id FROM notes WHERE participant=? AND (deleted_at IS NOT NULL)=?',(participant,int(trash)))};selected=[i for i in selected if i in active]
        with store.db:store.db.execute('UPDATE tg_members SET selection=? WHERE participant=?',(json.dumps(selected),participant))
    heading='Select from Trash' if trash and select else 'Trash' if trash else 'Select notes' if select else 'Notes'
    text=suffix+f"{heading} · {data['total']}\nNewest first · {prefs['timezone']}\n"
    if not trash and any(prefs[k]!=v for k,v in [('period','all'),('type','all'),('theme',''),('query','')]):text+=f"{PERIODS[prefs['period']]} · {TYPES[prefs['type']]} · {prefs['theme'] or 'All themes'} · {prefs['query']}\n"
    rows=[];last=None
    for n in data['notes']:
        day=saved_at(n,prefs['timezone'])
        if day!=last:text+='\n'+day+'\n';last=day
        text+='• '+n['title'][:90]+' · '+TYPES[kind(n)]+'\n'
        label=n['title'][:48] or n['original'][:48]
        rows.append([button((('✓ ' if n['id'] in selected else '○ ') if select else '')+label,(('trashpick:' if trash else 'pick:')+n['id']+':'+str(data['page'])+(':0' if n['id'] in selected else ':1')) if select else 'v:'+n['id'])])
    if not data['notes']:text+=('\nTrash is empty. Your active notes are unchanged.' if trash else '\nNo notes here. Send something to save it, or reset your filters.')
    text+=f"\nPage {data['page']+1} of {data['pages']}"
    nav=[];prefix='trashselect' if trash and select else 'trash' if trash else 'select' if select else 'n'
    if data['page']:nav.append(button('← Previous',f"{prefix}:{data['page']-1}"))
    if data['page']+1<data['pages']:nav.append(button('Next →',f"{prefix}:{data['page']+1}"))
    if nav:rows.append(nav)
    if select:
        if selected:rows.append([button(f'Delete selected ({len(selected)})' if trash else f'Trash selected ({len(selected)})','purgeselected' if trash else 'selected')])
        rows.append([button('Done selecting','trash:0' if trash else 'n:0')])
    return text,rows

ACTIONS={'more','noteactions','trashmore','trashmanage','filterdates','filtertypes','digestoptions','digestdates','digestgroups','privacy','chathelp','purge','emptytrash','purgeselected','purgeconfirm','purgecancel','trashselect','trashpick','n','trash','select','pick','selected','v','d','clear','confirm','restore','restoreall','manage','prefs','timezone','search','filter','themes','theme','themeset','period','type','reset','digest','digestperiod','digestpage','group','generate'}

from telegram_notebook import ACTIONS as NOTEBOOK_ACTIONS
ACTIONS |= NOTEBOOK_ACTIONS
