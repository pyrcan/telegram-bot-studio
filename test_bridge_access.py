"""One explicitly reviewed scope; all stores/transport are synthetic."""
import os
import unittest
from unittest.mock import patch
import test_telegram as fixture
import telegram_store as tg
from assistant_store import Problem
from catalog import Catalog
from service import dispatch,connect_access_bridge,access_bot_handle

class BridgeAccessTests(unittest.TestCase):
 activate=fixture.TelegramTests.activate
 def setUp(self):
  fixture.TelegramTests.setUp(self);self.activate()
  self.grant={'scope':'owned-bots-v1','app_id':18,'grant':'a'*12,'owner_user_id':77,'binding':'b'*64}
  connect_access_bridge(self.store,self.grant)
  self.handle=access_bot_handle(self.grant['grant'],'pocket-notes',self.conn['epoch'],0)
  self.env=patch.dict(os.environ,{'APP_ID':'24'});self.env.start()
  self.binding=patch('service.access_bridge_binding');self.binding.start()
  tg.request_access(self.store,{'id':88,'first_name':'Guest'},77)
 def tearDown(self):
  self.binding.stop();self.env.stop();fixture.TelegramTests.tearDown(self)
 def call(self,action='users',body=None,actor=None):
  suffix='bots' if action=='bots' else self.handle+'/'+action
  return dispatch(self.store,{'path':'bridge-access/'+self.grant['grant']+'/'+suffix,'method':'POST' if body is not None else 'GET','body':body,'actor':actor or {'scope':'app','app_id':18}})
 def changebody(self):
  data=self.call();user=data['users'][0]
  return {'user_id':88,'operation':'allow','review':user['review'],'epoch':data['epoch'],'generation':data['generation']}
 def test_shared_domain_and_stale_review(self):
  body=self.changebody();self.call('change',body)
  self.assertEqual(tg.access_list(self.store,'pocket-notes')['users'][0]['state'],'allowed')
  with self.assertRaises(Problem):self.call('change',body)
  self.assertEqual(self.store.db.execute("SELECT count(*) FROM tg_deliveries WHERE id LIKE 'access:%'").fetchone()[0],1)
 def test_wrong_app_owner_participant_delegation_cannot_use(self):
  for actor in ({'scope':'app','app_id':19},{'scope':'owner'},{'scope':'participant'},{'scope':'app','app_id':18,'delegated':True}):
   with self.assertRaises(Problem):self.call(actor=actor)
 def test_generic_app_routes_remain_denied(self):
  for action in ('admin/state','access/users','access/change','admin/session'):
   with self.assertRaises(Problem):dispatch(self.store,{'path':'assistants/pocket-notes/'+action,'method':'GET','actor':{'scope':'app','app_id':18}})
  with self.assertRaises(Problem):self.call('notes')
 def test_pairing_check_failure_denies_read_and_change(self):
  body=self.changebody()
  with patch('service.access_bridge_binding',side_effect=Problem('Pair changed',403)):
   for action,data in (('users',None),('change',body)):
    with self.assertRaises(Problem):self.call(action,data)
 def test_disconnect_and_reconnect_fences(self):
  body=self.changebody()
  connect_access_bridge(self.store,{'app_id':18,'grant':self.grant['grant'],'operation':'disconnect'})
  with self.assertRaises(Problem):self.call('change',body)
  connect_access_bridge(self.store,{**self.grant,'grant':'c'*12})
  with self.assertRaises(Problem):self.call()
 def test_changed_epoch_fences_controls_but_not_owner_connection(self):
  self.store.db.execute("UPDATE telegram_connections SET epoch='changed'");self.store.db.commit()
  with self.assertRaises(Problem):self.call()
  bots=self.call('bots')['bots'];self.assertEqual(len(bots),1)
  self.assertNotEqual(bots[0]['grant'],self.handle)
  self.handle=bots[0]['grant'];self.assertEqual(self.call()['epoch'],'changed')
 def test_unauthorized_app_cannot_create_grant(self):
  for actor in ({'scope':'app','app_id':24},{'scope':'app','app_id':18}):
   with self.assertRaises(Problem):dispatch(self.store,{'path':'owner/access-bridge','method':'POST','body':self.grant,'actor':actor})
 def test_read_projection_no_private_content(self):
  data=self.call()
  self.assertEqual(set(data),{'users','policy','epoch','generation','assistant','notifications'})
  self.assertEqual(set(data['users'][0]),{'user_id','name','username','created','state','owner','review'})
 def test_old_per_bot_approval_is_not_promoted(self):
  legacy={'assistant':'pocket-notes','app_id':18,'grant':'d'*12,'epoch':self.conn['epoch'],'generation':0,'owner_user_id':77,'binding':'b'*64}
  with self.assertRaises(Problem):connect_access_bridge(self.store,legacy)
  self.store.set_config('bridge-access:'+legacy['grant'],legacy)
  self.store.set_config('bridge-access-current:18:pocket-notes',legacy['grant'])
  with self.assertRaises(Problem):dispatch(self.store,{'path':'bridge-access/'+legacy['grant']+'/users','method':'GET','actor':{'scope':'app','app_id':18}})
 def test_future_bot_included_only_if_published_and_owned(self):
  catalog=Catalog(self.store)
  for owner in (77,99):
   identity=catalog.create({'name':'Future','request_id':'future-'+str(owner)})['id']
   conn=tg.reserve(self.store,identity);tg.begin_verification(self.store,identity,conn['epoch'])
   tg.verified(self.store,identity,{'epoch':conn['epoch'],'bot_id':owner+2000,'username':'Future_bot','owner_user_id':owner})
   self.assertNotIn(identity,[b['assistant'] for b in self.call('bots')['bots']])
   with catalog.open(identity) as child:child.publish()
   ids=[b['assistant'] for b in self.call('bots')['bots']]
   self.assertEqual(identity in ids,owner==77)
 def test_notification_preference_is_per_bot_and_replay_safe(self):
  self.assertTrue(self.call()['notifications'])
  for _ in range(2):self.call('notifications',{'enabled':False})
  self.assertFalse(self.call()['notifications'])
  self.assertEqual(tg.access_user(self.store,88,77)['state'],'pending')
  self.call('notifications',{'enabled':True});self.assertTrue(self.call()['notifications'])
  with self.assertRaises(Problem):self.call('notifications',{'enabled':'yes'})
 def test_owner_change_excludes_bot_and_blocks_old_handle(self):
  self.store.db.execute('UPDATE telegram_connections SET owner_user_id=99');self.store.db.commit()
  self.assertEqual(self.call('bots')['bots'],[])
  with self.assertRaises(Problem):self.call()
 def test_lifecycle_change_invalidates_handle(self):
  self.store.db.execute("INSERT INTO assistant_lifecycle(id,generation,deleted_at,name) VALUES ('pocket-notes',1,NULL,'Pocket Notes')");self.store.db.commit()
  with self.assertRaises(Problem):self.call()
 def test_revoke_between_read_and_write_is_rechecked_under_lock(self):
  body=self.changebody();original=tg.change_access
  def change(*args,**kwargs):
   connect_access_bridge(self.store,{'app_id':18,'grant':self.grant['grant'],'operation':'disconnect'})
   return original(*args,**kwargs)
  with patch('telegram_store.change_access',side_effect=change):
   with self.assertRaises(Problem):self.call('change',body)
  self.assertEqual(tg.access_user(self.store,88,77)['state'],'pending')

 def test_alert_preference_survives_same_owner_reconnection(self):
  self.call('notifications',{'enabled':False})
  connect_access_bridge(self.store,{'app_id':18,'grant':self.grant['grant'],'operation':'disconnect'})
  self.grant={**self.grant,'grant':'c'*12};connect_access_bridge(self.store,self.grant)
  self.handle=access_bot_handle(self.grant['grant'],'pocket-notes',self.conn['epoch'],0)
  self.assertFalse(self.call()['notifications'])
 def test_deleted_bot_is_excluded_and_old_controls_denied(self):
  self.store.db.execute("INSERT INTO assistant_lifecycle(id,generation,deleted_at,name) VALUES ('pocket-notes',1,1,'Pocket Notes')");self.store.db.commit()
  self.assertEqual(self.call('bots')['bots'],[])
  with self.assertRaises(Problem):self.call()
