"""Invitation → Start → first save, using isolated stores and no live messages."""
import json
import threading
import unittest
from unittest.mock import Mock
import test_telegram as fixture
from assistant_store import Store, Problem
from catalog import Catalog
import telegram_store as tg
import telegram_menu as menu
import telegram_onboarding as onboarding

class OnboardingTests(unittest.TestCase):
    setUp=fixture.TelegramTests.setUp
    tearDown=fixture.TelegramTests.tearDown
    update=fixture.TelegramTests.update
    handle=fixture.TelegramTests.handle
    def reply(self,mid):
        return json.loads(self.store.db.execute('SELECT body FROM tg_deliveries WHERE id=?',(f'u{mid}:0',)).fetchone()[0])
    def join(self,user=88,mid=1):
        __import__('test_access').approve_fixture(self.store,user);self.handle(mid,'/start',user)
        return self.store.db.execute('SELECT participant FROM tg_members WHERE user_id=?',(user,)).fetchone()[0]
    def test_invited_start_is_ready_without_extra_setup_or_model_work(self):
        self.store.note('owner-test',{'text':'operator-only sentinel','request_id':'fixture-owner'})
        participant=self.join();body=self.reply(1)
        self.assertIn('Your notebook is ready',body['text']);self.assertIn('first note',body['text'])
        self.assertIn('operator can access',body['text']);self.assertIn('/privacy',body['text'])
        self.assertNotIn('sentinel',body['text']);self.assertNotIn('reply_markup',body)
        self.assertNotEqual(participant,'owner-test');self.assertEqual(self.store.notes(participant),[])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT notebook_since FROM tg_members').fetchone()[0],0)
    def test_first_save_hint_is_scoped_quiet_and_only_for_first_note(self):
        first=self.join();second=self.join(99,2)
        self.handle(3,'A synthetic reading idea',88);self.handle(4,'Another thought',88);self.handle(5,'A different notebook',99)
        self.assertIn('Your first note is saved',self.reply(3)['text'])
        self.assertNotIn('first note',self.reply(4)['text'])
        self.assertIn('Your first note is saved',self.reply(5)['text'])
        self.assertEqual(len(self.store.notes(first)),2);self.assertEqual(len(self.store.notes(second)),1)
        for mid in (3,4,5):self.assertNotIn('reply_markup',self.reply(mid))
    def test_returning_start_reuses_notebook_and_keeps_preferences(self):
        participant=self.join();self.handle(2,'An existing note',88)
        from note_views import preferences
        preferences(self.store,participant,{'timezone':'Europe/London'})
        self.handle(3,'/start',88)
        self.assertIn('Welcome back',self.reply(3)['text'])
        self.assertEqual(len(self.store.notes(participant)),1)
        self.assertEqual(preferences(self.store,participant)['timezone'],'Europe/London')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM tg_members').fetchone()[0],1)
    def test_no_invite_expired_and_revoked_do_not_admit_or_run_models(self):
        self.handle(1,'/start',88);self.assertIn('private pilot',self.reply(1)['text'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM tg_members').fetchone()[0],0)
        code=tg.invite(self.store)
        with self.store.db:self.store.db.execute('UPDATE tg_invites SET expires=0')
        self.handle(2,'/start '+code,88);self.assertIn('Request access',self.reply(2)['text'])
        participant=self.join(88,3);self.store.revoke(participant);self.handle(4,'/start '+tg.invite(self.store),88)
        self.assertIn('not approved',self.reply(4)['text'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
    def test_replayed_admission_and_first_save_do_not_duplicate(self):
        __import__('test_access').approve_fixture(self.store);code=tg.invite(self.store);update=self.update(1,'/start '+code,88)
        tg.handle_update(self.store,update,77);tg.handle_update(self.store,update,77)
        self.handle(2,'First saved note',88)
        # Simulate the crash gap after the note commit but before its receipt.
        with self.store.db:self.store.db.execute("DELETE FROM tg_deliveries WHERE id='u2:0'")
        tg.handle_update(self.store,self.update(2,'First saved note',88),77)
        self.assertIn('first note',self.reply(2)['text'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM notes').fetchone()[0],1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM tg_members').fetchone()[0],1)
    def test_help_is_available_without_starting_a_tutorial_or_task(self):
        self.join();self.handle(2,'/help',88)
        self.assertIn('/ask',self.reply(2)['text']);self.assertIn('/privacy',self.reply(2)['text'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
    def test_catalog_preview_contains_product_copy_not_participant_content(self):
        self.join();self.handle(2,'private-participant-sentinel',88)
        value=Catalog(self.store).summary('pocket-notes')
        self.assertEqual(value['onboarding']['welcome'],onboarding.welcome(value['published_name']))
        self.assertNotIn('sentinel',json.dumps(value));self.assertNotIn('participants',value)
    def test_profile_registration_is_bounded_and_message_free(self):
        self.assertLessEqual(len(onboarding.DESCRIPTION),512);self.assertLessEqual(len(onboarding.SHORT_DESCRIPTION),120)
        send=Mock();menu.sync(self.store,self.conn,send);menu.sync(self.store,self.conn,send)
        self.assertEqual([c.args[1] for c in send.call_args_list],['setMyCommands','setChatMenuButton','setMyDescription','setMyShortDescription'])
        self.assertIn('Private pilot',onboarding.DESCRIPTION)
    def test_partial_profile_failure_remains_retryable_without_false_ready(self):
        calls=[]
        def fail(slot,method,body):
            calls.append(method)
            if method=='setMyShortDescription':raise RuntimeError('synthetic failure')
        menu.sync(self.store,self.conn,fail)
        self.assertIsNone(self.store.config('telegram_menu'))
        self.store.set_config('telegram_menu_retry_at',0);send=Mock();menu.sync(self.store,self.conn,send)
        self.assertEqual(self.store.config('telegram_menu')['version'],menu.VERSION)
    def test_two_people_cannot_redeem_the_same_invitation(self):
        code=tg.invite(self.store);barrier=threading.Barrier(2);outcomes=[]
        def redeem(user):
            s=Store(self.temp.name)
            try:
                barrier.wait()
                try:outcomes.append(('joined',tg.member(s,user,user,77,code)['participant']))
                except Problem:outcomes.append(('denied',None))
            finally:s.db.close()
        threads=[threading.Thread(target=redeem,args=(u,)) for u in (88,99)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(3);self.assertFalse(thread.is_alive())
        self.assertEqual(sorted(x[0] for x in outcomes),['denied','denied'])
