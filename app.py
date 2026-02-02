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
    TextMessage,
    ShowLoadingAnimationRequest # ← 追加
)
from linebot.v3.webhooks import MessageEvent, ImageMessageContent, TextMessageContent
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

if not all([LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    print("【Warning】環境変数が不足しています。Renderの設定画面を確認してください。")

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-3-flash-preview") 

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

# ==========================================
# ▼▼▼ 1. 画像メッセージの処理（最終レイアウト決定版） ▼▼▼
# ==========================================
@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_blob_api = MessagingApiBlob(api_client)
        
        try:
            user_id = event.source.user_id

            # Loadingアニメーション
            line_bot_api.show_loading_animation(
                ShowLoadingAnimationRequest(chatId=user_id, loadingSeconds=20)
            )

            # ユーザー登録チェック
            user_check = supabase.table("users").select("user_id", "target_weight").eq("user_id", user_id).execute()
            
            if not user_check.data:
                try:
                    profile = line_bot_api.get_profile(user_id)
                    display_name = profile.display_name
                except:
                    display_name = "Guest"
                
                supabase.table("users").insert({
                    "user_id": user_id,
                    "user_name": display_name,
                    "target_weight": 0,
                    "current_weight": 0
                }).execute()

            # 画像取得 & Gemini解析
            image_data = line_bot_blob_api.get_message_content(event.message.id)
            image = Image.open(io.BytesIO(image_data))

            prompt = """
            あなたはユーザー（20代女性）の親友「ユキ」です。
            送られた食事の写真を見て、以下のJSONフォーマットのみを出力してください。
            
            【重要な制約】
            ・カロリーや栄養素は画像からの「推測値」です。
            ・医療的アドバイスは禁止。
            
            【出力フォーマット】
            {
                "food_name": "料理名（短く）",
                "calorie": 整数値,
                "carbs": "炭水化物の推測値（例: 50g）",
                "protein": "タンパク質の推測値（例: 20g）",
                "fat": "脂質の推測値（例: 15g）",
                "reply_text": "タメ口ギャル語で全肯定。数値には触れず、見た目やバランスを褒める。3行以内。"
            }
            """
            
            response = model.generate_content(
                [prompt, image],
                generation_config={"response_mime_type": "application/json"}
            )
            
            data = json.loads(response.text)
            
            food_name = data.get("food_name", "ご飯")
            calorie = data.get("calorie", 0)
            carbs = data.get("carbs", "不明")
            protein = data.get("protein", "不明")
            fat = data.get("fat", "不明")
            reply_base = data.get("reply_text", "美味しそう！✨")

            # データを保存
            supabase.table("food_logs").insert({
                "user_id": user_id,
                "food_name": food_name,
                "calorie": calorie
            }).execute()

            # 今日の合計を計算
            jst = datetime.timezone(datetime.timedelta(hours=9))
            today_start = datetime.datetime.now(jst).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            rows = supabase.table("food_logs").select("calorie").eq("user_id", user_id).gte("created_at", today_start).execute()
            total_cal = sum([row['calorie'] for row in rows.data])

            # ==========================================
            # ▼▼▼ レイアウト修正（ご指定の形式） ▼▼▼
            # ==========================================
            final_reply = (
                f"{reply_base}\n\n"
                f"🍽️ {food_name}\n"
                f"📊 今回の目安:\n"
                f"・カロリー: 約{calorie}kcal\n"
                f"・P(タンパク質): {protein}\n"
                f"・F(脂質): {fat}\n"
                f"・C(炭水化物): {carbs}\n\n"
                f"────────\n"
                f"今日の合計: {total_cal}kcal 📝"
            )

            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=final_reply)]
                )
            )
            
        except Exception as e:
            print(f"Error: {e}")
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="ごめん、ちょっと見えなかったかも💦 もう一回送ってみて！🥺")]
                )
            )

# ==========================================
# ▼▼▼ 2. テキストメッセージの処理 (シンプル会話版) ▼▼▼
# ==========================================
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        
        user_id = event.source.user_id
        user_message = event.message.text
        
        # 1. Loadingアニメーション（会話でも出すと親切）
        line_bot_api.show_loading_animation(
            ShowLoadingAnimationRequest(chatId=user_id, loadingSeconds=20)
        )

        try:
            # 2. ユーザーの名前だけ取得（親しみを込めるため）
            user_data = supabase.table("users").select("user_name").eq("user_id", user_id).execute()
            user_name = "キミ"
            if user_data.data:
                user_name = user_data.data[0]['user_name']

            # 3. Geminiへのプロンプト（体重管理の話は削除）
            prompt = f"""
            あなたは20代女性の親友「ユキ」です。
            ユーザー（名前: {user_name}）から以下のメッセージが来ました。
            
            メッセージ: "{user_message}"
            
            以下のルールで返信してください：
            ・タメ口、ギャル語、全肯定。
            ・短くテンポよく（3行以内）。
            ・「お腹すいた」などの相談には、食事管理を頑張ってる友達として励ます。
            ・体重の数字や目標設定の話は自分からはしない。
            """
            
            response = model.generate_content(prompt)
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
            # エラー時は何もしない

if __name__ == "__main__":
    app.run(port=5000)