import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from nutrition import day_bounds, validate_meal, calorie_summary
import app as bot


class NutritionTests(unittest.TestCase):
    def test_fractional_calories_match_existing_integer_storage(self):
        meal = validate_meal(dict(is_food=True, food_name='定食', calorie=500.6, protein=20.14, fat=15, carbs=60, advice='野菜の小鉢を添えてみてね。', encouragement='写真を送ってくれてありがとう。'))
        self.assertEqual(meal['calorie'], 501)
        self.assertIsInstance(meal['calorie'], int)
        self.assertEqual(meal['protein'], 20.1)

    def test_legacy_unknown_calories_are_disclosed_and_excluded(self):
        total, notice = calorie_summary([{'calorie': 500}, {'calorie': None}, {'calorie': 0}])
        self.assertEqual(total, 500)
        self.assertIn('不明の1件', notice)
        self.assertEqual(calorie_summary([]), (0, ''))

    def test_day_is_jst_and_has_upper_bound(self):
        start, end = day_bounds(dt.datetime(2026, 10, 18, 0, 1, tzinfo=dt.timezone(dt.timedelta(hours=9))))
        self.assertEqual(start, '2026-10-17T15:00:00+00:00')
        self.assertEqual(end, '2026-10-18T15:00:00+00:00')

    def test_invalid_ai_output_is_not_zero_calorie_record(self):
        good = dict(is_food=True, food_name='定食', calorie=500, protein=20, fat=15, carbs=60, advice='野菜の小鉢を添えてみてね。', encouragement='写真を送ってくれてありがとう。')
        self.assertEqual(validate_meal(good)['calorie'], 500)
        for bad in [dict(good, calorie=-1), dict(good, calorie=True), dict(good, protein=float('nan')),
                    dict(good, is_food=False), dict(good, fat='15g'), {}, [], dict(good, food_name='')]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_meal(bad)

    def test_invalid_comments_are_rejected_before_storage(self):
        good = dict(is_food=True, food_name='定食', calorie=500, protein=20, fat=15,
                    carbs=60, advice='野菜の小鉢を添えてみてね。', encouragement='ありがとう。')
        for key, value in [('advice', ''), ('advice', 'あ' * 81),
                           ('advice', '工夫\nもう一つ'), ('encouragement', None),
                           ('encouragement', 'あ' * 61)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_meal(dict(good, **{key: value}))

    def test_help_focuses_on_photo_instead_of_command_catalogue(self):
        self.assertIn('写真', bot.HELP)
        self.assertIn('プライバシー', bot.HELP)
        for command in ['履歴', '訂正', '取消', '7日間', 'PFC', '手入力']:
            self.assertNotIn(command, bot.HELP)


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.client = bot.app.test_client()
        self.env = patch.dict(os.environ, {**{k: 'test' for k in bot.REQUIRED}, 'GEMINI_FREE_TIER_CONFIRMED': 'true'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_missing_or_bad_signature(self):
        self.assertEqual(self.client.post('/callback', data='{}').status_code, 400)
        self.assertEqual(self.client.post('/callback', data='{}', headers={'X-Line-Signature': 'bad'}).status_code, 400)

    def test_signed_empty_webhook(self):
        body = json.dumps({'destination': 'test', 'events': []})
        secret = os.getenv('LINE_CHANNEL_SECRET', '')
        with patch.object(bot, 'handler', bot.WebhookHandler(secret)):
            sig = base64.b64encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()).decode()
            self.assertEqual(self.client.post('/callback', data=body, headers={'X-Line-Signature': sig}).status_code, 200)

    def test_health_missing_config_is_503(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': ''}):
            self.assertEqual(self.client.get('/healthz').status_code, 503)

    @patch.object(bot, 'db')
    def test_readiness_requires_applied_migration(self, db):
        db.return_value.rpc.return_value.execute.return_value.data = '001_beta'
        self.assertEqual(self.client.get('/readyz').status_code, 200)
        db.return_value.rpc.return_value.execute.return_value.data = 'unexpected'
        self.assertEqual(self.client.get('/readyz').status_code, 503)

    @patch.object(bot, 'db')
    def test_database_failure_details_are_not_exposed(self, db):
        db.return_value.rpc.side_effect = RuntimeError('sensitive-provider-error')
        with self.assertLogs(bot.app.logger, level='ERROR') as logs:
            response = self.client.get('/readyz')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('sensitive-provider-error', response.get_data(as_text=True))
        self.assertNotIn('sensitive-provider-error', ''.join(logs.output))

    @patch.object(bot, 'generate')
    @patch.object(bot, 'db')
    @patch.object(bot, 'MessagingApi')
    def test_signed_help_works_without_google_or_database_configuration(self, api, db, generate):
        secret = 'test-secret'
        body = json.dumps({'destination': 'test', 'events': [{
            'type': 'message', 'timestamp': 1792339200000,
            'webhookEventId': 'event-help', 'deliveryContext': {'isRedelivery': False},
            'source': {'type': 'user', 'userId': 'user'},
            'replyToken': 'reply', 'mode': 'active',
            'message': {'type': 'text', 'id': 'message-help', 'text': '使い方', 'quoteToken': 'quote'},
        }]})
        sig = base64.b64encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()).decode()
        with patch.dict(os.environ, {'LINE_CHANNEL_SECRET': secret, 'GEMINI_API_KEY': '', 'SUPABASE_URL': ''}), \
                patch.object(bot, 'handler', bot.WebhookHandler(secret)):
            bot.handler.add(bot.MessageEvent, message=bot.TextMessageContent)(bot.handle_text_message)
            response = self.client.post('/callback', data=body, headers={'X-Line-Signature': sig})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(api.return_value.reply_message.call_args.args[0].messages[0].text, bot.HELP)
        db.assert_not_called()
        generate.assert_not_called()

    @patch.object(bot, 'db')
    def test_consent_uses_database_clock_and_preserves_existing_profile(self, db):
        bot.text_command('user', '同意する')
        db.return_value.rpc.assert_called_once_with('accept_nutrition_consent', {'p_user_id': 'user'})

    @patch.object(bot, 'db')
    def test_consent_is_required_before_ai(self, db):
        db.return_value.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        self.assertEqual(bot.text_command('user', '相談'), bot.INTRO)

    @patch.object(bot, 'consented', return_value=True)
    @patch.object(bot, 'today_rows', return_value=[{'calorie': 500}, {'calorie': None}])
    def test_today_supports_nullable_production_calorie_column(self, rows, consent):
        reply = bot.text_command('user', '今日')
        self.assertIn('2件、約500kcal', reply)
        self.assertIn('不明の1件', reply)

    @patch.object(bot, 'db')
    def test_delete_is_transactional_rpc(self, db):
        bot.text_command('user', '削除を確定')
        db.return_value.rpc.assert_called_once_with('delete_nutrition_user', {'p_user_id': 'user'})

    @patch.object(bot, 'generate')
    @patch.object(bot, 'reserve', return_value='duplicate')
    @patch.object(bot, 'consented', return_value=True)
    @patch.object(bot, 'ApiClient')
    @patch.object(bot, 'MessagingApi')
    def test_redelivery_never_generates_or_replies_twice(self, api, client, consent, reserve, generate):
        event = SimpleNamespace(source=SimpleNamespace(type='user', user_id='u'), reply_token='r')
        bot.process(event, True)
        generate.assert_not_called()
        api.return_value.reply_message.assert_not_called()

    @patch.object(bot, 'generate')
    @patch.object(bot, 'reserve', return_value='limit')
    @patch.object(bot, 'consented', return_value=True)
    @patch.object(bot, 'ApiClient')
    @patch.object(bot, 'MessagingApi')
    def test_quota_blocks_ai(self, api, client, consent, reserve, generate):
        event = SimpleNamespace(source=SimpleNamespace(type='user', user_id='u'), reply_token='r')
        bot.process(event, True)
        generate.assert_not_called()
        self.assertIn('上限', api.return_value.reply_message.call_args.args[0].messages[0].text)

    @patch.object(bot, 'db')
    @patch.object(bot, 'today_rows', return_value=[{'calorie': 500}])
    @patch.object(bot, 'generate')
    @patch.object(bot, 'reserve', return_value='allowed')
    @patch.object(bot, 'consented', return_value=True)
    @patch.object(bot, 'MessagingApiBlob')
    @patch.object(bot, 'ApiClient')
    @patch.object(bot, 'MessagingApi')
    def test_photo_saved_and_replied(self, api, client, blob, consent, reserve, generate, rows, db):
        import io
        from PIL import Image
        data = io.BytesIO()
        Image.new('RGB', (20, 20)).save(data, format='JPEG')
        blob.return_value.get_message_content.return_value = data.getvalue()
        generate.return_value = json.dumps(dict(is_food=True, food_name='定食', calorie=500, protein=20, fat=15, carbs=60, advice='野菜の小鉢を添えてみてね。', encouragement='写真を送ってくれてありがとう。'))
        event = SimpleNamespace(source=SimpleNamespace(type='user', user_id='u'), reply_token='r',
                                webhook_event_id='e', message=SimpleNamespace(id='m'))
        bot.process(event, True)
        self.assertEqual(db.return_value.rpc.call_args.args[0], 'save_nutrition_meal')
        self.assertIn('500kcal', api.return_value.reply_message.call_args.args[0].messages[0].text)
        generate.assert_called_once()
        rows.assert_not_called()
        stored = db.return_value.rpc.call_args.args[1]['p_meal']
        self.assertEqual(set(stored), {'food_name', 'calorie', 'protein', 'fat', 'carbs'})
        reply = api.return_value.reply_message.call_args.args[0].messages[0].text
        self.assertIn('野菜の小鉢を添えてみてね。', reply)
        self.assertIn('写真を送ってくれてありがとう。', reply)
        self.assertNotIn('合計', reply)
        self.assertNotIn('タンパク質', reply)


    @patch.object(bot, 'generate')
    @patch.object(bot, 'reserve')
    @patch.object(bot, 'consented', return_value=False)
    @patch.object(bot, 'ApiClient')
    @patch.object(bot, 'MessagingApi')
    def test_photo_before_consent_never_uses_ai(self, api, client, consent, reserve, generate):
        event = SimpleNamespace(source=SimpleNamespace(type='user', user_id='u'), reply_token='r')
        bot.process(event, True)
        generate.assert_not_called()
        reserve.assert_not_called()
        self.assertEqual(api.return_value.reply_message.call_args.args[0].messages[0].text, bot.INTRO)

    @patch.object(bot.requests, 'post')
    def test_existing_environment_uses_working_model_without_new_setting(self, post):
        post.return_value.json.return_value = {'candidates': [{
            'finishReason': 'STOP', 'content': {'parts': [{'text': 'こんにちは'}]}
        }]}
        for model, expected in [('', bot.DEFAULT_GEMINI_MODEL), ('custom-model', 'custom-model')]:
            with self.subTest(model=model), patch.dict(os.environ, {'GEMINI_MODEL': model}):
                self.assertEqual(bot.generate('hello'), 'こんにちは')
                self.assertIn('/models/' + expected + ':generateContent', post.call_args.args[0])
                self.assertEqual(self.client.get('/healthz').status_code, 200)

    @patch.object(bot.requests, 'post')
    def test_truncated_response_rejected(self, post):
        post.return_value.json.return_value = {'candidates': [{'finishReason': 'MAX_TOKENS'}]}
        with self.assertRaises(RuntimeError):
            bot.generate('hello')
        self.assertEqual(post.call_args.kwargs['timeout'], (5, 20))


if __name__ == '__main__':
    unittest.main()
