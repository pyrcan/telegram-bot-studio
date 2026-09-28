"""Deletion verification uses temporary stores only. No real bot is changed."""
import json
import os
import tempfile
import unittest
from unittest.mock import Mock,patch
from assistant_store import Store,Problem
from catalog import Catalog
from bot_lifecycle import change
from service import dispatch
import telegram_store as tg
from telegram_transport import deliver
from worker import process_one

class BotDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name);self.catalog=Catalog(self.store)
        tg.schema(self.store);self.store.publish()
        self.child=self.catalog.create({'name':'Synthetic bot','request_id':'create'})['id']
        with self.catalog.open(self.child) as child:child.publish()
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def body(self,identity):
        item=self.catalog.summary(identity)
        return {'name':item['name'],'generation':item['generation'],'revision':item['revision'],'epoch':item['connection'].get('epoch')}
    def connect(self,identity):
        c=tg.reserve(self.store,identity);tg.begin_verification(self.store,identity,c['epoch'])
        tg.verified(self.store,identity,{'epoch':c['epoch'],'bot_id':123,'username':'Synthetic_bot','owner_user_id':77})
        tg.activate(self.store,identity,{'epoch':c['epoch']});return c
    def test_delete_restore_each_store_preserves_content_and_other_bots(self):
        for identity in ('pocket-notes',self.child):
            with self.catalog.open(identity) as selected:
                selected.note('owner-test',{'text':'Synthetic private content','request_id':'note'})
                selected.set_config('editing_chat',{'id':'synthetic-owner-chat','needs_draft':False})
            before=self.catalog.summary(identity);body=self.body(identity)
            change(self.catalog,identity,body)
            self.assertNotIn(identity,self.catalog.ids());self.assertIn(identity,self.catalog.ids(True))
            self.assertEqual(len(self.catalog.listing()['assistants']),1)
            self.assertEqual(self.catalog.listing()['deleted'][0]['name'],before['name'])
            with self.assertRaises(Problem):
                with self.catalog.open(identity):pass
            with self.catalog.open(identity,include_deleted=True) as selected:
                self.assertEqual(selected.db.execute('SELECT original FROM notes').fetchone()[0],'Synthetic private content')
                self.assertEqual(selected.config('editing_chat')['id'],'synthetic-owner-chat')
                self.assertEqual(selected.draft_revision(),body['revision'])
            trash=self.catalog.listing()['deleted'][0]
            change(self.catalog,identity,trash,restore=True)
            self.assertEqual(len(self.catalog.listing()['assistants']),2)
            with self.catalog.open(identity) as selected:self.assertEqual(len(selected.notes('owner-test')),1)
    def test_deleting_root_never_stops_other_bot_or_its_tasks(self):
        self.connect(self.child)
        with self.catalog.open(self.child) as selected:
            task=selected.enqueue('owner-test',{'text':'Other bot task','request_id':'q'})
            change(self.catalog,'pocket-notes',self.body('pocket-notes'))
            self.assertEqual(tg.connection(self.store,self.child)['state'],'active')
            self.assertFalse(selected.config('bot_deleted',False))
            self.assertEqual(selected.db.execute('SELECT status FROM tasks WHERE id=?',(task['id'],)).fetchone()[0],'queued')

    def test_active_bot_stops_and_stale_activation_cannot_revive(self):
        c=self.connect(self.child);body=self.body(self.child)
        change(self.catalog,self.child,body)
        self.assertEqual(tg.connection(self.store,self.child)['state'],'deleted')
        with self.assertRaises(Problem):tg.activate(self.store,self.child,{'epoch':c['epoch']})
        trash=self.catalog.listing()['deleted'][0];change(self.catalog,self.child,trash,restore=True)
        conn=tg.connection(self.store,self.child)
        self.assertEqual(conn['state'],'paused');self.assertEqual(conn['slot'],c['slot']);self.assertNotEqual(conn['epoch'],c['epoch'])
        with self.assertRaises(Problem):tg.activate(self.store,self.child,{'epoch':c['epoch']})
        tg.activate(self.store,self.child,{'epoch':conn['epoch']})
        self.assertEqual(tg.connection(self.store,self.child)['state'],'active')
    def test_pending_work_and_invitations_do_not_replay_after_restore(self):
        self.connect(self.child)
        with self.catalog.open(self.child) as selected:
            tg.schema(selected);tg.member(selected,77,77,77)
            task=selected.enqueue('owner-test',{'text':'A pending synthetic question','request_id':'q'})
            running=selected.take_task();tg.reply(selected,'synthetic',77,'Pending reply')
            tg.invite(selected);tg.ingest(selected,[{'update_id':42,'message':{}}])
            with selected.db:selected.db.execute('INSERT INTO tg_requests(task_id,chat_id) VALUES (?,?)',(task['id'],77))
            change(self.catalog,self.child,self.body(self.child))
            self.assertFalse(selected.task_live(task['id']))
            selected.finish(running,'Late answer that must not survive',[])
            self.assertEqual(selected.db.execute('SELECT status,output FROM tasks WHERE id=?',(task['id'],)).fetchone()['status'],'cancelled')
            runner=Mock();self.assertFalse(process_one(selected,runner));runner.assert_not_called()
            send=Mock();self.assertEqual(deliver(selected,self.store,self.child,send),0);send.assert_not_called()
            self.assertEqual(selected.db.execute("SELECT count(*) FROM tg_deliveries WHERE status='pending'").fetchone()[0],0)
            self.assertEqual(selected.db.execute("SELECT count(*) FROM tg_updates WHERE status='pending'").fetchone()[0],0)
            self.assertEqual(selected.db.execute('SELECT sum(used) FROM tg_invites').fetchone()[0],1)
            change(self.catalog,self.child,self.catalog.listing()['deleted'][0],restore=True)
            self.assertFalse(process_one(selected,runner));runner.assert_not_called()
            self.assertEqual(selected.db.execute('SELECT delivered FROM tg_requests').fetchone()[0],1)
    def test_restore_does_not_revive_old_destructive_confirmation(self):
        import note_management as notes
        with self.catalog.open(self.child) as selected:
            note=selected.note('owner-test',{'text':'Keep this synthetic note','request_id':'n'})
            session=selected.issue_session('owner-test')
            with selected.db:selected.db.execute("INSERT INTO note_cleanup(id,participant,notes,expires,operation) VALUES ('old','owner-test',?,9999999999,'trash')",(json.dumps([note['id']]),))
            change(self.catalog,self.child,self.body(self.child))
            change(self.catalog,self.child,self.catalog.listing()['deleted'][0],restore=True)
            self.assertEqual(selected.db.execute("SELECT expires FROM note_cleanup WHERE id='old'").fetchone()[0],0)
            with self.assertRaises(Problem):selected.resolve(session)
            self.assertEqual(len(selected.notes('owner-test')),1)

    def test_no_confirmation_bypass_from_app_public_or_participant(self):
        for actor in ({'scope':'app','app_id':24},{'scope':'app','app_id':18},{'scope':'participant'},{'scope':'chat_embed'},{'scope':'agent','delegated':True}):
            with patch.dict(os.environ,{'APP_ID':'24'}):
                for op in ('delete','restore'):
                    with self.assertRaises(Problem):dispatch(self.store,{'actor':actor,'path':f'assistants/{self.child}/admin/{op}','method':'POST','body':self.body(self.child)})
        self.assertIn(self.child,self.catalog.ids())
    def test_deleted_routes_including_root_alias_are_closed(self):
        change(self.catalog,'pocket-notes',self.body('pocket-notes'))
        for path in ('admin/state','admin/draft','assistants/pocket-notes/admin/state','assistants/pocket-notes/telegram/activate','assistants/pocket-notes/admin/editing/prepare'):
            with self.assertRaises(Problem):dispatch(self.store,{'actor':{'scope':'owner'},'path':path,'method':'GET','body':{}})
        self.assertEqual(len(dispatch(self.store,{'actor':{'scope':'owner'},'path':'owner/catalog','method':'GET'})['assistants']),1)
    def test_stale_confirmation_cannot_delete_renamed_or_reconnected_bot(self):
        body=self.body(self.child)
        with self.catalog.open(self.child) as selected:selected.save_draft({**selected.config('draft'),'name':'Renamed'})
        with self.assertRaises(Problem):change(self.catalog,self.child,body)
        body=self.body(self.child);self.connect(self.child)
        with self.assertRaises(Problem):change(self.catalog,self.child,body)
        self.assertIn(self.child,self.catalog.ids())
    def test_retries_idempotent_but_old_consent_never_crosses_restore_cycle(self):
        body=self.body(self.child);change(self.catalog,self.child,body)
        self.assertTrue(change(self.catalog,self.child,body)['unchanged'])
        trash=self.catalog.listing()['deleted'][0];change(self.catalog,self.child,trash,restore=True)
        self.assertTrue(change(self.catalog,self.child,trash,restore=True)['unchanged'])
        with self.assertRaises(Problem):change(self.catalog,self.child,body)
        self.assertIn(self.child,self.catalog.ids())
    def test_unknown_id_and_invalid_generation_rejected(self):
        for identity,body in (('../pocket-notes',{}),(self.child,{}),(self.child,{**self.body(self.child),'generation':False})):
            with self.assertRaises(Problem):change(self.catalog,identity,body)
    def test_create_retry_does_not_resurrect_deleted_bot(self):
        change(self.catalog,self.child,self.body(self.child))
        with self.assertRaises(Problem):self.catalog.create({'name':'Synthetic bot','request_id':'create'})
        self.assertNotIn(self.child,self.catalog.ids())
    def test_connection_check_must_finish_before_delete(self):
        c=tg.reserve(self.store,self.child);tg.begin_verification(self.store,self.child,c['epoch'])
        with self.assertRaises(Problem):change(self.catalog,self.child,self.body(self.child))
        self.assertIn(self.child,self.catalog.ids())
    def test_owner_action_uses_exact_selected_identity_and_context(self):
        from owner_action import execute
        context={'assistant':self.child,'operation':'delete',**self.body(self.child)}
        with patch.dict(os.environ,{'AGENT_TOKEN':'synthetic-owner'}),patch('owner_action.call') as call:
            with patch.dict(os.environ,{},clear=False):
                old=os.environ.pop('APP_TOKEN',None)
                try:execute({'action':'bot-lifecycle','context':context,'fields':{}})
                finally:
                    if old is not None:os.environ['APP_TOKEN']=old
            call.assert_called_once_with(f'assistants/{self.child}/admin/delete',context)
