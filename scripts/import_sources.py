"""
내역 파일(카드·통장 내보내기) 읽기 · 출처 서명 · 정규화 거래 — scripts/pc_import.py가 쓴다.

웹(claude.ai)은 이 파일을 쓰지 않는다. 설계: 2026-10-26 내역 자동화 탐색 기준선 3-0 2·4·5·6(설계안 A).

- 금융사마다 열 이름·값 꼴이 다르고 실물 전에는 [추론]이라, 파서는 코드가 아니라 설정(clone 밖
  rules/계좌.toml의 [[source]])으로 정한다. 코드는 "서명이 맞으면 읽고, 하나라도 어긋나면 그 출처 실패"만 한다.
- 출처 식별은 넷 다(3-0 2): (a) 열 이름 목록 정확 일치 (b) 열마다 값 서명(셀 형 + 정규식)
  (c) 열 역할(날짜·금액·취소·할부·승인번호·잔액 …) (d) 파일 식별 값(카드·계좌 뒷자리·별명).
- 행 하나라도 날짜·금액·방향을 해석하지 못하면 그 출처 실패다(errors='coerce'·행 버리기 없음).
- 암호 걸린 엑셀·0바이트 파일은 '건너뜀'이 아니라 실패다(msoffcrypto 없음 — 이월 3).
- HTML 표(.xls 확장자)는 표준 라이브러리 html.parser로 읽는다(실물 현대카드 파일 — 2026-10-27 리허설). 그 출처의
  file_types에 "html"을 적은 출처만 받는다. 로그인 화면이 저장된 HTML은 열 이름 서명에 맞지 않아 실패다.
"""
import csv
import datetime as dt
import hashlib
import html.parser
import io
import os
import re
import tomllib
import zipfile
from dataclasses import dataclass, field

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"
SQLITE_MAGIC = b"SQLite format 3\x00"
ENCRYPTED_MARK = "EncryptedPackage".encode("utf-16-le")

CELL_TYPES = {"s", "n", "d", "t", "b"}     # 문자열 · 숫자 · 날짜시각 · 시각 · 참거짓 ("" = 빈칸)
SOURCE_KINDS = {"card", "bank"}
ROLE_NAMES = {
    "date", "time", "amount", "out", "in", "merchant", "memo", "approval", "balance", "cancel",
    "installment_months", "installment_seq", "billed", "points", "foreign", "pay_date",
    "direction", "category",
}
INSTALLMENT_MODES = {"total", "per_seq", "none"}
# 지점 접미만 뗀다(3-0 6). 브랜드에 붙은 숫자(이마트24·GS25)는 남는다.
BRANCH_SUFFIX = re.compile(r"\s?\d+(호)?점$")


class SourceError(Exception):
    """출처 실패 — 요란하게 멈춘다. 메시지에 파일·행·이유가 들어간다."""


class ConfigError(Exception):
    """계좌.toml이 규칙에 맞지 않다."""


# ------------------------------------------------------------
# 파일 읽기
# ------------------------------------------------------------

def sniff(data):
    """파일 바이트 → 'empty' | 'xlsx' | 'zip' | 'xls' | 'encrypted' | 'html' | 'sqlite' | 'text'."""
    if not data:
        return "empty"
    if data.startswith(ZIP_MAGIC):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                return "xlsx" if "xl/workbook.xml" in z.namelist() else "zip"
        except zipfile.BadZipFile:
            return "zip"
    if data.startswith(OLE_MAGIC):
        # 암호 걸린 xlsx는 OLE 상자 안에 EncryptedPackage 흐름이 있다(엑셀 '암호 설정' 저장).
        return "encrypted" if ENCRYPTED_MARK in data else "xls"
    if data[:2] in (b"\x09\x08", b"\x09\x04", b"\x09\x02", b"\x09\x00"):
        return "xls"        # OLE 상자 없는 BIFF 흐름(xlrd가 그대로 읽는다)
    if data.startswith(SQLITE_MAGIC):
        return "sqlite"
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith(b"<") and (b"<html" in head or b"<table" in head or b"<!doctype" in head):
        return "html"
    return "text"


def _cell(v):
    """파이썬 값 → (형, 값). 빈 문자열·공백만 있는 문자열은 빈칸."""
    if v is None:
        return ("", None)
    if isinstance(v, bool):
        return ("b", v)
    if isinstance(v, (int, float)):
        return ("n", v)
    if isinstance(v, dt.datetime):
        return ("d", v)
    if isinstance(v, dt.date):
        return ("d", dt.datetime(v.year, v.month, v.day))
    if isinstance(v, dt.time):
        return ("t", v)
    s = str(v)
    return ("", None) if s.strip() == "" else ("s", s)


def _read_xlsx(data):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        return [(ws.title, [[_cell(v) for v in row] for row in ws.iter_rows(values_only=True)])
                for ws in wb.worksheets]
    finally:
        wb.close()


def _read_xls(data):
    import xlrd
    book = xlrd.open_workbook(file_contents=data)
    sheets = []
    for sh in book.sheets():
        rows = []
        for r in range(sh.nrows):
            row = []
            for c in range(sh.ncols):
                t, v = sh.cell_type(r, c), sh.cell_value(r, c)
                if t == xlrd.XL_CELL_DATE:
                    row.append(("d", xlrd.xldate.xldate_as_datetime(v, book.datemode)))
                elif t == xlrd.XL_CELL_NUMBER:
                    row.append(("n", v))
                elif t == xlrd.XL_CELL_TEXT:
                    row.append(_cell(v))
                elif t == xlrd.XL_CELL_BOOLEAN:
                    row.append(("b", bool(v)))
                elif t == xlrd.XL_CELL_ERROR:
                    row.append(("s", f"#ERR{v}"))
                else:
                    row.append(("", None))
            rows.append(row)
        sheets.append((sh.name, rows))
    return sheets


class _HtmlRows(html.parser.HTMLParser):
    """<tr>마다 한 행, <td>/<th>마다 한 칸(문서 순서). 스크립트 안 글자는 칸이 아니다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def _read_html(data):
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SourceError("HTML 글자 인코딩을 읽을 수 없다(UTF-8·CP949 둘 다 아님)")
    p = _HtmlRows()
    p.feed(text)
    p.close()
    return [("html", [[_cell(v) for v in r] for r in p.rows])]


def _read_text(data):
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SourceError("글자 인코딩을 읽을 수 없다(UTF-8·CP949 둘 다 아님)")
    lines = [ln for ln in text.splitlines() if ln.strip()][:20]
    delim = "\t" if sum(ln.count("\t") for ln in lines) > sum(ln.count(",") for ln in lines) else ","
    rows = [[_cell(v) for v in r] for r in csv.reader(io.StringIO(text, newline=""), delimiter=delim)]
    return [("", rows)]


def read_table(path):
    """파일 → (종류, [(시트 이름, [[(형, 값), …], …]), …]). 읽을 수 없는 꼴은 SourceError."""
    with open(path, "rb") as f:
        data = f.read()
    kind = sniff(data)
    if kind == "empty":
        raise SourceError("0바이트 파일")
    if kind == "encrypted":
        raise SourceError("암호 걸린 엑셀 — 엑셀에서 열어 암호 없이 '다른 이름으로 저장'한 파일만 둔다(고른 항목 4)")
    if kind in ("zip", "sqlite"):
        raise SourceError(f"내역 파일이 아닌 {kind} 파일")
    try:
        if kind == "xlsx":
            return kind, _read_xlsx(data)
        if kind == "xls":
            return kind, _read_xls(data)
        if kind == "html":
            return kind, _read_html(data)
    except SourceError:
        raise
    except Exception as e:  # noqa: BLE001 — 깨진 엑셀은 그 출처 실패로
        raise SourceError(f"{kind} 파일을 읽지 못했다: {type(e).__name__}: {e}") from None
    return kind, _read_text(data)


def cell_text(c):
    """셀 → 비교·서명용 문자열(앞뒤 공백 제거). 숫자는 정수면 정수 꼴."""
    t, v = c
    if t == "":
        return ""
    if t == "n":
        return str(int(v)) if float(v).is_integer() else repr(float(v))
    if t == "d":
        return v.isoformat(sep=" ")
    if t == "t":
        return v.isoformat()
    return str(v).strip()


def row_texts(row):
    out = [cell_text(c) for c in row]
    while out and out[-1] == "":
        out.pop()
    return out


# ------------------------------------------------------------
# 설정(계좌.toml)
# ------------------------------------------------------------

@dataclass
class ColumnSig:
    types: set
    regex: object = None
    empty_ok: bool = False


@dataclass
class Source:
    id: str
    kind: str
    account: str                  # bank: 그 통장(결제수단) · card: 청구 계좌(결제수단 기본값)
    card: str = ""                # card: 비고에 적는 카드명
    file_types: tuple = ()
    sheet: str = ""
    columns: tuple = ()
    header_max_row: int = 30
    identity: tuple = ()
    identity_column: str = ""
    identity_weak: bool = False
    weak_key: bool = False
    date_basis: str = ""
    installment_mode: str = "none"
    roles: dict = field(default_factory=dict)
    values: dict = field(default_factory=dict)
    date_formats: tuple = ()
    time_formats: tuple = ()
    footer_re: object = None
    period_re: object = None
    cancel_values: tuple = ()
    foreign_values: tuple = ()
    pay_day: int = 0
    cycle_months_before: int = 1
    cycle_start_day: int = 1
    exclude_rows: tuple = ()      # ((열, (값, …)), …) — 다른 카드 행(체크카드) 등. 건수는 보고에 남는다
    count_re: object = None       # 파일 머리의 '총건수' — 행 수와 다르면 실패(잘린 파일)
    direction_values: dict = field(default_factory=dict)   # 통장 구분 열 값 → in/out
    cancel_by_merchant: bool = False   # 취소 행의 승인번호가 원거래와 다른 출처(가맹점·금액으로 짝)


def _anchored(p):
    """적요 패턴: 앞·뒤 앵커 정규식이거나(^…·…$) 정확 일치 문자열(3-0 5)."""
    if p.startswith("^") or p.endswith("$"):
        return re.compile(p)
    return re.compile("^" + re.escape(p) + "$")


def load_config(path, account_names, card_names):
    """계좌.toml → dict. 규칙에 어긋나면 ConfigError(목록)."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    errs = []
    cfg = {
        "start_date": None,
        "slack": {"card": 2, "bank": 0},
        "sources": [],
        "transfer": [],
        "transfer_ambiguous": [],
        "transfer_pair": {},
        "card_payment": [],
        "card_transfer": [],
        "income": [],
        "platform_keys": [],
        "platform_fixed": [],
        "ignore_files": [],
        "pre_asof_billing": [],
        "internal": [],
        "internal_balance": [],
        "raw": raw,
    }
    try:
        cfg["start_date"] = dt.date.fromisoformat(str(raw.get("start_date", "")))
    except ValueError:
        errs.append("start_date(YYYY-MM-DD)가 없다")
    sl = raw.get("date_slack", {})
    cfg["slack"] = {"card": int(sl.get("card", 2)), "bank": int(sl.get("bank", 0))}

    seen = set()
    for i, s in enumerate(raw.get("source", [])):
        where = f"[[source]] {i + 1}번째({s.get('id', '?')})"
        sid = str(s.get("id", "")).strip()
        if not sid or sid in seen or "|" in sid:
            errs.append(f"{where}: id가 비었거나 겹치거나 '|'가 들어 있다")
        seen.add(sid)
        kind = s.get("kind")
        if kind not in SOURCE_KINDS:
            errs.append(f"{where}: kind는 card/bank")
        acct = s.get("account", "")
        if acct not in account_names:
            errs.append(f"{where}: account '{acct}'가 pipeline ACCOUNT_KEY 값이 아니다")
        card = s.get("card", "")
        if kind == "card" and card not in card_names:
            errs.append(f"{where}: card '{card}'가 pipeline CARD_TARGETS 카드명이 아니다")
        cols = tuple(str(c).strip() for c in s.get("columns", []))
        if not cols or len(set(cols)) != len(cols):
            errs.append(f"{where}: columns가 비었거나 겹친다")
        roles = dict(s.get("roles", {}))
        for r, col in roles.items():
            if r not in ROLE_NAMES:
                errs.append(f"{where}: 모르는 역할 {r}")
            for c in (col if isinstance(col, list) else [col]):
                if c and c not in cols:
                    errs.append(f"{where}: 역할 {r}의 열 '{c}'가 columns에 없다")
        if not roles.get("date"):
            errs.append(f"{where}: 역할 date가 없다")
        if kind == "card" and not roles.get("amount"):
            errs.append(f"{where}: 카드 출처는 역할 amount가 있어야 한다")
        if kind == "bank" and not ((roles.get("out") and roles.get("in")) or roles.get("amount")):
            errs.append(f"{where}: 통장 출처는 역할 out·in(출금·입금 열) 또는 amount(부호 있는 금액 한 열)가 있어야 한다")
        dvals = dict(s.get("direction_values", {}))
        if roles.get("direction") and not (dvals and set(dvals.values()) <= {"in", "out"}):
            errs.append(f"{where}: 역할 direction이 있으면 direction_values(열 값 → in/out)가 있어야 한다")
        if not roles.get("merchant"):
            errs.append(f"{where}: 역할 merchant(가맹점·내용)가 없다")
        has_strong = any(roles.get(k) for k in ("approval", "time", "balance"))
        weak = bool(s.get("weak_key", False))
        if not has_strong and not weak:
            errs.append(f"{where}: 승인번호·시각·잔액 열이 없는 출처는 weak_key = true('키 약함')를 적어야 파싱한다")
        values = {}
        for c in cols:
            spec = (s.get("values") or {}).get(c)
            if spec is None:
                errs.append(f"{where}: 열 '{c}'의 값 서명(values)이 없다 — 열마다 셀 형·형식을 적는다")
                continue
            types = spec.get("type", [])
            types = set([types] if isinstance(types, str) else types)
            if not types or not (types <= CELL_TYPES | {"any"}):
                errs.append(f"{where}: 열 '{c}'의 type은 s/n/d/t/b/any 중에서")
            try:
                rx = re.compile(spec["re"]) if spec.get("re") else None
            except re.error as e:
                errs.append(f"{where}: 열 '{c}' 정규식 오류 {e}")
                rx = None
            values[c] = ColumnSig(types, rx, bool(spec.get("empty_ok", False)))
        ident = tuple(str(x) for x in s.get("identity", []) if str(x).strip())
        ident_weak = bool(s.get("identity_weak", False))
        if not ident and not ident_weak:
            errs.append(f"{where}: identity(파일 식별 값 — 카드·계좌 뒷자리·별명)가 없다. "
                        f"정말 없으면 identity_weak = true")
        imode = s.get("installment_mode", "none")
        if imode not in INSTALLMENT_MODES:
            errs.append(f"{where}: installment_mode는 total/per_seq/none")
        if imode == "total" and not roles.get("installment_months"):
            errs.append(f"{where}: installment_mode=total이면 역할 installment_months가 있어야 한다")
        if imode == "per_seq" and not roles.get("installment_seq"):
            errs.append(f"{where}: installment_mode=per_seq이면 역할 installment_seq가 있어야 한다")
        basis = s.get("date_basis", "")
        if kind == "card" and basis not in ("승인일", "매입일"):
            errs.append(f"{where}: 카드 date_basis는 승인일/매입일")
        try:
            footer = re.compile(s["footer_re"]) if s.get("footer_re") else None
            period = re.compile(s["period_re"]) if s.get("period_re") else None
            count = re.compile(s["count_re"]) if s.get("count_re") else None
        except re.error as e:
            errs.append(f"{where}: footer_re/period_re/count_re 정규식 오류 {e}")
            footer = period = count = None
        excl = []
        for x in s.get("exclude_rows", []):
            if x.get("column") not in cols or not x.get("values"):
                errs.append(f"{where}: exclude_rows는 column(columns 안)·values가 있어야 한다")
                continue
            excl.append((x["column"], tuple(str(v) for v in x["values"])))
        fmts = tuple(s.get("date_formats", []))
        if "d" not in values.get(roles.get("date", ""), ColumnSig(set())).types and not fmts:
            errs.append(f"{where}: 날짜가 문자열이면 date_formats가 있어야 한다")
        if fmts and any("%Y" not in f and "%y" not in f for f in fmts) and not period:
            errs.append(f"{where}: 연도 없는 날짜 형식은 period_re(파일 머리의 조회기간)가 있어야 한다")
        cfg["sources"].append(Source(
            id=sid, kind=kind, account=acct, card=card if kind == "card" else "",
            file_types=tuple(s.get("file_types", ["xlsx", "xls", "text"])),
            sheet=s.get("sheet", ""), columns=cols, header_max_row=int(s.get("header_max_row", 30)),
            identity=ident, identity_column=s.get("identity_column", ""), identity_weak=ident_weak,
            weak_key=weak, date_basis=basis, installment_mode=imode, roles=roles, values=values,
            date_formats=fmts, time_formats=tuple(s.get("time_formats", [])),
            footer_re=footer, period_re=period,
            cancel_values=tuple(s.get("cancel_values", [])), foreign_values=tuple(s.get("foreign_values", [])),
            pay_day=int(s.get("pay_day", 0)), cycle_months_before=int(s.get("cycle_months_before", 1)),
            cycle_start_day=int(s.get("cycle_start_day", 1)),
            exclude_rows=tuple(excl), count_re=count, direction_values=dvals,
            cancel_by_merchant=bool(s.get("cancel_by_merchant", False)),
        ))
        if kind == "card" and not 1 <= cfg["sources"][-1].pay_day <= 31:
            errs.append(f"{where}: 카드 pay_day(결제일 1~31)가 없다")

    bank_accounts = {s.account for s in cfg["sources"] if s.kind == "bank"}

    def fld(t, where):
        f_ = t.get("field", "all")
        if f_ not in ("all", "merchant", "memo", "category"):
            errs.append(f"{where}: field는 all/merchant/memo/category")
        return f_

    def pats(lst, where):
        out = []
        for p in lst:
            try:
                out.append(_anchored(str(p)))
            except re.error as e:
                errs.append(f"{where}: 패턴 {p!r} 오류 {e}")
        return out

    for t in raw.get("transfer", []):
        where = f"[[transfer]] {t.get('account')}→{t.get('counterpart')}"
        if t.get("account") not in bank_accounts:
            errs.append(f"{where}: account는 파일이 있는 통장(bank 출처의 account)")
        if t.get("counterpart") not in account_names or t.get("counterpart") == t.get("account"):
            errs.append(f"{where}: counterpart는 다른 계좌(ACCOUNT_KEY 값)")
        cfg["transfer"].append({"account": t.get("account"), "counterpart": t.get("counterpart"),
                                "direction": t.get("direction", "both"), "field": fld(t, where),
                                "patterns": pats(t.get("patterns", []), where)})
    for t in raw.get("transfer_ambiguous", []):
        where = f"[[transfer_ambiguous]] {t.get('account')}"
        cands = list(t.get("candidates", []))
        if not set(cands) <= set(account_names) or t.get("default", cands[0] if cands else "") not in cands:
            errs.append(f"{where}: candidates·default는 ACCOUNT_KEY 값")
        cfg["transfer_ambiguous"].append({"account": t.get("account"), "candidates": cands,
                                          "default": t.get("default", cands[0] if cands else ""),
                                          "direction": t.get("direction", "both"), "field": fld(t, where),
                                          "patterns": pats(t.get("patterns", []), where)})
    for t in raw.get("transfer_pair", []):
        k = (t.get("from"), t.get("to"))
        if not set(k) <= set(account_names):
            errs.append(f"[[transfer_pair]] {k}: from·to는 ACCOUNT_KEY 값")
        # auto_only_with_income: 이 쌍은 출금 계좌(from)에 같은 날·같은 금액의 자동 수입([[income]] 규칙)이 있을 때만
        # 자동 이체 행 — 그 밖은 '확인 필요'(받은 수입을 옮긴 돈과 귀속 구독값을 채운 돈을 가른다)
        gate = t.get("auto_only_with_income", False)
        if not isinstance(gate, bool) or (gate and t.get("from") not in bank_accounts):
            errs.append(f"[[transfer_pair]] {k}: auto_only_with_income은 true/false, true면 from이 파일이 있는 통장")
        cfg["transfer_pair"][k] = {"대분류": t.get("대분류", "이체"), "세부내용": t.get("세부내용", ""),
                                   "고정여부": t.get("고정여부", ""), "auto_only_with_income": gate is True}
    for t in raw.get("card_payment", []):
        where = f"[[card_payment]] {t.get('card')}"
        if t.get("card") not in card_names or t.get("account") not in bank_accounts:
            errs.append(f"{where}: card는 카드명, account는 파일이 있는 통장")
        cfg["card_payment"].append({"account": t.get("account"), "card": t.get("card"), "field": fld(t, where),
                                    "patterns": pats(t.get("patterns", []), where)})
    card_billing = {s.card: s.account for s in cfg["sources"] if s.kind == "card"}
    for t in raw.get("card_transfer", []):
        # 카드값 이체 창: 청구 계좌에서 카드결제 계좌(자동화 밖)로 옮기는 본인 이름 이체 — 결제일(그 카드 source의
        # pay_day) −days_before일 ~ 결제일 안에서 patterns에 맞으면 '이체 상대 모름' 질문의 제안이 카드대금(카드명)
        where = f"[[card_transfer]] {t.get('card')}"
        days = t.get("days_before", 3)
        if t.get("card") not in card_billing:
            errs.append(f"{where}: card는 카드 출처([[source]] kind=card)가 있는 카드명")
        elif t.get("account") != card_billing[t.get("card")]:
            errs.append(f"{where}: account는 그 카드의 청구 계좌({card_billing[t.get('card')]})")
        if not isinstance(days, int) or isinstance(days, bool) or not 0 <= days <= 10:
            errs.append(f"{where}: days_before는 0~10 정수")
            days = 3
        if not t.get("patterns"):
            errs.append(f"{where}: patterns(이체 상대 — 예: 본인 이름)가 없다")
        cfg["card_transfer"].append({"account": t.get("account"), "card": t.get("card"), "field": fld(t, where),
                                     "patterns": pats(t.get("patterns", []), where), "days_before": days})
    for t in raw.get("income", []):
        where = f"[[income]] {t.get('account')}"
        if t.get("account") not in bank_accounts:
            errs.append(f"{where}: account는 파일이 있는 통장")
        if t.get("고정여부", "") not in ("고정", ""):
            errs.append(f"{where}: 고정여부는 '고정' 또는 빈칸")
        cfg["income"].append({"account": t.get("account"), "patterns": pats(t.get("patterns", []), where),
                              "field": fld(t, where),
                              "대분류": t.get("대분류", ""), "소분류": t.get("소분류", ""),
                              "세부내용": t.get("세부내용", ""), "고정여부": t.get("고정여부", "")})
    for t in raw.get("internal", []):
        where = f"[[internal]] {t.get('account')} {t.get('name')}"
        if t.get("account") not in bank_accounts or not t.get("name"):
            errs.append(f"{where}: account는 파일이 있는 통장, name(칸 이름 — 예: 세이프박스)이 있어야 한다")
        cfg["internal"].append({"account": t.get("account"), "name": t.get("name", ""), "field": fld(t, where),
                                "patterns": pats(t.get("patterns", []), where)})
    for b in raw.get("internal_balance", []):
        try:
            cfg["internal_balance"].append({"account": b["account"], "name": b["name"],
                                            "date": dt.date.fromisoformat(str(b["date"])), "amount": int(b["amount"])})
        except (KeyError, ValueError):
            errs.append("[[internal_balance]]: account·name·date(YYYY-MM-DD)·amount")
    cfg["platform_keys"] = [normalize_merchant(k) for k in raw.get("platform_keys", []) if str(k).strip()]
    for t in raw.get("platform_fixed", []):
        # 플랫폼 결제(늘 묻는 가맹점) 중 카드·금액이 정확히 같은 것만 묻지 않고 이 값으로 쓴다 — 예: 쿠팡 월 회비
        where = f"[[platform_fixed]] {t.get('merchant')} {t.get('card')} {t.get('amount')}"
        key = normalize_merchant(t.get("merchant", ""))
        if not key:
            errs.append(f"{where}: merchant(플랫폼 가맹점 이름 일부)가 없다")
        elif not any(pk and pk in key for pk in cfg["platform_keys"]):
            errs.append(f"{where}: merchant가 platform_keys에 안 걸린다(플랫폼이 아니면 분류 규칙을 쓴다)")
        if t.get("card") not in card_billing:
            errs.append(f"{where}: card는 카드 출처([[source]] kind=card)가 있는 카드명")
        amount = t.get("amount")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1:
            errs.append(f"{where}: amount는 1 이상 정수(원)")
        for c in ("대분류", "소분류", "세부내용"):
            if not str(t.get(c, "")).strip():
                errs.append(f"{where}: {c}가 비었다")
        if t.get("고정여부", "") not in ("고정", ""):
            errs.append(f"{where}: 고정여부는 '고정' 또는 빈칸")
        if t.get("결제수단", "") and t.get("결제수단") not in account_names:
            errs.append(f"{where}: 결제수단은 계좌 이름 또는 빈칸(빈칸 = 카드 청구 계좌)")
        cfg["platform_fixed"].append({"key": key, "card": t.get("card"), "amount": amount,
                                      **{c: str(t.get(c, "")).strip()
                                         for c in ("대분류", "소분류", "세부내용", "고정여부", "결제수단")}})
    for f_ in raw.get("ignore_files", []):
        if not f_.get("path") or not re.fullmatch(r"[0-9a-f]{32}", str(f_.get("md5", ""))):
            errs.append("[[ignore_files]]: path와 md5(32자)가 둘 다 있어야 한다")
        cfg["ignore_files"].append({"path": str(f_.get("path", "")).replace("\\", "/"),
                                    "md5": str(f_.get("md5", "")), "reason": f_.get("reason", "")})
    for b in raw.get("pre_asof_billing", []):
        try:
            cfg["pre_asof_billing"].append({"account": b["account"], "card": b["card"],
                                            "pay_month": str(b["pay_month"]), "amount": int(b["amount"])})
        except (KeyError, ValueError):
            errs.append("[[pre_asof_billing]]: account·card·pay_month·amount")
    if errs:
        raise ConfigError(errs)
    return cfg


# ------------------------------------------------------------
# 출처 식별
# ------------------------------------------------------------

def find_header(rows, src):
    want = list(src.columns)
    for i, row in enumerate(rows[: src.header_max_row]):
        if row_texts(row) == want:
            return i
    return None


def _squash(s):
    return re.sub(r"\s+", "", s)


def identify(sheets, src):
    """(sheet_index, header_row) 또는 None(열 이름 불일치) — 식별 값까지 맞아야 한다.
    열은 맞는데 식별 값이 다르면 SourceError(다른 카드·계좌 파일 — K12)."""
    for si, (name, rows) in enumerate(sheets):
        if src.sheet and name != src.sheet:
            continue
        h = find_header(rows, src)
        if h is None:
            continue
        if src.identity_weak and not src.identity:
            return si, h
        idents = [_squash(x) for x in src.identity]
        if src.identity_column:
            ci = list(src.columns).index(src.identity_column)
            cols = list(src.columns)
            body = [r for r in _data_rows(rows, h, src)[0]
                    if not any(cell_text(_col(list(r) + [("", None)] * len(cols), cols, c)) in vals_
                               for c, vals_ in src.exclude_rows)]
            vals = [_squash(cell_text(r[ci])) if ci < len(r) else "" for r in body]
            if vals and all(any(x in v for x in idents) for v in vals):
                return si, h
            raise SourceError(f"식별 열 '{src.identity_column}' 값이 설정한 카드·계좌({', '.join(src.identity)})와 "
                              f"다르다 — 다른 카드·계좌 파일")
        head = _squash(" ".join(" ".join(row_texts(r)) for r in rows[:h])) + _squash(name)
        if any(x in head for x in idents):
            return si, h
        raise SourceError(f"파일 머리에 식별 값({', '.join(src.identity)})이 없다 — 다른 카드·계좌 파일이거나 "
                          f"전체 카드 화면 파일")
    return None


def _is_blank(row):
    return all(t == "" for t, _ in row)


def _data_rows(rows, h, src):
    """머리 아래 데이터 행 → (행 목록, 원래 행 번호 목록, 표 뒤 행 목록)."""
    data, idx = [], []
    end = len(rows)
    for i in range(h + 1, len(rows)):
        row = rows[i]
        if _is_blank(row):
            end = i
            break
        texts = row_texts(row)
        if src.footer_re and texts and src.footer_re.search(" ".join(t for t in texts if t)):
            end = i
            break
        data.append(row)
        idx.append(i)
    return data, idx, rows[end:]


# ------------------------------------------------------------
# 값 해석
# ------------------------------------------------------------

def parse_int_amount(c):
    """셀 → 정수 원. 숫자 셀은 정수여야 하고, 문자열은 쉼표·'원'·괄호 음수·부호를 받는다. 못 읽으면 ValueError."""
    t, v = c
    if t == "":
        return 0
    if t == "n":
        if not float(v).is_integer():
            raise ValueError(f"금액이 정수가 아니다: {v!r}")
        return int(v)
    if t != "s":
        raise ValueError(f"금액 칸이 숫자·문자가 아니다: {t}")
    s = str(v).strip().replace(",", "").replace("원", "").replace(" ", "")
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    if not re.fullmatch(r"[+-]?\d+", s):
        raise ValueError(f"금액을 읽을 수 없다: {v!r}")
    n = int(s)
    return -n if neg else n


def _with_year(d, period):
    if not period:
        raise ValueError("연도 없는 날짜인데 파일 머리의 조회기간을 못 읽었다")
    start, end = period
    for y in sorted({start.year, end.year}):
        try:
            cand = d.replace(year=y)
        except ValueError:
            continue
        if start <= cand.date() <= end:
            return cand
    raise ValueError(f"연도 없는 날짜 {d:%m-%d}가 조회기간 {start}~{end} 밖이다")


def parse_datetime(c, src, period):
    """셀 → datetime. 문자열은 date_formats 중 하나와 정확히 맞아야 한다."""
    t, v = c
    if t == "d":
        return v
    if t in ("s", "n"):
        s = cell_text(c)
        for fmt in src.date_formats:
            try:
                d = dt.datetime.strptime(s, fmt)
            except ValueError:
                continue
            if "%Y" not in fmt and "%y" not in fmt:
                d = _with_year(d, period)
            return d
    raise ValueError(f"날짜를 읽을 수 없다: {cell_text(c)!r}")


def parse_time(c, src):
    t, v = c
    if t == "":
        return ""
    if t == "t":
        return v.strftime("%H:%M:%S")
    if t == "d":
        return v.strftime("%H:%M:%S")
    s = cell_text(c)
    for fmt in src.time_formats or ("%H:%M:%S", "%H:%M"):
        try:
            return dt.datetime.strptime(s, fmt).strftime("%H:%M:%S")
        except ValueError:
            continue
    raise ValueError(f"시각을 읽을 수 없다: {s!r}")


def header_period(rows, h, src):
    """파일 머리의 조회기간(연도 없는 날짜 보충용) → (시작, 끝) 또는 None."""
    if not src.period_re:
        return None
    text = " ".join(" ".join(row_texts(r)) for r in rows[:h])
    m = src.period_re.search(text)
    if not m:
        return None
    g = [int(x) for x in m.groups()[:6]]
    try:
        return dt.date(g[0], g[1], g[2]), dt.date(g[3], g[4], g[5])
    except (ValueError, IndexError):
        return None


def normalize_merchant(s):
    """분류 규칙 키: 공백·대소문자 + 끝의 지점 접미만 뗀다(3-0 6)."""
    s = re.sub(r"\s+", "", str(s)).lower()
    return BRANCH_SUFFIX.sub("", s)


@dataclass
class Tx:
    source: str
    kind: str
    account: str
    card: str
    file: str
    row: int
    date: dt.date
    time: str
    amount: int                  # 카드: 파일 값 그대로(취소 음수면 음수) · 통장: 양수
    direction: str               # 카드 spend/cancel · 통장 out/in
    merchant: str
    memo: tuple
    approval: str = ""
    status: str = ""
    cancel: bool = False
    inst_months: int = 0
    inst_seq: int = 0
    inst_total: int = 0
    foreign: bool = False
    points: int = 0
    billed: object = None
    pay_date: object = None
    balance: object = None
    raw_hash: str = ""
    base: str = ""
    seq: int = 0
    key: str = ""
    order: int = 0               # 잔액 열이 이어지는 시간순 자리(파일 안)

    def to_json(self):
        d = dict(self.__dict__)
        d["date"] = self.date.isoformat()
        d["pay_date"] = self.pay_date.isoformat() if self.pay_date else None
        d["memo"] = list(self.memo)
        return d

    @classmethod
    def from_json(cls, d):
        d = dict(d)
        d["date"] = dt.date.fromisoformat(d["date"])
        d["pay_date"] = dt.date.fromisoformat(d["pay_date"]) if d.get("pay_date") else None
        d["memo"] = tuple(d.get("memo") or ())
        return cls(**d)


def _col(row, cols, name):
    if not name:
        return ("", None)
    i = cols.index(name)
    return row[i] if i < len(row) else ("", None)


def _inst(c):
    """할부 개월: '' · 일시불 · 0 · 1 → 0, '3' · '3개월' · '할부(12개월)' → 개월 수."""
    s = cell_text(c)
    if s in ("", "일시불", "0", "1"):
        return 0
    m = re.fullmatch(r"(\d+)(?:개월)?", s) or re.fullmatch(r"할부\s*\(?\s*(\d+)\s*개월\s*\)?", s)
    if not m:
        raise ValueError(f"할부 개월을 읽을 수 없다: {s!r}")
    n = int(m.group(1))
    return 0 if n <= 1 else n


def _seq(c):
    s = cell_text(c)
    if s == "":
        return 0, 0
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", s) or re.fullmatch(r"(\d+)", s)
    if not m:
        raise ValueError(f"할부 회차를 읽을 수 없다: {s!r}")
    return int(m.group(1)), int(m.group(2)) if m.lastindex == 2 else 0


def parse_sheet(rows, h, src, relpath, stats=None):
    """머리 아래 행 → [Tx]. 행 하나라도 서명·해석이 어긋나면 SourceError(파일:행·이유).
    stats(dict)에 설정으로 뺀 행 수(excluded)를 적는다."""
    cols = list(src.columns)
    data, idx, after = _data_rows(rows, h, src)
    period = header_period(rows, h, src)
    if src.count_re:
        text = " ".join(" ".join(row_texts(r)) for r in rows[:h])
        m = src.count_re.search(text)
        if not m:
            raise SourceError(f"{relpath}: 파일 머리의 총건수를 못 읽었다(count_re)")
        if int(m.group(1)) != len(data):
            raise SourceError(f"{relpath}: 파일 머리 총건수 {m.group(1)} ≠ 표의 행 {len(data)} — 잘리거나 섞인 파일")
    excluded = 0
    out = []
    for row, ri in zip(data, idx):
        if src.exclude_rows:
            cells0 = list(row) + [("", None)] * (len(cols) - len(row))
            if any(cell_text(_col(cells0, cols, c)) in vals for c, vals in src.exclude_rows):
                excluded += 1
                continue
        where = f"{relpath}:{ri + 1}행"
        cells = list(row) + [("", None)] * (len(cols) - len(row))
        if any(t != "" for t, _ in cells[len(cols):]):
            raise SourceError(f"{where}: 열 수보다 칸이 많다")
        for c, cell in zip(cols, cells):
            sig = src.values[c]
            t = cell[0]
            if t == "":
                if not sig.empty_ok:
                    raise SourceError(f"{where}: 열 '{c}'가 비었다(값 서명은 빈칸 금지)")
                continue
            if "any" not in sig.types and t not in sig.types:
                raise SourceError(f"{where}: 열 '{c}'의 셀 형 {t} ≠ 서명 {sorted(sig.types)} "
                                  f"(엑셀에서 다시 저장해 형이 바뀌었는지 확인)")
            if sig.regex and not sig.regex.fullmatch(cell_text(cell)):
                raise SourceError(f"{where}: 열 '{c}' 값 꼴이 서명과 다르다")
        r = src.roles
        try:
            d = parse_datetime(_col(cells, cols, r.get("date")), src, period)
            tm = parse_time(_col(cells, cols, r.get("time")), src) if r.get("time") else (
                d.strftime("%H:%M:%S") if d.time() != dt.time(0) else "")
            merchant = cell_text(_col(cells, cols, r.get("merchant")))
            memo_cols = r.get("memo") or []
            memo_cols = memo_cols if isinstance(memo_cols, list) else [memo_cols]
            memo = tuple(cell_text(_col(cells, cols, m)) for m in memo_cols)
            approval = cell_text(_col(cells, cols, r.get("approval"))) if r.get("approval") else ""
            status = cell_text(_col(cells, cols, r.get("cancel"))) if r.get("cancel") else ""
            balance = parse_int_amount(_col(cells, cols, r.get("balance"))) if r.get("balance") else None
            if src.kind == "bank" and r.get("out"):
                o = parse_int_amount(_col(cells, cols, r["out"]))
                i = parse_int_amount(_col(cells, cols, r["in"]))
                if o < 0 or i < 0 or (o > 0) == (i > 0):
                    raise ValueError("입금·출금 중 정확히 하나만 양수여야 한다")
                amount, direction, cancel = (o, "out", False) if o > 0 else (i, "in", False)
            elif src.kind == "bank":
                # 부호 있는 금액 한 열(음수 = 출금). 구분 열이 있으면 부호와 같아야 한다
                a = parse_int_amount(_col(cells, cols, r["amount"]))
                if a == 0:
                    raise ValueError("통장 금액이 0원")
                amount, direction, cancel = abs(a), ("out" if a < 0 else "in"), False
                if r.get("direction"):
                    dv = cell_text(_col(cells, cols, r["direction"]))
                    if src.direction_values.get(dv) != direction:
                        raise ValueError(f"구분 열 값 {dv!r}이 금액 부호({direction})와 맞지 않다")
            else:
                amount = parse_int_amount(_col(cells, cols, r["amount"]))
                if amount == 0:
                    raise ValueError("카드 금액이 0원")
                cancel = (amount < 0 or (bool(status) and (status in src.cancel_values or "취소" in status))
                          or (bool(r.get("approval")) and not approval))
                direction = "cancel" if cancel else "spend"
            inst_months = _inst(_col(cells, cols, r.get("installment_months"))) if r.get("installment_months") else 0
            inst_seq, inst_total = _seq(_col(cells, cols, r.get("installment_seq"))) if r.get("installment_seq") else (0, 0)
            billed = parse_int_amount(_col(cells, cols, r.get("billed"))) if r.get("billed") else None
            if r.get("billed") and _col(cells, cols, r.get("billed"))[0] == "":
                billed = None
            points = parse_int_amount(_col(cells, cols, r.get("points"))) if r.get("points") else 0
            fv = cell_text(_col(cells, cols, r.get("foreign"))) if r.get("foreign") else ""
            foreign = (fv in src.foreign_values) if src.foreign_values else (fv not in ("", "0", "KRW", "원", "국내"))
            if src.kind == "bank" and r.get("category"):
                status = cell_text(_col(cells, cols, r["category"]))
            pay_date = (parse_datetime(_col(cells, cols, r.get("pay_date")), src, period).date()
                        if r.get("pay_date") and _col(cells, cols, r.get("pay_date"))[0] != "" else None)
        except ValueError as e:
            raise SourceError(f"{where}: {e}") from None
        raw = "\x1f".join(cell_text(c) for c in cells)
        out.append(Tx(source=src.id, kind=src.kind, account=src.account, card=src.card, file=relpath, row=ri + 1,
                      date=d.date(), time=tm, amount=amount, direction=direction, merchant=merchant, memo=memo,
                      approval=approval, status=status, cancel=cancel, inst_months=inst_months,
                      inst_seq=inst_seq, inst_total=inst_total, foreign=foreign, points=points, billed=billed,
                      pay_date=pay_date, balance=balance,
                      raw_hash=hashlib.md5(raw.encode("utf-8")).hexdigest()))
    # 표가 중간에 끊긴 파일(빈 줄 뒤에 거래 행이 더 있음)은 행이 빠진 파일이다.
    date_col = src.roles.get("date")
    for row in after:
        c = _col(list(row) + [("", None)] * len(cols), cols, date_col)
        if c[0] == "":
            continue
        try:
            parse_datetime(c, src, period)
        except ValueError:
            continue
        raise SourceError(f"{relpath}: 표 끝(빈 줄·합계 줄) 뒤에 거래 날짜가 있는 행이 더 있다 — 표가 끊긴 파일")
    if not out:
        raise SourceError(f"{relpath}: 머리 아래 거래 행이 없다")
    if stats is not None:
        stats["excluded"] = excluded
    return out


def assign_keys(txs, src):
    """거래키 = 출처 + (승인번호+할부회차 | 거래일시+금액+잔액 [+가맹점: 키 약함]) + 같은 값의 파일 안 순번.
    순번은 파일 행 순서가 아니라 (날짜·시각·원거래 먼저·행) 정렬 순서로 매긴다 — 최신순 파일과 오래된순 파일이
    같은 키를 내게(3-0 4)."""
    order = sorted(txs, key=lambda t: (t.date, t.time, t.cancel, t.row))
    counts = {}
    for t in order:
        if t.approval:
            base = f"A{t.approval}/{t.inst_seq}"
        else:
            parts = [t.date.isoformat(), t.time, str(abs(t.amount)), t.direction,
                     "" if t.balance is None else str(t.balance)]
            if src.weak_key and not t.time and t.balance is None:
                parts.append(normalize_merchant(t.merchant))
            base = "D" + "/".join(parts)
        counts[base] = counts.get(base, 0) + 1
        t.base, t.seq = base, counts[base]
        t.key = f"{src.id}|{base}|{t.seq}"
    return txs


def check_balance_chain(txs, relpath):
    """잔액 열 연속성(3-0 8(a)): 앞 잔액 ± 금액 = 잔액. 파일 순서(오래된순·최신순) 둘 중 하나로 이어져야 한다.
    끊기면 SourceError(행이 빠진 파일). 잔액 없는 출처는 None. 이어지면 시간순 목록을 돌려준다."""
    if not txs or txs[0].balance is None:
        return None
    by_row = sorted(txs, key=lambda t: t.row)
    for seq in (by_row, list(reversed(by_row))):
        ok = True
        for a, b in zip(seq, seq[1:]):
            delta = b.amount if b.direction == "in" else -b.amount
            if a.balance + delta != b.balance:
                ok = False
                break
        if ok:
            for i, t in enumerate(seq):
                t.order = i
            return seq
    raise SourceError(f"{relpath}: 잔액 열이 이어지지 않는다(앞 잔액 ± 금액 ≠ 잔액) — 행이 빠졌거나 순서가 섞인 파일")
