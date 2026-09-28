import json
import tempfile
import unittest
from assistant_store import Store
from link_metadata import LinkContent, PageText, read_page, youtube_id
from worker import enrich_one, task_material, process_one

class MetadataTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.store=Store(self.temp.name)
 def tearDown(self):self.store.db.close();self.temp.cleanup()
 def note(self):return self.store.note('owner-test',{'text':'https://www.youtube.com/shorts/abcdefghijk','request_id':'fixture'})
 def test_youtube_forms(self):
  for url in ['https://www.youtube.com/shorts/abcdefghijk','https://youtube.com/watch?v=abcdefghijk&x=y','https://youtu.be/abcdefghijk?t=3','https://youtube.com/embed/abcdefghijk']:
   self.assertEqual(youtube_id(url),'abcdefghijk')
  for url in ['https://youtube.com.evil.test/watch?v=abcdefghijk','https://youtube.com/watch?v=bad','https://user:pass@youtube.com/watch?v=abcdefghijk']:
   self.assertIsNone(youtube_id(url))
 def test_youtube_metadata_is_not_transcript(self):
  urls=[]
  def fetch(url):urls.append(url);return 'application/json',json.dumps({'title':'Example video','author_name':'Fixture channel'})
  content=read_page('https://www.youtube.com/shorts/abcdefghijk',fetch)
  self.assertIn('/oembed?',urls[0]);self.assertEqual(content.text,'');self.assertEqual(content.metadata['author'],'Fixture channel')
  self.note();enrich_one(self.store,lambda _:content)
  note=self.store.notes('owner-test')[0]
  self.assertEqual(note['enrichment'],'metadata-only');self.assertIn('shorts/',note['original'])
  self.assertEqual(self.store.retrieve('owner-test','Example')[0]['id'],note['id'])
  self.assertEqual(self.store.retrieve('owner-test','Fixture channel')[0]['id'],note['id'])
  self.assertEqual(self.store.retrieve('guest-test','Example'),[])
 def test_web_metadata_priority_and_entities(self):
  html='<html><head><title>Fallback</title><meta property="og:title" content="Example &amp; Sample"><meta name="description" content="A short description"><meta name="author" content="Writer"></head><body><script>secret()</script><p>Article</p></body></html>'
  result=read_page('https://example.com',lambda _:('text/html',html))
  self.assertEqual(result.title,'Example & Sample');self.assertEqual(result.metadata['description'],'A short description')
  self.assertIn('Article',result.text);self.assertNotIn('secret',result.text);self.assertNotIn('Fallback',result.text)
 def test_title_only_page_is_useful(self):
  self.note();enrich_one(self.store,lambda _:LinkContent('Example','',{'kind':'webpage','title':'Example'}))
  self.assertEqual(self.store.notes('owner-test')[0]['enrichment'],'metadata-only')
 def test_plaintext_and_binary(self):
  self.assertEqual(read_page('https://example.com',lambda _:('text/plain','Text')).text,'Text')
  with self.assertRaises(ValueError):read_page('https://example.com',lambda _:('image/png','binary'))
 def test_legacy_schema_upgrade_preserves_note(self):
  original=self.note()
  with self.store.db:self.store.db.execute('ALTER TABLE notes DROP COLUMN metadata')
  self.store.db.close();self.store=Store(self.temp.name)
  restored=self.store.notes('owner-test')[0]
  self.assertEqual(restored['id'],original['id']);self.assertEqual(restored['original'],original['original']);self.assertEqual(restored['metadata'],{})
 def test_refresh_preserves_original_and_useful_metadata_on_failure(self):
  self.note();enrich_one(self.store,lambda _:LinkContent('Example','',{'kind':'video','title':'Example'}))
  before=self.store.notes('owner-test')[0]
  self.assertEqual(self.store.refresh_links(),{'queued':1})
  def failed(_):raise ValueError('Blocked')
  enrich_one(self.store,failed);after=self.store.notes('owner-test')[0]
  for key in ('id','original','url','title','metadata','enrichment'):self.assertEqual(before[key],after[key])
 def test_metadata_reaches_answer_material_without_model_call(self):
  self.note();enrich_one(self.store,lambda _:LinkContent('Example','',{'kind':'video','author':'Channel'}));self.store.publish()
  task=self.store.enqueue('owner-test',{'text':'Example','request_id':'question'})
  captured=[]
  process_one(self.store,lambda prompt,*args,**kwargs:(captured.append(prompt) or 'Fixture answer'))
  self.assertIn('"metadata": {"kind": "video", "author": "Channel"}',captured[0]);self.assertIn('not a transcript',captured[0])
 def test_large_collection_search_uses_metadata(self):
  self.note();enrich_one(self.store,lambda _:LinkContent('Example','',{'kind':'video','author':'Unique creator'}))
  for i in range(45):self.store.note('owner-test',{'text':'Unrelated '+str(i),'request_id':str(i)})
  self.store.publish();task=self.store.enqueue('owner-test',{'text':'Find Unique creator','request_id':'q'})
  notes,total=task_material(self.store,self.store.take_task())
  self.assertEqual(total,46);self.assertEqual(len(notes),1);self.assertEqual(notes[0]['title'],'Example')
