"""
日次バッチオーケストレーション

全パイプラインを結合して実行する。
fetch → extract → evaluate → aggregate → publish → archive
"""

import argparse
import logging
import os
from datetime import date
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from kokkai_fetcher import KokkaiAPIClient
from qa_extractor import QAPairExtractor
from master_manager import MasterManager
from evaluator import QAPairEvaluator, detect_duplicates
from scorer import ScoreAggregator
from x_publisher import OGPImageGenerator, XPublisher
from models import DailyResult

logger = logging.getLogger(__name__)


class DailyPipeline:
    """日次パイプライン"""

    def __init__(self, dry_run: bool = False, result_dir: str = "data/results"):
        self.fetcher = KokkaiAPIClient()
        self.extractor = QAPairExtractor()
        self.master_manager = MasterManager()
        self.evaluator = QAPairEvaluator()
        self.scorer = ScoreAggregator()
        self.image_gen = OGPImageGenerator()
        self.publisher = XPublisher(
            require_approval=os.environ.get("REQUIRE_APPROVAL", "true").lower() == "true",
        )
        self.dry_run = dry_run
        self.result_dir = Path(result_dir)

    def run(
        self,
        target_date: str,
        session: int,
        name_of_house: Optional[str] = None,
        name_of_meeting: str = "予算委員会",
    ) -> Optional[DailyResult]:
        """パイプラインを実行"""
        logger.info("=== パイプライン開始: %s session=%d ===", target_date, session)

        # 1. 新規議事録チェック
        new_meetings = self.fetcher.check_new_meetings(
            session=session,
            target_date=target_date,
            name_of_house=name_of_house,
        )
        if not new_meetings:
            logger.info("新規議事録なし: %s", target_date)
            return None
        logger.info("新規議事録: %d件", len(new_meetings))

        # 2. 議事録取得
        houses = set()
        if name_of_house:
            houses.add(name_of_house)
        else:
            houses = {m["house"] for m in new_meetings}

        all_results = []
        for house in houses:
            meetings = self.fetcher.fetch_meetings(
                session=session,
                name_of_house=house,
                name_of_meeting=name_of_meeting,
                date_from=target_date,
                date_until=target_date,
            )

            for meeting in meetings:
                result = self._process_meeting(meeting, session)
                if result:
                    all_results.append(result)

        if not all_results:
            logger.info("処理対象の会議なし")
            return None

        # 最初の結果を返す（複数会議の場合は最初のみ）
        return all_results[0]

    def _process_meeting(self, meeting, session: int) -> Optional[DailyResult]:
        """1つの会議を処理"""
        logger.info("処理中: %s %s %s", meeting.name_of_house,
                     meeting.name_of_meeting, meeting.date)

        # 3. QAPair抽出
        pairs = self.extractor.extract(meeting)
        if not pairs:
            logger.info("質疑応答ペアなし")
            return None

        # 4. マスタ更新
        self.master_manager.update_from_qa_pairs(pairs, session=session)
        self.master_manager.save()

        # 5. AI評価
        if not self.dry_run:
            self.evaluator.evaluate_batch(pairs)
        else:
            logger.info("[dry-run] AI評価をスキップ")

        # 6. 重複検出
        detect_duplicates(pairs)

        # 7. スコア集計
        result = self.scorer.create_daily_result(
            meeting, pairs, self.master_manager.members,
        )

        # 8. OGP画像生成 + X投稿
        if not self.dry_run:
            try:
                daily_img = self.image_gen.generate_daily_summary(result)
                self.publisher.post_daily_summary(result, daily_img)
            except Exception as e:
                logger.error("日次サマリー投稿失敗: %s", e)

            if result.member_scores:
                top_member = result.member_scores[0]
                try:
                    member_img = self.image_gen.generate_member_highlight(top_member, result)
                    self.publisher.post_member_highlight(top_member, result, member_img)
                except Exception as e:
                    logger.error("ハイライト投稿失敗: %s", e)
        else:
            logger.info("[dry-run] 画像生成・X投稿をスキップ")

        # 9. 結果保存
        result_path = self.result_dir / f"{result.date}_{meeting.name_of_house}.json"
        result.save(result_path)
        logger.info("結果保存: %s", result_path)

        return result


def main():
    """CLI エントリーポイント"""
    load_dotenv()

    log_format = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    handlers.append(logging.FileHandler(log_dir / "pipeline.log", encoding="utf-8"))

    logging.basicConfig(level=logging.INFO, format=log_format, handlers=handlers)

    parser = argparse.ArgumentParser(description="国会審議スコアボード 日次バッチ")
    parser.add_argument("--date", default=date.today().isoformat(),
                        help="対象日 (YYYY-MM-DD)")
    parser.add_argument("--session", type=int,
                        default=int(os.environ.get("CURRENT_SESSION", 215)),
                        help="国会回次")
    parser.add_argument("--house", default=os.environ.get("TARGET_HOUSE", None),
                        help="衆議院 / 参議院 (空=両院)")
    parser.add_argument("--meeting", default=os.environ.get("TARGET_MEETING", "予算委員会"),
                        help="委員会名")
    parser.add_argument("--dry-run", action="store_true",
                        help="AI評価・X投稿をスキップ")
    args = parser.parse_args()

    pipeline = DailyPipeline(dry_run=args.dry_run)
    result = pipeline.run(
        target_date=args.date,
        session=args.session,
        name_of_house=args.house if args.house else None,
        name_of_meeting=args.meeting,
    )

    if result:
        logger.info("完了: %s %s %dペア 議題関連率%.1f%%",
                     result.house, result.meeting_name,
                     result.total_qa_pairs, result.topic_relevance_rate)
    else:
        logger.info("処理対象なし")


if __name__ == "__main__":
    main()
