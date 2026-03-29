"""
X自動投稿 + OGP画像生成

DailyResultからスコアカード画像を生成し、X (Twitter) に投稿する。
"""

import logging
import os
from pathlib import Path
from typing import Optional

from models import DailyResult, MemberScoreCard

logger = logging.getLogger(__name__)

MEDAL = {0: "\U0001f947", 1: "\U0001f948", 2: "\U0001f949"}  # 🥇🥈🥉

# 審議中継URL
SHUGIINTV_URL = "https://www.shugiintv.go.jp/"
SANGIINTV_URL = "https://www.webtv.sangiin.go.jp/"


# ============================================================
# OGP画像生成
# ============================================================

class OGPImageGenerator:
    """HTMLテンプレートからPlaywrightでスクリーンショットを撮ってOGP画像を生成"""

    def __init__(self, output_dir: str = "./data/images"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_daily_summary(self, result: DailyResult) -> Path:
        """政党ランキングカード画像を生成"""
        html = self._build_daily_summary_html(result)
        filename = f"daily_{result.date}_{result.house}.png"
        output_path = self.output_dir / filename
        self._screenshot(html, output_path)
        return output_path

    def generate_member_highlight(
        self, member: MemberScoreCard, result: DailyResult,
    ) -> Path:
        """議員個人ハイライトカード画像を生成"""
        html = self._build_member_highlight_html(member, result)
        filename = f"member_{result.date}_{member.name}.png"
        output_path = self.output_dir / filename
        self._screenshot(html, output_path)
        return output_path

    def _screenshot(self, html: str, output_path: Path) -> None:
        """PlaywrightでHTMLをスクリーンショット"""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 630})
            page.set_content(html)
            page.screenshot(path=str(output_path))
            browser.close()

        logger.info("OGP画像生成: %s", output_path)

    def _build_daily_summary_html(self, result: DailyResult) -> str:
        """政党ランキングカードのHTML"""
        rows = ""
        for i, ps in enumerate(result.party_scores[:5]):
            medal = MEDAL.get(i, f"{i + 1}.")
            rows += f"""
            <tr>
                <td class="rank">{medal}</td>
                <td class="party">{ps.party}</td>
                <td class="score">{ps.overall_score}点</td>
                <td class="detail">Q:{ps.avg_question_quality} 関連:{ps.topic_relevance_rate}%</td>
            </tr>"""

        return f"""\
<!DOCTYPE html>
<html lang="ja">
<head><meta charset="utf-8">
<style>
  body {{ margin: 0; padding: 40px; background: linear-gradient(135deg, #1a1a2e, #16213e);
         color: #fff; font-family: 'Noto Sans JP', sans-serif; }}
  .header {{ font-size: 28px; font-weight: bold; margin-bottom: 8px; }}
  .subheader {{ font-size: 18px; color: #8892b0; margin-bottom: 30px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  tr {{ border-bottom: 1px solid #2a2a4a; }}
  td {{ padding: 16px 8px; font-size: 22px; }}
  .rank {{ width: 50px; font-size: 28px; }}
  .party {{ font-weight: bold; }}
  .score {{ font-size: 26px; color: #64ffda; font-weight: bold; text-align: right; }}
  .detail {{ color: #8892b0; font-size: 16px; text-align: right; }}
  .footer {{ margin-top: 30px; font-size: 14px; color: #5a5a7a; }}
</style></head>
<body>
  <div class="header">{result.house} {result.meeting_name} 審議品質スコア</div>
  <div class="subheader">{result.date} | QAペア: {result.total_qa_pairs} | 議題関連率: {result.topic_relevance_rate}%</div>
  <table>{rows}</table>
  <div class="footer">国会審議スコアボード kokkai-score.jp ※AIによる参考評価値</div>
</body></html>"""

    def _build_member_highlight_html(
        self, member: MemberScoreCard, result: DailyResult,
    ) -> str:
        """議員個人ハイライトカードのHTML"""
        return f"""\
<!DOCTYPE html>
<html lang="ja">
<head><meta charset="utf-8">
<style>
  body {{ margin: 0; padding: 40px; background: linear-gradient(135deg, #1a1a2e, #0f3460);
         color: #fff; font-family: 'Noto Sans JP', sans-serif; }}
  .title {{ font-size: 22px; color: #64ffda; margin-bottom: 8px; }}
  .name {{ font-size: 36px; font-weight: bold; margin-bottom: 4px; }}
  .party {{ font-size: 20px; color: #8892b0; margin-bottom: 30px; }}
  .score-big {{ font-size: 72px; font-weight: bold; color: #64ffda; }}
  .score-label {{ font-size: 18px; color: #8892b0; }}
  .metrics {{ display: flex; gap: 40px; margin-top: 30px; }}
  .metric {{ text-align: center; }}
  .metric-value {{ font-size: 28px; font-weight: bold; }}
  .metric-label {{ font-size: 14px; color: #8892b0; }}
  .footer {{ margin-top: 40px; font-size: 14px; color: #5a5a7a; }}
</style></head>
<body>
  <div class="title">{result.date} {result.house} {result.meeting_name}</div>
  <div class="name">{member.name}</div>
  <div class="party">{member.party}</div>
  <div class="score-label">総合スコア</div>
  <div class="score-big">{member.overall_score}</div>
  <div class="metrics">
    <div class="metric"><div class="metric-value">{member.avg_substantiveness}</div><div class="metric-label">本質性</div></div>
    <div class="metric"><div class="metric-value">{member.avg_specificity}</div><div class="metric-label">具体性</div></div>
    <div class="metric"><div class="metric-value">{member.topic_relevance_rate}%</div><div class="metric-label">議題関連率</div></div>
    <div class="metric"><div class="metric-value">{member.duplicate_rate}%</div><div class="metric-label">重複率</div></div>
  </div>
  <div class="footer">国会審議スコアボード kokkai-score.jp ※AIによる参考評価値</div>
</body></html>"""


# ============================================================
# X投稿
# ============================================================

class XPublisher:
    """X (Twitter) APIで投稿"""

    def __init__(self, require_approval: bool = True):
        self.require_approval = require_approval
        self._client = None

    @property
    def client(self):
        """tweepy.Clientを遅延初期化"""
        if self._client is None:
            import tweepy
            self._client = tweepy.Client(
                bearer_token=os.environ["X_BEARER_TOKEN"],
                consumer_key=os.environ["X_API_KEY"],
                consumer_secret=os.environ["X_API_SECRET"],
                access_token=os.environ["X_ACCESS_TOKEN"],
                access_token_secret=os.environ["X_ACCESS_SECRET"],
            )
        return self._client

    def post_daily_summary(
        self, result: DailyResult, image_path: Optional[Path] = None,
    ) -> Optional[str]:
        """日次サマリーを投稿"""
        text = self._build_daily_text(result)
        return self._post(text, image_path)

    def post_member_highlight(
        self,
        member: MemberScoreCard,
        result: DailyResult,
        image_path: Optional[Path] = None,
    ) -> Optional[str]:
        """議員ハイライトを投稿"""
        text = self._build_member_text(member, result)
        return self._post(text, image_path)

    def _post(self, text: str, image_path: Optional[Path] = None) -> Optional[str]:
        """投稿実行（承認フロー付き）"""
        if self.require_approval:
            print("\n=== X投稿プレビュー ===")
            print(text)
            if image_path:
                print(f"\n画像: {image_path}")
            print("========================")
            confirm = input("投稿しますか？ [y/N]: ").strip().lower()
            if confirm != "y":
                logger.info("投稿キャンセル")
                return None

        media_ids = None
        if image_path and image_path.exists():
            import tweepy
            auth = tweepy.OAuth1UserHandler(
                os.environ["X_API_KEY"],
                os.environ["X_API_SECRET"],
                os.environ["X_ACCESS_TOKEN"],
                os.environ["X_ACCESS_SECRET"],
            )
            api_v1 = tweepy.API(auth)
            media = api_v1.media_upload(str(image_path))
            media_ids = [media.media_id]

        response = self.client.create_tweet(text=text, media_ids=media_ids)
        tweet_id = response.data["id"]
        logger.info("投稿完了: tweet_id=%s", tweet_id)
        return tweet_id

    def _build_daily_text(self, result: DailyResult) -> str:
        """日次サマリーの投稿テキスト"""
        house_short = "衆院" if "衆" in result.house else "参院"
        tv_url = SHUGIINTV_URL if "衆" in result.house else SANGIINTV_URL

        lines = [f"\U0001f4ca 本日の{house_short}{result.meeting_name} 審議品質スコア\n"]

        for i, ps in enumerate(result.party_scores[:3]):
            medal = MEDAL.get(i, "")
            lines.append(f"{medal} {ps.party} {ps.overall_score}点")

        lines.append(f"\n議題関連率: {result.topic_relevance_rate}% | "
                      f"重複質問率: {result.duplicate_rate}%")
        lines.append(f"\n\U0001f449 詳細: https://kokkai-score.jp/{result.date}")
        lines.append(f"\U0001f4fa 録画: {tv_url}")
        lines.append(f"\n#国会審議スコアボード #{result.meeting_name}")

        return "\n".join(lines)

    def _build_member_text(
        self, member: MemberScoreCard, result: DailyResult,
    ) -> str:
        """議員ハイライトの投稿テキスト"""
        tv_url = SHUGIINTV_URL if "衆" in result.house else SANGIINTV_URL

        lines = [
            "\U0001f3c6 本日のベスト質疑\n",
            f"{member.name} 議員（{member.party}）",
            f"総合スコア: {member.overall_score}点\n",
            f"\u2705 本質性 {member.avg_substantiveness} | 具体性 {member.avg_specificity}",
            f"\u2705 議題関連率 {member.topic_relevance_rate}% | 重複率 {member.duplicate_rate}%",
            f"\n\U0001f4fa 録画: {tv_url}",
            f"\n#国会審議スコアボード #{result.meeting_name}",
        ]

        return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # テスト用: テキスト生成のみ（画像・投稿なし）
    from models import PartyScoreCard
    result = DailyResult(
        date="2026-03-10",
        meeting_id="test",
        house="参議院",
        meeting_name="予算委員会",
        total_qa_pairs=5,
        party_scores=[
            PartyScoreCard(party="国民民主党", overall_score=78.0,
                          avg_question_quality=75.0, topic_relevance_rate=85.0),
            PartyScoreCard(party="日本維新の会", overall_score=72.0,
                          avg_question_quality=70.0, topic_relevance_rate=80.0),
            PartyScoreCard(party="立憲民主党", overall_score=45.0,
                          avg_question_quality=30.0, topic_relevance_rate=40.0),
        ],
        topic_relevance_rate=62.0,
        duplicate_rate=23.0,
    )
    pub = XPublisher(require_approval=True)
    print(pub._build_daily_text(result))

    print("\n---\n")

    member = MemberScoreCard(
        name="玉木雄一郎", party="国民民主党",
        overall_score=86.0, avg_substantiveness=88.0,
        avg_specificity=84.0, topic_relevance_rate=92.0, duplicate_rate=4.0,
    )
    print(pub._build_member_text(member, result))
