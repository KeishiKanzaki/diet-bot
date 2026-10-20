"""Deterministic meal-record commands; no model calls or unscoped DB access."""
import datetime as dt
import re
from dataclasses import dataclass
from typing import Optional

from nutrition import JST, calorie_summary


class RecordInputError(Exception):
    """A command error safe to show directly to the user."""


EDIT_HELP = '「履歴」で番号を確認し、「訂正 番号 料理名 カロリー」と送ってください。\n例：訂正 123 焼き魚定食 600\nカロリーは0〜5000の整数です。'
CANCEL_HELP = '「履歴」で番号を確認し、「取消 番号」と送ってください。\n例：取消 123\n確認後、「取消確定 番号」でその1件を削除します。'
MANUAL_HELP = '「記録 料理名 カロリー」と送ってください。\n例：記録 焼き魚定食 600\nカロリーは0〜5000の整数です。AIやPFCの推定は使いません。'


def is_manual_record_request(text):
    return bool(re.match(r'^記録(?:\s|$)', text))


def parse_manual_meal(text):
    if not is_manual_record_request(text):
        return None
    match = re.fullmatch(r'記録\s+(.+?)\s+([0-9]{1,4})(?:kcal)?', text, re.IGNORECASE)
    if not match:
        raise RecordInputError(MANUAL_HELP)
    name, calorie = ' '.join(match[1].split()), int(match[2])
    if not 1 <= len(name) <= 100 or calorie > 5000:
        raise RecordInputError(MANUAL_HELP)
    return dict(food_name=name, calorie=calorie, protein=None, fat=None, carbs=None)


def save_manual_meal(database, user_id, message_id, meal):
    if not message_id:
        raise RecordInputError('メッセージを識別できませんでした。新しいメッセージで再送してください。')
    # The unique message_id index already exists in 001_beta. A duplicate must
    # neither overwrite a later edit nor create a second entry.
    rows = database.table('food_logs').upsert(
        {'user_id': user_id, 'message_id': message_id, 'food_name': meal['food_name'],
         'calorie': meal['calorie'], 'protein': None, 'fat': None, 'carbs': None},
        on_conflict='message_id', ignore_duplicates=True,
    ).execute().data
    if not rows:
        return 'このメッセージはすでに記録済みです。重ねて保存していません。「履歴」で確認してください。'
    row = rows[0]
    return (f"手入力で記録しました。\n#{row['id']} {meal['food_name']} / {meal['calorie']}kcal（入力値）\n"
            'PFCは不明として保存しています。\n「今日」で合計、「履歴」で番号を確認できます。')


@dataclass(frozen=True)
class RecordCommand:
    action: str
    record_id: int
    food_name: Optional[str] = None
    calorie: Optional[int] = None


def parse_record_command(text):
    if not text.startswith(('訂正', '取消')):
        return None
    if text.startswith('訂正'):
        match = re.fullmatch(r'訂正\s+([1-9][0-9]{0,18})\s+(.+?)\s+([0-9]{1,4})(?:kcal)?', text, re.IGNORECASE)
        if not match:
            raise RecordInputError(EDIT_HELP)
        record_id, name, calorie = int(match[1]), match[2].strip(), int(match[3])
        if record_id > 2**63 - 1 or not 1 <= len(name) <= 100 or calorie > 5000:
            raise RecordInputError(EDIT_HELP)
        return RecordCommand('edit', record_id, name, calorie)
    match = re.fullmatch(r'(取消確定|取消)\s+([1-9][0-9]{0,18})', text)
    if not match or int(match[2]) > 2**63 - 1:
        raise RecordInputError(CANCEL_HELP)
    return RecordCommand('cancel' if match[1] == '取消確定' else 'preview_cancel', int(match[2]))


def week_bounds(now=None):
    now = now or dt.datetime.now(JST)
    today = now.astimezone(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    start, end = today - dt.timedelta(days=6), today + dt.timedelta(days=1)
    return start, end


def local_time(value):
    value = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('Record timestamp must have a timezone')
    return value.astimezone(JST)


def safe_name(value):
    return ' '.join((value or '料理名不明').split())[:100]


def calorie_label(value):
    return 'カロリー不明' if value is None else f'約{value:g}kcal'


def format_history(rows):
    if not rows:
        return '直近7日間の記録はありません。食事写真、または「記録 料理名 カロリー」で記録できます。'
    lines = ['直近7日間の履歴（新しい順・最大20件）']
    for row in rows[:20]:
        when = local_time(row['created_at']).strftime('%m/%d %H:%M')
        lines.append(f"#{row['id']} {when} {safe_name(row.get('food_name'))} / {calorie_label(row.get('calorie'))}")
    lines.append('訂正：訂正 番号 料理名 カロリー\n取消：取消 番号\n先頭の番号を使ってください。')
    return '\n'.join(lines)


def format_week(rows, now=None):
    start, end = week_bounds(now)
    days = {start.date() + dt.timedelta(days=i): [] for i in range(7)}
    for row in rows:
        date = local_time(row['created_at']).date()
        if date in days:
            days[date].append(row)
    included = [row for daily in days.values() for row in daily]
    total, notice = calorie_summary(included)
    active = sum(bool(daily) for daily in days.values())
    known_days = sum(any(row.get('calorie') is not None for row in daily) for daily in days.values())
    last = end - dt.timedelta(days=1)
    lines = [f"7日間の振り返り（{start:%m/%d}〜{last:%m/%d}・日本時間）",
             f'記録した日：{active}/7日、食事：{len(included)}件', f'記録合計：約{total:g}kcal']
    if known_days:
        lines.append(f'カロリー記録のある日平均：約{round(total / known_days):g}kcal（{known_days}日）')
    for date, daily in days.items():
        if not daily:
            text = '記録なし'
        elif all(row.get('calorie') is None for row in daily):
            text = f'カロリー不明（{len(daily)}件）'
        else:
            value, _ = calorie_summary(daily)
            unknown = sum(row.get('calorie') is None for row in daily)
            text = f'約{value:g}kcal（{len(daily)}件' + (f'・不明{unknown}件' if unknown else '') + '）'
        lines.append(f'{date:%m/%d}：{text}')
    if notice:
        lines.append(notice.strip())
    lines.append('写真は推定値、手入力・訂正は入力値です。未記録の食事は含みません。記録なしは摂取ゼロを意味しません。')
    return '\n'.join(lines)


def handle_record_command(database, user_id, command):
    # Every read and mutation is scoped to both owner and a stable record ID.
    if command.action == 'preview_cancel':
        rows = database.table('food_logs').select('id,food_name,calorie').eq(
            'user_id', user_id).eq('id', command.record_id).execute().data
        if not rows:
            return '指定した記録が見つかりません。「履歴」で番号を確認してください。'
        row = rows[0]
        return (f"#{row['id']} {safe_name(row.get('food_name'))} / {calorie_label(row.get('calorie'))}\n"
                f'この1件を削除します。元に戻せません。\n実行する場合は「取消確定 {command.record_id}」と送ってください。')
    if command.action == 'cancel':
        rows = database.table('food_logs').delete().eq('user_id', user_id).eq('id', command.record_id).execute().data
        if not rows:
            return '指定した記録は見つからないか、すでに取消済みです。他の記録は変更していません。'
        return f'#{command.record_id} の記録を取り消しました。「今日」または「7日間」で合計を確認できます。'
    if command.action != 'edit':
        raise ValueError('Unknown record action')
    # Old macros no longer describe the corrected meal. Do not retain misleading values.
    rows = database.table('food_logs').update({
        'food_name': command.food_name, 'calorie': command.calorie,
        'protein': None, 'fat': None, 'carbs': None,
    }).eq('user_id', user_id).eq('id', command.record_id).execute().data
    if not rows:
        return '指定した記録が見つかりません。「履歴」で番号を確認してください。'
    return (f'#{command.record_id} を訂正しました。\n{command.food_name} / {command.calorie}kcal（入力値）\n'
            'PFCは再推定せず、不明として扱います。')
