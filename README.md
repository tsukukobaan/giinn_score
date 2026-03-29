# 国会審議スコアボード (kokkai-scorer)

国会の審議品質を AI で定量評価し、議員・政党ごとにスコアリングして可視化するシステム。

**運営**: NXJI x MorpheusAI | **プロダクト**: kokkai-score.jp

## 仕組み

```
国会議事録API → QA抽出 → Claude AI評価 → スコア集計 → OGP画像 → X投稿
```

1. [国会会議録API](https://kokkai.ndl.go.jp/api.html) から議事録を取得・構造化
2. 質問者と答弁者のペアを自動抽出（委員長発言・手続き発言を除外）
3. **Claude API** で各QAペアの品質を8軸でスコアリング（0-100）
4. 議員別・政党別・答弁者別にスコアカードを集計
5. OGP画像を生成し、X (Twitter) に自動投稿

### 評価軸

| 質問品質 | 答弁品質 |
|---------|---------|
| 本質性 — 審議の核心に関わるか | 直接性 — 正面から答えているか |
| 具体性 — データ・事実に基づくか | 具体性 — 数値・根拠を示しているか |
| 建設性 — 代替案・改善提案を含むか | 論理性 — 論理的に一貫しているか |
| 新規性 — 既出でない独自の切り口か | 回避度 — 論点すり替えの度合い |

加えて **議題関連性**（0-100）と **TF-IDF 重複検出** で質問の重複率も算出。

## ファイル構成

```
src/
  models.py            データモデル
  kokkai_fetcher.py    国会議事録APIクライアント（キャッシュ・レート制限付き）
  qa_extractor.py      質疑応答ペア抽出
  master_manager.py    議員・答弁者マスタ管理（JSON永続化）
  evaluator.py         Claude API評価エンジン + 重複検出
  scorer.py            スコア集計・ランキング生成
  x_publisher.py       OGP画像生成（Playwright）+ X投稿（tweepy）
  daily_pipeline.py    日次バッチオーケストレーション
```

## セットアップ

```bash
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
playwright install chromium  # OGP画像生成用
cp .env.example .env         # APIキー等を設定
```

### 環境変数 (.env)

| 変数 | 用途 |
|------|------|
| `ANTHROPIC_API_KEY` | Claude API — QAペアの品質評価に使用 |
| `X_API_KEY` / `X_API_SECRET` | X API 認証 |
| `X_ACCESS_TOKEN` / `X_ACCESS_SECRET` | X アクセストークン |
| `X_BEARER_TOKEN` | X Bearer トークン |
| `CURRENT_SESSION` | 国会回次（例: `215`） |
| `TARGET_MEETING` | 対象委員会（デフォルト: `予算委員会`） |
| `REQUIRE_APPROVAL` | X投稿前にCLI承認を求めるか (`true`/`false`) |

## 使い方

```bash
# dry-run（議事録取得・QA抽出のみ、AI評価・X投稿スキップ）
python -m src.daily_pipeline --dry-run --date 2024-03-05 --session 213

# 本番実行
python -m src.daily_pipeline --session 215

# 全オプション
python -m src.daily_pipeline --date YYYY-MM-DD --session N --house 参議院 --meeting 予算委員会 --dry-run
```

## テスト

```bash
pytest tests/ -v   # 55テスト
ruff check src/ tests/
```

## 自動化

GitHub Actions で毎日 18:00 JST に自動実行（`.github/workflows/daily.yml`）。
手動トリガーも可能（Actions > Daily Pipeline > Run workflow）。

必要な GitHub Secrets: `ANTHROPIC_API_KEY`, `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`, `X_BEARER_TOKEN`

## 注意事項

- 国会会議録 API への過剰アクセス禁止（1.5秒インターバル厳守）
- 評価は AI による参考値であり、絶対的な品質指標ではありません
- 議事録の著作権は国立国会図書館のコンテンツ利用規約に準拠
- 特定の政党・議員を攻撃する目的ではなく「審議の質の可視化」が目的です
