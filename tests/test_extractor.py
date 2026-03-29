"""
テスト: QAPairExtractor + MasterManager
"""

import tempfile

from qa_extractor import QAPairExtractor
from master_manager import MasterManager
from mock_data import generate_mock_meeting


def test_qa_extraction():
    """QAPair抽出が正しく動くか"""
    meeting = generate_mock_meeting()
    extractor = QAPairExtractor()
    pairs = extractor.extract(meeting)

    # 委員長発言を除外し、質問-答弁ペアを抽出
    assert len(pairs) >= 4, f"Expected >=4 pairs, got {len(pairs)}"

    # 最初のペア: 玉木 → 齋藤
    assert pairs[0].question.speaker == "玉木雄一郎"
    assert pairs[0].answer.speaker == "齋藤健"
    assert pairs[0].question.is_question is True
    assert pairs[0].answer.is_answer is True

    # 蓮舫のスキャンダル質問もペアとして抽出される
    renho_pairs = [p for p in pairs if p.question.speaker == "蓮舫"]
    assert len(renho_pairs) == 1

    # 浅田の質問は政府参考人が答弁
    asada_pairs = [p for p in pairs if p.question.speaker == "浅田均"]
    assert len(asada_pairs) == 1
    assert "政府参考人" in asada_pairs[0].answer.speaker_position

    print(f"✓ QA抽出テスト通過: {len(pairs)}ペア")


def test_master_manager():
    """マスタ管理が正しく動くか"""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = MasterManager(data_dir=tmpdir)
        meeting = generate_mock_meeting()
        extractor = QAPairExtractor()
        pairs = extractor.extract(meeting)

        manager.update_from_qa_pairs(pairs, session=215)

        # 議員が登録されている
        assert "玉木雄一郎" in manager.members
        assert "蓮舫" in manager.members
        assert "音喜多駿" in manager.members

        # 玉木の所属政党
        tamaki = manager.members["玉木雄一郎"]
        assert tamaki.current_party() == "国民民主党"
        assert "215" in tamaki.elections

        # 答弁者が登録されている
        assert "齋藤健" in manager.respondents
        assert "増田和夫" in manager.respondents

        # 政府参考人の登録
        masuda = manager.respondents["増田和夫"]
        assert "防衛省" in masuda.current_position
        assert masuda.positions[0].appearances == 1

        # 永続化テスト
        manager.save()
        manager2 = MasterManager(data_dir=tmpdir)
        assert "玉木雄一郎" in manager2.members
        assert "増田和夫" in manager2.respondents

        print(f"✓ マスタ管理テスト通過: 議員{len(manager.members)}名, 答弁者{len(manager.respondents)}名")


def test_party_extraction():
    """所属会派から政党名を正しく抽出できるか"""
    assert MasterManager._extract_party_from_group("国民民主党・新緑風会") == "国民民主党"
    assert MasterManager._extract_party_from_group("立憲民主党・社民") == "立憲民主党"
    assert MasterManager._extract_party_from_group("自由民主党") == "自由民主党"
    assert MasterManager._extract_party_from_group("日本維新の会") == "日本維新の会"
    assert MasterManager._extract_party_from_group("") == "不明"
    print("✓ 政党名抽出テスト通過")


if __name__ == "__main__":
    test_qa_extraction()
    test_master_manager()
    test_party_extraction()
    print("\n全テスト通過")
