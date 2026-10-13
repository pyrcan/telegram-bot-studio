import json
import os
import tempfile
import unittest
from unittest.mock import patch
from assistant_store import Store,Problem
from catalog import Catalog
import telegram_store as tg
from telegram_transport import deliver,TelegramError
from service import dispatch
from worker import process_one

class TelegramTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.store=Store(self.temp.name);tg.schema(self.store)
  self.owner=77;self.identity='pocket-notes';self.store.publish()
  self.conn=tg.reserve(self.store,self.identity)
  tg.begin_verification(self.store,self.identity,self.conn['epoch'])
  tg.verified(self.store,self.identity,{'epoch':self.conn['epoch'],'bot_id':1000,'username':'Fixture_bot','owner_user_id':self.owner})
 def tearDown(self):self.store.db.close();self.temp.cleanup()
 def update(self,mid,text,user=77,**extra):
  return {'update_id':mid,'message':{'message_id':mid,'text':text,'from':{'id':user,'first_name':'Tester'},'chat':{'id':user,'type':'private'},**extra}}
 def handle(self,mid,text,user=77):
  u=self.update(mid,text,user);tg.ingest(self.store,[u]);tg.consume(self.store,self.owner)
 def activate(self):tg.activate(self.store,self.identity,{'epoch':self.conn['epoch']})
 def test_verified_not_active(self):self.assertEqual(tg.connection(self.store,self.identity)['state'],'verified')
 def test_activation_requires_exact_epoch(self):
  with self.assertRaises(Problem):tg.activate(self.store,self.identity,{'epoch':'wrong'})
 def test_activation_requires_published_definition(self):
  self.store.set_config('deployed',None)
  with self.assertRaises(Problem):self.activate()
 def test_known_owner_maps_original_collection(self):
  self.handle(1,'hello');m=self.store.db.execute('SELECT participant FROM tg_members').fetchone();self.assertEqual(m[0],'owner-test')
 def test_unknown_user_cannot_capture_or_call_model(self):
  self.handle(1,'My private note',88);self.assertEqual(self.store.notes('owner-test'),[]);self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
 def test_approved_user_has_private_collection_and_link_cannot_admit_another(self):
  __import__('test_access').approve_fixture(self.store);code=tg.invite(self.store);self.handle(1,'/start '+code,88);self.handle(2,'Guest note',88)
  self.assertEqual(self.store.notes('owner-test'),[])
  self.handle(3,'/start '+code,99)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM tg_members').fetchone()[0],1)
 def test_expired_invitation_denied(self):
  code=tg.invite(self.store)
  with self.store.db:self.store.db.execute('UPDATE tg_invites SET expires=0')
  self.handle(1,'/start '+code,88);self.assertEqual(self.store.db.execute('SELECT count(*) FROM tg_members').fetchone()[0],0)
 def test_duplicate_update_saves_once(self):
  self.handle(1,'A note');self.handle(1,'A note');self.assertEqual(len(self.store.notes('owner-test')),1)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM tg_deliveries').fetchone()[0],1)
 def test_durable_offset_follows_saved_batch(self):
  tg.ingest(self.store,[self.update(50,'a'),self.update(51,'b')]);self.assertEqual(self.store.config('telegram_offset'),52)
  self.assertEqual(self.store.db.execute("SELECT count(*) FROM tg_updates WHERE status='pending'").fetchone()[0],2)
 def test_mode_replay_never_changes_question_to_note(self):
  self.handle(1,'Ask notes');u=self.update(2,'the project colour');tg.handle_update(self.store,u,self.owner);tg.handle_update(self.store,u,self.owner)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],1);self.assertEqual(self.store.notes('owner-test'),[])
 def test_save_mode_can_save_question(self):
  self.handle(1,'Save note');self.handle(2,'What is the point?');self.assertEqual(len(self.store.notes('owner-test')),1)
 def test_pasted_youtube_query_link_saves_directly(self):
  url='https://www.youtube.com/watch?v=JRKyEfBcXFQ'
  self.handle(1,url)
  self.assertEqual(self.store.notes('owner-test')[0]['url'],url)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
  delivery=json.loads(self.store.db.execute('SELECT body FROM tg_deliveries').fetchone()[0])
  self.assertTrue(delivery['text'].startswith('Saved · '));self.assertIn('UTC',delivery['text'])
  self.assertNotIn('reply_markup',delivery)
 def test_pasted_link_overrides_stale_ask_mode(self):
  self.handle(1,'Ask notes');self.handle(2,'https://www.youtube.com/watch?v=JRKyEfBcXFQ')
  self.assertEqual(len(self.store.notes('owner-test')),1)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
  self.assertIsNone(self.store.db.execute('SELECT mode FROM tg_members').fetchone()[0])
 def test_link_with_nonquestion_caption_saves(self):
  self.handle(1,'Watch later https://www.youtube.com/watch?v=JRKyEfBcXFQ')
  self.assertEqual(len(self.store.notes('owner-test')),1)
 def test_question_outside_link_still_asks(self):
  self.handle(1,'Have I saved https://www.youtube.com/watch?v=JRKyEfBcXFQ before?')
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
 def test_explicit_ask_link_still_asks(self):
  self.handle(1,'/ask https://www.youtube.com/watch?v=JRKyEfBcXFQ')
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
 def test_explicit_save_overrides_selected_ask_mode(self):
  self.handle(1,'Ask notes');self.handle(2,'/save Why is this useful?')
  self.assertEqual(len(self.store.notes('owner-test')),1)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
 def callback(self,mid,data='pn:ask',user=77,chat_id=None):
  return {'update_id':mid,'callback_query':{'id':'callback-'+str(mid),'data':data,'from':{'id':user},'message':{'message_id':9,'from':{'id':1000,'is_bot':True},'chat':{'id':user if chat_id is None else chat_id,'type':'private'}}}}
 def test_inline_action_and_ack(self):
  self.activate();tg.ingest(self.store,[self.callback(1)]);tg.consume(self.store,self.owner)
  self.assertEqual(self.store.db.execute('SELECT mode FROM tg_members').fetchone()[0],'ask')
  sent=[]
  def fake(slot,method,body):sent.append((method,body));return True if method=='answerCallbackQuery' else {'message_id':10}
  deliver(self.store,self.store,self.identity,fake)
  self.assertEqual(sent[0][0],'answerCallbackQuery');self.assertIn('inline_keyboard',sent[1][1]['reply_markup'])
  self.assertEqual(self.store.db.execute("SELECT count(*) FROM tg_deliveries WHERE status='sent'").fetchone()[0],2)
 def test_inline_digest_deduplicates_and_stays_in_collection(self):
  self.store.publish();u=self.callback(1,'pn:generate');tg.ingest(self.store,[u]);tg.consume(self.store,self.owner);tg.ingest(self.store,[u]);tg.consume(self.store,self.owner)
  tasks=self.store.db.execute('SELECT participant,kind FROM tasks').fetchall();self.assertEqual(len(tasks),1);self.assertEqual(tuple(tasks[0]),('owner-test','digest'))
 def test_inline_other_chat_rejected(self):
  tg.handle_update(self.store,self.callback(1,user=88,chat_id=77),self.owner)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM tg_members').fetchone()[0],0)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
 def test_inline_unknown_or_revoked_user_cannot_ask(self):
  tg.handle_update(self.store,self.callback(1,'pn:digest',user=88),self.owner)
  self.handle(2,'hi');self.store.revoke('owner-test');tg.handle_update(self.store,self.callback(3,'pn:digest'),self.owner)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
 def test_legacy_keyboard_removed_once_on_next_interaction(self):
  self.handle(1,'hi')
  with self.store.db:self.store.db.execute('UPDATE tg_members SET controls_version=0')
  self.handle(2,'New note');self.handle(3,'Another note')
  payloads=[json.loads(r[0]) for r in self.store.db.execute('SELECT body FROM tg_deliveries')]
  self.assertEqual(sum(p.get('reply_markup',{}).get('remove_keyboard',False) for p in payloads),1)
  self.assertEqual(len(self.store.notes('owner-test')),2)
 def test_expired_callback_ack_does_not_break_connection(self):
  self.activate();tg.queue_delivery(self.store,'ack',77,{'callback_query_id':'expired'},'answerCallbackQuery')
  def expired(*args):raise TelegramError(400)
  deliver(self.store,self.store,self.identity,expired)
  self.assertIsNone(tg.connection(self.store,self.identity)['error'])
 def test_hi_no_model_task(self):
  self.handle(1,'hi');self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
 def test_group_ignored(self):
  u=self.update(1,'private',chat={'type':'group','id':-1});tg.handle_update(self.store,u,self.owner);self.assertEqual(self.store.db.execute('SELECT count(*) FROM tg_members').fetchone()[0],0)
 def test_revoked_user_cannot_rejoin(self):
  self.handle(1,'hi');self.store.revoke('owner-test');self.handle(2,'/start');self.assertEqual(self.store.db.execute('SELECT active FROM participants WHERE id=?',('owner-test',)).fetchone()[0],0)
 def test_pause_cancels_telegram_not_web_tasks(self):
  self.activate();self.handle(1,'What do I know?');self.store.enqueue('guest-test',{'text':'Web test','request_id':'web'});tg.pause(self.store,self.identity)
  rows={r['request_id']:r['status'] for r in self.store.db.execute('SELECT request_id,status FROM tasks')}
  self.assertEqual(rows['telegram:1'],'cancelled');self.assertEqual(rows['web'],'queued')
 def test_no_delivery_before_activation(self):
  self.handle(1,'hi');calls=[];deliver(self.store,self.store,self.identity,lambda *args:calls.append(args));self.assertFalse(calls)
 def test_definite_delivery_once(self):
  self.activate();self.handle(1,'a note');calls=[]
  def fake(*args):calls.append(args);return {'message_id':5}
  deliver(self.store,self.store,self.identity,fake);deliver(self.store,self.store,self.identity,fake);self.assertEqual(len(calls),1)
  timing=tg.public_state(self.store,self.identity)['timing']['delivery']
  self.assertGreaterEqual(timing['outbox_ms'],0);self.assertGreaterEqual(timing['send_ms'],0)
  self.assertNotIn('a note',json.dumps(timing))
 def test_ambiguous_delivery_not_repeated(self):
  self.activate();self.handle(1,'a note');calls=[]
  def fake(*args):calls.append(args);raise TelegramError()
  deliver(self.store,self.store,self.identity,fake);deliver(self.store,self.store,self.identity,fake)
  self.assertEqual(len(calls),1);self.assertEqual(self.store.db.execute('SELECT status FROM tg_deliveries').fetchone()[0],'uncertain')
 def test_rate_limit_backoff_preserves_delivery(self):
  self.activate();self.handle(1,'a note')
  def fake(*args):raise TelegramError(429,10)
  deliver(self.store,self.store,self.identity,fake);r=self.store.db.execute('SELECT * FROM tg_deliveries').fetchone();self.assertEqual(r['status'],'pending');self.assertGreater(r['retry_at'],r['created'])
 def test_late_answer_not_delivered_to_revoked_user(self):
  self.activate();self.handle(1,'What did I save?');process_one(self.store);tg.collect_answers(self.store);self.store.revoke('owner-test');calls=[]
  deliver(self.store,self.store,self.identity,lambda *args:calls.append(args));self.assertFalse(calls)
 def test_empty_question_end_to_end_without_model(self):
  self.activate();self.handle(1,'What did I save?')
  process_one(self.store,lambda *a,**k:self.fail('empty collection needs no model'));tg.collect_answers(self.store)
  calls=[];deliver(self.store,self.store,self.identity,lambda slot,method,body:(calls.append(body) or {'message_id':9}))
  self.assertTrue(any('No saved notes' in b['text'] for b in calls))
 def test_invite_requires_owner_trying_bot_first(self):
  self.activate()
  with self.assertRaises(Problem):dispatch(self.store,{'path':'assistants/pocket-notes/telegram/invite','method':'POST','body':{},'actor':{'scope':'agent'}})
 def test_foreign_app_cannot_activate(self):
  self.store.set_config('owner_navigation_app_ids',[18])
  with self.assertRaises(Problem):dispatch(self.store,{'path':'assistants/pocket-notes/telegram/activate','method':'POST','body':{'epoch':self.conn['epoch']},'actor':{'scope':'app','app_id':18}})
 def test_no_second_sealed_verifier(self):
  other=Catalog(self.store).create({'name':'Second','request_id':'second'})['id'];row=tg.reserve(self.store,other);tg.begin_verification(self.store,other,row['epoch'])
  with self.assertRaises(Problem):tg.begin_verification(self.store,other,row['epoch'])
 def test_stale_failed_verifier_cannot_reset_verified(self):
  tg.reset_verification(self.store,self.identity,self.conn['epoch']);self.assertEqual(tg.connection(self.store,self.identity)['state'],'verified')
 def test_atomic_apply_keeps_release_and_draft_in_sync(self):
  d=self.store.config('draft');revision=self.store.draft_revision();self.store.apply_definition({**d,'name':'Changed'},revision)
  self.assertEqual(self.store.definition(),self.store.config('draft'))
  with self.assertRaises(Problem):self.store.apply_definition(d,revision)


class TelegramGatewayContractTests(unittest.TestCase):
 def test_own_job_slot_poll_options_pass_as_data(self):
  import os
  from unittest.mock import patch
  import telegram_transport as transport
  params={'offset':42,'timeout':5,'allowed_updates':['message']}
  with patch.dict(os.environ,{'API_BASE_URL':'http://localhost:8080','APP_ID':'24','APP_TOKEN':'synthetic-scoped-token'}),patch.object(transport,'call_saved',return_value=[]) as saved:
   self.assertEqual(transport.call(1,'getUpdates',params),[])
   saved.assert_called_once_with('tg-1','getUpdates',params,app_id='24',bearer='synthetic-scoped-token',api_base='http://localhost:8080')
 def test_own_job_reply_unicode_and_keyboard_pass_without_url_encoding(self):
  import os
  from unittest.mock import patch
  import telegram_transport as transport
  keyboard={'keyboard':[[{'text':'Ask notes'}]],'resize_keyboard':True}
  params={'chat_id':7,'text':'A & B? café','reply_markup':keyboard}
  with patch.dict(os.environ,{'API_BASE_URL':'http://localhost:8080','APP_ID':'24','APP_TOKEN':'synthetic-scoped-token'}),patch.object(transport,'call_saved',return_value={'message_id':9}) as saved:
   self.assertEqual(transport.call(1,'sendMessage',params),{'message_id':9})
   self.assertEqual(saved.call_args.args,('tg-1','sendMessage',params))

class PollHealthTests(unittest.TestCase):
 def test_management_health_contains_only_transport_shape_and_timing(self):
  import tempfile
  import telegram_transport as transport
  from assistant_store import Store
  from catalog import Catalog
  import telegram_store as tg
  with tempfile.TemporaryDirectory() as tmp:
   store=Store(tmp)
   try:
    identity=Catalog(store).create({'name':'Health fixture','request_id':'health'})['id']
    tg.reserve(store,identity)
    transport.poll_succeeded(store,identity,{'seconds':5.002,'result_type':'list','received_updates':False})
    state=tg.public_state(store,identity)
    self.assertEqual(state['poll_health'],{'seconds':5.002,'result_type':'list','received_updates':False})
    self.assertGreater(state['last_seen'],0)
   finally:store.db.close()

class ConcurrentDeliveryTests(unittest.TestCase):
 setUp=TelegramTests.setUp
 tearDown=TelegramTests.tearDown
 update=TelegramTests.update
 handle=TelegramTests.handle
 activate=TelegramTests.activate
 def test_two_connections_cannot_send_the_same_claimed_delivery(self):
  import threading
  self.activate();self.handle(1,'one synthetic note')
  barrier=threading.Barrier(2);entered=threading.Event();release=threading.Event();calls=[];errors=[]
  def send(slot,method,body):
   calls.append(method);entered.set()
   if not release.wait(3):raise RuntimeError('Test release missing')
   return {'message_id':123}
  def worker():
   store=Store(self.temp.name)
   try:
    tg.schema(store);barrier.wait(2);deliver(store,store,self.identity,send=send)
   except Exception as exc:errors.append(exc)
   finally:store.db.close()
  threads=[threading.Thread(target=worker) for _ in range(2)]
  for thread in threads:thread.start()
  try:self.assertTrue(entered.wait(2))
  finally:release.set()
  for thread in threads:thread.join(4);self.assertFalse(thread.is_alive())
  self.assertEqual(errors,[]);self.assertEqual(calls,['sendMessage'])
  self.assertEqual(self.store.db.execute("SELECT status FROM tg_deliveries WHERE id='u1:0'").fetchone()[0],'sent')

class InvalidSlotTests(unittest.TestCase):
 def test_invalid_slot_never_reads_any_secret(self):
  from unittest.mock import patch
  import telegram_transport as transport
  for slot in (0,9,True,'../owner'):
   with patch.object(transport,'call_saved') as call:
    with self.assertRaises(transport.TelegramError):transport.call(slot,'getMe')
    call.assert_not_called()
