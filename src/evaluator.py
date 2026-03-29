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

from models import QAPair, QuestionScores, AnswerScores

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-20250514"
MAX_RETRIES = 5

SYSTEM_PROMPT = """\
あなたは国会審議の品質を評価する専門家です。
質疑応答ペアを分析し、以下の観点でスコアリングしてください。

## 質問品質（各 0-100）
- substantiveness（本質性）: 審議対象の核心に関わる質問か
- specificity（具体性）: データ・事実・数値に基づく質問か
- constructiveness（建設性）: 代替案・改善提案を含むか
- novelty（新規性）: 既出でない独自の切り口か

## 答弁品質（各 0-100）
- directness（直接性）: 質問に正面から答えているか
- specificity（具体性）: 数値・根拠・具体例を示しているか
- logical_coherence（論理性）: 論理的に一貫しているか
- evasiveness（回避度）: 論点すり替え・はぐらかしの度合い（0=回避なし、100=完全に回避）

## 議題関連性（0-100）
- topic_relevance: 当該会議の議題に直結しているか（0=無関係、100=直結）

必ず以下のJSON形式のみで回答してください。他のテキストは不要です。
"""

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
    "substantiveness": <0-100>,
    "specificity": <0-100>,
    "constructiveness": <0-100>,
    "novelty": <0-100>,
    "rationale": "<1行の評価理由>"
  }},
  "answer_scores": {{
    "directness": <0-100>,
    "specificity": <0-100>,
    "logical_coherence": <0-100>,
    "evasiveness": <0-100>,
    "rationale": "<1行の評価理由>"
  }},
  "topic_relevance": <0-100>
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
                "substantiveness": qa_pair.question_scores.substantiveness,
                "specificity": qa_pair.question_scores.specificity,
                "constructiveness": qa_pair.question_scores.constructiveness,
                "novelty": qa_pair.question_scores.novelty,
                "rationale": qa_pair.question_scores.rationale,
            },
            "answer_scores": {
                "directness": qa_pair.answer_scores.directness,
                "specificity": qa_pair.answer_scores.specificity,
                "logical_coherence": qa_pair.answer_scores.logical_coherence,
                "evasiveness": qa_pair.answer_scores.evasiveness,
                "rationale": qa_pair.answer_scores.rationale,
            },
            "topic_relevance": qa_pair.topic_relevance,
        }
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _parse_cached(self, data: dict) -> Optional[dict]:
        """キャッシュJSONをスコアオブジェクトに変換"""
        try:
            qs = data["question_scores"]
            ans = data["answer_scores"]
            return {
                "question_scores": QuestionScores(
                    substantiveness=float(qs["substantiveness"]),
                    specificity=float(qs["specificity"]),
                    constructiveness=float(qs["constructiveness"]),
                    novelty=float(qs["novelty"]),
                    rationale=str(qs.get("rationale", "")),
                ),
                "answer_scores": AnswerScores(
                    directness=float(ans["directness"]),
                    specificity=float(ans["specificity"]),
                    logical_coherence=float(ans["logical_coherence"]),
                    evasiveness=float(ans["evasiveness"]),
                    rationale=str(ans.get("rationale", "")),
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
            question_text=qa_pair.question.speech_text[:3000],
            answerer=qa_pair.answer.speaker,
            answerer_position=qa_pair.answer.speaker_position,
            answer_text=qa_pair.answer.speech_text[:3000],
        )

        response_text = self._call_api(user_prompt)
        scores = self._parse_response(response_text)

        if scores:
            qa_pair.question_scores = scores["question_scores"]
            qa_pair.answer_scores = scores["answer_scores"]
            qa_pair.topic_relevance = scores["topic_relevance"]
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

    def _call_api(self, user_prompt: str) -> str:
        """Claude APIを呼び出し、レスポンステキストを返す"""
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.messages.create(
                    model=MODEL,
                    max_tokens=1024,
                    temperature=0,
                    system=SYSTEM_PROMPT,
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
                    substantiveness=float(qs["substantiveness"]),
                    specificity=float(qs["specificity"]),
                    constructiveness=float(qs["constructiveness"]),
                    novelty=float(qs["novelty"]),
                    rationale=str(qs.get("rationale", "")),
                ),
                "answer_scores": AnswerScores(
                    directness=float(ans["directness"]),
                    specificity=float(ans["specificity"]),
                    logical_coherence=float(ans["logical_coherence"]),
                    evasiveness=float(ans["evasiveness"]),
                    rationale=str(ans.get("rationale", "")),
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
