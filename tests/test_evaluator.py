"""
テスト: QAPairEvaluator + 重複検出
"""

import json
from unittest.mock import MagicMock, patch

from evaluator import (
    QAPairEvaluator, detect_duplicates,
    _tokenize, _compute_tfidf, _cosine_similarity,
)
from qa_extractor import QAPairExtractor
from mock_data import generate_mock_meeting


# テスト用のClaude APIレスポンス
MOCK_API_RESPONSE = json.dumps({
    "question_scores": {
        "substantiveness": 85,
        "specificity": 78,
        "constructiveness": 72,
        "novelty": 80,
        "rationale": "エネルギー安全保障に関する具体的な数値を用いた質問",
    },
    "answer_scores": {
        "directness": 70,
        "specificity": 65,
        "logical_coherence": 75,
        "evasiveness": 30,
        "rationale": "概ね質問に答えているが一部曖昧な表現あり",
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


class TestEvaluate:
    """Claude API評価のテスト"""

    def test_basic_evaluation(self):
        """基本的な評価が正しくパースされるか"""
        meeting = generate_mock_meeting()
        extractor = QAPairExtractor()
        pairs = extractor.extract(meeting)

        client = _make_mock_client()
        evaluator = QAPairEvaluator(client=client)
        result = evaluator.evaluate(pairs[0])

        assert result.question_scores.substantiveness == 85
        assert result.question_scores.specificity == 78
        assert result.question_scores.constructiveness == 72
        assert result.question_scores.novelty == 80
        assert result.question_scores.average > 0

        assert result.answer_scores.directness == 70
        assert result.answer_scores.evasiveness == 30
        assert result.answer_scores.average > 0

        assert result.topic_relevance == 88

    def test_evaluate_batch(self):
        """バッチ評価"""
        meeting = generate_mock_meeting()
        extractor = QAPairExtractor()
        pairs = extractor.extract(meeting)[:3]

        client = _make_mock_client()
        evaluator = QAPairEvaluator(client=client)
        results = evaluator.evaluate_batch(pairs)

        assert len(results) == 3
        assert client.messages.create.call_count == 3
        for p in results:
            assert p.question_scores.substantiveness == 85

    def test_json_in_code_block(self):
        """```json ... ``` で囲まれたレスポンス"""
        wrapped = f"```json\n{MOCK_API_RESPONSE}\n```"
        client = _make_mock_client(wrapped)
        evaluator = QAPairEvaluator(client=client)

        meeting = generate_mock_meeting()
        pairs = QAPairExtractor().extract(meeting)
        result = evaluator.evaluate(pairs[0])

        assert result.question_scores.substantiveness == 85

    def test_malformed_json(self):
        """不正なJSONでもクラッシュしない"""
        client = _make_mock_client("this is not json")
        evaluator = QAPairEvaluator(client=client)

        meeting = generate_mock_meeting()
        pairs = QAPairExtractor().extract(meeting)
        result = evaluator.evaluate(pairs[0])

        # スコアはデフォルト(0)のまま
        assert result.question_scores.substantiveness == 0.0
        assert result.topic_relevance == 0.0

    def test_empty_response(self):
        """空レスポンス"""
        client = _make_mock_client("")
        evaluator = QAPairEvaluator(client=client)

        meeting = generate_mock_meeting()
        pairs = QAPairExtractor().extract(meeting)
        result = evaluator.evaluate(pairs[0])

        assert result.question_scores.substantiveness == 0.0

    def test_prompt_content(self):
        """プロンプトに正しいコンテキストが含まれるか"""
        meeting = generate_mock_meeting()
        pairs = QAPairExtractor().extract(meeting)

        client = _make_mock_client()
        evaluator = QAPairEvaluator(client=client)
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
        """トークナイズの基本動作"""
        tokens = _tokenize("エネルギー政策について")
        assert len(tokens) > 0
        assert all(isinstance(t, str) for t in tokens)

    def test_cosine_identical(self):
        """同一ベクトルの類似度は1.0"""
        vec = {"a": 1.0, "b": 2.0}
        assert abs(_cosine_similarity(vec, vec) - 1.0) < 0.001

    def test_cosine_orthogonal(self):
        """直交ベクトルの類似度は0.0"""
        vec_a = {"a": 1.0}
        vec_b = {"b": 1.0}
        assert _cosine_similarity(vec_a, vec_b) == 0.0

    def test_detect_similar_questions(self):
        """類似した質問が重複として検出される"""
        meeting = generate_mock_meeting()
        pairs = QAPairExtractor().extract(meeting)

        # 蓮舫と辻元の質問は同じ政治資金スキャンダルについて（類似度≈0.39）
        detect_duplicates(pairs, threshold=0.35)

        renho_pair = next(p for p in pairs if p.question.speaker == "蓮舫")
        tsujimoto_pair = next(p for p in pairs if p.question.speaker == "辻元清美")

        # どちらかが重複としてマークされるはず
        duplicates = [p for p in pairs if p.is_duplicate]
        assert len(duplicates) >= 1

    def test_no_duplicate_single_pair(self):
        """1ペアのみの場合は重複なし"""
        meeting = generate_mock_meeting()
        pairs = QAPairExtractor().extract(meeting)[:1]

        detect_duplicates(pairs)
        assert not pairs[0].is_duplicate

    def test_high_threshold_no_duplicates(self):
        """閾値が高すぎると重複なし"""
        meeting = generate_mock_meeting()
        pairs = QAPairExtractor().extract(meeting)

        detect_duplicates(pairs, threshold=0.99)
        assert all(not p.is_duplicate for p in pairs)

    def test_tfidf_vectors(self):
        """TF-IDFベクトルが生成される"""
        docs = [_tokenize("エネルギー政策"), _tokenize("防衛予算について")]
        vectors = _compute_tfidf(docs)
        assert len(vectors) == 2
        assert all(isinstance(v, dict) for v in vectors)
        assert all(len(v) > 0 for v in vectors)
