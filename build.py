import html as _html
import json

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

# ---- pre-render some static table rows (values only; layout/format via CSS) ----

_visible_accounts = [a for a in bundle['accounts']['list'] if not a.get('hidden')]
_n_accounts = len(_visible_accounts)

def _account_names():
    """계좌 이름 목록. ACCOUNTS_START가 유일한 원본이며 여기에 적지 않는다."""
    return " / ".join(a['name'].replace(' 통장', '')
                      for a in _visible_accounts)

def account_cards():
    accts = _visible_accounts
    cards = []
    # 기준일·증감 부가 설명 줄은 사용자 요청으로 뺐다 (2026-09-18).
    # 카드에는 계좌명과 현재 잔고만 보여준다.
    for a in accts:
        cards.append(f"""
        <div class="kpi-card">
          <div class="label">{a['name']}</div>
          <div class="value">{won(a['current_balance'])}원</div>
        </div>""")
    return "".join(cards)

_cs = bundle.get('couple_settlement') or {}
_cs_rule = _cs.get('rule', {})

def _cs_md(iso):
    """'2026-10-23' → '10/23'"""
    _, m, d = (int(x) for x in iso.split('-'))
    return f"{m}/{d}"

def couple_settlement_html():
    """02 커플통장 정산. 계산·규칙 값은 pipeline.py가 bundle['couple_settlement']에 실어 보낸다
    (COUPLE_SETTLEMENT가 유일한 원본 — 여기에 대분류·계좌 이름을 적지 않는다).
    정산 받을 금액만 보이고 커플통장 잔고는 보이지 않는다(HIDDEN_ACCOUNTS)."""
    if not _cs:
        return '<div class="card"><p class="muted" style="margin:0;">정산 정보 없음</p></div>'
    acct, dest = esc(_cs_rule['account']), esc(_cs_rule['receive_to'])
    recent = _cs.get('recent', [])
    if _cs['owed'] > 0:
        head = f"""
    <div class="card settle-card">
      <div class="settle-label">정산 받을 금액</div>
      <div class="settle-value">{won(_cs['owed'])}원</div>
      <div class="settle-sub">{acct} → {dest}로 이체할 금액 · {esc(_cs['period'])} · {_cs['count']}건</div>
    </div>"""
    else:
        last = (f"마지막 정산 {_cs_md(recent[0]['날짜'])} {won(recent[0]['금액'])}원"
                if recent else "정산 기록 없음")
        over = (f'<div class="settle-sub"><span class="badge warn">더 받음 {won(_cs["over"])}원</span></div>'
                if _cs.get('over') else '')
        head = f"""
    <div class="card settle-card">
      <div class="settle-label">정산 받을 금액</div>
      <div class="settle-value settle-zero">정산할 금액 없음</div>
      <div class="settle-sub">{last}</div>{over}
    </div>"""

    days = []
    for d in _cs.get('days', []):
        rows = []
        for it in d['items']:
            n = f' <span class="muted">×{it["건수"]}</span>' if it['건수'] > 1 else ''
            part = (f' <span class="badge warn">일부 남음</span>'
                    f' <span class="muted settle-orig">원래 {won(it["일부"])}원</span>') if it.get('일부') else ''
            rows.append(f"""
          <tr>
            <td class="settle-name">{esc(it['세부내용'])}{n}{part}</td>
            <td class="num strong settle-amt">{won(it['금액'])}원</td>
          </tr>""")
        days.append(f"""
      <div class="settle-day">
        <div class="card-list-head">
          <div class="card-list-title">{_cs_md(d['날짜'])}</div>
          <div class="muted card-list-meta">{d['건수']}건 · {won(d['소계'])}원</div>
        </div>
        <table><tbody>{''.join(rows)}</tbody></table>
      </div>""")
    days_html = f'<div class="card settle-days">{"".join(days)}</div>' if days else ''

    if recent:
        rec_rows = "".join(f"""
        <tr><td class="muted">{_cs_md(r['날짜'])}</td><td class="num">{won(r['금액'])}원</td></tr>"""
                           for r in recent)
        rec_html = f'<div class="card"><table><tbody>{rec_rows}</tbody></table></div>'
    else:
        rec_html = '<div class="card"><p class="muted" style="margin:0;">아직 정산 기록이 없어요</p></div>'
    return f"""{head}{days_html}
    <h3 class="sub-head">최근 정산</h3>
    {rec_html}"""

def _cs_lede():
    if not _cs_rule:
        return ""
    acct, dest = esc(_cs_rule['account']), esc(_cs_rule['receive_to'])
    return (f"{esc(_cs_rule['start'])}부터 '{esc(_cs_rule['대분류'])} / {esc(_cs_rule['소분류'])}' 지출 중 "
            f"{acct}에서 바로 내지 않은 것을 모아, {acct} → {dest} 이체로 받은 정산을 오래된 것부터 지운 "
            f"나머지예요. 남은 항목은 날짜별로, 같은 날 같은 내용은 한 줄로 묶었어요.")

def shinhan_rows():
    rows = []
    total_expected = 0
    total_actual = 0
    for r in bundle['shinhan_fixed']:
        total_expected += r['예상금액']
        total_actual += r['출금액']
        actual_str = won(r['출금액']) + '원' if r['matched'] else '<span class="muted">–</span>'
        diff = r['출금액'] - r['예상금액']
        diff_str = ''
        if r['matched'] and diff != 0:
            sign = '+' if diff > 0 else ''
            cls = 'red' if diff > 0 else 'green'
            diff_str = f' <span class="{cls}" style="font-size:11.5px;">({sign}{won(diff)})</span>'
        rows.append(f"""
        <tr>
          <td class="name">{r['항목']}</td>
          <td class="num muted">{won(r['예상금액'])}원</td>
          <td class="num strong">{actual_str}{diff_str}</td>
        </tr>""")
    rows.append(f"""
    <tr style="border-top:2px solid var(--ink);">
      <td class="name">합계</td>
      <td class="num muted">{won(total_expected)}원</td>
      <td class="num strong">{won(total_actual)}원</td>
    </tr>""")
    return "".join(rows)

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
    days_in_month = cp.get('days_in_month', 30)
    cards_html = []
    for c in cp.get('cards', []):
        pct = min(c['pct'], 100)
        over = c['pct'] >= 100
        bar_color = 'var(--green)' if over else 'var(--red)'
        status = f'<span class="badge" style="background:#EAF2EE;color:var(--green);">달성</span>' if over else f'<span class="muted card-mini-pct">{c["pct"]}%</span>'

        if over:
            pace_html = f'<div class="card-pace green">목표 달성 완료</div>'
        else:
            expected = c.get('expected_pct', 0)
            on_pace = c.get('on_pace', False)
            pace_cls = 'green' if on_pace else 'red'
            pace_word = '정상 페이스' if on_pace else '페이스 부족'
            marker = f'<div class="pace-marker" style="left:{min(expected,100)}%"></div>'
            pace_html = (f'<div class="card-pace {pace_cls}">{pace_word} '
                         f'<span class="muted">· {day}일차 기준 예상 {expected}% / 현재 {c["pct"]}% '
                         f'· 남은 금액 {won(c["remaining"])}원</span></div>')

        marker = ''
        if not over:
            marker = f'<div class="pace-marker" style="left:{min(c.get("expected_pct",0),100)}%"></div>'

        cards_html.append(f"""
        <div class="card">
          <div class="card-perf-head">
            <div class="card-perf-title">{c['name']}</div>
            {status}
          </div>
          <div class="card-perf-amount">{won(c['spent'])}원 <span class="muted card-perf-target">/ {won(c['target'])}원</span></div>
          <div class="bar-track pace-track" style="height:10px; margin:10px 0 6px;"><div class="bar-fill" style="width:{pct}%; background:{bar_color};"></div>{marker}</div>
          {pace_html}
          <div class="muted card-perf-blurb" style="margin-top:10px;">{blurbs.get(c['name'],'')}</div>
        </div>""")
    return "".join(cards_html)

def card_category_rows():
    blocks = []
    # 카드 목록도 CARD_TARGETS에서 온다. 여기에 이름을 적어두면 카드를 추가했을 때
    # 03번에는 뜨는데 04번에서만 조용히 빠진다.
    for card in bundle.get('card_category_detail', {}) or bundle.get('card_targets', {}):
        items = bundle.get('card_category_detail', {}).get(card, [])
        if not items:
            blocks.append(f"""
            <div class="card">
              <div class="card-list-title">{card}</div>
              <div class="muted card-perf-blurb">이번 달 결제 내역 없음</div>
            </div>""")
            continue
        total = sum(i['금액'] for i in items)
        rows = "".join(f"""
          <tr>
            <td class="num muted card-tx-date">{i['날짜']}</td>
            <td class="name">{esc(i['소분류'])}</td>
            <td>{esc(i['세부내용'])}</td>
            <td class="num strong card-tx-amt">{won(i['금액'])}원</td>
          </tr>""" for i in items)
        blocks.append(f"""
        <div class="card">
          <div class="card-list-head">
            <div class="card-list-title">{card}</div>
            <div class="muted card-list-meta">{len(items)}건 · {won(total)}원</div>
          </div>
          <table><tbody>{rows}</tbody></table>
        </div>""")
    return "".join(blocks)

def card_list_summary():
    """04번 접힌 줄의 한 줄 요약 — 카드 수·건수만. 가맹점·금액은 펼쳐야 보인다(2026-10-27 사용자 요청).
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

def monthly_tx_tabs_and_panels():
    months = sorted(bundle['monthly_tx'].keys())
    months_included = bundle.get('months_included', months)
    net_by_month = dict(zip(months_included, bundle.get('monthly', {}).get('net', [])))
    income_by_month = dict(zip(months_included, bundle.get('monthly', {}).get('income', [])))
    expense_by_month = dict(zip(months_included, bundle.get('monthly', {}).get('expense', [])))
    tabs = []
    panels = []
    for i, m in enumerate(months):
        mm = int(m.split('-')[1])
        active_tab = ' class="tx-tab active"' if i == 0 else ' class="tx-tab"'
        active_panel = ' active' if i == 0 else ''
        tabs.append(f'<button{active_tab} data-month="{m}" onclick="showTxMonth(\'{m}\')">{mm}월</button>')

        recs = bundle['monthly_tx'][m]
        rows = []
        cards = []
        for r in recs:
            if r['구분'] == '수입':
                sign_class = 'tx-income'
                sign = '+'
            elif r['구분'] == '이체':
                sign_class = 'tx-transfer'
                sign = '↔'
            else:
                sign_class = 'tx-expense'
                sign = '-'
            fixed_tag = f'<span class="tag fixed">고정</span>' if r['고정여부'] == '고정' else ('<span class="tag var">변동</span>' if r['구분']=='지출' else '')
            rows.append(f"""
            <tr>
              <td class="num muted">{r['날짜']}</td>
              <td class="{sign_class}">{r['구분']}</td>
              <td class="name">{esc(r['대분류'])}</td>
              <td class="muted">{esc(r['소분류'])}</td>
              <td>{esc(r['세부내용'])}</td>
              <td>{fixed_tag}</td>
              <td class="num {sign_class}">{won(r['금액'])}원</td>
            </tr>""")
            desc = r['세부내용'] if r['세부내용'] != '-' else r['소분류']
            cards.append(f"""
            <div class="tx-card">
              <div class="tx-card-top">
                <span class="tx-card-desc">{esc(desc)}</span>
                <span class="tx-card-amt {sign_class}">{sign}{won(r['금액'])}원</span>
              </div>
              <div class="tx-card-meta muted">{esc(r['대분류'])} · {esc(r['소분류'])} · {r['날짜']}</div>
            </div>""")
        net_val = net_by_month.get(m, 0)
        net_sign = '+' if net_val >= 0 else ''
        net_class = 'tx-income' if net_val >= 0 else 'tx-expense'
        inc_val = income_by_month.get(m, 0)
        exp_val = expense_by_month.get(m, 0)
        panels.append(f"""
        <div class="tx-panel{active_panel}" id="tx-{m}">
          <div class="tx-panel-head">
            <div class="tx-head-row1">
              <span class="tx-head-full">{mm}월 · 총 {len(recs)}건</span>
              <span class="tx-head-short">총 {len(recs)}건</span>
              <span class="tx-head-net {net_class}">순잉여 {net_sign}{won(net_val)}원</span>
            </div>
            <div class="tx-head-row2 muted">수입 {won(inc_val)}원 · 지출 {won(exp_val)}원</div>
          </div>
          <table class="tx-table-view">
            <thead><tr><th>날짜</th><th>구분</th><th>대분류</th><th>소분류</th><th>세부내용</th><th></th><th style="text-align:right;">금액</th></tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
          <div class="tx-card-view">{''.join(cards)}</div>
        </div>""")
    return "".join(tabs), "".join(panels)

_rs = bundle.get('real_spend') or {}
_rs_rule = _rs.get('rule', {})

def real_spend_note():
    """06번 내여자 펼침 아래 참고 줄 — 실질 지출(정산 받은 데이트비 뺌 · 커플통장 이체 더함).
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
    return '<div class="rs-notes muted">' + "".join(f"<p>{ln}</p>" for ln in lines) + '</div>'

def category_detail_accordions():
    header = """
    <div class="cd-row cd-header">
      <span></span>
      <span class="cd-rank cat-rank-col"></span>
      <span class="cd-name">대분류</span>
      <span class="cd-num">합계</span>
      <span class="cd-num cd-avg-col muted">월평균</span>
      <span class="cd-num muted">비중</span>
    </div>"""
    blocks = [header]
    for i, c in enumerate(category, start=1):
        name = c['대분류']
        subs = bundle['detail'][name]
        sub_rows = "".join(f"""
          <tr>
            <td class="name">{esc(s['소분류'])}</td>
            <td class="num muted">{s['건수']}건</td>
            <td class="num">{won(s['합계'])}원</td>
          </tr>""" for s in subs)
        note = real_spend_note() if name == _rs_rule.get('대분류') else ""
        blocks.append(f"""
        <details class="detail-item">
          <summary class="cd-row cd-summary">
            <span class="cd-rank cat-rank-col">{i:02d}</span>
            <span class="cd-name name">{esc(name)}</span>
            <span class="cd-num num">{won(c['합계'])}원</span>
            <span class="cd-num cd-avg-col num muted">{won(c['월평균'])}원</span>
            <span class="cd-num num muted">{c['비중']}%</span>
          </summary>
          <table class="sub-table">
            <tbody>{sub_rows}</tbody>
          </table>{note}
        </details>""")
    return "".join(blocks)


def warning_items():
    """09번 '확인이 필요한 항목'. pipeline.py의 warnings_for()가 낸 경고를 그대로 보여준다.

    예전에는 여기가 하드코딩 2줄(커플통장 월평균·일시 수입 조언)이었고 pipeline 경고는
    화면에 안 올라갔다(2026-09-06 #2·#3·#6). 커플통장 줄은 2026-09부터 커플통장이
    지출이 아니라 이체로 기록되면서 지표가 무의미해져 삭제했다(2026-09-26 사용자 지시).
    커플통장 등 감춘 계좌를 언급하는 경고는 pipeline 쪽(screen_warnings_for)에서 이미 뺐다.
    """
    warns = bundle.get('warnings', [])
    if not warns:
        return '<p class="muted" style="margin:0;">확인할 항목 없음 — 이번 CSV 집계에서 경고가 없었어요.</p>'
    items = "".join(f"<li>{esc(w)}</li>" for w in warns)
    return f'<ol class="action-list">{items}</ol>'


tx_tabs, tx_panels = monthly_tx_tabs_and_panels()

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
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>이관범 가계부 분석 · {_month_label(months_included)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@500;600;700&family=Noto+Sans+KR:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --paper: #F7F1E7;
    --paper-line: rgba(140,26,34,0.10);
    --ink: #241A17;
    --ink-muted: #8A7268;
    --red: #A8172A;
    --red-deep: #7A0F1E;
    --red-soft: #F3DEDD;
    --red-softer: #F8EAE8;
    --green: #2E6B52;
    --card: #FFFFFF;
    --border: #E7D9C9;
    --shadow: 0 1px 0 rgba(36,26,23,0.04);
  }}
  * {{ box-sizing: border-box; }}
  html {{ scroll-behavior: smooth; }}
  body {{
    margin: 0;
    background: var(--paper);
    background-image: repeating-linear-gradient(
      to bottom,
      transparent 0,
      transparent 34px,
      var(--paper-line) 34px,
      var(--paper-line) 35px
    );
    color: var(--ink);
    font-family: 'Noto Sans KR', sans-serif;
    font-variant-numeric: tabular-nums;
    line-height: 1.6;
  }}
  h1, h2, h3 {{ font-family: 'Noto Serif KR', serif; }}
  .wrap {{ max-width: 960px; margin: 0 auto; padding: 48px 24px 96px; }}

  /* ---------- Header ---------- */
  .masthead {{
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 24px;
    padding-bottom: 28px;
    border-bottom: 2px solid var(--ink);
    margin-bottom: 8px;
    position: relative;
  }}
  .masthead .titles .kicker {{
    font-size: 13px;
    letter-spacing: 0.04em;
    color: var(--red);
    font-weight: 600;
    margin-bottom: 10px;
  }}
  .masthead h1 {{
    font-size: 34px;
    font-weight: 700;
    margin: 0 0 8px;
    letter-spacing: -0.01em;
  }}
  .masthead .period {{
    color: var(--ink-muted);
    font-size: 14.5px;
  }}
  .seal {{
    flex: none;
    width: 78px; height: 78px;
    border-radius: 50%;
    border: 2.5px solid var(--red);
    color: var(--red);
    display: flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    font-family: 'Noto Serif KR', serif;
    font-weight: 700;
    font-size: 15px;
    line-height: 1.3;
    transform: rotate(-8deg);
    letter-spacing: 0.02em;
  }}

  /* ---------- KPI ---------- */
  .kpi-grid {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 1px;
    background: var(--border);
    border: 1px solid var(--border);
    margin: 32px 0 56px;
  }}
  .kpi-card {{
    background: var(--card);
    padding: 22px 20px;
  }}
  .kpi-card .label {{ font-size: 13px; color: var(--ink-muted); margin-bottom: 10px; }}
  .kpi-card .value {{ font-size: 26px; font-weight: 700; font-family: 'Noto Serif KR', serif; }}
  .kpi-card .value.red {{ color: var(--red); }}
  .kpi-card .value.green {{ color: var(--green); }}
  .kpi-card .sub {{ font-size: 12.5px; color: var(--ink-muted); margin-top: 6px; }}
  .kpi-card .sub.red {{ color: var(--red); font-weight: 600; }}
  .kpi-card .sub.green {{ color: var(--green); font-weight: 600; }}
  .kpi-card .sub.muted {{ color: var(--ink-muted); }}

  /* ---------- Section ---------- */
  section {{ margin: 64px 0; }}
  .section-head {{
    display: flex;
    align-items: baseline;
    gap: 14px;
    border-bottom: 1px solid var(--ink);
    padding-bottom: 12px;
    margin-bottom: 22px;
    flex-wrap: wrap;
  }}
  .section-num {{
    font-family: 'Noto Serif KR', serif;
    font-weight: 700;
    color: var(--red);
    font-size: 15px;
    min-width: 26px;
  }}
  .section-head h2 {{
    font-size: 20px;
    margin: 0;
    font-weight: 700;
  }}
  .section-head .note {{
    color: var(--ink-muted);
    font-size: 13.5px;
    margin-left: auto;
  }}
  .lede {{ color: var(--ink-muted); font-size: 14.5px; margin: -10px 0 20px; }}
  .sub-head {{
    font-size: 16.5px;
    font-weight: 700;
    margin: 34px 0 14px;
    padding-top: 22px;
    border-top: 1px solid var(--border);
  }}

  /* ---------- Card / Chart container ---------- */
  .card {{
    background: var(--card);
    border: 1px solid var(--border);
    padding: 24px;
    box-shadow: var(--shadow);
  }}
  .total-balance-card {{
    text-align: center;
    padding: 28px 24px;
    margin-bottom: 16px;
    background: linear-gradient(135deg, var(--red-deep), var(--red));
  }}
  .total-balance-label {{ color: rgba(255,255,255,0.75); font-size: 13px; margin-bottom: 8px; }}
  .total-balance-value {{
    color: #fff;
    font-family: 'Noto Serif KR', serif;
    font-weight: 700;
    font-size: clamp(22px, 7vw, 34px);
    word-break: keep-all;
  }}
  .settle-card {{ text-align: center; padding: 26px 20px; }}
  .settle-label {{ font-size: 13px; color: var(--ink-muted); margin-bottom: 8px; }}
  .settle-value {{
    color: var(--red);
    font-family: 'Noto Serif KR', serif;
    font-weight: 700;
    font-size: clamp(22px, 7vw, 34px);
    word-break: keep-all;
  }}
  .settle-value.settle-zero {{ color: var(--green); font-size: clamp(18px, 5vw, 24px); }}
  .settle-sub {{ font-size: 12.5px; color: var(--ink-muted); margin-top: 8px; word-break: keep-all; }}
  .settle-days {{ margin-top: 16px; }}
  .settle-day + .settle-day {{ margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--border); }}
  .settle-name {{ word-break: break-word; }}
  .settle-amt {{ white-space: nowrap; }}
  .settle-orig {{ font-size: 11.5px; white-space: nowrap; }}
  .card-perf-head {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; }}
  .card-perf-title {{ font-family: 'Noto Serif KR', serif; font-weight: 700; font-size: 16px; }}
  .card-perf-amount {{ font-size: 22px; font-weight: 700; font-family: 'Noto Serif KR', serif; margin-bottom: 4px; word-break: break-word; }}
  .card-perf-target {{ font-size: 14px; font-weight: 400; }}
  .card-perf-blurb {{ font-size: 12.5px; line-height: 1.5; }}
  .card-mini-pct {{ font-size: 12.5px; }}
  .card-list-head {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; }}
  .card-list-title {{ font-family: 'Noto Serif KR', serif; font-weight: 700; font-size: 15px; margin-bottom: 10px; }}
  .card-list-meta {{ font-size: 13px; }}
  .pace-track {{ position: relative; overflow: visible; }}
  .pace-marker {{
    position: absolute;
    top: -3px;
    width: 2px;
    height: 16px;
    background: var(--ink);
    opacity: 0.55;
  }}
  .card-pace {{ font-size: 12px; font-weight: 600; }}
  .card-pace.green {{ color: var(--green); }}
  .card-pace.red {{ color: var(--red); }}
  .card-pace .muted {{ font-weight: 400; }}
  .chart-box {{ position: relative; height: 280px; }}
  .chart-box.tall {{ height: 420px; }}
  .svg-chart {{ width: 100%; height: 100%; }}
  .svg-chart .bar-income {{ fill: #EAD9CE; }}
  .svg-chart .bar-expense {{ fill: #A8172A; }}
  .svg-chart .net-line {{ fill: none; stroke: #2E6B52; stroke-width: 2.5; }}
  .svg-chart .net-dot {{ fill: #2E6B52; }}
  .svg-chart .axis-line {{ stroke: #E7D9C9; stroke-width: 1; }}
  .svg-chart .axis-label {{ fill: #8A7268; font-size: 11px; font-family: 'Noto Sans KR', sans-serif; }}
  .svg-chart .month-label {{ fill: #8A7268; font-size: 12px; font-family: 'Noto Sans KR', sans-serif; }}
  .chart-legend {{ display: flex; gap: 20px; justify-content: flex-end; margin-bottom: 8px; font-size: 12.5px; color: var(--ink-muted); }}
  .chart-legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
  .chart-legend i {{ width: 10px; height: 10px; border-radius: 2px; display: inline-block; }}


  /* ---------- Table ---------- */
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th {{
    text-align: left;
    font-weight: 600;
    font-size: 12.5px;
    color: var(--ink-muted);
    padding: 8px 10px;
    border-bottom: 1px solid var(--ink);
  }}
  td {{ padding: 10px 10px; border-bottom: 1px solid var(--border); vertical-align: middle; }}
  tr:last-child td {{ border-bottom: none; }}
  td.name {{ font-weight: 600; }}
  td.num {{ text-align: right; font-weight: 600; }}
  td.num.muted {{ color: var(--ink-muted); font-weight: 400; }}
  td.num.strong {{ color: var(--red); font-weight: 700; }}
  .muted {{ color: var(--ink-muted); }}
  .red {{ color: var(--red); }}
  .green {{ color: var(--green); }}

  .bar-track {{ width: 100%; height: 7px; background: var(--red-softer); border-radius: 4px; overflow: hidden; }}
  .bar-fill {{ height: 100%; background: var(--red); border-radius: 4px; }}

  .badge {{
    display: inline-block;
    font-size: 11.5px;
    padding: 3px 9px;
    border-radius: 20px;
    background: var(--red-soft);
    color: var(--red-deep);
    font-weight: 600;
  }}
  .badge.warn {{ background: #FBEFD4; color: #8A5A0B; }}
  .tag {{ font-size: 11.5px; padding: 2px 8px; border-radius: 4px; font-weight: 600; }}
  .tag.fixed {{ background: var(--red-soft); color: var(--red-deep); }}
  .tag.var {{ background: #EAF2EE; color: var(--green); }}

  /* ---------- Income bars ---------- */

  /* ---------- Detail accordion ---------- */
  .detail-item {{ border-bottom: 1px solid var(--border); }}
  .detail-item:first-child {{ border-top: 1px solid var(--border); }}
  .detail-item summary {{
    list-style: none;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 14px 4px;
    cursor: pointer;
    font-size: 14px;
  }}
  .detail-item summary::-webkit-details-marker {{ display: none; }}
  .detail-item summary .name {{ font-weight: 600; }}
  .detail-item summary .num {{ font-weight: 700; font-family: 'Noto Serif KR', serif; }}
  .detail-item summary::before {{
    content: '+';
    display: inline-block;
    margin-right: 12px;
    color: var(--red);
    font-weight: 700;
    width: 12px;
  }}
  .detail-item[open] summary::before {{ content: '–'; }}
  .sub-table {{ margin: 0 0 16px 24px; width: calc(100% - 24px); }}
  .sub-table td {{ font-size: 14px; padding: 6px 10px; }}
  .rs-notes {{ margin: -6px 0 16px 34px; font-size: 12.5px; line-height: 1.55; word-break: keep-all; overflow-wrap: anywhere; }}
  .rs-notes p {{ margin: 0 0 4px; }}
  /* 04번 카드 목록 접기 — 처음엔 접힘(open 없음 · 열림 상태 저장 없음). 06번과 같은 .detail-item 줄 */
  .card-fold {{ border-top: 1px solid var(--border); }}
  .card-fold > summary .name {{ margin-right: auto; }}
  .card-fold .fold-open, .card-fold[open] .fold-closed {{ display: none; }}
  .card-fold[open] .fold-open {{ display: inline; }}
  .card-fold > .kpi-grid {{ margin: 4px 0 18px; }}

  /* ---------- Category + detail combined accordion ---------- */
  .cd-row {{
    display: grid;
    grid-template-columns: 16px 28px 1fr 110px 110px 60px;
    align-items: center;
    gap: 8px;
  }}
  .cd-header {{
    padding: 4px 4px 10px;
    border-bottom: 1px solid var(--ink);
    font-size: 12.5px;
    color: var(--ink-muted);
  }}
  .cd-rank {{ color: var(--red); font-weight: 700; font-family: 'Noto Serif KR', serif; font-size: 13px; }}
  .cd-num {{ text-align: right; }}
  .detail-item summary.cd-summary {{ display: grid; padding: 14px 4px; }}
  .detail-item summary.cd-summary::before {{ margin-right: 0; }}

  /* ---------- Heatmap ---------- */
  .heatmap-scroll {{ overflow-x: auto; }}
  table.heatmap {{ min-width: 720px; border-collapse: separate; border-spacing: 0; }}
  table.heatmap th, table.heatmap td {{ text-align: center; padding: 8px 6px; font-size: 12.5px; }}
  table.heatmap th.rowh, table.heatmap td.rowh {{ text-align: left; font-weight: 600; font-size: 13px; padding-left: 4px; }}
  .heat-cell {{ font-weight: 600; border-radius: 3px; }}

  /* ---------- Actions ---------- */
  .action-list {{ list-style: none; margin: 0; padding: 0; counter-reset: action; }}
  .action-list li {{
    counter-increment: action;
    display: flex; gap: 14px;
    padding: 14px 0; border-bottom: 1px solid var(--border);
    font-size: 14.5px;
  }}
  .action-list li:last-child {{ border-bottom: none; }}
  .action-list li::before {{
    content: counter(action, decimal-leading-zero);
    font-family: 'Noto Serif KR', serif;
    font-weight: 700;
    color: var(--red);
    flex: none;
  }}

  footer {{
    margin-top: 72px;
    padding-top: 20px;
    border-top: 2px solid var(--ink);
    font-size: 12.5px;
    color: var(--ink-muted);
    display: flex;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;
  }}

  /* ---------- Monthly transaction tabs ---------- */
  .tx-tabs {{
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 18px;
  }}
  .tx-tab {{
    font-family: 'Noto Serif KR', serif;
    font-weight: 600;
    font-size: 14px;
    padding: 9px 18px;
    background: var(--card);
    border: 1px solid var(--border);
    color: var(--ink-muted);
    cursor: pointer;
    transition: background 0.15s, color 0.15s, border-color 0.15s;
  }}
  .tx-tab:hover {{ border-color: var(--red); color: var(--red-deep); }}
  .tx-tab.active {{ background: var(--red); border-color: var(--red); color: #fff; }}
  .tx-panel {{ display: none; }}
  .tx-panel.active {{ display: block; }}
  .tx-panel-head {{
    font-size: 13px;
    color: var(--ink-muted);
    margin-bottom: 10px;
    position: sticky;
    top: 0;
    z-index: 3;
    background: var(--card);
    padding: 8px 4px;
  }}
  .tx-head-full {{ display: none; }}
  .tx-head-short {{ display: inline; }}
  .tx-head-net {{ display: inline; font-weight: 700; }}
  .tx-head-net.tx-income {{ color: var(--green); }}
  .tx-head-net.tx-expense {{ color: var(--red); }}
  .tx-head-row1 {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 8px;
  }}
  .tx-head-row2 {{ display: block; font-size: 11.5px; margin-top: 2px; }}
  .tx-panel table {{ font-size: 13px; }}
  .tx-panel td, .tx-panel th {{ padding: 7px 8px; }}
  td.tx-income {{ color: var(--green); font-weight: 600; }}
  td.tx-expense {{ color: var(--red); font-weight: 600; }}
  td.tx-transfer {{ color: #8A5A0B; font-weight: 600; }}
  .tx-card-view {{ display: none; }}
  .tx-card {{ padding: 12px 4px; border-bottom: 1px solid var(--border); }}
  .tx-card:last-child {{ border-bottom: none; }}
  .tx-card-top {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-bottom: 4px; }}
  .tx-card-desc {{ font-size: 14px; font-weight: 600; }}
  .tx-card-amt {{ font-size: 14px; font-weight: 700; white-space: nowrap; }}
  .tx-card-amt.tx-income {{ color: var(--green); }}
  .tx-card-amt.tx-expense {{ color: var(--red); }}
  .tx-card-amt.tx-transfer {{ color: #8A5A0B; }}
  .tx-card-meta {{ font-size: 12px; }}
  .tx-scroll {{ max-height: 640px; overflow-y: auto; overflow-x: auto; }}

  @media (max-width: 720px) {{
    .kpi-grid {{ grid-template-columns: repeat(2, 1fr) !important; }}
    .masthead {{ flex-direction: column; }}
    .seal {{
      position: absolute;
      top: 0; right: 0;
      align-self: unset;
      width: 60px; height: 60px;
      font-size: 12px;
    }}
    .masthead .titles {{ }}
    .masthead .titles .kicker {{ padding-right: 76px; }}
    .masthead h1 {{ padding-right: 76px; }}
    .masthead .period {{ word-break: keep-all; white-space: normal; }}
    table {{ font-size: 12.5px; }}
    .chart-box.tall {{ height: 260px; }}
    .sub-table td {{ font-size: 12.5px; }}
    .detail-item summary {{ font-size: 12.5px; }}
    .tx-panel table {{ font-size: 12.5px; }}
    .tx-scroll {{ max-height: 480px; }}
    .cd-row {{ grid-template-columns: 14px 24px 1fr 95px 95px 50px; gap: 6px; }}
    table.heatmap {{ min-width: 560px; }}
    table.heatmap th, table.heatmap td {{ padding: 6px 4px; font-size: 12px; }}
    table.heatmap th.rowh, table.heatmap td.rowh {{
      position: sticky;
      left: 0;
      z-index: 2;
      background: var(--card);
    }}
    .kpi-card .value {{ font-size: 20px; }}
    .kpi-card .label {{ font-size: 12px; }}
    .card-perf-title {{ font-size: 15px; }}
    .card-perf-amount {{ font-size: 19px; }}
    .card-perf-target {{ font-size: 13px; }}
    .card-list-title {{ font-size: 14px; }}
    .card-list-meta {{ font-size: 12.5px; }}
    .sub-rank-col {{ display: none; }}
    .sub-pct-col {{ display: none; }}
  }}

  @media (max-width: 480px) {{
    .kpi-grid {{ grid-template-columns: repeat(2, 1fr) !important; gap: 1px; }}
    .card-list-grid {{ grid-template-columns: 1fr !important; }}
    .wrap {{ padding: 32px 16px 64px; }}
    .masthead h1 {{ font-size: 28px; }}
    .kpi-card {{ padding: 14px 12px; }}
    .kpi-card .label {{ font-size: 11.5px; }}
    .kpi-card .value {{ font-size: 17px; }}
    .kpi-card .sub {{ font-size: 11px; }}
    .chart-box.tall {{ height: 230px; }}
    .card-tx-date {{ display: none; }}
    .cat-rank-col {{ display: none; }}
    .cd-avg-col {{ display: none; }}
    .cd-row {{ grid-template-columns: 14px 1fr 88px 52px; gap: 6px; }}
    .card-tx-amt {{ white-space: nowrap; font-size: 13px; }}
    .total-balance-card {{ padding: 20px 16px; }}
    .card-perf-title {{ font-size: 14px; }}
    .card-perf-amount {{ font-size: 17px; }}
    .card-perf-target {{ font-size: 12px; }}
    .card-list-title {{ font-size: 13.5px; }}
    .card-list-meta {{ font-size: 12px; }}
    .tx-scroll {{ max-height: 420px; }}
    table.heatmap {{ min-width: 460px; }}
    table.heatmap th, table.heatmap td {{ padding: 4px 3px; font-size: 11px; }}
    .heat-cell {{ min-width: 22px !important; padding: 3px 4px !important; }}
    table.heatmap th.rowh, table.heatmap td.rowh {{
      position: sticky;
      left: 0;
      z-index: 2;
      background: var(--card);
    }}
    .tx-table-view {{ display: none; }}
    .tx-card-view {{ display: block; }}
  }}
</style>
</head>
<body>
<div class="wrap">

  <header class="masthead">
    <div class="titles">
      <div class="kicker">가계부 분석 리포트</div>
      <h1>이관범 가계부</h1>
      <div class="period">집계 기간 <strong>{period_label}</strong> · {bundle.get('latest_update','')} 업데이트</div>
    </div>
    <div class="seal">가계부<br>분석</div>
  </header>

  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="label">총 수입</div>
      <div class="value">{won(kpi['total_income'])}원</div>
      <div class="sub">월평균 {won(kpi['total_income']/N_MONTHS)}원</div>
    </div>
    <div class="kpi-card">
      <div class="label">총 지출</div>
      <div class="value red">{won(kpi['total_expense'])}원</div>
      <div class="sub">월평균 {won(kpi['total_expense']/N_MONTHS)}원</div>
    </div>
    <div class="kpi-card">
      <div class="label">순 잉여</div>
      <div class="value green">{won(kpi['net'])}원</div>
      <div class="sub">누적 저축분</div>
    </div>
    <div class="kpi-card">
      <div class="label">저축률</div>
      <div class="value">{kpi['savings_rate']}%</div>
      <div class="sub">순잉여 ÷ 총수입</div>
    </div>
  </div>

  <section id="s1">
    <div class="section-head">
      <span class="section-num">01</span>
      <h2>계좌 잔고</h2>
      <span class="note">{bundle.get('latest_update','')} 기준 · {_account_names()} {_n_accounts}개 항목</span>
    </div>
    <div class="total-balance-card">
      <div class="total-balance-label">총 잔고 ({_n_accounts}개 항목 합계)</div>
      <div class="total-balance-value">{won(sum(a['current_balance'] for a in _visible_accounts))}원</div>
    </div>
    <div class="kpi-grid" style="grid-template-columns: repeat({min(_n_accounts, 4)},1fr);">
      {account_cards()}
    </div>
  </section>

  <section id="s-couple">
    <div class="section-head">
      <span class="section-num">02</span>
      <h2>{esc(_cs_rule.get('account', ''))} 정산</h2>
      <span class="note">{esc(_cs_rule.get('account', ''))} → {esc(_cs_rule.get('receive_to', ''))} · 오래된 것부터 정산</span>
    </div>
    <p class="lede">{_cs_lede()}</p>
    {couple_settlement_html()}
  </section>

  <section id="s2">
    <div class="section-head">
      <span class="section-num">03</span>
      <h2>신용카드별 실적 체크</h2>
      <span class="note">{_card_month_label()} · 매달 초기화</span>
    </div>
    <div class="kpi-grid" style="grid-template-columns: repeat(2,1fr);">
      {card_performance_cards()}
    </div>
  </section>

  <section id="s3">
    <div class="section-head">
      <span class="section-num">04</span>
      <h2>카드사별 결제 내역</h2>
      <span class="note">{_card_month_label()} · 비고 태그 기준</span>
    </div>
    <details class="detail-item card-fold">
      <summary><span class="name">결제 내역 <span class="fold-closed">펼치기</span><span class="fold-open">접기</span></span><span class="num muted">{card_list_summary()}</span></summary>
      <div class="kpi-grid card-list-grid" style="grid-template-columns: repeat(2,1fr);">
        {card_category_rows()}
      </div>
    </details>
  </section>

  <section id="s5">
    <div class="section-head">
      <span class="section-num">05</span>
      <h2>월별 수입·지출 추이</h2>
    </div>
    <div class="card">
      <div class="chart-legend">
        <span><i style="background:#EAD9CE"></i>수입</span>
        <span><i style="background:#A8172A"></i>지출</span>
        <span><i style="background:#2E6B52"></i>순잉여</span>
      </div>
      <div class="chart-box tall"><div class="svg-chart" id="chartMonthly"></div></div>
    </div>
  </section>

  <section id="s6">
    <div class="section-head">
      <span class="section-num">06</span>
      <h2>대분류별 지출 비중·소분류 상세</h2>
      <span class="note">클릭해서 소분류 펼치기</span>
    </div>
    <div class="card">{category_detail_accordions()}</div>
  </section>

  <section id="s9">
    <div class="section-head">
      <span class="section-num">07</span>
      <h2>카테고리 × 월 히트맵</h2>
      <span class="note">진할수록 해당 월 지출이 큼</span>
    </div>
    <div class="card">
      <div class="heatmap-scroll">
        <table class="heatmap" id="heatmapTable"></table>
      </div>
    </div>
  </section>

  <section id="s4">
    <div class="section-head">
      <span class="section-num">08</span>
      <h2>신한은행 고정지출 예상 vs 실제</h2>
      <span class="note">{_shinhan_month_label()} 기준 · 매달 갱신</span>
    </div>
    <p class="lede">신한은행 통장에서 빠져나가는 고정비 {len(bundle['shinhan_fixed'])}개 항목이에요. 관범님이 직접 정리해주신 목록 기준(합계 {won(bundle.get('shinhan_fixed_total',0))}원)이에요.</p>
    <div class="card" style="overflow-x:auto;">
      <table>
        <thead><tr><th>고정지출</th><th style="text-align:right;">금액(예상)</th><th style="text-align:right;">출금액(실제)</th></tr></thead>
        <tbody>{shinhan_rows()}</tbody>
      </table>
    </div>
  </section>

  <section id="s13">
    <div class="section-head">
      <span class="section-num">09</span>
      <h2>확인이 필요한 항목</h2>
    </div>
    <p class="lede">이번 CSV를 집계하면서 pipeline이 낸 경고예요. 계산은 됐지만 값이 조용히 어긋날 수 있는 자리라 한 번 확인이 필요해요. 경고가 없으면 "확인할 항목 없음"으로 표시돼요.</p>
    <div class="card">
      {warning_items()}
    </div>
  </section>

  <section id="s14">
    <div class="section-head">
      <span class="section-num">10</span>
      <h2>월별 전체 내역</h2>
      <span class="note">업로드 원본 그대로 · 수입·지출 전체</span>
    </div>
    <p class="lede">가공 없이 업로드하신 CSV에 기록된 거래를 월별로 그대로 볼 수 있는 원장이에요. 날짜순으로 정렬돼 있고, 수입은 초록색, 지출은 빨간색으로 구분돼요. 이체는 계좌 잔고 계산에만 반영되고 이 목록에도 함께 표시돼요.</p>
    <div class="card">
      <div class="tx-tabs">{tx_tabs}</div>
      <div class="tx-scroll">{tx_panels}</div>
    </div>
  </section>

  <footer>
    <div>이관범 가계부 · {period_label} 집계</div>
    <div>업로드 CSV 기준({bundle.get('latest_update','')}) · 관범 정리</div>
  </footer>
</div>

<script>
const DATA = {DATA_JSON};

function fmtMan(v) {{ return Math.round(v/10000).toLocaleString() + '만'; }}
function escHtml(s) {{ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }}

// ---- 05 월별 수입·지출 추이 (순수 SVG, 라이브러리 없음) ----
(function() {{
  const el = document.getElementById('chartMonthly');
  const W = el.clientWidth || 880, H = el.clientHeight || 420;
  const M = {{ top: 16, right: 16, bottom: 34, left: 52 }};
  const plotW = W - M.left - M.right;
  const plotH = H - M.top - M.bottom;

  const {{ labels, income, expense, net }} = DATA.monthly;
  const n = labels.length;
  const maxVal = Math.max(...income, ...expense, ...net, 0);
  const minVal = Math.min(...net, 0);
  const yMax = maxVal * 1.08;
  const yMin = minVal < 0 ? minVal * 1.15 : 0;
  const yScale = v => plotH - ((v - yMin) / (yMax - yMin)) * plotH;
  const zeroY = yScale(0);

  const groupW = plotW / n;
  const barW = groupW * 0.30;

  let bars = '';
  let gridLines = '';
  let yTicks = 5;
  for (let i = 0; i <= yTicks; i++) {{
    const v = yMin + (yMax - yMin) * (i / yTicks);
    const y = yScale(v);
    gridLines += `<line class="axis-line" x1="0" y1="${{y}}" x2="${{plotW}}" y2="${{y}}" />`;
    gridLines += `<text class="axis-label" x="-8" y="${{y+4}}" text-anchor="end">${{fmtMan(v)}}</text>`;
  }}

  let monthLabels = '';
  let netPoints = [];
  labels.forEach((lab, i) => {{
    const gx = i * groupW;
    const cx = gx + groupW/2;
    const incH = zeroY - yScale(income[i]);
    const expH = zeroY - yScale(expense[i]);
    bars += `<rect class="bar-income" x="${{cx - barW - 3}}" y="${{Math.min(yScale(income[i]),zeroY)}}" width="${{barW}}" height="${{Math.abs(incH)}}" rx="2"/>`;
    bars += `<rect class="bar-expense" x="${{cx + 3}}" y="${{Math.min(yScale(expense[i]),zeroY)}}" width="${{barW}}" height="${{Math.abs(expH)}}" rx="2"/>`;
    monthLabels += `<text class="month-label" x="${{cx}}" y="${{plotH + 22}}" text-anchor="middle">${{lab}}</text>`;
    netPoints.push([cx, yScale(net[i])]);
  }});

  const linePath = netPoints.map((p,i) => (i===0?'M':'L') + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ');
  let netDots = netPoints.map(p => `<circle class="net-dot" cx="${{p[0]}}" cy="${{p[1]}}" r="3.5"/>`).join('');

  el.innerHTML = `<svg viewBox="0 0 ${{W}} ${{H}}" width="100%" height="100%">
    <g transform="translate(${{M.left}},${{M.top}})">
      ${{gridLines}}
      <line class="axis-line" x1="0" y1="${{zeroY}}" x2="${{plotW}}" y2="${{zeroY}}" style="stroke:#241A17;stroke-width:1"/>
      ${{bars}}
      <path class="net-line" d="${{linePath}}"/>
      ${{netDots}}
      ${{monthLabels}}
    </g>
  </svg>`;
}})();

function showTxMonth(m) {{
  document.querySelectorAll('.tx-tab').forEach(b => b.classList.toggle('active', b.dataset.month === m));
  document.querySelectorAll('.tx-panel').forEach(p => p.classList.toggle('active', p.id === 'tx-' + m));
}}

// ---- 07 히트맵 (JS 렌더) ----
(function() {{
  const h = DATA.heatmap;
  const table = document.getElementById('heatmapTable');
  let thead = '<thead><tr><th class="rowh">대분류</th>' + h.months.map(m => `<th>${{escHtml(m)}}</th>`).join('') + '</tr></thead>';
  let bodyRows = '';
  h.categories.forEach((cat, i) => {{
    const row = h.matrix[i];
    const max = Math.max(...row, 1);
    bodyRows += '<tr><td class="rowh">' + escHtml(cat) + '</td>';
    row.forEach(v => {{
      const t = v / max;
      const bg = t === 0 ? 'transparent' : `rgba(168,23,42,${{(0.12 + t*0.75).toFixed(2)}})`;
      const color = t > 0.55 ? '#fff' : '#241A17';
      const label = v >= 10000 ? Math.round(v/10000) + '만' : (v>0 ? Math.round(v/1000)+'천' : '–');
      bodyRows += `<td><span class="heat-cell" style="background:${{bg}};color:${{color}};display:inline-block;padding:5px 6px;min-width:38px;">${{label}}</span></td>`;
    }});
    bodyRows += '</tr>';
  }});
  table.innerHTML = thead + '<tbody>' + bodyRows + '</tbody>';
}})();

if ('serviceWorker' in navigator) {{
  window.addEventListener('load', () => {{
    navigator.serviceWorker.register('sw.js').catch(() => {{}});
  }});
}}
</script>
</body>
</html>
"""

import re as _re


def renumber_sections(doc):
    """섹션 번호를 문서 등장 순서대로 다시 매기고, {{REF:섹션id}} 상호참조를 해석한다.

    섹션을 추가·삭제·이동해도 번호와 본문 속 '○번' 표기가 자동으로 따라오게 하려는 것.
    번호를 본문에 직접 적으면 개편할 때마다 어긋난다.
    """
    # 1) section id → 순번 매핑 (등장 순서 기준)
    ids = _re.findall(r'<section id="([^"]+)"', doc)
    id_to_num = {sid: i for i, sid in enumerate(ids, start=1)}

    # 2) section-num 값을 순서대로 덮어쓰기
    counter = iter(range(1, len(ids) + 1))
    doc = _re.sub(r'(<span class="section-num">)\d+(</span>)',
                  lambda m: f"{m.group(1)}{next(counter):02d}{m.group(2)}", doc)

    # 3) 상호참조 해석
    def resolve(m):
        sid = m.group(1)
        if sid not in id_to_num:
            raise ValueError(f"상호참조 대상 섹션을 찾을 수 없습니다: {sid}")
        return str(id_to_num[sid])

    doc = _re.sub(r'\{REF:([^}]+)\}', resolve, doc)

    leftover = doc.count("{REF:")
    if leftover:
        raise ValueError(f"해석되지 않은 상호참조 {leftover}건")
    print(f"섹션 {len(ids)}개 번호 재부여 완료")
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
