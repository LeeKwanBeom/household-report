# D:\household — 가계부 리포트 (이 PC Code 탭)

- 이 폴더의 가계부 작업("가계부 업데이트"·"가계부 리포트"·"가계부 배포" 등)은 로컬 스킬 `/q-household-run`(`.claude/skills/q-household-run`)으로 한다. 설치본 `household-report-update` 스킬을 부르지 않는다 — 그 1절(codeload 받기)을 돌리지 않고 `/home/claude`·`/mnt/…` 경로를 만들지 않는다.
- 저장소 SKILL.md 7절의 토큰 경로(토큰 요청·`deploy.py`로 업로드)와 대체 경로의 `git reset --hard`를 쓰지 않는다. 토큰을 묻거나 출력·저장하지 않고 `git credential fill`을 치지 않는다. 배포는 `scripts/pc_run.py deploy --confirm`의 git push(이 PC의 자격 증명)만 쓴다.
- `D:\household` 루트에서 `git init`·원격 추가를 하지 않는다(uploads·work에 실데이터가 있다). git 쓰기는 clone `household-report` 안에서 `pc_run.py`로만 하고, `git add -A`·`commit -a`·`--force`를 쓰지 않는다.
- 실 CSV·`data_bundle.json`·report는 clone 밖(`uploads\`·`work\`)에만 둔다. 저장소 `tests/fixtures/`에 실 CSV 행을 옮기지 않는다(공개 저장소).
- 파이썬은 `/d/household/.venv/Scripts/python.exe`만 쓴다(`python3`는 Microsoft Store 스텁). 패키지 설치는 먼저 사용자에게 묻는다.
- 정본은 저장소 `pc/`(`code-tab.md` 절차 · `q-household-run/SKILL.md` · `CLAUDE.md` · `settings.json`)다. 이 파일과 로컬 스킬은 그 사본이다 — 고칠 때는 `pc/`를 고쳐 커밋한 뒤 복사한다(`pc_run.py check`가 md5를 대조한다).
