"""Synthetic owner accounting: no secrets, provider calls or real app data."""
import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from assistant_store import Store, Problem
from catalog import Catalog
from analytics import record, dashboard
from service import dispatch
from worker import process_one
import cost_settings as costs
import telegram_store as tg

class CostSettingsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Store(self.tmp.name);self.catalog=Catalog(self.root)
  self.env=patch.dict(os.environ,{'APP_ID':'24'});self.env.start()
 def tearDown(self):self.env.stop();self.root.db.close();self.tmp.cleanup()
 def settings(self):
  value=costs.read(self.root);value['expected_revision']=value.pop('revision');return value
 def save(self,value):return costs.save(self.catalog,value)
 def profile(self,identity='shared',kind='subscription',model=''):
  return {'id':identity,'name':'Test profile','provider':'Owner supplied',
          'model':model,'kind':kind,'monthly_cost':100,
          'input_per_million':2,'cached_per_million':.5,'output_per_million':10}
 def bot(self,identity):
  return {'id':identity,'connected':True,'uses_model':True,'combined':{'ai_calls':0},'cost':{}}
 def test_migration_preserves_owner_values_and_revision_without_rewrite(self):
  old={**costs.LEGACY_DEFAULT,'currency':'USD','monthly_hosting':20,'monthly_ai':100,'ai_mode':'allocation','revision':'owner-rev'}
  self.root.set_config('studio_costs',old)
  value=costs.read(self.root)
  self.assertEqual(value['revision'],'owner-rev');self.assertEqual(value['monthly_hosting'],20)
  self.assertEqual(value['profiles'][0]['monthly_cost'],100)
  self.assertEqual(value['profiles'][0]['kind'],'subscription')
  self.assertEqual(self.root.config('studio_costs'),old)
  bots=[self.bot('pocket-notes'),{**self.bot('media'),'uses_model':False}]
  summary=costs.budgets(bots,value)
  self.assertEqual(summary['monthly_budget_estimate'],120)
  self.assertEqual(bots[0]['cost']['monthly_budget_estimate'],110)
  self.assertEqual(bots[1]['cost']['monthly_budget_estimate'],10)
 def test_shared_subscription_counted_once_default_and_override(self):
  value=self.settings();value.update(monthly_hosting=20,profiles=[self.profile()],default_profile='shared')
  bots=[self.bot('one'),self.bot('two')]
  summary=costs.budgets(bots,value)
  self.assertEqual(summary['monthly_budget_estimate'],120)
  self.assertEqual([b['cost']['ai_monthly_allocation_estimate'] for b in bots],[50,50])
  value['profiles'].append(self.profile('small'));value['profiles'][1]['monthly_cost']=5
  value['assignments']={'two':'small'}
  summary=costs.budgets(bots,value)
  self.assertEqual(summary['monthly_budget_estimate'],125)
  self.assertEqual([b['cost']['ai_monthly_allocation_estimate'] for b in bots],[100,5])
 def test_owner_three_bot_ceiling_preserved(self):
  value=self.settings();value.update(currency='USD',monthly_hosting=20,
         hosting_basis='ceiling',profiles=[self.profile()],default_profile='shared')
  bots=[self.bot('pocket-notes'),{**self.bot('audio'),'uses_model':False},
        {**self.bot('video'),'uses_model':False}]
  summary=costs.budgets(bots,value)
  self.assertEqual(summary['monthly_budget_estimate'],120)
  self.assertAlmostEqual(bots[0]['cost']['monthly_budget_estimate'],106.666667,places=5)
  self.assertAlmostEqual(bots[1]['cost']['monthly_budget_estimate'],6.666667,places=5)
  self.assertEqual(bots[1]['cost']['ai_monthly_allocation_estimate'],0)
 def test_unused_pool_remains_in_overall_budget(self):
  value=self.settings();value.update(monthly_hosting=20,profiles=[self.profile()],default_profile=None)
  summary=costs.budgets([],value)
  self.assertEqual(summary['monthly_budget_estimate'],120)
  self.assertEqual(summary['unallocated_ai_monthly_estimate'],100)
 def test_unknown_and_zero_are_distinct(self):
  value=self.settings();value.update(monthly_hosting=0,profiles=[self.profile()],default_profile='shared')
  value['profiles'][0]['monthly_cost']=None
  self.assertIsNone(costs.budgets([self.bot('one')],value)['monthly_budget_estimate'])
  value['profiles'][0]['monthly_cost']=0
  self.assertEqual(costs.budgets([self.bot('one')],value)['monthly_budget_estimate'],0)
 def test_no_profile_does_not_make_ai_free(self):
  value=self.settings();value.update(monthly_hosting=20,profiles=[],default_profile=None)
  self.assertIsNone(costs.budgets([self.bot('one')],value)['monthly_budget_estimate'])
  bot={**self.bot('media'),'uses_model':False}
  self.assertEqual(costs.budgets([bot],value)['monthly_budget_estimate'],20)
 def test_model_specific_rates_missing_or_mismatch_are_unknown(self):
  profile=self.profile(kind='tokens',model='cheap')
  usage={'input_tokens':1000000,'cached_input_tokens':200000,'output_tokens':100000}
  self.assertEqual(costs.token_estimate(profile,'cheap',usage),2.7)
  self.assertIsNone(costs.token_estimate(profile,'expensive',usage))
  profile['cached_per_million']=None
  self.assertIsNone(costs.token_estimate(profile,'cheap',usage))
 def test_profile_amount_validation_and_unknown_fields(self):
  value=self.settings()
  for amount in (-1,True,float('nan'),float('inf'),'10'):
   with self.assertRaises(Problem):self.save({**value,'monthly_hosting':amount})
  for key,val in (('schema',3),('currency','bad'),('hosting_basis','bad'),('provider_key','secret')):
   with self.assertRaises(Problem):self.save({**value,key:val})
  for p in ({**self.profile(),'id':'bad/id'},{**self.profile(),'monthly_cost':True},
            {**self.profile(),'model':'a'*161},{**self.profile(),'extra':'not accepted'}):
   with self.assertRaises(Problem):self.save({**value,'profiles':[p],'default_profile':None})
 def test_references_and_duplicate_profiles_rejected(self):
  value=self.settings()
  with self.assertRaises(Problem):self.save({**value,'default_profile':'missing'})
  with self.assertRaises(Problem):self.save({**value,'assignments':{'foreign':'existing-ai'}})
  with self.assertRaises(Problem):self.save({**value,'profiles':[self.profile(),self.profile()]})
  with self.assertRaises(Problem):self.save({**value,'assignments':{'pocket-notes':'missing'}})
 def test_stale_save_is_atomic_and_history_is_immutable(self):
  value=self.settings();before=copy.deepcopy(value)
  first=self.save(value)
  with self.assertRaises(Problem) as caught:self.save(value)
  self.assertEqual(caught.exception.status,409)
  self.assertEqual(costs.read(self.root),first)
  history=costs.history(self.root)
  self.assertEqual(len(history),2)
  self.assertEqual(history[-1]['settings']['monthly_hosting'],before['monthly_hosting'])
  self.assertEqual(self.root.db.execute('SELECT count(*) FROM studio_cost_versions').fetchone()[0],2)
 def token_settings(self):
  value=self.settings();value.update(profiles=[self.profile(kind='tokens')],default_profile='shared')
  return self.save(value)
 def record_call(self,identity,context,mode='live'):
  context['calls']=[{'model':'fake','usage':{'input_tokens':1000000,'cached_input_tokens':200000,'output_tokens':100000}}]
  return record(self.root,'task',identity,{'ai_calls':1,'metered_ai_calls':1},mode=mode,cost_context=context)
 def test_rate_edit_does_not_reprice_prior_usage_or_replay(self):
  original=self.token_settings();pinned=costs.pin(self.root,'pocket-notes')
  changed=self.settings();changed['profiles'][0]['input_per_million']=20;self.save(changed)
  self.assertTrue(self.record_call('first',pinned))
  self.assertFalse(self.record_call('first',pinned))
  self.record_call('second',costs.pin(self.root,'pocket-notes'),mode='test')
  data=dashboard(self.catalog)
  cost=data['bots'][0]['cost']
  self.assertAlmostEqual(cost['metered_ai_estimate'],2.7+17.1)
  self.assertEqual(cost['priced_ai_calls'],2)
  rows=self.root.db.execute('SELECT revision,mode,amount FROM studio_ai_costs ORDER BY amount').fetchall()
  self.assertEqual(rows[0]['revision'],original['revision']);self.assertEqual(rows[0]['mode'],'live')
  self.assertEqual(rows[1]['mode'],'test')
 def test_currency_edit_never_converts_prior_estimates(self):
  self.token_settings();self.record_call('first',costs.pin(self.root,'pocket-notes'))
  changed=self.settings();changed['currency']='USD';self.save(changed)
  data=dashboard(self.catalog);cost=data['bots'][0]['cost']
  self.assertIsNone(cost['metered_ai_estimate']);self.assertEqual(cost['other_currency_calls'],1)
  self.assertEqual(cost['estimate_history'][0]['currency'],'GBP')
  self.assertEqual(cost['estimate_history'][0]['metered_ai_estimate'],2.7)
  self.record_call('second',costs.pin(self.root,'pocket-notes'))
  self.assertEqual(dashboard(self.catalog)['bots'][0]['cost']['metered_ai_estimate'],2.7)
 def test_earlier_unpriced_calls_are_not_backfilled(self):
  record(self.root,'old',1,{'ai_calls':1,'metered_ai_calls':1})
  self.token_settings()
  cost=dashboard(self.catalog)['bots'][0]['cost']
  self.assertIsNone(cost['metered_ai_estimate']);self.assertEqual(cost['legacy_ai_calls'],1)
  self.assertEqual(cost['unknown_ai_calls'],1)
 def test_worker_pins_task_start_and_actual_model(self):
  self.token_settings()
  draft=self.root.config('draft');draft.update(runtime='conversation-v1',model='fake');self.root.save_draft(draft);self.root.publish()
  self.root.enqueue('owner-test',{'text':'SECRET','request_id':'q'})
  def fake(*args,**kwargs):
   updated=self.settings();updated['profiles'][0]['input_per_million']=20;self.save(updated)
   kwargs['on_usage']({'input_tokens':1000000,'cached_input_tokens':200000,'output_tokens':100000})
   return 'Answer'
  with patch('worker.run_text',fake):self.assertTrue(process_one(self.root,fake))
  self.assertEqual(dashboard(self.catalog)['bots'][0]['cost']['metered_ai_estimate'],2.7)
  raw=json.dumps([dict(row) for row in self.root.db.execute('SELECT * FROM studio_ai_costs')])
  self.assertNotIn('SECRET',raw);self.assertNotIn('owner-test',raw)
 def test_worker_uses_catalog_costs_for_other_bot_and_private_test(self):
  identity=self.catalog.create({'name':'Other bot','request_id':'other-cost','runtime':'conversation-v1','model':'fake'})['id']
  value=self.settings();value.update(profiles=[self.profile('cheap',kind='tokens'),self.profile('expensive',kind='tokens')],default_profile='cheap',assignments={identity:'expensive'})
  value['profiles'][1]['input_per_million']=20;self.save(value)
  with self.catalog.open(identity) as live,tempfile.TemporaryDirectory() as directory:
   private=Store(directory);draft=private.config('draft');draft.update(runtime='conversation-v1',model='fake');private.save_draft(draft);private.publish();private.set_config('test_identity',{'epoch':'synthetic'})
   private.enqueue('owner-test',{'text':'SECRET_OTHER','request_id':'q'})
   def fake(*args,**kwargs):
    kwargs['on_usage']({'input_tokens':1000000,'cached_input_tokens':200000,'output_tokens':100000})
    return 'Answer'
   with patch('worker.run_text',fake):process_one(private,fake,metrics_store=live,cost_root=self.root,cost_identity=identity)
   self.assertEqual(private.db.execute("SELECT count(*) FROM sqlite_master WHERE name='studio_ai_costs'").fetchone()[0],0)
   private.db.close()
  data=dashboard(self.catalog);other=next(b for b in data['bots'] if b['id']==identity)
  self.assertEqual(other['cost']['metered_ai_estimate'],17.1)
  self.assertEqual(other['cost']['estimate_history'][0]['profile'],'expensive')
  self.assertEqual(other['cost']['estimate_history'][0]['mode'],'test')
  self.assertEqual(data['bots'][0]['cost']['priced_ai_calls'],0)
 def test_missing_measurement_and_failure_are_not_free(self):
  self.token_settings();context=costs.pin(self.root,'pocket-notes')
  context['calls']=[{'model':'fake','usage':None}]
  record(self.root,'task',1,{'ai_calls':1},cost_context=context)
  cost=dashboard(self.catalog)['bots'][0]['cost']
  self.assertIsNone(cost['metered_ai_estimate']);self.assertEqual(cost['unknown_ai_calls'],1)
 def test_mixed_pools_and_token_rates_do_not_hide_known_fixed_budget(self):
  value=self.settings();value.update(monthly_hosting=20,profiles=[self.profile(),self.profile('metered',kind='tokens')],default_profile='shared',assignments={'two':'metered'})
  bots=[self.bot('one'),self.bot('two')]
  summary=costs.budgets(bots,value)
  self.assertEqual(summary['ai_monthly_allocation_estimate'],100)
  self.assertIsNone(summary['monthly_budget_estimate']);self.assertTrue(summary['variable_ai_cost_unknown'])
 def test_owner_chat_client_reads_and_writes_same_revision_guarded_contract(self):
  import io,owner
  data=dashboard(self.catalog)
  output=io.StringIO()
  with patch.object(owner,'call',return_value=data) as call,patch('sys.argv',['owner.py','costs']),patch('sys.stdout',output):
   owner.main()
  call.assert_called_once_with('owner/dashboard')
  result=json.loads(output.getvalue());self.assertIn('assumptions',result);self.assertNotIn('users',result)
  value=self.settings();value.pop('expected_revision')
  with patch.object(owner,'call',return_value={'revision':'next'}) as call,patch('sys.argv',['owner.py','save-costs','--revision','observed']),patch('sys.stdin',io.StringIO(json.dumps(value))),patch('sys.stdout',io.StringIO()):
   owner.main()
  call.assert_called_once_with('owner/cost-assumptions',{**value,'expected_revision':'observed'})
 def test_diagnostics_failure_does_not_change_model_task_outcome(self):
  draft=self.root.config('draft');draft['runtime']='conversation-v1';self.root.save_draft(draft);self.root.publish()
  self.root.enqueue('owner-test',{'text':'Question','request_id':'q'})
  with patch('cost_settings.pin',side_effect=RuntimeError('Synthetic accounting failure')):
   self.assertTrue(process_one(self.root,lambda *a,**k:'Answer'))
  self.assertTrue(self.root.config('analytics_incomplete'))
  self.assertEqual(self.root.db.execute('SELECT output FROM tasks').fetchone()[0],'Answer')
 def test_foreign_public_delegated_and_participant_cannot_edit_profiles(self):
  body=self.settings()
  for actor in ({'scope':'app','app_id':18},{'scope':'participant'},{'scope':'agent','delegated':True}):
   with self.assertRaises(Problem):dispatch(self.root,{'path':'owner/cost-assumptions','method':'POST','body':body,'actor':actor})
  with self.assertRaises(Problem):dispatch(self.root,{'path':'owner/cost-assumptions','method':'POST','body':body,'actor':{'scope':'owner'},'public':True})
 def test_bot_assignment_cannot_affect_draft_release_or_other_bot(self):
  identity=self.catalog.create({'name':'Other','request_id':'new'})['id']
  before=self.catalog.summary('pocket-notes')
  with self.catalog.open(identity) as store:definition=store.config('draft')
  value=self.settings();value['assignments']={identity:'existing-ai'};self.save(value)
  self.assertEqual(self.catalog.summary('pocket-notes'),before)
  with self.catalog.open(identity) as store:self.assertEqual(store.config('draft'),definition)

if __name__=='__main__':unittest.main()
