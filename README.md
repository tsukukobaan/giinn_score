# 国会審議スコアボード (kokkai-scorer)

国会の審議品質を AI で定量評価し、議員・政党ごとにスコアリングして可視化するシステム。

**運営**: NXJI x MorpheusAI
**プロダクト**: kokkai-score.jp

## 機能

- 国会会議録 API から議事録を自動取得・構造化
- Claude AI による質疑応答の品質評価（質問4軸 + 答弁4軸）
- TF-IDF ベースの重複質問検出
- 議員別・政党別・答弁者別のスコアカード集計
- OGP 画像自動生成（Playwright）
- X (Twitter) への自動投稿

## アーキテクチャ

```
fetch → extract → evaluate → aggregate → publish → archive
```

| ファイル | 役割 |
|---------|------|
| `src/kokkai_fetcher.py` | 国会会議録 API クライアント |
| `src/qa_extractor.py` | 質疑応答ペア抽出 |
| `src/master_manager.py` | 議員・答弁者マスタ管理 |
| `src/evaluator.py` | Claude API 評価 + 重複検出 |
| `src/scorer.py` | スコア集計・ランキング |
| `src/x_publisher.py` | OGP 画像生成 + X 投稿 |
| `src/daily_pipeline.py` | 日次バッチオーケストレーション |
| `src/models.py` | データモデル定義 |

## セットアップ

```bash
# Python 3.12+
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 依存パッケージ
pip install -e ".[dev]"

# Playwright (OGP画像生成用)
playwright install chromium
```

### 環境変数

`.env.example` をコピーして `.env` を作成:

```bash
cp .env.example .env
```

| 変数 | 説明 |
|------|------|
| `ANTHROPIC_API_KEY` | Claude API キー |
| `X_API_KEY` / `X_API_SECRET` | X API キー |
| `X_ACCESS_TOKEN` / `X_ACCESS_SECRET` | X アクセストークン |
| `X_BEARER_TOKEN` | X Bearer トークン |
| `CURRENT_SESSION` | 国会回次（例: `215`） |
| `TARGET_HOUSE` | 対象院（空=両院） |
| `TARGET_MEETING` | 対象委員会（例: `予算委員会`） |
| `REQUIRE_APPROVAL` | X投稿前に承認を求めるか（`true`/`false`） |

## 使い方

```bash
# dry-run（AI評価・X投稿をスキップ）
python -m src.daily_pipeline --dry-run --date 2026-03-10

# 本番実行
python -m src.daily_pipeline --session 215

# オプション
python -m src.daily_pipeline \
  --date 2026-03-10 \
  --session 215 \
  --house 参議院 \
  --meeting 予算委員会 \
  --dry-run
```

## テスト

```bash
pytest tests/ -v
```

51テスト（fetcher, extractor, evaluator, scorer, publisher, pipeline）

## 評価軸

### 質問品質（各 0-100）
- **本質性**: 審議対象の核心に関わるか
- **具体性**: データ・事実に基づくか
- **建設性**: 代替案・改善提案を含むか
- **新規性**: 既出でない独自の切り口か

### 答弁品質（各 0-100）
- **直接性**: 正面から答えているか
- **具体性**: 数値・根拠を示しているか
- **論理性**: 論理的に一貫しているか
- **回避度**: 論点すり替えの度合い（高い=悪い）

## 注意事項

- 国会会議録 API への過剰アクセス禁止（1.5秒インターバル厳守）
- 評価は AI による参考値であり、絶対的な品質指標ではありません
- 議事録の著作権は国立国会図書館のコンテンツ利用規約に準拠
- 特定の政党・議員を攻撃する目的ではなく「審議の質の可視化」が目的です

## ライセンス

TBD
