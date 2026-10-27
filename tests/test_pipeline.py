"""
회귀 테스트 — pipeline.py · scripts/deploy.py

실행:
    cd /home/claude/gagyebu && python3 -m unittest discover tests

fixture(tests/fixtures/sample.csv)는 합성·익명 데이터만 쓴다. 실제 가계부 CSV 행을
복사해 넣지 않는다 (저장소가 Public이다).

2026-09-26 정기 점검 1회차의 실측 스크립트를 옮긴 것이다. 각 테스트가 무엇을
지키는지는 audit/last-audit.md의 결함 번호로 적어 두었다.
"""
import copy
import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pipeline  # noqa: E402
from pipeline import (  # noqa: E402
    ACCOUNTS_START, ACCOUNT_KEY, COUPLE_REAL_SPEND, COUPLE_SETTLEMENT, ENDED_FIXED_ITEMS, EXPECTED_COLS,
    EXPECTED_MONTHLY_TRANSFERS, HIDDEN_ACCOUNTS, MANUALLY_EXCLUDED_FIXED_ITEMS,
    NO_CORE_ROWS_MSG, SUBSCRIPTION_ALIASES, build_bundle, fixed_app_name, load_csv,
    screen_warnings_for, today_kst, validate, warnings_for,
)
import deploy  # noqa: E402
import detect_ended  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "sample.csv")
TODAY = "2026-09-26"  # fixture의 마지막 행(09-25)보다 뒤, 미래 날짜 테스트 행(10-05)보다 앞


def fixture_rows():
    return pd.read_csv(FIXTURE, encoding="utf-8-sig", dtype=str).fillna("")


def run_rows(df, today=TODAY, encoding="utf-8-sig", raw_text=None):
    """DataFrame(또는 raw 텍스트)을 임시 CSV로 써서 load_csv → build_bundle까지 돌린다.
    로딩 단계 print는 삼킨다."""
    tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                      encoding=encoding, newline="")
    if raw_text is not None:
        tmp.write(raw_text)
    else:
        df.to_csv(tmp, index=False)
    tmp.close()
    try:
        with redirect_stdout(io.StringIO()):
            loaded = load_csv(tmp.name, today=today)
            bundle = build_bundle(loaded)
    finally:
        os.unlink(tmp.name)
    return loaded, bundle


def add_row(df, **kw):
    row = {c: "" for c in EXPECTED_COLS}
    row.update(kw)
    return pd.concat([df, pd.DataFrame([row])], ignore_index=True)


def account(bundle, name):
    return next(a for a in bundle["accounts"]["list"] if a["name"] == name)


def amt(s):
    return float(str(s).replace(",", "") or 0)


class FixtureSanity(unittest.TestCase):
    def setUp(self):
        self.df, self.bundle = run_rows(fixture_rows())

    def test_validate_passes(self):
        self.assertEqual(validate(self.bundle), [])

    def test_no_transfer_issues_in_fixture(self):
        self.assertEqual(self.bundle["diagnostics"]["transfer_issues"], [])

    def test_balances_match_independent_calculation(self):
        """계좌 잔고 = 시작 잔고 + as_of 다음날부터의 (수입 - 지출 - 이체출금 + 이체입금)."""
        raw = fixture_rows()
        raw["날짜"] = pd.to_datetime(raw["날짜"])
        raw["금액"] = raw["금액"].map(amt)
        for name, meta in ACCOUNTS_START.items():
            short = ACCOUNT_KEY[name]
            s = raw[raw["날짜"] > pd.Timestamp(meta["as_of"])]
            inc = s[(s["구분"] == "수입") & (s["결제수단"] == short)]["금액"].sum()
            exp = s[(s["구분"] == "지출") & (s["결제수단"] == short)]["금액"].sum()
            out = s[(s["구분"] == "이체") & (s["결제수단"] == short)]["금액"].sum()
            into = s[(s["구분"] == "이체") & (s["소분류"] == short)]["금액"].sum()
            expected = round(meta["balance"] + inc - exp - out + into)
            self.assertEqual(account(self.bundle, name)["current_balance"], expected, name)

    def test_projection_total_independent(self):
        """예상 고정지출 = 완성된 달(최신 월 제외)의 고정 항목 합계 ÷ 완성 월수, 제외 키 빼고."""
        raw = fixture_rows()
        raw["금액"] = raw["금액"].map(amt)
        raw["월"] = raw["날짜"].str[:7]
        core = raw[raw["구분"].isin(["수입", "지출"])]
        months = sorted(core["월"].unique())
        comp = months[:-1]
        fx = core[(core["구분"] == "지출") & core["월"].isin(comp) & (core["고정여부"] == "고정")].copy()
        fx["세부내용"] = fx["세부내용"].replace(SUBSCRIPTION_ALIASES)
        fx["키"] = fx.apply(lambda r: "문화생활비 / 어플·멤버쉽 / " + fixed_app_name(r["세부내용"])
                          if r["소분류"] == "어플/멤버쉽" else r["대분류"] + " / " + r["소분류"], axis=1)
        g = fx.groupby("키")["금액"].sum()
        g = g[~g.index.isin(ENDED_FIXED_ITEMS + MANUALLY_EXCLUDED_FIXED_ITEMS)]
        expected = round(sum((g / len(comp)).round(0)))
        self.assertEqual(self.bundle["projection"]["months_used"], len(comp))
        self.assertEqual(self.bundle["projection"]["total"], expected)

    def test_month_labels_single_year(self):
        self.assertEqual(self.bundle["monthly"]["labels"], ["7월", "8월", "9월"])

    def test_screen_warnings_exclude_hidden_accounts(self):
        """감춘 계좌를 언급하는 경고는 화면용 목록에서 빠진다 (결함 6 보완)."""
        b = copy.deepcopy(self.bundle)
        hidden = HIDDEN_ACCOUNTS[0]
        account(b, hidden)["current_balance"] = -1
        self.assertTrue(any(hidden in w for w in warnings_for(b)))
        self.assertFalse(any(hidden in w for w in screen_warnings_for(b)))


class ValidateChecks(unittest.TestCase):
    """validate() 6개 검사가 각각 살아 있는지 + 0건 가드 (결함 9)."""

    def setUp(self):
        _, self.bundle = run_rows(fixture_rows())

    def _broken(self, mutate):
        b = copy.deepcopy(self.bundle)
        mutate(b)
        return validate(b)

    def test_each_check_fails_when_broken(self):
        muts = {
            "월별 추이 지출 합": lambda b: b["monthly"]["expense"].__setitem__(0, b["monthly"]["expense"][0] + 1),
            "대분류 합": lambda b: b["category"][0].__setitem__("합계", b["category"][0]["합계"] + 1),
            "고정/변동 합": lambda b: b["fixed_variable"][0].__setitem__("고정", b["fixed_variable"][0]["고정"] + 1),
            "히트맵 합": lambda b: b["heatmap"]["matrix"][0].__setitem__(0, b["heatmap"]["matrix"][0][0] + 1),
            "월별 추이 수입 합": lambda b: b["monthly"]["income"].__setitem__(0, b["monthly"]["income"][0] + 1),
            "수입원 합": lambda b: b["income_breakdown"][0].__setitem__("합계", b["income_breakdown"][0]["합계"] + 1),
        }
        for label, mut in muts.items():
            errs = self._broken(mut)
            self.assertTrue(errs and label in errs[0], f"{label}: {errs}")

    def test_zero_guard(self):
        def zero(b):
            n = len(b["monthly"]["expense"])
            b["kpi"]["total_expense"] = 0
            b["monthly"]["expense"] = [0] * n
            b["category"] = []
            b["fixed_variable"] = []
            b["heatmap"]["matrix"] = []
            b["kpi"]["total_income"] = 0
            b["monthly"]["income"] = [0] * n
            b["income_breakdown"] = []
        errs = self._broken(zero)
        self.assertTrue(any("총지출이 0원" in e for e in errs), errs)
        self.assertTrue(any("총수입이 0원" in e for e in errs), errs)


class LoadCsvGuards(unittest.TestCase):
    """입력이 잘못됐을 때 원인을 말하며 멈추는지 (결함 2, 개선안 3)."""

    def test_missing_column_raises(self):
        for col in ["결제수단", "고정여부", "구분", "비고", "세부내용", "금액", "날짜"]:
            with self.subTest(col=col):
                with self.assertRaises(ValueError) as cm:
                    run_rows(fixture_rows().drop(columns=[col]))
                self.assertIn(col, str(cm.exception))

    def test_column_order_and_spaces_are_fine(self):
        df = fixture_rows()
        df = df[list(reversed(df.columns))]
        df.columns = ["  " + c + " " for c in df.columns]
        _, b = run_rows(df)
        self.assertEqual(validate(b), [])

    def test_cp949_gives_message(self):
        with open(FIXTURE, encoding="utf-8-sig") as f:
            text = f.read()
        with self.assertRaises(ValueError) as cm:
            run_rows(None, raw_text=text, encoding="cp949")
        self.assertIn("UTF-8", str(cm.exception))

    def test_empty_csv_raises(self):
        with self.assertRaises(ValueError) as cm:
            run_rows(fixture_rows().iloc[0:0])
        self.assertIn("데이터 행이 없습니다", str(cm.exception))

    def test_transfer_only_raises(self):
        df = fixture_rows()
        with self.assertRaises(ValueError) as cm:
            run_rows(df[df["구분"] == "이체"])
        self.assertIn("수입·지출 행", str(cm.exception))


class Warnings(unittest.TestCase):
    """조용히 틀리던 경로가 이제 경고를 내는지 (결함 1·3·4·10, 개선안 2·3)."""

    def _warns(self, df, today=TODAY):
        _, b = run_rows(df, today=today)
        return warnings_for(b), b

    def test_card_name_in_payment_warns(self):
        df = add_row(fixture_rows(), 날짜="2026-09-21", 구분="지출", 대분류="식비", 소분류="외식",
                     세부내용="실험", 금액="900,000", 결제수단="현대카드", 비고="현대카드")
        warns, b = self._warns(df)
        self.assertTrue(any("결제수단에 카드명" in w for w in warns), warns)
        self.assertEqual(b["diagnostics"]["card_in_payment"]["rows"], 1)

    def test_blank_payment_after_start_warns(self):
        df = add_row(fixture_rows(), 날짜="2026-09-21", 구분="지출", 대분류="식비", 소분류="외식",
                     세부내용="실험", 금액="900,000", 결제수단="")
        warns, b = self._warns(df)
        self.assertTrue(any("결제수단이 빈" in w for w in warns), warns)
        self.assertEqual(b["diagnostics"]["blank_payment_after_start"], 1)

    def test_blank_payment_before_start_is_normal(self):
        _, b = run_rows(fixture_rows())  # 7·8월 행은 결제수단이 비어 있다
        self.assertEqual(b["diagnostics"]["blank_payment_after_start"], 0)

    def test_future_date_excluded_and_warned(self):
        df = add_row(fixture_rows(), 날짜="2026-10-05", 구분="지출", 대분류="식비", 소분류="외식",
                     세부내용="실험", 금액="900,000", 결제수단="생활비")
        warns, b = self._warns(df)
        self.assertTrue(any("실행일" in w and "1건" in w and "2026-10-05" in w for w in warns), warns)
        self.assertEqual(b["months_included"][-1], "2026-09")
        self.assertEqual(b["card_performance"]["month"], "2026-09")
        _, base = run_rows(fixture_rows())
        self.assertEqual(b["kpi"]["total_expense"], base["kpi"]["total_expense"])
        self.assertEqual(account(b, "생활비 통장")["current_balance"],
                         account(base, "생활비 통장")["current_balance"])

    def test_bad_date_warned(self):
        df = add_row(fixture_rows(), 날짜="2026/09/21", 구분="지출", 대분류="식비", 소분류="외식",
                     세부내용="실험", 금액="900,000", 결제수단="생활비")
        warns, _ = self._warns(df)
        self.assertTrue(any("날짜를 해석할 수 없어" in w for w in warns), warns)

    def test_unknown_type_warned(self):
        df = add_row(fixture_rows(), 날짜="2026-09-21", 구분="지 출", 대분류="식비", 소분류="외식",
                     세부내용="실험", 금액="900,000", 결제수단="생활비")
        warns, _ = self._warns(df)
        self.assertTrue(any("알 수 없는 '구분'" in w for w in warns), warns)

    def test_negative_and_zero_amount_warned(self):
        df = add_row(fixture_rows(), 날짜="2026-09-21", 구분="지출", 대분류="식비", 소분류="외식",
                     세부내용="실험", 금액="-900,000", 결제수단="생활비")
        df = add_row(df, 날짜="2026-09-22", 구분="지출", 대분류="식비", 소분류="외식",
                     세부내용="실험", 금액="", 결제수단="생활비")
        warns, _ = self._warns(df)
        self.assertTrue(any("음수인 행 1건" in w for w in warns), warns)
        self.assertTrue(any("0원·빈칸인 행 1건" in w for w in warns), warns)

    def test_duplicate_rows_warned_not_stopped(self):
        df = fixture_rows()
        df = pd.concat([df, df.tail(1)], ignore_index=True)
        warns, b = self._warns(df)
        self.assertTrue(any("완전히 같은 행 2건" in w for w in warns), warns)
        self.assertEqual(validate(b), [])

    def test_unknown_memo_tag_warned(self):
        df = add_row(fixture_rows(), 날짜="2026-09-21", 구분="지출", 대분류="식비", 소분류="외식",
                     세부내용="실험", 금액="900,000", 결제수단="생활비", 비고="현대 카드")
        warns, _ = self._warns(df)
        self.assertTrue(any("비고에 카드명이 아닌 값" in w for w in warns), warns)

    def test_transfer_missing_warned(self):
        df = fixture_rows()
        df = df[~((df["구분"] == "이체") & (df["소분류"] == "청년미래적금"))]
        warns, _ = self._warns(df)
        self.assertTrue(any("생활비→청년미래적금 이체 행 없음" in w for w in warns), warns)

    def test_transfer_sum_mismatch_warned(self):
        df = fixture_rows()
        i = df[(df["구분"] == "이체") & (df["소분류"] == "신한은행")].index[0]
        df.loc[i, "금액"] = "68,000"
        warns, _ = self._warns(df)
        self.assertTrue(any("생활비→신한은행 이체 합계" in w and "≠ 예상" in w for w in warns), warns)

    def test_transfer_check_skips_months_before_start(self):
        """계좌 관리 시작(start) 이전 달에는 이체가 없는 게 정상 — 경고 없음."""
        _, b = run_rows(fixture_rows())
        issues = b["diagnostics"]["transfer_issues"]
        self.assertFalse(any(m < EXPECTED_MONTHLY_TRANSFERS["start"] for m in
                             [x.split()[0] for x in issues]), issues)

    def test_transfer_sum_mismatch_screen_has_no_amount(self):
        """화면 14번(screen_warnings_for)에는 합계 불일치를 금액 없이 건수만 싣고,
        터미널(warnings_for)에는 금액을 그대로 둔다 (2026-09-27 정정·수정 회차 2, 선택 7)."""
        df = fixture_rows()
        i = df[(df["구분"] == "이체") & (df["소분류"] == "신한은행")].index[0]
        df.loc[i, "금액"] = "68,000"
        _, b = run_rows(df)
        terminal = [w for w in warnings_for(b) if w.startswith("매달 이체 확인:")]
        screen = [w for w in screen_warnings_for(b) if w.startswith("매달 이체 확인:")]
        self.assertEqual(len(terminal), 1, terminal)
        self.assertEqual(len(screen), 1, screen)
        self.assertIn("≠ 예상", terminal[0])
        self.assertIn("68,000원", terminal[0])
        self.assertEqual(screen[0], "매달 이체 확인: 2026-09 생활비→신한은행 이체 합계가 예상과 다릅니다 (1건)")
        self.assertNotIn("원", screen[0])
        self.assertIsNone(re.search(r"\d{1,3}(,\d{3})+", screen[0]), screen[0])  # 금액 꼴 숫자 없음
        # "이체 행 없음"은 금액이 없으므로 양쪽 문구가 같다
        df2 = fixture_rows()
        df2 = df2[~((df2["구분"] == "이체") & (df2["소분류"] == "청년미래적금"))]
        _, b2 = run_rows(df2)
        missing = "매달 이체 확인: 2026-09 생활비→청년미래적금 이체 행 없음"
        self.assertIn(missing, warnings_for(b2))
        self.assertIn(missing, screen_warnings_for(b2))

    def test_future_date_uses_korean_date(self):
        """실행일 기준은 컨테이너 UTC가 아니라 한국 날짜 — UTC로는 아직 내일이지만 KST로는
        오늘인 날짜 행이 집계에 남는다 (2026-09-27 검증 실측, 수정 6)."""
        # 2026-09-26 15:30 UTC = 2026-09-27 00:30 KST
        fixed_now = pd.Timestamp("2026-09-26 15:30", tz="UTC")
        with mock.patch.object(pipeline, "_now_utc", return_value=fixed_now):
            self.assertEqual(today_kst(), pd.Timestamp("2026-09-27"))
            df = add_row(fixture_rows(), 날짜="2026-09-27", 구분="지출", 대분류="식비", 소분류="외식",
                         세부내용="실험", 금액="900,000", 결제수단="생활비")
            warns, b = self._warns(df, today=None)  # today=None → today_kst() 경로
        _, base = run_rows(fixture_rows())
        self.assertFalse(any("실행일" in w for w in warns), warns)
        self.assertEqual(b["kpi"]["total_expense"], base["kpi"]["total_expense"] + 900_000)
        self.assertEqual(b["latest_update"], "9월 27일")
        # 같은 시각에 KST로도 미래인 날짜(09-28)는 여전히 빠진다
        with mock.patch.object(pipeline, "_now_utc", return_value=fixed_now):
            df2 = add_row(fixture_rows(), 날짜="2026-09-28", 구분="지출", 대분류="식비", 소분류="외식",
                          세부내용="실험", 금액="900,000", 결제수단="생활비")
            warns2, b2 = self._warns(df2, today=None)
        self.assertTrue(any("실행일(2026-09-27)" in w and "2026-09-28" in w for w in warns2), warns2)
        self.assertEqual(b2["kpi"]["total_expense"], base["kpi"]["total_expense"])


class MonthLabels(unittest.TestCase):
    def test_year_prefix_when_crossing_year(self):
        """같은 월 번호가 두 해에 걸치면 최신 연도가 아닌 달에 연도가 붙는다 (결함 13)."""
        df = fixture_rows()
        d = pd.to_datetime(df["날짜"])
        # 2026-07..09 → 2025-11, 2025-12, 2026-01 로 이동 (일자 유지)
        shifted = []
        for x in d:
            m = x.month - 1 - 8
            y = x.year + m // 12
            m = m % 12 + 1
            shifted.append(f"{y}-{m:02d}-{min(x.day, 28):02d}")
        df["날짜"] = shifted
        _, b = run_rows(df, today="2026-01-31")
        self.assertEqual(b["monthly"]["labels"], ["2025.11월", "2025.12월", "1월"])
        self.assertEqual(b["heatmap"]["months"], b["monthly"]["labels"])


class PcPortability(unittest.TestCase):
    """이 PC(Windows)에서 막히던 자리를 고친 줄이 웹(리눅스) 결과를 바꾸지 않는지
    (2026-10-12 Code 탭 이전 — 탐색 기준선 1-5·1-7, 시험 후보 2)."""

    def test_latest_update_single_digit_month_day(self):
        df = fixture_rows()
        df = df[df["날짜"] <= "2026-09-05"]
        _, b = run_rows(df)
        self.assertEqual(b["latest_update"], "9월 5일")

    def test_latest_update_format_equals_strftime_no_pad(self):
        """f"{m}월 {d}일" = strftime("%-m월 %-d일")(glibc, 웹) — Windows판은 %#m으로 대조. 2025~2028 매일."""
        from datetime import date, timedelta
        fmt = "%#m월 %#d일" if os.name == "nt" else "%-m월 %-d일"
        d = date(2025, 1, 1)
        while d <= date(2028, 12, 31):
            self.assertEqual(f"{d.month}월 {d.day}일", d.strftime(fmt))
            d += timedelta(days=1)

    def _tmp_csv_dir(self, rows):
        import shutil
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        rows.to_csv(os.path.join(tmp, "in.csv"), index=False, encoding="utf-8-sig")
        return tmp

    def test_main_under_redirect_stdout(self):
        """stdout utf-8 줄은 __main__ 아래에 있다 — main()을 redirect_stdout 아래에서 불러도 깨지지 않는다."""
        tmp = self._tmp_csv_dir(fixture_rows())
        cwd = os.getcwd()
        try:
            os.chdir(tmp)
            with mock.patch.object(sys, "argv", ["pipeline.py", "in.csv"]), redirect_stdout(io.StringIO()):
                pipeline.main()
        finally:
            os.chdir(cwd)
        self.assertTrue(os.path.exists(os.path.join(tmp, "data_bundle.json")))

    def test_cp949_console_does_not_crash(self):
        """콘솔이 cp949여도 경고 줄의 '—'에서 죽지 않고 bundle을 쓴다(예전: UnicodeEncodeError · exit 1)."""
        import subprocess
        df = fixture_rows()
        tmp = self._tmp_csv_dir(pd.concat([df, df.tail(1)], ignore_index=True))  # 같은 행 → '—' 경고
        env = dict(os.environ, PYTHONIOENCODING="cp949", PYTHONUTF8="0", PYTHONDONTWRITEBYTECODE="1")
        for argv in (["pipeline.py", "in.csv"], ["scripts/detect_ended.py", "in.csv"]):
            r = subprocess.run([sys.executable, os.path.join(ROOT, argv[0]), argv[1]], cwd=tmp,
                               capture_output=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
            self.assertIn("—", r.stdout.decode("utf-8"))
        self.assertTrue(os.path.exists(os.path.join(tmp, "data_bundle.json")))


class DetectEndedChecks(unittest.TestCase):
    def test_transfer_only_csv_stops_with_pipeline_message(self):
        """이체만 있는 CSV: "완성된 달이 없어…"가 아니라 pipeline과 같은 [중단] 문구로 exit 1
        (2026-09-27 정정·수정 회차 2, 수정 5)."""
        df = fixture_rows()
        df = df[df["구분"] == "이체"]
        tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                          encoding="utf-8-sig", newline="")
        df.to_csv(tmp, index=False)
        tmp.close()
        try:
            with mock.patch.object(sys, "argv", ["detect_ended.py", tmp.name]):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as cm:
                        detect_ended.main()
        finally:
            os.unlink(tmp.name)
        self.assertEqual(str(cm.exception), "[중단] " + NO_CORE_ROWS_MSG)
        self.assertIn("수입·지출 행이 하나도 없습니다", str(cm.exception))
        self.assertNotIn("완성된 달", str(cm.exception))
        # pipeline 쪽(build_bundle)도 같은 문구다
        with self.assertRaises(ValueError) as cm2:
            run_rows(df)
        self.assertEqual(str(cm2.exception), NO_CORE_ROWS_MSG)

    def _detect_blocks(self, df):
        """detect_ended.main() 출력을 머리줄 → 항목 줄 목록으로 나눈다."""
        tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                          encoding="utf-8-sig", newline="")
        df.to_csv(tmp, index=False)
        tmp.close()
        out = io.StringIO()
        try:
            with mock.patch.object(sys, "argv", ["detect_ended.py", tmp.name]), redirect_stdout(out):
                detect_ended.main()
        finally:
            os.unlink(tmp.name)
        blocks, head = {}, None
        for ln in out.getvalue().splitlines():
            if ln.startswith("["):
                head = ln[:ln.index("]") + 1]
                blocks[head] = []
            elif head and ln.startswith("  - "):
                blocks[head].append(ln[4:])
        return blocks

    def test_irregular_fixed_items_use_their_own_gap(self):
        """불규칙 고정 항목은 항목별 기준(IRREGULAR_FIXED_ITEMS)을 넘을 때만 [확인 필요] (2026-10-12).
        fixture의 완성된 마지막 달은 2026-08이다."""
        df = fixture_rows()
        df = add_row(df, 날짜="2026-05-10", 구분="지출", 대분류="교통비", 소분류="대중교통",
                     세부내용="교통", 금액="5,000", 고정여부="고정")     # 3개월 공백 < 4
        df = add_row(df, 날짜="2026-04-10", 구분="지출", 대분류="뷰티", 소분류="복용약",
                     세부내용="약", 금액="9,000", 고정여부="고정")       # 4개월 공백 < 13
        df = add_row(df, 날짜="2026-03-10", 구분="지출", 대분류="의료비", 소분류="약값",
                     세부내용="약", 금액="9,000", 고정여부="고정")       # 5개월 공백 ≥ 4
        df = add_row(df, 날짜="2026-03-12", 구분="지출", 대분류="샘플", 소분류="정기",
                     세부내용="샘플", 금액="1,000", 고정여부="고정")     # 기본 기준 2개월
        blocks = self._detect_blocks(df)
        ask = " ".join(blocks.get("[확인 필요]", []))
        irregular = " ".join(blocks.get("[불규칙 고정]", []))
        self.assertIn("의료비 / 약값", ask)
        self.assertIn("샘플 / 정기", ask)
        self.assertNotIn("대중교통", ask)
        self.assertNotIn("복용약", ask)
        self.assertIn("교통비 / 대중교통", irregular)
        self.assertIn("뷰티 / 복용약", irregular)

    def test_fixed_app_groups_join_renamed_subscription(self):
        """'클로드' → '클로드 맥스 5x'처럼 이름이 바뀐 구독은 한 키로 본다 — 종료 추정에 안 걸린다."""
        df = fixture_rows()
        df = add_row(df, 날짜="2026-04-10", 구분="지출", 대분류="문화생활비", 소분류="어플/멤버쉽",
                     세부내용="클로드", 금액="30,000", 고정여부="고정")
        df = add_row(df, 날짜="2026-08-10", 구분="지출", 대분류="문화생활비", 소분류="어플/멤버쉽",
                     세부내용="클로드 맥스 5x", 금액="180,000", 고정여부="고정")
        blocks = self._detect_blocks(df)
        everything = " ".join(" ".join(v) for v in blocks.values())
        self.assertNotIn("클로드", everything)


class FixedAppGroups(unittest.TestCase):
    def test_projection_joins_names_but_transactions_keep_them(self):
        """예상 고정지출은 '클로드…'를 한 항목으로 묶고, 거래 내역의 세부내용은 원본 그대로 둔다."""
        df = fixture_rows()
        df = add_row(df, 날짜="2026-07-10", 구분="지출", 대분류="문화생활비", 소분류="어플/멤버쉽",
                     세부내용="클로드", 금액="30,000", 고정여부="고정")
        df = add_row(df, 날짜="2026-08-10", 구분="지출", 대분류="문화생활비", 소분류="어플/멤버쉽",
                     세부내용="클로드 맥스 5x", 금액="180,000", 고정여부="고정")
        _, b = run_rows(df)
        items = [p for p in b["projection"]["items"] if "클로드" in p["항목"]]
        self.assertEqual([p["항목"] for p in items], ["문화생활비 / 어플·멤버쉽 / 클로드"])
        self.assertEqual(items[0]["합계"], 210000)
        self.assertEqual(items[0]["마지막월"], "2026-08")
        self.assertIn("클로드 맥스 5x", [t["세부내용"] for t in b["monthly_tx"]["2026-08"]])

    def test_fixed_app_name(self):
        self.assertEqual(fixed_app_name("클로드 추가료"), "클로드")
        self.assertEqual(fixed_app_name("샘플OTT"), "샘플OTT")


SETTLE_TODAY = "2027-12-31"  # 정산 시험 행은 2027년 합성 날짜·금액이다(실 가계부와 겹치지 않게)


def spend(df, 날짜, 금액, 세부내용="합성 데이트", 결제수단="생활비", 대분류="내여자", 소분류="데이트비용"):
    return add_row(df, 날짜=날짜, 구분="지출", 대분류=대분류, 소분류=소분류, 세부내용=세부내용,
                   금액=금액, 결제수단=결제수단)


def settle(df, 날짜, 금액, 결제수단="커플통장", 소분류="생활비"):
    return add_row(df, 날짜=날짜, 구분="이체", 대분류="이체", 소분류=소분류, 세부내용="합성 정산",
                   금액=금액, 결제수단=결제수단)


class CoupleSettlement(unittest.TestCase):
    """커플통장 정산(COUPLE_SETTLEMENT) — 대상·받음 판정(K3) · 오래된 것부터 지우기·0·더 받음·환불(K4) ·
    validate 정합(K2) (2026-10-27 커플정산 기록 '정한 것' 1~4)."""

    def _cs(self, df):
        _, b = run_rows(df, today=SETTLE_TODAY)
        return b["couple_settlement"], b

    def test_fixture_has_nothing_to_settle(self):
        """fixture의 데이트비용 행은 커플통장에서 바로 낸 것 — 대상이 아니다."""
        cs, b = self._cs(fixture_rows())
        self.assertEqual((cs["target_count"], cs["received_count"], cs["owed"], cs["over"]), (0, 0, 0, 0))
        self.assertEqual(cs["talk"], ["커플통장 정산: 받을 금액 없음"])
        self.assertEqual(validate(b), [])

    def test_targets_are_only_settlement_rows(self):
        """대상 = 지출 · 내여자/데이트비용 · 결제수단 ≠ 커플통장(빈칸·신한은행 포함). 개인 결제·선물·
        다른 대분류·커플통장에서 바로 낸 것·수입 행은 아니다(K3)."""
        df = spend(fixture_rows(), "2027-03-02", "11,113", 결제수단="생활비")
        df = spend(df, "2027-03-02", "2,221", 결제수단="")
        df = spend(df, "2027-03-03", "3,337", 결제수단="신한은행")
        df = spend(df, "2027-03-03", "9,991", 소분류="개인 결제")
        df = spend(df, "2027-03-03", "8,883", 소분류="선물")
        df = spend(df, "2027-03-04", "7,771", 대분류="식비")
        df = spend(df, "2027-03-04", "6,661", 결제수단="커플통장")
        df = add_row(df, 날짜="2027-03-04", 구분="수입", 대분류="내여자", 소분류="데이트비용",
                     세부내용="합성 데이트", 금액="5,551", 결제수단="생활비")
        cs, b = self._cs(df)
        self.assertEqual(cs["target_count"], 3)
        self.assertEqual(cs["target_total"], 11_113 + 2_221 + 3_337)
        self.assertEqual(cs["owed"], 16_671)
        self.assertEqual(validate(b), [])

    def test_received_is_only_couple_to_living_transfer(self):
        """받음 = 이체 · 결제수단 커플통장 · 소분류 생활비. 반대 방향·다른 계좌로 간 이체는 아니다(K3)."""
        df = spend(fixture_rows(), "2027-03-02", "30,003")
        df = settle(df, "2027-03-05", "10,001")
        df = settle(df, "2027-03-05", "4,441", 결제수단="생활비", 소분류="커플통장")
        df = settle(df, "2027-03-05", "5,557", 소분류="신한은행")
        cs, _ = self._cs(df)
        self.assertEqual((cs["received_count"], cs["received_total"]), (1, 10_001))
        self.assertEqual(cs["owed"], 20_002)

    def test_start_date_is_inclusive(self):
        """시작일 전 행은 대상·받음 모두 아니다. 시작일 당일은 들어간다(K3)."""
        with mock.patch.dict(pipeline.COUPLE_SETTLEMENT, {"start": "2027-03-05"}):
            df = spend(fixture_rows(), "2027-03-04", "4,447")
            df = spend(df, "2027-03-05", "5,553")
            df = settle(df, "2027-03-04", "1,117")
            cs, b = self._cs(df)
        self.assertEqual((cs["target_total"], cs["received_total"], cs["owed"]), (5_553, 0, 5_553))
        self.assertEqual(cs["rule"]["start"], "2027-03-05")
        self.assertEqual(validate(b), [])

    def test_exact_match_is_zero(self):
        df = spend(fixture_rows(), "2027-03-02", "12,341")
        df = spend(df, "2027-03-03", "3,331")
        df = settle(df, "2027-03-06", "15,672")
        cs, b = self._cs(df)
        self.assertEqual((cs["owed"], cs["over"], cs["count"], cs["days"], cs["period"]), (0, 0, 0, [], ""))
        self.assertEqual(cs["talk"], ["커플통장 정산: 받을 금액 없음(마지막 정산 3/6 15,672원)"])
        self.assertFalse(any("더 받음" in w for w in warnings_for(b)))
        self.assertEqual(validate(b), [])

    def test_partial_clears_oldest_first(self):
        """받음 합을 오래된 대상부터 지우고, 걸친 행은 남은 몫만 '일부'로 남긴다(K4)."""
        df = spend(fixture_rows(), "2027-03-02", "10,001", 세부내용="합성 가")
        df = spend(df, "2027-03-03", "20,003", 세부내용="합성 나")
        df = spend(df, "2027-03-03", "5,005", 세부내용="합성 다")
        df = spend(df, "2027-03-04", "7,007", 세부내용="합성 라")
        df = settle(df, "2027-03-05", "25,000")
        cs, b = self._cs(df)
        self.assertEqual(cs["owed"], 42_016 - 25_000)
        self.assertEqual([(d["날짜"], d["건수"], d["소계"]) for d in cs["days"]],
                         [("2027-03-03", 2, 10_009), ("2027-03-04", 1, 7_007)])
        self.assertEqual(cs["days"][0]["items"], [
            {"세부내용": "합성 다", "건수": 1, "금액": 5_005, "일부": None},
            {"세부내용": "합성 나", "건수": 1, "금액": 5_004, "일부": 20_003}])
        self.assertEqual((cs["count"], cs["period"]), (3, "3/3~3/4"))
        self.assertEqual(cs["talk"][0], "커플통장 정산: 받을 금액 17,016원 (3/3~3/4 · 3건)")
        self.assertEqual(cs["talk"][1], "이체한 뒤 CSV에 넣을 행: 날짜 <이체한 날> · 구분 이체 · 대분류 이체 · "
                                        "소분류 생활비 · 세부내용 데이트비 정산 · 금액 17,016 · 결제수단 커플통장")
        self.assertEqual(validate(b), [])

    def test_order_is_date_then_csv(self):
        """날짜 순이 먼저(CSV 뒤에 적힌 이른 날짜 행이 먼저 지워진다), 같은 날은 CSV 순(K4)."""
        df = spend(fixture_rows(), "2027-03-02", "10,001", 세부내용="합성 가")
        df = spend(df, "2027-03-03", "5,005", 세부내용="합성 다")
        df = spend(df, "2027-03-03", "20,003", 세부내용="합성 나")
        df = spend(df, "2027-03-04", "7,007", 세부내용="합성 라")
        df = spend(df, "2027-03-01", "1,001", 세부내용="합성 마")
        df = settle(df, "2027-03-05", "26,001")
        cs, b = self._cs(df)
        self.assertEqual(cs["owed"], 17_016)
        self.assertEqual(cs["days"][0]["items"], [{"세부내용": "합성 나", "건수": 1, "금액": 10_009, "일부": 20_003}])
        self.assertEqual(validate(b), [])

    def test_over_received_is_zero_and_warns_in_talk_only(self):
        """받음이 더 크면 0원 · '더 받음' · 경고는 대화(warnings_for)에만, 화면 14번(screen_warnings_for)엔 없다(K4)."""
        df = spend(fixture_rows(), "2027-03-02", "8,881")
        df = settle(df, "2027-03-03", "10,009")
        cs, b = self._cs(df)
        self.assertEqual((cs["owed"], cs["over"], cs["days"]), (0, 1_128, []))
        self.assertEqual(cs["talk"], ["커플통장 정산: 받을 금액 없음(마지막 정산 3/3 10,009원) · 더 받음 1,128원"])
        over = [w for w in warnings_for(b) if "더 받음" in w]
        self.assertEqual(len(over), 1, over)
        self.assertIn("1,128원", over[0])
        self.assertIn(COUPLE_SETTLEMENT["account"], over[0])
        self.assertFalse(any("더 받음" in w for w in screen_warnings_for(b)))
        self.assertEqual(validate(b), [])

    def test_refund_reduces_owed(self):
        """음수 금액(환불)은 받을 금액을 줄이고, 남은 항목의 합은 받을 금액과 같다(K4)."""
        df = spend(fixture_rows(), "2027-03-02", "10,001", 세부내용="합성 가")
        df = spend(df, "2027-03-03", "6,006", 세부내용="합성 나")
        df = spend(df, "2027-03-04", "-3,001", 세부내용="합성 환불")
        cs, b = self._cs(df)
        self.assertEqual((cs["target_total"], cs["refund_total"], cs["owed"]), (13_006, 3_001, 13_006))
        self.assertEqual(sum(d["소계"] for d in cs["days"]), 13_006)
        self.assertEqual(cs["days"][0]["items"][0]["일부"], 10_001)
        self.assertEqual(validate(b), [])

    def test_same_day_same_detail_grouped(self):
        """같은 날 같은 세부내용은 한 줄(건수)로, 금액 큰 순. 다른 날은 따로(정한 것 5)."""
        df = fixture_rows()
        for _ in range(3):
            df = spend(df, "2027-03-02", "1,013", 세부내용="합성 뽑기")
        df = spend(df, "2027-03-02", "2,027", 세부내용="합성 식당")
        df = spend(df, "2027-03-03", "1,013", 세부내용="합성 뽑기")
        cs, b = self._cs(df)
        self.assertEqual([(d["날짜"], d["건수"], d["소계"]) for d in cs["days"]],
                         [("2027-03-02", 4, 5_066), ("2027-03-03", 1, 1_013)])
        self.assertEqual([(i["세부내용"], i["건수"], i["금액"]) for i in cs["days"][0]["items"]],
                         [("합성 뽑기", 3, 3_039), ("합성 식당", 1, 2_027)])
        self.assertEqual(validate(b), [])

    def test_recent_five_newest_first(self):
        df = fixture_rows()
        for i in range(1, 7):
            df = settle(df, f"2027-03-0{i}", f"{i},001")
        cs, _ = self._cs(df)
        self.assertEqual([r["날짜"] for r in cs["recent"]],
                         ["2027-03-06", "2027-03-05", "2027-03-04", "2027-03-03", "2027-03-02"])
        self.assertEqual(cs["recent"][0]["금액"], 6_001)

    def test_validate_catches_settlement_mismatch(self):
        """받을 금액 ≠ max(0, 대상 − 받음) · 날짜별 소계 합 ≠ 받을 금액이면 [실패](K2)."""
        df = spend(fixture_rows(), "2027-03-02", "10,001")
        df = spend(df, "2027-03-03", "7,007")
        df = settle(df, "2027-03-05", "4,000")
        _, b = run_rows(df, today=SETTLE_TODAY)
        self.assertEqual(validate(b), [])
        muts = {
            "받을 금액": lambda cs: cs.__setitem__("received_total", cs["received_total"] + 1),
            "날짜별 소계 합": lambda cs: cs["days"][0].update(소계=cs["days"][0]["소계"] + 1),
            "날짜별 건수 합": lambda cs: cs.__setitem__("count", cs["count"] + 1),
            "항목 합": lambda cs: cs["days"][-1]["items"][0].update(금액=0),
        }
        for label, mut in muts.items():
            bb = copy.deepcopy(b)
            mut(bb["couple_settlement"])
            errs = validate(bb)
            self.assertTrue(any("커플통장 정산" in e and label in e for e in errs), f"{label}: {errs}")


class CoupleSettlementOutput(unittest.TestCase):
    """pipeline 끝 요약의 정산 줄(K7) · 화면 02번 섹션·번호·{REF}·0원/더 받음 렌더(K6)."""

    def test_main_prints_settlement_after_account_lines(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        df = spend(fixture_rows(), "2027-03-02", "10,001")
        df.to_csv(os.path.join(tmp, "in.csv"), index=False, encoding="utf-8-sig")
        out, cwd = io.StringIO(), os.getcwd()
        try:
            os.chdir(tmp)
            with mock.patch.object(pipeline, "_now_utc", return_value=pd.Timestamp("2027-12-31", tz="UTC")), \
                    mock.patch.object(sys, "argv", ["pipeline.py", "in.csv"]), redirect_stdout(out):
                pipeline.main()
        finally:
            os.chdir(cwd)
        lines = out.getvalue().splitlines()
        acct_last = max(i for i, ln in enumerate(lines) if ln.startswith("  주식잔고:"))
        self.assertEqual(lines[acct_last + 1], "  커플통장 정산: 받을 금액 10,001원 (3/2 · 1건)")
        self.assertTrue(lines[acct_last + 2].startswith("  이체한 뒤 CSV에 넣을 행: 날짜 <이체한 날> · 구분 이체"))

    def _report(self, df):
        """bundle(합성) → build.py → report HTML. pipeline.main처럼 화면 경고를 bundle에 싣는다."""
        import shutil
        import subprocess
        _, b = run_rows(df, today=SETTLE_TODAY)
        self.assertEqual(validate(b), [])
        b["warnings"] = screen_warnings_for(b)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "data_bundle.json"), "w", encoding="utf-8") as f:
            json.dump(b, f, ensure_ascii=False)
        out = os.path.join(tmp, "report.html")
        env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", HOUSEHOLD_OUT=out)
        subprocess.run([sys.executable, os.path.join(ROOT, "build.py")], cwd=tmp, check=True,
                       capture_output=True, env=env)
        with open(out, encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _section(html, sid):
        return re.search(rf'<section id="{re.escape(sid)}">(.*?)</section>', html, re.S).group(1)

    def test_section_is_second_and_numbers_follow(self):
        """새 섹션이 02번 · 뒤 섹션이 03~15 · {REF:s11}이 12번 · 계좌 잔고에 커플통장 카드 없음(K6·K5)."""
        html = self._report(fixture_rows())
        ids = re.findall(r'<section id="([^"]+)"', html)
        nums = re.findall(r'<span class="section-num">(\d+)</span>', html)
        self.assertEqual(ids, ["s1", "s-couple", "s2", "s3", "s5", "s6", "s-mom", "s7", "s9", "s10",
                               "s4", "s11", "s12", "s13", "s14"])
        self.assertEqual(nums, [f"{i:02d}" for i in range(1, 16)])
        self.assertIn("다음 12번의 예상 고정지출", html)
        self.assertNotIn("{REF:", html)
        self.assertNotIn(f'<div class="label">{HIDDEN_ACCOUNTS[0]}</div>', self._section(html, "s1"))
        # 0원 · 정산 기록 없음 상태
        sec = self._section(html, "s-couple")
        self.assertIn("정산할 금액 없음", sec)
        self.assertIn("정산 기록 없음", sec)

    def test_owed_and_over_states_render(self):
        df = spend(fixture_rows(), "2027-03-02", "10,001", 세부내용="합성 가")
        df = spend(df, "2027-03-03", "1,013", 세부내용="합성 뽑기")
        df = spend(df, "2027-03-03", "1,013", 세부내용="합성 뽑기")
        df = settle(df, "2027-03-04", "4,001")
        sec = self._section(self._report(df), "s-couple")
        self.assertIn("정산 받을 금액", sec)
        self.assertIn("8,026원", sec)
        self.assertIn("3/2~3/3 · 3건", sec)
        self.assertIn("일부 남음", sec)
        self.assertIn("×2", sec)
        self.assertIn("최근 정산", sec)
        # 더 받음: 화면 02번에는 보이고, 14번 경고에는 없다
        df2 = settle(spend(fixture_rows(), "2027-03-02", "8,881"), "2027-03-03", "10,009")
        html2 = self._report(df2)
        self.assertIn("더 받음 1,128원", self._section(html2, "s-couple"))
        self.assertIn("마지막 정산 3/3 10,009원", self._section(html2, "s-couple"))
        self.assertNotIn("더 받음", self._section(html2, "s13"))


def xfer(df, 날짜, 금액, 비고="", 결제수단="생활비", 소분류="커플통장", 세부내용="합성 커플 이체"):
    return add_row(df, 날짜=날짜, 구분="이체", 대분류="이체", 소분류=소분류, 세부내용=세부내용,
                   금액=금액, 결제수단=결제수단, 고정여부="고정", 비고=비고)


def other(df, 날짜, 금액, 세부내용="합성 장보기"):
    return add_row(df, 날짜=날짜, 구분="지출", 대분류="식비", 소분류="장보기", 세부내용=세부내용,
                   금액=금액, 결제수단="생활비")


def month_exp(b, m):
    return b["monthly"]["expense"][b["months_included"].index(m)]


def by_month(b, key, m):
    return b["real_spend"][key][b["months_included"].index(m)]


def subs(b, main_cat):
    return {s["소분류"]: (s["합계"], s["건수"]) for s in b["detail"].get(main_cat, [])}


class CoupleRealSpend(unittest.TestCase):
    """내여자 실질 지출(COUPLE_REAL_SPEND) — 정산 받은 몫 빼기·커플통장 이체 더하기·'N월분' 귀속(K4) ·
    지출 집계가 실질 값·validate 정합(K3) (2026-10-27 커플실지출 기록 '정한 것' 1~5).
    합성 값만 — 2027·2028년 날짜(실 가계부와 겹치지 않게), 시작일 경계는 상수를 patch한다."""

    def _b(self, df, today=SETTLE_TODAY):
        _, b = run_rows(df, today=today)
        self.assertEqual(validate(b), [])
        return b

    def test_fully_received_removes_targets(self):
        """다 받음 → 대상 전액이 지출에서 빠진다(행째 — 건수·TOP15에도 없다)."""
        df = spend(fixture_rows(), "2027-03-02", "60,001", 세부내용="합성 큰 데이트")
        df = spend(df, "2027-03-03", "5,005", 세부내용="합성 작은 데이트")
        df = other(df, "2027-03-04", "3,303")
        b = self._b(settle(df, "2027-03-09", "65,006"))
        self.assertEqual(month_exp(b, "2027-03"), 3_303)
        self.assertEqual(by_month(b, "settled_by_month", "2027-03"), 65_006)
        self.assertEqual(b["real_spend"]["settled_total"], 65_006)
        self.assertNotIn("데이트비용", subs(b, "내여자"))
        self.assertFalse(any(t["세부내용"].startswith("합성") and t["대분류"] == "내여자" for t in b["top_expenses"]))
        # 정산 전이면 그대로 지출(같은 행, 받음만 없음)
        b0 = self._b(df)
        self.assertEqual(month_exp(b0, "2027-03"), 3_303 + 60_001 + 5_005)
        self.assertEqual(b0["real_spend"]["settled_total"], 0)
        self.assertEqual(subs(b0, "내여자")["데이트비용"], (65_006, 2))
        self.assertIn(60_001, [t["금액"] for t in b0["top_expenses"]])

    def test_partial_clears_oldest_first_and_keeps_rest(self):
        """일부 받음 → 오래된 것부터 지우고, 걸친 행은 지워진 몫만 빠진다. 빠지는 달 = 대상 행의 달."""
        df = spend(fixture_rows(), "2027-03-02", "10,001", 세부내용="합성 가")
        df = spend(df, "2027-03-03", "70,003", 세부내용="합성 나")
        df = spend(df, "2027-04-01", "7,007", 세부내용="합성 다")
        b = self._b(settle(df, "2027-04-02", "40,002"))
        self.assertEqual((month_exp(b, "2027-03"), month_exp(b, "2027-04")), (40_002, 7_007))
        self.assertEqual((by_month(b, "settled_by_month", "2027-03"), by_month(b, "settled_by_month", "2027-04")),
                         (40_002, 0))
        self.assertEqual(subs(b, "내여자")["데이트비용"], (47_009, 2))
        self.assertEqual(b["couple_settlement"]["owed"], 47_009)     # 남은 지출 = 02번 받을 금액
        self.assertIn(("합성 나", 40_002), [(t["세부내용"], t["금액"]) for t in b["top_expenses"]])

    def test_over_received_only_up_to_target(self):
        """더 받음 → 대상 합까지만 빠진다(음수 지출 없음). 개인 결제·선물은 그대로다."""
        df = spend(fixture_rows(), "2027-03-02", "8,881")
        df = spend(df, "2027-03-02", "9,991", 소분류="개인 결제")
        df = spend(df, "2027-03-03", "8,883", 소분류="선물")
        b = self._b(settle(df, "2027-03-05", "30,007"))
        self.assertEqual(b["real_spend"]["settled_total"], 8_881)
        self.assertEqual(b["couple_settlement"]["over"], 30_007 - 8_881)
        self.assertEqual(month_exp(b, "2027-03"), 9_991 + 8_883)
        self.assertEqual({k: v for k, v in subs(b, "내여자").items() if k in ("개인 결제", "선물")},
                         {"개인 결제": (9_991, 1), "선물": (8_883, 1)})
        self.assertTrue(all(v >= 0 for v in b["monthly"]["expense"]))

    def test_refund_is_not_subtracted_twice(self):
        """환불(음수 대상)은 02번처럼 지울 몫에 들어가 행째 빠진다 — 정산 받은 몫 합 = 받음, 남은 지출 = 받을 금액."""
        df = spend(fixture_rows(), "2027-03-02", "10,001", 세부내용="합성 가")
        df = spend(df, "2027-03-03", "6,006", 세부내용="합성 나")
        df = spend(df, "2027-03-04", "-3,001", 세부내용="합성 환불")
        b = self._b(settle(df, "2027-03-05", "4,000"))
        self.assertEqual(b["real_spend"]["settled_total"], 4_000)
        self.assertEqual(month_exp(b, "2027-03"), 13_006 - 4_000)
        self.assertEqual(month_exp(b, "2027-03"), b["couple_settlement"]["owed"])
        self.assertEqual(subs(b, "내여자")["데이트비용"], (9_006, 2))

    def test_before_start_is_unchanged(self):
        """시작일 전 데이트비·이체·커플통장 결제는 원래 행 그대로(정산·이체 셈 없음)."""
        with mock.patch.dict(pipeline.COUPLE_SETTLEMENT, {"start": "2027-04-01"}):
            df = spend(fixture_rows(), "2027-03-30", "4,447")
            df = spend(df, "2027-03-30", "2,221", 결제수단="커플통장")
            df = xfer(df, "2027-03-15", "3,331")
            df = settle(df, "2027-03-31", "4,447")
            df = spend(df, "2027-04-02", "5,553")
            b = self._b(settle(df, "2027-04-03", "5,553"))
        rs = b["real_spend"]
        self.assertEqual((month_exp(b, "2027-03"), month_exp(b, "2027-04")), (4_447 + 2_221, 0))
        self.assertEqual((rs["settled_total"], rs["transfer_total"], rs["couple_paid_total"]), (5_553, 0, 0))
        # fixture(2026년)의 커플통장 이체·커플통장 결제도 시작일 전이라 원래 값 그대로
        n = b["months_included"].index("2027-03")
        self.assertEqual(b["monthly"]["expense"][:n], rs["original_by_month"][:n])

    def test_transfer_counted_in_memo_month(self):
        """'N월분' → 그 달(시작일 전 날짜라도 귀속이 시작 달이면 센다) · 비고 없음·다른 비고 → 날짜의 달."""
        with mock.patch.dict(pipeline.COUPLE_SETTLEMENT, {"start": "2027-04-01"}):
            df = other(fixture_rows(), "2027-03-02", "1,111")
            df = other(df, "2027-04-02", "2,227")
            df = xfer(df, "2027-03-30", "40,001", 비고="4월분")
            df = xfer(df, "2027-03-15", "10,003")
            df = xfer(df, "2027-04-10", "10,007", 비고="합성 메모")
            df = xfer(df, "2027-04-20", "10,009")
            b = self._b(df)
        self.assertEqual(month_exp(b, "2027-03"), 1_111)
        self.assertEqual(month_exp(b, "2027-04"), 2_227 + 40_001 + 10_007 + 10_009)
        self.assertEqual((b["real_spend"]["transfer_total"], b["real_spend"]["transfer_count"]), (60_017, 3))
        as_main, as_sub = COUPLE_REAL_SPEND["as_expense"]
        self.assertEqual(subs(b, as_main)[as_sub], (60_017, 3))
        # 귀속된 이체는 그 달 1일 행이라 전월 대비(1~20일로 자름)에도 4월로 들어간다
        mom = {x["대분류"]: x for x in b["month_over_month"]["items"]}
        self.assertEqual(b["month_over_month"]["same_day_cutoff"], 20)
        self.assertEqual(mom[as_main]["당월"], 60_017)

    def test_memo_month_wraps_year(self):
        """12/31 '1월분' → 다음 해 1월."""
        df = other(fixture_rows(), "2027-12-02", "1,111")
        df = other(df, "2028-01-05", "2,227")
        b = self._b(xfer(df, "2027-12-31", "30,011", 비고="1월분"), today="2028-12-31")
        self.assertEqual((month_exp(b, "2027-12"), month_exp(b, "2028-01")), (1_111, 2_227 + 30_011))
        self.assertEqual(by_month(b, "transfer_by_month", "2028-01"), 30_011)

    def test_memo_month_rule(self):
        mm = pipeline._memo_month
        ts = pd.Timestamp
        self.assertEqual(mm(ts("2027-08-29"), "9월분"), "2027-09")
        self.assertEqual(mm(ts("2027-12-31"), "1월분"), "2028-01")
        self.assertEqual(mm(ts("2027-01-02"), "12월분"), "2026-12")
        self.assertEqual(mm(ts("2027-03-15"), "9월분"), "2027-09")      # 앞뒤 6개월 → 뒤(선이체)
        self.assertEqual(mm(ts("2027-03-15"), " 04월분 "), "2027-04")
        for memo in ("", "13월분", "0월분", "4월분 이체", "합성 메모", "현대카드"):
            self.assertIsNone(mm(ts("2027-03-15"), memo), memo)

    def test_next_month_share_waits_for_its_month(self):
        """귀속 달이 아직 리포트에 없으면(다음 달 몫 선이체) 세지 않고 대화로 알린다 — 그 달 행이 생기면 센다."""
        df = other(fixture_rows(), "2027-03-02", "1,111")
        df = xfer(df, "2027-03-31", "40,001", 비고="4월분")
        b = self._b(df)
        rs = b["real_spend"]
        self.assertEqual(rs["pending"], [{"날짜": "2027-03-31", "금액": 40_001, "귀속": "2027-04"}])
        self.assertEqual(month_exp(b, "2027-03"), 1_111)
        self.assertIn("다음 달 몫 40,001원(2027-04)은 그 달에 셈", rs["talk"])
        b2 = self._b(other(df, "2027-04-02", "2,227"))
        self.assertEqual((b2["real_spend"]["pending"], month_exp(b2, "2027-04")), ([], 2_227 + 40_001))

    def test_other_transfers_unchanged(self):
        """신한은행·주식잔고·적금·커플통장→생활비(정산 대상 없음)·다른 계좌→커플통장 이체는 지출에 안 든다."""
        base = other(fixture_rows(), "2027-03-02", "1,111")
        df = xfer(base, "2027-03-03", "7,001", 소분류="신한은행")
        df = xfer(df, "2027-03-03", "7,003", 소분류="주식잔고")
        df = xfer(df, "2027-03-03", "7,005", 소분류="청년미래적금")
        df = settle(df, "2027-03-04", "7,007")
        df = xfer(df, "2027-03-04", "7,009", 결제수단="신한은행")
        b0, b = self._b(base), self._b(df)
        self.assertEqual(b["monthly"]["expense"], b0["monthly"]["expense"])
        self.assertEqual(b["kpi"]["total_expense"], b0["kpi"]["total_expense"])
        self.assertEqual((b["real_spend"]["transfer_total"], b["real_spend"]["settled_total"]),
                         (b0["real_spend"]["transfer_total"], 0))

    def test_paid_from_couple_account_after_start_not_counted_twice(self):
        """시작일부터 커플통장에서 바로 낸 지출은 뺀다 — 그 돈은 커플통장에 넣을 때 이체로 셌다(임의 결정)."""
        df = other(fixture_rows(), "2027-03-02", "1,111")
        b0 = self._b(df)
        b = self._b(spend(df, "2027-03-03", "6,661", 결제수단="커플통장"))
        self.assertEqual(month_exp(b, "2027-03"), 1_111)
        self.assertEqual(b["real_spend"]["couple_paid_total"] - b0["real_spend"]["couple_paid_total"], 6_661)
        self.assertIn(f"커플통장에서 바로 낸 {b['real_spend']['couple_paid_total']:,}원 뺌", b["real_spend"]["talk"])

    def test_aggregates_use_real_spend(self):
        """KPI·월별·대분류·히트맵·고정/변동·목표 대비·예상 고정지출이 같은 실질 지출에서 나온다(K3)."""
        df = other(fixture_rows(), "2027-03-02", "1,111")
        df = spend(df, "2027-03-02", "20,003")
        df = xfer(df, "2027-03-05", "40,001")
        df = settle(df, "2027-03-06", "20,003")
        df = other(df, "2027-04-02", "2,227")
        b = self._b(df)
        n = b["months_included"].index("2027-03")
        self.assertEqual(month_exp(b, "2027-03"), 1_111 + 40_001)
        cats = b["heatmap"]["categories"]
        self.assertEqual(b["heatmap"]["matrix"][cats.index("내여자")][n],
                         by_month(b, "transfer_by_month", "2027-03"))
        fv = {x["대분류"]: x for x in b["fixed_variable"]}
        self.assertEqual(fv["내여자"]["고정"], b["real_spend"]["transfer_total"])   # 이체 행의 고정여부 그대로
        fvt = {x["월"]: x["실제"] for x in b["fixed_vs_target"]["months"]}
        self.assertEqual(fvt["2027-03"], 40_001)
        self.assertIn("내여자 / 커플통장", [p["항목"] for p in b["projection"]["items"]])
        # 월별 전체 내역은 원래 행 그대로(이체는 이체, 정산 받은 데이트비도 원래 금액)
        tx = b["monthly_tx"]["2027-03"]
        self.assertIn(("이체", "커플통장", 40_001), [(t["구분"], t["소분류"], t["금액"]) for t in tx])
        self.assertIn(("지출", "데이트비용", 20_003), [(t["구분"], t["소분류"], t["금액"]) for t in tx])

    def test_real_spend_does_not_touch_balances_cards_or_warnings(self):
        """계좌 잔고·카드 실적·월별 전체 내역·02번 정산·경고는 실질 지출 규칙이 있어도 없어도 같다(K2 축소판)."""
        df = spend(fixture_rows(), "2027-03-21", "13,013")
        df = add_row(df, 날짜="2027-03-22", 구분="지출", 대분류="내여자", 소분류="데이트비용", 세부내용="합성 카드 데이트",
                     금액="7,007", 결제수단="생활비", 비고="현대카드")
        df = settle(df, "2027-03-23", "15,000")
        df = xfer(df, "2027-03-24", "100,003")
        _, b = run_rows(df, today=SETTLE_TODAY)
        self.assertEqual(validate(b), [])
        with mock.patch.object(pipeline, "couple_real_spend", lambda df_, exp, months, clear: (exp, {
                "rule": {"account": "커플통장"}, "original_total": 0, "settled_total": 0, "couple_paid_total": 0,
                "transfer_total": 0, "talk": ""})):
            _, b_old = run_rows(df, today=SETTLE_TODAY)
        for key in ("accounts", "card_performance", "card_category_detail", "monthly_tx", "couple_settlement",
                    "diagnostics", "shinhan_fixed", "shinhan_unmatched"):
            self.assertEqual(b[key], b_old[key], key)
        self.assertEqual(warnings_for(b), warnings_for(b_old))
        self.assertNotEqual(b["kpi"]["total_expense"], b_old["kpi"]["total_expense"])

    def test_validate_catches_real_spend_mismatch(self):
        """KPI ≠ 원래 − 정산 받은 몫 − 커플통장 결제 + 이체 · 달별 합 · 달마다 · 02번과 다른 정산 몫이면 [실패](K3)."""
        df = spend(fixture_rows(), "2027-03-02", "10,001")
        df = xfer(df, "2027-03-05", "40,001")
        b = self._b(settle(df, "2027-03-06", "4,000"))
        n = b["months_included"].index("2027-03")

        def kpi(rs):
            rs["transfer_total"] += 1

        def month_shift(rs):
            rs["transfer_by_month"][n] -= 1
            rs["transfer_by_month"][0] += 1

        def settle_vs_02(rs):
            for k in ("settled_total", "original_total"):
                rs[k] += 1
            rs["settled_by_month"][n] += 1
            rs["original_by_month"][n] += 1

        def by_month_sum(rs):
            rs["settled_by_month"][n] += 1
            rs["original_by_month"][n] += 1

        muts = {"KPI 총지출": kpi, "월별 지출": month_shift, "정산 대상 합 − 받을 금액": settle_vs_02,
                "settled 달별 합": by_month_sum, "길이": lambda rs: rs["settled_by_month"].pop()}
        for label, mut in muts.items():
            bb = copy.deepcopy(b)
            mut(bb["real_spend"])
            errs = validate(bb)
            self.assertTrue(any(e.startswith("실질 지출") and label in e for e in errs), f"{label}: {errs}")


class CoupleRealSpendOutput(unittest.TestCase):
    """pipeline 끝 요약의 실질 지출 줄(K6) · 06번 내여자 펼침 아래 참고 줄 · 섹션 15개 번호 그대로(K5)."""

    _report = CoupleSettlementOutput._report
    _section = staticmethod(CoupleSettlementOutput._section)

    def test_main_prints_real_spend_line_under_kpi(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        df = settle(spend(fixture_rows(), "2027-03-02", "10,001"), "2027-03-03", "10,001")
        df.to_csv(os.path.join(tmp, "in.csv"), index=False, encoding="utf-8-sig")
        out, cwd = io.StringIO(), os.getcwd()
        try:
            os.chdir(tmp)
            with mock.patch.object(pipeline, "_now_utc", return_value=pd.Timestamp("2027-12-31", tz="UTC")), \
                    mock.patch.object(sys, "argv", ["pipeline.py", "in.csv"]), redirect_stdout(out):
                pipeline.main()
            with open(os.path.join(tmp, "data_bundle.json"), encoding="utf-8") as f:
                b = json.load(f)
        finally:
            os.chdir(cwd)
        lines = out.getvalue().splitlines()
        k = next(i for i, ln in enumerate(lines) if ln.startswith("  총수입 "))
        self.assertIn(f"총지출 {b['kpi']['total_expense']:,}원", lines[k])
        # fixture: 9/1 커플통장 이체 400,000 셈 · 9/20 커플통장에서 바로 낸 20,000 뺌
        self.assertEqual(lines[k + 1], "  실질 지출: 정산 받은 데이트비용 10,001원 뺌 · 커플통장 이체 400,000원 더함 · "
                                       "커플통장에서 바로 낸 20,000원 뺌 (2026-09-01부터)")
        self.assertTrue(lines[k + 2].startswith("  생활비 통장:"))

    def test_note_under_couple_category(self):
        df = spend(fixture_rows(), "2027-03-02", "10,001")
        df = spend(df, "2027-03-03", "5,005")
        df = settle(df, "2027-03-04", "12,000")
        html = self._report(df)
        ids = re.findall(r'<section id="([^"]+)"', html)
        self.assertEqual(len(ids), 15)
        self.assertEqual(re.findall(r'<span class="section-num">(\d+)</span>', html), [f"{i:02d}" for i in range(1, 16)])
        sec = self._section(html, "s6")
        blocks = re.findall(r'<details class="detail-item">(.*?)</details>', sec, re.S)
        mine = [blk for blk in blocks if '<span class="cd-name name">내여자</span>' in blk]
        self.assertEqual(len(mine), 1)
        self.assertIn("데이트비용: 내 카드·계좌로 낸 15,006원 중 커플통장에서 정산 받은 12,000원은 지출에서 뺐어요(9/1부터)",
                      mine[0])
        self.assertIn("커플통장: 9/1부터는 생활비 → 커플통장 이체 400,000원을 지출로 셌어요(비고 'N월분'이면 그 달로)",
                      mine[0])
        self.assertIn("커플통장에서 바로 낸 20,000원은 이체로 이미 셌으니 지출에서 뺐어요", mine[0])
        self.assertEqual(sum("rs-notes" in blk for blk in blocks), 1)


class DeployChecks(unittest.TestCase):
    def test_blob_sha_matches_git(self):
        # git hash-object "hello\n" == 'ce013625030ba8dba906f756967f9e9ca394464a'
        self.assertEqual(deploy._blob_sha(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")

    def test_extract_data_survives_brace_semicolon_in_string(self):
        """세부내용에 '};'가 있어도 DATA를 통째로 잡는다 (결함 14)."""
        data = {"a": "실험 };", "accounts": {"list": []}}
        html = "<html><script>\nconst DATA = " + json.dumps(data, ensure_ascii=False) + ";\n\nfoo();\n</script></html>"
        self.assertEqual(deploy._extract_data(html), data)

    def test_balance_jump_gate(self):
        """직전 배포본 대비 급변이면 --ack-balance 없이는 멈추고, 있으면 진행 (개선안 5)."""
        prev = {"accounts": {"list": [{"name": "생활비 통장", "current_balance": 1_000_000}]}}
        new = {"accounts": {"list": [{"name": "생활비 통장", "current_balance": 1_000_000 + deploy.BALANCE_JUMP_THRESHOLD + 1}]}}
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write("<script>\nconst DATA = " + json.dumps(prev, ensure_ascii=False) + ";\n</script>")
            prev_path = f.name
        try:
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    deploy.check_balance_jump(new, ack=False, prev_html_path=prev_path)
                jumps = deploy.check_balance_jump(new, ack=True, prev_html_path=prev_path)
                self.assertEqual(len(jumps), 1)
                same = deploy.check_balance_jump(prev, ack=False, prev_html_path=prev_path)
                self.assertEqual(same, [])
        finally:
            os.unlink(prev_path)

    def test_scan_never_includes_csv_or_bundle(self):
        files = deploy._scan_code_files()
        self.assertFalse(any(f.endswith(".csv") and not f.startswith("tests/fixtures/") for f in files), files)
        self.assertNotIn("index.html", files)
        self.assertNotIn("data_bundle.json", files)
        for must in ("SKILL.md", "pipeline.py", "build.py", "scripts/deploy.py"):
            self.assertIn(must, files)


if __name__ == "__main__":
    unittest.main()
