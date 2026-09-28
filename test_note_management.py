import json
import tempfile
import unittest
from assistant_store import Store,Problem,uid
import note_management as nm
import telegram_store as tg
from telegram_notes import render

class NotesTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name);self.s.publish();tg.schema(self.s)
  tg.member(self.s,77,77,77)
  self.p='owner-test';self.other=self.s.add_person('Another')['id']
 def tearDown(self):self.s.db.close();self.tmp.cleanup()
 def note(self,text='hello',person=None):return self.s.note(person or self.p,{'text':text,'request_id':uid()})
 def test_snapshot_replay_restore(self):
  a=self.note();nm.prepare(self.s,self.p,{'ids':[a['id']]})
  pending=nm.prepare(self.s,self.p,{'all':True});b=self.note('new')
  self.assertEqual(nm.confirm(self.s,self.p,pending)['moved'],1)
  self.assertEqual(nm.confirm(self.s,self.p,pending)['moved'],1)
  self.assertEqual([n['id'] for n in self.s.notes(self.p)],[b['id']])
  self.assertFalse(nm.sources_available(self.s,self.p,[{'id':a['id']}]))
  self.assertEqual(nm.browse(self.s,self.p,{'trash':True})['total'],1)
  self.assertEqual(nm.restore(self.s,self.p,{'all':True})['restored'],1)
  self.assertEqual(len(self.s.notes(self.p)),2)
 def test_isolation(self):
  n=self.note();action=nm.prepare(self.s,self.p,{'all':True})
  for fn,arg in [(nm.prepare,{'ids':[n['id']]}),(nm.confirm,action),(nm.detail,n['id'])]:
   with self.assertRaises(Problem):fn(self.s,self.other,arg)
  self.assertEqual(nm.restore(self.s,self.other,{'id':n['id']})['restored'],0)
  self.assertEqual(nm.browse(self.s,self.other,{})['total'],0)
 def test_expired_and_cancelled_work(self):
  self.note();action=nm.prepare(self.s,self.p,{'all':True})
  with self.s.db:self.s.db.execute('UPDATE note_cleanup SET expires=0')
  with self.assertRaises(Problem):nm.confirm(self.s,self.p,action)
  self.assertEqual(len(self.s.notes(self.p)),1)
  task=self.s.enqueue(self.p,{'text':'hello?','request_id':'test'})
  action=nm.prepare(self.s,self.p,{'all':True});nm.confirm(self.s,self.p,action)
  self.assertEqual(self.s.db.execute('SELECT status FROM tasks WHERE id=?',(task['id'],)).fetchone()[0],'cancelled')
 def test_pages_search_and_trash_exclusion(self):
  for i in range(17):self.note('note '+str(i))
  self.assertEqual(len(nm.browse(self.s,self.p,{})['notes']),8)
  self.assertEqual(len(nm.browse(self.s,self.p,{'page':2})['notes']),1)
  self.assertEqual(nm.browse(self.s,self.p,{'query':'note 16'})['total'],1)
  nm.confirm(self.s,self.p,nm.prepare(self.s,self.p,{'all':True}))
  self.assertEqual(self.s.retrieve(self.p,'note',100),[])
 def test_telegram_select_confirm_restore_no_model(self):
  a=self.note('fixture')
  text,rows=render(self.s,self.p,'pick:'+a['id']+':0:1')
  self.assertIn('Select notes',text)
  text,rows=render(self.s,self.p,'selected')
  self.assertEqual(len(self.s.notes(self.p)),1)
  action=rows[0][1]['callback_data'][3:]
  render(self.s,self.p,action)
  self.assertEqual(self.s.notes(self.p),[])
  render(self.s,self.p,'restore:'+a['id'])
  self.assertEqual(len(self.s.notes(self.p)),1)
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
  for row in rows:
   for button in row:self.assertLessEqual(len(button['callback_data'].encode()),64)
 def test_telegram_my_notes_updates_in_place(self):
  self.note('fixture')
  tg.handle_update(self.s,{'update_id':1,'callback_query':{'id':'a','data':'pn:n:0','from':{'id':77},'message':{'message_id':3,'chat':{'id':77,'type':'private'}}}},77)
  from telegram_notebook import prepare_delivery
  row=prepare_delivery(self.s,self.s.db.execute("SELECT * FROM tg_deliveries WHERE purpose='panel'").fetchone())
  self.assertEqual(row['method'],'editMessageText')
  self.assertEqual(json.loads(row['body'])['message_id'],3)
  self.assertIn('Notes',json.loads(row['body'])['text'])
 def test_duplicate_selection_callback_is_idempotent(self):
  n=self.note('selection fixture')
  action='pick:'+n['id']+':0:1'
  render(self.s,self.p,action);render(self.s,self.p,action)
  selected=json.loads(self.s.db.execute('SELECT selection FROM tg_members WHERE participant=?',(self.p,)).fetchone()[0])
  self.assertEqual(selected,[n['id']])
 def test_trashed_sources_block_queued_telegram_answer(self):
  from telegram_transport import deliver
  n=self.note();task=self.s.enqueue(self.p,{'text':'Find hello?','request_id':'queued'})
  with self.s.db:self.s.db.execute("UPDATE tasks SET status='done',sources=? WHERE id=?",(json.dumps([{'id':n['id']}]),task['id']))
  conn=tg.reserve(self.s,'pocket-notes');tg.begin_verification(self.s,'pocket-notes',conn['epoch'])
  tg.verified(self.s,'pocket-notes',{'epoch':conn['epoch'],'bot_id':12345,'username':'Fixture_bot','owner_user_id':77})
  tg.activate(self.s,'pocket-notes',{'epoch':conn['epoch']})
  tg.reply(self.s,'t'+task['id'],77,'Synthetic output')
  nm.confirm(self.s,self.p,nm.prepare(self.s,self.p,{'all':True}))
  sent=[];deliver(self.s,self.s,'pocket-notes',lambda *args:sent.append(args))
  self.assertEqual(sent,[])
  self.assertEqual(self.s.db.execute('SELECT status FROM tg_deliveries').fetchone()[0],'blocked')
