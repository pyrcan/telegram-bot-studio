"""Synthetic-only notebook presentation and destructive-call contracts."""
import json
import tempfile
import time
import unittest
from unittest.mock import patch
from assistant_store import Store,Problem
import telegram_store as tg
import telegram_notebook as nb
from telegram_notes import render
from telegram_transport import deliver,TelegramError

class NotebookTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name);self.s.publish();tg.schema(self.s);tg.member(self.s,77,77,77)
        c=tg.reserve(self.s,'pocket-notes');tg.begin_verification(self.s,'pocket-notes',c['epoch'])
        tg.verified(self.s,'pocket-notes',{'epoch':c['epoch'],'bot_id':12345,'username':'Fixture_bot','owner_user_id':77});tg.activate(self.s,'pocket-notes',{'epoch':c['epoch']})
        self.p='owner-test';self.next_id=1000;self.sent=[]
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def text(self,i,text='synthetic saved thought',user=77,at=None):
        return {'update_id':i,'message':{'message_id':i,'date':int(time.time()) if at is None else at,'text':text,'from':{'id':user},'chat':{'id':user,'type':'private'}}}
    def cb(self,i,action,user=77):return {'update_id':i,'callback_query':{'id':str(i),'data':'pn:'+action,'from':{'id':user},'message':{'message_id':900,'chat':{'id':user,'type':'private'}}}}
    def send(self,slot,method,body):
        self.sent.append((method,dict(body)));self.next_id+=1
        return {'message_id':body.get('message_id',self.next_id),'date':int(time.time())} if method in ('sendMessage','editMessageText') else True
    def flush(self):deliver(self.s,self.s,'pocket-notes',self.send)
    def due(self):
        with self.s.db:self.s.db.execute('UPDATE tg_deliveries SET retry_at=0')
    def handle(self,update):tg.handle_update(self.s,update,77)
    def enable(self):render(self.s,self.p,'notebookon')
    def test_default_and_review_are_nondestructive(self):
        self.assertFalse(nb.enabled(self.s,self.p));text,rows=render(self.s,self.p,'notebookreview')
        self.assertIn('NEW save',text);self.assertFalse(nb.enabled(self.s,self.p))
        self.handle(self.text(1));self.flush();self.assertEqual(len(self.s.notes(self.p)),1)
        self.assertFalse(any(m=='deleteMessage' for m,b in self.sent))
    def test_save_receipt_panel_then_delayed_cleanup(self):
        self.enable();self.handle(self.text(1));self.assertEqual(len(self.s.notes(self.p)),1)
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM tg_deliveries WHERE purpose='delete'").fetchone()[0],0)
        self.flush();self.assertEqual(self.s.db.execute("SELECT count(*) FROM tg_deliveries WHERE purpose='delete'").fetchone()[0],2)
        self.flush();self.assertTrue(nb.member(self.s,self.p)['notebook_panel'])
        self.assertFalse(any(m=='deleteMessage' for m,b in self.sent))
        self.due();self.flush();self.assertEqual(sum(m=='deleteMessage' for m,b in self.sent),2)
        self.assertEqual(len(self.s.notes(self.p)),1)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
    def test_failed_save_does_not_delete(self):
        self.enable()
        with patch.object(self.s,'note',side_effect=Problem('Synthetic save failure')):self.handle(self.text(1))
        self.flush();self.due();self.flush()
        self.assertFalse(any(m=='deleteMessage' for m,b in self.sent));self.assertEqual(self.s.notes(self.p),[])
    def test_ambiguous_saved_reply_keeps_original(self):
        self.enable();self.handle(self.text(1))
        def uncertain(*args):raise TelegramError()
        deliver(self.s,self.s,'pocket-notes',uncertain);self.due();self.flush()
        self.assertFalse(any(m=='deleteMessage' for m,b in self.sent))
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM tg_deliveries WHERE purpose='delete'").fetchone()[0],0)
        self.assertEqual(len(self.s.notes(self.p)),1)
    def test_capture_replay_and_existing_messages_stay(self):
        self.handle(self.text(1));self.flush();self.enable();self.handle(self.text(1));self.flush()
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM tg_deliveries WHERE purpose='delete'").fetchone()[0],0)
        self.handle(self.text(2));self.handle(self.text(2));self.flush();self.flush();self.due();self.flush()
        self.assertEqual(len(self.s.notes(self.p)),2);self.assertEqual(sum(m=='deleteMessage' for m,b in self.sent),2)
        self.assertNotIn(1,[b['message_id'] for m,b in self.sent if m=='deleteMessage'])
    def test_disable_and_reenable_cannot_revive_old_cleanup(self):
        self.enable();self.handle(self.text(1));self.flush();self.flush()
        render(self.s,self.p,'notebookoff');self.enable();self.due();self.flush()
        self.assertFalse(any(m=='deleteMessage' for m,b in self.sent))
    def test_questions_and_answers_are_not_auto_deleted(self):
        self.enable();self.handle(self.text(1,'/ask what did I save?'));self.flush();self.due();self.flush()
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
        self.assertFalse(any(m=='deleteMessage' for m,b in self.sent))
    def test_commands_open_here_and_callbacks_edit_tapped_message(self):
        self.enable();self.handle(self.text(1,'/notes'));self.flush();panel=nb.member(self.s,self.p)['notebook_panel']
        self.handle(self.text(2,'/notes'));self.flush();self.handle(self.cb(3,'manage'));self.flush()
        self.assertEqual(nb.member(self.s,self.p)['notebook_panel'],900)
        panels=[(m,b) for m,b in self.sent if m!='answerCallbackQuery']
        self.assertEqual([m for m,b in panels],['sendMessage','sendMessage','editMessageText'])
        self.assertEqual(panels[-1][1]['message_id'],900)
    def test_settings_opens_here_even_without_notebook_mode(self):
        with self.s.db:self.s.db.execute('UPDATE tg_members SET notebook_panel=10')
        self.handle(self.text(100,'/settings'));self.flush()
        self.assertEqual(self.sent[-1][0],'sendMessage')
        self.assertIn('Settings',self.sent[-1][1]['text'])
        self.assertIn('Privacy',str(self.sent[-1][1]['reply_markup']))
        self.assertFalse(nb.enabled(self.s,self.p))
        self.assertEqual(self.s.notes(self.p),[])
    def test_explicit_ask_cancel_and_unknown_command(self):
        self.handle(self.text(1,'/ask'));self.flush()
        self.assertEqual(nb.member(self.s,self.p)['mode'],'ask')
        self.assertIn('Cancel',str(self.sent[-1][1]))
        self.handle(self.text(2,'/cancel'));self.flush()
        self.assertIsNone(nb.member(self.s,self.p)['mode'])
        self.handle(self.text(3,'/setings'));self.flush()
        self.assertIn('Nothing was saved',self.sent[-1][1]['text'])
        self.assertEqual(self.s.notes(self.p),[])
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
    def test_help_exits_ask_mode(self):
        self.handle(self.text(1,'/ask'));self.handle(self.text(2,'/help'))
        self.assertIsNone(nb.member(self.s,self.p)['mode'])
        self.handle(self.text(3,'a synthetic note'))
        self.assertEqual(len(self.s.notes(self.p)),1)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
    def test_ask_next_message_then_normal_capture(self):
        self.handle(self.text(1,'/ask'));self.handle(self.text(2,'my reading list'))
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
        self.assertIsNone(nb.member(self.s,self.p)['mode'])
        self.handle(self.text(3,'new synthetic note'))
        self.assertEqual(len(self.s.notes(self.p)),1)
    def test_privacy_and_chat_help_do_not_mutate_notes_or_consent(self):
        self.handle(self.text(1));self.flush()
        for action in ('privacy','chathelp'):
            self.handle(self.cb(2 if action=='privacy' else 3,action));self.flush()
        self.assertIn('Clear History',self.sent[-1][1]['text'])
        self.assertFalse(nb.enabled(self.s,self.p))
        self.assertEqual(len(self.s.notes(self.p)),1)
        self.assertFalse(any(m=='deleteMessage' for m,b in self.sent))

    def test_missing_panel_recreated_only_on_definite_rejection(self):
        self.enable();self.handle(self.text(1,'/notes'));self.flush();self.handle(self.cb(2,'n:0'))
        def missing(*args):raise TelegramError(400,reason='edit-unavailable')
        with self.s.db:self.s.db.execute('UPDATE tg_members SET notebook_panel=900')
        deliver(self.s,self.s,'pocket-notes',missing);self.assertIsNone(nb.member(self.s,self.p)['notebook_panel'])
        self.flush();self.assertIsNotNone(nb.member(self.s,self.p)['notebook_panel']);self.assertEqual(self.sent[-1][0],'sendMessage')
    def test_not_modified_is_success_no_duplicate_send(self):
        self.enable();self.handle(self.text(1,'/notes'));self.flush();self.handle(self.cb(2,'n:0'))
        def same(*args):raise TelegramError(400,reason='not-modified')
        deliver(self.s,self.s,'pocket-notes',same)
        self.assertEqual(self.s.db.execute("SELECT status FROM tg_deliveries WHERE id='u2:notes'").fetchone()[0],'sent')
    def test_manual_review_cancel_isolation_and_exact_snapshot(self):
        self.handle(self.text(1));self.flush();identity,count=nb.prepare_clean(self.s,self.p)
        self.assertEqual(count,2)
        with self.assertRaises(Problem):nb.confirm_clean(self.s,'guest-test',identity)
        nb.cancel_clean(self.s,self.p,identity)
        with self.assertRaises(Problem):nb.confirm_clean(self.s,self.p,identity)
        identity,count=nb.prepare_clean(self.s,self.p);self.handle(self.text(2));self.flush()
        nb.confirm_clean(self.s,self.p,identity);nb.confirm_clean(self.s,self.p,identity);self.flush()
        self.assertEqual(sum(m=='deleteMessage' for m,b in self.sent),2);self.assertEqual(len(self.s.notes(self.p)),2)
        self.assertNotIn(2,[b['message_id'] for m,b in self.sent if m=='deleteMessage'])
    def test_confirmation_expiry_and_telegram_age_limits(self):
        self.handle(self.text(1,at=int(time.time())-49*3600));self.flush();identity,count=nb.prepare_clean(self.s,self.p)
        self.assertEqual(count,1)
        with self.s.db:self.s.db.execute('UPDATE tg_chat_cleanup SET expires=0')
        with self.assertRaises(Problem):nb.confirm_clean(self.s,self.p,identity)
        identity,count=nb.prepare_clean(self.s,self.p);nb.confirm_clean(self.s,self.p,identity)
        with self.s.db:self.s.db.execute('UPDATE tg_message_index SET sent=?',(time.time()-49*3600,))
        self.flush();self.assertFalse(any(m=='deleteMessage' for m,b in self.sent));self.assertIn('Not removed: 1',nb.clean_status(self.s,self.p,identity))
    def test_rate_limit_retry_and_uncertain_status(self):
        self.handle(self.text(1));self.flush();identity,count=nb.prepare_clean(self.s,self.p);nb.confirm_clean(self.s,self.p,identity)
        def limited(*args):raise TelegramError(429,2)
        deliver(self.s,self.s,'pocket-notes',limited);self.assertIn('Waiting: 2',nb.clean_status(self.s,self.p,identity))
        self.due()
        def uncertain(*args):raise TelegramError()
        deliver(self.s,self.s,'pocket-notes',uncertain);self.assertIn('Unconfirmed: 2',nb.clean_status(self.s,self.p,identity))
        self.flush();self.assertFalse(any(m=='deleteMessage' for m,b in self.sent))
    def test_optin_and_cleanup_do_not_cross_participants(self):
        guest=__import__('test_access').approve_fixture(self.s)
        self.enable();self.assertFalse(nb.enabled(self.s,guest));self.handle(self.text(1,user=88));self.flush()
        with self.assertRaises(Problem):nb.prepare_clean(self.s,self.p)
        self.assertEqual(len(self.s.notes(guest)),1)
        identity,count=nb.prepare_clean(self.s,guest)
        with self.assertRaises(Problem):nb.confirm_clean(self.s,self.p,identity)
    def test_restart_keeps_consent_delays_and_receipts(self):
        self.enable();self.handle(self.text(1));self.flush();self.s.db.close();self.s=Store(self.tmp.name);tg.schema(self.s)
        self.assertTrue(nb.enabled(self.s,self.p));self.flush();self.due();self.flush()
        self.assertEqual(sum(m=='deleteMessage' for m,b in self.sent),2)
    def test_actual_cleanup_menu_callbacks_and_limits(self):
        self.handle(self.text(1));self.flush();self.handle(self.cb(2,'chatclean'));self.flush()
        body=[b for m,b in self.sent if m=='editMessageText'][-1]
        self.assertIn('Saved notes and stored answers stay',body['text'])
        action=body['reply_markup']['inline_keyboard'][1][0]['callback_data'][3:]
        self.handle(self.cb(3,action));self.flush();self.flush()
        self.assertEqual(sum(m=='deleteMessage' for m,b in self.sent),2)
        for row in body['reply_markup']['inline_keyboard']:
            for b in row:self.assertLessEqual(len(b['callback_data'].encode()),64)

    def test_failed_panel_keeps_capture(self):
        self.enable();self.handle(self.text(1));self.flush()
        def fail(*args):raise TelegramError(403)
        deliver(self.s,self.s,'pocket-notes',fail);self.due();self.flush()
        self.assertFalse(any(m=='deleteMessage' for m,b in self.sent))
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM tg_deliveries WHERE purpose='delete' AND status='blocked'").fetchone()[0],2)

    def test_deleted_note_does_not_authorize_capture_cleanup(self):
        import note_management as nm
        self.enable();self.handle(self.text(1));self.flush();self.flush()
        nm.confirm(self.s,self.p,nm.prepare(self.s,self.p,{'all':True}))
        nm.confirm_delete(self.s,self.p,nm.prepare_delete(self.s,self.p,{'all':True}))
        self.due();self.flush()
        self.assertFalse(any(m=='deleteMessage' for m,b in self.sent))

    def test_two_delivery_workers_deliver_each_command_once(self):
        self.enable();self.handle(self.text(1,'/notes'));self.handle(self.text(2,'/notes'))
        def simultaneous(slot,method,body):
            other=Store(self.tmp.name)
            try:deliver(other,other,'pocket-notes',self.send)
            finally:other.db.close()
            return self.send(slot,method,body)
        deliver(self.s,self.s,'pocket-notes',simultaneous)
        self.assertEqual(sum(m=='sendMessage' for m,b in self.sent),2)
        self.assertEqual(sum(m=='editMessageText' for m,b in self.sent),0)

    def test_revocation_blocks_queued_deletion(self):
        self.handle(self.text(1));self.flush();identity,count=nb.prepare_clean(self.s,self.p)
        nb.confirm_clean(self.s,self.p,identity);self.s.revoke(self.p);self.flush()
        self.assertFalse(any(m=='deleteMessage' for m,b in self.sent))

    def test_panel_ambiguity_never_automatically_repeats_send(self):
        self.enable();self.handle(self.text(1,'/notes'))
        def uncertain(*args):raise TelegramError()
        deliver(self.s,self.s,'pocket-notes',uncertain);self.flush()
        self.assertEqual(self.sent,[]);self.assertIsNone(nb.member(self.s,self.p)['notebook_panel'])

if __name__=='__main__':unittest.main()
