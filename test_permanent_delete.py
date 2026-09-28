"""All destructive checks use disposable notebooks and simulated Telegram."""
import json
import tempfile
import unittest
from assistant_store import Store,Problem,uid
import note_management as nm
import telegram_store as tg
from telegram_notes import render
from telegram_transport import deliver,TelegramError
from worker import enrich_one,process_one

class PermanentDeleteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name)
        self.s.publish();tg.schema(self.s);tg.member(self.s,77,77,77)
        self.p='owner-test';self.other='guest-test'
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def note(self,text='PRIVATE_FIXTURE',p=None,key=None):
        return self.s.note(p or self.p,{'text':text,'request_id':key or uid()})
    def trash(self,*notes):return nm.confirm(self.s,self.p,nm.prepare(self.s,self.p,{'ids':[n['id'] for n in notes]}))
    def prepare(self,*notes):return nm.prepare_delete(self.s,self.p,{'ids':[n['id'] for n in notes]})
    def cb(self,i,action,user=77):
        return {'update_id':i,'callback_query':{'id':str(i),'data':'pn:'+action,'from':{'id':user},'message':{'message_id':1,'text':'PRIVATE_FIXTURE','chat':{'id':user,'type':'private'}}}}
    def active_connection(self):
        c=tg.reserve(self.s,'pocket-notes');tg.begin_verification(self.s,'pocket-notes',c['epoch'])
        tg.verified(self.s,'pocket-notes',{'epoch':c['epoch'],'bot_id':12345,'username':'Fixture_bot','owner_user_id':77})
        tg.activate(self.s,'pocket-notes',{'epoch':c['epoch']})
    def test_delete_is_trash_only_scoped_snapshot_and_idempotent(self):
        a=self.note();b=self.note('Keep active');other=self.note(p=self.other)
        with self.assertRaises(Problem):self.prepare(a)
        self.trash(a);review=nm.prepare_delete(self.s,self.p,{'all':True})
        late=self.note('Trashed after review');self.trash(late)
        self.assertEqual(nm.confirm_delete(self.s,self.p,review),{'deleted':1})
        self.assertEqual(nm.confirm_delete(self.s,self.p,review),{'deleted':1})
        for n in (b,late,other):self.assertIsNotNone(self.s.db.execute('SELECT 1 FROM notes WHERE id=?',(n['id'],)).fetchone())
        self.assertEqual(nm.restore(self.s,self.p,{'id':a['id']}),{'restored':0})
        self.assertFalse(nm.sources_available(self.s,self.p,[{'id':a['id']}]))
    def test_other_participant_cannot_prepare_cancel_or_confirm(self):
        a=self.note();self.trash(a);review=self.prepare(a)
        with self.assertRaises(Problem):nm.prepare_delete(self.s,self.other,{'ids':[a['id']]})
        with self.assertRaises(Problem):nm.confirm_delete(self.s,self.other,review)
        nm.cancel_delete(self.s,self.other,review['confirmation'])
        self.assertEqual(nm.confirm_delete(self.s,self.p,review)['deleted'],1)
    def test_revoked_cannot_delete(self):
        a=self.note();self.trash(a);review=self.prepare(a);self.s.revoke(self.p)
        with self.assertRaises(Problem):nm.confirm_delete(self.s,self.p,review)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM notes').fetchone()[0],1)
    def test_operation_confusion_expiry_and_cancellation(self):
        a=self.note();soft=nm.prepare(self.s,self.p,{'all':True});self.trash(a);review=self.prepare(a)
        with self.assertRaises(Problem):nm.confirm_delete(self.s,self.p,soft)
        with self.assertRaises(Problem):nm.confirm(self.s,self.p,review)
        nm.cancel_delete(self.s,self.p,review['confirmation'])
        with self.assertRaises(Problem):nm.confirm_delete(self.s,self.p,review)
        review=self.prepare(a)
        with self.s.db:self.s.db.execute('UPDATE note_cleanup SET expires=0')
        with self.assertRaises(Problem):nm.confirm_delete(self.s,self.p,review)
    def test_restore_and_retrash_invalidate_old_confirmation_atomically(self):
        a=self.note();b=self.note();self.trash(a,b);review=self.prepare(a,b)
        nm.restore(self.s,self.p,{'id':a['id']})
        with self.assertRaises(Problem):nm.confirm_delete(self.s,self.p,review)
        self.trash(a)
        with self.assertRaises(Problem):nm.confirm_delete(self.s,self.p,review)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM notes').fetchone()[0],2)
    def test_purge_clears_metadata_tasks_previews_not_other_people(self):
        a=self.note('PRIVATE_FIXTURE https://example.com');other=self.note('OTHER_CONTENT',self.other)
        with self.s.db:self.s.db.execute("UPDATE notes SET article='PRIVATE_FIXTURE',metadata=?,tags=? WHERE id=?",(json.dumps({'title':'PRIVATE_FIXTURE'}),json.dumps(['PRIVATE_FIXTURE']),a['id']))
        task=self.s.enqueue(self.p,{'text':'PRIVATE_FIXTURE?','request_id':'q'})
        other_task=self.s.enqueue(self.other,{'text':'OTHER_CONTENT?','request_id':'q'})
        with self.s.db:
            self.s.db.execute("UPDATE tasks SET status='done',output='PRIVATE_FIXTURE',sources=? WHERE id=?",(json.dumps([{'id':a['id'],'title':'PRIVATE_FIXTURE'}]),task['id']))
            self.s.db.execute('INSERT INTO tg_requests VALUES (?,77,0)',(task['id'],))
            self.s.db.execute('INSERT INTO tg_inputs VALUES (20,?,?)',(self.p,'PRIVATE_FIXTURE'))
        tg.reply(self.s,'preview',77,'PRIVATE_FIXTURE');tg.reply(self.s,'other',88,'OTHER_CONTENT')
        tg.ingest(self.s,[self.cb(40,'trash:0')]);self.trash(a)
        nm.confirm_delete(self.s,self.p,self.prepare(a));tg.collect_answers(self.s)
        own=self.s.db.execute('SELECT * FROM tasks WHERE id=?',(task['id'],)).fetchone()
        self.assertEqual(own['input'],'');self.assertIsNone(own['output']);self.assertEqual(own['sources'],'[]')
        self.assertEqual(self.s.db.execute('SELECT input FROM tasks WHERE id=?',(other_task['id'],)).fetchone()[0],'OTHER_CONTENT?')
        self.assertNotIn('PRIVATE_FIXTURE', '\n'.join(self.s.db.iterdump()))
        self.assertIn('OTHER_CONTENT', '\n'.join(self.s.db.iterdump()))
    def test_capture_tombstone_survives_replay_and_reopen(self):
        a=self.note(key='telegram:9');self.trash(a);nm.confirm_delete(self.s,self.p,self.prepare(a))
        with self.assertRaises(Problem):self.note(key='telegram:9')
        with self.s.db:self.s.db.execute("UPDATE tg_members SET mode='ask'")
        tg.handle_update(self.s,{'update_id':9,'message':{'text':'PRIVATE_FIXTURE','from':{'id':77},'chat':{'id':77,'type':'private'}}},77)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
        self.s.db.close();self.s=Store(self.tmp.name)
        with self.assertRaises(Problem):self.note(key='telegram:9')
        self.note(key='telegram:10');self.assertEqual(len(self.s.notes(self.p)),1)
    def test_running_answer_cannot_repopulate_after_purge(self):
        a=self.note();review=None
        self.s.enqueue(self.p,{'text':'Find fixture?','request_id':'model'})
        def runner(*args,**kwargs):
            self.trash(a);nm.confirm_delete(self.s,self.p,self.prepare(a))
            self.assertFalse(kwargs['alive']());return 'PRIVATE_FIXTURE'
        process_one(self.s,runner)
        row=self.s.db.execute('SELECT status,output,sources FROM tasks').fetchone()
        self.assertEqual(tuple(row),('cancelled',None,'[]'))
    def test_queued_and_inflight_rate_limited_delivery_cannot_retry(self):
        self.active_connection();a=self.note();self.trash(a);review=self.prepare(a)
        tg.reply(self.s,'preview',77,'PRIVATE_FIXTURE')
        def send(*args):
            nm.confirm_delete(self.s,self.p,review);raise TelegramError(429,1)
        deliver(self.s,self.s,'pocket-notes',send)
        row=self.s.db.execute('SELECT body,status FROM tg_deliveries').fetchone()
        self.assertEqual(tuple(row),('{}','blocked'))
        calls=[];deliver(self.s,self.s,'pocket-notes',lambda *a:calls.append(a));self.assertEqual(calls,[])
    def test_browse_select_cancel_and_confirm_via_actual_update_handler(self):
        a=self.note();b=self.note();self.trash(a,b)
        for i,action in enumerate(('trash:0','trashselect:0','trashpick:'+a['id']+':0:1','purgeselected'),100):
            tg.handle_update(self.s,self.cb(i,action),77)
        payload=json.loads(self.s.db.execute("SELECT body FROM tg_deliveries WHERE id='u103:notes'").fetchone()[0])
        self.assertIn('cannot be undone',payload['text']);self.assertIn('AI answer history',payload['text'])
        buttons=[b for row in payload['reply_markup']['inline_keyboard'] for b in row]
        confirm=buttons[1]['callback_data'][3:];cancel=buttons[0]['callback_data'][3:]
        tg.handle_update(self.s,self.cb(104,cancel),77)
        with self.assertRaises(Problem):render(self.s,self.p,confirm)
        text,rows=render(self.s,self.p,'purgeselected');confirm=rows[1][0]['callback_data'][3:]
        tg.ingest(self.s,[self.cb(105,confirm)]);tg.consume(self.s,77)
        self.assertEqual(nm.browse(self.s,self.p,{'trash':True})['total'],1)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
        for b in buttons:self.assertLessEqual(len(b['callback_data'].encode()),64)
    def test_trash_pages_and_empty_trash_controls(self):
        saved=[self.note(str(i)) for i in range(10)];self.trash(*saved)
        text,rows=render(self.s,self.p,'trash:0');self.assertNotIn('Empty Trash',[b['text'] for r in rows for b in r])
        _,rows=render(self.s,self.p,'trashmanage');self.assertIn('Empty Trash',[b['text'] for r in rows for b in r])
        text,rows=render(self.s,self.p,'trashselect:1');self.assertIn('Page 2 of 2',text)
        for row in rows:
            for b in row:self.assertLessEqual(len(b['callback_data'].encode()),64)
        text,rows=render(self.s,self.p,'emptytrash');self.assertIn('delete 10 notes',text)
        render(self.s,self.p,rows[1][0]['callback_data'][3:])
        text,rows=render(self.s,self.p,'trash:0');self.assertIn('Trash is empty',text)
        self.assertNotIn('Empty Trash',[b['text'] for r in rows for b in r])
    def test_migration_keeps_old_trash_receipt(self):
        a=self.note();review=nm.prepare(self.s,self.p,{'all':True})
        self.s.db.close();self.s=Store(self.tmp.name)
        self.assertEqual(nm.confirm(self.s,self.p,review)['moved'],1)
        self.assertEqual(nm.restore(self.s,self.p,{'all':True})['restored'],1)

    def test_background_enrichment_cannot_recreate_deleted_note(self):
        from link_metadata import LinkContent
        a=self.note('https://example.com/fixture')
        def reader(url):
            other=Store(self.tmp.name)
            try:
                nm.confirm(other,self.p,nm.prepare(other,self.p,{'ids':[a['id']]}))
                nm.confirm_delete(other,self.p,nm.prepare_delete(other,self.p,{'all':True}))
            finally:other.db.close()
            return LinkContent('PRIVATE_FIXTURE','PRIVATE_FIXTURE',{'title':'PRIVATE_FIXTURE'})
        enrich_one(self.s,reader)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM notes').fetchone()[0],0)

    def test_actual_pre_purge_schema_migrates_without_losing_receipts(self):
        import sqlite3
        from pathlib import Path
        root=Path(self.tmp.name)/'legacy';root.mkdir()
        db=sqlite3.connect(root/'assistants.sqlite3')
        db.execute('CREATE TABLE note_cleanup(id TEXT PRIMARY KEY,participant TEXT NOT NULL,notes TEXT NOT NULL,expires REAL NOT NULL,completed INTEGER)')
        db.execute("INSERT INTO note_cleanup VALUES ('legacy','owner-test','[]',9999999999,0)");db.commit();db.close()
        migrated=Store(root)
        try:
            self.assertEqual(migrated.db.execute('SELECT operation FROM note_cleanup').fetchone()[0],'trash')
            self.assertEqual(nm.confirm(migrated,self.p,{'confirmation':'legacy'}),{'moved':0})
        finally:migrated.db.close()

    def test_active_notes_menu_has_no_permanent_delete(self):
        a=self.note()
        for action in ('n:0','manage','v:'+a['id']):
            text,rows=render(self.s,self.p,action)
            self.assertFalse(any(b['callback_data'].startswith(('pn:purge','pn:emptytrash')) for r in rows for b in r))

if __name__=='__main__':unittest.main()
