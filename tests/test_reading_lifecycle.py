"""Failure handling, stream completeness and result tools without provider calls."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch, Mock
import requests
from fastapi.testclient import TestClient
from app.main import app
from app import synthesis
from app.routers import web

GOOD = {'user_input':{'name':'Тест','gender':'ж','birth_date':'1990-05-15','analysis_type':'personality'},'report':{'generation_status':'ready','short_summary':'Готово','full_report':'## Коротко\nСмысл готового ответа.'},'calculation_modules':{}}
FAILED = {**GOOD,'report':{'generation_status':'failed','full_report':'Не готово','short_summary':'Ошибка'}}

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        web._web_status.clear();web._job_started.clear();web._job_requests.clear();web._retry_targets.clear();web._clarify_busy.clear();web._clarify_times.clear()

    @patch.object(web.config,'ai_ready',return_value=False)
    @patch.object(web,'_build_and_save')
    def test_offline_never_creates_a_fake_report(self,build,ready):
        r=self.client.post('/report',data={'birth_date':'15.05.1990','name':'Тест','gender':'ж'})
        self.assertEqual(r.status_code,503);build.assert_not_called()
        self.assertIn('Открыть рабочий сайт',r.text)
        self.assertNotIn('OPENROUTER_API_KEY',r.text)

    @patch.object(web.config,'ai_ready',return_value=False)
    def test_local_preview_is_clearly_labelled(self,_):
        r=TestClient(app,base_url='http://127.0.0.1:8766').get('/')
        self.assertIn('Локальное превью',r.text)

    @patch.object(web.database,'get_profile',return_value=None)
    def test_unknown_link_does_not_spin_forever(self,_):
        self.assertEqual(self.client.get('/r/missing').status_code,404)
        self.assertEqual(self.client.get('/r/missing/status').json()['status'],'not_found')

    @patch.object(web.database,'get_profile',return_value=None)
    def test_pending_then_failure_remains_failed_on_refresh(self,_):
        web._register_job('pending')
        self.assertEqual(self.client.get('/r/pending/status').json()['status'],'processing')
        web._web_status['pending']='error:internal diagnostic with secret-like text'
        for i in range(2):
            r=self.client.get('/r/pending');self.assertEqual(r.status_code,503)
            self.assertNotIn('internal diagnostic',r.text)
            self.assertEqual(self.client.get('/r/pending/status').json()['status'],'failed')

    @patch.object(web.database,'get_profile',return_value=FAILED)
    def test_persisted_provider_failure_is_not_ready(self,_):
        self.assertEqual(self.client.get('/r/failed/status').json()['status'],'failed')
        self.assertEqual(self.client.get('/r/failed').status_code,503)
        self.assertEqual(self.client.get('/r/failed/download').status_code,404)

    @patch.object(web.config,'ai_ready',return_value=True)
    @patch.object(web,'_build_and_save')
    @patch.object(web.database,'get_profile',return_value=FAILED)
    def test_retry_reuses_input_and_deduplicates(self,get,build,ready):
        a=self.client.post('/r/failed/retry',follow_redirects=False)
        b=self.client.post('/r/failed/retry',follow_redirects=False)
        self.assertEqual(a.status_code,303);self.assertEqual(a.headers['location'],b.headers['location'])
        self.assertEqual(build.call_count,1)
        self.assertEqual(build.call_args.args[1].birth_date.isoformat(),'1990-05-15')

    @patch.object(web.config, 'ai_ready', return_value=True)
    @patch.object(web, '_build_and_save')
    def test_simultaneous_retries_start_only_one_generation(self, build, ready):
        barrier = Barrier(2)

        def load_profile(_):
            barrier.wait(timeout=5)
            return FAILED

        def retry():
            return TestClient(app).post('/r/failed/retry', follow_redirects=False)

        with patch.object(web.database, 'get_profile', side_effect=load_profile):
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: retry(), range(2)))
        self.assertEqual(responses[0].status_code, 303)
        self.assertEqual(responses[0].headers['location'], responses[1].headers['location'])
        self.assertEqual(build.call_count, 1)

    @patch.object(web.database,'get_profile',return_value=GOOD)
    def test_download_and_meta_are_safe(self,_):
        r=self.client.get('/r/test/download');self.assertEqual(r.status_code,200)
        self.assertIn('attachment',r.headers['content-disposition'])
        self.assertIn('Смысл готового ответа',r.text)
        meta=web._reading_tools('a'*32,'&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotIn('</script><script>alert(1)',meta)
        self.assertIn('\\u003c/script',meta)

    @patch.object(web.config,'ai_ready',return_value=True)
    @patch.object(web.database,'get_profile',return_value=GOOD)
    @patch.object(web.synthesis,'clarify',return_value='Пояснение готово')
    def test_clarification_input_origin_guard_and_rate_limit(self,clarify,get,ready):
        self.assertEqual(self.client.post('/r/test/clarify',data={'question':'Почему?'}).status_code,403)
        headers={'X-Matrix-Action':'clarify'}
        self.assertEqual(self.client.post('/r/test/clarify',headers=headers,data={'question':'x'*501}).status_code,422)
        for i in range(3):
            r=self.client.post('/r/test/clarify',headers=headers,data={'question':'Объясни подробнее'})
            self.assertEqual(r.status_code,200);self.assertEqual(r.json()['answer'],'Пояснение готово')
        self.assertEqual(self.client.post('/r/test/clarify',headers=headers,data={'question':'Ещё вопрос'}).status_code,429)
        self.assertEqual(clarify.call_count,3)

class StreamTests(unittest.TestCase):
    def response(self,lines):
        response=Mock(status_code=200)
        def stream():
            for line in lines:
                if isinstance(line,Exception):raise line
                yield ('data: '+(line if isinstance(line,str) else json.dumps(line))).encode()
        response.iter_lines.side_effect=stream
        return response

    def test_complete_stream_is_accepted_and_closed(self):
        response=self.response([{'choices':[{'delta':{'content':'Готовый ответ'}}]},'[DONE]'])
        with patch.object(synthesis.requests,'post',return_value=response):
            self.assertEqual(synthesis._post_openrouter([]),(True,'Готовый ответ'))
        response.close.assert_called_once()

    def test_partial_or_provider_errored_stream_is_rejected(self):
        chunk={'choices':[{'delta':{'content':'Начало ответа'}}]}
        endings=[[],[requests.ConnectionError('lost')],[{'error':{'message':'provider failed'}}],[{'choices':[{'finish_reason':'length','delta':{}}]}]]
        for ending in endings:
            with self.subTest(ending=ending):
                response=self.response([chunk,*ending])
                with patch.object(synthesis.requests,'post',return_value=response):
                    self.assertFalse(synthesis._post_openrouter([])[0])
                response.close.assert_called_once()

    def test_user_facing_failure_does_not_leak_provider_error(self):
        report=synthesis._error_report({},'provider-secret-diagnostic')
        self.assertEqual(report['generation_status'],'failed')
        self.assertNotIn('provider-secret-diagnostic',json.dumps(report))

if __name__=='__main__':unittest.main()
