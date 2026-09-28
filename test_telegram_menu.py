import unittest
from unittest.mock import Mock,patch
import telegram_menu as menu

class MenuTests(unittest.TestCase):
    def setUp(self):
        self.values={};self.store=Mock();self.store.definition.return_value={'runtime':'notes-v1'}
        self.store.config.side_effect=lambda k,d=None:self.values.get(k,d)
        self.store.set_config.side_effect=lambda k,v:self.values.update({k:v})
        self.connection={'epoch':'fixture','slot':'synthetic-slot'}
    def test_metadata_only_once_per_connection(self):
        send=Mock();menu.sync(self.store,self.connection,send);menu.sync(self.store,self.connection,send)
        self.assertEqual([x.args[1] for x in send.call_args_list],['setMyCommands','setChatMenuButton','setMyDescription','setMyShortDescription'])
        self.assertEqual([c['command'] for c in menu.COMMANDS],['notes','ask','digest','settings','help'])
        menu.sync(self.store,{**self.connection,'epoch':'another'},send)
        self.assertEqual(send.call_count,8)
    def test_failure_does_not_block_intake_or_claim_ready(self):
        send=Mock(side_effect=RuntimeError('synthetic failure'))
        with patch.object(menu.time,'time',return_value=100):
            menu.sync(self.store,self.connection,send);menu.sync(self.store,self.connection,send)
        self.assertEqual(send.call_count,1);self.assertNotIn('telegram_menu',self.values)
        send.side_effect=None
        with patch.object(menu.time,'time',return_value=161):menu.sync(self.store,self.connection,send)
        self.assertEqual(self.values['telegram_menu']['epoch'],'fixture')
