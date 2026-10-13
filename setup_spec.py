"""Reviewed sealed prompt/command identity; contains no credentials or auth code."""
import json
from uuid import NAMESPACE_URL, uuid5

TITLE = 'Connect your separate Telegram bot'
DESCRIPTION = ('Verifies a separate bot and stores its key encrypted in this exact Studio installation. '
               'No webhook takeover, activation, tests, publication or replies. '
               'Do not use your personal Bridge bot or a bot running elsewhere.')
FIELDS = [{'name': 'bot_token', 'type': 'password', 'label': 'Telegram bot key', 'autocomplete': 'off'}]


def consumer_command(interpreter, consumer, app_id, nonce, revision, identity, epoch, slot, api_base, owner_run):
    return [str(interpreter), str(consumer), str(app_id), nonce, revision, identity, epoch, str(slot),
            '--api-base', api_base, '--owner-run', owner_run]


def request_identity(chat_id, owner_run, command, cwd):
    # Exact public save_owner_question UUID projection after spec normalization.
    card = {'questions': [{'id': 'secure_input', 'header': 'Secure input', 'question': TITLE, 'options': []}],
            'secure_input': {'title': TITLE, 'description': DESCRIPTION, 'mode': 'sealed', 'fields': FIELDS}}
    spec = {'command': list(command), 'cwd': str(cwd), 'action': 'run'}
    return str(uuid5(NAMESPACE_URL, json.dumps([chat_id, owner_run, {'card': card, 'consumer': spec}], sort_keys=True)))
