"""
이 PC(Windows · Code 탭)에서 금융사 내역 파일 → 가계부 CSV 행 정리 (내역 자동화 설계안 A — 파일 넣기형).

웹(claude.ai)은 이 파일을 쓰지 않는다. 설계·완료 기준: 2026-10-26 내역 자동화 탐색 기준선(로컬 기록) 3-0 · 4절.
금융사 파일 형식(열·값 서명)·이체 적요·카드대금 적요는 clone 밖 rules/계좌.toml에 둔다(예시 pc/import/계좌.toml.example).
흐름 설명은 pc/code-tab.md "내역 정리".

사용법 (PY = D:\\household\\.venv\\Scripts\\python.exe, 경로는 / 로 적는다):
    $PY scripts/pc_import.py scan
        ② uploads/내역/의 모든 폴더를 다시 읽어 제안을 만든다. 쓰기는 새 회차 폴더(work/내역/<회차>)뿐
    $PY scripts/pc_import.py answer --run <회차> --item <번호> --set 처리=추가 [--set 대분류=…]
        ③ 대화로 받은 답을 그 회차 확인필요.xlsx 답 칸에 적는다(엑셀이 열려 있으면 멈춤)
    $PY scripts/pc_import.py apply --run <회차> [--skip <출처|파일>] [--ack-card <카드> <사유>]
                                   [--ack-balance <계좌> <사유>] [--ack-gap <출처> <사유>]
                                   [--ack-warning <종류> <사유>] [--confirm]
        ④ 기본은 dry-run(쓰기 0). --confirm이면 같은 프로세스에서 ②를 다시 계산해 stamp와 같을 때만
           배타 열기 → 백업 복사(md5) → 새 CSV 임시 파일 → 검사 → uploads/가계부_<회차>.csv → 옛 CSV를
           backup/<회차>/로 이동 → 분류규칙.csv → 원장 → apply.json 순서로 쓴다
    --rehearsal init : 리허설 놀이터(work-rehearsal/내역/)에 실 CSV·rules 사본을 만든다
    --rehearsal …    : 내역 파일은 uploads/내역/에서 읽기만 하고, CSV·rules·원장·회차 폴더는 놀이터에만 쓴다
    --home DIR       : 작업 루트(기본: clone의 부모 = D:\\household)

지키는 것(3-0 1~14): scan 쓰기 0 · 출처 서명 넷 · 매번 전체 재계산 + 시작일 · 원장 겹/CSV 겹 중복 방지 ·
옛 줄 바이트 그대로 · 규칙 없는 거래는 '확인 필요' · 잔액 대조 · 실데이터·자격 증명은 clone 밖.
"""
import argparse
import codecs
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from collections import defaultdict
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path

# 저장소 안에 __pycache__를 만들지 않는다(pc_run.py와 같은 이유).
sys.dont_write_bytecode = True
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
import import_sources as srcmod  # noqa: E402
import pipeline as pl  # noqa: E402
from import_sources import ConfigError, SourceError, Tx, normalize_merchant  # noqa: E402

RUN_NAME = re.compile(r"^(\d{8}-\d{6})(?:-(\d+))?$")
KST = dt.timezone(dt.timedelta(hours=9))
COLS = list(pl.EXPECTED_COLS)
RULE_COLS = ["키", "대분류", "소분류", "세부내용", "고정여부", "결제수단", "물음", "원문", "만든 날", "근거 회차"]
ACCOUNTS = sorted(set(pl.ACCOUNT_KEY.values()))
CARDS = sorted(pl.CARD_TARGETS)
LONG_NAME = {v: k for k, v in pl.ACCOUNT_KEY.items()}       # 생활비 → 생활비 통장 (ACCOUNTS_START 키)
NEAR_DAYS = 7                                               # 짝 의심 범위(±7일)

# 답 칸
ANSWER_COLS = ["처리", "구분", "대분류", "소분류", "세부내용", "고정여부", "결제수단", "비고",
               "할부 개월", "짝 CSV 행", "규칙", "새 이름", "금액(해외만)", "메모"]
INFO_COLS = ["번호", "항목", "종류", "사유", "출처", "날짜", "금액", "가맹점·적요", "후보 CSV 행"]

ROW_OPTS = ["추가", "제외", "보류"]
PAIR_OPTS = ["짝", "추가", "제외", "보류"]
KINDS = {
    # 종류: (사유, 처리 선택지, 규칙 저장 가능, 답 없으면 apply 막힘)
    "new_merchant": ("새 가맹점 — 규칙 없음", ROW_OPTS + ["할부"], True, False),
    "ask_every": ("'매번' 묻는 가맹점", ROW_OPTS + ["할부"], False, False),
    "platform": ("플랫폼 결제 — 항상 매번(규칙 저장 불가)", ROW_OPTS + ["할부"], False, False),
    "raw_diff": ("같은 규칙 키에 다른 원문 — '매번'으로 승격", ROW_OPTS + ["할부"], False, False),
    "empty_merchant": ("가맹점·내용이 비었다", ROW_OPTS, False, False),
    "income": ("입금 — 항목명(N빵·상환·환불 …)", ROW_OPTS, False, False),
    "foreign": ("해외 결제 — 원화 청구 확정액?", ROW_OPTS, False, False),
    "dup_weak": ("같은 날 같은 금액 n건 — 실제 n건?", ROW_OPTS, True, False),
    "memo_conflict": ("적요가 카드대금·이체 패턴 둘 이상에 걸림", ROW_OPTS, False, False),
    "transfer_unknown": ("이체 상대 모름(본인 이름 등 둘 이상 계좌에 걸림)", ROW_OPTS, False, False),
    "transfer_noin": ("이체 — 입금 쪽 내역 없음(상대 파일이 그 날짜를 덮는데)", ROW_OPTS, False, False),
    "transfer_noout": ("입금 — 출금 계좌?(파일이 있는 계좌에서 온 이체)", ROW_OPTS, False, False),
    "transfer_conflict": ("이체 짝 충돌(기다리는 이체와 상대·금액이 어긋남)", ROW_OPTS, False, False),
    "pair_gap": ("손 행과 짝 — 날짜 차", PAIR_OPTS, True, False),
    "pair_amb": ("손 행 짝 모호(같은 경로·금액 묶음 건수가 다름)", PAIR_OPTS, True, False),
    "pair_suspect": ("손 행 짝 의심(±7일 안 같은 경로·금액)", PAIR_OPTS, True, False),
    "pair_route": ("경로 다른 짝(비고·카드·결제수단이 다른 손 행)", PAIR_OPTS, True, False),
    "pair_future": ("손 행 날짜 오타?(미래·빈·못 읽는 날짜 손 행)", PAIR_OPTS, True, False),
    "cancel_row": ("취소 — 원거래가 CSV에 있다. CSV 행 삭제?", ["삭제", "유지", "보류"], False, False),
    "cancel_partial": ("부분 취소", ["제외", "보류"], False, False),
    "cancel_orphan": ("짝 없는 취소(원거래가 파일에 없음)", ["제외", "보류"], False, False),
    "cancel_ambiguous": ("취소 원거래 모호(후보 둘 이상)", ["제외", "보류"], False, False),
    "inst_vs_hand": ("할부 — 손 행에 전액 1행이 있다. 월별로?", ["월별", "전액", "보류"], False, False),
    "inst_mode": ("할부 표기 방식 모호(총액/회차)", ROW_OPTS + ["할부"], False, False),
    "changed": ("이미 적은 거래의 금액·날짜·상태가 파일에서 바뀜", ["유지", "삭제", "보류"], False, True),
    "vanished": ("취소 의심 — 자동·짝 행의 거래가 파일 기간 안인데 파일에서 사라짐", ["삭제", "유지", "보류"], False, True),
    "vanished_sched": ("취소 의심 — 할부 일정의 구매 거래가 최신 파일에서 사라짐", ["일정 끝냄", "유지", "보류"], False, True),
    "vanished_new": ("최신 파일에 없는 거래(옛 폴더 파일에만 있음 — 취소 의심)", ROW_OPTS, False, False),
    "gone_row": ("사라진 행 — 자동화가 적은(짝지은) CSV 행이 없다", ["지운 것 맞음", "고친 행", "다시 넣기", "보류"], False, False),
    "csv_only": ("내역에 없는 CSV 행(파일 기간 안)", ["현금", "커플통장", "오타", "기타", "보류"], False, False),
    "cardpay_missing": ("카드 행이 있는 달인데 카드대금 출금이 없다", ["확인", "보류"], False, False),
}


class Stop(Exception):
    """멈춤 — 메시지를 [FAIL]로 찍고 끝낸다."""


def today_kst():
    """실행일(한국 날짜). 시험이 이 함수만 바꿔 끼운다."""
    return dt.datetime.now(KST).date()


def now_kst():
    return dt.datetime.now(KST)


def p(path):
    return str(path).replace("\\", "/")


def md5_bytes(b):
    return hashlib.md5(b).hexdigest()


def md5_file(path):
    with open(path, "rb") as f:
        return md5_bytes(f.read())


def write_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, path)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def add_months(y, m, k):
    m0 = y * 12 + (m - 1) + k
    return m0 // 12, m0 % 12 + 1


def month_day(y, m, d):
    import calendar
    return dt.date(y, m, min(d, calendar.monthrange(y, m)[1]))


# ------------------------------------------------------------
# 경로 · 잠금
# ------------------------------------------------------------

class Paths:
    def __init__(self, repo, home, rehearsal):
        self.repo = os.path.abspath(repo)
        self.home = os.path.abspath(home)
        self.rehearsal = rehearsal
        self.inbox = os.path.join(self.home, "uploads", "내역")
        if rehearsal:
            self.base = os.path.join(self.home, "work-rehearsal", "내역")
            self.csv_dir = os.path.join(self.base, "uploads")
            self.rules = os.path.join(self.base, "rules")
            self.runs = os.path.join(self.base, "runs")
            self.log = os.path.join(self.base, "log.md")
        else:
            self.base = None
            self.csv_dir = os.path.join(self.home, "uploads")
            self.rules = os.path.join(self.home, "rules")
            self.runs = os.path.join(self.home, "work", "내역")
            self.log = os.path.join(self.home, "runs", "log.md")
        self.backup = os.path.join(self.csv_dir, "backup")
        self.config = os.path.join(self.rules, "계좌.toml")
        self.rules_csv = os.path.join(self.rules, "분류규칙.csv")
        self.ledger = os.path.join(self.rules, "원장.sqlite")

    def check_outside_clone(self):
        """실 내역·rules·회차 폴더·CSV가 clone 안이면 시작하지 않는다(3-0 11)."""
        repo = os.path.normcase(os.path.realpath(self.repo)) + os.sep
        for name, d in (("내역 폴더", self.inbox), ("CSV 폴더", self.csv_dir), ("rules", self.rules),
                        ("회차 폴더", self.runs)):
            if (os.path.normcase(os.path.realpath(d)) + os.sep).startswith(repo):
                raise Stop(f"{name} {p(d)}가 clone({p(self.repo)}) 안에 있다 — 실데이터는 clone 밖에만 둔다")


class RunLock:
    """CSV 폴더별 실행 잠금(OS 이름 있는 뮤텍스). 리허설과 운영은 CSV 폴더가 달라 서로 막지 않는다.
    WAIT_ABANDONED(앞 실행이 죽음)도 획득으로 본다(3-0 13)."""

    def __init__(self, key):
        self.name = "household-import-" + md5_bytes(os.path.normcase(os.path.abspath(key)).encode("utf-8"))[:16]
        self.h = None
        self.f = None

    def __enter__(self):
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateMutexW.restype = wintypes.HANDLE
            k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            k32.WaitForSingleObject.restype = wintypes.DWORD
            h = k32.CreateMutexW(None, False, "Local\\" + self.name)
            if not h:
                raise Stop("실행 잠금(뮤텍스)을 만들지 못했다")
            r = k32.WaitForSingleObject(h, 0)
            if r not in (0, 0x80):
                k32.CloseHandle(h)
                raise Stop("다른 pc_import 실행이 같은 CSV 폴더에서 돌고 있다 — 끝난 뒤 다시")
            self.h, self.k32 = h, k32
        else:
            import fcntl
            self.f = open(os.path.join(tempfile.gettempdir(), self.name + ".lock"), "w")
            try:
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.f.close()
                raise Stop("다른 pc_import 실행이 같은 CSV 폴더에서 돌고 있다 — 끝난 뒤 다시") from None
        return self

    def __exit__(self, *exc):
        if self.h:
            self.k32.ReleaseMutex(self.h)
            self.k32.CloseHandle(self.h)
        if self.f:
            self.f.close()


def file_in_use(path):
    """엑셀 등이 파일을 열고 있나 — 배타 열기 실패로만 판정한다(3-0 10). 엑셀 잠금 파일(~$이름)도 본다."""
    if not os.path.exists(path):
        return False
    d, n = os.path.split(path)
    if os.path.exists(os.path.join(d, "~$" + n)):
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        h = k32.CreateFileW(path, 0x80000000 | 0x40000000, 0, None, 3, 0x80, None)
        if h == wintypes.HANDLE(-1).value or h is None:
            return True
        k32.CloseHandle(h)
        return False
    try:
        with open(path, "r+b"):
            return False
    except PermissionError:
        return True


# ------------------------------------------------------------
# CSV — 원문 줄 단위(3-0 4·7)
# ------------------------------------------------------------

def split_records(text):
    """텍스트 → [(레코드 원문, 줄바꿈)]. 따옴표 안의 줄바꿈은 레코드 안이다."""
    recs, start, i, n, inq = [], 0, 0, len(text), False
    while i < n:
        ch = text[i]
        if ch == '"':
            inq = not inq
        elif not inq and ch in "\r\n":
            term = "\r\n" if text.startswith("\r\n", i) else ch
            recs.append((text[start:i], term))
            i += len(term)
            start = i
            continue
        i += 1
    if start < n:
        recs.append((text[start:], ""))
    return recs


def parse_fields(rec_text):
    if rec_text == "":
        return []
    return next(csv.reader(io.StringIO(rec_text, newline="")))


@dataclass
class Rec:
    no: int                  # 파일 안 레코드 번호(머리 = 1 — 엑셀 행 번호와 같다)
    text: str
    term: str
    fields: list
    v: dict                  # 열 → 앞뒤 공백 뺀 값
    blank: bool
    date: object = None      # dt.date 또는 None
    date_state: str = ""     # ok · blank · bad · future
    amount: object = None    # int 또는 None
    route: object = None
    key: str = ""


def csv_route(v, cards):
    """CSV 행의 경로: 카드 행은 (card, 카드, 구분) · 이체는 (transfer, 출금, 입금) · 통장 행은 (acct, 계좌, 구분)."""
    if v["비고"] in cards:
        return ("card", v["비고"], v["구분"])
    if v["구분"] == "이체":
        return ("transfer", v["결제수단"], v["소분류"]) if v["결제수단"] and v["소분류"] else None
    if v["구분"] in ("지출", "수입") and v["결제수단"]:
        return ("acct", v["결제수단"], v["구분"])
    return None


def row_key(date_part, v, amount_part, seq):
    return "|".join([date_part, v["구분"], v["결제수단"], v["비고"], amount_part, str(seq)])


class CsvDoc:
    """가계부 CSV를 원문 레코드 단위로 읽는다. 비교 값은 load_csv와 같은 정규화(strip · parse_amount ·
    pd.to_datetime), 원문은 쓰기에만 쓴다. 행 키는 값 기반(날짜·구분·결제수단·비고·금액·같은 값 순번)."""

    def __init__(self, path, today, cards=CARDS):
        import pandas as pd
        self.path = path
        with open(path, "rb") as f:
            self.data = f.read()
        self.bom = self.data.startswith(codecs.BOM_UTF8)
        try:
            text = self.data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise Stop("CSV를 UTF-8로 읽을 수 없다 — 엑셀 기본 저장(CP949)일 수 있다. "
                       "'CSV UTF-8(쉼표로 분리)'로 다시 저장") from None
        recs = split_records(text)
        if not recs:
            raise Stop("CSV가 비었다")
        htext, hterm = recs[0]
        self.header_text, self.header_term = htext, hterm
        self.nl = hterm or "\r\n"
        self.header = [c.strip() for c in parse_fields(htext)]
        if len(self.header) != len(COLS) or set(self.header) != set(COLS):
            raise Stop(f"CSV 머리 9열이 기대와 다르다 — 기대 {COLS} / 실제 {self.header}")
        self.recs = []
        for i, (t, term) in enumerate(recs[1:], start=2):
            fields = parse_fields(t)
            vals = [f.strip() for f in fields]
            v = {c: (vals[j] if j < len(vals) else "") for j, c in enumerate(self.header)}
            extra = any(x for x in vals[len(self.header):])
            self.recs.append(Rec(no=i, text=t, term=term, fields=fields, v=v,
                                 blank=not any(vals) and not extra))
        # 끝의 빈 레코드 묶음(`,,,,,,,,`·빈 줄) — 새 행은 이 앞에 덧붙인다(고른 항목 12)
        end = len(self.recs)
        while end > 0 and self.recs[end - 1].blank:
            end -= 1
        self.tail_start = end
        # 날짜: load_csv처럼 빈 날짜 행을 빼고, 앞뒤 공백을 벗기지 않은 원문으로 한 번에
        # pd.to_datetime(errors='coerce') — load_csv가 버리는 날짜는 여기서도 'bad'다
        di = self.header.index("날짜")
        dated = [r for r in self.recs if not r.blank and r.v["날짜"] != ""]
        parsed = pd.to_datetime(pd.Series([r.fields[di] for r in dated], dtype=object), errors="coerce")
        for r, d in zip(dated, parsed):
            if pd.isna(d):
                r.date_state = "bad"
            else:
                r.date = d.date()
                r.date_state = "future" if r.date > today else "ok"
        counts = {}
        for r in self.recs:
            if r.blank:
                continue
            if r.v["날짜"] == "":
                r.date_state = "blank"
            try:
                a = pl.parse_amount(r.v["금액"])
                r.amount = int(round(a))
            except ValueError:
                r.amount = None
            r.route = csv_route(r.v, cards)
            dpart = r.date.isoformat() if r.date else "?" + r.v["날짜"]
            apart = str(r.amount) if r.amount is not None else "?" + r.v["금액"]
            base = row_key(dpart, r.v, apart, 0)
            counts[base] = counts.get(base, 0) + 1
            r.key = row_key(dpart, r.v, apart, counts[base])
        self.by_key = {r.key: r for r in self.recs if r.key}
        self.by_no = {r.no: r for r in self.recs if r.key}
        self.names = {c: sorted({r.v[c] for r in self.recs if not r.blank and r.v[c]})
                      for c in ("대분류", "소분류", "세부내용")}

    def data_count(self):
        return self.tail_start

    def build(self, new_rows, delete_keys):
        """새 CSV 바이트 → (바이트, 새 레코드 번호 목록). 옛 레코드는 원문 그대로 같은 순서, 지울 것은 키가 같은 레코드뿐."""
        out = [self.header_text + self.header_term]
        body = [r for r in self.recs[: self.tail_start] if r.key not in delete_keys]
        tail = self.recs[self.tail_start:]
        lines = [[r.text, r.term] for r in body]
        new_lines = [[render_row(row, self.header), self.nl] for row in new_rows]
        if new_lines:
            if lines and lines[-1][1] == "":
                lines[-1][1] = self.nl
                if not tail:
                    new_lines[-1][1] = ""
            elif not lines and self.header_term == "":
                out[0] = self.header_text + self.nl
        all_lines = lines + new_lines + [[r.text, r.term] for r in tail]
        text = out[0] + "".join(t + term for t, term in all_lines)
        data = (codecs.BOM_UTF8 if self.bom else b"") + text.encode("utf-8")
        first_new = 2 + len(lines)
        return data, list(range(first_new, first_new + len(new_lines)))


def render_row(row, header):
    """새 행 꼴은 한 가지: 금액 ≥1000은 "n,nnn", 미만은 정수, 쉼표·따옴표 든 칸만 따옴표, 앞뒤 공백 없음."""
    out = []
    for c in header:
        v = row[c]
        if c == "금액":
            s = f"{int(v):,}" if abs(int(v)) >= 1000 else str(int(v))
        else:
            s = str(v).strip()
        if any(ch in s for ch in ',"\r\n'):
            s = '"' + s.replace('"', '""') + '"'
        out.append(s)
    return ",".join(out)


# ------------------------------------------------------------
# 분류규칙.csv · 원장.sqlite
# ------------------------------------------------------------

class Rules:
    def __init__(self, path):
        self.path = path
        self.rows = []
        self.data = b""
        if os.path.exists(path):
            with open(path, "rb") as f:
                self.data = f.read()
            text = self.data.decode("utf-8-sig")
            rd = csv.reader(io.StringIO(text, newline=""))
            head = next(rd, None)
            if head is None or [h.strip() for h in head] != RULE_COLS:
                raise Stop(f"분류규칙.csv 머리가 {RULE_COLS}가 아니다")
            for r in rd:
                if any(x.strip() for x in r):
                    self.rows.append(dict(zip(RULE_COLS, [x.strip() for x in r] + [""] * 10)))
        self.by_key = defaultdict(list)
        for r in self.rows:
            self.by_key[r["키"]].append(r)

    def lookup(self, key):
        rows = self.by_key.get(key)
        if not rows:
            return None
        vals = {(r["대분류"], r["소분류"], r["세부내용"], r["고정여부"], r["결제수단"]) for r in rows}
        every = any(r["물음"] == "매번" for r in rows) or len(vals) > 1
        last = rows[-1]
        return {"mode": "매번" if every else "자동", "raws": {r["원문"] for r in rows},
                "values": {c: last[c] for c in ("대분류", "소분류", "세부내용", "고정여부", "결제수단")}}

    def appended_bytes(self, new):
        """옛 바이트 그대로 + 새 줄. 새 파일이면 BOM·머리부터."""
        buf = io.StringIO(newline="")
        w = csv.writer(buf, lineterminator="\r\n")
        if not self.data:
            w.writerow(RULE_COLS)
        for r in new:
            w.writerow([r.get(c, "") for c in RULE_COLS])
        head = self.data if self.data else codecs.BOM_UTF8
        if self.data and not self.data.endswith(b"\n"):
            head += b"\r\n"
        return head + buf.getvalue().encode("utf-8")


LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS tx(key TEXT PRIMARY KEY, source TEXT, data TEXT, first_run TEXT, last_run TEXT);
CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, state TEXT, csv_key TEXT, extra TEXT, run TEXT);
CREATE TABLE IF NOT EXISTS csvnote(id TEXT PRIMARY KEY, state TEXT, answer TEXT, run TEXT);
CREATE TABLE IF NOT EXISTS transfer(out_key TEXT, in_key TEXT, src TEXT, dst TEXT, amount INTEGER,
                                    date TEXT, csv_key TEXT, run TEXT);
CREATE TABLE IF NOT EXISTS schedule(key TEXT PRIMARY KEY, source TEXT, card TEXT, total INTEGER, months INTEGER,
                                    first_month TEXT, row TEXT, run TEXT, mode TEXT DEFAULT 'rows');
CREATE TABLE IF NOT EXISTS anchor(account TEXT PRIMARY KEY, end_date TEXT, end_balance INTEGER, baseline INTEGER,
                                  reason TEXT, run TEXT);
CREATE TABLE IF NOT EXISTS period(source TEXT, file TEXT, md5 TEXT, start TEXT, end_ TEXT, run TEXT,
                                  PRIMARY KEY(source, file, md5));
CREATE TABLE IF NOT EXISTS runs(run TEXT PRIMARY KEY, applied TEXT, csv_before TEXT, csv_after TEXT,
                                csv_name TEXT, summary TEXT);
"""

DONE_STATES = {"written", "paired", "suppressed", "cancel_done", "card_payment", "transfer_pair", "excluded",
               "inst_pair", "sched", "kept_missing", "points_full", "internal", "cancel_kept"}
LINK_STATES = {"written", "paired", "kept_missing"}


class Ledger:
    """원장(sqlite) — scan은 읽기 전용으로 열어 메모리에 올린다."""

    def __init__(self, path):
        self.path = path
        self.tx, self.state, self.csvnote, self.schedule, self.anchor, self.runs = {}, {}, {}, {}, {}, {}
        self.transfers, self.periods = [], []
        if not os.path.exists(path):
            return
        con = sqlite3.connect(Path(path).absolute().as_uri() + "?mode=ro", uri=True)
        try:
            for k, src, data in con.execute("SELECT key, source, data FROM tx"):
                self.tx[k] = Tx.from_json(json.loads(data))
            for k, st, ck, extra, run in con.execute("SELECT key, state, csv_key, extra, run FROM state"):
                self.state[k] = {"state": st, "csv_key": ck or "", "extra": json.loads(extra or "{}"), "run": run}
            for i, st, ans, run in con.execute("SELECT id, state, answer, run FROM csvnote"):
                self.csvnote[i] = {"state": st, "answer": ans, "run": run}
            for row in con.execute("SELECT out_key, in_key, src, dst, amount, date, csv_key, run FROM transfer"):
                self.transfers.append(dict(zip(("out_key", "in_key", "src", "dst", "amount", "date",
                                                "csv_key", "run"), row)))
            for row in con.execute("SELECT key, source, card, total, months, first_month, row, run, mode FROM schedule"):
                d = dict(zip(("key", "source", "card", "total", "months", "first_month", "row", "run", "mode"), row))
                d["row"] = json.loads(d["row"])
                self.schedule[d["key"]] = d
            for row in con.execute("SELECT account, end_date, end_balance, baseline, reason, run FROM anchor"):
                self.anchor[row[0]] = dict(zip(("account", "end_date", "end_balance", "baseline", "reason", "run"), row))
            for row in con.execute("SELECT source, file, md5, start, end_, run FROM period"):
                self.periods.append(dict(zip(("source", "file", "md5", "start", "end", "run"), row)))
            for row in con.execute("SELECT run, applied, csv_before, csv_after, csv_name, summary FROM runs"):
                self.runs[row[0]] = dict(zip(("run", "applied", "csv_before", "csv_after", "csv_name", "summary"), row))
        finally:
            con.close()

    def linked_csv_keys(self):
        keys = {s["csv_key"] for s in self.state.values() if s["state"] in LINK_STATES and s["csv_key"]}
        return keys


# ------------------------------------------------------------
# 계산 결과
# ------------------------------------------------------------

@dataclass
class Item:
    id: str
    kind: str
    tx: object = None
    rec: object = None
    row: dict = None          # '추가' 답의 기본값(제안)
    cands: list = field(default_factory=list)   # 후보 CSV 레코드
    note: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def reason(self):
        return KINDS[self.kind][0] + (f" — {self.note}" if self.note else "")


@dataclass
class Disp:
    """거래 하나의 처리. status: 자동(새 행) · 짝(CSV 손 행) · 제외(행 없음) · 확인 필요 · 끝남(원장)."""
    tx: object
    status: str
    reason: str
    state: str = ""               # apply 때 원장 상태
    row: dict = None
    route: tuple = None
    rdate: object = None          # 짝 비교 날짜(새 행 날짜)
    csv_key: str = ""
    item: object = None
    partner: str = ""             # 이체 짝·취소 짝 거래키
    transfer: tuple = None        # (출금 계좌, 입금 계좌)
    rule_key: str = ""
    wait_fill: str = ""           # 원장의 기다리는 이체를 채울 쪽(in_key/out_key)


class Result:
    def __init__(self):
        self.fatal = []
        self.failures = []        # {file, source, reason}
        self.ignored = []
        self.files = []
        self.notes = []
        self.items = []
        self.disps = {}
        self.cur = {}
        self.pre = []
        self.union = {}
        self.new_rows = []        # (행, 출처 설명)
        self.deletes = set()
        self.card_checks = []
        self.gaps = []
        self.balance = []
        self.checks = {}
        self.warn_up = {}
        self.inputs = {}
        self.source_status = {}
        self.inherited = 0
        self.answer_errors = []
        self.rule_appends = []
        self.rule_notes = []
        self.ledger_ops = []
        self.draft = b""
        self.blocked = []


# ------------------------------------------------------------
# 엔진
# ------------------------------------------------------------

def find_csv(P):
    if not os.path.isdir(P.csv_dir):
        raise Stop(f"CSV 폴더가 없다: {p(P.csv_dir)}" + (" — 먼저 `--rehearsal init`" if P.rehearsal else ""))
    names = sorted(n for n in os.listdir(P.csv_dir)
                   if n.lower().endswith(".csv") and not n.startswith("~$")
                   and os.path.isfile(os.path.join(P.csv_dir, n)))
    if len(names) != 1:
        raise Stop(f"{p(P.csv_dir)}의 CSV가 {len(names)}개 {names} — 한 파일이어야 한다"
                   f"(지난 apply가 중간에 끝났으면 backup/의 사본 md5와 대조해 옛 파일을 옮긴다)")
    return os.path.join(P.csv_dir, names[0])


def folder_key(rel):
    """내역 폴더 이름(받은 날) → 정렬 키. YYYY-MM-DD·YYYYMMDD 둘 다 날짜로 읽는다(실물 폴더는 20261027 꼴)."""
    top = rel.split("/")[0]
    m = re.fullmatch(r"(\d{4})-?(\d{2})-?(\d{2})", top)
    return (0, m.group(1) + m.group(2) + m.group(3)) if m else (1, top)


def inbox_files(P):
    out = []
    if not os.path.isdir(P.inbox):
        return out
    for root, dirs, files in os.walk(P.inbox):
        dirs.sort()
        for n in sorted(files):
            if n.startswith("~$"):
                continue
            full = os.path.join(root, n)
            out.append((os.path.relpath(full, P.inbox).replace("\\", "/"), full))
    return out


class Engine:
    def __init__(self, P, today, answers=None, run_id="", opts=None):
        self.P, self.today, self.answers, self.run_id = P, today, answers or {}, run_id
        self.opts = opts or {}
        self.R = Result()

    # ---- 바깥 ----
    def run(self):
        R = self.R
        P = self.P
        try:
            self.cfg = srcmod.load_config(P.config, set(ACCOUNTS), set(CARDS))
        except FileNotFoundError:
            raise Stop(f"설정이 없다: {p(P.config)} — 예시 pc/import/계좌.toml.example을 보고 실물 파일에 맞춰 만든다")
        except ConfigError as e:
            raise Stop("계좌.toml 규칙 위반:\n  " + "\n  ".join(e.args[0]))
        self.start = self.cfg["start_date"]
        self.src_by_id = {s.id: s for s in self.cfg["sources"]}
        self.bank_src = defaultdict(list)
        for s in self.cfg["sources"]:
            if s.kind == "bank":
                self.bank_src[s.account].append(s)
        self.card_src = {s.card: s for s in self.cfg["sources"] if s.kind == "card"}
        self.csv_path = find_csv(P)
        self.doc = CsvDoc(self.csv_path, self.today)
        self.led = Ledger(P.ledger)
        self.rules = Rules(P.rules_csv)
        R.inputs = self.input_md5()
        self.read_files()
        self.build_union()
        self.classify_all()
        self.pair_all()
        self.csv_only()
        self.card_payment_checks()
        self.apply_answers()
        self.make_draft()
        self.balance_checks()
        self.final_blocks()
        return R

    def input_md5(self):
        P = self.P
        d = {"csv": [os.path.basename(self.csv_path), md5_file(self.csv_path)]}
        for name, path in (("config", P.config), ("rules", P.rules_csv), ("ledger", P.ledger)):
            d[name] = md5_file(path) if os.path.exists(path) else None
        d["files"] = {rel: md5_file(full) for rel, full in inbox_files(P)}
        return d

    # ---- 파일 ----
    def read_files(self):
        R = self.R
        ignore = {(f["path"], f["md5"]): f for f in self.cfg["ignore_files"]}
        self.parsed = []          # (rel, md5, src, txs, period, chain)
        for rel, full in inbox_files(self.P):
            m = R.inputs["files"][rel]
            ent = {"file": rel, "md5": m, "source": None, "status": "", "reason": "", "rows": 0, "period": None,
                   "excluded": 0}
            R.files.append(ent)
            if (rel, m) in ignore:
                ent.update(status="무시(설정)", reason=ignore[(rel, m)]["reason"])
                R.ignored.append(ent)
                continue
            try:
                kind, sheets = srcmod.read_table(full)
            except SourceError as e:
                self.fail(ent, None, str(e))
                continue
            matched, errs = [], []
            for s in self.cfg["sources"]:
                if kind not in s.file_types:
                    continue
                try:
                    hit = srcmod.identify(sheets, s)
                except SourceError as e:
                    errs.append((s, str(e)))
                    continue
                if hit:
                    matched.append((s, hit))
            if len(matched) > 1:
                self.fail(ent, None, "출처 둘 이상에 맞는다(" + ", ".join(s.id for s, _ in matched) + ") — 식별 값을 고친다")
                continue
            if not matched:
                if len(errs) == 1:
                    self.fail(ent, errs[0][0].id, errs[0][1])
                elif errs:
                    self.fail(ent, None, " / ".join(f"{s.id}: {e}" for s, e in errs))
                else:
                    self.fail(ent, None, f"어느 출처 서명(열 이름 목록)에도 맞지 않는다({kind})")
                continue
            s, (si, h) = matched[0]
            ent["source"] = s.id
            try:
                stats = {}
                txs = srcmod.parse_sheet(sheets[si][1], h, s, rel, stats)
                ent["excluded"] = stats.get("excluded", 0)
                chain = srcmod.check_balance_chain(txs, rel)
                srcmod.assign_keys(txs, s)
            except SourceError as e:
                self.fail(ent, s.id, str(e))
                continue
            period = srcmod.header_period(sheets[si][1], h, s)
            lo, hi = min(t.date for t in txs), max(t.date for t in txs)
            if period:
                lo, hi = min(lo, period[0]), max(hi, period[1])
            ent.update(status="읽음", rows=len(txs), period=(lo.isoformat(), hi.isoformat()))
            self.parsed.append({"rel": rel, "md5": m, "src": s, "txs": txs, "period": (lo, hi), "chain": chain,
                                "mtime": os.stat(full).st_mtime})
        for s in self.cfg["sources"]:
            got = [f for f in self.parsed if f["src"].id == s.id]
            bad = [f for f in R.failures if f["source"] == s.id]
            R.source_status[s.id] = "실패" if bad else ("읽음" if got else "건너뜀(파일 없음)")

    def fail(self, ent, sid, reason):
        ent.update(status="실패", source=sid, reason=reason)
        self.R.failures.append({"file": ent["file"], "source": sid, "reason": reason})

    def covers(self, sid, d, current_only=False):
        for f in self.parsed:
            if f["src"].id == sid and f["period"][0] <= d <= f["period"][1]:
                return True
        if current_only:
            return False
        for per in self.led.periods:
            if per["source"] == sid and per["start"] <= d.isoformat() <= per["end"]:
                return True
        return False

    def account_covers(self, acct, d, slack=0):
        return any(self.covers(s.id, d + dt.timedelta(days=k))
                   for s in self.bank_src.get(acct, []) for k in range(-slack, slack + 1))

    # ---- 합치기 · 시작일 · 원장 ----
    def build_union(self):
        R = self.R
        union, origin = {}, {}
        order = sorted(self.parsed, key=lambda f: (folder_key(f["rel"]), f["mtime"]))
        for f in order:
            f["keys"] = {t.key for t in f["txs"]}
            for t in f["txs"]:
                if t.key in union and self.content(union[t.key]) != self.content(t):
                    R.notes.append(f"같은 거래키가 두 파일에서 다르다 → 나중 파일 값({f['rel']}): {t.key.split('|')[0]}")
                union[t.key] = t
                origin[t.key] = f["rel"]
        # 출처마다 '그 날짜를 덮는 가장 늦게 받은 파일'이 정본이다. 옛 날짜 폴더 파일에만 남은 거래는
        # 그 뒤 받은 파일에서 사라진 거래다(행 삭제형 취소 의심 — 3-0 4 셋째 항). 합집합에서 뺀다.
        self.stale = []
        for k, t in list(union.items()):
            latest = None
            for f in order:
                if f["src"].id == t.source and f["period"][0] <= t.date <= f["period"][1]:
                    latest = f
            if latest is not None and k not in latest["keys"]:
                self.stale.append(t)
                del union[k]
        R.union = union
        R.pre = [t for t in union.values() if t.date < self.start]
        cur = {k: t for k, t in union.items() if t.date >= self.start}
        self.from_ledger = set()
        self.vanished = [t for t in self.stale if t.date >= self.start]
        stale_keys = {t.key for t in self.stale}
        for k, t in self.led.tx.items():
            if k in union or k in stale_keys or t.date < self.start or "#" in k:
                continue
            if t.source not in self.src_by_id:
                continue
            if self.covers(t.source, t.date, current_only=True):
                if self.led.state.get(k, {}).get("state") in ("written", "paired", "sched", "held", None):
                    self.vanished.append(t)
                continue
            cur[k] = t
            self.from_ledger.add(k)
        R.cur = cur
        # 공백: 출처마다 파일 기간(지금 파일 + 원장의 지난 파일)이 이어지지 않으면 취소·누락을 못 본다
        for s in self.cfg["sources"]:
            spans = [f["period"] for f in self.parsed if f["src"].id == s.id]
            spans += [(dt.date.fromisoformat(x["start"]), dt.date.fromisoformat(x["end"]))
                      for x in self.led.periods if x["source"] == s.id]
            spans = sorted({(max(a, self.start), b) for a, b in spans if b >= self.start})
            reach = None
            for a, b in spans:
                if reach is not None and a > reach + dt.timedelta(days=1):
                    R.gaps.append({"source": s.id, "from": (reach + dt.timedelta(days=1)).isoformat(),
                                   "to": (a - dt.timedelta(days=1)).isoformat()})
                reach = b if reach is None else max(reach, b)

    @staticmethod
    def content(t):
        # 상태 글자(승인 → 전표접수 등)는 넣지 않는다 — 취소는 cancel로 본다
        return (t.date, t.amount, t.direction, t.cancel, t.inst_months)

    # ---- 분류 ----
    def disp(self, t, status, reason, **kw):
        old = self.R.disps.get(t.key)
        d = Disp(tx=t, status=status, reason=reason, **kw)
        if old is not None and old.item is not None and old.item is not d.item:
            self.R.items = [x for x in self.R.items if x is not old.item]
        self.R.disps[t.key] = d
        return d

    def item(self, kind, t=None, rec=None, row=None, cands=(), note="", **extra):
        iid = ("tx:" + t.key) if t is not None else ("csv:" + rec.key if rec is not None else extra.pop("iid"))
        if kind == "gone_row":
            iid = "gone:" + t.key
        it = Item(id=iid, kind=kind, tx=t, rec=rec, row=row, cands=list(cands), note=note, extra=extra)
        self.R.items = [x for x in self.R.items if x.id != iid] + [it]
        return it

    def ask(self, t, kind, row=None, note="", route=None, cands=(), dkw=None, **extra):
        it = self.item(kind, t=t, row=row, note=note, cands=cands, **extra)
        return self.disp(t, "확인 필요", it.reason, state="held", row=row, route=route,
                         rdate=dt.date.fromisoformat(row["날짜"]) if row else t.date, item=it, **(dkw or {}))

    def card_row(self, t, values, amount):
        s = self.src_by_id[t.source]
        return {"날짜": t.date.isoformat(), "구분": "지출", "대분류": values.get("대분류", ""),
                "소분류": values.get("소분류", ""), "세부내용": values.get("세부내용", ""), "금액": amount,
                "결제수단": values.get("결제수단") or s.account, "고정여부": values.get("고정여부", ""), "비고": s.card}

    def bank_row(self, t, kind, values):
        return {"날짜": t.date.isoformat(), "구분": kind, "대분류": values.get("대분류", ""),
                "소분류": values.get("소분류", ""), "세부내용": values.get("세부내용", ""), "금액": t.amount,
                "결제수단": t.account, "고정여부": values.get("고정여부", ""), "비고": ""}

    def transfer_row(self, src, dst, d, amount):
        dflt = self.cfg["transfer_pair"].get((src, dst), {"대분류": "이체", "세부내용": "", "고정여부": ""})
        return {"날짜": d.isoformat(), "구분": "이체", "대분류": dflt["대분류"], "소분류": dst,
                "세부내용": dflt["세부내용"], "금액": amount, "결제수단": src, "고정여부": dflt["고정여부"], "비고": ""}

    def suggestion(self, key):
        return self.hand_suggest.get(key, {})

    def prime_suggestions(self):
        """같은 날·같은 경로·같은 금액 손 행의 분류를 '제안 칸'에만 쓴다(규칙은 답으로만 생긴다 — 1-3)."""
        idx = defaultdict(list)
        for r in self.doc.recs:
            if r.route and r.date and r.amount is not None:
                idx[(r.route, r.amount, r.date)].append(r)
        for t in self.R.cur.values():
            if t.kind == "card" and not t.cancel:
                route, amt = ("card", t.card, "지출"), self.amount_after_points(t)
            elif t.kind == "bank" and t.direction == "out":
                route, amt = ("acct", t.account, "지출"), t.amount
            else:
                continue
            hits = idx.get((route, amt, t.date))
            key = normalize_merchant(t.merchant)
            if hits and key:
                self.hand_suggest.setdefault(key, {c: hits[0].v[c] for c in
                                                   ("대분류", "소분류", "세부내용", "고정여부", "결제수단")})

    def classify_merchant(self, t, amount, make_row):
        """가맹점 규칙(3-0 6). 규칙에 정확히 있고 '자동'이며 원문도 본 것일 때만 자동 행."""
        key = normalize_merchant(t.merchant)
        rule = self.rules.lookup(key) if key else None
        base = (rule or {}).get("values") or self.suggestion(key)
        row = make_row(t, base, amount)
        route = self.route_of_row(row)
        if not key:
            return self.ask(t, "empty_merchant", row=row, route=route)
        if any(pk and pk in key for pk in self.cfg["platform_keys"]):
            return self.ask(t, "platform", row=row, route=route, rule_key=key)
        if rule is None:
            return self.ask(t, "new_merchant", row=row, route=route, rule_key=key)
        if rule["mode"] == "매번":
            return self.ask(t, "ask_every", row=row, route=route, rule_key=key)
        if t.merchant.strip() not in rule["raws"]:
            return self.ask(t, "raw_diff", row=row, route=route, rule_key=key)
        return self.disp(t, "자동", f"규칙 '{key}'", state="written", row=row, route=route, rdate=t.date,
                         rule_key=key)

    def route_of_row(self, row):
        return csv_route({c: str(row[c]) for c in COLS}, set(CARDS))

    def match_pats(self, pats, t, field="all"):
        """field: merchant(가맹점·내용 칸) · memo(적요 칸들) · category(거래구분 칸) · all(셋 다)."""
        fields = {"merchant": [t.merchant], "memo": list(t.memo),
                  "category": [t.status] if t.kind == "bank" else []}.get(field)
        if fields is None:
            fields = [t.merchant, *t.memo] + ([t.status] if t.kind == "bank" else [])
        return any(rx.search(f) for rx in pats for f in fields if f)

    def classify_all(self):
        R = self.R
        cfg = self.cfg
        self.hand_suggest = {}
        self.prime_suggestions()
        self.cancels, self.installs, self.transfer_out, self.transfer_in = [], [], [], []
        self.cardpays = []
        self.cancelled_sched = set()      # 이번 회차에 취소로 끝낼 할부 일정(구매 거래키)
        # 원장에 끝난 상태가 있는 거래
        for k, t in sorted(R.cur.items()):
            st = self.led.state.get(k)
            if not st or st["state"] == "held":
                continue
            old = self.led.tx.get(k)
            if k not in self.from_ledger and old is not None and self.content(old) != self.content(t):
                if st["state"] == "sched" and t.cancel and not old.cancel:
                    # 할부 구매가 '취소' 상태로 바뀜 — 일정을 끝내고 이미 적힌 회차 행은 삭제를 묻는다
                    self.cancelled_sched.add(k)
                    self.disp(t, "제외", "할부 구매 취소(상태 변경) — 일정 끝냄", state="cancel_done")
                    continue
                if st["state"] in ("written", "paired"):
                    kind = "cancel_row" if (t.cancel and not old.cancel) else "changed"
                    rec = self.doc.by_key.get(st["csv_key"])
                    self.ask(t, kind, note="원장 값과 파일 값이 다름", cands=[rec] if rec else [],
                             csv_key=st["csv_key"], ledger_change=True)
                    continue
            if st["state"] in ("written", "paired") and st["csv_key"] and st["csv_key"] not in self.doc.by_key:
                self.ask(t, "gone_row", note=f"CSV 행 키 {st['csv_key']}")
                continue
            self.disp(t, "끝남", f"원장: {st['state']}", state=st["state"], csv_key=st.get("csv_key", ""))
        for t in self.vanished:
            st = self.led.state.get(t.key) or {"state": "", "csv_key": ""}
            if st["state"] in ("written", "paired"):
                rec = self.doc.by_key.get(st["csv_key"])
                it = self.item("vanished", t=t, cands=[rec] if rec else [], csv_key=st["csv_key"])
                self.disp(t, "확인 필요", it.reason, state=st["state"], csv_key=st["csv_key"], item=it)
            elif st["state"] == "sched":
                it = self.item("vanished_sched", t=t)
                self.disp(t, "확인 필요", it.reason, state="sched", item=it)
            elif st["state"] in ("", "held"):
                row = (self.card_row(t, self.rule_values(t), self.amount_after_points(t)) if t.kind == "card"
                       else self.bank_row(t, "지출" if t.direction == "out" else "수입", {}))
                it = self.item("vanished_new", t=t, row=row)
                self.disp(t, "확인 필요", it.reason, state="held", row=row, item=it)
            else:
                self.disp(t, "끝남", f"원장: {st['state']}(최신 파일에 없음)", state=st["state"],
                          csv_key=st.get("csv_key", ""))
        # 새 거래(또는 보류)
        weak_groups = defaultdict(list)
        for k, t in sorted(R.cur.items()):
            if k in R.disps:
                continue
            s = self.src_by_id[t.source]
            if s.weak_key and not t.time and t.balance is None:
                weak_groups[t.base].append(t)
            if t.kind == "card":
                self.classify_card(t, s)
            else:
                self.classify_bank(t, s)
        self.match_transfers()
        self.match_cancels()
        self.plan_installments()
        self.pair_billing_rows()
        # 키 약한 출처에서 같은 날·같은 금액·같은 가맹점 n건은 실제 n건인지 묻는다(K9)
        for base, ts in weak_groups.items():
            if len(ts) < 2:
                continue
            for t in ts:
                d = R.disps.get(t.key)
                if d and d.status in ("자동", "확인 필요") and d.row and (
                        d.item is None or d.item.kind in ("new_merchant", "ask_every", "platform", "raw_diff",
                                                          "income")):
                    self.ask(t, "dup_weak", row=d.row, route=d.route, note=f"{len(ts)}건",
                             dkw={"transfer": d.transfer, "partner": d.partner})

    def amount_after_points(self, t):
        if t.billed is not None:
            return t.billed
        return t.amount - (t.points or 0)

    def classify_card(self, t, s):
        if t.cancel:
            self.cancels.append(t)
            return
        if s.installment_mode == "total" and t.inst_months >= 2:
            if s.roles.get("installment_seq"):
                row = self.card_row(t, {}, t.amount)
                self.ask(t, "inst_mode", row=row, route=self.route_of_row(row), months=t.inst_months)
                return
            self.installs.append(t)
            return
        amount = self.amount_after_points(t)
        if amount <= 0:
            self.disp(t, "제외", "전액 포인트(청구액 0)", state="points_full")
            return
        if t.foreign and t.billed is None:
            row = self.card_row(t, {}, amount)
            self.ask(t, "foreign", row=row, route=self.route_of_row(row))
            return
        self.classify_merchant(t, amount, lambda tt, v, a: self.card_row(tt, v, a))

    def classify_bank(self, t, s):
        cfg = self.cfg
        mp = self.match_pats
        cps = [c for c in cfg["card_payment"] if c["account"] == t.account and mp(c["patterns"], t, c["field"])]
        ins = [c for c in cfg["internal"] if c["account"] == t.account and mp(c["patterns"], t, c["field"])]
        trs = [c for c in cfg["transfer"] if c["account"] == t.account
               and c["direction"] in ("both", t.direction) and mp(c["patterns"], t, c["field"])]
        amb = [c for c in cfg["transfer_ambiguous"] if c["account"] == t.account
               and c["direction"] in ("both", t.direction) and mp(c["patterns"], t, c["field"])]
        kind = "지출" if t.direction == "out" else "수입"
        # 판정 순서: 카드대금 → 내부 이동(세이프박스 등) → 이체 → 수입/지출. 앞 둘이 다른 것과 겹치면 적요 충돌
        if (cps and (ins or trs or amb)) or (ins and (trs or amb)) or len({c["card"] for c in cps}) > 1 \
                or len({c["name"] for c in ins}) > 1:
            row = self.bank_row(t, kind, {})
            self.ask(t, "memo_conflict", row=row, route=self.route_of_row(row))
            return
        if ins:
            self.disp(t, "제외", f"내부 이동({ins[0]['name']}) — 같은 계좌 안의 칸 이동, 행 0", state="internal")
            return
        if cps:
            if t.direction != "out":
                row = self.bank_row(t, kind, {})
                self.ask(t, "memo_conflict", row=row, route=self.route_of_row(row), note="카드사 입금")
                return
            self.disp(t, "제외", f"카드대금({cps[0]['card']})", state="card_payment")
            self.cardpays.append((t, cps[0]["card"]))
            return
        cps_tr = {c["counterpart"] for c in trs}
        # 계좌마다 유일한 이체 규칙이 하나로 풀리면 그것이 본인 이름 같은 모호 패턴보다 먼저다
        if len(cps_tr) == 1:
            other = next(iter(cps_tr))
            (self.transfer_out if t.direction == "out" else self.transfer_in).append((t, other))
            return
        if trs or amb:
            cands = sorted(cps_tr | {x for c in amb for x in c["candidates"]})
            dflt = amb[0]["default"] if amb else cands[0]
            src, dst = (t.account, dflt) if t.direction == "out" else (dflt, t.account)
            row = self.transfer_row(src, dst, t.date, t.amount)
            self.ask(t, "transfer_unknown", row=row, route=self.route_of_row(row), note="후보 " + "/".join(cands))
            return
        if t.direction == "in":
            inc = [c for c in cfg["income"] if c["account"] == t.account and mp(c["patterns"], t, c["field"])]
            if len(inc) == 1:
                row = self.bank_row(t, "수입", inc[0])
                self.disp(t, "자동", "수입 규칙(계좌.toml)", state="written", row=row,
                          route=self.route_of_row(row), rdate=t.date)
                return
            row = self.bank_row(t, "수입", {"대분류": "기타 수익"})
            self.ask(t, "income", row=row, route=self.route_of_row(row),
                     note="수입 규칙 둘 이상" if inc else "")
            return
        self.classify_merchant(t, t.amount, lambda tt, v, a: self.bank_row(tt, "지출", v))

    # ---- 이체(3-0 5) ----
    def match_transfers(self):
        R = self.R
        outs = list(self.transfer_out)       # (t, 입금 계좌)
        ins = list(self.transfer_in)         # (t, 출금 계좌)
        used = set()
        # 원장의 기다리는 이체: 한쪽 키만 있고 다른 쪽이 '파일 있는 계좌'라 나중에 올 수 있는 것
        waiting_out = [x for x in self.led.transfers if x["out_key"] and not x["in_key"]]
        waiting_in = [x for x in self.led.transfers if x["in_key"] and not x["out_key"]]
        # 1) 새 출금 × 새 입금
        pairs = []
        for ti, (o, dst) in enumerate(outs):
            for tj, (i, src) in enumerate(ins):
                if src == o.account and dst == i.account and o.amount == i.amount and abs((o.date - i.date).days) <= 1:
                    pairs.append((abs((o.date - i.date).days), o.date, ti, tj))
        pairs.sort()
        done_o, done_i = set(), set()
        for _, _, ti, tj in pairs:
            if ti in done_o or tj in done_i:
                continue
            done_o.add(ti)
            done_i.add(tj)
            o, dst = outs[ti]
            i, src = ins[tj]
            row = self.transfer_row(o.account, dst, o.date, o.amount)
            self.disp(o, "자동", f"이체 {o.account}→{dst}(양쪽 내역)", state="written", row=row,
                      route=self.route_of_row(row), rdate=o.date, partner=i.key, transfer=(o.account, dst))
            self.disp(i, "제외", f"이체 짝(입금 쪽 — 행은 출금 쪽 {o.date})", state="transfer_pair",
                      partner=o.key, transfer=(o.account, dst))
        # 2) 남은 입금 × 원장의 기다리는 출금 · 3) 남은 출금 × 원장의 기다리는 입금
        for tj, (i, src) in enumerate(ins):
            if tj in done_i:
                continue
            for w in waiting_out:
                if id(w) in used:
                    continue
                if w["src"] == src and w["dst"] == i.account and abs((dt.date.fromisoformat(w["date"]) - i.date).days) <= 1:
                    if w["amount"] == i.amount:
                        used.add(id(w))
                        done_i.add(tj)
                        self.disp(i, "제외", "이체 짝(원장의 기다리는 출금)", state="transfer_pair",
                                  partner=w["out_key"], transfer=(src, i.account), wait_fill="in_key")
                        break
        for ti, (o, dst) in enumerate(outs):
            if ti in done_o:
                continue
            for w in waiting_in:
                if id(w) in used:
                    continue
                if w["src"] == o.account and w["dst"] == dst and abs((dt.date.fromisoformat(w["date"]) - o.date).days) <= 1:
                    if w["amount"] == o.amount:
                        used.add(id(w))
                        done_o.add(ti)
                        self.disp(o, "제외", "이체 짝(원장의 기다리는 입금)", state="transfer_pair",
                                  partner=w["in_key"], transfer=(o.account, dst), wait_fill="out_key")
                        break
        # 4) 남은 출금
        for ti, (o, dst) in enumerate(outs):
            if ti in done_o:
                continue
            row = self.transfer_row(o.account, dst, o.date, o.amount)
            route = self.route_of_row(row)
            if self.conflicting_wait(o.account, dst, o.date, o.amount, waiting_in):
                self.ask(o, "transfer_conflict", row=row, route=route, dkw={"transfer": (o.account, dst)})
            elif dst in self.bank_src and self.account_covers(dst, o.date) and \
                    self.account_covers(dst, o.date + dt.timedelta(days=1)):
                self.ask(o, "transfer_noin", row=row, route=route, dkw={"transfer": (o.account, dst)})
            else:
                why = "제외 계좌" if dst not in self.bank_src else "상대 파일이 그 날짜를 안 덮음 — 원장에 기다림"
                self.disp(o, "자동", f"이체 {o.account}→{dst}(출금 쪽만 · {why})", state="written", row=row,
                          route=route, rdate=o.date, transfer=(o.account, dst))
        # 5) 남은 입금
        for tj, (i, src) in enumerate(ins):
            if tj in done_i:
                continue
            row = self.transfer_row(src, i.account, i.date, i.amount)
            route = self.route_of_row(row)
            if self.conflicting_wait(src, i.account, i.date, i.amount, waiting_out):
                self.ask(i, "transfer_conflict", row=row, route=route, dkw={"transfer": (src, i.account)})
            elif src not in self.bank_src:
                self.disp(i, "자동", f"이체 {src}→{i.account}(입금 쪽만 · 파일 없는 제외 계좌)", state="written",
                          row=row, route=route, rdate=i.date, transfer=(src, i.account))
            else:
                self.ask(i, "transfer_noout", row=row, route=route, note=f"제안 {src}",
                         dkw={"transfer": (src, i.account)})

    def conflicting_wait(self, src, dst, d, amount, waits):
        for w in waits:
            wd = dt.date.fromisoformat(w["date"])
            if abs((wd - d).days) <= 1 and w["amount"] == amount and (w["src"], w["dst"]) != (src, dst):
                return True
        return False

    # ---- 취소(3-0 5) ----
    def match_cancels(self):
        R = self.R
        pool = list(R.union.values()) + [t for k, t in self.led.tx.items() if k not in R.union and "#" not in k]
        used = set()
        for c in sorted(self.cancels, key=lambda t: (t.date, t.key)):
            s = self.src_by_id[c.source]
            if c.amount > 0 and c.approval and not any(
                    o.approval == c.approval and o.key != c.key for o in pool if o.source == c.source):
                # 원거래 행 자체가 '취소' 상태로 바뀐 꼴(행 하나) — 손 행이 있으면 삭제를 묻는다
                rec = self.find_hand(c, self.amount_after_points(c))
                if rec:
                    self.ask(c, "cancel_row", cands=[rec], csv_key=rec.key, note="상태가 취소인 거래")
                else:
                    self.disp(c, "제외", "취소된 거래(상태 취소 — 원거래와 한 행)", state="cancel_done")
                continue
            origs = []
            if c.approval:
                origs = [o for o in pool if o.source == c.source and o.approval == c.approval and not o.cancel
                         and o.key not in used]
            if not origs:
                # 승인번호가 없거나 원거래와 다른 출처(실물 신한카드) — 같은 가맹점·같은 금액·이전 날짜
                origs = [o for o in pool if o.source == c.source and not o.cancel and o.key not in used
                         and normalize_merchant(o.merchant) == normalize_merchant(c.merchant)
                         and o.amount == abs(c.amount) and o.date <= c.date]
            if not origs:
                self.ask(c, "cancel_orphan")
                continue
            if len(origs) > 1:
                self.ask(c, "cancel_ambiguous", note=f"후보 {len(origs)}건")
                continue
            o = origs[0]
            used.add(o.key)
            if abs(c.amount) != o.amount:
                self.ask(c, "cancel_partial", note=f"원거래 {o.date}")
                continue
            ost = self.led.state.get(o.key, {})
            if ost.get("state") == "sched" or o.key in self.led.schedule:
                # 할부 구매의 취소 — 둘 다 행 0, 일정을 끝내고 이미 적힌 회차 행은 삭제를 묻는다
                self.cancelled_sched.add(o.key)
                self.disp(o, "제외", "할부 구매 취소 — 일정 끝냄", state="cancel_done", partner=c.key)
                self.disp(c, "제외", "취소(할부 구매와 짝)", state="cancel_done", partner=o.key)
                continue
            if ost.get("state") in ("written", "paired") and ost.get("csv_key") in self.doc.by_key:
                rec = self.doc.by_key[ost["csv_key"]]
                self.ask(c, "cancel_row", cands=[rec], csv_key=rec.key, orig=o.key)
                if o.key in R.disps:
                    R.disps[o.key].status, R.disps[o.key].reason = "끝남", "원장: 취소 질문 중"
                continue
            rec = self.find_hand(o, self.amount_after_points(o))
            if rec:
                self.ask(c, "cancel_row", cands=[rec], csv_key=rec.key, orig=o.key)
                if o.key in R.cur:
                    self.disp(o, "제외", "취소된 원거래(취소 질문 중)", state="held", partner=c.key)
                continue
            if o.key in R.cur:
                self.disp(o, "제외", "취소된 원거래(취소 행과 짝)", state="cancel_done", partner=c.key)
            self.disp(c, "제외", "취소(원거래와 짝 — 둘 다 넣지 않음)", state="cancel_done", partner=o.key)

    def find_hand(self, t, amount):
        """원거래·할부 총액과 같은 경로·금액·±D의 손 행(아직 원장에 묶이지 않은 것)."""
        D = self.cfg["slack"]["card"]
        route = ("card", t.card, "지출")
        linked = self.led.linked_csv_keys()
        best = None
        for r in self.doc.recs:
            if r.route == route and r.amount == amount and r.date and r.key not in linked \
                    and abs((r.date - t.date).days) <= D:
                if best is None or abs((r.date - t.date).days) < abs((best.date - t.date).days):
                    best = r
        return best

    # ---- 할부(3-0 5) ----
    def pay_month(self, card, d):
        s = self.card_src[card]
        k = s.cycle_months_before if d.day >= s.cycle_start_day else s.cycle_months_before - 1
        return add_months(d.year, d.month, k)

    def pay_date(self, card, ym):
        return month_day(ym[0], ym[1], self.card_src[card].pay_day)

    def cycle_span(self, card, ym):
        s = self.card_src[card]
        y0, m0 = add_months(ym[0], ym[1], -s.cycle_months_before)
        start = month_day(y0, m0, s.cycle_start_day)
        y1, m1 = add_months(y0, m0, 1)
        end = month_day(y1, m1, s.cycle_start_day) - dt.timedelta(days=1)
        return start, end

    def schedule_rows(self, key, card, total, months, first_month, row, purchase=None):
        """할부 일정 → [(가상 거래키, 회차, 날짜, 금액)] — 실행일 이전 달까지만(미래 날짜 행 0)."""
        per = total // months
        out = []
        for k in range(1, months + 1):
            ym = add_months(first_month[0], first_month[1], k - 1)
            d = self.pay_date(card, ym)
            amt = per if k < months else total - per * (months - 1)
            out.append((f"{key}#{k}", k, d, amt))
        return out

    def plan_installments(self):
        R = self.R
        self.sched_rows = []      # (가상 Tx, 행) — 새 일정·원장 일정의 이번까지 회차
        self.sched_index = []     # 청구 회차 행 짝용: (카드, (연, 월), 금액, 가맹점 키, 가상 키)
        for t in self.installs:
            if t.key in R.disps:          # 취소와 짝지어진 할부 구매 — 일정 0
                continue
            s = self.src_by_id[t.source]
            total = self.amount_after_points(t)
            rec = self.find_hand(t, total)
            if rec:
                row = self.card_row(t, self.rule_values(t), total)
                self.ask(t, "inst_vs_hand", row=row, cands=[rec], csv_key=rec.key, months=t.inst_months)
                continue
            key = normalize_merchant(t.merchant)
            rule = self.rules.lookup(key)
            if rule and rule["mode"] == "자동" and t.merchant.strip() in rule["raws"] and \
                    not any(pk and pk in key for pk in self.cfg["platform_keys"]):
                row = self.card_row(t, rule["values"], total)
                self.disp(t, "제외", f"할부 {t.inst_months}개월 — 일정(규칙 '{key}')", state="sched", row=row,
                          rule_key=key)
                self.add_schedule(t.key, t.card, total, t.inst_months, self.pay_month(t.card, t.date), row, t)
            else:
                row = self.card_row(t, (rule or {}).get("values") or self.suggestion(key), total)
                self.ask(t, "new_merchant" if not rule else "ask_every", row=row, route=self.route_of_row(row),
                         note=f"할부 {t.inst_months}개월 — '할부'로 답하면 월별 행", months=t.inst_months,
                         rule_key=key)
        for key, sc in self.led.schedule.items():
            first = tuple(int(x) for x in sc["first_month"].split("-"))
            if sc.get("mode") == "cancelled" or key in self.cancelled_sched:
                self.ask_cancelled_rows(key, sc, first)
                continue
            self.add_schedule(key, sc["card"], sc["total"], sc["months"], first, sc["row"], None,
                              full=sc.get("mode") == "full")

    def ask_cancelled_rows(self, key, sc, first):
        """끝난(취소된) 할부 일정 — 새 회차 행 0. 이미 적힌 회차 행은 답할 때까지 'CSV 행 삭제?'를 묻는다."""
        for vkey, k, d, amt in self.schedule_rows(key, sc["card"], sc["total"], sc["months"], first, sc["row"]):
            st = self.led.state.get(vkey)
            if not st or st["state"] not in ("written", "paired") or st["csv_key"] not in self.doc.by_key:
                continue
            vt = self.virtual_tx(key, sc["card"], k, sc["months"], d, amt, None)
            self.R.cur[vkey] = vt
            rec = self.doc.by_key[st["csv_key"]]
            self.ask(vt, "cancel_row", cands=[rec], csv_key=rec.key, note=f"할부 취소 — 이미 적은 {k}회 행")

    def virtual_tx(self, key, card, k, months, d, amt, purchase):
        base = self.led.tx.get(key) or purchase
        return Tx(source=base.source if base else self.card_src[card].id, kind="card",
                  account=self.card_src[card].account, card=card, file="(할부 일정)", row=k, date=d, time="",
                  amount=amt, direction="spend", merchant=base.merchant if base else "", memo=(),
                  approval=base.approval if base else "", inst_seq=k, inst_total=months,
                  key=f"{key}#{k}", base=key, seq=k)

    def pair_billing_rows(self, extra=()):
        """파일의 청구 회차 행(같은 카드·같은 달·같은 금액·같은 가맹점)은 일정 회차와 반드시 짝(이중 0).
        일정 회차가 이번에 새로 생겼든 원장에 이미 적혔든, '전액' 답(full)이든 같다."""
        R = self.R
        used = set()
        for card, ym, amt, mkey, vkey in list(self.sched_index) + list(extra):
            if vkey in used:
                continue
            for t in sorted(R.cur.values(), key=lambda x: (x.date, x.key)):
                d = R.disps.get(t.key)
                if t.card != card or t.kind != "card" or t.cancel or "#" in t.key or t.amount != amt \
                        or (t.date.year, t.date.month) != ym or normalize_merchant(t.merchant) != mkey:
                    continue
                if not d or d.status not in ("자동", "확인 필요") or d.state == "inst_pair" or \
                        (d.item is not None and d.item.kind not in ("new_merchant", "ask_every", "platform",
                                                                    "raw_diff", "foreign")):
                    continue
                if d.item is not None and self.answers.get(d.item.id, {}).get("처리"):
                    continue
                used.add(vkey)
                self.disp(t, "제외", f"할부 청구 회차 행 — 일정 {vkey.rsplit('#', 1)[1]}회와 짝", state="inst_pair",
                          partner=vkey)
                break

    def rule_values(self, t):
        rule = self.rules.lookup(normalize_merchant(t.merchant))
        return (rule or {}).get("values") or self.suggestion(normalize_merchant(t.merchant))

    def add_schedule(self, key, card, total, months, first_month, row, purchase, full=False):
        """할부 일정 → 실행일까지의 회차 행. full(손 행 '전액' 답)이면 행은 내지 않고 청구 회차 행 짝에만 쓴다."""
        R = self.R
        for vkey, k, d, amt in self.schedule_rows(key, card, total, months, first_month, row):
            vt = self.virtual_tx(key, card, k, months, d, amt, purchase)
            self.sched_index.append((card, (d.year, d.month), amt, normalize_merchant(vt.merchant), vkey))
            if d > self.today or full:
                continue
            st = self.led.state.get(vkey)
            R.cur[vkey] = vt
            if st:
                if st["state"] in ("written", "paired") and st["csv_key"] and st["csv_key"] not in self.doc.by_key:
                    self.ask(vt, "gone_row", note=f"할부 {k}회 행 — CSV 행 키 {st['csv_key']}")
                    continue
                self.disp(vt, "끝남", f"원장: {st['state']}", state=st["state"], csv_key=st.get("csv_key", ""))
                continue
            r = dict(row)
            r.update({"날짜": d.isoformat(), "금액": amt, "구분": "지출", "비고": card})
            self.disp(vt, "자동", f"할부 {k}/{months}회(일정)", state="written", row=r,
                      route=self.route_of_row(r), rdate=d)

    # ---- 손 행 짝(3-0 4 CSV 겹) ----
    def pair_all(self):
        R = self.R
        D = self.cfg["slack"]
        linked = self.led.linked_csv_keys()
        lo = self.start - dt.timedelta(days=NEAR_DAYS)
        self.avail = defaultdict(list)
        self.odd = []
        for r in self.doc.recs:
            if r.blank or r.route is None or r.amount is None or r.key in linked:
                continue
            if r.date_state == "ok" and r.date >= lo:
                self.avail[(r.route, r.amount)].append(r)
            elif r.date_state in ("future", "bad", "blank"):
                self.odd.append(r)
        self.consumed = {}        # csv 키 → 거래키
        self.in_question = set()
        cands = [d for d in R.disps.values() if d.status in ("자동", "확인 필요") and d.route and d.row
                 and (d.item is None or d.item.kind in ("new_merchant", "ask_every", "platform", "raw_diff",
                                                         "empty_merchant", "income", "foreign", "dup_weak",
                                                         "transfer_unknown", "transfer_noin", "transfer_noout",
                                                         "transfer_conflict", "memo_conflict", "inst_mode"))]
        groups = defaultdict(list)
        for d in cands:
            groups[(d.route, int(d.row["금액"]))].append(d)
        # A) 거리 0 먼저 전부 소비
        rest = {}
        for g, ds in groups.items():
            rows = [r for r in self.avail.get(g, []) if r.key not in self.consumed]
            left = []
            for d in sorted(ds, key=lambda d: (d.rdate, d.tx.key)):
                hit = next((r for r in rows if r.date == d.rdate and r.key not in self.consumed), None)
                if hit:
                    self.consumed[hit.key] = d.tx.key
                    self.mark_paired(d, hit)
                else:
                    left.append(d)
            rest[g] = left
        # 취소 질문이 가리킨 손 행이 같은 날·경로·금액의 다른 거래와 짝이 됐으면 그 손 행은 그 거래의 것이다 —
        # 취소된 거래는 행 0, 삭제 질문 없음(실물 리허설: 취소 뒤 같은 날 같은 금액 재승인 — 3건 중 2건)
        for it in list(R.items):
            if it.kind == "cancel_row" and not it.extra.get("ledger_change") and it.tx is not None \
                    and "#" not in it.tx.key and it.cands and it.cands[0] is not None \
                    and it.cands[0].key in self.consumed:
                c, orig = it.tx, it.extra.get("orig", "")
                self.disp(c, "제외", "취소(손 행은 같은 날 다른 거래와 짝 — 삭제 질문 없음)", state="cancel_done",
                          partner=orig)
                od = R.disps.get(orig)
                if od is not None and od.status != "끝남":
                    self.disp(od.tx, "제외", "취소된 원거래(취소 행과 짝)", state="cancel_done", partner=c.key)
        # B·C) 남은 것: D 안은 확인 필요(짝 — 날짜 차) · 묶음 건수가 다르면 전부 확인 필요 · ±7일은 짝 의심
        for g, ds in rest.items():
            if not ds:
                continue
            slack = D["card"] if g[0][0] == "card" else D["bank"]
            rows = sorted((r for r in self.avail.get(g, []) if r.key not in self.consumed), key=lambda r: r.date)
            near = {d.tx.key: [r for r in rows if abs((r.date - d.rdate).days) <= slack] for d in ds}
            if any(near.values()):
                ds_sorted = sorted(ds, key=lambda d: d.rdate)
                if len(ds) == len(rows) and all(abs((r.date - d.rdate).days) <= slack
                                                for d, r in zip(ds_sorted, rows)):
                    for d, r in zip(ds_sorted, rows):
                        self.pair_question(d, "pair_gap", [r], note=f"날짜 차 {abs((r.date - d.rdate).days)}일")
                    continue
                for d in ds:
                    cand = [r for r in rows if abs((r.date - d.rdate).days) <= NEAR_DAYS]
                    if cand:
                        self.pair_question(d, "pair_amb", cand, note=f"묶음 거래 {len(ds)} · 손 행 {len(rows)}")
                continue
            for d in ds:
                cand = [r for r in rows if abs((r.date - d.rdate).days) <= NEAR_DAYS]
                if cand:
                    self.pair_question(d, "pair_suspect", cand)
        # D) 다른 경로 · 날짜 오타 손 행
        for d in cands:
            if d.status == "짝" or (d.item and d.item.kind.startswith("pair_")):
                continue
            slack = D["card"] if d.route[0] == "card" else D["bank"]
            if d.route[0] == "transfer":
                slack = max(slack, 1)     # 이체 손 행은 출금일·입금일 어느 쪽에도 적힌다
            amt = int(d.row["금액"])
            other = []
            for rt in self.other_routes(d.route):
                other += [r for r in self.avail.get((rt, amt), []) if r.key not in self.consumed
                          and abs((r.date - d.rdate).days) <= slack]
            if other:
                self.pair_question(d, "pair_route", other)
                continue
            odd = [r for r in self.odd if r.route == d.route and r.amount == amt]
            if odd:
                self.pair_question(d, "pair_future", odd)
        # 취소 질문·할부 질문이 가리키는 손 행은 '내역에 없는 CSV 행'으로 따로 묻지 않는다
        for it in R.items:
            for r in it.cands:
                if r is not None:
                    self.in_question.add(r.key)

    def other_routes(self, route):
        kind = route[0]
        out = []
        if kind == "card":
            card, gubun = route[1], route[2]
            out += [("card", c, gubun) for c in CARDS if c != card]
            out.append(("acct", self.card_src[card].account if card in self.card_src else "", gubun))
        elif kind == "acct":
            acct, gubun = route[1], route[2]
            out += [("card", c, gubun) for c, s in self.card_src.items() if s.account == acct]
            out += [("acct", a, gubun) for a in self.bank_src if a != acct]
        elif kind == "transfer":
            # 옮긴 돈을 손으로 '받은 계좌 수입'·'보낸 계좌 지출'로 적어 온 행(실물 리허설 — 신한은행에 들어와
            # 생활비로 옮긴 돈을 '생활비 수입'으로 적음). 이체 행을 더하면 그 계좌에 두 번 잡힌다
            src, dst = route[1], route[2]
            out += [("acct", dst, "수입"), ("acct", src, "지출")]
        return out

    def mark_paired(self, d, rec):
        R = self.R
        d.status, d.state, d.csv_key = "짝", "paired", rec.key
        d.reason = f"CSV {rec.no}행과 짝(같은 날·같은 경로·같은 금액)"
        if d.item is not None:
            R.items = [x for x in R.items if x is not d.item]
            d.item = None
        key = normalize_merchant(d.tx.merchant)
        if key and rec.route and rec.route[0] != "transfer":
            self.hand_suggest.setdefault(key, {c: rec.v[c] for c in ("대분류", "소분류", "세부내용", "고정여부", "결제수단")})

    def pair_question(self, d, kind, recs, note=""):
        R = self.R
        for r in recs:
            self.in_question.add(r.key)
        extra = {}
        if d.item is not None:
            extra = {k: v for k, v in d.item.extra.items() if k not in ("csv_key",)}
            extra["was"] = d.item.kind
            R.items = [x for x in R.items if x is not d.item]
        it = self.item(kind, t=d.tx, row=d.row, cands=recs, note=note, **extra)
        d.status, d.reason, d.state, d.item = "확인 필요", it.reason, "held", it

    # ---- 내역에 없는 CSV 행 · 사라진 행 ----
    def csv_only(self):
        R = self.R
        cur_cards = {s.card: s for s in self.cfg["sources"] if s.kind == "card"}
        for g, rows in self.avail.items():
            for r in rows:
                if r.key in self.consumed or r.key in self.in_question or r.date < self.start or r.date > self.today:
                    continue
                note = self.led.csvnote.get("csv:" + r.key)
                if note and note["state"] == "explained":
                    continue
                rt = r.route
                covered = False
                if rt[0] == "card" and rt[1] in cur_cards:
                    covered = self.covers(cur_cards[rt[1]].id, r.date, current_only=True)
                elif rt[0] == "acct":
                    covered = any(self.covers(s.id, r.date, current_only=True) for s in self.bank_src.get(rt[1], []))
                elif rt[0] == "transfer":
                    covered = any(self.covers(s.id, r.date, current_only=True)
                                  for a in (rt[1], rt[2]) for s in self.bank_src.get(a, []))
                if covered:
                    self.item("csv_only", rec=r)

    # ---- 카드대금 대조(3-0 5) ----
    def card_payment_checks(self):
        R = self.R
        alltx = list(R.union.values())
        sched_by_month = defaultdict(int)
        for k, t in R.cur.items():
            if "#" in k:
                sched_by_month[(t.card, (t.date.year, t.date.month))] += t.amount
        seen_months = defaultdict(set)
        for t, card in self.cardpays:
            s = self.card_src.get(card)
            if s is None:
                continue
            best = min((add_months(t.date.year, t.date.month, k) for k in (-1, 0, 1)),
                       key=lambda ym: abs((self.pay_date(card, ym) - t.date).days))
            seen_months[card].add(best)
            lo, hi = self.cycle_span(card, best)
            covered = all(self.covers(s.id, lo + dt.timedelta(days=i))
                          for i in range((hi - lo).days + 1))
            rows = [x for x in alltx if x.source == s.id and lo <= x.date <= hi]
            spend = sum(self.amount_after_points(x) for x in rows if not x.cancel
                        and not (s.installment_mode == "total" and x.inst_months >= 2))
            cancel = sum(-abs(x.amount) for x in rows if x.cancel and x.amount < 0)
            inst = sched_by_month.get((card, best), 0)
            expected = spend + cancel + inst
            edge = sum(self.amount_after_points(x) for x in alltx if x.source == s.id and not x.cancel and (
                0 < (x.date - hi).days <= 3 or 0 < (lo - x.date).days <= 3))
            chk = {"card": card, "pay_month": f"{best[0]}-{best[1]:02d}", "date": t.date.isoformat(),
                   "paid": t.amount, "expected": expected, "diff": t.amount - expected, "covered": covered,
                   "span": (lo.isoformat(), hi.isoformat()), "spend": spend, "cancel": cancel, "inst": inst,
                   "edge": edge, "points": sum(x.points or 0 for x in rows), "key": t.key}
            chk["ok"] = covered and chk["diff"] == 0
            R.card_checks.append(chk)
        # 카드 행이 있는 달인데 그 청구의 카드대금이 (청구 계좌 파일이 결제일을 덮는데) 없다
        for card, s in self.card_src.items():
            months = set()
            for k, t in R.cur.items():
                if t.source == s.id and not t.cancel and "#" not in k:
                    months.add(self.pay_month(card, t.date))
            for ym in sorted(months):
                pd_ = self.pay_date(card, ym)
                if ym in seen_months[card] or pd_ + dt.timedelta(days=3) > self.today:
                    continue
                if not self.account_covers(s.account, pd_ + dt.timedelta(days=3)) or \
                        not self.account_covers(s.account, pd_ - dt.timedelta(days=3)):
                    continue
                iid = f"cardpay:{card}:{ym[0]}-{ym[1]:02d}"
                if self.led.csvnote.get(iid, {}).get("state") == "explained":
                    continue
                self.item("cardpay_missing", iid=iid, note=f"{card} {ym[0]}-{ym[1]:02d} 결제일 {pd_}")

    # ---- 답(3-0 3·6) ----
    def apply_answers(self):
        R = self.R
        A = self.answers
        self.answered = {}
        self.answer_used = set()
        self.dropped_answers = 0
        for it in R.items:
            a = A.get(it.id)
            if not a or not a.get("처리"):
                continue
            err = self.validate_answer(it, a)
            if err:
                if self.opts.get("lenient"):
                    # 지난 회차 답을 이어받을 때 이번 항목에 맞지 않는 답은 버린다(다시 묻는다)
                    self.dropped_answers += 1
                    continue
                R.answer_errors.append(f"{it.id}: {err}")
                continue
            if a["처리"] in ("짝", "고친 행"):
                self.answer_used.add(self.answer_rec(a).key)
            self.answered[it.id] = a
        if self.dropped_answers:
            R.notes.append(f"지난 회차 답 {self.dropped_answers}건은 이번 항목과 맞지 않아 버렸다(다시 묻는다)")

    def validate_answer(self, it, a):
        opts = KINDS[it.kind][1]
        act = a.get("처리", "")
        if act not in opts:
            return f"처리 '{act}'는 이 항목의 선택지 {opts}가 아니다"
        if act in ("추가", "할부"):
            row = self.answer_row(it, a)
            if row is None:
                return "추가할 행의 날짜·금액을 정할 수 없다"
            if row["구분"] not in ("수입", "지출", "이체"):
                return "구분은 수입/지출/이체"
            new_ok = a.get("새 이름", "").upper() in ("Y", "새", "예")
            for c in ("대분류", "소분류", "세부내용"):
                if row["구분"] == "이체" and c == "소분류":
                    continue
                if row[c] and row[c] not in self.doc.names[c] and not new_ok:
                    return f"{c} '{row[c]}'는 CSV에 없는 이름 — 새 이름이면 '새 이름' 칸에 Y"
            if row["결제수단"] not in ACCOUNTS:
                return f"결제수단 '{row['결제수단']}'는 계좌명({', '.join(ACCOUNTS)})이 아니다"
            if row["고정여부"] not in ("고정", ""):
                return "고정여부는 '고정' 또는 빈칸"
            if row["비고"] not in CARDS + [""]:
                return f"비고는 카드명({', '.join(CARDS)}) 또는 빈칸"
            if row["구분"] == "이체" and (row["소분류"] not in ACCOUNTS or row["소분류"] == row["결제수단"]):
                return "이체의 소분류(입금 계좌)는 결제수단과 다른 계좌명"
            if row["구분"] == "이체" and row["비고"]:
                return "이체 행의 비고는 빈칸"
            if a.get("금액(해외만)") and it.kind != "foreign":
                return "금액은 해외 결제 항목에서만 바꾼다"
            if act == "할부":
                try:
                    if int(a.get("할부 개월", "0")) < 2:
                        raise ValueError
                except ValueError:
                    return "할부 개월은 2 이상 정수"
                if it.tx is None or it.tx.kind != "card":
                    return "할부는 카드 거래에만"
        if act in ("짝", "고친 행"):
            rec = self.answer_rec(a)
            if rec is None:
                return "짝 CSV 행(행 번호)이 없거나 못 찾는다"
            if rec.key in self.led.linked_csv_keys() or rec.key in self.consumed or rec.key in self.answer_used:
                return f"CSV {rec.no}행은 이미 다른 거래와 짝이다"
            if rec.blank or rec.route is None:
                return f"CSV {rec.no}행은 경로(결제수단·비고)가 없는 행이다"
        if act == "삭제" and not (it.extra.get("csv_key") in self.doc.by_key):
            return "지울 CSV 행이 없다"
        if act == "월별" and it.extra.get("csv_key") not in self.doc.by_key:
            return "지울 손 행이 없다"
        if act == "삭제" and it.kind == "vanished_sched":
            return "할부 일정은 '일정 끝냄'으로 답한다"
        return ""

    def answer_rec(self, a):
        v = str(a.get("짝 CSV 행", "")).strip()
        if v.isdigit():
            return self.doc.by_no.get(int(v))
        return self.doc.by_key.get(v)

    def answer_row(self, it, a):
        base = dict(it.row) if it.row else None
        t = it.tx
        if base is None:
            if t is None:
                return None
            base = (self.card_row(t, {}, self.amount_after_points(t)) if t.kind == "card"
                    else self.bank_row(t, "지출" if t.direction == "out" else "수입", {}))
        row = dict(base)
        for c in ("구분", "대분류", "소분류", "세부내용", "고정여부", "결제수단", "비고"):
            if c in a and a[c] is not None:
                row[c] = str(a[c]).strip()
        if a.get("금액(해외만)"):
            try:
                row["금액"] = int(str(a["금액(해외만)"]).replace(",", ""))
            except ValueError:
                return None
        if row["구분"] != "이체" and t is not None and t.kind == "card" and not row["비고"]:
            row["비고"] = t.card
        return row

    # ---- 새 행 · 지울 행 · 초안 ----
    def make_draft(self):
        R = self.R
        rows, deletes = [], set()
        self.row_origin = []      # (행, 거래키 또는 항목 id)
        # 답으로 만드는 일정('할부'·'월별'·'전액')도 같은 회차의 청구 회차 행과 먼저 짝짓는다(이중 0)
        extra = []
        for it in R.items:
            a = self.answered.get(it.id)
            if not a or it.tx is None or a["처리"] not in ("할부", "월별", "전액"):
                continue
            t = it.tx
            if a["처리"] == "할부":
                total, months = int(self.answer_row(it, a)["금액"]), int(a["할부 개월"])
            else:
                total, months = int(it.row["금액"]), int(it.extra.get("months") or 0)
            if months < 2:
                continue
            for vkey, k, d, amt in self.schedule_rows(t.key, t.card, total, months, self.pay_month(t.card, t.date),
                                                      it.row or {}):
                extra.append((t.card, (d.year, d.month), amt, normalize_merchant(t.merchant), vkey))
        if extra:
            self.pair_billing_rows(extra)
        for k, d in sorted(R.disps.items(), key=lambda kv: kv[0]):
            if d.status == "자동" and d.row:
                rows.append(d.row)
                self.row_origin.append((d.row, k))
        for it in R.items:
            a = self.answered.get(it.id)
            if not a:
                continue
            act = a["처리"]
            if act == "추가":
                row = self.answer_row(it, a)
                rows.append(row)
                self.row_origin.append((row, it.id))
            elif act in ("삭제", "월별"):
                deletes.add(it.extra.get("csv_key"))
            elif act == "할부":
                t = it.tx
                n = int(a["할부 개월"])
                row = self.answer_row(it, a)
                for vkey, k, d, amt in self.schedule_rows(t.key, t.card, row["금액"], n,
                                                          self.pay_month(t.card, t.date), row):
                    if d <= self.today:
                        r = dict(row)
                        r.update({"날짜": d.isoformat(), "금액": amt})
                        rows.append(r)
                        self.row_origin.append((r, vkey))
        if any(it.kind == "inst_vs_hand" and self.answered.get(it.id, {}).get("처리") == "월별" for it in R.items):
            for it in R.items:
                if it.kind == "inst_vs_hand" and self.answered.get(it.id, {}).get("처리") == "월별":
                    t = it.tx
                    for vkey, k, d, amt in self.schedule_rows(t.key, t.card, it.row["금액"], it.extra["months"],
                                                              self.pay_month(t.card, t.date), it.row):
                        if d <= self.today:
                            r = dict(it.row)
                            r.update({"날짜": d.isoformat(), "금액": amt})
                            rows.append(r)
                            self.row_origin.append((r, vkey))
        order = sorted(range(len(rows)), key=lambda i: (rows[i]["날짜"], rows[i]["구분"], str(rows[i]["결제수단"]),
                                                        str(rows[i]["비고"]), int(rows[i]["금액"])))
        R.new_rows = [rows[i] for i in order]
        self.row_origin = [self.row_origin[i] for i in order]
        R.deletes = deletes
        R.draft, self.new_nos = self.doc.build(R.new_rows, deletes)
        self.pre_write_checks()

    def pre_write_checks(self):
        """3-0 9 — load_csv 읽힘 · 행 수 · 옛 줄 부분열 · 경고 종류별 건수 비증가 · 계좌 집합."""
        R = self.R
        c = {}
        with tempfile.TemporaryDirectory(prefix="pcimport-") as td:
            old_p, new_p = os.path.join(td, "old.csv"), os.path.join(td, "new.csv")
            with open(old_p, "wb") as f:
                f.write(self.doc.data)
            with open(new_p, "wb") as f:
                f.write(R.draft)
            try:
                with redirect_stdout(io.StringIO()):
                    new_df = pl.load_csv(new_p, today=self.today)
                c["load_csv"] = True
            except Exception as e:  # noqa: BLE001
                c["load_csv"] = f"{type(e).__name__}: {e}"
                new_df = None
            try:
                with redirect_stdout(io.StringIO()):
                    old_df = pl.load_csv(old_p, today=self.today)
                old_w = warning_counts(pl.build_bundle(old_df))
            except Exception as e:  # noqa: BLE001
                old_w, c["old_bundle"] = None, f"{type(e).__name__}: {e}"
            new_w = None
            if new_df is not None:
                try:
                    new_w = warning_counts(pl.build_bundle(new_df))
                except Exception as e:  # noqa: BLE001
                    c["new_bundle"] = f"{type(e).__name__}: {e}"
            self.new_df = new_df
        nd = CsvDoc.__new__(CsvDoc)
        text = R.draft.decode("utf-8-sig")
        recs = split_records(text)
        body = [t for t, _ in recs[1:]]
        old_body = [r.text for r in self.doc.recs if r.key not in R.deletes]
        it = iter(body)
        c["subsequence"] = all(any(x == y for y in it) for x in old_body)
        c["header_same"] = recs[0][0] == self.doc.header_text
        c["rows"] = (len([x for x in body]) == len(self.doc.recs) + len(R.new_rows) - len(R.deletes))
        c["deletes_found"] = all(k in self.doc.by_key for k in R.deletes)
        bad_acct = [r for r in R.new_rows if r["결제수단"] not in ACCOUNTS or r["비고"] not in CARDS + [""]
                    or (r["구분"] == "이체" and r["소분류"] not in ACCOUNTS)]
        c["accounts"] = not bad_acct
        R.warn_up = {}
        if old_w is not None and new_w is not None:
            R.warn_up = {k: (old_w.get(k, 0), v) for k, v in new_w.items() if v > old_w.get(k, 0)}
        c["warnings"] = {"old": old_w, "new": new_w}
        R.checks = c

    # ---- 잔액 대조(3-0 8) ----
    def balance_checks(self):
        R = self.R
        if self.new_df is None:
            return
        rows_by_link = self.csv_links()
        sched_marks = {(t.card, t.date, t.amount) for k, t in R.cur.items() if "#" in k}
        for acct, srcs in sorted(self.bank_src.items()):
            ids = {s.id for s in srcs}
            txs = [t for t in R.union.values() if t.source in ids]
            txs += [t for k, t in self.led.tx.items() if t.source in ids and k not in R.union]
            if not txs:
                R.balance.append({"account": acct, "status": "생략(파일이 한 번도 없던 계좌)"})
                continue
            with_bal = [t for t in txs if t.balance is not None]
            if not with_bal:
                R.balance.append({"account": acct, "status": "불가(잔액 열 없음)"})
                continue
            chain = None
            for f in self.parsed:
                if f["src"].id in ids and f["chain"]:
                    chain = f["chain"] if chain is None or f["chain"][-1].date >= chain[-1].date else chain
            if chain:
                last = chain[-1]
            else:   # 원장 앵커: 가장 늦게 끝나는 파일의 시간순 마지막 줄
                by_file = defaultdict(list)
                for t in with_bal:
                    by_file[t.file].append(t)
                lastfile = max(by_file.values(), key=lambda ts: max(t.date for t in ts))
                last = max(lastfile, key=lambda t: (t.date, t.order))
            E, B = last.date, last.balance
            long = LONG_NAME[acct]
            as_of = dt.date.fromisoformat(pl.ACCOUNTS_START[long]["as_of"])
            bal, _ = pl.account_balance(self.new_df, long, until=E)
            file_start = min(t.date for t in txs)
            terms = defaultdict(int)
            df = self.new_df
            sub = df[(df["날짜"].dt.date > as_of) & (df["날짜"].dt.date <= E)]
            for _, r in sub.iterrows():
                card, pay = r["비고"], r["결제수단"]
                if r["구분"] != "지출" or card not in self.card_src:
                    continue
                billing = self.card_src[card].account
                d = r["날짜"].date()
                amt = int(round(r["금액"]))
                if (card, d, amt) in sched_marks:
                    paid = d <= E
                else:
                    paid = self.is_paid(card, d, E, file_start)
                if pay == acct and billing == acct and not paid:
                    terms["미결제 카드(+)"] += amt
                elif pay == acct and billing != acct:
                    terms["다른 계좌 청구 카드 상계(+)"] += amt
                elif billing == acct and pay != acct and pay in ACCOUNTS and paid:
                    terms["이 계좌가 낸 다른 결제수단 카드(−)"] -= amt
            for b in self.cfg["pre_asof_billing"]:
                if b["account"] == acct and b["card"] in self.card_src:
                    y, m = (int(x) for x in b["pay_month"].split("-"))
                    pdd = self.pay_date(b["card"], (y, m))
                    if as_of < pdd <= E:
                        terms["as_of 이전 구매분 청구(−)"] -= b["amount"]
            # 통장 밖 칸(세이프박스 등): 파일 잔액은 칸을 뺀 값, 리포트 잔고는 칸까지 합친 값이다
            for ib in self.cfg["internal"]:
                if ib["account"] != acct:
                    continue
                base = next((b for b in self.cfg["internal_balance"]
                             if b["account"] == acct and b["name"] == ib["name"]), None)
                lo = base["date"] if base else as_of
                moved = sum((t.amount if t.direction == "out" else -t.amount) for t in txs
                            if lo < t.date <= E and (R.disps.get(t.key) and R.disps[t.key].state == "internal"
                                                     or self.led.state.get(t.key, {}).get("state") == "internal"))
                label = f"{ib['name']} 칸 잔액(−)" + ("" if base else " — 시작 잔액 미입력(기준 차액에 남음)")
                terms[label] -= moved + (base["amount"] if base else 0)
            for t in txs:
                if not (as_of < t.date <= E) or t.date < self.start:
                    continue
                d = R.disps.get(t.key)
                st = d.state if d else self.led.state.get(t.key, {}).get("state", "")
                if st in ("card_payment", "internal") or (d is None and st in ("written", "paired", "transfer_pair",
                                                                                "kept_missing")):
                    continue
                if d is not None and self.has_row(d):
                    continue
                sign = -1 if t.direction == "out" else 1
                terms["보류·확인 필요·제외(입금 + / 출금 −)"] += sign * t.amount
            for card, s in self.card_src.items():
                if s.account != acct:
                    continue
                for t in R.cur.values():
                    if t.source != s.id or t.cancel or "#" in t.key or not (as_of < t.date):
                        continue
                    d = R.disps.get(t.key)
                    if d and not self.has_row(d) and d.state not in ("cancel_done", "sched", "inst_pair",
                                                                     "points_full", "suppressed") \
                            and self.is_paid(card, t.date, E, file_start):
                        terms["행 없는 카드 거래 중 청구된 것(−)"] -= self.amount_after_points(t)
            unb = 0
            for r in self.doc_draft_recs():
                if r["date"] is None or not (max(file_start, self.start) <= r["date"] <= E) or r["date"] <= as_of:
                    continue
                if r["key"] in rows_by_link.get(acct, set()):
                    continue
                v = r["v"]
                if v["비고"] in self.card_src:
                    continue
                if v["구분"] in ("지출", "수입") and v["결제수단"] == acct:
                    unb += r["amount"] if v["구분"] == "지출" else -r["amount"]
                elif v["구분"] == "이체" and acct in (v["결제수단"], v["소분류"]):
                    unb += r["amount"] if v["결제수단"] == acct else -r["amount"]
            terms["내역과 짝 없는 CSV 행(지출 + / 수입 −)"] = unb
            diff = B - bal
            explained = sum(terms.values())
            resid = diff - explained
            anc = self.led.anchor.get(acct)
            ent = {"account": acct, "status": "", "end_date": E.isoformat(), "file_balance": B, "csv_balance": bal,
                   "diff": diff, "terms": dict(terms), "residual": resid,
                   "baseline": anc["baseline"] if anc else None, "from_ledger": not any(
                       f["src"].id in ids for f in self.parsed)}
            if anc is None:
                ent["status"] = "통과(잔차 0)" if resid == 0 else "기준 차액 필요(첫 회차 — --ack-balance로 사유)"
                ent["ok"] = resid == 0
            else:
                ent["ok"] = resid == anc["baseline"]
                ent["status"] = "통과(기준 차액 그대로)" if ent["ok"] else "차액 변화가 설명되지 않는다"
                if not ent["ok"] and anc.get("reason", "").endswith(f"[잔차 {resid}]"):
                    ent["status"] += " — 같은 차액 반복: 공식 오류 의심"
            R.balance.append(ent)

    def has_row(self, d):
        """이 거래를 받치는 CSV 행이 새 CSV 초안에 있나(자동 행·손 행 짝·답으로 추가·이체 짝)."""
        if d.status in ("자동", "짝"):
            return True
        if d.status == "끝남":
            return d.state in ("written", "paired", "transfer_pair", "kept_missing")
        if d.state == "transfer_pair":
            return True
        a = self.answered.get(d.item.id) if d.item is not None else None
        if d.item is not None and d.item.kind in ("vanished", "changed", "gone_row"):
            return not (a and a["처리"] in ("삭제", "지운 것 맞음", "다시 넣기"))
        return bool(a and a["처리"] in ("추가", "짝", "고친 행", "전액", "유지"))

    def is_paid(self, card, d, E, file_start):
        ym = self.pay_month(card, d)
        nominal = self.pay_date(card, ym)
        for c in self.R.card_checks:
            if c["card"] == card and c["pay_month"] == f"{ym[0]}-{ym[1]:02d}":
                return dt.date.fromisoformat(c["date"]) <= E
        return nominal < file_start

    def doc_draft_recs(self):
        if not hasattr(self, "_draft_recs"):
            text = self.R.draft.decode("utf-8-sig")
            recs = split_records(text)
            header = [c.strip() for c in parse_fields(recs[0][0])]
            out, counts = [], {}
            import pandas as pd
            vals, raw_dates = [], []
            di = header.index("날짜")
            for t, _ in recs[1:]:
                raw = parse_fields(t)
                f = [x.strip() for x in raw]
                vals.append({c: (f[j] if j < len(f) else "") for j, c in enumerate(header)})
                raw_dates.append(raw[di] if di < len(raw) and raw[di].strip() else None)
            dated = [x for x in raw_dates if x is not None]
            parsed = iter(pd.to_datetime(pd.Series(dated, dtype=object), errors="coerce"))
            dates = [next(parsed) if x is not None else pd.NaT for x in raw_dates]
            for v, d in zip(vals, dates):
                if not any(v.values()):
                    continue
                try:
                    amt = int(round(pl.parse_amount(v["금액"])))
                except ValueError:
                    amt = None
                dd = None if pd.isna(d) else d.date()
                dpart = dd.isoformat() if dd else "?" + v["날짜"]
                apart = str(amt) if amt is not None else "?" + v["금액"]
                base = row_key(dpart, v, apart, 0)
                counts[base] = counts.get(base, 0) + 1
                out.append({"key": row_key(dpart, v, apart, counts[base]), "v": v, "date": dd,
                            "amount": amt or 0})
            self._draft_recs = out
        return self._draft_recs

    def csv_links(self):
        """새 CSV 초안의 행 키 → 그 행을 받치는 통장(거래가 있는 계좌) — 잔액 대조의 '짝 없는 CSV 행'용."""
        R = self.R
        links = defaultdict(set)
        new_keys = self.new_row_keys()
        for k, d in R.disps.items():
            t = d.tx
            if t.kind != "bank":
                continue
            ck = d.csv_key if d.status in ("짝", "끝남") else new_keys.get(k, "")
            if d.state == "transfer_pair" and d.partner:
                pd_ = R.disps.get(d.partner)
                ck = (pd_.csv_key if pd_ and pd_.status in ("짝", "끝남") else new_keys.get(d.partner, "")) or \
                    next((x["csv_key"] for x in self.led.transfers if d.partner in (x["out_key"], x["in_key"])), "")
            if ck:
                links[t.account].add(ck)
        for x in self.led.transfers:
            for side, acct in (("out_key", x["src"]), ("in_key", x["dst"])):
                if x[side] and x["csv_key"]:
                    links[acct].add(x["csv_key"])
        for it in R.items:
            a = self.answered.get(it.id)
            if a and a["처리"] in ("짝",) and it.tx is not None and it.tx.kind == "bank":
                rec = self.answer_rec(a)
                if rec:
                    links[it.tx.account].add(rec.key)
        return links

    def new_row_keys(self):
        """새 행(거래키·항목 id) → 새 CSV에서의 행 키."""
        if hasattr(self, "_new_keys"):
            return self._new_keys
        recs = {r["key"]: r for r in self.doc_draft_recs()}
        nos = {}
        all_recs = self.doc_draft_recs()
        n_old = len([r for r in self.doc.recs[: self.doc.tail_start] if r.key not in self.R.deletes and r.key])
        # 새 행은 초안에서 옛 데이터 행 다음에 순서대로 온다
        old_nonblank = [r for r in self.doc.recs[: self.doc.tail_start] if r.key not in self.R.deletes and not r.blank]
        new_part = all_recs[len(old_nonblank): len(old_nonblank) + len(self.R.new_rows)]
        for (row, origin), rr in zip(self.row_origin, new_part):
            key = origin[3:] if origin.startswith("tx:") else origin
            nos[key] = rr["key"]
        self._new_keys = nos
        del recs, n_old
        return nos

    # ---- apply 막힘 ----
    def final_blocks(self):
        R = self.R
        skip = set(self.opts.get("skip", []))
        acks = self.opts.get("acks", {})
        for f in R.failures:
            if not (f["source"] in skip or f["file"] in skip):
                R.blocked.append(f"출처 실패: {f['file']}" + (f" ({f['source']})" if f["source"] else "")
                                 + f" — {f['reason']}  [--skip {f['source'] or f['file']}]")
        bad_skip = [x for x in skip if not any(x in (f["source"], f["file"]) for f in R.failures)]
        for x in bad_skip:
            R.blocked.append(f"--skip {x}: 실패한 출처·파일이 아니다(--skip은 '실패'에만 쓴다)")
        for g in R.gaps:
            if g["source"] not in acks.get("gap", {}):
                R.blocked.append(f"공백: {g['source']} {g['from']}~{g['to']} 파일이 없다 — 취소·누락을 못 본다"
                                 f"  [--ack-gap {g['source']} <사유>]")
        for c in R.card_checks:
            if not c["ok"] and c["card"] not in acks.get("card", {}):
                why = "이용기간을 파일이 다 덮지 않아 대조 불가" if not c["covered"] else f"차이 {c['diff']:,}원"
                R.blocked.append(f"카드대금 {c['card']} {c['pay_month']}: {why}  [--ack-card {c['card']} <사유>]")
        for b in R.balance:
            if b.get("ok") is False and b["account"] not in acks.get("balance", {}):
                R.blocked.append(f"잔액 대조 {b['account']}: {b['status']} (잔차 {b['residual']:,} · 기준 "
                                 f"{b['baseline'] if b['baseline'] is not None else '없음'})"
                                 f"  [--ack-balance {b['account']} <사유>]")
        c = R.checks
        for k in ("load_csv", "subsequence", "header_same", "rows", "deletes_found", "accounts"):
            if c.get(k) is not True:
                R.blocked.append(f"쓰기 전 검사 {k}: {c.get(k)}")
        for k in ("old_bundle", "new_bundle"):
            if c.get(k):
                R.blocked.append(f"쓰기 전 검사 {k}: {c[k]}")
        for k, (o, n) in R.warn_up.items():
            if k not in acks.get("warning", {}):
                R.blocked.append(f"경고 '{k}' 건수 증가 {o} → {n}  [--ack-warning {k} <사유>]")
        for it in R.items:
            if KINDS[it.kind][3] and self.answered.get(it.id, {}).get("처리") not in ("삭제", "유지"):
                R.blocked.append(f"답 필요(막음): {it.id} — {it.reason}")
        for e in R.answer_errors:
            R.blocked.append(f"답 오류(되묻기): {e}")
        unexplained = [k for k, t in R.cur.items() if k not in R.disps]
        if unexplained:
            R.blocked.append(f"설명 안 된 거래 {len(unexplained)}건(엔진 오류) — {unexplained[:3]}")


def warning_counts(bundle):
    """warnings_for의 종류별 건수(3-0 9)."""
    d = bundle.get("diagnostics", {})
    ld = d.get("load", {})
    accts = bundle.get("accounts", {}).get("list", [])
    return {
        "bad_dates": ld.get("bad_dates", 0), "future_rows": ld.get("future_rows", 0),
        "unknown_types": ld.get("unknown_type_rows", 0), "dup_rows": ld.get("dup_rows", 0),
        "unknown_payment": len(d.get("unknown_payment", [])),
        "card_in_payment": (d.get("card_in_payment") or {}).get("rows", 0),
        "blank_payment": d.get("blank_payment_after_start", 0),
        "unknown_memo": d.get("unknown_memo_rows", 0), "unknown_transfer": len(d.get("unknown_transfer", [])),
        "negative_rows": d.get("negative_rows", 0), "zero_rows": d.get("zero_rows", 0),
        "transfer_issues": len(d.get("transfer_issues", [])),
        "stale_rule_keys": len(d.get("stale_rule_keys", [])),
        "negative_balance": sum(1 for a in accts if a["current_balance"] < 0),
        "no_tx_account": sum(1 for a in accts if a.get("tx_count", 0) == 0),
        "card_zero": sum(1 for c in bundle.get("card_performance", {}).get("cards", []) if c["spent"] == 0),
        "shinhan_unmatched": len(bundle.get("shinhan_unmatched", [])),
    }


# ------------------------------------------------------------
# 회차 폴더 · 출력
# ------------------------------------------------------------

def run_key(name):
    m = RUN_NAME.match(name)
    return (m.group(1), int(m.group(2) or 1)) if m else None


def run_dirs(P):
    if not os.path.isdir(P.runs):
        return []
    return sorted((n for n in os.listdir(P.runs) if run_key(n) and os.path.isdir(os.path.join(P.runs, n))),
                  key=run_key)


def new_run_dir(P):
    os.makedirs(P.runs, exist_ok=True)
    base, n = now_kst().strftime("%Y%m%d-%H%M%S"), 1
    existing = run_dirs(P)
    if existing and run_key(existing[-1]) >= (base, 1):
        base, n = run_key(existing[-1])
        n += 1
    while True:
        name = base if n == 1 else f"{base}-{n}"
        path = os.path.join(P.runs, name)
        try:
            os.mkdir(path)
            return path
        except FileExistsError:
            n += 1


def read_answers(path):
    """확인필요.xlsx → {항목 id: {열: 값}} (처리 칸이 빈 줄도 담는다 — 제안 값 이어받기용)."""
    import openpyxl
    if not os.path.exists(path):
        return {}
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb["확인 필요"]
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()
    if not rows:
        return {}
    head = [str(h or "").strip() for h in rows[0]]
    out = {}
    for r in rows[1:]:
        d = {h: ("" if v is None else str(v).strip()) for h, v in zip(head, r)}
        if d.get("항목"):
            out[d["항목"]] = {c: d.get(c, "") for c in ANSWER_COLS}
    return out


def item_rows(engine):
    R = engine.R
    out = []
    for n, it in enumerate(sorted(R.items, key=lambda x: (x.tx.date.isoformat() if x.tx else
                                                          (x.rec.date.isoformat() if x.rec and x.rec.date else ""),
                                                          x.id)), start=1):
        t, rec = it.tx, it.rec
        row = it.row or {}
        info = {
            "번호": n, "항목": it.id, "종류": it.kind, "사유": it.reason,
            "출처": t.source if t else ("CSV" if rec else ""),
            "날짜": (t.date.isoformat() if t else (rec.v["날짜"] if rec else "")),
            "금액": (t.amount if t else (rec.amount if rec else "")),
            "가맹점·적요": (" / ".join(x for x in (t.merchant, *t.memo) if x) if t else
                        (" / ".join(rec.v[c] for c in ("대분류", "소분류", "세부내용") if rec.v[c]) if rec else "")),
            "후보 CSV 행": ", ".join(str(r.no) for r in it.cands if r is not None),
        }
        ans = {c: "" for c in ANSWER_COLS}
        for c in ("구분", "대분류", "소분류", "세부내용", "고정여부", "결제수단", "비고"):
            ans[c] = row.get(c, "")
        if it.extra.get("months"):
            ans["할부 개월"] = it.extra["months"]
        if it.cands and it.kind.startswith("pair_"):
            ans["짝 CSV 행"] = it.cands[0].no
        if KINDS[it.kind][2]:
            ans["규칙"] = "저장"
        out.append((info, ans, it))
    return out


def write_items_xlsx(path, engine, inherited):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    R = engine.R
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "확인 필요"
    head = INFO_COLS + ANSWER_COLS
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    fill = PatternFill("solid", fgColor="FFF2CC")
    lists = wb.create_sheet("목록")
    names = engine.doc.names
    columns = {"구분": ["수입", "지출", "이체"], "결제수단": ACCOUNTS, "고정여부": ["고정"], "비고": CARDS,
               "규칙": ["저장", "이번만"], "새 이름": ["Y"], "대분류": names["대분류"], "소분류": names["소분류"]}
    refs = {}
    for j, (k, vals) in enumerate(columns.items(), start=1):
        lists.cell(row=1, column=j, value=k)
        for i, v in enumerate(vals, start=2):
            lists.cell(row=i, column=j, value=v)
        col = openpyxl.utils.get_column_letter(j)
        refs[k] = f"목록!${col}$2:${col}${max(2, len(vals) + 1)}"
    lists.sheet_state = "hidden"
    rows = item_rows(engine)
    n_inh = 0
    dv_by_kind = {}
    for i, (info, ans, it) in enumerate(rows, start=2):
        prev = inherited.get(it.id)
        if prev and prev.get("처리"):
            ans = {c: prev.get(c, ans.get(c, "")) for c in ANSWER_COLS}
            ans["메모"] = (ans.get("메모") or "") + " [지난 회차 답 이어받음]"
            n_inh += 1
        ws.append([info[c] for c in INFO_COLS] + [ans.get(c, "") for c in ANSWER_COLS])
        for j in range(len(INFO_COLS) + 1, len(head) + 1):
            ws.cell(row=i, column=j).fill = fill
        dv_by_kind.setdefault(it.kind, []).append(i)
    pc = head.index("처리") + 1
    pcol = openpyxl.utils.get_column_letter(pc)
    for kind, rws in dv_by_kind.items():
        dv = DataValidation(type="list", formula1='"' + ",".join(KINDS[kind][1]) + '"', allow_blank=True)
        ws.add_data_validation(dv)
        for r in rws:
            dv.add(f"{pcol}{r}")
    last = max(2, len(rows) + 1)
    for k, ref in refs.items():
        if k in head:
            col = openpyxl.utils.get_column_letter(head.index(k) + 1)
            dv = DataValidation(type="list", formula1=ref, allow_blank=True, showErrorMessage=False)
            ws.add_data_validation(dv)
            dv.add(f"{col}2:{col}{last}")
    # 후보 CSV 행을 옆 시트에(답할 때 보도록)
    cs = wb.create_sheet("CSV 행")
    cs.append(["행", "키"] + COLS)
    seen = set()
    for _, _, it in rows:
        for r in it.cands + ([it.rec] if it.rec else []):
            if r is not None and r.no not in seen:
                seen.add(r.no)
                cs.append([r.no, r.key] + [r.v[c] for c in COLS])
    ws.freeze_panes = "C2"
    tmp = path + ".tmp"
    wb.save(tmp)
    os.replace(tmp, path)
    return n_inh


def write_proposals(path, engine):
    R = engine.R
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["상태", "사유", "출처", "파일", "행", "날짜", "금액", "방향", "가맹점", "적요", "거래키", "짝 CSV 행",
                "항목"] + ["새 " + c for c in COLS])
    for k, d in sorted(R.disps.items(), key=lambda kv: (kv[1].tx.date, kv[0])):
        t = d.tx
        rec = engine.doc.by_key.get(d.csv_key)
        row = d.row if d.status in ("자동", "확인 필요") and d.row else {}
        w.writerow([d.status, d.reason, t.source, t.file, t.row, t.date.isoformat(), t.amount, t.direction,
                    t.merchant, " / ".join(t.memo), k, rec.no if rec else "", d.item.id if d.item else ""]
                   + [row.get(c, "") for c in COLS])
    for it in R.items:
        if it.tx is None:
            w.writerow(["확인 필요", it.reason, "CSV" if it.rec else "", "", it.rec.no if it.rec else "",
                        it.rec.v["날짜"] if it.rec else "", it.rec.amount if it.rec else "", "", "", "",
                        "", it.rec.no if it.rec else "", it.id] + [""] * len(COLS))
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(buf.getvalue())


def counts_by_source(engine):
    R = engine.R
    out = {}
    for s in engine.cfg["sources"]:
        c = defaultdict(int)
        for k, d in R.disps.items():
            if d.tx.source == s.id and "#" not in k:
                c[d.status] += 1
        c["시작일 전(버림)"] = sum(1 for t in R.pre if t.source == s.id)
        c["파일 행"] = sum(f["rows"] for f in R.files if f["source"] == s.id and f["status"] == "읽음")
        out[s.id] = dict(c)
    return out


def report_md(engine, run_id, mode):
    """회차 보고.md — 로컬·공개 안 함. 건수·합계·차액만(가맹점·개별 거래 금액 0 — 3-0 14)."""
    R = engine.R
    L = [f"# 내역 정리 보고 — 회차 {run_id} ({mode})", "",
         f"- CSV: {R.inputs['csv'][0]} (md5 {R.inputs['csv'][1][:8]}) · 자동화 시작일 {engine.start}"
         f" · 실행일 {engine.today}", ""]
    L.append("## 파일")
    L.append("| 파일 | 출처 | 상태 | 행 | 설정으로 뺀 행 | 기간 |")
    L.append("|---|---|---|---|---|---|")
    for f in R.files:
        per = "~".join(f["period"]) if f["period"] else ""
        L.append(f"| {f['file']} | {f['source'] or '-'} | {f['status']} | {f['rows']} | {f.get('excluded', 0)} | {per} |")
    for f in R.failures:
        L.append(f"- 실패: {f['file']} ({f['source'] or '미식별'}) — {f['reason']}")
    L += ["", "## 출처별 건수", "| 출처 | 상태 | 건수 |", "|---|---|---|"]
    for sid, c in counts_by_source(engine).items():
        L.append(f"| {sid} | {R.source_status.get(sid)} | " + " · ".join(f"{k} {v}" for k, v in sorted(c.items())) + " |")
    kinds = defaultdict(int)
    for it in R.items:
        kinds[it.kind] += 1
    L += ["", f"## 확인 필요 {len(R.items)}건 (이어받은 답 {R.inherited}건)"]
    for k, v in sorted(kinds.items()):
        L.append(f"- {KINDS[k][0]}: {v}")
    new_csv_only = sum(1 for it in R.items if it.kind == "csv_only")
    L.append(f"- 새로 생긴 미설명 CSV 행: {new_csv_only}")
    L += ["", f"## 새 행 {len(R.new_rows)} · 지울 행 {len(R.deletes)}"]
    L += ["", "## 카드대금 대조"]
    for c in R.card_checks:
        L.append(f"- {c['card']} {c['pay_month']} 결제 {c['date']}: 출금 {c['paid']:,} · 이용기간 {c['span'][0]}~"
                 f"{c['span'][1]} 합계 {c['expected']:,} (일시불 {c['spend']:,} · 취소 {c['cancel']:,} · 할부 회차 "
                 f"{c['inst']:,} · 포인트 {c['points']:,} · 경계 ±3일 {c['edge']:,}) · 차이 {c['diff']:,} · "
                 f"{'대조됨' if c['ok'] else ('대조 불가(파일 기간 부족)' if not c['covered'] else '불일치')}")
    if not R.card_checks:
        L.append("- 카드대금 출금 없음")
    L += ["", "## 잔액 대조(3-0 8)"]
    for b in R.balance:
        if "diff" not in b:
            L.append(f"- {b['account']}: {b['status']}")
            continue
        L.append(f"- {b['account']} ({'원장 앵커' if b['from_ledger'] else '파일'} 끝 {b['end_date']}): "
                 f"파일 잔액 {b['file_balance']:,} − CSV 초안 잔고 {b['csv_balance']:,} = 차액 {b['diff']:,}")
        for k, v in b["terms"].items():
            L.append(f"  - {k}: {v:,}")
        L.append(f"  - 잔차 {b['residual']:,} · 기준 차액 {b['baseline'] if b['baseline'] is not None else '없음'}"
                 f" → {b['status']}")
    L += ["", "## 쓰기 전 검사(3-0 9)"]
    for k, v in R.checks.items():
        if k != "warnings":
            L.append(f"- {k}: {v}")
    for k, (o, n) in R.warn_up.items():
        L.append(f"- 경고 '{k}' 증가 {o} → {n}")
    if R.gaps:
        L += ["", "## 공백"] + [f"- {g['source']} {g['from']}~{g['to']}" for g in R.gaps]
    L += ["", "## apply 막힘" if R.blocked else "## apply 막힘 없음"] + [f"- {b}" for b in R.blocked]
    if R.notes:
        L += ["", "## 참고"] + [f"- {n}" for n in R.notes]
    return "\n".join(L) + "\n"


def print_summary(engine, run_dir, limit=8):
    R = engine.R
    print("── 파일 ──")
    for f in R.files:
        per = "~".join(f["period"]) if f["period"] else ""
        tag = "[FAIL] " if f["status"] == "실패" else ""
        print(f"  {tag}{f['file']} · {f['source'] or '미식별'} · {f['status']} · {f['rows']}행"
              + (f"(설정으로 뺀 행 {f['excluded']})" if f.get("excluded") else "") + f" {per}"
              + (f" — {f['reason']}" if f["reason"] else ""))
    if not R.files:
        print(f"  (내역 파일 없음 — {p(engine.P.inbox)}/<받은 날>/에 넣는다)")
    print("── 출처별 ──")
    for sid, c in counts_by_source(engine).items():
        print(f"  {sid} [{R.source_status.get(sid)}]: " + " · ".join(f"{k} {v}" for k, v in sorted(c.items())))
    print(f"── 새 행 {len(R.new_rows)} · 지울 행 {len(R.deletes)} · 확인 필요 {len(R.items)}(이어받은 답 {R.inherited}) ──")
    by = defaultdict(list)
    for info, ans, it in item_rows(engine):
        by[it.kind].append((info, ans))
    for kind, lst in by.items():
        print(f"  {KINDS[kind][0]}: {len(lst)}건")
        for info, ans in lst[:limit]:
            print(f"    #{info['번호']} {info['날짜']} {info['금액']} {info['가맹점·적요']}"
                  + (f" · 후보 CSV {info['후보 CSV 행']}행" if info["후보 CSV 행"] else "")
                  + (f" · 답 {ans.get('처리')}" if ans.get("처리") else ""))
        if len(lst) > limit:
            print(f"    … {len(lst) - limit}건 더(확인필요.xlsx)")
    for c in R.card_checks:
        print(f"  카드대금 {c['card']} {c['pay_month']}: 차이 {c['diff']:,} "
              f"{'대조됨' if c['ok'] else ('대조 불가' if not c['covered'] else '불일치')}")
    for b in R.balance:
        print(f"  잔액 {b['account']}: {b['status']}" + (f" · 잔차 {b['residual']:,}" if "residual" in b else ""))
    for e in R.answer_errors:
        print(f"  [답 오류] {e}")
    print(f"── apply {'막힘 ' + str(len(R.blocked)) + '건' if R.blocked else '막힘 없음'} ──")
    for b in R.blocked:
        print(f"  - {b}")
    print(f"회차 폴더: {p(run_dir)}")


# ------------------------------------------------------------
# 명령
# ------------------------------------------------------------

def mode_line(P, cmd):
    if P.rehearsal:
        print(f"[리허설] pc_import {cmd} · 놀이터 {p(P.base)} · 내역 {p(P.inbox)}(읽기만)")
    else:
        print(f"[운영] pc_import {cmd} · CSV {p(P.csv_dir)} · rules {p(P.rules)} · 회차 {p(P.runs)}")


def previous_answers(P, exclude):
    for name in reversed(run_dirs(P)):
        if name == exclude:
            continue
        x = os.path.join(P.runs, name, "확인필요.xlsx")
        if os.path.exists(x):
            try:
                return read_answers(x), name
            except Exception:  # noqa: BLE001 — 깨진 지난 답은 이어받지 않는다
                return {}, name
    return {}, None


def last_apply(P, exclude):
    """'했었나'는 원장 + CSV md5로(3-0 13): 지난 apply가 중간에 끝났으면 알린다."""
    for name in reversed(run_dirs(P)):
        if name == exclude:
            continue
        j = os.path.join(P.runs, name, "apply.json")
        if os.path.exists(j):
            ph = read_json(j).get("phase")
            if ph != "done":
                print(f"[주의] 지난 apply(회차 {name})가 '{ph}' 단계에서 끝났다 — 백업 {p(P.backup)}/{name}/ 사본은 "
                      f"남아 있다. 원장에 안 적힌 새 행은 이번 scan이 손 행처럼 짝지어 다시 묶는다")
            return


def snapshot(P):
    """scan 전후 대조용 md5(완료 기준 1): CSV 폴더·rules·내역 파일·log."""
    out = {}
    for root in (P.csv_dir, P.rules, P.inbox):
        if os.path.isdir(root):
            for r, _, fs in os.walk(root):
                for n in fs:
                    full = os.path.join(r, n)
                    out[full] = md5_file(full)
    if os.path.exists(P.log):
        out[P.log] = md5_file(P.log)
    return out


def cmd_scan(P, args):
    mode_line(P, "scan")
    P.check_outside_clone()
    with RunLock(P.csv_dir):
        before = snapshot(P)
        run = new_run_dir(P)
        rid = os.path.basename(run)
        prev, prev_name = previous_answers(P, rid)
        last_apply(P, rid)
        today = today_kst()
        eng = Engine(P, today, answers=prev, run_id=rid, opts={"lenient": True})
        try:
            eng.run()
        except Stop as e:
            with open(os.path.join(run, "보고.md"), "w", encoding="utf-8", newline="") as f:
                f.write(f"# 내역 정리 — 회차 {rid}\n\n[FAIL] {e}\n")
            print(f"[FAIL] {e}")
            return 1
        R = eng.R
        R.inherited = write_items_xlsx(os.path.join(run, "확인필요.xlsx"), eng, eng.answered)
        write_proposals(os.path.join(run, "제안.csv"), eng)
        with open(os.path.join(run, f"초안_가계부_{rid}.csv"), "wb") as f:
            f.write(R.draft)
        with open(os.path.join(run, "거래.json"), "w", encoding="utf-8", newline="") as f:
            json.dump([t.to_json() for t in sorted(R.union.values(), key=lambda t: (t.date, t.key))], f,
                      ensure_ascii=False, indent=0)
        inputs_dir = os.path.join(run, "입력")
        for rel, full in inbox_files(P):
            dst = os.path.join(inputs_dir, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(full, dst)
        with open(os.path.join(run, "보고.md"), "w", encoding="utf-8", newline="") as f:
            f.write(report_md(eng, rid, P.rehearsal and "리허설" or "운영"))
        stamp = {"version": 1, "run": rid, "mode": "rehearsal" if P.rehearsal else "operational",
                 "created": now_kst().isoformat(timespec="seconds"), "today": today.isoformat(),
                 "inputs": R.inputs, "csv_path": p(eng.csv_path), "prev_answers": prev_name,
                 "items": len(R.items), "new_rows": len(R.new_rows), "deletes": len(R.deletes),
                 "draft_md5": md5_bytes(R.draft), "repo": p(P.repo)}
        write_json_atomic(os.path.join(run, "stamp.json"), stamp)
        after = snapshot(P)
        if before != after:
            changed = sorted(set(before) ^ set(after) | {k for k in before if before.get(k) != after.get(k)})
            print(f"[FAIL] scan 전후로 바뀐 파일이 있다(scan은 쓰기 0이어야 한다): {[p(x) for x in changed]}")
            return 1
        print_summary(eng, run)
        print(f"[PASS] scan 끝 · 회차 {rid} · 쓰기는 회차 폴더뿐(CSV·rules·내역 전후 md5 같음)")
        print(f"다음: 확인 필요에 답(확인필요.xlsx 또는 answer) → apply --run {rid} (dry-run) → apply --run {rid} --confirm")
        return 2 if R.blocked else 0


def load_scan_run(P, arg):
    run = os.path.join(P.runs, arg) if os.sep not in arg and "/" not in arg else os.path.abspath(arg)
    rid = os.path.basename(run)
    if not run_key(rid) or os.path.normcase(os.path.dirname(os.path.realpath(run))) != \
            os.path.normcase(os.path.realpath(P.runs)):
        raise Stop(f"회차 폴더는 {p(P.runs)} 바로 아래여야 한다: {p(run)}")
    sp = os.path.join(run, "stamp.json")
    if not os.path.exists(sp):
        raise Stop(f"회차 {rid}에 stamp.json이 없다(scan이 끝나지 않은 회차)")
    stamp = read_json(sp)
    if stamp.get("mode") != ("rehearsal" if P.rehearsal else "operational"):
        raise Stop(f"이 회차는 {stamp.get('mode')} 모드에서 만들었다")
    runs = run_dirs(P)
    if not runs or runs[-1] != rid:
        raise Stop(f"가장 최근 scan 회차가 아니다(최근 {runs[-1] if runs else '-'}) — 가장 최근 회차에만 답하고 apply한다")
    return run, rid, stamp


def cmd_answer(P, args):
    mode_line(P, "answer")
    import openpyxl
    run, rid, stamp = load_scan_run(P, args.run)
    path = os.path.join(run, "확인필요.xlsx")
    if file_in_use(path):
        raise Stop("확인필요.xlsx가 엑셀에서 열려 있다 — 닫고 다시")
    if args.expect_md5 and md5_file(path) != args.expect_md5:
        raise Stop("확인필요.xlsx가 그 사이 바뀌었다(md5 다름) — 다시 읽고 답한다")
    sets = {}
    for s in args.set or []:
        if "=" not in s:
            raise Stop(f"--set은 열=값: {s!r}")
        k, v = s.split("=", 1)
        if k not in ANSWER_COLS:
            raise Stop(f"답 칸 {k!r}는 {ANSWER_COLS} 중 하나")
        sets[k] = v
    wb = openpyxl.load_workbook(path)
    ws = wb["확인 필요"]
    head = [c.value for c in ws[1]]
    hit = None
    for row in ws.iter_rows(min_row=2):
        if str(row[head.index("번호")].value) == str(args.item) or row[head.index("항목")].value == args.item:
            hit = row
            break
    if hit is None:
        raise Stop(f"항목 {args.item}이 없다")
    kind = hit[head.index("종류")].value
    if "처리" in sets and sets["처리"] not in KINDS[kind][1]:
        raise Stop(f"처리 '{sets['처리']}'는 {KINDS[kind][1]} 중 하나")
    for k, v in sets.items():
        hit[head.index(k)].value = v
    tmp = path + ".tmp"
    wb.save(tmp)
    os.replace(tmp, path)
    print(f"[PASS] #{hit[0].value} {hit[head.index('항목')].value}: " + " · ".join(f"{k}={v}" for k, v in sets.items()))
    print(f"확인필요.xlsx md5 {md5_file(path)}")
    return 0


def parse_acks(args):
    acks = {"card": {}, "balance": {}, "gap": {}, "warning": {}}
    for name in acks:
        for k, reason in getattr(args, f"ack_{name}") or []:
            if not reason.strip():
                raise Stop(f"--ack-{name} {k}: 사유가 비었다")
            acks[name][k] = reason.strip()
    return acks


def cmd_apply(P, args):
    mode_line(P, "apply" + (" --confirm" if args.confirm else " (dry-run)"))
    P.check_outside_clone()
    with RunLock(P.csv_dir):
        run, rid, stamp = load_scan_run(P, args.run)
        if os.path.exists(os.path.join(run, "apply.json")):
            raise Stop(f"이 회차는 이미 apply했다({read_json(os.path.join(run, 'apply.json')).get('phase')}) — "
                       f"scan부터 다시")
        xlsx = os.path.join(run, "확인필요.xlsx")
        if file_in_use(xlsx):
            raise Stop("확인필요.xlsx가 엑셀에서 열려 있다 — 저장하고 닫은 뒤 다시")
        answers = read_answers(xlsx)
        ans_md5 = md5_file(xlsx) if os.path.exists(xlsx) else None
        acks = parse_acks(args)
        today = dt.date.fromisoformat(stamp["today"])
        if today_kst() != today:
            raise Stop(f"scan 날({today})과 오늘({today_kst()})이 다르다 — 미래 날짜·할부 회차 기준이 바뀌므로 scan부터")
        eng = Engine(P, today, answers=answers, run_id=rid, opts={"skip": args.skip or [], "acks": acks})
        eng.run()
        R = eng.R
        if R.inputs != stamp["inputs"]:
            diff = [k for k in R.inputs if R.inputs.get(k) != stamp["inputs"].get(k)]
            raise Stop(f"scan 뒤 입력이 바뀌었다({diff}) — scan부터 다시(새 회차)")
        print_summary(eng, run)
        print(f"── 적용할 것: 새 행 {len(R.new_rows)} · 지울 행 {len(R.deletes)} · "
              f"규칙 추가 {len(rule_rows(eng, rid))} · 답 {len(eng.answered)} ──")
        if R.blocked:
            print(f"[FAIL] apply 막힘 {len(R.blocked)}건 — 쓰기 0")
            return 1
        if not args.confirm:
            print("dry-run — 쓰기 0. 위 내용으로 쓰려면 --confirm")
            return 0
        return write_apply(P, eng, run, rid, stamp, ans_md5, acks)


def rule_rows(eng, rid):
    """'추가' 답 중 규칙 저장 → 분류규칙.csv에 붙일 줄. 카드사명·'카드대금'·계좌명이 든 답은 거부(3-0 5)."""
    if hasattr(eng, "_rule_rows"):
        return eng._rule_rows
    out, notes = [], []
    # 카드사명·'카드대금'·계좌명은 부분 문자열로 거부한다. 다만 '생활비'는 실제 대분류 '주거생활비'에 들어 있어
    # (실물 리허설) 칸 전체가 같을 때만 거부한다.
    banned = set(CARDS) | set(pl.ACCOUNT_KEY) | {"카드대금"} | {a for a in ACCOUNTS if a != "생활비"}
    banned_eq = set(ACCOUNTS)
    for it in eng.R.items:
        a = eng.answered.get(it.id)
        if not a or a["처리"] not in ("추가", "할부") or it.tx is None:
            continue
        if not KINDS[it.kind][2] and it.kind not in ("raw_diff",):
            continue
        if a.get("규칙", "저장") == "이번만" or (it.kind != "raw_diff" and a.get("규칙") not in ("저장", "")):
            continue
        t = it.tx
        if t.kind == "bank" and t.direction == "in":
            continue
        key = normalize_merchant(t.merchant)
        if not key or any(pk and pk in key for pk in eng.cfg["platform_keys"]):
            continue
        row = eng.answer_row(it, a)
        if row["구분"] != "지출":
            continue
        if any(b and b in row[c] for c in ("대분류", "소분류", "세부내용") for b in banned) or \
                any(row[c] in banned_eq for c in ("대분류", "소분류", "세부내용")):
            notes.append(f"규칙 저장 거부(카드사명·카드대금·계좌명): {it.id}")
            continue
        s = eng.src_by_id.get(t.source)
        pay = row["결제수단"] if (t.kind == "card" and s and row["결제수단"] != s.account) else ""
        old = eng.rules.lookup(key)
        vals = (row["대분류"], row["소분류"], row["세부내용"], row["고정여부"], pay)
        mode = "자동"
        if it.kind == "raw_diff" or (old and (old["mode"] == "매번" or tuple(old["values"][c] for c in (
                "대분류", "소분류", "세부내용", "고정여부", "결제수단")) != vals or t.merchant.strip() not in old["raws"])):
            mode = "매번"
            notes.append(f"'매번'으로 승격: 키 {key}")
        same_new = [r for r in out if r["키"] == key]
        if same_new and any((r["대분류"], r["소분류"], r["세부내용"], r["고정여부"], r["결제수단"]) != vals
                            or r["원문"] != t.merchant.strip() for r in same_new):
            mode = "매번"
            for r in same_new:
                r["물음"] = "매번"
            notes.append(f"'매번'으로 승격(이번 답끼리 다름): 키 {key}")
        out.append({"키": key, "대분류": vals[0], "소분류": vals[1], "세부내용": vals[2], "고정여부": vals[3],
                    "결제수단": vals[4], "물음": mode, "원문": t.merchant.strip(),
                    "만든 날": eng.today.isoformat(), "근거 회차": rid})
    eng._rule_rows = out
    eng.R.rule_notes = notes
    return out


def ledger_ops(eng, rid, new_keys_final, key_remap):
    """원장에 적을 것 — 정규화 거래 전체 · 처리 상태 · 이체 쌍 · 할부 일정 · 설명됨 · 앵커 · 파일 기간."""
    R = eng.R
    ops = {"tx": [], "state": [], "csvnote": [], "transfer": [], "schedule": [], "anchor": [], "period": [],
           "schedule_cancel": sorted(eng.cancelled_sched)}
    answered = eng.answered
    by_tx_item = {it.tx.key: it for it in R.items if it.tx is not None}
    keep_old = set()      # 답 없는 '바뀜' 질문 — 원장 값을 옛 값으로 둬야 다음 회차에 다시 묻는다

    def st(k, state, ck="", extra=None):
        ck = key_remap.get(ck, ck)
        ops["state"].append((k, state, ck, json.dumps(extra or {}, ensure_ascii=False), rid))

    def transfer(out_k, in_k, src, dst, amount, d, ck):
        # 파일 없는 제외 계좌 쪽은 '(제외 계좌)'로 채워 기다림으로 남지 않게 한다
        if not out_k and src not in eng.bank_src:
            out_k = "(제외 계좌)"
        if not in_k and dst not in eng.bank_src:
            in_k = "(제외 계좌)"
        ops["transfer"].append((out_k, in_k, src, dst, int(amount), d, key_remap.get(ck, ck), rid))

    for k, d in R.disps.items():
        t = d.tx
        if d.status == "끝남":
            continue
        it = by_tx_item.get(k) if d.item is not None else None
        a = answered.get(it.id) if it else None
        act = a["처리"] if a else ""
        if d.status == "자동":
            ck = new_keys_final.get(k, "")
            st(k, "written", ck)
            if d.transfer:
                transfer(k if t.direction == "out" else (d.partner or ""),
                         k if t.direction == "in" else (d.partner or ""), *d.transfer, d.row["금액"],
                         d.rdate.isoformat(), ck)
            continue
        if d.status == "짝":
            st(k, "paired", d.csv_key)
            if d.transfer:
                transfer(k if t.direction == "out" else (d.partner or ""),
                         k if t.direction == "in" else (d.partner or ""), *d.transfer, d.row["금액"],
                         d.rdate.isoformat(), d.csv_key)
            continue
        if d.status == "제외":
            if d.wait_fill:
                ops["transfer"].append(("__fill__", d.wait_fill, k, d.partner, 0, "", "", rid))
            st(k, d.state, d.csv_key)
            continue
        # 확인 필요
        if it is None:
            continue
        if it.extra.get("ledger_change") or it.kind in ("vanished", "vanished_sched", "gone_row") \
                or (it.kind == "cancel_row" and "#" in k):
            # 답 없는 '바뀜·사라짐·끝난 할부 회차' 질문은 원장 값·상태를 그대로 둬 다음 회차에 다시 묻는다
            if act not in KINDS[it.kind][1] or act == "보류":
                keep_old.add(k)
                continue
        if act == "추가":
            ck = new_keys_final.get(it.id, "")
            st(k, "written", ck)
            row = eng.answer_row(it, a)
            if row["구분"] == "이체" and t.kind == "bank":
                partner = d.partner if d.transfer == (row["결제수단"], row["소분류"]) else ""
                transfer(k if t.direction == "out" else partner, k if t.direction == "in" else partner,
                         row["결제수단"], row["소분류"], row["금액"], row["날짜"], ck)
        elif act == "할부":
            st(k, "sched")
        elif act == "제외":
            st(k, "suppressed")
        elif act in ("짝", "고친 행"):
            st(k, "paired", eng.answer_rec(a).key)
        elif act == "삭제":
            if it.kind == "cancel_row":
                st(k, "cancel_done")
                if it.extra.get("orig"):
                    st(it.extra["orig"], "suppressed")
            elif it.kind == "changed":
                ops["state"].append((k, "__delete__", "", "{}", rid))   # 이번 값으로 다음 회차에 다시 제안
            else:
                st(k, "suppressed")
        elif act == "유지":
            if it.kind == "cancel_row" and "#" in k:
                st(k, "cancel_kept", it.extra.get("csv_key", ""))
            elif it.kind == "vanished_sched":
                st(k, "sched")
            elif it.kind == "cancel_row":
                st(k, "cancel_done")
                if it.extra.get("orig"):
                    st(it.extra["orig"], "paired", it.extra.get("csv_key", ""))
            elif it.kind == "vanished":
                st(k, "kept_missing", it.extra.get("csv_key", ""))
            elif it.kind == "changed":
                old = eng.led.state[k]
                st(k, old["state"], old["csv_key"])
        elif act == "지운 것 맞음":
            st(k, "suppressed")
        elif act == "일정 끝냄":
            st(k, "cancel_done")
            ops["schedule_cancel"].append(k)
        elif act == "다시 넣기":
            ops["state"].append((k, "__delete__", "", "{}", rid))
        elif act == "월별":
            st(k, "sched")
        elif act == "전액":
            st(k, "paired", it.extra.get("csv_key", ""))
        else:
            st(k, "held")
    # 답으로 만든 할부 회차 행(가상 거래키 …#k)도 '씀'으로 — 다음 회차에 일정이 같은 행을 또 내지 않게
    for row, origin in eng.row_origin:
        if "#" in origin and origin not in R.disps:
            st(origin, "written", new_keys_final.get(origin, ""))
    for t in R.union.values():
        if t.key not in keep_old:
            ops["tx"].append((t.key, t.source, json.dumps(t.to_json(), ensure_ascii=False)))
    for it in R.items:
        a = answered.get(it.id)
        if not a:
            continue
        if it.kind == "csv_only" and a["처리"] in ("현금", "커플통장", "오타", "기타"):
            ops["csvnote"].append((it.id, "explained", a["처리"], rid))
        if it.kind == "cardpay_missing" and a["처리"] == "확인":
            ops["csvnote"].append((it.id, "explained", "확인", rid))
        if a["처리"] == "할부" and it.tx is not None:
            row = eng.answer_row(it, a)
            ops["schedule"].append((it.tx.key, it.tx.source, it.tx.card, int(row["금액"]), int(a["할부 개월"]),
                                    "%d-%02d" % eng.pay_month(it.tx.card, it.tx.date),
                                    json.dumps(row, ensure_ascii=False), rid, "rows"))
        if a["처리"] in ("월별", "전액") and it.tx is not None and int(it.extra.get("months") or 0) >= 2:
            ops["schedule"].append((it.tx.key, it.tx.source, it.tx.card, int(it.row["금액"]),
                                    int(it.extra["months"]), "%d-%02d" % eng.pay_month(it.tx.card, it.tx.date),
                                    json.dumps(it.row, ensure_ascii=False), rid,
                                    "full" if a["처리"] == "전액" else "rows"))
    for k, d in R.disps.items():
        if d.state == "sched" and d.status == "제외" and k not in eng.led.schedule:
            t = d.tx
            ops["schedule"].append((k, t.source, t.card, int(d.row["금액"]), t.inst_months,
                                    "%d-%02d" % eng.pay_month(t.card, t.date),
                                    json.dumps(d.row, ensure_ascii=False), rid, "rows"))
    for b in R.balance:
        if "diff" in b:
            reason = eng.opts["acks"].get("balance", {}).get(b["account"], "")
            anc = eng.led.anchor.get(b["account"])
            base = b["residual"] if (anc is None or reason) else anc["baseline"]
            why = (reason + f" [잔차 {b['residual']}]") if reason else (anc["reason"] if anc else "첫 회차 차액 0")
            ops["anchor"].append((b["account"], b["end_date"], b["file_balance"], base, why, rid))
    for f in eng.parsed:
        ops["period"].append((f["src"].id, f["rel"], f["md5"], f["period"][0].isoformat(),
                              f["period"][1].isoformat(), rid))
    # 원장의 csv 키를 새 CSV 키로(지운 줄 때문에 같은 값 순번이 당겨진 경우)
    remap_states = [(k, key_remap[s["csv_key"]]) for k, s in eng.led.state.items()
                    if s["csv_key"] in key_remap and key_remap[s["csv_key"]] != s["csv_key"]]
    remap_transfers = [(old, new) for old, new in key_remap.items() if old != new]
    return ops, remap_states, remap_transfers


def write_apply(P, eng, run, rid, stamp, ans_md5, acks):
    R = eng.R
    csv_path = eng.csv_path
    name = os.path.basename(csv_path)
    new_name = f"가계부_{rid}.csv"
    new_path = os.path.join(P.csv_dir, new_name)
    bdir = os.path.join(P.backup, rid)
    journal = {"run": rid, "phase": "start", "csv_before": R.inputs["csv"], "new_csv": new_name,
               "answers_md5": ans_md5, "acks": acks, "started": now_kst().isoformat(timespec="seconds")}
    jpath = os.path.join(run, "apply.json")

    def phase(ph, **kw):
        journal.update(phase=ph, **kw)
        write_json_atomic(jpath, journal)

    # 1) 배타 열기
    if file_in_use(csv_path):
        raise Stop(f"{name}이 엑셀에서 열려 있다 — 엑셀을 닫고 다시")
    if os.path.exists(new_path):
        raise Stop(f"{new_name}이 이미 있다")
    phase("start")
    # 2) 백업 복사(md5 = 원본)
    os.makedirs(bdir, exist_ok=True)
    bpath = os.path.join(bdir, name)
    shutil.copyfile(csv_path, bpath)
    if md5_file(bpath) != R.inputs["csv"][1] or md5_file(csv_path) != R.inputs["csv"][1]:
        raise Stop("백업 사본 md5가 원본과 다르다 — 멈춘다(정본은 그대로)")
    phase("backup_copied", backup=p(bpath))
    # 3) 새 CSV 임시 파일 → 4) 검사(엔진 계산 때와 같은 바이트인지 다시)
    tmp = new_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(R.draft)
    if md5_file(tmp) != md5_bytes(R.draft):
        raise Stop(f"임시 파일 {tmp}이 계산한 바이트와 다르다 — 임시 파일을 남기고 멈춘다")
    # 5) 새 파일로 교체
    try:
        os.replace(tmp, new_path)
    except PermissionError as e:
        raise Stop(f"새 CSV로 바꾸지 못했다({e}) — 임시 파일 {p(tmp)}을 남기고 멈춘다") from None
    phase("csv_written", csv_after=[new_name, md5_file(new_path)])
    # 6) 옛 파일을 backup으로 이동(같은 md5의 사본 자리에 덮어 옮긴다)
    if md5_file(csv_path) != md5_file(bpath):
        raise Stop("옛 CSV가 백업 사본과 다르다 — 옮기지 않고 멈춘다(uploads에 CSV 둘)")
    os.replace(csv_path, bpath)
    phase("old_moved")
    # 7) 규칙
    new_rules = rule_rows(eng, rid)
    if new_rules:
        rtmp = P.rules_csv + ".tmp"
        with open(rtmp, "wb") as f:
            f.write(eng.rules.appended_bytes(new_rules))
        os.replace(rtmp, P.rules_csv)
    phase("rules", rules_added=len(new_rules), rule_notes=R.rule_notes)
    # 8) 원장(트랜잭션)
    newdoc = CsvDoc(new_path, eng.today)
    new_keys_final = {}
    first_new = 2 + len([r for r in eng.doc.recs[: eng.doc.tail_start] if r.key not in R.deletes])
    for (row, origin), no in zip(eng.row_origin, range(first_new, first_new + len(R.new_rows))):
        rec = newdoc.by_no.get(no)
        k = origin[3:] if origin.startswith("tx:") else origin
        new_keys_final[k] = rec.key if rec else ""
        if origin.startswith("tx:"):
            new_keys_final[origin] = rec.key if rec else ""
    key_remap = {}
    old_kept = [r for r in eng.doc.recs if r.key and r.key not in R.deletes]
    new_old = [r for r in newdoc.recs if r.key][: len([r for r in old_kept if not r.blank])]
    for o, n in zip([r for r in old_kept if not r.blank], new_old):
        if o.text == n.text:
            key_remap[o.key] = n.key
    ops, remap_states, remap_transfers = ledger_ops(eng, rid, new_keys_final, key_remap)
    os.makedirs(os.path.dirname(P.ledger), exist_ok=True)
    con = sqlite3.connect(P.ledger)
    try:
        con.executescript(LEDGER_SCHEMA)
        with con:
            for k, src, data in ops["tx"]:
                con.execute("INSERT INTO tx(key, source, data, first_run, last_run) VALUES(?,?,?,?,?) "
                            "ON CONFLICT(key) DO UPDATE SET data=excluded.data, last_run=excluded.last_run",
                            (k, src, data, rid, rid))
            for k, ck in remap_states:
                con.execute("UPDATE state SET csv_key=? WHERE key=?", (ck, k))
            remap = dict(remap_transfers)
            for rowid, ck in con.execute("SELECT rowid, csv_key FROM transfer").fetchall():
                if ck in remap:
                    con.execute("UPDATE transfer SET csv_key=? WHERE rowid=?", (remap[ck], rowid))
            for k, st, ck, extra, r in ops["state"]:
                if st == "__delete__":
                    con.execute("DELETE FROM state WHERE key=?", (k,))
                else:
                    con.execute("INSERT OR REPLACE INTO state(key, state, csv_key, extra, run) VALUES(?,?,?,?,?)",
                                (k, st, ck, extra, r))
            for row in ops["csvnote"]:
                con.execute("INSERT OR REPLACE INTO csvnote(id, state, answer, run) VALUES(?,?,?,?)", row)
            for out_k, in_k, src, dst, amt, d, ck, r in ops["transfer"]:
                if out_k == "__fill__":
                    # 원장의 기다리는 이체: (in_k 열) = 이번 거래키, 반대 열 = 짝(src)
                    col = in_k
                    other = "in_key" if col == "out_key" else "out_key"
                    con.execute(f"UPDATE transfer SET {col}=? WHERE {other}=? AND ({col} IS NULL OR {col}='')",
                                (src, dst))
                    continue
                con.execute("INSERT INTO transfer(out_key, in_key, src, dst, amount, date, csv_key, run) "
                            "VALUES(?,?,?,?,?,?,?,?)", (out_k, in_k, src, dst, amt, d, ck, r))
            for row in ops["schedule"]:
                con.execute("INSERT OR REPLACE INTO schedule(key, source, card, total, months, first_month, row, run, "
                            "mode) VALUES(?,?,?,?,?,?,?,?,?)", row)
            for k in ops["schedule_cancel"]:
                con.execute("UPDATE schedule SET mode='cancelled' WHERE key=?", (k,))
            for row in ops["anchor"]:
                con.execute("INSERT OR REPLACE INTO anchor(account, end_date, end_balance, baseline, reason, run) "
                            "VALUES(?,?,?,?,?,?)", row)
            for row in ops["period"]:
                con.execute("INSERT OR REPLACE INTO period(source, file, md5, start, end_, run) VALUES(?,?,?,?,?,?)", row)
            summary = {"new_rows": len(R.new_rows), "deletes": len(R.deletes), "items": len(R.items),
                       "answered": len(eng.answered), "rules": len(new_rules)}
            con.execute("INSERT OR REPLACE INTO runs(run, applied, csv_before, csv_after, csv_name, summary) "
                        "VALUES(?,?,?,?,?,?)", (rid, now_kst().isoformat(timespec="seconds"), R.inputs["csv"][1],
                                                md5_file(new_path), new_name, json.dumps(summary)))
    finally:
        con.close()
    phase("ledger")
    # 9) 기록 한 줄(금액·가맹점 0)
    log_line(P, eng, rid, new_name, md5_file(new_path), acks)
    phase("done", finished=now_kst().isoformat(timespec="seconds"))
    print(f"[PASS] apply 끝 · 새 CSV {p(new_path)} (md5 {md5_file(new_path)[:8]}) · 옛 CSV → {p(bpath)} · "
          f"새 행 {len(R.new_rows)} · 지운 행 {len(R.deletes)} · 규칙 {len(new_rules)}줄")
    for n in R.rule_notes:
        print(f"  - {n}")
    print("다음: /q-household-run ①부터(uploads에 CSV 한 파일 — 새 판)" if not P.rehearsal else
          "[리허설] 놀이터 사본에만 썼다 — 운영 uploads·rules·work는 그대로")
    return 0


def log_line(P, eng, rid, new_name, new_md5, acks):
    """runs/log.md 한 줄 — pc_run 표와 같은 11칸. 출처별 건수·자동/확인 필요/보류·ack만(금액·가맹점 0)."""
    R = eng.R
    cnt = counts_by_source(eng)
    src = "; ".join(f"{k}:" + ",".join(f"{a}{b}" for a, b in sorted(v.items())) for k, v in cnt.items())
    ack = ",".join(f"{k}:{'/'.join(v)}" for k, v in acks.items() if v) or "-"
    unexplained = len([k for k in R.cur if k not in R.disps])
    bal = ",".join(f"{b['account']}:{'통과' if b.get('ok') else ('ack' if b['account'] in acks['balance'] else '-')}"
                   for b in R.balance if "diff" in b) or "-"
    line = (f"| {now_kst():%Y-%m-%d %H:%M} | 내역 {rid} | {new_name} | {new_md5[:8]} | +{len(R.new_rows)}/-{len(R.deletes)} | "
            f"확인필요 {len(R.items)}·보류 {len(R.items) - len(eng.answered)} | - | - | - | "
            f"내역 정리 apply [{src}] 설명 안 된 줄 {unexplained} · 잔액 {bal} · ack {ack} | {len(R.warn_up)} |")
    os.makedirs(os.path.dirname(P.log), exist_ok=True)
    existing = ""
    if os.path.exists(P.log):
        with open(P.log, encoding="utf-8") as f:
            existing = f.read()
    with open(P.log, "a", encoding="utf-8", newline="") as f:
        if not existing:
            f.write("# 가계부 리포트 실행 기록 (이 PC · 공개 안 함 · 금액·세부내용 없음)\n\n"
                    "| 기록 시각 | 회차 | CSV | CSV md5 | 행 | 기간 | 코드 | report md5 | 커밋 | 반영 | 경고 |\n"
                    "|---|---|---|---|---|---|---|---|---|---|---|\n")
        f.write(line + "\n")


def cmd_init(P, args):
    mode_line(P, "init")
    if not P.rehearsal:
        raise Stop("init은 --rehearsal에서만 쓴다(놀이터 만들기)")
    P.check_outside_clone()
    if os.path.isdir(P.base) and os.listdir(P.base):
        raise Stop(f"놀이터 {p(P.base)}가 이미 있다 — 그대로 쓰거나, 새로 하려면 사용자 확인 뒤 폴더 이름을 바꾼다")
    real = os.path.join(P.home, "uploads")
    csvs = sorted(n for n in os.listdir(real) if n.lower().endswith(".csv") and os.path.isfile(os.path.join(real, n)))
    if len(csvs) != 1:
        raise Stop(f"운영 uploads의 CSV가 {len(csvs)}개 — 한 파일이어야 사본을 만든다")
    os.makedirs(P.csv_dir)
    src = os.path.join(real, csvs[0])
    shutil.copyfile(src, os.path.join(P.csv_dir, csvs[0]))
    print(f"[PASS] CSV 사본 {csvs[0]} md5 {md5_file(src)[:8]} = {md5_file(os.path.join(P.csv_dir, csvs[0]))[:8]}")
    real_rules = os.path.join(P.home, "rules")
    os.makedirs(P.rules)
    for n in ("계좌.toml", "분류규칙.csv", "원장.sqlite"):
        s = os.path.join(real_rules, n)
        if os.path.exists(s):
            shutil.copyfile(s, os.path.join(P.rules, n))
            print(f"[PASS] rules 사본 {n} md5 {md5_file(s)[:8]}")
        else:
            print(f"[정보] rules/{n} 없음 — 사본 없이 시작")
    return 0


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="pc_import.py", description="이 PC 내역 파일 → 가계부 CSV 행 정리")
    ap.add_argument("--rehearsal", action="store_true")
    ap.add_argument("--home")
    ap.add_argument("--repo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    sub.add_parser("init")
    a = sub.add_parser("answer")
    a.add_argument("--run", required=True)
    a.add_argument("--item", required=True)
    a.add_argument("--set", action="append")
    a.add_argument("--expect-md5")
    b = sub.add_parser("apply")
    b.add_argument("--run", required=True)
    b.add_argument("--skip", action="append")
    for name in ("card", "balance", "gap", "warning"):
        b.add_argument(f"--ack-{name}", nargs=2, action="append", metavar=("대상", "사유"))
    b.add_argument("--confirm", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo = args.repo or os.path.dirname(_HERE)
    home = args.home or os.path.dirname(os.path.abspath(repo))
    P = Paths(repo, home, args.rehearsal)
    try:
        return {"scan": cmd_scan, "apply": cmd_apply, "answer": cmd_answer, "init": cmd_init}[args.cmd](P, args)
    except Stop as e:
        print(f"[FAIL] {e}")
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
