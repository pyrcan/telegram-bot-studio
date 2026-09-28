"""Reusable definitions never clone operational state or participant content."""
import tempfile
import unittest
from assistant_store import Store, Problem
from catalog import Catalog
from bot_behaviors import CONVERSATION_DEFAULT

class StarterTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Store(self.temp.name);self.catalog=Catalog(self.root)
 def tearDown(self):self.root.db.close();self.temp.cleanup()
 def source(self,identity='pocket-notes'):
  s=self.catalog.summary(identity)['starter'];return {'id':s['id'],'revision':s['revision']}
 def create(self,source=None,key='new',**extra):
  return self.catalog.create({'name':'My new bot','request_id':key,**({'source':source} if source else {}),**extra})['id']
 def test_blank_remains_default_and_not_a_suggestion(self):
  identity=self.create()
  self.assertIsNone(self.catalog.summary(identity)['starter'])
  with self.catalog.open(identity) as store:self.assertEqual(store.config('draft')['runtime'],'blank-v1')
 def test_copy_published_not_unpublished_changes(self):
  self.root.publish();published=self.root.definition()
  self.root.save_draft({**published,'instructions':'Draft secret','description':'Unpublished intro','model':'different-model'})
  item=self.catalog.summary('pocket-notes')['starter']
  self.assertEqual(item['version'],1);self.assertEqual(item['description'],published['description'])
  self.assertNotIn('instructions',item)
  identity=self.create(self.source())
  with self.catalog.open(identity) as store:
   draft=store.config('draft')
   for field in ('description','instructions','model','runtime','capabilities'):self.assertEqual(draft[field],published[field])
   self.assertEqual(draft['id'],identity);self.assertEqual(draft['name'],'My new bot')
   self.assertIsNone(store.config('deployed'));self.assertEqual(store.config('based_on')['version'],1)
 def test_unpublished_custom_bot_can_be_reused(self):
  first=self.create(key='first',**CONVERSATION_DEFAULT)
  source=self.source(first);second=self.create(source)
  with self.catalog.open(second) as store:self.assertEqual(store.config('draft')['runtime'],'conversation-v1')
  self.assertIsNone(self.catalog.summary(second)['based_on']['version'])
 def test_no_data_sessions_connections_or_editing_chat_copied(self):
  from telegram_store import schema,public_state
  schema(self.root)
  self.root.note('owner-test',{'text':'Private note','request_id':'note'})
  self.root.issue_session('owner-test');self.root.set_config('editing_chat',{'id':'private-chat'})
  self.root.set_config('private_test',{'enabled':True,'secret':'never-copy'})
  identity=self.create(self.source())
  with self.catalog.open(identity) as store:
   self.assertEqual(store.notes('owner-test'),[])
   self.assertEqual(store.db.execute('SELECT count(*) FROM sessions').fetchone()[0],0)
   self.assertIsNone(store.config('editing_chat'));self.assertIsNone(store.config('private_test'))
  self.assertFalse(public_state(self.root,identity).get('username'))
  self.assertEqual(len(self.root.notes('owner-test')),1)
 def test_stale_source_rejected_without_new_catalog_entry(self):
  source=self.source();self.root.save_draft({**self.root.config('draft'),'description':'Changed'})
  before=self.catalog.ids()
  with self.assertRaises(Problem) as error:self.create(source)
  self.assertEqual(error.exception.status,409);self.assertEqual(before,self.catalog.ids())
 def test_publishing_changes_review_but_draft_edits_after_release_do_not(self):
  self.root.publish();source=self.source()
  self.root.save_draft({**self.root.config('draft'),'description':'New description'})
  self.assertEqual(source,self.source());self.root.publish()
  with self.assertRaises(Problem):self.create(source)
 def test_idempotent_retry_survives_source_change(self):
  source=self.source();one=self.create(source)
  self.root.save_draft({**self.root.config('draft'),'description':'Changed'})
  self.assertEqual(one,self.create(source))
 def test_unknown_deleted_and_malformed_source_rejected(self):
  for source in ({'id':'../bad','revision':'x'},'bad',{'id':'pocket-notes'}, {'id':[]}):
   with self.assertRaises(Problem):self.create(source)
  source=self.source()
  self.root.db.execute('INSERT INTO assistant_lifecycle VALUES (?,1,1,?)',('pocket-notes','Deleted'));self.root.db.commit()
  with self.assertRaises(Problem):self.create(source)
 def test_source_and_copy_change_independently(self):
  identity=self.create(self.source());original=self.root.config('draft')
  with self.catalog.open(identity) as store:store.save_draft({**store.config('draft'),'description':'Copy only'})
  self.assertEqual(self.root.config('draft'),original)
  self.root.save_draft({**original,'description':'Source only'})
  with self.catalog.open(identity) as store:self.assertEqual(store.config('draft')['description'],'Copy only')
 def test_capabilities_are_derived_and_extra_configuration_is_not_copied(self):
  self.root.set_config('draft',{**self.root.config('draft'),'capabilities':['unsafe.fake'],'secret_field':'not-allowed'})
  identity=self.create(self.source())
  with self.catalog.open(identity) as store:
   self.assertNotIn('secret_field',store.config('draft'));self.assertNotIn('unsafe.fake',store.config('draft')['capabilities'])

if __name__=='__main__':unittest.main()
