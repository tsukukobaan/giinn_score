"""
議員・答弁者マスタ管理
"""

import json
import logging
from pathlib import Path
from typing import Optional

from models import (
    QAPair, Member, MemberTerm, Respondent, RespondentPosition,
)

logger = logging.getLogger(__name__)


class MasterManager:
    """議員・答弁者マスタの管理"""

    def __init__(self, data_dir: str = "./data/masters"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.members_file = self.data_dir / "members.json"
        self.respondents_file = self.data_dir / "respondents.json"

        self.members: dict[str, Member] = {}
        self.respondents: dict[str, Respondent] = {}

        self._load()

    # --- 議員 ---

    def get_or_create_member(
        self,
        name: str,
        yomi: str = "",
        group: str = "",
        session: Optional[int] = None,
        house: str = "",
    ) -> Member:
        """議員を取得、なければ作成"""
        if name in self.members:
            member = self.members[name]
            # 新しいsessionの情報があれば追加
            if session and str(session) not in member.elections:
                party = self._extract_party_from_group(group)
                member.elections[str(session)] = MemberTerm(
                    party=party, district="", house=house,
                    elected_date="", status="",
                )
            return member

        party = self._extract_party_from_group(group)
        member = Member(name=name, yomi=yomi)
        if session:
            member.elections[str(session)] = MemberTerm(
                party=party, district="", house=house,
                elected_date="", status="",
            )
        self.members[name] = member
        logger.info("新規議員登録: %s (%s)", name, party)
        return member

    # --- 答弁者 ---

    def get_or_create_respondent(
        self, name: str, position: str, seen_date: str,
    ) -> Respondent:
        """答弁者を取得、なければ作成。出現を記録"""
        if name in self.respondents:
            resp = self.respondents[name]
            resp.add_appearance(position, seen_date)
            return resp

        resp = Respondent(name=name)
        resp.add_appearance(position, seen_date)
        self.respondents[name] = resp
        logger.info("新規答弁者登録: %s (%s)", name, position)
        return resp

    # --- 会議からまとめて更新 ---

    def update_from_qa_pairs(
        self, pairs: list[QAPair], session: int,
    ) -> None:
        """QAPairリストから議員・答弁者マスタを一括更新"""
        for pair in pairs:
            q = pair.question
            a = pair.answer
            meeting_date = pair.meeting.date
            house = pair.meeting.name_of_house

            # 質問者（議員）
            self.get_or_create_member(
                name=q.speaker, yomi=q.speaker_yomi,
                group=q.speaker_group, session=session,
                house=house,
            )

            # 答弁者
            if a.speaker_position:
                self.get_or_create_respondent(
                    name=a.speaker, position=a.speaker_position,
                    seen_date=meeting_date,
                )

    # --- 永続化 ---

    def save(self) -> None:
        with open(self.members_file, "w", encoding="utf-8") as f:
            data = {k: v.to_dict() for k, v in self.members.items()}
            json.dump(data, f, ensure_ascii=False, indent=2)

        with open(self.respondents_file, "w", encoding="utf-8") as f:
            data = {k: v.to_dict() for k, v in self.respondents.items()}
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(
            "マスタ保存: 議員%d名, 答弁者%d名",
            len(self.members), len(self.respondents),
        )

    def _load(self) -> None:
        if self.members_file.exists():
            with open(self.members_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self.members = {k: Member.from_dict(v) for k, v in raw.items()}
            logger.info("議員マスタ読込: %d名", len(self.members))

        if self.respondents_file.exists():
            with open(self.respondents_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self.respondents = {k: Respondent.from_dict(v) for k, v in raw.items()}
            logger.info("答弁者マスタ読込: %d名", len(self.respondents))

    @staticmethod
    def _extract_party_from_group(group: str) -> str:
        """所属会派文字列から政党名を抽出（「国民民主党・新緑風会」→「国民民主党」）"""
        if not group:
            return "不明"
        return group.split("・")[0].split("（")[0].strip()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "tests"))
    from mock_data import generate_mock_meeting
    from qa_extractor import QAPairExtractor

    meeting = generate_mock_meeting()
    extractor = QAPairExtractor()
    pairs = extractor.extract(meeting)

    manager = MasterManager()
    manager.update_from_qa_pairs(pairs, session=215)
    manager.save()

    print("議員マスタ:")
    for name, m in manager.members.items():
        print(f"  {name}: {m.current_party()}")

    print("\n答弁者マスタ:")
    for name, r in manager.respondents.items():
        print(f"  {name}: {r.current_position}")
