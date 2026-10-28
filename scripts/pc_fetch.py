#!/usr/bin/env python3
"""
이 PC(Windows · Code 탭)에서 금융사 내역 파일 받기 — 신한카드·현대카드·신한은행 웹 반자동 내려받기 +
카카오뱅크 메일 첨부 수거·암호 풀기 (내역 받기 설계안 A, 안 C 지정 폴더 수거는 --mail-only 하향 길).

웹(claude.ai)은 이 파일을 쓰지 않는다. 설계·완료 기준: 2026-10-28 내역 받기 탐색 기준선(로컬 기록) 3-0 · 3-A · 3-C · 4절.
설정은 clone 밖 rules/받기.toml(예시 pc/import/받기.toml.example), 출처 서명·식별 값은 rules/계좌.toml을 그대로 쓴다.
흐름 설명은 pc/code-tab.md 12절 "내역 받기".

사용법 (PY = D:\\household\\.venv\\Scripts\\python.exe):
    $PY scripts/pc_fetch.py                 ① dry-run(기본) — 브라우저 0·메일 0·파일 변경 0: 할 일 표만
    $PY scripts/pc_fetch.py --run           ② 전용 크롬으로 신한카드 → 현대카드 → 신한은행(로그인은 사용자) →
                                               카카오뱅크 메일(IMAP 읽기 전용) 수거·복호화 → ③ 새 폴더로 원자 이동
        --mail-only     웹 0 — 카카오뱅크만 지정 폴더(mail_drop.folder)에서 수거(안 C 하향 길)
        --no-web        웹 0 — 카카오뱅크만 IMAP으로      --no-mail   카카오뱅크 0
        --sites 신한카드,현대카드                       웹 출처 일부만
        --start 신한카드=YYYY-MM-DD                    받는 시작일을 계산값보다 앞으로(리허설 >20건 등 — 근거가 표·summary에)
        --config PATH   다른 설정(리허설: paths.inbox_root만 바꾼 사본)     --debug   실패 때 그림·예외 원문을 회차 debug\\에만
        --probe-paging  (리허설 전용) 신한카드 nextKey가 있어도 엑셀을 받아 회차 폴더에만 두고 '받지 못함(실측)'으로 기록
        --try-extend    (리허설 전용) 사이트마다 세션 '연장' 동작을 한 번 해 보고 전·후 남은 초만 기록(실패시키지 않음)
    $PY scripts/pc_fetch.py compare --a DIR [DIR…] --b DIR [DIR…] [--from D --to D]
        받은 묶음 ↔ 손 묶음을 scan과 같은 파서·서명으로 읽어 출처별 거래 건수·합계·잔액·거래키를 대조(금액은 찍지 않음)

종료 코드: 0 전부 받음(0건·이미 있음 포함) · 2 하나라도 받지 못함(받은 것은 ③으로 확정) · 1 시작도 못 함(설정·프로필 잠금·환경).

지키는 것(3-0 · 4절 1~21):
- 금융사 쓰기 0: 클릭은 click_allowed 한 곳(허용 목록·보이는 요소 1개·기대 글자·금지 문구 · iframe 포함), 키 입력은 type_text,
  JS 값·체크·선택은 set_js_value·set_check·set_select — 넷 다 설정의 허용 목록만. 좌표 클릭 0.
- 로그인은 사용자만: 로그인 대기 중 클릭·입력·dialog accept/dismiss 0(리스너는 기록만). 스크립트 단계 dialog는 dismiss + 그 출처 실패.
- 메일 쓰기 0: IMAP은 TLS 검증(ssl.create_default_context) · select(readonly=True) · SEARCH · FETCH(BODY.PEEK·INTERNALDATE·
  RFC822.SIZE) · logout만.
- 기존 파일 덮어쓰기·삭제 0: 받은 파일은 회차 폴더에만 쓰고, 검사를 다 통과한 것만 회차 폴더 stage\\에 모아 폴더째 이름 바꾸기로
  inbox(uploads\\내역\\) 아래 새 폴더 하나로 옮긴다(대상이 있으면 실패 · inbox 안 임시 이름 0).
- 노출 0: 자격 증명은 Windows 자격 증명 관리자에서 읽기만(사용자가 Claude 밖 창에서 cmdkey로 넣는다) · 명령줄·환경변수로 받지 않음 ·
  응답·요청 본문은 건수·기간·필터 값만 뽑고 버림 · 출력은 scrub() 한 곳(긴 숫자·금액·메일 주소 가림) · 스크린샷 기본 0.
"""
import argparse
import datetime as dt
import email
import email.header
import email.policy
import email.utils
import hashlib
import imaplib
import json
import os
import re
import shutil
import sqlite3
import ssl
import sys
import time
import tomllib
import types
from urllib.parse import parse_qsl

# 저장소 안에 __pycache__를 만들지 않는다(pc_import.py와 같은 이유).
sys.dont_write_bytecode = True
for _s in (sys.stdout, sys.stderr):  # Code 탭 파이프(cp949)에서 한글
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:  # pragma: no cover
        pass
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import import_sources as srcmod  # noqa: E402
import pc_import as imp  # noqa: E402

REPO = os.path.dirname(_HERE)
HOME = os.path.dirname(REPO)
DEFAULT_CONFIG = os.path.join(HOME, "rules", "받기.toml")
EXIT_OK, EXIT_STOP, EXIT_PARTIAL = 0, 1, 2
ACTION_TYPES = ("click", "date", "js_date", "check", "select")
CLICK_BY = ("css", "text", "text_regex")
SNIFF_KINDS = ("xlsx", "xls", "html")
PATH_KEYS = ("profile_dir", "runs_root", "inbox_root", "scan_inbox", "accounts", "ledger")
DIALOG_WORDS = ("로그인", "로그아웃", "세션", "시간", "연장", "보안", "설치", "오류", "확인", "인증", "종료")
CRED_HELP = ("자격 증명은 사용자가 **Claude 앱 밖의 별도 cmd 창**에서 넣는다 — "
             "`cmdkey /generic:{target} /user:{user} /pass` (`/pass` 뒤에 값을 붙이지 않고 뜨는 프롬프트에 입력). "
             "Claude는 대신 치지도 묻지도 않고, 그 직후 터미널 판을 읽지 않는다.")


class StopRun(Exception):
    """시작도 못 함(exit 1) — 설정·프로필 잠금·환경."""


class ItemFail(Exception):
    """그 출처 하나의 실패 — 다른 출처와 ③은 계속(3-0 부분 실패)."""


# ------------------------------------------------------------
# 노출 0 — 출력 정화는 여기 한 곳
# ------------------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_AMOUNT = re.compile(r"\d{1,3}(?:,\d{3})+")
_DIGITS = re.compile(r"\d[\d-]{5,}\d|\d{5,}")
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def trust(s):
    """우리가 만든 값(회차 이름·폴더 경로·날짜 폴더) — scrub이 가리지 않는다. 금융사·메일에서 온 글자에는 쓰지 않는다."""
    return f"\x02{s}\x03"


def _valid_iso(t):
    m = _ISO.fullmatch(t)
    if not m:
        return False
    try:
        dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return True
    except ValueError:
        return False


def _scrub_plain(p):
    p = _EMAIL.sub("<메일>", p)
    p = _AMOUNT.sub("#,###", p)
    return _DIGITS.sub(lambda m: m.group(0) if _valid_iso(m.group(0)) else "<숫자>", p)


def src_err(e):
    """import_sources 오류 문구 → 기록용(괄호 안 식별 값·끝의 셀 값 repr을 뺀다 — 열 이름·행 번호만 남김)."""
    msg = re.sub(r"\([^)]*\)", "(…)", str(e))
    return re.sub(r":\s*(['\"]).*?\1\s*$", ": <값>", msg)


def scrub(s):
    """출력·기록 글자 정화: 메일 주소 · 쉼표 금액 · 5자리 이상 숫자 · 하이픈 숫자열(계좌·카드번호·승인번호·파일 이름 숫자)을
    가린다. ISO 날짜(YYYY-MM-DD)와 trust()로 감싼 우리 값만 그대로."""
    parts = re.split(r"\x02(.*?)\x03", str(s), flags=re.S)
    return "".join(p if i % 2 else _scrub_plain(p) for i, p in enumerate(parts))


def scrub_obj(v):
    if isinstance(v, dict):
        return {scrub(k): scrub_obj(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [scrub_obj(x) for x in v]
    if isinstance(v, str):
        return scrub(v)
    return v


def short_hash(s):
    return hashlib.sha256(str(s).encode("utf-8")).hexdigest()[:8]


def text_note(text, expect=None):
    """요소·화면 글자를 기록에 남기는 꼴 — 기대 글자와 같을 때만 원문, 다르면 길이·해시(3-0 노출 0)."""
    t = " ".join(str(text or "").split())
    if expect is not None and t == expect:
        return t
    return f"<글자 {len(t)}자 #{short_hash(t)}>"


def ename(e):
    return type(e).__name__


class Log:
    """출력의 유일한 자리 — 모든 줄이 scrub을 거친다. --debug면 예외 원문은 회차 debug\\errors.txt에만."""

    def __init__(self, debug_dir=None):
        self.lines = []
        self.debug_dir = debug_dir

    def __call__(self, s=""):
        line = scrub(s)
        self.lines.append(line)
        print(line, flush=True)

    def exc(self, where, e):
        if not self.debug_dir:
            return
        try:
            os.makedirs(self.debug_dir, exist_ok=True)
            with open(os.path.join(self.debug_dir, "errors.txt"), "a", encoding="utf-8") as f:
                f.write(f"[{where}] {ename(e)}: {e}\n")
        except OSError:
            pass


# ------------------------------------------------------------
# 설정
# ------------------------------------------------------------

def load_config(path):
    try:
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
    except FileNotFoundError:
        raise StopRun(f"받기 설정이 없다: {trust(path)} — 예시 pc/import/받기.toml.example을 보고 만든다")
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise StopRun(f"받기 설정을 읽을 수 없다: {trust(path)} ({ename(e)})")
    errs = config_problems(cfg)
    if errs:
        raise StopRun("받기 설정 규칙 위반:\n  " + "\n  ".join(errs))
    return cfg


def _expects(spec):
    e = spec.get("expect", [])
    return [e] if isinstance(e, str) else list(e)


def config_problems(cfg):
    """설정 자체의 모순을 실행 전에 막는다(허용 글자 안에 금지 문구가 들어가면 그 버튼은 못 누르거나 검사가 거짓말을 한다)."""
    errs = []
    paths = cfg.get("paths", {})
    errs += [f"paths.{k}가 없다" for k in PATH_KEYS if not paths.get(k)]
    bad_words = [w for w in cfg.get("forbidden_texts", []) if isinstance(w, str) and w.strip()]
    if not bad_words:
        errs.append("forbidden_texts(금지 문구 목록)가 비었다")
    t = cfg.get("timeout", {})
    for k in ("login", "login_stable", "page", "query", "download", "click", "site_step"):
        v = t.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            errs.append(f"timeout.{k}(양수 초)가 없다")
    if not isinstance(cfg.get("period", {}).get("overlap_days"), int):
        errs.append("period.overlap_days(정수)가 없다")
    seen = set()
    for i, s in enumerate(cfg.get("site", [])):
        where = f"[[site]] {i + 1}번째({s.get('id', '?')})"
        sid = s.get("id", "")
        if not sid or sid in seen:
            errs.append(f"{where}: id(계좌.toml [[source]] id)가 비었거나 겹친다")
        seen.add(sid)
        if not str(s.get("start_url", "")).startswith("https://"):
            errs.append(f"{where}: start_url은 https://")
        if s.get("expect_kind") not in SNIFF_KINDS:
            errs.append(f"{where}: expect_kind는 {SNIFF_KINDS} 중 하나")
        marks = s.get("login", {}).get("markers", [])
        if not marks or any(not m.get("css") or not (m.get("text") or m.get("re")) for m in marks):
            errs.append(f"{where}: login.markers(css + text 또는 re)가 비었다")
        acts = s.get("actions", {})
        for name, a in acts.items():
            aw = f"{where} actions.{name}"
            typ = a.get("type", "click")
            if typ not in ACTION_TYPES:
                errs.append(f"{aw}: 모르는 type {typ!r}")
            if a.get("by", "css") not in CLICK_BY or not a.get("value"):
                errs.append(f"{aw}: by는 {CLICK_BY} 중 하나, value 필수")
            if typ in ("click", "check") and not (_expects(a) or a.get("expect_re")):
                errs.append(f"{aw}: expect(누르기 전 대조할 글자) 또는 expect_re 필수")
            if typ in ("date", "js_date") and (a.get("date") not in ("start", "end") or not a.get("fmt")):
                errs.append(f"{aw}: date(start/end)·fmt 필수")
            if typ == "date" and not a.get("shown_fmt"):
                errs.append(f"{aw}: shown_fmt(입력 뒤 칸에 보일 꼴) 필수")
            if typ == "select" and not a.get("option"):
                errs.append(f"{aw}: option(고를 글자) 필수")
            for exp in _expects(a) + ([a["option"]] if a.get("option") else []):
                if not str(exp).strip():
                    errs.append(f"{aw}: 빈 기대 글자")
                for bad in bad_words:
                    if bad in str(exp):
                        errs.append(f"{aw}: 허용 글자 {exp!r} 안에 금지 문구 {bad!r} — 둘 중 하나를 고친다")
        flow = s.get("flow", {})
        names = list(flow.get("prepare", [])) + [flow.get("query", "")] + list(flow.get("excel", [])) \
            + list(flow.get("extend", [])) + list(s.get("screen", {}).get("menu", []))
        for n in names:
            if n not in acts:
                errs.append(f"{where}: flow·screen.menu의 {n!r}가 actions에 없다")
        if not flow.get("excel"):
            errs.append(f"{where}: flow.excel(마지막이 내려받기를 일으키는 동작)이 없다")
        sc = s.get("screen", {})
        if not (sc.get("url") or sc.get("menu")) or not sc.get("title_contains"):
            errs.append(f"{where}: screen.url 또는 screen.menu, screen.title_contains가 있어야 한다")
        q = s.get("query", {})
        if not q.get("url_re") or not q.get("count_path") or q.get("body", "") not in ("form", "json", "") \
                and not str(q.get("body", "")).startswith("form_json:"):
            errs.append(f"{where}: query.url_re·count_path·body(form/json/form_json:<칸>)")
        sv = s.get("screen_values", {})
        for k in ("count_re", "period_re"):
            try:
                re.compile(sv.get(k, ""))
            except re.error as e:
                errs.append(f"{where}: screen_values.{k} 정규식 오류 {e}")
            if not sv.get(k):
                errs.append(f"{where}: screen_values.{k}가 없다")
        ef = s.get("excel_file", {})
        if ef.get("count_where") not in ("head", "tail") or not ef.get("count_re"):
            errs.append(f"{where}: excel_file.count_re·count_where(head/tail)")
    mail = cfg.get("mail")
    if mail:
        for k in ("host", "port", "cred_target", "from", "subject_contains", "attach_re", "since_days", "wait_sec",
                  "wait_tries", "max_candidates"):
            if mail.get(k) in (None, ""):
                errs.append(f"mail.{k}가 없다")
    kk = cfg.get("kakao")
    if kk:
        for k in ("source", "cred_target", "pattern_encrypted", "pattern_plain", "requested_re"):
            if not kk.get(k):
                errs.append(f"kakao.{k}가 없다")
    return errs


def resolve_paths(cfg):
    p = {k: os.path.abspath(str(v)) for k, v in cfg["paths"].items()}
    drop = (cfg.get("mail_drop") or {}).get("folder")
    p["mail_drop"] = os.path.abspath(drop) if drop else None
    return p


def _inside(child, parent):
    c = os.path.normcase(os.path.realpath(child)) + os.sep
    q = os.path.normcase(os.path.realpath(parent)) + os.sep
    return c.startswith(q)


def path_problems(paths):
    """회차 폴더·프로필·설정 사본이 inbox 안이면 scan이 서명 불일치로 읽는다(1-1 4) · 실데이터는 clone 밖(3-0 11)."""
    bad = []
    for k in ("runs_root", "profile_dir"):
        for root in ("inbox_root", "scan_inbox"):
            if _inside(paths[k], paths[root]):
                bad.append(f"paths.{k}가 paths.{root} 안에 있다")
    if paths.get("mail_drop"):
        for root in ("inbox_root", "scan_inbox"):
            if _inside(paths["mail_drop"], paths[root]):
                bad.append(f"mail_drop.folder가 paths.{root} 안에 있다 — scan이 암호 원본을 읽는다")
    for k in ("runs_root", "inbox_root", "scan_inbox", "profile_dir", "accounts", "ledger"):
        if _inside(paths[k], REPO):
            bad.append(f"paths.{k}가 clone 안에 있다 — 실데이터는 clone 밖에만 둔다")
    return bad


def load_accounts(path):
    try:
        acc = srcmod.load_config(path, set(imp.ACCOUNTS), set(imp.CARDS))
    except FileNotFoundError:
        raise StopRun(f"계좌.toml이 없다: {trust(path)}")
    except srcmod.ConfigError as e:
        raise StopRun("계좌.toml 규칙 위반:\n  " + "\n  ".join(e.args[0]))
    acc["by_id"] = {s.id: s for s in acc["sources"]}
    return acc


# ------------------------------------------------------------
# 자격 증명 — Windows 자격 증명 관리자 읽기만(ctypes CredReadW · 의존 0)
# ------------------------------------------------------------

def _win_cred_read(target):
    """일반 자격 증명 (UserName, 비밀) 또는 None(없음). 비밀은 부르는 쪽이 쓰고 바로 버린다 — 출력·기록 0."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
                    ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
    adv.CredReadW.restype = wintypes.BOOL
    adv.CredFree.argtypes = [ctypes.c_void_p]
    pc = ctypes.POINTER(CREDENTIALW)()
    if not adv.CredReadW(target, 1, 0, ctypes.byref(pc)):  # 1 = CRED_TYPE_GENERIC(cmdkey /generic)
        if ctypes.get_last_error() == 1168:                 # ERROR_NOT_FOUND
            return None
        raise OSError("CredReadW 실패")
    try:
        c = pc.contents
        blob = bytes(c.CredentialBlob[:c.CredentialBlobSize])
        return (c.UserName or ""), blob.decode("utf-16-le")
    finally:
        adv.CredFree(pc)


def cred_status(reader, target):
    """dry-run용 — 항목 '있음/없음'만(값·UserName 0)."""
    try:
        got = reader(target)
    except Exception as e:  # noqa: BLE001
        return f"읽기 오류({ename(e)})"
    ok = bool(got and got[1])
    del got
    return "있음" if ok else "없음"


def have_msoffcrypto():
    import importlib.util
    return importlib.util.find_spec("msoffcrypto") is not None


# ------------------------------------------------------------
# 받는 기간(3-0 기간 기준 · 고른 항목 4)
# ------------------------------------------------------------

def file_period(full, acc):
    """내역 파일 하나 → (출처 id, 시작, 끝). scan read_files와 같은 순서(read_table → identify → parse_sheet → 머리 조회기간)."""
    kind, sheets = srcmod.read_table(full)
    hits = []
    for s in acc["sources"]:
        if kind not in s.file_types:
            continue
        try:
            h = srcmod.identify(sheets, s)
        except srcmod.SourceError:
            continue
        if h:
            hits.append((s, h))
    if len(hits) != 1:
        raise srcmod.SourceError(f"출처 서명 {len(hits)}개에 맞는다")
    s, (si, h) = hits[0]
    txs = srcmod.parse_sheet(sheets[si][1], h, s, os.path.basename(full))
    lo, hi = min(t.date for t in txs), max(t.date for t in txs)
    per = srcmod.header_period(sheets[si][1], h, s)
    if per:
        lo, hi = min(lo, per[0]), max(hi, per[1])
    return s.id, lo, hi


def last_received(acc, paths):
    """출처마다 '지난 받은 기간 끝' = 원장 period(apply된 파일) + scan inbox 파일(apply 전)의 최대 끝 — 둘 다 읽기만.
    반환 (끝 dict, inbox 정보 {files, ignored, failed})."""
    ends, info = {}, {"files": 0, "ignored": 0, "failed": 0, "md5": set()}
    led = paths["ledger"]
    if os.path.exists(led):
        from pathlib import Path
        con = sqlite3.connect(Path(led).absolute().as_uri() + "?mode=ro", uri=True)
        try:
            for sid, end, m in con.execute("SELECT source, end_, md5 FROM period"):
                try:
                    d = dt.date.fromisoformat(end)
                except (TypeError, ValueError):
                    continue
                ends[sid] = max(ends.get(sid, d), d)
                info["md5"].add(m)
        finally:
            con.close()
    ignore = {(f["path"], f["md5"]) for f in acc["ignore_files"]}
    for rel, full in imp.inbox_files(types.SimpleNamespace(inbox=paths["scan_inbox"])):
        info["files"] += 1
        m = imp.md5_file(full)
        info["md5"].add(m)
        if (rel, m) in ignore:
            info["ignored"] += 1
            continue
        try:
            sid, _lo, hi = file_period(full, acc)
        except Exception:  # noqa: BLE001 — 읽기 실패는 경고 후 제외(원장 period로 대체)
            info["failed"] += 1
            continue
        ends[sid] = max(ends.get(sid, hi), hi)
    return ends, info


def plan_period(sid, acc, ends, today, overlap, max_days=None, override=None):
    """출처 하나의 받을 기간 → dict(start, end, basis, warn)."""
    base = acc["start_date"]
    last = ends.get(sid)
    if last is None:
        start, basis = base, f"첫 받기 — 계좌.toml start_date {base}"
    else:
        start = max(base, last - dt.timedelta(days=overlap))
        basis = (f"지난 받은 끝 {last} − {overlap}일" if start > base
                 else f"계좌.toml start_date {base}(지난 받은 끝 {last} − {overlap}일보다 늦음)")
    warn = ""
    if override is not None:
        if override > start:
            raise StopRun(f"--start {sid}={override}가 계산한 시작 {start}보다 늦다 — 앞으로 넓히는 지정만 받는다(공백 방지)")
        basis += f" → 지정 --start {override}(앞으로 넓힘)"
        start = override
    if max_days:
        lo = today - dt.timedelta(days=int(max_days))
        if start < lo:
            warn = f"한 번에 받는 상한 {max_days}일 — 시작 {start} → {lo}로 자름(공백은 scan --ack-gap이 잡는다)"
            start = lo
    return {"source": sid, "start": start, "end": today, "basis": basis, "warn": warn}


def kakao_buttons(start, today):
    """앱 기간 버튼 안내 — 버튼이 권장 시작을 덮으면 그 버튼, 아니면 직접 입력."""
    for label, months in (("1개월", 1), ("3개월", 3), ("12개월", 12)):
        y, m = imp.add_months(today.year, today.month, -months)
        if imp.month_day(y, m, today.day) <= start:
            return label
    return "시작~종료 직접 입력"


# ------------------------------------------------------------
# 폴더 · 파일
# ------------------------------------------------------------

def md5(path):
    return imp.md5_file(path)


def free_name(base, roots):
    n = 1
    while True:
        name = base if n == 1 else f"{base}_{n}"
        if not any(os.path.exists(os.path.join(r, name)) for r in roots):
            return name
        n += 1


def make_new_dir(root, base):
    """root 아래 새 폴더 — 이미 있는 폴더는 쓰지 않는다(덮어쓰기 0). 반환 (경로, 이름)."""
    os.makedirs(root, exist_ok=True)
    n = 1
    while True:
        name = base if n == 1 else f"{base}-{n}"
        n += 1
        try:
            os.mkdir(os.path.join(root, name))
        except FileExistsError:
            continue
        return os.path.join(root, name), name


def unique_file(folder, fname):
    path = os.path.join(folder, fname)
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(fname)
    n = 2
    while os.path.exists(os.path.join(folder, f"{stem}_{n}{ext}")):
        n += 1
    return os.path.join(folder, f"{stem}_{n}{ext}")


def copy_new(src, dst, hook=None):
    """배타 생성('xb')으로 복사 — 대상이 있으면 FileExistsError(덮어쓰기 0)."""
    with open(src, "rb") as fi, open(dst, "xb") as fo:
        while True:
            b = fi.read(1 << 16)
            if not b:
                break
            fo.write(b)
            if hook:
                hook(dst)


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(scrub_obj(obj), f, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, path)


# ------------------------------------------------------------
# 파일 검사(3-0 파일 검사 1~6 — 브라우저 무관)
# ------------------------------------------------------------

def _squash(s):
    return re.sub(r"\s+", "", str(s))


def read_identified(path, acc, sid):
    """sniff · read_table · identify(기대한 출처 하나에만 맞아야) · parse_sheet · 잔액 연속. 반환 dict 또는 ItemFail."""
    with open(path, "rb") as f:
        kind = srcmod.sniff(f.read())
    try:
        k2, sheets = srcmod.read_table(path)
    except srcmod.SourceError as e:
        raise ItemFail(f"파일을 읽을 수 없다({kind}): {src_err(e)}")
    hits, errs = [], []
    for s in acc["sources"]:
        if k2 not in s.file_types:
            continue
        try:
            h = srcmod.identify(sheets, s)
        except srcmod.SourceError as e:
            errs.append((s.id, src_err(e)))
            continue
        if h:
            hits.append((s, h))
    if [s.id for s, _ in hits] != [sid]:
        why = "; ".join(f"{i}: {m}" for i, m in errs if i == sid) or f"맞은 출처 {[s.id for s, _ in hits]}"
        raise ItemFail(f"출처 서명이 {sid} 하나에 맞지 않는다 — {why}")
    s, (si, h) = hits[0]
    rows = sheets[si][1]
    stats = {}
    try:
        txs = srcmod.parse_sheet(rows, h, s, os.path.basename(path), stats)
        chain = srcmod.check_balance_chain(txs, os.path.basename(path))
        srcmod.assign_keys(txs, s)
    except srcmod.SourceError as e:
        raise ItemFail(f"서명·행 해석 실패: {src_err(e)}")
    data, _idx, after = srcmod._data_rows(rows, h, s)
    return {"kind": k2, "src": s, "rows": rows, "h": h, "txs": txs, "excluded": stats.get("excluded", 0),
            "data_rows": len(data), "after": after, "chain": chain, "period": srcmod.header_period(rows, h, s)}


def file_count(r, ef):
    """파일 안 건수 — tail: 표 뒤 행의 칸 하나가 정규식과 정확히 맞는 것(소계 줄 아님) / head: 머리 글자."""
    rx = re.compile(ef["count_re"])
    if ef["count_where"] == "head":
        text = " ".join(" ".join(srcmod.row_texts(x)) for x in r["rows"][:r["h"]])
        got = {int(m.group(1)) for m in rx.finditer(text)}
    else:
        got = {int(m.group(1)) for row in r["after"] for c in srcmod.row_texts(row) if (m := rx.fullmatch(c))}
    return got.pop() if len(got) == 1 else None


def check_web_file(path, suggested, site, acc, item):
    """웹 출처 파일 검사 — 모두 참이어야 '받음'. 반환 검사 목록(dict name·ok·msg), 실패는 ItemFail."""
    checks = []

    def add(name, ok, msg):
        checks.append({"name": name, "ok": bool(ok), "msg": msg})
        if not ok:
            raise ItemFail(f"파일 검사 {name}: {msg}")

    with open(path, "rb") as f:
        kind = srcmod.sniff(f.read())
    add("종류", kind == site["expect_kind"], f"{kind} ↔ 기대 {site['expect_kind']}")
    if site.get("file_name_re"):
        add("이름", re.fullmatch(site["file_name_re"], suggested or "") is not None, "내려받은 이름 꼴")
    r = read_identified(path, acc, site["id"])
    add("서명", True, f"{site['id']} 서명·식별 값·행 해석 통과")
    n_rows = r["data_rows"]
    n_file = file_count(r, site["excel_file"])
    want = item.get("screen_count")
    resp = item.get("resp_count")
    add("건수", n_file is not None and n_rows == n_file == want == resp,
        f"파일 행 {n_rows} · 파일 안 건수 {n_file} · 화면 {want} · 응답 {resp}")
    if site.get("screen_values", {}).get("in_out_re"):
        io_ = item.get("screen_in_out")
        add("입출 건수", io_ is not None and sum(io_) == n_file, f"화면 입금+출금 {io_} ↔ 파일 총건수 {n_file}")
    lo, hi = item["start"], item["end"]
    ds = [t.date for t in r["txs"]]
    add("기간", all(lo <= d <= hi for d in ds), f"행 날짜 {min(ds)}~{max(ds)} ⊂ 요청 {lo}~{hi}")
    if r["src"].period_re:
        add("머리 조회기간", r["period"] == (lo, hi), f"머리 {r['period']} ↔ 요청 {lo}~{hi}")
    head_word = site["excel_file"].get("head_word")
    if head_word:
        head = " ".join(" ".join(srcmod.row_texts(x)) for x in r["rows"][:r["h"]])
        add("머리 상태 글자", head_word in head, f"머리에 '{head_word}'")
    extra = [_squash(x) for x in site.get("extra_identity", [])]
    col = r["src"].identity_column
    if col:
        ci = list(r["src"].columns).index(col)
        allowed = [_squash(x) for x in r["src"].identity] + extra
        vals = set()
        for row in srcmod._data_rows(r["rows"], r["h"], r["src"])[0]:
            vals.add(_squash(srcmod.cell_text(row[ci])) if ci < len(row) else "")
        add("카드 식별 값", all(any(a in v for a in allowed) for v in vals),
            f"'{col}' 값 {len(vals)}종 ⊂ 식별 값(제외 행 포함)")
    if r["chain"] is not None or r["src"].kind == "bank":
        add("잔액 연속", r["chain"] is not None, "잔액 열 사슬")
    return checks, r


# ------------------------------------------------------------
# 화면 기간 글자(끝 연도 없는 꼴 · 해 넘김 — 4절 20)
# ------------------------------------------------------------

def screen_period_ok(m, start, end):
    """정규식 이름 묶음 y1? m1 d1 y2? m2 d2 → 요청 기간과 같은가. 연도가 없으면 요청 연도로, 끝 연도가 없으면
    '시작 연도 또는 +1 중 요청 끝과 같은 것'."""
    g = m.groupdict()
    try:
        y1 = int(g["y1"]) if g.get("y1") else start.year
        s = dt.date(y1, int(g["m1"]), int(g["d1"]))
        if s != start:
            return False
        ys = [int(g["y2"])] if g.get("y2") else [y1, y1 + 1]
        return any(dt.date(y, int(g["m2"]), int(g["d2"])) == end for y in ys)
    except (ValueError, TypeError, KeyError):
        return False


def fmt_day(fmt, d):
    """설정의 날짜 꼴(f-string 이름 y·m·d — Windows strftime %-m 불가라 쓰지 않는다)."""
    return fmt.format(y=d.year, m=d.month, d=d.day)


# ------------------------------------------------------------
# 조회 요청·응답(읽기만 — 필요한 칸만 뽑고 본문은 버린다)
# ------------------------------------------------------------

def _get(obj, path):
    for part in path.split("."):
        if isinstance(obj, list):
            obj = obj[int(part)]
        else:
            obj = obj[part]
    return obj


def parse_request(post, q):
    """조회 요청 본문 → {match 칸 · expect_fields 칸 · period_fields 칸}만. 본문 원문은 돌려주지 않는다."""
    mode = q.get("body", "form")
    try:
        if mode == "json":
            body = json.loads(post or "")
        else:
            body = dict(parse_qsl(post or "", keep_blank_values=True))
            if mode.startswith("form_json:"):
                body = json.loads(body[mode.split(":", 1)[1]])
    except (ValueError, KeyError):
        return None
    out = {}
    for k in list(q.get("match", {})) + list(q.get("expect_fields", {})) + list(q.get("period_fields", [])):
        try:
            v = _get(body, k)
        except (KeyError, IndexError, ValueError, TypeError):
            v = None
        out[k] = v if isinstance(v, (str, int, float)) and not isinstance(v, bool) else None
    del body
    return out


def parse_response(raw, q):
    """조회 응답 본문 → 건수·목록 길이·다음 쪽 키 유무·되돌려 준 기간·기대 칸만."""
    out = {"count": None, "list_len": None, "next_key": None, "period": None, "expect": {}, "error": ""}
    try:
        data = json.loads((raw or b"").decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        out["error"] = f"JSON 아님({ename(e)})"
        return out
    try:
        out["count"] = int(str(_get(data, q["count_path"])).replace(",", "").strip())
    except (KeyError, IndexError, ValueError, TypeError):
        out["error"] = f"{q['count_path']} 없음"
    if q.get("list_path"):
        try:
            v = _get(data, q["list_path"])
            out["list_len"] = len(v) if isinstance(v, list) else None
        except (KeyError, IndexError, ValueError, TypeError):
            pass
    if q.get("next_key_path"):
        try:
            v = _get(data, q["next_key_path"])
            out["next_key"] = bool(str(v).strip()) if v is not None else False
            s = str(v if v is not None else "").strip()   # 값 꼴만(리허설 실측 — 값은 남기지 않음)
            out["next_key_shape"] = f"{len(s)}자 " + ("빈칸" if not s else "숫자" if s.isdigit() else "글자")
        except (KeyError, IndexError, ValueError, TypeError):
            out["next_key"], out["next_key_shape"] = None, "없음"
    if q.get("resp_period"):
        try:
            out["period"] = tuple(str(_get(data, p)).strip() for p in q["resp_period"])
        except (KeyError, IndexError, ValueError, TypeError):
            out["period"] = None
    for k in q.get("resp_expect", {}):
        try:
            v = _get(data, k)
            out["expect"][k] = v if isinstance(v, (str, int)) and not isinstance(v, bool) else None
        except (KeyError, IndexError, ValueError, TypeError):
            out["expect"][k] = None
    del data
    return out


def request_problems(fields, q, start, end):
    bad = []
    for k, want in q.get("expect_fields", {}).items():
        if fields.get(k) != want:
            bad.append(f"요청 {k}={text_note(fields.get(k), want)} ≠ 기대 {want!r}")
    pf = q.get("period_fields", [])
    if pf:
        got = tuple(str(fields.get(k)) for k in pf)
        if got != (start.strftime("%Y%m%d"), end.strftime("%Y%m%d")):
            bad.append(f"요청 기간 칸 {list(pf)} ≠ {start}~{end}")
    return bad


def response_problems(info, q, start, end):
    bad = []
    if info["error"]:
        bad.append(info["error"])
    for k, want in q.get("resp_expect", {}).items():
        if info["expect"].get(k) != want:
            bad.append(f"응답 {k}≠{want!r}")
    if q.get("resp_period"):
        fmt = q.get("resp_period_fmt", "{y}{m:02d}{d:02d}")
        if info["period"] != (fmt_day(fmt, start), fmt_day(fmt, end)):
            bad.append(f"응답 기간 ≠ {start}~{end}")
    if q.get("list_equals_count") and info["list_len"] != info["count"]:
        bad.append(f"응답 목록 {info['list_len']}행 ≠ 건수 {info['count']}(쪽 나눔 의심)")
    return bad


# ------------------------------------------------------------
# 브라우저 — 여기서부터 Playwright. evaluate는 ev() 한 곳, JS는 아래 상수만(JSON.stringify 문자열로 받는다)
# ------------------------------------------------------------

JS_VIS = ("const vis = e => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));"
          "const norm = s => (s || '').replace(/\\s+/g, ' ').trim();")

ELEMENT_INFO_JS = """e => JSON.stringify({tag: e.tagName.toLowerCase(), id: e.id || '', type: (e.type || '').toLowerCase(),
  text: (e.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 200),
  value: (e.tagName === 'INPUT' && ['button', 'submit', 'reset'].includes((e.type || '').toLowerCase())) ? (e.value || '') : '',
  field: (e.tagName === 'INPUT' && /^[\\d.\\- /]{0,14}$/.test(e.value || '')) ? (e.value || '') : null,
  title: e.getAttribute('title') || '', aria: e.getAttribute('aria-label') || '', alt: e.getAttribute('alt') || '',
  label: (e.labels && e.labels.length) ? (e.labels[0].innerText || '').replace(/\\s+/g, ' ').trim() : '',
  checked: e.tagName === 'INPUT' ? !!e.checked : null,
  selected: (e.tagName === 'SELECT' && e.selectedIndex >= 0) ? (e.options[e.selectedIndex].text || '').replace(/\\s+/g, ' ').trim() : null,
  options: e.tagName === 'SELECT' ? Array.from(e.options).map(o => (o.text || '').replace(/\\s+/g, ' ').trim()) : null})"""

MARKERS_JS = "(ms) => {" + JS_VIS + r"""
  return JSON.stringify(ms.map(m => Array.from(document.querySelectorAll(m.css)).some(e => {
    if (!vis(e)) return false;
    const t = norm(e.innerText || e.value || e.getAttribute('aria-label') || '');
    return m.re ? new RegExp(m.re).test(t) : t === m.text; }))); }"""

LAYERS_JS = "(sels) => {" + JS_VIS + r"""
  let n = 0; for (const s of sels) for (const e of document.querySelectorAll(s)) if (vis(e)) n++;
  return JSON.stringify(n); }"""

TIMER_JS = "(t) => {" + JS_VIS + r"""
  const rx = new RegExp(t.re || '^\\d{2}:\\d{2}$');
  if (t.css) { const e = Array.from(document.querySelectorAll(t.css)).find(vis);
    const s = e ? norm(e.innerText) : ''; const m = s.match(/\d{2}:\d{2}/); return JSON.stringify(m ? m[0] : null); }
  for (const e of document.querySelectorAll('body *')) {
    if (e.children.length || !vis(e)) continue; const s = norm(e.textContent); if (rx.test(s)) return JSON.stringify(s); }
  return JSON.stringify(null); }"""

STATES_JS = "(q) => {" + JS_VIS + r"""
  const one = s => document.querySelector(s);
  const out = {checked: {}, text: {}, select: {}, field: {}};
  for (const s of q.checked) { const e = one(s); out.checked[s] = e ? !!e.checked : null; }
  for (const s of q.text) { const e = Array.from(document.querySelectorAll(s)).find(vis); out.text[s] = e ? norm(e.innerText) : null; }
  for (const s of q.select) { const e = one(s);
    out.select[s] = (e && e.tagName === 'SELECT' && e.selectedIndex >= 0) ? norm(e.options[e.selectedIndex].text) : null; }
  for (const s of q.field) { const e = one(s); out.field[s] = e ? (e.value || '') : null; }
  out.vtext = {};
  for (const t of (q.vtext || [])) out.vtext[t] = Array.from(document.querySelectorAll('a, button, span, strong, em, p, li, label, div'))
    .some(e => vis(e) && e.children.length <= 1 && norm(e.innerText) === t);
  return JSON.stringify(out); }"""

BODY_TEXT_JS = "() => JSON.stringify(document.body ? document.body.innerText : '')"
ACTIVE_ID_JS = "() => JSON.stringify(document.activeElement ? document.activeElement.id || '' : '')"
FIELD_VALUE_JS = "(s) => { const e = document.querySelector(s); return JSON.stringify(e ? e.value : null); }"
BUSY_JS = "(sels) => {" + JS_VIS + " return JSON.stringify(sels.some(s => Array.from(document.querySelectorAll(s)).some(vis))); }"
SET_VALUE_JS = """(e, v) => { e.value = v; e.dispatchEvent(new Event('input', {bubbles: true}));
  e.dispatchEvent(new Event('change', {bubbles: true})); return JSON.stringify(e.value); }"""


def ev(target, js, arg=None):
    """evaluate의 유일한 자리 — JS는 위 상수만, 반환은 JSON 문자열(신한은행 페이지는 객체·배열 반환이 None으로 온다).
    목록·사전 인자도 그 페이지에서 깨진다(2026-10-28 리허설 1 — 로그인 표식 읽기가 늘 예외) → JSON 글자로 넘겨 페이지 안에서 푼다."""
    if isinstance(arg, (list, dict)):
        s = target.evaluate("(a) => (" + js + ")(JSON.parse(a))", json.dumps(arg, ensure_ascii=False))
    else:
        s = target.evaluate(js) if arg is None else target.evaluate(js, arg)
    if not isinstance(s, str):
        raise ItemFail(f"화면 읽기 반환이 글자가 아님({type(s).__name__}) — JSON.stringify를 거치지 않은 읽기")
    return json.loads(s)


def name_ok(text, label):
    t = " ".join((text or "").split())
    return t == label or re.fullmatch(r"[\W_]*" + re.escape(label) + r"[\W_]*", t) is not None


def frame_count(page, pat):
    return sum(1 for f in page.frames if f != page.main_frame and re.search(pat, f.url or ""))


def _frame_of(page, spec):
    pat = spec.get("frame")
    if not pat:
        return page
    fs = [f for f in page.frames if f != page.main_frame and re.search(pat, f.url or "")]
    if len(fs) != 1:
        raise ItemFail(f"iframe '{pat}' {len(fs)}개 — 1개일 때만")
    return fs[0]


def _locate(page, spec):
    target = _frame_of(page, spec)
    by, val = spec.get("by", "css"), spec["value"]
    if by == "css":
        return target.locator(val)
    if by == "text":
        return target.get_by_text(val, exact=True)
    return target.get_by_text(re.compile(val))


def _the_one(page, spec, name):
    loc = _locate(page, spec)
    try:
        n = loc.count()
        idx = list(range(min(n, 30))) if spec.get("hidden_ok") else [i for i in range(min(n, 30)) if loc.nth(i).is_visible()]
    except ItemFail:
        raise
    except Exception as e:
        raise ItemFail(f"{name}: 요소를 찾다 오류 {ename(e)}")
    if len(idx) != 1:
        raise ItemFail(f"{name}: {'요소' if spec.get('hidden_ok') else '보이는 요소'} {len(idx)}개(전체 {n}) — 1개일 때만 누른다")
    el = loc.nth(idx[0])
    return el, ev(el, ELEMENT_INFO_JS)


def _guard_texts(info, cfg, name, spec):
    texts = [info[k] for k in ("text", "value", "title", "aria", "alt", "label") if info.get(k)]
    for bad in cfg["forbidden_texts"]:
        if bad and any(bad in t for t in texts):
            raise ItemFail(f"{name}: 요소 글자에 금지 문구 {bad!r} — 누르지 않음")
    return texts


def _spec(site, name, typ):
    acts = site.get("actions", {})
    if name not in acts:
        raise ItemFail(f"허용 목록 밖 동작 {name!r} — 누르지 않음")
    spec = acts[name]
    if spec.get("type", "click") != typ:
        raise ItemFail(f"{name}: 동작 종류 {spec.get('type', 'click')} ≠ {typ}")
    return spec


def click_allowed(page, cfg, site, name, rec, typed=None):
    """**유일한 클릭 자리.** 허용 목록의 동작만, (iframe 안까지) 보이는 요소가 정확히 1개일 때만, 요소 글자에 금지 문구가 없고
    기대 글자(또는 정규식)와 같을 때만 누른다. 어기면 누르지 않고 ItemFail. typed = 기간 칸에 칠 값(type_text가 부를 때)."""
    spec = _spec(site, name, "date" if typed is not None else "click")
    el, info = _the_one(page, spec, name)
    texts = _guard_texts(info, cfg, name, spec)
    if typed is not None:
        if info["tag"] != "input" or info["type"] not in ("text", "") or info["field"] is None:
            raise ItemFail(f"{name}: 기간 칸이 아님(tag={info['tag']} type={info['type']}) — 누르지 않음")
        shown = f"{info['field']} → {typed}"
    else:
        exps, rx = _expects(spec), spec.get("expect_re")
        if not any(name_ok(t, x) for t in texts for x in exps) and not (rx and any(re.fullmatch(rx, t) for t in texts)):
            raise ItemFail(f"{name}: 화면 글자 {text_note(' | '.join(texts))} ≠ 기대 {exps or rx} — 누르지 않음")
        shown = next((x for t in texts for x in exps if name_ok(t, x)), "re")
    rec.append({"site": site["id"], "action": name, "type": "click", "id": info["id"], "text": shown})
    try:
        el.click(timeout=cfg["timeout"]["click"] * 1000)
    except Exception as e:  # noqa: BLE001 — 누를 수 있게 되기를 기다리다 시간 초과(다른 요소가 가림 등) — 눌리지 않았다
        rec[-1]["result"] = f"누르지 못함({ename(e)})"
        raise ItemFail(f"{name}: 누르지 못함({ename(e)}) — 다른 요소(안내·툴팁·레이어)가 가렸을 수 있음")
    return info


def type_text(page, cfg, site, name, day, rec):
    """**유일한 키 입력 자리** — 허용 목록의 기간 칸(type date)만. 칸 클릭(click_allowed) → 포커스 확인 → Ctrl+A → 숫자 → Tab
    → 칸 값이 shown_fmt 꼴이 아니면 ItemFail."""
    spec = _spec(site, name, "date")
    digits = fmt_day(spec["fmt"], day)
    info = click_allowed(page, cfg, site, name, rec, typed=digits)
    target = _frame_of(page, spec)
    if not info["id"] or ev(target, ACTIVE_ID_JS) != info["id"]:
        raise ItemFail(f"{name}: 포커스가 기간 칸이 아님 — 키 입력 안 함")
    page.keyboard.press("Control+A")
    page.keyboard.type(digits)
    page.keyboard.press("Tab")
    want = fmt_day(spec["shown_fmt"], day)
    got = None
    for _ in range(15):
        page.wait_for_timeout(100)
        got = ev(target, FIELD_VALUE_JS, spec["value"])
        if got == want:
            return
    raise ItemFail(f"{name}: 입력 뒤 칸 값 {text_note(got, want)} ≠ {want}")


def set_js_value(page, cfg, site, name, day, rec):
    """readonly·숨은 기간 칸(type js_date)에 JS로 값 + input/change — 허용 목록의 칸만, 지금 값이 날짜 꼴(또는 빈칸)일 때만."""
    spec = _spec(site, name, "js_date")
    el, info = _the_one(page, spec, name)
    _guard_texts(info, cfg, name, spec)
    if info["tag"] != "input" or info["type"] not in ("text", "hidden", "") or info["field"] is None:
        raise ItemFail(f"{name}: 기간 칸이 아님(tag={info['tag']} type={info['type']}) — 값 넣지 않음")
    want = fmt_day(spec["fmt"], day)
    rec.append({"site": site["id"], "action": name, "type": "js_date", "id": info["id"], "text": f"{info['field']} → {want}"})
    got = ev(el, SET_VALUE_JS, want)
    if got != want:
        raise ItemFail(f"{name}: 값 넣은 뒤 {text_note(got, want)} ≠ {want}")


def set_check(page, cfg, site, name, rec):
    """체크박스(type check) — 허용 목록의 칸만, 라벨·글자가 기대와 같을 때만. 이미 체크면 그대로, 아니면 click 이벤트 한 번
    (라벨이 가로막아 마우스 클릭이 안 되는 칸 — 신한은행 실측)."""
    spec = _spec(site, name, "check")
    el, info = _the_one(page, spec, name)
    texts = _guard_texts(info, cfg, name, spec)
    if info["tag"] != "input" or info["type"] != "checkbox":
        raise ItemFail(f"{name}: 체크박스가 아님 — 누르지 않음")
    if not any(name_ok(t, x) for t in texts for x in _expects(spec)):
        raise ItemFail(f"{name}: 칸 글자 {text_note(' | '.join(texts))} ≠ 기대 {_expects(spec)} — 누르지 않음")
    rec.append({"site": site["id"], "action": name, "type": "check", "id": info["id"], "text": _expects(spec)[0],
                "before": info["checked"]})
    if not info["checked"]:
        el.dispatch_event("click")
    for _ in range(15):
        if ev(el, ELEMENT_INFO_JS)["checked"]:
            return
        page.wait_for_timeout(100)
    raise ItemFail(f"{name}: 체크되지 않음")


def set_select(page, cfg, site, name, rec):
    """select(type select) — 허용 목록의 칸만, 고를 글자(option)가 선택지에 정확히 하나 있을 때만."""
    spec = _spec(site, name, "select")
    el, info = _the_one(page, spec, name)
    _guard_texts(info, cfg, name, spec)
    want = spec["option"]
    if info["tag"] != "select" or (info["options"] or []).count(want) != 1:
        raise ItemFail(f"{name}: select가 아니거나 '{want}' 선택지가 1개가 아님 — 고르지 않음")
    rec.append({"site": site["id"], "action": name, "type": "select", "id": info["id"], "text": want,
                "before": "같음" if info["selected"] == want else "다름"})
    if info["selected"] != want:
        el.select_option(label=want, timeout=cfg["timeout"]["click"] * 1000)
    for _ in range(15):
        if ev(el, ELEMENT_INFO_JS)["selected"] == want:
            return
        page.wait_for_timeout(100)
    raise ItemFail(f"{name}: '{want}'로 바뀌지 않음")


class DialogWatch:
    """브라우저 dialog — 로그인 대기 중(또는 끝난 출처·모르는 창)은 기록만(accept/dismiss 호출 0 — 사용자가 창에서 답한다).
    스크립트 단계면 dismiss + 그 출처 실패 표시. 문구는 길이·해시·아는 낱말만 기록(이름이 들 수 있다)."""

    def __init__(self, site_id, log, sink):
        self.site_id, self.log, self.sink = site_id, log, sink
        self.phase = "login"
        self.unknown = None

    def __call__(self, dialog):
        msg = dialog.message or ""
        rec = {"site": self.site_id, "phase": self.phase, "type": dialog.type, "len": len(msg),
               "hash": short_hash(msg), "words": [w for w in DIALOG_WORDS if w in msg], "action": "none"}
        if self.phase == "script":
            try:
                dialog.dismiss()
                rec["action"] = "dismiss"
            except Exception as e:  # noqa: BLE001
                rec["action"] = f"error {ename(e)}"
            if self.unknown is None:
                self.unknown = rec
        self.sink.append(rec)
        self.log(f"  [dialog] {self.site_id} {rec['phase']} {rec['type']} ({rec['len']}자 · 낱말 {rec['words']}) → "
                 + ("dismiss — 그 출처 실패" if rec["action"] == "dismiss" else
                    "기록만(창에서 직접 닫아 주세요)" if rec["action"] == "none" else rec["action"]))


class NetWatch:
    """조회 요청(설정 query.url_re)만 모은다 — 요청 객체만 들고 있고 본문은 Runner가 필요한 칸만 뽑는다. XHR 진행 수(settle용)."""

    def __init__(self, url_re):
        self.rx = re.compile(url_re)
        self.items, self.seq, self.inflight = [], 0, {}

    def on_request(self, req):
        try:
            if req.resource_type in ("xhr", "fetch"):
                self.inflight[req] = time.time()
            if self.rx.search(req.url or ""):
                self.seq += 1
                self.items.append({"seq": self.seq, "req": req, "state": "진행"})
        except Exception:  # noqa: BLE001 — 리스너 예외가 다음 API 호출을 깨지 않게
            pass

    def _end(self, req, state):
        try:
            self.inflight.pop(req, None)
            for it in self.items:
                if it["req"] is req and it["state"] == "진행":
                    it["state"] = state
        except Exception:  # noqa: BLE001
            pass

    def on_finished(self, req):
        self._end(req, "끝")

    def on_failed(self, req):
        self._end(req, "실패")

    def busy(self, max_age=15):
        now = time.time()
        return any(now - t < max_age for t in list(self.inflight.values()))


class WindowWatch:
    """새 창(ctx 'page' 이벤트) — 스크립트가 만든 탭은 own. 나머지는 기록만(누르기·닫기 0). 스크립트 단계면 그 출처 실패."""

    def __init__(self, log, dialogs):
        self.log, self.dialogs = log, dialogs
        self.own, self.others = set(), []
        self.site, self.phase, self.expect_own = None, "idle", False

    def __call__(self, pg):
        if self.expect_own or pg in self.own:
            self.own.add(pg)
            return
        rec = {"site": self.site, "phase": self.phase, "t": time.time(), "page": pg}
        self.others.append(rec)
        try:
            pg.on("dialog", DialogWatch(f"{self.site}(새 창)", self.log, self.dialogs))  # 기록만 — 사용자가 답할 창
        except Exception:  # noqa: BLE001
            pass
        self.log(f"  [창] {self.site} {self.phase} 중 새 창 — 기록만(스크립트는 누르거나 닫지 않음)")

    def claim(self, pg):
        """스크립트가 ctx.new_page()로 연 탭 — 'page' 이벤트가 먼저 와서 남의 창으로 적혔으면 지운다."""
        self.own.add(pg)
        self.others = [r for r in self.others if r["page"] is not pg]

    def export(self):
        return [{"site": r["site"], "phase": r["phase"]} for r in self.others]

    def open_since(self, t0):
        n = 0
        for r in self.others:
            try:
                if r["t"] >= t0 and not r["page"].is_closed():
                    n += 1
            except Exception:  # noqa: BLE001
                n += 1
        return n

    def script_new(self, site_id):
        return any(r["site"] == site_id and r["phase"] == "script" for r in self.others)


class SiteRunner:
    """출처 하나(웹) — 로그인 대기 → 내역 화면 → 조회 조건 → 조회 → 응답·화면 대조 → 엑셀 → 파일 검사."""

    def __init__(self, ctx, page, cfg, site, acc, item, run_dir, log, rec, hooks, windows, dialogs):
        self.ctx, self.page, self.cfg, self.site, self.acc = ctx, page, cfg, site, acc
        self.item, self.run_dir, self.log, self.rec, self.hooks = item, run_dir, log, rec, hooks
        self.windows, self.T = windows, cfg["timeout"]
        self.dw = DialogWatch(site["id"], log, dialogs)
        self.net = NetWatch(site["query"]["url_re"])
        page.on("dialog", self.dw)
        page.on("request", self.net.on_request)
        page.on("requestfinished", self.net.on_finished)
        page.on("requestfailed", self.net.on_failed)
        self.t0 = time.time()
        self.script_t0 = None

    # -------- 공통
    def step(self, name):
        hook = self.hooks.get("step_hook")
        if hook:
            hook(self.site["id"], name)

    def alive(self):
        if self.dw.unknown:
            raise ItemFail(f"모르는 브라우저 확인창({self.dw.unknown['type']}) → dismiss 하고 그 출처 실패")
        if self.windows.script_new(self.site["id"]):
            raise ItemFail("스크립트 단계에 새 창이 열림 — 닫지 않고 그 출처 실패")
        if self.script_t0 and time.time() - self.script_t0 > self.T["site_step"]:
            raise ItemFail(f"스크립트 단계 {self.T['site_step']}초 초과(자동 로그아웃 대비 상한)")

    def wait(self, ms=200):
        self.page.wait_for_timeout(ms)
        self.alive()

    def until(self, pred, sec, step=250):
        end = time.time() + sec
        while time.time() < end:
            try:
                if pred():
                    return True
            except ItemFail:
                raise
            except Exception:  # noqa: BLE001
                pass
            self.wait(step)
        return False

    def markers(self):
        return ev(self.page, MARKERS_JS, self.site["login"]["markers"])

    def layers(self):
        sels = self.site.get("login", {}).get("layer_selectors", [])
        return ev(self.page, LAYERS_JS, sels) if sels else 0

    def logged_in(self):
        return all(self.markers())

    def ensure_logged_in(self):
        if self.logged_in():
            return
        self.wait(1500)
        if not self.logged_in():
            raise ItemFail("로그인 표식이 사라짐 — 로그인이 풀린 것 같다(입력 없이 그 출처 실패)")

    def settle(self, quiet=0.8, sec=None):
        busy_sels = self.site.get("busy_selectors", [])
        end, since = time.time() + (sec or self.T["page"]), None
        while time.time() < end:
            self.wait(200)
            if not self.net.busy() and not (busy_sels and ev(self.page, BUSY_JS, busy_sels)):
                since = since or time.time()
                if time.time() - since >= quiet:
                    return True
            else:
                since = None
        return False

    def visible_count(self, name):
        spec = self.site["actions"][name]
        try:
            loc = _locate(self.page, spec)
            n = loc.count()
            return n if spec.get("hidden_ok") else sum(1 for i in range(min(n, 30)) if loc.nth(i).is_visible())
        except Exception:  # noqa: BLE001
            return 0

    def act(self, name):
        """허용 동작 하나 — 종류(click/date/js_date/check/select)에 맞는 guard 함수로만."""
        self.alive()
        self.step(name)
        spec = self.site["actions"][name]
        self.until(lambda: self.visible_count(name) >= 1, self.T["page"])
        typ = spec.get("type", "click")
        day = (self.item["start"] if spec.get("date") == "start" else self.item["end"]) if spec.get("date") else None
        if typ == "click":
            click_allowed(self.page, self.cfg, self.site, name, self.rec)
        elif typ == "date":
            type_text(self.page, self.cfg, self.site, name, day, self.rec)
        elif typ == "js_date":
            set_js_value(self.page, self.cfg, self.site, name, day, self.rec)
        elif typ == "check":
            set_check(self.page, self.cfg, self.site, name, self.rec)
        else:
            set_select(self.page, self.cfg, self.site, name, self.rec)
        after = spec.get("after_checked")
        if after and not self.until(lambda: ev(_frame_of(self.page, spec), STATES_JS,
                                               {"checked": [after], "text": [], "select": [], "field": []})
                                    ["checked"][after] is True, 5):
            raise ItemFail(f"{name}: 누른 뒤 {after}가 선택되지 않음")
        self.wait(150)

    # -------- 로그인(사용자 몫 — 클릭·입력·dialog 호출 0)
    def wait_login(self):
        lim, stable = self.T["login"], self.T["login_stable"]
        self.log(f"[login] {self.site['id']}: 창에서 직접 로그인해 주세요 — {self.site.get('login_hint', '평소 방식')} "
                 f"(최대 {int(lim) // 60}분). 이 스크립트는 그동안 아무것도 누르거나 입력하지 않고, 뜨는 안내창도 직접 닫으셔야 합니다.")
        end, since, last, note, last_err = time.time() + lim, None, None, 0, None
        while time.time() < end:
            self.page.wait_for_timeout(1000)
            try:
                marks = self.markers()
                lay = self.layers()
                title = self.page.title() or ""
                last_err = None
            except Exception as e:  # noqa: BLE001 — 화면 바뀌는 중(이름만 — 계속되면 읽기 결함)
                marks, lay, title = [False] * len(self.site["login"]["markers"]), 0, ""
                if ename(e) != last_err:
                    last_err = ename(e)
                    self.item.setdefault("login_read_errors", []).append(last_err)
                    self.log(f"[login] {self.site['id']}: 화면 읽기 예외 {last_err}")
            extra = self.windows.open_since(self.t0)
            ok = all(marks) and extra == 0 and lay == 0 and "로그인" not in title
            state = (tuple(marks), extra, lay, "로그인" in title)
            if state != last:
                self.log(f"[login] {self.site['id']}: 표식 {sum(marks)}/{len(marks)} · 새 창 {extra} · 레이어 {lay}"
                         + (" · 제목에 '로그인'" if "로그인" in title else ""))
                last = state
            if ok:
                since = since or time.time()
                if time.time() - since >= stable:
                    self.log(f"[login] {self.site['id']}: 로그인 끝으로 판단({stable}초 그대로) — 이제부터 스크립트가 진행합니다")
                    return
            else:
                since = None
            if time.time() - note >= 60:
                note = time.time()
                self.log(f"[login] {self.site['id']}: 대기 중 — 남은 {int(end - time.time()) // 60}분")
        raise ItemFail(f"로그인 대기 {lim}초 초과 — 입력 없이 그 출처 실패")

    # -------- 세션 타이머(신한카드·신한은행 10분)
    def timer_left(self):
        t = self.site.get("timer")
        if not t:
            return None
        try:
            s = ev(self.page, TIMER_JS, {"css": t.get("css", ""), "re": t.get("re", "")})
        except ItemFail:
            raise
        except Exception:  # noqa: BLE001
            return None
        m = re.fullmatch(r"(\d{2}):(\d{2})", s or "")
        return int(m.group(1)) * 60 + int(m.group(2)) if m else None

    def try_extend_once(self):
        """(리허설 --try-extend) 남은 시간과 상관없이 연장 동작을 한 번 — 결과(전·후 초)만 기록하고 출처를 실패시키지 않는다."""
        if not (self.site.get("timer") and self.site["flow"].get("extend")):
            return
        before = self.timer_left()
        try:
            for name in self.site["flow"]["extend"]:
                self.act(name)
            self.wait(800)
            self.item["try_extend"] = {"before": before, "after": self.timer_left()}
        except ItemFail as e:
            self.item["try_extend"] = {"before": before, "error": str(e)}
        self.log(f"  [timer] {self.site['id']}: 연장 시도 {self.item['try_extend']}")

    def initial_states(self):
        """화면을 연 직후(조건을 넣기 전) '전체여야 하는 선택'의 상태 — 서버가 지난 필터를 기억하는지 관찰(이월 9)."""
        sv = self.site["screen_values"]
        q = {"checked": list(sv.get("must_checked", [])), "text": [], "select": list(sv.get("must_select", {})),
             "field": [], "vtext": []}
        try:
            st = ev(self.page, STATES_JS, q)
            self.item["initial_states"] = {**{s: st["checked"].get(s) for s in q["checked"]},
                                           **{s: st["select"].get(s) == w for s, w in sv.get("must_select", {}).items()}}
        except Exception as e:  # noqa: BLE001 — 기록용이라 실패시키지 않는다
            self.item["initial_states"] = f"못 읽음({ename(e)})"

    def ensure_time(self):
        t = self.site.get("timer")
        if not t:
            return
        left = self.timer_left()
        if left is None:
            self.log(f"  [timer] {self.site['id']}: 남은 시간 글자를 못 읽음 — 단계 상한 {self.T['site_step']}초로만 지킴")
            return
        if left >= t.get("min_left", 60):
            return
        self.log(f"  [timer] {self.site['id']}: 남은 {left}초 < {t.get('min_left', 60)}초 — 연장 1회")
        for name in self.site["flow"].get("extend", []):
            self.act(name)
        self.wait(800)
        after = self.timer_left()
        self.item["extended"] = {"before": left, "after": after}
        if after is None or after <= left:
            raise ItemFail(f"연장 뒤 남은 시간이 늘지 않음({left} → {after})")
        self.log(f"  [timer] {self.site['id']}: 연장됨 {left}초 → {after}초")

    # -------- 화면
    def open_screen(self):
        sc = self.site["screen"]
        if sc.get("url"):
            try:
                self.page.goto(sc["url"], wait_until="domcontentloaded", timeout=self.T["page"] * 1000)
            except Exception as e:  # noqa: BLE001
                raise ItemFail(f"내역 화면을 열지 못함({ename(e)})")
        else:
            try:
                for name in sc["menu"]:
                    self.act(name)
                    self.settle(quiet=0.5)
            except ItemFail as e:
                lim = self.T.get("menu_manual", 0)
                if not lim or self.dw.unknown or self.windows.script_new(self.site["id"]):
                    raise
                self.log(f"  [화면] 메뉴 허용 클릭이 안 됨({e}) — 창에서 직접 '{sc['title_contains']}' 화면으로 가 주세요"
                         f"(최대 {int(lim)}초 · 스크립트는 누르지 않고 기다립니다)")
                self.item["menu_manual"] = True
                # 사용자가 움직이는 동안은 로그인 대기와 같은 규칙(dialog·새 창 기록만 · 단계 상한 멈춤)
                self.dw.phase, self.windows.phase, self.script_t0 = "manual", "manual", None
                try:
                    if not self.until(lambda: sc["title_contains"] in (self.page.title() or ""), lim, step=1000):
                        raise ItemFail(f"'{sc['title_contains']}' 화면이 열리지 않음")
                finally:
                    self.dw.phase, self.windows.phase, self.script_t0 = "script", "script", time.time()
        if not self.until(lambda: sc["title_contains"] in (self.page.title() or ""), self.T["page"]):
            try:   # 실측 근거: 주소 경로('?'·'#'·';' 앞)와 제목에 '로그인'이 있는지만
                self.item["screen_url"] = re.split(r"[?#;]", self.page.url or "", maxsplit=1)[0][:120]
                self.item["screen_title_login"] = "로그인" in (self.page.title() or "")
            except Exception:  # noqa: BLE001
                pass
            raise ItemFail(f"화면 제목에 '{sc['title_contains']}'가 없음 — 지금 주소 {self.item.get('screen_url', '?')}")
        self.settle()
        self.ensure_logged_in()

    def states(self, which):
        """조회 직전(before)·직후(after) '전체여야 하는 선택' 대조. 직후는 레이어가 닫혀도 읽히는 칸만(after_checked·
        must_text_after — 기본은 직전과 같은 체크 목록) — 그 밖은 조회 요청 본문의 필터 값(expect_fields)이 지킨다."""
        sv = self.site["screen_values"]
        checked = list(sv.get("must_checked", [])) if which == "before" else list(sv.get("after_checked",
                                                                                         sv.get("must_checked", [])))
        texts = dict(sv.get("must_text", {})) if which == "before" else dict(sv.get("must_text_after", {}))
        q = {"checked": checked, "text": list(texts),
             "select": list(sv.get("must_select", {})) + ([sv["account_select"]] if sv.get("account_select") else []),
             "field": [], "vtext": list(sv.get("must_vtext", []))}
        if which == "before":
            for name, spec in self.site["actions"].items():
                if spec.get("type") in ("date", "js_date") and name in self.site["flow"].get("prepare", []):
                    q["field"].append(spec["value"])
        st = ev(self.page, STATES_JS, q)
        bad = []
        for s in q["checked"]:
            if st["checked"].get(s) is not True:
                bad.append(f"{s} 선택={st['checked'].get(s)}(전체여야 함)")
        for s, want in texts.items():
            if st["text"].get(s) != want:
                bad.append(f"{s} 글자 {text_note(st['text'].get(s), want)} ≠ '{want}'")
        for t in q["vtext"]:
            if st["vtext"].get(t) is not True:
                bad.append(f"'{t}' 글자가 화면에 없음")
        for s, want in sv.get("must_select", {}).items():
            if st["select"].get(s) != want:
                bad.append(f"{s} 선택 {text_note(st['select'].get(s), want)} ≠ '{want}'")
        if sv.get("account_select"):
            got = _squash(st["select"].get(sv["account_select"]) or "")
            ok = any(_squash(x) in got for x in self.acc["by_id"][self.site["id"]].identity)
            self.item["account_select"] = "식별 값과 일치" if ok else "불일치"
            if not ok:
                bad.append("계좌 선택 글자가 계좌.toml 식별 값과 다름")
        for name, spec in self.site["actions"].items():
            if spec.get("date") and spec["value"] in q["field"]:
                d = self.item["start"] if spec["date"] == "start" else self.item["end"]
                want = fmt_day(spec.get("shown_fmt") or spec["fmt"], d)
                if st["field"].get(spec["value"]) != want:
                    bad.append(f"{name} 칸 {text_note(st['field'].get(spec['value']), want)} ≠ {want}")
        del st
        return bad

    def screen_numbers(self):
        """조회 뒤 화면 글자에서 '총 N건'·기간 글자·(입금·출금 건수)만 뽑는다 — 글자 원문은 버린다."""
        sv = self.site["screen_values"]
        body = ev(self.page, BODY_TEXT_JS)
        counts = {int(m.group(1).replace(",", "")) for m in re.finditer(sv["count_re"], body)}
        pers = [m for m in re.finditer(sv["period_re"], body)]
        per_ok = [screen_period_ok(m, self.item["start"], self.item["end"]) for m in pers]
        io_ = None
        if sv.get("in_out_re"):
            got = [re.search(r, body) for r in sv["in_out_re"]]
            io_ = [int(g.group(1).replace(",", "")) for g in got] if all(got) else None
        del body
        return {"count": next(iter(counts)) if len(counts) == 1 else None, "counts_seen": len(counts),
                "period": bool(per_ok) and all(per_ok), "periods_seen": len(pers), "in_out": io_}

    # -------- 조회
    def query(self):
        q = self.site["query"]
        mark = self.net.seq
        self.act(self.site["flow"]["query"])
        end, done, mine = time.time() + self.T["query"], False, []
        while time.time() < end:
            self.wait(250)
            mine = []
            for it in self.net.items:
                if it["seq"] <= mark or it["state"] == "진행":
                    continue
                if "fields" not in it:
                    try:
                        it["fields"] = parse_request(it["req"].post_data, q)
                    except Exception:  # noqa: BLE001
                        it["fields"] = None
                f = it["fields"]
                if f is not None and all(f.get(k) == v for k, v in q.get("match", {}).items()):
                    mine.append(it)
            pending = [it for it in self.net.items if it["seq"] > mark and it["state"] == "진행"]
            if mine and not pending:
                done = True
                break
        if not done:
            raise ItemFail(f"조회 요청을 확인하지 못함({self.T['query']}초)")
        it = mine[-1]
        if it["state"] == "실패":
            raise ItemFail("조회 요청 실패(requestfailed) — 판정 불가")
        try:
            resp = it["req"].response()
            status, raw = (resp.status, resp.body()) if resp is not None else (None, b"")
        except Exception as e:  # noqa: BLE001
            raise ItemFail(f"조회 응답을 못 읽음({ename(e)})")
        if status is None or not 200 <= status < 300:
            raise ItemFail(f"조회 응답 HTTP {status} — 판정 불가")
        info = parse_response(raw, q)
        del raw
        bad = request_problems(it["fields"] or {}, q, self.item["start"], self.item["end"]) \
            + response_problems(info, q, self.item["start"], self.item["end"])
        self.item["query"] = {"requests": len(mine), "count": info["count"], "list_len": info["list_len"],
                              "next_key": info["next_key"], "next_key_shape": info.get("next_key_shape"), "problems": bad}
        if bad:
            raise ItemFail("조회 요청·응답이 기대와 다름 — 엑셀 안 누름: " + "; ".join(bad))
        self.settle(quiet=1.0)
        return info

    # -------- 한 출처
    def run(self, probe_paging=False, try_extend=False):
        it, site = self.item, self.site
        self.step("goto")
        try:
            self.page.goto(site["start_url"], wait_until="domcontentloaded", timeout=self.T["page"] * 1000)
        except Exception as e:  # noqa: BLE001
            raise ItemFail(f"첫 화면을 열지 못함({ename(e)})")
        self.wait_login()
        self.dw.phase = "script"
        self.windows.phase = "script"
        self.script_t0 = time.time()
        self.settle()
        self.alive()
        self.open_screen()
        self.initial_states()
        self.ensure_time()
        for name in site["flow"].get("prepare", []):
            self.act(name)
        self.alive()
        bad = self.states("before")
        if bad:
            raise ItemFail("조회 직전 화면 값이 기대와 다름 — 조회 안 함: " + "; ".join(bad))
        self.ensure_time()
        info = self.query()
        self.ensure_logged_in()
        nums = self.screen_numbers()
        it.update(resp_count=info["count"], screen_count=nums["count"], screen_in_out=nums["in_out"])
        bad = ([] if nums["period"] else [f"화면 기간 글자 ≠ 요청({nums['periods_seen']}곳)"]) \
            + ([] if nums["count"] is not None else [f"화면 '총 N건'을 하나로 못 읽음({nums['counts_seen']}곳)"]) \
            + [b for b in self.states("after")]
        if bad:
            raise ItemFail("조회 뒤 화면 값이 기대와 다름 — 엑셀 안 누름: " + "; ".join(bad))
        if info["count"] == 0 or nums["count"] == 0:
            if info["count"] == 0 and nums["count"] == 0:
                it["status"] = "0건"
                self.log(f"  → {site['id']}: 0건(응답 0 · 화면 '총 0건') — 엑셀 안 누름")
                return
            raise ItemFail(f"0건 판정 불가 — 응답 {info['count']} · 화면 {nums['count']}")
        if info["count"] != nums["count"]:
            raise ItemFail(f"화면 '총 {nums['count']}건' ≠ 응답 {info['count']} — 엑셀 안 누름")
        if info["next_key"] is not False and site["query"].get("next_key_path"):
            if not probe_paging:
                raise ItemFail(f"응답 다음 쪽 키 {'있음' if info['next_key'] else '못 읽음'} — 쪽 나눔(미실측)이라 엑셀 안 누름")
            it["paging_probe"] = True
            self.log(f"  [probe-paging] {site['id']}: 다음 쪽 키가 있지만 실측용으로 받는다(회차 폴더에만 · 넣지 않음)")
        self.ensure_time()
        self.download()
        if try_extend:   # 사이트 끝에서 — 연장 툴팁이 조회 레이어를 가렸다(2026-10-28 리허설 1)
            self.try_extend_once()

    def download(self):
        it, site = self.item, self.site
        steps = site["flow"]["excel"]
        for name in steps[:-1]:
            self.act(name)
            fr = site["actions"][name].get("wait_frame")
            if fr and not self.until(lambda: frame_count(self.page, fr) == 1, self.T["page"]):
                raise ItemFail(f"iframe '{fr}'가 1개로 뜨지 않음")
        checks = site.get("excel_checks")
        if checks:
            fr = _frame_of(self.page, {"frame": checks["frame"]})
            st = ev(fr, STATES_JS, {"checked": checks["ids"], "text": [], "select": [], "field": []})["checked"]
            if not all(v is True for v in st.values()):
                raise ItemFail(f"저장할 열 {sum(1 for v in st.values() if v is True)}/{len(st)}개만 체크 — 엑셀 안 누름")
        site_dir = os.path.join(self.run_dir, site["id"])
        os.makedirs(site_dir, exist_ok=True)
        try:
            with self.page.expect_download(timeout=self.T["download"] * 1000) as dl_info:
                self.act(steps[-1])
            dl = dl_info.value
        except ItemFail:
            raise
        except Exception as e:  # noqa: BLE001 — 세션 풀림 때는 JSON·HTML·XML 응답이라 내려받기가 안 난다
            self.alive()
            raise ItemFail(f"엑셀을 눌렀는데 내려받기가 시작되지 않음({ename(e)})")
        sug = dl.suggested_filename or "download.bin"
        dest = unique_file(site_dir, os.path.basename(sug))
        try:
            dl.save_as(dest)
        except Exception as e:  # noqa: BLE001
            raise ItemFail(f"내려받기 저장 실패({ename(e)})")
        if dl.failure():
            raise ItemFail("내려받기 실패(failure)")
        it.update(_path=dest, file=os.path.basename(dest), suggested=sug, md5=md5(dest), bytes=os.path.getsize(dest))
        self.log(f"  저장 {site['id']}\\{it['file']} ({it['bytes'] // 1024}KB)")
        checks, _r = check_web_file(dest, sug, site, self.acc, it)
        it["checks"] = checks
        if it.get("paging_probe"):
            it["status"] = "받지 못함"
            it["error"] = "쪽 나눔 실측(--probe-paging) — 회차 폴더에만, inbox에 넣지 않음"
            return
        it["status"] = "받음"
        self.alive()
        self.ensure_logged_in()


# ------------------------------------------------------------
# Playwright 바깥 틀(홈택스 fetch_hometax.py에서 가져옴)
# ------------------------------------------------------------

def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise StopRun("playwright가 없다 — 설치가 필요하면 사용자에게 먼저 묻는다")
    return sync_playwright


def profile_in_use(profile_dir):
    if os.path.lexists(os.path.join(profile_dir, "SingletonLock")):
        return True
    lock = os.path.join(profile_dir, "lockfile")
    if os.name == "nt" and os.path.exists(lock):
        try:
            with open(lock, "a"):
                pass
        except OSError:
            return True
    return False


def clean_download_history(profile_dir, log):
    """launch 전 프로필 다운로드 기록 중 파일이 없는 행만 지운다(같은 프로필 2회째 다운로드 순간 크롬이 죽던 결함 대비 —
    홈택스와 같음). 실제 파일이 있는 기록·다른 표는 건드리지 않는다."""
    db = None
    for p in (os.path.join(profile_dir, "Default", "History"), os.path.join(profile_dir, "History")):
        if os.path.isfile(p):
            db = p
            break
    if db is None:
        return {"status": "skip"}
    try:
        con = sqlite3.connect(db, timeout=1, isolation_level=None)
        try:
            con.execute("BEGIN IMMEDIATE")
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            gone = []
            if "downloads" in tables:
                gone = [(i,) for i, tp in con.execute("SELECT id, target_path FROM downloads").fetchall()
                        if not (tp and os.path.exists(tp))]
            if gone:
                if "downloads_url_chains" in tables:
                    con.executemany("DELETE FROM downloads_url_chains WHERE id = ?", gone)
                if "downloads_slices" in tables:
                    con.executemany("DELETE FROM downloads_slices WHERE download_id = ?", gone)
                con.executemany("DELETE FROM downloads WHERE id = ?", gone)
            con.execute("COMMIT")
        finally:
            con.close()
        return {"status": "ok", "deleted": len(gone)}
    except sqlite3.Error as e:
        log(f"[주의] 프로필 다운로드 기록 정리 못 함 — 그대로 계속({ename(e)})")
        return {"status": "error"}


def launch(p, cfg, profile_dir, headless=False):
    os.makedirs(profile_dir, exist_ok=True)
    kw = dict(headless=headless, accept_downloads=True)
    if headless:
        kw["viewport"] = {"width": 1400, "height": 900}
    else:
        kw["no_viewport"] = True
        kw["args"] = ["--start-maximized"]
    channel = cfg.get("browser", {}).get("channel")
    try:
        if channel:
            return p.chromium.launch_persistent_context(profile_dir, channel=channel, **kw)
        return p.chromium.launch_persistent_context(profile_dir, **kw)
    except Exception as e:  # noqa: BLE001
        raise StopRun(f"브라우저를 띄우지 못함(channel={channel}): {ename(e)}")


def run_web(cfg, sites, acc, items, run_dir, log, state, hooks, probe_paging):
    """웹 출처 차례로 — 한 출처의 ItemFail·임의 예외는 그 출처만 '받지 못함'(3-0 부분 실패)."""
    sync_playwright = hooks.get("sync_playwright") or _playwright()
    state["profile_cleanup"] = clean_download_history(state["paths"]["profile_dir"], log)
    with sync_playwright() as p:
        ctx = launch(p, cfg, state["paths"]["profile_dir"], headless=hooks.get("headless", False))
        try:
            if hooks.get("on_context"):
                hooks["on_context"](ctx)
            windows = WindowWatch(log, state["dialogs"])
            state["window_watch"] = windows
            ctx.on("page", windows)
            first = ctx.pages[0] if ctx.pages else None
            if first is not None:
                windows.claim(first)
            for site in sites:
                it = items[site["id"]]
                it["status"] = "진행"
                log(f"[{site['id']}] {it['start']} ~ {it['end']}")
                page = None
                try:
                    windows.site, windows.phase = site["id"], "login"
                    if first is not None:
                        page, first = first, None
                    else:
                        windows.expect_own = True
                        try:
                            page = ctx.new_page()
                        finally:
                            windows.expect_own = False
                        windows.claim(page)
                    runner = SiteRunner(ctx, page, cfg, site, acc, it, run_dir, log, state["clicks"], hooks,
                                        windows, state["dialogs"])
                    try:
                        runner.run(probe_paging, state.get("try_extend", False))
                    finally:
                        runner.dw.phase = "done"
                        windows.phase = "idle"
                except ItemFail as e:
                    it.update(status="받지 못함", error=str(e))
                    _debug_shot(state, page, site["id"], log)
                except Exception as e:  # noqa: BLE001 — 임의 예외도 그 출처만(종류 이름만 기록)
                    it.update(status="받지 못함", error=f"예외 {ename(e)}")
                    log.exc(site["id"], e)
                log(f"  → {site['id']}: {it['status']}" + (f" — {it['error']}" if it.get("error") else ""))
        finally:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass


def _debug_shot(state, page, sid, log):
    """--debug일 때만 회차 debug\\에 그림 1장(세션에서 열지 않는다)."""
    if not state.get("debug") or page is None:
        return
    try:
        d = os.path.join(state["run_dir"], "debug")
        os.makedirs(d, exist_ok=True)
        page.screenshot(path=os.path.join(d, f"{sid}_fail.png"))
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------
# 카카오뱅크 — 메일 수거(안 A IMAP · 안 C 지정 폴더) · 복호화 · 검사
# ------------------------------------------------------------

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def imap_date(d):
    return f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}"  # 로캘과 무관(strftime %b는 로캘을 탄다)


def _hdr(raw_headers, name):
    msg = email.message_from_bytes(raw_headers or b"", policy=email.policy.default)
    v = msg.get(name)
    return str(v) if v is not None else ""


def _fetch_parts(data):
    """FETCH 응답 → [(seq, 머리 바이트, 앞부분 바이트)] — imaplib의 (b'1 (INTERNALDATE … {n}', 본문) 꼴."""
    out = []
    for x in data or []:
        if isinstance(x, tuple) and len(x) >= 2:
            m = re.match(rb"(\d+)\s", x[0] or b"")
            if m:
                out.append((m.group(1), x[0], x[1]))
    return out


def imap_collect(mc, kc, run_start, folder, cred_reader, log, sleep=time.sleep):
    """IMAP 읽기 전용 수거 — 후보 메일의 첨부(attach_re)를 folder에 저장. 반환 (저장한 [(메일 번호, 경로, 받은 시각)], 메모).
    화이트리스트(4절 12): IMAP4_SSL(ssl_context=create_default_context) · login · select(readonly=True) · search · fetch(PEEK) · logout."""
    notes = []
    stats = {"hits": 0, "fresh": 0, "from_ok": 0, "subject_ok": 0, "tz": []}  # 리허설 실측(한글 헤더·시간대 — 이월 7)
    got = cred_reader(mc["cred_target"])
    if not got or not got[1]:
        raise ItemFail(f"자격 증명 {mc['cred_target']} 없음 — " + CRED_HELP.format(target=mc["cred_target"], user="<네이버 아이디>"))
    tls = ssl.create_default_context()
    try:
        M = imaplib.IMAP4_SSL(mc["host"], int(mc["port"]), ssl_context=tls, timeout=30)
    except Exception as e:  # noqa: BLE001
        raise ItemFail(f"IMAP 접속 실패({ename(e)})")
    saved = []
    try:
        try:
            M.login(got[0], got[1])
        except Exception as e:  # noqa: BLE001 — 서버 문구에 아이디가 들 수 있어 종류만
            raise ItemFail(f"IMAP 로그인 실패({ename(e)}) — 네이버 2단계 인증·애플리케이션 비밀번호·IMAP 사용 설정을 확인")
        finally:
            got = None
        since = imap_date((run_start - dt.timedelta(days=int(mc["since_days"]))).date())
        floor = run_start - dt.timedelta(minutes=10)
        addr = mc["from"].strip().lower()
        for attempt in range(int(mc["wait_tries"]) + 1):
            if attempt:
                log(f"  [메일] 카카오뱅크 메일 없음 — {mc['wait_sec']}초 뒤 다시({attempt}/{mc['wait_tries']})")
                sleep(int(mc["wait_sec"]))
            typ, _ = M.select("INBOX", readonly=True)
            if typ != "OK":
                raise ItemFail("받은편지함을 읽기 전용으로 열지 못함")
            typ, data = M.search(None, "SINCE", since, "FROM", f'"{addr}"')
            ids = (data[0] or b"").split() if typ == "OK" and data else []
            stats["hits"] = len(ids)
            if not ids:
                continue
            typ, data = M.fetch(b",".join(ids), "(INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT AUTHENTICATION-RESULTS)])")
            cands = []
            stats.update(fresh=0, from_ok=0, subject_ok=0)
            for seq, meta, head in _fetch_parts(data):
                tt = imaplib.Internaldate2tuple(meta)
                if tt is None:
                    continue
                tz = re.search(rb'INTERNALDATE "[^"]* ([+-]\d{4})"', meta)
                if tz and tz.group(1).decode() not in stats["tz"]:
                    stats["tz"].append(tz.group(1).decode())
                when = dt.datetime.fromtimestamp(time.mktime(tt))
                frm = email.utils.parseaddr(_hdr(head, "From"))[1].lower()
                subj = _hdr(head, "Subject")
                size = re.search(rb"RFC822\.SIZE (\d+)", meta)
                stats["fresh"] += when >= floor
                stats["from_ok"] += frm == addr
                stats["subject_ok"] += mc["subject_contains"] in subj
                if when < floor or frm != addr or mc["subject_contains"] not in subj:
                    continue
                if size and int(size.group(1)) > 20 * 1024 * 1024:
                    notes.append("20MB 넘는 메일 건너뜀")
                    continue
                auth = _hdr(head, "Authentication-Results").lower()
                if not ("dkim=pass" in auth and "kakaobank.com" in auth):
                    notes.append("DKIM 통과 표시 없음(경고만 — 이월)")
                cands.append((when, seq))
            cands.sort(reverse=True)
            for k, (when, seq) in enumerate(cands[:int(mc["max_candidates"])], 1):
                typ, data = M.fetch(seq, "(BODY.PEEK[])")
                parts = _fetch_parts(data)
                if typ != "OK" or not parts:
                    continue
                msg = email.message_from_bytes(parts[0][2], policy=email.policy.default)
                for part in msg.iter_attachments():
                    fn = part.get_filename() or ""
                    if not re.fullmatch(mc["attach_re"], fn):
                        continue
                    d = os.path.join(folder, f"m{k}")
                    os.makedirs(d, exist_ok=True)
                    path = unique_file(d, os.path.basename(fn))
                    with open(path, "xb") as f:
                        f.write(part.get_payload(decode=True) or b"")
                    saved.append((k, path, when))
                del msg
            if saved:
                break
            if len(cands) > int(mc["max_candidates"]):
                notes.append(f"후보 {len(cands)}통 중 최신 {mc['max_candidates']}통만 봄")
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass
    notes.append(stats)
    return saved, notes


def decrypt_xlsx(src, dst, password):
    """msoffcrypto로 암호만 벗긴다(원본 OOXML 그대로). 실패하면 부분 출력(우리가 'xb'로 만든 파일)을 지운다."""
    import msoffcrypto
    made = False
    try:
        with open(src, "rb") as f:
            of = msoffcrypto.OfficeFile(f)
            of.load_key(password=password, verify_password=True)
            with open(dst, "xb") as o:
                made = True
                of.decrypt(o)
    except BaseException:
        if made:
            try:
                os.remove(dst)
            except OSError:
                pass
        raise


def kakao_requested(r, kc):
    head = " ".join(" ".join(srcmod.row_texts(x)) for x in r["rows"][:r["h"]])
    m = re.search(kc["requested_re"], head)
    if not m:
        return None
    try:
        return dt.datetime(*[int(x) for x in m.groups()[:6]])
    except (ValueError, TypeError):
        return None


def check_kakao(path, acc, kc, rec_start, today, floor):
    """카뱅 검사 2·4·5 — 서명·식별(마스킹 계좌)·행 해석·잔액 연속 + 머리 조회기간 시작 ≤ 권장 시작 ∧ 끝 ≥ 오늘−1
    ∧ 머리 요청일시 ≥ floor ∧ 행 날짜 ⊂ 머리 조회기간."""
    with open(path, "rb") as f:
        kind = srcmod.sniff(f.read())
    if kind != "xlsx":
        raise ItemFail(f"카뱅 파일 종류 {kind} ≠ xlsx")
    r = read_identified(path, acc, kc["source"])
    per = r["period"]
    if per is None:
        raise ItemFail("머리 조회기간을 못 읽음")
    req = kakao_requested(r, kc)
    ds = [t.date for t in r["txs"]]
    bad = []
    if per[0] > rec_start:
        bad.append(f"머리 조회기간 시작 {per[0]} > 권장 시작 {rec_start}")
    if per[1] < today - dt.timedelta(days=1):
        bad.append(f"머리 조회기간 끝 {per[1]} < 오늘−1")
    if req is None or req < floor:
        bad.append("머리 요청일시가 이번 실행 것이 아님(지난 내보내기)" if req else "머리 요청일시를 못 읽음")
    if not all(per[0] <= d <= per[1] for d in ds):
        bad.append("행 날짜가 머리 조회기간 밖")
    if r["chain"] is None:
        bad.append("잔액 열 사슬 없음")
    if bad:
        raise ItemFail("카뱅 검사 4: " + "; ".join(bad))
    return {"period": [per[0].isoformat(), per[1].isoformat()], "requested": req.isoformat(sep=" "), "rows": len(r["txs"])}


def name_time(fname, kc):
    m = re.search(r"_(\d{16})", fname)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group(1)[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None


def last_collected(runs_root, inbox_root):
    """지난 성공 회차(같은 inbox로 ③까지 간 카뱅 '받음')의 수거 시각 — 회차 summary.json 읽기만."""
    best = None
    if not os.path.isdir(runs_root):
        return None
    for n in os.listdir(runs_root):
        p = os.path.join(runs_root, n, "summary.json")
        try:
            with open(p, encoding="utf-8") as f:
                s = json.load(f)
        except (OSError, ValueError):
            continue
        if os.path.normcase(str(s.get("inbox_root", ""))) != os.path.normcase(inbox_root) or not s.get("committed"):
            continue
        k = (s.get("items") or {}).get("카카오뱅크") or {}
        if k.get("status") == "받음" and k.get("collected_at"):
            try:
                t = dt.datetime.fromisoformat(k["collected_at"])
            except ValueError:
                continue
            best = t if best is None or t > best else best
    return best


def run_kakao(cfg, acc, item, run_dir, log, state, hooks, mail_only):
    """카카오뱅크 한 출처 — IMAP(기본) 또는 지정 폴더(--mail-only) → 복호화 → 검사 → 첫 통과 1개 채택."""
    kc = cfg["kakao"]
    reader = hooks.get("cred_read") or _win_cred_read
    decrypt = hooks.get("decrypt") or decrypt_xlsx
    can_decrypt = bool(hooks.get("decrypt")) or have_msoffcrypto()
    run_start = state["run_start"]
    folder = os.path.join(run_dir, "kakao")
    os.makedirs(folder, exist_ok=True)
    today = state["today"]
    rec_start = item["start"]
    cands = []  # (시각, 경로, 암호 여부, 출처 설명)
    if mail_only:
        drop = state["paths"].get("mail_drop")
        if not drop:
            raise ItemFail("mail_drop.folder 설정이 없다")
        lc = last_collected(state["paths"]["runs_root"], state["paths"]["inbox_root"])
        floor = lc or dt.datetime.combine(run_start.date() - dt.timedelta(days=1), dt.time())
        item["floor_basis"] = f"지난 성공 회차 수거 시각 {lc.isoformat(sep=' ')}" if lc else "기록 없음 — 실행일 − 1일 0시"
        enc_rx, plain_rx = re.compile(kc["pattern_encrypted"]), re.compile(kc["pattern_plain"])
        seen, skipped = set(), []
        names = sorted(os.listdir(drop)) if os.path.isdir(drop) else []
        for n in names:
            full = os.path.join(drop, n)
            if n.startswith("~$") or not os.path.isfile(full) or n.endswith((".tmp", ".crdownload", ".part")):
                continue
            enc, plain = enc_rx.fullmatch(n), plain_rx.fullmatch(n)
            if not (enc or plain):
                continue
            m = md5(full)
            if m in seen:
                skipped.append("같은 폴더 md5 중복")
                continue
            seen.add(m)
            if enc and not can_decrypt:
                skipped.append("풀지 않은 원본(자동 복호화 꺼짐 — 엑셀에서 풀어 _v.xlsx로 저장)")
                continue
            if plain and can_decrypt:
                skipped.append("손으로 푼 _v.xlsx(무시 — 자동 복호화 켜짐)")
                continue
            when = name_time(n, kc) if enc else None
            cands.append((when, full, bool(enc), "지정 폴더"))
        item["skipped"] = skipped
    else:
        if not cfg.get("mail"):
            raise ItemFail("[mail] 설정이 없다")
        if not can_decrypt:
            raise ItemFail("msoffcrypto가 없어 암호를 풀 수 없다 — 설치는 사용자에게 먼저 묻는다(하향: 손으로 푼 _v.xlsx만 새 폴더에)")
        floor = run_start - dt.timedelta(minutes=10)
        saved, notes = imap_collect(cfg["mail"], kc, run_start, folder, reader, log, hooks.get("sleep", time.sleep))
        item["mail_notes"] = notes
        for k, path, when in saved:
            cands.append((when, path, True, f"메일 {k}"))
        if not cands:
            raise ItemFail("메일 없음 — 카카오뱅크 앱에서 '받기'를 눌렀는지 확인")
    tried = []
    for when, path, enc, where in sorted(cands, key=lambda x: x[0] or dt.datetime.min, reverse=True):
        if mail_only and when is not None and when < floor:
            tried.append(f"{where}: 파일 이름 시각이 지난 수거 시각보다 이름")
            continue
        try:
            if enc:
                if mail_only:  # 지정 폴더 원본은 읽기만 — 회차 폴더 사본에서 푼다
                    src_copy = unique_file(folder, os.path.basename(path))
                    copy_new(path, src_copy)
                    path = src_copy
                out = unique_file(os.path.dirname(path), os.path.splitext(os.path.basename(path))[0] + "_v.xlsx")
                got = reader(kc["cred_target"])
                if not got or not got[1]:
                    raise ItemFail(f"자격 증명 {kc['cred_target']} 없음 — " + CRED_HELP.format(target=kc["cred_target"], user="xlsx"))
                try:
                    decrypt(path, out, got[1])
                except ItemFail:
                    raise
                except Exception as e:  # noqa: BLE001 — InvalidKeyError 등(문구는 남기지 않음)
                    raise ItemFail(f"복호화 실패({ename(e)})")
                finally:
                    got = None
            else:
                out = unique_file(folder, os.path.basename(path))
                copy_new(path, out)
            res = check_kakao(out, acc, kc, rec_start, today, floor)
        except ItemFail as e:
            tried.append(f"{where}: {e}")
            continue
        item.update(status="받음", _path=out, file=os.path.basename(out), md5=md5(out), check=res,
                    collected_at=run_start.isoformat(timespec="seconds"), via=where)
        item["others"] = tried
        return
    item["others"] = tried
    raise ItemFail("검사를 통과한 카카오뱅크 파일 없음 — " + ("; ".join(tried) if tried else "후보 0"))


# ------------------------------------------------------------
# ③ 확정 — 회차 stage\ 완성 → 폴더째 이름 바꾸기(같은 볼륨 · 대상 있으면 실패)
# ------------------------------------------------------------

def commit(items, paths, run_dir, today, known_md5, log, hooks):
    ok = [it for it in items.values() if it.get("status") == "받음"]
    for it in ok:
        if it["md5"] in known_md5:
            it["status"], it["error"] = "이미 있음", "같은 md5 파일이 inbox·원장에 이미 있다 — 넣지 않음"
            log(f"  [③] {it['source']}: 이미 있음(같은 md5) — 넣지 않음")
    ok = [it for it in ok if it["status"] == "받음"]
    if not ok:
        return None
    inbox = paths["inbox_root"]
    name = free_name(today.strftime("%Y%m%d"), [inbox])
    stage = os.path.join(run_dir, "stage", name)
    os.makedirs(stage)
    for it in ok:
        dst = unique_file(stage, it["file"])
        copy_new(it["_path"], dst, hooks.get("copy_hook"))
        if md5(dst) != it["md5"]:
            raise OSError("stage 사본 md5가 원본과 다름")
        it["final_name"] = os.path.basename(dst)
    if hooks.get("before_rename"):
        hooks["before_rename"](stage)
    target = os.path.join(inbox, name)
    os.makedirs(inbox, exist_ok=True)
    if os.path.exists(target):
        raise OSError("대상 폴더가 생겼다 — 덮어쓰지 않음")
    os.rename(stage, target)  # Windows: 대상이 있으면 실패(덮어쓰기 0) · 같은 볼륨에서 폴더째 한 번에
    return target


# ------------------------------------------------------------
# 계획 · dry-run · 실행
# ------------------------------------------------------------

def make_plan(cfg, acc, paths, today, args):
    sel = [s.strip() for s in (args.sites or "").split(",") if s.strip()]
    sites = [] if (args.mail_only or args.no_web) else [s for s in cfg.get("site", []) if not sel or s["id"] in sel]
    unknown = [s for s in sel if s not in {x["id"] for x in cfg.get("site", [])}]
    if unknown:
        raise StopRun(f"--sites의 {unknown}가 설정에 없다")
    for s in sites:
        if s["id"] not in acc["by_id"]:
            raise StopRun(f"[[site]] {s['id']}가 계좌.toml [[source]] id에 없다")
    overrides = {}
    for x in args.start or []:
        sid, _, d = x.partition("=")
        try:
            overrides[sid.strip()] = dt.date.fromisoformat(d.strip())
        except ValueError:
            raise StopRun(f"--start 꼴은 출처=YYYY-MM-DD: {x!r}")
    ends, info = last_received(acc, paths)
    items = {}
    ov = cfg["period"]["overlap_days"]
    for s in sites:
        items[s["id"]] = dict(plan_period(s["id"], acc, ends, today, ov, s.get("max_days"), overrides.get(s["id"])),
                              kind="web", status="미실행")
    kakao = cfg.get("kakao")
    if kakao and not args.no_mail and (args.mail_only or cfg.get("mail")):
        if kakao["source"] not in acc["by_id"]:
            raise StopRun(f"kakao.source {kakao['source']}가 계좌.toml에 없다")
        items[kakao["source"]] = dict(plan_period(kakao["source"], acc, ends, today, ov, None, overrides.get(kakao["source"])),
                                      kind="mail_drop" if args.mail_only else "imap", status="미실행")
    for k, v in items.items():
        v["source"] = k
    return {"sites": sites, "items": items, "inbox": info, "today": today}


def kakao_guide(it, today):
    return (f"카카오뱅크 앱 → 거래내역 다운로드 → 기간 {it['start']} ~ {today} "
            f"({kakao_buttons(it['start'], today)}) → 이메일 → '받기'")


def cmd_dry_run(cfg, acc, paths, plan, args, hooks):
    reader = hooks.get("cred_read") or _win_cred_read
    log = Log()
    log(f"[dry-run] 브라우저 0 · 메일 0 · 파일·폴더 변경 0 · 기준일 {plan['today']}")
    if os.path.normcase(paths["inbox_root"]) != os.path.normcase(paths["scan_inbox"]):
        log(f"[리허설] ③ 대상 {trust(paths['inbox_root'])} ≠ scan inbox {trust(paths['scan_inbox'])}")
    inf = plan["inbox"]
    log(f"기간 근거: 원장 period + scan inbox 파일 {inf['files']}개(무시(설정) {inf['ignored']} · 읽기 실패 {inf['failed']} — 제외)"
        f" · 겹침 {cfg['period']['overlap_days']}일")
    log("| 출처 | 받는 길 | 시작 | 끝 | 일수 | 근거 |")
    log("|---|---|---|---|---|---|")
    for sid, it in plan["items"].items():
        way = {"web": "웹(로그인은 사용자)", "imap": "메일 IMAP", "mail_drop": "지정 폴더"}[it["kind"]]
        log(f"| {sid} | {way} | {it['start']} | {it['end']} | {(it['end'] - it['start']).days + 1} | {it['basis']} |")
        if it["warn"]:
            log(f"  [주의] {sid}: {it['warn']}")
    base = plan["today"].strftime("%Y%m%d")
    log(f"회차 폴더: {trust(paths['runs_root'])}\\<YYYYMMDD-hhmmss>\\ (받은 원본·summary·stage — inbox 밖)")
    log(f"③ 새 폴더: {trust(os.path.join(paths['inbox_root'], free_name(base, [paths['inbox_root']])))} (받음이 1개 이상일 때만 · 폴더째 이동)")
    for s in plan["sites"]:
        acts = s.get("actions", {})
        by = {t: sum(1 for a in acts.values() if a.get("type", "click") == t) for t in ACTION_TYPES}
        log(f"  {s['id']}: 허용 동작 {len(acts)}개(" + " · ".join(f"{t} {n}" for t, n in by.items() if n) + ")"
            + f" · 사용자 몫: {s.get('login_hint', '로그인')}")
    if plan["sites"]:
        log(f"  프로필 {trust(paths['profile_dir'])} · 금지 문구 {len(cfg['forbidden_texts'])}개 · 로그인 대기 최대 "
            f"{int(cfg['timeout']['login']) // 60}분/곳 · dialog accept 0")
    kk = cfg.get("kakao")
    ksrc = kk["source"] if kk else None
    if ksrc in plan["items"]:
        it = plan["items"][ksrc]
        log(f"카카오뱅크: msoffcrypto {'있음' if have_msoffcrypto() else '없음'} · 자격 증명 {kk['cred_target']} "
            f"{cred_status(reader, kk['cred_target'])}")
        if it["kind"] == "imap":
            mc = cfg["mail"]
            log(f"  메일: {mc['host']}:{mc['port']} 읽기 전용(EXAMINE·PEEK·TLS 검증) · 조건 항목 from·subject_contains·attach_re·"
                f"since_days · 자격 증명 {mc['cred_target']} {cred_status(reader, mc['cred_target'])}")
            log(f"  누를 시점: `--run`을 시작한 뒤(또는 시작 10분 전부터) — {kakao_guide(it, plan['today'])}")
        else:
            log(f"  지정 폴더 {trust(paths['mail_drop'] or '(없음)')}: 원본 꼴·_v 꼴 두 패턴 · 지난 성공 수거 시각 뒤 것만")
        for t in (kk["cred_target"],) + ((cfg["mail"]["cred_target"],) if it["kind"] == "imap" else ()):
            if cred_status(reader, t) != "있음":
                log("  [설정] " + CRED_HELP.format(target=t, user="<네이버 아이디>" if "naver" in t else "xlsx"))
    log("다음: `--run`(창이 뜨면 사이트마다 로그인) → 끝에 찍히는 새 폴더로 `$IMP scan`")
    return EXIT_OK


def cmd_run(cfg, acc, paths, plan, args, hooks):
    now = hooks.get("now") or dt.datetime.now
    run_start = now()
    if plan["sites"] and os.path.isdir(paths["profile_dir"]) and profile_in_use(paths["profile_dir"]):
        print(f"[FAIL] 전용 프로필을 쓰는 크롬이 이미 떠 있음 — 그 창을 닫고 다시 실행({scrub(trust(paths['profile_dir']))})")
        return EXIT_STOP
    run_dir, rid = make_new_dir(paths["runs_root"], run_start.strftime("%Y%m%d-%H%M%S"))
    log = Log(os.path.join(run_dir, "debug") if args.debug else None)
    items = plan["items"]
    state = {"paths": paths, "run_dir": run_dir, "run_start": run_start, "today": plan["today"], "clicks": [],
             "dialogs": [], "debug": args.debug, "try_extend": args.try_extend}
    summary = {"tool": "pc_fetch.py", "run": trust(rid), "started": run_start.isoformat(timespec="seconds"),
               "inbox_root": trust(paths["inbox_root"]), "scan_inbox": trust(paths["scan_inbox"]), "committed": None,
               "mode": "mail-only" if args.mail_only else ("no-web" if args.no_web else "run")}
    write_json(os.path.join(run_dir, "summary.json"), dict(summary, result="진행"))
    log(f"[run] 회차 {trust(rid)} · 기준일 {plan['today']} · 출처 {len(items)}개 · 회차 폴더 {trust(run_dir)}")
    kk = cfg.get("kakao") or {}
    ksrc = kk.get("source")
    if ksrc in items and items[ksrc]["kind"] == "imap":
        log("[카카오뱅크] 지금 휴대폰에서: " + kakao_guide(items[ksrc], plan["today"]) + " (메일은 끝에 수거)")
    try:
        if plan["sites"]:
            run_web(cfg, plan["sites"], acc, items, run_dir, log, state, hooks, args.probe_paging)
    except StopRun as e:
        for it in items.values():
            if it["status"] in ("미실행", "진행") and it["kind"] == "web":
                it.update(status="받지 못함", error=str(e))
        log(f"[멈춤] 웹: {e}")
    except Exception as e:  # noqa: BLE001 — 브라우저 죽음 등: 웹 출처만 실패, 메일·③은 돈다
        for it in items.values():
            if it["status"] in ("미실행", "진행") and it["kind"] == "web":
                it.update(status="받지 못함", error=f"예외 {ename(e)}")
        log.exc("web", e)
        log(f"[멈춤] 웹: 예외 {ename(e)}")
    if ksrc in items:
        it = items[ksrc]
        it["status"] = "진행"
        log(f"[{ksrc}] {'지정 폴더' if args.mail_only else '메일'} 수거")
        try:
            run_kakao(cfg, acc, it, run_dir, log, state, hooks, args.mail_only)
        except ItemFail as e:
            it.update(status="받지 못함", error=str(e))
        except Exception as e:  # noqa: BLE001
            it.update(status="받지 못함", error=f"예외 {ename(e)}")
            log.exc(ksrc, e)
        log(f"  → {ksrc}: {it['status']}" + (f" — {it['error']}" if it.get("error") else ""))
    # ---- ③
    known = set(plan["inbox"]["md5"])
    for r, _d, fs in os.walk(paths["inbox_root"]):
        for n in fs:
            try:
                known.add(md5(os.path.join(r, n)))
            except OSError:
                pass
    target, commit_err = None, None
    try:
        target = commit(items, paths, run_dir, plan["today"], known, log, hooks)
    except Exception as e:  # noqa: BLE001
        commit_err = ename(e)
        log(f"[FAIL] ③ 확정 실패({commit_err}) — 받은 파일은 회차 폴더에만, inbox 변화 0")
    got = [k for k, v in items.items() if v["status"] == "받음"]
    missed = [k for k, v in items.items() if v["status"] not in ("받음", "0건", "이미 있음")]
    if commit_err:
        missed = sorted(set(missed) | set(got))
    print("| 출처 | 결과 | 기간 | 파일 | 비고 |")
    print("|---|---|---|---|---|")
    for k, v in items.items():
        print(scrub(f"| {k} | {v['status']} | {v['start']}~{v['end']} | {v.get('final_name') or v.get('file') or '-'} | "
                    f"{(v.get('error') or '')[:140]} |"))
    if target:
        log(f"[③] 새 폴더 {trust(target)} — 내역 파일 {len(got)}개(폴더째 이동 · 기존 폴더·파일 쓰기 0)")
    ww = state.get("window_watch")
    summary.update({"finished": now().isoformat(timespec="seconds"), "committed": trust(target) if target else None,
                    "commit_error": commit_err, "clicks": state["clicks"], "dialogs": state["dialogs"],
                    "windows": ww.export() if ww else [], "profile_cleanup": state.get("profile_cleanup"),
                    "items": {k: {x: (str(y) if isinstance(y, dt.date) else y) for x, y in v.items() if not x.startswith("_")}
                              for k, v in items.items()},
                    "result": "ok" if not missed else "partial"})
    summary["log"] = log.lines
    write_json(os.path.join(run_dir, "summary.json"), summary)
    if missed:
        where = trust(target) if target else trust(os.path.join(paths["inbox_root"], plan["today"].strftime("%Y%m%d")))
        log(f"[받지 못함] {', '.join(missed)} — 손으로 받아 {where}\\ 에 넣는다(카카오뱅크는 엑셀에서 푼 _v.xlsx만 · 암호 원본은 넣지 않음)"
            + ("" if target else " — 폴더가 없으면 새로 만든다"))
    log("다음: `$IMP scan`(code-tab 12절 ②)" + (" — summary는 회차 폴더에만" if target else ""))
    return EXIT_OK if not missed else EXIT_PARTIAL


# ------------------------------------------------------------
# 대조(리허설 — 완료 18·19): 받은 묶음 ↔ 손 묶음
# ------------------------------------------------------------

def bundle(dirs, acc):
    """폴더들의 내역 파일 → 출처마다 {거래키: Tx}·덮는 기간 — scan과 같은 파서·서명(읽기만)."""
    out, per, fails = {}, {}, []
    for d in dirs:
        for r, _ds, fs in os.walk(d):
            for n in sorted(fs):
                if n.startswith("~$") or n.endswith(".json"):
                    continue
                full = os.path.join(r, n)
                try:
                    kind, sheets = srcmod.read_table(full)
                except srcmod.SourceError:
                    fails.append(n)
                    continue
                hit = None
                for s in acc["sources"]:
                    if kind not in s.file_types:
                        continue
                    try:
                        h = srcmod.identify(sheets, s)
                    except srcmod.SourceError:
                        continue
                    if h:
                        hit = (s, h)
                        break
                if not hit:
                    fails.append(n)
                    continue
                s, (si, h) = hit
                try:
                    txs = srcmod.parse_sheet(sheets[si][1], h, s, n)
                    srcmod.check_balance_chain(txs, n)
                    srcmod.assign_keys(txs, s)
                except srcmod.SourceError:
                    fails.append(n)
                    continue
                lo, hi = min(t.date for t in txs), max(t.date for t in txs)
                hp = srcmod.header_period(sheets[si][1], h, s)
                if hp:
                    lo, hi = min(lo, hp[0]), max(hi, hp[1])
                out.setdefault(s.id, {}).update({t.key: t for t in txs})
                a, b = per.get(s.id, (lo, hi))
                per[s.id] = (min(a, lo), max(b, hi))
    return out, per, fails


def stats(txs):
    """건수 · 부호 있는 합계 · 기간 첫·끝 잔액(잔액 열이 있는 출처)."""
    signed = sum((-t.amount if t.direction in ("out",) else t.amount) for t in txs)
    bal = [t for t in txs if t.balance is not None]
    order = sorted(bal, key=lambda t: (t.date, t.time, t.order, t.row))
    return {"n": len(txs), "sum": signed, "first_bal": order[0].balance if order else None,
            "last_bal": order[-1].balance if order else None}


def cmd_compare(args):
    cfg = load_config(args.config)
    paths = resolve_paths(cfg)
    acc = load_accounts(paths["accounts"])
    A, pa, fa = bundle(args.a, acc)
    B, pb, fb = bundle(args.b, acc)
    log = Log()
    log(f"[compare] 받은 묶음 {len(args.a)}폴더 · 손 묶음 {len(args.b)}폴더 — 같은 파서·서명(계좌.toml), 금액은 찍지 않음")
    if fa or fb:
        log(f"  읽지 못한 파일: 받은 {len(fa)} · 손 {len(fb)}(암호 원본·다른 꼴 — 대조 밖)")
    same_all = True
    log("| 출처 | 기간 | 건수(받은/손) | 합계 | 첫 잔액 | 끝 잔액 | 거래키 | 판정 |")
    log("|---|---|---|---|---|---|---|---|")
    for sid in sorted(set(A) | set(B)):
        if sid not in A or sid not in B:
            log(f"| {sid} | - | {len(A.get(sid, {}))}/{len(B.get(sid, {}))} | - | - | - | - | 한쪽에만 |")
            same_all = False
            continue
        lo = max(pa[sid][0], pb[sid][0])
        hi = min(pa[sid][1], pb[sid][1]) - dt.timedelta(days=0 if args.to else 1)
        if args.date_from:
            lo = max(lo, dt.date.fromisoformat(args.date_from))
        if args.to:
            hi = min(hi, dt.date.fromisoformat(args.to))
        ta = [t for t in A[sid].values() if lo <= t.date <= hi]
        tb = [t for t in B[sid].values() if lo <= t.date <= hi]
        sa, sb = stats(ta), stats(tb)
        ka, kb = {t.key for t in ta}, {t.key for t in tb}
        eq = {k: sa[k] == sb[k] for k in sa}
        ok = all(eq.values()) and ka == kb
        same_all &= ok
        log(f"| {sid} | {lo}~{hi} | {sa['n']}/{sb['n']} | {'같음' if eq['sum'] else '다름'} | "
            f"{'같음' if eq['first_bal'] else '다름'} | {'같음' if eq['last_bal'] else '다름'} | "
            f"{'같음' if ka == kb else f'받은만 {len(ka - kb)} · 손만 {len(kb - ka)}'} | {'같음' if ok else '다름'} |")
    log("판정: " + ("출처별 거래 건수·합계·잔액·거래키 모두 같음" if same_all else "다른 곳이 있음 — 위 표"))
    return EXIT_OK if same_all else EXIT_PARTIAL


# ------------------------------------------------------------
# main
# ------------------------------------------------------------

def parse_args(argv):
    ap = argparse.ArgumentParser(description="금융사 내역 파일 받기(이 PC 전용) — 기본 dry-run")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="받기 설정(기본 D:\\household\\rules\\받기.toml)")
    sub = ap.add_subparsers(dest="cmd")
    cp = sub.add_parser("compare", help="받은 묶음 ↔ 손 묶음 대조(리허설)")
    cp.add_argument("--a", nargs="+", required=True, help="받은 묶음 폴더들")
    cp.add_argument("--b", nargs="+", required=True, help="손으로 받은 묶음 폴더들")
    cp.add_argument("--from", dest="date_from")
    cp.add_argument("--to")
    ap.add_argument("--run", action="store_true", help="실행(없으면 dry-run)")
    ap.add_argument("--mail-only", action="store_true", help="웹 0 — 카카오뱅크만 지정 폴더에서(안 C 하향 길)")
    ap.add_argument("--no-web", action="store_true", help="웹 0 — 카카오뱅크만 IMAP으로")
    ap.add_argument("--no-mail", action="store_true", help="카카오뱅크 0")
    ap.add_argument("--sites", help="웹 출처 일부만(쉼표)")
    ap.add_argument("--start", action="append", metavar="출처=YYYY-MM-DD", help="받는 시작일을 앞으로 넓힘(근거가 표에)")
    ap.add_argument("--today", help="기준일(시험용)")
    ap.add_argument("--debug", action="store_true", help="실패 때 그림·예외 원문을 회차 debug\\에만")
    ap.add_argument("--probe-paging", action="store_true", help="(리허설 전용) 쪽 나눔 실측")
    ap.add_argument("--try-extend", action="store_true", help="(리허설 전용) 사이트마다 세션 연장 1회 시도 — 결과만 기록")
    return ap.parse_args(argv)


def main(argv=None, hooks=None):
    hooks = hooks or {}
    a = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if a.cmd == "compare":
            return cmd_compare(a)
        cfg = load_config(a.config)
        paths = resolve_paths(cfg)
        bad = path_problems(paths)
        if bad:
            raise StopRun("경로 규칙 위반: " + "; ".join(bad))
        if a.mail_only and not paths.get("mail_drop"):
            raise StopRun("--mail-only인데 mail_drop.folder 설정이 없다")
        acc = load_accounts(paths["accounts"])
        today = dt.date.fromisoformat(a.today) if a.today else imp.today_kst()
        plan = make_plan(cfg, acc, paths, today, a)
        if not plan["items"]:
            raise StopRun("받을 출처가 없다(--sites·--no-mail 확인)")
        if not a.run:
            return cmd_dry_run(cfg, acc, paths, plan, a, hooks)
        if plan["sites"] and not hooks.get("sync_playwright"):
            _playwright()
        return cmd_run(cfg, acc, paths, plan, a, hooks)
    except StopRun as e:
        print(scrub(f"[FAIL] {e}"))
        return EXIT_STOP


if __name__ == "__main__":
    sys.exit(main())
