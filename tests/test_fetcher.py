"""
テスト: KokkaiAPIClient
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from kokkai_fetcher import KokkaiAPIClient


# テスト用APIレスポンス
SAMPLE_MEETING_RECORD = {
    "issueID": "121520926X00320260310",
    "session": 215,
    "nameOfHouse": "参議院",
    "nameOfMeeting": "予算委員会",
    "issue": "令和８年度一般会計予算",
    "date": "2026-03-10",
    "speechRecord": [
        {
            "speechID": "121520926X00320260310_001",
            "speechOrder": 1,
            "speaker": "山田太郎",
            "speakerYomi": "やまだたろう",
            "speakerGroup": "",
            "speakerPosition": "委員長",
            "speakerRole": "委員長",
            "speech": "ただいまから予算委員会を開会いたします。",
        },
        {
            "speechID": "121520926X00320260310_002",
            "speechOrder": 2,
            "speaker": "鈴木一郎",
            "speakerYomi": "すずきいちろう",
            "speakerGroup": "自由民主党",
            "speakerPosition": "",
            "speakerRole": "",
            "speech": "○鈴木一郎 エネルギー政策について伺います。",
        },
        {
            "speechID": "121520926X00320260310_003",
            "speechOrder": 3,
            "speaker": "佐藤花子",
            "speakerYomi": "さとうはなこ",
            "speakerGroup": "",
            "speakerPosition": "経済産業大臣",
            "speakerRole": "",
            "speech": "佐藤花子 お答えいたします。エネルギー安全保障は重要な課題です。",
        },
    ],
}


def _make_api_response(records: list[dict], total: int | None = None) -> dict:
    """APIレスポンス形式のdictを生成"""
    if total is None:
        total = len(records)
    return {
        "numberOfRecords": str(total),
        "numberOfReturn": str(len(records)),
        "meetingRecord": records,
    }


class TestParseMeetings:
    """_parse_meetingsのテスト"""

    def test_basic_parse(self):
        """基本的なパースが正しく動くか"""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = KokkaiAPIClient(cache_dir=tmpdir)
            meetings = client._parse_meetings([SAMPLE_MEETING_RECORD])

            assert len(meetings) == 1
            m = meetings[0]
            assert m.name_of_house == "参議院"
            assert m.name_of_meeting == "予算委員会"
            assert m.date == "2026-03-10"
            assert m.session == 215
            assert len(m.speeches) == 3

    def test_speaker_name_removal(self):
        """発言冒頭の「○氏名」が除去されるか"""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = KokkaiAPIClient(cache_dir=tmpdir)
            meetings = client._parse_meetings([SAMPLE_MEETING_RECORD])

            # "○鈴木一郎 エネルギー..." → "エネルギー..."
            suzuki = meetings[0].speeches[1]
            assert suzuki.speaker == "鈴木一郎"
            assert not suzuki.speech_text.startswith("○")
            assert suzuki.speech_text.startswith("エネルギー")

            # "佐藤花子 お答え..." → "お答え..."
            sato = meetings[0].speeches[2]
            assert sato.speaker == "佐藤花子"
            assert sato.speech_text.startswith("お答え")

    def test_single_record_as_dict(self):
        """meetingRecordがdictの場合（1件のみ）もリストとして処理"""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = KokkaiAPIClient(cache_dir=tmpdir)
            # speechRecordが単一dictの場合
            record = {
                "issueID": "test",
                "session": 215,
                "nameOfHouse": "衆議院",
                "nameOfMeeting": "本会議",
                "issue": "",
                "date": "2026-03-11",
                "speechRecord": {
                    "speechID": "s1",
                    "speechOrder": 1,
                    "speaker": "テスト",
                    "speakerYomi": "",
                    "speakerGroup": "",
                    "speakerPosition": "",
                    "speakerRole": "",
                    "speech": "テスト発言",
                },
            }
            meetings = client._parse_meetings([record])
            assert len(meetings) == 1
            assert len(meetings[0].speeches) == 1

    def test_empty_records(self):
        """空のレコードリスト"""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = KokkaiAPIClient(cache_dir=tmpdir)
            meetings = client._parse_meetings([])
            assert meetings == []


class TestCacheHit:
    """キャッシュ機能のテスト"""

    def test_cache_read(self):
        """キャッシュファイルがあればAPIを呼ばない"""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = KokkaiAPIClient(cache_dir=tmpdir)

            # キャッシュファイルを先に作成
            cache_key = "meeting_215_参議院_予算委員会"
            cache_file = Path(tmpdir) / f"{cache_key}.json"
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump([SAMPLE_MEETING_RECORD], f, ensure_ascii=False)

            # _fetch_jsonが呼ばれないことを確認
            with patch.object(client, "_fetch_json") as mock_fetch:
                meetings = client.fetch_meetings(
                    session=215,
                    name_of_house="参議院",
                    name_of_meeting="予算委員会",
                )
                mock_fetch.assert_not_called()

            assert len(meetings) == 1
            assert meetings[0].name_of_meeting == "予算委員会"


class TestPagination:
    """ページネーションのテスト"""

    def test_multi_page(self):
        """複数ページのレコードを正しく結合"""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = KokkaiAPIClient(cache_dir=tmpdir)

            page1 = {
                "numberOfRecords": "2",
                "numberOfReturn": "1",
                "nextRecordPosition": "2",
                "meetingRecord": [SAMPLE_MEETING_RECORD],
            }
            record2 = {**SAMPLE_MEETING_RECORD, "date": "2026-03-11"}
            page2 = {
                "numberOfRecords": "2",
                "numberOfReturn": "1",
                "meetingRecord": [record2],
            }

            with patch.object(client, "_fetch_json", side_effect=[page1, page2]):
                meetings = client.fetch_meetings(
                    session=215,
                    name_of_house="参議院",
                    name_of_meeting="予算委員会",
                )

            assert len(meetings) == 2
            assert meetings[0].date == "2026-03-10"
            assert meetings[1].date == "2026-03-11"


class TestCheckNewMeetings:
    """check_new_meetingsのテスト"""

    def test_no_records(self):
        """新規議事録なし"""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = KokkaiAPIClient(cache_dir=tmpdir)

            with patch.object(client, "_fetch_json", return_value={
                "numberOfRecords": "0",
                "numberOfReturn": "0",
            }):
                result = client.check_new_meetings(215, "2026-03-10")
            assert result == []

    def test_with_records(self):
        """新規議事録あり"""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = KokkaiAPIClient(cache_dir=tmpdir)

            api_resp = {
                "numberOfRecords": "1",
                "numberOfReturn": "1",
                "meetingRecord": [{
                    "issueID": "test1",
                    "nameOfHouse": "参議院",
                    "nameOfMeeting": "予算委員会",
                    "issue": "令和８年度予算案",
                    "date": "2026-03-10",
                }],
            }
            with patch.object(client, "_fetch_json", return_value=api_resp):
                result = client.check_new_meetings(215, "2026-03-10")

            assert len(result) == 1
            assert result[0]["house"] == "参議院"
            assert result[0]["meeting"] == "予算委員会"


class TestRateLimit:
    """レート制限のテスト"""

    def test_rate_limit_enforced(self):
        """連続リクエスト時にインターバルが入る"""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = KokkaiAPIClient(cache_dir=tmpdir)
            client._last_req = 0.0

            with patch("time.sleep") as mock_sleep:
                # _last_reqを「今」に設定して_rate_limitを呼ぶ
                import time
                client._last_req = time.time()
                client._rate_limit()
                # sleepが呼ばれるはず
                mock_sleep.assert_called_once()
                slept = mock_sleep.call_args[0][0]
                assert 0 < slept <= 1.5
