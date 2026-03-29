# CLAUDE.md — GiinScore（国会審議スコアボード）

## プロジェクト概要

国会の審議品質をAIで定量評価し、議員・政党ごとにスコアリングして可視化するシステム。
衆参両院・全委員会の議事録を取得・構造化し、質疑応答の品質をClaude APIで評価する。
日次で自動実行し、スコアカード画像を生成してXアカウントから自動投稿する。

**プロダクト名**: GiinScore

---

## ディレクトリ構成

```
kokkai-scorer/
├── CLAUDE.md                    ← このファイル（ClaudeCodeへの指示）
├── pyproject.toml               ← Pythonプロジェクト設定
├── .env.example                 ← 環境変数テンプレート
├── src/
│   ├── __init__.py
│   ├── kokkai_fetcher.py        ← 国会会議録APIクライアント
│   ├── qa_extractor.py          ← 質疑応答ペア抽出エンジン
│   ├── evaluator.py             ← Claude API評価エンジン
│   ├── master_manager.py        ← 議員・答弁者マスタ管理
│   ├── scorer.py                ← スコア集計・ランキング生成
│   ├── x_publisher.py           ← X自動投稿 + OGP画像生成
│   ├── daily_pipeline.py        ← 日次バッチ（全体オーケストレーション）
│   └── models.py                ← データモデル定義
├── data/
│   ├── cache/                   ← APIレスポンスキャッシュ
│   └── masters/
│       ├── members.json         ← 議員マスタ（選挙回次別）
│       └── respondents.json     ← 答弁者マスタ（動的登録）
├── tests/
│   ├── test_fetcher.py
│   ├── test_extractor.py
│   ├── test_evaluator.py
│   └── mock_data.py             ← テスト用モックデータ
└── frontend/                    ← 後続フェーズ（Next.js）
```

---

## 技術スタック

- **言語**: Python 3.12+
- **AI評価**: Anthropic Claude API (claude-sonnet-4-20250514)
- **DB**: Supabase (PostgreSQL) — 後続フェーズで導入、MVP期はJSONファイル
- **フロントエンド**: Next.js + Vercel (後続フェーズ)
- **OGP画像生成**: Playwright (ヘッドレスChrome) でHTMLをスクリーンショット
- **X投稿**: tweepy (Twitter/X API v2)
- **スケジューラ**: cron or GitHub Actions

---

## 実装順序（ClaudeCodeが従うべき順番）

### Phase 1: データ取得・構造化

1. **`src/models.py`** — 全データモデルを定義
   - `Speech`: 個別発言（speech_id, speaker, group, position, role, text, order）
   - `Meeting`: 会議（issue_id, session, house, meeting_name, issue, date, speeches[]）
   - `QAPair`: 質疑応答ペア（question: Speech, answer: Speech, meeting: Meeting, scores）
   - `Member`: 議員（name, yomi, elections: dict[int, MemberTerm]）
     - `MemberTerm`: 選挙回次ごとの情報（party, district, house, elected_date）
   - `Respondent`: 答弁者（name, position, ministry, first_seen_date, appearances: int）
   - `DailyResult`: 日次評価結果（date, meeting, qa_pairs[], member_scores, party_scores）

2. **`src/kokkai_fetcher.py`** — 国会会議録API クライアント
   - API仕様: https://kokkai.ndl.go.jp/api.html
   - エンドポイント:
     - `/api/meeting_list` — 会議一覧（発言本文なし）
     - `/api/meeting` — 会議単位出力（発言本文あり）← メインで使用
     - `/api/speech` — 発言単位出力
   - パラメータ: `recordPacking=json`, `maximumRecords=100`
   - ページネーション: `startRecord` + `nextRecordPosition` で全件取得
   - レート制限: リクエスト間1.5秒のインターバル（API側への配慮）
   - キャッシュ: `data/cache/` にJSONファイルで保存、2回目以降はキャッシュから読み込み
   - 注意: 検索結果の会議数が1000件超でエラー → sessionFrom/To で回次を絞る

3. **`src/qa_extractor.py`** — 質疑応答ペア抽出
   - 国会の質疑パターン:
     1. 委員長が質問者を指名
     2. 質問者（議員）が質問
     3. 委員長が答弁者を指名
     4. 答弁者（大臣・政府参考人等）が答弁
     5. 2-4の繰り返し
   - 委員長・議長の発言はスキップ（指名のみの発言）
   - 答弁者判定: speaker_position に「大臣」「政府参考人」「内閣官房長官」等を含む
   - 質問者判定: 答弁者でも委員長でもない → 質問者
   - 発言冒頭の発言者名除去（APIの仕様上「○氏名」が含まれる）

4. **`src/master_manager.py`** — マスタ管理
   - 議員マスタ: 選挙回次をキーとしてタイムライン管理
     - 初回は会議録APIの `speakerGroup` から所属会派を取得して自動登録
     - 選挙ごとに党籍・選挙区が変わりうるのでterm単位で保持
     - JSONファイル `data/masters/members.json` で永続化
   - 答弁者マスタ: 役人・参考人を動的に登録
     - 会議録に新しい答弁者（政府参考人等）が出現したら自動登録
     - position（肩書き）、所属省庁、初出日時、登場回数を記録
     - JSONファイル `data/masters/respondents.json` で永続化
   - メソッド:
     - `get_or_create_member(name, yomi, group, session)` → Member
     - `get_or_create_respondent(name, position)` → Respondent
     - `update_respondent_appearance(name, date)` → void
     - `save()` / `load()` — JSON永続化

### Phase 2: AI評価エンジン

5. **`src/evaluator.py`** — Claude APIで質疑を評価
   - 各QAPairに対して以下のスコアを算出（各0-100）:
     - **質問品質** (question_quality):
       - 本質性 (substantiveness): 審議対象の核心に関わるか
       - 具体性 (specificity): データ・事実に基づくか
       - 建設性 (constructiveness): 代替案・改善提案を含むか
       - 新規性 (novelty): 既出質問との重複がないか
     - **答弁品質** (answer_quality):
       - 直接性 (directness): 正面から答えているか
       - 具体性 (specificity): 数値・根拠を示しているか
       - 論理性 (logical_coherence): 論理的に一貫しているか
       - 回避度 (evasiveness): 論点すり替え・一般論での回避がないか（逆転スコア）
     - **議題関連性** (topic_relevance): 0=無関係 ～ 100=直結
   - 重複検出: embeddingベースのcosine類似度（会期内の既出質問と比較）
   - Claude APIコール:
     - model: claude-sonnet-4-20250514（コスト効率）
     - JSONモードで構造化出力
     - バッチ処理: 1会議の全QAPairを一括投入ではなく、1ペアずつ評価（品質担保）
   - エラーハンドリング: レート制限時のリトライ（exponential backoff）

6. **`src/scorer.py`** — スコア集計
   - 議員別スコアカード:
     - 当該日の全質問の平均スコア
     - 議題関連率（関連スコア50以上の質問の割合）
     - 重複率（重複フラグがついた質問の割合）
     - 答弁引出力（質の高い答弁を引き出せた率）
   - 政党別スコアカード:
     - 所属議員の加重平均（発言回数で重み付け）
     - 政策分野カバレッジ（異なる政策分野に質問が分散しているか）
   - 答弁者別スコアカード:
     - 平均答弁品質
     - 回避率（回避度が高い答弁の割合）
     - 具体性（数値・根拠を示した答弁の割合）

### Phase 3: 日次パイプライン + X投稿

7. **`src/x_publisher.py`** — X自動投稿 + OGP画像生成
   - OGP画像生成:
     - HTMLテンプレートでスコアカードを描画
     - Playwrightでスクリーンショット → PNG画像
     - 2種類: 議員個人スコアカード、政党スコアカード
   - X投稿 (tweepy):
     - 投稿パターン1: 「本日の〇〇委員会（衆院/参院）審議品質スコア」+ 政党カード + 録画リンク
     - 投稿パターン2: 「注目の質疑」+ 議員個人カード + 該当部分の録画タイムスタンプ
     - 録画リンク: 衆議院/参議院インターネット審議中継のURL
       - 衆議院: https://www.shugiintv.go.jp/
       - 参議院: https://www.webtv.sangiin.go.jp/
   - 投稿タイミング: 議事録公開後（通常は翌営業日）
   - ハッシュタグ: #国会審議スコアボード #予算委員会 etc.

8. **`src/daily_pipeline.py`** — 日次バッチオーケストレーション
   - 実行フロー:
     1. 国会会議録APIをポーリング → 新規議事録があるか確認
     2. あれば取得・パース
     3. 議員/答弁者マスタを更新（新規答弁者を自動登録）
     4. QAPair抽出
     5. Claude APIで評価
     6. スコア集計
     7. OGP画像生成
     8. X投稿
     9. 結果をDB（当面はJSON）に保存
   - cron設定: 毎日 18:00 JST に実行（議事録は通常午後に更新される）
   - ログ: 実行結果をログファイルに記録

---

## コーディング規約

- **型ヒント**: 全関数に型アノテーション必須
- **docstring**: Google style、日本語OK
- **ログ**: `logging` モジュールを使用、print禁止
- **エラー処理**: APIコール・ファイルI/Oは必ずtry-exceptで囲む
- **テスト**: pytest、モックデータは `tests/mock_data.py` に集約
- **環境変数**: `.env` ファイルから `python-dotenv` で読み込み
  - `ANTHROPIC_API_KEY`
  - `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`
  - `X_BEARER_TOKEN`

---

## データモデル詳細

### 議員マスタ (members.json)

```json
{
  "玉木雄一郎": {
    "name": "玉木雄一郎",
    "yomi": "たまきゆういちろう",
    "elections": {
      "49": {
        "party": "国民民主党",
        "district": "香川2区",
        "house": "衆議院",
        "elected_date": "2021-10-31",
        "status": "当選"
      },
      "50": {
        "party": "国民民主党",
        "district": "香川2区",
        "house": "衆議院",
        "elected_date": "2024-10-27",
        "status": "当選"
      }
    }
  }
}
```

選挙回次がキー。党籍変更・鞍替え（衆→参）を追跡可能。
初回登録は会議録APIの `speakerGroup` から自動生成。
手動で選挙区・当選回数を補完する運用。

### 答弁者マスタ (respondents.json)

```json
{
  "田中太郎": {
    "name": "田中太郎",
    "positions": [
      {
        "title": "政府参考人（財務省主計局長）",
        "first_seen": "2026-03-10",
        "last_seen": "2026-03-15",
        "appearances": 4
      }
    ]
  }
}
```

同一人物が肩書き変更する場合はpositionsに追加。
答弁者として初めて登場した際に自動登録される。

---

## 評価プロンプト設計方針

- 1 QAPairにつき1回のClaude APIコール
- systemプロンプトで「国会審議品質の専門評価者」としてのロールを定義
- 会議の基本情報（院名・委員会名・日付・議題）をコンテキストとして提供
- JSON出力を強制（`response_format` ではなくプロンプト内で指示 + パース）
- 評価の安定性のため `temperature=0` を設定
- 各スコアに加えて1行の評価理由 (rationale) も出力させる（透明性確保）

---

## X投稿テンプレート

### パターン1: 日次サマリー
```
📊 本日の参院予算委員会 審議品質スコア

🥇 国民民主党 78点
🥈 日本維新の会 72点
🥉 自由民主党 58点

議題関連率: 62% | 重複質問率: 23%

👉 詳細: https://kokkai-score.jp/2026-03-10
📺 録画: [審議中継リンク]

#国会審議スコアボード #予算委員会
```

### パターン2: 個人ハイライト
```
🏆 本日のベスト質疑

玉木雄一郎 議員（国民民主党）
総合スコア: 86点

✅ 本質性 88 | 具体性 84
✅ 議題関連率 92% | 重複率 4%

エネルギー安全保障予算について具体的数値を引き出す質疑

📺 該当部分の録画: [タイムスタンプ付きリンク]
```

---

## 注意事項

- 国会会議録APIへの過剰アクセス禁止（1.5秒間隔を厳守）
- 評価はあくまでAIによる参考値であることをUI・投稿で明示
- 特定の政党・議員を攻撃する目的ではなく「審議の質の可視化」が目的
- 議事録の著作権: 国立国会図書館のコンテンツ利用規約に準拠
- X投稿は事前に手動承認フローを入れる選択肢も検討（初期運用）

---

## 開発の進め方

ClaudeCodeでの作業時は以下の順序で進める:

1. まず `src/models.py` を完成させる（全データモデル）
2. `src/kokkai_fetcher.py` を実装 + `tests/test_fetcher.py` でモックテスト
3. `src/qa_extractor.py` を実装 + `tests/test_extractor.py` でテスト
4. `src/master_manager.py` を実装
5. `src/evaluator.py` を実装（Claude APIプロンプト含む）
6. `src/scorer.py` を実装
7. `src/x_publisher.py` を実装
8. `src/daily_pipeline.py` で全体を結合
9. テスト通過を確認

各ファイルは単体で動作確認できるように `if __name__ == "__main__"` ブロックを含める。
モックデータ（`tests/mock_data.py`）を使って、API接続なしでも全フローをテスト可能にする。
