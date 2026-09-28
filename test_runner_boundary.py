"""Exercise the installed CLI against localhost only: no paid model calls.

The captured model request is the authority boundary, not a prompt assertion.
A future CLI adding an action tool must fail this regression test.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from text_runner import command, parse_output

class RunnerBoundaryTest(unittest.TestCase):
 def test_local_wire_has_no_action_tools_or_user_config(self):
  seen=[]
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):
    self.send_response(200);self.end_headers();self.wfile.write(b'{}')
   def do_POST(self):
    seen.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
    item={'id':'msg_probe','type':'message','role':'assistant','content':[{'type':'output_text','text':'PROBE_OK','annotations':[]}],'status':'completed'}
    events=[{'type':'response.created','response':{'id':'resp_probe','status':'in_progress','output':[]}}, {'type':'response.output_item.done','output_index':0,'item':item}, {'type':'response.completed','response':{'id':'resp_probe','status':'completed','output':[item],'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}}]
    for event in events:self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode())
  server=HTTPServer(('127.0.0.1',0),Handler)
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with tempfile.TemporaryDirectory(prefix='assistant-cli-test-') as home:
    # This synthetic config must never be read. The real credential directory
    # is neither opened nor copied by this test.
    Path(home,'config.toml').write_text('invalid toml !!!')
    args=command('gpt-5.6-luna')[:-1]
    for setting in ['model_provider="probe"','model_providers.probe.name="probe"',f'model_providers.probe.base_url="http://127.0.0.1:{server.server_port}/v1"','model_providers.probe.wire_api="responses"','model_providers.probe.requires_openai_auth=false','model_providers.probe.supports_websockets=false']:
     args+=['-c',setting]
    proc=subprocess.run(args+['-'],input='Return PROBE_OK.',text=True,capture_output=True,cwd=home,
      env={'PATH':os.environ['PATH'],'HOME':home,'CODEX_HOME':home,'LANG':'C.UTF-8'},timeout=30)
    self.assertEqual(proc.returncode,0,proc.stderr[-500:]);self.assertEqual(parse_output(proc.stdout),'PROBE_OK')
    self.assertEqual(len(seen),1)
    names={t.get('name',t.get('type')) for t in seen[0].get('tools',[])}
    # The CLI may advertise its inert non-interactive clarification tool.
    # It cannot read files, run commands, browse, delegate, or call connectors.
    self.assertLessEqual(names,{'request_user_input'})
  finally:
   server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
