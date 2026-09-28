import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from assistant_store import Store, Problem
from service import dispatch
from worker import process_one, enrich_one, task_material, PageText, LinkContent
from text_runner import command, parse_output, runtime_environment, failure_message

class PrototypeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)
  self.a=self.store.issue_session('owner-test');self.b=self.store.issue_session('guest-test')
 def tearDown(self):self.store.db.close();self.tmp.cleanup()
 def call(self,path,body=None,**overrides):
  req={'path':path,'method':'POST','body':body or {},'public':False,'actor':{'scope':'app','app_id':int(__import__('os').environ.get('APP_ID','0')),'delegated':False}}
  req.update(overrides);return dispatch(self.store,req)
 def save(self,who='owner-test',text='My website should have a quiet green homepage.',key='a'):
  return self.store.note(who,{'text':text,'request_id':key})
 def task(self,who='owner-test',kind='ask',key='q'):
  self.store.publish();return self.store.enqueue(who,{'text':'What about my website?','kind':kind,'request_id':key})
 def test_notes_survive_reopen(self):
  self.save();other=Store(self.tmp.name)
  self.assertEqual(other.notes('owner-test')[0]['original'],'My website should have a quiet green homepage.');other.db.close()
 def test_duplicate_capture_once(self):
  self.assertEqual(self.save()['id'],self.save()['id']);self.assertEqual(len(self.store.notes('owner-test')),1)
 def test_separate_collections(self):
  self.save();self.assertEqual(self.store.overview('guest-test')['notes'],[])
  with self.assertRaises(Problem):self.call('participant/state',{'session':self.b})
 def test_body_cannot_impersonate(self):
  with self.assertRaises(Problem):self.call('participant/save',{'session':self.a,'participant':'guest-test','text':'Private A','request_id':'x'})
  self.assertEqual(self.store.notes('guest-test'),[])
 def test_cross_scope_search_empty(self):
  self.save();self.assertEqual(self.store.retrieve('guest-test','website'),[])
  with self.assertRaises(Problem):self.call('participant/search',{'session':self.b,'text':'website'})
 def test_cross_scope_export_empty(self):
  self.save();self.assertEqual(self.store.notes('guest-test'),[])
  with self.assertRaises(Problem):self.call('participant/export',{'session':self.b})
 def test_unknown_session_denied(self):
  with self.assertRaises(Problem):self.call('participant/state',{'session':'nope'})
 def test_expired_session_denied(self):
  with self.store.db:self.store.db.execute('UPDATE sessions SET expires=0')
  with self.assertRaises(Problem):self.store.resolve(self.a)
 def test_revocation_cancels_sessions_tasks(self):
  self.task();self.store.revoke('owner-test')
  with self.assertRaises(Problem):self.store.resolve(self.a)
  self.assertEqual(self.store.db.execute('SELECT status FROM tasks').fetchone()[0],'cancelled')
 def test_public_admin_denied(self):
  with self.assertRaises(Problem):self.call('admin/publish',public=True)
 def test_public_participant_denied_even_with_session(self):
  with self.assertRaises(Problem):self.call('participant/state',{'session':self.a},public=True)
 def test_delegated_admin_denied(self):
  with self.assertRaises(Problem):self.call('admin/publish',actor={'scope':'app','delegated':True})
 def test_export_definition_no_private_notes(self):
  self.save(text='SECRET_SENTINEL')
  self.assertNotIn('SECRET_SENTINEL',json.dumps(self.call('admin/export',method='GET')))
 def test_release_immutable(self):
  self.store.publish();d=self.store.config('draft');d['name']='Different';self.store.save_draft(d)
  self.assertEqual(self.store.definition(1)['name'],'Pocket Notes')
 def test_task_pins_version(self):
  self.task();d=self.store.config('draft');d['instructions']='NEW INSTRUCTIONS';self.store.save_draft(d);self.store.publish()
  self.assertEqual(self.store.take_task()['version'],1)
 def test_duplicate_task_once(self):
  self.store.publish();a=self.store.enqueue('owner-test',{'text':'Hi','request_id':'x'});b=self.store.enqueue('owner-test',{'text':'Hi','request_id':'x'})
  self.assertEqual(a['id'],b['id'])
 def test_bounded_pending(self):
  self.task(key='1');self.task(key='2')
  with self.assertRaises(Problem):self.task(key='3')
 def test_stop_cannot_cancel_other_participant(self):
  task=self.task();self.store.cancel('guest-test',task['id']);self.assertEqual(self.store.take_task()['id'],task['id'])
 def test_stop_prevents_late_result(self):
  task=self.task();running=self.store.take_task();self.store.cancel('owner-test',task['id']);self.store.finish(running,'late',[])
  self.assertEqual(self.store.overview('owner-test')['tasks'][0]['status'],'cancelled')
  self.assertIsNone(self.store.overview('owner-test')['tasks'][0]['output'])
 def test_revoked_task_not_claimed(self):
  self.task();self.store.revoke('owner-test');self.assertIsNone(self.store.take_task())
 def test_interrupted_work_is_not_silently_replayed(self):
  self.task();self.store.take_task()
  with self.store.db:self.store.db.execute('UPDATE tasks SET started=0')
  self.assertIsNone(self.store.take_task());self.assertEqual(self.store.overview('owner-test')['tasks'][0]['status'],'error')
 def test_model_gets_only_selected_collection(self):
  self.save(text='OWNER_SENTINEL');self.save('guest-test','GUEST_SECRET','b');self.task()
  def fake(prompt,model,alive):
   self.assertIn('OWNER_SENTINEL',prompt);self.assertNotIn('GUEST_SECRET',prompt);self.assertTrue(alive());return 'Your note says OWNER_SENTINEL [1].'
  process_one(self.store,fake)
  result=self.store.overview('owner-test')['tasks'][0];self.assertEqual(result['status'],'done');self.assertEqual(len(result['sources']),1)
 def test_empty_collection_does_not_call_model(self):
  self.task()
  process_one(self.store,lambda *a,**k:self.fail('model should not run'))
  self.assertEqual(self.store.overview('owner-test')['tasks'][0]['status'],'done')
 def test_weekly_digest_excludes_old_notes(self):
  self.save();self.save(key='old',text='Old thing')
  with self.store.db:self.store.db.execute("UPDATE notes SET created=0 WHERE request_id='old'")
  task=self.task(kind='digest');material,_=task_material(self.store,self.store.take_task());self.assertEqual(len(material),1)
 def test_link_failure_keeps_original(self):
  self.save(text='Read https://example.com/article')
  def fail(url):raise ValueError('blocked')
  enrich_one(self.store,fail)
  n=self.store.notes('owner-test')[0];self.assertEqual(n['original'],'Read https://example.com/article');self.assertEqual(n['enrichment'],'failed')
 def test_link_success_separate_from_original(self):
  self.save(text='https://example.com')
  enrich_one(self.store,lambda url:LinkContent('Example','Public page text',{'kind':'webpage'}))
  n=self.store.notes('owner-test')[0];self.assertEqual(n['original'],'https://example.com');self.assertEqual(n['article'],'Public page text')
 def test_html_excludes_scripts(self):
  p=PageText();p.feed('<title>Title</title><script>SECRET</script><p>Body</p>');self.assertNotIn('SECRET',''.join(p.parts))
 def test_credentials_in_url_rejected(self):
  with self.assertRaises(Problem):self.save(text='https://user:password@example.com')
 def test_model_failure_is_visible_and_note_preserved(self):
  self.save();self.task()
  def fail(*a,**k):raise RuntimeError('Model unavailable.')
  process_one(self.store,fail);self.assertEqual(len(self.store.notes('owner-test')),1);self.assertEqual(self.store.overview('owner-test')['tasks'][0]['status'],'error')
 def test_tags_cannot_edit_other_collection(self):
  n=self.save()
  with self.assertRaises(Problem):self.store.tag('guest-test',{'id':n['id'],'tags':['steal']})
 def test_tags_preserve_original(self):
  n=self.save();self.store.tag('owner-test',{'id':n['id'],'tags':['Idea','idea',' website ']})
  got=self.store.notes('owner-test')[0];self.assertEqual(got['original'],n['original']);self.assertEqual(got['tags'],['idea','website'])
 def test_followup_history_is_participant_scoped(self):
  self.save();self.task(key='first');process_one(self.store,lambda *a,**k:'Earlier owner answer')
  self.save('guest-test','Guest note','b');self.task('guest-test',key='guest');process_one(self.store,lambda *a,**k:'SECRET GUEST ANSWER')
  self.task(key='next')
  def fake(prompt,model,alive):
   self.assertIn('Earlier owner answer',prompt);self.assertNotIn('SECRET GUEST ANSWER',prompt);return 'Followup'
  process_one(self.store,fake)
 def test_cli_no_owner_or_action_tools(self):
  with patch('shutil.which',return_value='/usr/bin/codex'):args=command('test-model')
  self.assertIn('--ignore-user-config',args);self.assertIn('--ignore-rules',args)
  for feature in ('shell_tool','unified_exec','apps','view_image','hooks','multi_agent','code_mode_host'):
   i=args.index(feature);self.assertEqual(args[i-1],'--disable')
  self.assertIn('web_search="disabled"',args);self.assertIn('mcp_servers={}',args)
 def test_job_environment_resolves_platform_login(self):
  with patch.dict('os.environ',{'PATH':'/bin','HOME':'/home/job','DATA_DIR':'/fixture','APP_TOKEN':'not-for-model','AGENT_TOKEN':'not-for-model'},clear=True):
   env=runtime_environment()
  self.assertEqual(env['CODEX_HOME'],'/fixture/cli-auth/codex')
  self.assertNotIn('APP_TOKEN',env);self.assertNotIn('AGENT_TOKEN',env)
 def test_explicit_login_location_preserved(self):
  with patch.dict('os.environ',{'CODEX_HOME':'/fixture/explicit'},clear=True):
   self.assertEqual(runtime_environment()['CODEX_HOME'],'/fixture/explicit')
 def test_error_diagnostics_never_return_provider_payload(self):
  result=failure_message('','401 Unauthorized PRIVATE_SENTINEL token=secret')
  self.assertIn('authenticate',result);self.assertNotIn('PRIVATE_SENTINEL',result);self.assertNotIn('secret',result)
 def test_connection_error_categories(self):
  self.assertIn('usage limit',failure_message('429 quota exceeded',''))
  self.assertIn('unavailable',failure_message('model_not_found',''))
  self.assertIn('could not reach',failure_message('','error sending request'))
 def test_parse_public_text_only(self):
  text='\n'.join(json.dumps(v) for v in [{'type':'item.completed','item':{'type':'reasoning','text':'PRIVATE'}},{'type':'item.completed','item':{'type':'agent_message','text':'Answer'}}])
  self.assertEqual(parse_output(text),'Answer')
 def test_path_traversal_not_interpreted(self):
  self.save(text='../../private owner notes');self.assertEqual(len(self.store.notes('owner-test')),1)

if __name__=='__main__':unittest.main()
