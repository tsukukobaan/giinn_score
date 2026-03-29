"""
質疑応答ペア抽出エンジン
"""

import logging

from models import Speech, Meeting, QAPair, SessionBlock

logger = logging.getLogger(__name__)


class QAPairExtractor:
    """会議の発言列から質疑応答ペアを抽出"""

    ANSWERER_POSITIONS = [
        "内閣総理大臣", "国務大臣", "副大臣", "大臣政務官",
        "政府参考人", "政府特別補佐人", "参考人",
        "内閣官房長官", "内閣法制局長官",
        "日本銀行総裁", "会計検査院長",
    ]

    CHAIR_KEYWORDS = ["委員長", "議長", "副議長", "会長"]

    PROCEDURAL_PHRASES = [
        "ただいまから", "開会いたします", "散会いたします",
        "休憩いたします", "再開いたします", "異議なし",
        "採決に入ります", "可決されました", "否決されました",
        "これにて", "以上で",
    ]

    def extract(self, meeting: Meeting) -> list[QAPair]:
        """質疑応答ペアを抽出"""
        pairs: list[QAPair] = []
        speeches = sorted(meeting.speeches, key=lambda s: s.speech_order)

        i = 0
        while i < len(speeches):
            sp = speeches[i]

            if self._is_chair(sp) or self._is_procedural(sp):
                i += 1
                continue

            if self._is_questioner(sp):
                sp.is_question = True
                # 直後の答弁を探す
                j = i + 1
                while j < len(speeches):
                    nxt = speeches[j]
                    if self._is_chair(nxt):
                        j += 1
                        continue
                    if self._is_answerer(nxt):
                        nxt.is_answer = True
                        pairs.append(QAPair(
                            question=sp, answer=nxt, meeting=meeting,
                        ))
                    break
            i += 1

        logger.info(
            "%s %s %s: %d発言 → %dペア抽出",
            meeting.name_of_house, meeting.name_of_meeting,
            meeting.date, len(speeches), len(pairs),
        )
        return pairs

    def _is_chair(self, sp: Speech) -> bool:
        pos = (sp.speaker_position or "") + (sp.speaker_role or "")
        return any(kw in pos for kw in self.CHAIR_KEYWORDS)

    def _is_answerer(self, sp: Speech) -> bool:
        pos = sp.speaker_position or ""
        if any(p in pos for p in self.ANSWERER_POSITIONS):
            return True
        if "大臣" in pos:
            return True
        return False

    def _is_questioner(self, sp: Speech) -> bool:
        return not self._is_answerer(sp) and not self._is_chair(sp)

    def extract_sessions(self, pairs: list[QAPair]) -> list[SessionBlock]:
        """QAペアを同一質問者の連続ブロックにグルーピング"""
        if not pairs:
            return []

        sessions: list[SessionBlock] = []
        current_name = pairs[0].question.speaker
        current_group = pairs[0].question.speaker_group
        current_pairs: list[QAPair] = [pairs[0]]

        for p in pairs[1:]:
            if p.question.speaker == current_name:
                current_pairs.append(p)
            else:
                sessions.append(SessionBlock(
                    questioner=current_name,
                    questioner_group=current_group,
                    qa_pairs=current_pairs,
                ))
                current_name = p.question.speaker
                current_group = p.question.speaker_group
                current_pairs = [p]

        sessions.append(SessionBlock(
            questioner=current_name,
            questioner_group=current_group,
            qa_pairs=current_pairs,
        ))

        logger.info("セッション分割: %dペア → %dブロック", len(pairs), len(sessions))
        return sessions

    def _is_procedural(self, sp: Speech) -> bool:
        head = sp.speech_text[:100]
        return any(p in head for p in self.PROCEDURAL_PHRASES)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    sys.path.insert(0, str(Path(__file__).parent.parent / "tests"))
    from mock_data import generate_mock_meeting

    meeting = generate_mock_meeting()
    extractor = QAPairExtractor()
    pairs = extractor.extract(meeting)

    print(f"\n抽出: {len(pairs)}ペア\n")
    for i, p in enumerate(pairs, 1):
        print(f"[{i}] Q: {p.question.speaker}（{p.question.speaker_group}）")
        print(f"    {p.question.speech_text[:60]}...")
        print(f"    A: {p.answer.speaker}（{p.answer.speaker_position}）")
        print(f"    {p.answer.speech_text[:60]}...")
        print()
