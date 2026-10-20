# ゼロコスト運用の確認記録

確認日: 2026-10-20。初期費用・運用費を0円にするという利用者の指定を最優先する。
アカウント開設、支払い方法登録、クレジット購入、課金有効化、プラン変更は実施していない。
この確認作業ではAI APIを呼んでいない。

## 現時点の確認結果

| 対象 | 公式条件で分かったこと | 実アカウントの確認状態 |
|---|---|---|
| Gemini | 使用モデルにはStandardのFree Tierがある。利用プロジェクトのTierを確認する必要がある | 提供画像のdietプロジェクトは無料枠。RenderのAPIキーの発行元との照合は未実施 |
| Render | Freeでも支払い方法登録済みなら、通信量・ビルド利用の超過で請求され得る | 提供画像でWorkspaceはHobby、カード未登録、使用量は無料枠内。サービスは既出画像でFree |
| Supabase | Free Planでは課金されない。ProのSpend Capは有料基本料を0円にするものではない | 提供画像のOrganizationはFree Plan、Spend cap有効。Marketplaceの表示済み過去請求はTotal Due $0.00。dietの所属照合と2026年10月の請求予定額は未確認 |
| LINE | Reply APIは月間メッセージ通数の算定対象外。固定月額は契約プランで別に発生し得る | 対象アカウントの提供画像でコミュニケーションプランを確認。コードはReply APIのみ。有料オプションの一覧は未確認 |

**全サービスの0円確認はまだ完了していない。** 公開URLやAPIキーの存在から契約・請求を推測しない。
プラグインは追加済みだが、この会話では課金・管理用の操作を呼び出せていない。

2026-10-20に提供されたRender画面の内訳:

- Payment Method: `No card on file.`
- Free Instance Hours: 0.77 / 750時間
- Included Bandwidth: 3MB / 5GB
- Included Pipeline Minutes: 1 / 500分
- Included Services: 1 / 25、Custom Domains: 0 / 2

以上は画面が取得された時点の状態。請求予定額・請求履歴自体の画像は未確認。
LINEも提供画像でコミュニケーションプランを確認済み。

追加提供されたSupabase Billing画面の確認結果:

- Subscription Plan: `Free Plan`
- Cost Control: `Spend cap is enabled`
- Organizationの管理および請求はVercel Marketplace側。
- Past InvoicesにもVercel Marketplaceで管理する旨が表示。
- 画像にはOrganization名・project ref・請求額自体は写っていない。
  本アプリのプロジェクト`nfcdspogzywouytdtjjy`の所属先であることを照合し、
  Marketplaceの請求画面を確認するまで、これだけで本アプリ全体の0円確認完了とはしない。

続いて提供されたVercel MarketplaceのSupabase Installation画面:

- 全体が見えている過去請求（2026年8月・3月・2月・1月、2025年11月・10月・9月）は、
  いずれも`Paid`かつ`Total Due $0.00`と表示されている。
- `Paid`は請求の処理状態。Supabaseの契約がPro等であるという意味ではない。
- Total Dueの表示だけから、過去の支払総額や請求書の内訳を断定しない。
- 2026年10月分の請求・現在の請求予定額は写っていないため未確認のまま。
- InstallationにはInstalled products 4と表示。個々のプロジェクトの内訳は未確認。

LINE Official Account Managerの提供画像:

- 対象アカウント「AI栄養士ユキ｜写真で食事アドバイス」の右側に
  `コミュニケーション`表示を確認。
- このプラン表示の確認をもって、未確認だったLINE基本プランの確認を完了。
  有料オプションの有無や請求履歴まで検証したものではない。

## 優先して確認する画面

1. [Google AI Studio Projects](https://aistudio.google.com/projects): 現在のAPIキーに対応するプロジェクトのBilling TierがFreeか確認。
   Set up billing、Buy credits、Prepay、Auto-reload等の操作は行わない。
2. Render DashboardのWorkspace Billing: Workspaceのプラン、支払い方法の有無、
   サービスのFree表示、月内の通信量・ビルド使用量・請求予定額を確認。
3. Supabaseの対象OrganizationのBilling: Free Plan表示と請求予定額を確認。
   対象プロジェクトが所属するOrganizationであることも照合する。
4. LINE Official Account Managerの料金プラン: コミュニケーションプラン/月額0円であること、
   有料オプションが付いていないことを確認。

共有してもらう情報はプラン名と請求予定額等の必要最小限だけ。
APIキー、カード情報、個人の請求先住所はチャットに貼らない。
有料契約が見つかっても勝手に解約・ダウングレードしない。他サービスやデータへの影響を確認する。

## 実装上の保護（新規コード、本番未反映）

- `GEMINI_FREE_TIER_CONFIRMED` が `true` 以外なら写真解析・AI相談を停止。
- Geminiネットワーク呼出し直前にも確認し、LINEハンドラ以外からも停止を迂回させない。
- 停止中は画像のダウンロードとAI利用枠の予約も行わない。
- 履歴・訂正・取消・当日集計・7日間集計はAI停止中も利用できる。
- 有料モデルへの自動切替、無料枠超過時の課金有効化、自動クレジット購入は実装しない。
- `/cost-status` はDB・AIを呼ばず、スイッチの状態を返す。クラウド側の請求を検証するAPIではない。
- `true` は運営者によるFree確認の申告であり、プロジェクトの課金状態を自動検証するものではない。
  後からGoogle側の課金設定を変えた場合は、スイッチをfalseへ戻して確認し直す。

既存のユーザー別1日10回制限は、プロジェクト全体の無料枠や金額上限ではない。
このスイッチだけでRender・DB・LINEの請求を防げるわけでもない。

## 反映順序

現在公開中のrecords-v1には上記スイッチがない。確認完了までは写真・AI相談の追加テストをしない。
Renderは提供画像でHobby・Freeサービス・カード未登録、ビルド1/500分を確認済み。
この確認に基づき、AIを呼ばない保護版のGitHub反映準備を進める。
有料プランへの変更や支払い方法登録はしない。新しいSQLの適用も不要。
保護版のデプロイ後は、未設定ならAIだけ停止する。再開はGoogleのFree Tier照合後に行う。
デプロイ後の確認は`/version`と`/cost-status`を使用し、確認のためにAIを呼ばない。

## 参照した公式資料

- [GeminiのBillingとTier確認](https://ai.google.dev/gemini-api/docs/billing)
- [Geminiモデルの料金](https://ai.google.dev/gemini-api/docs/pricing)
- [Render Freeサービスの使用量・請求条件](https://render.com/docs/free)
- [RenderのFreeでも請求が発生する条件](https://render.com/docs/faq)
- [Supabaseの費用制御](https://supabase.com/docs/guides/platform/cost-control)
- [LINEの通数算定・Reply API](https://developers.line.biz/en/docs/messaging-api/pricing/)
