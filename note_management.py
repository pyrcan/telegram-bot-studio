"""Participant-scoped browsing and exact-snapshot Trash and permanent deletion."""
import json
import time
from assistant_store import Problem, uid

PAGE_SIZE=8

def browse(store,participant,body,*,page_size=PAGE_SIZE,focus=None):
    store.active(participant)
    trash=bool(body.get('trash'));query=str(body.get('query') or '')[:200].strip().lower()
    rows=[dict(row) for row in store.db.execute('SELECT * FROM notes WHERE participant=? AND (deleted_at IS NOT NULL)=? ORDER BY created DESC,id DESC',(participant,int(trash)))]
    from note_views import filter_notes
    for n in rows:n['metadata']=json.loads(n['metadata']);n['tags']=json.loads(n['tags'])
    rows=filter_notes(rows,{**body,'query':query},time.time())
    total=len(rows);pages=max(1,(total+page_size-1)//page_size)
    try:page=max(0,min(pages-1,int(body.get('page',0))))
    except (ValueError,TypeError):page=0
    if focus is not None:
        position=next((i for i,n in enumerate(rows) if n['id']==focus),None)
        if position is not None:page=position//page_size
    counts=store.db.execute('SELECT sum(deleted_at IS NULL),sum(deleted_at IS NOT NULL) FROM notes WHERE participant=?',(participant,)).fetchone()
    return {'notes':rows[page*page_size:(page+1)*page_size],'page':page,'pages':pages,'total':total,'active_count':counts[0] or 0,'trash_count':counts[1] or 0,'trash':trash}

def detail(store,participant,identity):
    store.active(participant)
    row=store.db.execute('SELECT * FROM notes WHERE id=? AND participant=?',(identity,participant)).fetchone()
    if not row:raise Problem('Note not found in your collection.',404)
    result=dict(row);result['metadata']=json.loads(result['metadata']);result['tags']=json.loads(result['tags']);return result

def prepare(store,participant,body):
    store.active(participant)
    if body.get('all'):
        ids=[r[0] for r in store.db.execute('SELECT id FROM notes WHERE participant=? AND deleted_at IS NULL',(participant,))]
    else:
        requested=body.get('ids')
        if not isinstance(requested,list) or len(requested)>1000 or any(not isinstance(n,str) for n in requested):raise Problem('Choose notes to move to Trash.')
        available={r[0] for r in store.db.execute('SELECT id FROM notes WHERE participant=? AND deleted_at IS NULL',(participant,))}
        if any(n not in available for n in requested):raise Problem('The selection changed. Reload your notes.',409)
        ids=list(dict.fromkeys(requested))
    if not ids:raise Problem('No notes selected.')
    confirmation=uid()
    with store.db:
        store.db.execute('DELETE FROM note_cleanup WHERE expires<?',(time.time(),))
        store.db.execute('INSERT INTO note_cleanup(id,participant,notes,expires) VALUES (?,?,?,?)',(confirmation,participant,json.dumps(ids),time.time()+600))
    return {'confirmation':confirmation,'count':len(ids),'message':f'Move {len(ids)} note'+('s' if len(ids)!=1 else '')+' to Trash? You can restore them later.'}

def confirm(store,participant,body):
    store.active(participant)
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        action=store.db.execute('SELECT * FROM note_cleanup WHERE id=? AND participant=?',(body.get('confirmation'),participant)).fetchone()
        if not action or action['operation']!='trash' or action['expires']<time.time():raise Problem('That confirmation expired. Select the notes again.',409)
        if action['completed'] is not None:return {'moved':action['completed']}
        count=0
        for identity in json.loads(action['notes']):
            count+=store.db.execute('UPDATE notes SET deleted_at=? WHERE id=? AND participant=? AND deleted_at IS NULL',(time.time(),identity,participant)).rowcount
        store.db.execute('UPDATE note_cleanup SET completed=? WHERE id=?',(count,action['id']))
        # No late answer may use a collection captured before cleanup.
        store.db.execute("UPDATE tasks SET status='cancelled',finished=? WHERE participant=? AND status IN ('queued','running')",(time.time(),participant))
    return {'moved':count}

def restore(store,participant,body):
    store.active(participant)
    with store.db:
        if body.get('all'):
            count=store.db.execute('UPDATE notes SET deleted_at=NULL WHERE participant=? AND deleted_at IS NOT NULL',(participant,)).rowcount
        else:
            count=store.db.execute('UPDATE notes SET deleted_at=NULL WHERE participant=? AND id=? AND deleted_at IS NOT NULL',(participant,body.get('id'))).rowcount
    return {'restored':count}

def sources_available(store,participant,sources):
    return all(store.db.execute('SELECT 1 FROM notes WHERE id=? AND participant=? AND deleted_at IS NULL',(source.get('id'),participant)).fetchone() for source in sources)


def prepare_delete(store, participant, body):
    """Bind irreversible consent to exact Trash entries, including their Trash epoch."""
    store.active(participant)
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        available = {r['id']: r['deleted_at'] for r in store.db.execute(
            'SELECT id,deleted_at FROM notes WHERE participant=? AND deleted_at IS NOT NULL', (participant,))}
        if body.get('all'):
            snapshot = available
        else:
            requested = body.get('ids')
            if not isinstance(requested, list) or len(requested)>1000 or any(not isinstance(n,str) for n in requested):
                raise Problem('Select notes in Trash first.')
            if any(n not in available for n in requested):
                raise Problem('Trash changed. Review your selection again.',409)
            snapshot = {n:available[n] for n in requested}
        if not snapshot: raise Problem('No notes selected in Trash.')
        identity = uid()
        store.db.execute('DELETE FROM note_cleanup WHERE expires<?',(time.time(),))
        store.db.execute("INSERT INTO note_cleanup(id,participant,notes,expires,operation) VALUES (?,?,?,?,'purge')",
                         (identity,participant,json.dumps(snapshot),time.time()+600))
    return {'confirmation':identity,'count':len(snapshot)}


def cancel_delete(store, participant, identity):
    store.active(participant)
    with store.db:
        store.db.execute("DELETE FROM note_cleanup WHERE id=? AND participant=? AND operation='purge' AND completed IS NULL",(identity,participant))


def confirm_delete(store, participant, body):
    store.active(participant)
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        action=store.db.execute("SELECT * FROM note_cleanup WHERE id=? AND participant=? AND operation='purge'",
                                (body.get('confirmation'),participant)).fetchone()
        if not action or action['expires']<time.time():
            raise Problem('That confirmation expired or was cancelled. Review Trash again.',409)
        if action['completed'] is not None: return {'deleted':action['completed']}
        snapshot=json.loads(action['notes'])
        # Restore/re-trash invalidates the old consent rather than deleting a
        # note which the participant has since decided to keep.
        for identity,epoch in snapshot.items():
            if not store.db.execute('SELECT 1 FROM notes WHERE id=? AND participant=? AND deleted_at=?',
                                    (identity,participant,epoch)).fetchone():
                raise Problem('Trash changed. Nothing was deleted. Review Trash again.',409)
        for identity in snapshot:
            store.db.execute('INSERT OR IGNORE INTO note_tombstones SELECT participant,request_id FROM notes WHERE id=? AND participant=?',(identity,participant))
            store.db.execute('DELETE FROM notes WHERE id=? AND participant=?',(identity,participant))
        # Older answers may incorporate earlier answers without a complete
        # source graph. Clear this participant's entire saved answer history;
        # keep content-free receipts and usage counts, never other users' data.
        store.db.execute("UPDATE tasks SET status='cancelled',input='',output=NULL,sources='[]',options='{}',finished=? WHERE participant=?",(time.time(),participant))
        prefs=store.config('note-view:'+participant)
        if prefs:
            prefs.update(query='',theme='')
            store.db.execute('UPDATE config SET value=? WHERE key=?',(json.dumps(prefs),'note-view:'+participant))
        tables={r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'tg_members' in tables:
            from telegram_store import purge_cached_content
            purge_cached_content(store,participant)
        store.db.execute('UPDATE note_cleanup SET completed=?,notes=? WHERE id=?',(len(snapshot),'{}',action['id']))
    return {'deleted':len(snapshot)}
