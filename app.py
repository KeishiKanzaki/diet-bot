import base64
import io
import json
import logging
import os
from functools import lru_cache

import requests
from flask import Flask, abort, jsonify, request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import ApiClient, Configuration, MessagingApi, MessagingApiBlob, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, ImageMessageContent, TextMessageContent
from PIL import Image, ImageOps, UnidentifiedImageError
from supabase import ClientOptions, create_client

from nutrition import SYSTEM_PROMPT, MEAL_PROMPT, day_bounds, validate_meal, meal_reply, calorie_summary

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024
logging.basicConfig(level=logging.INFO)
# httpx INFO logs include database query URLs containing LINE user IDs.
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
REQUIRED = ('LINE_CHANNEL_ACCESS_TOKEN', 'LINE_CHANNEL_SECRET', 'GEMINI_API_KEY', 'SUPABASE_URL', 'SUPABASE_KEY')
DEFAULT_GEMINI_MODEL = 'gemini-3-flash-preview'
LINE_REQUIRED = ('LINE_CHANNEL_ACCESS_TOKEN', 'LINE_CHANNEL_SECRET')
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET', 'not-configured'))
Image.MAX_IMAGE_PIXELS = 20_000_000
CONSENT_VERSION = '2026-10-18'
INTRO = ('ユキは食生活を支えるAIです。管理栄養士・医師ではなく、診断や個別の治療は行いません。'
         '\n18歳以上の一般成人向け招待制β版です。\n食事画像・相談文をGoogle Geminiへ送信し、'
         'LINEユーザーID・食事記録・同意日時をSupabaseに保存します。画像自体は当サービスのDBに保存しません。'
         '\n写真の栄養値は推定です。\n「プライバシー」で詳細を確認し、同意する場合は「同意する」と送信してください。')
HELP = '初めての方は「プライバシー」を確認し、「同意する」と送ってください。\n写真で食事を記録できます。\n「今日」合計を確認\n「料金」β版の案内\n「プライバシー」データの扱い\n「データ削除」全記録を削除\n相談も送れます。'


@lru_cache(maxsize=1)
def db():
    return create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'],
                         options=ClientOptions(postgrest_client_timeout=10))


def generate(text, image=None):
    parts = [{'text': text}]
    if image:
        parts.append({'inlineData': {'mimeType': 'image/jpeg', 'data': base64.b64encode(image).decode()}})
    config = {'maxOutputTokens': 2048}
    if image:
        config['responseMimeType'] = 'application/json'
    response = requests.post(
        'https://generativelanguage.googleapis.com/v1beta/models/'
        + (os.getenv('GEMINI_MODEL') or DEFAULT_GEMINI_MODEL) + ':generateContent',
        headers={'x-goog-api-key': os.environ['GEMINI_API_KEY']},
        json={'systemInstruction': {'parts': [{'text': SYSTEM_PROMPT}]},
              'contents': [{'role': 'user', 'parts': parts}], 'generationConfig': config}, timeout=(5, 20))
    response.raise_for_status()
    candidates = response.json().get('candidates', [])
    if not candidates or candidates[0].get('finishReason') != 'STOP':
        raise RuntimeError('AI response incomplete')
    result = ''.join(p.get('text', '') for p in candidates[0].get('content', {}).get('parts', []) if not p.get('thought'))
    if not result.strip():
        raise RuntimeError('AI response empty')
    return result


@app.get('/')
def home():
    return 'Yuki nutrition AI beta'


@app.get('/healthz')
def health():
    ready = all(os.getenv(key) for key in REQUIRED)
    return jsonify(status='configured' if ready else 'not_configured'), 200 if ready else 503


@app.get('/readyz')
def readiness():
    if not all(os.getenv(key) for key in REQUIRED):
        return jsonify(status='not_configured'), 503
    try:
        revision = db().rpc('nutrition_schema_version', {}).execute().data
        if revision != '001_beta':
            raise RuntimeError('Unexpected schema revision')
    except Exception as exc:
        app.logger.error('database_readiness_failed type=%s', type(exc).__name__)
        return jsonify(status='database_not_ready'), 503
    # This checks DB access and migration, not Google/LINE credentials or model access.
    return jsonify(status='ready', schema=revision), 200


@app.post('/callback')
def callback():
    # Keep help/price/privacy available even when Google or the database is down.
    if not all(os.getenv(key) for key in LINE_REQUIRED):
        app.logger.error('webhook_line_configuration_missing')
        abort(503)
    signature = request.headers.get('X-Line-Signature')
    if not signature:
        abort(400)
    try:
        handler.handle(request.get_data(as_text=True), signature)
    except (InvalidSignatureError, ValueError):
        app.logger.warning('webhook_rejected')
        abort(400)
    return 'OK'


def today_rows(user_id):
    start, end = day_bounds()
    return db().table('food_logs').select('calorie').eq('user_id', user_id).gte('created_at', start).lt('created_at', end).execute().data


def consented(user_id):
    rows = db().table('users').select('consent_version').eq('user_id', user_id).execute().data
    return bool(rows and rows[0].get('consent_version') == CONSENT_VERSION)


def text_command(user_id, text):
    if text in ('使い方', 'ヘルプ'):
        return HELP
    if text == 'プライバシー':
        return INTRO + '\n記録は削除するまで保持。削除は「データ削除」→「削除を確定」。バックアップや外部事業者側の保持は各社規定によります。\n' + os.getenv('PRIVACY_URL', '運営者への連絡先・正式ポリシーは招待時の案内をご確認ください。')
    if text == '料金':
        return '現在は無料の招待制β版です。1日あたりAI利用は最大' + os.getenv('DAILY_AI_LIMIT', '10') + '回（失敗も含む）。自動課金はありません。有料版は検証後にご案内します。'
    if text == 'データ削除':
        return '全食事記録・同意情報・利用履歴を削除します。実行する場合は「削除を確定」と送信してください。'
    if text == '削除を確定':
        db().rpc('delete_nutrition_user', {'p_user_id': user_id}).execute()
        return 'データを削除しました。再開時には再度同意が必要です。'
    if text == '同意する':
        db().rpc('accept_nutrition_consent', {'p_user_id': user_id}).execute()
        return '同意を記録しました。\n' + HELP
    if not consented(user_id):
        return INTRO
    if text == '今日':
        rows = today_rows(user_id)
        total, notice = calorie_summary(rows)
        return f"今日の記録: {len(rows)}件、約{total:g}kcal。{notice}\n写真からの推定です。未記録の食事は含みません。"
    return None


def reserve(event):
    # Atomic database reservation: duplicate events never consume a second slot.
    return db().rpc('reserve_ai_usage', {'p_user_id': event.source.user_id,
                   'p_event_id': event.webhook_event_id, 'p_limit': int(os.getenv('DAILY_AI_LIMIT', '10'))}).execute().data


def process(event, is_image):
    # Group messages may contain other people's personal data. Support personal chats only.
    if event.source.type != 'user':
        return
    user_id = event.source.user_id
    stage = 'command'
    with ApiClient(Configuration(access_token=os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))) as client:
        api = MessagingApi(client)
        try:
            reply = None if is_image else text_command(user_id, event.message.text.strip())
            if is_image and not consented(user_id):
                reply = INTRO
            if reply is None:
                stage = 'usage_reservation'
                status = reserve(event)
                if status == 'duplicate':
                    return
                if status == 'limit':
                    reply = '今日のAI利用上限に達しました。明日また使ってください。「今日」で記録を確認できます。'
                elif status != 'allowed':
                    raise RuntimeError('Unexpected reservation status')
                elif is_image:
                    stage = 'line_image_download'
                    blob = MessagingApiBlob(client).get_message_content(event.message.id)
                    if len(blob) > 10 * 1024 * 1024:
                        raise ValueError('写真は10MB以下にしてください。')
                    with Image.open(io.BytesIO(blob)) as source:
                        if source.width * source.height > 20_000_000:
                            raise ValueError('写真のサイズを小さくして送ってください。')
                        image = ImageOps.exif_transpose(source).convert('RGB')
                        image.thumbnail((1600, 1600))
                        buffer = io.BytesIO()
                        image.save(buffer, format='JPEG', quality=85)
                    stage = 'gemini_image_analysis'
                    meal = validate_meal(json.loads(generate(MEAL_PROMPT, buffer.getvalue())))
                    stage = 'meal_save'
                    db().rpc('save_nutrition_meal', {'p_user_id': user_id, 'p_event_id': event.webhook_event_id,
                             'p_message_id': event.message.id, 'p_meal': meal}).execute()
                    stage = 'daily_summary'
                    reply = meal_reply(meal, *calorie_summary(today_rows(user_id)))
                else:
                    stage = 'gemini_chat'
                    reply = generate(event.message.text[:2000])[:4000]
        except (ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            app.logger.warning('message_validation_failed stage=%s', stage)
            reply = '内容を確認できませんでした。食事がはっきり写った小さめの写真、または短い文章で再送してください。記録を確認するには「今日」と送信してください。'
        except Exception as exc:
            # Never log request bodies, identifiers, keys, or provider error details.
            app.logger.error('message_processing_failed stage=%s type=%s', stage, type(exc).__name__)
            reply = '一時的に処理できませんでした。「今日」で保存済みの記録を確認し、時間をおいて再送してください。'
        try:
            api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)]))
        except Exception as exc:
            app.logger.error('line_reply_failed type=%s', type(exc).__name__)
            raise RuntimeError('LINE reply failed') from None


@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    process(event, True)


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    process(event, False)


if __name__ == '__main__':
    app.run(port=int(os.getenv('PORT', '5000')))
