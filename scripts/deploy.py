"""
빌드된 리포트를 GitHub Pages(leekwanbeom.github.io/household-report)에 배포한다.

사용법:
    python3 scripts/deploy.py <html경로> <토큰>                # 리포트만
    python3 scripts/deploy.py <html경로> <토큰> --with-code    # 리포트 + 코드
    python3 scripts/deploy.py <토큰> --code-only               # 코드만 (화면 유지)

    토큰은 인자 대신 환경변수 GITHUB_TOKEN 으로도 넘길 수 있다:
    GITHUB_TOKEN=<토큰> python3 scripts/deploy.py <html경로> [--with-code]
    GITHUB_TOKEN=<토큰> python3 scripts/deploy.py --code-only

    --ack-balance : 배포 전 검증의 "잔고 급변" 확인을 사용자에게 받은 뒤 붙인다.

토큰은 인자·환경변수로만 받는다. 이 파일에 절대 적어두지 않는다.
"""
import base64
import glob
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

REPO = "LeeKwanBeom/household-report"
BRANCH = "main"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --with-code / --code-only 로 올리는 파일. 목록을 손으로 적지 않고 패턴으로 찾는다.
# (예전에는 6개 하드코딩이라 audit/checklist.md·install/SKILL.md·tests/ 가 빠졌다.)
# CSV·data_bundle.json·index.html 은 여기 절대 넣지 않는다 — 개인 데이터이거나 따로 다룬다.
CODE_GLOBS = [
    "SKILL.md", "pipeline.py", "build.py",
    "scripts/*.py",
    "audit/*.md",
    "install/*.md",
    "tests/*.py", "tests/fixtures/*.csv",
]

# 배포 전 검증: 직전 배포본(저장소에서 받은 index.html)과 계좌 잔고가 이 이상 벌어지면
# 멈추고 사용자에게 확인을 받는다. 하드 중단이 아니라 "확인 후 --ack-balance 로 진행".
# 월급·이체로 매달 움직이는 폭보다 넉넉히 잡되, 컬럼 누락으로 한 달 지출이 통째로 빠지는
# 사고는 잡히도록 한다. 값은 사용자가 조정한다.
BALANCE_JUMP_THRESHOLD = 3_000_000


def _scan_code_files():
    files = []
    for pat in CODE_GLOBS:
        for p in sorted(glob.glob(os.path.join(ROOT, pat))):
            if os.path.isfile(p):
                files.append(os.path.relpath(p, ROOT).replace(os.sep, "/"))
    return files


def _blob_sha(content: bytes) -> str:
    """GitHub Contents API가 돌려주는 sha와 같은 git blob sha."""
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def api(token, method, path, payload=None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/{path}",
        method=method,
        data=json.dumps(payload).encode() if payload else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body


def head_sha(token):
    """main 브랜치 최신 커밋 sha. 실패하면 None."""
    status, result = api(token, "GET", f"commits?sha={BRANCH}&per_page=1")
    if status == 200 and isinstance(result, list) and result:
        return result[0].get("sha")
    return None


def push(token, local_path, remote_path, message):
    if not os.path.exists(local_path):
        print(f"  [건너뜀] {remote_path}: 로컬 파일 없음 ({local_path})")
        return False

    with open(local_path, "rb") as f:
        content = f.read()

    # 기존 파일 수정 시 SHA가 필요하다. 없으면 신규 생성.
    status, current = api(token, "GET", f"contents/{remote_path}?ref={BRANCH}")
    payload = {
        "message": message,
        "content": base64.b64encode(content).decode(),
        "branch": BRANCH,
    }
    if status == 200 and isinstance(current, dict) and "sha" in current:
        # 내용이 같으면 올리지 않는다. 예전에는 같은 내용도 PUT해서
        # diff 0 커밋이 쌓였다(2026-09-18 2c41f4c 등 4건 확인).
        if current["sha"] == _blob_sha(content):
            print(f"  {remote_path}: 변경 없음, 건너뜀")
            return True
        payload["sha"] = current["sha"]

    status, result = api(token, "PUT", f"contents/{remote_path}", payload)
    if status in (200, 201):
        sha = result.get("content", {}).get("sha", "")[:8]
        print(f"  {remote_path}: OK ({len(content):,} bytes, {sha})")
        return True

    print(f"  {remote_path}: 실패 [{status}] {result}")
    return False


def _extract_data(html_text):
    """임베드 DATA(JSON)를 꺼낸다. build.py가 DATA를 한 줄로 쓰므로 줄 단위로 잡는다.
    예전 정규식 `(\\{.*?\\});` 은 non-greedy라 JSON 문자열 안의 '};'에서 먼저 끊겨
    세부내용에 '};'만 있어도 "JSON 파싱 실패"로 오진단했다(2026-09-06 #5)."""
    m = re.search(r"^const DATA = (.*?);\s*$", html_text, re.M)
    if not m:
        raise SystemExit("HTML에서 DATA 블록을 찾을 수 없습니다.")
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise SystemExit(f"임베드 JSON 파싱 실패: {e}") from None


def check_balance_jump(data, ack=False, prev_html_path=None):
    """직전 배포본(저장소 index.html)과 계좌 잔고를 비교한다.

    임계치를 넘으면 경고를 내고 멈춘다. 사용자에게 확인을 받은 뒤 --ack-balance 로
    다시 실행하면 그대로 진행한다(잘못된 배포를 막는 게 목적이지, 큰 변동 자체를
    금지하는 게 아니다). 직전 배포본이 없으면 비교하지 않는다.
    """
    prev_path = prev_html_path or os.path.join(ROOT, "index.html")
    if not os.path.exists(prev_path):
        print("  잔고 급변 검사: 직전 배포본(index.html) 없음 — 건너뜀")
        return []
    with open(prev_path, encoding="utf-8") as f:
        prev = _extract_data(f.read())
    prev_bal = {a["name"]: a["current_balance"] for a in prev.get("accounts", {}).get("list", [])}
    jumps = []
    for a in data.get("accounts", {}).get("list", []):
        if a["name"] not in prev_bal:
            continue
        diff = a["current_balance"] - prev_bal[a["name"]]
        if abs(diff) > BALANCE_JUMP_THRESHOLD:
            jumps.append((a["name"], prev_bal[a["name"]], a["current_balance"], diff))
    if not jumps:
        print(f"  잔고 급변 검사: 계좌별 변동 {BALANCE_JUMP_THRESHOLD:,}원 이내")
        return []
    print(f"  [확인 필요] 직전 배포본 대비 잔고가 {BALANCE_JUMP_THRESHOLD:,}원 넘게 바뀐 계좌:")
    for name, p, n, d in jumps:
        print(f"    - {name}: {p:,}원 → {n:,}원 ({'+' if d > 0 else ''}{d:,}원)")
    if not ack:
        raise SystemExit(
            "잔고 급변 — 배포를 멈췄습니다. 사용자에게 위 변동이 맞는지 확인받은 뒤\n"
            "같은 명령에 --ack-balance 를 붙여 다시 실행하세요.")
    print("  (--ack-balance) 사용자 확인 받음, 계속 진행")
    return jumps


def validate_html(path, ack_balance=False, prev_html_path=None):
    """배포 전 검증.

    섹션 번호 순차 검사는 일부러 넣지 않는다. build.py가 renumber_sections()로
    방금 01..N을 새로 매긴 결과를 다시 세는 것이라 정의상 절대 실패할 수 없고,
    "검증 통과"라는 문구만 만들어 낸다. 대신 실제로 깨질 수 있는 것을 본다.
    """
    import shutil
    import subprocess
    import tempfile

    with open(path, encoding="utf-8") as f:
        c = f.read()

    if len(c) < 10000:
        raise SystemExit(f"HTML이 비정상적으로 작습니다 ({len(c):,} bytes)")

    # 1) 임베드 JSON이 파싱되는지
    data = _extract_data(c)

    # 2) <script> 블록 JS 문법 (node가 있을 때만)
    scripts = re.findall(r"<script>(.*?)</script>", c, re.S)
    node = shutil.which("node")
    if node:
        for i, js in enumerate(scripts, 1):
            with tempfile.NamedTemporaryFile("w", suffix=".js",
                                             delete=False, encoding="utf-8") as tf:
                tf.write(js)
                tmp = tf.name
            r = subprocess.run([node, "--check", tmp],
                               capture_output=True, text=True)
            os.unlink(tmp)
            if r.returncode != 0:
                raise SystemExit(f"script 블록 {i} 문법 오류:\n{r.stderr.strip()}")
        js_note = f"JS {len(scripts)}블록 OK"
    else:
        js_note = "JS 검사 건너뜀(node 없음)"

    # 3) 치환되지 않은 자리표시자가 남아 있는지
    for token_ in ("{REF:", "{won(", "{bundle["):
        if token_ in c:
            raise SystemExit(f"치환되지 않은 자리표시자가 남아 있습니다: {token_}")

    sections = len(re.findall(r'<section id="', c))
    print(f"  검증: {len(c):,} bytes · 섹션 {sections}개 · {js_note} · "
          f"계좌 {len(data.get('accounts', {}).get('list', []))}개")

    # 4) 직전 배포본 대비 잔고 급변 (경고 + 사용자 확인)
    check_balance_jump(data, ack=ack_balance, prev_html_path=prev_html_path)
    return sections


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    code_only = "--code-only" in flags
    with_code = "--with-code" in flags or code_only
    ack_balance = "--ack-balance" in flags
    env_token = os.environ.get("GITHUB_TOKEN")

    if code_only:
        # 새 CSV 없이 코드만 고쳤을 때. index.html은 건드리지 않으므로
        # 지금 화면에 올라가 있는 리포트가 그대로 유지된다.
        token = args[0] if args else env_token
        if not token:
            raise SystemExit("사용법: python3 scripts/deploy.py <토큰> --code-only "
                             "(또는 GITHUB_TOKEN=<토큰> … --code-only)")
        html_path = None
    else:
        if len(args) >= 2:
            html_path, token = args[0], args[1]
        elif len(args) == 1 and env_token:
            html_path, token = args[0], env_token
        else:
            raise SystemExit(
                "사용법: python3 scripts/deploy.py <html경로> <토큰> [--with-code] [--ack-balance]\n"
                "        python3 scripts/deploy.py <토큰> --code-only\n"
                "        (토큰은 환경변수 GITHUB_TOKEN 으로 대신 넘겨도 된다)")
        if not os.path.exists(html_path):
            raise SystemExit(f"HTML 파일을 찾을 수 없습니다: {html_path}")
        validate_html(html_path, ack_balance=ack_balance)

    print(f"배포 시작 → {REPO} ({BRANCH})")
    before = head_sha(token)
    ok = True
    if html_path:
        ok = push(token, html_path, "index.html", "리포트 갱신")
    else:
        print("  index.html: 건너뜀 (--code-only)")

    if with_code:
        # SKILL.md는 설치본이 아니라 이 저장소가 원본이다. 설치본
        # (/mnt/skills/plugins/...)은 세션마다 패키지에서 다시 풀리므로
        # 거기 쓴 수정은 사라진다. 반드시 여기로 올릴 것.
        for name in _scan_code_files():
            push(token, os.path.join(ROOT, name), name, f"{name} 갱신")

    if not ok:
        raise SystemExit("배포 실패 — 토큰 권한(Contents: Read and write)을 확인하세요.")

    # push 뒤 HEAD 커밋이 실제로 바뀌었는지 확인한다. 내용이 전부 같아 건너뛴 경우는 안 바뀐다.
    after = head_sha(token)
    print(f"  HEAD: {(before or '?')[:7]} → {(after or '?')[:7]}"
          + ("" if before != after else " (변경된 파일 없음)"))

    print()
    if html_path:
        print("배포 완료: https://leekwanbeom.github.io/household-report/")
        print("(반영까지 1~2분 걸릴 수 있습니다)")
    else:
        print("코드 반영 완료. 화면(index.html)은 그대로입니다.")
        print("다음 CSV를 올리면 새 코드로 리포트가 만들어집니다.")


if __name__ == "__main__":
    main()
