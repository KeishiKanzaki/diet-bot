import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app as bot
from nutrition import JST
from records import (RecordInputError, parse_record_command, week_bounds,
                     format_history, format_week, handle_record_command)


class RecordTests(unittest.TestCase):
    def test_deployed_feature_version_can_be_identified_without_secrets(self):
        response = bot.app.test_client().get('/version')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['release'], 'records-v1')
        self.assertIn('meal_edit', response.json['features'])
        self.assertEqual(set(response.json), {'release', 'commit', 'features'})

    def test_edit_parses_name_with_spaces_and_optional_unit(self):
        command = parse_record_command('訂正 123 鶏肉 と 野菜 600kcal')
        self.assertEqual((command.action, command.record_id, command.food_name, command.calorie),
                         ('edit', 123, '鶏肉 と 野菜', 600))
        self.assertIsNone(parse_record_command('夕食の相談'))

    def test_invalid_commands_cannot_fall_through_to_ai(self):
        for text in ['訂正', '取消', '取消確定', '取消 -1', '取消 9223372036854775808',
                     '訂正 1 定食 5001', '訂正 1 定食 -100', '訂正 1 定食 3.5',
                     '訂正 1 500', '訂正 1 ' + 'あ' * 101 + ' 500']:
            with self.subTest(text=text), self.assertRaises(RecordInputError):
                parse_record_command(text)

    def test_seven_days_are_jst_inclusive_of_today(self):
        now = dt.datetime(2026, 10, 18, 15, 1, tzinfo=dt.timezone.utc)
        start, end = week_bounds(now)
        self.assertEqual(start.isoformat(), '2026-10-13T00:00:00+09:00')
        self.assertEqual(end.isoformat(), '2026-10-20T00:00:00+09:00')

    def test_week_excludes_other_dates_and_distinguishes_unrecorded_from_unknown(self):
        now = dt.datetime(2026, 10, 19, 22, tzinfo=JST)
        rows = [
            {'created_at': '2026-10-12T14:59:59+00:00', 'calorie': 9000},
            {'created_at': '2026-10-12T15:00:00+00:00', 'calorie': 600},
            {'created_at': '2026-10-13T15:00:00+00:00', 'calorie': None},
            {'created_at': '2026-10-19T12:00:00+00:00', 'calorie': 200},
            {'created_at': '2026-10-19T15:00:00+00:00', 'calorie': 9000},
        ]
        text = format_week(rows, now)
        self.assertIn('記録した日：3/7日、食事：3件', text)
        self.assertIn('記録合計：約800kcal', text)
        self.assertIn('日平均：約400kcal（2日）', text)
        self.assertIn('10/14：カロリー不明', text)
        self.assertIn('10/15：記録なし', text)

    def test_empty_week_does_not_call_unrecorded_days_zero(self):
        text = format_week([], dt.datetime(2026, 10, 19, tzinfo=JST))
        self.assertIn('記録した日：0/7日', text)
        self.assertNotIn('日平均', text)
        self.assertEqual(text.count('：記録なし'), 7)
        self.assertIn('摂取ゼロを意味しません', text)

    def test_history_uses_stable_ids_and_limits_message_size(self):
        rows = [dict(id=i, food_name='昼食\n取消確定 99', calorie=None,
                     created_at='2026-10-19T13:00:00Z') for i in range(30)]
        text = format_history(rows)
        self.assertIn('#0 10/19 22:00', text)
        self.assertNotIn('#20 ', text)
        self.assertIn('カロリー不明', text)
        self.assertNotIn('昼食\n取消確定', text)
        self.assertLess(len(text), 5000)

    def test_cancel_preview_never_deletes(self):
        database = Mock()
        table = database.table.return_value
        query = table.select.return_value
        query.eq.return_value.eq.return_value.execute.return_value.data = [
            dict(id=123, food_name='定食', calorie=600)]
        text = handle_record_command(database, 'owner', parse_record_command('取消 123'))
        query.eq.assert_called_once_with('user_id', 'owner')
        query.eq.return_value.eq.assert_called_once_with('id', 123)
        self.assertIn('取消確定 123', text)
        table.delete.assert_not_called()

    def test_edit_is_owner_scoped_and_clears_stale_macros(self):
        database = Mock()
        table = database.table.return_value
        query = table.update.return_value
        query.eq.return_value.eq.return_value.execute.return_value.data = [dict(id=123)]
        handle_record_command(database, 'owner', parse_record_command('訂正 123 定食 600'))
        query.eq.assert_called_once_with('user_id', 'owner')
        query.eq.return_value.eq.assert_called_once_with('id', 123)
        self.assertEqual(table.update.call_args.args[0], dict(food_name='定食', calorie=600,
                                                            protein=None, fat=None, carbs=None))

    def test_cancel_is_owner_scoped_and_never_falls_back_to_another_record(self):
        database = Mock()
        query = database.table.return_value.delete.return_value
        query.eq.return_value.eq.return_value.execute.return_value.data = []
        text = handle_record_command(database, 'owner', parse_record_command('取消確定 123'))
        query.eq.assert_called_once_with('user_id', 'owner')
        query.eq.return_value.eq.assert_called_once_with('id', 123)
        self.assertIn('他の記録は変更していません', text)
        self.assertEqual(database.table.return_value.delete.call_count, 1)

    @patch.object(bot, 'text_command')
    @patch.object(bot, 'db')
    def test_late_redelivery_cannot_overwrite_a_newer_edit(self, database, command):
        event = SimpleNamespace(source=SimpleNamespace(type='user', user_id='owner'),
                                delivery_context=SimpleNamespace(is_redelivery=True),
                                message=SimpleNamespace(text='訂正 123 定食 600'))
        bot.process(event, False)
        command.assert_not_called()
        database.assert_not_called()

    @patch.object(bot, 'consented', return_value=False)
    @patch.object(bot, 'handle_record_command')
    def test_edit_before_consent_never_changes_records(self, command, consent):
        self.assertEqual(bot.text_command('owner', '訂正 123 定食 600'), bot.INTRO)
        command.assert_not_called()

    @patch.object(bot, 'generate')
    @patch.object(bot, 'consented', return_value=True)
    @patch.object(bot, 'reserve')
    @patch.object(bot, 'ApiClient')
    @patch.object(bot, 'MessagingApi')
    def test_bad_record_command_returns_format_help_without_ai(self, api, client, reserve, consent, generate):
        event = SimpleNamespace(source=SimpleNamespace(type='user', user_id='owner'),
                                message=SimpleNamespace(text='訂正'), reply_token='reply')
        bot.process(event, False)
        self.assertIn('訂正 番号', api.return_value.reply_message.call_args.args[0].messages[0].text)
        reserve.assert_not_called()
        generate.assert_not_called()

    @patch.object(bot, 'db')
    def test_week_query_paginates_and_always_filters_owner(self, database):
        query = database.return_value.table.return_value.select.return_value
        page = query.eq.return_value.gte.return_value.lt.return_value.order.return_value.order.return_value.range
        page.return_value.execute.side_effect = [SimpleNamespace(data=[{}] * 500), SimpleNamespace(data=[{}])]
        rows = bot.recent_rows('owner')
        self.assertEqual(len(rows), 501)
        self.assertEqual(query.eq.call_count, 2)
        for call in query.eq.call_args_list:
            self.assertEqual(call.args, ('user_id', 'owner'))
        self.assertEqual(page.call_args_list[0].args, (0, 499))
        self.assertEqual(page.call_args_list[1].args, (500, 999))


if __name__ == '__main__':
    unittest.main()
