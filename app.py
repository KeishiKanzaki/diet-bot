import os
import io
import json
import datetime
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import MessageEvent, ImageMessageContent
import google.generativeai as genai
from PIL import Image
from supabase import create_client, Client

app = Flask(__name__)

# ==========================================
# ▼▼▼ 環境変数の取得 ▼▼▼
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

# キー設定チェック
if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    print("【Warning】環境変数が不足しています。Renderの設定画面を確認してください。")

# LINE設定
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Gemini設定
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash') # モデル名は安定版の2.5-flash推奨

# Supabase設定
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.route("/")
def home():
    return "I'm alive!"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_blob_api = MessagingApiBlob(api_client)
        
        try:
            # 1. 画像の取得
            image_data = line_bot_blob_api.get_message_content(event.message.id)
            image = Image.open(io.BytesIO(image_data))
            
            # ユーザーID取得（データベース用）
            user_id = event.source.user_id

            # 2. プロンプト（JSONモードで数値を抽出させる）
            prompt = """
            あなたはユーザー（20代女性）の親友「ユキ」です。
            送られた食事の写真を見て、以下のJSONフォーマットのみを出力してください。
            余計な文字（```json など）は含めないでください。

            {
                "food_name": "料理名（短く）",
                "calorie": カロリーの推定値（整数のみ、例: 600）,
                "reply_text": "本人への返信（タメ口ギャル語、全肯定、カロリー数値には触れず「美味しそう！」などの感想メインで3行以内）"
            }
            """
            
            # JSON生成モードでリクエスト
            response = model.generate_content(
                [prompt, image],
                generation_config={"response_mime_type": "application/json"}
            )
            
            # JSONを辞書型に変換
            data = json.loads(response.text)
            food_name = data.get("food_name", "ご飯")
            calorie = data.get("calorie", 0)
            reply_base = data.get("reply_text", "美味しそう！✨")

            # 3. Supabaseに保存（記憶）
            supabase.table("food_logs").insert({
                "user_id": user_id,
                "food_name": food_name,
                "calorie": calorie
            }).execute()

            # 4. 今日の合計カロリーを計算
            # 日本時間(JST)の今日0時を作成
            jst = datetime.timezone(datetime.timedelta(hours=9))
            today_start = datetime.datetime.now(jst).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            
            # データベースから今日の分を取得して集計
            rows = supabase.table("food_logs").select("calorie").eq("user_id", user_id).gte("created_at", today_start).execute()
            total_cal = sum([row['calorie'] for row in rows.data])

            # 5. 返信メッセージ作成
            final_reply = f"{reply_base}\n\n(今日の合計: {total_cal}kcal 📝)"

            # LINEへ送信
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=final_reply)]
                )
            )
            
        except Exception as e:
            print(f"Error: {e}")
            # エラー時は安全なメッセージを返す
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="ごめん、ちょっと計算ミスっちゃった💦 もう一回送ってみて！🥺")]
                )
            )

if __name__ == "__main__":
    app.run(port=5000)