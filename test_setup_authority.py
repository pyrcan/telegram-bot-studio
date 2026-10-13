"""Exact public-core primitives, disposable DB and no submitted values/network."""
import json
import os
from pathlib import Path
import sys
from datetime import timedelta
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app import auth, models, browser_access
from app.database import Base
from app.deps import get_agent_run_principal, require_installation_owner_control
from app.secure_inputs import validate_request_spec
from app.timeutil import now_naive_utc
from assistant_store import Store, Problem
from service import dispatch, domain_dispatch
from setup_spec import TITLE, DESCRIPTION, FIELDS, request_identity, consumer_command
import connect_telegram as connector
import setup_telegram as setup

ROOT=Path(__file__).resolve().parent
RUN='synthetic-owner-run'
CHAT='synthetic-owner-chat'

@pytest.fixture
def authority(tmp_path,monkeypatch):
    (tmp_path/'db').mkdir()
    engine=create_engine('sqlite:///'+str(tmp_path/'db/ultimate.db'))
    tables=['owner','chats','chat_runs','chat_run_updates','apps','delegations','saved_secure_inputs','browser_access_grants','browser_access_sessions']
    Base.metadata.create_all(engine,tables=[Base.metadata.tables[name] for name in tables])
    factory=sessionmaker(bind=engine)
    monkeypatch.setenv('DATA_DIR',str(tmp_path));monkeypatch.setenv('CHAT_ID',CHAT)
    monkeypatch.delenv('APP_TOKEN',raising=False);monkeypatch.delenv('AGENT_TOKEN',raising=False)
    monkeypatch.delenv('APP_OWNER_ACTION',raising=False)
    monkeypatch.setattr('app.database.SessionLocal',factory)
    with factory() as db:
        owner=models.Owner(username='synthetic-owner',hashed_password='synthetic-not-a-password',token_epoch=0)
        chat=models.Chat(id=CHAT,legacy_messages=[],title='Synthetic setup')
        app=models.App(id=7,source_dir=str(tmp_path/'source'),name='Synthetic Studio',slug='synthetic-studio',description='Synthetic',jsx_source='export default function App(){return null}',compiled_path=str(tmp_path/'compiled.js'),runtime_revision='b'*64,token_nonce='a'*32,capability_contract={'service':{'id':'assistant-studio','entry':'service.py'},'data':{'job_secret_read':[f'tg-{i}' for i in range(1,9)]}})
        db.add_all([owner,chat,app]);db.commit()
        db.add(models.ChatRun(id=RUN,chat_id=CHAT,status='running',provider='synthetic'))
        db.commit()
        bearer=auth.create_agent_token(CHAT,owner.username,owner.token_epoch,run_id=RUN)
    current=connector.installation(7,'a'*32,'b'*64)
    current['runtime'].mkdir(parents=True)
    monkeypatch.setattr(connector,'__file__',str(current['runtime']/'connect_telegram.py'))
    monkeypatch.setattr(setup,'__file__',str(current['runtime']/'setup_telegram.py'))
    command=consumer_command(sys.executable,connector.__file__,7,'a'*32,'b'*64,'pocket-notes','c'*32,1,'http://127.0.0.1:1',RUN)
    qid=request_identity(CHAT,RUN,command,str(current['runtime']))
    with factory() as db:
        db.get(models.Chat,CHAT).pending_question_id=qid
        db.add(models.SavedSecureInput(request_id=qid,chat_id=CHAT,command_json=command,cwd=str(current['runtime']),action='run',status='consuming'))
        db.commit()
    yield factory,current,command,qid,bearer
    engine.dispose()


def claim(current,owner_run=RUN):
    return connector.sealed_claim(current,7,'a'*32,'b'*64,'pocket-notes','c'*32,1,'http://127.0.0.1:1',owner_run)


def test_reviewed_spec_matches_actual_public_normalization():
    assert validate_request_spec(title=TITLE,description=DESCRIPTION,mode='sealed',fields=FIELDS)==(TITLE,DESCRIPTION,'sealed',FIELDS)
    assert len(DESCRIPTION)<=240
    cmd=['python','connect','--owner-run',RUN]
    assert request_identity(CHAT,RUN,cmd,'/synthetic')!=request_identity(CHAT,'guest-run',cmd,'/synthetic')


def test_owner_preflight_uses_real_public_principal_and_consuming_db(authority,monkeypatch):
    factory,current,command,qid,bearer=authority
    monkeypatch.setenv('AGENT_TOKEN',bearer)
    with factory() as db:
        principal=get_agent_run_principal(bearer,db)
        require_installation_owner_control(principal)
        assert principal.run_id==RUN and principal.chat_id==CHAT
    assert connector.authoring_owner()==RUN
    monkeypatch.delenv('AGENT_TOKEN')
    claim(current)
    token=connector.own_bearer(7,current)
    payload=auth.decode_access_token(token)
    assert payload['scope']=='app' and payload['app_id']==7 and payload['app_nonce']=='a'*32
    assert not any(k in payload for k in ('service','job_secrets','agent_chat','agent_run','delegation_id','browser_grant','browser_session'))


@pytest.mark.parametrize('change',['browser-run','delegated-chat','missing-run','different-chat','wrong-run','pending','cancelled','arbitrary-id','unreviewed-prompt','argv','cwd','action','pending-id','deleted-chat'])
def test_consuming_provenance_denies_before_mint_or_calls(authority,monkeypatch,change):
    factory,current,command,qid,bearer=authority
    with factory() as db:
        row=db.get(models.SavedSecureInput,qid);chat=db.get(models.Chat,CHAT);run=db.get(models.ChatRun,RUN)
        if change=='browser-run':run.browser_grant_id='synthetic-guest'
        elif change=='delegated-chat':
            db.add(models.Delegation(id='synthetic-child',parent_chat_id=CHAT,parent_root_run_id='synthetic-parent',task_key='synthetic',child_chat_id=CHAT,provider='synthetic',scope='owner',cwd='/synthetic',prompt_sha256='d'*64))
        elif change=='missing-run':db.delete(run)
        elif change=='different-chat':run.chat_id='another-chat'
        elif change in ('pending','cancelled'):row.status=change
        elif change in ('arbitrary-id','unreviewed-prompt'):
            row.request_id='arbitrary-id' if change=='arbitrary-id' else request_identity(CHAT,RUN,command,str(current['runtime'])+'/unreviewed')
            chat.pending_question_id=row.request_id
        elif change=='argv':row.command_json=command+['--extra']
        elif change=='cwd':row.cwd='/wrong'
        elif change=='action':row.action='check'
        elif change=='pending-id':chat.pending_question_id='another-card'
        elif change=='deleted-chat':chat.deleted_at=now_naive_utc()
        db.commit()
    monkeypatch.setattr(connector,'own_bearer',lambda *a:pytest.fail('must reject before mint'))
    monkeypatch.setattr(connector,'telegram',lambda *a:pytest.fail('must reject before provider'))
    monkeypatch.setattr(connector,'put_key',lambda *a:pytest.fail('must reject before write'))
    with pytest.raises(ValueError):
        connector.connect(7,'a'*32,'b'*64,'pocket-notes','c'*32,1,'synthetic-not-a-key','http://127.0.0.1:1','wrong-run' if change=='wrong-run' else RUN)


@pytest.mark.parametrize('change',['browser-run','delegated-chat','stopped-run','revoked-owner','wrong-chat','app-token','ordinary-owner','browser-token','delegated-token'])
def test_authoring_requires_actual_installation_owner_before_service_mutation(authority,monkeypatch,change):
    factory,current,command,qid,bearer=authority
    with factory() as db:
        owner=db.query(models.Owner).first();run=db.get(models.ChatRun,RUN)
        if change=='browser-run':run.browser_grant_id='synthetic-guest'
        elif change=='stopped-run':run.status='completed'
        elif change=='revoked-owner':owner.token_epoch=1
        elif change in ('delegated-chat','delegated-token'):
            db.add(models.Delegation(id='synthetic-child',parent_chat_id=CHAT,parent_root_run_id='synthetic-parent',task_key='synthetic',child_chat_id=CHAT,provider='synthetic',scope='owner',cwd='/synthetic',prompt_sha256='d'*64))
            if change=='delegated-token':bearer=auth.create_agent_token(CHAT,owner.username,0,run_id=RUN,delegation_id='synthetic-child',delegation_chat=CHAT)
        elif change=='browser-token':
            db.add(browser_access.BrowserAccessGrant(id='synthetic-guest',owner_id=owner.id,label='Synthetic guest',kind='invite'))
            db.commit()
            bearer=auth.create_agent_token(CHAT,owner.username,0,run_id=RUN,browser=browser_access.BrowserLineage(grant_id='synthetic-guest'))
        elif change=='app-token':bearer=auth.create_app_token(7,owner.username,0,app_nonce='a'*32)
        elif change=='ordinary-owner':bearer=auth.create_access_token({'sub':owner.username},token_epoch=0)
        db.commit()
    monkeypatch.setenv('AGENT_TOKEN',bearer)
    if change=='wrong-chat':monkeypatch.setenv('CHAT_ID','another-chat')
    monkeypatch.setattr(setup,'call',lambda *a:pytest.fail('owner provenance must precede service calls'))
    monkeypatch.setattr(sys,'argv',['setup_telegram.py','prepare','pocket-notes'])
    with pytest.raises((ValueError,HTTPException)):setup.main()


# This guard covers ALL management actions, not just private test/release.
PROTECTED=[('POST','owner/create'),('POST','owner/navigation'),('POST','owner/access-bridge'),('POST','owner/cost-assumptions'),('GET','owner/dashboard'),
           ('POST','assistants/pocket-notes/identity'),('GET','assistants/pocket-notes/access/users'),('POST','assistants/pocket-notes/access/change')]
PROTECTED += [('POST','assistants/pocket-notes/'+p) for p in ['development/test','development/stop-test','development/release','admin/delete','admin/restore','telegram/pairing-code','telegram/reserve','telegram/prepare-identity','telegram/await-owner','telegram/activate','telegram/begin-verify','telegram/reset-setup','telegram/pause','telegram/setup-error','telegram/reconnect-plan','telegram/reconnect','admin/apply','admin/draft','admin/publish','admin/editing/prepare','admin/editing/ready','admin/editing/handoff','admin/check-connection']]
PROTECTED += [('GET','assistants/pocket-notes/'+p) for p in ['telegram/connection','telegram/state','admin/state','admin/export']]
PROTECTED += [('GET','bridge-access/'+'a'*12+'/bots')]

@pytest.mark.parametrize('lineage',[{'browser_grant_id':'synthetic-guest'},{'browser_session_id':'synthetic-session'},{'delegated':True},{'delegation_id':'synthetic-child'}])
@pytest.mark.parametrize('method,path',PROTECTED)
def test_service_owning_seam_denies_guest_and_delegation_for_all_management(tmp_path,monkeypatch,method,path,lineage):
    monkeypatch.setenv('APP_ID','0');root=Store(tmp_path)
    try:
        with pytest.raises(Problem) as denied:
            dispatch(root,{'method':method,'path':path,'body':{},'actor':{'scope':'owner',**lineage}})
        assert denied.value.status==403
    finally:root.db.close()

@pytest.mark.parametrize('lineage',[{'browser_grant_id':'synthetic-guest'},{'browser_session_id':'synthetic-session'},{'delegated':True},{'delegation_id':'synthetic-child'}])
def test_direct_domain_guard_cannot_bypass_top_level_admission(tmp_path,lineage):
    root=Store(tmp_path)
    try:
        for method,path in [('GET','admin/state'),('POST','admin/draft'),('POST','admin/editing/prepare')]:
            with pytest.raises(Problem) as denied:domain_dispatch(root,{'method':method,'path':path,'body':{},'actor':{'scope':'owner',**lineage}})
            assert denied.value.status==403
    finally:root.db.close()


def test_catalog_browser_metadata_does_not_expand_foreign_app_authority(tmp_path,monkeypatch):
    root=Store(tmp_path);monkeypatch.setenv('APP_ID','0')
    try:
        actor={'scope':'owner','browser_grant_id':'synthetic-guest'}
        assert dispatch(root,{'method':'GET','path':'owner/catalog','actor':actor})['navigation_contract']=='owner-metadata-v1'
        actor={'scope':'app','app_id':17,'browser_session_id':'synthetic-session'}
        with pytest.raises(Problem):dispatch(root,{'method':'GET','path':'owner/catalog','actor':actor})
        root.set_config('owner_navigation_app_ids',[17])
        result=dispatch(root,{'method':'GET','path':'owner/catalog','actor':actor})
        assert result['navigation_contract']=='owner-metadata-v1' and 'participants' not in result
    finally:root.db.close()


def test_setup_preparation_checks_accepted_runtime_before_reserve(authority,monkeypatch):
    factory,current,command,qid,bearer=authority
    monkeypatch.setenv('AGENT_TOKEN',bearer)
    monkeypatch.setattr(setup,'__file__',str(ROOT/'setup_telegram.py'))
    calls=[]
    def call(path,*body):
        calls.append(path)
        if path=='owner/catalog':return {'app_id':7}
        pytest.fail('No mutation is allowed from unaccepted setup bytes')
    monkeypatch.setattr(setup,'call',call)
    monkeypatch.setattr(sys,'argv',['setup_telegram.py','prepare','pocket-notes'])
    with pytest.raises(RuntimeError):setup.main()
    assert calls==['owner/catalog']


@pytest.mark.parametrize('field,value',[('token_nonce','d'*32),('runtime_revision','e'*64)])
def test_changed_installation_rejected_before_consumer_authority(authority,monkeypatch,field,value):
    factory,current,command,qid,bearer=authority
    with factory() as db:
        setattr(db.get(models.App,7),field,value);db.commit()
    monkeypatch.setattr(connector,'own_bearer',lambda *a:pytest.fail('Must fail before mint'))
    with pytest.raises(ValueError):connector.connect(7,'a'*32,'b'*64,'pocket-notes','c'*32,1,'synthetic-not-a-key','http://127.0.0.1:1',RUN)
