"""Synthetic aggregate/accounting/privacy coverage. No provider or Telegram calls."""
import datetime as dt
import json
import tempfile
import unittest
import os
from unittest.mock import patch
from assistant_store import Store,Problem
from catalog import Catalog
from service import dispatch
from analytics import record,dashboard,totals,FIELDS
from text_runner import parse_output
from worker import process_one
import cost_settings as costs
import telegram_store as tg

class AnalyticsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name);self.catalog=Catalog(self.store)
  self.env=patch.dict(os.environ,{'APP_ID':'24'});self.env.start()
 def tearDown(self):self.env.stop();self.store.db.close();self.tmp.cleanup()
 def request(self,path,body=None,actor=None,**extra):
  return dispatch(self.store,{'path':path,'method':'POST' if body is not None else 'GET','body':body,'actor':actor or {'scope':'app','app_id':24},**extra})
 def test_forward_only_and_numeric_projection(self):
  self.store.note('owner-test',{'text':'SECRET_CONTENT','request_id':'note'})
  data=self.request('owner/dashboard')
  self.assertEqual(data['usage']['live']['interactions'],0)
  self.assertEqual(data['users']['joined'],0)
  record(self.store,'task','secret-receipt',{'tasks':1,'process_ms':100})
  data=self.request('owner/dashboard')
  raw=json.dumps(data)
  for hidden in ('SECRET_CONTENT','secret-receipt','owner-test','user_id','participant','input','output','sources'):
   # token counters are explicitly numerical and are not input/output text.
   if hidden in ('input','output'):continue
   self.assertNotIn('"'+hidden+'"',raw)
  self.assertEqual(data['usage']['live']['tasks'],1)
  self.assertIsNone(data['bots'][0]['cost']['measured_bill'])
 def test_replay_and_test_bucket_are_isolated(self):
  self.assertTrue(record(self.store,'intake',1,{'interactions':1}))
  self.assertFalse(record(self.store,'intake',1,{'interactions':1}))
  record(self.store,'intake',1,{'interactions':1},mode='test')
  self.assertEqual(self.request('owner/dashboard')['usage']['live']['interactions'],1)
  self.assertEqual(self.request('owner/dashboard')['usage']['test']['interactions'],1)
 def test_foreign_bridge_delegated_public_and_participant_denied(self):
  self.request('owner/navigation',{'app_id':18},actor={'scope':'owner'})
  for actor in ({'scope':'app','app_id':18},{'scope':'participant'},{'scope':'agent','delegated':True}):
   for path,body in (('owner/dashboard',None),('owner/cost-assumptions',{})):
    with self.assertRaises(Problem):self.request(path,body,actor)
  with self.assertRaises(Problem):self.request('owner/dashboard',public=True)
  self.assertNotIn('usage',self.request('owner/catalog',actor={'scope':'app','app_id':18}))
 def test_bot_isolation_and_memberships_not_people(self):
  other=self.catalog.create({'name':'Second','request_id':'second'})['id']
  record(self.store,'intake',8,{'interactions':3})
  with self.catalog.open(other) as new:record(new,'intake',8,{'interactions':7})
  data=self.request('owner/dashboard')
  self.assertEqual([b['usage']['live']['interactions'] for b in data['bots']],[3,7])
  self.assertEqual(data['usage']['live']['interactions'],10)
 def test_cost_unknown_not_zero_and_explicit_assumptions(self):
  self.assertIsNone(costs.token_estimate(None,'fake',{'input_tokens':1}))
  profile=costs.read(self.store)['profiles'][0]
  profile.update(kind='tokens',input_per_million=2,cached_per_million=.5,output_per_million=10)
  usage={'input_tokens':1000000,'cached_input_tokens':200000,'output_tokens':100000}
  self.assertAlmostEqual(costs.token_estimate(profile,'fake',usage),2.7)
 def test_cost_conflict_and_invalid_amounts(self):
  body=costs.read(self.store);body['expected_revision']=body.pop('revision')
  costs.save(self.catalog,body)
  with self.assertRaises(Problem) as caught:costs.save(self.catalog,body)
  self.assertEqual(caught.exception.status,409)
  for amount in (-1,float('nan'),float('inf'),True,'secret'):
   with self.assertRaises(Problem):costs.save(self.catalog,{**body,'monthly_hosting':amount})
 def test_parse_usage_drops_all_non_numeric_fields(self):
  seen=[]
  lines=[{'type':'item.completed','item':{'type':'agent_message','text':'answer'}},
         {'type':'turn.completed','usage':{'input_tokens':15,'cached_input_tokens':5,'output_tokens':7,'secret':'never'}}]
  self.assertEqual(parse_output('\n'.join(json.dumps(x) for x in lines),seen.append),'answer')
  self.assertEqual(seen,[{'input_tokens':15,'cached_input_tokens':5,'output_tokens':7}])
  lines[-1]['usage']['output_tokens']='secret';seen.clear()
  parse_output('\n'.join(json.dumps(x) for x in lines),seen.append);self.assertEqual(seen,[])
 def test_completed_task_counts_without_exposing_question(self):
  self.store.publish();self.store.enqueue('owner-test',{'text':'SECRET_QUESTION','request_id':'q'})
  self.assertTrue(process_one(self.store,lambda *a,**k:self.fail('Empty notes require no AI')))
  self.assertFalse(process_one(self.store))
  data=self.request('owner/dashboard')
  self.assertEqual(data['usage']['live']['tasks'],1);self.assertEqual(data['usage']['live']['ai_calls'],0)
  self.assertNotIn('SECRET_QUESTION',json.dumps(data))
 def test_usage_callback_and_failed_ai_attempt_are_counted(self):
  definition=self.store.config('draft');definition['runtime']='conversation-v1';self.store.save_draft(definition);self.store.publish()
  self.store.enqueue('owner-test',{'text':'Question','request_id':'q'})
  def fake(*args,**kwargs):
   kwargs['on_usage']({'input_tokens':20,'cached_input_tokens':2,'output_tokens':4})
   return 'Answer'
  with patch('worker.run_text',fake):self.assertTrue(process_one(self.store,fake))
  usage=self.request('owner/dashboard')['usage']['live']
  self.assertEqual(usage['ai_calls'],1);self.assertEqual(usage['metered_ai_calls'],1);self.assertEqual(usage['input_tokens'],20)
 def test_test_task_accounted_in_owning_bot_not_test_domain(self):
  with tempfile.TemporaryDirectory() as directory:
   test=Store(directory);test.set_config('test_identity',{'epoch':'test'})
   test.publish();test.enqueue('owner-test',{'text':'Question','request_id':'q'})
   process_one(test,metrics_store=self.store);test.db.close()
  data=self.request('owner/dashboard');self.assertEqual(data['usage']['test']['tasks'],1)
  self.assertEqual(data['usage']['live']['tasks'],0)
 def test_metric_shape_rejects_content_and_negative_values(self):
  for values in ({'message':'secret'},{'interactions':'secret'},{'interactions':-1}):
   with self.assertRaises(ValueError):record(self.store,'intake',1,values)
 def test_retention_and_month_boundary(self):
  old=dt.datetime(2024,1,1,tzinfo=dt.timezone.utc).timestamp()
  new=dt.datetime(2026,10,1,tzinfo=dt.timezone.utc).timestamp()
  record(self.store,'intake',1,{'interactions':1},now=old)
  record(self.store,'intake',2,{'interactions':4},now=new)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM studio_receipts').fetchone()[0],1)
  data=dashboard(self.catalog,now=new);self.assertEqual(data['period']['start'],'2026-10-01')
  self.assertEqual(data['period']['end'],'2026-11-01');self.assertEqual(data['usage']['live']['interactions'],4)

class IntakeDeliveryAnalytics(unittest.TestCase):
 def setUp(self):
  import test_telegram as fixture
  fixture.TelegramTests.setUp(self);fixture.TelegramTests.activate(self)
 def tearDown(self):
  import test_telegram as fixture
  fixture.TelegramTests.tearDown(self)
 def update(self,mid,text,user=77):
  return {'update_id':mid,'message':{'message_id':mid,'text':text,'from':{'id':user},'chat':{'id':user,'type':'private'}}}
 def usage(self):
  return dashboard(Catalog(self.store))['usage']['live']
 def test_only_admitted_interactions_count_and_replay_deduplicates(self):
  tg.handle_update(self.store,self.update(1,'SECRET',88),self.owner)
  tg.handle_update(self.store,self.update(2,'A note'),self.owner)
  tg.handle_update(self.store,self.update(2,'A note'),self.owner)
  self.assertEqual(self.usage()['interactions'],1)
  self.assertNotIn('SECRET',json.dumps(dashboard(Catalog(self.store))))
 def test_confirmed_delivery_count_and_replay(self):
  from telegram_transport import deliver
  tg.handle_update(self.store,self.update(1,'A note'),self.owner)
  fake=lambda *a,**k:{'message_id':8}
  deliver(self.store,self.store,self.identity,fake)
  count=self.usage()['telegram_confirmed'];self.assertGreater(count,0)
  deliver(self.store,self.store,self.identity,fake);self.assertEqual(self.usage()['telegram_confirmed'],count)
 def test_diagnostic_failure_does_not_change_capture(self):
  with patch('analytics.record',side_effect=RuntimeError('Synthetic telemetry failure')):
   tg.handle_update(self.store,self.update(1,'Safe note'),self.owner)
  self.assertEqual(len(self.store.notes('owner-test')),1)
  self.assertTrue(self.store.config('analytics_incomplete'))
 def test_opaque_receipts_do_not_include_participant_or_content(self):
  tg.handle_update(self.store,self.update(1,'SECRET_NOTE'),self.owner)
  raw=json.dumps([dict(r) for r in self.store.db.execute('SELECT * FROM studio_receipts')])
  self.assertNotIn('SECRET_NOTE',raw);self.assertNotIn('owner-test',raw);self.assertNotIn('user_id',raw)
