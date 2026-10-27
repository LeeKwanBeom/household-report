"""
가계부 CSV → data_bundle.json 파이프라인

사용법:
    python3 pipeline.py 가계부9.csv

모든 비즈니스 규칙이 이 파일에 명시되어 있습니다.
규칙이 바뀌면 아래 RULES 섹션만 수정하세요.
"""
import re
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
    # 3,412,800원은 통장 앱으로 확인한 2026-08-31 마감 잔액이다(2026-09-10 실측).
    # 기준일이 8-31이라 9/1 이후 거래가 전부 차감된다. 8/29에 미리 나간
    # 커플통장 40만원은 이 숫자에 이미 반영돼 있어 계산에서 빠진다.
    "생활비 통장":   {"balance": 3412800, "as_of": "2026-08-31", "desc": "유동 지출 (카드값·경조사·기타)"},
    # 9/1 시점 잔액 29,748원. 여기에 9/2 495,252원을 넣어 525,000원을 맞췄다.
    "신한은행 통장": {"balance": 29748,   "as_of": "2026-09-01", "desc": "매달 고정비 선입금 (통신비·보험비 등)"},
    "청년미래적금":  {"balance": 1500000, "as_of": "2026-09-03", "desc": "적금 (매달 1일 생활비에서 50만원 이체)"},
    "대여금":       {"balance": 3300000, "as_of": "2026-09-01", "desc": "타인에게 빌려준 돈 (회수 시 감소)"},
    # 이관범 명의 커플통장. 2026-09-01부터 계좌로 관리한다.
    # 그 이전 40만원은 지출('내여자/커플통장')로 기록돼 있고 소급하지 않는다.
    #
    # 800,000원이 9/1 실제 잔고였으나, 9/7 정산 이전에 개별 기록하지 않은
    # 데이트 지출 323,475원이 있었다(2026-09-10 사용자 확인). 건별로 넣으면
    # 작업량이 커서 시작 잔고에서 차감해 현재 잔고를 맞췄다.
    # 800,000 - 323,475 = 476,525.
    # 이 금액은 계좌 잔고에만 반영되고 총지출 집계에는 잡히지 않는다.
    "커플통장":     {"balance": 476525,  "as_of": "2026-09-01", "desc": "커플통장 (매달 1일 생활비에서 40만원 이체 · 데이트비 정산)"},
    # 2026-09-18 신설 (사용자 지시). 시작 잔고 0원, 기준일을 첫 이체보다 앞에 둬서
    # 생활비 → 주식잔고 이체가 전부 잔고에 잡히게 한다. 평가손익은 반영하지 않고
    # 이체로 넣은 원금만 추적한다.
    "주식잔고":     {"balance": 0,       "as_of": "2026-08-31", "desc": "주식 계좌 (생활비에서 이체한 원금 기준)"},
}

# 리포트 화면의 '계좌 잔고' 섹션에서 감출 계좌 (사용자 요청, 2026-09-10)
# 잔고 계산·이체 처리·터미널 출력에는 그대로 들어간다. 화면에만 안 나온다.
# 총 잔고 합계에서도 빠지므로, 화면 합계는 보이는 계좌들의 합이다.
HIDDEN_ACCOUNTS = ["커플통장"]

# CSV의 '결제수단'·이체 '소분류'에 실제로 적히는 표기 → 계좌명
# CSV 표기가 바뀌면 여기만 고친다. 매칭 실패는 검증 단계에서 잡힌다.
ACCOUNT_KEY = {
    "생활비 통장":   "생활비",
    "신한은행 통장": "신한은행",
    "청년미래적금":  "청년미래적금",
    "대여금":       "대여금",
    "커플통장":     "커플통장",
    "주식잔고":     "주식잔고",
}

# 종료 확인된 고정 항목 → 매달 예상 고정지출 계산에서 제외
ENDED_FIXED_ITEMS = [
    "금융 / 회생",
    "주거생활비 / OTT",
    # 리모트뷰: 2026-06 종료됐다가 2026-09부터 고정 구독 재개 (2026-09-17 사용자 확인).
    # 종료 목록에서 뺐다. 9월 결제 행에 '고정' 표기가 붙어야 종료 추정에 다시 안 걸린다.
    "문화생활비 / 어플·멤버쉽 / Chat GPT",
    "문화생활비 / 어플·멤버쉽 / MIB",
    "문화생활비 / 어플·멤버쉽 / 삼성 케어",
    # 5,700원 월구독을 쓰다가 1년권으로 갈아탐. 2026-07부터 연간권 사용,
    # 월구독은 그 직전에 종료. 연간권 쪽은 아래 MANUALLY_EXCLUDED에 있다.
    "문화생활비 / 어플·멤버쉽 / 이모티콘",
]

# 사용자 요청으로 제외한 고정 항목 (연간결제성 등)
# 1년에 한 번 결제라 월평균에 넣으면 그 달만 튄다.
# (카카오 '톡서랍'은 별개이고 지금도 쓰는 월 990원 항목이다. SHINHAN_FIXED_ITEMS 참고)
MANUALLY_EXCLUDED_FIXED_ITEMS = [
    # 2026-06-25 결제, 42,000원. CSV 세부내용 표기가 '카톡 이모티콘 1년'이며
    # 이 문자열이 그대로 키가 된다(키 = 대분류 / 어플·멤버쉽 / 세부내용).
    # 예전에 '이모티콘 1년', '카톡 톡서랍 1년' 두 이름으로 적혀 있었으나
    # 둘 다 데이터에 없는 이름이어서 제외가 한 번도 적용되지 않았다. 2026-09-10 정정.
    "문화생활비 / 어플·멤버쉽 / 카톡 이모티콘 1년",
]

# 같은 구독인데 이름이 다르게 기록된 항목 → 하나로 통합
SUBSCRIPTION_ALIASES = {
    "이모티콘 월구독": "이모티콘",
}

# 고정 항목 키를 만들 때만 하나로 묶는 어플·멤버쉽 이름 (세부내용이 이 이름으로 시작하면 그 이름).
# SUBSCRIPTION_ALIASES와 달리 세부내용 자체는 바꾸지 않는다 — 거래 내역 화면에는 원본 이름이 남는다.
FIXED_APP_GROUPS = [
    # 2026-09부터 '클로드 맥스 5x'·'클로드 맥스 20x'·'클로드 추가료'로 적는다 (2026-10-12 사용자 확인)
    "클로드",
]


def fixed_app_name(name):
    """어플·멤버쉽 고정 항목 키에 쓸 이름. FIXED_APP_GROUPS 접두어면 그 이름으로 묶는다."""
    for base in FIXED_APP_GROUPS:
        if name.startswith(base):
            return base
    return name


# 결제 간격이 불규칙한 고정 항목 → 종료 추정(detect_ended.py)이 [확인 필요]로 묻는 공백 기준(개월)
# 기본 기준(2개월)보다 길게 잡는다. 예상 고정지출에는 그대로 들어간다. (2026-10-12 사용자 확인)
IRREGULAR_FIXED_ITEMS = {
    "교통비 / 대중교통": 4,
    "문화생활비 / 어플·멤버쉽 / 클로드": 4,
    "의료비 / 약값": 4,    # 알레르기 약 — 2026-09부터 이 분류로 적는다
    "뷰티 / 복용약": 13,   # 이소티논: 1년에 한 번 사서 계속 먹는다 (1년 + 한 달 여유)
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
    ("실업크레딧",   16640,  ["실업크레딧"]),  # 2026-09-29 추가 (사용자 지시)
    ("휴대폰 요금",  130000, ["휴대폰"]),
    ("신한이자",     11776,  ["이자"]),
    ("카카오 톡서랍", 990,    ["톡서랍"]),
    ("어도비",       26400,  ["어도비"]),
    ("유투브",       14900,  ["유투브", "유튜브"]),
    ("네이버 멤버쉽", 4900,   ["네이버 멤버", "네이버멤버"]),  # 2026-09-29 추가 (사용자 지시)
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

# 매달 있어야 하는 이체 (출금 계좌 표기, 입금 계좌 표기, 예상 합계)
#
# 3단계의 "이체 건 확인"을 코드로 받친다. 사람이 잊어도 누락·합계 불일치가 경고로 뜬다.
# 경고일 뿐 배포를 막지는 않으므로 3단계에서 사용자에게 묻는 절차는 그대로 유지한다.
# start = 이 검사를 시작하는 월. 계좌 관리 시작(2026-09) 이전 달에는 이체 기록이
# 없는 게 정상이라 그 달들은 보지 않는다.
EXPECTED_MONTHLY_TRANSFERS = {
    "start": "2026-09",
    "items": [
        ("생활비", "신한은행",     702000),  # SHINHAN_FIXED_ITEMS 합계(701,578원)를 올림한 금액. 2026-10부터 (2026-09-29 사용자 지시)
        ("생활비", "청년미래적금", 500000),
        ("생활비", "커플통장",     400000),
    ],
}

# 커플통장 정산 (2026-10-27 사용자 결정)
#
# 데이트 비용 중 내 카드·계좌로 내고 커플통장에서 돌려받는 것을 모아 "정산 받을 금액"을 보인다.
# 받을 금액 = 대상 합 − 받음 합. 어떤 항목이 남았는지는 대상을 날짜 순(같은 날은 CSV 순)으로 놓고
# 받음 합을 오래된 것부터 지운 나머지다. 커플통장 잔고는 여전히 보이지 않는다(HIDDEN_ACCOUNTS).
# - 대상: start 이후(그날 포함) · 구분 지출 · (대분류, 소분류) = target · 결제수단 ≠ account
#   (커플통장에서 바로 낸 것은 정산할 것이 없다. 결제수단 빈칸·신한은행도 대상이다)
# - 받음: start 이후 · 구분 이체 · 결제수단 = account · 소분류 = receive_to (세부내용은 보지 않는다)
# 화면·대화 문구는 이 값을 옮겨 적지 않고 bundle["couple_settlement"]["rule"]에서 읽는다.
COUPLE_SETTLEMENT = {
    "start": "2026-09-01",
    "target": ("내여자", "데이트비용"),
    "account": "커플통장",
    "receive_to": "생활비",
    "memo": "데이트비 정산",   # 대화의 이체 행 예시에 적는 세부내용(계산에는 쓰지 않는다)
}

# 내여자 실질 지출 (2026-10-27 사용자 결정)
#
# "실질적으로 지출된 것은 커플통장으로 이체한 것과 개인 결제뿐 — 정산 받은 것은 돈을 쓴 게 아니다."
# 시작일은 COUPLE_SETTLEMENT["start"](값을 두 번 적지 않는다). 그날부터 지출을 모으는 화면·숫자
# (KPI·월별 추이·대분류·히트맵·소분류·고정/변동·TOP15·전월 대비·예상 고정지출·목표 대비)는 실질 지출로 센다.
#   실질 지출 = 지출 행 − 정산 받은 데이트비 몫 − 커플통장에서 바로 낸 지출 + 셈할 커플통장 이체
# - 정산 받은 몫: 커플통장 정산이 오래된 대상부터 지운 몫(_settle_clear 한 곳). 걸친 행은 지워진 몫만 빠지고,
#   정산 전인 대상은 지출 그대로다. 빠지는 달 = 대상 행의 달. 받음이 더 커도 대상 합까지만 빠진다.
# - 커플통장 이체: 구분 이체 · 결제수단 fund_from · 소분류 = COUPLE_SETTLEMENT["account"].
#   귀속 달 = 비고가 'N월분'이면 그 행 날짜에서 가장 가까운 N월(12/31 '1월분' → 다음 해 1월), 아니면 날짜의 달.
#   귀속 달이 시작일의 달 이상인 것만 센다. 지출로 셀 때 (대분류, 소분류) = as_expense, 세부내용·고정여부는 그 행 값.
# - 커플통장에서 바로 낸 지출(결제수단 = 커플통장)은 그 돈을 넣을 때 이체로 이미 셌으니 시작일부터 뺀다.
# CSV 행은 그대로다 — 계좌 잔고·카드 실적·거래 내역·커플통장 정산·매달 이체 검사·신한은행 고정지출·
# 종료 추정·경고는 원래 행으로 본다. 화면·대화 문구는 bundle["real_spend"]["rule"]에서 읽는다.
COUPLE_REAL_SPEND = {
    "fund_from": "생활비",                 # 커플통장으로 보내는 계좌(셈할 이체 행의 결제수단)
    "as_expense": ("내여자", "커플통장"),    # 셈한 이체를 지출로 셀 때 (대분류, 소분류)
    "month_memo": r"(\d{1,2})\s*월분",      # 귀속 달 비고 꼴 — 비고 전체(앞뒤 공백 무시)가 이 꼴일 때만
}


# ============================================================
# CSV 로딩 — 견고한 파싱
# ============================================================

# CSV 컬럼 9개. 여기가 유일한 정의다. 하나라도 없으면 load_csv가 중단한다.
EXPECTED_COLS = ["날짜", "구분", "대분류", "소분류", "세부내용", "금액", "결제수단", "고정여부", "비고"]
TEXT_COLS = [c for c in EXPECTED_COLS if c not in ("날짜", "금액")]


def _ym(s):
    """'2026-09' → (2026, 9)"""
    y, m = s.split("-")[:2]
    return int(y), int(m)


def _now_utc():
    """현재 시각(UTC, tz-aware). 테스트가 이 함수만 고정값으로 바꿔 시각을 통제한다."""
    return pd.Timestamp.now(tz="UTC")


def today_kst():
    """미래 날짜 판정 기준일 = 한국 날짜(tz 없는 자정 Timestamp).

    컨테이너 시계는 UTC라 pd.Timestamp.today()를 쓰면 KST 00~09시에 그날 적은 행이
    '실행일보다 뒤'로 빠졌다(2026-09-27 검증 실측). 가계부는 한국에서 적으므로
    실행일은 한국 날짜 기준이다.
    """
    return _now_utc().tz_convert("Asia/Seoul").normalize().tz_localize(None)


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


def load_csv(path, today=None):
    """CSV → DataFrame.

    로딩 단계에서 빼거나 못 읽은 행은 조용히 넘기지 않고 df.attrs["load_notices"]에
    건수를 남긴다. build_bundle이 이것을 diagnostics에 실어 warnings_for()가
    [경고] 블록으로 출력한다. (예전에는 여기서 [주의]만 찍고 경고 목록에는 없어서
    "경고 없음"으로 끝날 수 있었다.)

    today: 미래 날짜 판정 기준일. None이면 실행일(한국 날짜, today_kst()). 테스트에서 고정값을 넣는다.
    """
    try:
        df = pd.read_csv(path, encoding="utf-8-sig", low_memory=False, dtype=str)
    except UnicodeDecodeError:
        raise ValueError(
            "CSV를 UTF-8로 읽을 수 없습니다. 엑셀 기본 저장(CP949)일 가능성이 큽니다 — "
            "'CSV UTF-8(쉼표로 분리)' 형식으로 다시 저장해서 올려주세요.") from None
    df.columns = [c.strip() for c in df.columns]

    # 필수 컬럼 9개 전부 있어야 한다. 예전에는 없는 컬럼을 빈 값으로 만들어 진행했고,
    # 결제수단 컬럼이 빠진 CSV가 경고 없이 잔고를 틀리게 만들었다.
    missing = [c for c in EXPECTED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV에 필요한 컬럼이 없습니다: {missing} "
                         f"(기대: {EXPECTED_COLS} / 실제: {list(df.columns)})")

    for c in TEXT_COLS:
        df[c] = df[c].fillna("").astype(str).str.strip().replace("nan", "")

    # 완전히 빈 행 제거
    df = df[df["날짜"].notna() & (df["날짜"].astype(str).str.strip() != "")]
    if len(df) == 0:
        raise ValueError("CSV에 데이터 행이 없습니다(헤더만 있음).")

    df["금액"] = df["금액"].apply(parse_amount)
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce")

    notices = {}

    bad_dates = int(df["날짜"].isna().sum())
    if bad_dates:
        print(f"  [주의] 날짜를 해석할 수 없는 행 {bad_dates}건을 건너뜁니다.")
        notices["bad_dates"] = bad_dates
    df = df.dropna(subset=["날짜"]).reset_index(drop=True)

    # 실행일보다 뒤인 날짜 행(연·월 오타)은 최신 월·업데이트일을 밀어버린다.
    # 중단하지 않고 집계에서 뺀 뒤 건수·최대 날짜를 경고로 낸다 (2026-09-26 사용자 지시).
    # 실행일은 컨테이너(UTC)가 아니라 한국 날짜다 — today_kst() 참고.
    today = pd.Timestamp(today).normalize() if today is not None else today_kst()
    future = df["날짜"] > today
    if future.any():
        n_future = int(future.sum())
        max_future = df.loc[future, "날짜"].max().strftime("%Y-%m-%d")
        print(f"  [주의] 실행일({today.date()})보다 뒤인 날짜 행 {n_future}건(최대 {max_future})을 집계에서 뺍니다.")
        notices["future_rows"] = n_future
        notices["future_max"] = max_future
        notices["today"] = str(today.date())
        df = df[~future].reset_index(drop=True)
    if len(df) == 0:
        raise ValueError("날짜를 읽을 수 있는 행이 하나도 없습니다(전부 해석 불가 또는 미래 날짜).")

    df["월"] = df["날짜"].dt.strftime("%Y-%m")

    # 구독 이름 통합
    df["세부내용"] = df["세부내용"].replace(SUBSCRIPTION_ALIASES)

    valid_types = {"수입", "지출", "이체"}
    unknown = sorted(set(df["구분"].unique()) - valid_types)
    if unknown:
        n_unknown = int(df["구분"].isin(unknown).sum())
        print(f"  [주의] 알 수 없는 '구분' 값 {unknown} — 해당 {n_unknown}행은 집계·잔고 어디에도 안 들어갑니다.")
        notices["unknown_types"] = unknown
        notices["unknown_type_rows"] = n_unknown

    # 완전히 같은 행. 실제로 별건일 수 있어(같은 날 같은 가게 다른 매장 등) 경고만 한다.
    dup_rows = int(df.duplicated(keep=False).sum())
    if dup_rows:
        notices["dup_rows"] = dup_rows
        notices["dup_groups"] = int(df[df.duplicated(keep=False)]
                                    .groupby(list(df.columns)).ngroups)

    df.attrs["load_notices"] = notices
    return df


# ============================================================
# 집계
# ============================================================

# 수입·지출 행이 0건일 때의 [중단] 메시지. scripts/detect_ended.py도 같은 상황에서 이 문구로
# 멈춘다(예전에는 "완성된 달이 없어…"로 원인을 잘못 말했다 — 2026-09-27 검증). 여기가 유일한 정의다.
NO_CORE_ROWS_MSG = ("수입·지출 행이 하나도 없습니다(이체만 있거나 '구분' 값이 전부 인식 불가). "
                    "집계할 수 없어 중단합니다.")


def account_balance(df, name, until=None):
    """계좌 잔고 → (잔고, 반영 건수).

    계좌마다 as_of가 다르므로 계좌별로 따로 자른다. as_of 당일까지의 거래는
    이미 시작 잔고에 들어 있으므로 '초과(>)' 비교를 쓴다.
    until: 이 날짜까지의 거래만 센다(None이면 상한 없음 — 리포트). PC 내역 정리
    (scripts/pc_import.py)의 잔액 대조가 은행 파일 끝 날짜까지 잘라 같은 공식을 쓴다.
    """
    meta = ACCOUNTS_START[name]
    start = meta["balance"]
    short = ACCOUNT_KEY[name]
    src = df[df["날짜"] > pd.Timestamp(meta["as_of"])]
    if until is not None:
        src = src[src["날짜"] <= pd.Timestamp(until)]
    income = src[(src["구분"] == "수입") & (src["결제수단"] == short)]["금액"].sum()
    expense = src[(src["구분"] == "지출") & (src["결제수단"] == short)]["금액"].sum()
    out = src[(src["구분"] == "이체") & (src["결제수단"] == short)]["금액"].sum()
    into = src[(src["구분"] == "이체") & (src["소분류"] == short)]["금액"].sum()
    moved = len(src[((src["결제수단"] == short) | (src["소분류"] == short))])
    return round(start + income - expense - out + into), moved


def _md(ts):
    """Timestamp → '10/23' (정산 기간·대화 줄의 짧은 날짜)."""
    return f"{ts.month}/{ts.day}"


def _settle_clear(df):
    """커플통장 정산의 대상·받음을 고르고, 받음 합(+ 환불)을 오래된 대상부터 지운다.

    커플통장 정산(couple_settlement)과 실질 지출(couple_real_spend)이 이 한 곳의 결과를 같이 쓴다 — 계산을 두 벌 두지 않는다.
    금액은 행마다 원 단위 정수. used[i] = 대상 행 i에서 지워진 몫(0 이하 행은 0).
    음수 금액(환불)은 대상 합을 줄이고, 받음과 같이 '지울 몫'이 되어 오래된 대상부터 지운다.
    그래서 남은 몫의 합은 언제나 max(0, 대상 합 − 받음 합)이다.
    """
    cs = COUPLE_SETTLEMENT
    main_cat, sub_cat = cs["target"]
    acct, dest = cs["account"], cs["receive_to"]
    src = df[df["날짜"] >= pd.Timestamp(cs["start"])]
    # kind="stable": 같은 날은 CSV 순서 그대로
    tgt = src[(src["구분"] == "지출") & (src["대분류"] == main_cat) & (src["소분류"] == sub_cat)
              & (src["결제수단"] != acct)].sort_values("날짜", kind="stable")
    rec = src[(src["구분"] == "이체") & (src["결제수단"] == acct)
              & (src["소분류"] == dest)].sort_values("날짜", kind="stable")

    amounts = [int(round(float(a))) for a in tgt["금액"]]
    received = [(r["날짜"], int(round(float(r["금액"])))) for _, r in rec.iterrows()]
    received_total = sum(a for _, a in received)
    refund_total = -sum(a for a in amounts if a < 0)

    credit = max(received_total + refund_total, 0)
    used = []
    for a in amounts:
        u = min(a, credit) if a > 0 else 0
        credit -= u
        used.append(u)
    return {"tgt": tgt, "amounts": amounts, "used": used, "received": received,
            "received_total": received_total, "refund_total": refund_total}


def couple_settlement(df, clear=None):
    """커플통장 정산(COUPLE_SETTLEMENT) → bundle["couple_settlement"].

    대상·받음·지우기는 _settle_clear 한 곳(clear를 넘기면 그 결과를 쓴다 — 실질 지출과 같은 계산).
    날짜별 소계의 합이 받을 금액과 정확히 같아야 한다(validate).
    """
    cs = COUPLE_SETTLEMENT
    main_cat, sub_cat = cs["target"]
    acct, dest = cs["account"], cs["receive_to"]
    clear = clear if clear is not None else _settle_clear(df)
    tgt, amounts, received = clear["tgt"], clear["amounts"], clear["received"]
    target_total = sum(amounts)
    received_total, refund_total = clear["received_total"], clear["refund_total"]
    owed = max(0, target_total - received_total)
    over = max(0, received_total - target_total)

    # 지워지고 남은 몫. 걸친 행은 남은 몫만 남긴다.
    left = [(r, a - u, a) for (_, r), a, u in zip(tgt.iterrows(), amounts, clear["used"]) if a > u]

    days = {}
    for r, rest, orig in left:
        days.setdefault(r["날짜"], []).append((r, rest, orig))
    day_list = []
    for d, rows in days.items():
        # 같은 날 같은 세부내용은 한 줄로 묶는다(×건수). 금액 큰 순, 같으면 먼저 나온 순.
        groups = {}
        for r, rest, orig in rows:
            name = r["세부내용"] or r["소분류"]
            g = groups.setdefault(name, {"세부내용": name, "건수": 0, "금액": 0, "일부": None})
            g["건수"] += 1
            g["금액"] += rest
            if rest != orig:
                g["일부"] = orig   # 일부만 남은 행의 원래 금액
        items = sorted(groups.values(), key=lambda g: -g["금액"])
        day_list.append({"날짜": d.strftime("%Y-%m-%d"), "건수": len(rows),
                         "소계": sum(rest for _, rest, _ in rows), "items": items})

    if left:
        first, last = left[0][0]["날짜"], left[-1][0]["날짜"]
        if first == last:
            period = _md(first)
        elif first.year == last.year:
            period = f"{_md(first)}~{_md(last)}"
        else:
            period = f"{first.year}.{_md(first)}~{last.year}.{_md(last)}"
    else:
        period = ""
    recent = [{"날짜": d.strftime("%Y-%m-%d"), "금액": a} for d, a in reversed(received[-5:])]

    # 대화 한 줄(pipeline 요약 · pc_run.py review가 그대로 찍는다 — 문구는 여기 한 곳에서만 만든다)
    if owed:
        talk = [f"{acct} 정산: 받을 금액 {owed:,}원 ({period} · {len(left)}건)",
                f"이체한 뒤 CSV에 넣을 행: 날짜 <이체한 날> · 구분 이체 · 대분류 이체 · 소분류 {dest} · "
                f"세부내용 {cs['memo']} · 금액 {owed:,} · 결제수단 {acct}"]
    else:
        last_txt = f"(마지막 정산 {_md(received[-1][0])} {received[-1][1]:,}원)" if received else ""
        talk = [f"{acct} 정산: 받을 금액 없음{last_txt}" + (f" · 더 받음 {over:,}원" if over else "")]

    return {
        "rule": {"start": cs["start"], "대분류": main_cat, "소분류": sub_cat,
                 "account": acct, "receive_to": dest, "memo": cs["memo"]},
        "target_total": target_total, "target_count": len(amounts),
        "received_total": received_total, "received_count": len(received),
        "refund_total": refund_total,
        "owed": owed, "over": over, "count": len(left), "period": period,
        "days": day_list, "recent": recent, "talk": talk,
    }


def _memo_month(date, memo):
    """이체 행 비고 'N월분' → 귀속 달 'YYYY-MM'. 그 행 날짜에서 가장 가까운 N월이다(해 넘김 — 12/31 '1월분'은
    다음 해 1월). 앞뒤 거리가 같으면(6개월) 뒤 달 — 선이체 관례. 그 꼴이 아니면(다른 비고·빈칸) None."""
    m = re.fullmatch(COUPLE_REAL_SPEND["month_memo"], str(memo).strip())
    if not m or not 1 <= int(m.group(1)) <= 12:
        return None
    here = date.year * 12 + date.month - 1
    cands = [y * 12 + int(m.group(1)) - 1 for y in (date.year - 1, date.year, date.year + 1)]
    best = min(cands, key=lambda c: (abs(c - here), -c))
    return f"{best // 12}-{best % 12 + 1:02d}"


def couple_real_spend(df, exp, months, clear):
    """내여자 실질 지출(COUPLE_REAL_SPEND) → (실질 지출 행, bundle["real_spend"]).

    exp = 원래 지출 행(구분 지출). 돌려주는 행은 거기서
    - 정산 대상 행의 금액을 _settle_clear가 지운 몫만큼 줄이고(0이 된 행은 뺀다 — 환불 행은 지울 몫에 들어갔으니 행째 뺀다),
    - 시작일부터 커플통장에서 바로 낸 지출 행을 빼고,
    - 셈할 커플통장 이체를 as_expense 지출 행으로 더한 것이다(날짜는 그대로, 비고로 다른 달에 귀속되면 그 달 1일).
    귀속 달이 아직 리포트 달(months)에 없는 이체(다음 달 몫 선이체)는 그 달 행이 생길 때까지 세지 않고 pending에 둔다.
    금액 합은 행마다 원 단위 정수 — validate가 'KPI = 원래 − 정산 받은 몫 − 커플통장 결제 + 이체'를 달마다 본다.
    """
    cs, rs = COUPLE_SETTLEMENT, COUPLE_REAL_SPEND
    acct = cs["account"]
    as_main, as_sub = rs["as_expense"]
    start = pd.Timestamp(cs["start"])
    start_m = start.strftime("%Y-%m")
    idx = {m: i for i, m in enumerate(months)}

    def by_month(pairs):
        out = [0] * len(months)
        for m, a in pairs:
            out[idx[m]] += a
        return out

    real = exp.copy()
    drop = []

    # 정산 받은 몫 — 커플통장 정산과 같은 지우기 결과
    settled = []
    for (i, r), a, u in zip(clear["tgt"].iterrows(), clear["amounts"], clear["used"]):
        d = u if a > 0 else a
        if d == 0:
            continue
        settled.append((r["월"], d))
        real.loc[i, "금액"] = real.loc[i, "금액"] - d
        if real.loc[i, "금액"] == 0:
            drop.append(i)

    # 커플통장에서 바로 낸 지출 — 넣을 때 이체로 셌다
    paid_rows = exp[(exp["날짜"] >= start) & (exp["결제수단"] == acct)]
    paid = [(r["월"], int(round(float(r["금액"])))) for _, r in paid_rows.iterrows()]
    drop += list(paid_rows.index)

    # 셈할 커플통장 이체
    tr = df[(df["구분"] == "이체") & (df["결제수단"] == rs["fund_from"]) & (df["소분류"] == acct)]
    add, counted, pending = [], [], []
    for _, r in tr.sort_values("날짜", kind="stable").iterrows():
        home = _memo_month(r["날짜"], r["비고"]) or r["월"]
        if home < start_m:
            continue
        a = int(round(float(r["금액"])))
        if home not in idx:
            pending.append({"날짜": r["날짜"].strftime("%Y-%m-%d"), "금액": a, "귀속": home})
            continue
        row = {c: r[c] for c in exp.columns if c in r.index}
        row.update({"날짜": r["날짜"] if home == r["월"] else pd.Timestamp(home + "-01"),
                    "구분": "지출", "대분류": as_main, "소분류": as_sub, "월": home})
        add.append(row)
        counted.append((home, a))

    real = real.drop(index=drop)
    if add:
        real = pd.concat([real, pd.DataFrame(add, columns=exp.columns)], ignore_index=True)

    orig_by = [round(v) for v in exp.groupby("월")["금액"].sum().reindex(months, fill_value=0)]
    info = {
        "rule": {"start": cs["start"], "account": acct, "fund_from": rs["fund_from"],
                 "대분류": as_main, "소분류": as_sub, "target": list(cs["target"]), "memo": "N월분"},
        "original_total": round(exp["금액"].sum()), "original_by_month": orig_by,
        "settled_total": sum(d for _, d in settled), "settled_by_month": by_month(settled),
        "couple_paid_total": sum(a for _, a in paid), "couple_paid_count": len(paid),
        "couple_paid_by_month": by_month(paid),
        "transfer_total": sum(a for _, a in counted), "transfer_count": len(counted),
        "transfer_by_month": by_month(counted),
        "pending": pending,
    }
    # 대화 한 줄(pipeline 요약이 그대로 찍는다 — 문구는 여기 한 곳에서만 만든다)
    talk = (f"실질 지출: 정산 받은 {cs['target'][1]} {info['settled_total']:,}원 뺌 · "
            f"{acct} 이체 {info['transfer_total']:,}원 더함")
    if paid:
        talk += f" · {acct}에서 바로 낸 {info['couple_paid_total']:,}원 뺌"
    if pending:
        talk += (f" · 다음 달 몫 {sum(p['금액'] for p in pending):,}원"
                 f"({', '.join(sorted({p['귀속'] for p in pending}))})은 그 달에 셈")
    info["talk"] = talk + f" ({cs['start']}부터)"
    return real, info


def build_bundle(df):
    # load_csv가 남긴 로딩 단계 통지 (필터링 전에 먼저 꺼낸다 — attrs는 연산을 거치며 사라질 수 있다)
    load_notices = dict(df.attrs.get("load_notices", {}))

    core = df[df["구분"].isin(["수입", "지출"])].copy()  # 이체는 수입/지출 집계 제외
    exp_rows = core[core["구분"] == "지출"].copy()       # 원래 지출 행 — 지출 집계는 아래 실질 지출(exp)로
    inc = core[core["구분"] == "수입"].copy()

    if len(core) == 0:
        raise ValueError(NO_CORE_ROWS_MSG)

    months = sorted(core["월"].unique())
    # 월 라벨. 최신 연도가 아닌 달에는 연도를 붙인다 ("2025.11월"). 연도 없이 "1월"만 쓰면
    # 데이터가 해를 넘길 때 히트맵·추이·원장 탭에 같은 라벨이 두 번 나온다.
    # build.py의 목표 대비 실적 표와 같은 규칙이다.
    latest_year = _ym(months[-1])[0]
    month_labels = []
    for m in months:
        y, mo = _ym(m)
        month_labels.append(f"{mo}월" if y == latest_year else f"{y}.{mo}월")
    N = len(months)

    # ---- 실질 지출 (COUPLE_REAL_SPEND) ----
    # 지출을 모으는 화면·숫자는 모두 exp(실질 지출 행)에서 나온다. 커플통장 정산과 같은 지우기 결과(clear)를 쓴다.
    # 원래 행 그대로 보는 것(계좌 잔고·카드 실적·월별 전체 내역·이체 검사·신한은행·진단)은 df·core를 쓴다.
    clear = _settle_clear(df)
    exp, real_spend = couple_real_spend(df, exp_rows, months, clear)

    total_income = inc["금액"].sum()
    total_expense = exp["금액"].sum()

    # ---- 월별 추이 ----
    piv = core.pivot_table(index="월", columns="구분", values="금액",
                           aggfunc="sum", fill_value=0).reindex(months, fill_value=0)
    mi = [round(v) for v in piv.get("수입", pd.Series([0] * N, index=months))]
    me = [round(v) for v in exp.groupby("월")["금액"].sum().reindex(months, fill_value=0)]
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

    fixed_rows = exp[(exp["월"].isin(completed_months)) &
                     (exp["고정여부"] == "고정")].copy()

    non_app = fixed_rows[fixed_rows["소분류"] != "어플/멤버쉽"].copy()
    non_app["키"] = non_app["대분류"] + " / " + non_app["소분류"]
    g1 = non_app.groupby(["대분류", "키"]).agg(
        등장월수=("월", "nunique"), 마지막월=("월", "max"), 합계=("금액", "sum")).reset_index()

    app = fixed_rows[fixed_rows["소분류"] == "어플/멤버쉽"].copy()
    app["대분류"] = "문화생활비"
    app["키"] = "문화생활비 / 어플·멤버쉽 / " + app["세부내용"].map(fixed_app_name)
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
    accounts_list = []
    for name, meta in ACCOUNTS_START.items():
        # 대여금도 예외 없이 계산한다. 회수 기록이 있으면 줄어야 하기 때문이다.
        bal, moved = account_balance(df, name)
        accounts_list.append({"name": name, "start_balance": meta["balance"],
                              "current_balance": bal, "desc": meta["desc"],
                              "as_of": meta["as_of"], "tx_count": moved,
                              "hidden": name in HIDDEN_ACCOUNTS})

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

    # 결제수단에 카드명이 적힌 행. 카드명은 '비고'에 가야 하고, 여기 적히면 그 행은
    # 어느 계좌에도 안 붙어 잔고에서 빠진다. 예전에는 카드명을 일부러 경고에서
    # 뺐기 때문에(위 card_tags) 아무 신호가 없었다 (2026-09-06 #1).
    _cp = core[core["결제수단"].isin(card_tags)]
    card_in_payment = {"rows": int(len(_cp)), "tags": sorted(_cp["결제수단"].unique())}

    # 기준일 이후인데 결제수단이 빈 수입·지출 행. 계좌 추적 전(8월 이전) 행은 비어 있는 게
    # 정상이라 가장 이른 기준일 이후만 본다.
    earliest_as_of = min(pd.Timestamp(m["as_of"]) for m in ACCOUNTS_START.values())
    blank_payment_after_start = int(len(core[(core["날짜"] > earliest_as_of) & (core["결제수단"] == "")]))

    # 금액이 음수·0인 행. 음수는 총지출을 줄이고 잔고를 늘린다(환불은 '수입'으로 적을 것).
    negative_rows = int((df["금액"] < 0).sum())
    zero_rows = int((df["금액"] == 0).sum())

    # 비고에 카드명이 아닌 값. 카드 실적·카드사별 결제 내역은 비고 == 카드명 정확 일치만 보므로
    # "현대 카드"처럼 적히면 실적에서 조용히 빠진다.
    _memo = core[(core["비고"] != "") & ~core["비고"].isin(card_tags)]
    unknown_memo_tags = sorted(_memo["비고"].unique())
    unknown_memo_rows = int(len(_memo))

    # 매달 있어야 하는 이체 (EXPECTED_MONTHLY_TRANSFERS) — 누락·합계 불일치
    # 터미널용(transfer_issues)은 금액을 적고, 화면용(transfer_issues_screen — '확인이 필요한 항목')은 합계 불일치를
    # 금액 없이 건수만 적는다(2026-09-27 사용자 지시). 둘은 같은 루프에서 만들어 어긋나지 않는다.
    transfer_issues = []
    transfer_issues_screen = []
    tr = df[df["구분"] == "이체"]
    for m in [x for x in months if x >= EXPECTED_MONTHLY_TRANSFERS["start"]]:
        for src_acct, dst_acct, expected_sum in EXPECTED_MONTHLY_TRANSFERS["items"]:
            hit = tr[(tr["월"] == m) & (tr["결제수단"] == src_acct) & (tr["소분류"] == dst_acct)]
            if len(hit) == 0:
                msg = f"{m} {src_acct}→{dst_acct} 이체 행 없음"
                transfer_issues.append(msg)
                transfer_issues_screen.append(msg)
            elif round(hit["금액"].sum()) != expected_sum:
                transfer_issues.append(
                    f"{m} {src_acct}→{dst_acct} 이체 합계 {round(hit['금액'].sum()):,}원 "
                    f"≠ 예상 {expected_sum:,}원 ({len(hit)}건)")
                transfer_issues_screen.append(
                    f"{m} {src_acct}→{dst_acct} 이체 합계가 예상과 다릅니다 ({len(hit)}건)")

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

    this_month_exp = core[(core["월"] == latest_month) & (core["구분"] == "지출")]   # 원래 행(카드 실적)
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
        actual = round(exp[(exp["월"] == m) & (exp["고정여부"] == "고정")]["금액"].sum())
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
        "couple_settlement": couple_settlement(df, clear),
        "real_spend": real_spend,
        "shinhan_fixed": shinhan_fixed,
        "shinhan_fixed_total": sum(x["예상금액"] for x in shinhan_fixed),
        "shinhan_month": sh_month,
        "shinhan_unmatched": shinhan_unmatched,
        # 화면이 규칙 상수를 손으로 다시 적지 않도록 bundle에 실어 보낸다
        "card_targets": CARD_TARGETS,
        "diagnostics": {"unknown_payment": unknown_payment,
                        "unknown_transfer": unknown_transfer,
                        "stale_rule_keys": stale_rule_keys,
                        "has_completed": has_completed,
                        "card_in_payment": card_in_payment,
                        "blank_payment_after_start": blank_payment_after_start,
                        "earliest_as_of": earliest_as_of.strftime("%Y-%m-%d"),
                        "negative_rows": negative_rows,
                        "zero_rows": zero_rows,
                        "unknown_memo_tags": unknown_memo_tags,
                        "unknown_memo_rows": unknown_memo_rows,
                        "transfer_issues": transfer_issues,
                        "transfer_issues_screen": transfer_issues_screen,
                        "load": load_notices},
        "card_performance": card_performance,
        "card_category_detail": card_detail,
        "fixed_vs_target": fixed_vs_target,
        "month_over_month": month_over_month,
        "months_included": months,
        # strftime("%-m월 %-d일")과 같은 문자열. %-m은 glibc 전용이라 Windows에서 ValueError가 났다.
        "latest_update": f"{latest_date.month}월 {latest_date.day}일",
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

    # 0건 가드. 위 검사들은 항등식이라 지출·수입이 전부 0이어도 "합계 일치"로
    # 통과한다. 누적 가계부에서 총지출·총수입 0은 데이터가 통째로 빠진 것이다.
    if kpi_exp == 0:
        errors.append("총지출이 0원입니다 — 지출 행이 하나도 집계되지 않았습니다.")
    if kpi_inc == 0:
        errors.append("총수입이 0원입니다 — 수입 행이 하나도 집계되지 않았습니다.")

    # 커플통장 정산: 받을 금액 = max(0, 대상 합 − 받음 합) · 날짜별 소계 합 = 받을 금액
    cs = bundle.get("couple_settlement")
    if cs is not None:
        label = f"{cs['rule']['account']} 정산"
        want = max(0, cs["target_total"] - cs["received_total"])
        if cs["owed"] != want:
            errors.append(f"{label} 받을 금액({cs['owed']:,}) != max(0, 대상 합 − 받음 합)({want:,})")
        days_sum = sum(d["소계"] for d in cs["days"])
        if days_sum != cs["owed"]:
            errors.append(f"{label} 날짜별 소계 합({days_sum:,}) != 받을 금액({cs['owed']:,})")
        if sum(d["건수"] for d in cs["days"]) != cs["count"]:
            errors.append(f"{label} 날짜별 건수 합 != 남은 건수({cs['count']})")
        for d in cs["days"]:
            if (sum(i["금액"] for i in d["items"]) != d["소계"]
                    or sum(i["건수"] for i in d["items"]) != d["건수"]):
                errors.append(f"{label} {d['날짜']} 항목 합이 그날 소계·건수와 다릅니다")

    # 실질 지출: KPI 총지출 = 원래 지출 합 − 정산 받은 몫 − 커플통장에서 바로 낸 지출 + 셈한 커플통장 이체(달마다도).
    # 위 '월별 추이 합 = 대분류 합 = … = KPI' 검사는 이미 실질 값끼리 맞는지를 본다.
    rs = bundle.get("real_spend")
    if rs is not None:
        acct = rs["rule"]["account"]
        parts = ("original", "settled", "couple_paid", "transfer")
        want = rs["original_total"] - rs["settled_total"] - rs["couple_paid_total"] + rs["transfer_total"]
        if kpi_exp != want:
            errors.append(f"실질 지출: KPI 총지출({kpi_exp:,}) != 원래 지출 합 − 정산 받은 몫 − "
                          f"{acct}에서 바로 낸 지출 + {acct} 이체({want:,})")
        for p in parts:
            if sum(rs[f"{p}_by_month"]) != rs[f"{p}_total"]:
                errors.append(f"실질 지출: {p} 달별 합({sum(rs[f'{p}_by_month']):,}) != 합계({rs[f'{p}_total']:,})")
        labels, me = bundle["monthly"]["labels"], bundle["monthly"]["expense"]
        if any(len(rs[f"{p}_by_month"]) != len(labels) for p in parts):
            errors.append("실질 지출: 달별 목록 길이가 월별 추이와 다릅니다")
        else:
            for i, lab in enumerate(labels):
                w = (rs["original_by_month"][i] - rs["settled_by_month"][i]
                     - rs["couple_paid_by_month"][i] + rs["transfer_by_month"][i])
                if me[i] != w:
                    errors.append(f"실질 지출: {lab} 월별 지출({me[i]:,}) != 원래 − 정산 받은 몫 − "
                                  f"{acct}에서 바로 낸 지출 + {acct} 이체({w:,})")
        # 정산 받은 몫 = 커플통장 정산이 지운 몫 = 대상 합 − 받을 금액(받음이 더 커도 대상 합까지만)
        if cs is not None and rs["settled_total"] != cs["target_total"] - cs["owed"]:
            errors.append(f"실질 지출: 정산 받은 몫({rs['settled_total']:,}) != "
                          f"{cs['rule']['account']} 정산 대상 합 − 받을 금액({cs['target_total'] - cs['owed']:,})")

    return errors


def warnings_for(bundle, screen=False):
    """중단시킬 정도는 아니지만 조용히 틀린 값을 만드는 상황들.

    위 validate()는 같은 데이터를 다시 집계해 비교하는 항등식이라 구조적으로
    거의 실패하지 않는다. 실제 사고는 대부분 '규칙이 데이터와 안 맞는' 쪽에서
    난다 — 계좌명 오타로 거래가 잔고에서 증발하거나, 제외 규칙 키가 옛날
    이름이라 종료된 항목이 계속 예상치에 들어가는 식이다.

    screen=True 는 화면('확인이 필요한 항목')용 문구. 지금은 "매달 이체 확인"의 합계 불일치만 다르다
    (금액 없이 건수만 — diagnostics["transfer_issues_screen"]). 그 외 문구는 터미널과 같다.

    여기 종류를 추가하면 SKILL.md 5단계 경고 목록도 같이 갱신할 것.
    """
    d = bundle.get("diagnostics", {})
    ld = d.get("load", {})
    warns = []

    # ---- 로딩 단계에서 뺀 행 (예전에는 [주의]로만 찍혀 경고 목록에 없었다) ----
    if ld.get("bad_dates"):
        warns.append(f"날짜를 해석할 수 없어 뺀 행 {ld['bad_dates']}건 (YYYY-MM-DD 형식인지 확인)")
    if ld.get("future_rows"):
        warns.append(f"실행일({ld.get('today')})보다 뒤인 날짜 행 {ld['future_rows']}건"
                     f"(최대 {ld.get('future_max')})을 집계에서 뺐습니다. 연·월 오타인지 확인하세요.")
    if ld.get("unknown_types"):
        warns.append(f"알 수 없는 '구분' 값 {ld['unknown_types']} 행 {ld.get('unknown_type_rows', 0)}건 "
                     f"→ 집계·잔고 어디에도 안 들어갔습니다 (수입/지출/이체 중 하나여야 함)")
    if ld.get("dup_rows"):
        warns.append(f"완전히 같은 행 {ld['dup_rows']}건({ld.get('dup_groups', 0)}묶음) — "
                     f"같은 날 같은 금액이 실제 별건이면 무시해도 됩니다")

    # ---- 결제수단·비고 표기 ----
    if d.get("unknown_payment"):
        warns.append("어느 계좌에도 속하지 않는 결제수단 → 잔고에 반영 안 됨: "
                     + ", ".join(d["unknown_payment"]))
    cip = d.get("card_in_payment") or {}
    if cip.get("rows"):
        warns.append(f"결제수단에 카드명이 적힌 행 {cip['rows']}건({', '.join(cip['tags'])}) → 잔고에 반영 안 됨. "
                     f"카드명은 '비고'에, 결제수단에는 계좌명을 적으세요.")
    if d.get("blank_payment_after_start"):
        warns.append(f"기준일({d.get('earliest_as_of')}) 이후인데 결제수단이 빈 수입·지출 행 "
                     f"{d['blank_payment_after_start']}건 → 잔고에 반영 안 됨")
    if d.get("unknown_memo_tags"):
        warns.append(f"비고에 카드명이 아닌 값 {d['unknown_memo_tags']} 행 {d.get('unknown_memo_rows', 0)}건 "
                     f"→ 카드 실적에 안 잡힘 (카드명 정확 일치만 인식)")
    if d.get("unknown_transfer"):
        warns.append("계좌로 인식되지 않는 이체 상대: "
                     + ", ".join(sorted(set(d["unknown_transfer"]))))

    # ---- 금액 ----
    if d.get("negative_rows"):
        warns.append(f"금액이 음수인 행 {d['negative_rows']}건 → 총지출이 줄고 잔고가 늘어납니다 "
                     f"(환불이면 '수입'으로 적으세요)")
    if d.get("zero_rows"):
        warns.append(f"금액이 0원·빈칸인 행 {d['zero_rows']}건")

    # ---- 이체 ----
    for issue in d.get("transfer_issues_screen" if screen else "transfer_issues", []):
        warns.append("매달 이체 확인: " + issue)

    # ---- 커플통장 정산 ----
    # 문구에 정산 계좌(커플통장 — HIDDEN_ACCOUNTS)가 들어가 screen_warnings_for가 화면에서 뺀다 → 대화로만.
    # 화면에서는 정산 섹션이 "더 받음"을 보인다.
    cs = bundle.get("couple_settlement") or {}
    if cs.get("over"):
        rule = cs["rule"]
        warns.append(f"{rule['account']} 정산: 받은 정산이 대상보다 {cs['over']:,}원 많습니다(더 받음) — "
                     f"{rule['account']}→{rule['receive_to']} 이체 금액이나 "
                     f"'{rule['대분류']}/{rule['소분류']}' 분류를 확인하세요")

    # ---- 규칙·기간 ----
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


def screen_warnings_for(bundle):
    """리포트 화면 '확인이 필요한 항목'에 올릴 경고.

    터미널 경고와 같되, 화면에서 감춘 계좌(HIDDEN_ACCOUNTS)를 언급하는 경고는
    뺀다 — 계좌를 화면에서 뺀 이유(2026-09-10)가 그대로 적용된다. 그 경고는
    대화로만 전달된다. "매달 이체 확인"의 합계 불일치는 금액 없이 건수만 싣는다
    (warnings_for(screen=True), 2026-09-27).
    """
    return [w for w in warnings_for(bundle, screen=True)
            if not any(h in w for h in HIDDEN_ACCOUNTS)]


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "가계부9.csv"
    print(f"[1/4] CSV 로딩: {src}")
    try:
        df = load_csv(src)
        print(f"      {len(df)}건, {df['날짜'].min().date()} ~ {df['날짜'].max().date()}")

        print("[2/4] 집계 중...")
        bundle = build_bundle(df)
    except ValueError as e:
        # 입력 자체가 잘못된 경우. 트레이스백 대신 원인을 한 줄로.
        print(f"      [중단] {e}")
        sys.exit(1)

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

    # 경고를 bundle에 실어 build.py가 화면 '확인이 필요한 항목'에 그대로 렌더한다.
    # (예전에는 pipeline이 계산만 하고 화면은 하드코딩 2줄이었다 — 2026-09-06 #3)
    bundle["warnings"] = screen_warnings_for(bundle)

    with open("data_bundle.json", "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False)
    print("[4/4] data_bundle.json 저장 완료")

    k = bundle["kpi"]
    print(f"\n  총수입 {k['total_income']:,}원 / 총지출 {k['total_expense']:,}원 "
          f"/ 순잉여 {k['net']:,}원 (저축률 {k['savings_rate']}%)")
    print(f"  {bundle['real_spend']['talk']}")
    for a in bundle["accounts"]["list"]:
        delta = a["current_balance"] - a["start_balance"]
        sign = "+" if delta > 0 else ""
        print(f"  {a['name']}: {a['current_balance']:,}원 "
              f"({a['as_of']} 기준 {a['start_balance']:,}원 → {sign}{delta:,}, "
              f"{a['tx_count']}건 반영)")
    for ln in bundle["couple_settlement"]["talk"]:
        print(f"  {ln}")


if __name__ == "__main__":
    # 콘솔이 cp949(Windows)면 경고 줄의 '—'에서 UnicodeEncodeError로 bundle을 쓰기 전에 죽는다.
    # main() 안에 두면 시험의 redirect_stdout(StringIO)에서 깨지므로 여기 둔다.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
