"""Synthetic cross-bot latency; no Telegram, provider or participant calls."""
import tempfile
import threading
import unittest
import json
import os
from unittest.mock import patch

from assistant_store import Store
from catalog import Catalog
from telegram_transport import run
import telegram_transport as transport
from worker import process_one
import telegram_store as tg


class ProcessingFairnessTests(unittest.TestCase):
 def test_slow_bot_does_not_block_another_bot(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Store(directory)
   fast_id=Catalog(root).create({'name':'Fast bot','request_id':'fast'})['id']
   root.db.close()
   slow_started=threading.Event();release=threading.Event();fast_finished=threading.Event();errors=[]
   def process(store,**kwargs):
    identity=store.config('draft')['id']
    if identity=='pocket-notes':slow_started.set();release.wait(3)
    elif identity==fast_id:fast_finished.set()
    return False
   def work():
    selected=Store(directory)
    try:run(selected,process,lambda _:False,duration=5)
    except Exception as exc:errors.append(exc)
    finally:selected.db.close()
   thread=threading.Thread(target=work,daemon=True);thread.start()
   try:
    self.assertTrue(slow_started.wait(3),'Slow fixture never started')
    self.assertTrue(fast_finished.wait(1),'Fast bot waited behind a blocked bot')
   finally:
    release.set();thread.join(5)
   self.assertFalse(thread.is_alive());self.assertEqual(errors,[])

 def test_task_and_connection_timings_contain_no_content(self):
  with tempfile.TemporaryDirectory() as directory:
   store=Store(directory)
   try:
    store.publish();store.enqueue('owner-test',{'text':'PRIVATE_SENTINEL','kind':'ask','request_id':'timing'})
    samples=[]
    self.assertTrue(process_one(store,lambda *a,**kw:self.fail('Empty collection needs no model'),on_timing=samples.append))
    self.assertEqual(set(samples[0]),{'queue_ms','process_ms'})
    self.assertTrue(all(type(value) is int and value>=0 for value in samples[0].values()))
    tg.reserve(store,'pocket-notes')
    tg.record_timing(store,'pocket-notes','work',samples[0])
    public=tg.public_state(store,'pocket-notes')
    self.assertEqual(public['timing']['work']['queue_ms'],samples[0]['queue_ms'])
    self.assertNotIn('PRIVATE_SENTINEL',json.dumps(public))
   finally:store.db.close()

 def test_active_listener_keeps_single_flight_while_bot_work_runs(self):
  with tempfile.TemporaryDirectory() as directory:
   store=Store(directory)
   try:
    store.publish();row=tg.reserve(store,'pocket-notes')
    tg.begin_verification(store,'pocket-notes',row['epoch'])
    tg.verified(store,'pocket-notes',{'epoch':row['epoch'],'bot_id':1000,'username':'Fixture_bot','owner_user_id':77})
    tg.activate(store,'pocket-notes',{'epoch':row['epoch']})
    starts=[];processed=[]
    def listener(root_path,identity,stop,wake,signal):
     starts.append(identity);stop.wait(1)
    class Response:
     def __enter__(self):return self
     def __exit__(self,*args):pass
     def read(self):return b'{"runtime_dir":"fixture"}'
    with patch.dict(os.environ,{'API_BASE_URL':'http://fixture.invalid','APP_ID':'24','APP_TOKEN':'fixture'}),patch.object(transport,'listen',side_effect=listener),patch.object(transport.urllib.request,'urlopen',return_value=Response()):
     run(store,lambda selected,**kwargs:processed.append(selected.config('draft')['id']) or False,lambda _:False,duration=.25)
    self.assertEqual(starts,['pocket-notes']);self.assertIn('pocket-notes',processed)
   finally:store.db.close()


if __name__=='__main__':unittest.main()
