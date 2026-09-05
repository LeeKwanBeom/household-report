import json

with open('data_bundle.json', encoding='utf-8') as f:
    bundle = json.load(f)

DATA_JSON = json.dumps(bundle, ensure_ascii=False)

def won(n):
    return f"{n:,.0f}"

kpi = bundle['kpi']
category = bundle['category']
fv = bundle['fixed_variable']
top_expenses = bundle['top_expenses']
months_included = bundle.get('months_included', [])
N_MONTHS = bundle['projection']['months_used']
period_label = f"2026.{months_included[0].split('-')[1]} — {months_included[-1].split('-')[1]} ({N_MONTHS}개월)" if months_included else ""
next_month_num = int(months_included[-1].split('-')[1]) + 1 if months_included else None
next_month_label = f"{next_month_num}월" if next_month_num else "다음달"
completed_months = bundle['projection'].get('completed_months', months_included[:-1])
N_COMPLETED = bundle['projection']['months_used']
completed_range_label = f"1~{int(completed_months[-1].split('-')[1])}월" if completed_months else ""

# ---- pre-render some static table rows (values only; layout/format via CSS) ----

def fv_rows():
    rows = []
    for r in fv:
        pct = r['고정비중']
        rows.append(f"""
        <tr>
          <td class="name">{r['대분류']}</td>
          <td class="num">{won(r['고정'])}원</td>
          <td class="num muted">{won(r['변동'])}원</td>
          <td class="num">{pct}%</td>
          <td class="fv-bar-col">
            <div class="bar-track"><div class="bar-fill" style="width:{pct}%"></div></div>
          </td>
        </tr>""")
    return "".join(rows)

def account_cards():
    accts = bundle['accounts']['list']
    cards = []
    for a in accts:
        cards.append(f"""
        <div class="kpi-card">
          <div class="label">{a['name']}</div>
          <div class="value">{won(a['current_balance'])}원</div>
        </div>""")
    return "".join(cards)

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

def card_performance_cards():
    cp = bundle.get('card_performance', {})
    blurbs = {
        "현대카드": "네이버 현대카드 Edition3 · 전월실적 40만원 이상이면 네이버플러스 적립 5%(더블적립 이벤트 중엔 최대 15%)가 열려요.",
        "신한카드": "신한카드 Mr.Life · 전월실적 100만원 이상 구간이면 월납/주말/Time 할인 한도가 각각 최대치(1만원·1만원·3만원)로 올라가요."
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
    for card in ['현대카드','신한카드']:
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
            <td class="name">{i['소분류']}</td>
            <td>{i['세부내용']}</td>
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

def top_rows():
    rows = []
    for i, r in enumerate(top_expenses, start=1):
        tag = f'<span class="tag fixed">고정</span>' if r['고정여부']=='고정' else '<span class="tag var">변동</span>'
        short_date = r['날짜'][5:]  # MM-DD
        rows.append(f"""
        <tr>
          <td class="rank top-rank-col">{i:02d}</td>
          <td class="num muted"><span class="top-date-full">{r['날짜']}</span><span class="top-date-short">{short_date}</span></td>
          <td class="name top-cat-col">{r['대분류']} <span class="muted">/ {r['소분류']}</span></td>
          <td>{r['세부내용']}</td>
          <td class="top-status-col">{tag}</td>
          <td class="num strong">{won(r['금액'])}원</td>
        </tr>""")
    return "".join(rows)

def projection_rows():
    rows = []
    for r in bundle['projection']['items']:
        status_badge = '<span class="badge warn">확인필요</span>' if r['상태']=='확인필요' else ''
        row_class = ' class="row-warn"' if r['상태']=='확인필요' else ''
        parts = r['항목'].split(' / ')
        if len(parts) == 3:
            item_html = f'<span class="proj-item-full">{r["항목"]}</span><span class="proj-item-short">{parts[-1]}</span>'
        else:
            item_html = r['항목']
        rows.append(f"""
        <tr{row_class}>
          <td class="name">{item_html}</td>
          <td class="num muted">{r['마지막월']}</td>
          <td class="num muted">{won(r['합계'])}원</td>
          <td class="num strong">{won(r['월평균'])}원</td>
          <td>{status_badge}</td>
        </tr>""")
    return "".join(rows)

def projection_cards():
    cards = []
    for r in bundle['projection']['items']:
        parts = r['항목'].split(' / ')
        short_name = parts[-1]
        main_cat = parts[0]
        warn_badge = ' <span class="badge warn">확인필요</span>' if r['상태']=='확인필요' else ''
        cards.append(f"""
        <div class="tx-card">
          <div class="tx-card-top">
            <span class="tx-card-desc">{short_name}{warn_badge}</span>
            <span class="tx-card-amt tx-expense">{won(r['월평균'])}원/월</span>
          </div>
          <div class="tx-card-meta muted">{main_cat} · 최근 {r['마지막월']} · {N_COMPLETED}개월 {won(r['합계'])}원</div>
        </div>""")
    return "".join(cards)

def fixed_vs_target_rows():
    data = bundle.get('fixed_vs_target', {})
    target = data.get('target', 0)
    months = data.get('months', [])
    if not months:
        return '<tr><td colspan="4" class="muted" style="text-align:center;padding:24px;">9월 데이터 집계 중이에요</td></tr>'
    rows = []
    for r in months:
        mm = int(r['월'].split('-')[1])
        diff = r['차이']
        over = diff > 0
        sign = '+' if diff > 0 else ''
        diff_class = 'red' if over else 'green'
        pct = round(r['실제']/target*100,1) if target else 0
        pct_capped = min(pct, 100)
        bar_color = 'var(--red)' if over else 'var(--green)'
        rows.append(f"""
        <tr>
          <td class="name" style="white-space:nowrap;">{mm}</td>
          <td class="num" style="white-space:nowrap;">{won(r['실제'])}원</td>
          <td class="num muted" style="white-space:nowrap;">{won(target)}원</td>
          <td class="num {diff_class}" style="white-space:nowrap;">{sign}{won(diff)}원</td>
          <td class="fv-bar-col">
            <div class="bar-track"><div class="bar-fill" style="width:{pct_capped}%;background:{bar_color};"></div></div>
          </td>
        </tr>""")
    return "".join(rows)

def projection_excluded_rows():
    rows = []
    for r in bundle['projection']['excluded']:
        rows.append(f"""
        <tr>
          <td class="name">{r['항목']}</td>
          <td class="num muted">{r['마지막월']}</td>
          <td class="num muted">{won(r['최근8개월합계'])}원</td>
          <td class="muted">{r['사유']}</td>
        </tr>""")
    return "".join(rows)

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
              <td class="name">{r['대분류']}</td>
              <td class="muted">{r['소분류']}</td>
              <td>{r['세부내용']}</td>
              <td>{fixed_tag}</td>
              <td class="num {sign_class}">{won(r['금액'])}원</td>
            </tr>""")
            desc = r['세부내용'] if r['세부내용'] != '-' else r['소분류']
            cards.append(f"""
            <div class="tx-card">
              <div class="tx-card-top">
                <span class="tx-card-desc">{desc}</span>
                <span class="tx-card-amt {sign_class}">{sign}{won(r['금액'])}원</span>
              </div>
              <div class="tx-card-meta muted">{r['대분류']} · {r['소분류']} · {r['날짜']}</div>
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
            <td class="name">{s['소분류']}</td>
            <td class="num muted">{s['건수']}건</td>
            <td class="num">{won(s['합계'])}원</td>
          </tr>""" for s in subs)
        blocks.append(f"""
        <details class="detail-item">
          <summary class="cd-row cd-summary">
            <span class="cd-rank cat-rank-col">{i:02d}</span>
            <span class="cd-name name">{name}</span>
            <span class="cd-num num">{won(c['합계'])}원</span>
            <span class="cd-num cd-avg-col num muted">{won(c['월평균'])}원</span>
            <span class="cd-num num muted">{c['비중']}%</span>
          </summary>
          <table class="sub-table">
            <tbody>{sub_rows}</tbody>
          </table>
        </details>""")
    return "".join(blocks)


def savings_trend_svg():
    m = bundle['monthly']
    labels = m['labels']
    rates = m.get('savings_rate', [])
    if not rates:
        return ''
    W, H = 700, 240
    ML, MR, MT, MB = 50, 16, 16, 30
    pw, ph = W - ML - MR, H - MT - MB
    lo, hi = min(min(rates), 0), max(max(rates), 0)
    span = (hi - lo) or 1
    pad = span * 0.12
    lo, hi = lo - pad, hi + pad
    span = hi - lo

    def y(v):
        return MT + ph - (v - lo) / span * ph

    def x(i):
        return ML + (pw / max(len(rates) - 1, 1)) * i

    grid = ''
    for k in range(5):
        v = lo + span * k / 4
        yy = y(v)
        grid += f'<line x1="{ML}" y1="{yy:.1f}" x2="{ML+pw}" y2="{yy:.1f}" class="axis-line"/>'
        grid += f'<text x="{ML-8}" y="{yy+4:.1f}" text-anchor="end" class="axis-label">{v:.0f}%</text>'
    zy = y(0)
    grid += f'<line x1="{ML}" y1="{zy:.1f}" x2="{ML+pw}" y2="{zy:.1f}" style="stroke:#241A17;stroke-width:1"/>'

    pts = [(x(i), y(v)) for i, v in enumerate(rates)]
    path = ' '.join(('M' if i == 0 else 'L') + f'{px:.1f},{py:.1f}'
                    for i, (px, py) in enumerate(pts))
    dots = ''.join(
        f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" fill="{"#2E6B52" if rates[i]>=0 else "#A8172A"}"/>'
        for i, (px, py) in enumerate(pts))
    xlabels = ''.join(
        f'<text x="{x(i):.1f}" y="{MT+ph+20}" text-anchor="middle" class="month-label">{lab}</text>'
        for i, lab in enumerate(labels))
    return (f'<svg viewBox="0 0 {W} {H}" width="100%" height="100%">{grid}'
            f'<path d="{path}" fill="none" stroke="#2E6B52" stroke-width="2.5"/>'
            f'{dots}{xlabels}</svg>')


def mom_rows():
    items = bundle.get('month_over_month', {}).get('items', [])
    if not items:
        return '<tr><td colspan="4" class="muted" style="text-align:center;padding:24px;">비교할 이전 달 데이터가 없어요</td></tr>'
    rows = []
    for r in items:
        d = r['증감']
        cls = 'red' if d > 0 else ('green' if d < 0 else 'muted')
        sign = '+' if d > 0 else ''
        rows.append(f"""
        <tr>
          <td class="name">{r['대분류']}</td>
          <td class="num muted mom-prev-col" style="white-space:nowrap;">{won(r['전월'])}원</td>
          <td class="num" style="white-space:nowrap;">{won(r['당월'])}원</td>
          <td class="num {cls}" style="white-space:nowrap;">{sign}{won(d)}원</td>
        </tr>""")
    return "".join(rows)


def subcategory_rows():
    rows = []
    for i, r in enumerate(bundle.get('subcategory_ranking', []), start=1):
        rows.append(f"""
        <tr>
          <td class="rank sub-rank-col">{i:02d}</td>
          <td class="name">{r['소분류']}</td>
          <td class="muted sub-main-col">{r['대분류']}</td>
          <td class="num" style="white-space:nowrap;">{won(r['합계'])}원</td>
          <td class="num muted sub-pct-col">{r['비중']}%</td>
        </tr>""")
    return "".join(rows)


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
<title>이관범 가계부 분석 · 2026 {int(months_included[0].split('-')[1])}–{int(months_included[-1].split('-')[1])}월</title>
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
  td.rank {{ color: var(--red); font-weight: 700; font-family: 'Noto Serif KR', serif; width: 30px; }}
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
  tr.row-warn {{ background: #FDFAF1; }}
  .tag {{ font-size: 11.5px; padding: 2px 8px; border-radius: 4px; font-weight: 600; }}
  .tag.fixed {{ background: var(--red-soft); color: var(--red-deep); }}
  .top-date-short {{ display: none; }}
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

  /* ---------- Summary / Actions ---------- */
  .summary-list {{ list-style: none; margin: 0; padding: 0; }}
  .summary-list li {{
    position: relative;
    padding: 10px 0 10px 20px;
    border-bottom: 1px solid var(--border);
    font-size: 14.5px;
  }}
  .summary-list li:last-child {{ border-bottom: none; }}
  .summary-list li::before {{
    content: '';
    position: absolute; left: 0; top: 18px;
    width: 7px; height: 7px;
    background: var(--red);
    border-radius: 50%;
  }}
  .summary-list b {{ color: var(--red-deep); }}

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
  .proj-item-short {{ display: none; }}
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
    .top-cat-col {{ display: none; }}
    .top-status-col {{ display: none; }}
    .top-rank-col {{ display: none; }}
    .sub-rank-col {{ display: none; }}
    .sub-pct-col {{ display: none; }}
    .mom-prev-col {{ display: none; }}
    .proj-item-full {{ display: none; }}
    .proj-item-short {{ display: inline; }}
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
    .fv-bar-col {{ display: none; }}
    table.heatmap {{ min-width: 460px; }}
    table.heatmap th, table.heatmap td {{ padding: 4px 3px; font-size: 11px; }}
    .heat-cell {{ min-width: 22px !important; padding: 3px 4px !important; }}
    .top-date-full {{ display: none; }}
    .top-date-short {{ display: inline; }}
    .top-cat-col {{ display: none; }}
    .top-status-col {{ display: none; }}
    .top-rank-col {{ display: none; }}
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
      <span class="note">{bundle.get('latest_update','')} 기준 · 생활비 / 신한은행 / 청년미래적금 / 대여금 4개 항목</span>
    </div>
    <div class="total-balance-card">
      <div class="total-balance-label">총 잔고 (4개 항목 합계)</div>
      <div class="total-balance-value">{won(sum(a['current_balance'] for a in bundle['accounts']['list']))}원</div>
    </div>
    <div class="kpi-grid" style="grid-template-columns: repeat(4,1fr);">
      {account_cards()}
    </div>
  </section>

  <section id="s2">
    <div class="section-head">
      <span class="section-num">02</span>
      <h2>신용카드별 실적 체크</h2>
      <span class="note">{int(bundle.get('card_performance',{}).get('month','2026-01').split('-')[1])}월 · 매달 초기화</span>
    </div>
    <div class="kpi-grid" style="grid-template-columns: repeat(2,1fr);">
      {card_performance_cards()}
    </div>
  </section>

  <section id="s3">
    <div class="section-head">
      <span class="section-num">03</span>
      <h2>카드사별 결제 내역</h2>
      <span class="note">{int(bundle.get('card_performance',{}).get('month','2026-01').split('-')[1])}월 · 비고 태그 기준</span>
    </div>
    <div class="kpi-grid card-list-grid" style="grid-template-columns: repeat(2,1fr);">
      {card_category_rows()}
    </div>
  </section>

  <section id="s5">
    <div class="section-head">
      <span class="section-num">04</span>
      <h2>월별 수입·지출 추이</h2>
      <span class="note">4월 회생 상환 종료 이후 순잉여 반전</span>
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

  <section id="s-savings">
    <div class="section-head">
      <span class="section-num">05</span>
      <h2>월별 저축률 추이</h2>
      <span class="note">순잉여 ÷ 그 달 수입 · 전체 평균 {bundle['kpi']['savings_rate']}%</span>
    </div>
    <div class="card">
      <div class="chart-box"><div class="svg-chart">{savings_trend_svg()}</div></div>
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

  <section id="s-subrank">
    <div class="section-head">
      <span class="section-num">07</span>
      <h2>소분류 TOP 20</h2>
      <span class="note">대분류 무관 · 금액순</span>
    </div>
    <p class="lede">06번이 대분류 안에서만 소분류를 보여준다면, 여기는 대분류를 무시하고 소분류만 금액순으로 줄 세운 목록이에요. 어떤 항목에 돈이 가장 많이 나갔는지 한눈에 볼 수 있어요.</p>
    <div class="card" style="overflow-x:auto;">
      <table>
        <thead><tr><th class="sub-rank-col"></th><th>소분류</th><th class="sub-main-col">대분류</th><th style="text-align:right;">합계</th><th style="text-align:right;" class="sub-pct-col">비중</th></tr></thead>
        <tbody>{subcategory_rows()}</tbody>
      </table>
    </div>
  </section>

  <section id="s-mom">
    <div class="section-head">
      <span class="section-num">08</span>
      <h2>전월 대비 증감</h2>
      <span class="note">{int(bundle.get('month_over_month',{}).get('prev','2026-01').split('-')[1])}월 → {int(bundle.get('month_over_month',{}).get('curr','2026-01').split('-')[1])}월 · 변동폭 큰 순</span>
    </div>
    <p class="lede">지난달 대비 이번 달 지출이 어떻게 달라졌는지 대분류별로 보여줘요. 빨간색은 늘어난 것, 초록색은 줄어든 것이에요. 이번 달이 아직 진행 중이라면 자연스럽게 감소로 보일 수 있어요.</p>
    <div class="card" style="overflow-x:auto;">
      <table>
        <thead><tr><th>대분류</th><th style="text-align:right;" class="mom-prev-col">전월</th><th style="text-align:right;">당월</th><th style="text-align:right;">증감</th></tr></thead>
        <tbody>{mom_rows()}</tbody>
      </table>
    </div>
  </section>

  <section id="s7">
    <div class="section-head">
      <span class="section-num">09</span>
      <h2>고정 지출 vs 변동 지출</h2>
      <span class="note">전체 고정비중 {bundle['fixed_variable_total']['고정비중']}% · 최고 {max(fv, key=lambda x: x['고정비중'])['대분류']} {max(fv, key=lambda x: x['고정비중'])['고정비중']}%</span>
    </div>
    <div class="card" style="overflow-x:auto;">
      <table>
        <thead><tr><th>대분류</th><th style="text-align:right;">고정</th><th style="text-align:right;">변동</th><th style="text-align:right;">고정비중</th><th style="width:120px;" class="fv-bar-col"></th></tr></thead>
        <tbody>{fv_rows()}</tbody>
      </table>
    </div>
  </section>

  <section id="s9">
    <div class="section-head">
      <span class="section-num">10</span>
      <h2>카테고리 × 월 히트맵</h2>
      <span class="note">진할수록 해당 월 지출이 큼</span>
    </div>
    <div class="card">
      <div class="heatmap-scroll">
        <table class="heatmap" id="heatmapTable"></table>
      </div>
    </div>
  </section>

  <section id="s10">
    <div class="section-head">
      <span class="section-num">11</span>
      <h2>최대 지출 TOP 15</h2>
      <span class="note">회생·가족 용돈·커플통장 제외 · 1위 {top_expenses[0]['세부내용']} ({won(top_expenses[0]['금액'])}원)</span>
    </div>
    <p class="lede">개인회생 상환·가족 용돈(1~4월 종료)과 커플통장(매달 꾸준히 나가는 정기 이체)은 성격상 순위를 독점하는 반복성 큰 금액이라 제외했어요. 대신 실제 낱개 소비 지출 위주로 다시 뽑았어요.</p>
    <div class="card" style="overflow-x:auto;">
      <table>
        <thead><tr><th class="top-rank-col"></th><th>날짜</th><th class="top-cat-col">카테고리</th><th>세부내용</th><th class="top-status-col">구분</th><th style="text-align:right;">금액</th></tr></thead>
        <tbody>{top_rows()}</tbody>
      </table>
    </div>
  </section>

  <section id="s4">
    <div class="section-head">
      <span class="section-num">12</span>
      <h2>신한은행 고정지출 예상 vs 실제</h2>
      <span class="note">{int(months_included[-1].split('-')[1])}월 기준 · 매달 갱신</span>
    </div>
    <p class="lede">신한은행 통장에서 빠져나가는 고정비 {len(bundle['shinhan_fixed'])}개 항목이에요. 관범님이 직접 정리해주신 목록 기준(합계 {won(bundle.get('shinhan_fixed_total',0))}원)이라, 다음 13번의 예상 고정지출({won(bundle['projection']['total'])}원)과는 집계 기준이 다릅니다 — 13번은 CSV의 고정 태그 전체를 통장 구분 없이 모은 값이에요.</p>
    <div class="card" style="overflow-x:auto;">
      <table>
        <thead><tr><th>고정지출</th><th style="text-align:right;">금액(예상)</th><th style="text-align:right;">출금액(실제)</th></tr></thead>
        <tbody>{shinhan_rows()}</tbody>
      </table>
    </div>
  </section>

  <section id="s11">
    <div class="section-head">
      <span class="section-num">13</span>
      <h2>매달 예상 고정지출</h2>
      <span class="note">{completed_range_label} 총액 ÷ {N_COMPLETED}개월 기준</span>
    </div>
    <p class="lede">고정 태그가 붙은 항목의 완성된 {N_COMPLETED}개월({completed_range_label}) 누적 금액을 {N_COMPLETED}로 나눈 월평균값이에요. 진행 중인 달({months_included[-1].split('-')[1]}월)은 아직 다 안 끝나서 계산에서 빠져요. 종료가 확인된 <b>회생·리모트뷰·Chat GPT·MIB·삼성 케어</b>는 계산에서 제외했어요. <b>이모티콘</b>은 2026년 6월에 월구독(5,700원)에서 연간권(42,000원)으로 갈아타서, 월구독분은 종료 처리하고 연간권은 연간결제성이라 별도 요청으로 제외했어요.</p>

    <div class="kpi-grid" style="grid-template-columns: repeat(2,1fr); margin-bottom:24px;">
      <div class="kpi-card">
        <div class="label">예상 고정지출 합계</div>
        <div class="value red">{won(bundle['projection']['total'])}원</div>
        <div class="sub">고정 항목 {len(bundle['projection']['items'])}개 기준</div>
      </div>
      <div class="kpi-card">
        <div class="label">계산 제외 항목</div>
        <div class="value green">{len(bundle['projection']['excluded'])}건</div>
        <div class="sub">종료 확인 5건 + 제외 요청 1건</div>
      </div>
    </div>

    <div class="card" style="overflow-x:auto;">
      <table class="tx-table-view" style="min-width:560px;">
        <thead><tr><th>항목</th><th style="text-align:right;">최근 등장월</th><th style="text-align:right;">{N_COMPLETED}개월 합계</th><th style="text-align:right;">월 예상액</th><th></th></tr></thead>
        <tbody>{projection_rows()}</tbody>
      </table>
      <div class="tx-card-view">{projection_cards()}</div>
    </div>

    <details class="detail-item" style="margin-top:8px;">
      <summary><span class="name">계산에서 제외한 항목 (참고용)</span><span class="num muted">{len(bundle['projection']['excluded'])}건</span></summary>
      <table class="sub-table">
        <thead><tr><th>항목</th><th style="text-align:right;">마지막 결제월</th><th style="text-align:right;">{N_COMPLETED}개월 합계(참고)</th><th>제외 사유</th></tr></thead>
        <tbody>{projection_excluded_rows()}</tbody>
      </table>
    </details>
  </section>

  <section id="s11-5">
    <div class="section-head">
      <span class="section-num">14</span>
      <h2>매달 목표 대비 실적</h2>
      <span class="note">목표 {won(bundle.get('fixed_vs_target',{}).get('target',0))}원 · 9월부터 비교</span>
    </div>
    <p class="lede">13번에서 계산한 예상 고정지출({won(bundle.get('fixed_vs_target',{}).get('target',0))}원)을 목표로 두고, 매달 실제 고정지출(고정여부="고정" 전체)이 이 기준을 넘었는지 비교해요. 회생·리모트뷰 같은 종료 항목이 다 정리된 9월부터 비교를 시작해요.</p>
    <div class="card" style="overflow-x:auto;">
      <table>
        <thead><tr><th style="white-space:nowrap;">월</th><th style="text-align:right;">지출</th><th style="text-align:right;">목표</th><th style="text-align:right;">차이</th><th style="width:120px;" class="fv-bar-col"></th></tr></thead>
        <tbody>{fixed_vs_target_rows()}</tbody>
      </table>
    </div>
  </section>

  <section id="s12">
    <div class="section-head">
      <span class="section-num">15</span>
      <h2>핵심 요약</h2>
    </div>
    <div class="card">
      <ul class="summary-list">
        <li><b>개인회생 상환이 4월에 완납</b>됐고, 4/18에 잔액 13,740원이 환급되며 종결이 확인됐어요. 이후 월 약 188만원의 여력이 생겼어요.</li>
        <li><b>5~7월 3개월 연속 순잉여 200만원대</b> — 회생 종료 직후 저축 여력이 크게 늘어난 구간이에요.</li>
        <li><b>8월은 실업급여 2차·퇴직금이 들어온 달</b>인 동시에 대형 지출이 몰려 순잉여가 크게 줄었어요. 최대 지출 1위는 {top_expenses[0]['세부내용']}({won(top_expenses[0]['금액'])}원)이에요.</li>
        <li>전체 지출의 <b>고정비 비중은 {bundle['fixed_variable_total']['고정비중']}%</b>({won(bundle['fixed_variable_total']['고정'])}원), 변동비는 {round(100 - bundle['fixed_variable_total']['고정비중'], 1)}%({won(bundle['fixed_variable_total']['변동'])}원)예요.</li>
        <li>{' · '.join(f"<b>{x['대분류']}({x['고정비중']}%)</b>" for x in sorted(fv, key=lambda x: -x['고정비중'])[:3])}는 거의 순수 고정비 성격이에요.</li>
        <li>수입은 <b>{bundle['income_breakdown'][0]['항목']}가 {bundle['income_breakdown'][0]['비중']}%</b>로 절대적이고, {bundle['income_breakdown'][1]['항목']}가 {bundle['income_breakdown'][1]['비중']}%로 뒤를 이어요.</li>
        <li><b>매달 예상 고정지출은 약 {won(bundle['projection']['total'])}원</b>(완성된 {N_COMPLETED}개월 기준, 연 환산 {won(bundle['projection']['annual'])}원) — 종료·제외 확인된 {len(bundle['projection']['excluded'])}건은 계산에서 뺐어요.</li>
      </ul>
    </div>
  </section>

  <section id="s13">
    <div class="section-head">
      <span class="section-num">16</span>
      <h2>확인이 필요한 항목</h2>
    </div>
    <div class="card">
      <ol class="action-list">
        <li>내여자 / 커플통장 실지출(월평균 61만원)과 마스터 지침 고정값(30만원) 간 차이 원인 확인</li>
        <li>회생 상환 종료로 생긴 월 188만원 여력 — 저축·투자 재배정 규칙 수립 검토</li>
        <li>실업급여·퇴직금 등 일시 수입 유입 시 소비 쏠림 방지용 배정 규칙(예: N% 저축 우선) 검토</li>
      </ol>
    </div>
  </section>

  <section id="s14">
    <div class="section-head">
      <span class="section-num">17</span>
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

// ---- 01 월별 수입/지출 (순수 SVG, 라이브러리 없음) ----
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

// ---- 05 히트맵 (JS 렌더) ----
(function() {{
  const h = DATA.heatmap;
  const table = document.getElementById('heatmapTable');
  let thead = '<thead><tr><th class="rowh">대분류</th>' + h.months.map(m => `<th>${{m}}</th>`).join('') + '</tr></thead>';
  let bodyRows = '';
  h.categories.forEach((cat, i) => {{
    const row = h.matrix[i];
    const max = Math.max(...row, 1);
    bodyRows += '<tr><td class="rowh">' + cat + '</td>';
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

with open('/mnt/user-data/outputs/이관범_가계부_분석_리포트.html', 'w', encoding='utf-8') as f:
    f.write(html)

print("done, length:", len(html))
