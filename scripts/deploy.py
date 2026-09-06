"""
빌드된 리포트를 GitHub Pages(leekwanbeom.github.io/household-report)에 배포한다.

사용법:
    python3 scripts/deploy.py <html경로> <토큰>
    python3 scripts/deploy.py <html경로> <토큰> --with-code   # 코드도 함께 push

토큰은 인자로만 받는다. 이 파일에 절대 적어두지 않는다.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

REPO = "LeeKwanBeom/household-report"
BRANCH = "main"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
        if current.get("sha") and current.get("size") == len(content):
            # 크기가 같아도 내용이 다를 수 있으므로 계속 진행하되 SHA는 넘긴다
            pass
        payload["sha"] = current["sha"]

    status, result = api(token, "PUT", f"contents/{remote_path}", payload)
    if status in (200, 201):
        sha = result.get("content", {}).get("sha", "")[:8]
        print(f"  {remote_path}: OK ({len(content):,} bytes, {sha})")
        return True

    print(f"  {remote_path}: 실패 [{status}] {result}")
    return False


def validate_html(path):
    """배포 전 검증.

    섹션 번호 순차 검사는 일부러 넣지 않는다. build.py가 renumber_sections()로
    방금 01..N을 새로 매긴 결과를 다시 세는 것이라 정의상 절대 실패할 수 없고,
    "검증 통과"라는 문구만 만들어 낸다. 대신 실제로 깨질 수 있는 것을 본다.
    """
    import re
    import shutil
    import subprocess
    import tempfile

    with open(path, encoding="utf-8") as f:
        c = f.read()

    if len(c) < 10000:
        raise SystemExit(f"HTML이 비정상적으로 작습니다 ({len(c):,} bytes)")

    # 1) 임베드 JSON이 파싱되는지
    m = re.search(r"const DATA = (\{.*?\});", c, re.S)
    if not m:
        raise SystemExit("HTML에서 DATA 블록을 찾을 수 없습니다.")
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise SystemExit(f"임베드 JSON 파싱 실패: {e}") from None

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
    return sections


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    code_only = "--code-only" in flags
    with_code = "--with-code" in flags or code_only

    if code_only:
        # 새 CSV 없이 코드만 고쳤을 때. index.html은 건드리지 않으므로
        # 지금 화면에 올라가 있는 리포트가 그대로 유지된다.
        if not args:
            raise SystemExit("사용법: python3 scripts/deploy.py <토큰> --code-only")
        token = args[0]
        html_path = None
    else:
        if len(args) < 2:
            raise SystemExit(
                "사용법: python3 scripts/deploy.py <html경로> <토큰> [--with-code]\n"
                "        python3 scripts/deploy.py <토큰> --code-only")
        html_path, token = args[0], args[1]
        if not os.path.exists(html_path):
            raise SystemExit(f"HTML 파일을 찾을 수 없습니다: {html_path}")
        validate_html(html_path)

    print(f"배포 시작 → {REPO} ({BRANCH})")
    ok = True
    if html_path:
        ok = push(token, html_path, "index.html", "리포트 갱신")
    else:
        print("  index.html: 건너뜀 (--code-only)")

    if with_code:
        for name in ("pipeline.py", "build.py",
                     "scripts/detect_ended.py", "scripts/deploy.py"):
            push(token, os.path.join(ROOT, name), name, f"{name} 갱신")

    if not ok:
        raise SystemExit("배포 실패 — 토큰 권한(Contents: Read and write)을 확인하세요.")

    print()
    if html_path:
        print("배포 완료: https://leekwanbeom.github.io/household-report/")
        print("(반영까지 1~2분 걸릴 수 있습니다)")
    else:
        print("코드 반영 완료. 화면(index.html)은 그대로입니다.")
        print("다음 CSV를 올리면 새 코드로 리포트가 만들어집니다.")


if __name__ == "__main__":
    main()
