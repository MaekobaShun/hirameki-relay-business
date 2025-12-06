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
    # 最初に文字数チェック（AI判定をスキップしてAPI呼び出しを削減）
    if len(detail) < 20:
        print("[AI判定] 詳細が20文字未満のため、AI判定をスキップします")
        return False, True, "詳細が20文字未満です。もう少し詳しく説明してください。"
    
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
        prompt = f"""以下の投稿内容について、明確な基準に基づいて判定してください。

タイトル: {title}
詳細: {detail}
カテゴリ: {category}

判定基準は以下とします。

------------------------------------
【1. 不適切な投稿（is_inappropriate）】
以下のいずれかに該当する場合に True とする：
- 暴力、脅迫、自傷行為の助長
- 差別、侮辱、誹謗中傷
- 露骨な性的内容
- 違法行為の助長（薬物、犯罪、著作権侵害など）
- 個人情報（氏名、住所、電話番号、メール、社員番号など）の記載
- 会社の機密情報（顧客名・売上・内部システム情報など）
- 明らかなスパム（宣伝・無関係なURL・意味のない文字列の羅列）

※該当がない場合は False とする。

------------------------------------
【2. 内容が薄い（is_thin_content）】
以下のうち **2つ以上**に該当する場合に True とする：

抽象的すぎる  
- 「すごい」「いい感じ」「やばい」など評価語のみで構成されている  
- 内容に固有名詞・具体的名詞が一切ない

情報量がほぼない  
- 単語の羅列、意味不明な文字列（例：aaaaa, test, ？？？ など）

冗長・重複  
- 同じ単語が3回以上連続して繰り返されている

文脈の不一致  
- タイトルと詳細に明確な関連がない  
  例：「新しい会議効率化ツール」→詳細が「今日は晴れでした」

※文字数チェック（20文字未満）は事前にサーバー側で実行済みのため、ここでは判定しません。

該当が1つ以下なら False とする。

------------------------------------

出力形式（JSONのみ）：
{{
  "is_inappropriate": true/false,
  "is_thin_content": true/false,
  "reason": "両方の判定理由を簡潔に記述"
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


def suggest_category(title: str, detail: str) -> str:
    """
    タイトルと詳細から適切なカテゴリを判定・提案
    
    Args:
        title: タイトル
        detail: 詳細
    
    Returns:
        カテゴリ名（見つからない場合は空文字列）
    """
    # モデレーションが無効化されている場合は判定をスキップ
    if not ENABLE_CONTENT_MODERATION:
        return ""
    
    # APIキーが設定されていない場合は判定をスキップ
    if not GEMINI_AVAILABLE or not GEMINI_API_KEY:
        return ""
    
    # 利用可能なカテゴリリスト
    available_categories = [
        "ビジネス・業務効率化",
        "教育",
        "エンタメ",
        "クリエイティブ・創作支援",
        "生活・ライフスタイル",
        "コミュニケーション",
        "開発者ツール",
        "その他"
    ]
    
    try:
        import google.generativeai as genai
        from google.api_core import exceptions as google_exceptions
        
        # Gemini 2.5 Flash-Liteモデルを初期化
        try:
            model = genai.GenerativeModel('gemini-2.5-flash-lite')
        except Exception:
            model = genai.GenerativeModel('gemini-1.5-flash')
        
        # カテゴリ判定用のプロンプト
        prompt = f"""以下の投稿内容から、最も適切なカテゴリを1つ選択してください。

タイトル: {title}
詳細: {detail}

選択可能なカテゴリ:
{', '.join(available_categories)}

JSON形式で回答してください：
{{
  "category": "カテゴリ名（上記のいずれか1つ）"
}}

該当するカテゴリがない場合は "その他" を選択してください。"""
        
        print("[カテゴリ判定] AIでカテゴリを判定しています...")
        
        max_retries = 3
        retry_delay = 1
        response = None
        last_exception = None
        
        for attempt in range(max_retries):
            try:
                response = model.generate_content(prompt)
                if attempt > 0:
                    print(f"[カテゴリ判定] リトライ成功しました（試行 {attempt + 1}/{max_retries}）")
                break
            except google_exceptions.ResourceExhausted as e:
                last_exception = e
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    print(f"[カテゴリ判定] レート制限エラー（429）。{wait_time}秒後にリトライします... (試行 {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                else:
                    print(f"[カテゴリ判定] リトライ上限に達しました（{max_retries}回試行）")
                    raise
        
        if response is None:
            raise last_exception if last_exception else Exception("API呼び出しに失敗しました")
        
        response_text = response.text.strip()
        print(f"[カテゴリ判定] AIからのレスポンス: {response_text[:200]}...")
        
        # JSON部分を抽出
        json_match = re.search(r'\{[^}]+\}', response_text, re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group())
                suggested_category = result.get('category', '')
                
                # 提案されたカテゴリが利用可能なリストに含まれているか確認
                if suggested_category in available_categories:
                    print(f"[カテゴリ判定] 提案されたカテゴリ: {suggested_category}")
                    return suggested_category
                else:
                    print(f"[カテゴリ判定] 無効なカテゴリが提案されました: {suggested_category}")
                    return ""
            except json.JSONDecodeError as e:
                print(f"[カテゴリ判定] JSONパースエラー: {e}")
                return ""
        else:
            # JSONが見つからない場合、テキストから判定を試みる
            for category in available_categories:
                if category in response_text:
                    print(f"[カテゴリ判定] テキストから検出されたカテゴリ: {category}")
                    return category
            return ""
            
    except Exception as e:
        print(f"[カテゴリ判定] エラー: {str(e)}")
        logger.error(f"Category suggestion error: {e}", exc_info=True)
        return ""

