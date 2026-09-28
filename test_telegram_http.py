import io
import json
import unittest
import urllib.error
from unittest.mock import Mock, patch
import telegram_http as tg

KEY='123:'+'a'*24

class Response(io.BytesIO):
    def __init__(self, value, code=200):
        super().__init__(json.dumps(value).encode()); self.code=code

class ClientTests(unittest.TestCase):
    def test_fixed_provider_post_and_timeout(self):
        opener=Mock();opener.open.return_value=Response({'ok':True,'result':[{'update_id':1}]})
        result=tg.call(KEY,'getUpdates',{'timeout':10},opener=opener)
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'https://api.telegram.org/bot'+KEY+'/getUpdates')
        self.assertEqual(request.method,'POST')
        self.assertEqual(json.loads(request.data),{'timeout':10})
        self.assertEqual(opener.open.call_args.kwargs,{'timeout':15})
        self.assertEqual(result,[{'update_id':1}])

    def test_invalid_paths_keys_and_parameters_never_reach_network(self):
        cases=[(KEY,'../getMe',{}),(KEY,'getUpdates',{'timeout':11}),
               (KEY,'getUpdates',{'timeout':True}),('bad/key','getMe',{}),
               (KEY,'getMe',{'value':float('nan')}),(KEY,'sendMessage',{'text':'x'*tg.MAX_REQUEST})]
        for key,method,params in cases:
            opener=Mock()
            with self.assertRaises(tg.TelegramError):tg.call(key,method,params,opener=opener)
            opener.open.assert_not_called()

    def test_errors_keep_only_safe_codes(self):
        for status,body in [(429,{'ok':False,'description':'secret '+KEY,'parameters':{'retry_after':3}}),
                            (400,{'ok':False,'description':'message is not modified '+KEY})]:
            opener=Mock();opener.open.return_value=Response(body,status)
            with self.assertRaises(tg.TelegramError) as caught:tg.call(KEY,'getMe',opener=opener)
            self.assertNotIn(KEY,str(caught.exception))
            self.assertNotIn(KEY,repr(caught.exception.__dict__))
            self.assertEqual(caught.exception.code,status)
        self.assertEqual(caught.exception.reason,'not-modified')

    def test_network_exception_never_reveals_token_url(self):
        opener=Mock();opener.open.side_effect=OSError('https://api.telegram.org/bot'+KEY)
        with self.assertRaises(tg.TelegramError) as caught:tg.call(KEY,'getMe',opener=opener)
        self.assertNotIn(KEY,str(caught.exception));self.assertTrue(caught.exception.__suppress_context__)

    def test_oversized_or_invalid_provider_response_is_bounded(self):
        for response in (Response('x'*tg.MAX_RESPONSE),Response([])):
            opener=Mock();opener.open.return_value=response
            with self.assertRaises(tg.TelegramError):tg.call(KEY,'getMe',opener=opener)

    def test_redirects_and_environment_proxies_disabled(self):
        self.assertIsNone(tg.NoRedirect().redirect_request(None,None,302,'',{},'https://other.test'))
        opener=Mock();opener.open.return_value=Response({'ok':True,'result':True})
        with patch.object(tg.urllib.request,'build_opener',return_value=opener) as build:
            tg.call(KEY,'getMe')
        self.assertEqual(build.call_args.args[0].proxies,{})
        self.assertIsInstance(build.call_args.args[1],tg.NoRedirect)

if __name__=='__main__':unittest.main()

class SavedKeyTests(unittest.TestCase):
    def test_scoped_read_keeps_key_out_of_parameters_and_returns_result_only(self):
        opener=Mock();opener.open.return_value=io.BytesIO(KEY.encode())
        with patch.object(tg.urllib.request,'build_opener',return_value=opener),patch.object(tg,'call',return_value=[]) as call:
            self.assertEqual(tg.call_saved('tg-1','getUpdates',{'timeout':5},app_id=24,bearer='FAKE_APP',api_base='http://localhost:8000'),[])
            request=opener.open.call_args.args[0]
            self.assertEqual(request.full_url,'http://localhost:8000/api/apps/24/secrets/tg-1')
            self.assertEqual(request.get_header('Authorization'),'Bearer FAKE_APP')
            call.assert_called_once_with(KEY,'getUpdates',{'timeout':5})
    def test_denied_key_read_never_reaches_telegram_or_reveals_exception(self):
        opener=Mock();opener.open.side_effect=OSError('FAKE_APP '+KEY)
        with patch.object(tg.urllib.request,'build_opener',return_value=opener),patch.object(tg,'call') as call:
            with self.assertRaises(tg.TelegramError) as exc:
                tg.call_saved('tg-1','getMe',app_id=24,bearer='FAKE_APP',api_base='http://localhost')
            self.assertEqual(str(exc.exception),'Telegram could not complete this request.')
            call.assert_not_called()
    def test_management_identity_drops_all_other_provider_fields(self):
        self.assertEqual(tg.bot_identity({'id':1,'is_bot':True,'username':'Fixture_bot','secret':KEY}),{'id':1,'is_bot':True,'username':'Fixture_bot'})
        with self.assertRaises(tg.TelegramError):tg.bot_identity({'id':1,'is_bot':True,'username':KEY})
    def test_blocked_poll_does_not_serialize_controls(self):
        import threading
        started=threading.Event();release=threading.Event();errors=[]
        def opened(request,**kwargs):
            if request.full_url.endswith('/getUpdates'):
                started.set()
                if not release.wait(2):raise RuntimeError('Control was blocked')
                return Response({'ok':True,'result':[]})
            return Response({'ok':True,'result':True})
        opener=Mock();opener.open.side_effect=opened
        def poll():
            try:tg.call(KEY,'getUpdates',{'timeout':5},opener=opener)
            except Exception as e:errors.append(e)
        thread=threading.Thread(target=poll);thread.start()
        try:
            self.assertTrue(started.wait(1))
            self.assertTrue(tg.call(KEY,'answerCallbackQuery',{'callback_query_id':'fake'},opener=opener))
        finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
