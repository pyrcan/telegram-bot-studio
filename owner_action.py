"""Owner chat actions after saved approval. JSON selection on stdin, never keys."""
import json
import os
import sys
from connect_telegram import call,api


def execute(request):
    if not os.environ.get('AGENT_TOKEN') or os.environ.get('APP_TOKEN'):
        raise ValueError('Owner chat required')
    context=request['context'];identity=context['assistant']
    if not isinstance(identity,str) or not identity or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in identity):raise ValueError()
    if request['action'] in ('test-draft','stop-test','publish-tested'):
        operation={'test-draft':'test','stop-test':'stop-test','publish-tested':'release'}[request['action']]
        call(f'assistants/{identity}/development/{operation}',context)
        if operation in ('test','release'):
            # Wake the normal single-flight supervisor; do not wait for a cron
            # tick or create a second listener. Never send a test message here.
            app_id=call('owner/catalog')['app_id']
            api(f'/api/apps/{app_id}/run-job',{},'POST')
    elif request['action']=='bot-lifecycle':
        operation=context.get('operation')
        if operation not in ('delete','restore'):raise ValueError()
        call(f'assistants/{identity}/admin/{operation}',context)
    elif request['action']=='change-access':
        call(f'assistants/{identity}/access/change',context)
    elif request['action']=='customize-bot':
        from editing_chat import ensure
        ensure(identity,call,api)
    elif request['action']=='activate-telegram':
        current=call(f'assistants/{identity}/telegram/connection')
        if not current or current['epoch']!=context.get('epoch') or current['username']!=context.get('bot'):
            raise ValueError()
        call(f'assistants/{identity}/telegram/activate',{'epoch':current['epoch']})
        app_id=call('owner/catalog')['app_id']
        api(f'/api/apps/{app_id}/run-job',{},'POST')
    else:raise ValueError()


if __name__=='__main__':
    values={}
    try:
        values=json.load(sys.stdin);execute(values)
    except Exception:sys.exit(1)
    finally:
        if isinstance(values,dict):
            fields=values.get('fields')
            if isinstance(fields,dict):fields.clear()
            values.clear()
