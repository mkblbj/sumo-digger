# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクト概要

SUUMOの物件URLから賃貸物件情報を抽出し、Excel/CSV/JSON形式でエクスポートするFlask Webアプリケーション。

## コマンド

```bash
# 仮想環境のアクティベート
source .venv/bin/activate  # macOS/Linux
.venv\Scripts\activate     # Windows

# 依存関係のインストール
pip install -r requirements.txt

# アプリケーション起動
python run.py
# ブラウザで http://localhost:5001 にアクセス
```

## ディレクトリ構造

```
Project_Suumo/
├── app/
│   ├── __init__.py              # アプリファクトリ
│   ├── routes.py                # APIルート定義
│   ├── models.py                # DBモデル (ScrapingTask, Property, Settings)
│   ├── schema/                  # 顧客フィールド仕様の統一スキーマ
│   │   ├── property_types.py    # PropertyType列挙（6種類）
│   │   ├── enums.py             # 列挙値定義 + JP→CN変換マップ
│   │   ├── field_definitions.py # タイプ別フィールド定義（セクション順）
│   │   └── mapper.py            # FieldMapper: JP→CNフィールド正規化
│   ├── scraper/
│   │   ├── suumo.py             # 賃貸物件スクレイパー
│   │   ├── buy_scraper.py       # 売買物件スクレイパー
│   │   ├── search_parser.py     # 検索結果パーサー
│   │   └── auth.py              # SUUMO認証（Selenium）
│   ├── services/
│   │   ├── llm_client.py        # LLMクライアント（Gemini/OpenAI）
│   │   ├── pdf_analyzer.py      # PDF図面分析（LLM Vision）
│   │   ├── ai_enrichment.py     # AI強化（為替、税金、説明文生成）
│   │   └── translator.py        # 日→中翻訳サービス
│   ├── exporters/
│   │   └── exporter.py          # Excel/CSV/JSON出力（スキーマ順序対応）
│   ├── templates/
│   └── static/css/
├── docs/                        # 顧客仕様書
├── config.py
├── run.py
└── requirements.txt
```

## 主要コンポーネント

| ファイル | 役割 |
|---------|------|
| `app/schema/` | 顧客フィールド仕様の真理源。6物件タイプのフィールド定義、JP→CN変換 |
| `app/schema/mapper.py` | スクレイパー/PDF出力を顧客仕様に正規化（FieldMapper） |
| `app/scraper/suumo.py` | BeautifulSoupでHTML解析、PropertyDataクラス |
| `app/scraper/buy_scraper.py` | 売買物件（マンション/一戸建て）スクレイパー |
| `app/services/ai_enrichment.py` | AI派生フィールド生成（為替換算、投資分析、説明文等） |
| `app/services/pdf_analyzer.py` | PDF図面のLLM Vision分析 |
| `app/exporters/exporter.py` | スキーマ順序対応のExcel/CSV/JSON出力 |
| `app/routes.py` | Flask API、SSE進捗表示、FieldMapper統合 |

## データフロー

```
SUUMO URL / PDF → Scraper/Analyzer → Raw JP Dict → FieldMapper.normalize() → Normalized CN Dict → DB
                                                                                        ↓
                                                            AI Enrichment (optional) → Enriched Dict → Export
```

## APIエンドポイント

- `GET /` - メインページ
- `POST /api/scrape` - スクレイピング開始
- `GET /api/scrape/stream/<task_id>` - 進捗ストリーム（SSE）
- `GET /api/download/<task_id>/<format>` - 結果ダウンロード
- `GET /api/properties/<task_id>/sectioned` - セクション分けされた物件データ
- `POST /api/enrich/<task_id>` - AI強化実行
- `POST /api/enrich/property/<id>` - 単一物件AI強化
- `POST /api/blueprint/upload` - PDF図面アップロード
- `POST /api/favorites` - お気に入りURL取得

## 注意事項

- サーバー負荷軽減のため、各URL処理間に2秒の待機時間あり
- SUUMOのHTML構造変更時は`suumo.py`のセレクタ更新が必要
- お気に入り取得にはChrome/ChromeDriverが必要
