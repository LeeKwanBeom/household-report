"""
회귀 테스트 — scripts/pc_run.py (이 PC Code 탭 실행 스크립트)

실행:
    python3 -m unittest discover tests            (웹·리눅스)
    $PY -m unittest discover tests                (이 PC — PY = D:/household/.venv/Scripts/python.exe)

원격은 git 임시 bare 저장소로 흉내 낸다 — 네트워크·토큰 없이 리눅스에서도 돈다. git이 없으면 건너뛴다.
공개판·Actions 읽기(http_get)와 시계(_time·_sleep)는 가짜로 바꿔 끼운다.
CSV는 tests/fixtures/sample.csv(합성)에서 만든 변형만 쓴다. 실제 가계부 CSV 행을 넣지 않는다.

지키는 것(탐색 기준선 4절 완료 기준 · 6절 K1·K2·K3 · 시험 후보 1·3·5):
옛 bundle · 빌드 뒤 로컬 커밋 · origin 바뀜 · 원본 CSV 바뀜 · 다른 회차 폴더 · 틀린 승인 코드 ·
push 범위 밖 파일 · 기준판 = git show · 옛 CSV · dry-run 쓰기 0 · 이체 멈춤 범위 · 리허설 막음.
"""
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pc_run  # noqa: E402

GIT = shutil.which("git")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "sample.csv")
CSV_NAME = "가계부T.csv"
# 저장소에서 임시 clone으로 복사할 파일(빌드·확인에 쓰는 것)
CODE = ["pipeline.py", "build.py", "scripts/deploy.py", "scripts/detect_ended.py", "scripts/pc_run.py"]
EXTRA_ROW = "2026-09-26,지출,식비,외식,시험 저녁,\"21,000\",생활비,,"
EXTRA_ROW2 = "2026-09-26,지출,식비,외식,시험 저녁2,\"34,000\",생활비,,"


def md5(b):
    return hashlib.md5(b).hexdigest()


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def fixture_text():
    with open(FIXTURE, encoding="utf-8-sig") as f:
        return f.read()


def csv_variant(add=(), drop=()):
    lines = [ln for ln in fixture_text().splitlines() if not any(d in ln for d in drop)]
    return "\n".join(lines + list(add)) + "\n"


def _rm_readonly(func, path, _):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def rmtree(path):
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_rm_readonly)
    else:
        shutil.rmtree(path, onerror=_rm_readonly)


def git(cwd, *args, check=True):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    r = subprocess.run(["git", "-C", cwd, "-c", "core.autocrlf=false", "-c", "user.name=t",
                        "-c", "user.email=t@example.com", *args], capture_output=True, env=env)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args}: {r.stderr.decode('utf-8', 'replace')}")
    return r.stdout.decode("utf-8", "replace").strip()


def build_report(csv_text, workdir):
    """저장소 코드로 pipeline → build를 돌려 report 바이트를 만든다(기준판 만들기용)."""
    os.makedirs(workdir, exist_ok=True)
    path = os.path.join(workdir, "in.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(csv_text)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0",
               HOUSEHOLD_OUT=os.path.join(workdir, "report.html"))
    for argv in ([os.path.join(ROOT, "pipeline.py"), "in.csv"], [os.path.join(ROOT, "build.py")]):
        subprocess.run([sys.executable, *argv], cwd=workdir, check=True, capture_output=True, env=env)
    return read_bytes(os.path.join(workdir, "report.html"))


BASE = {}   # 이름 → 기준판(index.html) 바이트. setUpModule에서 한 번 만든다.
_TMP = None


def setUpModule():
    global _TMP
    if not GIT:
        return
    _TMP = tempfile.mkdtemp(prefix="pcrun-base-")
    BASE["fixture"] = build_report(csv_variant(), os.path.join(_TMP, "a"))
    BASE["newer"] = build_report(csv_variant(add=[EXTRA_ROW]), os.path.join(_TMP, "b"))


def tearDownModule():
    if _TMP:
        rmtree(_TMP)


def fake_http(pages=None, actions=None):
    """pages: 공개판 바이트(또는 함수) · None이면 접속 실패. actions: (status, conclusion) 또는 None."""
    def get(url, timeout=20, accept="*/*"):
        if url.startswith(pc_run.PAGES_URL):
            if pages is None:
                raise OSError("offline")
            return 200, pages() if callable(pages) else pages
        if "/actions/runs" in url:
            sha = url.split("head_sha=")[1].split("&")[0]
            runs = []
            if actions:
                runs.append({"name": pc_run.PAGES_WORKFLOW, "head_sha": sha,
                             "status": actions[0], "conclusion": actions[1]})
            return 200, json.dumps({"workflow_runs": runs}).encode()
        raise OSError(f"시험에서 네트워크 없음: {url}")
    return get


@unittest.skipUnless(GIT, "git 없음")
class PcRunBase(unittest.TestCase):
    """임시 원격(bare) + 작업 루트(home) + 운영 clone(home/household-report)을 만든다."""

    base_name = "fixture"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcrun-")
        self.addCleanup(rmtree, self.tmp)
        self.origin = os.path.join(self.tmp, "origin.git")
        os.makedirs(self.origin)
        git(self.origin, "init", "-q", "--bare")
        git(self.origin, "symbolic-ref", "HEAD", "refs/heads/main")
        seed = os.path.join(self.tmp, "seed")
        os.makedirs(seed)
        git(seed, "init", "-q")
        git(seed, "symbolic-ref", "HEAD", "refs/heads/main")
        git(seed, "config", "core.autocrlf", "false")
        for rel in CODE:
            os.makedirs(os.path.dirname(os.path.join(seed, rel)) or seed, exist_ok=True)
            shutil.copyfile(os.path.join(ROOT, rel), os.path.join(seed, rel))
        if os.path.isdir(os.path.join(ROOT, "pc")):
            shutil.copytree(os.path.join(ROOT, "pc"), os.path.join(seed, "pc"))
        with open(os.path.join(seed, "index.html"), "wb") as f:
            f.write(BASE[self.base_name])
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "seed")
        git(seed, "remote", "add", "origin", self.origin)
        git(seed, "push", "-q", "origin", "main")
        self.home = os.path.join(self.tmp, "home")
        self.repo = os.path.join(self.home, "household-report")
        os.makedirs(os.path.join(self.home, "uploads"))
        subprocess.run(["git", "clone", "-q", "-c", "core.autocrlf=false", self.origin, self.repo],
                       check=True, capture_output=True)
        self.csv = os.path.join(self.home, "uploads", CSV_NAME)
        self.write_csv(csv_variant())
        rc, out = self.pc("sync")
        self.assertEqual(rc, 0, out)
        self.http = fake_http(pages=BASE[self.base_name], actions=("completed", "success"))

    # ---- 도우미 ----
    def write_csv(self, text, path=None):
        with open(path or self.csv, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def pc(self, *argv, rehearsal=False, repo=None, http=None):
        buf = io.StringIO()
        args = (["--rehearsal"] if rehearsal else []) + ["--home", self.home, "--repo", repo or self.repo, *argv]
        with mock.patch.object(pc_run, "http_get", http or getattr(self, "http", fake_http())), \
                redirect_stdout(buf):
            rc = pc_run.main(args)
        return rc, buf.getvalue()

    def build(self, **kw):
        rc, out = self.pc("build", "--csv", self.csv, **kw)
        runs = pc_run.run_dirs(pc_run.Ctx(kw.get("repo") or self.repo, self.home, kw.get("rehearsal", False)))
        return rc, out, (runs[-1] if runs else None)

    def stamp(self, rid, rehearsal=False):
        root = "work-rehearsal" if rehearsal else "work"
        with open(os.path.join(self.home, root, rid, "stamp.json"), encoding="utf-8") as f:
            return json.load(f)

    def code_of(self, rid):
        return self.stamp(rid)["report_md5"][:8]

    def remote_main(self):
        return git(self.origin, "rev-parse", "refs/heads/main")

    def commit_in_clone(self, rel, text, msg="code change", force_add=False):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="") as f:
            f.write(text)
        git(self.repo, "add", *(["-f"] if force_add else []), "--", rel)
        git(self.repo, "commit", "-q", "-m", msg)

    def snapshot(self, rid):
        run = os.path.join(self.home, "work", rid)
        files = {n: md5(read_bytes(os.path.join(run, n))) for n in sorted(os.listdir(run))}
        return (git(self.repo, "rev-parse", "HEAD"), git(self.repo, "status", "--porcelain"),
                self.remote_main(), md5(read_bytes(os.path.join(self.repo, "index.html"))), files)


class BuildChecks(PcRunBase):
    def test_build_writes_run_folder_stamp_and_git_show_base(self):
        """회차 폴더 · stamp · 기준판 = git show <stamp.head>:index.html · LF 출력 · clone은 그대로(완료 기준 5·9)."""
        rc, out, rid = self.build()
        self.assertEqual(rc, 0, out)
        run = os.path.join(self.home, "work", rid)
        st = self.stamp(rid)
        self.assertEqual(st["head"], git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(st["origin_main"], st["head"])
        self.assertEqual(st["code_files"], [])
        base = subprocess.run(["git", "-C", self.repo, "show", f"{st['head']}:index.html"],
                              capture_output=True, check=True).stdout
        self.assertEqual(read_bytes(os.path.join(run, "base.html")), base)
        self.assertEqual(st["base_md5"], md5(base))
        report = read_bytes(os.path.join(run, "report.html"))
        self.assertNotIn(b"\r\n", report)                       # newline='' — Windows에서도 LF
        self.assertEqual(md5(report), md5(BASE["fixture"]))     # 같은 CSV → 같은 판
        self.assertEqual(st["csv_src_md5"], md5(read_bytes(self.csv)))
        self.assertEqual(st["csv_rows"], 25)
        self.assertEqual(git(self.repo, "status", "--porcelain", "--ignored"), "")  # __pycache__도 없음

    def test_pipeline_failure_stops_before_build(self):
        """pipeline rc ≠ 0이면 build를 하지 않고 stamp도 없다 → 옛 bundle로 만든 판이 생기지 않는다(M1)."""
        self.write_csv(fixture_text().replace("결제수단,", "결제방법,", 1))  # 필수 컬럼 누락 → [중단]
        rc, out, rid = self.build()
        self.assertEqual(rc, 1, out)
        run = os.path.join(self.home, "work", rid)
        self.assertFalse(os.path.exists(os.path.join(run, "report.html")))
        self.assertFalse(os.path.exists(os.path.join(run, "stamp.json")))
        rc, out = self.pc("review", "--run", rid)
        self.assertEqual(rc, 1)
        self.assertIn("stamp.json이 없다", out)

    def test_dirty_tree_or_branch_blocks_build(self):
        with open(os.path.join(self.repo, "pipeline.py"), "a", encoding="utf-8") as f:
            f.write("# 커밋 안 한 수정\n")
        rc, out, rid = self.build()
        self.assertEqual(rc, 1)
        self.assertIn("작업 트리가 깨끗하지 않다", out)
        self.assertIsNone(rid)
        git(self.repo, "checkout", "-q", "--", "pipeline.py")
        git(self.repo, "checkout", "-q", "-b", "feature")
        rc, out, _ = self.build()
        self.assertEqual(rc, 1)
        self.assertIn("main이 아니다", out)


class DeployFlow(PcRunBase):
    def test_same_version_dry_run_writes_nothing(self):
        """dry-run은 쓰기 0 · 같은 판이면 '커밋될 파일 없음'(완료 기준 15·18)."""
        rc, out, rid = self.build()
        self.assertEqual(rc, 0, out)
        rc, out = self.pc("review", "--run", rid)
        self.assertEqual(rc, 0, out)
        self.assertIn(f"승인 코드: {self.code_of(rid)}", out)
        before = self.snapshot(rid)
        rc, out = self.pc("deploy", "--run", rid)
        self.assertEqual(rc, 0, out)
        self.assertIn("커밋될 파일: 없음(같은 판)", out)
        self.assertIn("dry-run — 쓰기 0", out)
        self.assertEqual(self.snapshot(rid), before)
        # 같은 판을 --confirm 해도 커밋·push 0
        rc, out = self.pc("deploy", "--run", rid, "--confirm", self.code_of(rid))
        self.assertEqual(rc, 0, out)
        self.assertIn("같은 판 — 커밋 0 · push 0", out)
        self.assertEqual(self.snapshot(rid)[:4], before[:4])

    def test_confirm_commits_index_only_and_pushes_once(self):
        """승인 코드가 맞으면 index.html 한 파일 커밋 · push 1회 · noreply 작성자 → live · record(완료 기준 11·13·14·17)."""
        self.write_csv(csv_variant(add=[EXTRA_ROW]))
        rc, out, rid = self.build()
        self.assertEqual(rc, 0, out)
        rc, out = self.pc("review", "--run", rid)
        self.assertEqual(rc, 0, out)
        code = self.code_of(rid)
        old_remote = self.remote_main()
        rc, out = self.pc("deploy", "--run", rid, "--confirm", code)
        self.assertEqual(rc, 0, out)
        head = git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(self.remote_main(), head)
        self.assertEqual(git(self.repo, "rev-list", "--count", f"{old_remote}..{head}"), "1")
        self.assertEqual(git(self.repo, "diff", "--name-only", old_remote, head), "index.html")
        self.assertEqual(git(self.repo, "log", "-1", "--format=%ae"), pc_run.COMMIT_EMAIL)
        self.assertRegex(git(self.repo, "log", "-1", "--format=%s"),
                         r"^리포트 갱신 \(\d{4}-\d{2}-\d{2}, CSV ~09-26\)$")
        self.assertEqual(md5(read_bytes(os.path.join(self.repo, "index.html"))), self.stamp(rid)["report_md5"])
        # 같은 회차를 다시 --confirm 하면 push하지 않는다
        rc, out = self.pc("deploy", "--run", rid, "--confirm", code)
        self.assertEqual(rc, 1)
        self.assertEqual(self.remote_main(), head)

        # ⑥ 공개판이 아직 옛 판(CDN) — 상한이 지나면 exit 3, "배포 완료"가 아니다
        clock = [10_000.0]
        dj = os.path.join(self.home, "work", rid, "deploy.json")
        with open(dj, encoding="utf-8") as f:
            d = json.load(f)
        d["pushed_at"] = clock[0]
        pc_run.write_json_atomic(dj, d)

        def tick(s):
            clock[0] += s
        with mock.patch.object(pc_run, "_time", lambda: clock[0]), mock.patch.object(pc_run, "_sleep", tick):
            rc, out = self.pc("live", "--run", rid,
                              http=fake_http(pages=BASE["fixture"], actions=("completed", "success")))
        self.assertEqual(rc, 3, out)
        self.assertIn("CDN 반영 미확인", out)
        rc, out = self.pc("record", "--run", rid)
        self.assertEqual(rc, 0, out)
        self.assertIn("Pages 성공·CDN 반영 미확인", out)
        self.assertNotIn("배포 완료", out)
        rc, out = self.pc("record", "--run", rid)
        self.assertEqual(rc, 1)
        self.assertIn("이미 기록", out)

    def test_live_ok_then_record_done(self):
        self.write_csv(csv_variant(add=[EXTRA_ROW]))
        rc, out, rid = self.build()
        code = self.code_of(rid)
        self.assertEqual(self.pc("review", "--run", rid)[0], 0)
        self.assertEqual(self.pc("deploy", "--run", rid, "--confirm", code)[0], 0)
        report = read_bytes(os.path.join(self.home, "work", rid, "report.html"))
        with mock.patch.object(pc_run, "_sleep", lambda s: None):
            rc, out = self.pc("live", "--run", rid, http=fake_http(pages=report, actions=("completed", "success")))
        self.assertEqual(rc, 0, out)
        rc, out = self.pc("record", "--run", rid)
        self.assertEqual(rc, 0, out)
        self.assertIn("배포 완료", out)
        with open(os.path.join(self.home, "runs", "log.md"), encoding="utf-8") as f:
            log = f.read()
        self.assertIn(rid, log)
        self.assertNotIn("21,000", log)  # 금액 없음

    def test_pages_build_failure_is_fail(self):
        self.write_csv(csv_variant(add=[EXTRA_ROW]))
        _, _, rid = self.build()
        code = self.code_of(rid)
        self.pc("review", "--run", rid)
        self.assertEqual(self.pc("deploy", "--run", rid, "--confirm", code)[0], 0)
        with mock.patch.object(pc_run, "_sleep", lambda s: None):
            rc, out = self.pc("live", "--run", rid, http=fake_http(pages=b"x", actions=("completed", "failure")))
        self.assertEqual(rc, 1, out)


class BindingK1K2(PcRunBase):
    """빌드한 것·승인한 것과 배포하는 것이 묶여 있는지(K1 · K2)."""

    def _built(self, csv_text=None):
        self.write_csv(csv_text or csv_variant(add=[EXTRA_ROW]))
        rc, out, rid = self.build()
        self.assertEqual(rc, 0, out)
        rc, out = self.pc("review", "--run", rid)
        self.assertEqual(rc, 0, out)
        return rid, self.code_of(rid)

    def assert_blocked(self, rid, code, needle):
        before = self.remote_main()
        rc, out = self.pc("deploy", "--run", rid, "--confirm", code)
        self.assertEqual(rc, 1, out)
        self.assertIn(needle, out)
        self.assertEqual(self.remote_main(), before)
        return out

    def test_local_commit_after_build_blocks(self):
        rid, code = self._built()
        self.commit_in_clone("pipeline.py", "# 빌드 뒤 고친 코드\n")
        self.assert_blocked(rid, code, "빌드 뒤 코드가 바뀌었다")
        rc, out = self.pc("review", "--run", rid)
        self.assertEqual(rc, 1, out)

    def test_origin_moved_blocks(self):
        rid, code = self._built()
        other = os.path.join(self.tmp, "other")
        subprocess.run(["git", "clone", "-q", "-c", "core.autocrlf=false", self.origin, other],
                       check=True, capture_output=True)
        with open(os.path.join(other, "build.py"), "a", encoding="utf-8", newline="") as f:
            f.write("# 웹에서 고친 코드\n")
        git(other, "commit", "-q", "-am", "web change")
        git(other, "push", "-q", "origin", "main")
        self.assert_blocked(rid, code, "원격이 바뀌었다")

    def test_source_csv_changed_blocks(self):
        rid, code = self._built()
        self.write_csv(csv_variant(add=[EXTRA_ROW, EXTRA_ROW2]))
        self.assert_blocked(rid, code, "원본 CSV가 build 뒤 바뀜")

    def test_other_run_folder_and_wrong_code_block(self):
        rid_a, code_a = self._built()
        rid_b, code_b = self._built(csv_variant(add=[EXTRA_ROW2]))
        self.assertNotEqual(code_a, code_b)
        self.assert_blocked(rid_a, code_a, "가장 최근 회차가 아니다")
        self.assert_blocked(rid_b, code_a, "사용자가 승인한 판이 아니다")
        self.assert_blocked(rid_b, "deadbeef", "사용자가 승인한 판이 아니다")
        rc, out = self.pc("deploy", "--run", rid_b, "--confirm", code_b.upper())
        self.assertEqual(rc, 0, out)

    def test_report_replaced_after_review_blocks(self):
        rid, code = self._built()
        report = os.path.join(self.home, "work", rid, "report.html")
        with open(report, "ab") as f:
            f.write(b"\n")
        self.assert_blocked(rid, code, "회차 무결성")


class ScopeAndExposure(PcRunBase):
    """push 범위 · 코드 회차 · 공개 노출(K3 포함)."""

    def test_code_round_pushes_code_and_index_once(self):
        self.commit_in_clone("pipeline.py", "# 코드 회차 시험\n")
        self.write_csv(csv_variant(add=[EXTRA_ROW]))
        rc, out, rid = self.build()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.stamp(rid)["code_files"], ["pipeline.py"])
        rc, out = self.pc("review", "--run", rid)
        self.assertEqual(rc, 0, out)
        self.assertIn("pipeline.py (코드 회차", out)
        old = self.remote_main()
        rc, out = self.pc("deploy", "--run", rid, "--confirm", self.code_of(rid))
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(git(self.repo, "diff", "--name-only", old, "HEAD").split()),
                         ["index.html", "pipeline.py"])
        self.assertEqual(self.remote_main(), git(self.repo, "rev-parse", "HEAD"))

    def test_code_round_with_rename_deploys(self):
        """폴더·파일 이름 변경 커밋: 최종 차이도 삭제+추가로 센다 — 커밋별 경로(range_touched)와 같아야
        '중간 커밋에만 있는 경로'로 잘못 막히지 않는다 (2026-10-13 pc/household-run → q-household-run)."""
        os.makedirs(os.path.join(self.repo, "pc", "moved"))
        git(self.repo, "mv", "pc/code-tab.md", "pc/moved/code-tab.md")
        git(self.repo, "commit", "-q", "-m", "rename")
        self.write_csv(csv_variant(add=[EXTRA_ROW]))
        rc, out, rid = self.build()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.stamp(rid)["code_files"], ["pc/code-tab.md", "pc/moved/code-tab.md"])
        rc, out = self.pc("review", "--run", rid)
        self.assertEqual(rc, 0, out)
        old = self.remote_main()
        rc, out = self.pc("deploy", "--run", rid, "--confirm", self.code_of(rid))
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(git(self.repo, "diff", "--name-only", "--no-renames", old, "HEAD").split()),
                         ["index.html", "pc/code-tab.md", "pc/moved/code-tab.md"])
        self.assertEqual(self.remote_main(), git(self.repo, "rev-parse", "HEAD"))

    def test_non_code_file_in_code_round_blocks_build(self):
        self.commit_in_clone("secret.csv", fixture_text(), force_add=True)
        rc, out, _ = self.build()
        self.assertEqual(rc, 1, out)
        self.assertIn("코드 경로 밖", out)

    def test_real_rows_in_fixture_block_build(self):
        real = csv_variant(add=[EXTRA_ROW])
        self.write_csv(real)
        header = fixture_text().splitlines()[0]
        self.commit_in_clone("tests/fixtures/sample.csv", header + "\n" + EXTRA_ROW + "\n", force_add=True)
        rc, out, _ = self.build()
        self.assertEqual(rc, 1, out)
        self.assertIn("실 CSV와 같은 줄", out)

    def test_reverted_intermediate_commit_with_real_rows_blocks(self):
        """중간 커밋에 들어갔다가 고치는 커밋으로 빠진 실 행도 막는다 — push는 커밋을 통째로 올린다(자체 검토 막음 B1)."""
        self.write_csv(csv_variant(add=[EXTRA_ROW]))
        header = fixture_text().splitlines()[0]
        self.commit_in_clone("tests/fixtures/leak.csv", header + "\n" + EXTRA_ROW + "\n", force_add=True)
        git(self.repo, "rm", "-q", "--", "tests/fixtures/leak.csv")
        git(self.repo, "commit", "-q", "-m", "fix")
        self.commit_in_clone("pipeline.py", "# 코드\n")
        rc, out, _ = self.build()
        self.assertEqual(rc, 1, out)
        self.assertIn("중간 커밋에서 건드리고 최종 차이에는 없는 경로", out)
        self.assertIn("실 CSV와 같은 줄", out)

    def test_force_added_csv_untracked_later_still_blocks(self):
        """강제 add한 CSV를 다음 커밋에서 rm --cached로 '지워도' 막는다(B1)."""
        self.commit_in_clone("data/x.csv", fixture_text(), force_add=True)
        git(self.repo, "rm", "-q", "--cached", "--", "data/x.csv")
        git(self.repo, "commit", "-q", "-m", "untrack")
        rc, out, _ = self.build()
        self.assertEqual(rc, 1, out)
        self.assertIn("코드 경로 밖", out)
        self.assertIn("중간 커밋에서 건드리고", out)

    def put_real_import_files(self):
        """작업 루트에 '실' 내역 파일·규칙(시험용 합성 값)을 둔다 — 대조 집합의 원천."""
        inbox = os.path.join(self.home, "uploads", "내역", "2026-10-27")
        os.makedirs(inbox)
        with open(os.path.join(inbox, "card.csv"), "w", encoding="utf-8", newline="") as f:
            f.write("카드번호,****9999\r\n이용일시,가맹점명,이용금액,승인번호\r\n"
                    "2026-10-05 13:47:00,비밀상점 역삼,\"23,456\",87654321\r\n")
        rules = os.path.join(self.home, "rules")
        os.makedirs(rules)
        with open(os.path.join(rules, "분류규칙.csv"), "w", encoding="utf-8-sig", newline="") as f:
            f.write("키,대분류,소분류,세부내용,고정여부,결제수단,물음,원문,만든 날,근거 회차\r\n"
                    "비밀가게,식비,외식,비밀가게,,,자동,비밀가게 3호점,2026-10-27,x\r\n")

    def test_real_import_values_in_commit_block_build(self):
        """내역 자동화 3-0 11 · 완료 기준 22: 실 내역 파일·규칙에서 뽑은 값 1개라도 커밋에 있으면 FAIL
        (승인번호 · 거래일시+금액 · 가맹점 원문 · 규칙 줄). 시험 후보 13."""
        self.put_real_import_files()
        cases = [
            ("tests/test_x.py", "APPROVAL = '87654321'\n", "승인번호"),
            ("tests/test_x.py", "ROW = '2026.10.05 13:47', 23456\n", "날짜+금액"),
            ("tests/test_x.py", "M = ['비밀가게 3호점']\n", "가맹점"),
            ("tests/fixtures/r.csv", "비밀가게,식비,외식,비밀가게,,,자동,비밀가게 3호점,2026-10-27,x\n", "실 CSV와 같은 줄"),
        ]
        for rel, text, want in cases:
            with self.subTest(want=want):
                base = git(self.repo, "rev-parse", "HEAD")
                self.commit_in_clone(rel, text, force_add=True)
                rc, out, _ = self.build()
                self.assertEqual(rc, 1, out)
                self.assertIn(want, out)
                git(self.repo, "reset", "-q", "--hard", base)
        # 같은 꼴의 가짜 값(다른 날짜·금액·번호)은 통과 — 오탐으로 막지 않는다
        self.commit_in_clone("tests/test_x.py", "ROW = '2026.10.05 13:48', 23457, '87654320', '가짜상점'\n")
        self.write_csv(csv_variant(add=[EXTRA_ROW]))
        rc, out, _ = self.build()
        self.assertEqual(rc, 0, out)

    def test_data_paths_and_binaries_block_build(self):
        """xlsx·xls·sqlite·toml 경로는 내용과 상관없이 FAIL · 바이너리(zip·OLE·sqlite 머리)는 이름을 바꿔도 FAIL."""
        base = git(self.repo, "rev-parse", "HEAD")
        self.commit_in_clone("tests/fixtures/real.xlsx", "x", force_add=True)
        rc, out, _ = self.build()
        self.assertEqual(rc, 1, out)
        self.assertIn("내역·규칙·원장 꼴 경로", out)
        git(self.repo, "reset", "-q", "--hard", base)
        path = os.path.join(self.repo, "tests", "fixtures", "renamed.csv")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"PK\x03\x04" + b"\x00" * 30)
        git(self.repo, "add", "-f", "--", "tests/fixtures/renamed.csv")
        git(self.repo, "commit", "-q", "-m", "bin")
        rc, out, _ = self.build()
        self.assertEqual(rc, 1, out)
        self.assertIn("바이너리", out)

    def test_stamp_code_list_narrower_than_push_blocks(self):
        """push 범위 = index.html + 승인된 코드 파일. stamp 목록 밖 파일이 범위에 있으면 push하지 않는다."""
        self.commit_in_clone("build.py", "# 코드\n")
        self.write_csv(csv_variant(add=[EXTRA_ROW]))
        rc, out, rid = self.build()
        self.assertEqual(rc, 0, out)
        sp = os.path.join(self.home, "work", rid, "stamp.json")
        with open(sp, encoding="utf-8") as f:
            st = json.load(f)
        st["code_files"] = []
        pc_run.write_json_atomic(sp, st)
        before = self.remote_main()
        rc, out = self.pc("deploy", "--run", rid, "--confirm", self.code_of(rid))
        self.assertEqual(rc, 1, out)
        self.assertIn("승인된 코드 파일", out)
        self.assertEqual(self.remote_main(), before)

    def test_unpushed_deploy_commit_stops_check_and_build(self):
        """커밋은 했는데 push 못 한 채 죽으면 S0가 멈춘다(공통 안전망)."""
        self.commit_in_clone("index.html", "<!-- 남은 배포 커밋 -->\n")
        rc, out = self.pc("check")
        self.assertEqual(rc, 1, out)
        self.assertIn("push되지 않은 배포 커밋", out)
        rc, out, _ = self.build()
        self.assertEqual(rc, 1, out)

    def test_root_git_blocks(self):
        """작업 루트를 git으로 두면 check·build가 멈춘다(K3)."""
        git(self.home, "init", "-q")
        rc, out = self.pc("check")
        self.assertEqual(rc, 1)
        self.assertIn("루트 git 금지", out)
        rc, out, _ = self.build()
        self.assertEqual(rc, 1)
        self.assertIn("루트 git 금지", out)

    def test_csv_inside_clone_blocks(self):
        inside = os.path.join(self.repo, "in.csv")
        self.write_csv(csv_variant(), inside)
        rc, out = self.pc("build", "--csv", inside)
        self.assertEqual(rc, 1)
        self.assertIn("clone 안에", out)


class ReviewStops(PcRunBase):
    def test_latest_month_transfer_missing_stops_until_ack(self):
        """최신 달 이체 행 없음 → 멈춤(승인 코드 없음). 답 '그대로 진행' → --ack-transfer(완료 기준 10 · 시험 후보 5)."""
        self.write_csv(csv_variant(drop=["2026-09-01,이체,이체,커플통장"]))
        rc, out, rid = self.build()
        self.assertEqual(rc, 0, out)
        rc, out = self.pc("review", "--run", rid)
        self.assertEqual(rc, 2, out)
        self.assertIn("[멈춤] 2026-09 생활비→커플통장 이체 행 없음", out)
        self.assertNotIn("승인 코드:", out)
        rc, out = self.pc("deploy", "--run", rid)
        self.assertEqual(rc, 1, out)
        self.assertIn("멈춤 질문이 남은 회차", out)
        rc, out = self.pc("review", "--run", rid, "--ack-transfer")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"승인 코드: {self.code_of(rid)}", out)
        rc, out = self.pc("deploy", "--run", rid, "--ack-transfer")
        self.assertEqual(rc, 0, out)

    def test_transfer_only_new_month_is_checked(self):
        """CSV 최신 날짜의 달에 이체 행만 있으면(수입·지출 없음) 그 달 이체도 대조해 멈춘다(자체 검토 막음 B2)."""
        rows = ['2026-10-01,이체,이체,신한은행,고정비 이체,"702,000",생활비,고정,',
                '2026-10-01,이체,이체,청년미래적금,샘플 적금 이체,"500,000",생활비,고정,']
        self.write_csv(csv_variant(add=rows))
        rc, out, rid = self.build()
        self.assertEqual(rc, 0, out)
        rc, out = self.pc("review", "--run", rid)
        self.assertEqual(rc, 2, out)
        self.assertIn("[멈춤] 2026-10 생활비→커플통장 이체 행 없음", out)
        self.assertNotIn("승인 코드:", out)
        rc, out = self.pc("deploy", "--run", rid)
        self.assertEqual(rc, 1, out)
        rows.append('2026-10-01,이체,이체,커플통장,커플통장 이체,"400,000",생활비,고정,')
        self.write_csv(csv_variant(add=rows))
        rc, out, rid = self.build()
        self.assertEqual(rc, 0, out)
        rc, out = self.pc("review", "--run", rid)
        self.assertEqual(rc, 0, out)
        self.assertIn("2026-10에는 수입·지출 행이 없고 이체 행만 있다", out)

    def test_validate_skip_line_or_empty_base_fails(self):
        """잔고 급변 검사를 '건너뜀'이면 FAIL(시험 후보 1)."""
        rc, out, rid = self.build()

        def skipped(*a, **k):
            print("  잔고 급변 검사: 직전 배포본(index.html) 없음 — 건너뜀")
            return 14
        with mock.patch.object(pc_run.deploy, "validate_html", skipped):
            rc, out = self.pc("review", "--run", rid)
        self.assertEqual(rc, 1, out)
        self.assertIn("잔고 급변 검사를 건너뛰었다", out)

    def test_check_public_mismatch_is_warning_only(self):
        """공개판(CDN)이 옛 판이거나 못 읽어도 S0는 [주의]만(시험 후보 3)."""
        rc, out = self.pc("check", http=fake_http(pages=b"<html>old</html>"))
        self.assertEqual(rc, 0, out)
        self.assertIn("[주의] 공개판 md5", out)
        rc, out = self.pc("check", http=fake_http(pages=None))
        self.assertEqual(rc, 0, out)
        self.assertIn("[주의] 공개판을 읽지 못했다", out)
        self.assertIn("[PASS] HEAD = origin/main", out)


class OldCsv(PcRunBase):
    base_name = "newer"   # 공개판이 CSV보다 하루 뒤까지

    def test_old_csv_stops(self):
        """CSV 최신 날짜 < 공개판 최신 날짜 → 멈춤 · 승인 코드 없음 · 배포 막힘(완료 기준 8 · M5)."""
        rc, out, rid = self.build()
        self.assertEqual(rc, 0, out)
        rc, out = self.pc("review", "--run", rid)
        self.assertEqual(rc, 2, out)
        self.assertIn("옛 CSV", out)
        self.assertNotIn("승인 코드:", out)
        rc, out = self.pc("deploy", "--run", rid, "--confirm", self.code_of(rid))
        self.assertEqual(rc, 1, out)


class EmptyBase(PcRunBase):
    def setUp(self):
        BASE["empty"] = b"<html><body>" + b"x" * 20000 + b"</body></html>"
        self.base_name = "empty"
        super().setUp()

    def test_base_without_data_fails(self):
        rc, out, rid = self.build()
        self.assertEqual(rc, 0, out)
        rc, out = self.pc("review", "--run", rid)
        self.assertEqual(rc, 1, out)
        self.assertIn("기준판이 비었거나", out)


class RehearsalAndSync(PcRunBase):
    def dev_clone(self):
        dev = os.path.join(self.home, "household-report-dev")
        subprocess.run(["git", "clone", "-q", "-c", "core.autocrlf=false", self.origin, dev],
                       check=True, capture_output=True)
        git(dev, "checkout", "-q", "-b", "feature/x")
        self.commit_in_clone_at(dev, "scripts/pc_run.py", "# 개발 커밋\n")
        return dev

    def commit_in_clone_at(self, repo, rel, text):
        with open(os.path.join(repo, rel), "a", encoding="utf-8", newline="") as f:
            f.write(text)
        git(repo, "commit", "-q", "-am", "dev")

    def test_rehearsal_runs_on_branch_and_blocks_confirm(self):
        dev = self.dev_clone()
        ops_before = git(self.repo, "rev-parse", "HEAD")
        rc, out = self.pc("check", rehearsal=True, repo=dev)
        self.assertTrue(out.startswith("[리허설] check"), out[:200])
        rc, out, rid = self.build(rehearsal=True, repo=dev)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.startswith("[리허설] build"))
        self.assertTrue(os.path.isfile(os.path.join(self.home, "work-rehearsal", rid, "stamp.json")))
        self.assertFalse(os.path.exists(os.path.join(self.home, "work")))
        rc, out = self.pc("review", "--run", rid, rehearsal=True, repo=dev)
        self.assertEqual(rc, 0, out)
        rc, out = self.pc("deploy", "--run", rid, rehearsal=True, repo=dev)
        self.assertEqual(rc, 0, out)
        self.assertIn("커밋될 파일: 없음(같은 판)", out)       # 개발 커밋은 배포 대상이 아니다
        self.assertIn("개발 브랜치 커밋의 파일 1개는 배포 대상이 아니다", out)
        before = self.remote_main()
        code = self.stamp(rid, rehearsal=True)["report_md5"][:8]
        rc, out = self.pc("deploy", "--run", rid, "--confirm", code, rehearsal=True, repo=dev)
        self.assertEqual(rc, 1)
        self.assertIn("[리허설] deploy --confirm은 막혀 있다", out)
        for cmd in (("sync",), ("record", "--run", rid)):
            rc, out = self.pc(*cmd, rehearsal=True, repo=dev)
            self.assertEqual(rc, 1, out)
        self.assertEqual(self.remote_main(), before)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), ops_before)
        # 운영 모드로 개발 clone을 돌리면 멈춘다
        rc, out = self.pc("build", "--csv", self.csv, repo=dev)
        self.assertEqual(rc, 1)
        self.assertIn("--rehearsal", out)
        # 리허설 회차를 운영 모드로 쓰지 못한다
        rc, out = self.pc("review", "--run", os.path.join(self.home, "work-rehearsal", rid))
        self.assertEqual(rc, 1)

    def test_sync_fast_forward_only(self):
        other = os.path.join(self.tmp, "other")
        subprocess.run(["git", "clone", "-q", "-c", "core.autocrlf=false", self.origin, other],
                       check=True, capture_output=True)
        self.commit_in_clone_at(other, "build.py", "# 웹 코드\n")
        git(other, "push", "-q", "origin", "main")
        rc, out = self.pc("check")
        self.assertEqual(rc, 1)
        self.assertIn("뒤에만 있다", out)
        rc, out = self.pc("sync")
        self.assertEqual(rc, 0, out)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.remote_main())
        # 앞서 있으면(코드 회차가 아닌 남은 커밋 포함) 멈추고 바꾸지 않는다
        self.commit_in_clone("build.py", "# 로컬\n")
        head = git(self.repo, "rev-parse", "HEAD")
        rc, out = self.pc("sync")
        self.assertEqual(rc, 1, out)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)
        # 갈라졌으면 멈춘다
        self.commit_in_clone_at(other, "build.py", "# 웹 코드 2\n")
        git(other, "push", "-q", "origin", "main")
        rc, out = self.pc("sync")
        self.assertEqual(rc, 1, out)
        self.assertIn("갈라졌다", out)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)

    def test_sync_adds_exclude_lines(self):
        path = os.path.join(self.repo, ".git", "info", "exclude")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for line in pc_run.EXCLUDE_LINES:
            self.assertIn(line, text.splitlines())
        # 회차 산출물·내역 파일·규칙·원장을 clone에 실수로 두어도 git status에 안 잡힌다
        os.makedirs(os.path.join(self.repo, "rules"))
        for name in ("x.csv", "data_bundle.json", "card.xlsx", "bank.xls", "rules/계좌.toml", "원장.sqlite"):
            with open(os.path.join(self.repo, name), "w") as f:
                f.write("x")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")


class Units(unittest.TestCase):
    def test_classify_transfers(self):
        """최신 달 '이체 행 없음'만 멈춤 · 지난 달 누락과 '합계 ≠'는 정보(시험 후보 5)."""
        b = {"months_included": ["2026-09", "2026-10"],
             "diagnostics": {"transfer_issues": [
                 "2026-09 생활비→신한은행 이체 행 없음",
                 "2026-09 생활비→커플통장 이체 합계 1원 ≠ 예상 2원 (1건)",
                 "2026-10 생활비→청년미래적금 이체 합계 1원 ≠ 예상 2원 (2건)",
                 "2026-10 생활비→커플통장 이체 행 없음"]}}
        stops, infos = pc_run.classify_transfers(b)
        self.assertEqual(stops, ["2026-10 생활비→커플통장 이체 행 없음"])
        self.assertEqual(len(infos), 3)

    def test_latest_date_of(self):
        from datetime import date
        self.assertEqual(pc_run.latest_date_of({"months_included": ["2026-10"], "latest_update": "10월 6일"}),
                         date(2026, 10, 6))
        self.assertEqual(pc_run.latest_date_of({"months_included": ["2026-12"], "latest_update": "1월 2일"}),
                         date(2027, 1, 2))
        self.assertIsNone(pc_run.latest_date_of({"months_included": [], "latest_update": "1월 2일"}))

    def test_run_key_order(self):
        names = ["20261012-161500-10", "20261012-161500", "20261012-161500-2", "20261011-235959"]
        self.assertEqual(sorted(names, key=pc_run.run_key)[-1], "20261012-161500-10")


if __name__ == "__main__":
    unittest.main()
