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


def _highlight_to_dict(h) -> dict:
    return {"text": h.text, "dimension": h.dimension,
            "sentiment": h.sentiment, "comment": h.comment}


def _qa_pairs_to_dicts(pairs) -> list[dict]:
    """QAPairリストを個別評価付きのdictリストに変換"""
    items = []
    for p in pairs:
        qs = p.question_scores
        ans = p.answer_scores
        items.append({
            "questioner": p.question.speaker,
            "questioner_group": p.question.speaker_group,
            "question_text": p.question.speech_text,
            "answerer": p.answer.speaker,
            "answerer_position": p.answer.speaker_position or "",
            "answer_text": p.answer.speech_text,
            "question_scores": {
                "justification": qs.justification,
                "evidence": qs.evidence,
                "constructiveness": qs.constructiveness,
                "novelty": qs.novelty,
                "public_interest": qs.public_interest,
                "average": qs.average,
                "rationale": qs.rationale,
                "highlights": [_highlight_to_dict(h) for h in qs.highlights],
            },
            "answer_scores": {
                "responsiveness": ans.responsiveness,
                "evidence": ans.evidence,
                "logical_coherence": ans.logical_coherence,
                "engagement": ans.engagement,
                "average": ans.average,
                "rationale": ans.rationale,
                "highlights": [_highlight_to_dict(h) for h in ans.highlights],
            },
            "topic_relevance": p.topic_relevance,
            "is_duplicate": p.is_duplicate,
        })
    return items


def _backfill_qa_pairs(out_file: Path, meeting, extractor, evaluator) -> None:
    """既存結果JSONにQAペア（テキスト+キャッシュスコア）を追加（API不使用）"""
    if not out_file.exists():
        return

    with open(out_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 既にqa_pairsがあって中身もあればスキップ
    existing = data.get("qa_pairs", [])
    if existing and existing[0].get("question_text"):
        logger.info("  スキップ（QAペアあり）: %s", out_file.name)
        return

    pairs = extractor.extract(meeting)
    if not pairs:
        return

    # キャッシュからスコアだけ読み込む（APIコールなし）
    for p in pairs:
        evaluator._load_cache(p)

    data["qa_pairs"] = _qa_pairs_to_dicts(pairs)

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("  QAペア追加: %s (%d件)", out_file.name, len(pairs))


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


def score_meeting(client, extractor, evaluator, scorer, meeting, session):
    """1会議をスコアリングして保存（全ペア評価）"""
    pairs = extractor.extract(meeting)
    if not pairs:
        logger.info("  QAペアなし、スキップ")
        return None

    logger.info("  %dペアを評価", len(pairs))

    evaluator.evaluate_batch(pairs)
    detect_duplicates(pairs)
    eval_pairs = pairs

    # セッション（質疑ブロック）評価
    session_blocks = extractor.extract_sessions(eval_pairs)
    session_data = []
    for sb in session_blocks:
        if len(sb.qa_pairs) >= 2:  # 2ペア以上のブロックのみ評価
            evaluator.evaluate_session(sb)
        session_data.append({
            "questioner": sb.questioner,
            "questioner_group": sb.questioner_group,
            "qa_count": len(sb.qa_pairs),
            "qa_average": sb.qa_average,
            "session_scores": {
                "argument_structure": sb.session_scores.argument_structure,
                "followup_quality": sb.session_scores.followup_quality,
                "time_efficiency": sb.session_scores.time_efficiency,
                "elicitation": sb.session_scores.elicitation,
                "overall_impact": sb.session_scores.overall_impact,
                "average": sb.session_scores.average,
                "rationale": sb.session_scores.rationale,
            },
            "combined_score": sb.combined_score,
        })

    with tempfile.TemporaryDirectory() as tmpdir:
        manager = MasterManager(data_dir=tmpdir)
        manager.update_from_qa_pairs(eval_pairs, session=session)
        result = scorer.create_daily_result(meeting, eval_pairs, manager.members)

    # 結果JSON に個別QAデータ + セッション評価 + 議事全文 + バージョンを含める
    result_dict = result.to_dict()
    result_dict["scoring_version"] = 2  # v1=旧軸, v2=DQI準拠+highlights+session
    result_dict["qa_pairs"] = _qa_pairs_to_dicts(eval_pairs)
    result_dict["session_blocks"] = session_data
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
    parser.add_argument("--max-meetings", type=int, default=0,
                        help="処理する会議数の上限 (0=全件)")
    parser.add_argument("--meeting", default=None,
                        help="対象委員会名 (省略=全委員会)")
    parser.add_argument("--force", action="store_true",
                        help="既存結果を上書き")
    parser.add_argument("--backfill-speeches", action="store_true",
                        help="既存結果に議事全文だけ追加（API不使用）")
    parser.add_argument("--upgrade", action="store_true",
                        help="v1（旧軸）の結果のみv2で再評価（v2済みはスキップ）")
    args = parser.parse_args()

    client = KokkaiAPIClient()
    extractor = QAPairExtractor()
    evaluator = QAPairEvaluator()
    scorer = ScoreAggregator()

    meetings_processed = 0
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
                    _backfill_qa_pairs(out_file, m, extractor, evaluator)
                    continue

                if out_file.exists():
                    if args.upgrade:
                        # v2済みならスキップ、v1なら再評価
                        try:
                            with open(out_file, "r", encoding="utf-8") as _f:
                                ver = json.load(_f).get("scoring_version", 1)
                            if ver >= 2:
                                logger.info("  スキップ（v2済み）: %s", out_file.name)
                                continue
                            logger.info("  v1→v2 アップグレード: %s", out_file.name)
                        except (json.JSONDecodeError, OSError):
                            pass
                    elif not args.force:
                        logger.info("  スキップ（既存）: %s", out_file.name)
                        continue

                if args.max_meetings and meetings_processed >= args.max_meetings:
                    logger.info("  会議数上限 (%d) に到達", args.max_meetings)
                    break

                score_meeting(client, extractor, evaluator, scorer,
                              m, session)
                meetings_processed += 1

            if args.max_meetings and meetings_processed >= args.max_meetings:
                break
        if args.max_meetings and meetings_processed >= args.max_meetings:
            break

    logger.info("=== バッチ完了: %d会議処理 ===", meetings_processed)


if __name__ == "__main__":
    main()
