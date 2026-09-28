"""Synthetic stores and fake network only; no live credentials or Telegram writes."""
import json
import tempfile
import unittest
from unittest.mock import patch
from assistant_store import Store,Problem
from catalog import Catalog
from bot_lifecycle import change
from service import dispatch
import telegram_store as tg
import telegram_reconnect as reconnect
import connect_telegram as connector

class ReconnectTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Store(self.temp.name);self.cat=Catalog(self.root)
  self.old=self.cat.create({'name':'Old bot','request_id':'old'})['id']
  row=tg.reserve(self.root,self.old);tg.begin_verification(self.root,self.old,row['epoch'])
  tg.verified(self.root,self.old,{'epoch':row['epoch'],'bot_id':1000,'username':'Fixture_bot','owner_user_id':77})
  with self.cat.open(self.old) as store:
   self.revision=store.draft_revision();store.note('owner-test',{'text':'PRIVATE_OLD_SENTINEL','request_id':'fixture-note'})
  change(self.cat,self.old,{'name':'Old bot','generation':0,'revision':self.revision,'epoch':row['epoch']})
  self.new=self.cat.create({'name':'New bot','request_id':'new'})['id'];self.row=tg.reserve(self.root,self.new)
 def tearDown(self):self.root.db.close();self.temp.cleanup()
 def call(self,path,body=None):return dispatch(self.root,{'path':path,'method':'POST' if body is not None else 'GET','body':body,'actor':{'scope':'owner'}})
 def plan(self):return reconnect.plan(self.root,self.new,{'source':self.old})
 def identity(self,bot_id=1000):return {'epoch':self.row['epoch'],'bot_id':bot_id,'username':'Fixture_bot','owner_user_id':77}
 def prepare(self):return tg.prepare_identity(self.root,self.new,self.identity())
 def transfer(self,slot=None,plan=None):
  body=plan or self.plan();return reconnect.commit(self.root,self.new,{**body,'verified_slot':slot if slot is not None else body['source_slot']})
 def test_deleted_duplicate_detected_before_secret_storage(self):
  writes=[]
  def api(path,body=None,method=None):
   if path=='/api/apps/':return [{'id':18,'slug':'telegram-bridge-renamed','capability_contract':{'service':{'id':'telegram-bridge'}}}]
   if path.endswith('/pairing.json'):return {'userId':77,'chatId':77}
   if path=='/api/apps/18/service/bot-info':return {'id':2000}
   writes.append(path);raise AssertionError('No secret write permitted')
  def telegram(token,method):return {'id':1000,'is_bot':True,'username':'Fixture_bot'} if method=='getMe' else {'url':''}
  with patch.object(connector,'call',side_effect=self.call),patch.object(connector,'api',side_effect=api),patch.object(connector,'telegram',side_effect=telegram):
   with self.assertRaises(ValueError):connector.connect(self.new,self.row['epoch'],'123:synthetic_fixture_only_not_a_real_token')
  self.assertEqual(writes,[])
  state=tg.public_state(self.root,self.new);self.assertEqual(state['state'],'reserved');self.assertEqual(state['reconnect']['source'],self.old)
  self.assertIn('deleted Studio entry',state['error']);self.assertNotIn('PRIVATE_OLD_SENTINEL',json.dumps(state))
 def test_transfer_slot_identity_atomic_and_data_not_copied(self):
  self.prepare();p=self.plan();result=self.transfer()
  self.assertEqual(result['state'],'verified');self.assertEqual(result['username'],'Fixture_bot')
  current=tg.connection(self.root,self.new);old=tg.connection(self.root,self.old)
  self.assertEqual(current['slot'],p['source_slot']);self.assertEqual(old['slot'],p['target_slot'])
  self.assertIsNone(old['bot_id']);self.assertEqual(old['state'],'deleted');self.assertNotEqual(current['epoch'],p['epoch'])
  with self.cat.open(self.old,include_deleted=True) as store:
   self.assertEqual(store.db.execute('SELECT original FROM notes').fetchone()[0],'PRIVATE_OLD_SENTINEL')
  with self.cat.open(self.new) as store:
   self.assertEqual(store.notes('owner-test'),[]);self.assertIsNone(store.config('deployed'));self.assertEqual(store.config('draft')['runtime'],'blank-v1')
 def test_verified_target_slot_can_be_retained(self):
  p=self.plan();self.transfer(p['target_slot']);self.assertEqual(tg.connection(self.root,self.new)['slot'],p['target_slot'])
 def test_restore_old_entry_cannot_resume_transferred_identity(self):
  self.transfer();change(self.cat,self.old,{'name':'Old bot','generation':1},restore=True)
  restored=tg.connection(self.root,self.old);self.assertEqual(restored['state'],'reserved');self.assertIsNone(restored['bot_id'])
  with self.assertRaises(Problem):tg.activate(self.root,self.old,{'epoch':restored['epoch']})
 def test_stale_plan_cannot_transfer_after_source_restore(self):
  p=self.plan();change(self.cat,self.old,{'name':'Old bot','generation':1},restore=True)
  with self.assertRaises(Problem):self.transfer(plan=p)
  self.assertIsNone(tg.connection(self.root,self.new)['bot_id'])
 def test_stale_draft_or_target_epoch_rejected(self):
  p=self.plan()
  with self.cat.open(self.new) as store:store.save_draft({**store.config('draft'),'name':'Renamed'})
  with self.assertRaises(Problem):self.transfer(plan=p)
  p=self.plan();p['epoch']='old'
  with self.assertRaises(Problem):self.transfer(plan=p)
 def test_active_source_cannot_be_reassigned(self):
  change(self.cat,self.old,{'name':'Old bot','generation':1},restore=True)
  result=self.prepare();self.assertEqual(result['code'],'duplicate-active')
  self.assertIsNone(tg.public_state(self.root,self.new)['reconnect'])
  with self.assertRaises(Problem):self.plan()
 def test_duplicate_check_claims_new_identity_before_storage(self):
  self.assertEqual(tg.prepare_identity(self.root,self.new,self.identity(3000))['status'],'ready')
  self.assertEqual(tg.connection(self.root,self.new)['bot_id'],3000)
  tg.reset_verification(self.root,self.new,self.row['epoch'])
  self.assertIsNone(tg.connection(self.root,self.new)['bot_id'])
  self.assertEqual(tg.connection(self.root,self.new)['state'],'reserved')
 def test_app_cannot_reassign_or_spoof_failure(self):
  for action in ('reconnect','reconnect-plan','prepare-identity','setup-error'):
   with self.assertRaises(Problem) as err:dispatch(self.root,{'path':f'assistants/{self.new}/telegram/{action}','method':'POST','body':{},'actor':{'scope':'app','app_id':0}})
   self.assertEqual(err.exception.status,403)
 def test_unknown_diagnostic_code_cannot_persist_secret_text(self):
  with self.assertRaises(Problem):tg.setup_error(self.root,self.new,{'epoch':self.row['epoch'],'code':'SENSITIVE_SENTINEL'})
  self.assertNotIn('SENSITIVE_SENTINEL',json.dumps(tg.public_state(self.root,self.new)))
 def test_consumer_only_checks_identity_and_webhook_before_reconnect(self):
  calls=[]
  def inspect(slot,method):
   calls.append(method)
   if method=='getMe':return {'id':1000,'is_bot':True,'username':'Fixture_bot'}
   if method=='getWebhookInfo':return {'url':''}
   self.fail('Unexpected call')
  result=reconnect.reconnect(self.new,self.plan(),self.call,None,inspect=inspect)
  self.assertEqual(result['state'],'verified');self.assertEqual(calls,['getMe','getWebhookInfo'])
 def test_wrong_stored_identity_never_transfers(self):
  with self.assertRaises(ValueError):reconnect.reconnect(self.new,self.plan(),self.call,None,inspect=lambda *a:{'id':999,'is_bot':True,'username':'Other_bot'})
  self.assertIsNone(tg.connection(self.root,self.new)['bot_id'])
 def test_existing_webhook_never_removed_or_reassigned(self):
  def inspect(slot,method):return {'id':1000,'is_bot':True,'username':'Fixture_bot'} if method=='getMe' else {'url':'https://example.com/hook'}
  with self.assertRaises(ValueError):reconnect.reconnect(self.new,self.plan(),self.call,None,inspect=inspect)
  self.assertIsNone(tg.connection(self.root,self.new)['bot_id'])
