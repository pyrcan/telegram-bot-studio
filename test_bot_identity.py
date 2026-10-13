import unittest
from test_owner import OwnerManagementTests
from assistant_store import Problem
from catalog import Catalog
from service import dispatch
class IdentityTests(unittest.TestCase):
 setUp=OwnerManagementTests.setUp
 tearDown=OwnerManagementTests.tearDown
 def call(self,body,actor=None):
  return dispatch(self.store,{'path':'assistants/pocket-notes/identity','method':'POST','body':body,'actor':actor or {'scope':'app','app_id':24}})
 def test_cosmetic_change_does_not_mutate_behavior(self):
  before=self.store.config('draft');revision=self.store.draft_revision()
  result=self.call({'icon':'📒','purpose':'My research','expected_revision':'0'})
  summary=Catalog(self.store).summary('pocket-notes')
  self.assertEqual(summary['display'],result)
  self.assertEqual(self.store.config('draft'),before)
  self.assertEqual(self.store.draft_revision(),revision)
  self.assertIsNone(self.store.config('deployed'))
 def test_stale_save_rejected(self):
  body={'icon':'PN','purpose':'Notes','expected_revision':'0'};self.call(body)
  with self.assertRaises(Problem) as caught:self.call(body)
  self.assertEqual(caught.exception.status,409)
 def test_foreign_and_delegated_rejected(self):
  for actor in ({'scope':'app','app_id':18},{'scope':'participant'},{'scope':'agent','delegated':True}):
   with self.assertRaises(Problem):self.call({'icon':'X','purpose':'X','expected_revision':'0'},actor)
 def test_unknown_fields_do_not_change_draft(self):
  with self.assertRaises(Problem):self.call({'icon':'X','purpose':'X','expected_revision':'0','runtime':'conversation-v1'})
 def test_appearance_cannot_target_unknown_bot(self):
  with self.assertRaises(Problem):dispatch(self.store,{'path':'assistants/unknown/identity','method':'POST','body':{},'actor':{'scope':'owner'}})
