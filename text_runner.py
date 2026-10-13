"""Confined, non-interactive text generation using the installed Codex login.

No owner agent, app tokens, tools, user config, MCP servers, hooks or repository
context are passed. Inputs and outputs are data. This is not an OS sandbox for
arbitrary third-party code; only this reviewed text runner may execute.
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

DISABLED = ('shell_tool','unified_exec','apps','browser_use','browser_use_external',
 'browser_use_full_cdp_access','computer_use','multi_agent','multi_agent_v2',
 'image_generation','goals','hooks','code_mode','code_mode_host','tool_suggest',
 'in_app_browser','in_app_local_automation','artifact','view_image')

def runtime_environment():
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','LANG','LC_ALL','CODEX_HOME')}
    # Chat-provider setup supplies CODEX_HOME; a scheduled app job may not.
    # Point the trusted CLI at the same platform-managed login without opening,
    # copying or exposing credential files, and without inheriting app tokens.
    env.setdefault('CODEX_HOME',str(Path(os.environ.get('DATA_DIR','/data'))/'cli-auth'/'codex'))
    return env

def login_status(env):
    executable=shutil.which('codex')
    if not executable:return False
    try:
        result=subprocess.run([executable,'login','status'],env=env,capture_output=True,text=True,timeout=10)
        return result.returncode==0 and 'logged in' in (result.stdout+' '+result.stderr).lower()
    except (OSError,subprocess.TimeoutExpired):return False

def check_connection():
    inherited={k:v for k,v in os.environ.items() if k in ('PATH','HOME','LANG','LC_ALL','CODEX_HOME')}
    return {'checked_at':time.time(), 'inherited_home':bool(inherited.get('CODEX_HOME')),
        'inherited_login':login_status(inherited), 'configured_login':login_status(runtime_environment())}

def failure_message(stdout,stderr):
    # Never return provider text: it can contain request bodies or credentials.
    value=(stdout+' '+stderr).lower()
    if any(s in value for s in ('401','unauthorized','not logged in','authentication failed')):
        return 'The specialist could not authenticate with the model provider. Check the connected Codex login.'
    if any(s in value for s in ('429','rate limit','usage limit','quota exceeded')):
        return 'The provider usage limit was reached. Your notes are safe; no automatic retry was made.'
    if any(s in value for s in ('model_not_found','unsupported model','model is not supported','does not exist')):
        return 'The configured specialist model is unavailable for this connection.'
    if any(s in value for s in ('connection refused','failed to connect','error sending request','dns error','stream disconnected')):
        return 'The specialist could not reach the model service. Your notes are safe; no automatic retry was made.'
    return 'The model could not answer. Check the configured model and your existing Codex connection.'

def command(model):
    executable = shutil.which('codex')
    if not executable: raise RuntimeError('The text runner is unavailable on this installation.')
    args = [executable,'exec','--json','--ephemeral','--ignore-user-config','--ignore-rules',
      '--strict-config','--skip-git-repo-check','--sandbox','read-only','--color','never',
      '-c','web_search="disabled"','-c','mcp_servers={}', '-c','model_reasoning_effort="low"']
    for feature in DISABLED: args += ['--disable',feature]
    args += ['--model',model,'-']
    return args

def parse_output(stdout, on_usage=None):
    parts=[]
    for line in stdout.splitlines():
        try: event=json.loads(line)
        except ValueError: continue
        if not isinstance(event,dict):continue
        usage=event.get('usage')
        if event.get('type')=='turn.completed' and isinstance(usage,dict) and on_usage:
            fields=('input_tokens','cached_input_tokens','output_tokens')
            if all(type(usage.get(k)) is int and 0<=usage[k]<=10**15 for k in fields) and usage['cached_input_tokens']<=usage['input_tokens']:
                on_usage({k:usage[k] for k in fields})
        item=event.get('item',{})
        if event.get('type')=='item.completed' and item.get('type')=='agent_message':
            parts.append(item.get('text',''))
    value='\n\n'.join(parts).strip()
    if not value: raise RuntimeError('The model returned no answer. Check its model setting and connection.')
    return value[:24000]

def run_text(prompt, model, alive=lambda:True, timeout=150, *, on_usage=None):
    env=runtime_environment()
    with tempfile.TemporaryDirectory(prefix='assistant-text-') as cwd:
        # File-backed pipe drains avoid child deadlock on a verbose/error output;
        # ephemeral files stay private and are removed on exit, never logged.
        with tempfile.TemporaryFile(mode='w+b') as out, tempfile.TemporaryFile(mode='w+b') as err:
            proc=subprocess.Popen(command(model),cwd=cwd,env=env,stdin=subprocess.PIPE,stdout=out,stderr=err)
            try:
                proc.stdin.write(prompt.encode()); proc.stdin.close()
                deadline=time.monotonic()+timeout
                while proc.poll() is None:
                    if not alive():
                        proc.terminate(); raise RuntimeError('Stopped.')
                    if time.monotonic()>deadline:
                        proc.terminate(); raise RuntimeError('The model took too long. Your notes are safe; try a narrower question.')
                    time.sleep(.25)
                if proc.returncode:
                    out.seek(0);err.seek(0)
                    raise RuntimeError(failure_message(out.read(200000).decode('utf-8','replace'),err.read(200000).decode('utf-8','replace')))
                out.seek(0);return parse_output(out.read(2_000_000).decode('utf-8','replace'),on_usage)
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
