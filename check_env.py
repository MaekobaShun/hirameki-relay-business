#!/usr/bin/env python3
"""
環境変数とGemini APIの接続をチェックするスクリプト
Renderデプロイ後に実行して確認できます
"""
import os
import sys

def check_environment():
    print("=" * 60)
    print("環境変数チェック")
    print("=" * 60)
    
    # 環境変数のチェック
    gemini_key = os.environ.get('GEMINI_API_KEY')
    enable_moderation = os.environ.get('ENABLE_CONTENT_MODERATION', 'true')
    
    print(f"GEMINI_API_KEY: {'設定済み' if gemini_key else '未設定 ❌'}")
    if gemini_key:
        print(f"  -> キーの長さ: {len(gemini_key)} 文字")
        print(f"  -> 先頭: {gemini_key[:10]}...")
    print(f"ENABLE_CONTENT_MODERATION: {enable_moderation}")
    
    if not gemini_key:
        print("\n⚠️ GEMINI_API_KEY が設定されていません！")
        print("Renderの環境変数に追加してください。")
        return False
    
    # Gemini APIの接続テスト
    print("\n" + "=" * 60)
    print("Gemini API 接続テスト")
    print("=" * 60)
    
    try:
        import google.generativeai as genai
        print("✅ google.generativeai のインポート成功")
        
        genai.configure(api_key=gemini_key)
        print("✅ APIキーの設定成功")
        
        # モデルリストの取得テスト
        models = genai.list_models()
        print(f"✅ 利用可能なモデル数: {len(list(models))}")
        
        # 簡単なテストリクエスト
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content("こんにちは")
        print(f"✅ テストリクエスト成功: {response.text[:50]}...")
        
        print("\n🎉 すべてのチェックに成功しました！")
        return True
        
    except ImportError as e:
        print(f"❌ google.generativeai のインポート失敗: {e}")
        print("requirements.txt に google-generativeai が含まれているか確認してください。")
        return False
    except Exception as e:
        print(f"❌ API接続エラー: {e}")
        print(f"エラータイプ: {type(e).__name__}")
        return False

if __name__ == "__main__":
    success = check_environment()
    sys.exit(0 if success else 1)
