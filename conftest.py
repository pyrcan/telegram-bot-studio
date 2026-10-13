"""Synthetic only: isolate settings and forbid unmocked network/process work."""
import os
import sys
import tempfile
from pathlib import Path
import socket
import subprocess
import pytest

ROOT=Path(__file__).resolve().parent
CORE=Path(os.environ.get('MOBIUS_CORE_SOURCE','/data/platform/backend')).resolve()
if not (CORE/'app/deps.py').is_file():
    raise RuntimeError('Set MOBIUS_CORE_SOURCE to the documented public backend checkout.')
sys.path[:0]=[str(ROOT),str(CORE)]
_SANDBOX=tempfile.TemporaryDirectory(prefix='studio-synthetic-')
os.environ.update(APP_ID='0',APP_TOKEN='synthetic-not-a-token',API_BASE_URL='http://127.0.0.1:1',
                  DATA_DIR=_SANDBOX.name,SECRET_KEY='synthetic-test-key-never-production-'+'x'*32,
                  DATABASE_URL='sqlite:///'+_SANDBOX.name+'/core.db',MOBIUS_TEST_DATABASE_ISOLATED='1',MOBIUS_TEST_RUNTIME='1')
for name in ('AGENT_TOKEN','CODEX_HOME','APP_OWNER_ACTION'):
    os.environ.pop(name,None)

def denied(*args,**kwargs):
    raise AssertionError('Unmocked network/provider subprocess forbidden in synthetic tests')

@pytest.fixture(autouse=True)
def guard(monkeypatch):
    monkeypatch.setattr(socket.socket,'connect',denied)
    monkeypatch.setattr(socket,'create_connection',denied)
    monkeypatch.setattr(subprocess,'Popen',denied)
