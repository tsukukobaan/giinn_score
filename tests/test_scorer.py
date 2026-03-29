"""
テスト: ScoreAggregator
"""

import tempfile

from models import QuestionScores, AnswerScores
from scorer import ScoreAggregator
from qa_extractor import QAPairExtractor
from master_manager import MasterManager
from mock_data import generate_mock_meeting


def _prepare_scored_pairs():
    """スコア付きQAPairリストを準備"""
    meeting = generate_mock_meeting()
    pairs = QAPairExtractor().extract(meeting)

    # 手動スコア設定
    scores_map = {
        "玉木雄一郎": (
            QuestionScores(substantiveness=88, specificity=84, constructiveness=72, novelty=80),
            AnswerScores(directness=75, specificity=70, logical_coherence=80, evasiveness=20),
            92.0,  # topic_relevance
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

    for p in pairs:
        name = p.question.speaker
        if name in scores_map:
            qs, ans, rel = scores_map[name]
            p.question_scores = qs
            p.answer_scores = ans
            p.topic_relevance = rel

    # 辻元を重複とマーク
    for p in pairs:
        if p.question.speaker == "辻元清美":
            p.is_duplicate = True
            p.duplicate_similarity = 0.85

    return meeting, pairs


class TestMemberScores:
    """議員別スコア集計"""

    def test_member_count(self):
        meeting, pairs = _prepare_scored_pairs()
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MasterManager(data_dir=tmpdir)
            manager.update_from_qa_pairs(pairs, session=215)
            agg = ScoreAggregator()
            cards = agg.aggregate_member_scores(pairs, manager.members)

        assert len(cards) == 5

    def test_ranking_order(self):
        """overall_score の降順"""
        meeting, pairs = _prepare_scored_pairs()
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MasterManager(data_dir=tmpdir)
            manager.update_from_qa_pairs(pairs, session=215)
            agg = ScoreAggregator()
            cards = agg.aggregate_member_scores(pairs, manager.members)

        scores = [c.overall_score for c in cards]
        assert scores == sorted(scores, reverse=True)

    def test_tamaki_scores(self):
        """玉木のスコアが正しい"""
        meeting, pairs = _prepare_scored_pairs()
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MasterManager(data_dir=tmpdir)
            manager.update_from_qa_pairs(pairs, session=215)
            agg = ScoreAggregator()
            cards = agg.aggregate_member_scores(pairs, manager.members)

        tamaki = next(c for c in cards if c.name == "玉木雄一郎")
        assert tamaki.party == "国民民主党"
        assert tamaki.question_count == 1
        assert tamaki.avg_substantiveness == 88.0
        assert tamaki.topic_relevance_rate == 100.0
        assert tamaki.duplicate_rate == 0.0

    def test_duplicate_rate(self):
        """辻元の重複率が100%"""
        meeting, pairs = _prepare_scored_pairs()
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MasterManager(data_dir=tmpdir)
            manager.update_from_qa_pairs(pairs, session=215)
            agg = ScoreAggregator()
            cards = agg.aggregate_member_scores(pairs, manager.members)

        tsujimoto = next(c for c in cards if c.name == "辻元清美")
        assert tsujimoto.duplicate_rate == 100.0


class TestPartyScores:
    """政党別スコア集計"""

    def test_party_count(self):
        meeting, pairs = _prepare_scored_pairs()
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MasterManager(data_dir=tmpdir)
            manager.update_from_qa_pairs(pairs, session=215)
            agg = ScoreAggregator()
            member_scores = agg.aggregate_member_scores(pairs, manager.members)
            cards = agg.aggregate_party_scores(member_scores, pairs)

        party_names = {c.party for c in cards}
        assert "国民民主党" in party_names
        assert "日本維新の会" in party_names
        assert "立憲民主党" in party_names

    def test_weighted_average(self):
        """維新は2人（音喜多+浅田）の加重平均"""
        meeting, pairs = _prepare_scored_pairs()
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MasterManager(data_dir=tmpdir)
            manager.update_from_qa_pairs(pairs, session=215)
            agg = ScoreAggregator()
            member_scores = agg.aggregate_member_scores(pairs, manager.members)
            cards = agg.aggregate_party_scores(member_scores, pairs)

        ishin = next(c for c in cards if c.party == "日本維新の会")
        assert ishin.member_count == 2
        assert ishin.total_questions == 2

    def test_ranking_order(self):
        meeting, pairs = _prepare_scored_pairs()
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MasterManager(data_dir=tmpdir)
            manager.update_from_qa_pairs(pairs, session=215)
            agg = ScoreAggregator()
            member_scores = agg.aggregate_member_scores(pairs, manager.members)
            cards = agg.aggregate_party_scores(member_scores, pairs)

        scores = [c.overall_score for c in cards]
        assert scores == sorted(scores, reverse=True)


class TestRespondentScores:
    """答弁者別スコア集計"""

    def test_respondent_count(self):
        meeting, pairs = _prepare_scored_pairs()
        agg = ScoreAggregator()
        cards = agg.aggregate_respondent_scores(pairs)

        names = {c.name for c in cards}
        assert "齋藤健" in names
        assert "高市早苗" in names

    def test_evasion_rate(self):
        """高市の回避率が高い"""
        meeting, pairs = _prepare_scored_pairs()
        agg = ScoreAggregator()
        cards = agg.aggregate_respondent_scores(pairs)

        takaichi = next(c for c in cards if c.name == "高市早苗")
        # 蓮舫(75)と辻元(80)の答弁 → 両方evasion_threshold(60)以上
        assert takaichi.evasion_rate == 100.0

    def test_ranking_order(self):
        meeting, pairs = _prepare_scored_pairs()
        agg = ScoreAggregator()
        cards = agg.aggregate_respondent_scores(pairs)

        scores = [c.avg_answer_quality for c in cards]
        assert scores == sorted(scores, reverse=True)


class TestDailyResult:
    """DailyResult生成"""

    def test_create_daily_result(self):
        meeting, pairs = _prepare_scored_pairs()
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MasterManager(data_dir=tmpdir)
            manager.update_from_qa_pairs(pairs, session=215)
            agg = ScoreAggregator()
            result = agg.create_daily_result(meeting, pairs, manager.members)

        assert result.date == "2026-03-10"
        assert result.house == "参議院"
        assert result.meeting_name == "予算委員会"
        assert result.total_qa_pairs == 5
        assert len(result.member_scores) == 5
        assert len(result.party_scores) >= 2
        assert len(result.respondent_scores) >= 2
        assert 0 <= result.topic_relevance_rate <= 100
        assert 0 <= result.duplicate_rate <= 100

    def test_save_and_load(self):
        meeting, pairs = _prepare_scored_pairs()
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MasterManager(data_dir=tmpdir)
            manager.update_from_qa_pairs(pairs, session=215)
            agg = ScoreAggregator()
            result = agg.create_daily_result(meeting, pairs, manager.members)

            from pathlib import Path
            import json
            path = Path(tmpdir) / "result.json"
            result.save(path)
            assert path.exists()

            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["date"] == "2026-03-10"
            assert data["total_qa_pairs"] == 5
