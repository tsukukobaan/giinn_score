"""
国会審議スコアボード — データモデル定義

全モジュールが共有するデータ構造。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class House(str, Enum):
    SHUGIIN = "衆議院"
    SANGIIN = "参議院"


# ============================================================
# 発言・会議
# ============================================================

@dataclass
class Speech:
    """個別発言"""
    speech_id: str
    speech_order: int
    speaker: str
    speaker_yomi: str
    speaker_group: str          # 所属会派
    speaker_position: str       # 肩書き
    speaker_role: str           # 役割（委員長・証人等）
    speech_text: str

    # 後工程で付与
    is_question: bool = False
    is_answer: bool = False


@dataclass
class Meeting:
    """会議"""
    issue_id: str
    session: int                # 国会回次
    name_of_house: str
    name_of_meeting: str        # 委員会名
    issue: str                  # 号数
    date: str                   # YYYY-MM-DD
    speeches: list[Speech] = field(default_factory=list)

    @property
    def date_obj(self) -> date:
        return datetime.strptime(self.date, "%Y-%m-%d").date()


# ============================================================
# 質疑応答ペアとスコア
# ============================================================

@dataclass
class QuestionScores:
    """質問品質スコア（各 0-100）"""
    substantiveness: float = 0.0    # 本質性
    specificity: float = 0.0        # 具体性
    constructiveness: float = 0.0   # 建設性
    novelty: float = 0.0            # 新規性
    rationale: str = ""             # 評価理由

    @property
    def average(self) -> float:
        return round(
            (self.substantiveness + self.specificity
             + self.constructiveness + self.novelty) / 4, 1
        )


@dataclass
class AnswerScores:
    """答弁品質スコア（各 0-100）"""
    directness: float = 0.0         # 直接性
    specificity: float = 0.0        # 具体性
    logical_coherence: float = 0.0  # 論理性
    evasiveness: float = 0.0        # 回避度（高い=悪い）
    rationale: str = ""

    @property
    def average(self) -> float:
        # evasivenessは逆転: 100-evasiveness で正規化
        return round(
            (self.directness + self.specificity
             + self.logical_coherence + (100 - self.evasiveness)) / 4, 1
        )


@dataclass
class QAPair:
    """質疑応答ペア"""
    question: Speech
    answer: Speech
    meeting: Meeting

    # AI評価スコア
    question_scores: QuestionScores = field(default_factory=QuestionScores)
    answer_scores: AnswerScores = field(default_factory=AnswerScores)
    topic_relevance: float = 0.0        # 議題関連性 0-100
    is_duplicate: bool = False
    duplicate_similarity: float = 0.0   # cosine類似度
    duplicate_of_speech_id: Optional[str] = None

    @property
    def question_quality(self) -> float:
        return self.question_scores.average

    @property
    def answer_quality(self) -> float:
        return self.answer_scores.average


# ============================================================
# 議員マスタ
# ============================================================

@dataclass
class MemberTerm:
    """選挙回次ごとの議員情報"""
    party: str
    district: str
    house: str
    elected_date: str           # YYYY-MM-DD
    status: str = "当選"        # 当選/落選/比例復活

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Member:
    """議員"""
    name: str
    yomi: str
    elections: dict[str, MemberTerm] = field(default_factory=dict)
    # key = 選挙回次の文字列（例: "50", "26参"）

    def current_party(self) -> str:
        """最新の選挙回次の所属政党"""
        if not self.elections:
            return "不明"
        latest_key = max(self.elections.keys())
        return self.elections[latest_key].party

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "yomi": self.yomi,
            "elections": {k: v.to_dict() for k, v in self.elections.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> Member:
        elections = {}
        for k, v in data.get("elections", {}).items():
            elections[k] = MemberTerm(**v)
        return cls(name=data["name"], yomi=data["yomi"], elections=elections)


# ============================================================
# 答弁者マスタ
# ============================================================

@dataclass
class RespondentPosition:
    """答弁者の肩書き履歴"""
    title: str
    first_seen: str             # YYYY-MM-DD
    last_seen: str              # YYYY-MM-DD
    appearances: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Respondent:
    """答弁者（大臣・政府参考人・参考人等）"""
    name: str
    positions: list[RespondentPosition] = field(default_factory=list)

    def add_appearance(self, position_title: str, seen_date: str) -> None:
        """出現を記録。新しい肩書きなら追加、既存なら更新"""
        for pos in self.positions:
            if pos.title == position_title:
                pos.last_seen = seen_date
                pos.appearances += 1
                return
        self.positions.append(RespondentPosition(
            title=position_title,
            first_seen=seen_date,
            last_seen=seen_date,
            appearances=1,
        ))

    @property
    def current_position(self) -> str:
        if not self.positions:
            return "不明"
        return max(self.positions, key=lambda p: p.last_seen).title

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "positions": [p.to_dict() for p in self.positions],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Respondent:
        positions = [RespondentPosition(**p) for p in data.get("positions", [])]
        return cls(name=data["name"], positions=positions)


# ============================================================
# スコアカード（集計結果）
# ============================================================

@dataclass
class MemberScoreCard:
    """議員別スコアカード"""
    name: str
    party: str
    question_count: int = 0
    avg_question_quality: float = 0.0
    avg_substantiveness: float = 0.0
    avg_specificity: float = 0.0
    topic_relevance_rate: float = 0.0   # %
    duplicate_rate: float = 0.0          # %
    answer_elicit_quality: float = 0.0   # 引き出した答弁の平均品質
    overall_score: float = 0.0


@dataclass
class PartyScoreCard:
    """政党別スコアカード"""
    party: str
    member_count: int = 0
    total_questions: int = 0
    avg_question_quality: float = 0.0
    avg_substantiveness: float = 0.0
    avg_specificity: float = 0.0
    avg_novelty: float = 0.0
    topic_relevance_rate: float = 0.0
    duplicate_rate: float = 0.0
    overall_score: float = 0.0


@dataclass
class RespondentScoreCard:
    """答弁者別スコアカード"""
    name: str
    position: str
    answer_count: int = 0
    avg_answer_quality: float = 0.0
    avg_directness: float = 0.0
    avg_specificity: float = 0.0
    evasion_rate: float = 0.0       # 回避率 %


@dataclass
class DailyResult:
    """日次評価結果"""
    date: str
    meeting_id: str
    house: str
    meeting_name: str
    total_qa_pairs: int
    member_scores: list[MemberScoreCard] = field(default_factory=list)
    party_scores: list[PartyScoreCard] = field(default_factory=list)
    respondent_scores: list[RespondentScoreCard] = field(default_factory=list)
    topic_relevance_rate: float = 0.0
    duplicate_rate: float = 0.0
    constructive_rate: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
