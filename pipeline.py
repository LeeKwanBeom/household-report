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
ACCOUNTS_START = {
    "생활비 통장":   {"balance": 3342548, "as_of": "2026-09-01", "desc": "유동 지출 (카드값·경조사·기타)"},
    "신한은행 통장": {"balance": 0,       "as_of": "2026-09-01", "desc": "매달 고정비 선입금 (통신비·보험비 등)"},
    "청년미래적금":  {"balance": 1500000, "as_of": "2026-09-03", "desc": "적금 (매달 1일 생활비에서 50만원 이체)"},
    "대여금":       {"balance": 3300000, "as_of": "2026-09-04", "desc": "타인에게 빌려준 돈 (회수 시 감소)"},
}

# 계좌 잔고 추적 시작일 (이 날짜 이후 거래만 잔고 계산에 반영)
BALANCE_TRACKING_START = "2026-09-01"

# 시작 잔고에 이미 반영된 이체 → 잔고 계산에서 영구 제외 (이중계산 방지)
# (날짜, 입금계좌) 조합으로 식별하며, 출금·입금 양쪽 모두 제외됨
TRANSFERS_ALREADY_IN_START_BALANCE = [
    ("2026-09-01", "청년미래적금"),
]

# 종료 확인된 고정 항목 → 매달 예상 고정지출 계산에서 제외
ENDED_FIXED_ITEMS = [
    "금융 / 회생",
    "주거생활비 / OTT",
    "문화생활비 / 어플·멤버쉽 / 리모트뷰",
    "문화생활비 / 어플·멤버쉽 / Chat GPT",
    "문화생활비 / 어플·멤버쉽 / MIB",
    "문화생활비 / 어플·멤버쉽 / 삼성 케어",
]

# 사용자 요청으로 제외한 고정 항목 (연간결제성 등)
MANUALLY_EXCLUDED_FIXED_ITEMS = [
    "문화생활비 / 어플·멤버쉽 / 카톡 톡서랍 1년",
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

# 목표 대비 실적 비교 시작 월 (회생·리모트뷰 등 종료 항목이 다 정리된 시점)
TARGET_COMPARISON_START = "2026-09"


# ============================================================
# CSV 로딩 — 견고한 파싱
# ============================================================

TEXT_COLS = ["구분", "대분류", "소분류", "세부내용", "결제수단", "고정여부", "비고"]


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
    last_completed = pd.to_datetime(completed_months[-1] + "-01")

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
    g["월평균"] = (g["합계"] / n_completed).round(0)

    excluded_keys = ENDED_FIXED_ITEMS + MANUALLY_EXCLUDED_FIXED_ITEMS
    proj = g[~g["키"].isin(excluded_keys)].sort_values("월평균", ascending=False)
    projection_items = [
        {"대분류": r["대분류"], "항목": r["키"], "마지막월": r["마지막월"],
         "공백": int(r["공백"]), "합계": round(r["합계"]), "월평균": round(r["월평균"]),
         "상태": "정상"}
        for _, r in proj.iterrows()
    ]
    projection_total = round(sum(p["월평균"] for p in projection_items))

    excluded_items = []
    for _, r in g[g["키"].isin(ENDED_FIXED_ITEMS)].iterrows():
        excluded_items.append({"항목": r["키"], "마지막월": r["마지막월"],
                               "최근8개월합계": round(r["합계"]), "사유": "종료 확인"})
    for _, r in g[g["키"].isin(MANUALLY_EXCLUDED_FIXED_ITEMS)].iterrows():
        excluded_items.append({"항목": r["키"], "마지막월": r["마지막월"],
                               "최근8개월합계": round(r["합계"]), "사유": "제외 요청(연간결제성)"})

    # ---- 계좌 잔고 ----
    tracked = df[df["날짜"] >= BALANCE_TRACKING_START].copy()

    # 시작 잔고에 이미 반영된 이체는 양쪽 모두 제외
    already_counted = pd.Series(False, index=tracked.index)
    for d, to_account in TRANSFERS_ALREADY_IN_START_BALANCE:
        already_counted |= ((tracked["구분"] == "이체") &
                            (tracked["소분류"] == to_account) &
                            (tracked["날짜"] == pd.Timestamp(d)))
    bal_src = tracked[~already_counted]

    def account_balance(name):
        start = ACCOUNTS_START[name]["balance"]
        short = name.replace(" 통장", "")
        income = bal_src[(bal_src["구분"] == "수입") & (bal_src["결제수단"] == short)]["금액"].sum()
        expense = bal_src[(bal_src["구분"] == "지출") & (bal_src["결제수단"] == short)]["금액"].sum()
        out = bal_src[(bal_src["구분"] == "이체") & (bal_src["결제수단"] == short)]["금액"].sum()
        into = bal_src[(bal_src["구분"] == "이체") & (bal_src["소분류"] == short)]["금액"].sum()
        return round(start + income - expense - out + into)

    accounts_list = []
    for name, meta in ACCOUNTS_START.items():
        bal = meta["balance"] if name == "대여금" else account_balance(name)
        accounts_list.append({"name": name, "start_balance": meta["balance"],
                              "current_balance": bal, "desc": meta["desc"]})

    # ---- 신한은행 고정지출 예상 vs 실제 ----
    sh = tracked[(tracked["구분"] == "지출") & (tracked["결제수단"] == "신한은행")].copy()
    sh["검색"] = sh["대분류"] + " " + sh["소분류"] + " " + sh["세부내용"]
    shinhan_fixed = []
    for name, expected, keywords in SHINHAN_FIXED_ITEMS:
        if keywords is None:
            m = sh[(sh["소분류"] == "유류비") | (sh["대분류"] == "교통비")]
        else:
            m = sh[sh["검색"].str.contains("|".join(keywords), na=False)]
        shinhan_fixed.append({"항목": name, "예상금액": expected,
                              "출금액": round(m["금액"].sum()), "matched": len(m) > 0})

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
    fvt_months = []
    for m in [x for x in months if x >= TARGET_COMPARISON_START]:
        actual = round(core[(core["월"] == m) & (core["구분"] == "지출") &
                            (core["고정여부"] == "고정")]["금액"].sum())
        is_current = (m == latest_month)
        fvt_months.append({"월": m, "실제": actual, "목표": projection_total,
                           "차이": actual - projection_total, "진행중": is_current})
    fixed_vs_target = {"target": projection_total, "months": fvt_months,
                       "elapsed_ratio": round(elapsed_ratio * 100, 1)}

    # ---- 카테고리별 전월 대비 증감 ----
    mom = []
    if len(months) >= 2:
        prev_m, curr_m = months[-2], months[-1]
        prev_s = exp[exp["월"] == prev_m].groupby("대분류")["금액"].sum()
        curr_s = exp[exp["월"] == curr_m].groupby("대분류")["금액"].sum()
        for c in set(prev_s.index) | set(curr_s.index):
            p, cu = round(prev_s.get(c, 0)), round(curr_s.get(c, 0))
            mom.append({"대분류": c, "전월": p, "당월": cu, "증감": cu - p})
        mom.sort(key=lambda x: -abs(x["증감"]))
    month_over_month = {"prev": months[-2] if len(months) >= 2 else None,
                        "curr": months[-1], "items": mom[:10]}

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
        "monthly_tx": monthly_tx,
        "projection": {"months_used": n_completed, "completed_months": completed_months,
                       "total": projection_total, "annual": projection_total * 12,
                       "items": projection_items, "excluded": excluded_items},
        "accounts": {"as_of": BALANCE_TRACKING_START, "list": accounts_list},
        "shinhan_fixed": shinhan_fixed,
        "shinhan_fixed_total": sum(x["예상금액"] for x in shinhan_fixed),
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
    print("      통과 (모든 섹션 합계 일치)")

    with open("data_bundle.json", "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False)
    print("[4/4] data_bundle.json 저장 완료")

    k = bundle["kpi"]
    print(f"\n  총수입 {k['total_income']:,}원 / 총지출 {k['total_expense']:,}원 "
          f"/ 순잉여 {k['net']:,}원 (저축률 {k['savings_rate']}%)")
    for a in bundle["accounts"]["list"]:
        print(f"  {a['name']}: {a['current_balance']:,}원")


if __name__ == "__main__":
    main()
