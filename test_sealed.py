import unittest
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
import connect_telegram as connector

class SealedConnectionTests(unittest.TestCase):
 def run_connect(self,bot_id=1000,webhook='',owner_bot=2000,existing=False,pair=None):
  calls=[]
  def call(path,body=None):
   calls.append((path,body))
   if path.endswith('/prepare-identity'):return {'status':'blocked' if existing else 'ready'}
   if path=='owner/catalog':return {'app_id':24,'assistants':[{'id':'pocket-notes'}]}
   if path.endswith('/connection'):return {'epoch':'e','state':'reserved','slot':1,'bot_id':bot_id if existing else None}
   return {'ok':True}
  def api(path,body=None,method=None):
   calls.append((path,method))
   if path=='/api/apps/':return [{'id':18,'slug':'telegram-bridge-renamed','capability_contract':{'service':{'id':'telegram-bridge'}}}]
   if path.endswith('/pairing.json'):return pair if pair is not None else {'userId':77,'chatId':77}
   if path=='/api/apps/18/service/bot-info':return {'id':owner_bot}
   if path=='/api/apps/24/secrets/tg-1' and method=='PUT':return None
   raise AssertionError('Unexpected platform route: '+path)
  def telegram(token,method):return {'id':bot_id,'is_bot':True,'username':'Fixture_bot'} if method=='getMe' else {'url':webhook}
  with patch.object(connector,'call',side_effect=call),patch.object(connector,'api',side_effect=api),patch.object(connector,'telegram',side_effect=telegram):
   connector.connect('pocket-notes','e','123:synthetic_fixture_only_not_a_real_token')
  return calls
 def test_sealed_connection_never_activates_or_polls(self):
  calls=self.run_connect();paths=[c[0] for c in calls]
  self.assertIn('/api/apps/',paths)
  self.assertIn('assistants/pocket-notes/telegram/prepare-identity',paths)
  self.assertIn('/api/apps/24/secrets/tg-1',paths)
  self.assertIn('assistants/pocket-notes/telegram/verified',paths)
  self.assertFalse(any('activate' in p or 'getUpdates' in p or 'sendMessage' in p for p in paths))
 def test_existing_webhook_rejected(self):
  with self.assertRaises(ValueError):self.run_connect(webhook='https://example.com/hook')
 def test_personal_owner_bot_rejected(self):
  with self.assertRaises(ValueError):self.run_connect(bot_id=2000)
 def test_already_connected_bot_rejected(self):
  with self.assertRaises(ValueError):self.run_connect(existing=True)
 def test_invalid_credential_never_touches_network(self):
  with patch.object(connector,'call') as local,patch.object(connector,'telegram') as remote:
   with self.assertRaises(ValueError):connector.connect('pocket-notes','e','invalid')
   remote.assert_not_called()
 def test_stale_epoch_never_touches_telegram(self):
  with patch.object(connector,'call',return_value={'epoch':'new','state':'reserved'}),patch.object(connector,'telegram') as remote:
   with self.assertRaises(ValueError):connector.connect('pocket-notes','old','123:synthetic_fixture_only_not_a_real_token')
   remote.assert_not_called()

 def test_private_positive_owner_pairing_required(self):
  for pair in ({'userId':77,'chatId':-88},{'userId':True,'chatId':True},{'userId':-77,'chatId':-77},{}):
   with self.assertRaises(ValueError):self.run_connect(pair=pair)

 def test_saved_consumer_uses_local_auth_without_agent_environment(self):
  with tempfile.TemporaryDirectory() as directory:
   (Path(directory)/'service-token.txt').write_text('synthetic-local-service-credential')
   env={'CHAT_ID':'synthetic-chat','DATA_DIR':directory,'API_BASE_URL':'http://localhost:8080'}
   with patch.dict(os.environ,env,clear=True),patch.object(connector.urllib.request,'urlopen') as opened:
    opened.return_value.__enter__.return_value.read.return_value=b'{"ok":true}'
    self.assertEqual(connector.call('owner/catalog'),{'ok':True})
    request=opened.call_args.args[0]
    self.assertEqual(request.full_url,'http://localhost:8080/api/services/assistant-studio/owner/catalog')
    self.assertEqual(request.get_header('Authorization'),'Bearer synthetic-local-service-credential')
    self.assertNotIn('AGENT_TOKEN',os.environ)
 def test_app_job_cannot_use_sealed_service_auth(self):
  with patch.dict(os.environ,{'CHAT_ID':'synthetic-chat','APP_TOKEN':'synthetic-app'},clear=True):
   with self.assertRaises(RuntimeError):connector.owner_bearer()
 def test_missing_sealed_context_cannot_use_service_auth(self):
  with patch.dict(os.environ,{},clear=True):
   with self.assertRaises(RuntimeError):connector.owner_bearer()

 def test_saved_setup_passes_nonsecret_api_address_explicitly(self):
  import setup_telegram
  env={'API_BASE_URL':'http://localhost:8080'}
  with patch.dict(os.environ,env,clear=True),patch.object(sys,'argv',['setup','prepare','pocket-notes']),patch.object(setup_telegram,'call',return_value={'state':'reserved','epoch':'e'}),patch.object(setup_telegram.subprocess,'run') as run:
   run.return_value.returncode=0
   with self.assertRaises(SystemExit) as result:setup_telegram.main()
   self.assertEqual(result.exception.code,0)
   self.assertEqual(run.call_args.args[0][-2:],['--api-base','http://localhost:8080'])

class StudioOwnerActionTests(unittest.TestCase):
 def test_key_connection_cannot_use_nonsealed_action_executor(self):
  import owner_action
  with patch.dict(os.environ,{'AGENT_TOKEN':'synthetic-owner'},clear=True):
   with self.assertRaises(ValueError):owner_action.execute({'action':'connect-telegram','context':{'assistant':'pocket-notes'},'fields':{'bot_token':'synthetic'}})
 def test_activation_matches_reviewed_identity(self):
  import owner_action
  with patch.dict(os.environ,{'AGENT_TOKEN':'synthetic-owner'},clear=True),patch.object(owner_action,'call',return_value={'epoch':'e','username':'fixture_bot','app_id':24}) as call,patch.object(owner_action,'api') as api:
   with self.assertRaises(ValueError):owner_action.execute({'action':'activate-telegram','context':{'assistant':'pocket-notes','epoch':'old','bot':'fixture_bot'},'fields':{}})
   self.assertEqual(call.call_count,1)
   owner_action.execute({'action':'activate-telegram','context':{'assistant':'pocket-notes','epoch':'e','bot':'fixture_bot'},'fields':{}})
   self.assertTrue(call.call_args_list[-2].args[0].endswith('/activate'))
   api.assert_called_once_with('/api/apps/24/run-job',{},'POST')
 def test_app_scoped_process_cannot_use_owner_action(self):
  import owner_action
  with patch.dict(os.environ,{'AGENT_TOKEN':'synthetic-owner','APP_TOKEN':'synthetic'},clear=True):
   with self.assertRaises(ValueError):owner_action.execute({})

 def test_approved_private_start_wakes_normal_supervisor_only(self):
  import owner_action
  context={'assistant':'pocket-notes','revision':'reviewed','epoch':'e','test_epoch':None,'generation':0}
  with patch.dict(os.environ,{'AGENT_TOKEN':'synthetic-owner'},clear=True),patch.object(owner_action,'call',return_value={'app_id':24}) as call,patch.object(owner_action,'api') as api:
   owner_action.execute({'action':'test-draft','context':context,'fields':{}})
   self.assertEqual(call.call_args_list[0].args,('assistants/pocket-notes/development/test',context))
   api.assert_called_once_with('/api/apps/24/run-job',{},'POST')
 def test_failed_private_consent_never_wakes_worker(self):
  import owner_action
  with patch.dict(os.environ,{'AGENT_TOKEN':'synthetic-owner'},clear=True),patch.object(owner_action,'call',side_effect=ValueError),patch.object(owner_action,'api') as api:
   with self.assertRaises(ValueError):owner_action.execute({'action':'test-draft','context':{'assistant':'pocket-notes'},'fields':{}})
   api.assert_not_called()
