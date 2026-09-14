"""HTTP regressions for the reading form. No AI calls, bot polling or remote DB."""
import calendar
import os
import unittest
from datetime import date
from unittest.mock import patch

for key in ('TELEGRAM_BOT_TOKEN', 'OPENROUTER_API_KEY', 'SUPABASE_URL', 'SUPABASE_KEY'):
    os.environ[key] = ''

from fastapi.testclient import TestClient
from app.main import app
from app.routers import web


class ReadingFormTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        configured = patch.object(web.config, 'ai_ready', return_value=True)
        configured.start()
        self.addCleanup(configured.stop)
        self.payload = dict(name='Тест', gender='ж', birth_date='15.05.1990',
                            birth_time='14:30', time_precision='exact',
                            birth_place='', analysis_type='personality')

    def post(self, **changes):
        return self.client.post('/report', data={**self.payload, **changes})

    @patch.object(web, '_build_and_save')
    def test_selected_question_and_precision_reach_engine(self, build):
        response = self.post(analysis_type='work', time_precision='approx',
                             main_request='Какой формат работы мне ближе?')
        self.assertEqual(response.status_code, 200)
        req = build.call_args.args[1]
        self.assertEqual(req.main_request, 'Какой формат работы мне ближе?')
        self.assertEqual(req.analysis_type.value, 'work')
        self.assertEqual(req.time_precision, 'approx')
        self.assertEqual(req.birth_time, '14:30')
        self.assertEqual(req.gender, 'ж')

    @patch.object(web, '_build_and_save')
    def test_unknown_time_ignores_stale_value(self, build):
        self.assertEqual(self.post(time_precision='unknown').status_code, 200)
        req = build.call_args.args[1]
        self.assertIsNone(req.birth_time)
        self.assertEqual(req.time_precision, 'unknown')

    @patch.object(web, '_build_and_save')
    def test_empty_legacy_time_and_iso_date_still_work(self, build):
        self.assertEqual(self.post(birth_date='1990-05-15', birth_time='').status_code, 200)
        req = build.call_args.args[1]
        self.assertEqual(req.birth_date, date(1990, 5, 15))
        self.assertEqual(req.time_precision, 'unknown')
        self.assertTrue(req.main_request)

    @patch.object(web, '_build_and_save')
    def test_current_period_has_explicit_month_boundaries(self, build):
        self.assertEqual(self.post(analysis_type='current_period').status_code, 200)
        req = build.call_args.args[1]
        today = date.today()
        self.assertEqual(req.period_from, today)
        self.assertEqual(req.period_to, date(today.year, today.month, calendar.monthrange(today.year, today.month)[1]))

    @patch.object(web, '_build_and_save')
    def test_invalid_input_never_schedules_work(self, build):
        for invalid in ({'birth_date':'31.02.1990'}, {'birth_date':'01.01.2999'},
                        {'birth_date':'01.01.1800'}, {'gender':''}, {'name':' '},
                        {'birth_time':'24:01'}, {'birth_time':'12:70'},
                        {'time_precision':'invented'}, {'analysis_type':'compatibility'},
                        {'main_request':'x' * 301}):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.post(**invalid).status_code, 422)
        build.assert_not_called()

    def test_pages_and_assets_are_available(self):
        for path in ('/', '/about', '/example', '/compat', '/event', '/health',
                     '/static/site.css', '/static/pages.css', '/static/site.js',
                     '/static/astrolabe.svg', '/static/mark.svg'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_existing_forms_have_connected_labels(self):
        for path in ('/compat', '/event'):
            body = self.client.get(path).text
            self.assertIn('<label for="field-1">', body)
            self.assertIn('id="field-1"', body)

    @patch.object(web.database, 'get_profile')
    def test_result_reads_summary_then_details_then_chart(self, get_profile):
        get_profile.return_value = {
            'user_input': {'name':'<script>alert(1)</script>', 'analysis_type':'personality'},
            'report': {'full_report':'## Коротко\nСмысл ответа.\n## Сильные стороны\nПодробнее здесь.'},
            'calculation_modules': {},
        }
        response = self.client.get('/r/test-fixture')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('<script>alert(1)</script>', response.text)
        self.assertLess(response.text.index('Смысл ответа.'), response.text.index('Подробнее здесь.'))
        self.assertLess(response.text.index('Подробнее здесь.'), response.text.index('Карта и расчёт'))


if __name__ == '__main__':
    unittest.main()
