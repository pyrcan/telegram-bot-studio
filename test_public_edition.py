"""Edition/descriptor/transport/sealed bounds using synthetic values only."""
import ast
import io
import json
import os
import sqlite3
from pathlib import Path
from contextlib import nullcontext
from unittest.mock import patch
import pytest
from assistant_store import Store,Problem
from catalog import Catalog
import telegram_store as tg
import connect_telegram as connector
import telegram_http as http
import owner_action
from service import dispatch
from bot_behaviors import behavior,BEHAVIORS
from app.manifest_contract import validate_manifest_contract,package_bytes_on_disk
from app.app_capabilities import contract_from_manifest,capability_digest

ROOT=Path(__file__).resolve().parent


def test_manifest_and_accepted_receipt_preserve_every_supported_descriptor():
    m=json.loads((ROOT/'mobius.json').read_text());validate_manifest_contract(m)
    c=contract_from_manifest(m)
    assert c['service']['id']=='assistant-studio' and c['service']['access']=='apps'
    assert c['data']['job_secret_read']==[f'tg-{i}' for i in range(1,9)]
    assert c['background']['job']=='worker.sh' and c['background']['cron']=='* * * * *'
    assert c['agent']['skills']==['assistant-studio.md']
    assert len(capability_digest(c))==64
    for absent in ('credentialed_fetch','text_serving'):assert absent not in m['permissions']
    assert set(m['service'])=={'id','entry','access'}
    assert 'capabilities' not in m and 'static_assets' not in m
    assert m['python']=={'lock':'requirements.lock'}
    assert package_bytes_on_disk(ROOT,m)<1024*1024


@pytest.mark.parametrize('name',['youtube-mp3-v1','youtube-mp4-v1','youtube-selectable-media-v1','youtube-browser-mp3-v1','notes-openai-pilot-v1','../../bad.py'])
def test_omitted_keys_fail_closed_never_redefine(name):
    assert set(BEHAVIORS)=={'blank-v1','conversation-v1'}
    with pytest.raises(Problem):behavior({'runtime':name})


@pytest.mark.parametrize('action',['connect-telegram','activate-telegram','customize-bot','test-draft','publish-tested','bot-lifecycle','change-access'])
def test_fake_private_owner_action_or_app_context_never_authorizes(action):
    with patch.dict(os.environ,{'APP_OWNER_ACTION':'1','APP_TOKEN':'synthetic'},clear=True),patch('owner_action.call') as call:
        with pytest.raises(ValueError):owner_action.execute({'action':action,'context':{'assistant':'pocket-notes'}})
        call.assert_not_called()


@pytest.mark.parametrize('path',['development/test','development/stop-test','development/release','telegram/reserve','telegram/prepare-identity','access/change','admin/delete','admin/editing/prepare'])
def test_ordinary_service_actor_cannot_cross_privileged_seam(tmp_path,path):
    root=Store(tmp_path)
    try:
        with patch.dict(os.environ,{'APP_ID':'0'}):
            with pytest.raises(Problem) as e:dispatch(root,{'method':'POST','path':'assistants/pocket-notes/'+path,'body':{},'actor':{'scope':'app','app_id':0}})
        assert e.value.status==403
    finally:root.db.close()


@pytest.mark.parametrize('path',['verify','audio','video','audio/file','owner/catalog'])
def test_public_routes_never_expose_content(tmp_path,path):
    root=Store(tmp_path)
    try:
        with pytest.raises(Problem) as e:dispatch(root,{'method':'GET','path':path,'public':True,'actor':{'scope':'public'}})
        assert e.value.status==403
    finally:root.db.close()


class Response(io.BytesIO):
    def __init__(self,data,status=200):super().__init__(data);self.code=self.status=status
    def __enter__(self):return self
    def __exit__(self,*a):self.close()


@pytest.mark.parametrize('method',['sendAudio','sendVideo','deleteWebhook','getFile','../../evil','https://elsewhere.invalid'])
def test_http_denies_removed_or_arbitrary_methods_before_network(method):
    with pytest.raises(http.TelegramError):http.call('123:synthetic_fixture_token_for_tests_only',method,opener=object())


def test_http_fixed_origin_redirect_proxy_size_timeout_and_sanitized_error():
    class Opener:
        def open(self,req,timeout):
            assert req.full_url=='https://api.telegram.org/bot123:synthetic_fixture_token_for_tests_only/getUpdates'
            assert json.loads(req.data)=={'timeout':5};assert timeout==10
            return Response(b'{"ok":true,"result":[]}')
    assert http.call('123:synthetic_fixture_token_for_tests_only','getUpdates',{'timeout':5},opener=Opener())==[]
    class Failed:
        def open(self,*a,**kw):raise RuntimeError('SENSITIVE_FAKE_TOKEN_URL')
    with pytest.raises(http.TelegramError) as e:http.call('123:synthetic_fixture_token_for_tests_only','getMe',opener=Failed())
    assert 'SENSITIVE' not in str(e.value)
    assert http.NoRedirect().redirect_request(None,None,None,None,None,None) is None
    with pytest.raises(http.TelegramError):http.call('123:synthetic_fixture_token_for_tests_only','getUpdates',{'timeout':11})
    with pytest.raises(http.TelegramError):http.call('123:synthetic_fixture_token_for_tests_only','sendMessage',{'text':'x'*(http.MAX_REQUEST+1)})


def test_saved_key_is_own_named_bounded_transient_and_never_returned():
    class Opener:
        def open(self,req,timeout):
            assert req.full_url=='http://127.0.0.1:1/api/apps/9/secrets/tg-1'
            assert req.headers['Authorization']=='Bearer synthetic-job';assert timeout==10
            return Response(b'123:synthetic_fixture_token_for_tests_only')
    with patch('urllib.request.build_opener',return_value=Opener()),patch('telegram_http.call',return_value={'id':123}) as call:
        assert http.call_saved('tg-1','getMe',app_id=9,bearer='synthetic-job',api_base='http://127.0.0.1:1')=={'id':123}
        assert call.call_args.args[0]=='123:synthetic_fixture_token_for_tests_only'
    with pytest.raises(http.TelegramError):http.call_saved('foreign','getMe',app_id=9,bearer='synthetic-job',api_base='http://127.0.0.1:1')
    with pytest.raises(http.TelegramError):http.call_saved('tg-1','getMe',app_id=0,bearer='synthetic-job',api_base='http://127.0.0.1:1')


def test_local_sealed_installation_checks_exact_identity_nonce_runtime(tmp_path):
    (tmp_path/'db').mkdir()
    with sqlite3.connect(tmp_path/'db/ultimate.db') as db:
        db.executescript('CREATE TABLE apps(id INTEGER,token_nonce TEXT,runtime_revision TEXT,capability_contract TEXT,deleted_at TEXT);CREATE TABLE owner(username TEXT,token_epoch INTEGER);')
        c={'service':{'id':'assistant-studio','entry':'service.py'},'data':{'job_secret_read':[f'tg-{i}' for i in range(1,9)]}}
        db.execute('INSERT INTO apps VALUES (7,?,?,?,NULL)',('a'*32,'b'*64,json.dumps(c)));db.execute('INSERT INTO owner VALUES (?,0)',('fixture',))
    with patch.dict(os.environ,{'DATA_DIR':str(tmp_path)}):
        got=connector.installation(7,'a'*32,'b'*64)
        assert got['runtime']==tmp_path/'app-runtime/7'/('b'*64)
        for nonce,rev in [('x'*32,'b'*64),('a'*32,'c'*64)]:
            with pytest.raises(ValueError):connector.installation(7,nonce,rev)
        with pytest.raises(ValueError):connector.installation(8)


@pytest.mark.parametrize('env',[{}, {'APP_TOKEN':'synthetic','CHAT_ID':'fixture'}, {'AGENT_TOKEN':'synthetic','CHAT_ID':'fixture'}, {'APP_OWNER_ACTION':'1','CHAT_ID':'fixture'}])
def test_sealed_consumer_rejects_jobs_services_agents_before_local_auth(env):
    with patch.dict(os.environ,env,clear=True),patch('connect_telegram.installation') as install:
        with pytest.raises(ValueError):connector.connect(7,'a'*32,'b'*64,'pocket-notes','c'*32,1,'synthetic','http://127.0.0.1:1','owner-run')
        install.assert_not_called()


def test_local_sealed_success_is_exact_slot_readonly_and_unactivated(tmp_path):
    data=tmp_path;root=Store(data/'apps/7');cat=Catalog(root);identity=cat.create({'name':'Sealed fixture','request_id':'fixture'})['id'];row=tg.reserve(root,identity);root.db.close()
    current={'data':data,'runtime':ROOT,'nonce':'a'*32,'revision':'b'*64,'owner':{'username':'fixture','token_epoch':0}}
    calls=[]
    def telegram(key,method):
        calls.append(method)
        return {'id':1234,'is_bot':True,'username':'sealed_fixture_bot'} if method=='getMe' else {'url':''}
    with patch.dict(os.environ,{'CHAT_ID':'fixture','DATA_DIR':str(data)},clear=True),patch('connect_telegram.installation',return_value=current),patch('connect_telegram.sealed_claim'),patch('app.applied_app_runtime.hold_runtime',return_value=nullcontext()),patch('connect_telegram.telegram',side_effect=telegram),patch('connect_telegram.own_bearer',return_value='synthetic-own-app') as bearer,patch('connect_telegram.put_key') as put:
        connector.connect(7,'a'*32,'b'*64,identity,row['epoch'],row['slot'],'123:synthetic_fixture_token_for_tests_only','http://127.0.0.1:1','owner-run')
        put.assert_called_once_with(7,row['slot'],'123:synthetic_fixture_token_for_tests_only','http://127.0.0.1:1','synthetic-own-app')
    root=Store(data/'apps/7')
    try:
        assert calls==['getMe','getWebhookInfo'];assert tg.connection(root,identity)['state']=='pairing'
        assert tg.connection(root,identity)['owner_user_id'] is None
        assert root.db.execute('SELECT count(*) FROM tg_members').fetchone()[0]==0
        with Catalog(root).open(identity) as s:
            assert s.config('deployed') is None and not s.config('private_test')
            assert s.db.execute('SELECT count(*) FROM tasks').fetchone()[0]==0
    finally:root.db.close()


def test_sealed_wrong_accepted_source_stale_epoch_or_slot_never_writes(tmp_path):
    current={'data':tmp_path,'runtime':tmp_path/'wrong','nonce':'a'*32,'revision':'b'*64,'owner':{'username':'fixture','token_epoch':0}}
    with patch.dict(os.environ,{'CHAT_ID':'fixture'},clear=True),patch('connect_telegram.installation',return_value=current),patch('connect_telegram.put_key') as put:
        with pytest.raises(ValueError):connector.connect(7,'a'*32,'b'*64,'pocket-notes','c'*32,1,'123:synthetic_fixture_token_for_tests_only','http://127.0.0.1:1','owner-run')
        put.assert_not_called()


def test_own_bearer_mints_app_scope_only_and_is_never_output():
    current={'nonce':'a'*32,'owner':{'username':'fixture','token_epoch':0}}
    with patch('app.auth.create_app_token',return_value='synthetic-scoped') as mint:
        assert connector.own_bearer(7,current)=='synthetic-scoped'
        assert mint.call_args.args==(7,'fixture',0)
        assert set(mint.call_args.kwargs)=={'app_nonce','expires_delta'}
        assert mint.call_args.kwargs['expires_delta'].total_seconds()==300


def test_package_imports_have_no_omitted_module_or_missing_sibling():
    files={p.stem for p in ROOT.glob('*.py')}
    for path in ROOT.glob('*.py'):
        tree=ast.parse(path.read_text())
        for node in ast.walk(tree):
            name=node.module if isinstance(node,ast.ImportFrom) else ''
            assert not (name or '').startswith(('youtube_','openai_serving','notes_openai_pilot','pilot_testing','serving_settings','telegram_browser','telegram_verification'))
    assert {'connect_telegram','owner_action','telegram_http'}<=files
    assert 'MIT License' in (ROOT/'LICENSE').read_text()
    assert json.loads((ROOT/'mobius.json').read_text())['license']=='MIT'

@pytest.mark.parametrize('change',['epoch','slot','webhook','duplicate'])
def test_sealed_stale_or_duplicate_setup_never_writes_a_key(tmp_path,change):
    root=Store(tmp_path/'apps/7');cat=Catalog(root)
    identity=cat.create({'name':'Sealed target','request_id':'target'})['id'];row=tg.reserve(root,identity)
    if change=='duplicate':
        other=cat.create({'name':'Existing bot','request_id':'other'})['id'];older=tg.reserve(root,other)
        tg.prepare_identity(root,other,{'epoch':older['epoch'],'bot_id':1234,'username':'sealed_fixture_bot'})
        tg.await_owner(root,other,{'epoch':older['epoch']})
    root.db.close()
    current={'data':tmp_path,'runtime':ROOT,'nonce':'a'*32,'revision':'b'*64,'owner':{'username':'fixture','token_epoch':0}}
    def telegram(key,method):
        return {'id':1234,'is_bot':True,'username':'sealed_fixture_bot'} if method=='getMe' else {'url':'https://synthetic.invalid/webhook' if change=='webhook' else ''}
    with patch.dict(os.environ,{'CHAT_ID':'fixture','DATA_DIR':str(tmp_path)},clear=True),patch('connect_telegram.installation',return_value=current),patch('connect_telegram.sealed_claim'),patch('app.applied_app_runtime.hold_runtime',return_value=nullcontext()),patch('connect_telegram.telegram',side_effect=telegram),patch('connect_telegram.own_bearer') as bearer,patch('connect_telegram.put_key') as put:
        with pytest.raises(ValueError):connector.connect(7,'a'*32,'b'*64,identity,'c'*32 if change=='epoch' else row['epoch'],row['slot']+1 if change=='slot' else row['slot'],'123:synthetic_fixture_token_for_tests_only','http://127.0.0.1:1','owner-run')
        put.assert_not_called();bearer.assert_not_called()
    root=Store(tmp_path/'apps/7')
    try:
        state=tg.connection(root,identity)
        assert state['state']=='reserved' and state['owner_user_id'] is None
        if change=='duplicate':assert 'already connected to another Studio bot' in state['error']
    finally:root.db.close()

@pytest.mark.parametrize('change',['none','pending','cancelled','different-chat','command','cwd','closed-chat'])
def test_sealed_metadata_claim_rejects_missing_or_changed_saved_submission(tmp_path,change):
    (tmp_path/'db').mkdir();current={'data':tmp_path,'runtime':ROOT}
    args=[7,'a'*32,'b'*64,'pocket-notes','c'*32,1,'http://127.0.0.1:1','owner-run']
    import sys
    cmd=[sys.executable,str(ROOT/'connect_telegram.py'),'7','a'*32,'b'*64,'pocket-notes','c'*32,'1','--api-base','http://127.0.0.1:1','--owner-run','owner-run']
    if change=='command':cmd[9]='http://127.0.0.1:2'
    from setup_spec import request_identity
    card=request_identity('fixture','owner-run',cmd,str(ROOT))
    with sqlite3.connect(tmp_path/'db/ultimate.db') as db:
        db.executescript('CREATE TABLE chat_runs(id TEXT,chat_id TEXT,browser_grant_id TEXT);CREATE TABLE delegations(child_chat_id TEXT);CREATE TABLE saved_secure_inputs(request_id TEXT,chat_id TEXT,command_json TEXT,cwd TEXT,action TEXT,status TEXT);CREATE TABLE chats(id TEXT,pending_question_id TEXT,deleted_at TEXT);')
        db.execute("INSERT INTO chat_runs VALUES ('owner-run','fixture',NULL)")
        db.execute('INSERT INTO chats VALUES (?, ?, ?)',('fixture',card,'deleted' if change=='closed-chat' else None))
        if change!='none':db.execute('INSERT INTO saved_secure_inputs VALUES (?, ?, ?, ?, ?, ?)',(card,'other' if change=='different-chat' else 'fixture',json.dumps(cmd),'/wrong' if change=='cwd' else str(ROOT),'run',change if change in ('pending','cancelled') else 'consuming'))
    with patch.dict(os.environ,{'CHAT_ID':'fixture'}):
        with pytest.raises(ValueError):connector.sealed_claim(current,*args)


def test_only_exact_backend_consuming_saved_command_passes(tmp_path):
    (tmp_path/'db').mkdir();current={'data':tmp_path,'runtime':ROOT}
    import sys
    cmd=[sys.executable,str(ROOT/'connect_telegram.py'),'7','a'*32,'b'*64,'pocket-notes','c'*32,'1','--api-base','http://127.0.0.1:1','--owner-run','owner-run']
    from setup_spec import request_identity
    card=request_identity('fixture','owner-run',cmd,str(ROOT))
    with sqlite3.connect(tmp_path/'db/ultimate.db') as db:
        db.executescript('CREATE TABLE chat_runs(id TEXT,chat_id TEXT,browser_grant_id TEXT);CREATE TABLE delegations(child_chat_id TEXT);CREATE TABLE saved_secure_inputs(request_id TEXT,chat_id TEXT,command_json TEXT,cwd TEXT,action TEXT,status TEXT);CREATE TABLE chats(id TEXT,pending_question_id TEXT,deleted_at TEXT);')
        db.execute("INSERT INTO chat_runs VALUES ('owner-run','fixture',NULL)")
        db.execute('INSERT INTO chats VALUES (?,?,NULL)',('fixture',card))
        db.execute('INSERT INTO saved_secure_inputs VALUES (?,?,?,?,?,?)',(card,'fixture',json.dumps(cmd),str(ROOT),'run','consuming'))
    with patch.dict(os.environ,{'CHAT_ID':'fixture'}):connector.sealed_claim(current,7,'a'*32,'b'*64,'pocket-notes','c'*32,1,'http://127.0.0.1:1','owner-run')
