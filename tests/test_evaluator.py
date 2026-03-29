"""
テスト: QAPairEvaluator + 重複検出
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from evaluator import (
    QAPairEvaluator, detect_duplicates,
    _tokenize, _compute_tfidf, _cosine_similarity,
)
from qa_extractor import QAPairExtractor
from mock_data import generate_mock_meeting


# テスト用のClaude APIレスポンス（新形式）
MOCK_API_RESPONSE = json.dumps({
    "question_scores": {
        "justification": 85,
        "evidence": 78,
        "constructiveness": 72,
        "novelty": 80,
        "public_interest": 75,
        "rationale": "エネルギー安全保障に関する具体的な数値を用いた質問",
        "highlights": [
            {"text": "石油備蓄の取り崩し計画", "dimension": "evidence",
             "sentiment": "positive", "comment": "具体的政策を指定"},
        ],
    },
    "answer_scores": {
        "responsiveness": 70,
        "evidence": 65,
        "logical_coherence": 75,
        "engagement": 68,
        "rationale": "概ね質問に答えているが一部曖昧な表現あり",
        "highlights": [
            {"text": "約百四十五日分", "dimension": "evidence",
             "sentiment": "positive", "comment": "具体的数値を提示"},
        ],
    },
    "topic_relevance": 88,
})


def _make_mock_client(response_text: str = MOCK_API_RESPONSE) -> MagicMock:
    """モックのAnthropicクライアントを作成"""
    client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=response_text)]
    client.messages.create.return_value = mock_response
    return client


def _make_evaluator(client, tmpdir=None):
    """tmpdir付きでevaluatorを作成"""
    if tmpdir is None:
        tmpdir = tempfile.mkdtemp()
    return QAPairEvaluator(client=client, cache_dir=tmpdir)


class TestEvaluate:
    """Claude API評価のテスト"""

    def test_basic_evaluation(self):
        """基本的な評価が正しくパースされるか"""
        meeting = generate_mock_meeting()
        pairs = QAPairExtractor().extract(meeting)

        client = _make_mock_client()
        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = _make_evaluator(client, tmpdir)
            result = evaluator.evaluate(pairs[0])

        assert result.question_scores.justification == 85
        assert result.question_scores.evidence == 78
        assert result.question_scores.constructiveness == 72
        assert result.question_scores.novelty == 80
        assert result.question_scores.public_interest == 75
        assert result.question_scores.average > 0
        assert len(result.question_scores.highlights) >= 1
        assert result.answer_scores.responsiveness == 70
        assert result.answer_scores.engagement == 68
        assert result.answer_scores.average > 0
        assert result.topic_relevance == 88

    def test_evaluate_batch(self):
        """バッチ評価"""
        meeting = generate_mock_meeting()
        pairs = QAPairExtractor().extract(meeting)[:3]

        client = _make_mock_client()
        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = _make_evaluator(client, tmpdir)
            results = evaluator.evaluate_batch(pairs)

        assert len(results) == 3
        assert client.messages.create.call_count == 3
        for p in results:
            assert p.question_scores.justification == 85

    def test_json_in_code_block(self):
        """```json ... ``` で囲まれたレスポンス"""
        wrapped = f"```json\n{MOCK_API_RESPONSE}\n```"
        client = _make_mock_client(wrapped)

        meeting = generate_mock_meeting()
        pairs = QAPairExtractor().extract(meeting)
        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = _make_evaluator(client, tmpdir)
            result = evaluator.evaluate(pairs[0])

        assert result.question_scores.justification == 85

    def test_malformed_json(self):
        """不正なJSONでもクラッシュしない"""
        client = _make_mock_client("this is not json")

        meeting = generate_mock_meeting()
        pairs = QAPairExtractor().extract(meeting)
        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = _make_evaluator(client, tmpdir)
            result = evaluator.evaluate(pairs[0])

        assert result.question_scores.substantiveness == 0.0
        assert result.topic_relevance == 0.0

    def test_empty_response(self):
        """空レスポンス"""
        client = _make_mock_client("")

        meeting = generate_mock_meeting()
        pairs = QAPairExtractor().extract(meeting)
        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = _make_evaluator(client, tmpdir)
            result = evaluator.evaluate(pairs[0])

        assert result.question_scores.substantiveness == 0.0

    def test_prompt_content(self):
        """プロンプトに正しいコンテキストが含まれるか"""
        meeting = generate_mock_meeting()
        pairs = QAPairExtractor().extract(meeting)

        client = _make_mock_client()
        with tempfile.TemporaryDirectory() as tmpdir:
            evaluator = _make_evaluator(client, tmpdir)
            evaluator.evaluate(pairs[0])

        call_kwargs = client.messages.create.call_args
        messages = call_kwargs.kwargs["messages"]
        user_msg = messages[0]["content"]

        assert "参議院" in user_msg
        assert "予算委員会" in user_msg
        assert "玉木雄一郎" in user_msg


class TestDuplicateDetection:
    """重複検出のテスト"""

    def test_tokenize(self):
        tokens = _tokenize("エネルギー政策について")
        assert len(tokens) > 0

    def test_cosine_identical(self):
        vec = {"a": 1.0, "b": 2.0}
        assert abs(_cosine_similarity(vec, vec) - 1.0) < 0.001

    def test_cosine_orthogonal(self):
        vec_a = {"a": 1.0}
        vec_b = {"b": 1.0}
        assert _cosine_similarity(vec_a, vec_b) == 0.0

    def test_detect_similar_questions(self):
        meeting = generate_mock_meeting()
        pairs = QAPairExtractor().extract(meeting)
        detect_duplicates(pairs, threshold=0.35)
        duplicates = [p for p in pairs if p.is_duplicate]
        assert len(duplicates) >= 1

    def test_no_duplicate_single_pair(self):
        meeting = generate_mock_meeting()
        pairs = QAPairExtractor().extract(meeting)[:1]
        detect_duplicates(pairs)
        assert not pairs[0].is_duplicate

    def test_high_threshold_no_duplicates(self):
        meeting = generate_mock_meeting()
        pairs = QAPairExtractor().extract(meeting)
        detect_duplicates(pairs, threshold=0.99)
        assert all(not p.is_duplicate for p in pairs)

    def test_tfidf_vectors(self):
        docs = [_tokenize("エネルギー政策"), _tokenize("防衛予算について")]
        vectors = _compute_tfidf(docs)
        assert len(vectors) == 2
        assert all(len(v) > 0 for v in vectors)


class TestEvaluationCache:
    """評価キャッシュのテスト"""

    def test_cache_miss_creates_file(self):
        """キャッシュミス時にキャッシュファイルが作成される"""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = _make_mock_client()
            evaluator = _make_evaluator(client, tmpdir)

            meeting = generate_mock_meeting()
            pairs = QAPairExtractor().extract(meeting)
            evaluator.evaluate(pairs[0])

            cache_file = Path(tmpdir) / f"{pairs[0].question.speech_id}.json"
            assert cache_file.exists()
            client.messages.create.assert_called_once()

    def test_cache_hit_skips_api(self):
        """キャッシュヒット時にAPIが呼ばれない"""
        with tempfile.TemporaryDirectory() as tmpdir:
            meeting = generate_mock_meeting()
            pairs = QAPairExtractor().extract(meeting)
            speech_id = pairs[0].question.speech_id

            cache_data = {
                "question_scores": {
                    "justification": 90, "evidence": 85,
                    "constructiveness": 80, "novelty": 75,
                    "public_interest": 70, "rationale": "cached",
                },
                "answer_scores": {
                    "responsiveness": 70, "evidence": 65,
                    "logical_coherence": 60, "engagement": 55, "rationale": "cached",
                },
                "topic_relevance": 92,
            }
            cache_file = Path(tmpdir) / f"{speech_id}.json"
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(cache_data, f)

            client = _make_mock_client()
            evaluator = _make_evaluator(client, tmpdir)
            result = evaluator.evaluate(pairs[0])

            client.messages.create.assert_not_called()
            assert result.question_scores.justification == 90
            assert result.topic_relevance == 92
