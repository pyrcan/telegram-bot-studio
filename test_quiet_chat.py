"""Quiet presentation over synthetic participants; no network or model calls."""
import json
import tempfile
import time
import unittest
from assistant_store import Store,Problem
import telegram_store as tg
import note_management as nm
from telegram_notes import render
from note_views import preferences

class QuietChatTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name);self.s.publish();tg.schema(self.s);tg.member(self.s,77,77,77);self.p='owner-test'
 def tearDown(self):self.s.db.close();self.tmp.cleanup()
 def note(self,text,who=None):return self.s.note(who or self.p,{'text':text,'request_id':str(time.time_ns())})
 def labels(self,rows):return [b['text'] for r in rows for b in r]
 def actions(self,rows):return [b['callback_data'][3:] for r in rows for b in r]
 def test_single_reader_bounds_and_context_controls(self):
  a=self.note('first fixture');b=self.note('second fixture');c=self.note('third fixture')
  text,rows=render(self.s,self.p,'n:0')
  self.assertIn('third fixture',text);self.assertNotIn('second fixture',text);self.assertIn('1 of 3',text)
  self.assertEqual(self.labels(rows),['Next ›','More'])
  text,rows=render(self.s,self.p,'n:1');self.assertIn('second fixture',text)
  self.assertEqual(self.labels(rows),['‹ Previous','Next ›','More'])
  text,rows=render(self.s,self.p,'n:99');self.assertIn('first fixture',text);self.assertEqual(self.labels(rows),['‹ Previous','More'])
 def test_more_actions_keep_exact_note_after_new_save(self):
  a=self.note('original');_,rows=render(self.s,self.p,'n:0');more=self.actions(rows)[-1]
  self.note('new incoming note');_,rows=render(self.s,self.p,more)
  self.assertIn('noteactions:'+a['id'],self.actions(rows))
  _,rows=render(self.s,self.p,'noteactions:'+a['id']);review=next(x for x in self.actions(rows) if x.startswith('d:'))
  text,rows=render(self.s,self.p,review);self.assertEqual(len(self.s.notes(self.p)),2)
  render(self.s,self.p,self.actions(rows)[1]);self.assertEqual([n['title'] for n in self.s.notes(self.p)],['new incoming note'])
 def test_reader_and_more_are_participant_scoped(self):
  foreign=self.note('PRIVATE_OTHER','guest-test');self.note('own note')
  self.assertNotIn('PRIVATE_OTHER',render(self.s,self.p,'n:0')[0])
  for action in ('v:','more:','noteactions:','trashmore:'):
   with self.assertRaises(Problem):render(self.s,self.p,action+foreign['id'])
 def test_filtered_single_reader_and_back_to_current_note(self):
  a=self.note('matching note');self.note('excluded note');preferences(self.s,self.p,{'query':'matching'})
  text,rows=render(self.s,self.p,'n:0');self.assertIn('1 of 1',text);self.assertIn('matching',text)
  self.assertEqual(self.labels(rows),['More'])
  preferences(self.s,self.p,{'query':'no match'})
  text,rows=render(self.s,self.p,'v:'+a['id']);self.assertIn('outside current filters',text);self.assertIn('matching note',text)
 def test_digest_no_duplicate_note_buttons_and_options_remain(self):
  for i in range(10):self.note('fixture '+str(i))
  text,rows=render(self.s,self.p,'digest');self.assertLessEqual(len(self.labels(rows)),3)
  self.assertEqual(self.labels(rows),['Next →','AI summary','Options'])
  self.assertNotIn('Date',self.labels(rows));self.assertNotIn('Notes',self.labels(rows))
  _,rows=render(self.s,self.p,'digestoptions');self.assertEqual(self.labels(rows),['Date range','Group by','Back to digest'])
  render(self.s,self.p,'digestperiod:30');render(self.s,self.p,'group:theme')
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
 def test_ordinary_save_and_answer_are_button_free(self):
  update={'update_id':1,'message':{'message_id':1,'text':'synthetic saved thought','from':{'id':77},'chat':{'id':77,'type':'private'}}}
  tg.handle_update(self.s,update,77)
  task=self.s.enqueue(self.p,{'text':'find it','request_id':'fixture-answer'})
  with self.s.db:
   self.s.db.execute("UPDATE tasks SET status='done',output='Synthetic answer' WHERE id=?",(task['id'],))
   self.s.db.execute('INSERT INTO tg_requests(task_id,chat_id) VALUES (?,?)',(task['id'],77))
  tg.collect_answers(self.s)
  for row in self.s.db.execute('SELECT body FROM tg_deliveries'):self.assertNotIn('reply_markup',json.loads(row[0]))
 def test_trash_is_single_note_and_cleanup_stays_explicit(self):
  a=self.note('one');b=self.note('two');nm.confirm(self.s,self.p,nm.prepare(self.s,self.p,{'all':True}))
  text,rows=render(self.s,self.p,'trash:0');self.assertIn('1 of 2',text);self.assertEqual(self.labels(rows),['Next ›','More'])
  _,rows=render(self.s,self.p,'trashmore:'+a['id']);self.assertIn('Delete permanently',self.labels(rows))
  self.assertEqual(nm.browse(self.s,self.p,{'trash':True})['total'],2)
  render(self.s,self.p,'trashmanage');self.assertEqual(nm.browse(self.s,self.p,{'trash':True})['total'],2)
 def test_every_new_route_has_bounded_payload_and_no_global_footer(self):
  a=self.note('fixture')
  for action in ['n:0','more:'+a['id'],'noteactions:'+a['id'],'filter','filterdates','filtertypes','digestoptions','digestdates','digestgroups','prefs','privacy','chathelp']:
   text,rows=render(self.s,self.p,action);self.assertLessEqual(len(text),3500)
   self.assertNotIn('Ask',self.labels(rows));self.assertNotIn('Digest',self.labels(rows))
   for r in rows:
    for b in r:self.assertLessEqual(len(b['callback_data'].encode()),64)
