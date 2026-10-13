"""Synthetic only: no owner credentials, paid calls, real chats or Telegram sends."""
import json
import os
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from unittest.mock import Mock,patch
from assistant_store import Store,Problem
from catalog import Catalog
from service import dispatch
from worker import process_one
from bot_behaviors import CONVERSATION_DEFAULT,BEHAVIORS,Conversation
import editing_chat
import telegram_store as tg
import telegram_menu
import test_telegram as fixture

class CustomizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)
        self.catalog=Catalog(self.store)
        self.identity=self.catalog.create({'name':'Language tutor','request_id':'new'})['id']
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def test_defaults_are_general_but_existing_notes_stay_notes(self):
        self.assertEqual(self.store.config('draft')['runtime'],'notes-v1')
        with self.catalog.open(self.identity) as s:
            self.assertEqual(s.config('draft')['runtime'],'blank-v1')
            self.assertIsNone(s.config('deployed'))
            self.assertEqual(s.config('draft')['capabilities'],[])
    def test_blank_does_not_inherit_notebook_or_collect_messages_or_call_model(self):
        with self.catalog.open(self.identity) as s:
            self.assertNotIn('notes',json.dumps(s.config('draft')).lower())
            self.assertEqual(s.notes('owner-test'),[])
            s.publish()  # synthetic deliberate publication still has no functionality
            tg.schema(s)
            update=fixture.TelegramTests.update(self,1,'https://example.invalid/',77)
            tg.handle_update(s,update,77)
            self.assertEqual(s.notes('owner-test'),[])
            self.assertEqual(s.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
            s.enqueue('owner-test',{'text':'hello','request_id':'q'})
            runner=Mock();process_one(s,runner);runner.assert_not_called()

    def test_runtime_draft_requires_publication_and_keeps_pinned_task(self):
        self.store.publish();self.store.note('owner-test',{'text':'Original notebook','request_id':'n'})
        task=self.store.enqueue('owner-test',{'text':'Summarize','request_id':'q'})
        draft={**self.store.config('draft'),**CONVERSATION_DEFAULT}
        self.store.save_draft(draft,self.store.draft_revision())
        self.assertEqual(self.store.definition()['runtime'],'notes-v1')
        self.store.publish(self.store.draft_revision())
        prompts=[];process_one(self.store,lambda p,*a,**kw:prompts.append(p) or 'Fixture answer')
        self.assertIn('Original notebook',prompts[0]);self.assertEqual(len(self.store.notes('owner-test')),1)
        self.assertEqual(self.store.definition(self.store.db.execute('SELECT version FROM tasks WHERE id=?',(task['id'],)).fetchone()[0])['runtime'],'notes-v1')
    def test_unregistered_code_and_paths_rejected(self):
        for name in ('../../evil.py','https://example.invalid/code.py','unknown'):
            with self.assertRaises(Problem):self.store.save_draft({**self.store.config('draft'),'runtime':name})
        self.assertEqual(self.store.config('draft')['runtime'],'notes-v1')
    def test_reviewed_extension_has_explicit_independent_runtime_key(self):
        class Specialist(Conversation):capabilities=['text.specialist']
        with patch.dict(BEHAVIORS,{'fixture-v1':Specialist()}):
            with self.catalog.open(self.identity) as s:
                s.save_draft({**s.config('draft'),'runtime':'fixture-v1'});s.publish()
                self.assertEqual(s.definition()['capabilities'],['text.specialist'])
        self.assertEqual(self.store.config('draft')['runtime'],'notes-v1')
    def test_preparation_owner_only_including_root_route(self):
        for actor in ({'scope':'app','app_id':24},{'scope':'chat_embed'},{'scope':'participant'},{'scope':'agent','delegated':True}):
            with patch.dict(os.environ,{'APP_ID':'24'}):
                for path in ('admin/editing/prepare',f'assistants/{self.identity}/admin/editing/prepare'):
                    with self.assertRaises(Problem):dispatch(self.store,{'path':path,'method':'POST','body':{},'actor':actor})
    def test_chat_identity_is_separate_retryable_and_compare_replace(self):
        a=editing_chat.prepare(self.store,{})
        self.assertEqual(a,editing_chat.prepare(self.store,{}))
        with self.catalog.open(self.identity) as s:self.assertNotEqual(a['id'],editing_chat.prepare(s,{})['id'])
        b=editing_chat.prepare(self.store,{'replace_id':a['id']})
        self.assertNotEqual(a['id'],b['id'])
        self.assertEqual(b,editing_chat.prepare(self.store,{'replace_id':a['id']}))
        with self.assertRaises(Problem):editing_chat.ready(self.store,{'id':a['id']})
    def test_handoff_is_once_only_and_does_not_reset_on_empty_chat(self):
        chat=editing_chat.prepare(self.store,{})
        self.assertTrue(editing_chat.handoff(self.store,chat)['needs_draft'])
        editing_chat.ready(self.store,{'id':chat['id'],'needs_draft':True})
        self.assertFalse(editing_chat.handoff(self.store,chat)['needs_draft'])
        with self.assertRaises(Problem):editing_chat.handoff(self.store,{'id':'wrong'})

    def test_concurrent_prepare_creates_one_identity(self):
        barrier=threading.Barrier(2);results=[]
        def create():
            s=Store(self.tmp.name)
            try:barrier.wait();results.append(editing_chat.prepare(s,{})['id'])
            finally:s.db.close()
        ts=[threading.Thread(target=create) for _ in range(2)]
        for t in ts:t.start()
        for t in ts:t.join(5);self.assertFalse(t.is_alive())
        self.assertEqual(len(set(results)),1)
    def test_owner_action_creates_empty_chat_reuses_and_replaces_deleted(self):
        chats={};deleted=set();created=[]
        def call(path,body=None):
            if path.endswith('/state'):return self.store.state()
            return (editing_chat.prepare if path.endswith('/prepare') else editing_chat.ready)(self.store,body)
        def api(path,body=None):
            if path=='/api/chats':
                if body['id'] in deleted:raise HTTPError(path,409,'deleted',{},None)
                self.assertEqual(set(body),{'id','title'}) # no transcript writes or auto-send
                chats[body['id']]={'id':body['id'],'messages':[]};created.append(body['id']);return chats[body['id']]
            identity=path.split('/')[-1].split('?')[0]
            if identity not in chats:raise HTTPError(path,404,'missing',{},None)
            return chats[identity]
        editing_chat.ensure('pocket-notes',call,api);first=created[0]
        self.assertTrue(self.store.config('editing_chat')['needs_draft'])
        chats[first]['messages']=[{'role':'user','content':'Owner synthetic intent'}]
        editing_chat.ensure('pocket-notes',call,api)
        self.assertEqual(len(created),1);self.assertFalse(self.store.config('editing_chat')['needs_draft'])
        deleted.add(first);del chats[first]
        editing_chat.ensure('pocket-notes',call,api)
        self.assertEqual(len(created),2);self.assertNotEqual(created[0],created[1])
    def test_action_denies_app_job(self):
        from owner_action import execute
        with patch.dict(os.environ,{'APP_OWNER_ACTION':'1','APP_TOKEN':'synthetic'}):
            with self.assertRaises(ValueError):execute({'action':'customize-bot','context':{'assistant':'pocket-notes'},'fields':{}})

class ConversationTests(unittest.TestCase):
    setUp=fixture.TelegramTests.setUp
    tearDown=fixture.TelegramTests.tearDown
    update=fixture.TelegramTests.update
    handle=fixture.TelegramTests.handle
    def general(self):
        self.store.save_draft({**self.store.config('draft'),**CONVERSATION_DEFAULT});self.store.publish()
    def test_messages_and_links_are_questions_not_notes_and_replay_safe(self):
        self.general();self.handle(1,'/start');self.handle(2,'Hello there');self.handle(3,'https://example.invalid/v')
        self.handle(2,'Hello there')
        self.assertEqual(self.store.notes('owner-test'),[])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],2)
        prompts=[];process_one(self.store,lambda p,*a,**kw:prompts.append(p) or 'Synthetic reply')
        self.assertIn('Hello there',prompts[0]);self.assertNotIn('REFERENCE DATA',prompts[0])
    def test_generic_commands_never_call_model_or_save_notes(self):
        self.general()
        for i,text in enumerate(['/start','/help','/privacy','/latest','/notes','/settings','/save'],1):self.handle(i,text)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
        self.assertEqual(self.store.notes('owner-test'),[])
    def test_history_scoped_to_participant_and_release_not_notebook(self):
        self.store.note('owner-test',{'text':'OLD_NOTE_SECRET','request_id':'private'})
        self.general();self.handle(1,'Owner conversation')
        process_one(self.store,lambda *a,**kw:'OWNER_REPLY_SECRET')
        __import__('test_access').approve_fixture(self.store);self.handle(2,'/start',88);self.handle(3,'Guest message',88)
        prompts=[];process_one(self.store,lambda p,*a,**kw:prompts.append(p) or 'Guest answer')
        self.assertNotIn('OLD_NOTE_SECRET',prompts[0]);self.assertNotIn('OWNER_REPLY_SECRET',prompts[0])
        self.store.publish();self.handle(4,'Owner follow up')
        prompts=[];process_one(self.store,lambda p,*a,**kw:prompts.append(p) or 'Owner second answer')
        self.assertIn('OWNER_REPLY_SECRET',prompts[0]);self.assertNotIn('Guest answer',prompts[0])
        self.store.save_draft({**self.store.config('draft'),'instructions':'A different owner-approved purpose'});self.store.publish()
        self.handle(5,'New purpose')
        prompts=[];process_one(self.store,lambda p,*a,**kw:prompts.append(p) or 'New answer')
        self.assertNotIn('OWNER_REPLY_SECRET',prompts[0]);self.assertNotIn('Owner second answer',prompts[0])
    def test_unknown_users_cannot_use_generic_model_and_revocation_cancels(self):
        self.general();self.handle(1,'Unauthorized',88)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
        __import__('test_access').approve_fixture(self.store);self.handle(2,'/start',88);self.handle(3,'Question',88)
        person=self.store.db.execute('SELECT participant FROM tg_members WHERE user_id=88').fetchone()[0]
        self.store.revoke(person);process_one(self.store,lambda *a,**kw:self.fail('No revoked calls'))
    def test_menu_uses_published_behavior_not_draft_and_marks_ready(self):
        send=Mock();telegram_menu.sync(self.store,self.conn,send)
        self.store.save_draft({**self.store.config('draft'),**CONVERSATION_DEFAULT});send.reset_mock()
        telegram_menu.sync(self.store,self.conn,send);send.assert_not_called()
        self.store.publish();telegram_menu.sync(self.store,self.conn,send)
        commands=send.call_args_list[0].args[2]['commands']
        self.assertNotIn('notes',[x['command'] for x in commands])
        self.assertEqual(telegram_menu.contract(self.store,self.conn),self.store.config('telegram_menu'))
