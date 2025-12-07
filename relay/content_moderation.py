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


# 融合モード定義
FUSION_MODES = {
    "creative": {
        "name": "創造モード",
        "description": "自由な発想で、面白さ重視の融合アイデアが出ます。"
    },
    "practical": {
        "name": "実現モード",
        "description": "現実に実行できる、現実的な融合アイデアが出ます。"
    }
}

# ペルソナ定義
PERSONA_DEFINITIONS = {
    "professor": {
        "name": "教授",
        "thinking": "論理的・理系の視点で考える",
        "tone": "丁寧で真面目な語調"
    },
    "gal": {
        "name": "ギャル",
        "thinking": "流行・SNSで話題になるかを重視",
        "tone": "明るく、砕けた口調"
    },
    "elementary": {
        "name": "小学生",
        "thinking": "常識に縛られず、直感で考える",
        "tone": "やさしく、素直な語調"
    },
    "alien": {
        "name": "宇宙人",
        "thinking": "人間の価値観にとらわれない視点",
        "tone": "静かで、不思議な語調"
    },
    "boss": {
        "name": "上司",
        "thinking": "実務・予算・実行可能性を重視",
        "tone": "落ち着いて、現実的な語調"
    },
    "ceo": {
        "name": "スタートアップCEO",
        "thinking": "市場性・伸ばし方・ビジネスモデル重視",
        "tone": "熱量が高い、カジュアル"
    },
    "future": {
        "name": "未来人",
        "thinking": "未来の社会と技術を前提に考える",
        "tone": "落ち着いて、客観的な語調"
    }
}


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
            # フォールバック: gemini-2.5-flashを使用
            print(f"[AI判定] gemini-2.5-flash-liteが利用できません。gemini-2.5-flashにフォールバックします: {model_error}")
            model = genai.GenerativeModel('gemini-2.5-flash')
            print("[AI判定] モデル: gemini-2.5-flash を使用します")
        
        # 判定用のプロンプト
        prompt = f"""以下の投稿内容について、明確な基準に基づいて判定してください。

タイトル: {title}
詳細: {detail}
カテゴリ: {category}

判定基準は以下とします。

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
  "reason": "判定理由を簡潔に記述"
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
            model = genai.GenerativeModel('gemini-2.5-flash')
        
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


def fuse_ideas(idea_list: list, mode: str = 'creative', persona: str = 'professor') -> dict:
    """
    複数のアイデアをAIで融合して新しいアイデアを生成
    
    Args:
        idea_list: アイデアのリスト。各アイデアは {"title": str, "detail": str, "category": str} の形式
        mode: 融合モード ('creative' or 'practical')
        persona: ペルソナID
    
    Returns:
        {"title": str, "detail": str, "category": str} または空のdict（エラー時）
    """
    # モデレーションが無効化されている場合はスキップ
    if not ENABLE_CONTENT_MODERATION:
        return {}
    
    # APIキーが設定されていない場合はスキップ
    if not GEMINI_AVAILABLE or not GEMINI_API_KEY:
        return {}
    
    # アイデアが2〜3個であることを確認
    if len(idea_list) < 2 or len(idea_list) > 3:
        print(f"[アイデア融合] アイデア数が不正です: {len(idea_list)}個（2〜3個である必要があります）")
        return {}
    
    # モードとペルソナの設定を取得
    mode_info = FUSION_MODES.get(mode, FUSION_MODES['creative'])
    persona_info = PERSONA_DEFINITIONS.get(persona, PERSONA_DEFINITIONS['professor'])
    
    print(f"[アイデア融合] モード: {mode_info['name']}, ペルソナ: {persona_info['name']}")
    
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
        
        def _generate_with_retry(model_name, prompt, max_retries=3, initial_delay=2):
            """指定されたモデルで生成を試みる（リトライ付き）"""
            try:
                model = genai.GenerativeModel(model_name)
                print(f"[アイデア融合] モデル: {model_name} を使用します")
            except Exception as e:
                print(f"[アイデア融合] モデル初期化エラー ({model_name}): {e}")
                return None

            last_exception = None
            retry_delay = initial_delay

            for attempt in range(max_retries):
                try:
                    response = model.generate_content(prompt)
                    if attempt > 0:
                        print(f"[アイデア融合] リトライ成功しました（試行 {attempt + 1}/{max_retries}）")
                    return response
                except google_exceptions.ResourceExhausted as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        # 待機時間を長めに設定（指数バックオフ + ランダム性）
                        wait_time = retry_delay * (2 ** attempt)
                        print(f"[アイデア融合] レート制限エラー（429）。{wait_time}秒後にリトライします... (試行 {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                    else:
                        print(f"[アイデア融合] リトライ上限に達しました（{max_retries}回試行）")
                except Exception as e:
                    print(f"[アイデア融合] 予期せぬエラー ({model_name}): {e}")
                    last_exception = e
                    break
            
            if last_exception:
                print(f"[アイデア融合] モデル {model_name} での生成に失敗しました: {last_exception}")
            return None

        # アイデア情報を整形
        ideas_text = ""
        for i, idea in enumerate(idea_list, 1):
            ideas_text += f"""
【アイデア{i}】
タイトル: {idea.get('title', '')}
詳細: {idea.get('detail', '')}
category: {idea.get('category', '')}
"""
        
        # 融合用のプロンプト
        prompt = f"""あなたは以下の【役割】に沿って
{len(idea_list)}つのアイデアを融合させ、新しいアイデアを提案します。

【モード】
{mode_info['description']}

【ペルソナ】
考え方：{persona_info['thinking']}
話し方：{persona_info['tone']}

【元のアイデア】
{ideas_text}

【出力要件】

1. タイトル（20文字以内）

2. 詳細説明（500文字以内）
   - ターゲットユーザー
   - 解決する課題
   - 主要機能
3. カテゴリ（1つ）

【注意】
- 元アイデアの要素を羅列しない。
- 自然な日本語で、ビジネス提案として一貫性ある形にまとめる
- ペルソナの「考え方」と「話し方」を反映して記述

【出力形式】
JSON形式で回答してください：
{{
  "title": "融合されたアイデアのタイトル（20文字以内）",
  "detail": "融合されたアイデアの詳細説明（500文字以内、上記の要件をすべて含む）",
  "category": "以下のカテゴリから最も適切なものを1つ選択"
}}

選択可能なカテゴリ:
{', '.join(available_categories)}

該当するカテゴリがない場合は "その他" を選択してください。"""
        
        print(f"[アイデア融合] {len(idea_list)}つのアイデアを融合しています...")
        
        # まず gemini-2.5-flash-lite で試行
        response = _generate_with_retry('gemini-2.5-flash-lite', prompt, max_retries=3, initial_delay=4)
        
        # 失敗した場合は gemini-2.5-flash でフォールバック
        if response is None:
            print("[アイデア融合] gemini-2.5-flash-lite が利用できません。gemini-2.5-flash にフォールバックします...")
            response = _generate_with_retry('gemini-2.5-flash', prompt, max_retries=3, initial_delay=4)

        if response is None:
            raise Exception("すべてのモデルでの生成に失敗しました")
        
        response_text = response.text.strip()
        print(f"[アイデア融合] AIからのレスポンス: {response_text[:200]}...")
        
        # JSON部分を抽出
        json_match = re.search(r'\{[^}]+\}', response_text, re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group())
                fused_title = result.get('title', '').strip()
                fused_detail = result.get('detail', '').strip()
                fused_category = result.get('category', '').strip()
                
                # カテゴリの検証
                if fused_category not in available_categories:
                    print(f"[アイデア融合] 無効なカテゴリが提案されました: {fused_category}")
                    fused_category = "その他"
                
                # タイトルと詳細の長さチェック
                if len(fused_title) > 20:
                    fused_title = fused_title[:20]
                if len(fused_detail) > 1000:
                    fused_detail = fused_detail[:1000]
                
                print(f"[アイデア融合] 融合成功:")
                print(f"  タイトル: {fused_title}")
                print(f"  カテゴリ: {fused_category}")
                
                return {
                    "title": fused_title,
                    "detail": fused_detail,
                    "category": fused_category
                }
            except json.JSONDecodeError as e:
                print(f"[アイデア融合] JSONパースエラー: {e}")
                print(f"[アイデア融合] 元のレスポンス: {response_text}")
                return {}
        else:
            print(f"[アイデア融合] JSONが見つかりません。レスポンス全文: {response_text}")
            return {}
            
    except Exception as e:
        print(f"[アイデア融合] エラー: {str(e)}")
        logger.error(f"Idea fusion error: {e}", exc_info=True)
        return {}
