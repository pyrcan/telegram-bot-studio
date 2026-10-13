"""Assistant identities. Existing Pocket Notes data stays in its original home.

New assistants use isolated domain stores; catalog IDs, never user paths, select
those stores. Definitions/collections/releases have the same implementation.
"""
import json
import hashlib
import time
from contextlib import contextmanager, nullcontext
from assistant_store import Store, Problem, uid, required
from bot_behaviors import BLANK_DEFAULT, behavior


def development_summary(draft, live, connection, test):
    """Read-only product state shared by Studio and owner Bridge.

    Loaded is never reported as verified. Connection, owner test and published
    release are separate facts. No participant records enter this projection.
    """
    impl = behavior(draft)
    runtime = draft.get('runtime', 'notes-v1')
    uses_model = impl is None or getattr(impl, 'uses_model', False)
    label = {'notes-v1': 'Personal notebook', 'conversation-v1': 'Text assistant',
             'blank-v1': 'Not configured'}.get(runtime, 'Custom assistant')
    enabled = bool(test.get('enabled'))
    current = bool(enabled and test.get('current'))
    state = connection.get('state')
    connected = bool(connection.get('username'))
    if connection.get('error'):
        status, next_step = 'Needs attention', 'Review the connection. Your saved data is unchanged.'
    elif state == 'pairing':
        status, next_step = 'Pair owner', 'Send a one-time code to this bot from your private Telegram account.'
    elif enabled:
        status = 'Private test'
        next_step = 'Latest draft loaded. Open Telegram to test it.' if current else 'Saved changes are not in your test yet.'
    elif state == 'active':
        status, next_step = 'Live', 'Customize privately without changing the published bot.'
    elif state == 'paused':
        status, next_step = 'Paused', 'Start a private test, or resume the published bot in Settings.'
    elif connected:
        status, next_step = 'Connected', 'Start private testing, then customize while using Telegram.'
    else:
        status = 'Checking connection' if state == 'checking' else 'Not connected'
        next_step = 'Connect Telegram, then build and test privately.'
    fields = [('name', 'Name'), ('description', 'Introduction'), ('runtime', 'Functionality'),
              ('instructions', 'Instructions'), ('model', 'Model')]
    changed = [title for key,title in fields if live is None or draft.get(key) != live.get(key)]
    return {'status': status, 'next_step': next_step, 'behavior_label': label,
            'uses_model': uses_model,
            'private_only': bool(getattr(impl,'private_only',False)),
            'published_provider': ('OpenAI Codex' if live and (behavior(live) is None or getattr(behavior(live),'uses_model',False)) else 'No AI'),
            'test_provider': ('OpenAI Codex' if current and uses_model else 'No AI' if current else 'Earlier test runtime') if enabled else 'Not running',
            'test_label': ('Latest draft loaded' if current else 'Earlier draft loaded') if enabled else 'Not running',
            'release_label': ('Serving approved users' if state == 'active' else 'Published · replies paused') if live else 'Not published',
            'draft_label': 'Unpublished changes' if changed else 'Matches published version',
            'changed_fields': changed,
            'can_publish': bool(current and connected and state in ('active','testing')
                                and runtime != 'blank-v1' and not getattr(impl, 'private_only', False))}

def onboarding_preview(definition):
    """Same copy as Telegram's published profile and first admitted /start."""
    from telegram_onboarding import DESCRIPTION, welcome
    impl = behavior(definition)
    if getattr(impl,'notebook',False):impl=None
    return {'description': definition['description'] if impl else DESCRIPTION,
            'welcome': impl.welcome(definition) if impl else welcome(definition['name'])}


def release_review(draft):
    from assistant_store import DAILY_TASK_LIMIT, MAX_PENDING_TASKS
    impl = behavior(draft)
    return {'onboarding': onboarding_preview(draft),
            'uses_model': impl is None or getattr(impl, 'uses_model', False),
            'private_only': bool(getattr(impl, 'private_only', False)),
            'daily_task_limit': DAILY_TASK_LIMIT, 'pending_task_limit': MAX_PENDING_TASKS}


def starter_snapshot(identity, definition, version, generation):
    """Config-only reuse preview. No instructions or participant records leave here."""
    if definition.get('runtime', 'notes-v1') == 'blank-v1':
        return None
    identity_fields = [identity, definition, version, generation]
    revision = hashlib.sha256(json.dumps(identity_fields, sort_keys=True).encode()).hexdigest()[:24]
    impl = behavior(definition)
    return {'id': identity, 'name': definition['name'], 'description': definition['description'],
            'version': version, 'revision': revision, 'runtime': definition.get('runtime', 'notes-v1'),
            'uses_model': impl is None or getattr(impl, 'uses_model', False),
            'model': definition['model'], 'private_only': bool(getattr(impl, 'private_only', False))}

class Catalog:
    def __init__(self, root_store):
        self.root = root_store
        self.root.db.execute('CREATE TABLE IF NOT EXISTS assistants(id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, created REAL NOT NULL)')
        self.root.db.execute('CREATE TABLE IF NOT EXISTS assistant_lifecycle(id TEXT PRIMARY KEY,generation INTEGER NOT NULL,deleted_at REAL,name TEXT NOT NULL)')
        self.root.db.commit()

    def ids(self, include_deleted=False):
        ids=['pocket-notes'] + [r[0] for r in self.root.db.execute('SELECT id FROM assistants ORDER BY created,id')]
        if include_deleted:return ids
        removed={r[0] for r in self.root.db.execute('SELECT id FROM assistant_lifecycle WHERE deleted_at IS NOT NULL')}
        return [identity for identity in ids if identity not in removed]

    @contextmanager
    def open(self, identity, include_deleted=False):
        if identity not in self.ids(include_deleted):raise Problem('Assistant not found or deleted.',404)
        if identity == 'pocket-notes':
            yield self.root
            return
        store = Store(self.root.root / 'assistants' / identity)
        try: yield store
        finally: store.db.close()

    def create(self, body):
        key = required(body.get('request_id'),100)
        name = required(body.get('name'),80)
        with self.root.db:
            self.root.db.execute('BEGIN IMMEDIATE')
            prior = self.root.db.execute('SELECT id FROM assistants WHERE request_id=?',(key,)).fetchone()
            if prior:
                from bot_lifecycle import deleted
                if deleted(self.root,prior[0]):raise Problem('This create request belongs to a deleted bot. Restore it or create a new bot.',409)
                return {'id':prior[0], 'created':False}
            inherited = None
            provenance = None
            if body.get('source') is not None:
                source = body['source']
                if not isinstance(source, dict) or not isinstance(source.get('id'), str):
                    raise Problem('Choose a starting bot again.', 409)
                from bot_lifecycle import generation
                with self.open(source['id']) as origin:
                    # Pin version and definition together; root is already locked.
                    with origin.db if origin is not self.root else nullcontext():
                        if origin is not self.root: origin.db.execute('BEGIN')
                        version = origin.config('deployed')
                        definition = origin.definition(version) if version else origin.config('draft')
                        provenance = starter_snapshot(source['id'], definition, version, generation(self.root, source['id']))
                        if not provenance or source.get('revision') != provenance['revision']:
                            raise Problem('The starting bot changed. Choose it again before creating your bot.', 409)
                        inherited = {field: definition[field] for field in ('description','instructions','model','runtime')}
            identity = uid()
            # Created data is unreachable until the catalog transaction commits.
            store = Store(self.root.root / 'assistants' / identity)
            try:
                definition = {**BLANK_DEFAULT, 'id':identity, 'name':name,
                  'model':self.root.config('draft')['model']}
                if inherited: definition.update(inherited)
                for field in ('description','instructions','model','runtime'):
                    if field in body: definition[field] = body[field]
                store.set_config('draft',definition)
                store.save_draft(definition)
                if provenance: store.set_config('based_on', provenance)
            finally: store.db.close()
            self.root.db.execute('INSERT INTO assistants VALUES (?,?,?)',(identity,key,time.time()))
        return {'id':identity, 'created':True}

    def summary(self, identity):
        from bot_lifecycle import generation,deleted
        from telegram_store import public_state
        conn=public_state(self.root,identity)
        with self.open(identity,include_deleted=True) as store:
            draft=store.config('draft');version=store.config('deployed')
            live=store.definition(version) if version else None
            test=__import__('private_testing').state(store)
            return {'id':identity,'deleted':deleted(self.root,identity),'generation':generation(self.root,identity),'name':draft['name'],'description':draft['description'],
              'draft_model':draft['model'],'published_name':live['name'] if live else None,
              'published_model':live['model'] if live else None,'version':version,
              'draft_changed':not live or draft!=live,'revision':store.draft_revision(),
              'target':'local','access':'approval-only' if conn['state']=='active' else 'owner-test-only','telegram':conn['state'],'connection':conn,
              'runtime':draft.get('runtime','notes-v1'),'published_runtime':live.get('runtime','notes-v1') if live else None,
              'editing_chat':store.config('editing_chat'),
              'starter':starter_snapshot(identity,live or draft,version,generation(self.root,identity)),
              'based_on':store.config('based_on'),
              'display':__import__('bot_identity').read(self.root,identity,draft),
              # Navigation readers (including Bridge) never receive permit,
              # spending-plan or execution metadata from the private store.
              'private_test':{k:v for k,v in test.items() if k in ('enabled','epoch','revision','created','current')},
              'development':development_summary(draft,live,conn,test),
              'release_review':release_review(draft),
              'onboarding':onboarding_preview(live or draft)}

    def listing(self):
        from telegram_store import MAX_CONNECTIONS
        assistants=[item for item in (self.summary(identity) for identity in self.ids()) if not item['deleted']]
        return {'app_id': int(__import__('os').environ.get('APP_ID','0')),
          'navigation_contract': 'owner-metadata-v1',
          'assistants':assistants,
          'connection_capacity':{'used':self.root.db.execute('SELECT count(*) FROM telegram_connections').fetchone()[0],
                                 'limit':MAX_CONNECTIONS},
          'deleted':[dict(row) for row in self.root.db.execute('SELECT id,name,generation,deleted_at FROM assistant_lifecycle WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC')]}
