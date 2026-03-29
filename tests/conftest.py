"""
共有 pytest フィクスチャ
"""

import tempfile
from pathlib import Path

import pytest

from models import QuestionScores, AnswerScores
from qa_extractor import QAPairExtractor
from mock_data import generate_mock_meeting


@pytest.fixture
def mock_meeting():
    """モック会議データ"""
    return generate_mock_meeting()


@pytest.fixture
def qa_pairs(mock_meeting):
    """抽出済みQAPairリスト"""
    return QAPairExtractor().extract(mock_meeting)


@pytest.fixture
def scored_pairs(qa_pairs):
    """スコア付きQAPairリスト（辻元は重複マーク済み）"""
    scores_map = {
        "玉木雄一郎": (
            QuestionScores(substantiveness=88, specificity=84, constructiveness=72, novelty=80),
            AnswerScores(directness=75, specificity=70, logical_coherence=80, evasiveness=20),
            92.0,
        ),
        "蓮舫": (
            QuestionScores(substantiveness=30, specificity=20, constructiveness=15, novelty=40),
            AnswerScores(directness=35, specificity=25, logical_coherence=50, evasiveness=75),
            15.0,
        ),
        "音喜多駿": (
            QuestionScores(substantiveness=85, specificity=82, constructiveness=78, novelty=75),
            AnswerScores(directness=80, specificity=85, logical_coherence=82, evasiveness=15),
            88.0,
        ),
        "辻元清美": (
            QuestionScores(substantiveness=25, specificity=18, constructiveness=12, novelty=10),
            AnswerScores(directness=30, specificity=20, logical_coherence=45, evasiveness=80),
            12.0,
        ),
        "浅田均": (
            QuestionScores(substantiveness=82, specificity=80, constructiveness=85, novelty=78),
            AnswerScores(directness=78, specificity=82, logical_coherence=80, evasiveness=10),
            90.0,
        ),
    }

    for p in qa_pairs:
        name = p.question.speaker
        if name in scores_map:
            qs, ans, rel = scores_map[name]
            p.question_scores = qs
            p.answer_scores = ans
            p.topic_relevance = rel

    for p in qa_pairs:
        if p.question.speaker == "辻元清美":
            p.is_duplicate = True
            p.duplicate_similarity = 0.85

    return qa_pairs


@pytest.fixture
def tmp_pipeline():
    """テスト用DailyPipeline（dry_run, tmpdir）"""
    from daily_pipeline import DailyPipeline

    with tempfile.TemporaryDirectory() as tmpdir:
        pipeline = DailyPipeline(dry_run=True, result_dir=tmpdir)
        pipeline.master_manager.data_dir = Path(tmpdir)
        pipeline.master_manager.members_file = Path(tmpdir) / "members.json"
        pipeline.master_manager.respondents_file = Path(tmpdir) / "respondents.json"
        pipeline._tmpdir = tmpdir  # テストからアクセス用
        yield pipeline
