"""Search narrowing must preserve the existing ranking and privacy boundary."""
import json
import tempfile
import unittest

from assistant_store import Store, terms
from worker import task_material


STOP_WORDS = {'find','notes','about','what','have','saved','show','please','could','that','with','from','collection','search','does','there','this','give','digest','summarize','summarise','classify','organise','organize'}


class RetrievalScaleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def add(self, key, text, participant='owner-test', tags='[]', metadata='{}'):
        note = self.store.note(participant, {'text': text, 'request_id': key})
        with self.store.db:
            self.store.db.execute('UPDATE notes SET tags=?, metadata=? WHERE id=?', (tags, metadata, note['id']))
        return note['id']

    def original_results(self, participant, query, limit):
        rows = self.store.notes(participant)
        words = terms(query) - STOP_WORDS
        if words:
            for row in rows:
                row['_score'] = len(words & terms(row['original']+' '+row['title']+' '+(row['article'] or '')+' '+' '.join(row['tags'])+' '+json.dumps(row['metadata'],ensure_ascii=False)))
            rows = sorted((r for r in rows if r['_score']), key=lambda r: (r['_score'],r['created']), reverse=True)
        return [r['id'] for r in rows[:limit]]

    def test_candidate_filter_preserves_ranking_isolation_and_trash(self):
        ids = [
            self.add('a', 'quartzfield research'),
            self.add('b', 'research field'),
            self.add('c', 'unrelated', tags='["quartzfield"]'),
            self.add('d', 'unrelated', metadata='{"topic":"research"}'),
            self.add('e', 'nothing here'),
            self.add('f', 'quartzfield research', participant='guest-test'),
        ]
        with self.store.db:
            self.store.db.execute('UPDATE notes SET deleted_at=1 WHERE id=?', (ids[0],))
        for query in ('quartzfield research', 'please find notes', 'research', 'QUARTZFIELD'):
            for participant in ('owner-test', 'guest-test'):
                self.assertEqual([r['id'] for r in self.store.retrieve(participant, query, 30)],
                                 self.original_results(participant, query, 30))

    def test_unicode_and_escaped_json_keep_exact_results(self):
        self.add('kelvin', 'A note about Kelvin')
        self.add('unicode-query', 'Résumé planning')
        self.add('escaped-json', 'Other topic', metadata='{"detail":"\\u212aelvin"}')
        self.add('linebreak', 'Line\nbreak topic')
        for query in ('kelvin', 'résumé', 'linebreak', 'topic'):
            self.assertEqual([r['id'] for r in self.store.retrieve('owner-test', query, 30)],
                             self.original_results('owner-test', query, 30))

    def test_large_task_counts_all_notes_but_selects_matches(self):
        for i in range(45):
            self.add(str(i), 'Ordinary saved item '+str(i))
        target = self.add('target', 'quartzfield research')
        self.store.publish()
        self.store.enqueue('owner-test', {'text': 'find quartzfield', 'request_id': 'q'})
        material, total = task_material(self.store, self.store.take_task())
        self.assertEqual(total, 46)
        self.assertEqual([item['id'] for item in material], [target])

    def test_many_perfect_matches_keep_newest_thirty(self):
        for i in range(10):
            self.add('partial-'+str(i), 'quartzfield note')
        for i in range(50):
            self.add('full-'+str(i), 'quartzfield research')
        query = 'quartzfield research'
        self.assertEqual([r['id'] for r in self.store.retrieve('owner-test', query, 30)],
                         self.original_results('owner-test', query, 30))


if __name__ == '__main__':
    unittest.main()
