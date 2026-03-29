"""
テスト: QAPairExtractor + MasterManager
"""

import tempfile

from qa_extractor import QAPairExtractor
from master_manager import MasterManager


def test_qa_extraction(mock_meeting):
    """QAPair抽出が正しく動くか"""
    extractor = QAPairExtractor()
    pairs = extractor.extract(mock_meeting)

    assert len(pairs) >= 4, f"Expected >=4 pairs, got {len(pairs)}"

    assert pairs[0].question.speaker == "玉木雄一郎"
    assert pairs[0].answer.speaker == "齋藤健"
    assert pairs[0].question.is_question is True
    assert pairs[0].answer.is_answer is True

    renho_pairs = [p for p in pairs if p.question.speaker == "蓮舫"]
    assert len(renho_pairs) == 1

    asada_pairs = [p for p in pairs if p.question.speaker == "浅田均"]
    assert len(asada_pairs) == 1
    assert "政府参考人" in asada_pairs[0].answer.speaker_position


def test_master_manager(qa_pairs):
    """マスタ管理が正しく動くか"""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = MasterManager(data_dir=tmpdir)
        manager.update_from_qa_pairs(qa_pairs, session=215)

        assert "玉木雄一郎" in manager.members
        assert "蓮舫" in manager.members
        assert "音喜多駿" in manager.members

        tamaki = manager.members["玉木雄一郎"]
        assert tamaki.current_party() == "国民民主党"
        assert "215" in tamaki.elections

        assert "齋藤健" in manager.respondents
        assert "増田和夫" in manager.respondents

        masuda = manager.respondents["増田和夫"]
        assert "防衛省" in masuda.current_position
        assert masuda.positions[0].appearances == 1

        manager.save()
        manager2 = MasterManager(data_dir=tmpdir)
        assert "玉木雄一郎" in manager2.members
        assert "増田和夫" in manager2.respondents


def test_party_extraction():
    """所属会派から政党名を正しく抽出できるか"""
    assert MasterManager._extract_party_from_group("国民民主党・新緑風会") == "国民民主党"
    assert MasterManager._extract_party_from_group("立憲民主党・社民") == "立憲民主党"
    assert MasterManager._extract_party_from_group("自由民主党") == "自由民主党"
    assert MasterManager._extract_party_from_group("日本維新の会") == "日本維新の会"
    assert MasterManager._extract_party_from_group("") == "不明"
