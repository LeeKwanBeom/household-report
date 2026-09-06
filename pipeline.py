"""
가계부 CSV → data_bundle.json 파이프라인

사용법:
    python3 pipeline.py 가계부9.csv

모든 비즈니스 규칙이 이 파일에 명시되어 있습니다.
규칙이 바뀌면 아래 RULES 섹션만 수정하세요.
"""
import sys
import json
import pandas as pd

# ============================================================
# RULES — 비즈니스 규칙 (변경이 필요하면 여기만 수정)
# ============================================================

# 계좌 시작 잔고
#
# as_of = 그 잔고를 실제로 확인한 날짜. 잔고 계산은 계좌마다 "as_of 다음날부터"의
# 거래만 더한다. as_of 당일까지의 거래는 이미 잔고 숫자 안에 녹아 있기 때문이다.
# 이 한 줄이 이중계산 방지 규칙의 전부이며, 예외 목록을 따로 두지 않는다.
#
# 잔고를 새로 재면 balance와 as_of를 **반드시 같이** 갱신할 것.
# 날짜만 옮기면 그 사이 거래가 통째로 사라지거나 두 번 세어진다.
ACCOUNTS_START = {
    "생활비 통장":   {"balance": 3342548, "as_of": "2026-09-01", "desc": "유동 지출 (카드값·경조사·기타)"},
    "신한은행 통장": {"balance": 0,       "as_of": "2026-09-01", "desc": "매달 고정비 선입금 (통신비·보험비 등)"},
    "청년미래적금":  {"balance": 1500000, "as_of": "2026-09-03", "desc": "적금 (매달 1일 생활비에서 50만원 이체)"},
    "대여금":       {"balance": 3300000, "as_of": "2026-09-01", "desc": "타인에게 빌려준 돈 (회수 시 감소)"},
}

# CSV의 '결제수단'·이체 '소분류'에 실제로 적히는 표기 → 계좌명
# CSV 표기가 바뀌면 여기만 고친다. 매칭 실패는 검증 단계에서 잡힌다.
ACCOUNT_KEY = {
    "생활비 통장":   "생활비",
    "신한은행 통장": "신한은행",
    "청년미래적금":  "청년미래적금",
    "대여금":       "대여금",
}

# 종료 확인된 고정 항목 → 매달 예상 고정지출 계산에서 제외
ENDED_FIXED_ITEMS = [
    "금융 / 회생",
    "주거생활비 / OTT",
    "문화생활비 / 어플·멤버쉽 / 리모트뷰",
    "문화생활비 / 어플·멤버쉽 / Chat GPT",
    "문화생활비 / 어플·멤버쉽 / MIB",
    "문화생활비 / 어플·멤버쉽 / 삼성 케어",
    # 5,700원 월구독을 쓰다가 1년권으로 갈아탐. 2026-07부터 연간권 사용,
    # 월구독은 그 직전에 종료. 연간권 쪽은 아래 MANUALLY_EXCLUDED에 있다.
    "문화생활비 / 어플·멤버쉽 / 이모티콘",
]

# 사용자 요청으로 제외한 고정 항목 (연간결제성 등)
# 카톡 이모티콘 1년권 — 1년에 한 번 결제라 월평균에 넣으면 그 달만 튄다.
# (카카오 '톡서랍'은 별개이고 지금도 쓰는 항목이라 여기 없다. SHINHAN_FIXED_ITEMS 참고)
MANUALLY_EXCLUDED_FIXED_ITEMS = [
    "문화생활비 / 어플·멤버쉽 / 이모티콘 1년",
]

# 같은 구독인데 이름이 다르게 기록된 항목 → 하나로 통합
SUBSCRIPTION_ALIASES = {
    "이모티콘 월구독": "이모티콘",
}

# TOP15에서 제외 (매달 반복되는 큰 금액이라 순위를 독점)
TOP_EXPENSE_EXCLUSIONS = [
    ("금융", "회생"),
    ("가족", "용돈"),
    ("내여자", "커플통장"),
]

# 신한은행 통장에서 나가는 고정비 목록 (사용자 제공)
# (표시명, 예상금액, 매칭키워드) — 키워드 None이면 특수 매칭
SHINHAN_FIXED_ITEMS = [
    ("보험_흥국",    39022,  ["흥국"]),
    ("보험_메리츠",  72000,  ["메리츠"]),
    ("보험_DB",      33560,  ["DB"]),
    ("휴대폰 요금",  130000, ["휴대폰"]),
    ("신한이자",     11776,  ["이자"]),
    ("카카오 톡서랍", 990,    ["톡서랍"]),
    ("어도비",       26400,  ["어도비"]),
    ("유투브",       14900,  ["유투브", "유튜브"]),
    ("클로드",       187500, ["클로드"]),  # 2026-09 맥스 5x 전환
    ("쿠팡 월 회비", 7890,   ["쿠팡"]),
    ("알레르기약",   6000,   ["알레르기"]),
    ("사장님 곗돈",  10000,  ["사장님"]),
    ("방학동 곗돈",  50000,  ["방학동"]),
    ("머리 컷트",    15000,  ["컷트", "머리"]),
    ("교통&유류비",  55000,  None),  # 소분류=유류비 또는 대분류=교통비
    ("로또",         20000,  ["로또"]),
]

# 신용카드 전월실적 목표
CARD_TARGETS = {"현대카드": 400000, "신한카드": 1000000}

# 목표 대비 실적 비교 시작 월
#
# 이 비교의 목표치는 ENDED_FIXED_ITEMS를 빼고 계산된다. 그 항목들을 아직 내고
# 있던 달까지 비교에 넣으면, 지금은 없는 지출 때문에 매달 초과로 찍혀 표가
# 쓸모없어진다. 그래서 "종료 항목이 다 정리된 달"부터 비교를 시작한다.
#
# "auto" = 종료 항목들의 마지막 결제월 다음 달을 데이터에서 계산한다. 기본값이며,
#          종료 항목을 추가/삭제하면 시작월도 알아서 따라온다.
# "2026-07" 처럼 직접 적으면 그 값을 그대로 쓴다 (특별한 이유가 있을 때만).
TARGET_COMPARISON_START = "auto"


# ============================================================
# CSV 로딩 — 견고한 파싱
# ============================================================

TEXT_COLS = ["구분", "대분류", "소분류", "세부내용", "결제수단", "고정여부", "비고"]


def _ym(s):
    """'2026-09' → (2026, 9)"""
    y, m = s.split("-")[:2]
    return int(y), int(m)


def parse_amount(value):
    """금액 문자열을 float으로 변환. 빈값·공백·괄호음수·통화기호 처리."""
    s = str(value).strip()
    if s in ("", "nan", "None", "-"):
        return 0.0
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    s = s.replace(",", "").replace("₩", "").replace("원", "").strip()
    if s in ("", "-"):
        return 0.0
    try:
        v = float(s)
    except ValueError:
        raise ValueError(f"금액을 숫자로 변환할 수 없습니다: {value!r}")
    return -v if negative else v


def load_csv(path):
    df = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    df.columns = [c.strip() for c in df.columns]

    for c in TEXT_COLS:
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].fillna("").astype(str).str.strip().replace("nan", "")

    if "금액" not in df.columns:
        raise ValueError("CSV에 '금액' 컬럼이 없습니다.")
    if "날짜" not in df.columns:
        raise ValueError("CSV에 '날짜' 컬럼이 없습니다.")

    # 완전히 빈 행 제거
    df = df[df["날짜"].notna() & (df["날짜"].astype(str).str.strip() != "")]

    df["금액"] = df["금액"].apply(parse_amount)
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce")

    bad_dates = df["날짜"].isna().sum()
    if bad_dates:
        print(f"  [주의] 날짜를 해석할 수 없는 행 {bad_dates}건을 건너뜁니다.")
    df = df.dropna(subset=["날짜"]).reset_index(drop=True)

    df["월"] = df["날짜"].dt.strftime("%Y-%m")

    # 구독 이름 통합
    df["세부내용"] = df["세부내용"].replace(SUBSCRIPTION_ALIASES)

    valid_types = {"수입", "지출", "이체"}
    unknown = set(df["구분"].unique()) - valid_types
    if unknown:
        print(f"  [주의] 알 수 없는 '구분' 값: {unknown}")

    return df


# ============================================================
# 집계
# ============================================================


def build_bundle(df):
    core = df[df["구분"].isin(["수입", "지출"])].copy()  # 이체는 수입/지출 집계 제외
    exp = core[core["구분"] == "지출"].copy()
    inc = core[core["구분"] == "수입"].copy()

    months = sorted(core["월"].unique())
    month_labels = [f"{int(m.split('-')[1])}월" for m in months]
    N = len(months)

    total_income = inc["금액"].sum()
    total_expense = exp["금액"].sum()

    # ---- 월별 추이 ----
    piv = core.pivot_table(index="월", columns="구분", values="금액",
                           aggfunc="sum", fill_value=0).reindex(months, fill_value=0)
    mi = [round(v) for v in piv.get("수입", pd.Series([0] * N, index=months))]
    me = [round(v) for v in piv.get("지출", pd.Series([0] * N, index=months))]
    mn = [i - e for i, e in zip(mi, me)]
    savings_rate_series = [round(n / i * 100, 1) if i else 0 for n, i in zip(mn, mi)]

    # ---- 대분류 ----
    cat = exp.groupby("대분류")["금액"].agg(["sum", "count"]).reset_index()
    cat.columns = ["대분류", "합계", "건수"]
    cat["월평균"] = cat["합계"] / N
    cat["비중"] = cat["합계"] / cat["합계"].sum() * 100
    cat = cat.sort_values("합계", ascending=False)
    category_data = [
        {"대분류": r["대분류"], "합계": round(r["합계"]), "건수": int(r["건수"]),
         "월평균": round(r["월평균"]), "비중": round(r["비중"], 1)}
        for _, r in cat.iterrows()
    ]

    # ---- 히트맵 ----
    hm = exp.pivot_table(index="대분류", columns="월", values="금액",
                         aggfunc="sum", fill_value=0).reindex(columns=months, fill_value=0)
    hm = hm.reindex(cat["대분류"].tolist())
    heatmap_data = {
        "categories": hm.index.tolist(),
        "months": month_labels,
        "matrix": [[round(v) for v in row] for row in hm.values.tolist()],
    }

    # ---- 대분류 > 소분류 ----
    detail = exp.groupby(["대분류", "소분류"])["금액"].agg(["sum", "count"]).reset_index()
    detail.columns = ["대분류", "소분류", "합계", "건수"]
    detail_grouped = {}
    for maincat in cat["대분류"]:
        sub = detail[detail["대분류"] == maincat].sort_values("합계", ascending=False)
        detail_grouped[maincat] = [
            {"소분류": r["소분류"] or "(미분류)", "합계": round(r["합계"]), "건수": int(r["건수"])}
            for _, r in sub.iterrows()
        ]

    # ---- 소분류 전체 랭킹 (대분류 무관) ----
    sub_rank = detail.sort_values("합계", ascending=False).head(20)
    subcategory_ranking = [
        {"대분류": r["대분류"], "소분류": r["소분류"] or "(미분류)",
         "합계": round(r["합계"]), "건수": int(r["건수"]),
         "비중": round(r["합계"] / total_expense * 100, 1)}
        for _, r in sub_rank.iterrows()
    ]

    # ---- 고정 vs 변동 ----
    exp["고정구분"] = exp["고정여부"].apply(lambda x: "고정" if x == "고정" else "변동")
    fv = exp.pivot_table(index="대분류", columns="고정구분", values="금액",
                         aggfunc="sum", fill_value=0).reindex(cat["대분류"].tolist(), fill_value=0)
    fv_data = []
    for c in fv.index:
        f = fv.loc[c].get("고정", 0)
        v = fv.loc[c].get("변동", 0)
        t = f + v
        fv_data.append({"대분류": c, "고정": round(f), "변동": round(v),
                        "고정비중": round(f / t * 100, 1) if t else 0})
    fixed_total = sum(x["고정"] for x in fv_data)
    variable_total = sum(x["변동"] for x in fv_data)

    # ---- 수입원 ----
    inc_cat = inc.groupby("대분류")["금액"].sum().sort_values(ascending=False).reset_index()
    inc_cat.columns = ["항목", "합계"]
    income_breakdown = [
        {"항목": r["항목"], "합계": round(r["합계"]),
         "비중": round(r["합계"] / total_income * 100, 1)}
        for _, r in inc_cat.iterrows()
    ]

    # ---- TOP 15 ----
    mask = pd.Series(False, index=exp.index)
    for maincat, subcat in TOP_EXPENSE_EXCLUSIONS:
        mask |= (exp["대분류"] == maincat) & (exp["소분류"] == subcat)
    top = exp[~mask].nlargest(15, "금액")[
        ["날짜", "대분류", "소분류", "세부내용", "금액", "고정여부"]].copy()
    top["날짜"] = top["날짜"].dt.strftime("%Y-%m-%d")
    top_expenses = [
        {"날짜": r["날짜"], "대분류": r["대분류"], "소분류": r["소분류"],
         "세부내용": r["세부내용"] or "-", "금액": round(r["금액"]),
         "고정여부": r["고정여부"] or "변동"}
        for _, r in top.iterrows()
    ]

    # ---- 월별 전체 내역 (이체 포함, 원본 그대로) ----
    df_sorted = df.sort_values("날짜")
    monthly_tx = {}
    for m in months:
        sub = df_sorted[df_sorted["월"] == m]
        monthly_tx[m] = [
            {"날짜": r["날짜"].strftime("%m/%d"), "구분": r["구분"], "대분류": r["대분류"],
             "소분류": r["소분류"] or "-", "세부내용": r["세부내용"] or "-",
             "금액": round(r["금액"]), "고정여부": r["고정여부"] or "-"}
            for _, r in sub.iterrows()
        ]

    # ---- 매달 예상 고정지출 (완성된 달만) ----
    completed_months = months[:-1]  # 최신 월은 항상 진행 중으로 간주
    n_completed = len(completed_months)
    # 한 달치만 올린 경우(예: 새해 첫 달) 완성된 달이 0개다. 예전에는 여기서
    # IndexError로 죽었다. 계산을 건너뛰고 나머지 섹션은 정상 생성한다.
    has_completed = n_completed > 0
    last_completed = (pd.to_datetime(completed_months[-1] + "-01")
                      if has_completed else pd.to_datetime(months[-1] + "-01"))

    fixed_rows = core[(core["구분"] == "지출") &
                      (core["월"].isin(completed_months)) &
                      (core["고정여부"] == "고정")].copy()

    non_app = fixed_rows[fixed_rows["소분류"] != "어플/멤버쉽"].copy()
    non_app["키"] = non_app["대분류"] + " / " + non_app["소분류"]
    g1 = non_app.groupby(["대분류", "키"]).agg(
        등장월수=("월", "nunique"), 마지막월=("월", "max"), 합계=("금액", "sum")).reset_index()

    app = fixed_rows[fixed_rows["소분류"] == "어플/멤버쉽"].copy()
    app["대분류"] = "문화생활비"
    app["키"] = "문화생활비 / 어플·멤버쉽 / " + app["세부내용"]
    g2 = app.groupby(["대분류", "키"]).agg(
        등장월수=("월", "nunique"), 마지막월=("월", "max"), 합계=("금액", "sum")).reset_index()

    g = pd.concat([g1, g2], ignore_index=True)
    g["마지막월_dt"] = pd.to_datetime(g["마지막월"] + "-01")
    g["공백"] = ((last_completed.year - g["마지막월_dt"].dt.year) * 12 +
                (last_completed.month - g["마지막월_dt"].dt.month))
    g["월평균"] = (g["합계"] / n_completed).round(0) if has_completed else 0

    excluded_keys = ENDED_FIXED_ITEMS + MANUALLY_EXCLUDED_FIXED_ITEMS
    proj = g[~g["키"].isin(excluded_keys)].sort_values("월평균", ascending=False)
    projection_items = [
        {"대분류": r["대분류"], "항목": r["키"], "마지막월": r["마지막월"],
         "공백": int(r["공백"]), "합계": round(r["합계"]), "월평균": round(r["월평균"]),
         "상태": "정상"}
        for _, r in proj.iterrows()
    ] if has_completed else []
    projection_total = round(sum(p["월평균"] for p in projection_items))

    excluded_items = []
    for _, r in g[g["키"].isin(ENDED_FIXED_ITEMS)].iterrows():
        excluded_items.append({"항목": r["키"], "마지막월": r["마지막월"],
                               "누적합계": round(r["합계"]), "사유": "종료 확인"})
    for _, r in g[g["키"].isin(MANUALLY_EXCLUDED_FIXED_ITEMS)].iterrows():
        excluded_items.append({"항목": r["키"], "마지막월": r["마지막월"],
                               "누적합계": round(r["합계"]), "사유": "제외 요청(연간결제성)"})

    # 규칙에 적어뒀는데 데이터에서 한 번도 안 걸린 키 → 이름이 바뀌었거나 오타
    seen_keys = set(g["키"])
    stale_rule_keys = [k for k in excluded_keys if k not in seen_keys]

    # ---- 계좌 잔고 ----
    # 계좌마다 as_of가 다르므로 계좌별로 따로 자른다. as_of 당일까지의 거래는
    # 이미 시작 잔고에 들어 있으므로 '초과(>)' 비교를 쓴다.
    def account_balance(name):
        meta = ACCOUNTS_START[name]
        start = meta["balance"]
        short = ACCOUNT_KEY[name]
        src = df[df["날짜"] > pd.Timestamp(meta["as_of"])]
        income = src[(src["구분"] == "수입") & (src["결제수단"] == short)]["금액"].sum()
        expense = src[(src["구분"] == "지출") & (src["결제수단"] == short)]["금액"].sum()
        out = src[(src["구분"] == "이체") & (src["결제수단"] == short)]["금액"].sum()
        into = src[(src["구분"] == "이체") & (src["소분류"] == short)]["금액"].sum()
        moved = len(src[((src["결제수단"] == short) | (src["소분류"] == short))])
        return round(start + income - expense - out + into), moved

    accounts_list = []
    for name, meta in ACCOUNTS_START.items():
        # 대여금도 예외 없이 계산한다. 회수 기록이 있으면 줄어야 하기 때문이다.
        bal, moved = account_balance(name)
        accounts_list.append({"name": name, "start_balance": meta["balance"],
                              "current_balance": bal, "desc": meta["desc"],
                              "as_of": meta["as_of"], "tx_count": moved})

    # 어느 계좌에도 붙지 않은 결제수단·이체 상대계좌 (조용히 증발하는 값 추적)
    known_accounts = set(ACCOUNT_KEY.values())
    card_tags = set(CARD_TARGETS)
    unknown_payment = sorted(
        {v for v in df[df["구분"].isin(["수입", "지출"])]["결제수단"].unique()
         if v and v not in known_accounts and v not in card_tags}
    )
    unknown_transfer = sorted(
        {v for v in df[df["구분"] == "이체"]["소분류"].unique()
         if v and v not in known_accounts}
    ) + sorted(
        {v for v in df[df["구분"] == "이체"]["결제수단"].unique()
         if v and v not in known_accounts}
    )

    # ---- 신한은행 고정지출 예상 vs 실제 ----
    # 예상금액이 '한 달치'이므로 실제도 최신 월 한 달치만 본다.
    # 누적 기간과 비교하면 달이 쌓일수록 무조건 초과로 보인다.
    sh_month = months[-1]
    sh = df[(df["구분"] == "지출") & (df["결제수단"] == ACCOUNT_KEY["신한은행 통장"]) &
            (df["월"] == sh_month)].copy()
    sh["검색"] = sh["대분류"] + " " + sh["소분류"] + " " + sh["세부내용"]

    # 한 거래가 여러 항목의 키워드에 걸리면 합계가 부풀려진다. 위에서부터
    # 선착순으로 한 번만 배정하고, 어디에도 안 걸린 건은 따로 보고한다.
    #
    # 또 키워드가 넓어서(예: "쿠팡") 무관한 변동 지출까지 빨아들이는 일이 있었다.
    # 1차로 '고정' 태그 거래만 보고, 거기서 못 찾은 항목만 2차로 나머지까지 넓힌다.
    # (교통&유류비처럼 애초에 변동으로 기록되는 항목이 있어 2차를 남겨둔다.)
    claimed = pd.Series(False, index=sh.index)
    is_fixed = sh["고정여부"] == "고정"

    def _hit(keywords):
        if keywords is None:
            return (sh["소분류"] == "유류비") | (sh["대분류"] == "교통비")
        return sh["검색"].str.contains("|".join(keywords), na=False)

    picked = {}
    for name, expected, keywords in SHINHAN_FIXED_ITEMS:
        hit = _hit(keywords) & is_fixed
        m = sh[hit & ~claimed]
        if len(m):
            claimed |= hit
            picked[name] = m

    for name, expected, keywords in SHINHAN_FIXED_ITEMS:
        if name in picked:
            continue
        hit = _hit(keywords)
        m = sh[hit & ~claimed]
        if len(m):
            claimed |= hit
            picked[name] = m

    shinhan_fixed = []
    for name, expected, keywords in SHINHAN_FIXED_ITEMS:
        m = picked.get(name)
        shinhan_fixed.append({"항목": name, "예상금액": expected,
                              "출금액": round(m["금액"].sum()) if m is not None else 0,
                              "matched": m is not None})
    shinhan_unmatched = [
        {"세부내용": r["세부내용"] or r["소분류"], "금액": round(r["금액"]),
         "고정여부": r["고정여부"] or "변동"}
        for _, r in sh[~claimed].iterrows()
    ]

    # ---- 신용카드 실적 (진행 페이스 포함) ----
    latest_month = months[-1]
    latest_date = df["날짜"].max()
    days_in_month = pd.Period(latest_month).days_in_month
    elapsed_ratio = latest_date.day / days_in_month

    this_month_exp = core[(core["월"] == latest_month) & (core["구분"] == "지출")]
    card_cards = []
    for card, tgt in CARD_TARGETS.items():
        spent = round(this_month_exp[this_month_exp["비고"] == card]["금액"].sum())
        pct = round(spent / tgt * 100, 1) if tgt else 0
        expected_pct = round(elapsed_ratio * 100, 1)
        card_cards.append({"name": card, "target": tgt, "spent": spent, "pct": pct,
                           "expected_pct": expected_pct,
                           "on_pace": pct >= expected_pct,
                           "remaining": max(tgt - spent, 0)})
    card_performance = {"month": latest_month, "cards": card_cards,
                        "day": latest_date.day, "days_in_month": days_in_month}

    # ---- 카드사별 결제 내역 (날짜 오름차순) ----
    card_detail = {}
    for card in CARD_TARGETS:
        sub = this_month_exp[this_month_exp["비고"] == card].sort_values("날짜")
        card_detail[card] = [
            {"날짜": r["날짜"].strftime("%m/%d"), "대분류": r["대분류"], "소분류": r["소분류"],
             "세부내용": r["세부내용"] or "-", "금액": round(r["금액"])}
            for _, r in sub.iterrows()
        ]

    # ---- 목표 대비 실적 ----
    # 비교 시작월 결정. "auto"면 종료 항목들이 마지막으로 결제된 달의 다음 달.
    if TARGET_COMPARISON_START == "auto":
        ended_last = g[g["키"].isin(ENDED_FIXED_ITEMS)]["마지막월"]
        if len(ended_last):
            _y, _m = _ym(ended_last.max())
            compare_start = f"{_y + 1}-01" if _m == 12 else f"{_y}-{_m + 1:02d}"
            compare_start_source = "자동 (종료 항목 마지막 결제월 다음 달)"
        else:
            compare_start = months[0]
            compare_start_source = "자동 (데이터에 종료 항목 없음 → 전체 기간)"
    else:
        compare_start = TARGET_COMPARISON_START
        compare_start_source = "수동 지정"

    fvt_months = []
    for m in [x for x in months if x >= compare_start]:
        actual = round(core[(core["월"] == m) & (core["구분"] == "지출") &
                            (core["고정여부"] == "고정")]["금액"].sum())
        is_current = (m == latest_month)
        fvt_months.append({"월": m, "실제": actual, "목표": projection_total,
                           "차이": actual - projection_total, "진행중": is_current})
    fixed_vs_target = {"target": projection_total, "months": fvt_months,
                       "elapsed_ratio": round(elapsed_ratio * 100, 1),
                       "start": compare_start, "start_source": compare_start_source}

    # ---- 카테고리별 전월 대비 증감 ----
    # 진행 중인 달은 전월 '전체'와 비교하면 무조건 감소로 보인다.
    # 같은 일자까지로 잘라서 비교해야 실제 증감을 볼 수 있다.
    mom = []
    same_day_cutoff = None
    if len(months) >= 2:
        prev_m, curr_m = months[-2], months[-1]
        cutoff_day = int(latest_date.day)

        prev_rows = exp[exp["월"] == prev_m]
        curr_rows = exp[exp["월"] == curr_m]

        # 전월이 이 일자를 넘겨 실제로 진행됐을 때만 잘라낸다
        prev_month_end = (pd.to_datetime(prev_m + "-01") + pd.offsets.MonthEnd(0)).day
        if cutoff_day < prev_month_end:
            same_day_cutoff = cutoff_day
            prev_rows = prev_rows[prev_rows["날짜"].dt.day <= cutoff_day]
            curr_rows = curr_rows[curr_rows["날짜"].dt.day <= cutoff_day]

        prev_s = prev_rows.groupby("대분류")["금액"].sum()
        curr_s = curr_rows.groupby("대분류")["금액"].sum()
        for c in set(prev_s.index) | set(curr_s.index):
            p, cu = round(prev_s.get(c, 0)), round(curr_s.get(c, 0))
            mom.append({"대분류": c, "전월": p, "당월": cu, "증감": cu - p})
        mom.sort(key=lambda x: -abs(x["증감"]))
    month_over_month = {"prev": months[-2] if len(months) >= 2 else None,
                        "curr": months[-1], "items": mom[:10],
                        "same_day_cutoff": same_day_cutoff}

    return {
        "kpi": {
            "total_income": round(total_income),
            "total_expense": round(total_expense),
            "net": round(total_income - total_expense),
            "savings_rate": round((total_income - total_expense) / total_income * 100, 1) if total_income else 0,
        },
        "monthly": {"labels": month_labels, "income": mi, "expense": me,
                    "net": mn, "savings_rate": savings_rate_series},
        "category": category_data,
        "heatmap": heatmap_data,
        "detail": detail_grouped,
        "subcategory_ranking": subcategory_ranking,
        "fixed_variable": fv_data,
        "fixed_variable_total": {"고정": fixed_total, "변동": variable_total,
                                 "고정비중": round(fixed_total / (fixed_total + variable_total) * 100, 1)},
        "income_breakdown": income_breakdown,
        "top_expenses": top_expenses,
        # 화면 문구가 이 목록을 손으로 적지 않도록 bundle에 함께 넘긴다
        "top_expense_exclusions": [sub for _, sub in TOP_EXPENSE_EXCLUSIONS],
        "monthly_tx": monthly_tx,
        "projection": {"months_used": n_completed, "completed_months": completed_months,
                       "total": projection_total, "annual": projection_total * 12,
                       "items": projection_items, "excluded": excluded_items},
        "accounts": {"list": accounts_list,
                     "as_of_range": sorted({a["as_of"] for a in accounts_list})},
        "shinhan_fixed": shinhan_fixed,
        "shinhan_fixed_total": sum(x["예상금액"] for x in shinhan_fixed),
        "shinhan_month": sh_month,
        "shinhan_unmatched": shinhan_unmatched,
        # 화면이 규칙 상수를 손으로 다시 적지 않도록 bundle에 실어 보낸다
        "card_targets": CARD_TARGETS,
        "diagnostics": {"unknown_payment": unknown_payment,
                        "unknown_transfer": unknown_transfer,
                        "stale_rule_keys": stale_rule_keys,
                        "has_completed": has_completed},
        "card_performance": card_performance,
        "card_category_detail": card_detail,
        "fixed_vs_target": fixed_vs_target,
        "month_over_month": month_over_month,
        "months_included": months,
        "latest_update": latest_date.strftime("%-m월 %-d일"),
    }


def validate(bundle):
    """섹션 간 합계 정합성 검증."""
    errors = []
    kpi_exp = bundle["kpi"]["total_expense"]
    checks = {
        "월별 추이 지출 합": sum(bundle["monthly"]["expense"]),
        "대분류 합": sum(c["합계"] for c in bundle["category"]),
        "고정/변동 합": sum(x["고정"] + x["변동"] for x in bundle["fixed_variable"]),
        "히트맵 합": sum(sum(r) for r in bundle["heatmap"]["matrix"]),
    }
    for label, v in checks.items():
        if v != kpi_exp:
            errors.append(f"{label}({v:,}) != KPI 총지출({kpi_exp:,})")

    kpi_inc = bundle["kpi"]["total_income"]
    if sum(bundle["monthly"]["income"]) != kpi_inc:
        errors.append("월별 추이 수입 합 != KPI 총수입")
    if sum(i["합계"] for i in bundle["income_breakdown"]) != kpi_inc:
        errors.append("수입원 합 != KPI 총수입")

    return errors


def warnings_for(bundle):
    """중단시킬 정도는 아니지만 조용히 틀린 값을 만드는 상황들.

    위 validate()는 같은 데이터를 다시 집계해 비교하는 항등식이라 구조적으로
    거의 실패하지 않는다. 실제 사고는 대부분 '규칙이 데이터와 안 맞는' 쪽에서
    난다 — 계좌명 오타로 거래가 잔고에서 증발하거나, 제외 규칙 키가 옛날
    이름이라 종료된 항목이 계속 예상치에 들어가는 식이다.
    """
    d = bundle.get("diagnostics", {})
    warns = []

    if d.get("unknown_payment"):
        warns.append("어느 계좌에도 속하지 않는 결제수단 → 잔고에 반영 안 됨: "
                     + ", ".join(d["unknown_payment"]))
    if d.get("unknown_transfer"):
        warns.append("계좌로 인식되지 않는 이체 상대: "
                     + ", ".join(sorted(set(d["unknown_transfer"]))))
    if d.get("stale_rule_keys"):
        warns.append("데이터에서 한 번도 안 걸린 제외 규칙 (이름 변경/오타 의심): "
                     + ", ".join(d["stale_rule_keys"]))
    if not d.get("has_completed", True):
        warns.append("완성된 달이 없어 예상 고정지출·목표 대비 실적을 계산하지 않았습니다.")

    for a in bundle["accounts"]["list"]:
        if a.get("tx_count", 0) == 0:
            warns.append(f"{a['name']}: 기준일({a['as_of']}) 이후 거래가 한 건도 없어 "
                         f"시작 잔고 그대로입니다. 기준일이 미래인지 확인하세요.")
        if a["current_balance"] < 0:
            warns.append(f"{a['name']} 잔고가 음수({a['current_balance']:,}원)입니다.")

    fvt = bundle.get("fixed_vs_target", {})
    if not fvt.get("months"):
        warns.append(f"목표 대비 실적에 표시할 달이 없습니다 "
                     f"(비교 시작 {fvt.get('start')} · {fvt.get('start_source')}). "
                     f"데이터 범위보다 뒤인지 확인하세요.")
    for c in bundle.get("card_performance", {}).get("cards", []):
        if c["spent"] == 0:
            warns.append(f"{c['name']} 실적이 0원입니다. CSV '비고' 태그를 확인하세요.")
    if bundle.get("shinhan_unmatched"):
        items = ", ".join(f"{x['세부내용']}({x['금액']:,})"
                          for x in bundle["shinhan_unmatched"])
        warns.append(f"신한은행 고정지출 목록에 없는 출금: {items}")

    return warns


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "가계부9.csv"
    print(f"[1/4] CSV 로딩: {src}")
    df = load_csv(src)
    print(f"      {len(df)}건, {df['날짜'].min().date()} ~ {df['날짜'].max().date()}")

    print("[2/4] 집계 중...")
    bundle = build_bundle(df)

    print("[3/4] 정합성 검증...")
    errors = validate(bundle)
    if errors:
        print("      [실패] 다음 불일치가 발견되었습니다:")
        for e in errors:
            print("        -", e)
        sys.exit(1)
    print("      합계 일치")

    warns = warnings_for(bundle)
    if warns:
        print("      [경고] 계산은 됐지만 확인이 필요합니다:")
        for w in warns:
            print("        -", w)
    else:
        print("      경고 없음")

    with open("data_bundle.json", "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False)
    print("[4/4] data_bundle.json 저장 완료")

    k = bundle["kpi"]
    print(f"\n  총수입 {k['total_income']:,}원 / 총지출 {k['total_expense']:,}원 "
          f"/ 순잉여 {k['net']:,}원 (저축률 {k['savings_rate']}%)")
    for a in bundle["accounts"]["list"]:
        delta = a["current_balance"] - a["start_balance"]
        sign = "+" if delta > 0 else ""
        print(f"  {a['name']}: {a['current_balance']:,}원 "
              f"({a['as_of']} 기준 {a['start_balance']:,}원 → {sign}{delta:,}, "
              f"{a['tx_count']}건 반영)")


if __name__ == "__main__":
    main()
