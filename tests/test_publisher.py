"""
テスト: OGPImageGenerator + XPublisher
"""

from unittest.mock import patch, MagicMock

from models import DailyResult, PartyScoreCard, MemberScoreCard
from x_publisher import OGPImageGenerator, XPublisher


def _make_result() -> DailyResult:
    """テスト用DailyResult"""
    return DailyResult(
        date="2026-03-10",
        meeting_id="121521501X003",
        house="参議院",
        meeting_name="予算委員会",
        total_qa_pairs=5,
        party_scores=[
            PartyScoreCard(party="国民民主党", overall_score=78.0,
                          avg_question_quality=75.0, topic_relevance_rate=85.0),
            PartyScoreCard(party="日本維新の会", overall_score=72.0,
                          avg_question_quality=70.0, topic_relevance_rate=80.0),
            PartyScoreCard(party="立憲民主党", overall_score=45.0,
                          avg_question_quality=30.0, topic_relevance_rate=40.0),
        ],
        topic_relevance_rate=62.0,
        duplicate_rate=23.0,
    )


def _make_member() -> MemberScoreCard:
    return MemberScoreCard(
        name="玉木雄一郎", party="国民民主党",
        question_count=3, overall_score=86.0,
        avg_question_quality=82.0, avg_substantiveness=88.0,
        avg_specificity=84.0, topic_relevance_rate=92.0, duplicate_rate=4.0,
    )


class TestOGPHtml:
    """HTML生成のテスト（Playwrightは使わない）"""

    def test_daily_summary_html(self):
        """政党ランキングHTMLの内容確認"""
        gen = OGPImageGenerator()
        result = _make_result()
        html = gen._build_daily_summary_html(result)

        assert "参議院" in html
        assert "予算委員会" in html
        assert "2026-03-10" in html
        assert "国民民主党" in html
        assert "78.0点" in html
        assert "kokkai-score.jp" in html

    def test_member_highlight_html(self):
        """議員ハイライトHTMLの内容確認"""
        gen = OGPImageGenerator()
        result = _make_result()
        member = _make_member()
        html = gen._build_member_highlight_html(member, result)

        assert "玉木雄一郎" in html
        assert "国民民主党" in html
        assert "86.0" in html
        assert "88.0" in html  # substantiveness
        assert "84.0" in html  # specificity
        assert "kokkai-score.jp" in html


class TestDailyText:
    """投稿テキスト生成のテスト"""

    def test_daily_text_content(self):
        result = _make_result()
        pub = XPublisher(require_approval=False)
        text = pub._build_daily_text(result)

        assert "参院" in text
        assert "予算委員会" in text
        assert "国民民主党" in text
        assert "78.0点" in text
        assert "kokkai-score.jp" in text
        assert "#国会審議スコアボード" in text
        assert "sangiin" in text  # 参議院の録画URL

    def test_member_text_content(self):
        result = _make_result()
        member = _make_member()
        pub = XPublisher(require_approval=False)
        text = pub._build_member_text(member, result)

        assert "玉木雄一郎" in text
        assert "国民民主党" in text
        assert "86.0点" in text
        assert "88.0" in text  # substantiveness
        assert "#国会審議スコアボード" in text

    def test_shugiin_url(self):
        """衆議院の場合は衆議院TVのURL"""
        result = _make_result()
        result.house = "衆議院"
        pub = XPublisher(require_approval=False)
        text = pub._build_daily_text(result)

        assert "shugiintv" in text

    def test_party_top3_only(self):
        """上位3政党のみ表示"""
        result = _make_result()
        result.party_scores.append(
            PartyScoreCard(party="テスト党", overall_score=10.0)
        )
        pub = XPublisher(require_approval=False)
        text = pub._build_daily_text(result)

        assert "テスト党" not in text


class TestPostApproval:
    """投稿承認フローのテスト"""

    def test_approval_cancel(self):
        """承認拒否で投稿されない"""
        pub = XPublisher(require_approval=True)

        with patch("builtins.input", return_value="n"):
            result = pub._post("テスト投稿")
        assert result is None

    def test_no_approval_needed(self):
        """require_approval=Falseで直接投稿"""
        pub = XPublisher(require_approval=False)

        mock_client = MagicMock()
        mock_client.create_tweet.return_value = MagicMock(data={"id": "12345"})
        pub._client = mock_client

        tweet_id = pub._post("テスト投稿")
        assert tweet_id == "12345"
        mock_client.create_tweet.assert_called_once()
