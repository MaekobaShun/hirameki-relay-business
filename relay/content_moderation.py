import os
import json
import re
import time
import logging
from typing import Tuple

# ロガーを設定
logger = logging.getLogger(__name__)

# コンテンツモデレーションの有効/無効を環境変数で制御
ENABLE_CONTENT_MODERATION = os.environ.get('ENABLE_CONTENT_MODERATION', 'true').lower() == 'true'

# Gemini APIキーを環境変数から取得
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

# APIキーが設定されている場合のみ初期化
if GEMINI_API_KEY and ENABLE_CONTENT_MODERATION:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
    except ImportError:
        GEMINI_AVAILABLE = False
else:
    GEMINI_AVAILABLE = False


def check_content(title: str, detail: str, category: str) -> Tuple[bool, bool, str]:
    """
    投稿内容をAIで判定する
    
    Args:
        title: タイトル
        detail: 詳細
        category: カテゴリ
    
    Returns:
        (is_inappropriate: bool, is_thin_content: bool, reason: str)
        - is_inappropriate: 不適切な投稿かどうか
        - is_thin_content: 内容が薄いかどうか
        - reason: 判定理由
    """
    # モデレーションが無効化されている場合は判定をスキップ
    if not ENABLE_CONTENT_MODERATION:
        logger.info("Content moderation is disabled")
        print("[AI判定] モデレーションが無効化されています")
        return False, False, ""
    
    # APIキーが設定されていない場合は判定をスキップ
    if not GEMINI_AVAILABLE or not GEMINI_API_KEY:
        logger.warning("Gemini API is not available or API key is not set")
        print("[AI判定] Gemini APIが利用できません（APIキーが設定されていない可能性があります）")
        return False, False, ""
    
    try:
        import google.generativeai as genai
        from google.api_core import exceptions as google_exceptions
        
        # Gemini 2.5 Flash-Liteモデルを初期化
        try:
            model = genai.GenerativeModel('gemini-2.5-flash-lite')
            print("[AI判定] モデル: gemini-2.5-flash-lite を使用します")
        except Exception as model_error:
            # フォールバック: gemini-1.5-flashを使用
            print(f"[AI判定] gemini-2.5-flash-liteが利用できません。gemini-1.5-flashにフォールバックします: {model_error}")
            model = genai.GenerativeModel('gemini-1.5-flash')
            print("[AI判定] モデル: gemini-1.5-flash を使用します")
        
        # 判定用のプロンプト
        prompt = f"""以下の投稿内容を判定してください。

タイトル: {title}
詳細: {detail}
カテゴリ: {category}

以下の2つの観点で判定してください：

1. 不適切な投稿かどうか
   - 暴力的、差別的、違法、スパム、個人情報の漏洩などの不適切な内容が含まれているか
   - 回答: True または False

2. 内容が薄いかどうか
   - 文字数が極端に少ない（詳細が20文字未満など）
   - 具体性がない（「いい感じ」「すごい」など抽象的な表現のみ）
   - 情報量が少ない（単語の羅列、意味不明な文字列）
   - 重複が多い（同じ単語の繰り返し）
   - 文脈がない（タイトルと詳細が関連していない）
   - 回答: True または False

以下のJSON形式で回答してください（JSON以外の説明は不要です）：
{{
    "is_inappropriate": true/false,
    "is_thin_content": true/false,
    "reason": "判定理由を簡潔に説明"
}}"""
        
        # デバッグログ: 判定開始
        print("\n" + "="*60)
        print("[AI判定] 判定を開始します")
        print(f"[AI判定] タイトル: {title}")
        print(f"[AI判定] 詳細: {detail}")
        print(f"[AI判定] カテゴリ: {category}")
        logger.info(f"Checking content - Title: {title[:50]}..., Detail: {detail[:50]}...")
        
        # AI判定を実行（リトライ機能付き）
        print("[AI判定] Gemini APIにリクエストを送信しています...")
        
        max_retries = 3
        retry_delay = 1  # 初回は1秒
        response = None
        last_exception = None
        
        for attempt in range(max_retries):
            try:
                response = model.generate_content(prompt)
                if attempt > 0:
                    print(f"[AI判定] リトライ成功しました（試行 {attempt + 1}/{max_retries}）")
                break
            except google_exceptions.ResourceExhausted as e:
                last_exception = e
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    print(f"[AI判定] レート制限エラー（429）。{wait_time}秒後にリトライします... (試行 {attempt + 1}/{max_retries})")
                    logger.warning(f"Rate limit error, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                else:
                    print(f"[AI判定] リトライ上限に達しました（{max_retries}回試行）")
                    logger.error(f"Max retries reached ({max_retries} attempts)")
                    raise
        
        if response is None:
            raise last_exception if last_exception else Exception("API呼び出しに失敗しました")
        
        response_text = response.text.strip()
        
        print(f"[AI判定] AIからのレスポンス: {response_text[:200]}...")
        logger.info(f"Gemini response: {response_text[:200]}")
        
        # JSON部分を抽出
        json_match = re.search(r'\{[^}]+\}', response_text, re.DOTALL)
        if json_match:
            try:
                json_str = json_match.group()
                print(f"[AI判定] 抽出したJSON: {json_str}")
                result = json.loads(json_str)
                is_inappropriate = result.get('is_inappropriate', False)
                is_thin_content = result.get('is_thin_content', False)
                reason = result.get('reason', '')
                
                print(f"[AI判定] 判定結果:")
                print(f"  - 不適切な投稿: {is_inappropriate}")
                print(f"  - 内容が薄い: {is_thin_content}")
                print(f"  - 理由: {reason}")
                print("="*60 + "\n")
                
                logger.info(f"Moderation result - Inappropriate: {is_inappropriate}, Thin: {is_thin_content}, Reason: {reason}")
                return bool(is_inappropriate), bool(is_thin_content), reason
            except json.JSONDecodeError as e:
                # JSONのパースに失敗した場合
                print(f"[AI判定] JSONパースエラー: {e}")
                print(f"[AI判定] 元のレスポンス: {response_text}")
                logger.error(f"JSON decode error: {e}, Response: {response_text}")
                return False, False, "判定結果の解析に失敗しました"
        else:
            # JSONが見つからない場合、テキストから判定を試みる
            # フォールバック処理
            print(f"[AI判定] JSONが見つかりません。テキストから判定を試みます...")
            print(f"[AI判定] レスポンス全文: {response_text}")
            logger.warning(f"JSON not found in response, trying text-based fallback: {response_text}")
            
            if '不適切' in response_text or 'inappropriate' in response_text.lower():
                print("[AI判定] フォールバック判定: 不適切な内容が検出されました")
                return True, False, "不適切な内容が検出されました"
            elif '薄い' in response_text or 'thin' in response_text.lower():
                print("[AI判定] フォールバック判定: 内容が不十分です")
                return False, True, "内容が不十分です"
            else:
                print("[AI判定] フォールバック判定: 判定結果の解析に失敗しました")
                return False, False, "判定結果の解析に失敗しました"
            
    except Exception as e:
        # エラーが発生した場合は投稿を許可（安全側に倒す）
        error_msg = f"判定処理でエラーが発生しました: {str(e)}"
        print(f"[AI判定] エラー: {error_msg}")
        print(f"[AI判定] エラータイプ: {type(e).__name__}")
        logger.error(f"Content moderation error: {e}", exc_info=True)
        return False, False, error_msg

