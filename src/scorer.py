"""
スコア集計・ランキング生成

QAPairレベルの評価を議員別・政党別・答弁者別に集計する。
"""

import logging
from collections import defaultdict

from models import (
    QAPair, Member,
    MemberScoreCard, PartyScoreCard, RespondentScoreCard, DailyResult,
    Meeting,
)

logger = logging.getLogger(__name__)

TOPIC_RELEVANCE_THRESHOLD = 50.0
EVASION_THRESHOLD = 60.0


class ScoreAggregator:
    """スコアを議員・政党・答弁者単位に集計"""

    def aggregate_member_scores(
        self,
        pairs: list[QAPair],
        members: dict[str, Member],
    ) -> list[MemberScoreCard]:
        """議員別スコアカードを生成"""
        by_member: dict[str, list[QAPair]] = defaultdict(list)
        for p in pairs:
            by_member[p.question.speaker].append(p)

        cards = []
        for name, member_pairs in by_member.items():
            n = len(member_pairs)
            member = members.get(name)
            party = member.current_party() if member else "不明"

            avg_q = sum(p.question_quality for p in member_pairs) / n
            avg_just = sum(p.question_scores.justification or p.question_scores.substantiveness
                           for p in member_pairs) / n
            avg_ev = sum(p.question_scores.evidence or p.question_scores.specificity
                         for p in member_pairs) / n
            avg_con = sum(p.question_scores.constructiveness for p in member_pairs) / n
            avg_pi = sum(p.question_scores.public_interest for p in member_pairs) / n
            relevance_rate = sum(
                1 for p in member_pairs if p.topic_relevance >= TOPIC_RELEVANCE_THRESHOLD
            ) / n * 100
            dup_rate = sum(1 for p in member_pairs if p.is_duplicate) / n * 100
            avg_answer = sum(p.answer_quality for p in member_pairs) / n

            overall = (avg_q * 0.4 + avg_answer * 0.2
                       + relevance_rate * 0.3 + (100 - dup_rate) * 0.1)

            cards.append(MemberScoreCard(
                name=name,
                party=party,
                question_count=n,
                avg_question_quality=round(avg_q, 1),
                avg_justification=round(avg_just, 1),
                avg_evidence=round(avg_ev, 1),
                avg_constructiveness=round(avg_con, 1),
                avg_public_interest=round(avg_pi, 1),
                topic_relevance_rate=round(relevance_rate, 1),
                duplicate_rate=round(dup_rate, 1),
                answer_elicit_quality=round(avg_answer, 1),
                overall_score=round(overall, 1),
                # 旧互換
                avg_substantiveness=round(avg_just, 1),
                avg_specificity=round(avg_ev, 1),
            ))

        cards.sort(key=lambda c: c.overall_score, reverse=True)
        return cards

    def aggregate_party_scores(
        self,
        member_scores: list[MemberScoreCard],
        pairs: list[QAPair],
    ) -> list[PartyScoreCard]:
        """政党別スコアカードを生成（質問回数で重み付け平均）"""
        by_party: dict[str, list[MemberScoreCard]] = defaultdict(list)
        for ms in member_scores:
            by_party[ms.party].append(ms)

        # ペアから新規性の平均を計算するため政党→ペアのマッピング
        party_pairs: dict[str, list[QAPair]] = defaultdict(list)
        for p in pairs:
            for ms in member_scores:
                if ms.name == p.question.speaker:
                    party_pairs[ms.party].append(p)
                    break

        cards = []
        for party, members in by_party.items():
            total_q = sum(m.question_count for m in members)
            if total_q == 0:
                continue

            avg_q = sum(m.avg_question_quality * m.question_count for m in members) / total_q
            avg_sub = sum(m.avg_substantiveness * m.question_count for m in members) / total_q
            avg_spec = sum(m.avg_specificity * m.question_count for m in members) / total_q

            pp = party_pairs.get(party, [])
            avg_nov = sum(p.question_scores.novelty for p in pp) / len(pp) if pp else 0

            relevance_rate = sum(
                1 for p in pp if p.topic_relevance >= TOPIC_RELEVANCE_THRESHOLD
            ) / len(pp) * 100 if pp else 0
            dup_rate = sum(1 for p in pp if p.is_duplicate) / len(pp) * 100 if pp else 0

            overall = (avg_q * 0.4 + relevance_rate * 0.3
                       + avg_nov * 0.2 + (100 - dup_rate) * 0.1)

            cards.append(PartyScoreCard(
                party=party,
                member_count=len(members),
                total_questions=total_q,
                avg_question_quality=round(avg_q, 1),
                avg_substantiveness=round(avg_sub, 1),
                avg_specificity=round(avg_spec, 1),
                avg_novelty=round(avg_nov, 1),
                topic_relevance_rate=round(relevance_rate, 1),
                duplicate_rate=round(dup_rate, 1),
                overall_score=round(overall, 1),
            ))

        cards.sort(key=lambda c: c.overall_score, reverse=True)
        return cards

    def aggregate_respondent_scores(
        self,
        pairs: list[QAPair],
    ) -> list[RespondentScoreCard]:
        """答弁者別スコアカードを生成"""
        by_resp: dict[str, list[QAPair]] = defaultdict(list)
        for p in pairs:
            by_resp[p.answer.speaker].append(p)

        cards = []
        for name, resp_pairs in by_resp.items():
            n = len(resp_pairs)
            position = resp_pairs[0].answer.speaker_position or ""

            avg_a = sum(p.answer_quality for p in resp_pairs) / n
            avg_resp = sum(p.answer_scores.responsiveness or p.answer_scores.directness
                           for p in resp_pairs) / n
            avg_ev = sum(p.answer_scores.evidence or p.answer_scores.specificity
                         for p in resp_pairs) / n
            avg_eng = sum(p.answer_scores.engagement for p in resp_pairs) / n
            # 旧互換: evasion_rate
            evasion_rate = sum(
                1 for p in resp_pairs
                if p.answer_scores.evasiveness >= EVASION_THRESHOLD
            ) / n * 100

            cards.append(RespondentScoreCard(
                name=name,
                position=position,
                answer_count=n,
                avg_answer_quality=round(avg_a, 1),
                avg_responsiveness=round(avg_resp, 1),
                avg_evidence=round(avg_ev, 1),
                avg_engagement=round(avg_eng, 1),
                avg_directness=round(avg_resp, 1),
                avg_specificity=round(avg_ev, 1),
                evasion_rate=round(evasion_rate, 1),
            ))

        cards.sort(key=lambda c: c.avg_answer_quality, reverse=True)
        return cards

    def create_daily_result(
        self,
        meeting: Meeting,
        pairs: list[QAPair],
        members: dict[str, Member],
    ) -> DailyResult:
        """全集計をまとめてDailyResultを生成"""
        member_scores = self.aggregate_member_scores(pairs, members)
        party_scores = self.aggregate_party_scores(member_scores, pairs)
        respondent_scores = self.aggregate_respondent_scores(pairs)

        n = len(pairs) if pairs else 1
        topic_rate = sum(
            1 for p in pairs if p.topic_relevance >= TOPIC_RELEVANCE_THRESHOLD
        ) / n * 100
        dup_rate = sum(1 for p in pairs if p.is_duplicate) / n * 100
        construct_rate = sum(
            1 for p in pairs if p.question_scores.constructiveness >= 50
        ) / n * 100

        return DailyResult(
            date=meeting.date,
            meeting_id=meeting.issue_id,
            house=meeting.name_of_house,
            meeting_name=meeting.name_of_meeting,
            total_qa_pairs=len(pairs),
            session=meeting.session,
            member_scores=member_scores,
            party_scores=party_scores,
            respondent_scores=respondent_scores,
            topic_relevance_rate=round(topic_rate, 1),
            duplicate_rate=round(dup_rate, 1),
            constructive_rate=round(construct_rate, 1),
        )


if __name__ == "__main__":
    import sys
    from pathlib import Path
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    sys.path.insert(0, str(Path(__file__).parent.parent / "tests"))
    from mock_data import generate_mock_meeting
    from qa_extractor import QAPairExtractor
    from master_manager import MasterManager
    import tempfile

    meeting = generate_mock_meeting()
    pairs = QAPairExtractor().extract(meeting)

    # テスト用にスコアを手動設定
    from models import QuestionScores, AnswerScores
    for i, p in enumerate(pairs):
        p.question_scores = QuestionScores(
            substantiveness=60 + i * 5,
            specificity=55 + i * 5,
            constructiveness=50 + i * 3,
            novelty=70 - i * 5,
        )
        p.answer_scores = AnswerScores(
            directness=65 + i * 3,
            specificity=60 + i * 4,
            logical_coherence=70,
            evasiveness=20 + i * 10,
        )
        p.topic_relevance = 80 - i * 15

    with tempfile.TemporaryDirectory() as tmpdir:
        manager = MasterManager(data_dir=tmpdir)
        manager.update_from_qa_pairs(pairs, session=215)

        aggregator = ScoreAggregator()
        result = aggregator.create_daily_result(meeting, pairs, manager.members)

        print(f"\n=== {result.date} {result.house} {result.meeting_name} ===")
        print(f"QAペア: {result.total_qa_pairs}")
        print(f"議題関連率: {result.topic_relevance_rate}%")
        print(f"重複率: {result.duplicate_rate}%")

        print("\n--- 議員ランキング ---")
        for ms in result.member_scores:
            print(f"  {ms.name}({ms.party}): {ms.overall_score}点 Q:{ms.avg_question_quality}")

        print("\n--- 政党ランキング ---")
        for ps in result.party_scores:
            print(f"  {ps.party}: {ps.overall_score}点 ({ps.total_questions}問)")

        print("\n--- 答弁者 ---")
        for rs in result.respondent_scores:
            print(f"  {rs.name}({rs.position}): {rs.avg_answer_quality}点 回避率:{rs.evasion_rate}%")
