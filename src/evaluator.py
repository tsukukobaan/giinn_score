"""
Claude API 評価エンジン

各QAPairに対して質問品質・答弁品質・議題関連性を評価し、
TF-IDFベースの重複検出を行う。
"""

import json
import logging
import math
import time
from collections import Counter
from pathlib import Path
from typing import Optional

import anthropic

from models import (
    QAPair, QuestionScores, AnswerScores, Highlight,
    SessionBlock, SessionScores,
)

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-20250514"
MAX_RETRIES = 5

# ============================================================
# 個別QAペア評価プロンプト
# ============================================================

SYSTEM_PROMPT = """\
あなたは国会審議の品質を評価する専門家です。Discourse Quality Index (DQI) に基づき、\
質疑応答ペアを以下の観点で評価してください。

## 質問品質（各 0-100）

### justification（論拠の深さ）
主張に理由・根拠・論理構造があるか。
- 20: 理由なしの主張や感情的訴えのみ
- 50: 理由はあるが根拠が薄い、または論理の飛躍がある
- 80: 複数の根拠を示し、論理的に組み立てられている

### evidence（エビデンス品質）
具体的な数値・法令・事例・専門家の見解を引用しているか。
- 20: 「国民は怒っている」等の裸の主張のみ
- 50: 一般的な事実に言及するが具体的数値や出典なし
- 80: 具体的数値・法令・報告書等を複数引用

### constructiveness（建設性）
代替案・改善提案を含むか。
- 20: 批判のみで代替案なし
- 50: 方向性は示すが具体的な代替案はない
- 80: 具体的な代替政策や改善提案を提示

### novelty（新規性）
既出でない独自の切り口・視点があるか。
- 20: 他の議員と同じ質問の繰り返し
- 50: 一般的なテーマだが独自の角度がある
- 80: 新しい論点や独自の分析を提示

### public_interest（公益志向）
広く国民の利益に関わるか、狭い党利・選挙区利益か。
- 20: 党派的攻撃やスキャンダル追及に終始
- 50: 政策に関するが特定の利益層寄り
- 80: 広く国民生活に関わる政策課題を扱っている

## 答弁品質（各 0-100）

### responsiveness（応答性）
質問の具体的な要求に正面から答えているか。
- 20: 質問を無視し一般論や定型句で回避
- 50: 部分的に答えているが核心を避けている
- 80: 質問の各要求に具体的に回答

### evidence（エビデンス品質）
数値・根拠・具体例を示しているか。
- 20: 「検討します」「適切に対応」等の定型句のみ
- 50: 方向性は示すが数値的根拠なし
- 80: 具体的数値・予算額・実施時期等を提示

### logical_coherence（論理的一貫性）
論理的に一貫し、矛盾がないか。
- 20: 論点のすり替えや矛盾する発言
- 50: 概ね一貫しているが論理の弱い部分がある
- 80: 一貫した論理で明快に説明

### engagement（対話姿勢）
質問の論点を認め、実質的に向き合っているか。
- 20: 質問を軽視・無視し、自分の主張のみ展開
- 50: 形式的には応じるが実質的な対話に至らない
- 80: 質問の問題意識を共有し、建設的に応答

## 議題関連性（0-100）
topic_relevance: 当該会議の議題に直結しているか（0=無関係、100=直結）

## テキストハイライト
各評価軸について、発言テキスト中の具体的な箇所を引用し、\
なぜ高評価/低評価なのかを示してください。

必ず以下のJSON形式のみで回答してください。"""

USER_PROMPT_TEMPLATE = """\
## 会議情報
- 院: {house}
- 委員会: {committee}
- 日付: {date}
- 議題: {issue}

## 質問者: {questioner}（{questioner_group}）
{question_text}

## 答弁者: {answerer}（{answerer_position}）
{answer_text}

上記の質疑応答を評価し、以下のJSON形式で回答してください:
{{
  "question_scores": {{
    "justification": <0-100>,
    "evidence": <0-100>,
    "constructiveness": <0-100>,
    "novelty": <0-100>,
    "public_interest": <0-100>,
    "rationale": "<2-3文の評価理由>",
    "highlights": [
      {{"text": "<発言から引用>", "dimension": "<軸名>", "sentiment": "positive|negative", "comment": "<理由>"}}
    ]
  }},
  "answer_scores": {{
    "responsiveness": <0-100>,
    "evidence": <0-100>,
    "logical_coherence": <0-100>,
    "engagement": <0-100>,
    "rationale": "<2-3文の評価理由>",
    "highlights": [
      {{"text": "<発言から引用>", "dimension": "<軸名>", "sentiment": "positive|negative", "comment": "<理由>"}}
    ]
  }},
  "topic_relevance": <0-100>
}}"""

# ============================================================
# セッション（質疑ブロック）評価プロンプト
# ============================================================

SESSION_SYSTEM_PROMPT = """\
あなたは国会審議の品質を評価する専門家です。
1人の議員に割り当てられた質疑時間全体を通してのパフォーマンスを評価してください。
個別のやり取りではなく、質疑全体を通しての戦略性・生産性を評価します。

## 評価軸（各 0-100）

### argument_structure（論点構成力）
質疑全体を通して論点が一貫し、論理的に積み上げられているか。
- 20: 脈絡なく論点が飛び、散漫
- 50: ある程度の流れはあるが構成に弱さ
- 80: 明確な戦略に基づき論点を段階的に構築

### followup_quality（掘り下げ力）
答弁を受けて深掘りできているか、はぐらかしに切り返せているか。
- 20: 答弁内容に関係なく次の質問へ移る
- 50: 部分的に掘り下げるが追及が甘い
- 80: 不十分な答弁に的確に切り返し、具体的回答を引き出す

### time_efficiency（時間効率）
割り当て時間を重要論点に集中させているか。
- 20: 枝葉末節や重複質問で時間を浪費
- 50: 概ね効率的だが冗長な部分がある
- 80: 限られた時間を最大限活用し重要論点に集中

### elicitation（引き出し力）
新しい情報・約束・具体的回答を答弁者から引き出せたか。
- 20: 定型句の答弁しか得られず実質的成果なし
- 50: 一部新しい情報を引き出したが限定的
- 80: 具体的数値・期限・方針など重要な回答を引き出した

### overall_impact（全体的インパクト）
この質疑が審議の質向上・国民の理解促進にどの程度貢献したか。
- 20: 審議への実質的貢献が乏しい
- 50: 一定の貢献はあるが印象は薄い
- 80: 重要な論点を明らかにし、審議を大きく前進させた

必ず以下のJSON形式のみで回答してください。"""

SESSION_USER_TEMPLATE = """\
## 会議情報
- 院: {house}
- 委員会: {committee}
- 日付: {date}
- 議題: {issue}

## 質問者: {questioner}（{questioner_group}）

以下はこの議員の質疑時間内の全やり取りです:

{qa_text}

上記の質疑全体を評価し、以下のJSON形式で回答してください:
{{
  "argument_structure": <0-100>,
  "followup_quality": <0-100>,
  "time_efficiency": <0-100>,
  "elicitation": <0-100>,
  "overall_impact": <0-100>,
  "rationale": "<3-4文の全体評価>"
}}"""


class QAPairEvaluator:
    """Claude APIを使って質疑応答ペアを評価"""

    def __init__(
        self,
        client: Optional[anthropic.Anthropic] = None,
        cache_dir: str = "./data/cache/eval",
    ):
        self.client = client or anthropic.Anthropic()
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, speech_id: str) -> Path:
        safe_id = speech_id.replace("/", "_")
        return self.cache_dir / f"{safe_id}.json"

    def _load_cache(self, qa_pair: QAPair) -> bool:
        """キャッシュから評価結果を読み込む。成功時True"""
        cache_file = self._cache_path(qa_pair.question.speech_id)
        if not cache_file.exists():
            return False
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            scores = self._parse_cached(data)
            if scores:
                qa_pair.question_scores = scores["question_scores"]
                qa_pair.answer_scores = scores["answer_scores"]
                qa_pair.topic_relevance = scores["topic_relevance"]
                logger.info("Cache hit: %s", qa_pair.question.speech_id)
                return True
        except (json.JSONDecodeError, KeyError):
            pass
        return False

    def _save_cache(self, qa_pair: QAPair) -> None:
        cache_file = self._cache_path(qa_pair.question.speech_id)
        data = {
            "question_scores": {
                "justification": qa_pair.question_scores.justification,
                "evidence": qa_pair.question_scores.evidence,
                "constructiveness": qa_pair.question_scores.constructiveness,
                "novelty": qa_pair.question_scores.novelty,
                "public_interest": qa_pair.question_scores.public_interest,
                "rationale": qa_pair.question_scores.rationale,
                "highlights": [
                    {"text": h.text, "dimension": h.dimension,
                     "sentiment": h.sentiment, "comment": h.comment}
                    for h in qa_pair.question_scores.highlights
                ],
            },
            "answer_scores": {
                "responsiveness": qa_pair.answer_scores.responsiveness,
                "evidence": qa_pair.answer_scores.evidence,
                "logical_coherence": qa_pair.answer_scores.logical_coherence,
                "engagement": qa_pair.answer_scores.engagement,
                "rationale": qa_pair.answer_scores.rationale,
                "highlights": [
                    {"text": h.text, "dimension": h.dimension,
                     "sentiment": h.sentiment, "comment": h.comment}
                    for h in qa_pair.answer_scores.highlights
                ],
            },
            "topic_relevance": qa_pair.topic_relevance,
        }
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _parse_highlights(raw: list) -> list[Highlight]:
        return [
            Highlight(
                text=h.get("text", ""),
                dimension=h.get("dimension", ""),
                sentiment=h.get("sentiment", "positive"),
                comment=h.get("comment", ""),
            )
            for h in (raw or []) if isinstance(h, dict)
        ]

    def _parse_cached(self, data: dict) -> Optional[dict]:
        """キャッシュJSONをスコアオブジェクトに変換（新旧両形式対応）"""
        try:
            qs = data["question_scores"]
            ans = data["answer_scores"]
            return {
                "question_scores": QuestionScores(
                    justification=float(qs.get("justification", 0)),
                    evidence=float(qs.get("evidence", 0)),
                    constructiveness=float(qs.get("constructiveness", 0)),
                    novelty=float(qs.get("novelty", 0)),
                    public_interest=float(qs.get("public_interest", 0)),
                    rationale=str(qs.get("rationale", "")),
                    highlights=self._parse_highlights(qs.get("highlights")),
                    # 旧形式互換
                    substantiveness=float(qs.get("substantiveness", 0)),
                    specificity=float(qs.get("specificity", 0)),
                ),
                "answer_scores": AnswerScores(
                    responsiveness=float(ans.get("responsiveness", 0)),
                    evidence=float(ans.get("evidence", 0)),
                    logical_coherence=float(ans.get("logical_coherence", 0)),
                    engagement=float(ans.get("engagement", 0)),
                    rationale=str(ans.get("rationale", "")),
                    highlights=self._parse_highlights(ans.get("highlights")),
                    # 旧形式互換
                    directness=float(ans.get("directness", 0)),
                    specificity=float(ans.get("specificity", 0)),
                    evasiveness=float(ans.get("evasiveness", 0)),
                ),
                "topic_relevance": float(data["topic_relevance"]),
            }
        except (KeyError, TypeError, ValueError):
            return None

    def evaluate(self, qa_pair: QAPair) -> QAPair:
        """1つのQAPairを評価してスコアを付与"""
        if self._load_cache(qa_pair):
            return qa_pair

        user_prompt = USER_PROMPT_TEMPLATE.format(
            house=qa_pair.meeting.name_of_house,
            committee=qa_pair.meeting.name_of_meeting,
            date=qa_pair.meeting.date,
            issue=qa_pair.meeting.issue,
            questioner=qa_pair.question.speaker,
            questioner_group=qa_pair.question.speaker_group,
            question_text=qa_pair.question.speech_text[:5000],
            answerer=qa_pair.answer.speaker,
            answerer_position=qa_pair.answer.speaker_position,
            answer_text=qa_pair.answer.speech_text[:5000],
        )

        response_text = self._call_api(user_prompt)
        scores = self._parse_response(response_text)

        if scores:
            qa_pair.question_scores = scores["question_scores"]
            qa_pair.answer_scores = scores["answer_scores"]
            qa_pair.topic_relevance = scores["topic_relevance"]
            # スコアが全0でなければキャッシュ（API失敗時の空データを防ぐ）
            if qa_pair.question_scores.average > 0 or qa_pair.answer_scores.average > 0:
                self._save_cache(qa_pair)
            logger.info(
                "評価完了: %s → Q:%.1f A:%.1f R:%.0f",
                qa_pair.question.speaker,
                qa_pair.question_scores.average,
                qa_pair.answer_scores.average,
                qa_pair.topic_relevance,
            )
        else:
            logger.warning("評価失敗: %s", qa_pair.question.speaker)

        return qa_pair

    def evaluate_batch(self, pairs: list[QAPair]) -> list[QAPair]:
        """複数のQAPairを順次評価"""
        for i, pair in enumerate(pairs, 1):
            logger.info("評価中 [%d/%d]: %s", i, len(pairs), pair.question.speaker)
            self.evaluate(pair)
        return pairs

    def evaluate_session(self, session_block: SessionBlock) -> SessionBlock:
        """1つの質疑ブロック全体を評価"""
        if not session_block.qa_pairs:
            return session_block

        meeting = session_block.qa_pairs[0].meeting

        # 全QAペアのテキストを連結
        qa_text_parts = []
        for i, p in enumerate(session_block.qa_pairs, 1):
            qa_text_parts.append(
                f"### やり取り {i}\n"
                f"【質問】{p.question.speaker}: {p.question.speech_text[:2000]}\n"
                f"【答弁】{p.answer.speaker}（{p.answer.speaker_position or ''}）: "
                f"{p.answer.speech_text[:2000]}\n"
            )
        qa_text = "\n".join(qa_text_parts)

        user_prompt = SESSION_USER_TEMPLATE.format(
            house=meeting.name_of_house,
            committee=meeting.name_of_meeting,
            date=meeting.date,
            issue=meeting.issue,
            questioner=session_block.questioner,
            questioner_group=session_block.questioner_group,
            qa_text=qa_text,
        )

        response_text = self._call_api(
            user_prompt, system_prompt=SESSION_SYSTEM_PROMPT,
        )
        scores = self._parse_session_response(response_text)
        if scores:
            session_block.session_scores = scores
            logger.info(
                "セッション評価完了: %s → %.1f",
                session_block.questioner,
                session_block.session_scores.average,
            )
        else:
            logger.warning("セッション評価失敗: %s", session_block.questioner)

        return session_block

    def _parse_session_response(self, text: str) -> Optional[SessionScores]:
        """セッション評価レスポンスをパース"""
        if not text:
            return None
        try:
            json_text = text
            if "```" in text:
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    json_text = text[start:end]

            data = json.loads(json_text)
            return SessionScores(
                argument_structure=float(data.get("argument_structure", 0)),
                followup_quality=float(data.get("followup_quality", 0)),
                time_efficiency=float(data.get("time_efficiency", 0)),
                elicitation=float(data.get("elicitation", 0)),
                overall_impact=float(data.get("overall_impact", 0)),
                rationale=str(data.get("rationale", "")),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.error("Session parse error: %s", e)
            return None

    def _call_api(self, user_prompt: str,
                  system_prompt: str = SYSTEM_PROMPT,
                  max_tokens: int = 2048) -> str:
        """Claude APIを呼び出し、レスポンステキストを返す"""
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.messages.create(
                    model=MODEL,
                    max_tokens=max_tokens,
                    temperature=0,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                return response.content[0].text
            except anthropic.RateLimitError:
                wait = 2 ** attempt
                logger.warning("Rate limit hit, retrying in %ds...", wait)
                time.sleep(wait)
            except anthropic.APIError as e:
                logger.error("API error: %s", e)
                if attempt == MAX_RETRIES - 1:
                    return ""
                time.sleep(2 ** attempt)
        return ""

    def _parse_response(self, text: str) -> Optional[dict]:
        """APIレスポンスからスコアをパース"""
        if not text:
            return None

        try:
            # JSON部分を抽出（```json ... ``` で囲まれている場合にも対応）
            json_text = text
            if "```" in text:
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    json_text = text[start:end]

            data = json.loads(json_text)

            qs = data["question_scores"]
            ans = data["answer_scores"]

            return {
                "question_scores": QuestionScores(
                    justification=float(qs.get("justification", 0)),
                    evidence=float(qs.get("evidence", 0)),
                    constructiveness=float(qs.get("constructiveness", 0)),
                    novelty=float(qs.get("novelty", 0)),
                    public_interest=float(qs.get("public_interest", 0)),
                    rationale=str(qs.get("rationale", "")),
                    highlights=self._parse_highlights(qs.get("highlights")),
                ),
                "answer_scores": AnswerScores(
                    responsiveness=float(ans.get("responsiveness", 0)),
                    evidence=float(ans.get("evidence", 0)),
                    logical_coherence=float(ans.get("logical_coherence", 0)),
                    engagement=float(ans.get("engagement", 0)),
                    rationale=str(ans.get("rationale", "")),
                    highlights=self._parse_highlights(ans.get("highlights")),
                ),
                "topic_relevance": float(data["topic_relevance"]),
            }
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.error("JSON parse error: %s\nResponse: %s", e, text[:500])
            return None


# ============================================================
# TF-IDF 重複検出
# ============================================================

def _tokenize(text: str) -> list[str]:
    """簡易トークナイズ（日本語対応: 2-gram + 助詞除去）"""
    # 句読点・記号を除去
    cleaned = ""
    for ch in text:
        if ch.isalnum() or ch in "ぁ-んァ-ヶ亜-龥":
            cleaned += ch
        else:
            cleaned += " "

    # 2-gramで分割（日本語の簡易トークナイズ）
    tokens = []
    words = cleaned.split()
    for w in words:
        if len(w) <= 2:
            tokens.append(w)
        else:
            for i in range(len(w) - 1):
                tokens.append(w[i:i + 2])
    return tokens


def _compute_tfidf(documents: list[list[str]]) -> list[dict[str, float]]:
    """TF-IDF ベクトルを計算"""
    n_docs = len(documents)
    if n_docs == 0:
        return []

    # DF (document frequency)
    df: Counter = Counter()
    for doc in documents:
        unique_tokens = set(doc)
        for token in unique_tokens:
            df[token] += 1

    # TF-IDF
    tfidf_vectors = []
    for doc in documents:
        tf: Counter = Counter(doc)
        total = len(doc) if doc else 1
        vec = {}
        for token, count in tf.items():
            tf_val = count / total
            idf_val = math.log((n_docs + 1) / (df[token] + 1)) + 1
            vec[token] = tf_val * idf_val
        tfidf_vectors.append(vec)

    return tfidf_vectors


def _cosine_similarity(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    """2つのスパースベクトル間のコサイン類似度"""
    common_keys = set(vec_a.keys()) & set(vec_b.keys())
    if not common_keys:
        return 0.0

    dot = sum(vec_a[k] * vec_b[k] for k in common_keys)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))

    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def detect_duplicates(
    pairs: list[QAPair],
    threshold: float = 0.7,
) -> list[QAPair]:
    """TF-IDFコサイン類似度で重複質問を検出"""
    if len(pairs) <= 1:
        return pairs

    texts = [p.question.speech_text for p in pairs]
    tokenized = [_tokenize(t) for t in texts]
    vectors = _compute_tfidf(tokenized)

    for i in range(len(pairs)):
        for j in range(i):
            sim = _cosine_similarity(vectors[i], vectors[j])
            if sim >= threshold:
                pairs[i].is_duplicate = True
                pairs[i].duplicate_similarity = round(sim, 3)
                pairs[i].duplicate_of_speech_id = pairs[j].question.speech_id
                logger.info(
                    "重複検出: '%s' ≈ '%s' (%.3f)",
                    pairs[i].question.speaker,
                    pairs[j].question.speaker,
                    sim,
                )
                break  # 最初に見つかった重複のみ記録

    n_dup = sum(1 for p in pairs if p.is_duplicate)
    logger.info("重複検出完了: %d/%d件が重複", n_dup, len(pairs))
    return pairs


# ============================================================
# スタンドアロン
# ============================================================

if __name__ == "__main__":
    import sys
    from pathlib import Path
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    sys.path.insert(0, str(Path(__file__).parent.parent / "tests"))
    from mock_data import generate_mock_meeting
    from qa_extractor import QAPairExtractor

    meeting = generate_mock_meeting()
    extractor = QAPairExtractor()
    pairs = extractor.extract(meeting)

    # 重複検出（APIなし）
    detect_duplicates(pairs, threshold=0.5)
    for p in pairs:
        dup = " [重複]" if p.is_duplicate else ""
        print(f"  {p.question.speaker}: {p.question.speech_text[:40]}...{dup}")

    # Claude API評価（ANTHROPIC_API_KEY が必要）
    print("\n--- Claude API 評価 ---")
    try:
        evaluator = QAPairEvaluator()
        evaluated = evaluator.evaluate(pairs[0])
        print(f"Q品質: {evaluated.question_scores.average}")
        print(f"A品質: {evaluated.answer_scores.average}")
        print(f"議題関連: {evaluated.topic_relevance}")
    except Exception as e:
        print(f"API評価スキップ: {e}")
