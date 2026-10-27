import calendar
import html as _html
import json
import re as _re
from datetime import date as _date

with open('data_bundle.json', encoding='utf-8') as f:
    bundle = json.load(f)

# JSON 문자열 안의 "</"는 "<\/"로 바꿔 둔다. 세부내용에 "</script>"가 들어오면
# 브라우저가 거기서 script 블록을 끊어 화면 전체가 죽는다. "\/"는 JSON에서 "/"와 같다.
DATA_JSON = json.dumps(bundle, ensure_ascii=False).replace("</", "<\\/")

def won(n):
    return f"{n:,.0f}"

def esc(s):
    """CSV에서 온 문자열(세부내용·소분류 등)을 HTML에 넣기 전에 이스케이프.
    '<'가 들어오면 표가 깨지고, '&'·'>'는 그대로 출력되던 자리."""
    return _html.escape(str(s), quote=False)

def _attr(s):
    """속성값(title·data-*)에 넣을 때 — 따옴표까지 바꾼다."""
    return _html.escape(str(s), quote=True)

def _signed(n):
    """증감 금액 '+12,345' · '-12,345' · '±0'."""
    return f"+{won(n)}" if n > 0 else (won(n) if n < 0 else "±0")

kpi = bundle['kpi']
category = bundle['category']
months_included = bundle.get('months_included', [])

# 상단 KPI의 '월평균'은 총수입·총지출을 나눈 값이다. 그 총액은 진행 중인 달까지
# 포함하므로 분모도 전체 월수여야 한다. 예전에는 완성월수(= 전체-1)로 나눠서
# 월평균이 조용히 부풀려졌다. 완성월수 분모는 pipeline의 예상 고정지출(bundle['projection'])에만 있다.
N_MONTHS = len(months_included) or 1
def _ym(s):
    """'2026-09' → (2026, 9)"""
    y, m = s.split('-')[:2]
    return int(y), int(m)

def _range_label(months, unit="개월"):
    """기간 라벨. 연도를 넘어가면 양쪽에 연도를 붙인다.
    연도를 문자열로 박아두면 해가 바뀌는 순간 조용히 틀린 값이 나온다."""
    if not months:
        return ""
    (y0, m0), (y1, m1) = _ym(months[0]), _ym(months[-1])
    if y0 == y1:
        return f"{y0}.{m0:02d} — {m1:02d} ({len(months)}{unit})"
    return f"{y0}.{m0:02d} — {y1}.{m1:02d} ({len(months)}{unit})"

def _month_label(months):
    """'1~9월' 형태. 연도를 넘어가면 연도를 함께 표기한다."""
    if not months:
        return ""
    (y0, m0), (y1, m1) = _ym(months[0]), _ym(months[-1])
    if y0 == y1:
        return f"{m0}~{m1}월"
    return f"{y0}.{m0}월~{y1}.{m1}월"

period_label = _range_label(months_included) if months_included else ""

# 전월 대비 증감 · 고정 지출 vs 변동 지출 · 최대 지출 TOP 15 · 매달 예상 고정지출(목표 대비 실적 포함) ·
# 핵심 요약은 화면에서 뺐다(2026-10-27 사용자 요청). 계산·bundle 키(month_over_month · fixed_variable ·
# top_expenses · projection · fixed_vs_target 등)는 pipeline.py에 그대로 있다 — validate()가 그 값으로 정합을 본다.

# ---- 달 고르기 (2026-10-27 대시보드 전환) ----
# 화면의 숫자는 모두 여기(파이썬)서 bundle 값으로 만든다. 달마다 바뀌는 패널(한눈에 보기·카테고리별 지출·
# 거래 내역)은 '전체'와 달마다 미리 그려 두고(data-show), script는 보이기/숨기기·추이 차트·툴팁·테마·
# 거래 거르기만 한다 — script의 돈 계산(합·차·평균) 0. 처음엔 가장 최근 달이 골라져 있다(고른 달은 저장하지 않는다).

_monthly = bundle['monthly']
_labels = _monthly['labels']                 # pipeline이 연도를 넘으면 '2025.12월'처럼 연도를 붙인다
_heat = bundle['heatmap']
_tx = bundle['monthly_tx']
_LAST = len(months_included) - 1
_SEL = months_included[-1] if months_included else 'all'

def _cut_day():
    """가장 최근 달이 아직 안 끝났으면 그 달의 최신 날짜(일), 끝났으면 None.
    최신 날짜는 bundle['latest_update']('10월 27일' — pipeline이 CSV 최신 날짜로 적는다)."""
    m = _re.match(r"^(\d{1,2})월 (\d{1,2})일$", str(bundle.get('latest_update') or ''))
    if not m or not months_included:
        return None
    y, mo = _ym(months_included[-1])
    if int(m.group(1)) != mo:
        return None
    day = int(m.group(2))
    return day if day < calendar.monthrange(y, mo)[1] else None

CUT_DAY = _cut_day()

# 월평균 세로 줄(카테고리별 지출)은 끝난 달만 — 진행 중인 달이 없으면 전체 달
_DONE = list(range(_LAST)) if CUT_DAY and _LAST > 0 else list(range(len(months_included)))

def _scope_label(i):
    """패널 머리 칩·달 칩의 고른 달 글자. i=None은 전체."""
    if i is None:
        return f"{_month_label(months_included)} 전체"
    return _labels[i] + (f" · {CUT_DAY}일까지" if i == _LAST and CUT_DAY else "")

def _show(views):
    """달 고르기로 바뀌는 덩어리의 속성. views = 보이는 보기('all' 또는 'YYYY-MM') 목록."""
    return f' data-show="{" ".join(views)}"' + ('' if _SEL in views else ' hidden')

def _day(r):
    """monthly_tx 행 날짜('10/27')의 일."""
    return int(r['날짜'].split('/')[1])

ICON = {
    'check': '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    'dash': '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M4 8h8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    'alert': '<svg viewBox="0 0 18 18" fill="none" aria-hidden="true"><path d="M9 2.2l7.2 12.6H1.8L9 2.2z" fill="var(--warn-mark)"/><path d="M9 7v3.6" stroke="var(--surface)" stroke-width="1.8" stroke-linecap="round"/><circle cx="9" cy="12.9" r="1" fill="var(--surface)"/></svg>',
    'ok': '<svg viewBox="0 0 18 18" fill="none" aria-hidden="true"><circle cx="9" cy="9" r="8" fill="var(--good)"/><path d="M5.5 9.2l2.3 2.3 4.6-5" stroke="var(--surface)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    'auto': '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.5"/><path d="M8 2a6 6 0 000 12z" fill="currentColor"/></svg>',
}

def month_strip():
    chips = [f'<button type="button" class="mchip all" data-m="all" data-label="{_attr(_scope_label(None))}" '
             f'aria-pressed="{str(_SEL == "all").lower()}">전체</button>']
    for i, m in enumerate(months_included):
        chips.append(f'<button type="button" class="mchip" data-m="{m}" data-label="{_attr(_scope_label(i))}" '
                     f'aria-pressed="{str(_SEL == m).lower()}">{esc(_labels[i])}</button>')
    return "".join(chips)

def _sel_scope():
    return _scope_label(None if _SEL == 'all' else months_included.index(_SEL))

# ---- 한눈에 보기 ----

def _spark(values, hi=None):
    """작은 추이선(SVG). hi = 강조할 달 칸(점). 점은 SVG 밖 HTML이라 늘어나도 동그랗다."""
    w, h = 120, 30
    lo, top = min(values), max(values)
    span = (top - lo) or 1
    n = len(values)
    pts = [((k / (n - 1) * w) if n > 1 else w / 2, h - 3 - (v - lo) / span * (h - 6)) for k, v in enumerate(values)]
    d = "".join(f"{'L' if k else 'M'}{x:.1f},{y:.1f}" for k, (x, y) in enumerate(pts))
    svg = (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">'
           f'<path class="sp-area" d="{d}L{pts[-1][0]:.1f},{h}L{pts[0][0]:.1f},{h}Z"/>'
           f'<path class="sp-line" d="{d}"/></svg>')
    dot = '' if hi is None else f'<i class="sp-dot" style="left:{pts[hi][0] / w * 100:.1f}%;top:{pts[hi][1] / h * 100:.1f}%"></i>'
    return f'<div class="spark">{svg}{dot}</div>'

def _tile(label, value, sub, spark):
    return (f'<div class="tile"><div class="t-label">{label}</div><div class="t-value num">{value}</div>'
            f'<div class="t-sub">{sub}</div>{spark}</div>')

def _prev_base(i):
    """그 달 지출 증감 칩의 비교 기준 (금액, 기준 글자, 칩 글자). 첫 달이면 None.

    진행 중인 가장 최근 달은 지난달 같은 날짜까지(pipeline month_over_month의 same_day_cutoff — 실질 지출)와 견준다.
    month_over_month.items는 증감 큰 대분류 10개만 싣는다(pipeline mom[:10]). 10개 미만이면 빠진 것이 없어 그 '전월'
    합이 그대로 기준이다. 잘렸으면 실린 대분류는 그 '전월'을 쓰고, 빠진 대분류는 원장(monthly_tx)에서 같은 날짜
    규칙으로 — 지난달 heatmap 칸에서 그 날짜 뒤 지출 행을 뺀다(실질 지출 조정이 없는 대분류는 이것이 정확하다.
    조정이 있는 대분류(내여자)는 증감이 커서 대개 실려 있다)."""
    if i == 0:
        return None
    prev = _labels[i - 1]
    mom = bundle.get('month_over_month') or {}
    cut = mom.get('same_day_cutoff')
    if i == _LAST and CUT_DAY and cut and mom.get('prev') == months_included[i - 1]:
        items = {x['대분류']: x['전월'] for x in mom.get('items', [])}
        whole = len(items) < 10
        later = {}
        for r in _tx.get(months_included[i - 1], []):
            if r['구분'] == '지출' and _day(r) > cut:
                later[r['대분류']] = later.get(r['대분류'], 0) + r['금액']
        base = sum(items.get(c, 0) if whole or c in items else _heat['matrix'][k][i - 1] - later.get(c, 0)
                   for k, c in enumerate(_heat['categories']))
        return base, f"{prev} {cut}일까지", f"{prev} 같은 날짜까지보다"
    return _monthly['expense'][i - 1], prev, f"{prev}보다"

def _delta_chip(curr, base, text):
    """지출 증감 칩 — 늘면 나쁨 색 ▲, 줄면 좋음 색 ▼. 기준이 0원이면 칩 없음."""
    if not base:
        return ''
    ch = (curr - base) / base * 100
    if abs(ch) < 0.05:
        return f'<span class="delta flat">{text} 비슷해요</span>'
    up = ch > 0
    return (f'<span class="delta {"up-bad" if up else "down-good"}">{"▲" if up else "▼"} {abs(ch):.1f}% '
            f'<span>{text}</span></span>')

def _exp_count(m):
    """그 달 지출 행 수(원장 — 월별 전체 내역과 같은 행)."""
    return sum(1 for r in _tx.get(m, []) if r['구분'] == '지출')

def overview_views():
    inc, exp, net, sr = _monthly['income'], _monthly['expense'], _monthly['net'], _monthly['savings_rate']
    n_all = sum(_exp_count(m) for m in months_included)
    views = [f"""
      <div class="mv"{_show(['all'])}>
        <div class="hero-label">{_month_label(months_included)} 총 지출</div>
        <div class="hero-value num">{won(kpi['total_expense'])}<span class="won">원</span></div>
        <div class="hero-foot"><span class="muted small">월평균 {won(kpi['total_expense'] / N_MONTHS)}원 · 지출 {n_all}건</span></div>
        <div class="tiles">{_tile("총 수입", f"{won(kpi['total_income'])}원", f"월평균 {won(kpi['total_income'] / N_MONTHS)}원", _spark(inc))}{_tile("순잉여", f"{won(kpi['net'])}원", "누적 저축분", _spark(net))}{_tile("저축률", f"{kpi['savings_rate']}%", "순잉여 ÷ 총수입", _spark(sr))}</div>
      </div>"""]
    for i, m in enumerate(months_included):
        partial = f" · {CUT_DAY}일까지" if i == _LAST and CUT_DAY else ""
        foot = ""
        pb = _prev_base(i)
        if pb:
            base, base_label, text = pb
            foot += _delta_chip(exp[i], base, text)
            foot += f'<span class="muted small">{base_label} {won(base)}원</span>'
        foot += f'<span class="muted small">지출 {_exp_count(m)}건</span>'
        inc_sub = f"지난달보다 {_signed(inc[i] - inc[i - 1])}원" if i else "첫 달"
        views.append(f"""
      <div class="mv"{_show([m])}>
        <div class="hero-label">{esc(_labels[i])} 지출{partial}</div>
        <div class="hero-value num">{won(exp[i])}<span class="won">원</span></div>
        <div class="hero-foot">{foot}</div>
        <div class="tiles">{_tile("수입", f"{won(inc[i])}원", inc_sub, _spark(inc, i))}{_tile("순잉여", f"{won(net[i])}원", "남은 돈" if net[i] >= 0 else "모자란 돈", _spark(net, i))}{_tile("저축률", f"{sr[i]}%", "순잉여 ÷ 수입", _spark(sr, i))}</div>
      </div>""")
    return "".join(views)

def check_link():
    n = len(bundle.get('warnings', []))
    if n:
        return (f'<a class="check-link" href="#s13">{ICON["alert"]}<span>확인이 필요한 항목 <b>{n}건</b></span>'
                f'<span class="go">보기 →</span></a>')
    return f'<a class="check-link ok" href="#s13">{ICON["ok"]}<span>확인할 항목 없음</span><span class="go">보기 →</span></a>'

# ---- 계좌 잔고 ----

_visible_accounts = [a for a in bundle['accounts']['list'] if not a.get('hidden')]
_n_accounts = len(_visible_accounts)

def account_rows():
    """계좌마다 이름·설명·잔고 + 비중 막대. 감춘 계좌(HIDDEN_ACCOUNTS)는 pipeline이 hidden으로 표시해 보낸다.
    기준일·증감 부가 설명 줄은 사용자 요청으로 뺐다 (2026-09-18)."""
    total = sum(a['current_balance'] for a in _visible_accounts)
    rows = []
    for a in _visible_accounts:
        share = a['current_balance'] / total * 100 if total > 0 else 0
        rows.append(f"""
          <li class="acc"><div><div class="a-name">{esc(a['name'])}</div><div class="a-desc">{esc(a.get('desc', ''))}</div></div>
            <div class="a-val num">{won(a['current_balance'])}원</div>
            <div class="a-bar" aria-hidden="true"><i style="width:{max(0, min(100, share)):.1f}%"></i></div></li>""")
    return "".join(rows)

# ---- 커플통장 정산 ----

_cs = bundle.get('couple_settlement') or {}
_cs_rule = _cs.get('rule', {})

def _cs_md(iso):
    """'2026-10-23' → '10/23'"""
    _, m, d = (int(x) for x in iso.split('-'))
    return f"{m}/{d}"

def couple_settlement_html():
    """커플통장 정산. 계산·규칙 값은 pipeline.py가 bundle['couple_settlement']에 실어 보낸다
    (COUPLE_SETTLEMENT가 유일한 원본 — 여기에 대분류·계좌 이름을 적지 않는다).
    정산 받을 금액만 보이고 커플통장 잔고는 보이지 않는다(HIDDEN_ACCOUNTS)."""
    if not _cs:
        return '<p class="muted small">정산 정보 없음</p>'
    acct, dest = esc(_cs_rule['account']), esc(_cs_rule['receive_to'])
    recent = _cs.get('recent', [])
    if _cs['owed'] > 0:
        head = f"""
        <div class="muted small">정산 받을 금액</div>
        <div class="settle-state"><span class="pill warn">{ICON['dash']}받을 돈 있음</span><span class="big num">{won(_cs['owed'])}원</span></div>
        <div class="muted small settle-sub">{acct} → {dest}로 이체할 금액 · {esc(_cs['period'])} · {_cs['count']}건</div>"""
    else:
        last = (f"마지막 정산 {_cs_md(recent[0]['날짜'])} {won(recent[0]['금액'])}원"
                if recent else "정산 기록 없음")
        over = (f'<div class="settle-sub"><span class="pill warn">더 받음 {won(_cs["over"])}원</span></div>'
                if _cs.get('over') else '')
        head = f"""
        <div class="muted small">정산 받을 금액</div>
        <div class="settle-state ok">{ICON['ok']}<span class="settle-zero">정산할 금액 없음</span></div>
        <div class="muted small settle-sub">{last}</div>{over}"""

    days = []
    for d in _cs.get('days', []):
        rows = []
        for it in d['items']:
            n = f' <span class="muted">×{it["건수"]}</span>' if it['건수'] > 1 else ''
            part = (f' <span class="pill warn">일부 남음</span>'
                    f' <span class="muted settle-orig">원래 {won(it["일부"])}원</span>') if it.get('일부') else ''
            rows.append(f"""
            <tr><td class="settle-name">{esc(it['세부내용'])}{n}{part}</td><td class="num">{won(it['금액'])}원</td></tr>""")
        days.append(f"""
          <div class="settle-day">
            <div class="cc-head"><b>{_cs_md(d['날짜'])}</b><span class="muted num">{d['건수']}건 · {won(d['소계'])}원</span></div>
            <table class="mini-table"><tbody>{''.join(rows)}</tbody></table>
          </div>""")
    days_html = f'<div class="settle-days">{"".join(days)}</div>' if days else ''

    if recent:
        rec_rows = "".join(f"""
            <tr><td class="muted">{_cs_md(r['날짜'])}</td><td class="num">{won(r['금액'])}원</td></tr>"""
                           for r in recent)
        rec_html = f'<table class="mini-table"><tbody>{rec_rows}</tbody></table>'
    else:
        rec_html = '<p class="muted small">아직 정산 기록이 없어요</p>'
    return f"""{head}{days_html}
        <details class="more"><summary>최근 정산 {len(recent)}건</summary>{rec_html}</details>
        <details class="more"><summary>정산 규칙</summary><p class="small rule">{_cs_lede()}</p></details>"""

def _cs_lede():
    if not _cs_rule:
        return ""
    acct, dest = esc(_cs_rule['account']), esc(_cs_rule['receive_to'])
    return (f"{esc(_cs_rule['start'])}부터 '{esc(_cs_rule['대분류'])} / {esc(_cs_rule['소분류'])}' 지출 중 "
            f"{acct}에서 바로 내지 않은 것을 모아, {acct} → {dest} 이체로 받은 정산을 오래된 것부터 지운 "
            f"나머지예요. 남은 항목은 날짜별로, 같은 날 같은 내용은 한 줄로 묶었어요.")

# ---- 신한은행 고정지출 ----

def shinhan_rows():
    """목록 항목마다 예상·실제 + 상태 칩(일치 / ±차이 / 아직) · 합계 줄."""
    rows = []
    total_expected = 0
    total_actual = 0
    for r in bundle['shinhan_fixed']:
        total_expected += r['예상금액']
        total_actual += r['출금액']
        diff = r['출금액'] - r['예상금액']
        if not r['matched']:
            st = f'<span class="pill mute">{ICON["dash"]}아직</span>'
        elif diff == 0:
            st = f'<span class="pill good">{ICON["check"]}일치</span>'
        else:
            st = f'<span class="pill {"warn" if diff > 0 else "good"}">{"+" if diff > 0 else ""}{won(diff)}</span>'
        actual = f"{won(r['출금액'])}원" if r['matched'] else '–'
        rows.append(f"""
          <li class="fx"><span class="n">{esc(r['항목'])}</span><span class="r e num">{won(r['예상금액'])}원</span><span class="r a num">{actual}</span><span class="st">{st}</span></li>""")
    rows.append(f"""
          <li class="fx total"><span class="n">합계</span><span class="r e num">{won(total_expected)}원</span><span class="r a num">{won(total_actual)}원</span><span class="st"></span></li>""")
    return "".join(rows)

def shinhan_summary():
    items = bundle['shinhan_fixed']
    expected = sum(r['예상금액'] for r in items)
    actual = sum(r['출금액'] for r in items)
    paid = sum(1 for r in items if r['matched'])
    fill = min(100, actual / expected * 100) if expected else 0
    return (f'<div class="fx-summary"><span class="big num">{won(actual)}원</span>'
            f'<span class="muted small">예상 {won(expected)}원 중 출금 {paid}/{len(items)}개</span></div>'
            f'<div class="meter" aria-hidden="true"><div class="fill good" style="width:{fill:.1f}%"></div></div>')

# ---- 신용카드 실적 ----

def _man(n):
    """금액을 '40만원' 꼴로. 혜택 문구가 CARD_TARGETS를 따라가게 하려는 것."""
    return f"{n // 10000:,}만원"

def card_performance_cards():
    cp = bundle.get('card_performance', {})
    # 목표 금액은 pipeline.py의 CARD_TARGETS가 유일한 원본이다.
    # 문구에 "40만원"이라고 적어두면 목표를 바꿔도 화면만 옛날 숫자로 남는다.
    tgt = bundle.get('card_targets', {})
    blurbs = {
        "현대카드": f"네이버 현대카드 Edition3 · 전월실적 {_man(tgt.get('현대카드', 0))} 이상이면 네이버플러스 적립 5%(더블적립 이벤트 중엔 최대 15%)가 열려요.",
        "신한카드": f"신한카드 Mr.Life · 전월실적 {_man(tgt.get('신한카드', 0))} 이상 구간이면 월납/주말/Time 할인 한도가 각각 최대치(1만원·1만원·3만원)로 올라가요."
    }
    day = cp.get('day', 0)
    cards_html = []
    for c in cp.get('cards', []):
        over = c['pct'] >= 100
        expected = c.get('expected_pct', 0)
        on_pace = c.get('on_pace', False)
        if over:
            pill = f'<span class="pill good">{ICON["check"]}달성</span>'
        elif on_pace:
            pill = f'<span class="pill good">{ICON["check"]}순조</span>'
        else:
            pill = f'<span class="pill warn">{ICON["dash"]}페이스 부족</span>'
        fill_cls = 'good' if over or on_pace else 'warn'
        marker = '' if over else f'<div class="pace" style="left:calc({min(expected, 100)}% - 1px)"></div>'
        right = "목표 달성 완료" if over else f"남은 금액 {won(c['remaining'])}원"
        pace = '' if over else (f'<div class="muted small">{day}일차 기준 예상 {expected}% · 지금 {c["pct"]}% '
                                f'(세로 줄 = 오늘 예상)</div>')
        cards_html.append(f"""
          <div class="card">
            <div class="card-top"><span class="card-name">{esc(c['name'])}</span>{pill}</div>
            <div class="meter" role="img" aria-label="{_attr(c['name'])} 실적 {c['pct']}%, 오늘 예상 {expected}%"><div class="fill {fill_cls}" style="width:{min(c['pct'], 100)}%"></div>{marker}</div>
            <div class="card-nums"><span><b class="num">{won(c['spent'])}원</b> / {won(c['target'])}원</span><span>{right}</span></div>
            {pace}
            <div class="muted small blurb">{blurbs.get(c['name'], '')}</div>
          </div>""")
    return "".join(cards_html)

# ---- 카드사별 결제 내역 ----

def card_category_rows():
    blocks = []
    # 카드 목록도 CARD_TARGETS에서 온다. 여기에 이름을 적어두면 카드를 추가했을 때
    # 신용카드 실적에는 뜨는데 결제 내역에서만 조용히 빠진다.
    for card in bundle.get('card_category_detail', {}) or bundle.get('card_targets', {}):
        items = bundle.get('card_category_detail', {}).get(card, [])
        if not items:
            blocks.append(f"""
          <div class="cc"><div class="cc-head"><b>{esc(card)}</b></div>
            <p class="muted small">이번 달 결제 내역 없음</p></div>""")
            continue
        total = sum(i['금액'] for i in items)
        rows = "".join(f"""
              <tr><td class="d muted">{esc(i['날짜'])}</td><td class="s">{esc(i['소분류'])}</td><td>{esc(i['세부내용'])}</td><td class="num r">{won(i['금액'])}원</td></tr>"""
                       for i in items)
        blocks.append(f"""
          <div class="cc">
            <div class="cc-head"><b>{esc(card)}</b><span class="muted num">{len(items)}건 · {won(total)}원</span></div>
            <table class="cc-table"><tbody>{rows}</tbody></table>
          </div>""")
    return "".join(blocks)

def card_list_summary():
    """접힌 줄의 한 줄 요약 — 카드 수·건수만. 가맹점·금액은 펼쳐야 보인다(2026-10-27 사용자 요청).
    카드 목록은 card_category_rows()와 같은 곳(CARD_TARGETS)에서 온다."""
    detail = bundle.get('card_category_detail', {})
    cards = list(detail or bundle.get('card_targets', {}))
    return f"카드 {len(cards)}개 · {sum(len(detail.get(c, [])) for c in cards)}건"

def _card_month_label():
    m = bundle.get('card_performance', {}).get('month')
    return f"{_ym(m)[1]}월" if m else "이번 달"

def _shinhan_month_label():
    """pipeline이 실제로 집계한 달을 그대로 쓴다. 예전에는 표는 추적 시작 이후
    전체를 누적하면서 라벨만 최신 월이라 적혀 있었다."""
    m = bundle.get('shinhan_month')
    return f"{_ym(m)[1]}월" if m else "이번 달"

# ---- 카테고리별 지출 ----

_rs = bundle.get('real_spend') or {}
_rs_rule = _rs.get('rule', {})

def real_spend_note():
    """전체 보기 아래 참고 줄 — 내여자 실질 지출(정산 받은 데이트비 뺌 · 커플통장 이체 더함).
    값·이름은 pipeline.py가 bundle['real_spend']·['couple_settlement']에 실어 보낸다(COUPLE_REAL_SPEND가 원본)."""
    if not _rs_rule:
        return ""
    acct, src = esc(_rs_rule['account']), esc(_rs_rule['fund_from'])
    since = _cs_md(_rs_rule['start'])
    lines = [f"{esc(_rs_rule['target'][1])}: 내 카드·계좌로 낸 {won(_cs.get('target_total', 0))}원 중 "
             f"{acct}에서 정산 받은 {won(_rs['settled_total'])}원은 지출에서 뺐어요({since}부터)",
             f"{esc(_rs_rule['소분류'])}: {since}부터는 {src} → {acct} 이체 {won(_rs['transfer_total'])}원을 "
             f"지출로 셌어요(비고 '{esc(_rs_rule['memo'])}'이면 그 달로)"]
    if _rs.get('couple_paid_total'):
        lines.append(f"{acct}에서 바로 낸 {won(_rs['couple_paid_total'])}원은 이체로 이미 셌으니 지출에서 뺐어요")
    if _rs.get('pending'):
        lines.append(f"다음 달 몫 이체 {won(sum(p['금액'] for p in _rs['pending']))}원은 그 달에 셀게요")
    return (f'<div class="note rs-notes"><b>{esc(_rs_rule["대분류"])} 실질 지출</b>'
            + "".join(f"<p>{ln}</p>" for ln in lines) + '</div>')

def _real_spend_month_note(i):
    """그 달 보기 아래 참고 줄 — 그 달 실질 지출 조정(real_spend.*_by_month)이 있을 때만."""
    if not _rs_rule:
        return ""
    settled, xfer = _rs['settled_by_month'][i], _rs['transfer_by_month'][i]
    paid = _rs.get('couple_paid_by_month', [0] * len(months_included))[i]
    if not (settled or xfer or paid):
        return ""
    main = _rs_rule['대분류']
    v = _heat['matrix'][_heat['categories'].index(main)][i] if main in _heat['categories'] else 0
    acct = esc(_rs_rule['account'])
    parts = []
    if settled:
        parts.append(f"정산 받은 {esc(_rs_rule['target'][1])} {won(settled)}원 뺌")
    if xfer:
        parts.append(f"{acct} 이체 {won(xfer)}원 더함")
    if paid:
        parts.append(f"{acct}에서 바로 낸 {won(paid)}원 뺌")
    return f'<div class="note"><b>{esc(main)} 실질 지출 {won(v)}원</b> — {" · ".join(parts)}</div>'

def _month_subs(i, cat):
    """그 달 대분류의 소분류 줄 [(이름, 건수|None, 금액)]. 원장(monthly_tx) 지출 행을 소분류로 합하고,
    실질 지출 조정(정산 받은 몫 · 커플통장 이체 · 커플통장에서 바로 낸 몫)은 따로 줄로 붙인다 —
    줄 합 = 그 달 heatmap 칸(pipeline 실질 지출). 원장 행은 원래 행이라 조정 줄이 그 차이다."""
    subs = {}
    for r in _tx.get(months_included[i], []):
        if r['구분'] == '지출' and r['대분류'] == cat:
            s = subs.setdefault(r['소분류'] or '(미분류)', [0, 0])
            s[0] += 1
            s[1] += r['금액']
    rows = sorted(((k, n, v) for k, (n, v) in subs.items()), key=lambda x: -x[2])
    if _rs_rule:
        acct = _rs_rule['account']
        settled, xfer = _rs['settled_by_month'][i], _rs['transfer_by_month'][i]
        if cat == _rs_rule['target'][0] and settled:
            rows.append((f"정산 받은 몫({_rs_rule['target'][1]})", None, -settled))
        if cat == _rs_rule['대분류'] and xfer:
            rows.append((f"{acct} 이체(지출로 셈)", None, xfer))
        rest = _heat['matrix'][_heat['categories'].index(cat)][i] - sum(v for _, _, v in rows)
        if rest:
            paid = _rs.get('couple_paid_by_month', [0] * len(months_included))[i]
            rows.append((f"{acct}에서 바로 낸 몫(이체로 셈)" if paid else "실질 지출 조정", None, rest))
    return rows

_ADJ = ' class="adj"'
_SEL_CLS = ' class="sel"'

def _sub_table(rows):
    body = "".join(
        f'<tr{_ADJ if n is None else ""}><td>{esc(name)}</td>'
        f'<td class="cnt">{"" if n is None else f"{n}건"}</td><td class="r">{won(v)}원</td></tr>'
        for name, n, v in rows)
    return f'<div class="subs"><table class="num"><tbody>{body}</tbody></table></div>'

def _cat_row(name, v, scale, small, title, rows, avg=None):
    width = max(0, v) / scale * 100 if scale else 0
    tick = '' if avg is None else f'<span class="c-avg" style="left:calc({avg / scale * 100 if scale else 0:.2f}% - 1px)"></span>'
    return f"""
          <details class="cat" data-cat="{_attr(name)}"><summary title="{_attr(title)}"><span class="c-name">{esc(name)}</span>
            <span class="c-track" aria-hidden="true"><span class="c-bar" style="width:{width:.2f}%"></span>{tick}</span>
            <span class="c-val num">{won(v)}원<small>{small}</small></span></summary>{_sub_table(rows)}</details>"""

def category_views():
    views = []
    # 전체 — 대분류 합계·월평균·비중(bundle category)과 소분류 상세(bundle detail)
    scale = max([c['합계'] for c in category] + [0])
    rows = "".join(
        _cat_row(c['대분류'], c['합계'], scale, f"월평균 {won(c['월평균'])}원 · 비중 {c['비중']}%",
                 f"{c['대분류']} {won(c['합계'])}원 · 월평균 {won(c['월평균'])}원",
                 [(s['소분류'], s['건수'], s['합계']) for s in bundle['detail'].get(c['대분류'], [])])
        for c in category)
    views.append(f"""
      <div class="mv"{_show(['all'])}>
        <div class="legend"><span><i class="key k-exp"></i>{_month_label(months_included)} 합계</span><span class="muted">대분류를 누르면 소분류</span></div>
        <div class="cat-list">{rows}</div>{real_spend_note()}
      </div>""")
    # 달마다 — 그 달 heatmap 열 + 끝난 달 월평균 세로 줄 + 비중
    cats, mat = _heat['categories'], _heat['matrix']
    done_label = _month_label([months_included[j] for j in _DONE])
    for i, m in enumerate(months_included):
        total = sum(r[i] for r in mat)
        shown = sorted(((c, mat[k][i], sum(mat[k][j] for j in _DONE) / len(_DONE))
                        for k, c in enumerate(cats) if mat[k][i]), key=lambda x: -x[1])
        scale = max([max(v, a) for _, v, a in shown] + [0])
        rows = "".join(
            _cat_row(c, v, scale, f"비중 {v / total * 100 if total else 0:.1f}%",
                     f"{c} {won(v)}원 · 월평균 {won(a)}원", _month_subs(i, c), avg=a)
            for c, v, a in shown)
        partial = f"({CUT_DAY}일까지)" if i == _LAST and CUT_DAY else ""
        empty = '' if shown else '<p class="muted small">이 달은 지출이 없어요.</p>'
        views.append(f"""
      <div class="mv"{_show([m])}>
        <div class="legend"><span><i class="key k-exp"></i>{esc(_labels[i])}{partial}</span><span><i class="key tick"></i>월평균({done_label})</span></div>
        <div class="cat-list">{rows}</div>{empty}{_real_spend_month_note(i)}
      </div>""")
    return "".join(views)

# ---- 카테고리 × 월 ----

def _heat_label(v):
    """칸 글자 — 옛 화면(JS)과 같은 규칙: 1만 이상 '○만', 0보다 크면 '○천', 아니면 '–'. JS Math.round(반 올림)."""
    if v >= 10000:
        return f"{int(v / 10000 + 0.5)}만"
    if v > 0:
        return f"{int(v / 1000 + 0.5)}천"
    return "–"

def heatmap_table():
    """대분류 × 월 표. 색 진하기는 옛 화면처럼 대분류(줄)마다 그 줄의 가장 큰 달 기준이다.
    글자는 모든 칸이 본문 글자색 — 가장 진한 칸에서도 라이트·다크 둘 다 흰/검은 글자를 바꿔 쓰는 것보다 잘 읽힌다."""
    head = "".join(f'<th scope="col" data-col="{m}"{_SEL_CLS if m == _SEL else ""}><span>{esc(_heat["months"][i])}</span></th>'
                   for i, m in enumerate(months_included))
    body = []
    for k, cat in enumerate(_heat['categories']):
        row = _heat['matrix'][k]
        mx = max(row + [1])
        cells = []
        for i, v in enumerate(row):
            t = v / mx
            pct = round((0.12 + t * 0.75) * 100) if t > 0 else 0
            style = (f"background:color-mix(in oklab, var(--exp) {pct}%, var(--surface));" if pct
                     else "background:var(--surface-2);")
            m = months_included[i]
            cells.append(f'<td data-col="{m}"{_SEL_CLS if m == _SEL else ""} style="{style}" '
                         f'title="{_attr(cat)} · {_attr(_heat["months"][i])} · {won(v)}원">{_heat_label(v)}</td>')
        body.append(f'<tr><th class="row-h" scope="row">{esc(cat)}</th>{"".join(cells)}</tr>')
    return f'<thead><tr><th class="row-h" scope="col">대분류</th>{head}</tr></thead><tbody>{"".join(body)}</tbody>'

# ---- 확인이 필요한 항목 ----

def warning_items():
    """'확인이 필요한 항목'. pipeline.py의 warnings_for()가 낸 경고를 그대로 보여준다.

    예전에는 여기가 하드코딩 2줄(커플통장 월평균·일시 수입 조언)이었고 pipeline 경고는
    화면에 안 올라갔다(2026-09-06 #2·#3·#6). 커플통장 줄은 2026-09부터 커플통장이
    지출이 아니라 이체로 기록되면서 지표가 무의미해져 삭제했다(2026-09-26 사용자 지시).
    커플통장 등 감춘 계좌를 언급하는 경고는 pipeline 쪽(screen_warnings_for)에서 이미 뺐다.
    """
    warns = bundle.get('warnings', [])
    if not warns:
        return f'<ul class="warn-list"><li class="ok">{ICON["ok"]}<span>확인할 항목 없음 — 이번 CSV 집계에서 경고가 없었어요.</span></li></ul>'
    items = "".join(f"<li>{ICON['alert']}<span>{esc(w)}</span></li>" for w in warns)
    return f'<ul class="warn-list">{items}</ul>'

# ---- 거래 내역 ----

_WD = "월화수목금토일"

def _tx_row(r):
    """거래 한 줄 — 세부내용 · 대분류·소분류 · 고정/이체 표시 · 금액(수입은 + 와 좋음 색). data-*는 거르기·찾기용."""
    kind = r['구분']
    what = r['세부내용'] if r['세부내용'] != '-' else r['소분류']
    cat = esc(r['대분류']) + (f" · {esc(r['소분류'])}" if r['소분류'] not in ('', '-') else '')
    fixed = r['고정여부'] == '고정'
    tags = ('<span class="tag">고정</span>' if fixed else '') + ('<span class="tag">이체</span>' if kind == '이체' else '')
    cls, sign = {'수입': (' inc', '+'), '이체': (' xfer', '')}.get(kind, ('', ''))
    q = _attr(f"{r['세부내용']} {r['대분류']} {r['소분류']}".lower())
    return (f'<div class="txr" data-k="{_attr(kind)}"{" data-fx" if fixed else ""} data-q="{q}">'
            f'<div class="what">{esc(what)}{tags}</div><div class="amt num{cls}">{sign}{won(r["금액"])}원</div>'
            f'<div class="cat">{cat}</div></div>')

def ledger_blocks():
    """달마다 그 달 전체 행(이체 포함 · 업로드 원본 그대로)을 날짜별로 묶는다 — 최근 날짜가 위, 최근 달이 위.
    '전체'를 고르면 모든 달이 보인다(같은 덩어리를 data-show로)."""
    inc, exp, net = _monthly['income'], _monthly['expense'], _monthly['net']
    n_all = sum(len(_tx.get(m, [])) for m in months_included)
    out = [f"""
      <div class="ledger-sum"{_show(['all'])}><b>전체 · 총 {n_all}건</b><span class="{"good" if kpi['net'] >= 0 else "crit"}">순잉여 {"+" if kpi['net'] >= 0 else ""}{won(kpi['net'])}원</span><span class="muted">수입 {won(kpi['total_income'])}원 · 지출 {won(kpi['total_expense'])}원</span></div>"""]
    for i in range(len(months_included) - 1, -1, -1):
        m = months_included[i]
        y = _ym(m)[0]
        recs = _tx.get(m, [])
        days = {}
        for r in recs:
            days.setdefault(r['날짜'], []).append(r)
        day_html = []
        for d in sorted(days, reverse=True):
            mm, dd = (int(x) for x in d.split('/'))
            day_html.append(f"""
        <div class="day"><div class="day-h"><span>{mm}월 {dd}일 ({_WD[_date(y, mm, dd).weekday()]})</span><span class="num">{len(days[d])}건</span></div>{"".join(_tx_row(r) for r in days[d])}</div>""")
        out.append(f"""
      <div class="lm"{_show(['all', m])}>
        <div class="ledger-sum"><b>{esc(_labels[i])} · 총 {len(recs)}건</b><span class="{"good" if net[i] >= 0 else "crit"}">순잉여 {"+" if net[i] >= 0 else ""}{won(net[i])}원</span><span class="muted">수입 {won(inc[i])}원 · 지출 {won(exp[i])}원</span></div>{"".join(day_html)}
        <div class="empty" hidden>조건에 맞는 거래가 없어요.</div>
      </div>""")
    return "".join(out)


CSS = """
  :root {
    --page: #F3F2F4; --surface: #FFFFFF; --surface-2: #F7F6F8; --ink: #17151A; --ink-2: #4B4751; --muted: #706B76;
    --line: #E5E2E7; --axis: #C9C5CD; --accent: #A8172A; --accent-ink: #FFFFFF; --accent-soft: #F7E4E7; --accent-text: #A8172A;
    --exp: #C8364B; --inc: #2A78D6; --net: #17151A; --good: #0E7A3A; --good-bg: #E2F2E7; --warn: #8F5400; --warn-bg: #FBEFD6;
    --warn-mark: #E89B0C; --crit: #B4232F; --crit-bg: #FBE4E6; --tip-bg: #17151A; --tip-ink: #FFFFFF;
    --shadow: rgba(23, 21, 26, 0.16);
    --font: "IBM Plex Sans KR", system-ui, -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
    --radius: 14px;
    color-scheme: light;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --page: #0E0D10; --surface: #18171B; --surface-2: #201F24; --ink: #F2F0F4; --ink-2: #C8C4CD; --muted: #9A95A0;
      --line: #2B2930; --axis: #3D3B43; --accent: #E0566B; --accent-ink: #1B0A0E; --accent-soft: #3A1A21; --accent-text: #F48A9A;
      --exp: #E2586C; --inc: #3987E5; --net: #F2F0F4; --good: #5BCB86; --good-bg: #15301F; --warn: #F2B544; --warn-bg: #33270F;
      --warn-mark: #F2B544; --crit: #F27B85; --crit-bg: #3A1A1E; --tip-bg: #F2F0F4; --tip-ink: #17151A;
      --shadow: rgba(0, 0, 0, 0.5);
      color-scheme: dark;
    }
  }
  :root[data-theme="dark"] {
    --page: #0E0D10; --surface: #18171B; --surface-2: #201F24; --ink: #F2F0F4; --ink-2: #C8C4CD; --muted: #9A95A0;
    --line: #2B2930; --axis: #3D3B43; --accent: #E0566B; --accent-ink: #1B0A0E; --accent-soft: #3A1A21; --accent-text: #F48A9A;
    --exp: #E2586C; --inc: #3987E5; --net: #F2F0F4; --good: #5BCB86; --good-bg: #15301F; --warn: #F2B544; --warn-bg: #33270F;
    --warn-mark: #F2B544; --crit: #F27B85; --crit-bg: #3A1A1E; --tip-bg: #F2F0F4; --tip-ink: #17151A;
    --shadow: rgba(0, 0, 0, 0.5);
    color-scheme: dark;
  }

  * { box-sizing: border-box; }
  [hidden] { display: none !important; }
  html { -webkit-text-size-adjust: 100%; }
  @media (prefers-reduced-motion: no-preference) { html { scroll-behavior: smooth; } }
  body {
    margin: 0; background: var(--page); color: var(--ink); font-family: var(--font);
    font-size: 14px; line-height: 1.55; -webkit-font-smoothing: antialiased;
  }
  body::before {
    content: ""; position: fixed; inset: 0 0 auto; height: env(safe-area-inset-top, 0px); z-index: 30; background: var(--page);
  }
  .inner {
    max-width: 1180px; margin: 0 auto;
    padding-left: max(16px, env(safe-area-inset-left, 0px));
    padding-right: max(16px, env(safe-area-inset-right, 0px));
  }
  .num { font-variant-numeric: tabular-nums; }
  button { font: inherit; color: inherit; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 6px; }
  .muted { color: var(--muted); }
  .small { font-size: 12.5px; }
  .good { color: var(--good); }
  .crit { color: var(--crit); }
  p { margin: 0; }

  /* ---------- 머리 ---------- */
  .top { display: flex; align-items: center; justify-content: space-between; gap: 12px;
         padding-top: calc(env(safe-area-inset-top, 0px) + 20px); padding-bottom: 14px; }
  .brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
  .mark {
    flex: none; width: 36px; height: 36px; border-radius: 10px; background: var(--accent); color: var(--accent-ink);
    display: grid; place-items: center; font-weight: 700; font-size: 17px;
  }
  .brand h1 { margin: 0; font-size: 19px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.2; }
  .brand .sub { margin: 2px 0 0; color: var(--muted); font-size: 12.5px; word-break: keep-all; }
  .theme-btn {
    flex: none; display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--line); background: var(--surface);
    border-radius: 999px; padding: 6px 12px 6px 10px; font-size: 12.5px; color: var(--ink-2); cursor: pointer;
  }
  .theme-btn svg { width: 16px; height: 16px; }

  /* ---------- 달 고르기 ---------- */
  .months {
    position: sticky; top: env(safe-area-inset-top, 0px); z-index: 20;
    background: var(--page);
    background: color-mix(in srgb, var(--page) 90%, transparent);
    -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px);
    border-bottom: 1px solid var(--line);
  }
  .months-row { position: relative; display: flex; gap: 6px; overflow-x: auto; padding-block: 10px; scrollbar-width: none; }
  .months-row::-webkit-scrollbar { display: none; }
  .mchip {
    flex: none; border: 1px solid var(--line); background: var(--surface); color: var(--ink-2); border-radius: 999px;
    padding: 6px 13px; font-size: 13px; font-weight: 500; cursor: pointer; white-space: nowrap;
  }
  .mchip:hover { border-color: var(--axis); }
  .mchip[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
  .mchip.all { margin-right: 4px; }

  /* ---------- 그리드 ---------- */
  main { padding-block: 16px 64px; }
  .grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 16px; align-items: start; }
  .panel { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 18px 20px; min-width: 0;
           scroll-margin-top: calc(env(safe-area-inset-top, 0px) + 64px); }
  .span-7 { grid-column: span 7; }
  .span-5 { grid-column: span 5; }
  .span-12 { grid-column: span 12; }
  .stack { display: grid; gap: 16px; min-width: 0; }
  @media (max-width: 920px) { .span-7, .span-5 { grid-column: span 12; } }
  @media (min-width: 921px) {
    /* 위 네 덩어리는 두 줄기 — 왼쪽 한눈에 보기·카테고리별 지출, 오른쪽 잔고·카드·정산 묶음·월별 추이.
       가운데 줄을 1fr로 두면 긴 묶음이 첫 줄을 늘리지 않아 한눈에 보기 아래가 비지 않는다 */
    .grid { grid-template-rows: auto 1fr auto; }
    #overview { grid-column: 1 / 8; grid-row: 1; }
    .stack { grid-column: 8 / 13; grid-row: 1 / 3; }
    #s6 { grid-column: 1 / 8; grid-row: 2 / 4; }
    #s5 { grid-column: 8 / 13; grid-row: 3; }
  }
  @media (max-width: 480px) {
    .panel { padding: 16px; border-radius: 12px; }
    .grid, .stack { gap: 12px; }
  }
  .sec-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 6px 12px; margin-bottom: 14px; }
  .sec-head h2 { margin: 0; font-size: 15px; font-weight: 600; letter-spacing: -0.01em; }
  .scope { font-size: 12px; font-weight: 500; border-radius: 999px; padding: 2px 9px; }
  .scope.m { background: var(--accent-soft); color: var(--accent-text); white-space: nowrap; }
  .scope.a { background: var(--surface-2); color: var(--muted); border: 1px solid var(--line); }
  .lede { color: var(--muted); font-size: 12.5px; margin: -6px 0 12px; overflow-wrap: anywhere; }

  /* ---------- 한눈에 보기 ---------- */
  .hero-label { color: var(--ink-2); font-size: 13.5px; font-weight: 500; }
  .hero-value { font-size: clamp(34px, 8vw, 52px); font-weight: 600; letter-spacing: -0.025em; line-height: 1.15; margin: 4px 0 6px; }
  .hero-value .won { font-size: 0.5em; font-weight: 500; margin-left: 2px; color: var(--ink-2); }
  .delta { display: inline-flex; align-items: center; gap: 4px; font-size: 13px; font-weight: 500; border-radius: 999px; padding: 2px 9px; }
  .delta.up-bad { background: var(--crit-bg); color: var(--crit); }
  .delta.down-good { background: var(--good-bg); color: var(--good); }
  .delta.flat { background: var(--surface-2); color: var(--muted); }
  .hero-foot { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; }
  .tiles { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: 18px; }
  .tile { background: var(--surface-2); border-radius: 10px; padding: 12px 14px; min-width: 0; display: grid; gap: 2px; }
  .tile .t-label { font-size: 12.5px; color: var(--muted); }
  .tile .t-value { font-size: 19px; font-weight: 600; letter-spacing: -0.01em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .tile .t-sub { font-size: 12px; color: var(--muted); }
  .spark { position: relative; margin-top: 6px; width: 100%; height: 30px; }
  .spark svg { display: block; width: 100%; height: 100%; overflow: visible; }
  .sp-area { fill: var(--muted); opacity: 0.10; }
  .sp-line { fill: none; stroke: var(--muted); stroke-width: 1.5; vector-effect: non-scaling-stroke; stroke-linejoin: round; }
  .sp-dot { position: absolute; width: 9px; height: 9px; margin: -4.5px 0 0 -4.5px; border-radius: 50%;
            background: var(--accent); box-shadow: 0 0 0 2px var(--surface-2); }
  @media (max-width: 560px) {
    .tiles { grid-template-columns: 1fr; gap: 8px; }
    .tile { grid-template-columns: minmax(0, 1fr) auto; grid-template-areas: "l s" "v s" "u s"; column-gap: 12px; align-items: center; }
    .tile .t-label { grid-area: l; } .tile .t-value { grid-area: v; } .tile .t-sub { grid-area: u; }
    .spark { grid-area: s; width: 96px; margin-top: 0; }
  }
  .check-link {
    display: flex; align-items: center; gap: 10px; margin-top: 14px; padding: 10px 12px; border-radius: 10px;
    text-decoration: none; color: var(--ink); background: var(--warn-bg); font-size: 13px;
  }
  .check-link.ok { background: var(--good-bg); }
  .check-link svg { flex: none; width: 18px; height: 18px; }
  .check-link .go { margin-left: auto; color: var(--ink-2); font-weight: 500; white-space: nowrap; }

  /* ---------- 계좌 잔고 ---------- */
  .big { font-size: 28px; font-weight: 600; letter-spacing: -0.02em; line-height: 1.2; overflow-wrap: anywhere; }
  .acc-list { list-style: none; margin: 14px 0 0; padding: 0; display: grid; gap: 10px; }
  .acc { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px 12px; align-items: baseline; }
  .acc .a-name { font-size: 13.5px; font-weight: 500; overflow-wrap: anywhere; }
  .acc .a-desc { font-size: 12px; color: var(--muted); overflow-wrap: anywhere; }
  .acc .a-val { font-size: 14px; font-weight: 600; text-align: right; white-space: nowrap; }
  .acc .a-bar { grid-column: 1 / -1; height: 4px; border-radius: 2px; background: var(--surface-2); overflow: hidden; }
  .acc .a-bar i { display: block; height: 100%; border-radius: 2px; background: var(--ink-2); opacity: 0.55; }

  /* ---------- 신용카드 실적 ---------- */
  .cards { display: grid; gap: 18px; }
  .card { display: grid; gap: 2px; }
  .card-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .card-name { font-weight: 600; font-size: 14px; }
  .blurb { margin-top: 4px; overflow-wrap: anywhere; }
  .pill { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; font-weight: 600; border-radius: 999px; padding: 2px 8px; white-space: nowrap; }
  .pill svg { width: 12px; height: 12px; }
  .pill.good { background: var(--good-bg); color: var(--good); }
  .pill.warn { background: var(--warn-bg); color: var(--warn); }
  .pill.mute { background: var(--surface-2); color: var(--muted); }
  .meter { position: relative; height: 10px; border-radius: 5px; background: var(--surface-2); margin: 10px 0 6px; }
  .meter .fill { position: absolute; inset: 0 auto 0 0; border-radius: 5px; }
  .meter .fill.good { background: var(--good); }
  .meter .fill.warn { background: var(--warn-mark); }
  .meter .pace { position: absolute; top: -4px; bottom: -4px; width: 2px; background: var(--ink); border-radius: 1px; }
  .card-nums { display: flex; justify-content: space-between; gap: 4px 8px; font-size: 12.5px; color: var(--muted); flex-wrap: wrap; }
  .card-nums b { color: var(--ink); font-weight: 600; }

  /* ---------- 커플통장 정산 ---------- */
  .settle-state { display: flex; align-items: center; gap: 10px; margin-top: 2px; }
  .settle-state .big { font-size: 22px; }
  .settle-state svg { flex: none; width: 20px; height: 20px; }
  .settle-zero { font-size: 18px; font-weight: 600; color: var(--good); }
  .settle-sub { margin-top: 6px; overflow-wrap: anywhere; }
  .settle-days { display: grid; gap: 10px; margin-top: 12px; }
  .settle-name { overflow-wrap: anywhere; }
  .settle-orig { font-size: 11.5px; white-space: nowrap; }
  .rule { margin-top: 8px; color: var(--ink-2); overflow-wrap: anywhere; }
  details.more { margin-top: 12px; border-top: 1px solid var(--line); padding-top: 10px; }
  details.more > summary { cursor: pointer; list-style: none; font-size: 13px; color: var(--ink-2); display: flex; align-items: center; gap: 6px; }
  details.more > summary::-webkit-details-marker { display: none; }
  details.more > summary::before { content: ""; width: 7px; height: 7px; border-right: 1.5px solid currentColor; border-bottom: 1.5px solid currentColor; transform: rotate(-45deg); transition: transform .15s; }
  details.more[open] > summary::before { transform: rotate(45deg); }
  .mini-table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }
  .mini-table td { padding: 5px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
  .mini-table td:last-child { text-align: right; white-space: nowrap; padding-left: 12px; }
  .mini-table tr:last-child td { border-bottom: 0; }

  /* ---------- 카테고리별 지출 ---------- */
  .legend { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 12px; color: var(--muted); margin: -4px 0 12px; }
  .legend span { display: inline-flex; align-items: center; gap: 6px; }
  .key { display: inline-block; width: 10px; height: 10px; border-radius: 3px; }
  .key.k-inc { background: var(--inc); }
  .key.k-exp { background: var(--exp); }
  .key.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--net); }
  .key.tick { width: 2px; height: 12px; border-radius: 1px; background: var(--ink); }
  .cat-list { display: grid; gap: 2px; }
  details.cat > summary {
    list-style: none; cursor: pointer; display: grid; grid-template-columns: 84px minmax(0, 1fr) auto; gap: 12px; align-items: center;
    padding: 7px 6px; border-radius: 8px;
  }
  details.cat > summary::-webkit-details-marker { display: none; }
  details.cat > summary:hover, details.cat[open] > summary { background: var(--surface-2); }
  .c-name { font-size: 13.5px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .c-track { position: relative; height: 12px; }
  .c-bar { position: absolute; left: 0; top: 0; bottom: 0; background: var(--exp); border-radius: 0 4px 4px 0; min-width: 2px; }
  .c-avg { position: absolute; top: -3px; bottom: -3px; width: 2px; background: var(--ink); border-radius: 1px; }
  .c-val { text-align: right; font-size: 13.5px; font-weight: 600; white-space: nowrap; }
  .c-val small { display: block; font-size: 11.5px; font-weight: 400; color: var(--muted); }
  .subs { padding: 4px 6px 10px 102px; }
  .subs table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  .subs td { padding: 4px 0; overflow-wrap: anywhere; }
  .subs td.r { text-align: right; white-space: nowrap; }
  .subs td.cnt { color: var(--muted); width: 48px; text-align: right; padding-right: 12px; white-space: nowrap; }
  .subs tr.adj td { color: var(--ink-2); font-style: italic; }
  .note { margin-top: 12px; font-size: 12.5px; color: var(--ink-2); background: var(--surface-2); border-radius: 8px; padding: 9px 12px; overflow-wrap: anywhere; }
  .note p { margin-top: 4px; }
  @media (max-width: 480px) {
    details.cat > summary { grid-template-columns: 64px minmax(0, 1fr) auto; gap: 10px; }
    .subs { padding-left: 12px; }
  }

  /* ---------- 월별 수입·지출 ---------- */
  .chart { position: relative; width: 100%; min-height: 230px; }
  .chart svg { display: block; }
  .chart text { fill: var(--muted); font-size: 11px; font-family: var(--font); font-variant-numeric: tabular-nums; }
  .chart text.sel { fill: var(--accent-text); font-weight: 600; }
  .chart .band { fill: var(--accent-soft); }
  .chart .grid-l { stroke: var(--line); }
  .chart .axis-l { stroke: var(--axis); }
  .chart .b-inc { fill: var(--inc); }
  .chart .b-exp { fill: var(--exp); }
  .chart .net-l { fill: none; stroke: var(--net); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
  .chart .net-d { fill: var(--net); stroke: var(--surface); stroke-width: 2; }
  .chart .hit { cursor: pointer; fill: transparent; }
  .hint { margin-top: 8px; }
  .tip {
    position: fixed; z-index: 50; pointer-events: none; background: var(--tip-bg); color: var(--tip-ink); border-radius: 8px;
    padding: 8px 10px; font-size: 12px; line-height: 1.5; min-width: 150px; box-shadow: 0 6px 20px var(--shadow);
  }
  .tip b { display: block; font-size: 12.5px; margin-bottom: 2px; }
  .tip .row { display: flex; justify-content: space-between; gap: 14px; font-variant-numeric: tabular-nums; }
  .tip .row span:first-child { display: inline-flex; align-items: center; gap: 6px; }
  .tip .key.dot { background: var(--tip-ink); }

  /* ---------- 카테고리 × 월 ---------- */
  .scroll-x { overflow-x: auto; margin: 0 -4px; padding: 0 4px; }
  table.heat { border-collapse: separate; border-spacing: 2px; width: 100%; min-width: 620px; font-size: 12px; }
  table.heat th { font-weight: 500; color: var(--muted); padding: 4px 2px; text-align: center; white-space: nowrap; }
  table.heat th.row-h { text-align: left; color: var(--ink); font-weight: 500; position: sticky; left: 0; background: var(--surface); padding-right: 10px; z-index: 1; }
  table.heat th.sel span { background: var(--accent); color: var(--accent-ink); border-radius: 999px; padding: 1px 8px; }
  table.heat td { text-align: center; border-radius: 4px; padding: 6px 2px; font-variant-numeric: tabular-nums; white-space: nowrap; color: var(--ink); }
  table.heat td.sel { font-weight: 700; box-shadow: inset 0 0 0 1.5px var(--ink); }
  .ramp { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); margin-top: 10px; }
  .ramp i { display: block; width: 120px; height: 8px; border-radius: 4px; background: linear-gradient(90deg, color-mix(in oklab, var(--exp) 12%, var(--surface)), color-mix(in oklab, var(--exp) 87%, var(--surface))); }

  /* ---------- 신한은행 고정지출 ---------- */
  .fx-summary { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 12px; }
  .fx-summary .big { font-size: 22px; }
  .fx-list { list-style: none; margin: 14px 0 0; padding: 0; }
  .fx { display: grid; grid-template-columns: minmax(0, 1fr) 104px 104px 92px; gap: 10px; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--line); font-size: 13px; }
  .fx:last-child { border-bottom: 0; }
  .fx.head { color: var(--muted); font-size: 12px; padding-top: 0; }
  .fx.total { font-weight: 600; border-top: 1.5px solid var(--ink); }
  .fx .n { overflow-wrap: anywhere; }
  .fx .r { text-align: right; white-space: nowrap; }
  .fx .st { justify-self: end; }
  @media (max-width: 560px) {
    .fx { grid-template-columns: minmax(0, 1fr) auto; grid-template-areas: "n s" "e a"; row-gap: 2px; }
    .fx.head { display: none; }
    .fx .n { grid-area: n; font-weight: 500; } .fx .st { grid-area: s; }
    .fx .e { grid-area: e; text-align: left; color: var(--muted); font-size: 12px; }
    .fx .a { grid-area: a; font-size: 12.5px; }
    .fx .e::before { content: "예상 "; } .fx .a::before { content: "실제 "; color: var(--muted); font-weight: 400; }
  }

  /* ---------- 확인이 필요한 항목 ---------- */
  .warn-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
  .warn-list li { display: grid; grid-template-columns: 18px minmax(0, 1fr); gap: 10px; font-size: 13px; background: var(--surface-2); border-radius: 8px; padding: 10px 12px; overflow-wrap: anywhere; }
  .warn-list svg { width: 18px; height: 18px; margin-top: 1px; }

  /* ---------- 카드사별 결제 내역 (처음엔 접힘 · 열림 상태 저장 없음) ---------- */
  details.fold > summary {
    list-style: none; cursor: pointer; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; padding: 10px 12px; border-radius: 10px;
    background: var(--surface-2); font-size: 13.5px; font-weight: 500;
  }
  details.fold > summary::-webkit-details-marker { display: none; }
  details.fold > summary .plus { width: 18px; height: 18px; border-radius: 50%; border: 1.5px solid var(--ink-2); display: grid; place-items: center; font-size: 13px; line-height: 1; }
  details.fold > summary .plus::before { content: "+"; }
  details.fold[open] > summary .plus::before { content: "\\2212"; }
  details.fold .open-t, details.fold[open] .closed-t { display: none; }
  details.fold[open] .open-t { display: inline; }
  details.fold > summary .meta { margin-left: auto; color: var(--muted); font-weight: 400; font-size: 12.5px; }
  .card-cols { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-top: 14px; }
  @media (max-width: 720px) { .card-cols { grid-template-columns: 1fr; } }
  .cc { min-width: 0; }
  .cc-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; padding-bottom: 8px; border-bottom: 1px solid var(--line); }
  .cc-head b { font-size: 14px; }
  .cc-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .cc-table td { padding: 7px 4px; border-bottom: 1px solid var(--line); vertical-align: top; overflow-wrap: anywhere; }
  .cc-table tr:last-child td { border-bottom: 0; }
  .cc-table td.d { white-space: nowrap; width: 52px; }
  .cc-table td.s { color: var(--muted); }
  .cc-table td.r { text-align: right; white-space: nowrap; font-weight: 600; }
  @media (max-width: 480px) { .cc-table td.s { display: none; } }

  /* ---------- 거래 내역 ---------- */
  .ledger-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px; margin-bottom: 10px; }
  .seg { display: inline-flex; flex-wrap: wrap; background: var(--surface-2); border-radius: 9px; padding: 3px; gap: 2px; }
  .seg button { border: 0; background: transparent; padding: 5px 11px; border-radius: 7px; font-size: 12.5px; color: var(--ink-2); cursor: pointer; }
  .seg button[aria-pressed="true"] { background: var(--surface); color: var(--ink); font-weight: 600; box-shadow: 0 1px 2px var(--shadow); }
  .search { flex: 1 1 180px; min-width: 0; border: 1px solid var(--line); background: var(--surface); color: var(--ink); border-radius: 9px; padding: 7px 11px; font: inherit; font-size: 13px; }
  .ledger-sum { display: flex; flex-wrap: wrap; gap: 2px 12px; font-size: 12.5px; color: var(--muted); padding: 8px 2px; }
  .ledger-sum b { color: var(--ink); font-weight: 600; }
  .ledger-sum .good, .ledger-sum .crit { font-weight: 600; }
  .lm { margin-top: 10px; }
  .lm + .lm { border-top: 2px solid var(--line); padding-top: 6px; }
  .day { margin-top: 6px; }
  .day-h { display: flex; justify-content: space-between; font-size: 12px; color: var(--muted); padding: 6px 2px; border-bottom: 1px solid var(--line);
           position: sticky; top: calc(env(safe-area-inset-top, 0px) + 53px); background: var(--surface); }
  .txr { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px 12px; padding: 8px 2px; border-bottom: 1px solid var(--line); }
  .txr:last-child { border-bottom: 0; }
  .txr .what { font-size: 13.5px; font-weight: 500; overflow-wrap: anywhere; }
  .txr .cat { font-size: 12px; color: var(--muted); overflow-wrap: anywhere; }
  .txr .amt { font-size: 13.5px; font-weight: 600; text-align: right; white-space: nowrap; grid-row: span 2; align-self: center; }
  .txr .amt.inc { color: var(--good); }
  .txr .amt.xfer { color: var(--muted); font-weight: 500; }
  .tag { display: inline-block; font-size: 11px; border: 1px solid var(--line); border-radius: 4px; padding: 0 5px; margin-left: 4px; color: var(--muted); font-weight: 400; }
  .empty { padding: 24px 0; text-align: center; color: var(--muted); font-size: 13px; }

  footer { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 12px; color: var(--muted); font-size: 12px; padding-top: 16px; }
  @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
"""

# 테마를 그리기 전에 붙인다(다크를 고른 사람에게 밝은 화면이 번쩍이지 않게). 키는 이것 하나다.
THEME_BOOT = """try { var t = localStorage.getItem("hh-theme"); if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t); } catch (e) {}"""

# 화면 script — 달 바꾸기 · 추이 차트(DATA.monthly 값 그대로) · 툴팁 · 테마 · 거래 거르기/찾기(행 숨기기)뿐.
# 돈 계산(합·차·평균)은 하지 않는다 — 그 숫자는 모두 위 파이썬이 HTML에 넣었다(차트 눈금 계산만 예외).
SCRIPT = r"""
(function () {
  "use strict";
  var doc = document, root = doc.documentElement;
  function byId(id) { return doc.getElementById(id); }
  function each(sel, fn, el) { Array.prototype.forEach.call((el || doc).querySelectorAll(sel), fn); }

  /* ---- 테마: 자동(OS 설정) · 라이트 · 다크 ---- */
  var THEMES = ["system", "light", "dark"];
  var THEME_LABEL = { system: "자동", light: "라이트", dark: "다크" };
  var THEME_ICON = {
    system: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.5"/><path d="M8 2a6 6 0 000 12z" fill="currentColor"/></svg>',
    light: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="3" stroke="currentColor" stroke-width="1.5"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6L13 13M3 13l1.4-1.4M11.6 4.4L13 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
    dark: '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M13.5 9.6A5.6 5.6 0 016.4 2.5 5.6 5.6 0 1013.5 9.6z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>'
  };
  var theme = "system";
  try { theme = localStorage.getItem("hh-theme") || "system"; } catch (e) {}
  if (THEMES.indexOf(theme) < 0) theme = "system";
  var themeBtn = byId("themeBtn");
  function applyTheme() {
    if (theme === "system") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", theme);
    themeBtn.innerHTML = THEME_ICON[theme] + "<span>" + THEME_LABEL[theme] + "</span>";
    themeBtn.setAttribute("aria-label", "화면 테마: " + THEME_LABEL[theme] + " (누르면 바뀜)");
  }
  themeBtn.addEventListener("click", function () {
    theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
    try {
      if (theme === "system") localStorage.removeItem("hh-theme"); else localStorage.setItem("hh-theme", theme);
    } catch (e) {}
    applyTheme();
  });
  applyTheme();

  /* ---- 달 고르기: 미리 그려 둔 덩어리(data-show)를 보이기/숨기기 ---- */
  var strip = byId("monthStrip");
  var chips = Array.prototype.slice.call(strip.querySelectorAll(".mchip"));
  var months = [];
  chips.forEach(function (b) { if (b.getAttribute("data-m") !== "all") months.push(b.getAttribute("data-m")); });
  var sel = "all";
  chips.forEach(function (b) { if (b.getAttribute("aria-pressed") === "true") sel = b.getAttribute("data-m"); });
  function chipOf(key) { for (var k = 0; k < chips.length; k++) if (chips[k].getAttribute("data-m") === key) return chips[k]; return null; }
  function revealChip(b) {
    if (!b) return;
    var l = b.offsetLeft, r = l + b.offsetWidth;
    if (l < strip.scrollLeft + 8) strip.scrollLeft = Math.max(0, l - 8);
    else if (r > strip.scrollLeft + strip.clientWidth - 8) strip.scrollLeft = r - strip.clientWidth + 8;
  }
  function select(key) {
    var chip = chipOf(key);
    if (!chip) return;
    sel = key;
    chips.forEach(function (b) { b.setAttribute("aria-pressed", String(b === chip)); });
    each("[data-show]", function (el) { el.hidden = el.getAttribute("data-show").split(" ").indexOf(key) < 0; });
    each("[data-scope]", function (el) { el.textContent = chip.getAttribute("data-label"); });
    each("#heat [data-col]", function (c) { c.classList.toggle("sel", c.getAttribute("data-col") === key); });
    drawTrend();
    filterLedger();
    revealChip(chip);
  }
  strip.addEventListener("click", function (ev) {
    var b = ev.target.closest(".mchip");
    if (b) select(b.getAttribute("data-m"));
  });

  /* ---- 월별 수입·지출 차트 (DATA.monthly 그대로 · 한 축 · 고른 달 띠) ---- */
  var M = DATA.monthly, NM = M.labels.length;
  var trend = byId("trend"), tip = byId("tip");
  function won(n) { return Number(n).toLocaleString("ko-KR") + "원"; }
  function man(n) {
    if (n === 0) return "0";
    var a = Math.abs(n), s = n < 0 ? "-" : "";
    if (a >= 10000) return s + Math.round(a / 10000).toLocaleString("ko-KR") + "만";
    if (a >= 1000) return s + Math.round(a / 1000) + "천";
    return s + a;
  }
  function niceStep(x) {
    var p = Math.pow(10, Math.floor(Math.log10(x))), c = [1, 2, 2.5, 5, 10];
    for (var k = 0; k < c.length; k++) if (c[k] * p >= x) return c[k] * p;
    return 10 * p;
  }
  function bar(x, yTop, w, h, cls) {
    if (h <= 0.5) return "";
    var r = Math.min(4, w / 2, h);
    return '<path class="' + cls + '" d="M' + x + "," + (yTop + h) + "V" + (yTop + r) + "Q" + x + "," + yTop + " " + (x + r) + "," + yTop +
      "H" + (x + w - r) + "Q" + (x + w) + "," + yTop + " " + (x + w) + "," + (yTop + r) + "V" + (yTop + h) + 'Z"/>';
  }
  function drawTrend() {
    if (!NM) return;
    var W = Math.max(260, trend.clientWidth), H = 230, m = { l: 44, r: 6, t: 12, b: 26 };
    var pw = W - m.l - m.r, ph = H - m.t - m.b;
    var maxV = Math.max(1, Math.max.apply(null, M.income.concat(M.expense, M.net)));
    var minV = Math.min(0, Math.min.apply(null, M.net.concat(M.income, M.expense)));
    var step = niceStep(maxV / 4), kTop = Math.ceil(maxV / step), kBot = Math.floor(minV / step);
    var top = kTop * step, bot = kBot * step;
    function y(v) { return m.t + (top - v) / (top - bot) * ph; }
    var bw = pw / NM, barW = Math.max(3, Math.min(14, bw * 0.3)), si = months.indexOf(sel);
    var every = bw < 30 ? Math.ceil(30 / bw) : 1;
    var s = '<svg width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="월별 수입·지출 막대와 순잉여 선">';
    if (si >= 0) s += '<rect class="band" x="' + (m.l + si * bw + 1) + '" y="2" width="' + Math.max(0, bw - 2) + '" height="' + (H - 4) + '" rx="8"/>';
    for (var k = kBot; k <= kTop; k++) {
      var v = k * step;
      s += '<line class="' + (k === 0 ? "axis-l" : "grid-l") + '" x1="' + m.l + '" x2="' + (W - m.r) + '" y1="' + y(v) + '" y2="' + y(v) + '"/>';
      s += '<text x="' + (m.l - 6) + '" y="' + (y(v) + 4) + '" text-anchor="end">' + man(v) + "</text>";
    }
    var pts = [], i;
    for (i = 0; i < NM; i++) {
      var cx = m.l + bw * i + bw / 2;
      s += bar(cx - barW - 1, y(Math.max(0, M.income[i])), barW, y(0) - y(Math.max(0, M.income[i])), "b-inc");
      s += bar(cx + 1, y(Math.max(0, M.expense[i])), barW, y(0) - y(Math.max(0, M.expense[i])), "b-exp");
      if (i % every === 0 || i === si) {
        s += '<text x="' + cx + '" y="' + (H - 7) + '" text-anchor="middle"' + (i === si ? ' class="sel"' : "") + ">" +
          String(M.labels[i]).replace(/^\d{4}\./, "") + "</text>";
      }
      pts.push([cx, y(M.net[i])]);
    }
    s += '<path class="net-l" d="' + pts.map(function (p, k2) { return (k2 ? "L" : "M") + p[0] + "," + p[1]; }).join("") + '"/>';
    pts.forEach(function (p) { s += '<circle class="net-d" cx="' + p[0] + '" cy="' + p[1] + '" r="4"/>'; });
    for (i = 0; i < NM; i++) s += '<rect class="hit" data-i="' + i + '" x="' + (m.l + bw * i) + '" y="0" width="' + bw + '" height="' + H + '"/>';
    trend.innerHTML = s + "</svg>";
  }
  function showTip(i, x, yy) {
    var chip = chipOf(months[i]);
    tip.innerHTML = "<b>" + (chip ? chip.getAttribute("data-label") : M.labels[i]) + "</b>" +
      '<div class="row"><span><i class="key k-inc"></i>수입</span><span>' + won(M.income[i]) + "</span></div>" +
      '<div class="row"><span><i class="key k-exp"></i>지출</span><span>' + won(M.expense[i]) + "</span></div>" +
      '<div class="row"><span><i class="key dot"></i>순잉여</span><span>' + won(M.net[i]) + "</span></div>";
    tip.hidden = false;
    var tw = tip.offsetWidth, th = tip.offsetHeight, vw = root.clientWidth;
    tip.style.left = Math.min(vw - tw - 8, Math.max(8, x + 12)) + "px";
    tip.style.top = Math.max(8, yy - th - 12) + "px";
  }
  trend.addEventListener("pointermove", function (ev) {
    var h = ev.target.closest(".hit");
    if (h) showTip(+h.getAttribute("data-i"), ev.clientX, ev.clientY); else tip.hidden = true;
  });
  trend.addEventListener("pointerleave", function () { tip.hidden = true; });
  trend.addEventListener("click", function (ev) {
    var h = ev.target.closest(".hit");
    if (h) select(months[+h.getAttribute("data-i")]);
  });
  window.addEventListener("scroll", function () { tip.hidden = true; }, { passive: true });
  var lastW = 0;
  function onResize() { var w = trend.clientWidth; if (w !== lastW) { lastW = w; drawTrend(); } }
  if (window.ResizeObserver) new ResizeObserver(onResize).observe(trend);
  else window.addEventListener("resize", onResize);

  /* ---- 거래 내역 거르기·찾기 (행 숨기기만) ---- */
  var filt = "all", search = byId("txSearch"), count = byId("txCount");
  function filterLedger() {
    var needle = search.value.trim().toLowerCase(), shown = 0;
    each("#s14 .lm", function (blk) {
      var n = 0;
      each(".day", function (day) {
        var dn = 0;
        each(".txr", function (r) {
          var kind = r.getAttribute("data-k");
          var ok = (filt === "all" || (filt === "고정" ? r.hasAttribute("data-fx") : kind === filt)) &&
            (!needle || r.getAttribute("data-q").indexOf(needle) >= 0);
          r.hidden = !ok;
          if (ok) dn++;
        }, day);
        day.hidden = !dn;
        n += dn;
      }, blk);
      blk.querySelector(".empty").hidden = n > 0;
      if (!blk.hidden) shown += n;
    });
    count.textContent = filt !== "all" || needle ? "보이는 거래 " + shown + "건" : "";
  }
  each("#s14 .seg button", function (b) {
    b.addEventListener("click", function () {
      filt = b.getAttribute("data-f");
      each("#s14 .seg button", function (x) { x.setAttribute("aria-pressed", String(x === b)); });
      filterLedger();
    });
  });
  search.addEventListener("input", filterLedger);

  select(sel);
})();

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  });
}
"""

html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<link rel="icon" href="favicon.svg" type="image/svg+xml">
<link rel="icon" href="favicon.ico" sizes="any">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="manifest" href="manifest.json">
<meta name="theme-color" content="#A8172A">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="가계부">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>이관범 가계부 분석 · {_month_label(months_included)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{CSS}</style>
<script>{THEME_BOOT}</script>
</head>
<body>

<header class="inner top">
  <div class="brand">
    <span class="mark" aria-hidden="true">가</span>
    <div>
      <h1>가계부</h1>
      <p class="sub">{period_label} · {bundle.get('latest_update','')} 업데이트</p>
    </div>
  </div>
  <button id="themeBtn" class="theme-btn" type="button" aria-label="화면 테마: 자동 (누르면 바뀜)">{ICON['auto']}<span>자동</span></button>
</header>

<nav class="months" aria-label="달 고르기">
  <div class="inner months-row" id="monthStrip" role="group" aria-label="볼 달">{month_strip()}</div>
</nav>

<main class="inner">
  <div class="grid">

    <div id="overview" class="panel span-7" role="region" aria-labelledby="ovTitle">
      <div class="sec-head"><h2 id="ovTitle">한눈에 보기</h2><span class="scope m" data-scope>{_sel_scope()}</span></div>
      {overview_views()}
      {check_link()}
    </div>

    <div class="stack span-5">
      <section id="s1" class="panel" aria-labelledby="s1t">
        <div class="sec-head"><h2 id="s1t">계좌 잔고</h2><span class="scope a">{bundle.get('latest_update','')} 기준</span></div>
        <div class="muted small">총 잔고 · {_n_accounts}개 항목 합계</div>
        <div class="big num">{won(sum(a['current_balance'] for a in _visible_accounts))}원</div>
        <ul class="acc-list">{account_rows()}
        </ul>
      </section>

      <section id="s2" class="panel" aria-labelledby="s2t">
        <div class="sec-head"><h2 id="s2t">신용카드 실적</h2><span class="scope a">{_card_month_label()} · 매달 초기화</span></div>
        <div class="cards">{card_performance_cards()}
        </div>
      </section>

      <section id="s-couple" class="panel" aria-labelledby="sct">
        <div class="sec-head"><h2 id="sct">{esc(_cs_rule.get('account', ''))} 정산</h2><span class="scope a">{esc(_cs_rule.get('account', ''))} → {esc(_cs_rule.get('receive_to', ''))} · 오래된 것부터 정산</span></div>
        {couple_settlement_html()}
      </section>
    </div>

    <section id="s6" class="panel span-7" aria-labelledby="s6t">
      <div class="sec-head"><h2 id="s6t">카테고리별 지출</h2><span class="scope m" data-scope>{_sel_scope()}</span></div>
      {category_views()}
    </section>

    <section id="s5" class="panel span-5" aria-labelledby="s5t">
      <div class="sec-head"><h2 id="s5t">월별 수입·지출</h2><span class="scope a">{_month_label(months_included)}</span></div>
      <div class="legend">
        <span><i class="key k-inc"></i>수입</span>
        <span><i class="key k-exp"></i>지출</span>
        <span><i class="key dot"></i>순잉여</span>
      </div>
      <div class="chart" id="trend"></div>
      <p class="muted small hint">막대를 누르면 그 달로 바뀌어요.</p>
    </section>

    <section id="s9" class="panel span-12" aria-labelledby="s9t">
      <div class="sec-head"><h2 id="s9t">카테고리 × 월</h2><span class="scope a">진할수록 해당 월 지출이 큼</span></div>
      <div class="scroll-x"><table class="heat" id="heat">{heatmap_table()}</table></div>
      <div class="ramp"><span>적음</span><i></i><span>많음 (대분류마다 그 줄 기준)</span></div>
    </section>

    <section id="s4" class="panel span-7" aria-labelledby="s4t">
      <div class="sec-head"><h2 id="s4t">신한은행 고정지출 예상 vs 실제</h2><span class="scope a">{_shinhan_month_label()} 기준 · 매달 갱신</span></div>
      <p class="lede">신한은행 통장에서 빠져나가는 고정비 {len(bundle['shinhan_fixed'])}개 항목이에요. 관범님이 직접 정리해주신 목록 기준(합계 {won(bundle.get('shinhan_fixed_total',0))}원)이에요.</p>
      {shinhan_summary()}
      <ul class="fx-list">
        <li class="fx head"><span>고정지출</span><span class="r">금액(예상)</span><span class="r">출금액(실제)</span><span class="st">상태</span></li>{shinhan_rows()}
      </ul>
    </section>

    <section id="s13" class="panel span-5" aria-labelledby="s13t">
      <div class="sec-head"><h2 id="s13t">확인이 필요한 항목</h2><span class="scope a">{f"{len(bundle.get('warnings', []))}건" if bundle.get('warnings') else "없음"}</span></div>
      <p class="lede">이번 CSV를 집계하면서 pipeline이 낸 경고예요. 계산은 됐지만 값이 조용히 어긋날 수 있는 자리라 한 번 확인이 필요해요.</p>
      {warning_items()}
    </section>

    <section id="s3" class="panel span-12" aria-labelledby="s3t">
      <div class="sec-head"><h2 id="s3t">카드사별 결제 내역</h2><span class="scope a">{_card_month_label()} · 비고 태그 기준</span></div>
      <details class="fold">
        <summary><span class="plus" aria-hidden="true"></span><span class="closed-t">결제 내역 펼치기</span><span class="open-t">결제 내역 접기</span><span class="meta">{card_list_summary()}</span></summary>
        <div class="card-cols">{card_category_rows()}
        </div>
      </details>
    </section>

    <section id="s14" class="panel span-12" aria-labelledby="s14t">
      <div class="sec-head"><h2 id="s14t">거래 내역</h2><span class="scope m" data-scope>{_sel_scope()}</span></div>
      <p class="lede">업로드하신 CSV 행을 가공 없이 그대로 보여 줘요(이체 포함 — 이체는 계좌 잔고 계산에만 쓰여요). 최근 날짜가 위예요.</p>
      <div class="ledger-tools">
        <div class="seg" role="group" aria-label="거래 종류">
          <button type="button" data-f="all" aria-pressed="true">전체</button>
          <button type="button" data-f="지출" aria-pressed="false">지출</button>
          <button type="button" data-f="수입" aria-pressed="false">수입</button>
          <button type="button" data-f="이체" aria-pressed="false">이체</button>
          <button type="button" data-f="고정" aria-pressed="false">고정</button>
        </div>
        <input class="search" id="txSearch" type="search" placeholder="세부내용·분류 찾기" aria-label="거래 찾기" autocomplete="off">
        <span class="muted small" id="txCount" aria-live="polite"></span>
      </div>
      {ledger_blocks()}
    </section>

  </div>
  <footer>
    <div>이관범 가계부 · {period_label} 집계</div>
    <div>업로드 CSV 기준({bundle.get('latest_update','')}) · 관범 정리</div>
  </footer>
</main>

<div class="tip" id="tip" hidden></div>

<script>
const DATA = {DATA_JSON};
{SCRIPT}</script>
</body>
</html>
"""


def renumber_sections(doc):
    """{{REF:섹션id}} 상호참조를 섹션 등장 순번으로 해석한다.

    화면에는 섹션 번호를 보이지 않는다(2026-10-27 대시보드 전환 — 제목만). 예전에는 여기서 section-num
    01~N도 다시 매겼다. 본문에 '○번'을 직접 적지 않고 {{REF:}}를 쓰면 섹션을 옮겨도 따라온다.
    """
    ids = _re.findall(r'<section id="([^"]+)"', doc)
    id_to_num = {sid: i for i, sid in enumerate(ids, start=1)}

    def resolve(m):
        sid = m.group(1)
        if sid not in id_to_num:
            raise ValueError(f"상호참조 대상 섹션을 찾을 수 없습니다: {sid}")
        return str(id_to_num[sid])

    doc = _re.sub(r'\{REF:([^}]+)\}', resolve, doc)

    leftover = doc.count("{REF:")
    if leftover:
        raise ValueError(f"해석되지 않은 상호참조 {leftover}건")
    print(f"섹션 {len(ids)}개 · 상호참조 해석 완료")
    return doc


html = renumber_sections(html)

# 출력 경로: 환경 변수 HOUSEHOLD_OUT이 있으면 그 경로(이 PC의 scripts/pc_run.py가 회차 폴더를 넘긴다),
# 없으면 웹 경로 그대로. newline=''은 Windows에서 \n이 \r\n으로 바뀌어 공개판과 바이트가 달라지는 것을 막는다
# (리눅스에서는 바뀌는 것이 없다).
import os as _os
_out = _os.environ.get('HOUSEHOLD_OUT') or '/mnt/user-data/outputs/이관범_가계부_분석_리포트.html'
with open(_out, 'w', encoding='utf-8', newline='') as f:
    f.write(html)

print("done, length:", len(html))
