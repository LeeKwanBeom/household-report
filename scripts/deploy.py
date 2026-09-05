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


def main():
    if len(sys.argv) < 3:
        raise SystemExit(
            "사용법: python3 scripts/deploy.py <html경로> <토큰> [--with-code]")

    html_path, token = sys.argv[1], sys.argv[2]
    with_code = "--with-code" in sys.argv[3:]

    if not os.path.exists(html_path):
        raise SystemExit(f"HTML 파일을 찾을 수 없습니다: {html_path}")

    print(f"배포 시작 → {REPO} ({BRANCH})")
    ok = push(token, html_path, "index.html", "리포트 갱신")

    if with_code:
        for name in ("pipeline.py", "build.py",
                     "scripts/detect_ended.py", "scripts/deploy.py"):
            push(token, os.path.join(ROOT, name), name, f"{name} 갱신")

    if not ok:
        raise SystemExit("배포 실패 — 토큰 권한(Contents: Read and write)을 확인하세요.")

    print()
    print("배포 완료: https://leekwanbeom.github.io/household-report/")
    print("(반영까지 1~2분 걸릴 수 있습니다)")


if __name__ == "__main__":
    main()
