"""Approval-only admission, exact owner consent and isolated user metadata.
All stores and Telegram/model adapters are synthetic.
"""
import json
import os
import unittest
from unittest.mock import patch
import test_telegram as fixture
import telegram_store as tg
from assistant_store import Problem,Store
from catalog import Catalog
from service import dispatch
from telegram_transport import deliver


def approve_fixture(store,user=88,owner=77,name='Tester'):
    """Explicit grant for unrelated participant-behavior tests, never real stores."""
    tg.request_access(store,{'id':user,'first_name':name},owner)
    catalog=Catalog(store)
    # A single-store fixture uses Pocket Notes as its owning bot.
    conn=tg.connection(store,'pocket-notes')
    if not conn:
        conn=tg.reserve(store,'pocket-notes');tg.begin_verification(store,'pocket-notes',conn['epoch'])
        conn=tg.verified(store,'pocket-notes',{'epoch':conn['epoch'],'bot_id':1000,'username':'Fixture_bot','owner_user_id':owner})
    prior=conn['state']
    with store.db:store.db.execute("UPDATE telegram_connections SET state='active' WHERE assistant='pocket-notes'")
    row=tg.access_user(store,user,owner)
    tg.change_access(store,'pocket-notes',{'user_id':user,'operation':'allow','review':row['review'],'epoch':conn['epoch'],'generation':0})
    with store.db:store.db.execute("UPDATE telegram_connections SET state=? WHERE assistant='pocket-notes'",(prior,))
    return tg.member(store,user,user,owner)['participant']

class AccessTests(fixture.TelegramTests):
 # Inherits the existing transport/owner tests as an additional regression.
 def request(self,user=88,mid=100):
  callback=self.callback(mid,'access:request',user)
  callback['callback_query']['from'].update(first_name='Synthetic tester',username='fixture_user')
  tg.handle_update(self.store,callback,self.owner)
 def body(self,user=88,operation='allow'):
  row=tg.access_user(self.store,user,self.owner)
  return {'user_id':user,'operation':operation,'review':row['review'],'epoch':self.conn['epoch'],'generation':0}
 def change(self,user=88,operation='allow'):
  return tg.change_access(self.store,'pocket-notes',self.body(user,operation))
 def test_start_discloses_identity_sharing_without_creating_request(self):
  self.handle(1,'/start',88)
  self.assertIsNone(tg.access_user(self.store,88,self.owner))
  body=json.loads(self.store.db.execute("SELECT body FROM tg_deliveries WHERE id='u1:0'").fetchone()[0])
  self.assertEqual(body['reply_markup']['inline_keyboard'][0][0]['callback_data'],'access:request')
  self.assertIn('account ID',body['text'])
 def test_explicit_request_is_idempotent_and_cannot_use_bot(self):
  self.request();self.request(mid=101);self.handle(102,'Secret note sent too early',88)
  self.assertEqual(len(tg.access_list(self.store,'pocket-notes')['users']),1)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM tg_members').fetchone()[0],0)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM notes').fetchone()[0],0)
 def test_approval_then_block_then_restore_preserves_notes_without_replaying_work(self):
  self.activate();self.request();self.change();self.handle(103,'A private note',88)
  person=tg.member(self.store,88,88,self.owner)['participant']
  self.handle(104,'What was my note?',88)
  self.change(operation='block');self.handle(105,'Not allowed to save',88)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM notes WHERE participant=?',(person,)).fetchone()[0],1)
  self.assertEqual(self.store.db.execute('SELECT status FROM tasks WHERE participant=?',(person,)).fetchone()[0],'cancelled')
  self.request(mid=106);self.assertEqual(tg.access_user(self.store,88,77)['state'],'blocked')
  self.change();self.assertEqual(tg.member(self.store,88,88,77)['participant'],person)
  self.assertEqual(len(self.store.notes(person)),1)
  self.assertEqual(self.store.db.execute('SELECT status FROM tasks WHERE participant=?',(person,)).fetchone()[0],'cancelled')
 def test_allow_notification_only_after_reviewed_approval(self):
  self.activate();self.request();before=self.body();self.change()
  with self.assertRaises(Problem):tg.change_access(self.store,'pocket-notes',before)
  sent=[];deliver(self.store,self.store,'pocket-notes',lambda slot,method,body:sent.append(body) or {'message_id':999})
  self.assertEqual(sum('access is approved' in x.get('text','') for x in sent),1)
 def test_user_metadata_has_no_content_or_session_authority(self):
  self.activate();self.request();self.change();self.handle(103,'CONTENT_SENTINEL',88)
  result=tg.access_list(self.store,'pocket-notes');raw=json.dumps(result)
  self.assertNotIn('CONTENT_SENTINEL',raw);self.assertNotIn('participant',raw);self.assertNotIn('session',raw)
  self.assertEqual(set(result['users'][0]),{'user_id','name','username','created','state','owner','review'})
 def test_changed_access_connection_and_owner_are_fenced(self):
  self.activate();self.request();before=self.body();self.change(operation='block')
  with self.assertRaises(Problem):tg.change_access(self.store,'pocket-notes',before)
  wrong={**self.body(),'epoch':'stale'}
  with self.assertRaises(Problem):tg.change_access(self.store,'pocket-notes',wrong)
  self.handle(1,'/start');ownerbody=self.body(77,'block')
  with self.assertRaises(Problem):tg.change_access(self.store,'pocket-notes',ownerbody)
 def test_paused_or_test_only_bot_cannot_allow(self):
  self.request()
  with self.assertRaises(Problem):self.change()
 def test_cross_bot_and_foreign_app_cannot_reuse_authority(self):
  self.activate();self.request();other=Catalog(self.store).create({'name':'Other','request_id':'other'})['id']
  with self.assertRaises(Problem):tg.change_access(self.store,other,self.body())
  self.store.set_config('owner_navigation_app_ids',[18])
  for actor in ({'scope':'app','app_id':18},{'scope':'participant'},{'scope':'owner','delegated':True}):
   with self.assertRaises(Problem):dispatch(self.store,{'path':'assistants/pocket-notes/access/users','method':'GET','actor':actor})
  with patch.dict(os.environ,{'APP_ID':'24'}):
   with self.assertRaises(Problem):dispatch(self.store,{'path':'assistants/pocket-notes/access/change','method':'POST','actor':{'scope':'app','app_id':24},'body':self.body()})
 def test_legacy_invite_route_and_revoke_shortcut_are_closed(self):
  for path in ('assistants/pocket-notes/telegram/invite','assistants/pocket-notes/admin/revoke'):
   with self.assertRaises(Problem):dispatch(self.store,{'path':path,'method':'POST','actor':{'scope':'owner'},'body':{}})
 def test_private_chat_sender_check_precedes_access_request(self):
  tg.handle_update(self.store,self.callback(1,'access:request',user=88,chat_id=99),self.owner)
  self.assertEqual(tg.access_list(self.store,'pocket-notes')['users'],[])
 def test_preapproval_message_does_not_become_a_note(self):
  self.activate();self.request();self.change()
  msg=self.update(107,'old message',88,date=1)
  tg.handle_update(self.store,msg,self.owner)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM notes').fetchone()[0],0)
 def test_trusted_action_routes_the_exact_review(self):
  from owner_action import execute
  ctx={'assistant':'pocket-notes','user_id':88,'operation':'allow','review':'r','epoch':'e','generation':0}
  with patch.dict(os.environ,{'AGENT_TOKEN':'synthetic-owner','CHAT_ID':'synthetic-chat'},clear=True),patch('owner_action.call') as call:
   execute({'action':'change-access','context':ctx})
   call.assert_called_once_with('assistants/pocket-notes/access/change',ctx)

if __name__=='__main__':unittest.main()
