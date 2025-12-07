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
├── app/                    # Flaskアプリケーション
│   ├── __init__.py         # アプリファクトリ
│   ├── routes.py           # APIルート定義
│   ├── scraper/
│   │   ├── suumo.py        # 物件情報スクレイパー
│   │   └── auth.py         # SUUMO認証（Selenium）
│   ├── exporters/
│   │   └── exporter.py     # Excel/CSV/JSON出力
│   ├── templates/          # HTMLテンプレート
│   └── static/css/         # スタイルシート
├── legacy/                 # 旧コード（Flet版、参照用）
├── config.py               # 設定ファイル
├── run.py                  # エントリーポイント
└── requirements.txt        # 依存関係
```

## 主要コンポーネント

| ファイル | 役割 |
|---------|------|
| `app/scraper/suumo.py` | BeautifulSoupでHTML解析、PropertyDataクラスでデータ管理 |
| `app/scraper/auth.py` | Seleniumでログイン、お気に入り物件URL取得 |
| `app/exporters/exporter.py` | pandas使用、3形式（Excel/CSV/JSON）出力 |
| `app/routes.py` | Flask API、SSEで進捗リアルタイム表示 |

## APIエンドポイント

- `GET /` - メインページ
- `POST /api/scrape` - スクレイピング開始
- `GET /api/scrape/stream/<task_id>` - 進捗ストリーム（SSE）
- `GET /api/download/<task_id>/<format>` - 結果ダウンロード
- `POST /api/favorites` - お気に入りURL取得

## 注意事項

- サーバー負荷軽減のため、各URL処理間に2秒の待機時間あり
- SUUMOのHTML構造変更時は`suumo.py`のセレクタ更新が必要
- お気に入り取得にはChrome/ChromeDriverが必要
