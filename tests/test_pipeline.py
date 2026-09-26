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
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pipeline  # noqa: E402
from pipeline import (  # noqa: E402
    ACCOUNTS_START, ACCOUNT_KEY, ENDED_FIXED_ITEMS, EXPECTED_COLS,
    EXPECTED_MONTHLY_TRANSFERS, HIDDEN_ACCOUNTS, MANUALLY_EXCLUDED_FIXED_ITEMS,
    SUBSCRIPTION_ALIASES, build_bundle, load_csv, screen_warnings_for, validate,
    warnings_for,
)
import deploy  # noqa: E402

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
        fx["키"] = fx.apply(lambda r: "문화생활비 / 어플·멤버쉽 / " + r["세부내용"]
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
