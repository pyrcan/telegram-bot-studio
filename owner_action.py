"""Actual owner-chat management AFTER the saved exact approval, never a service.

No frame calls this consumer. The public platform authenticates the owner agent
and Studio's unchanged service guard rechecks owner/agent scope. Source prep,
selection JSON and a navigation click are not approval. Never forge the removed
private APP_OWNER_ACTION environment. No credentials are accepted here.
"""
import os
from owner import call,api


def execute(request):
    if not os.environ.get('AGENT_TOKEN') or not os.environ.get('CHAT_ID') or os.environ.get('APP_TOKEN') or os.environ.get('APP_OWNER_ACTION'):
        raise ValueError('Actual owner chat required')
    if not isinstance(request,dict) or set(request)!={'action','context'} or not isinstance(request['context'],dict):raise ValueError('Review exact operation')
    context=request['context'];identity=context.get('assistant')
    if not isinstance(identity,str) or not identity or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in identity):raise ValueError('Invalid bot')
    action=request['action']
    if action in ('test-draft','stop-test','publish-tested'):
        operation={'test-draft':'test','stop-test':'stop-test','publish-tested':'release'}[action]
        return call(f'assistants/{identity}/development/{operation}',context)
    if action=='bot-lifecycle':
        operation=context.get('operation')
        if operation not in ('delete','restore'):raise ValueError('Unknown operation')
        return call(f'assistants/{identity}/admin/{operation}',context)
    if action=='change-access':return call(f'assistants/{identity}/access/change',context)
    if action=='customize-bot':
        from editing_chat import ensure
        ensure(identity,call,api)
        return {'ok':True,'assistant':identity,'instruction':'Read owner.py context in the canonical chat; no model turn submitted.'}
    if action=='activate-telegram':
        current=call(f'assistants/{identity}/telegram/connection')
        if not current or current['epoch']!=context.get('epoch') or current['username']!=context.get('bot'):raise ValueError('Connection changed')
        return call(f'assistants/{identity}/telegram/activate',{'epoch':current['epoch']})
    raise ValueError('Unsupported operation; use sealed setup for connection')
