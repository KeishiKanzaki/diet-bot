"""Block model requests until the operator confirms the project's free tier.

This switch does not query or change provider billing settings. A true value is
an operator attestation, not proof that a provider account remains on Free.
"""
import os

AI_PAUSED_REPLY = ('AIの写真解析・相談は、無料枠の確認が終わるまで停止中です。'
                   '\n「今日」「履歴」「7日間」と、記録の訂正・取消は利用できます。')


class AIRequestsDisabled(RuntimeError):
    pass


def ai_requests_enabled():
    return os.getenv('GEMINI_FREE_TIER_CONFIRMED', '').strip().lower() == 'true'


def require_free_tier_confirmation():
    if not ai_requests_enabled():
        raise AIRequestsDisabled('Free tier is not confirmed')
