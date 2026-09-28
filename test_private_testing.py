"""Synthetic only: no external traffic, credentials, model calls or user records."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from assistant_store import Store,Problem
from catalog import Catalog
from bot_behaviors import CONVERSATION_DEFAULT
import private_testing as dev
import telegram_store as tg
from telegram_transport import deliver
from telegram_menu import sync
from service import dispatch

class PrivateTesting(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Store(self.temp.name);self.catalog=Catalog(self.root)
  self.id=self.catalog.create({'name':'Clean bot','request_id':'one'})['id']
  self.cm=self.catalog.open(self.id);self.live=self.cm.__enter__();tg.schema(self.live)
  self.conn=tg.reserve(self.root,self.id);tg.begin_verification(self.root,self.id,self.conn['epoch'])
  self.conn=tg.verified(self.root,self.id,{'epoch':self.conn['epoch'],'bot_id':1000,'username':'Fixture_bot','owner_user_id':77})
 def tearDown(self):self.cm.__exit__(None,None,None);self.root.db.close();self.temp.cleanup()
 def body(self):return {'epoch':self.conn['epoch'],'generation':0,'revision':self.live.draft_revision(),'test_epoch':dev.state(self.live).get('epoch')}
 def action(self,op='test'):return dev.change(self.catalog,self.id,self.body(),op)
 def message(self,n,text,user=77):return {'update_id':n,'message':{'message_id':n,'text':text,'from':{'id':user},'chat':{'id':user,'type':'private'}}}
 def intake(self,*updates):
  conn=tg.connection(self.root,self.id);tg.ingest(self.live,list(updates),connection=conn);dev.consume(self.live,conn)
 def approve_guest(self,user=88):
  tg.request_access(self.live,{'id':user,'first_name':'Guest'},77)
  tg.change_access(self.root,self.id,{'user_id':user,'operation':'allow','review':tg.access_user(self.live,user,77)['review'],**self.body()})
 def configure(self,runtime='conversation-v1'):
  self.live.save_draft({**CONVERSATION_DEFAULT,'name':'Helper','model':'fake-model','runtime':runtime},self.live.draft_revision())
 def test_blank_connection_and_private_start_without_publication(self):
  self.action();self.assertEqual(tg.connection(self.root,self.id)['state'],'testing');self.assertIsNone(self.live.config('deployed'))
  self.intake(self.message(1,'hello'),self.message(2,'hello',88))
  with dev.open_test(self.live) as test:
   self.assertEqual(test.definition()['runtime'],'blank-v1');self.assertEqual(test.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
   self.assertEqual(test.db.execute('SELECT count(*) FROM tg_members').fetchone()[0],1)
   self.assertEqual(len(test.notes('owner-test')),0)
   sent=[];deliver(test,self.root,self.id,lambda slot,method,body:sent.append(body) or {'message_id':3},live=self.live)
   self.assertTrue(sent[0]['text'].startswith('Private test ·'))
  self.assertEqual(self.live.db.execute('SELECT count(*) FROM tg_members').fetchone()[0],0)
 def test_live_guests_keep_published_and_owner_tests_new_snapshot(self):
  self.configure('notes-v1');self.live.publish();tg.activate(self.root,self.id,{'epoch':self.conn['epoch']})
  self.approve_guest();code='legacy-link';self.intake(self.message(1,'/start '+code,88))
  live_version=self.live.config('deployed');self.configure();self.action()
  self.intake(self.message(2,'hello owner'),self.message(3,'guest note',88))
  participant=self.live.db.execute('SELECT participant FROM tg_members WHERE user_id=88').fetchone()[0]
  self.assertEqual(len(self.live.notes(participant)),1)
  self.assertEqual(self.live.config('deployed'),live_version)
  with dev.open_test(self.live) as test:
   self.assertEqual(test.definition()['runtime'],'conversation-v1')
   self.assertEqual(test.db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
   self.assertEqual(test.db.execute('SELECT count(*) FROM tg_members WHERE user_id=88').fetchone()[0],0)
  self.assertEqual(len(self.live.notes('owner-test')),0)
 def test_stale_draft_confirmation_fails(self):
  before=self.body();self.configure()
  with self.assertRaises(Problem):dev.change(self.catalog,self.id,before,'test')
 def test_draft_changes_do_not_change_loaded_test(self):
  self.action();first=dev.state(self.live)['epoch'];self.configure()
  with dev.open_test(self.live) as test:self.assertEqual(test.definition()['runtime'],'blank-v1')
  self.assertFalse(dev.state(self.live)['current']);self.action()
  self.assertNotEqual(first,dev.state(self.live)['epoch'])
  with dev.open_test(self.live,first) as old:self.assertTrue(old.config('bot_deleted'))
 def test_old_update_never_replayed_into_new_test_or_live(self):
  self.configure('notes-v1');self.action();conn=tg.connection(self.root,self.id)
  tg.ingest(self.live,[self.message(1,'old private note')],connection=conn)
  self.configure();self.action();dev.consume(self.live,conn)
  with dev.open_test(self.live) as test:self.assertEqual(test.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
  self.assertEqual(self.live.notes('owner-test'),[])
 def test_pause_quiesces_test_and_does_not_publish(self):
  self.configure();self.action();self.intake(self.message(1,'hello'))
  tg.pause(self.root,self.id)
  self.assertFalse(dev.state(self.live)['enabled']);self.assertIsNone(self.live.config('deployed'))
  with dev.open_test(self.live) as test:
   self.assertTrue(test.config('bot_deleted'));self.assertEqual(test.db.execute('SELECT status FROM tasks').fetchone()[0],'cancelled')
 def test_publication_requires_current_test_and_is_exact(self):
  self.configure()
  with self.assertRaises(Problem):self.action('release')
  self.action();self.intake(self.message(1,'hello'))
  self.action('release');self.assertFalse(dev.state(self.live)['enabled'])
  self.assertEqual(self.live.definition(),self.live.config('draft'));self.assertEqual(tg.connection(self.root,self.id)['state'],'active')
  self.assertEqual(self.live.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
  self.assertTrue(tg.public_state(self.root,self.id)['owner_joined'])
 def test_blank_cannot_be_published_through_release(self):
  self.action()
  with self.assertRaises(Problem):self.action('release')
 def test_paused_guests_are_not_resumed_by_test(self):
  self.configure();self.live.publish();tg.activate(self.root,self.id,{'epoch':self.conn['epoch']});tg.pause(self.root,self.id)
  self.action();self.assertEqual(tg.connection(self.root,self.id)['state'],'testing')
  self.action('stop-test');self.assertEqual(tg.connection(self.root,self.id)['state'],'paused')
 def test_stop_private_testing_keeps_live_bot_active(self):
  self.configure();self.live.publish();tg.activate(self.root,self.id,{'epoch':self.conn['epoch']})
  self.action();self.action('stop-test');self.assertEqual(tg.connection(self.root,self.id)['state'],'active')
 def test_callback_isolation_and_size(self):
  self.configure('notes-v1');self.action();conn=tg.connection(self.root,self.id)
  cb={'update_id':1,'callback_query':{'from':{'id':77},'message':{'chat':{'type':'private','id':77}},'data':'pn:purgeconfirm:'+'a'*32}}
  self.assertEqual(dev.route(self.live,cb,conn),'discard')
  with dev.open_test(self.live) as test:
   payload=dev.payload(test,{'reply_markup':{'inline_keyboard':[[{'callback_data':cb['callback_query']['data']}]]}})
   data=payload['reply_markup']['inline_keyboard'][0][0]['callback_data'];self.assertLessEqual(len(data.encode()),64)
   cb['callback_query']['data']=data;self.assertEqual(dev.route(self.live,cb,conn),dev.state(self.live)['epoch'])
   cb['callback_query']['from']['id']=88;cb['callback_query']['message']['chat']['id']=88
   self.assertEqual(dev.route(self.live,cb,conn),'discard')
  self.action('stop-test');cb['callback_query']['from']['id']=77;cb['callback_query']['message']['chat']['id']=77
  self.assertEqual(dev.route(self.live,cb,tg.connection(self.root,self.id)),'discard')
 def test_outbox_never_sends_private_data_to_guest(self):
  self.action()
  with dev.open_test(self.live) as test:
   tg.reply(test,'malformed',88,'private material');send=[]
   deliver(test,self.root,self.id,lambda *args:send.append(args),live=self.live)
   self.assertEqual(send,[]);self.assertEqual(test.db.execute('SELECT status FROM tg_deliveries').fetchone()[0],'blocked')
 def test_commands_private_scope_and_public_description_not_draft(self):
  self.configure();self.action();calls=[]
  sync(self.live,tg.connection(self.root,self.id),lambda *args:calls.append(args))
  self.assertTrue(any(method=='setMyCommands' and body['scope']=={'type':'chat','chat_id':77} for _,method,body in calls))
  self.assertNotIn(self.live.config('draft')['description'],[body['description'] for _,method,body in calls if method=='setMyDescription'])
  self.action('release');calls.clear();sync(self.live,tg.connection(self.root,self.id),lambda *args:calls.append(args))
  self.assertTrue(any(method=='deleteMyCommands' for _,method,body in calls))
 def test_app_cannot_activate_private_test(self):
  with self.assertRaises(Problem) as ctx:dispatch(self.root,{'actor':{'scope':'app','app_id':0},'path':f'assistants/{self.id}/development/test','method':'POST','body':self.body()})
  self.assertEqual(ctx.exception.status,403)
 def test_delete_and_restore_never_revive_preview(self):
  from bot_lifecycle import change
  self.action();self.intake(self.message(1,'hi'))
  body={**self.body(),'name':self.live.config('draft')['name']}
  change(self.catalog,self.id,body)
  self.assertFalse(dev.state(self.live)['enabled'])
  change(self.catalog,self.id,{'generation':1,'name':body['name']},restore=True)
  self.assertFalse(dev.state(self.live)['enabled']);self.assertEqual(tg.connection(self.root,self.id)['state'],'paused')
 def test_private_update_replay_deduplicates_after_transfer(self):
  self.configure('notes-v1');self.action();u=self.message(5,'single private capture')
  conn=tg.connection(self.root,self.id);tg.ingest(self.live,[u],connection=conn)
  # Simulate crash after target handled the update but before intake was marked done.
  with dev.open_test(self.live) as test:tg.ingest(test,[u]);tg.consume(test,77)
  dev.consume(self.live,conn);self.intake(u)
  with dev.open_test(self.live) as test:self.assertEqual(len(test.notes('owner-test')),1)
  self.assertEqual(self.live.notes('owner-test'),[])
 def test_stale_callback_gets_ack_without_mutation(self):
  self.configure('notes-v1');self.action()
  update={'update_id':9,'callback_query':{'id':'callback-fixture','data':'pn:clear','from':{'id':77},'message':{'message_id':10,'chat':{'id':77,'type':'private'}}}}
  self.intake(update)
  with dev.open_test(self.live) as test:
   row=test.db.execute('SELECT method,body FROM tg_deliveries').fetchone()
   self.assertEqual(row['method'],'answerCallbackQuery');self.assertIn('another version',row['body'])
   self.assertEqual(test.db.execute('SELECT count(*) FROM note_cleanup').fetchone()[0],0)
 def test_repeated_test_action_same_revision_does_not_reset_history(self):
  self.configure('notes-v1');self.action();self.intake(self.message(1,'keep this test note'));epoch=dev.state(self.live)['epoch']
  self.action();self.assertEqual(epoch,dev.state(self.live)['epoch'])
  with dev.open_test(self.live) as test:self.assertEqual(len(test.notes('owner-test')),1)
 def test_live_owner_pending_work_stops_but_guest_pending_work_kept(self):
  self.configure();self.live.publish();tg.activate(self.root,self.id,{'epoch':self.conn['epoch']})
  self.approve_guest();code='legacy-link';self.intake(self.message(1,'/start '+code,88),self.message(2,'owner question'),self.message(3,'guest question',88))
  self.action()
  rows=[dict(r) for r in self.live.db.execute('SELECT participant,status FROM tasks')]
  self.assertEqual(next(r['status'] for r in rows if r['participant']=='owner-test'),'cancelled')
  self.assertEqual(next(r['status'] for r in rows if r['participant']!='owner-test'),'queued')

 def test_legacy_publication_cannot_bypass_owner_confirmation(self):
  for path in ('admin/publish','admin/apply'):
   with self.assertRaises(Problem) as ctx:dispatch(self.root,{'actor':{'scope':'app','app_id':0},'path':f'assistants/{self.id}/'+path,'method':'POST','body':{'expected_revision':self.live.draft_revision()}})
   self.assertEqual(ctx.exception.status,403)
 def test_listener_serves_owner_before_any_publication(self):
  import threading
  import telegram_transport as transport
  self.action();stop=threading.Event();delivered=threading.Event();errors=[];messages=[];polls=0;refresh=[]
  def fake(slot,method,body=None):
   nonlocal polls
   if method=='getUpdates':
    polls+=1
    if polls==1:return [self.message(1,'hello'),self.message(2,'guest message',88)]
    stop.wait(.01);return []
   if method=='sendMessage':messages.append(body);delivered.set();return {'message_id':101}
   return True
  def invoke():
   try:transport.listen(self.root.root,self.id,stop,threading.Event())
   except Exception as e:errors.append(str(e))
  with patch.object(transport,'call',side_effect=fake),patch.object(transport,'changed',side_effect=lambda:refresh.append(True)):
   thread=threading.Thread(target=invoke);thread.start()
   try:self.assertTrue(delivered.wait(3),'Private-only listener did not deliver')
   finally:stop.set();thread.join(3)
  self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
  self.assertEqual([m['chat_id'] for m in messages],[77]);self.assertTrue(messages[0]['text'].startswith('Private test ·'))
  self.assertIsNone(self.live.config('deployed'))
 def test_test_generation_uses_snapshot_not_later_draft(self):
  from worker import process_one
  self.configure();self.action();self.intake(self.message(1,'Synthetic question'))
  self.live.save_draft({**self.live.config('draft'),'instructions':'LATER_DRAFT_SENTINEL'},self.live.draft_revision())
  calls=[]
  def fake(prompt,model,alive):
   self.assertNotIn('LATER_DRAFT_SENTINEL',prompt);self.assertTrue(alive());calls.append(model);return 'Synthetic answer'
  with dev.open_test(self.live) as test:
   self.assertTrue(process_one(test,fake));tg.collect_answers(test)
  self.assertEqual(calls,['fake-model']);self.assertEqual(self.live.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)

 def test_fresh_test_does_not_import_pretest_queued_messages(self):
  self.configure('notes-v1');self.action();created=int(dev.state(self.live)['created'])
  old=self.message(1,'Old queued note');old['message']['date']=created-60
  new=self.message(2,'New private note');new['message']['date']=created+1
  self.intake(old,new)
  with dev.open_test(self.live) as test:self.assertEqual([n['original'] for n in test.notes('owner-test')],['New private note'])
