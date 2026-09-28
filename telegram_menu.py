"""Native commands: published profile for everyone, private draft menu for owner."""
import time
from telegram_onboarding import DESCRIPTION, SHORT_DESCRIPTION
VERSION=3
COMMANDS=[{'command':'notes','description':'Notes — browse and search'},
          {'command':'ask','description':'Ask about your notes'},
          {'command':'digest','description':'Digest — review saved notes'},
          {'command':'settings','description':'Settings, privacy and chat cleanup'},
          {'command':'help','description':'How Pocket Notes works'}]

def contract(store, connection):
    value={'version':VERSION,'epoch':connection['epoch']}
    if store.config('deployed'):
        definition=store.definition()
        if definition.get('runtime','notes-v1')!='notes-v1':
            value.update(runtime=definition['runtime'],release=store.config('deployed'))
    else:value['runtime']='unpublished'
    return value

def commands(definition):
    from bot_behaviors import behavior
    impl=behavior(definition)
    return impl.commands if impl else COMMANDS

def sync(store,connection,send):
    from bot_behaviors import behavior,BLANK_DEFAULT
    from private_testing import state,open_test
    definition=store.definition() if store.config('deployed') else {**BLANK_DEFAULT,'description':'This bot is being set up. Access is not open yet.'}
    impl=behavior(definition);expected=contract(store,connection)
    if store.config('telegram_menu')!=expected and store.config('telegram_menu_retry_at',0)<=time.time():
        try:
            send(connection['slot'],'setMyCommands',{'commands':commands(definition),'scope':{'type':'all_private_chats'}})
            send(connection['slot'],'setChatMenuButton',{'menu_button':{'type':'commands'}})
            send(connection['slot'],'setMyDescription',{'description':definition['description'] if impl else DESCRIPTION})
            send(connection['slot'],'setMyShortDescription',{'short_description':definition['description'][:120] if impl else SHORT_DESCRIPTION})
        except RuntimeError:
            store.set_config('telegram_menu_retry_at',time.time()+60)
        else:
            store.set_config('telegram_menu',expected);store.set_config('telegram_menu_retry_at',0)
    preview=state(store);epoch=preview.get('epoch') if preview.get('enabled') else None
    desired={'epoch':connection['epoch'],'test':epoch}
    previous=store.config('telegram_owner_menu')
    # Existing bots without a preview need no new metadata calls on upgrade.
    if not previous and not epoch:return
    if previous==desired or store.config('telegram_owner_menu_retry_at',0)>time.time():return
    scope={'type':'chat','chat_id':connection['owner_user_id']}
    try:
        if epoch:
            with open_test(store,epoch) as test:
                send(connection['slot'],'setMyCommands',{'commands':commands(test.definition()),'scope':scope})
        else:send(connection['slot'],'deleteMyCommands',{'scope':scope})
    except RuntimeError:
        store.set_config('telegram_owner_menu_retry_at',time.time()+60)
        return
    store.set_config('telegram_owner_menu',desired);store.set_config('telegram_owner_menu_retry_at',0)
