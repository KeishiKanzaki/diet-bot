import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app as bot
from cost_control import AIRequestsDisabled, AI_PAUSED_REPLY, ai_requests_enabled


class CostControlTests(unittest.TestCase):
    def test_only_explicit_free_tier_confirmation_enables_ai(self):
        for value in ['', 'false', 'unknown', 'paid', '1', 'yes']:
            with self.subTest(value=value), patch.dict(os.environ, {'GEMINI_FREE_TIER_CONFIRMED': value}):
                self.assertFalse(ai_requests_enabled())
        with patch.dict(os.environ, {'GEMINI_FREE_TIER_CONFIRMED': 'true'}):
            self.assertTrue(ai_requests_enabled())

    @patch.object(bot.requests, 'post')
    def test_direct_generation_is_blocked_before_network_even_with_a_key(self, post):
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'configured', 'GEMINI_FREE_TIER_CONFIRMED': 'false'}):
            with self.assertRaises(AIRequestsDisabled):
                bot.generate('test', b'fake-image')
        post.assert_not_called()

    @patch.object(bot, 'MessagingApi')
    @patch.object(bot, 'ApiClient')
    @patch.object(bot, 'MessagingApiBlob')
    @patch.object(bot, 'consented', return_value=True)
    @patch.object(bot, 'reserve')
    @patch.object(bot, 'generate')
    def test_paused_image_neither_downloads_nor_reserves_nor_generates(self, generate, reserve, consent, blob, client, api):
        event = SimpleNamespace(source=SimpleNamespace(type='user', user_id='u'), reply_token='reply')
        with patch.dict(os.environ, {'GEMINI_FREE_TIER_CONFIRMED': ''}):
            bot.process(event, True)
        generate.assert_not_called()
        reserve.assert_not_called()
        blob.assert_not_called()
        self.assertEqual(api.return_value.reply_message.call_args.args[0].messages[0].text, AI_PAUSED_REPLY)

    @patch.object(bot, 'MessagingApi')
    @patch.object(bot, 'ApiClient')
    @patch.object(bot, 'consented', return_value=True)
    @patch.object(bot, 'reserve')
    @patch.object(bot, 'generate')
    def test_paused_chat_never_generates_or_reserves(self, generate, reserve, consent, client, api):
        event = SimpleNamespace(source=SimpleNamespace(type='user', user_id='u'), reply_token='reply',
                                message=SimpleNamespace(text='夕食の相談'))
        with patch.dict(os.environ, {'GEMINI_FREE_TIER_CONFIRMED': 'false'}):
            bot.process(event, False)
        generate.assert_not_called()
        reserve.assert_not_called()
        self.assertEqual(api.return_value.reply_message.call_args.args[0].messages[0].text, AI_PAUSED_REPLY)

    @patch.object(bot, 'consented', return_value=True)
    @patch.object(bot, 'recent_rows', return_value=[])
    @patch.object(bot, 'generate')
    def test_history_and_week_remain_available_when_ai_is_paused(self, generate, rows, consent):
        with patch.dict(os.environ, {'GEMINI_FREE_TIER_CONFIRMED': 'false'}):
            self.assertIn('記録はありません', bot.text_command('u', '履歴'))
            self.assertIn('7日間の振り返り', bot.text_command('u', '7日間'))
        generate.assert_not_called()

    @patch.object(bot.requests, 'post')
    @patch.object(bot, 'db')
    def test_public_cost_status_makes_no_provider_request_and_does_not_claim_billing_verified(self, db, post):
        with patch.dict(os.environ, {'GEMINI_FREE_TIER_CONFIRMED': 'false'}):
            response = bot.app.test_client().get('/cost-status')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json['ai_requests_enabled'])
        self.assertFalse(response.json['provider_billing_auto_verified'])
        self.assertFalse(response.json['paid_fallback_enabled'])
        db.assert_not_called()
        post.assert_not_called()
