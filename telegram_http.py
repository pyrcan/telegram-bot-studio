"""App-owned Telegram client. Only trusted backend code supplies a bot key."""
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

METHODS = frozenset({
    'getMe', 'getWebhookInfo', 'getUpdates', 'sendMessage', 'editMessageText',
    'answerCallbackQuery', 'deleteMessage', 'setMyCommands', 'deleteMyCommands',
    'setChatMenuButton', 'setMyDescription', 'setMyShortDescription',
    'sendChatAction',
})
MAX_REQUEST = 256 * 1024
MAX_RESPONSE = 2 * 1024 * 1024


class TelegramError(RuntimeError):
    def __init__(self, code=0, retry=0, reason=''):
        super().__init__('Telegram could not complete this request.')
        self.code, self.retry, self.reason = code, retry, reason


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _failure(body, status):
    code = body.get('error_code', status)
    if type(code) is not int or not 100 <= code <= 599:
        code = 0
    parameters = body.get('parameters')
    retry = parameters.get('retry_after', 0) if isinstance(parameters, dict) else 0
    if type(retry) is not int or not 0 <= retry <= 86400:
        retry = 0
    description = str(body.get('description', '')).lower()
    reason = ''
    if 'message is not modified' in description:
        reason = 'not-modified'
    elif any(value in description for value in ('message to edit not found', "message can't be edited", 'message_id_invalid')):
        reason = 'edit-unavailable'
    return TelegramError(code, retry, reason)


def call(key, method, params=None, *, opener=None):
    # A fixed provider and method set means this is not a general URL proxy.
    if not isinstance(key, str) or not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]{20,150}', key):
        raise TelegramError(reason='invalid-key')
    if not isinstance(method, str) or method not in METHODS or (params is not None and not isinstance(params, dict)):
        raise TelegramError(reason='invalid-request')
    values = dict(params or {})
    if method == 'getUpdates':
        wait = values.get('timeout', 0)
        if type(wait) is not int or not 0 <= wait <= 10:
            raise TelegramError(reason='invalid-request')
    else:
        wait = 0
    try:
        encoded = json.dumps(values, ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError):
        raise TelegramError(reason='invalid-request') from None
    if len(encoded) > MAX_REQUEST:
        raise TelegramError(reason='request-too-large')
    request = urllib.request.Request(
        'https://api.telegram.org/bot' + key + '/' + method,
        data=encoded, headers={'Content-Type': 'application/json'}, method='POST',
    )
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        try:
            response = opener.open(request, timeout=wait + 5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            status = response.code
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise TelegramError(reason='response-too-large')
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise TelegramError(reason='invalid-response')
    except TelegramError:
        raise
    except Exception:
        # HTTP exceptions contain the token-bearing URL. Never propagate them.
        raise TelegramError() from None
    if status != 200 or body.get('ok') is not True:
        raise _failure(body, status)
    return body.get('result')


def call_saved(secret_name, method, params=None, *, app_id, bearer, api_base):
    """Read a named own-app key with the own job/service credential; never cache it."""
    if (type(secret_name) is not str or not re.fullmatch(r'tg-[1-8]', secret_name)
            or not str(app_id).isdigit() or int(app_id)<=0 or method not in METHODS):
        raise TelegramError(reason='invalid-request')
    parsed=urlsplit(api_base)
    if parsed.scheme not in ('http','https') or parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
        raise TelegramError(reason='invalid-platform')
    try:
        request = urllib.request.Request(
            api_base.rstrip('/') + f'/api/apps/{int(app_id)}/secrets/{secret_name}',
            headers={'Authorization': 'Bearer ' + bearer},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=10) as response:
            raw = response.read(8193)
        if len(raw) > 8192:
            raise ValueError()
        key = raw.decode()
    except Exception:
        raise TelegramError(reason='key-unavailable') from None
    try:
        return call(key, method, params)
    finally:
        key = None


def bot_identity(bot):
    """Only this explicit projection may reach management screens/other apps."""
    if (not isinstance(bot, dict) or type(bot.get('id')) is not int
            or bot.get('is_bot') is not True
            or not isinstance(bot.get('username'), str)
            or not re.fullmatch(r'[A-Za-z0-9_]{1,64}', bot['username'])):
        raise TelegramError(reason='invalid-response')
    return {'id': bot['id'], 'is_bot': True, 'username': bot['username']}
