"""Synthetic transport gates: menu delivery cannot wait on another lane."""
import threading
import time
from unittest.mock import patch
import test_notebook_mode as fixture
import telegram_transport as transport

# Reuse only the fixture, not its inherited tests.
import unittest
class MenuLatencyTests(unittest.TestCase):
    setUp=fixture.NotebookTests.setUp
    tearDown=fixture.NotebookTests.tearDown
    cb=fixture.NotebookTests.cb
    text=fixture.NotebookTests.text
    def listen(self, fake, changed=None):
        stop=threading.Event();errors=[]
        def invoke():
            try:transport.listen(self.tmp.name,'pocket-notes',stop,threading.Event())
            except Exception as e:errors.append(e)
        with patch.object(transport,'call',side_effect=fake),patch.object(transport,'changed',side_effect=changed):
            thread=threading.Thread(target=invoke);thread.start()
            try:yield stop,errors
            finally:stop.set();thread.join(3);self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
    from contextlib import contextmanager
    listen=contextmanager(listen)

    def test_ack_and_old_reply_do_not_block_menu_or_intake(self):
        import telegram_store as tg
        tg.handle_update(self.s,self.text(1),77) # queued save receipt (not a real note)
        release=threading.Event();ack_started=threading.Event();reply_started=threading.Event();menu=threading.Event();poll_again=threading.Event()
        polls=0
        def fake(slot,method,body=None):
            nonlocal polls
            if method=='getUpdates':
                polls+=1
                if polls==1:return [self.cb(2,'prefs')]
                poll_again.set();release.wait(.02);return []
            if method=='answerCallbackQuery':ack_started.set();self.assertTrue(release.wait(2));return True
            if method=='sendMessage':reply_started.set();self.assertTrue(release.wait(2));return {'message_id':1001}
            if method=='editMessageText':menu.set();return {'message_id':900}
            return True
        try:
            with self.listen(fake) as (stop,errors):
                self.assertTrue(ack_started.wait(1));self.assertTrue(reply_started.wait(1))
                self.assertTrue(menu.wait(1),'Menu waited behind blocked network calls')
                self.assertTrue(poll_again.wait(1),'Intake waited behind a menu')
                release.set();stop.set()
        finally:release.set()

    def test_ordinary_navigation_does_not_publish_studio_revision(self):
        calls=[];menu=threading.Event();polls=0
        def fake(slot,method,body=None):
            nonlocal polls
            if method=='getUpdates':
                polls+=1
                if polls==1:return [self.cb(1,'prefs')]
                menu.wait(.02);return []
            if method=='editMessageText':menu.set();return {'message_id':900}
            return True
        with self.listen(fake,lambda:calls.append('refresh')) as (stop,errors):
            self.assertTrue(menu.wait(1));stop.set()
        self.assertEqual(calls,[])

    def test_panel_delivery_preserves_fifo_under_parallel_lanes(self):
        first=threading.Event();release=threading.Event();done=threading.Event();panels=[];polls=0
        def fake(slot,method,body=None):
            nonlocal polls
            if method=='getUpdates':
                polls+=1
                if polls==1:return [self.cb(1,'prefs'),self.cb(2,'privacy')]
                done.wait(.02);return []
            if method=='editMessageText':
                panels.append(body['text'])
                if len(panels)==1:first.set();self.assertTrue(release.wait(2))
                else:done.set()
                return {'message_id':900}
            return True
        try:
            with self.listen(fake) as (stop,errors):
                self.assertTrue(first.wait(1));self.assertEqual(len(panels),1)
                release.set();self.assertTrue(done.wait(1));stop.set()
        finally:release.set()
        self.assertEqual(len(panels),2);self.assertIn('Settings',panels[0]);self.assertIn('Privacy',panels[1])


    def test_first_owner_join_refresh_is_not_in_menu_path(self):
        with self.s.db:self.s.db.execute('DELETE FROM tg_members')
        refreshing=threading.Event();release=threading.Event();menu=threading.Event();polls=0
        def changed():refreshing.set();self.assertTrue(release.wait(2))
        def fake(slot,method,body=None):
            nonlocal polls
            if method=='getUpdates':
                polls+=1
                if polls==1:return [self.text(1,'/start')]
                if polls==2:
                    self.assertTrue(refreshing.wait(1));return [self.cb(2,'prefs')]
                menu.wait(.02);return []
            if method=='editMessageText':menu.set();return {'message_id':900}
            if method=='sendMessage':return {'message_id':1000}
            return True
        try:
            with self.listen(fake,changed) as (stop,errors):
                self.assertTrue(menu.wait(1),'Menu waited behind owner-join notification')
                release.set();stop.set()
        finally:release.set()

    def test_shutdown_leaves_unclaimed_delivery_pending(self):
        import telegram_store as tg
        tg.handle_update(self.s,self.cb(1,'prefs'),77)
        stop=threading.Event();stop.set()
        with patch.object(transport,'call') as send:
            transport.deliver(self.s,self.s,'pocket-notes',lane='panel',stop=stop)
            send.assert_not_called()
        self.assertGreater(self.s.db.execute("SELECT count(*) FROM tg_deliveries WHERE status='pending'").fetchone()[0],0)
