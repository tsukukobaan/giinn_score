"""
国会会議録検索システム APIクライアント

API仕様: https://kokkai.ndl.go.jp/api.html
- /api/meeting    : 会議単位出力（発言本文あり）← メイン
- /api/speech     : 発言単位出力
- /api/meeting_list : 会議一覧（本文なし）

制約:
- maximumRecords=100（1リクエスト上限）
- 会議数1000件超でエラー → 回次で絞る
- レート制限: 1.5秒インターバル
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from models import Speech, Meeting

logger = logging.getLogger(__name__)

BASE_URL = "https://kokkai.ndl.go.jp/api"
MAX_RECORDS = 10
INTERVAL_SEC = 1.5


class KokkaiAPIClient:
    """国会会議録検索システムAPIクライアント"""

    def __init__(self, cache_dir: str = "./data/cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_req = 0.0

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_req
        if elapsed < INTERVAL_SEC:
            time.sleep(INTERVAL_SEC - elapsed)
        self._last_req = time.time()

    def _fetch_json(self, endpoint: str, params: dict) -> dict:
        """1ページ取得（requests必須）"""
        import requests as req

        params.setdefault("recordPacking", "json")
        params.setdefault("maximumRecords", MAX_RECORDS)
        url = f"{BASE_URL}/{endpoint}"

        self._rate_limit()
        logger.info("GET %s params=%s", url, params)
        resp = req.get(url, params=params, timeout=30)
        if resp.status_code == 400:
            logger.warning("API 400 Bad Request: %s", resp.url)
            return {"numberOfRecords": "0", "numberOfReturn": "0"}
        resp.raise_for_status()
        return resp.json()

    # ----------------------------------------------------------
    # 会議単位取得（発言本文を含む）
    # ----------------------------------------------------------

    def fetch_meetings(
        self,
        session: int,
        name_of_house: str = "参議院",
        name_of_meeting: str = "予算委員会",
        date_from: Optional[str] = None,
        date_until: Optional[str] = None,
    ) -> list[Meeting]:
        """
        会議単位で議事録を取得。

        Args:
            session: 国会回次（例: 215）
            name_of_house: 衆議院 / 参議院
            name_of_meeting: 委員会名
            date_from / date_until: YYYY-MM-DD
        """
        params: dict = {
            "sessionFrom": session,
            "sessionTo": session,
            "nameOfHouse": name_of_house,
            "nameOfMeeting": name_of_meeting,
        }
        if date_from:
            params["from"] = date_from
        if date_until:
            params["until"] = date_until

        # キャッシュ
        safe_meeting = name_of_meeting.replace("/", "_")
        cache_key = f"meeting_{session}_{name_of_house}_{safe_meeting}"
        if date_from:
            cache_key += f"_from{date_from}"
        if date_until:
            cache_key += f"_until{date_until}"
        cache_file = self.cache_dir / f"{cache_key}.json"

        if cache_file.exists():
            logger.info("Cache hit: %s", cache_file)
            with open(cache_file, "r", encoding="utf-8") as f:
                return self._parse_meetings(json.load(f))

        # ページネーションで全件取得
        all_records: list[dict] = []
        start = None

        while True:
            if start is not None:
                params["startRecord"] = start
            data = self._fetch_json("meeting", params)

            total = int(data.get("numberOfRecords", 0))
            returned = int(data.get("numberOfReturn", 0))
            logger.info("取得 %d件 / 全%d件 (start=%s)", returned, total, start)

            if returned == 0:
                break

            records = data.get("meetingRecord", [])
            if isinstance(records, dict):
                records = [records]
            all_records.extend(records)

            nxt = data.get("nextRecordPosition")
            if nxt is None:
                break
            start = int(nxt)

        # キャッシュ保存
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(all_records, f, ensure_ascii=False, indent=2)

        return self._parse_meetings(all_records)

    # ----------------------------------------------------------
    # 新規議事録の有無を確認（日次ポーリング用）
    # ----------------------------------------------------------

    def check_new_meetings(
        self,
        session: int,
        target_date: str,
        name_of_house: Optional[str] = None,
    ) -> list[dict]:
        """
        指定日に公開された議事録があるか確認。
        meeting_list（軽量）を使用。
        """
        params: dict = {
            "sessionFrom": session,
            "sessionTo": session,
            "from": target_date,
            "until": target_date,
        }
        if name_of_house:
            params["nameOfHouse"] = name_of_house

        data = self._fetch_json("meeting_list", params)
        total = int(data.get("numberOfRecords", 0))

        if total == 0:
            return []

        records = data.get("meetingRecord", [])
        if isinstance(records, dict):
            records = [records]

        results = []
        for rec in records:
            results.append({
                "issue_id": rec.get("issueID", ""),
                "house": rec.get("nameOfHouse", ""),
                "meeting": rec.get("nameOfMeeting", ""),
                "issue": rec.get("issue", ""),
                "date": rec.get("date", ""),
            })
        return results

    # ----------------------------------------------------------
    # パース
    # ----------------------------------------------------------

    REQUIRED_MEETING_FIELDS = ("issueID", "nameOfHouse", "nameOfMeeting", "date")

    def _parse_meetings(self, records: list[dict]) -> list[Meeting]:
        meetings = []
        for rec in records:
            # 必須フィールドの検証
            missing = [f for f in self.REQUIRED_MEETING_FIELDS if not rec.get(f)]
            if missing:
                logger.warning("会議レコードをスキップ（欠落: %s）: %s",
                               missing, rec.get("issueID", "unknown"))
                continue

            speech_records = rec.get("speechRecord", [])
            if isinstance(speech_records, dict):
                speech_records = [speech_records]

            speeches = []
            for sr in speech_records:
                try:
                    text = sr.get("speech", "")
                    name = sr.get("speaker", "")

                    # 発言冒頭の「○氏名」除去
                    if text.startswith(f"○{name}"):
                        text = text[len(f"○{name}"):].strip()
                    elif text.startswith(name):
                        text = text[len(name):].strip()

                    speeches.append(Speech(
                        speech_id=sr.get("speechID") or "",
                        speech_order=int(sr.get("speechOrder") or 0),
                        speaker=name,
                        speaker_yomi=sr.get("speakerYomi") or "",
                        speaker_group=sr.get("speakerGroup") or "",
                        speaker_position=sr.get("speakerPosition") or "",
                        speaker_role=sr.get("speakerRole") or "",
                        speech_text=text,
                    ))
                except (TypeError, ValueError) as e:
                    logger.warning("発言レコードをスキップ: %s", e)
                    continue

            meetings.append(Meeting(
                issue_id=rec.get("issueID", ""),
                session=int(rec.get("session", 0)),
                name_of_house=rec.get("nameOfHouse", ""),
                name_of_meeting=rec.get("nameOfMeeting", ""),
                issue=rec.get("issue", ""),
                date=rec.get("date", ""),
                speeches=speeches,
            ))
        return meetings


# ----------------------------------------------------------
# スタンドアロン実行
# ----------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # モックなしで実APIを叩く例
    # client = KokkaiAPIClient()
    # meetings = client.fetch_meetings(session=215, name_of_house="参議院", name_of_meeting="予算委員会")
    # for m in meetings:
    #     print(f"{m.date} {m.name_of_house} {m.name_of_meeting} {m.issue}: {len(m.speeches)}発言")

    print("kokkai_fetcher.py — use KokkaiAPIClient to fetch meeting records.")
    print("See CLAUDE.md for usage instructions.")
