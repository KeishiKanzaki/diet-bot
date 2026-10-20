import base64
import hashlib
import hmac
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app as bot
from records import RecordInputError, parse_manual_meal, save_manual_meal


class ManualMealTests(unittest.TestCase):
    def test_manual_meal_uses_input_and_does_not_invent_macros(self):
        meal = parse_manual_meal('記録 鶏肉 と 野菜 450kcal')
        self.assertEqual(meal, dict(food_name='鶏肉 と 野菜', calorie=450,
                                    protein=None, fat=None, carbs=None))
        self.assertIsNone(parse_manual_meal('記録を続けたい'))
        self.assertEqual(parse_manual_meal('記録 お茶 0')['calorie'], 0)

    def test_invalid_manual_input_does_not_become_an_ai_request(self):
        for text in ['記録', '記録 お弁当', '記録 500', '記録 お弁当 -1',
                     '記録 お弁当 5001', '記録 お弁当 3.5',
                     '記録 ' + '食' * 101 + ' 500', '記録 お弁当 500\n記録 お茶 0']:
            with self.subTest(text=text), self.assertRaises(RecordInputError):
                parse_manual_meal(text)

    def test_save_uses_verified_owner_and_unique_message_id(self):
        database = Mock()
        table = database.table.return_value
        table.upsert.return_value.execute.return_value.data = [{'id': 42}]
        meal = dict(food_name='昼食', calorie=450, user_id='other', message_id='other', protein=999)
        reply = save_manual_meal(database, 'owner', 'message-1', meal)
        payload = table.upsert.call_args.args[0]
        self.assertEqual(payload['user_id'], 'owner')
        self.assertEqual(payload['message_id'], 'message-1')
        self.assertIsNone(payload['protein'])
        self.assertEqual(table.upsert.call_args.kwargs, dict(on_conflict='message_id', ignore_duplicates=True))
        self.assertIn('#42 昼食 / 450kcal（入力値）', reply)

    def test_duplicate_never_overwrites_an_existing_correction(self):
        database = Mock()
        table = database.table.return_value
        table.upsert.return_value.execute.return_value.data = []
        reply = save_manual_meal(database, 'owner', 'message-1', parse_manual_meal('記録 昼食 450'))
        self.assertIn('重ねて保存していません', reply)
        self.assertTrue(table.upsert.call_args.kwargs['ignore_duplicates'])
        table.update.assert_not_called()
        table.delete.assert_not_called()

    def test_missing_message_id_never_writes(self):
        database = Mock()
        with self.assertRaises(RecordInputError):
            save_manual_meal(database, 'owner', None, parse_manual_meal('記録 昼食 450'))
        database.table.assert_not_called()

    @patch.object(bot, 'db')
    @patch.object(bot, 'consented', return_value=False)
    def test_manual_record_requires_consent(self, consent, database):
        self.assertEqual(bot.text_command('owner', '記録 昼食 450', 'message-1'), bot.INTRO)
        database.assert_not_called()

    @patch.object(bot, 'text_command')
    @patch.object(bot, 'db')
    def test_redelivery_cannot_recreate_a_cancelled_manual_record(self, database, command):
        event = SimpleNamespace(source=SimpleNamespace(type='user', user_id='owner'),
                                delivery_context=SimpleNamespace(is_redelivery=True),
                                message=SimpleNamespace(text='記録 昼食 450', id='message-1'))
        bot.process(event, False)
        command.assert_not_called()
        database.assert_not_called()

    @patch.object(bot, 'MessagingApi')
    @patch.object(bot, 'generate')
    @patch.object(bot, 'reserve')
    @patch.object(bot, 'db')
    def test_signed_manual_webhook_works_with_ai_disabled_and_without_reserving_ai(self, database, reserve, generate, api):
        secret = 'test-manual-secret'
        body = json.dumps({'destination': 'test', 'events': [{
            'type': 'message', 'timestamp': 1792493400000, 'webhookEventId': 'manual-event-1',
            'deliveryContext': {'isRedelivery': False}, 'source': {'type': 'user', 'userId': 'owner'},
            'replyToken': 'reply', 'mode': 'active',
            'message': {'type': 'text', 'id': 'message-1', 'text': '記録 昼食 450', 'quoteToken': 'quote'},
        }]})
        signature = base64.b64encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()).decode()
        table = database.return_value.table.return_value
        table.select.return_value.eq.return_value.execute.return_value.data = [{'consent_version': bot.CONSENT_VERSION}]
        table.upsert.return_value.execute.return_value.data = [{'id': 42}]
        with patch.dict(os.environ, {'LINE_CHANNEL_SECRET': secret, 'LINE_CHANNEL_ACCESS_TOKEN': 'test',
                                    'GEMINI_FREE_TIER_CONFIRMED': 'false'}), \
                patch.object(bot, 'handler', bot.WebhookHandler(secret)):
            bot.handler.add(bot.MessageEvent, message=bot.TextMessageContent)(bot.handle_text_message)
            response = bot.app.test_client().post('/callback', data=body, headers={'X-Line-Signature': signature})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(table.upsert.call_args.args[0]['message_id'], 'message-1')
        self.assertEqual(table.upsert.call_args.args[0]['user_id'], 'owner')
        self.assertIn('#42', api.return_value.reply_message.call_args.args[0].messages[0].text)
        generate.assert_not_called()
        reserve.assert_not_called()

    @patch.object(bot, 'MessagingApi')
    @patch.object(bot, 'ApiClient')
    @patch.object(bot, 'generate')
    @patch.object(bot, 'reserve')
    @patch.object(bot, 'consented', return_value=True)
    def test_malformed_manual_command_returns_instructions_without_ai(self, consent, reserve, generate, client, api):
        event = SimpleNamespace(source=SimpleNamespace(type='user', user_id='owner'), reply_token='reply',
                                message=SimpleNamespace(text='記録 昼食 -1', id='message-1'))
        with patch.dict(os.environ, {'GEMINI_FREE_TIER_CONFIRMED': 'true'}):
            bot.process(event, False)
        self.assertIn('記録 料理名', api.return_value.reply_message.call_args.args[0].messages[0].text)
        generate.assert_not_called()
        reserve.assert_not_called()


if __name__ == '__main__':
    unittest.main()
