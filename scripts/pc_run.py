"""
이 PC(Windows · Claude Code 탭)에서 가계부 리포트를 재계산 → 확인 → 배포하는 실행 스크립트.

웹(claude.ai)은 이 파일을 쓰지 않는다 — 웹 절차는 SKILL.md 그대로다. PC 절차 설명은 pc/code-tab.md.

사용법 (PY = D:\\household\\.venv\\Scripts\\python.exe, 경로는 / 로 적는다):
    $PY scripts/pc_run.py check                               S0 사전 점검 (쓰기 0)
    $PY scripts/pc_run.py sync                                운영 clone 빨리 감기(뒤에만 있을 때) + exclude 줄
    $PY scripts/pc_run.py build  --csv <CSV 경로>             ② 새 회차 폴더에 재계산. stamp.json은 여기서만 쓴다
    $PY scripts/pc_run.py review --run <회차> [--ack-transfer] ③ 확인. 멈춤 질문이 없을 때만 승인 코드를 찍는다
    $PY scripts/pc_run.py deploy --run <회차> [--ack-transfer] [--confirm <승인 코드>]
                                                              ④ 배포 전 읽기(기본 dry-run, 쓰기 0) / ⑤ 배포
    $PY scripts/pc_run.py live   --run <회차>                 ⑥ 다시 읽어 확인 (exit 0 일치 · 1 실패 · 3 CDN 미확인)
    $PY scripts/pc_run.py record --run <회차>                 ⑦ runs/log.md 한 줄

    <회차>는 회차 폴더 이름(예: 20261012-161500)이나 경로.
    --rehearsal : 리허설 모드. 개발 clone(기능 브랜치)으로 돌린다. 회차 폴더는 work-rehearsal/에 만들고
                  deploy --confirm · sync · record는 막는다. 첫 줄에 [리허설]이 찍힌다. 운영 회차에서는 쓰지 않는다.
    --home DIR  : 작업 루트 (기본: clone의 부모 폴더 = D:\\household)
    --repo DIR  : clone (기본: 이 파일이 들어 있는 clone)

지키는 것(탐색 기준선 3-0 · 완료 기준 5~15):
- 회차마다 새 폴더. pipeline rc ≠ 0이면 build하지 않는다. build는 그 폴더의 bundle만 읽는다.
- build는 main · 깨끗한 트리에서만. stamp에 로컬 HEAD · origin/main · 원본 CSV md5 · report md5를 적는다.
- 승인 코드(report md5 앞 8자)는 멈춤 질문이 다 끝난 회차에만 나온다. deploy --confirm은 같은 프로세스에서
  ④를 다시 확인하고, 사용자가 답한 코드 = 그 폴더 report md5일 때만 커밋 1개 · push 1회(force·재시도 없음).
- 기준판(잔고 급변 비교)은 git show <stamp.head>:index.html. 없거나 비었거나 "건너뜀"이면 FAIL.
- push 범위 = index.html(+ 코드 회차의 승인된 코드 파일). CSV·bundle·report는 clone 밖 회차 폴더에만 있다.
- 토큰을 쓰지 않는다. push는 이 PC의 git 자격 증명(GCM)으로, 창 없이(GCM_INTERACTIVE=never).
"""
import argparse
import fnmatch
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone

# 저장소 안에 __pycache__를 만들지 않는다(만들면 트리가 더러워져 ④가 멈춘다 — 이월 7).
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deploy  # noqa: E402  (validate_html · check_balance_jump · _extract_data · REPO)

PAGES_URL = "https://leekwanbeom.github.io/household-report/"
ACTIONS_API = "https://api.github.com/repos/{repo}/actions/runs?head_sha={sha}&per_page=20"
PAGES_WORKFLOW = "pages build and deployment"
# 커밋 작성자. 개인 이메일이면 push가 거절된다(SKILL.md 대체 경로 5).
COMMIT_NAME = "LeeKwanBeom"
COMMIT_EMAIL = "322668067+LeeKwanBeom@users.noreply.github.com"

OPS_CLONE = "household-report"          # 운영 clone 폴더 이름 (<home>/household-report)
EXCLUDE_LINES = ["*.csv", "!tests/fixtures/*.csv", "data_bundle.json", "__pycache__/"]
# 2026-10-12 리허설·탐색에서 공개판과 같은 md5를 낸 판. 다르면 S0가 [주의](이월 8).
REFERENCE_VERSIONS = {"pandas": "3.0.6", "numpy": "2.5.3"}
# (작업 루트 기준 로컬 파일, 저장소 정본). md5가 다르면 S0가 [주의].
LOCAL_FILES = [
    (".claude/skills/household-run/SKILL.md", "pc/household-run/SKILL.md"),
    ("CLAUDE.md", "pc/CLAUDE.md"),
    (".claude/settings.json", "pc/settings.json"),
]
# 코드 회차에 함께 올릴 수 있는 경로 = 웹 --with-code와 같은 패턴(deploy.CODE_GLOBS) + PC 문서(pc/).
PC_DOC_GLOBS = ["pc/*"]
ACTIONS_DEADLINE = 180   # push 뒤 Pages 빌드 성공을 기다리는 상한(초)
PUBLIC_DEADLINE = 720    # push 뒤 공개판 md5 일치를 기다리는 상한(초) = 빌드 + CDN max-age 600
POLL_SECONDS = 20
RUN_NAME = re.compile(r"^(\d{8}-\d{6})(?:-(\d+))?$")
KST = timezone(timedelta(hours=9))


# ------------------------------------------------------------
# 바깥과 닿는 자리 — 시험이 이 함수들만 바꿔 끼운다
# ------------------------------------------------------------

def http_get(url, timeout=20, accept="*/*"):
    req = urllib.request.Request(url, headers={"User-Agent": "household-pc-run", "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def _time():
    return time.time()


def _sleep(seconds):
    time.sleep(seconds)


def now_kst():
    return datetime.now(KST)


# ------------------------------------------------------------
# 작은 도구
# ------------------------------------------------------------

def p(path):
    """출력용 경로. Git Bash에 그대로 붙여 넣어도 깨지지 않게 / 로 적는다."""
    return str(path).replace("\\", "/")


def same_path(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def md5_bytes(b):
    return hashlib.md5(b).hexdigest()


def md5_file(path):
    with open(path, "rb") as f:
        return md5_bytes(f.read())


def write_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Out:
    """[PASS]/[FAIL]/[주의]/[정보]/[멈춤] 줄을 찍고 FAIL을 센다."""

    def __init__(self):
        self.fails = []

    def ok(self, msg):
        print(f"[PASS] {msg}")

    def fail(self, msg):
        print(f"[FAIL] {msg}")
        self.fails.append(msg)

    def warn(self, msg):
        print(f"[주의] {msg}")

    def info(self, msg):
        print(f"[정보] {msg}")


def _git_env():
    env = dict(os.environ)
    # 창·프롬프트 없이. 인증이 안 되면 기다리지 않고 실패한다. status가 .git/index를 고쳐 쓰지 않게.
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", GIT_OPTIONAL_LOCKS="0")
    for k in ("GIT_ASKPASS", "SSH_ASKPASS"):
        env.pop(k, None)
    return env


def git(repo, *args, timeout=120):
    """(rc, stdout bytes, stderr 문자열). rc None = 시간 초과(결과 모름)."""
    cmd = ["git", "-C", repo, "-c", "core.quotepath=false", *args]
    try:
        r = subprocess.run(cmd, capture_output=True, env=_git_env(), timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, b"", f"시간 초과({timeout}초)"
    except FileNotFoundError:
        return 127, b"", "git을 찾을 수 없음"
    return r.returncode, r.stdout, r.stderr.decode("utf-8", "replace").strip()


def gtext(repo, *args, timeout=120):
    rc, out, err = git(repo, *args, timeout=timeout)
    return rc, out.decode("utf-8", "replace").strip(), err


def glines(repo, *args):
    rc, out, _ = gtext(repo, *args)
    return [x for x in out.splitlines() if x.strip()] if rc == 0 else None


# ------------------------------------------------------------
# 실행 맥락
# ------------------------------------------------------------

class Ctx:
    def __init__(self, repo, home, rehearsal):
        self.repo = os.path.abspath(repo)
        self.home = os.path.abspath(home)
        self.rehearsal = rehearsal
        self.mode = "rehearsal" if rehearsal else "operational"
        self.ops_clone = os.path.join(self.home, OPS_CLONE)
        self.work_root = os.path.join(self.home, "work-rehearsal" if rehearsal else "work")
        self.uploads = os.path.join(self.home, "uploads")
        self.log_path = os.path.join(self.home, "runs", "log.md")


def branch_of(ctx):
    rc, out, _ = gtext(ctx.repo, "symbolic-ref", "--short", "-q", "HEAD")
    return out if rc == 0 else ""


def mode_line(ctx, cmd):
    """모든 명령의 첫 줄. 리허설이면 [리허설]."""
    if ctx.rehearsal:
        print(f"[리허설] {cmd} · 개발 clone {p(ctx.repo)} ({branch_of(ctx) or '분리된 HEAD'}) · "
              f"회차 폴더 {p(ctx.work_root)} · deploy --confirm·sync·record 막힘")
    else:
        print(f"[운영] {cmd} · clone {p(ctx.repo)} · 회차 폴더 {p(ctx.work_root)}")


def check_mode_repo(ctx, out):
    """운영 모드는 운영 clone의 main에서만 돈다. 개발 clone이면 --rehearsal을 붙여야 한다."""
    if ctx.rehearsal:
        if same_path(ctx.repo, ctx.ops_clone):
            out.info("리허설인데 운영 clone을 쓰고 있다(쓰기는 회차 폴더에만 한다)")
        return
    if not same_path(ctx.repo, ctx.ops_clone):
        out.fail(f"운영 모드는 운영 clone({p(ctx.ops_clone)})에서만 돈다 — 지금 {p(ctx.repo)}. "
                 f"개발 clone이면 --rehearsal")


def home_git_toplevel(ctx):
    """작업 루트가 git 작업 트리 안이면 그 최상위 경로(K3 — 루트 git 금지)."""
    if os.path.exists(os.path.join(ctx.home, ".git")):
        return ctx.home
    rc, out, _ = gtext(ctx.home, "rev-parse", "--show-toplevel")
    return out if rc == 0 and out else None


def check_root_git(ctx, out):
    top = home_git_toplevel(ctx)
    if top:
        out.fail(f"작업 루트 {p(ctx.home)}가 git 작업 트리({p(top)}) 안에 있다 — 실데이터(uploads·work)가 "
                 f"이력에 들어갈 수 있다. 루트 git 금지")
    else:
        out.ok("작업 루트에 git 없음")


def missing_exclude_lines(ctx):
    rc, out, _ = gtext(ctx.repo, "rev-parse", "--git-path", "info/exclude")
    if rc != 0:
        return list(EXCLUDE_LINES)
    path = out if os.path.isabs(out) else os.path.join(ctx.repo, out)
    have = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            have = {ln.strip() for ln in f}
    return [x for x in EXCLUDE_LINES if x not in have]


def check_exclude(ctx, out):
    miss = missing_exclude_lines(ctx)
    if not miss:
        out.ok(".git/info/exclude 줄 있음")
    elif ctx.rehearsal:
        out.warn(f".git/info/exclude에 없는 줄 {miss} (리허설 — 운영 clone은 sync가 넣는다)")
    else:
        out.fail(f".git/info/exclude에 없는 줄 {miss} — `pc_run.py sync`가 넣는다")


def check_autocrlf(ctx, out):
    rc, val, _ = gtext(ctx.repo, "config", "--get", "core.autocrlf")
    if val == "false":
        out.ok("core.autocrlf=false")
    else:
        out.fail(f"core.autocrlf={val or '(없음 → 시스템 값)'} — clone은 false여야 한다"
                 f"(index.html을 바이트 그대로). `git -C {p(ctx.repo)} config core.autocrlf false`")


def is_clean(ctx):
    rc, out, _ = gtext(ctx.repo, "status", "--porcelain", "--untracked-files=all")
    return rc == 0 and out == "", out


def fetch(ctx, out):
    rc, _, err = gtext(ctx.repo, "fetch", "--quiet", "--no-tags", "origin", timeout=180)
    if rc == 0:
        out.ok("fetch origin")
        return True
    out.fail(f"fetch origin 실패 — {err or rc}")
    return False


def rev(ctx, ref):
    rc, out, _ = gtext(ctx.repo, "rev-parse", "-q", "--verify", ref + "^{commit}")
    return out if rc == 0 else None


def ahead_behind(ctx):
    rc, out, _ = gtext(ctx.repo, "rev-list", "--left-right", "--count", "HEAD...refs/remotes/origin/main")
    if rc != 0:
        return None, None
    a, b = out.split()
    return int(a), int(b)


def diff_files(ctx, a, b):
    files = glines(ctx.repo, "diff", "--name-only", a, b)
    if files is None:  # 범위 검사가 조용히 빈 목록으로 통과하지 않게
        raise RuntimeError(f"git diff --name-only {a} {b} 실패")
    return files


def show_blob(ctx, spec):
    rc, data, _ = git(ctx.repo, "show", spec)
    return data if rc == 0 else None


def _csv_rows(data):
    """CSV 바이트 → 헤더를 뺀 데이터 줄 집합(BOM·CRLF 무시)."""
    lines = [ln.strip() for ln in data.decode("utf-8-sig", "replace").splitlines()]
    return {ln for ln in lines[1:] if ln}


def code_file_problems(ctx, files, csv_src):
    """코드 회차 파일 검사 — 공개 저장소로 나갈 파일이 코드 경로 밖이거나(실데이터 강제 add 등)
    fixture CSV에 실 CSV 행이 그대로 들어 있으면 문제 목록을 돌려준다."""
    pats = list(deploy.CODE_GLOBS) + PC_DOC_GLOBS
    bad, real = [], None
    for f in files:
        if f == "index.html":
            bad.append("index.html(배포 커밋은 deploy만 만든다)")
        elif not any(fnmatch.fnmatchcase(f, pat) for pat in pats):
            bad.append(f"{f}(코드 경로 밖)")
        elif f.lower().endswith(".csv"):
            blob = show_blob(ctx, f"HEAD:{f}")
            if blob is None:  # 지운 파일
                continue
            if real is None:
                with open(csv_src, "rb") as fh:
                    real = _csv_rows(fh.read())
            same = _csv_rows(blob) & real
            if same:
                bad.append(f"{f}(실 CSV와 같은 행 {len(same)}개)")
    return bad


def ls_remote_main(ctx):
    rc, out, err = gtext(ctx.repo, "ls-remote", "origin", "refs/heads/main", timeout=60)
    if rc != 0 or not out:
        return None
    return out.split()[0]


def run_key(name):
    m = RUN_NAME.match(name)
    return (m.group(1), int(m.group(2) or 1)) if m else None


def run_dirs(ctx):
    if not os.path.isdir(ctx.work_root):
        return []
    names = [n for n in os.listdir(ctx.work_root)
             if run_key(n) and os.path.isdir(os.path.join(ctx.work_root, n))]
    return sorted(names, key=run_key)


def resolve_run(ctx, arg):
    if os.sep not in arg and "/" not in arg:
        return os.path.join(ctx.work_root, arg)
    return os.path.abspath(arg)


def versions():
    from importlib import metadata
    v = {"python": platform.python_version()}
    for pkg in ("pandas", "numpy"):
        try:
            v[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            v[pkg] = None
    return v


def latest_date_of(data):
    """bundle/DATA의 최신 날짜 = months_included[-1]의 연도 + latest_update의 월·일.
    (공개판에는 연도가 붙은 최신 날짜 칸이 없다 — 탐색 기준선 이월 2)"""
    months = data.get("months_included") or []
    m = re.match(r"^(\d{1,2})월 (\d{1,2})일$", str(data.get("latest_update") or ""))
    if not months or not m:
        return None
    y, mm = (int(x) for x in months[-1].split("-")[:2])
    mo, d = int(m.group(1)), int(m.group(2))
    if mo < mm:  # 최신 월이 12월이고 이체 행만 새해에 있을 때
        y += 1
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def extract_data(html_bytes):
    """report/base HTML의 임베드 DATA. 못 읽으면 None."""
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            return deploy._extract_data(html_bytes.decode("utf-8"))
    except (SystemExit, UnicodeDecodeError, ValueError):
        return None


def classify_transfers(bundle):
    """매달 이체 확인 경고 → (멈춤, 정보).
    최신 달의 "이체 행 없음"만 멈추고 묻는다(웹 3단계). 지난 달 누락·"합계 ≠"는 정보 줄."""
    months = bundle.get("months_included") or [""]
    latest = months[-1]
    stops, infos = [], []
    for issue in bundle.get("diagnostics", {}).get("transfer_issues", []):
        if issue.split(" ", 1)[0] == latest and issue.endswith("이체 행 없음"):
            stops.append(issue)
        else:
            infos.append(issue)
    return stops, infos


def log_block(text, header, prefix):
    """pipeline.log의 [경고] 줄 / detect.log의 [확인 필요] 줄을 꺼낸다."""
    items, on = [], False
    for ln in text.splitlines():
        if header in ln:
            on = True
            continue
        if on:
            if ln.startswith(prefix):
                items.append(ln[len(prefix):])
            else:
                on = False
    return items


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


# ------------------------------------------------------------
# S0 check
# ------------------------------------------------------------

def cmd_check(ctx, args):
    mode_line(ctx, "check")
    out = Out()
    check_mode_repo(ctx, out)
    check_root_git(ctx, out)
    if gtext(ctx.repo, "rev-parse", "--is-inside-work-tree")[1] != "true":
        out.fail(f"{p(ctx.repo)}가 git clone이 아니다")
        return 1
    br = branch_of(ctx)
    if ctx.rehearsal:
        out.info(f"브랜치 {br or '분리된 HEAD'} (리허설)")
    elif br == "main":
        out.ok("브랜치 main")
    else:
        out.fail(f"브랜치가 main이 아니다({br or '분리된 HEAD'})")
    check_autocrlf(ctx, out)
    check_exclude(ctx, out)
    clean, st = is_clean(ctx)
    if clean:
        out.ok("작업 트리 깨끗")
    else:
        out.fail("작업 트리에 바뀐 파일이 있다:\n" + st)

    fetched = fetch(ctx, out)
    origin = rev(ctx, "refs/remotes/origin/main")
    head = rev(ctx, "HEAD")
    if fetched and origin:
        a, b = ahead_behind(ctx)
        if ctx.rehearsal:
            out.info(f"HEAD {head[:7]} · origin/main {origin[:7]} · 앞섬 {a} · 뒤 {b} (리허설 — 개발 커밋)")
        elif a == 0 and b == 0:
            out.ok(f"HEAD = origin/main ({head[:7]})")
        elif a == 0:
            out.fail(f"운영 clone이 origin/main보다 {b}커밋 뒤에만 있다 — 빨리 감기 가능: "
                     f"`pc_run.py sync` 뒤 check 다시")
        elif b == 0:
            files = diff_files(ctx, "refs/remotes/origin/main", "HEAD")
            commits = glines(ctx.repo, "log", "--oneline", "refs/remotes/origin/main..HEAD") or []
            if "index.html" in files:
                out.fail("push되지 않은 배포 커밋이 있다(안전망) — 커밋 목록을 사용자에게 보이고 묻는다. "
                         "기본: `git reset --hard origin/main` 뒤 build부터(코드 수정이었으면 다시 한다). "
                         "자동 push 금지:\n  " + "\n  ".join(commits))
            else:
                out.warn(f"코드 회차 커밋 {a}개(push 전): {', '.join(files)} — 코드 회차가 아니면 멈추고 묻는다:\n  "
                         + "\n  ".join(commits))
        else:
            out.fail(f"HEAD와 origin/main이 갈라졌다(앞섬 {a} · 뒤 {b}) — 멈추고 사용자에게 묻는다(reset·force 금지)")
    elif fetched:
        out.fail("origin/main이 없다")

    remote = ls_remote_main(ctx)
    out.info(f"원격 main: {remote or '(읽지 못함)'}")
    # 인증 확인. 원격과 같은 커밋을 dry-run으로 밀어 보는 것이라 무엇도 바꾸지 않는다.
    rc, _, err = gtext(ctx.repo, "push", "--dry-run", "--porcelain", "origin",
                       "refs/remotes/origin/main:refs/heads/main", timeout=90)
    if rc == 0:
        out.ok("push --dry-run 인증 통과(창 없이)")
    else:
        out.fail(f"push --dry-run 실패 — {err or rc}")

    v = versions()
    if ctx.rehearsal or same_path(sys.prefix, os.path.join(ctx.home, ".venv")):
        out.info(f"파이썬 {p(sys.executable)} {v['python']}")
    else:
        out.warn(f"파이썬이 {p(os.path.join(ctx.home, '.venv'))}가 아니다: {p(sys.executable)}")
    if not v["pandas"]:
        out.fail("pandas 없음 — 설치는 사용자에게 먼저 묻는다")
    diff = {k: v[k] for k in REFERENCE_VERSIONS if v.get(k) != REFERENCE_VERSIONS[k]}
    if diff:
        out.warn(f"pandas {v['pandas']} · numpy {v['numpy']} — 공개판과 같은 md5를 낸 판"
                 f"({REFERENCE_VERSIONS})과 다르다. 출력이 바뀔 수 있다")
    else:
        out.ok(f"pandas {v['pandas']} · numpy {v['numpy']} (기준 판과 같음)")
    node = shutil.which("node")
    if node:
        r = subprocess.run([node, "--version"], capture_output=True, text=True)
        out.info(f"node {r.stdout.strip()} (JS 문법 검사)")
    else:
        out.warn("node 없음 — 배포 전 검증의 JS 문법 검사를 건너뛴다")

    csvs = []
    if os.path.isdir(ctx.uploads):
        csvs = sorted(n for n in os.listdir(ctx.uploads) if n.lower().endswith(".csv"))
    if not csvs:
        out.warn(f"{p(ctx.uploads)}에 CSV가 없다")
    for n in csvs:
        st_ = os.stat(os.path.join(ctx.uploads, n))
        t = datetime.fromtimestamp(st_.st_mtime, KST).strftime("%Y-%m-%d %H:%M")
        out.info(f"uploads CSV: {n} · {st_.st_size:,} B · 수정 {t}")
    if len(csvs) > 1:
        out.warn(f"CSV가 {len(csvs)}개 — 어느 것인지 사용자에게 묻는다(이름·수정 시각·기간)")

    origin_index = show_blob(ctx, "refs/remotes/origin/main:index.html") if origin else None
    origin_md5 = md5_bytes(origin_index) if origin_index is not None else None
    try:
        status, body = http_get(PAGES_URL)
        live_md5 = md5_bytes(body) if status == 200 else None
    except Exception as e:  # noqa: BLE001 — 공개판 읽기는 [주의]만
        live_md5, status = None, f"{type(e).__name__}"
    if live_md5 and live_md5 == origin_md5:
        out.ok(f"공개판 md5 = origin/main index.html ({origin_md5[:8]})")
    elif live_md5:
        out.warn(f"공개판 md5 {live_md5[:8]} ≠ origin/main index.html {str(origin_md5)[:8]} "
                 f"— CDN 지연(최대 10분)일 수 있다")
    else:
        out.warn(f"공개판을 읽지 못했다({status})")

    runs = run_dirs(ctx)
    if runs:
        last = runs[-1]
        sp = os.path.join(ctx.work_root, last, "stamp.json")
        if not os.path.exists(sp):
            out.info(f"지난 회차 {last}: stamp 없음(build가 끝나지 않음)")
        else:
            st = read_json(sp)
            if origin_md5 and st.get("report_md5") == origin_md5:
                out.info(f"지난 회차 {last}: 배포됨(원격 index.html = 그 report {origin_md5[:8]})")
            else:
                out.info(f"지난 회차 {last}: 그 report({str(st.get('report_md5'))[:8]})는 원격에 없음")
            dj = os.path.join(ctx.work_root, last, "deploy.json")
            if os.path.exists(dj) and read_json(dj).get("status") in ("pushing", "unknown"):
                out.warn(f"지난 회차 {last}: push 결과를 모른 채 끝났다 — 위 원격 대조로 판정하고 다시 push하지 않는다")

    for local, canon in LOCAL_FILES:
        lp, cp = os.path.join(ctx.home, local), os.path.join(ctx.repo, canon)
        if not os.path.exists(cp):
            out.warn(f"저장소 정본 {canon} 없음")
        elif not os.path.exists(lp):
            out.warn(f"로컬 {local} 없음 — 정본 {canon}을 복사해 둔다"
                     + (" (settings.json은 사용자가 둔다)" if local.endswith("settings.json") else ""))
        elif md5_file(lp) != md5_file(cp):
            out.warn(f"로컬 {local} md5 ≠ 정본 {canon} — 정본에서 다시 복사")
        else:
            out.ok(f"로컬 {local} = 정본")

    if ctx.rehearsal and os.path.isdir(ctx.ops_clone) and not same_path(ctx.ops_clone, ctx.repo):
        rc, oh, _ = gtext(ctx.ops_clone, "--no-optional-locks", "rev-parse", "--short", "HEAD")
        rc2, ost, _ = gtext(ctx.ops_clone, "--no-optional-locks", "status", "--porcelain")
        rc3, ob, _ = gtext(ctx.ops_clone, "--no-optional-locks", "symbolic-ref", "--short", "-q", "HEAD")
        out.info(f"운영 clone(읽기만): {ob} {oh} · {'깨끗' if rc2 == 0 and not ost else '바뀐 파일 있음'}")

    print()
    print(f"S0 결과: {'FAIL ' + str(len(out.fails)) + '건' if out.fails else 'PASS'}")
    return 1 if out.fails else 0


# ------------------------------------------------------------
# sync — 운영 clone 빨리 감기
# ------------------------------------------------------------

def cmd_sync(ctx, args):
    mode_line(ctx, "sync")
    out = Out()
    if ctx.rehearsal:
        out.fail("리허설에서는 sync를 쓰지 않는다(운영 clone을 바꾸지 않는다)")
        return 1
    check_mode_repo(ctx, out)
    check_root_git(ctx, out)
    if out.fails:
        return 1
    if branch_of(ctx) != "main":
        out.fail("브랜치가 main이 아니다")
        return 1
    clean, st = is_clean(ctx)
    if not clean:
        out.fail("작업 트리에 바뀐 파일이 있다 — 멈춘다:\n" + st)
        return 1
    if not fetch(ctx, out):
        return 1
    a, b = ahead_behind(ctx)
    if a is None:
        out.fail("origin/main을 읽지 못했다")
        return 1
    if a == 0 and b == 0:
        out.ok("이미 origin/main과 같다")
    elif a == 0:
        old = rev(ctx, "HEAD")
        rc, _, err = gtext(ctx.repo, "merge", "--ff-only", "--quiet", "refs/remotes/origin/main")
        if rc != 0:
            out.fail(f"빨리 감기 실패 — {err}")
            return 1
        new = rev(ctx, "HEAD")
        out.ok(f"빨리 감기 {old[:7]} → {new[:7]} ({b}커밋): " + ", ".join(diff_files(ctx, old, new)))
    else:
        out.fail(f"origin/main보다 앞서 있거나 갈라졌다(앞섬 {a} · 뒤 {b}) — 빨리 감기만 하므로 멈춘다. "
                 f"커밋 목록을 사용자에게 보이고 묻는다")
        return 1
    miss = missing_exclude_lines(ctx)
    if miss:
        rc, path, _ = gtext(ctx.repo, "rev-parse", "--git-path", "info/exclude")
        path = path if os.path.isabs(path) else os.path.join(ctx.repo, path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        prefix = ""
        if os.path.exists(path) and os.path.getsize(path):
            with open(path, "rb") as f:
                prefix = "" if f.read().endswith(b"\n") else "\n"
        with open(path, "a", encoding="utf-8", newline="") as f:
            f.write(prefix + "\n".join(miss) + "\n")
        out.ok(f".git/info/exclude에 줄 추가: {miss}")
    else:
        out.ok(".git/info/exclude 줄 있음")
    return 0


# ------------------------------------------------------------
# ② build
# ------------------------------------------------------------

def _subrun(ctx, run, argv, log_name, extra_env=None):
    env = dict(os.environ)
    # PYTHONHASHSEED 고정: pipeline.py의 전월 대비 증감은 set 순회라 |증감| 동률이면 순서가 프로세스마다
    # 바뀐다(실 CSV는 동률이 없어 md5가 늘 같았다 — 탐색 실측). 같은 입력 → 같은 바이트가 되게 한다.
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0")
    env.pop("HOUSEHOLD_OUT", None)
    env.update(extra_env or {})
    r = subprocess.run([sys.executable, *argv], cwd=run, capture_output=True, env=env, timeout=900)
    text = (r.stdout + (b"\n" + r.stderr if r.stderr else b"")).decode("utf-8", "replace")
    with open(os.path.join(run, log_name), "w", encoding="utf-8", newline="") as f:
        f.write(text)
    print(text.rstrip())
    return r.returncode, text


def new_run_dir(ctx):
    os.makedirs(ctx.work_root, exist_ok=True)
    base = now_kst().strftime("%Y%m%d-%H%M%S")
    n = 1
    existing = run_dirs(ctx)
    if existing and run_key(existing[-1]) >= (base, 1):  # 시계가 뒤로 가도 새 회차가 가장 최근이 되게
        base, n = run_key(existing[-1])
        n += 1
    while True:
        name = base if n == 1 else f"{base}-{n}"
        path = os.path.join(ctx.work_root, name)
        try:
            os.mkdir(path)
            return path
        except FileExistsError:
            n += 1


def cmd_build(ctx, args):
    mode_line(ctx, "build")
    out = Out()
    check_mode_repo(ctx, out)
    check_root_git(ctx, out)
    if out.fails:
        return 1
    br = branch_of(ctx)
    if not ctx.rehearsal and br != "main":
        out.fail(f"브랜치가 main이 아니다({br or '분리된 HEAD'}) — build하지 않는다")
    check_autocrlf(ctx, out)
    check_exclude(ctx, out)
    clean, st = is_clean(ctx)
    if not clean:
        out.fail("작업 트리가 깨끗하지 않다 — 코드를 고쳤으면 먼저 커밋한다(코드 회차):\n" + st)
    csv_src = os.path.abspath(args.csv)
    if not os.path.isfile(csv_src):
        out.fail(f"CSV가 없다: {p(csv_src)}")
    elif os.path.normcase(os.path.realpath(csv_src)).startswith(
            os.path.normcase(os.path.realpath(ctx.repo)) + os.sep):
        out.fail("CSV가 clone 안에 있다 — 실데이터는 clone 밖(uploads)에만 둔다")
    if out.fails:
        return 1
    if not fetch(ctx, out):
        return 1
    head, origin = rev(ctx, "HEAD"), rev(ctx, "refs/remotes/origin/main")
    a, b = ahead_behind(ctx)
    if origin is None or a is None:
        out.fail("origin/main을 읽지 못했다")
        return 1
    code_files = diff_files(ctx, "refs/remotes/origin/main", "HEAD") if a else []
    if ctx.rehearsal:
        out.info(f"리허설: HEAD {head[:7]} · origin/main {origin[:7]} · 앞섬 {a} · 뒤 {b}")
    elif b:
        out.fail(f"origin/main보다 {b}커밋 뒤에 있다 — `pc_run.py sync` 뒤 다시(옛 코드로 빌드하지 않는다)")
    elif a and "index.html" in code_files:
        out.fail("origin/main보다 앞선 커밋에 index.html이 있다(push 안 된 배포 커밋) — check의 안전망 절차")
    elif a:
        out.info(f"코드 회차: 앞선 커밋 {a}개 · 코드 파일 {code_files} — 승인 질문에 싣고 index.html과 함께 1 push")
    else:
        out.ok(f"HEAD = origin/main ({head[:7]})")
    if code_files and os.path.isfile(csv_src):
        bad = code_file_problems(ctx, code_files, csv_src)
        if bad:
            out.fail(f"앞선 커밋에 공개 저장소로 나가면 안 되는 파일: {bad}")
    base_html = show_blob(ctx, f"{head}:index.html")
    if base_html is None:
        out.fail(f"{head[:7]}에 index.html이 없다 — 기준판을 만들 수 없다")
    if out.fails:
        return 1

    csvs = []
    if os.path.isdir(ctx.uploads):
        csvs = sorted(n for n in os.listdir(ctx.uploads) if n.lower().endswith(".csv"))
    if len(csvs) > 1:
        out.warn(f"uploads에 CSV {len(csvs)}개 {csvs} — 사용자가 고른 것이 {os.path.basename(csv_src)}인지 "
                 f"승인 질문에 싣는다")

    run = new_run_dir(ctx)
    rid = os.path.basename(run)
    print(f"회차 폴더: {p(run)}")
    with open(csv_src, "rb") as f:
        csv_bytes = f.read()
    csv_name = os.path.basename(csv_src)
    with open(os.path.join(run, csv_name), "wb") as f:
        f.write(csv_bytes)
    csv_src_md5 = md5_bytes(csv_bytes)
    if md5_file(os.path.join(run, csv_name)) != csv_src_md5:
        out.fail("CSV 사본 md5가 원본과 다르다")
        return 1
    with open(os.path.join(run, "base.html"), "wb") as f:
        f.write(base_html)

    print("── pipeline.py ──")
    rc, plog = _subrun(ctx, run, [os.path.join(ctx.repo, "pipeline.py"), csv_name], "pipeline.log")
    if rc != 0 or not os.path.exists(os.path.join(run, "data_bundle.json")):
        out.fail(f"pipeline rc={rc} — build하지 않는다(옛 bundle을 쓰지 않는다). 회차 {rid}는 stamp 없이 남는다")
        return 1
    print("── build.py ──")
    report = os.path.join(run, "report.html")
    rc, _ = _subrun(ctx, run, [os.path.join(ctx.repo, "build.py")], "build.log",
                    {"HOUSEHOLD_OUT": report})
    if rc != 0 or not os.path.exists(report):
        out.fail(f"build rc={rc} — 회차 {rid}는 stamp 없이 남는다")
        return 1
    print("── detect_ended.py ──")
    drc, _ = _subrun(ctx, run, [os.path.join(ctx.repo, "scripts", "detect_ended.py"), csv_name], "detect.log")
    if drc != 0:
        out.warn(f"detect_ended rc={drc} — 종료 추정을 못 했다(리포트에는 영향 없음). review에서 대화로 알린다")

    # 빌드하는 동안 코드·트리가 바뀌지 않았는지
    if rev(ctx, "HEAD") != head or not is_clean(ctx)[0]:
        out.fail("빌드하는 동안 HEAD나 작업 트리가 바뀌었다 — stamp를 쓰지 않는다")
        return 1
    m = re.search(r"^\s+(\d+)건, (\d{4}-\d{2}-\d{2}) ~ (\d{4}-\d{2}-\d{2})", plog, re.M)
    v = versions()
    stamp = {
        "version": 1,
        "mode": ctx.mode,
        "run": rid,
        "created": now_kst().isoformat(timespec="seconds"),
        "repo": p(ctx.repo),
        "branch": br,
        "head": head,
        "origin_main": origin,
        "ahead": a,
        "code_files": code_files,
        "csv_src": p(csv_src),
        "csv_src_md5": csv_src_md5,
        "csv_src_mtime": datetime.fromtimestamp(os.stat(csv_src).st_mtime, KST).isoformat(timespec="seconds"),
        "csv_copy": csv_name,
        "csv_copy_md5": md5_file(os.path.join(run, csv_name)),
        "csv_rows": int(m.group(1)) if m else None,
        "csv_period": [m.group(2), m.group(3)] if m else None,
        "uploads_csv_count": len(csvs),
        "versions": v,
        "bundle_md5": md5_file(os.path.join(run, "data_bundle.json")),
        "report_md5": md5_file(report),
        "base_md5": md5_bytes(base_html),
        "detect_rc": drc,
    }
    write_json_atomic(os.path.join(run, "stamp.json"), stamp)
    print()
    print(f"[PASS] build 끝 · 회차 {rid} · report md5 {stamp['report_md5']} · 기준판 md5 {stamp['base_md5'][:8]}")
    print(f"다음: review --run {rid}")
    return 0


# ------------------------------------------------------------
# 회차 읽기 · 무결성 · ③ 평가 (review와 deploy가 같이 쓴다)
# ------------------------------------------------------------

def load_run(ctx, arg, out):
    run = resolve_run(ctx, arg)
    rid = os.path.basename(run)
    if not same_path(os.path.dirname(run), ctx.work_root) or not run_key(rid):
        out.fail(f"회차 폴더는 {p(ctx.work_root)} 바로 아래여야 한다: {p(run)}")
        return None, None
    sp = os.path.join(run, "stamp.json")
    if not os.path.exists(sp):
        out.fail(f"회차 {rid}에 stamp.json이 없다(build가 끝나지 않은 회차) — 쓸 수 없다")
        return None, None
    stamp = read_json(sp)
    if stamp.get("version") != 1 or stamp.get("run") != rid:
        out.fail("stamp.json 형식이 맞지 않다")
        return None, None
    if stamp.get("mode") != ctx.mode:
        out.fail(f"이 회차는 {stamp.get('mode')} 모드에서 만들었다 — 지금은 {ctx.mode}")
        return None, None
    if not same_path(stamp.get("repo", ""), ctx.repo):
        out.fail(f"이 회차는 다른 clone({stamp.get('repo')})에서 만들었다")
        return None, None
    runs = run_dirs(ctx)
    if not runs or runs[-1] != rid:
        out.fail(f"가장 최근 회차가 아니다(최근: {runs[-1] if runs else '-'}) — 가장 최근 회차만 확인·배포한다")
        return None, None
    return run, stamp


def check_integrity(ctx, run, stamp, out):
    """회차 폴더 파일과 원본 CSV가 build 때 그대로인지(K1 · K2)."""
    bad = []
    pairs = [("report.html", "report_md5"), ("data_bundle.json", "bundle_md5"),
             ("base.html", "base_md5"), (stamp.get("csv_copy", ""), "csv_copy_md5")]
    for name, key in pairs:
        fp = os.path.join(run, name)
        if not os.path.isfile(fp) or md5_file(fp) != stamp.get(key):
            bad.append(name)
    src = stamp.get("csv_src", "")
    if not os.path.isfile(src):
        bad.append(f"원본 CSV 없음({src})")
    elif md5_file(src) != stamp.get("csv_src_md5"):
        bad.append(f"원본 CSV가 build 뒤 바뀜({src}) — 새 CSV로 build부터")
    blob = show_blob(ctx, f"{stamp.get('head')}:index.html")
    if blob is None or md5_bytes(blob) != stamp.get("base_md5"):
        bad.append("base.html ≠ git show <stamp.head>:index.html")
    if bad:
        out.fail("회차 무결성: " + " · ".join(bad))
    else:
        out.ok("회차 무결성(report·bundle·base·CSV 사본·원본 CSV md5 = stamp, base = git show stamp.head)")


def check_local_head(ctx, stamp, out):
    head = rev(ctx, "HEAD")
    if head != stamp.get("head"):
        out.fail(f"로컬 HEAD {str(head)[:7]} ≠ build 때 {stamp.get('head', '')[:7]} — 빌드 뒤 코드가 바뀌었다. build부터")
    else:
        out.ok(f"로컬 HEAD = build 때 ({head[:7]})")
    clean, st = is_clean(ctx)
    if clean:
        out.ok("작업 트리 깨끗")
    else:
        out.fail("작업 트리에 바뀐 파일이 있다 — 커밋되지 않은 수정:\n" + st)


class Eval:
    def __init__(self):
        self.stops = []       # (종류, 사용자에게 할 질문)
        self.jumps = []
        self.notes = []       # 승인 질문에 싣는 줄
        self.same_as_base = False
        self.warn_count = 0


def evaluate(ctx, run, stamp, out, acks, verbose=True):
    """③ (a)~(f). FAIL은 out에, 멈춤 질문은 ev.stops에."""
    ev = Eval()
    say = print if verbose else (lambda *a, **k: None)
    bundle = read_json(os.path.join(run, "data_bundle.json"))
    with open(os.path.join(run, "report.html"), "rb") as f:
        report_bytes = f.read()
    with open(os.path.join(run, "base.html"), "rb") as f:
        base_bytes = f.read()
    plog = read_text(os.path.join(run, "pipeline.log"))
    dlog = read_text(os.path.join(run, "detect.log"))
    latest_month = (bundle.get("months_included") or ["?"])[-1]
    ev.same_as_base = md5_bytes(report_bytes) == md5_bytes(base_bytes)

    # (a) 이체
    say("── (a) 매달 이체 ──")
    stops, infos = classify_transfers(bundle)
    for s in stops:
        dst = s.split("→", 1)[1].split(" ", 1)[0] if "→" in s else "?"
        if acks.get("transfer"):
            out.info(f"{s} — 사용자가 그대로 진행하기로 답함(--ack-transfer)")
            ev.notes.append(f"이체 확인: {s} → 그대로 진행(사용자 답)")
        else:
            print(f"[멈춤] {s}")
            ev.stops.append(("transfer",
                             f"이번 CSV에 {latest_month} [{dst}] 이체 건이 안 보여요. 빠뜨리신 걸까요? "
                             f"빠뜨리셨다면 그 줄(날짜 {latest_month}-01 · 구분 이체 · 소분류 {dst} · 결제수단 생활비 · "
                             f"금액은 pipeline.py EXPECTED_MONTHLY_TRANSFERS 값)을 추가한 CSV를 다시 주시고, "
                             f"그대로 두실 거면 '그대로 진행'이라고 답해 주세요."))
    for s in infos:
        out.info(f"매달 이체 확인(지난 달·합계 — 확인만): {s}")
    tx = [t for t in bundle.get("monthly_tx", {}).get(latest_month, []) if t.get("구분") == "이체"]
    if tx:
        agg = {}
        for t in tx:
            agg[t.get("소분류")] = agg.get(t.get("소분류"), 0) + t.get("금액", 0)
        out.info(f"{latest_month} 이체 행: " + " · ".join(f"{k} {v:,}원" for k, v in agg.items()))
    elif not stops:
        out.info(f"{latest_month} 이체 행 없음(검사 시작 월 전이면 정상)")

    # (b) [경고] 전부
    warns = log_block(plog, "[경고]", "        - ")
    ev.warn_count = len(warns)
    say(f"── (b) pipeline [경고] {len(warns)}건 — 전부 대화로 전달(화면에서 빠지는 감춘 계좌 경고 포함) ──")
    for w in warns:
        say(f"  - {w}")

    # (c) 종료 추정
    ended = log_block(dlog, "[확인 필요]", "  - ")
    say(f"── (c) 종료 추정 [확인 필요] {len(ended)}건 ──")
    for e in ended:
        say(f"  - {e}")
    if stamp.get("detect_rc"):
        out.warn(f"detect_ended rc={stamp.get('detect_rc')} — 종료 추정을 못 했다: "
                 + (dlog.strip().splitlines()[-1] if dlog.strip() else ""))
    if ended:
        ev.notes.append(f"종료 추정 [확인 필요] {len(ended)}건 — 끝난 항목이면 말씀해 주세요(코드 회차로 따로 한다)")

    # (d) 옛 CSV
    say("── (d) CSV 날짜 대 공개판(기준판) ──")
    base_data = extract_data(base_bytes)
    new_latest = latest_date_of(bundle)
    base_latest = latest_date_of(base_data) if base_data else None
    if new_latest is None:
        out.fail("새 bundle의 최신 날짜를 읽지 못했다")
    elif base_latest is None:
        out.fail("기준판의 최신 날짜를 읽지 못했다(기준판 DATA 없음)")
    elif new_latest < base_latest:
        print(f"[멈춤] CSV 최신 {new_latest} < 공개판 최신 {base_latest} — 옛 CSV다. 배포하지 않는다")
        ev.stops.append(("old_csv", f"고르신 CSV({stamp.get('csv_copy')})는 {new_latest}까지인데 공개판은 "
                                    f"{base_latest}까지예요. 더 최신 CSV를 주세요."))
    elif new_latest == base_latest:
        out.info(f"CSV 최신 {new_latest} = 공개판 최신 — 같은 날짜까지다(같은 판이거나 정정판)")
        if not ev.same_as_base:
            ev.notes.append(f"공개판과 같은 날짜({new_latest})까지의 CSV입니다 — 정정판이 맞는지 확인해 주세요")
    else:
        out.ok(f"CSV 최신 {new_latest} > 공개판 최신 {base_latest}")

    # (e) 배포 전 검증 — 기준판 = git show <stamp.head>:index.html
    say(f"── (e) 배포 전 검증 (기준판 = git show {stamp.get('head', '')[:7]}:index.html) ──")
    base_path = os.path.join(run, "base.html")
    if not base_data or not (base_data.get("accounts") or {}).get("list"):
        out.fail("기준판이 비었거나 DATA·계좌가 없다 — 잔고 급변 검사를 할 수 없다")
    else:
        buf = io.StringIO()
        report_path = os.path.join(run, "report.html")
        try:
            with redirect_stdout(buf):
                deploy.validate_html(report_path, ack_balance=True, prev_html_path=base_path)
                data = deploy._extract_data(report_bytes.decode("utf-8"))
                ev.jumps = deploy.check_balance_jump(data, ack=True, prev_html_path=base_path)
            text = buf.getvalue()
            for ln in text.splitlines():
                if ln.strip().startswith("검증:"):
                    say("  " + ln.strip())
            if any("잔고 급변 검사" in ln and "건너뜀" in ln for ln in text.splitlines()):
                out.fail("잔고 급변 검사를 건너뛰었다(기준판 없음) — 배포하지 않는다")
            elif "JS 검사 건너뜀" in text:
                out.warn("node가 없어 JS 문법 검사를 건너뛰었다")
            if ev.jumps:
                out.warn(f"[확인 필요] 기준판 대비 잔고가 {deploy.BALANCE_JUMP_THRESHOLD:,}원 넘게 바뀐 계좌 — 승인 질문에 싣는다")
                for name, prev_b, new_b, d in ev.jumps:
                    line = f"{name}: {prev_b:,}원 → {new_b:,}원 ({'+' if d > 0 else ''}{d:,}원)"
                    say("    - " + line)
                    ev.notes.append("잔고 급변 [확인 필요] " + line)
            else:
                out.ok(f"잔고 급변 없음(계좌별 {deploy.BALANCE_JUMP_THRESHOLD:,}원 이내)")
        except SystemExit as e:
            for ln in buf.getvalue().splitlines():
                say("  " + ln)
            out.fail(f"배포 전 검증 실패: {e}")

    # (f) 기준판 대비 변동
    say("── (f) 기준판 대비 ──")
    if base_data:
        bk, nk = base_data.get("kpi", {}), bundle.get("kpi", {})
        for key, label in (("total_income", "총수입"), ("total_expense", "총지출"), ("net", "순잉여")):
            b_, n_ = bk.get(key, 0), nk.get(key, 0)
            say(f"  {label} {b_:,} → {n_:,} ({'+' if n_ - b_ > 0 else ''}{n_ - b_:,})")
        bacc = {a["name"]: a.get("current_balance") for a in base_data.get("accounts", {}).get("list", [])}
        for a in bundle.get("accounts", {}).get("list", []):
            b_ = bacc.get(a["name"])
            n_ = a.get("current_balance")
            tag = " (화면에서 감춤)" if a.get("hidden") else ""
            if b_ is None:
                say(f"  {a['name']}{tag}: (기준판에 없음) → {n_:,}")
            else:
                say(f"  {a['name']}{tag}: {b_:,} → {n_:,} ({'+' if n_ - b_ > 0 else ''}{n_ - b_:,})")
        say(f"  기간 {(base_data.get('months_included') or ['?'])[0]} ~ {base_latest} → "
            f"{(bundle.get('months_included') or ['?'])[0]} ~ {new_latest}")
    if ev.same_as_base:
        out.info("report = 기준판(바이트까지 같음) — 배포해도 index.html 커밋 없음(같은 판)")
    return ev


def deploy_code_files(ctx, stamp):
    """함께 push될 코드 파일. 리허설의 앞선 커밋은 개발 브랜치라 배포 대상이 아니다(병합으로 올린다)."""
    return [] if ctx.rehearsal else list(stamp.get("code_files", []))


def approval_block(ctx, stamp, ev):
    code = stamp["report_md5"][:8]
    files = ([] if ev.same_as_base else [f"index.html ({stamp['base_md5'][:8]} → {code})"]) + \
        [f"{f} (코드 회차, 이미 로컬 커밋됨)" for f in deploy_code_files(ctx, stamp)]
    period = stamp.get("csv_period") or ["?", "?"]
    lines = [
        f"배포할 판: 회차 {stamp['run']} · CSV {stamp['csv_copy']} (md5 {stamp['csv_src_md5'][:8]} · "
        f"수정 {stamp.get('csv_src_mtime', '?')[:16].replace('T', ' ')} · {stamp.get('csv_rows')}행 · "
        f"{period[0]} ~ {period[1]})",
    ]
    if (stamp.get("uploads_csv_count") or 0) > 1:
        lines.append(f"uploads의 CSV {stamp['uploads_csv_count']}개 중 위 파일을 골랐습니다")
    lines.append("커밋될 파일: " + (", ".join(files) if files else "없음(같은 판 — 커밋·push 0)"))
    if ctx.rehearsal and stamp.get("code_files"):
        lines.append(f"[리허설] 개발 브랜치 커밋의 파일 {len(stamp['code_files'])}개는 배포 대상이 아니다"
                     f"(검증 뒤 병합으로 올린다)")
    lines.append(f"[경고] {ev.warn_count}건은 위에서 말씀드린 그대로입니다")
    lines += ev.notes
    if ev.same_as_base:
        lines.append("공개판과 바이트까지 같은 판입니다 — 배포해도 바뀌는 것이 없습니다")
    if ctx.rehearsal:
        lines.append(f"[리허설] 배포 질문은 하지 않는다(운영이면 여기서 \"배포 {code}\" 답을 받는다)")
    else:
        lines.append(f"배포하려면 \"배포 {code}\"라고 답해 주세요. 다른 답이면 배포하지 않습니다.")
    return code, lines


def cmd_review(ctx, args):
    mode_line(ctx, "review")
    out = Out()
    check_mode_repo(ctx, out)
    check_root_git(ctx, out)
    run, stamp = load_run(ctx, args.run, out)
    if not run:
        return 1
    period = stamp.get("csv_period") or ["?", "?"]
    print(f"회차 {stamp['run']} · CSV {stamp['csv_copy']} (md5 {stamp['csv_src_md5'][:8]} · "
          f"{stamp.get('csv_rows')}행 · {period[0]} ~ {period[1]}) · 코드 {stamp['head'][:7]}")
    check_integrity(ctx, run, stamp, out)
    check_local_head(ctx, stamp, out)
    if out.fails:
        print("\n결과: FAIL — 승인 코드를 내지 않는다")
        return 1
    ev = evaluate(ctx, run, stamp, out, {"transfer": args.ack_transfer})
    print("── 결과 ──")
    if out.fails:
        print(f"FAIL {len(out.fails)}건 — 승인 코드를 내지 않는다")
        return 1
    if ev.stops:
        print(f"[멈춤] 질문 {len(ev.stops)}개 — 사용자 답을 받기 전에는 승인 코드를 내지 않는다.")
        for i, (_, q) in enumerate(ev.stops, 1):
            print(f"  질문 {i}: {q}")
        print("  답이 CSV나 코드를 바꾸면: 새 CSV로 build부터(새 회차 폴더 · 새 승인 코드).")
        if any(k == "transfer" for k, _ in ev.stops):
            print(f"  이체 질문에 '그대로 진행'이면: review --run {stamp['run']} --ack-transfer")
        return 2
    code, lines = approval_block(ctx, stamp, ev)
    print(f"승인 코드: {code}")
    print("──── 승인 질문(이 블록을 그대로 사용자에게) ────")
    for ln in lines:
        print(ln)
    print("────────────────────────────────────────")
    ack = " --ack-transfer" if args.ack_transfer else ""
    if ctx.rehearsal:
        print(f"[리허설] 여기서는 deploy --run {stamp['run']}{ack} (dry-run)까지만 한다. --confirm은 막혀 있다.")
    else:
        print(f"다음: 사용자 답이 \"배포 {code}\"이면 deploy --run {stamp['run']}{ack} --confirm {code}")
    return 0


# ------------------------------------------------------------
# ④ 배포 전 읽기 · ⑤ 배포
# ------------------------------------------------------------

def preflight(ctx, run, stamp, out, acks, code):
    """④ — 쓰기 전에 다시 읽어 모두 대조한다. deploy --confirm은 이것을 같은 프로세스에서 돈다."""
    check_mode_repo(ctx, out)
    check_root_git(ctx, out)
    br = branch_of(ctx)
    if not ctx.rehearsal and br != "main":
        out.fail(f"브랜치가 main이 아니다({br})")
    check_autocrlf(ctx, out)
    if not fetch(ctx, out):
        return None
    check_local_head(ctx, stamp, out)
    origin = rev(ctx, "refs/remotes/origin/main")
    if origin != stamp.get("origin_main"):
        out.fail(f"origin/main {str(origin)[:7]} ≠ build 때 {stamp.get('origin_main', '')[:7]} — 원격이 바뀌었다"
                 f"(웹 배포 등). sync 뒤 build부터")
    else:
        out.ok(f"origin/main = build 때 ({origin[:7]})")
    files = diff_files(ctx, "refs/remotes/origin/main", "HEAD")
    allowed = set(stamp.get("code_files", []))
    if "index.html" in files or not set(files) <= allowed:
        out.fail(f"origin/main..HEAD 파일 {files} ⊄ 승인된 코드 파일 {sorted(allowed)}")
    elif files:
        out.info(f"코드 회차 파일(승인 질문에 실림): {files}")
    bad = code_file_problems(ctx, files, stamp["csv_src"]) if files and os.path.isfile(stamp["csv_src"]) else []
    if bad:
        out.fail(f"공개 저장소로 나가면 안 되는 파일: {bad}")
    check_integrity(ctx, run, stamp, out)
    ev = evaluate(ctx, run, stamp, out, acks, verbose=False)
    if ev.stops:
        out.fail("③의 멈춤 질문이 남은 회차다: " + " / ".join(k for k, _ in ev.stops)
                 + " — review에서 답을 받은 뒤 같은 --ack-*를 붙인다")
    else:
        out.ok("③ 멈춤 질문 없음")
    if code is not None:
        want = stamp["report_md5"][:8]
        got = code.strip().lower()
        if got != want or md5_file(os.path.join(run, "report.html"))[:8] != got:
            out.fail(f"승인 코드 {code!r} ≠ 이 회차 report md5 앞 8자 — 사용자가 승인한 판이 아니다")
        else:
            out.ok(f"승인 코드 = 이 회차 report md5 ({want})")
    return ev


def _restore_index(ctx):
    git(ctx.repo, "reset", "-q", "--", "index.html")
    git(ctx.repo, "checkout", "-q", "--", "index.html")


def cmd_deploy(ctx, args):
    mode_line(ctx, "deploy" + (" --confirm" if args.confirm is not None else " (dry-run)"))
    out = Out()
    if ctx.rehearsal and args.confirm is not None:
        out.fail("[리허설] deploy --confirm은 막혀 있다 — 리허설은 dry-run까지만")
        return 1
    run, stamp = load_run(ctx, args.run, out)
    if not run:
        return 1
    print("── ④ 배포 전 읽기 ──")
    ev = preflight(ctx, run, stamp, out, {"transfer": args.ack_transfer}, args.confirm)
    remote = ls_remote_main(ctx)
    out.info(f"원격 main: {remote or '(읽지 못함)'}")
    if ev is None or out.fails:
        print(f"\n결과: FAIL {len(out.fails)}건 — 쓰기 0")
        return 1
    head_index = show_blob(ctx, "HEAD:index.html")
    same = head_index is not None and md5_bytes(head_index) == stamp["report_md5"]
    code_files = deploy_code_files(ctx, stamp)
    if ctx.rehearsal and stamp.get("code_files"):
        out.info(f"[리허설] 개발 브랜치 커밋의 파일 {len(stamp['code_files'])}개는 배포 대상이 아니다(병합으로 올린다)")
    if same:
        print("커밋될 파일: " + ("없음(같은 판)" if not code_files else
                                f"index.html 없음(같은 판) · 코드 {code_files}(이미 로컬 커밋됨)"))
    else:
        print(f"커밋될 파일: index.html ({md5_bytes(head_index or b'')[:8]} → {stamp['report_md5'][:8]})"
              + (f" · 코드 {code_files}(이미 로컬 커밋됨)" if code_files else ""))

    if args.confirm is None:
        print(f"\ndry-run — 쓰기 0(커밋 0 · push 0). 배포는 사용자 답 \"배포 {stamp['report_md5'][:8]}\" 뒤 --confirm.")
        return 0

    # ⑤ 배포 — 여기서부터 쓰기. 결과는 deploy.json에 남긴다(stamp는 건드리지 않는다).
    dj_path = os.path.join(run, "deploy.json")
    if os.path.exists(dj_path):
        out.fail(f"이 회차는 이미 deploy를 했다({read_json(dj_path).get('status')}) — 다시 push하지 않는다. check로 원격 확인")
        return 1
    record = {"run": stamp["run"], "base_head": stamp["head"], "report_md5": stamp["report_md5"]}
    if same and not code_files:
        record.update(status="same", commit=stamp["head"], pushed_at=_time())
        write_json_atomic(dj_path, record)
        print("[PASS] 같은 판 — 커밋 0 · push 0")
        return 0
    if not same:
        shutil.copyfile(os.path.join(run, "report.html"), os.path.join(ctx.repo, "index.html"))
        rc, _, err = gtext(ctx.repo, "add", "--", "index.html")
        staged = glines(ctx.repo, "diff", "--cached", "--name-only")
        if rc != 0 or staged != ["index.html"]:
            _restore_index(ctx)
            out.fail(f"스테이징이 index.html 하나가 아니다: {staged} {err}")
            return 1
        latest = latest_date_of(read_json(os.path.join(run, "data_bundle.json")))
        msg = f"리포트 갱신 ({now_kst():%Y-%m-%d}, CSV ~{latest:%m-%d})"
        rc, _, err = gtext(ctx.repo, "-c", f"user.name={COMMIT_NAME}", "-c", f"user.email={COMMIT_EMAIL}",
                           "commit", "-q", "-m", msg)
        if rc != 0:
            _restore_index(ctx)
            out.fail(f"커밋 실패 — {err}")
            return 1
        print(f"[PASS] 커밋: {msg}")
    new_head = rev(ctx, "HEAD")
    # push 직전 범위 검사
    files = diff_files(ctx, "refs/remotes/origin/main", "HEAD")
    allowed = set(code_files) | ({"index.html"} if not same else set())
    n_commits = int(gtext(ctx.repo, "rev-list", "--count", "refs/remotes/origin/main..HEAD")[1] or -1)
    expect_commits = stamp.get("ahead", 0) + (0 if same else 1)
    if not set(files) <= allowed or n_commits != expect_commits:
        out.fail(f"push 범위가 승인과 다르다 — 파일 {files} ⊄ {sorted(allowed)} 또는 커밋 {n_commits} ≠ {expect_commits}. "
                 f"push하지 않는다(로컬 커밋은 남는다 — check의 안전망 절차)")
        return 1
    print(f"[PASS] push 범위 = {files} · 커밋 {n_commits}개")
    record.update(status="pushing", commit=new_head, files=files, started_at=_time())
    write_json_atomic(dj_path, record)
    rc, pout, err = gtext(ctx.repo, "push", "--porcelain", "origin", "HEAD:refs/heads/main", timeout=180)
    if rc is None:
        record["status"] = "unknown"
        write_json_atomic(dj_path, record)
        out.fail("push 결과를 모른다(시간 초과) — 다시 push하지 않는다. check로 원격을 먼저 확인")
        return 1
    if rc != 0:
        record["status"] = "rejected"
        write_json_atomic(dj_path, record)
        print(pout)
        out.fail(f"push 거절 — {err}. 다시 push하지 않는다(force·재시도 금지). check로 원격 확인")
        return 1
    remote = ls_remote_main(ctx)
    record.update(status="pushed", pushed_at=_time(), remote_main_after=remote)
    write_json_atomic(dj_path, record)
    if remote == new_head:
        print(f"[PASS] push 1회 · 원격 main = {new_head}")
    else:
        out.warn(f"push는 됐는데 원격 main {remote} ≠ {new_head} — live에서 판정")
    print(f"다음: live --run {stamp['run']} (백그라운드로, 최대 약 {PUBLIC_DEADLINE // 60}분)")
    return 0


# ------------------------------------------------------------
# ⑥ live · ⑦ record
# ------------------------------------------------------------

def pages_run(sha):
    """그 커밋의 'pages build and deployment' 실행 → (status, conclusion) 또는 None."""
    status, body = http_get(ACTIONS_API.format(repo=deploy.REPO, sha=sha), accept="application/vnd.github+json")
    if status != 200:
        return None
    for r in json.loads(body.decode("utf-8")).get("workflow_runs", []):
        if r.get("name") == PAGES_WORKFLOW and r.get("head_sha") == sha:
            return r.get("status"), r.get("conclusion")
    return None


def public_md5():
    try:
        status, body = http_get(PAGES_URL)
        return md5_bytes(body) if status == 200 else None
    except Exception:  # noqa: BLE001
        return None


def cmd_live(ctx, args):
    mode_line(ctx, "live")
    out = Out()
    run, stamp = load_run(ctx, args.run, out)
    if not run:
        return 1
    dj_path = os.path.join(run, "deploy.json")
    if not os.path.exists(dj_path):
        out.fail("deploy.json 없음 — deploy --confirm 뒤에 부른다")
        return 1
    dj = read_json(dj_path)
    result = {"checked_at": now_kst().isoformat(timespec="seconds"), "actions": None, "public_md5": None}

    def finish(code, msg):
        result["exit"] = code
        result["message"] = msg
        write_json_atomic(os.path.join(run, "live.json"), result)
        print(("[PASS] " if code == 0 else "[FAIL] " if code == 1 else "[주의] ") + msg)
        return code

    if dj.get("status") not in ("pushed", "same"):
        return finish(1, f"push 성공 기록이 없다(status={dj.get('status')}) — check로 원격 확인")
    remote = ls_remote_main(ctx)
    if remote != dj.get("commit"):
        return finish(1, f"원격 main {remote} ≠ 배포 커밋 {dj.get('commit')}")
    out.ok(f"원격 main = 배포 커밋 ({remote[:7]})")
    want = stamp["report_md5"]
    start = dj.get("pushed_at") or _time()
    if dj["status"] == "pushed":
        concl = None
        while True:
            try:
                r = pages_run(dj["commit"])
            except Exception as e:  # noqa: BLE001
                r = None
                out.info(f"Actions 읽기 실패({type(e).__name__}) — 다시 본다")
            if r and r[0] == "completed":
                concl = r[1]
                break
            if _time() >= start + ACTIONS_DEADLINE:
                break
            _sleep(POLL_SECONDS)
        result["actions"] = concl
        if concl == "success":
            out.ok("Pages 빌드 성공(pages build and deployment)")
        elif concl:
            return finish(1, f"Pages 빌드 {concl}")
        else:
            out.warn(f"Pages 빌드 결과를 {ACTIONS_DEADLINE}초 안에 확인하지 못했다")
    while True:
        got = public_md5()
        result["public_md5"] = got
        if got == want:
            return finish(0, f"공개판 md5 = report md5 ({want[:8]}) — 반영 확인")
        if _time() >= start + PUBLIC_DEADLINE:
            break
        _sleep(POLL_SECONDS)
    if dj["status"] == "same" or result["actions"] == "success":
        return finish(3, f"Pages 성공·CDN 반영 미확인 — 공개판 md5 {str(got)[:8]} ≠ {want[:8]}"
                         f"(push 뒤 {PUBLIC_DEADLINE // 60}분)")
    return finish(1, f"반영 확인 실패 — Pages 결과 모름 · 공개판 md5 {str(got)[:8]} ≠ {want[:8]}")


def cmd_record(ctx, args):
    mode_line(ctx, "record")
    out = Out()
    if ctx.rehearsal:
        out.fail("[리허설] record는 막혀 있다")
        return 1
    run, stamp = load_run(ctx, args.run, out)
    if not run:
        return 1
    dj_path, lj_path = os.path.join(run, "deploy.json"), os.path.join(run, "live.json")
    if not os.path.exists(dj_path):
        out.fail("deploy.json 없음 — 배포하지 않은 회차는 기록하지 않는다")
        return 1
    dj = read_json(dj_path)
    lj = read_json(lj_path) if os.path.exists(lj_path) else None
    st = dj.get("status")
    if st in ("rejected", "unknown", "pushing"):
        result = f"실패(push {st})"
    elif lj is None:
        result = "반영 미확인(live 안 함)"
    elif lj.get("exit") == 0 and lj.get("public_md5") == stamp["report_md5"]:
        result = "같은 판(커밋 없음)" if st == "same" else "배포 완료"
    elif lj.get("exit") == 3:
        result = "Pages 성공·CDN 반영 미확인"
    else:
        result = "실패(반영 확인 안 됨)"
    warns = len(log_block(read_text(os.path.join(run, "pipeline.log")), "[경고]", "        - "))
    period = stamp.get("csv_period") or ["?", "?"]
    line = (f"| {now_kst():%Y-%m-%d %H:%M} | {stamp['run']} | {stamp['csv_copy']} | {stamp['csv_src_md5'][:8]} | "
            f"{stamp.get('csv_rows')} | {period[0]}~{period[1]} | {stamp['head'][:7]} | {stamp['report_md5'][:8]} | "
            f"{str(dj.get('commit'))[:7]} | {result} | {warns} |")
    os.makedirs(os.path.dirname(ctx.log_path), exist_ok=True)
    existing = read_text(ctx.log_path)
    if f"| {stamp['run']} |" in existing:
        out.fail(f"회차 {stamp['run']}는 이미 기록돼 있다")
        return 1
    with open(ctx.log_path, "a", encoding="utf-8", newline="") as f:
        if not existing:
            f.write("# 가계부 리포트 실행 기록 (이 PC · 공개 안 함 · 금액·세부내용 없음)\n\n"
                    "| 기록 시각 | 회차 | CSV | CSV md5 | 행 | 기간 | 코드 | report md5 | 커밋 | 반영 | 경고 |\n"
                    "|---|---|---|---|---|---|---|---|---|---|---|\n")
        f.write(line + "\n")
    print(line)
    print(f"[PASS] {p(ctx.log_path)}에 한 줄 기록 — {result}")
    return 0


# ------------------------------------------------------------

def parse_args(argv):
    ap = argparse.ArgumentParser(prog="pc_run.py", description="이 PC Code 탭 가계부 실행 스크립트")
    ap.add_argument("--rehearsal", action="store_true", help="리허설 모드(개발 clone · --confirm 막힘)")
    ap.add_argument("--repo", help="clone 경로(기본: 이 파일이 있는 clone)")
    ap.add_argument("--home", help="작업 루트(기본: clone의 부모)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("sync")
    b = sub.add_parser("build")
    b.add_argument("--csv", required=True)
    for name in ("review", "deploy"):
        s = sub.add_parser(name)
        s.add_argument("--run", required=True)
        s.add_argument("--ack-transfer", action="store_true",
                       help="최신 달 이체 누락 질문에 사용자가 '그대로 진행'이라고 답했을 때")
        if name == "deploy":
            s.add_argument("--confirm", metavar="승인코드")
    for name in ("live", "record"):
        s = sub.add_parser(name)
        s.add_argument("--run", required=True)
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo = args.repo or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    home = args.home or os.path.dirname(os.path.abspath(repo))
    ctx = Ctx(repo, home, args.rehearsal)
    return {"check": cmd_check, "sync": cmd_sync, "build": cmd_build, "review": cmd_review,
            "deploy": cmd_deploy, "live": cmd_live, "record": cmd_record}[args.cmd](ctx, args)


if __name__ == "__main__":
    # cp949 콘솔에서도 '—'·'≠'가 찍히게. 함수 안이 아니라 여기 둔다(시험의 redirect_stdout).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
