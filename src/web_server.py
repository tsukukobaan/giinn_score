"""
GiinScore ローカルWebサーバー

data/results/*.json を読み込んでダッシュボードを表示する。

ページ構成:
  / — トップ（ハイ/ローパフォーマー + 委員会一覧）
  /detail?file=... — 委員会詳細
  /member?name=...&file=... — 議員詳細（個別QA評価）
  /party?party=...&session=... — 政党詳細
"""

import json
import logging
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote

logger = logging.getLogger(__name__)

RESULTS_DIR = Path("data/results")

PARTY_COLORS = {
    "自由民主党": "#c0392b", "立憲民主党": "#2980b9", "日本維新の会": "#27ae60",
    "国民民主党": "#f39c12", "公明党": "#8e44ad", "日本共産党": "#e74c3c",
    "れいわ新選組": "#e91e63", "社民党": "#1abc9c", "参政党": "#d35400",
}
MEDAL = {0: "\U0001f947", 1: "\U0001f948", 2: "\U0001f949"}


def _load_results() -> list[dict]:
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


def _score_badge(score: float) -> str:
    color = "#27ae60" if score >= 70 else "#f39c12" if score >= 50 else "#e74c3c"
    return f'<span class="score-badge" style="background:{color}">{score:.0f}</span>'


# ============================================================
# トップページ
# ============================================================

def _render_index(results: list[dict], session_filter: str = "") -> str:
    sessions = sorted({r.get("session", 0) for r in results if r.get("session")}, reverse=True)
    if session_filter:
        results = [r for r in results if str(r.get("session", "")) == session_filter]

    # ハイ/ローパフォーマー集計
    all_members: dict[str, list] = defaultdict(list)
    for r in results:
        for ms in r.get("member_scores", []):
            if ms.get("overall_score", 0) > 0:
                all_members[ms["name"]].append(ms)

    member_avg = []
    for name, scores in all_members.items():
        avg = sum(s["overall_score"] for s in scores) / len(scores)
        party = scores[0].get("party", "")
        total_q = sum(s.get("question_count", 0) for s in scores)
        member_avg.append({"name": name, "party": party, "avg": avg, "appearances": len(scores), "total_q": total_q})

    # 2回以上登場した議員のみ
    qualified = [m for m in member_avg if m["appearances"] >= 2]
    if not qualified:
        qualified = member_avg
    qualified.sort(key=lambda m: m["avg"], reverse=True)

    top5 = qualified[:5]
    bottom5 = list(reversed(qualified[-5:])) if len(qualified) > 5 else []

    # 回次タブ
    tabs = f'<div class="tabs"><a href="/" class="tab {"active" if not session_filter else ""}">全回次</a>'
    for s in sessions:
        cls = "active" if session_filter == str(s) else ""
        tabs += f'<a href="/?session={s}" class="tab {cls}">第{s}回</a>'
    tabs += '</div>'

    # パフォーマーカード
    def _perf_cards(members, title, icon):
        if not members:
            return ""
        cards = f'<h2>{icon} {title}</h2><div class="perf-grid">'
        for m in members:
            color = _party_color(m["party"])
            cards += f'''<a href="/member_profile?name={quote(m["name"])}" class="perf-card">
                <div class="perf-score" style="color:{"#27ae60" if m["avg"]>=60 else "#e74c3c"}">{m["avg"]:.0f}</div>
                <div class="perf-name">{m["name"]}</div>
                <div class="perf-party"><span class="party-dot" style="background:{color}"></span>{m["party"]}</div>
                <div class="perf-meta">{m["total_q"]}問 / {m["appearances"]}委員会</div>
            </a>'''
        cards += '</div>'
        return cards

    perf_html = _perf_cards(top5, "高評価議員", "\U0001f3c6") + _perf_cards(bottom5, "低評価議員", "\u26a0\ufe0f")

    # 委員会一覧
    rows = ""
    for r in results:
        if r.get("total_qa_pairs", 0) == 0:
            continue
        house_badge = "衆" if "衆" in r.get("house", "") else "参"
        house_cls = "shu" if house_badge == "衆" else "san"
        session_label = f'第{r["session"]}回' if r.get("session") else ""
        rows += f"""
        <tr onclick="location.href='/detail?file={r['_file']}'">
            <td>{r['date']}</td>
            <td class="small">{session_label}</td>
            <td><span class="badge {house_cls}">{house_badge}</span> {r.get('meeting_name','')}</td>
            <td class="num">{r.get('total_qa_pairs',0)}</td>
            <td class="num">{r.get('topic_relevance_rate',0):.0f}%</td>
        </tr>"""

    return _page("GiinScore", f"""
    <h1>GiinScore</h1>
    <p class="sub">AI による国会質疑品質の定量評価</p>
    {tabs}
    {perf_html}
    <h2>委員会別スコア</h2>
    <table>
        <thead><tr><th>日付</th><th>回次</th><th>委員会</th><th>QAペア</th><th>議題関連率</th></tr></thead>
        <tbody>{rows}</tbody>
    </table>
    """)


# ============================================================
# 委員会詳細
# ============================================================

def _render_detail(data: dict) -> str:
    fname = data.get("_file", "")

    party_rows = ""
    for i, ps in enumerate(data.get("party_scores", [])):
        medal = MEDAL.get(i, f"{i+1}.")
        color = _party_color(ps["party"])
        session = data.get("session", "")
        party_rows += f"""
        <tr onclick="location.href='/party?party={quote(ps["party"])}&session={session}'">
            <td>{medal}</td>
            <td><span class="party-dot" style="background:{color}"></span>{ps['party']}</td>
            <td class="num">{ps.get('member_count',0)}人</td>
            <td class="num">{ps.get('total_questions',0)}問</td>
            <td>{_score_bar(ps.get('overall_score',0))}</td>
            <td class="num">{ps.get('topic_relevance_rate',0):.0f}%</td>
        </tr>"""

    member_rows = ""
    for i, ms in enumerate(data.get("member_scores", [])[:30]):
        color = _party_color(ms["party"])
        member_rows += f"""
        <tr onclick="location.href='/member?name={quote(ms["name"])}&file={fname}'">
            <td class="num">{i+1}</td>
            <td>{ms['name']}</td>
            <td><span class="party-dot" style="background:{color}"></span>{ms['party']}</td>
            <td class="num">{ms.get('question_count',0)}</td>
            <td>{_score_bar(ms.get('overall_score',0))}</td>
            <td>{_score_bar(ms.get('avg_substantiveness',0))}</td>
            <td>{_score_bar(ms.get('avg_specificity',0))}</td>
            <td class="num">{ms.get('topic_relevance_rate',0):.0f}%</td>
        </tr>"""

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
    has_speeches = bool(data.get("speeches"))
    transcript_link = f'<a href="/transcript?file={fname}" class="btn">議事全文を見る</a>' if has_speeches else ""
    return _page(f"{data['date']} {house} {data.get('meeting_name','')}", f"""
    <a href="/" class="back">&larr; 一覧に戻る</a>
    <h1>{data['date']} {house} {data.get('meeting_name','')}</h1>
    <div class="stats">
        <div class="stat"><div class="stat-val">{data.get('total_qa_pairs',0)}</div><div class="stat-label">QAペア</div></div>
        <div class="stat"><div class="stat-val">{data.get('topic_relevance_rate',0):.0f}%</div><div class="stat-label">議題関連率</div></div>
        <div class="stat"><div class="stat-val">{data.get('duplicate_rate',0):.0f}%</div><div class="stat-label">重複率</div></div>
        <div class="stat"><div class="stat-val">{data.get('constructive_rate',0):.0f}%</div><div class="stat-label">建設率</div></div>
        <div class="stat">{transcript_link}</div>
    </div>
    <h2>政党ランキング</h2>
    <table>
        <thead><tr><th></th><th>政党</th><th>議員数</th><th>質問数</th><th>総合スコア</th><th>議題関連率</th></tr></thead>
        <tbody>{party_rows}</tbody>
    </table>
    <h2>議員ランキング</h2>
    <table>
        <thead><tr><th>#</th><th>議員</th><th>政党</th><th>質問数</th><th>総合</th><th>本質性</th><th>具体性</th><th>関連率</th></tr></thead>
        <tbody>{member_rows}</tbody>
    </table>
    <h2>答弁者ランキング</h2>
    <table>
        <thead><tr><th>答弁者</th><th>役職</th><th>答弁数</th><th>答弁品質</th><th>直接性</th><th>回避率</th></tr></thead>
        <tbody>{resp_rows}</tbody>
    </table>
    """)


# ============================================================
# 議員詳細（個別QA評価）
# ============================================================

def _render_member(data: dict, member_name: str) -> str:
    # 議員のスコアカード
    ms = None
    for m in data.get("member_scores", []):
        if m["name"] == member_name:
            ms = m
            break

    if not ms:
        return _page("Not Found", '<a href="/" class="back">&larr;</a><h1>議員が見つかりません</h1>')

    color = _party_color(ms["party"])
    fname = data.get("_file", "")

    # 個別QAペア
    qa_html = ""
    qa_pairs = data.get("qa_pairs", [])
    member_qas = [q for q in qa_pairs if q.get("questioner") == member_name]

    if member_qas:
        for i, qa in enumerate(member_qas, 1):
            qs = qa.get("question_scores", {})
            ans = qa.get("answer_scores", {})
            q_avg = qs.get("average", 0)
            a_avg = ans.get("average", 0)

            qa_html += f'''
            <div class="qa-card">
                <div class="qa-header">
                    <span class="qa-num">Q&A #{i}</span>
                    {_score_badge(q_avg)} Q品質 &nbsp; {_score_badge(a_avg)} A品質
                    &nbsp; <span class="small">議題関連: {qa.get("topic_relevance",0):.0f}</span>
                    {"<span class='dup-badge'>重複</span>" if qa.get("is_duplicate") else ""}
                </div>
                <div class="qa-section">
                    <div class="qa-label">質問 — {qa.get("questioner","")}</div>
                    <div class="qa-text">{qa.get("question_text","")}</div>
                    <div class="qa-scores">
                        本質性 {_score_bar(qs.get("substantiveness",0))}
                        具体性 {_score_bar(qs.get("specificity",0))}
                        建設性 {_score_bar(qs.get("constructiveness",0))}
                        新規性 {_score_bar(qs.get("novelty",0))}
                    </div>
                    <div class="qa-rationale">{qs.get("rationale","")}</div>
                </div>
                <div class="qa-section answer">
                    <div class="qa-label">答弁 — {qa.get("answerer","")}（{qa.get("answerer_position","")}）</div>
                    <div class="qa-text">{qa.get("answer_text","")}</div>
                    <div class="qa-scores">
                        直接性 {_score_bar(ans.get("directness",0))}
                        具体性 {_score_bar(ans.get("specificity",0))}
                        論理性 {_score_bar(ans.get("logical_coherence",0))}
                        回避度 {_score_bar(ans.get("evasiveness",0))}
                    </div>
                    <div class="qa-rationale">{ans.get("rationale","")}</div>
                </div>
            </div>'''
    else:
        qa_html = '<p class="small">個別QAデータが含まれていません。バッチを再実行してください。</p>'

    return _page(f"{member_name} — {data.get('meeting_name','')}", f"""
    <a href="/detail?file={fname}" class="back">&larr; {data.get('meeting_name','')}に戻る</a>
    <h1>{member_name}</h1>
    <p class="sub"><span class="party-dot" style="background:{color}"></span>{ms['party']} — {data['date']} {data.get('house','')} {data.get('meeting_name','')}</p>
    <div class="stats">
        <div class="stat"><div class="stat-val">{ms.get('overall_score',0):.0f}</div><div class="stat-label">総合スコア</div></div>
        <div class="stat"><div class="stat-val">{ms.get('avg_substantiveness',0):.0f}</div><div class="stat-label">本質性</div></div>
        <div class="stat"><div class="stat-val">{ms.get('avg_specificity',0):.0f}</div><div class="stat-label">具体性</div></div>
        <div class="stat"><div class="stat-val">{ms.get('topic_relevance_rate',0):.0f}%</div><div class="stat-label">議題関連率</div></div>
        <div class="stat"><div class="stat-val">{ms.get('question_count',0)}</div><div class="stat-label">質問数</div></div>
    </div>
    <h2>個別質疑の評価</h2>
    {qa_html}
    """)


# ============================================================
# 議事全文
# ============================================================

def _render_transcript(data: dict) -> str:
    fname = data.get("_file", "")
    speeches = data.get("speeches", [])

    if not speeches:
        return _page("議事全文", f'''
        <a href="/detail?file={fname}" class="back">&larr; 戻る</a>
        <h1>議事全文</h1>
        <p class="small">全文データが含まれていません。<code>--force</code> でバッチを再実行してください。</p>
        ''')

    house = data.get("house", "")
    speech_html = ""
    for s in speeches:
        pos = s.get("speaker_position", "")
        role = s.get("speaker_role", "")
        label = pos or role or ""
        group = s.get("speaker_group", "")
        color = _party_color(group.split("・")[0]) if group else "#5a6a7a"

        speech_html += f'''
        <div class="speech">
            <div class="speech-header">
                <span class="speech-num">#{s.get("order","")}</span>
                <strong>{s.get("speaker","")}</strong>
                {"<span class='small'>（" + label + "）</span>" if label else ""}
                {"<span class='party-dot' style='background:" + color + "'></span><span class='small'>" + group + "</span>" if group else ""}
            </div>
            <div class="speech-text">{s.get("text","")}</div>
        </div>'''

    return _page(f"議事全文 — {data['date']} {house} {data.get('meeting_name','')}", f"""
    <a href="/detail?file={fname}" class="back">&larr; スコアに戻る</a>
    <h1>{data['date']} {house} {data.get('meeting_name','')}</h1>
    <p class="sub">議事全文 — {len(speeches)}発言</p>
    {speech_html}
    """)


# ============================================================
# 政党詳細
# ============================================================

def _render_party(results: list[dict], party_name: str, session_filter: str = "") -> str:
    if session_filter:
        results = [r for r in results if str(r.get("session", "")) == session_filter]

    # 全結果から該当政党のデータを集約
    appearances = []
    member_totals: dict[str, dict] = defaultdict(lambda: {"scores": [], "questions": 0})

    for r in results:
        for ps in r.get("party_scores", []):
            if ps["party"] == party_name:
                appearances.append({
                    "date": r["date"], "house": r.get("house", ""),
                    "meeting": r.get("meeting_name", ""), "file": r.get("_file", ""),
                    "score": ps.get("overall_score", 0), "questions": ps.get("total_questions", 0),
                })
        for ms in r.get("member_scores", []):
            if ms.get("party") == party_name:
                m = member_totals[ms["name"]]
                m["scores"].append(ms.get("overall_score", 0))
                m["questions"] += ms.get("question_count", 0)
                m["party"] = party_name

    if not appearances:
        return _page("Not Found", '<a href="/" class="back">&larr;</a><h1>政党データなし</h1>')

    avg_score = sum(a["score"] for a in appearances) / len(appearances)
    total_q = sum(a["questions"] for a in appearances)
    color = _party_color(party_name)

    # 委員会別
    app_rows = ""
    for a in sorted(appearances, key=lambda x: x["date"], reverse=True):
        app_rows += f'''
        <tr onclick="location.href='/detail?file={a["file"]}'">
            <td>{a["date"]}</td>
            <td>{a["house"]} {a["meeting"]}</td>
            <td>{_score_bar(a["score"])}</td>
            <td class="num">{a["questions"]}問</td>
        </tr>'''

    # 所属議員
    mem_rows = ""
    for name, m in sorted(member_totals.items(), key=lambda x: sum(x[1]["scores"])/len(x[1]["scores"]) if x[1]["scores"] else 0, reverse=True):
        avg = sum(m["scores"]) / len(m["scores"]) if m["scores"] else 0
        mem_rows += f'''
        <tr>
            <td>{name}</td>
            <td>{_score_bar(avg)}</td>
            <td class="num">{m["questions"]}問</td>
            <td class="num">{len(m["scores"])}回</td>
        </tr>'''

    session_label = f"第{session_filter}回" if session_filter else "全回次"
    return _page(f"{party_name}", f"""
    <a href="/" class="back">&larr; 一覧に戻る</a>
    <h1><span class="party-dot" style="background:{color}"></span>{party_name}</h1>
    <p class="sub">{session_label} | {len(appearances)}委員会に参加 | 計{total_q}問</p>
    <div class="stats">
        <div class="stat"><div class="stat-val">{avg_score:.0f}</div><div class="stat-label">平均スコア</div></div>
        <div class="stat"><div class="stat-val">{total_q}</div><div class="stat-label">総質問数</div></div>
        <div class="stat"><div class="stat-val">{len(member_totals)}</div><div class="stat-label">議員数</div></div>
    </div>
    <h2>委員会別スコア</h2>
    <table>
        <thead><tr><th>日付</th><th>委員会</th><th>スコア</th><th>質問数</th></tr></thead>
        <tbody>{app_rows}</tbody>
    </table>
    <h2>所属議員</h2>
    <table>
        <thead><tr><th>議員</th><th>平均スコア</th><th>質問数</th><th>登場回数</th></tr></thead>
        <tbody>{mem_rows}</tbody>
    </table>
    """)


# ============================================================
# 議員プロフィール（横断まとめ）
# ============================================================

def _render_member_profile(results: list[dict], member_name: str) -> str:
    # 全結果からこの議員の出演データを収集
    timeline = []  # [{date, house, meeting, score, file, questions}, ...]
    party = ""

    for r in results:
        for ms in r.get("member_scores", []):
            if ms["name"] == member_name:
                if not party:
                    party = ms.get("party", "")
                timeline.append({
                    "date": r["date"],
                    "house": r.get("house", ""),
                    "meeting": r.get("meeting_name", ""),
                    "file": r.get("_file", ""),
                    "score": ms.get("overall_score", 0),
                    "q_quality": ms.get("avg_question_quality", 0),
                    "substantiveness": ms.get("avg_substantiveness", 0),
                    "specificity": ms.get("avg_specificity", 0),
                    "questions": ms.get("question_count", 0),
                    "relevance": ms.get("topic_relevance_rate", 0),
                    "session": r.get("session", 0),
                })

    if not timeline:
        return _page("Not Found", '<a href="/" class="back">&larr;</a><h1>議員データなし</h1>')

    timeline.sort(key=lambda t: t["date"])
    color = _party_color(party)
    avg_score = sum(t["score"] for t in timeline) / len(timeline)
    total_q = sum(t["questions"] for t in timeline)
    best = max(timeline, key=lambda t: t["score"])
    worst = min(timeline, key=lambda t: t["score"])

    # SVG 時系列チャート
    chart_svg = _build_timeline_chart(timeline)

    # 過去の質疑一覧
    history_rows = ""
    for t in reversed(timeline):
        house_badge = "衆" if "衆" in t["house"] else "参"
        house_cls = "shu" if house_badge == "衆" else "san"
        score_color = "#27ae60" if t["score"] >= 70 else "#f39c12" if t["score"] >= 50 else "#e74c3c"
        history_rows += f'''
        <tr onclick="location.href='/member?name={quote(member_name)}&file={t["file"]}'">
            <td>{t["date"]}</td>
            <td class="small">第{t["session"]}回</td>
            <td><span class="badge {house_cls}">{house_badge}</span> {t["meeting"]}</td>
            <td><span style="color:{score_color};font-weight:bold">{t["score"]:.0f}</span></td>
            <td>{_score_bar(t["substantiveness"])}</td>
            <td>{_score_bar(t["specificity"])}</td>
            <td class="num">{t["questions"]}問</td>
            <td class="num">{t["relevance"]:.0f}%</td>
        </tr>'''

    return _page(f"{member_name}", f"""
    <a href="/" class="back">&larr; 一覧に戻る</a>
    <h1>{member_name}</h1>
    <p class="sub"><span class="party-dot" style="background:{color}"></span>{party}</p>
    <div class="stats">
        <div class="stat"><div class="stat-val">{avg_score:.0f}</div><div class="stat-label">平均スコア</div></div>
        <div class="stat"><div class="stat-val">{total_q}</div><div class="stat-label">総質問数</div></div>
        <div class="stat"><div class="stat-val">{len(timeline)}</div><div class="stat-label">登場委員会</div></div>
        <div class="stat"><div class="stat-val">{best["score"]:.0f}</div><div class="stat-label">最高スコア</div></div>
        <div class="stat"><div class="stat-val">{worst["score"]:.0f}</div><div class="stat-label">最低スコア</div></div>
    </div>

    <h2>スコア推移</h2>
    {chart_svg}

    <h2>質疑履歴</h2>
    <p class="small">行をクリックすると、その委員会での個別質疑評価が見られます</p>
    <table>
        <thead><tr><th>日付</th><th>回次</th><th>委員会</th><th>総合</th><th>本質性</th><th>具体性</th><th>質問数</th><th>関連率</th></tr></thead>
        <tbody>{history_rows}</tbody>
    </table>
    """)


def _build_timeline_chart(timeline: list[dict]) -> str:
    """SVG で時系列スコアチャートを描画"""
    if len(timeline) < 2:
        return '<p class="small">データが2件以上でチャートを表示します</p>'

    w, h = 800, 200
    pad_x, pad_y = 50, 20
    chart_w = w - pad_x * 2
    chart_h = h - pad_y * 2

    n = len(timeline)
    x_step = chart_w / max(n - 1, 1)

    points = []
    labels = []
    for i, t in enumerate(timeline):
        x = pad_x + i * x_step
        y = pad_y + chart_h - (t["score"] / 100 * chart_h)
        points.append(f"{x:.0f},{y:.0f}")
        color = "#27ae60" if t["score"] >= 70 else "#f39c12" if t["score"] >= 50 else "#e74c3c"
        labels.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="4" fill="{color}"/>')
        if i % max(1, n // 8) == 0 or i == n - 1:
            labels.append(f'<text x="{x:.0f}" y="{h - 2}" text-anchor="middle" fill="#8892b0" font-size="10">{t["date"][5:]}</text>')
            labels.append(f'<text x="{x:.0f}" y="{y - 8:.0f}" text-anchor="middle" fill="#e0e0e0" font-size="11" font-weight="bold">{t["score"]:.0f}</text>')

    polyline = f'<polyline points="{" ".join(points)}" fill="none" stroke="#64ffda" stroke-width="2"/>'

    # Y軸ガイド
    guides = ""
    for v in [25, 50, 75]:
        gy = pad_y + chart_h - (v / 100 * chart_h)
        guides += f'<line x1="{pad_x}" y1="{gy:.0f}" x2="{w - pad_x}" y2="{gy:.0f}" stroke="#1a2a3a" stroke-dasharray="4"/>'
        guides += f'<text x="{pad_x - 8}" y="{gy + 4:.0f}" text-anchor="end" fill="#5a6a7a" font-size="10">{v}</text>'

    return f'''<svg viewBox="0 0 {w} {h}" style="width:100%;max-width:{w}px;background:#1a2332;border-radius:8px;padding:8px;margin-bottom:16px;">
        {guides}{polyline}{"".join(labels)}
    </svg>'''


# ============================================================
# HTML テンプレート
# ============================================================

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
.stats {{ display:flex; gap:16px; margin:20px 0 32px; flex-wrap:wrap; }}
.stat {{ background:#1a2332; border-radius:8px; padding:14px 20px; text-align:center; min-width:100px; }}
.stat-val {{ font-size:1.8rem; font-weight:bold; color:#64ffda; }}
.stat-label {{ font-size:0.8rem; color:#8892b0; margin-top:4px; }}
table {{ width:100%; border-collapse:collapse; margin-bottom:24px; }}
thead {{ border-bottom:2px solid #2a3a4a; }}
th {{ text-align:left; padding:8px 10px; font-size:0.8rem; color:#8892b0; font-weight:600; }}
td {{ padding:8px 10px; border-bottom:1px solid #1a2a3a; font-size:0.85rem; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
td.small {{ font-size:0.75rem; color:#8892b0; max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
tr:hover {{ background:#1a2a3a; cursor:pointer; }}
.badge {{ display:inline-block; width:22px; height:22px; border-radius:50%; text-align:center; line-height:22px; font-size:0.7rem; font-weight:bold; color:#fff; }}
.badge.shu {{ background:#c0392b; }}
.badge.san {{ background:#2980b9; }}
.party-dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; vertical-align:middle; }}
.bar {{ display:inline-flex; align-items:center; gap:6px; }}
.bar .fill {{ height:7px; border-radius:4px; min-width:2px; width:60px; }}
.bar span {{ font-size:0.8rem; min-width:30px; text-align:right; font-variant-numeric:tabular-nums; }}
.tabs {{ display:flex; gap:8px; margin-bottom:20px; flex-wrap:wrap; }}
.tab {{ padding:6px 14px; border-radius:20px; text-decoration:none; color:#8892b0; background:#1a2332; font-size:0.85rem; }}
.tab:hover {{ background:#2a3342; }}
.tab.active {{ background:#64ffda; color:#0f1923; font-weight:600; }}
.perf-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(160px,1fr)); gap:12px; margin-bottom:24px; }}
.perf-card {{ background:#1a2332; border-radius:8px; padding:16px; text-align:center; text-decoration:none; color:inherit; display:block; transition:background .15s; }}
a.perf-card:hover {{ background:#243040; }}
.perf-score {{ font-size:2.2rem; font-weight:bold; }}
.perf-name {{ font-size:1rem; font-weight:600; margin:6px 0 2px; }}
.perf-party {{ font-size:0.8rem; color:#8892b0; }}
.perf-meta {{ font-size:0.75rem; color:#5a6a7a; margin-top:4px; }}
.score-badge {{ display:inline-block; padding:2px 8px; border-radius:10px; color:#fff; font-size:0.8rem; font-weight:bold; }}
.dup-badge {{ display:inline-block; padding:2px 8px; border-radius:10px; background:#e74c3c; color:#fff; font-size:0.75rem; }}
.qa-card {{ background:#1a2332; border-radius:8px; padding:20px; margin-bottom:16px; }}
.qa-header {{ margin-bottom:12px; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }}
.qa-num {{ font-weight:bold; color:#64ffda; }}
.qa-section {{ margin-bottom:16px; }}
.qa-section.answer {{ border-top:1px solid #2a3a4a; padding-top:12px; }}
.qa-label {{ font-size:0.85rem; font-weight:600; color:#8892b0; margin-bottom:6px; }}
.qa-text {{ font-size:0.85rem; line-height:1.6; color:#c0c0c0; margin-bottom:10px; white-space:pre-wrap; word-break:break-all; }}
.qa-scores {{ display:flex; gap:16px; flex-wrap:wrap; font-size:0.8rem; margin-bottom:6px; }}
.qa-rationale {{ font-size:0.8rem; color:#64ffda; font-style:italic; }}
.small {{ font-size:0.8rem; color:#8892b0; }}
.btn {{ display:inline-block; padding:8px 16px; background:#64ffda; color:#0f1923; border-radius:6px; text-decoration:none; font-weight:600; font-size:0.85rem; }}
.btn:hover {{ background:#52e0c4; }}
.speech {{ margin-bottom:16px; padding:12px 16px; background:#1a2332; border-radius:6px; border-left:3px solid #2a3a4a; }}
.speech-header {{ margin-bottom:6px; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }}
.speech-num {{ color:#5a6a7a; font-size:0.75rem; }}
.speech-text {{ font-size:0.85rem; line-height:1.7; color:#c0c0c0; white-space:pre-wrap; word-break:break-all; }}
</style>
</head><body>{body}
<footer style="margin-top:48px;padding-top:16px;border-top:1px solid #1a2a3a;color:#5a6a7a;font-size:0.75rem;">
GiinScore — AI による参考評価値
</footer>
</body></html>"""


# ============================================================
# HTTP Handler
# ============================================================

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/" or path == "":
            results = _load_results()
            session_filter = qs.get("session", [""])[0]
            html = _render_index(results, session_filter)
            self._respond(200, html)

        elif path == "/detail":
            html = self._load_and_render(qs, _render_detail)
            self._respond(200 if html else 404, html or _page("Not Found", "<h1>Not Found</h1>"))

        elif path == "/transcript":
            html = self._load_and_render(qs, _render_transcript)
            self._respond(200 if html else 404, html or _page("Not Found", "<h1>Not Found</h1>"))

        elif path == "/member":
            fname = qs.get("file", [None])[0]
            name = qs.get("name", [None])[0]
            if fname and name:
                filepath = RESULTS_DIR / fname
                if filepath.exists():
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    data["_file"] = fname
                    self._respond(200, _render_member(data, name))
                    return
            self._respond(404, _page("Not Found", "<h1>Not Found</h1>"))

        elif path == "/member_profile":
            name = qs.get("name", [None])[0]
            if name:
                results = _load_results()
                self._respond(200, _render_member_profile(results, name))
            else:
                self._respond(404, _page("Not Found", "<h1>Not Found</h1>"))

        elif path == "/party":
            party = qs.get("party", [None])[0]
            session = qs.get("session", [""])[0]
            if party:
                results = _load_results()
                self._respond(200, _render_party(results, party, session))
            else:
                self._respond(404, _page("Not Found", "<h1>Not Found</h1>"))

        else:
            self._respond(404, _page("Not Found", "<h1>Not Found</h1>"))

    def _load_and_render(self, qs, renderer):
        fname = qs.get("file", [None])[0]
        if fname:
            filepath = RESULTS_DIR / fname
            if filepath.exists():
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["_file"] = fname
                return renderer(data)
        return None

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
