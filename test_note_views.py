import json
import tempfile
import time
import unittest
from datetime import datetime,timezone as utc
from assistant_store import Store,Problem
from service import dispatch
from telegram_store import member,schema,handle_update,reply
from telegram_notes import render,theme_id
from note_views import preferences,saved_at,digest_notes,cutoff,kind
from note_management import browse
from worker import task_material,process_one

class NoteViewTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name);schema(self.s);self.s.publish();member(self.s,77,77,77);self.p='owner-test'
 def tearDown(self):self.s.db.close();self.tmp.cleanup()
 def note(self,text='Fixture',at=None,p=None):
  n=self.s.note(p or self.p,{'text':text,'request_id':str(time.time_ns())})
  if at is not None:
   with self.s.db:self.s.db.execute('UPDATE notes SET created=? WHERE id=?',(at,n['id']))
   n['created']=at
  return n
 def cb(self,i,action):return {'update_id':i,'callback_query':{'id':str(i),'data':'pn:'+action,'from':{'id':77},'message':{'message_id':1,'chat':{'id':77,'type':'private'}}}}
 def text(self,i,text):return {'update_id':i,'message':{'message_id':i,'text':text,'from':{'id':77},'chat':{'id':77,'type':'private'}}}
 def test_dates_keep_original_timestamp_and_dst(self):
  n=self.note(at=datetime(2026,7,1,12,tzinfo=utc.utc).timestamp());before=n['created']
  self.assertIn('13:00 Europe/London',saved_at(n,'Europe/London',True));self.assertEqual(n['created'],before)
  n['created']=datetime(2026,1,1,12,tzinfo=utc.utc).timestamp();self.assertIn('12:00 Europe/London',saved_at(n,'Europe/London',True))
 def test_today_uses_participant_midnight(self):
  at=datetime(2026,7,1,23,30,tzinfo=utc.utc).timestamp()
  self.assertEqual(cutoff('today','Europe/London',at),datetime(2026,7,1,23,tzinfo=utc.utc).timestamp())
 def test_preferences_isolated_and_invalid_not_saved(self):
  preferences(self.s,self.p,{'timezone':'Europe/London'});self.assertEqual(preferences(self.s,'guest-test')['timezone'],'UTC')
  with self.assertRaises(Problem):preferences(self.s,self.p,{'timezone':'not-a-timezone'})
  self.assertEqual(preferences(self.s,self.p)['timezone'],'Europe/London')
 def test_type_detection_no_network(self):
  self.assertEqual(kind({'url':'https://www.youtube.com/shorts/x'}),'video');self.assertEqual(kind({'url':'https://example.com'}),'link');self.assertEqual(kind({}),'note')
 def test_browse_filters_and_private_search(self):
  n=self.note('Reading a book');self.s.tag(self.p,{'id':n['id'],'tags':['reading']});self.note('https://youtu.be/x');self.note('PRIVATE_OTHER',p='guest-test')
  self.assertEqual(browse(self.s,self.p,{'theme':'reading'})['total'],1)
  self.assertEqual(browse(self.s,self.p,{'type':'video'})['total'],1)
  self.assertEqual(browse(self.s,self.p,{'query':'PRIVATE_OTHER'})['total'],0)
 def test_ordinary_reply_has_no_navigation(self):
  reply(self.s,'fixture',77,'Saved.')
  body=json.loads(self.s.db.execute('SELECT body FROM tg_deliveries').fetchone()[0])
  self.assertNotIn('reply_markup',body)
 def test_browse_hides_destructive_controls(self):
  self.note();text,rows=render(self.s,self.p,'n:0');labels=[b['text'] for row in rows for b in row]
  self.assertEqual(labels,['More']);self.assertNotIn('Clear all',labels);self.assertIn('Newest first',text)
 def test_metadata_detail(self):
  n=self.note('Fixture title');text,rows=render(self.s,self.p,'v:'+n['id']);self.assertIn('Saved ',text);self.assertIn('UTC',text);self.assertIn('Text notes',text)
 def test_no_model_for_digest_controls(self):
  self.note()
  for action in ['digest','digestperiod:30','group:type','group:theme','digestpage:1']:
   handle_update(self.s,self.cb(100+len(action),action),77)
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
 def test_digest_snapshot_period_and_group(self):
  old=self.note('Old link https://example.com',time.time()-15*86400);new=self.note('Recent',time.time())
  render(self.s,self.p,'digestperiod:30');render(self.s,self.p,'group:type')
  render(self.s,self.p,'generate','task-1');render(self.s,self.p,'digestperiod:7')
  task=self.s.take_task();self.assertEqual(json.loads(task['options'])['period'],'30');material,_=task_material(self.s,task);self.assertEqual(len(material),2);self.assertIn('saved_local',material[0])
 def test_digest_does_not_mix_collections_or_trash(self):
  n=self.note();self.note('OTHER',p='guest-test')
  rows,total=digest_notes(self.s,self.p,{'period':'all'},time.time());self.assertEqual(total,1)
  from note_management import prepare,confirm
  confirm(self.s,self.p,prepare(self.s,self.p,{'all':True}));self.assertEqual(digest_notes(self.s,self.p,{'period':'all'},time.time())[1],0)
 def test_digest_cap_is_explicit_and_newest_before_grouping(self):
  for i in range(45):self.note('Fixture '+str(i),time.time()-i)
  rows,total=digest_notes(self.s,self.p,{'period':'all','group':'type'},time.time());self.assertEqual(total,45);self.assertEqual(len(rows),40)
  text,_=render(self.s,self.p,'digest');self.assertIn('latest 40',text)
 def test_theme_input_and_replay_never_capture(self):
  n=self.note();handle_update(self.s,self.cb(1,'themeset:'+n['id']),77)
  u=self.text(2,'Reading, Work');handle_update(self.s,u,77);handle_update(self.s,u,77)
  self.assertEqual(len(self.s.notes(self.p)),1);self.assertEqual(self.s.notes(self.p)[0]['tags'],['reading','work'])
  text,_=render(self.s,self.p,'theme:'+theme_id('reading'));self.assertIn('Fixture',text)
 def test_search_input_and_cancel(self):
  self.note('Find me');render(self.s,self.p,'search');handle_update(self.s,self.text(3,'Find'),77)
  self.assertEqual(preferences(self.s,self.p)['query'],'Find');self.assertEqual(len(self.s.notes(self.p)),1)
  render(self.s,self.p,'timezone');handle_update(self.s,self.text(4,'/cancel'),77);self.assertIsNone(self.s.db.execute('SELECT mode FROM tg_members').fetchone()[0])
 def test_all_callback_payloads_fit(self):
  n=self.note();self.s.tag(self.p,{'id':n['id'],'tags':['reading']})
  for action in ['n:0','v:'+n['id'],'filter','themes','prefs','digest','select:0','manage','d:'+n['id']]:
   text,rows=render(self.s,self.p,action);self.assertLessEqual(len(text),3500)
   for row in rows:
    for b in row:self.assertLessEqual(len(b['callback_data'].encode()),64)
 def test_management_cannot_impersonate_even_with_valid_session(self):
  session=self.s.issue_session(self.p);n=self.note('PRIVATE_SENTINEL')
  for actor in ({'scope':'owner'},{'scope':'agent'},{'scope':'app','app_id':0}):
   for prefix in ('','assistants/pocket-notes/'):
    for path in ('admin/session','admin/notes','admin/notes/confirm','participant/state','participant/export','participant/save','participant/ask'):
     with self.assertRaises(Problem) as err:dispatch(self.s,{'actor':actor,'method':'POST','path':prefix+path,'body':{'session':session,'participant':self.p}})
     self.assertEqual(err.exception.status,403)
  state=dispatch(self.s,{'actor':{'scope':'owner'},'method':'GET','path':'admin/state'})
  self.assertNotIn('participants',state);self.assertNotIn('PRIVATE_SENTINEL',json.dumps(state));self.assertEqual(self.s.notes(self.p)[0]['id'],n['id'])
