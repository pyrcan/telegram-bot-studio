import json
import os
import tempfile
import unittest
from unittest.mock import patch
from assistant_store import Store, Problem
from catalog import Catalog
from service import dispatch
from worker import process_one

class OwnerManagementTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)
  self.env=patch.dict(os.environ,{'APP_ID':'24'});self.env.start()
 def tearDown(self):self.store.db.close();self.tmp.cleanup();self.env.stop()
 def call(self,path,body=None,actor=None,**extra):
  return dispatch(self.store,{'path':path,'method':'POST' if body is not None else 'GET','body':body,'actor':actor or {'scope':'agent'},**extra})
 def create(self):return self.call('owner/create',{'name':'Research','request_id':'new'})['id']
 def test_create_idempotent_and_separate(self):
  identity=self.create();self.assertEqual(identity,self.create())
  self.store.note('owner-test',{'text':'PRIVATE','request_id':'x'})
  with Catalog(self.store).open(identity) as new:
   self.assertEqual(new.notes('owner-test'),[]);self.assertEqual(new.config('draft')['id'],identity)
   self.assertIsNone(new.config('deployed'))
 def test_identity_survives_edit_and_release(self):
  identity=self.create();prefix=f'assistants/{identity}/admin/'
  state=self.call(prefix+'state');draft={**state['draft'],'name':'Different','expected_revision':state['revision']}
  self.call(prefix+'draft',draft)
  state=self.call(prefix+'state');self.call(prefix+'publish',{'expected_revision':state['revision']})
  self.assertEqual(self.call(prefix+'state')['published']['id'],identity)
  self.assertIsNone(self.store.config('deployed'))
 def test_stale_save_and_publish_rejected(self):
  state=self.call('admin/state');draft={**state['draft'],'name':'New','expected_revision':state['revision']};self.call('admin/draft',draft)
  for path,body in [('admin/draft',draft),('admin/publish',{'expected_revision':state['revision']})]:
   with self.assertRaises(Problem) as err:self.call(path,body)
   self.assertEqual(err.exception.status,409)
 def test_publish_repeated_is_same_version(self):
  revision=self.call('admin/state')['revision']
  one=self.call('admin/publish',{'expected_revision':revision});two=self.call('admin/publish',{'expected_revision':revision})
  self.assertEqual(one['version'],two['version']);self.assertEqual(len(self.store.state()['versions']),1)
 def test_missing_revision_rejected(self):
  with self.assertRaises(Problem):self.call('admin/draft',self.store.config('draft'))
 def test_foreign_denied_before_connection(self):
  with self.assertRaises(Problem):self.call('owner/catalog',actor={'scope':'app','app_id':18})
 def test_connected_bridge_metadata_only(self):
  self.call('owner/navigation',{'app_id':18})
  self.store.note('owner-test',{'text':'NOTE_SECRET','request_id':'x'})
  draft=self.store.config('draft');draft['instructions']='INSTRUCTION_SECRET';self.store.save_draft(draft)
  actor={'scope':'app','app_id':18};result=self.call('owner/catalog',actor=actor)
  self.assertEqual(result['app_id'],24)
  raw=json.dumps(result)
  for hidden in ('NOTE_SECRET','INSTRUCTION_SECRET','"owner-test"','session','participants'):self.assertNotIn(hidden,raw)
  for path,body in [('admin/state',None),('owner/catalog',{}),('admin/publish',{}),('owner/create',{'name':'Hack','request_id':'h'}),('assistants/pocket-notes/admin/state',None),('participant/state',{})]:
   with self.assertRaises(Problem):self.call(path,body,actor)
 def test_other_apps_cannot_self_grant(self):
  with self.assertRaises(Problem):self.call('owner/navigation',{'app_id':18},{'scope':'app','app_id':24})
 def test_public_and_delegated_cannot_read_catalog(self):
  for extra in ({'public':True},{'actor':{'scope':'agent','delegated':True}}):
   with self.assertRaises(Problem):self.call('owner/catalog',**extra)
 def test_sessions_do_not_cross_assistants(self):
  identity=self.create();session=self.store.issue_session('owner-test')
  with self.assertRaises(Problem):self.call(f'assistants/{identity}/participant/state',{'session':session})
 def test_unknown_id_never_opens_user_path(self):
  with self.assertRaises(Problem):self.call('assistants/unknown/admin/state')
 def test_new_assistant_uses_existing_worker(self):
  identity=self.create()
  with Catalog(self.store).open(identity) as new:
   new.publish();new.enqueue('owner-test',{'text':'Hi','request_id':'q'})
   self.assertEqual(new.definition()['runtime'],'blank-v1')
   self.assertTrue(process_one(new,lambda *a,**kw:self.fail('Blank bots must not call a model')))
   self.assertEqual(new.overview('owner-test')['tasks'][0]['status'],'done')
 def test_published_model_not_draft_model(self):
  self.store.publish();draft=self.store.config('draft');draft['model']='another-model';self.store.save_draft(draft)
  item=self.call('owner/catalog')['assistants'][0]
  self.assertNotEqual(item['draft_model'],item['published_model']);self.assertTrue(item['draft_changed'])


from unittest.mock import patch
from bot_behaviors import BEHAVIORS, Blank

class PrivateFixture(Blank):
    private_only = True

def fixture_behaviors():
    return patch.dict(BEHAVIORS, {'fixture-deterministic-v1': Blank(), 'fixture-private-v1': PrivateFixture()})

class DevelopmentPresentationTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  DevelopmentPresentationTests.registry = fixture_behaviors(); DevelopmentPresentationTests.registry.start()
 @classmethod
 def tearDownClass(cls):
  DevelopmentPresentationTests.registry.stop()
 def summary(self,runtime='notes-v1',live=True,conn=None,test=None):
  from catalog import development_summary
  draft={'name':'Fixture','description':'Intro','instructions':'Guide','model':'fake','runtime':runtime}
  return development_summary(draft,dict(draft) if live else None,conn or {},test or {})
 def test_loaded_draft_not_called_verified_or_published(self):
  s=self.summary(live=False,conn={'state':'testing','username':'Fixture'},test={'enabled':True,'current':True})
  self.assertEqual(s['test_label'],'Latest draft loaded');self.assertEqual(s['release_label'],'Not published')
  self.assertNotIn('verified',str(s).lower());self.assertTrue(s['can_publish'])
 def test_live_and_private_test_are_separate_facts(self):
  s=self.summary(conn={'state':'active','username':'Fixture'},test={'enabled':True,'current':False})
  self.assertEqual(s['status'],'Private test');self.assertEqual(s['test_label'],'Earlier draft loaded')
  self.assertEqual(s['release_label'],'Serving approved users');self.assertFalse(s['can_publish'])
 def test_capabilities_not_bot_names_control_model_settings(self):
  for runtime,expected in [('notes-v1',True),('conversation-v1',True),('blank-v1',False),('fixture-deterministic-v1',False)]:
   self.assertEqual(self.summary(runtime)['uses_model'],expected)
 def test_blank_and_private_browser_never_offer_publication(self):
  for runtime in ['blank-v1','fixture-private-v1']:
   s=self.summary(runtime,conn={'state':'testing','username':'Fixture'},test={'enabled':True,'current':True})
   self.assertFalse(s['can_publish'])
 def test_changed_fields_expose_no_instruction_content(self):
  from catalog import development_summary
  d={'name':'Same','description':'Same','runtime':'conversation-v1','model':'fake','instructions':'DRAFT_SECRET'}
  s=development_summary(d,{**d,'instructions':'LIVE_SECRET'},{'state':'active'}, {})
  self.assertEqual(s['changed_fields'],['Instructions']);self.assertNotIn('SECRET',json.dumps(s))
 def test_error_text_is_product_neutral(self):
  s=self.summary('fixture-deterministic-v1',conn={'error':'fixture','state':'testing'})
  self.assertEqual(s['status'],'Needs attention');self.assertNotIn('notes',s['next_step'])
