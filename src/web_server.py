"""
ローカルWebサーバー — スコアリング結果ダッシュボード

data/results/*.json を読み込んで表示する。
"""

import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

RESULTS_DIR = Path("data/results")

PARTY_COLORS = {
    "自由民主党": "#c0392b",
    "立憲民主党": "#2980b9",
    "日本維新の会": "#27ae60",
    "国民民主党": "#f39c12",
    "公明党": "#8e44ad",
    "日本共産党": "#e74c3c",
    "れいわ新選組": "#e91e63",
    "社民党": "#1abc9c",
    "参政党": "#d35400",
    "NHK党": "#95a5a6",
}


def _load_results() -> list[dict]:
    """全結果JSONを読み込み、日付降順で返す"""
    results = []
    if not RESULTS_DIR.exists():
        return results
    for f in RESULTS_DIR.glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
                data["_file"] = f.name
                results.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    results.sort(key=lambda r: r.get("date", ""), reverse=True)
    return results


def _party_color(party: str) -> str:
    return PARTY_COLORS.get(party, "#7f8c8d")


def _score_bar(score: float, max_val: float = 100) -> str:
    pct = min(score / max_val * 100, 100) if max_val else 0
    color = "#27ae60" if score >= 70 else "#f39c12" if score >= 50 else "#e74c3c"
    return f'<div class="bar"><div class="fill" style="width:{pct:.0f}%;background:{color}"></div><span>{score:.1f}</span></div>'


def _render_index(results: list[dict]) -> str:
    rows = ""
    for r in results:
        house_badge = "衆" if "衆" in r.get("house", "") else "参"
        house_cls = "shu" if house_badge == "衆" else "san"
        rows += f"""
        <tr onclick="location.href='/detail?file={r['_file']}'">
            <td>{r['date']}</td>
            <td><span class="badge {house_cls}">{house_badge}</span> {r.get('meeting_name','')}</td>
            <td class="num">{r.get('total_qa_pairs',0)}</td>
            <td class="num">{r.get('topic_relevance_rate',0):.0f}%</td>
            <td class="num">{r.get('duplicate_rate',0):.0f}%</td>
        </tr>"""

    return _page("GiinScore", f"""
    <h1>GiinScore</h1>
    <p class="sub">AI による国会質疑品質の定量評価</p>
    {"<p class='empty'>データがありません。パイプラインを実行してください。</p>" if not results else ""}
    <table>
        <thead><tr><th>日付</th><th>委員会</th><th>QAペア</th><th>議題関連率</th><th>重複率</th></tr></thead>
        <tbody>{rows}</tbody>
    </table>
    """)


def _render_detail(data: dict) -> str:
    # 政党ランキング
    party_rows = ""
    medals = ["🥇", "🥈", "🥉"]
    for i, ps in enumerate(data.get("party_scores", [])):
        medal = medals[i] if i < 3 else f"{i+1}."
        color = _party_color(ps["party"])
        party_rows += f"""
        <tr>
            <td>{medal}</td>
            <td><span class="party-dot" style="background:{color}"></span>{ps['party']}</td>
            <td class="num">{ps.get('member_count',0)}人</td>
            <td class="num">{ps.get('total_questions',0)}問</td>
            <td>{_score_bar(ps.get('overall_score',0))}</td>
            <td class="num">{ps.get('topic_relevance_rate',0):.0f}%</td>
        </tr>"""

    # 議員ランキング
    member_rows = ""
    for i, ms in enumerate(data.get("member_scores", [])[:20]):
        color = _party_color(ms["party"])
        member_rows += f"""
        <tr>
            <td class="num">{i+1}</td>
            <td>{ms['name']}</td>
            <td><span class="party-dot" style="background:{color}"></span>{ms['party']}</td>
            <td class="num">{ms.get('question_count',0)}</td>
            <td>{_score_bar(ms.get('overall_score',0))}</td>
            <td>{_score_bar(ms.get('avg_substantiveness',0))}</td>
            <td>{_score_bar(ms.get('avg_specificity',0))}</td>
            <td class="num">{ms.get('topic_relevance_rate',0):.0f}%</td>
            <td class="num">{ms.get('duplicate_rate',0):.0f}%</td>
        </tr>"""

    # 答弁者ランキング
    resp_rows = ""
    for rs in data.get("respondent_scores", [])[:15]:
        resp_rows += f"""
        <tr>
            <td>{rs['name']}</td>
            <td class="small">{rs.get('position','')}</td>
            <td class="num">{rs.get('answer_count',0)}</td>
            <td>{_score_bar(rs.get('avg_answer_quality',0))}</td>
            <td>{_score_bar(rs.get('avg_directness',0))}</td>
            <td class="num">{rs.get('evasion_rate',0):.0f}%</td>
        </tr>"""

    house = data.get("house", "")
    return _page(f"{data['date']} {house} {data.get('meeting_name','')}", f"""
    <a href="/" class="back">&larr; 一覧に戻る</a>
    <h1>{data['date']} {house} {data.get('meeting_name','')}</h1>
    <div class="stats">
        <div class="stat"><div class="stat-val">{data.get('total_qa_pairs',0)}</div><div class="stat-label">QAペア</div></div>
        <div class="stat"><div class="stat-val">{data.get('topic_relevance_rate',0):.0f}%</div><div class="stat-label">議題関連率</div></div>
        <div class="stat"><div class="stat-val">{data.get('duplicate_rate',0):.0f}%</div><div class="stat-label">重複率</div></div>
        <div class="stat"><div class="stat-val">{data.get('constructive_rate',0):.0f}%</div><div class="stat-label">建設率</div></div>
    </div>

    <h2>政党ランキング</h2>
    <table>
        <thead><tr><th></th><th>政党</th><th>議員数</th><th>質問数</th><th>総合スコア</th><th>議題関連率</th></tr></thead>
        <tbody>{party_rows}</tbody>
    </table>

    <h2>議員ランキング</h2>
    <table>
        <thead><tr><th>#</th><th>議員</th><th>政党</th><th>質問数</th><th>総合</th><th>本質性</th><th>具体性</th><th>関連率</th><th>重複率</th></tr></thead>
        <tbody>{member_rows}</tbody>
    </table>

    <h2>答弁者ランキング</h2>
    <table>
        <thead><tr><th>答弁者</th><th>役職</th><th>答弁数</th><th>答弁品質</th><th>直接性</th><th>回避率</th></tr></thead>
        <tbody>{resp_rows}</tbody>
    </table>
    """)


def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:#0f1923; color:#e0e0e0; padding:24px; max-width:1200px; margin:0 auto; }}
h1 {{ font-size:1.8rem; margin-bottom:4px; }}
h2 {{ font-size:1.3rem; margin:32px 0 12px; color:#64ffda; }}
.sub {{ color:#8892b0; margin-bottom:24px; }}
.back {{ color:#64ffda; text-decoration:none; display:inline-block; margin-bottom:16px; }}
.back:hover {{ text-decoration:underline; }}
.empty {{ color:#8892b0; font-size:1.1rem; margin:40px 0; }}
.stats {{ display:flex; gap:24px; margin:20px 0 32px; }}
.stat {{ background:#1a2332; border-radius:8px; padding:16px 24px; text-align:center; }}
.stat-val {{ font-size:2rem; font-weight:bold; color:#64ffda; }}
.stat-label {{ font-size:0.85rem; color:#8892b0; margin-top:4px; }}
table {{ width:100%; border-collapse:collapse; margin-bottom:24px; }}
thead {{ border-bottom:2px solid #2a3a4a; }}
th {{ text-align:left; padding:8px 12px; font-size:0.85rem; color:#8892b0; font-weight:600; }}
td {{ padding:8px 12px; border-bottom:1px solid #1a2a3a; font-size:0.9rem; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
td.small {{ font-size:0.8rem; color:#8892b0; max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
tr:hover {{ background:#1a2a3a; cursor:pointer; }}
.badge {{ display:inline-block; width:22px; height:22px; border-radius:50%; text-align:center; line-height:22px; font-size:0.75rem; font-weight:bold; color:#fff; }}
.badge.shu {{ background:#c0392b; }}
.badge.san {{ background:#2980b9; }}
.party-dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; vertical-align:middle; }}
.bar {{ display:flex; align-items:center; gap:8px; }}
.bar .fill {{ height:8px; border-radius:4px; min-width:2px; }}
.bar span {{ font-size:0.85rem; min-width:36px; text-align:right; font-variant-numeric:tabular-nums; }}
</style>
</head><body>{body}
<footer style="margin-top:48px;padding-top:16px;border-top:1px solid #1a2a3a;color:#5a6a7a;font-size:0.8rem;">
GiinScore — AI による参考評価値
</footer>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/" or path == "":
            results = _load_results()
            html = _render_index(results)
            self._respond(200, html)

        elif path == "/detail":
            filename = qs.get("file", [None])[0]
            if filename:
                filepath = RESULTS_DIR / filename
                if filepath.exists():
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    html = _render_detail(data)
                    self._respond(200, html)
                    return
            self._respond(404, _page("Not Found", "<h1>Not Found</h1>"))

        else:
            self._respond(404, _page("Not Found", "<h1>Not Found</h1>"))

    def _respond(self, code: int, html: str):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        logger.info(format, *args)


def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    parser = argparse.ArgumentParser(description="GiinScore ローカルサーバー")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="localhost")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), Handler)
    print(f"Server running at http://{args.host}:{args.port}/")
    print("Ctrl+C to stop")
    server.serve_forever()


if __name__ == "__main__":
    main()
