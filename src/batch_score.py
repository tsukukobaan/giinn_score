"""
バッチスコアリング — 指定回次の全予算委員会を一括処理

Usage:
    python -m src.batch_score --sessions 217 218 219 220 221
    python -m src.batch_score --sessions 221 --max-pairs 0  # 全ペア評価
"""

import argparse
import json
import logging
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from kokkai_fetcher import KokkaiAPIClient
from qa_extractor import QAPairExtractor
from evaluator import QAPairEvaluator, detect_duplicates
from scorer import ScoreAggregator
from master_manager import MasterManager

logger = logging.getLogger(__name__)


def _discover_committees(client: KokkaiAPIClient, session: int) -> list[tuple[str, str]]:
    """指定回次の全委員会を (院, 委員会名) のリストで返す"""
    import requests
    # meeting_list は maximumRecords=100 が上限
    all_recs: list[dict] = []
    start = 1
    while True:
        params = {
            "sessionFrom": session, "sessionTo": session,
            "recordPacking": "json", "maximumRecords": 100,
        }
        if start > 1:
            params["startRecord"] = start
        r = requests.get("https://kokkai.ndl.go.jp/api/meeting_list",
                         params=params, timeout=30)
        if r.status_code != 200:
            break
        data = r.json()
        recs_page = data.get("meetingRecord", [])
        if isinstance(recs_page, dict):
            recs_page = [recs_page]
        all_recs.extend(recs_page)
        nxt = data.get("nextRecordPosition")
        if nxt is None:
            break
        start = int(nxt)
    seen = set()
    result = []
    for rec in all_recs:
        house = rec.get("nameOfHouse", "")
        meeting = rec.get("nameOfMeeting", "")
        if not house or not meeting:
            continue
        key = (house, meeting)
        if key not in seen:
            seen.add(key)
            result.append(key)

    # 本会議を除外（質疑応答形式でない）
    result = [(h, m) for h, m in result if "本会議" not in m]
    logger.info("第%d回: %d委員会発見", session, len(result))
    return result


def _qa_pairs_to_dicts(pairs) -> list[dict]:
    """QAPairリストを個別評価付きのdictリストに変換"""
    items = []
    for p in pairs:
        items.append({
            "questioner": p.question.speaker,
            "questioner_group": p.question.speaker_group,
            "question_text": p.question.speech_text,
            "answerer": p.answer.speaker,
            "answerer_position": p.answer.speaker_position or "",
            "answer_text": p.answer.speech_text,
            "question_scores": {
                "substantiveness": p.question_scores.substantiveness,
                "specificity": p.question_scores.specificity,
                "constructiveness": p.question_scores.constructiveness,
                "novelty": p.question_scores.novelty,
                "average": p.question_scores.average,
                "rationale": p.question_scores.rationale,
            },
            "answer_scores": {
                "directness": p.answer_scores.directness,
                "specificity": p.answer_scores.specificity,
                "logical_coherence": p.answer_scores.logical_coherence,
                "evasiveness": p.answer_scores.evasiveness,
                "average": p.answer_scores.average,
                "rationale": p.answer_scores.rationale,
            },
            "topic_relevance": p.topic_relevance,
            "is_duplicate": p.is_duplicate,
        })
    return items


def _backfill_speeches(out_file: Path, meeting) -> None:
    """既存結果JSONに議事全文だけ追加（API不使用）"""
    if not out_file.exists():
        logger.info("  スキップ（結果なし）: %s", out_file.name)
        return

    with open(out_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("speeches"):
        logger.info("  スキップ（全文あり）: %s", out_file.name)
        return

    data["speeches"] = [
        {
            "order": s.speech_order,
            "speaker": s.speaker,
            "speaker_group": s.speaker_group,
            "speaker_position": s.speaker_position or "",
            "speaker_role": s.speaker_role or "",
            "text": s.speech_text,
        }
        for s in sorted(meeting.speeches, key=lambda s: s.speech_order)
    ]

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("  全文追加: %s (%d発言)", out_file.name, len(data["speeches"]))


def score_meeting(client, extractor, evaluator, scorer, meeting, session, max_pairs):
    """1会議をスコアリングして保存"""
    pairs = extractor.extract(meeting)
    if not pairs:
        logger.info("  QAペアなし、スキップ")
        return None

    # 評価対象を制限
    eval_pairs = pairs if max_pairs == 0 else pairs[:max_pairs]
    logger.info("  %d/%dペアを評価", len(eval_pairs), len(pairs))

    evaluator.evaluate_batch(eval_pairs)
    detect_duplicates(eval_pairs)

    with tempfile.TemporaryDirectory() as tmpdir:
        manager = MasterManager(data_dir=tmpdir)
        manager.update_from_qa_pairs(eval_pairs, session=session)
        result = scorer.create_daily_result(meeting, eval_pairs, manager.members)

    # 結果JSON に個別QAデータ + 議事全文を含める
    result_dict = result.to_dict()
    result_dict["qa_pairs"] = _qa_pairs_to_dicts(eval_pairs)
    result_dict["speeches"] = [
        {
            "order": s.speech_order,
            "speaker": s.speaker,
            "speaker_group": s.speaker_group,
            "speaker_position": s.speaker_position or "",
            "speaker_role": s.speaker_role or "",
            "text": s.speech_text,
        }
        for s in sorted(meeting.speeches, key=lambda s: s.speech_order)
    ]

    out = Path("data/results")
    out.mkdir(parents=True, exist_ok=True)
    safe_name = meeting.name_of_meeting.replace("/", "_")
    filename = f"{result.date}_{meeting.name_of_house}_{safe_name}.json"
    filepath = out / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result_dict, f, ensure_ascii=False, indent=2)

    logger.info("  保存: %s (%dペア)", filename, result.total_qa_pairs)
    return result


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="バッチスコアリング")
    parser.add_argument("--sessions", nargs="+", type=int, required=True,
                        help="処理する国会回次")
    parser.add_argument("--max-pairs", type=int, default=15,
                        help="会議あたりの最大評価ペア数 (0=全件)")
    parser.add_argument("--meeting", default=None,
                        help="対象委員会名 (省略=全委員会)")
    parser.add_argument("--force", action="store_true",
                        help="既存結果を上書き")
    parser.add_argument("--backfill-speeches", action="store_true",
                        help="既存結果に議事全文だけ追加（API不使用）")
    args = parser.parse_args()

    client = KokkaiAPIClient()
    extractor = QAPairExtractor()
    evaluator = QAPairEvaluator()
    scorer = ScoreAggregator()

    for session in args.sessions:
        if args.meeting:
            # 特定委員会のみ（衆参両院）
            committee_list = [("衆議院", args.meeting), ("参議院", args.meeting)]
        else:
            # 全委員会を meeting_list から取得
            committee_list = _discover_committees(client, session)

        for house, meeting_name in committee_list:
            logger.info("=== 第%d回 %s %s ===", session, house, meeting_name)

            meetings = client.fetch_meetings(
                session=session,
                name_of_house=house,
                name_of_meeting=meeting_name,
            )
            logger.info("  %d会議取得", len(meetings))

            for m in meetings:
                logger.info("  [%s] %s %s %s: %d発言",
                            m.date, m.name_of_house, m.name_of_meeting,
                            m.issue, len(m.speeches))

                safe_name = m.name_of_meeting.replace("/", "_")
                out_file = Path("data/results") / f"{m.date}_{house}_{safe_name}.json"

                if args.backfill_speeches:
                    _backfill_speeches(out_file, m)
                    continue

                if out_file.exists() and not args.force:
                    logger.info("  スキップ（既存）: %s", out_file.name)
                    continue

                score_meeting(client, extractor, evaluator, scorer,
                              m, session, args.max_pairs)

    logger.info("=== バッチ完了 ===")


if __name__ == "__main__":
    main()
