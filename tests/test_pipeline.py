"""
テスト: DailyPipeline
"""

import tempfile
import json
from pathlib import Path
from unittest.mock import patch

from daily_pipeline import DailyPipeline
from mock_data import generate_mock_meeting


def _make_pipeline(tmpdir: str) -> DailyPipeline:
    """テスト用パイプライン（外部依存はdry_runで回避）"""
    pipeline = DailyPipeline(dry_run=True, result_dir=tmpdir)
    pipeline.master_manager.data_dir = Path(tmpdir)
    pipeline.master_manager.members_file = Path(tmpdir) / "members.json"
    pipeline.master_manager.respondents_file = Path(tmpdir) / "respondents.json"
    return pipeline


class TestPipelineFlow:
    """パイプライン全体フローのテスト"""

    def test_no_new_meetings(self):
        """新規議事録なしの場合はNone"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = _make_pipeline(tmpdir)

            with patch.object(pipeline.fetcher, "check_new_meetings", return_value=[]):
                result = pipeline.run("2026-03-10", session=215)

            assert result is None

    def test_full_flow_dry_run(self):
        """dry-runでフルフロー実行"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = _make_pipeline(tmpdir)
            meeting = generate_mock_meeting()

            with patch.object(pipeline.fetcher, "check_new_meetings", return_value=[
                {"house": "参議院", "meeting": "予算委員会", "date": "2026-03-10"},
            ]):
                with patch.object(pipeline.fetcher, "fetch_meetings", return_value=[meeting]):
                    result = pipeline.run(
                        "2026-03-10", session=215,
                        name_of_house="参議院",
                    )

            assert result is not None
            assert result.date == "2026-03-10"
            assert result.house == "参議院"
            assert result.total_qa_pairs >= 4

    def test_process_meeting(self):
        """単一会議の処理"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = _make_pipeline(tmpdir)
            meeting = generate_mock_meeting()

            result = pipeline._process_meeting(meeting, session=215)

            assert result is not None
            assert result.total_qa_pairs >= 4
            assert len(result.member_scores) >= 4
            assert len(result.party_scores) >= 2

            # マスタが更新されている
            assert "玉木雄一郎" in pipeline.master_manager.members

    def test_result_saved_to_json(self):
        """結果がJSONファイルに保存される"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = _make_pipeline(tmpdir)
            meeting = generate_mock_meeting()

            result = pipeline._process_meeting(meeting, session=215)

            result_file = Path(tmpdir) / "2026-03-10_参議院.json"
            assert result_file.exists()
            with open(result_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["date"] == "2026-03-10"
            assert data["total_qa_pairs"] >= 4


class TestPipelineComponents:
    """パイプライン各コンポーネントの連携テスト"""

    def test_master_persistence(self):
        """マスタが永続化される"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = _make_pipeline(tmpdir)
            meeting = generate_mock_meeting()

            pipeline._process_meeting(meeting, session=215)

            members_file = Path(tmpdir) / "members.json"
            assert members_file.exists()
            with open(members_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert "玉木雄一郎" in data

    def test_empty_meeting(self):
        """発言のない会議はNone"""
        from models import Meeting
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = _make_pipeline(tmpdir)
            empty_meeting = Meeting(
                issue_id="test", session=215, name_of_house="参議院",
                name_of_meeting="予算委員会", issue="", date="2026-03-10",
                speeches=[],
            )

            result = pipeline._process_meeting(empty_meeting, session=215)
            assert result is None
