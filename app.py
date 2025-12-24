import os
import io
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

app = Flask(__name__)

# ==========================================
# ▼▼▼ 修正箇所：鍵はサーバー（Render）から読み込む設定に変更 ▼▼▼
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
# ==========================================

# サーバー側で鍵設定を忘れたときにエラーを出すための安全装置
if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, GEMINI_API_KEY]):
    print("【Warning】環境変数が設定されていません。Renderの設定画面でキーを入力してください。")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

genai.configure(api_key=GEMINI_API_KEY)
# 無料枠の最新モデルを指定
model = genai.GenerativeModel('gemini-2.5-flash')

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
            # 画像の取得
            image_data = line_bot_blob_api.get_message_content(event.message.id)
            image = Image.open(io.BytesIO(image_data))

            # プロンプト（激甘ユキちゃん）
            prompt = """
            あなたは、ユーザー（20代女性）の「親友ユキ」です。
            送られた食事の写真に対し、以下のルールで返信してください。
            ・タメ口で、ギャルっぽく明るく全肯定する。
            ・「わぁ！✨」「やば！🤤」などリアクションから入る。
            ・美容や健康の観点で褒める。
            ・カロリーは「だいたい〇〇kcalかな？」と軽く添える。
            ・敬語は禁止。3行程度の短文で。
            """
            
            response = model.generate_content([prompt, image])
            reply_text = response.text

            # 返信
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)]
                )
            )
            
        except Exception as e:
            print(f"Error: {e}")
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="ごめんね、うまく見えなかったみたい💦 もう一回送ってくれる？🥺")]
                )
            )

if __name__ == "__main__":
    app.run(port=5000)