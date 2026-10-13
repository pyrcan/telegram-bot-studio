"""Synthetic owner pairing; no live Telegram account, key, or messages."""
import json
import os
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

from assistant_store import Store,Problem
from catalog import Catalog
from bot_lifecycle import change as lifecycle_change
from service import dispatch
import telegram_store as tg
import telegram_transport as transport


class OwnerPairingTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Store(self.temp.name)
  self.identity=Catalog(self.root).create({'name':'Fixture','request_id':'pairing'})['id']
  self.conn=tg.reserve(self.root,self.identity)
  self.assertEqual(tg.prepare_identity(self.root,self.identity,{'epoch':self.conn['epoch'],'bot_id':1000,'username':'Fixture_bot'})['status'],'ready')
  tg.await_owner(self.root,self.identity,{'epoch':self.conn['epoch']})
 def tearDown(self):self.root.db.close();self.temp.cleanup()
 def update(self,code,user=77,*,chat_type='private',chat_id=None,date=None):
  return {'update_id':101,'message':{'text':'/pair '+code,'date':int(time.time()) if date is None else date,
   'from':{'id':user,'is_bot':False},'chat':{'id':user if chat_id is None else chat_id,'type':chat_type}}}
 def issue(self):return tg.issue_pairing(self.root,self.identity,{'epoch':self.conn['epoch']})['code']
 def test_code_is_hashed_not_returned_in_state_and_cannot_activate(self):
  code=self.issue();state=tg.public_state(self.root,self.identity)
  self.assertEqual(state['state'],'pairing');self.assertNotIn(code,json.dumps(state))
  listed=Catalog(self.root).summary(self.identity)
  self.assertEqual(listed['development']['status'],'Pair owner')
  self.assertIn('one-time code',listed['development']['next_step'])
  self.assertNotIn(code,json.dumps(tg.connection(self.root,self.identity)))
  with self.assertRaises(Problem):tg.activate(self.root,self.identity,{'epoch':self.conn['epoch']})
  with self.assertRaises(Problem):tg.pause(self.root,self.identity)
 def test_private_verified_sender_becomes_owner_once(self):
  code=self.issue();self.assertTrue(tg.accept_pairing(self.root,self.identity,self.conn['epoch'],self.update(code)))
  row=tg.connection(self.root,self.identity)
  self.assertEqual((row['state'],row['owner_user_id']),('verified',77))
  self.assertFalse(tg.accept_pairing(self.root,self.identity,self.conn['epoch'],self.update(code,user=88)))
  self.assertEqual(tg.connection(self.root,self.identity)['owner_user_id'],77)
 def test_deleted_unpaired_bot_restores_to_pairing_without_old_code(self):
  code=self.issue()
  with Catalog(self.root).open(self.identity) as selected:
   name=selected.config('draft')['name'];revision=selected.draft_revision()
  lifecycle_change(Catalog(self.root),self.identity,{'name':name,'generation':0,'revision':revision,'epoch':self.conn['epoch']})
  lifecycle_change(Catalog(self.root),self.identity,{'name':name,'generation':1},restore=True)
  row=tg.connection(self.root,self.identity)
  self.assertEqual(row['state'],'pairing');self.assertIsNone(row['owner_user_id'])
  self.assertFalse(tg.accept_pairing(self.root,self.identity,row['epoch'],self.update(code)))
  with self.assertRaises(Problem):tg.activate(self.root,self.identity,{'epoch':row['epoch']})
 def test_group_forward_bot_and_stale_or_wrong_code_rejected(self):
  old=self.issue();code=self.issue()
  variants=[self.update(old),self.update(code,chat_type='group'),self.update(code,chat_id=88),
            self.update(code,date=int(time.time())-60),self.update(code[:-1]+('X' if code[-1]!='X' else 'Y'))]
  forwarded=self.update(code);forwarded['message']['sender_chat']={'id':77};variants.append(forwarded)
  bot=self.update(code);bot['message']['from']['is_bot']=True;variants.append(bot)
  for update in variants:self.assertFalse(tg.accept_pairing(self.root,self.identity,self.conn['epoch'],update))
  self.assertIsNone(tg.connection(self.root,self.identity)['owner_user_id'])
 def test_expired_code_rejected(self):
  code=self.issue()
  with self.root.db:self.root.db.execute("UPDATE telegram_connections SET setup=? WHERE assistant=?",(json.dumps({'pairing_hash':__import__('hashlib').sha256(code.encode()).hexdigest(),'pairing_expires':time.time()-1,'pairing_issued':time.time()-20}),self.identity))
  self.assertFalse(tg.accept_pairing(self.root,self.identity,self.conn['epoch'],self.update(code)))
 def test_service_is_app_local_and_cannot_serve_public_or_foreign_app(self):
  path=f'assistants/{self.identity}/telegram/pairing-code';body={'epoch':self.conn['epoch']}
  with patch.dict(os.environ,{'APP_ID':'24'}):
   for actor in ({'scope':'app','app_id':18},{'scope':'participant'},{'scope':'agent','delegated':True}):
    with self.assertRaises(Problem):dispatch(self.root,{'path':path,'method':'POST','body':body,'actor':actor})
   issued=dispatch(self.root,{'path':path,'method':'POST','body':body,'actor':{'scope':'app','app_id':24}})
   with self.assertRaises(Problem):dispatch(self.root,{'path':f'assistants/{self.identity}/telegram/verified','method':'POST','body':{'epoch':self.conn['epoch'],'owner_user_id':77,'bot_id':1000,'username':'Fixture_bot'},'actor':{'scope':'owner'}})
  self.assertEqual(len(issued['code']),24)
 def test_listener_discards_nonpairing_updates_and_checkpoints_before_admission(self):
  code=self.issue();stop=threading.Event();calls=[]
  def fake_call(slot,method,params):
   calls.append((slot,method,params))
   return [{'update_id':100,'message':{'text':'private ignored','date':int(time.time()),'from':{'id':99},'chat':{'id':99,'type':'private'}}},self.update(code)]
  original=tg.accept_pairing
  def checked(root,identity,epoch,update):
   with Catalog(root).open(identity) as store:self.assertEqual(store.config('telegram_offset'),102)
   return original(root,identity,epoch,update)
  with patch.object(transport,'call',side_effect=fake_call),patch.object(tg,'accept_pairing',side_effect=checked),patch.object(transport,'changed'):
   transport.listen_pairing(self.root.root,self.identity,stop,threading.Event())
  with Catalog(self.root).open(self.identity) as store:
   self.assertEqual(store.config('telegram_offset'),102)
   self.assertEqual(store.db.execute('SELECT count(*) FROM tg_updates').fetchone()[0],0)
   self.assertEqual(store.db.execute('SELECT count(*) FROM tg_deliveries').fetchone()[0],0)
  self.assertEqual([c[1] for c in calls],['getUpdates'])
  self.assertEqual(tg.connection(self.root,self.identity)['state'],'verified')


if __name__=='__main__':unittest.main()
