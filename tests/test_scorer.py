"""
テスト: ScoreAggregator
"""

import tempfile

from scorer import ScoreAggregator
from master_manager import MasterManager


class TestMemberScores:
    """議員別スコア集計"""

    def test_member_count(self, mock_meeting, scored_pairs):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MasterManager(data_dir=tmpdir)
            manager.update_from_qa_pairs(scored_pairs, session=215)
            agg = ScoreAggregator()
            cards = agg.aggregate_member_scores(scored_pairs, manager.members)

        assert len(cards) == 5

    def test_ranking_order(self, mock_meeting, scored_pairs):
        """overall_score の降順"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MasterManager(data_dir=tmpdir)
            manager.update_from_qa_pairs(scored_pairs, session=215)
            agg = ScoreAggregator()
            cards = agg.aggregate_member_scores(scored_pairs, manager.members)

        scores = [c.overall_score for c in cards]
        assert scores == sorted(scores, reverse=True)

    def test_tamaki_scores(self, mock_meeting, scored_pairs):
        """玉木のスコアが正しい"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MasterManager(data_dir=tmpdir)
            manager.update_from_qa_pairs(scored_pairs, session=215)
            agg = ScoreAggregator()
            cards = agg.aggregate_member_scores(scored_pairs, manager.members)

        tamaki = next(c for c in cards if c.name == "玉木雄一郎")
        assert tamaki.party == "国民民主党"
        assert tamaki.question_count == 1
        assert tamaki.avg_substantiveness == 88.0
        assert tamaki.topic_relevance_rate == 100.0
        assert tamaki.duplicate_rate == 0.0

    def test_duplicate_rate(self, mock_meeting, scored_pairs):
        """辻元の重複率が100%"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MasterManager(data_dir=tmpdir)
            manager.update_from_qa_pairs(scored_pairs, session=215)
            agg = ScoreAggregator()
            cards = agg.aggregate_member_scores(scored_pairs, manager.members)

        tsujimoto = next(c for c in cards if c.name == "辻元清美")
        assert tsujimoto.duplicate_rate == 100.0


class TestPartyScores:
    """政党別スコア集計"""

    def test_party_count(self, mock_meeting, scored_pairs):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MasterManager(data_dir=tmpdir)
            manager.update_from_qa_pairs(scored_pairs, session=215)
            agg = ScoreAggregator()
            member_scores = agg.aggregate_member_scores(scored_pairs, manager.members)
            cards = agg.aggregate_party_scores(member_scores, scored_pairs)

        party_names = {c.party for c in cards}
        assert "国民民主党" in party_names
        assert "日本維新の会" in party_names
        assert "立憲民主党" in party_names

    def test_weighted_average(self, mock_meeting, scored_pairs):
        """維新は2人（音喜多+浅田）の加重平均"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MasterManager(data_dir=tmpdir)
            manager.update_from_qa_pairs(scored_pairs, session=215)
            agg = ScoreAggregator()
            member_scores = agg.aggregate_member_scores(scored_pairs, manager.members)
            cards = agg.aggregate_party_scores(member_scores, scored_pairs)

        ishin = next(c for c in cards if c.party == "日本維新の会")
        assert ishin.member_count == 2
        assert ishin.total_questions == 2

    def test_ranking_order(self, mock_meeting, scored_pairs):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MasterManager(data_dir=tmpdir)
            manager.update_from_qa_pairs(scored_pairs, session=215)
            agg = ScoreAggregator()
            member_scores = agg.aggregate_member_scores(scored_pairs, manager.members)
            cards = agg.aggregate_party_scores(member_scores, scored_pairs)

        scores = [c.overall_score for c in cards]
        assert scores == sorted(scores, reverse=True)


class TestRespondentScores:
    """答弁者別スコア集計"""

    def test_respondent_count(self, scored_pairs):
        agg = ScoreAggregator()
        cards = agg.aggregate_respondent_scores(scored_pairs)

        names = {c.name for c in cards}
        assert "齋藤健" in names
        assert "高市早苗" in names

    def test_evasion_rate(self, scored_pairs):
        """高市の回避率が高い"""
        agg = ScoreAggregator()
        cards = agg.aggregate_respondent_scores(scored_pairs)

        takaichi = next(c for c in cards if c.name == "高市早苗")
        # 蓮舫(75)と辻元(80)の答弁 → 両方evasion_threshold(60)以上
        assert takaichi.evasion_rate == 100.0

    def test_ranking_order(self, scored_pairs):
        agg = ScoreAggregator()
        cards = agg.aggregate_respondent_scores(scored_pairs)

        scores = [c.avg_answer_quality for c in cards]
        assert scores == sorted(scores, reverse=True)


class TestDailyResult:
    """DailyResult生成"""

    def test_create_daily_result(self, mock_meeting, scored_pairs):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MasterManager(data_dir=tmpdir)
            manager.update_from_qa_pairs(scored_pairs, session=215)
            agg = ScoreAggregator()
            result = agg.create_daily_result(mock_meeting, scored_pairs, manager.members)

        assert result.date == "2026-03-10"
        assert result.house == "参議院"
        assert result.meeting_name == "予算委員会"
        assert result.total_qa_pairs == 5
        assert len(result.member_scores) == 5
        assert len(result.party_scores) >= 2
        assert len(result.respondent_scores) >= 2
        assert 0 <= result.topic_relevance_rate <= 100
        assert 0 <= result.duplicate_rate <= 100

    def test_save_and_load(self, mock_meeting, scored_pairs):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MasterManager(data_dir=tmpdir)
            manager.update_from_qa_pairs(scored_pairs, session=215)
            agg = ScoreAggregator()
            result = agg.create_daily_result(mock_meeting, scored_pairs, manager.members)

            from pathlib import Path
            import json
            path = Path(tmpdir) / "result.json"
            result.save(path)
            assert path.exists()

            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["date"] == "2026-03-10"
            assert data["total_qa_pairs"] == 5
