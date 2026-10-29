"""
회귀 테스트 — scripts/pc_fetch.py (이 PC 내역 받기)

실행:
    $PY -m unittest tests.test_pc_fetch -v            (이 PC — PY = D:/household/.venv/Scripts/python.exe)
    $PY -m unittest tests.test_pc_fetch.E2E -v        가짜 사이트(헤드리스 번들 크로미움)만

금융사·메일 접속 0. 가짜 사이트 3개는 이 파일 안의 HTML을 Playwright route로 띄우고(네트워크 0), 가짜 IMAP은 imaplib 자리에
끼운다. 내역 파일(xlsx·xls·HTML 표·암호 xlsx 흉내)은 전부 시험 중 임시 폴더에 합성 값으로 만든다 — 실제 거래내역·식별 값 0.
지키는 것: 2026-10-28 내역 받기 탐색 기준선 4절 완료 기준 1~17·20·21(18·19는 실제 환경 리허설).
"""
import ast
import base64
import contextlib
import datetime as dt
import email.policy
import imaplib
import io
import json
import os
import re
import shutil
import sqlite3
import ssl
import sys
import tempfile
import time
import unittest
from email.message import EmailMessage
from unittest import mock
from urllib.parse import parse_qsl, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.dont_write_bytecode = True

import import_sources as srcmod  # noqa: E402
import pc_fetch as pf  # noqa: E402
import pc_import  # noqa: E402
from test_pc_import import write_xls, write_xlsx  # noqa: E402

TODAY = dt.date(2026, 10, 28)
NOW = dt.datetime(2026, 10, 28, 15, 0, 0)
EXAMPLE = os.path.join(ROOT, "pc", "import", "받기.toml.example")
SRC = os.path.join(ROOT, "scripts", "pc_fetch.py")
FAKE_SECRETS = {"household/naver-imap": ("fakeuser_ZQ8", "FAKE-APP-PW-7Q3x"),
                "household/kakaobank-xlsx": ("xlsx", "FAKEXLSXPW-91z")}
SECRET_STRINGS = ("fakeuser_ZQ8", "FAKE-APP-PW-7Q3x", "FAKEXLSXPW-91z")
SHC_ID, SHC_CHECK, HYC_ID, SHB_ID, KB_ID = "본인111*", "본인222*", "*-9876*", "110-555-666666", "****-**-***4321"

ACCOUNTS = r'''
start_date = "2026-10-01"
date_slack = { card = 2, bank = 0 }
platform_keys = ["가짜페이"]

[[source]]
id = "현대카드"
kind = "card"
card = "현대카드"
account = "생활비"
file_types = ["html"]
columns = ["승인일", "승인시각", "카드구분", "카드종류", "가맹점명", "승인금액", "이용구분", "할부개월", "승인번호", "취소일", "승인구분"]
identity = ["*-9876*"]
identity_column = "카드종류"
date_basis = "승인일"
date_formats = ["%Y년 %m월 %d일"]
time_formats = ["%H:%M"]
installment_mode = "total"
pay_day = 12
footer_re = "^-"
[source.roles]
date = "승인일"
time = "승인시각"
merchant = "가맹점명"
amount = "승인금액"
installment_months = "할부개월"
approval = "승인번호"
cancel = "승인구분"
[source.values]
"승인일" = { type = "s", re = '\d{4}년 \d{2}월 \d{2}일' }
"승인시각" = { type = "s", re = '\d{2}:\d{2}' }
"카드구분" = { type = "s", re = '본인|가족' }
"카드종류" = { type = "s" }
"가맹점명" = { type = "s" }
"승인금액" = { type = "s", re = '[\d,]+' }
"이용구분" = { type = "s", re = '일시불|할부.*' }
"할부개월" = { type = "s", re = '\d+' }
"승인번호" = { type = "s", re = '\d{6}|\d{8}' }
"취소일" = { type = "s", re = '-|\d{4}년 \d{2}월 \d{2}일' }
"승인구분" = { type = "s", re = '전표접수|승인|취소|취소전표접수' }

[[source]]
id = "신한카드"
kind = "card"
card = "신한카드"
account = "생활비"
file_types = ["xlsx"]
columns = ["거래일", "카드구분", "이용카드", "가맹점명", "업종", "승인번호", "금액", "매입구분", "이용구분", "거래통화", "최초결제일자", "해외이용금액", "취소상태"]
identity = ["본인111*"]
identity_column = "이용카드"
exclude_rows = [{ column = "카드구분", values = ["체크"] }]
date_basis = "승인일"
date_formats = ["%Y.%m.%d %H:%M"]
installment_mode = "total"
pay_day = 14
footer_re = '^(?!\d{4}\.)'
[source.roles]
date = "거래일"
merchant = "가맹점명"
amount = "금액"
approval = "승인번호"
cancel = "매입구분"
installment_months = "이용구분"
foreign = "해외이용금액"
[source.values]
"거래일" = { type = "s", re = '\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}' }
"카드구분" = { type = "s", re = '신용|체크' }
"이용카드" = { type = "s" }
"가맹점명" = { type = "s" }
"업종" = { type = "s", empty_ok = true }
"승인번호" = { type = "s", re = '\d{8}' }
"금액" = { type = "n" }
"매입구분" = { type = "s", re = '승인|결제확정|승인취소' }
"이용구분" = { type = "s", re = '일시불|할부\(\d+개월\)' }
"거래통화" = { type = "s", empty_ok = true }
"최초결제일자" = { type = ["s", "d"], empty_ok = true }
"해외이용금액" = { type = ["s", "n"], empty_ok = true }
"취소상태" = { type = "s", re = '취소', empty_ok = true }

[[source]]
id = "신한은행"
kind = "bank"
account = "신한은행"
file_types = ["xls"]
columns = ["거래일자", "거래시간", "적요", "출금(원)", "입금(원)", "내용", "잔액(원)", "거래점"]
identity = ["110-555-666666"]
count_re = '총건수\s*(\d+)'
period_re = '(\d{4})\.(\d{2})\.(\d{2})\s*~\s*(\d{4})\.(\d{2})\.(\d{2})'
date_formats = ["%Y-%m-%d"]
[source.roles]
date = "거래일자"
time = "거래시간"
merchant = "내용"
memo = ["적요", "내용", "거래점"]
out = "출금(원)"
in = "입금(원)"
balance = "잔액(원)"
[source.values]
"거래일자" = { type = "s", re = '\d{4}-\d{2}-\d{2}' }
"거래시간" = { type = "s", re = '\d{2}:\d{2}:\d{2}' }
"적요" = { type = "s" }
"출금(원)" = { type = "n" }
"입금(원)" = { type = "n" }
"내용" = { type = "s", empty_ok = true }
"잔액(원)" = { type = "n" }
"거래점" = { type = "s", empty_ok = true }

[[source]]
id = "카카오뱅크"
kind = "bank"
account = "생활비"
file_types = ["xlsx"]
columns = ["", "거래일시", "구분", "거래금액", "거래 후 잔액", "거래구분", "내용", "메모"]
identity = ["****-**-***4321"]
period_re = '(\d{4})\.(\d{2})\.(\d{2})\s*-\s*(\d{4})\.(\d{2})\.(\d{2})'
date_formats = ["%Y.%m.%d %H:%M:%S"]
direction_values = { "입금" = "in", "출금" = "out" }
[source.roles]
date = "거래일시"
merchant = "내용"
memo = ["거래구분", "내용", "메모"]
amount = "거래금액"
direction = "구분"
balance = "거래 후 잔액"
category = "거래구분"
[source.values]
"" = { type = "any", empty_ok = true }
"거래일시" = { type = "s", re = '\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}' }
"구분" = { type = "s", re = '입금|출금' }
"거래금액" = { type = "s", re = '-?[\d,]+' }
"거래 후 잔액" = { type = "s", re = '[\d,]+' }
"거래구분" = { type = "s" }
"내용" = { type = "s", empty_ok = true }
"메모" = { type = "s", empty_ok = true }
'''

# ---------------------------------------------------------------- 합성 행(가짜 상호·가짜 금액)
SHC_ROWS = [  # (날짜, 시각, 구분, 이용카드, 가맹점, 승인번호, 금액, 매입구분)
    ("2026-10-03", "12:01", "신용", SHC_ID, "가상식당", "40000001", 12013, "결제확정"),
    ("2026-10-09", "08:30", "체크", SHC_CHECK, "가상편의점", "40000002", 3013, "결제확정"),
    ("2026-10-15", "19:45", "신용", SHC_ID, "가상마트", "40000003", 45013, "결제확정"),
    ("2026-10-21", "13:10", "신용", SHC_ID, "가상카페", "40000004", 5513, "승인취소"),
    ("2026-10-28", "09:05", "신용", SHC_ID, "가상서점", "40000005", 21013, "승인"),
]
SHC_MANY = [(f"2026-10-{1 + i % 28:02d}", f"{8 + i % 12:02d}:{i % 60:02d}", "신용", SHC_ID, f"가상가게{i}",
             f"{41000000 + i}", 1013 + 100 * i, "결제확정") for i in range(47)]   # >20건(응답 목록은 20 · 다음 쪽 키)
HYC_ROWS = [  # (날짜, 시각, 가맹점, 금액, 승인번호, 상태)
    ("2026-10-02", "10:00", "가상주유소", 51013, "70000001", "전표접수"),
    ("2026-10-11", "18:20", "가상빵집", 8013, "70000002", "전표접수"),
    ("2026-10-19", "21:00", "가상식품", 33013, "70000003", "승인"),
    ("2026-10-27", "07:40", "가상약국", 6013, "70000004", "승인"),
]
SHB_ROWS = [  # (날짜, 시각, 적요, 출금, 입금, 내용)
    ("2026-10-02", "09:00:00", "급여", 0, 2000013, "가상회사"),
    ("2026-10-12", "11:00:00", "카드대금", 300013, 0, "현대카드"),
    ("2026-10-14", "11:30:00", "카드대금", 200013, 0, "신한카드"),
    ("2026-10-25", "16:00:00", "체크", 3013, 0, "가상편의점"),
]
KB_ROWS = [  # (날짜시각, 구분, 금액(부호), 거래구분, 내용)
    ("2026.10.05 10:00:00", "입금", 500013, "일반입금", "가상입금"),
    ("2026.10.17 12:00:00", "출금", -120013, "체크카드", "가상식당"),
    ("2026.10.26 20:00:00", "출금", -30013, "일반출금", "가상가게"),
]


def in_period(rows, start, end, key=lambda r: r[0]):
    return [r for r in rows if start <= dt.date.fromisoformat(key(r)[:10].replace(".", "-")) <= end]


def shc_xlsx(path, rows, tail=None):
    """신한카드 실물 꼴: 시트 '카드이용내역' · 헤더 0행 13열 · 데이터 · 꼬리 1행(가맹점명 칸 '총 N건')."""
    cols = ["거래일", "카드구분", "이용카드", "가맹점명", "업종", "승인번호", "금액", "매입구분", "이용구분", "거래통화",
            "최초결제일자", "해외이용금액", "취소상태"]
    data = [cols]
    for d, t, kind, card, m, ap, a, buy in sorted(rows, reverse=True):
        amt = -a if buy == "승인취소" else a
        data.append([f"{d.replace('-', '.')} {t}", kind, card, m, "", ap, amt, buy, "일시불", "", "", "",
                     "취소" if buy == "승인취소" else ""])
    data.append(["", "", "", f"총 {len(rows) if tail is None else tail}건", "", "", sum(r[6] for r in rows)])
    write_xlsx(path, data, sheet="카드이용내역")


def hyc_html(rows, tail=None, head="실시간 이용내역"):
    """현대카드 실물 꼴 HTML(.xls) — 머리 2줄 · 헤더 11열 · 데이터 · 꼬리 3줄(소계 2 + 총 합계 소계)."""
    def tr(cells):
        return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    body = [tr([head]), tr(["이용내역"]),
            tr(["승인일", "승인시각", "카드구분", "카드종류", "가맹점명", "승인금액", "이용구분", "할부개월", "승인번호", "취소일", "승인구분"])]
    for d, t, m, a, ap, st in sorted(rows, reverse=True):
        y, mo, da = d.split("-")
        body.append(tr([f"{y}년 {mo}월 {da}일", t, "본인", "4***-****-****-9876*", m, f"{a:,}", "일시불", "0", ap, "-", st]))
    n = len(rows)
    body += [tr(["-", f"국내 일시불 소계 {max(n - 1, 0)}건", f"{sum(r[3] for r in rows):,}"]),
             tr(["-", "해외 일시불 소계 1건", "1,000"]),
             tr(["-", f"총 합계 소계 {n if tail is None else tail}건", f"{sum(r[3] for r in rows):,}"])]
    return ("<html><head><meta charset='utf-8'></head><body><table>" + "".join(body) + "</table></body></html>").encode("utf-8")


def shb_xls(path, rows, start, end, count=None, ident=SHB_ID, bal0=1_000_013):
    """신한은행 실물 꼴 BIFF: 머리 거래내역조회·계좌번호·조회기간·총건수 · 헤더 6행 8열 · 최신순."""
    bal, data = bal0, []
    for d, t, memo, o, i, content in sorted(rows):
        bal = bal - o + i
        data.append([d, t, memo, o, i, content, bal, "가상지점"])
    data.reverse()
    head = [["거래내역조회"], [], ["계좌번호", ident], ["조회기간", f"{start:%Y.%m.%d} ~ {end:%Y.%m.%d}"],
            ["총건수", len(rows) if count is None else count], []]
    write_xls(path, head + [["거래일자", "거래시간", "적요", "출금(원)", "입금(원)", "내용", "잔액(원)", "거래점"]] + data,
              sheet="신한은행_거래내역조회")


def kb_xlsx(path, rows, start, end, requested, ident=KB_ID, bal0=700_013):
    """카카오뱅크 푼 파일 꼴: 시트 '카카오뱅크 거래내역' · 머리(성명·조회기간·계좌번호·요청일시·안내) · 헤더 10행 · 모든 칸 문자열."""
    bal, data = bal0, []
    for when, kind, a, cat, content in rows:
        bal += a
        data.append(["", when, kind, f"{a:,}", f"{bal:,}", cat, content, ""])
    data.reverse()
    head = [["카카오뱅크 거래내역"], [], [],
            ["성명", "가짜이름", "조회기간", f"{start:%Y.%m.%d} - {end:%Y.%m.%d}"],
            ["계좌번호", ident, "요청일시", f"{requested:%Y.%m.%d %H:%M:%S}"], [],
            ["※ 금액앞에 '-' 표시는 출금 금액입니다."], ["※ 참고용 문서입니다."], ["※ 안내"], []]
    write_xlsx(path, head + [["", "거래일시", "구분", "거래금액", "거래 후 잔액", "거래구분", "내용", "메모"]] + data,
               sheet="카카오뱅크 거래내역")


FAKE_MARK = srcmod.OLE_MAGIC + srcmod.ENCRYPTED_MARK


def fake_encrypt(plain, pw):
    """암호 xlsx 흉내 — OLE 머리 + EncryptedPackage 표식(sniff = encrypted) + 암호 해시 + 본문."""
    return FAKE_MARK + b"|" + pf.short_hash(pw).encode() + b"|" + base64.b64encode(plain)


def fake_decrypt(src, dst, pw):
    with open(src, "rb") as f:
        data = f.read()
    if not data.startswith(FAKE_MARK):
        raise ValueError("not encrypted")
    _m, h, body = data.split(b"|", 2)
    if h.decode() != pf.short_hash(pw):
        raise ValueError("InvalidKeyError")
    with open(dst, "xb") as o:
        o.write(base64.b64decode(body))


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def rmtree(p):
    shutil.rmtree(p, ignore_errors=True)


def snapshot(*roots):
    out = []
    for r in roots:
        if not os.path.exists(r):
            out.append((r, "없음"))
            continue
        for dp, dns, fns in os.walk(r):
            dns.sort()
            for n in sorted(dns + fns):
                p = os.path.join(dp, n)
                st = os.stat(p)
                out.append((p, st.st_size if os.path.isfile(p) else -1, st.st_mtime_ns,
                            pc_import.md5_file(p) if os.path.isfile(p) else ""))
    return out


# ---------------------------------------------------------------- 시험 세계(임시 작업 루트)
class World:
    """임시 D:\\household 흉내: rules/(계좌.toml·받기.toml·원장) · uploads/내역/ · uploads/받은메일/ · work/받기/."""

    def __init__(self, tc, fake_hosts=True, timeouts=None):
        self.tc = tc
        self.home = tempfile.mkdtemp(prefix="pcfetch-")
        tc.addCleanup(rmtree, self.home)
        h = self.home.replace("\\", "/")
        self.rules = os.path.join(self.home, "rules")
        self.inbox = os.path.join(self.home, "uploads", "내역")
        self.drop = os.path.join(self.home, "uploads", "받은메일")
        self.runs = os.path.join(self.home, "work", "받기")
        self.profile = os.path.join(self.home, "chrome-profile-fetch")
        for d in (self.rules, self.inbox, self.drop):
            os.makedirs(d)
        with open(os.path.join(self.rules, "계좌.toml"), "w", encoding="utf-8") as f:
            f.write(ACCOUNTS)
        text = read_bytes(EXAMPLE).decode("utf-8").replace("D:/household/", h + "/")
        text = text.replace('extra_identity = ["<체크카드 이용카드 값 예: 본인000*>"]', f'extra_identity = ["{SHC_CHECK}"]')
        if fake_hosts:
            for real, fake in (("https://www.shinhancard.com", "https://fake-shc.test"),
                               ("https://www.hyundaicard.com", "https://fake-hyc.test"),
                               ("https://bank.shinhan.com", "https://fake-shb.test")):
                text = text.replace(real, fake)
        t = {"login": 8, "login_stable": 1, "page": 6, "query": 6, "download": 6, "click": 4, "site_step": 60, "menu_manual": 0}
        t.update(timeouts or {})
        for k, v in t.items():
            text, n = re.subn(rf"(?m)^{k} = \d+", f"{k} = {v}", text, count=1)
            tc.assertEqual(n, 1, k)
        self.config = os.path.join(self.rules, "받기.toml")
        with open(self.config, "w", encoding="utf-8") as f:
            f.write(text)

    def ledger(self, periods):
        con = sqlite3.connect(os.path.join(self.rules, "원장.sqlite"))
        con.executescript(pc_import.LEDGER_SCHEMA)
        con.executemany("INSERT INTO period VALUES (?,?,?,?,?,?)",
                        [(s, f"f{i}", f"{i:032x}", a, b, "r") for i, (s, a, b) in enumerate(periods)])
        con.commit()
        con.close()

    def put(self, folder, name, data):
        d = os.path.join(self.inbox, folder)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        if callable(data):
            data(p)
        else:
            with open(p, "wb") as f:
                f.write(data)
        return p

    def run(self, *argv, hooks=None, today=TODAY):
        hk = {"cred_read": lambda t: FAKE_SECRETS.get(t), "decrypt": fake_decrypt, "now": lambda: NOW,
              "sleep": lambda s: None}
        hk.update(hooks or {})
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = pf.main(["--config", self.config, "--today", today.isoformat(), *argv], hooks=hk)
        self.out = out.getvalue() + err.getvalue()
        return rc, self.out

    def run_dirs(self):
        return sorted(os.listdir(self.runs)) if os.path.isdir(self.runs) else []

    def summary(self, rid=None):
        rid = rid or self.run_dirs()[-1]
        with open(os.path.join(self.runs, rid, "summary.json"), encoding="utf-8") as f:
            return json.load(f)

    def all_text(self, *roots):
        """회차 폴더 등의 모든 파일 바이트(노출 grep용)."""
        blob = b""
        for r in roots:
            for dp, _d, fs in os.walk(r):
                for n in fs:
                    blob += read_bytes(os.path.join(dp, n)) + n.encode("utf-8")
        return blob


def load_acc(w):
    return pf.load_accounts(os.path.join(w.rules, "계좌.toml"))


# ================================================================ 1. 설정
class ConfigTests(unittest.TestCase):
    def test_example_valid_and_action_counts(self):
        """예시 설정은 규칙에 맞고, 허용 목록 길이는 설계 수(타이핑 2 · JS 값 6 · 체크 1(+8열 읽기) · select 1)와 같다."""
        cfg = pf.load_config(EXAMPLE)
        by = {}
        for s in cfg["site"]:
            for a in s["actions"].values():
                by[a.get("type", "click")] = by.get(a.get("type", "click"), 0) + 1
        self.assertEqual((by["date"], by["js_date"], by["check"], by["select"]), (2, 6, 1, 1))
        shb = [s for s in cfg["site"] if s["id"] == "신한은행"][0]
        self.assertEqual(len(shb["excel_checks"]["ids"]), 8)

    def test_contradiction_rejected(self):
        """허용 글자 안의 금지 문구·빈 기대 글자·없는 동작 참조·표 밖으로 샌 금지 목록은 실행 전에 막는다."""
        base = read_bytes(EXAMPLE).decode("utf-8")
        cases = {
            "금지 문구": base.replace('expect = ["엑셀 저장"]', 'expect = ["엑셀 저장 신청"]'),
            "빈 기대": base.replace('expect = ["필터 적용"]', 'expect = [" "]'),
            "없는 동작": base.replace('prepare = ["start", "end", "rows_all"]', 'prepare = ["start", "end", "rows_x"]'),
            "금지 목록 없음": base.replace("forbidden_texts = [", "x_forbidden = ["),
        }
        for name, text in cases.items():
            with self.subTest(name):
                td = tempfile.mkdtemp()
                self.addCleanup(rmtree, td)
                p = os.path.join(td, "c.toml")
                with open(p, "w", encoding="utf-8") as f:
                    f.write(text)
                with self.assertRaises(pf.StopRun):
                    pf.load_config(p)

    def test_paths_inside_inbox_or_clone_rejected(self):
        w = World(self)
        cfg = pf.load_config(w.config)
        p = pf.resolve_paths(cfg)
        self.assertEqual(pf.path_problems(p), [])
        bad = dict(p, runs_root=os.path.join(p["inbox_root"], "x"))
        self.assertTrue(pf.path_problems(bad))
        bad = dict(p, mail_drop=os.path.join(p["scan_inbox"], "메일"))
        self.assertTrue(pf.path_problems(bad))
        bad = dict(p, inbox_root=os.path.join(ROOT, "x"))
        self.assertTrue(pf.path_problems(bad))


# ================================================================ 2. 정적 시험(AST) — 완료 기준 2·3·11·12·13·15
class StaticTests(unittest.TestCase):
    OPS = {"click", "dblclick", "tap", "press", "type", "fill", "check", "uncheck", "select_option", "set_checked",
           "dispatch_event", "press_sequentially", "insert_text", "set_input_files", "drag_to", "hover", "focus"}
    GUARDS = {"click_allowed", "type_text", "set_js_value", "set_check", "set_select"}

    @classmethod
    def setUpClass(cls):
        cls.src = read_bytes(SRC).decode("utf-8")
        cls.tree = ast.parse(cls.src)
        cls.calls = []

        def walk(node, fns):
            for ch in ast.iter_child_nodes(node):
                f2 = fns + [ch.name] if isinstance(ch, (ast.FunctionDef, ast.ClassDef)) else fns
                if isinstance(ch, ast.Call):
                    cls.calls.append((ch, f2))
                if isinstance(ch, ast.Attribute) and ch.attr in ("keyboard", "mouse", "touchscreen"):
                    cls.calls.append((ch, f2))
                walk(ch, f2)

        walk(cls.tree, [])

    def attr(self, name):
        return [(c, f) for c, f in self.calls if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                and c.func.attr == name]

    def test_ops_only_in_guards(self):
        """조작 호출(click·키보드·dispatch_event·select_option …)은 guard 함수 다섯 안에만 — 좌표 클릭(mouse) 0."""
        found = []
        for c, fns in self.calls:
            if isinstance(c, ast.Attribute):
                found.append((fns[-1] if fns else None, c.attr, c.lineno))
            elif isinstance(c.func, ast.Attribute) and c.func.attr in self.OPS:
                found.append((fns[-1] if fns else None, c.func.attr, c.lineno))
        outside = [x for x in found if x[0] not in self.GUARDS]
        self.assertEqual(outside, [])
        kinds = {}
        for fn, op, _ in found:
            kinds.setdefault(fn, set()).add(op)
        self.assertEqual(kinds, {"click_allowed": {"click"}, "type_text": {"keyboard", "press", "type"},
                                 "set_check": {"dispatch_event"}, "set_select": {"select_option"}})
        self.assertNotIn("mouse", self.src)

    def test_js_writes_only_in_set_js_value(self):
        """evaluate 문자열 안 '.click('·'.checked ='은 0, '.value ='는 SET_VALUE_JS 하나 — 그 이름은 set_js_value에서만."""
        consts = {}
        for n in self.tree.body:
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name):
                try:
                    v = ast.literal_eval(n.value)
                except ValueError:
                    v = ast.unparse(n.value)
                consts[n.targets[0].id] = str(v)
        js = {k: v for k, v in consts.items() if k.endswith("_JS") or k == "JS_VIS"}
        self.assertEqual([k for k, v in js.items() if ".click(" in v], [])
        self.assertFalse(re.search(r"\.checked\s*=[^=]", self.src))
        with_value = [k for k, v in js.items() if re.search(r"\.value\s*=[^=]", v)]
        self.assertEqual(with_value, ["SET_VALUE_JS"])
        uses = [fns[-1] for c, fns in self.calls if isinstance(c, ast.Call)
                for a in c.args if isinstance(a, ast.Name) and a.id == "SET_VALUE_JS"]
        self.assertEqual(uses, ["set_js_value"])

    def test_evaluate_only_in_ev_with_constant_js(self):
        """evaluate는 ev() 한 곳 · ev의 JS 인자는 모듈 대문자 상수(설정·변수에서 온 JS 0) · 상수는 모두 JSON.stringify로 돌려준다."""
        ev = self.attr("evaluate")
        self.assertEqual({f[-1] for _c, f in ev}, {"ev"})
        upper = {t.id for n in self.tree.body if isinstance(n, ast.Assign) for t in n.targets
                 if isinstance(t, ast.Name) and t.id.isupper() and t.id.endswith("_JS")}
        sites = [c for c, _f in self.calls if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "ev"]
        self.assertGreater(len(sites), 10)
        bad = [ast.unparse(c)[:60] for c in sites if not (isinstance(c.args[1], ast.Name) and c.args[1].id in upper)]
        self.assertEqual(bad, [])
        for n in self.tree.body:
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id in upper:
                self.assertIn("JSON.stringify", ast.get_source_segment(self.src, n.value), n.targets[0].id)

    def test_dialog_never_accepted_dismiss_only_in_script_phase(self):
        """accept 호출 0 · dismiss는 DialogWatch.__call__의 '스크립트 단계' 분기 하나(로그인 대기 중 호출 0)."""
        self.assertEqual(self.attr("accept"), [])
        dis = self.attr("dismiss")
        self.assertEqual([f for _c, f in dis], [["DialogWatch", "__call__"]])
        cls = [n for n in self.tree.body if isinstance(n, ast.ClassDef) and n.name == "DialogWatch"][0]
        call = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__call__"][0]
        ifs = [n for n in ast.walk(call) if isinstance(n, ast.If) and "dismiss" in ast.unparse(n)]
        self.assertEqual(ast.unparse(ifs[0].test), "self.phase == 'script'")

    def test_close_sites(self):
        """.close()는 sqlite(con)와 끝 정리(ctx) 뿐 — 새 창·사용자 창을 닫지 않는다. IMAP은 close() 0(logout만)."""
        sites = sorted((f[-1], ast.unparse(c.func.value)) for c, f in self.attr("close"))
        self.assertEqual(sites, [("clean_download_history", "con"), ("last_received", "con"), ("run_web", "ctx")])

    def test_imap_whitelist(self):
        """IMAP: IMAP4_SSL(ssl_context=create_default_context()) · login · select(readonly=True) · search · fetch(PEEK) · logout뿐.
        close·store·copy·expunge·create·uid·append·move·delete·debug 0."""
        fn = [n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == "imap_collect"][0]
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and isinstance(n.func.value, ast.Name) and n.func.value.id == "M"]
        self.assertEqual(sorted({c.func.attr for c in calls}), ["fetch", "login", "logout", "search", "select"])
        for c in calls:
            if c.func.attr == "select":
                self.assertEqual([(k.arg, ast.literal_eval(k.value)) for k in c.keywords], [("readonly", True)])
            if c.func.attr == "fetch":
                spec = ast.literal_eval(c.args[1])
                items = re.findall(r"[A-Z0-9.]+(?:\[[^\]]*\])?", spec.replace("(", " ").replace(")", " "))
                self.assertTrue(all(i in ("INTERNALDATE", "RFC822.SIZE", "FROM", "SUBJECT", "AUTHENTICATION-RESULTS")
                                    or i.startswith("BODY.PEEK[") for i in items), spec)
        ssl_calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and ast.unparse(n.func) == "imaplib.IMAP4_SSL"]
        self.assertEqual(len(ssl_calls), 1)
        self.assertEqual([k.arg for k in ssl_calls[0].keywords if k.arg == "ssl_context"], ["ssl_context"])
        self.assertIn("tls = ssl.create_default_context()", ast.unparse(fn))
        body = ast.unparse(fn)
        for bad in ("M.store", "M.copy", "M.expunge", "M.create", "M.uid", "M.append", "M.move", "M.close", "M.delete",
                    ".debug", "Debug", "RFC822)", "'BODY[", "readonly=False"):
            self.assertNotIn(bad, body, bad)
        self.assertNotIn("imaplib.Debug", self.src)
        self.assertNotIn(".debug =", self.src)

    def test_no_secret_inputs_or_dumps(self):
        """비밀을 명령줄·환경변수·getpass로 받지 않고, 응답·요청 본문 저장·tracing·HAR·storage_state·스크린샷(디버그 밖) 0."""
        for bad in ("getpass", "os.environ", "record_har", "tracing", "storage_state", "add_argument(\"--password",
                    "--pass", "/pass:"):
            self.assertNotIn(bad, self.src, bad)
        shots = self.attr("screenshot")
        self.assertEqual([f[-1] for _c, f in shots], ["_debug_shot"])


# ================================================================ 3. 노출 0 — 정화기
class ScrubTests(unittest.TestCase):
    def test_masks_ids_amounts_mail_keeps_dates_and_trusted(self):
        s = pf.scrub("계좌 110-555-666666 · 카드 4111-2222-3333-4444 · 승인 40000001 · 금액 1,234,567원 · 12345원 · "
                     "a.b+c@naver.com · 2026-10-28 · 15:30:00 · " + pf.trust(r"D:\work\받기\20261028-150000"))
        for leak in ("110-555-666666", "4111", "40000001", "1,234,567", "12345", "naver.com"):
            self.assertNotIn(leak, s)
        self.assertIn("2026-10-28", s)
        self.assertIn("15:30:00", s)
        self.assertIn("20261028-150000", s)
        self.assertNotIn("\x02", s)

    def test_text_note_and_src_err(self):
        self.assertEqual(pf.text_note("조회", "조회"), "조회")
        self.assertNotIn("홍길동", pf.text_note("홍길동님 환영합니다", "조회"))
        e = srcmod.SourceError("식별 열 '이용카드' 값이 설정한 카드·계좌(본인111*)와 다르다 — 다른 카드·계좌 파일")
        self.assertNotIn("본인111", pf.src_err(e))
        self.assertNotIn("4,013", pf.src_err(srcmod.SourceError("f:3행: 금액을 읽을 수 없다: '4,013원x'")))


# ================================================================ 4. 받는 기간 — 완료 기준 4
class PeriodTests(unittest.TestCase):
    def plan(self, w, *argv):
        cfg = pf.load_config(w.config)
        paths = pf.resolve_paths(cfg)
        acc = load_acc(w)
        args = pf.parse_args(["--config", w.config, *argv])
        return pf.make_plan(cfg, acc, paths, TODAY, args)

    def test_no_files_ledger_inbox_caps(self):
        w = World(self)
        p = self.plan(w)
        self.assertEqual({k: (v["start"], v["end"]) for k, v in p["items"].items()},
                         {k: (dt.date(2026, 10, 1), TODAY) for k in ("신한카드", "현대카드", "신한은행", "카카오뱅크")})
        self.assertIn("첫 받기", p["items"]["신한카드"]["basis"])
        # 원장만: 지난 끝 − 30일(start_date보다 늦을 때)
        w2 = World(self)
        with open(os.path.join(w2.rules, "계좌.toml"), "w", encoding="utf-8") as f:
            f.write(ACCOUNTS.replace('start_date = "2026-10-01"', 'start_date = "2026-01-01"'))
        w2.ledger([("신한은행", "2026-01-01", "2026-10-20"), ("현대카드", "2026-03-01", "2026-05-01")])
        p = self.plan(w2)
        self.assertEqual(p["items"]["신한은행"]["start"], dt.date(2026, 9, 20))
        self.assertIn("지난 받은 끝 2026-10-20 − 30일", p["items"]["신한은행"]["basis"])
        # 현대카드 90일 상한에서 자르고 경고 · 신한카드 1년(파일 없음 → start_date 2026-01-01은 1년 안)
        self.assertEqual(p["items"]["현대카드"]["start"], TODAY - dt.timedelta(days=90))
        self.assertIn("상한 90일", p["items"]["현대카드"]["warn"])
        self.assertEqual(p["items"]["신한카드"]["start"], dt.date(2026, 1, 1))
        # inbox만(apply 전 파일): 머리 조회기간 끝을 본다 · 암호 원본은 ignore_files면 무시, 아니면 읽기 실패로 제외
        w3 = World(self)
        with open(os.path.join(w3.rules, "계좌.toml"), "w", encoding="utf-8") as f:
            f.write(ACCOUNTS.replace('start_date = "2026-10-01"', 'start_date = "2026-01-01"')
                    + '\n[[ignore_files]]\npath = "20261020/enc.xlsx"\nmd5 = "%s"\nreason = "암호 원본"\n'
                    % pc_import.md5_bytes(fake_encrypt(b"x", "pw")))
        w3.put("20261020", "s.xls", lambda q: shb_xls(q, SHB_ROWS[:2], dt.date(2026, 10, 1), dt.date(2026, 10, 18)))
        w3.put("20261020", "enc.xlsx", fake_encrypt(b"x", "pw"))
        w3.put("20261020", "enc2.xlsx", fake_encrypt(b"y", "pw"))
        w3.put("20261020", "broken.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1broken")
        p = self.plan(w3)
        self.assertEqual(p["items"]["신한은행"]["start"], dt.date(2026, 9, 18))
        self.assertEqual((p["inbox"]["files"], p["inbox"]["ignored"], p["inbox"]["failed"]), (4, 1, 2))

    def test_start_override_widens_only(self):
        w = World(self)
        p = self.plan(w, "--start", "신한카드=2026-09-01")
        self.assertEqual(p["items"]["신한카드"]["start"], dt.date(2026, 9, 1))
        self.assertIn("지정 --start 2026-09-01", p["items"]["신한카드"]["basis"])
        with self.assertRaises(pf.StopRun):
            self.plan(w, "--start", "신한카드=2026-10-05")
        p = self.plan(w, "--start", "현대카드=2026-01-01")  # 앞으로 넓혀도 상한에서 자르고 경고
        self.assertEqual(p["items"]["현대카드"]["start"], TODAY - dt.timedelta(days=90))
        self.assertTrue(p["items"]["현대카드"]["warn"])


# ================================================================ 5. 파일 검사 — 완료 기준 6·7·20
def site_cfg(sid):
    return [s for s in pf.load_config(EXAMPLE)["site"] if s["id"] == sid][0] | (
        {"extra_identity": [SHC_CHECK]} if sid == "신한카드" else {})


class FileCheckTests(unittest.TestCase):
    S, E = dt.date(2026, 10, 1), dt.date(2026, 10, 28)

    def setUp(self):
        self.w = World(self)
        self.acc = load_acc(self.w)
        self.td = tempfile.mkdtemp()
        self.addCleanup(rmtree, self.td)

    def item(self, n, **kw):
        return dict({"start": self.S, "end": self.E, "screen_count": n, "resp_count": n}, **kw)

    def check(self, sid, path, sug, it):
        return pf.check_web_file(path, sug, site_cfg(sid), self.acc, it)

    def test_three_pass(self):
        p = os.path.join(self.td, "Shinhancard_20261028.xlsx")
        shc_xlsx(p, SHC_ROWS)
        checks, r = self.check("신한카드", p, os.path.basename(p), self.item(5))
        self.assertTrue(all(c["ok"] for c in checks))
        self.assertEqual(r["excluded"], 1)          # 체크카드 행은 계좌.toml exclude_rows가 뺀다(건수는 5 그대로)
        p = os.path.join(self.td, "hyundaicard_20261028.xls")
        with open(p, "wb") as f:
            f.write(hyc_html(HYC_ROWS))
        self.check("현대카드", p, os.path.basename(p), self.item(4))
        p = os.path.join(self.td, "신한은행_거래내역조회_20261028150000.xls")
        shb_xls(p, SHB_ROWS, self.S, self.E)
        self.check("신한은행", p, os.path.basename(p), self.item(4, screen_in_out=[1, 3]))

    def test_failures(self):
        """잘린 파일(행 ≠ 꼬리)·다른 식별 값·기간 밖 행·로그인 HTML·JSON 응답·0바이트·암호 xlsx·이름 꼴·머리 상태 글자 →
        받지 못함."""
        cases = []
        p = os.path.join(self.td, "Shinhancard_20261028.xlsx")
        shc_xlsx(p, SHC_ROWS, tail=6)
        cases.append(("신한카드", p, self.item(5), "건수"))
        p2 = os.path.join(self.td, "Shinhancard_20261027.xlsx")
        shc_xlsx(p2, SHC_ROWS[:3])
        cases.append(("신한카드", p2, self.item(5), "건수"))           # 행 지운 파일(화면·응답은 5)
        p3 = os.path.join(self.td, "Shinhancard_20261026.xlsx")
        shc_xlsx(p3, [r[:3] + ("본인999*",) + r[4:] for r in SHC_ROWS])
        cases.append(("신한카드", p3, self.item(5), "서명"))          # 다른 카드
        p4 = os.path.join(self.td, "Shinhancard_20261025.xlsx")
        shc_xlsx(p4, SHC_ROWS + [("2026-09-20", "10:00", "신용", SHC_ID, "가상옛집", "40000009", 1013, "결제확정")])
        cases.append(("신한카드", p4, self.item(6), "기간"))
        p5 = os.path.join(self.td, "hyundaicard_20261028.xls")
        with open(p5, "wb") as f:
            f.write("<html><head><title>로그인-간편번호-현대카드</title></head><body><table><tr><td>로그인</td></tr></table></body></html>"
                    .encode("utf-8"))
        cases.append(("현대카드", p5, self.item(4), "서명"))
        p6 = os.path.join(self.td, "Shinhancard_20261024.xlsx")
        with open(p6, "wb") as f:
            f.write(b'{"mbw_result":"F","mbw_message":"login"}')
        cases.append(("신한카드", p6, self.item(5), "종류"))
        p7 = os.path.join(self.td, "Shinhancard_20261023.xlsx")
        open(p7, "wb").close()
        cases.append(("신한카드", p7, self.item(5), "종류"))
        p8 = os.path.join(self.td, "Shinhancard_20261022.xlsx")
        with open(p8, "wb") as f:
            f.write(fake_encrypt(b"x", "pw"))
        cases.append(("신한카드", p8, self.item(5), "종류"))
        p9 = os.path.join(self.td, "hyundaicard_20261021.xls")
        with open(p9, "wb") as f:
            f.write(hyc_html(HYC_ROWS, head="결제 확정 내역"))
        cases.append(("현대카드", p9, self.item(4), "머리 상태 글자"))
        p10 = os.path.join(self.td, "other.xlsx")
        shc_xlsx(p10, SHC_ROWS)
        cases.append(("신한카드", p10, self.item(5), "이름"))
        p11 = os.path.join(self.td, "신한은행_거래내역조회_20261028150001.xls")
        shb_xls(p11, SHB_ROWS, self.S, self.E, ident="110-777-888888")
        cases.append(("신한은행", p11, self.item(4, screen_in_out=[1, 3]), "서명"))
        p12 = os.path.join(self.td, "신한은행_거래내역조회_20261028150002.xls")
        shb_xls(p12, SHB_ROWS, self.S, self.E)
        cases.append(("신한은행", p12, self.item(4, screen_in_out=[1, 2]), "입출 건수"))
        p13 = os.path.join(self.td, "신한은행_거래내역조회_20261028150003.xls")
        shb_xls(p13, SHB_ROWS, dt.date(2026, 10, 2), self.E)
        cases.append(("신한은행", p13, self.item(4, screen_in_out=[1, 3]), "머리 조회기간"))
        for sid, path, it, want in cases:
            with self.subTest(os.path.basename(path)):
                with self.assertRaises(pf.ItemFail) as cm:
                    self.check(sid, path, os.path.basename(path), it)
                self.assertIn(want, str(cm.exception))

    def test_tail_regex_ignores_subtotals_and_amount_lines(self):
        """현대카드 꼬리: 국내·해외 소계 줄이 아니라 '총 합계 소계' 줄만(소계 수가 같아도 앵커) · 신한은행 입금합계 줄의 숫자는 안 잡음."""
        p = os.path.join(self.td, "hyundaicard_20261028.xls")
        with open(p, "wb") as f:
            f.write(hyc_html(HYC_ROWS, tail=7))
        with self.assertRaises(pf.ItemFail):
            self.check("현대카드", p, os.path.basename(p), self.item(4))
        r = pf.read_identified(p, self.acc, "현대카드")
        self.assertEqual(pf.file_count(r, site_cfg("현대카드")["excel_file"]), 7)
        sv = site_cfg("신한은행")["screen_values"]
        body = "조회결과2026.10.01 ~ 2026.10.28[총4건] 입금합계 (건수) 2,000,013원(1건) 출금합계 (건수) 503,039원(3건)"
        self.assertEqual({int(m.group(1)) for m in re.finditer(sv["count_re"], body)}, {4})
        self.assertEqual([int(re.search(x, body).group(1)) for x in sv["in_out_re"]], [1, 3])

    def test_screen_period_year_rollover(self):
        """화면 기간 글자: 끝 연도 없는 꼴(신한카드)·연도 없는 꼴(현대카드)·12→1월 해 넘김."""
        shc = re.compile(site_cfg("신한카드")["screen_values"]["period_re"])
        hyc = re.compile(site_cfg("현대카드")["screen_values"]["period_re"])
        ok = pf.screen_period_ok
        self.assertTrue(ok(shc.search("2026.10.01 ~ 10.28"), dt.date(2026, 10, 1), dt.date(2026, 10, 28)))
        self.assertTrue(ok(shc.search("2026.12.15 ~ 01.10"), dt.date(2026, 12, 15), dt.date(2027, 1, 10)))
        self.assertFalse(ok(shc.search("2026.10.02 ~ 10.28"), dt.date(2026, 10, 1), dt.date(2026, 10, 28)))
        self.assertTrue(ok(hyc.search("12월 1일 ~ 1월 10일"), dt.date(2026, 12, 1), dt.date(2027, 1, 10)))
        self.assertFalse(ok(hyc.search("10월 1일 ~ 10월 27일"), dt.date(2026, 10, 1), dt.date(2026, 10, 28)))


# ================================================================ 6. 조회 요청·응답 판정 — 완료 기준 5·7
class QueryTests(unittest.TestCase):
    def test_request_filter_and_period(self):
        q = site_cfg("신한카드")["query"]
        good = {"oriType": "PAY", "encCrdN": "", "qyTcd": "0", "fmlOCrdC": "0", "dmsTsF": "0", "crdTcd": "A",
                "qyStd": "20261001", "qyEdd": "20261028", "monthType": "4", "nextKey": "", "secret": "x"}
        post = "mbw_json=" + json.dumps(good)
        f = pf.parse_request(post, q)
        self.assertNotIn("secret", f)                     # 필요한 칸만
        self.assertEqual(pf.request_problems(f, q, dt.date(2026, 10, 1), dt.date(2026, 10, 28)), [])
        for k, v in (("dmsTsF", "1"), ("crdTcd", "C"), ("encCrdN", "abc"), ("qyStd", "20261002")):
            f2 = pf.parse_request("mbw_json=" + json.dumps(dict(good, **{k: v})), q)
            self.assertTrue(pf.request_problems(f2, q, dt.date(2026, 10, 1), dt.date(2026, 10, 28)), k)

    def test_response_count_next_key_period(self):
        q = site_cfg("신한카드")["query"]
        r = pf.parse_response(json.dumps({"mbw_json": {"totalCnt": "20", "payList": [1] * 20, "nextKey": ""}}).encode(), q)
        self.assertEqual((r["count"], r["list_len"], r["next_key"]), (20, 20, False))
        r = pf.parse_response(json.dumps({"mbw_json": {"totalCnt": "45", "payList": [1] * 20, "nextKey": "K2"}}).encode(), q)
        self.assertTrue(r["next_key"])
        self.assertEqual(r["next_key_shape"], "2자 글자")      # 값이 아니라 꼴만(리허설 실측 기록)
        r = pf.parse_response(json.dumps({"mbw_json": {"totalCnt": "5", "payList": [1] * 5}}).encode(), q)
        self.assertEqual((r["next_key"], r["next_key_shape"]), (None, "없음"))
        r = pf.parse_response(b"<html>login</html>", q)
        self.assertIsNone(r["count"])
        self.assertTrue(pf.response_problems(r, q, dt.date(2026, 10, 1), dt.date(2026, 10, 28)))
        qb = site_cfg("신한은행")["query"]
        body = {"dataBody": {"조회시작일_display": "2026.10.01", "조회종료일_display": "2026.10.28", "반복횟수": 4}}
        r = pf.parse_response(json.dumps(body, ensure_ascii=False).encode(), qb)
        self.assertEqual(pf.response_problems(r, qb, dt.date(2026, 10, 1), dt.date(2026, 10, 28)), [])
        self.assertTrue(pf.response_problems(r, qb, dt.date(2026, 10, 2), dt.date(2026, 10, 28)))
        qh = site_cfg("현대카드")["query"]
        bad = {"bdy": {"rcntSummaryInfo": {"totUseCnt": "4", "srtDt": "20261001", "endDt": "20261028", "crno": "",
                                           "zoneClsf": "1", "useClsf": "9", "usplClsf": "9"}, "rcntAvItm": [1] * 4}}
        r = pf.parse_response(json.dumps(bad).encode(), qh)
        self.assertTrue(any("zoneClsf" in x for x in pf.response_problems(r, qh, dt.date(2026, 10, 1), dt.date(2026, 10, 28))))
        bad["bdy"]["rcntSummaryInfo"]["zoneClsf"] = "9"
        bad["bdy"]["rcntAvItm"] = [1] * 3
        r = pf.parse_response(json.dumps(bad).encode(), qh)
        self.assertTrue(any("쪽 나눔" in x for x in pf.response_problems(r, qh, dt.date(2026, 10, 1), dt.date(2026, 10, 28))))


# ================================================================ 7. 카카오뱅크 — 완료 기준 6(카뱅 검사 4)·12·14·21
class FakeIMAP:
    """imaplib.IMAP4_SSL 자리 — 화이트리스트 밖 호출은 AttributeError. 읽기 전용·PEEK만 받는다."""
    instances = []
    messages = []          # [(INTERNALDATE epoch, raw bytes)]
    fail_login = False

    def __init__(self, host, port, ssl_context=None, timeout=None):
        assert isinstance(ssl_context, ssl.SSLContext), "ssl_context 없음"
        assert ssl_context.verify_mode == ssl.CERT_REQUIRED and ssl_context.check_hostname, "TLS 검증 꺼짐"
        self.host, self.log = host, []
        FakeIMAP.instances.append(self)

    def login(self, user, pw):
        self.log.append(("login", user, pw))
        if FakeIMAP.fail_login:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] fakeuser_ZQ8 invalid")
        return "OK", [b"ok"]

    def select(self, mbox, readonly=False):
        assert readonly is True, "readonly=False"
        self.log.append(("select", mbox))
        return "OK", [str(len(FakeIMAP.messages)).encode()]

    def search(self, charset, *crit):
        self.log.append(("search",) + crit)
        assert crit[0] == "SINCE" and crit[2] == "FROM", crit
        ids = [str(i + 1) for i in range(len(FakeIMAP.messages))]
        return "OK", [" ".join(ids).encode()]

    def fetch(self, ids, spec):
        self.log.append(("fetch", spec))
        assert "PEEK" in spec and "RFC822)" not in spec and "BODY[" not in spec.replace("BODY.PEEK[", ""), spec
        seqs = ids.decode().split(",") if isinstance(ids, bytes) else [ids.decode() if isinstance(ids, bytes) else str(ids)]
        out = []
        for s in seqs:
            when, raw = FakeIMAP.messages[int(s) - 1]
            if "HEADER.FIELDS" in spec:
                head = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
                meta = (f"{s} (INTERNALDATE {imaplib.Time2Internaldate(when)} RFC822.SIZE {len(raw)} "
                        f"BODY[HEADER.FIELDS (FROM SUBJECT AUTHENTICATION-RESULTS)] {{{len(head)}}}").encode()
                out += [(meta, head), b")"]
            else:
                out += [(f"{s} (BODY[] {{{len(raw)}}}".encode(), raw), b")"]
        return "OK", out

    def logout(self):
        self.log.append(("logout",))
        return "BYE", [b""]


def kakao_mail(attach, name=None, subject="[카카오뱅크] 고객님께서 요청하신 거래내역 엑셀파일입니다.",
               sender="no-reply@mail.kakaobank.com"):
    m = EmailMessage(policy=email.policy.SMTP)
    m["From"] = f"카카오뱅크 <{sender}>"
    m["Subject"] = subject
    m["Authentication-Results"] = "mx.naver.com; dkim=pass header.i=@mail.kakaobank.com"
    m.set_content("첨부")
    m.add_attachment(attach, maintype="application", subtype="octet-stream",
                     filename=name or "카카오뱅크_거래내역_N1234567890_2026102815010012.xlsx")
    return m.as_bytes()


class KakaoTests(unittest.TestCase):
    def setUp(self):
        self.w = World(self)
        FakeIMAP.instances, FakeIMAP.messages, FakeIMAP.fail_login = [], [], False
        self.td = tempfile.mkdtemp()
        self.addCleanup(rmtree, self.td)

    def kb(self, start=dt.date(2026, 10, 1), end=TODAY, requested=NOW + dt.timedelta(minutes=1), ident=KB_ID):
        p = os.path.join(self.td, f"kb{len(os.listdir(self.td))}.xlsx")
        kb_xlsx(p, KB_ROWS, start, end, requested, ident)
        return read_bytes(p)

    def epoch(self, d):
        return time.mktime(d.timetuple())

    def test_kakao_check4(self):
        """머리 조회기간 시작 ≤ 권장 시작 ∧ 끝 ≥ 오늘−1 ∧ 요청일시 ≥ 실행 시작−10분 ∧ 행 ⊂ 머리 기간 — 3개월 버튼 파일은 통과,
        지난 회차 파일·짧은 기간·다른 계좌는 받지 못함."""
        acc = load_acc(self.w)
        kc = pf.load_config(EXAMPLE)["kakao"]
        floor = NOW - dt.timedelta(minutes=10)
        good = os.path.join(self.td, "g.xlsx")
        with open(good, "wb") as f:
            f.write(self.kb(start=dt.date(2026, 7, 28)))
        res = pf.check_kakao(good, acc, kc, dt.date(2026, 10, 1), TODAY, floor)
        self.assertEqual(res["rows"], 3)
        for name, data in (("old", self.kb(requested=NOW - dt.timedelta(days=1))),
                           ("short", self.kb(start=dt.date(2026, 10, 10))),
                           ("stale", self.kb(end=dt.date(2026, 10, 20))),
                           ("other", self.kb(ident="****-**-***9999"))):
            with self.subTest(name):
                p = os.path.join(self.td, name + ".xlsx")
                with open(p, "wb") as f:
                    f.write(data)
                with self.assertRaises(pf.ItemFail):
                    pf.check_kakao(p, acc, kc, dt.date(2026, 10, 1), TODAY, floor)

    def run_mail(self, *argv):
        with mock.patch.object(pf.imaplib, "IMAP4_SSL", FakeIMAP):
            return self.w.run("--run", "--no-web", *argv)

    def test_imap_happy_newest_first_skips_other_account_and_old(self):
        """후보 = INTERNALDATE ≥ 실행 시작−10분 · 최신순 ≤3통 중 검사 통과 첫 1통(다른 계좌 내보내기 메일 혼재) · 옛 메일 제외 ·
        읽기 전용(EXAMINE·PEEK) · logout만 · 암호 원본은 회차 폴더에만 · 새 폴더에는 푼 파일 하나."""
        enc_good = fake_encrypt(self.kb(), "FAKEXLSXPW-91z")
        enc_other = fake_encrypt(self.kb(ident="****-**-***9999"), "FAKEXLSXPW-91z")
        FakeIMAP.messages = [(self.epoch(NOW - dt.timedelta(days=2)), kakao_mail(enc_good)),
                             (self.epoch(NOW + dt.timedelta(minutes=2)), kakao_mail(enc_good)),
                             (self.epoch(NOW + dt.timedelta(minutes=3)), kakao_mail(enc_other)),
                             (self.epoch(NOW + dt.timedelta(minutes=4)), kakao_mail(enc_good, sender="evil@example.com"))]
        rc, out = self.run_mail()
        self.assertEqual(rc, 0, out)
        s = self.w.summary()
        k = s["items"]["카카오뱅크"]
        self.assertEqual((k["status"], k["via"]), ("받음", "메일 2"))  # 최신(다른 계좌)은 떨어지고 그다음 통
        self.assertEqual(len(k["others"]), 1)
        new = os.listdir(self.w.inbox)
        self.assertEqual(len(new), 1)
        files = os.listdir(os.path.join(self.w.inbox, new[0]))
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].endswith("_v.xlsx"))
        self.assertEqual(srcmod.sniff(read_bytes(os.path.join(self.w.inbox, new[0], files[0]))), "xlsx")
        stats = [x for x in k["mail_notes"] if isinstance(x, dict)][0]
        self.assertEqual((stats["hits"], stats["fresh"], stats["from_ok"], stats["subject_ok"]), (4, 3, 3, 4))
        self.assertEqual(len(stats["tz"]), 1)
        im = FakeIMAP.instances[0]
        self.assertEqual({x[0] for x in im.log}, {"login", "select", "search", "fetch", "logout"})
        self.assertEqual(im.log[-1], ("logout",))
        self.assertEqual(im.log[0], ("login", "fakeuser_ZQ8", "FAKE-APP-PW-7Q3x"))
        blob = self.w.all_text(self.w.runs, self.w.inbox) + out.encode("utf-8")
        for sec in SECRET_STRINGS:
            self.assertNotIn(sec.encode(), blob, sec)

    def test_imap_failures_are_kakao_only(self):
        FakeIMAP.messages = [(self.epoch(NOW - dt.timedelta(days=1)), kakao_mail(fake_encrypt(self.kb(), "x")))]
        rc, out = self.run_mail()
        self.assertEqual(rc, 2)
        self.assertIn("메일 없음", self.w.summary()["items"]["카카오뱅크"]["error"])
        self.assertEqual(os.listdir(self.w.inbox), [])
        FakeIMAP.messages = [(self.epoch(NOW + dt.timedelta(minutes=1)), kakao_mail(fake_encrypt(self.kb(), "WRONG")))]
        rc, out = self.run_mail()
        self.assertEqual(rc, 2)
        self.assertIn("복호화 실패", self.w.summary()["items"]["카카오뱅크"]["error"])
        kdir = os.path.join(self.w.runs, self.w.run_dirs()[-1], "kakao")
        left = [n for _d, _s, fs in os.walk(kdir) for n in fs if n.endswith("_v.xlsx")]
        self.assertEqual(left, [])                      # 부분 출력 0
        FakeIMAP.fail_login = True
        rc, out = self.run_mail()
        self.assertEqual(rc, 2)
        self.assertIn("IMAP 로그인 실패", out)
        self.assertNotIn("fakeuser_ZQ8", out)           # 서버 문구(아이디 포함)는 남기지 않는다
        rc, out = self.w.run("--run", "--no-web", hooks={"cred_read": lambda t: None})
        self.assertEqual(rc, 2)
        self.assertIn("cmdkey /generic:household/naver-imap", out)
        self.assertNotIn("/pass:", out)

    def test_real_msoffcrypto_roundtrip(self):
        try:
            import msoffcrypto  # noqa: F401
            from msoffcrypto.format.ooxml import OOXMLFile
        except ImportError:
            self.skipTest("msoffcrypto 없음 — 설치 뒤 실제 복호화(완료 기준 14·19)")
        plain = os.path.join(self.td, "p.xlsx")
        with open(plain, "wb") as f:
            f.write(self.kb())
        enc = os.path.join(self.td, "e.xlsx")
        with open(plain, "rb") as fi, open(enc, "wb") as fo:
            OOXMLFile(fi).encrypt("pw-1", fo)
        self.assertEqual(srcmod.sniff(read_bytes(enc)), "encrypted")
        out = os.path.join(self.td, "e_v.xlsx")
        pf.decrypt_xlsx(enc, out, "pw-1")
        self.assertEqual(srcmod.sniff(read_bytes(out)), "xlsx")
        bad = os.path.join(self.td, "bad_v.xlsx")
        with self.assertRaises(Exception):
            pf.decrypt_xlsx(enc, bad, "nope")
        self.assertFalse(os.path.exists(bad))

    def drop(self, name, data, mtime=None):
        p = os.path.join(self.w.drop, name)
        with open(p, "wb") as f:
            f.write(data)
        if mtime:
            os.utime(p, (mtime, mtime))
        return p

    def test_drop_folder_rules(self):
        """안 C(--mail-only): 두 패턴 · 시각 규칙(이름 16자리 · 머리 요청일시 ≥ 지난 수거 시각) · 우선순위(복호화 가능이면 원본만) ·
        같은 폴더 md5 중복 · '~$' · '(1)' · 지난 첨부만 있으면 받지 못함 · 지정 폴더는 읽기만."""
        before = snapshot(self.w.drop)
        self.drop("카카오뱅크_거래내역_N1234567890_2026102714000012.xlsx",
                  fake_encrypt(self.kb(requested=dt.datetime(2026, 10, 26, 14)), "FAKEXLSXPW-91z"))   # 실행일−1일 0시보다 앞
        rc, out = self.w.run("--run", "--mail-only")
        self.assertEqual(rc, 2, out)
        self.assertEqual(os.listdir(self.w.inbox), [])
        self.drop("카카오뱅크_거래내역_N1234567890_2026102814550012 (1).xlsx",
                  fake_encrypt(self.kb(requested=NOW - dt.timedelta(minutes=5)), "FAKEXLSXPW-91z"))
        self.drop("~$카카오뱅크_거래내역_N1234567890_2026102815010013.xlsx", b"lock")
        self.drop("카카오뱅크_거래내역_v.xlsx", self.kb())                    # 손으로 푼 것 — 복호화가 켜져 있어 무시
        rc, out = self.w.run("--run", "--mail-only")
        self.assertEqual(rc, 0, out)
        k = self.w.summary()["items"]["카카오뱅크"]
        self.assertEqual(k["status"], "받음")
        self.assertTrue(any("무시" in x for x in k["skipped"]))
        self.assertEqual(len(os.listdir(self.w.inbox)), 1)
        # 다시 돌리면 지난 성공 수거 시각보다 이른 첨부라 받지 못함(옛 첨부 혼입 0)
        rc, out = self.w.run("--run", "--mail-only", hooks={"now": lambda: NOW + dt.timedelta(hours=1)})
        self.assertEqual(rc, 2, out)
        self.assertEqual(len(os.listdir(self.w.inbox)), 1)
        # 복호화가 꺼지면 _v만 후보(원본은 '풀지 않은 원본'으로 보고)
        w2 = World(self)
        p = os.path.join(w2.drop, "카카오뱅크_거래내역_vv.xlsx")
        with open(p, "wb") as f:
            f.write(self.kb())
        with open(os.path.join(w2.drop, "카카오뱅크_거래내역_N1234567890_2026102815010012.xlsx"), "wb") as f:
            f.write(fake_encrypt(self.kb(), "FAKEXLSXPW-91z"))
        with mock.patch.object(pf, "have_msoffcrypto", lambda: False):
            rc, out = w2.run("--run", "--mail-only", hooks={"decrypt": None})
        self.assertEqual(rc, 0, out)
        k = w2.summary()["items"]["카카오뱅크"]
        self.assertTrue(any("풀지 않은 원본" in x for x in k["skipped"]))
        self.assertEqual(snapshot(self.w.drop)[:0], before[:0])
        self.assertTrue(os.path.exists(os.path.join(self.w.drop, "카카오뱅크_거래내역_v.xlsx")))  # 지정 폴더는 읽기만

    def test_drop_folder_inside_inbox_exit1(self):
        with open(self.w.config, encoding="utf-8") as f:
            text = f.read()
        h = self.w.home.replace("\\", "/")
        with open(self.w.config, "w", encoding="utf-8") as f:
            f.write(text.replace(f'folder = "{h}/uploads/받은메일"', f'folder = "{h}/uploads/내역/받은메일"'))
        rc, out = self.w.run("--run", "--mail-only")
        self.assertEqual(rc, 1)
        self.assertIn("mail_drop.folder", out)


# ================================================================ 8. ③ 확정·죽음 안전망 — 완료 기준 8·9
class CommitTests(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def staged_run(self, hooks=None):
        """지정 폴더 카뱅 하나로 ③까지(브라우저 없이 확정 경로를 끝까지)."""
        d = os.path.join(self.w.drop, "카카오뱅크_거래내역_N1234567890_2026102815010012.xlsx")
        if not os.path.exists(d):
            p = os.path.join(self.w.home, "kb.xlsx")
            kb_xlsx(p, KB_ROWS, dt.date(2026, 10, 1), TODAY, NOW + dt.timedelta(minutes=1))
            with open(d, "wb") as f:
                f.write(fake_encrypt(read_bytes(p), "FAKEXLSXPW-91z"))
        return self.w.run("--run", "--mail-only", hooks=hooks)

    def test_existing_folder_never_reused(self):
        os.makedirs(os.path.join(self.w.inbox, "20261028"))
        with open(os.path.join(self.w.inbox, "20261028", "keep.txt"), "wb") as f:
            f.write(b"keep")
        before = snapshot(os.path.join(self.w.inbox, "20261028"))
        rc, out = self.staged_run()
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(os.listdir(self.w.inbox)), ["20261028", "20261028_2"])
        self.assertEqual(snapshot(os.path.join(self.w.inbox, "20261028")), before)
        self.assertEqual(os.listdir(os.path.join(self.w.inbox, "20261028_2")),
                         ["카카오뱅크_거래내역_N1234567890_2026102815010012_v.xlsx"])
        self.assertFalse([n for _d, _s, fs in os.walk(self.w.inbox) for n in fs if n.endswith(".json")])

    def test_death_during_copy_and_before_rename(self):
        """복사 도중 사망·이름 바꾸기 직전 사망 → inbox 변화 0 · 다시 돌리면 새 회차가 완전한 새 폴더 하나."""
        before = snapshot(self.w.inbox)

        def die(_p):
            raise KeyboardInterrupt
        for hk in ({"copy_hook": die}, {"before_rename": die}):
            with self.subTest(list(hk)):
                with self.assertRaises(KeyboardInterrupt):
                    self.staged_run(hooks=hk)
                self.assertEqual(snapshot(self.w.inbox), before)
        rc, out = self.staged_run()
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.w.run_dirs()), 3)
        new = os.listdir(self.w.inbox)
        self.assertEqual(new, ["20261028"])
        self.assertEqual(len(os.listdir(os.path.join(self.w.inbox, "20261028"))), 1)

    def test_same_md5_already_there(self):
        rc, out = self.staged_run()
        self.assertEqual(rc, 0, out)
        rc, out = self.staged_run(hooks={"now": lambda: NOW + dt.timedelta(minutes=5)})
        k = self.w.summary()["items"]["카카오뱅크"]
        self.assertIn(k["status"], ("이미 있음", "받지 못함"))
        self.assertEqual(len(os.listdir(self.w.inbox)), 1)


# ================================================================ 9. dry-run — 완료 기준 1·13
class DryRunTests(unittest.TestCase):
    def test_no_browser_no_mail_no_change(self):
        w = World(self)
        w.put("20261027", "s.xls", lambda q: shb_xls(q, SHB_ROWS[:2], dt.date(2026, 10, 1), dt.date(2026, 10, 18)))
        w.ledger([("신한카드", "2026-10-01", "2026-10-21")])
        before = snapshot(w.home)

        def boom(*a, **k):
            raise AssertionError("dry-run이 브라우저를 불렀다")
        with mock.patch.object(pf.imaplib, "IMAP4_SSL", boom):
            rc, out = w.run(hooks={"sync_playwright": boom})
        self.assertEqual(rc, 0, out)
        self.assertEqual(snapshot(w.home), before)
        self.assertIn("| 신한은행 | 웹(로그인은 사용자) | 2026-10-01 |", out)
        self.assertIn("자격 증명 household/naver-imap 있음", out)
        for sec in SECRET_STRINGS:
            self.assertNotIn(sec, out)
        rc, out = w.run(hooks={"cred_read": lambda t: None})
        self.assertIn("cmdkey /generic:household/naver-imap /user:<네이버 아이디> /pass", out)
        self.assertIn("Claude 앱 밖", out)
        self.assertNotIn("/pass:", out)

    def test_docs_have_fixed_cmdkey_text_and_no_inline_pass(self):
        """안내문(code-tab·SKILL·CLAUDE·예시 설정)에 `/pass:` 꼴 0 · 'Claude 앱 밖 창 · 값 없이 · 대신 치지도 묻지도 · 터미널 판' 문구."""
        for rel in ("pc/code-tab.md", "pc/q-household-run/SKILL.md", "pc/CLAUDE.md", "pc/import/받기.toml.example"):
            text = read_bytes(os.path.join(ROOT, rel)).decode("utf-8")
            self.assertNotIn("/pass:", text, rel)
            if rel != "pc/import/받기.toml.example":
                self.assertIn("Claude 앱 밖", text, rel)
                self.assertIn("대신 치지도 묻지도", text, rel)
                self.assertIn("터미널 판", text, rel)
        skill = read_bytes(os.path.join(ROOT, "pc/q-household-run/SKILL.md")).decode("utf-8")
        self.assertIn("비밀번호(카카오뱅크 암호 엑셀 포함)를 묻거나 받지 않는다", skill)
        self.assertIn("_v.xlsx`만", skill)


# ================================================================ 10. scan 쪽 1줄 · 공개 노출 대조 — 완료 기준 15·17
class ScanSideTests(unittest.TestCase):
    def test_folder_key_n_is_seq_within_date(self):
        names = ["20261027_2/a", "20261028/a", "20261027/a", "20261027_10/a", "20261027_3/a", "2026-10-26/z", "zz/x"]
        self.assertEqual(sorted(names, key=pc_import.folder_key),
                         ["2026-10-26/z", "20261027/a", "20261027_2/a", "20261027_3/a", "20261027_10/a", "20261028/a", "zz/x"])

    def test_receive_toml_values_in_exposure_check(self):
        """pc_run 공개 노출 대조가 rules/받기.toml의 식별 값 칸·긴 숫자를 실값으로 든다 — 선택자·안내 글자는 아님."""
        import pc_run
        home = tempfile.mkdtemp(prefix="pcexpo-")
        self.addCleanup(rmtree, home)
        os.makedirs(os.path.join(home, "rules"))
        os.makedirs(os.path.join(home, "uploads"))
        text = read_bytes(EXAMPLE).decode("utf-8").replace(
            'extra_identity = ["<체크카드 이용카드 값 예: 본인000*>"]', 'extra_identity = ["본인777*", "계좌 31234567890"]')
        with open(os.path.join(home, "rules", "받기.toml"), "w", encoding="utf-8") as f:
            f.write(text)
        v = pc_run.real_values(pc_run.Ctx(ROOT, home, False), None)
        self.assertTrue(pc_run.blob_hits('X = ["본인777*"]'.encode(), v))
        self.assertTrue(pc_run.blob_hits(b"n = 31234567890", v))
        for line in ('value = "#btn_excel"', 'expect = ["엑셀 저장"]', "host = imap.naver.com"):
            self.assertEqual(pc_run.blob_hits(line.encode(), v), [], line)


# ================================================================ 11. 대조(리허설 도구)
class CompareTests(unittest.TestCase):
    def test_same_and_different(self):
        w = World(self)
        a, b = os.path.join(w.home, "a"), os.path.join(w.home, "b")
        os.makedirs(a)
        os.makedirs(b)
        shb_xls(os.path.join(a, "s.xls"), SHB_ROWS, dt.date(2026, 10, 1), dt.date(2026, 10, 28))
        shb_xls(os.path.join(b, "s.xls"), SHB_ROWS, dt.date(2026, 10, 1), dt.date(2026, 10, 27))
        rc, out = w.run("compare", "--a", a, "--b", b)
        self.assertEqual(rc, 0, out)
        self.assertIn("| 신한은행 | 2026-10-01~2026-10-26 | 4/4 |", out)
        for amt in ("2,000,013", "2000013", "300,013"):
            self.assertNotIn(amt, out)
        os.remove(os.path.join(b, "s.xls"))
        shb_xls(os.path.join(b, "s2.xls"), SHB_ROWS[:3], dt.date(2026, 10, 1), dt.date(2026, 10, 27))
        rc, out = w.run("compare", "--a", a, "--b", b, "--to", "2026-10-27")
        self.assertEqual(rc, 2, out)


# ================================================================ 12. 가짜 사이트 끝까지(브라우저) — 완료 기준 2·3·5·7·9·10·11
SHC_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><title>__TITLE__</title></head><body>
<div id="hdr"></div>
<div id="screen" style="display:none">
 <h2>카드 이용내역</h2><div id="per">2026.09.28 ~ 10.28</div><div id="cnt">총 22건</div>
 <button type="button" id="popUsagefilterView" aria-label="필터 선택"></button>
 <div id="layer" style="display:none">
  <input type="radio" name="oriType" id="oriType_01" checked><label for="oriType_01">승인</label>
  <input type="radio" name="oriType" id="oriType_02"><label for="oriType_02">결제확정</label>
  <button type="button" id="cardType-custom" aria-label="카드구분">전체</button>
  <button type="button" aria-label="카드 선택">__CARDSEL__</button>
  <input type="radio" name="areaSelect" id="areaSelect_01" checked><label for="areaSelect_01">전체</label>
  <input type="radio" name="areaSelect" id="areaSelect_02"><label for="areaSelect_02">국내</label>
  <input type="radio" name="useType" id="useType_01" checked><label for="useType_01">전체</label>
  <input type="radio" name="useType" id="useType_02"><label for="useType_02">일시불</label>
  <input type="radio" name="conditionPeriod" id="conditionPeriod02" checked><label for="conditionPeriod02"><span>1개월</span></label>
  <input type="radio" name="conditionPeriod" id="conditionPeriod05"><label for="conditionPeriod05"><span>직접 선택</span></label>
  <input type="text" id="startDate" readonly value="2026.09.28" aria-label="시작일">
  <input type="text" id="endDate" readonly value="2026.10.28" aria-label="종료일">
  <button type="button" id="btnQuery"><span>조회</span></button>
 </div>
 __EXCEL__
</div>
<script>
const S = __SCN__, PAGE = "__PAGE__";
const ev = (t, d) => navigator.sendBeacon("/__ev", JSON.stringify({t, d}));
document.addEventListener("click", e => ev("click", e.target.id || (e.target.textContent || "").trim().slice(0, 20)), true);
document.addEventListener("keydown", e => ev("key", (document.activeElement || {}).id || ""), true);
const li = () => document.cookie.includes("li=1");
let left = S.timer || 600;
function hdr() {
  const h = document.getElementById("hdr");
  if (!li()) { h.innerHTML = "<span>로그인해 주세요</span>"; return; }
  h.innerHTML = '<span>가짜님</span> <button type="button" class="btn-logout">로그아웃</button> '
    + '<button type="button" class="btn-login-status" aria-label="로그인 정보보기"></button>'
    + '<div id="tip" style="display:none"><button type="button" id="extendLogoutBtn">연장하기</button></div>';
  const b = h.querySelector(".btn-login-status");
  const show = () => { b.textContent = String(Math.floor(left / 60)).padStart(2, "0") + ":" + String(left % 60).padStart(2, "0"); };
  show(); setInterval(() => { left = Math.max(0, left - 1); show(); }, 1000);
  b.onclick = () => { document.getElementById("tip").style.display = "block"; };
  document.getElementById("extendLogoutBtn").onclick = () => { if (!S.extend_broken) left = 600; show(); };
}
if (PAGE === "main") {
  if (li()) document.title = "신한카드";
  else if (!S.login_never) setTimeout(() => { document.cookie = "li=1; path=/"; document.title = "신한카드"; hdr(); }, S.login_ms || 500);
  hdr();
} else {
  hdr();
  document.title = "카드이용내역 | 마이 | 신한카드";
  document.getElementById("screen").style.display = "block";
  document.getElementById("popUsagefilterView").onclick = () => { document.getElementById("layer").style.display = "block"; };
  document.getElementById("btnQuery").onclick = async () => {
    const sd = startDate.value.replace(/\./g, ""), ed = endDate.value.replace(/\./g, "");
    const mb = {oriType: "PAY", encCrdN: S.card_sel ? "ENC1" : "", qyTcd: "0", fmlOCrdC: "0", dmsTsF: S.dmsTsF || "0", crdTcd: "A", qyStd: sd, qyEdd: ed,
      qySeq: "1", monthType: document.getElementById("conditionPeriod05").checked ? "4" : "1", crdNm: "전체", nextKey: ""};
    const r = await fetch("/mob/MOBFM045N/MOBFM045R0106.ajax", {method: "POST", headers: {"Content-Type": "application/x-www-form-urlencoded"},
      body: "mbw_json=" + encodeURIComponent(JSON.stringify(mb))});
    const j = await r.json();
    document.getElementById("per").textContent = startDate.value + " ~ " + endDate.value.slice(5);
    document.getElementById("cnt").textContent = "총 " + (S.screen_count ?? j.mbw_json.totalCnt) + "건";
    document.getElementById("layer").style.display = "none";
    if (S.cover) document.body.insertAdjacentHTML("beforeend",
      '<div id="cover" style="position:fixed;left:0;top:0;width:100%;height:100%;z-index:99">안내</div>');
  };
  for (const b of document.querySelectorAll(".xl")) b.onclick = () => {
    if (S.excel_dialog) alert("세션 확인");
    if (S.excel_popup) window.open("/popup", "_blank");
    const f = document.createElement("form"); f.method = "POST"; f.action = "/mob/MOBFM045N/MOBFM045R0101Excel.ajax";
    document.body.appendChild(f); f.submit();
    if (S.logout_after_excel) { document.cookie = "li=; path=/; max-age=0"; document.getElementById("hdr").innerHTML = "<span>로그인해 주세요</span>"; }
  };
}
</script></body></html>"""

HYC_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><title>__TITLE__</title></head><body>
<div id="hdr"></div>
<div id="screen" style="display:none">
 <div>최근 이용 내역</div><div id="per">9월 28일 ~ 10월 28일</div><a href="#none">보유카드 전체</a>
 <input type="radio" name="listClsf" id="listClsf_01" checked><label for="listClsf_01">실시간 승인</label>
 <input type="radio" name="listClsf" id="listClsf_02"><label for="listClsf_02">결제 확정</label>
 <input type="radio" name="zoneClsf" id="zoneClsf_01" __ZONE__><label for="zoneClsf_01">전체</label>
 <input type="radio" name="zoneClsf" id="zoneClsf_02"><label for="zoneClsf_02">국내</label>
 <input type="radio" name="useClsf" id="useClsf_01" checked><label for="useClsf_01">전체</label>
 <input type="radio" name="usplClsf" id="usplClsf_01" checked><label for="usplClsf_01">전체</label>
 <input type="radio" name="dtClsf" id="dtClsf_02" checked><label for="dtClsf_02"><strong>1개월</strong></label>
 <input type="radio" name="dtClsf" id="dtClsf_04"><label for="dtClsf_04"><strong>직접입력</strong></label>
 <input type="text" id="srtDt" readonly value="2026. 9. 28"> ~ <input type="text" id="endDt" readonly value="2026. 10. 28">
 <form id="form1" method="post"><input type="hidden" id="iqrySrtDt" name="srtDt" value="20260928"><input type="hidden" id="iqryEndDt" name="endDt" value="20261028"></form>
 <button type="button" id="goFilter">필터 적용</button>
 <div id="cnt">총 9건</div>
 <a href="javascript:excelAction();"><span class="blind">xls 파일 다운받기</span></a>
</div>
<script>
const S = __SCN__, PAGE = "__PAGE__";
const ev = (t, d) => navigator.sendBeacon("/__ev", JSON.stringify({t, d}));
document.addEventListener("click", e => ev("click", e.target.id || e.target.getAttribute("for") || (e.target.textContent || "").trim().slice(0, 20)), true);
const li = () => document.cookie.includes("li=1");
function hdr() {
  document.getElementById("hdr").innerHTML = li() ? '<a href="#n">가짜</a> <a href="/cpm/mb/CPMMB0101_04.hc">로그아웃</a>' : "<a href='#l'>로그인</a>";
}
function excelAction() { const f = document.getElementById("form1"); f.action = "/cpa/cb/CPACB0101_105.hc"; f.submit(); }
if (PAGE === "main") {
  if (li()) document.title = "Account-현대카드";
  else if (!S.login_never) setTimeout(() => { document.cookie = "li=1; path=/"; document.title = "Account-현대카드"; hdr(); }, S.login_ms || 500);
  hdr();
} else {
  hdr();
  document.title = S.wrong_title ? "보안프로그램 설치 안내-현대카드" : "최근 이용 내역-현대카드";  // 실물 제목의 공백은 U+00A0
  document.getElementById("screen").style.display = "block";
  const md = s => { const m = s.match(/(\d{4})(\d{2})(\d{2})/); return (+m[2]) + "월 " + (+m[3]) + "일"; };
  document.getElementById("goFilter").onclick = async () => {
    const p = new URLSearchParams({dmfrClsf: "", sortType: "1", crno: "", listClsf: "0", dtClsf: "9", srtDt: iqrySrtDt.value,
      endDt: iqryEndDt.value, zoneClsf: document.getElementById("zoneClsf_01").checked ? "9" : "1", useClsf: "9", usplClsf: "9"});
    const r = await fetch("/cpa/cb/apiCPACB0101_21.hc", {method: "POST", body: p});
    const j = await r.json();
    document.getElementById("per").textContent = md(iqrySrtDt.value) + " ~ " + md(iqryEndDt.value);
    document.getElementById("cnt").textContent = "총 " + j.bdy.rcntSummaryInfo.totUseCnt + "건";
  };
}
</script></body></html>"""

SHB_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><title>신한은행 개인뱅킹</title></head><body>
<div id="hdr"></div>
<div id="menu" style="display:none"><a href="#m" id="wq_uuid_11" aria-label="하위메뉴 펼치기">계좌조회</a>
 <div id="sub" style="display:none"><a href="#h" id="wq_uuid_12">거래내역조회</a></div></div>
<div id="screen" style="display:none">
 <select id="sbx_accno_input_0"><option>110-555-666666 [가짜 주거래 통장]</option><option>110-777-888888 [다른 통장]</option></select>
 <input type="text" id="wfr_searchCalendar_ica_fr_input" value="2026.10.28" aria-label="조회시작일"> ~
 <input type="text" id="wfr_searchCalendar_ica_to_input" value="2026.10.28" aria-label="조회종료일">
 <input type="radio" name="g" id="rad_InquiryGbn_input_0" checked><label for="rad_InquiryGbn_input_0">전체내역</label>
 <a href="#q" id="btn_inquiry">조회</a> <a href="#t" id="btn_inq이체버튼">이체</a>
 <select id="wfr_grd_inq_btngrp_div_sbx_viewRowCnt_input_0"><option>5개 보기</option><option selected>10개 보기</option><option>50개 보기</option><option>전체 보기</option></select>
 <a href="#f" id="wfr_grd_inq_btngrp_div_btn_downFile">파일저장</a>
 <div id="res">조회결과최근거래내역[총10건]</div><div id="sums"></div><div id="pop"></div>
</div>
<script>
const S = __SCN__;
const ev = (t, d) => navigator.sendBeacon("/__ev", JSON.stringify({t, d}));
document.addEventListener("click", e => ev("click", e.target.id || (e.target.textContent || "").trim().slice(0, 20)), true);
document.addEventListener("keydown", e => ev("key", (document.activeElement || {}).id || ""), true);
// 실물(WebSquare)처럼 evaluate의 객체·배열 반환이 깨지는 페이지 — 시험은 Playwright evaluate도 None으로 바꿔 끼운다
let left = S.timer || 600;
function login() {
  document.getElementById("hdr").innerHTML = '<span>가짜님</span> <span id="tm"></span><a href="#e" id="wq_uuid_3">연장</a> <a href="#o" id="btn_logout">로그아웃</a>';
  const tm = document.getElementById("tm");
  const show = () => { tm.textContent = String(Math.floor(left / 60)).padStart(2, "0") + ":" + String(left % 60).padStart(2, "0"); };
  show(); setInterval(() => { left = Math.max(0, left - 1); show(); }, 1000);
  document.getElementById("wq_uuid_3").onclick = () => { if (!S.extend_broken) left = 600; show(); };
  document.getElementById("menu").style.display = "block";
}
if (!S.login_never) setTimeout(login, S.login_ms || 500);
document.getElementById("wq_uuid_11").onclick = () => { document.getElementById("sub").style.display = "block"; };
document.getElementById("wq_uuid_12").onclick = () => { document.title = "거래내역조회 | 신한은행 개인뱅킹"; document.getElementById("screen").style.display = "block"; };
for (const id of ["wfr_searchCalendar_ica_fr_input", "wfr_searchCalendar_ica_to_input"]) {
  const e = document.getElementById(id);
  e.addEventListener("blur", () => { const m = e.value.match(/^(\d{4})(\d{2})(\d{2})$/); if (m) e.value = m[1] + "." + m[2] + "." + m[3]; });
}
document.getElementById("btn_inquiry").onclick = async () => {
  await fetch("/serviceEndpoint/httpDigital", {method: "POST", body: JSON.stringify({dataHeader: {trxCd: "NOISE01"}, dataBody: "x"})});
  const per = btoa(JSON.stringify({fr: wfr_searchCalendar_ica_fr_input.value, to: wfr_searchCalendar_ica_to_input.value}));
  const r = await fetch("/serviceEndpoint/httpDigital", {method: "POST", body: JSON.stringify({dataHeader: {trxCd: "RSRDE0108A01", encG: "1"}, dataBody: per})});
  const j = await r.json(), b = j.dataBody;
  document.getElementById("res").textContent = "조회결과" + b["조회시작일_display"] + " ~ " + b["조회종료일_display"] + "[총" + b["반복횟수"] + "건]";
  document.getElementById("sums").textContent = "입금합계 (건수) 1,000원(" + b.n_in + "건) 출금합계 (건수) 2,000원(" + b.n_out + "건)";
};
document.getElementById("wfr_grd_inq_btngrp_div_btn_downFile").onclick = () => {
  document.getElementById("pop").innerHTML = '<iframe src="/comjsp/popup.jsp" width="600" height="300"></iframe>';
};
</script></body></html>"""

SHB_POPUP = r"""<!doctype html><html><head><meta charset="utf-8"></head><body>
<div style="position:relative"><input type="checkbox" id="cbx_columnAll_input_0"><label for="cbx_columnAll_input_0"
 style="position:absolute;left:0;top:0;width:60px;height:20px;background:#fff">전체</label></div>
__COLS__
<a href="#s" id="btn_saveXls">엑셀저장</a> <a href="#t" id="btn_saveTxt">텍스트 저장</a>
<form id="xf" method="post" action="/websquare/xmlToExcel2.wq"></form>
<script>
const ev = (t, d) => navigator.sendBeacon("/__ev", JSON.stringify({t, d}));
document.addEventListener("click", e => ev("click", e.target.id || (e.target.textContent || "").trim().slice(0, 20)), true);
const all = document.getElementById("cbx_columnAll_input_0");
all.addEventListener("click", () => { for (let i = 0; i < 8; i++) document.getElementById("cbx_downColumn_input_" + i).checked = all.checked; });
document.getElementById("btn_saveXls").onclick = () => { document.getElementById("xf").submit(); };
</script></body></html>"""


def shc_excel_html(scn):
    if scn.get("two_excel"):
        return '<button type="button" class="xl" id="btn_excel">엑셀 저장</button><button type="button" class="xl" id="btn_excel">엑셀 저장</button>'
    return f'<button type="button" class="xl" id="btn_excel">{scn.get("excel_label", "엑셀 저장")}</button>'


class FakeBanks:
    """Playwright route로 가짜 금융사 3곳(네트워크 0). 조회 응답·내려받기 파일은 위 합성 행에서 그때그때 만든다."""

    def __init__(self, td, scn=None):
        self.td, self.scn = td, {"shc": {}, "hyc": {}, "shb": {}}
        for k, v in (scn or {}).items():
            self.scn[k].update(v)
        self.events, self.posts, self.downloads = [], [], []
        self.period = {}

    def install(self, ctx):
        ctx.route("https://fake-shc.test/**", self.shc)
        ctx.route("https://fake-hyc.test/**", self.hyc)
        ctx.route("https://fake-shb.test/**", self.shb)

    def common(self, route, u):
        if u.path == "/__ev":
            self.events.append(json.loads(route.request.post_data or "{}"))
            route.fulfill(status=204, body="")
            return True
        if u.path == "/popup":
            route.fulfill(status=200, content_type="text/html", body="<html><title>팝업</title></html>")
            return True
        return False

    def page(self, route, html, scn, page, title):
        body = (html.replace("__SCN__", json.dumps(scn)).replace("__PAGE__", page).replace("__TITLE__", title)
                .replace("__EXCEL__", shc_excel_html(scn)).replace("__CARDSEL__", scn.get("card_sel", "전체"))
                .replace("__ZONE__", "" if scn.get("zone_off") else "checked"))
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

    def file(self, route, name, data, ctype):
        self.downloads.append(name)
        route.fulfill(status=200, headers={"Content-Type": ctype,
                                           "Content-Disposition": f"attachment; filename*=UTF-8''{name}"}, body=data)

    def shc(self, route):
        u, scn = urlparse(route.request.url), self.scn["shc"]
        if self.common(route, u):
            return
        if u.path == "/":
            self.page(route, SHC_HTML, scn, "main", "로그인 | 신한카드")
        elif u.path == "/mob/MOBFM045N/MOBFM045R01.shc":
            self.page(route, SHC_HTML, scn, "screen", "로딩")
        elif u.path.endswith("R0106.ajax"):
            mb = json.loads(dict(parse_qsl(route.request.post_data or ""))["mbw_json"])
            self.posts.append(("shc", mb))
            s, e = (dt.datetime.strptime(mb[k], "%Y%m%d").date() for k in ("qyStd", "qyEdd"))
            self.period["shc"] = (s, e)
            rows = in_period(scn.get("rows", SHC_ROWS), s, e)
            j = {"mbw_json": {"totalCnt": str(len(rows)), "payList": [{"x": 1}] * min(20, len(rows)),
                              "nextKey": scn.get("next_key", "")}}
            for k in scn.get("drop", []):   # 응답 칸 빠짐 흉내(목록 길이·다음 쪽 키 못 읽음)
                j["mbw_json"].pop(k)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(j))
        elif u.path.endswith("R0101Excel.ajax"):
            self.posts.append(("shc_excel", {}))
            if scn.get("session_lost"):
                route.fulfill(status=200, content_type="application/json",
                              body='{"mbw_result":"F","mbw_message":"로그인이 필요한 서비스 입니다."}')
                return
            s, e = self.period["shc"]
            p = os.path.join(self.td, "shc.xlsx")
            rows = in_period(scn.get("rows", SHC_ROWS), s, e)
            if scn.get("excel_page"):        # 쪽 단위 엑셀 흉내 — 최신 N행만(꼬리 '총 N건'도 그 쪽)
                rows = sorted(rows, reverse=True)[:scn["excel_page"]]
            shc_xlsx(p, rows, tail=scn.get("tail"))
            self.file(route, f"Shinhancard_{e:%Y%m%d}.xlsx", read_bytes(p), "application/octet-stream")
        else:
            route.fulfill(status=404, body="")

    def hyc(self, route):
        u, scn = urlparse(route.request.url), self.scn["hyc"]
        if self.common(route, u):
            return
        if u.path == "/":
            self.page(route, HYC_HTML, scn, "main", "로그인-간편번호-현대카드")
        elif u.path == "/cpa/cb/CPACB0101_01.hc":
            self.page(route, HYC_HTML, scn, "screen", "로딩")
        elif u.path.endswith("apiCPACB0101_21.hc"):
            f = dict(parse_qsl(route.request.post_data or "", keep_blank_values=True))
            self.posts.append(("hyc", f))
            s, e = (dt.datetime.strptime(f[k], "%Y%m%d").date() for k in ("srtDt", "endDt"))
            self.period["hyc"] = (s, e)
            rows = in_period(scn.get("rows", HYC_ROWS), s, e)
            j = {"bdy": {"nextKey": "", "rcntSummaryInfo": {"totUseCnt": str(len(rows)), "srtDt": f["srtDt"], "endDt": f["endDt"],
                                                            "crno": f["crno"], "zoneClsf": f["zoneClsf"], "useClsf": "9",
                                                            "usplClsf": "9"},
                         "rcntAvItm": [{"x": 1}] * len(rows)}}
            route.fulfill(status=200, content_type="application/json", body=json.dumps(j))
        elif u.path.endswith("CPACB0101_105.hc"):
            self.posts.append(("hyc_excel", {}))
            s, e = self.period["hyc"]
            self.file(route, f"hyundaicard_{e:%Y%m%d}.xls",
                      hyc_html(in_period(scn.get("rows", HYC_ROWS), s, e), tail=scn.get("tail")), "application/vnd.ms-excel")
        else:
            route.fulfill(status=404, body="")

    def shb(self, route):
        u, scn = urlparse(route.request.url), self.scn["shb"]
        if self.common(route, u):
            return
        if u.path in ("/", "/index.jsp"):
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=SHB_HTML.replace("__SCN__", json.dumps(scn)))
        elif u.path == "/comjsp/popup.jsp":
            cols = "".join(f'<input type="checkbox" id="cbx_downColumn_input_{i}"><label>열{i}</label>' for i in range(8))
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=SHB_POPUP.replace("__COLS__", cols))
        elif u.path == "/serviceEndpoint/httpDigital":
            j = json.loads(route.request.post_data or "{}")
            if j["dataHeader"]["trxCd"] != "RSRDE0108A01":
                route.fulfill(status=200, content_type="application/json", body='{"dataBody": {"반복횟수": 99}}')
                return
            per = json.loads(base64.b64decode(j["dataBody"]))
            s, e = (dt.datetime.strptime(per[k], "%Y.%m.%d").date() for k in ("fr", "to"))
            self.period["shb"] = (s, e)
            rows = in_period(scn.get("rows", SHB_ROWS), s, e)
            self.posts.append(("shb", {"n": len(rows)}))
            b = {"조회시작일_display": per["fr"], "조회종료일_display": per["to"], "반복횟수": len(rows),
                 "n_in": sum(1 for r in rows if r[4]), "n_out": sum(1 for r in rows if r[3]), "R_RIBD1110_1": [{"x": 1}] * len(rows)}
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"dataBody": b}, ensure_ascii=False))
        elif u.path == "/websquare/xmlToExcel2.wq":
            self.posts.append(("shb_excel", {}))
            s, e = self.period["shb"]
            p = os.path.join(self.td, "shb.xls")
            shb_xls(p, in_period(scn.get("rows", SHB_ROWS), s, e), s, e)
            self.file(route, "%EC%8B%A0%ED%95%9C%EC%9D%80%ED%96%89_%EA%B1%B0%EB%9E%98%EB%82%B4%EC%97%AD%EC%A1%B0%ED%9A%8C_20261028150000.xls",
                      read_bytes(p), "application/vnd.ms-excel")
        else:
            route.fulfill(status=404, body="")


def _none_for_objects(orig):
    """신한은행 실물(WebSquare) 흉내 — evaluate의 객체·배열 반환은 None, 목록·사전 인자는 예외(2026-10-28 리허설 1 실측)."""
    def w(self, *a, **k):
        if any(isinstance(x, (list, dict)) for x in a[1:]):
            raise RuntimeError("evaluate 객체 인자가 깨진 페이지")
        r = orig(self, *a, **k)
        return r if r is None or isinstance(r, (str, int, float, bool)) else None
    return w


ALLOWED_CLICKS = {"popUsagefilterView", "직접 선택", "조회", "btn_excel", "btnQuery", "extendLogoutBtn",
                  "dtClsf_04", "직접입력", "goFilter", "xls 파일 다운받기", "wq_uuid_11", "wq_uuid_12", "btn_inquiry",
                  "wfr_searchCalendar_ica_fr_input", "wfr_searchCalendar_ica_to_input", "wq_uuid_3",
                  "wfr_grd_inq_btngrp_div_btn_downFile", "cbx_columnAll_input_0", "btn_saveXls", "conditionPeriod05",
                  "wfr_grd_inq_btngrp_div_sbx_viewRowCnt_input_0"}


@unittest.skipUnless(os.environ.get("PC_FETCH_E2E", "1") != "0", "PC_FETCH_E2E=0")
class E2E(unittest.TestCase):
    def setUp(self):
        try:
            import playwright.sync_api._generated as g
        except ImportError:
            self.skipTest("playwright 없음")
        for cls in (g.Page, g.Frame, g.Locator):   # 신한은행 실측: evaluate의 객체·배열 반환이 None — 모든 사이트에 재현
            p = mock.patch.object(cls, "evaluate", _none_for_objects(cls.evaluate))
            p.start()
            self.addCleanup(p.stop)
        self.w = World(self)
        FakeIMAP.instances, FakeIMAP.fail_login = [], False
        kb = os.path.join(self.w.home, "kb.xlsx")
        kb_xlsx(kb, KB_ROWS, dt.date(2026, 10, 1), TODAY, NOW + dt.timedelta(minutes=1))
        FakeIMAP.messages = [(time.mktime((NOW + dt.timedelta(minutes=2)).timetuple()),
                              kakao_mail(fake_encrypt(read_bytes(kb), "FAKEXLSXPW-91z")))]

    def go(self, *argv, scn=None, hooks=None):
        self.fake = FakeBanks(self.w.home, scn)
        hk = {"headless": True, "on_context": self.fake.install}
        hk.update(hooks or {})
        cfg = pf.load_config(self.w.config)
        with mock.patch.object(pf.imaplib, "IMAP4_SSL", FakeIMAP), \
                mock.patch.dict(pf.__dict__, {"launch": self.launch_bundled(cfg)}):
            return self.w.run("--run", *argv, hooks=hk)

    @staticmethod
    def launch_bundled(_cfg):
        orig = pf.launch

        def launch(p, cfg, profile_dir, headless=False):
            return orig(p, dict(cfg, browser={"channel": None}), profile_dir, headless=headless)
        return launch

    def assert_safe(self, s):
        clicks = {e["d"] for e in self.fake.events if e.get("t") == "click"}
        self.assertTrue(clicks <= ALLOWED_CLICKS, clicks - ALLOWED_CLICKS)
        keys = {e["d"] for e in self.fake.events if e.get("t") == "key"}
        self.assertTrue(keys <= {"wfr_searchCalendar_ica_fr_input", "wfr_searchCalendar_ica_to_input"}, keys)
        self.assertFalse([d for d in s["dialogs"] if d["action"] not in ("none", "dismiss")])
        cfg = pf.load_config(self.w.config)
        acts = {(x["id"], a) for x in cfg["site"] for a in x["actions"]}
        self.assertTrue({(c["site"], c["action"]) for c in s["clicks"]} <= acts)

    def test_happy_three_sites_and_mail(self):
        """한 기동으로 3곳 + 카뱅 메일 → exit 0 · 새 폴더 하나에 내역 파일 4개 · 허용 클릭만 · dialog accept 0 · 비밀 노출 0 ·
        신한은행: 메뉴 2단계·iframe·체크박스(라벨 가림)·조회수 select·잡음 요청 · 모든 evaluate 반환 None 재현."""
        rc, out = self.go()
        self.assertEqual(rc, 0, out)
        s = self.w.summary()
        self.assertEqual({k: v["status"] for k, v in s["items"].items()},
                         {"신한카드": "받음", "현대카드": "받음", "신한은행": "받음", "카카오뱅크": "받음"})
        new = os.listdir(self.w.inbox)
        self.assertEqual(new, ["20261028"])
        files = sorted(os.listdir(os.path.join(self.w.inbox, "20261028")))
        self.assertEqual(len(files), 4, files)
        self.assertIn("Shinhancard_20261028.xlsx", files)
        self.assertIn("hyundaicard_20261028.xls", files)
        self.assertIn("신한은행_거래내역조회_20261028150000.xls", files)
        self.assert_safe(s)
        self.assertEqual([p[0] for p in self.fake.posts].count("shc_excel"), 1)
        blob = self.w.all_text(self.w.runs, self.w.inbox) + out.encode("utf-8")
        for sec in SECRET_STRINGS:                      # 자격 증명은 회차 폴더·inbox·출력 어디에도 0
            self.assertNotIn(sec.encode("utf-8"), blob, sec)
        said = out + json.dumps(s, ensure_ascii=False)   # 출력·summary에는 계좌·이름·응답 본문 0(내역 파일 안에만)
        for leak in ("110-555-666666", "110555666666", "가짜님", "가짜 주거래", "payList", "rcntAvItm", "R_RIBD1110_1",
                     "mbw_json", "가상식당", "12,013"):
            self.assertNotIn(leak, said, leak)

    def test_partial_failures_do_not_block_others(self):
        """현대카드 꼬리 건수 불일치 · 신한은행 임의 예외(AttributeError) → 그 둘만 받지 못함 · 신한카드·카뱅은 ③으로 확정 · exit 2."""
        def boom(site, step):
            if site == "신한은행" and step == "query":
                raise AttributeError("evaluate None")
        rc, out = self.go(scn={"hyc": {"tail": 9}}, hooks={"step_hook": boom})
        self.assertEqual(rc, 2, out)
        st = {k: v["status"] for k, v in self.w.summary()["items"].items()}
        self.assertEqual(st, {"신한카드": "받음", "현대카드": "받지 못함", "신한은행": "받지 못함", "카카오뱅크": "받음"})
        self.assertEqual(len(os.listdir(os.path.join(self.w.inbox, "20261028"))), 2)
        self.assertIn("[받지 못함] 현대카드, 신한은행", out)
        self.assertIn("_v.xlsx만", out)

    def one(self, scn=None, sid="신한카드", **kw):
        key = {"신한카드": "shc", "현대카드": "hyc", "신한은행": "shb"}[sid]
        rc, out = self.go("--sites", sid, "--no-mail", scn={key: scn or {}}, **kw)
        return rc, out, self.w.summary()["items"][sid]

    def test_guards_refuse(self):
        """금지 문구 버튼 · 보이는 엑셀 버튼 2개 · 다음 쪽 키 · 필터가 걸린 요청 · 카드 선택이 '전체' 아님 · 스크립트 단계 dialog ·
        새 창 · 세션 풀림(엑셀이 JSON) → 엑셀 누르지 않거나 받지 못함, 새 폴더 0."""
        cases = [({"excel_label": "엑셀 저장 신청"}, "금지 문구", False),
                 ({"two_excel": True}, "보이는 요소 2개", False),
                 ({"next_key": "K2"}, "다음 쪽 키", False),
                 ({"dmsTsF": "1"}, "dmsTsF", False),
                 ({"card_sel": "본인카드"}, "encCrdN", False),   # 카드 선택은 요청 본문이 지킨다
                 ({"excel_dialog": True}, "확인창", None),
                 ({"excel_popup": True}, "새 창", None),
                 ({"cover": True}, "누르지 못함", False),
                 ({"session_lost": True}, "내려받기가 시작되지 않음", True)]
        for scn, want, pressed in cases:
            with self.subTest(want):
                rc, out, it = self.one(scn)
                self.assertEqual(rc, 2, out)
                self.assertEqual(it["status"], "받지 못함")
                self.assertIn(want, it["error"])
                if pressed is not None:
                    self.assertEqual("shc_excel" in [p[0] for p in self.fake.posts], pressed)
                self.assertEqual(os.listdir(self.w.inbox), [])

    def test_next_key_count_over_list_takes_whole_excel(self):
        """(2026-10-29 1회차 P1) 다음 쪽 키 있음 ∧ 응답 건수 > 목록 길이 → 엑셀을 누르고 건수 넷 일치로 판정:
        (b) 쪽 단위 엑셀(파일 20 ≠ 응답 47) → 받지 못함('건수') · inbox 0 / 목록·다음 쪽 키 못 읽음 → 엑셀 안 누름 /
        (a) 전체 엑셀 47행 → 받음 · 새 폴더 · summary query 47/20/true."""
        rc, out, it = self.one({"rows": SHC_MANY, "next_key": "K2", "excel_page": 20})
        self.assertEqual((rc, it["status"]), (2, "받지 못함"), out)
        self.assertIn("건수", it["error"])
        self.assertIn("shc_excel", [p[0] for p in self.fake.posts])
        self.assertEqual(os.listdir(self.w.inbox), [])
        for drop in (["payList"], ["nextKey"]):
            with self.subTest(drop=drop):
                rc, out, it = self.one({"rows": SHC_MANY, "next_key": "K2", "drop": drop})
                self.assertEqual((rc, it["status"]), (2, "받지 못함"), out)
                self.assertIn("다음 쪽 키", it["error"])
                self.assertNotIn("shc_excel", [p[0] for p in self.fake.posts])
                self.assertEqual(os.listdir(self.w.inbox), [])
        rc, out, it = self.one({"rows": SHC_MANY, "next_key": "K2"})
        self.assertEqual((rc, it["status"]), (0, "받음"), out)
        self.assertEqual((it["query"]["count"], it["query"]["list_len"], it["query"]["next_key"]), (47, 20, True))
        self.assertIn("응답 건수 47 > 목록 20", out)
        self.assertEqual(os.listdir(self.w.inbox), ["20261028"])
        self.assertEqual(os.listdir(os.path.join(self.w.inbox, "20261028")), ["Shinhancard_20261028.xlsx"])
        self.assert_safe(self.w.summary())

    def test_logout_marker_gone_after_excel_still_received(self):
        """(1회차 P9) 엑셀 내려받기 직후 로그아웃 표식이 사라져도 검사를 통과한 파일은 받음."""
        rc, out, it = self.one({"logout_after_excel": True})
        self.assertEqual((rc, it["status"]), (0, "받음"), out)
        self.assertEqual(len(os.listdir(os.path.join(self.w.inbox, "20261028"))), 1)

    def test_login_never_and_dialog_record_only(self):
        rc, out, it = self.one({"login_never": True}, hooks={})
        self.assertEqual(rc, 2)
        self.assertIn("로그인 대기", it["error"])
        self.assertEqual([e for e in self.fake.events if e.get("t") in ("click", "key")], [])
        self.assertEqual(self.w.summary()["clicks"], [])
        logs = []
        dw = pf.DialogWatch("x", logs.append, [])

        class D:
            message, type, calls = "홍길동님 보안 확인", "confirm", []

            def accept(self):
                D.calls.append("accept")

            def dismiss(self):
                D.calls.append("dismiss")
        dw(D())
        self.assertEqual(D.calls, [])               # 로그인 대기 중: 호출 0
        self.assertNotIn("홍길동", " ".join(logs))
        dw.phase = "script"
        dw(D())
        self.assertEqual(D.calls, ["dismiss"])
        self.assertIsNotNone(dw.unknown)

    def test_timer_extend(self):
        rc, out, it = self.one({"timer": 30})
        self.assertEqual((rc, it["status"]), (0, "받음"), out)
        self.assertGreater(it["extended"]["after"], it["extended"]["before"])
        rc, out, it = self.one({"timer": 30, "extend_broken": True})
        self.assertEqual(it["status"], "받지 못함")
        self.assertIn("연장 뒤", it["error"])
        rc, out, it = self.one({"timer": 30}, sid="신한은행")
        self.assertEqual((rc, it["status"]), (0, "받음"), out)

    def test_try_extend_and_initial_states_record_only(self):
        """(리허설) --try-extend: 연장 1회 시도의 전·후 초만 기록하고 출처는 그대로 받음 · 연장이 안 늘어도 실패시키지 않음 ·
        화면 첫 필터 상태 기록 · --probe-paging: 다음 쪽 키가 있어도 받아 회차 폴더에만 두고 inbox 0."""
        rc, out = self.go("--sites", "신한카드", "--no-mail", "--try-extend", scn={"shc": {"timer": 500}})
        it = self.w.summary()["items"]["신한카드"]
        self.assertEqual((rc, it["status"]), (0, "받음"), out)
        self.assertGreater(it["try_extend"]["after"], it["try_extend"]["before"])
        self.assertEqual(it["initial_states"], {"#oriType_01": True, "#areaSelect_01": True, "#useType_01": True})
        rc, out = self.go("--sites", "신한카드", "--no-mail", "--try-extend", scn={"shc": {"timer": 500, "extend_broken": True}})
        it = self.w.summary()["items"]["신한카드"]
        self.assertEqual(it["status"], "받음", out)
        n0 = len(os.listdir(self.w.inbox))
        rc, out = self.go("--sites", "신한카드", "--no-mail", "--probe-paging", scn={"shc": {"next_key": "K2"}})
        it = self.w.summary()["items"]["신한카드"]
        self.assertEqual((rc, it["status"]), (2, "받지 못함"), out)
        self.assertIn("실측", it["error"])
        self.assertEqual(it["query"]["next_key_shape"], "2자 글자")
        self.assertEqual(len(os.listdir(self.w.inbox)), n0)
        self.assertTrue(os.path.exists(os.path.join(self.w.runs, self.w.run_dirs()[-1], "신한카드", "Shinhancard_20261028.xlsx")))

    def test_zero_rows_and_filter_radio(self):
        rc, out, it = self.one({"rows": []}, sid="현대카드")
        self.assertEqual((rc, it["status"]), (0, "0건"), out)
        self.assertNotIn("hyc_excel", [p[0] for p in self.fake.posts])
        self.assertEqual(os.listdir(self.w.inbox), [])
        rc, out, it = self.one({"zone_off": True}, sid="현대카드")
        self.assertEqual(it["status"], "받지 못함")
        self.assertIn("zoneClsf_01", it["error"])
        rc, out, it = self.one({"wrong_title": True}, sid="현대카드")   # 내역 화면이 아님 → 주소 경로만 기록
        self.assertEqual(it["status"], "받지 못함")
        self.assertIn("/cpa/cb/CPACB0101_01.hc", it["error"])
        self.assertFalse(it["screen_title_login"])


if __name__ == "__main__":
    unittest.main()
