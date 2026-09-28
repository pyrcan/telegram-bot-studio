#!/usr/bin/env python3
"""Isolated synthetic UI fixture. No owner/app credentials or live API calls."""
import functools
import http.server
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parent
PLATFORM=Path(os.environ.get('MOBIUS_PLATFORM_SOURCE','/data/platform'))
sys.path.insert(0,str(PLATFORM/'backend'))
from app.app_compile_contract import rolldown_command

NAME='studio'
CSS='html{--bg:#101114;--surface:#191b20;--surface-hover:#24262d;--text:#f3f4f6;--muted:#a3a7b1;--border:#32353e;--accent:#ad9bff;--accent-fg:#171127;--accent-dim:#2b2542;--green:#71d9ac;--red:#fa9494;--font:Inter,system-ui,sans-serif;background:var(--bg);color:var(--text)}body{margin:0}'
FIXTURE="window.fixtureStorage={};\nconst draft={name:'Pocket Notes',description:'A private notebook in Telegram.',instructions:'Help with saved notes.',model:'Configured model',runtime:'notes-v1'};\nconst bot={id:'pocket-notes',name:'Pocket Notes',runtime:'notes-v1',revision:'demo',version:null,connection:{state:'reserved'},development:{status:'Not connected',behavior_label:'Personal notebook',release_label:'Not published',next_step:'Connect Telegram, then build and test privately.',uses_model:true}};\nwindow.fetch=async(url,opts={})=>{const p=String(url);if(p.endsWith('owner/catalog'))return Response.json({app_id:24,assistants:[bot],deleted:[]});if(p.endsWith('/admin/state'))return Response.json({draft,revision:'demo'});throw Error('Unexpected fixture request: '+p)};window.__syntheticTelegramPreview=true;window.fixtureCalls=[];window.mobius={signal(){},storage:{async get(k){return window.fixtureStorage[k]??null},async set(k,v){window.fixtureStorage[k]=v},subscribe(k,fn){queueMicrotask(()=>fn(window.fixtureStorage[k]??null));return ()=>{}}},capabilities:{available(){return true},async invoke(name,body){window.fixtureCalls.push(body);return {status:'cancelled'}}}};"

def build(out):
    out.mkdir(parents=True,exist_ok=True)
    subprocess.run(rolldown_command(ROOT/'index.jsx',out/(NAME+'.js'),report=out/(NAME+'-report.json')),check=True,capture_output=True)
    html='<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+CSS+'</style><div id="root"></div><script>'+FIXTURE+'</script><script type="module">import App from "./'+NAME+'.js";const rt=window.__mobiusCompiledRuntime;rt.createRoot(document.getElementById("root")).render(rt.createElement(App,{appId:24,token:"synthetic-fixture"}));</script>'
    (out/(NAME+'.html')).write_text(html)

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='telegram-ui-fixture-') as directory:
        out=Path(directory);build(out)
        handler=functools.partial(http.server.SimpleHTTPRequestHandler,directory=out)
        with http.server.ThreadingHTTPServer(('127.0.0.1',8768),handler) as server:
            print('Synthetic preview: http://127.0.0.1:8768/'+NAME+'.html',flush=True)
            try:server.serve_forever()
            except KeyboardInterrupt:pass
