"""Release projections: synthetic configuration, never Telegram or model traffic."""
import tempfile
import unittest
from assistant_store import Store, DAILY_TASK_LIMIT, MAX_PENDING_TASKS
from catalog import Catalog, release_review, onboarding_preview
from bot_behaviors import CONVERSATION_DEFAULT, BLANK_DEFAULT


from unittest.mock import patch
from bot_behaviors import BEHAVIORS, Blank

class PrivateFixture(Blank):
    private_only = True

def fixture_behaviors():
    return patch.dict(BEHAVIORS, {'fixture-deterministic-v1': Blank(), 'fixture-private-v1': PrivateFixture()})

class ReleaseReviewTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  ReleaseReviewTests.registry = fixture_behaviors(); ReleaseReviewTests.registry.start()
 @classmethod
 def tearDownClass(cls):
  ReleaseReviewTests.registry.stop()
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(); self.root=Store(self.temp.name); self.catalog=Catalog(self.root)
  self.identity=self.catalog.create({'name':'Fixture','request_id':'fixture'})['id']
 def tearDown(self):self.root.db.close();self.temp.cleanup()
 def test_review_uses_draft_and_share_uses_published_definition(self):
  with self.catalog.open(self.identity) as store:
   store.save_draft({**CONVERSATION_DEFAULT,'name':'Live fixture','description':'Live introduction'})
   store.publish()
   store.save_draft({**CONVERSATION_DEFAULT,'name':'New draft','description':'New introduction'})
  item=self.catalog.summary(self.identity)
  self.assertEqual(item['onboarding']['description'],'Live introduction')
  self.assertEqual(item['release_review']['onboarding']['description'],'New introduction')
  self.assertIn('New draft',item['release_review']['onboarding']['welcome'])
  self.assertEqual(item['version'],1)
  self.assertFalse(item['development']['can_publish'])
  self.assertNotIn('participants',item);self.assertNotIn('notes',item)
 def test_limits_match_enforcement_and_models_match_runtime(self):
  value=release_review({**CONVERSATION_DEFAULT,'name':'Fixture'})
  self.assertEqual(value['daily_task_limit'],DAILY_TASK_LIMIT)
  self.assertEqual(value['pending_task_limit'],MAX_PENDING_TASKS)
  self.assertTrue(value['uses_model'])
  self.assertFalse(release_review({**BLANK_DEFAULT,'name':'Fixture'})['uses_model'])
  self.assertFalse(release_review({**CONVERSATION_DEFAULT,'name':'Fixture','runtime':'fixture-deterministic-v1'})['uses_model'])
 def test_notes_preview_matches_telegram_profile_and_welcome(self):
  from telegram_onboarding import DESCRIPTION,welcome
  value=onboarding_preview(self.root.config('draft'))
  self.assertEqual(value['description'],DESCRIPTION)
  self.assertEqual(value['welcome'],welcome(self.root.config('draft')['name']))
 def test_private_prototype_remains_explicitly_private(self):
  value=release_review({**CONVERSATION_DEFAULT,'name':'Fixture','runtime':'fixture-private-v1'})
  self.assertTrue(value['private_only'])
 def test_preview_has_no_mutating_side_effects(self):
  with self.catalog.open(self.identity) as store:
   before=store.db.total_changes
   release_review(store.config('draft'));onboarding_preview(store.config('draft'))
   self.assertEqual(before,store.db.total_changes)
   self.assertIsNone(store.config('deployed'))

if __name__=='__main__':unittest.main()
