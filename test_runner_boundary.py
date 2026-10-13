"""Synthetic command/env/parser boundary only; NEVER launch a provider CLI."""
import json
from unittest.mock import patch
import text_runner as runner


def test_confined_command_has_no_action_tools_or_user_configuration():
    with patch('shutil.which',return_value='/synthetic/codex'):
        args=runner.command('synthetic-model')
    for required in ('--ephemeral','--ignore-user-config','--ignore-rules','--strict-config','--sandbox','read-only'):
        assert required in args
    for setting in ('web_search="disabled"','mcp_servers={}'):assert setting in args
    for name in runner.DISABLED:assert args[args.index(name)-1]=='--disable'
    assert args[-1]=='-'


def test_environment_strips_owner_app_tokens_and_arbitrary_configuration():
    with patch.dict('os.environ',{'PATH':'/synthetic','HOME':'/synthetic','APP_TOKEN':'synthetic','AGENT_TOKEN':'synthetic','CHAT_ID':'synthetic','API_BASE_URL':'http://127.0.0.1:1','CODEX_HOME':'/synthetic/private-fixture'},clear=True):
        env=runner.runtime_environment()
    assert set(env)<={'PATH','HOME','LANG','LC_ALL','CODEX_HOME'}
    assert 'APP_TOKEN' not in env and 'AGENT_TOKEN' not in env


def test_parser_accepts_message_data_and_usage_without_executing_tools():
    events=[{'type':'item.completed','item':{'type':'agent_message','text':'synthetic answer'}},
            {'type':'turn.completed','usage':{'input_tokens':1,'cached_input_tokens':0,'output_tokens':1}},
            {'type':'item.completed','item':{'type':'command_execution','command':'untrusted'}}]
    usage=[]
    assert runner.parse_output('\n'.join(json.dumps(e) for e in events),usage.append)=='synthetic answer'
    assert usage==[{'input_tokens':1,'cached_input_tokens':0,'output_tokens':1}]
