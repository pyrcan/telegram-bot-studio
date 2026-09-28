"""App storage change marker for the existing reactive mini-app bridge."""
import json
import os
import time
import urllib.request

def changed():
    app_id=os.environ.get('APP_ID')
    token=os.environ.get('APP_TOKEN')
    base=os.environ.get('API_BASE_URL')
    if not all((app_id,token,base)):return
    req=urllib.request.Request(f'{base.rstrip("/")}/api/storage/apps/{app_id}/revision.json',
        data=json.dumps({'revision':time.time_ns()}).encode(), method='PUT',
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=3):pass
    except Exception:
        # Durable domain writes already committed. UI also refreshes while work
        # is pending and exposes a manual refresh; a marker is not the data.
        pass
