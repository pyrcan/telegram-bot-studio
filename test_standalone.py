"""Fresh Studio setup through native owner pairing; fixtures only, no Bridge."""
import time
from unittest.mock import patch

from assistant_store import Store
from bot_behaviors import CONVERSATION_DEFAULT
from catalog import Catalog
import private_testing as development
import telegram_store as telegram


def test_native_owner_can_test_and_release_without_bridge(tmp_path):
    with patch('urllib.request.urlopen', side_effect=AssertionError('No outbound requests in standalone fixture')):
        root = Store(tmp_path)
        try:
            catalog = Catalog(root)
            identity = catalog.create({'name': 'Standalone fixture', 'request_id': 'standalone'})['id']
            connection = telegram.reserve(root, identity)
            assert telegram.prepare_identity(root, identity, {
                'epoch': connection['epoch'], 'bot_id': 12345, 'username': 'standalone_fixture_bot',
            })['status'] == 'ready'
            telegram.await_owner(root, identity, {'epoch': connection['epoch']})
            code = telegram.issue_pairing(root, identity, {'epoch': connection['epoch']})['code']
            assert telegram.accept_pairing(root, identity, connection['epoch'], {
                'message': {'text': '/pair ' + code, 'date': int(time.time()),
                            'from': {'id': 77, 'is_bot': False},
                            'chat': {'type': 'private', 'id': 77}},
            })
            assert telegram.connection(root, identity)['owner_user_id'] == 77
            assert root.config('owner_navigation_app_ids', []) == []
            with catalog.open(identity) as bot:
                bot.save_draft({**CONVERSATION_DEFAULT, 'name': 'Standalone fixture',
                                'model': 'fixture-only'}, bot.draft_revision())
                body = {'epoch': connection['epoch'], 'generation': 0,
                        'revision': bot.draft_revision(), 'test_epoch': None}
                development.change(catalog, identity, body, 'test')
                assert telegram.connection(root, identity)['state'] == 'testing'
                assert bot.config('deployed') is None
                assert development.state(bot)['current'] is True
                body['test_epoch'] = development.state(bot)['epoch']
                development.change(catalog, identity, body, 'release')
                assert telegram.connection(root, identity)['state'] == 'active'
                assert bot.definition()['name'] == 'Standalone fixture'
                assert development.state(bot)['enabled'] is False
                assert bot.db.execute('SELECT count(*) FROM tasks').fetchone()[0] == 0
                assert root.config('owner_navigation_app_ids', []) == []
        finally:
            root.db.close()
