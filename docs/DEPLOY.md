# 既存Renderへの改修版切り替え

対象: `diet-bot` / `https://diet-bot-yrtn.onrender.com`。
Supabase: `nfcdspogzywouytdtjjy`。旧版は2026-10-19に写真解析の復旧を利用者が確認済み。
2026-10-19: 利用者が本番SQL Editorで001_beta.sqlを実行し、
`schema_version = 001_beta` の結果画像で移行完了を確認。
改修版のGitHub反映・Renderデプロイ・実機受入はまだ実行していない。

2026-10-19に提供されたpreflight_reportを確認済み。users.user_idはtextの主キー、
user_nameはnullable text、food_logs.calorieはnullable integer、created_atはtimestamptzでnow()が既定値。
両テーブルでRLSが有効、ポリシーは0件。移行はこの構造と互換。
カロリー列は型変更せず、新規値をアプリ側で整数に揃える。未入力の既存値は集計から除外し件数を表示する。

## 操作するための接続

RenderとSupabaseの連携を接続する。APIキーをチャットに貼る必要はない。
連携できない場合は、以下の順で各管理画面を操作する。
ローカルに設定・認証がない状態では、こちらから管理画面の変更を実行できない。

## 1. 本番DBを確認・バックアップ

- SupabaseのSQL Editorで `migrations/000_preflight.sql` を実行する（読み取り専用）。
- users.user_idがtext/varcharで一意、food_logs.user_idも同じ型、calorieが数値、created_atがtimestamptzであることを確認。
- 既存の必須列、デフォルト値、RLSポリシー、制約を確認する。
  例: usersにデフォルト値のない必須列があれば、そのまま同意RPCを適用せず対応を追加する。
- SQL変更前にusersとfood_logsのデータと定義をバックアップする。
- Renderの `SUPABASE_KEY` がサーバー専用service_roleキーであることを管理画面内で確認する。
  匿名キーの場合は移行で権限がなくなるため、先に適切なキーに変更する。

## 2. DB移行

- SQL Editorで `migrations/001_beta.sql` の全体を一度に実行する。
- `BEGIN`〜`COMMIT`の一括処理。エラー時は中途半端な移行を続行せず、エラーの原因を確認する。
- 既存の食事記録を削除するSQLは含まない。ただしRLSとAPI権限を変更するので、他アプリと共有している場合は事前確認する。
- 移行後に次を実行し、`001_beta`が返ることを確認する。

```sql
select public.nutrition_schema_version();
```

## 3. Render設定

- 既存のLINE・Google・Supabaseの環境変数を維持する。
- `GEMINI_MODEL=gemini-3-flash-preview` を追加する。
  これは今回復旧した旧コードで使っているモデル。新規導入時は利用可能性を別途確認する。
- `DAILY_AI_LIMIT=10` を追加する。
- `PRIVACY_URL` に運営者と問い合わせ先を含む公開ポリシーURLを設定する。
  未設定のまま利用者への一般公開はしない。運営者本人の疎通確認は可能。
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn app:app`（既存のまま。gunicorn.conf.pyを自動読込）
- 現在動いているコミット番号と設定を記録する。移行と設定の完了前にmainをpushしない。

## 4. コードを反映

- テストと差分確認後、コードをGitHubへ反映し、Renderでデプロイする。
- このリポジトリのoriginは `KeishiKanzaki/diet-bot` のmain。
- `/` が `Yuki nutrition AI beta` を返すことを確認する。
- `/healthz` が200であることを確認する（設定の存在確認）。
- `/readyz` が200で `schema: 001_beta` を返すことを確認する（DBと移行確認）。
  Googleモデルの利用権やLINEトークンの有効性は、これらの診断では検証しない。
- LINE DevelopersのWebhook URLは既存の `/callback` のまま。

## 5. 実機確認

「使い方」→「プライバシー」→「同意する」→写真→「今日」を確認する。
既存記録がある場合は当日合計にそれも含まれる。
データ削除試験は実ユーザーの履歴を消さず、専用テストアカウントだけで実施する。

## 不具合時

- 原因はログの `stage` と例外クラスで切り分ける。秘密キーや食事原文をログへ追加しない。
- Render側のロールバックで記録しておいた旧コミットへ戻す。
- DBは追加した列・テーブルを残す。食事記録や同意情報を削除して戻さない。
- 旧版でもservice_roleキーならRLSの変更後に接続できる。旧版の利用継続は短期間に留める。
- Render無料枠の休止とSupabaseプロジェクト休止の再発を監視する。
