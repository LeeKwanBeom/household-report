# 가계부 리포트 — 이 PC Claude Code 탭 절차

웹(claude.ai)은 저장소 `SKILL.md` 그대로다. 이 문서는 이 PC(Windows · `D:\household` · Code 탭)에서만 쓴다.
실행은 `scripts/pc_run.py`가 하고, 세션은 로컬 스킬 `/q-household-run`(정본 `pc/q-household-run/SKILL.md`)의 순서를 따른다.
설계 근거: 2026-10-12 Code 탭 이전 탐색 기준선(설계안 2) — 요약은 `audit/last-audit.md` 맨 위 절.

## 1. 폴더

```
D:\household\
  CLAUDE.md                          로컬 안내 (정본 pc/CLAUDE.md)
  .claude\skills\q-household-run\    진입 스킬 /q-household-run (정본 pc/q-household-run/SKILL.md)
  .claude\settings.json              설치본 스킬 거부 1줄 (정본 pc/settings.json — 사용자가 둔다)
  household-report\                  운영 clone — main · core.autocrlf=false · git 쓰기는 여기서만(pc_run.py)
  .venv\                             python -m venv --system-site-packages --without-pip (설치 0)
  uploads\                           사용자가 CSV를 넣는 곳
  work\<YYYYMMDD-HHMMSS>\            회차 폴더(매번 새로): CSV 사본 · base.html · data_bundle.json · report.html
                                     · pipeline/build/detect.log · stamp.json · deploy.json · live.json
  work-rehearsal\<…>\                리허설 회차 폴더
  runs\log.md                        로컬 실행 기록(공개 안 함 · 금액·세부내용 없음)
```

`D:\household` 루트는 git으로 두지 않는다(uploads·work에 실데이터 — `check`·`build`가 막는다).

## 2. 처음 한 번

1. venv: `python -m venv --system-site-packages --without-pip D:\household\.venv` — 사용자 site의 pandas·numpy를 그대로 쓴다(설치 0). `check`가 판을 찍고, 공개판과 같은 md5를 낸 판(pandas 3.0.6 · numpy 2.5.3)과 다르면 [주의].
2. 로컬 파일: `pc/q-household-run/SKILL.md` → `D:\household\.claude\skills\q-household-run\SKILL.md`, `pc/CLAUDE.md` → `D:\household\CLAUDE.md`를 바이트 그대로 복사한다.
3. 설치본 거부(사용자가 둔다 — PowerShell):
   ```powershell
   New-Item -ItemType Directory -Force D:\household\.claude | Out-Null; Copy-Item -LiteralPath D:\household\household-report\pc\settings.json -Destination D:\household\.claude\settings.json
   ```
   `Skill(skill:<이름>)` 거부 규칙은 그 스킬을 어떤 이름으로 불러도 막는다. 이 폴더에서만 막히고 웹·다른 폴더는 그대로다.
4. 운영 clone 갱신: 아래 6절(빨리 감기만) → `pc_run.py sync`가 `.git/info/exclude`에 `*.csv` · `!tests/fixtures/*.csv` · `data_bundle.json` · `__pycache__/`를 넣는다.

`check`는 2·3의 로컬 파일 md5를 `pc/` 정본과 대조해 다르면 [주의]를 낸다.

## 3. 회차 흐름

`RUN="$PY /d/household/household-report/scripts/pc_run.py"` (PY = `/d/household/.venv/Scripts/python.exe`, 매 Bash 호출 첫머리는 로컬 스킬 참고)

| 단계 | 명령 | 하는 일 · 멈추는 곳 | 쓰기 |
|---|---|---|---|
| S0 | `$RUN check` | clone = 운영 clone · main · autocrlf=false · exclude 줄 · 깨끗 · fetch 뒤 HEAD = origin/main(뒤에만 → sync / 앞섬 → 코드 회차 [주의] 또는 남은 배포 커밋 FAIL / 갈라짐 FAIL) · push --dry-run 인증(창 없이) · 파이썬·pandas·numpy 판 · node · uploads CSV 목록 · 공개판 md5 대 origin/main index.html([주의]만 — CDN 지연) · 지난 회차가 배포됐는지 · 로컬 파일 md5 · 루트 git | `.git`(fetch)만 |
| ① | (세션) | uploads의 CSV — 여럿이면 묻는다 | 0 |
| ② | `$RUN build --csv <경로>` | main·깨끗한 트리·origin/main보다 뒤가 아닐 때만 새 회차 폴더 → CSV 사본 → `base.html = git show HEAD:index.html` → pipeline(rc ≠ 0이면 멈춤, stamp 없음) → build(`HOUSEHOLD_OUT`=회차 폴더, LF) → detect_ended → stamp.json(로컬 HEAD · origin/main · 코드 파일 · 원본 CSV md5 · report md5 · 판) | 회차 폴더만 |
| ③ | `$RUN review --run <회차>` | (a) 최신 달 "이체 행 없음" → [멈춤] 질문(지난 달·"합계 ≠"는 정보) (b) [경고] 전부 (c) 종료 추정 [확인 필요] (d) CSV 최신 날짜 < 공개판 → [멈춤](옛 CSV) (e) `validate_html(prev = base.html)` — 기준판이 비었거나 "건너뜀"이면 FAIL, 잔고 급변은 승인 질문에 싣는다 (f) 기준판 대비 합계·잔고 변동. 멈춤이 없을 때만 **승인 코드(report md5 앞 8자)**와 승인 질문 | 0 |
| ④ | `$RUN deploy --run <회차>` | fetch → 로컬 HEAD = stamp · origin/main = stamp · origin/main..HEAD 파일 ⊆ 승인된 코드 파일 · 회차 파일·원본 CSV md5 = stamp · 깨끗 · ③ 멈춤 없음 · 가장 최근 회차. 기본은 dry-run("커밋될 파일" 줄까지) | 0 |
| ⑤ | `… deploy --run <회차> --confirm <코드>` | 같은 프로세스에서 ④를 다시 돈 뒤, 코드 = 그 폴더 report md5일 때만 report → index.html → `git add index.html` → 스테이징 = index.html 하나 → 커밋(noreply 작성자, "리포트 갱신 (날짜, CSV ~MM-DD)") → push 직전 범위 검사 → `git push origin HEAD:refs/heads/main` **1회**(force·재시도 없음) | 원격 1 push |
| ⑥ | `$RUN live --run <회차>` | `ls-remote` = 배포 커밋 → 그 sha의 `pages build and deployment` 성공(인증 없이, 상한 3분) → 공개판 md5 = report(상한 push 뒤 12분). exit 0 / 3(Pages 성공·CDN 미확인) / 1 | 회차 폴더 live.json |
| ⑦ | `$RUN record --run <회차>` | `runs\log.md` 한 줄. "배포 완료"는 live exit 0이고 공개판 md5 = report일 때만 | 로컬 1줄 |

같은 CSV를 다시 돌리면 report가 공개판과 바이트까지 같아 "커밋될 파일 없음(같은 판)"이다(PC 빌드 = 웹 빌드 — 2026-10-12 실측).

## 4. 승인 코드

- `review`는 멈춤 질문이 하나도 남지 않았을 때만 승인 코드와 승인 질문을 찍는다. 사용자는 "배포 <승인 코드>"로 답한다.
- `deploy --confirm`은 그 코드가 **그 회차 폴더 report md5 앞 8자**일 때만, 그리고 그 폴더가 **가장 최근 회차**일 때만 쓴다(다른 회차 폴더의 판을 막는다).
- 답이 CSV나 코드를 바꾸면 ①/②부터 다시 — 새 회차 폴더, 새 승인 코드.
- 이체 질문에 "그대로 진행"이면 `review`와 `deploy` 둘 다 `--ack-transfer`.

## 5. 리허설 모드

`$PY <개발 clone>/scripts/pc_run.py --rehearsal <명령>` — 운영 clone(main)을 바꾸지 않고 개발 브랜치 코드로 돌린다.
첫 줄에 `[리허설]`이 찍히고, 회차 폴더는 `work-rehearsal\`, `deploy --confirm`·`sync`·`record`는 막힌다. 운영 회차에서는 쓰지 않는다.

## 6. 운영 clone 갱신 — 빨리 감기만

웹이 코드를 push했거나 병합 뒤에는 운영 clone이 origin/main보다 뒤에 있다. 뒤에만 있을 때만 갱신한다:
`$RUN sync`(= fetch → 깨끗·main·`0 N`일 때만 `git merge --ff-only origin/main` → exclude 줄). 앞서 있거나 갈라졌으면 멈추고 사용자에게 묻는다.
`pc_run.py`가 아직 없을 때(병합 직후 첫 회)는 로컬 스킬 0절의 git 명령으로 같은 일을 한다.

## 7. 코드 회차 (종료 항목·시작 잔고 수정 등)

clone에서 코드를 고쳐 **먼저 커밋**한다(push하지 않음, noreply 작성자) → `$PY -m unittest discover tests` → `build`부터.
`build`는 origin/main보다 앞선 커밋의 파일을 stamp에 적고(코드 경로 `deploy.CODE_GLOBS` + `pc/` 밖이거나 fixture에 실 CSV와 같은 행이 있으면 FAIL), 승인 질문에 싣는다. ⑤에서 index.html과 함께 1 push.

## 8. 공통 안전망 (강제 종료·응답 잃음·절전)

"했었나"는 원격 상태로만 판정한다.
- `check`가 지난 회차 report md5를 `origin/main:index.html`과 대조해 "배포됨 / 원격에 없음"을 찍는다.
- push 결과를 모르면(시간 초과) 다시 push하지 않는다 — `check`로 먼저 확인한다.
- 커밋은 했는데 push를 못 했으면 `check`가 "push되지 않은 배포 커밋"으로 멈춘다 → 커밋 목록을 보이고 묻는다. 기본은 `git reset --hard origin/main` 뒤 ②부터(코드 수정이었으면 다시 한다). 자동 push는 없다.
- 회차 폴더는 지우지 않는다(stamp가 남는다).

## 9. 되돌리기 (사용자가 요청할 때만)

```bash
cd /d/household/household-report
git -c user.name=LeeKwanBeom -c user.email=322668067+LeeKwanBeom@users.noreply.github.com revert --no-edit <배포 커밋>
git push origin HEAD:refs/heads/main          # 1회 · force 없음
git ls-remote origin refs/heads/main          # = 로컬 HEAD
```

반영 뒤 공개판 md5가 그 회차 `base.html` md5와 같은지 본다. 되돌리기도 새 커밋 1개다.

## 10. 시험

`$PY -m unittest discover tests` — `tests/test_pc_run.py`는 git 임시 bare 저장소로 원격을 흉내 내서 네트워크·토큰 없이 돈다(리눅스에서도). 공개판·Actions 읽기와 시계는 가짜로 바꿔 끼운다.

## 11. 웹과 다른 점

| | 웹(SKILL.md) | 이 PC |
|---|---|---|
| 코드 받기 | codeload tar → `/home/claude/gagyebu` | 운영 clone(빨리 감기만) |
| CSV | `/mnt/user-data/uploads/` | `D:\household\uploads\` → 회차 폴더 사본 |
| 출력 | `/mnt/user-data/outputs/…html` | `HOUSEHOLD_OUT` = 회차 폴더 `report.html`(LF) |
| 배포 | 사용자 토큰 + Contents API(`deploy.py`) | 이 PC의 git 자격 증명 + git push 1회(토큰 0) |
| 기준판 | 받은 `index.html` | `git show <stamp.head>:index.html` |
| 반영 확인 | 커밋 sha까지 | `ls-remote` + Pages 실행 성공 + 공개판 md5(CDN 최대 10분) |
