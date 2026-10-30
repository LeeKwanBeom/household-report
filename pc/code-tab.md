# 가계부 리포트 — 이 PC Claude Code 탭 절차

웹(claude.ai)은 저장소 `SKILL.md` 그대로다. 이 문서는 이 PC(Windows · `D:\household` · Code 탭)에서만 쓴다.
실행은 `scripts/pc_run.py`가 하고, 세션은 로컬 스킬 `/q-household-run`(정본 `pc/q-household-run/SKILL.md`)의 순서를 따른다(인자 없이 부르면 12절 내역 받기부터 배포 승인 질문까지).
설계 근거: 2026-10-12 Code 탭 이전 탐색 기준선(설계안 2) — 요약은 `audit/last-audit.md` 맨 위 절.

## 1. 폴더

```
D:\household\
  CLAUDE.md                          로컬 안내 (정본 pc/CLAUDE.md)
  .claude\skills\q-household-run\    진입 스킬 /q-household-run (정본 pc/q-household-run/SKILL.md)
  .claude\settings.json              설치본 스킬 거부 1줄 (정본 pc/settings.json — 사용자가 둔다)
  household-report\                  운영 clone — main · core.autocrlf=false · git 쓰기는 여기서만(pc_run.py)
  .venv\                             python -m venv --system-site-packages --without-pip (설치 0)
  uploads\                           사용자가 CSV를 넣는 곳(늘 CSV 한 파일)
  uploads\내역\<받은 날>\             금융사 내역 파일(pc_fetch가 새 폴더째 넣거나 사용자가 받아 넣음 — 12절)
  uploads\받은메일\                   카카오뱅크 메일 첨부를 손으로 저장하는 곳(pc_fetch --mail-only가 읽기만 · inbox 밖)
  chrome-profile-fetch\              내역 받기 전용 크롬 프로필(홈택스 프로필과 다른 폴더)
  uploads\backup\<회차>\              내역 정리 apply 직전 CSV 사본(지우지 않는다)
  rules\                             내역 정리 설정·규칙·원장(계좌.toml · 받기.toml · 분류규칙.csv · 원장.sqlite — 공개 안 함)
  work\<YYYYMMDD-HHMMSS>\            회차 폴더(매번 새로): CSV 사본 · base.html · data_bundle.json · report.html
                                     · pipeline/build/detect.log · stamp.json · deploy.json · live.json
  work\내역\<회차>\                   내역 정리 회차 폴더(12절)
  work\받기\<회차>\                   내역 받기 회차 폴더(받은 원본·암호 원본·summary.json·stage — 12절)
  work-rehearsal\<…>\                리허설 회차 폴더(내역 정리 리허설 놀이터는 work-rehearsal\내역\)
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
| (내역 정리) | `$IMP scan` → 답 → `$IMP apply --run <회차> --confirm` | 금융사 내역 파일을 CSV 행으로 정리해 uploads에 새 판 CSV를 둔다 — 12절. 사용자가 "내역 정리"라고 할 때만 | uploads·rules(apply만) |
| S0 | `$RUN check` | clone = 운영 clone · main · autocrlf=false · exclude 줄 · 깨끗 · fetch 뒤 HEAD = origin/main(뒤에만 → sync / 앞섬 → 코드 회차 [주의] 또는 남은 배포 커밋 FAIL / 갈라짐 FAIL) · push --dry-run 인증(창 없이) · 파이썬·pandas·numpy 판 · node · uploads CSV 목록 · 공개판 md5 대 origin/main index.html([주의]만 — CDN 지연) · 지난 회차가 배포됐는지 · 로컬 파일 md5 · 루트 git | `.git`(fetch)만 |
| ① | (세션) | uploads의 CSV — 여럿이면 묻는다 | 0 |
| ② | `$RUN build --csv <경로>` | main·깨끗한 트리·origin/main보다 뒤가 아닐 때만 새 회차 폴더 → CSV 사본 → `base.html = git show HEAD:index.html` → pipeline(rc ≠ 0이면 멈춤, stamp 없음) → build(`HOUSEHOLD_OUT`=회차 폴더, LF) → detect_ended → stamp.json(로컬 HEAD · origin/main · 코드 파일 · 원본 CSV md5 · report md5 · 판) | 회차 폴더만 |
| ③ | `$RUN review --run <회차>` | (a) 최신 달 "이체 행 없음" → [멈춤] 질문(지난 달·"합계 ≠"는 정보) · 커플통장 정산 줄(bundle 문구 그대로 — 대화로 · 옛 회차 bundle이면 없음) (b) [경고] 전부 (c) 종료 추정 [확인 필요] (d) CSV 최신 날짜 < 공개판 → [멈춤](옛 CSV) (e) `validate_html(prev = base.html)` — 기준판이 비었거나 "건너뜀"이면 FAIL, 잔고 급변은 승인 질문에 싣는다 (f) 기준판 대비 합계·잔고 변동. 멈춤이 없을 때만 **승인 코드(report md5 앞 8자)**와 승인 질문 | 0 |
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

## 11. 웹과 다른 점 (내역 정리는 PC에만 있다)

| | 웹(SKILL.md) | 이 PC |
|---|---|---|
| 코드 받기 | codeload tar → `/home/claude/gagyebu` | 운영 clone(빨리 감기만) |
| CSV | `/mnt/user-data/uploads/` | `D:\household\uploads\` → 회차 폴더 사본 |
| 출력 | `/mnt/user-data/outputs/…html` | `HOUSEHOLD_OUT` = 회차 폴더 `report.html`(LF) |
| 배포 | 사용자 토큰 + Contents API(`deploy.py`) | 이 PC의 git 자격 증명 + git push 1회(토큰 0) |
| 기준판 | 받은 `index.html` | `git show <stamp.head>:index.html` |
| 반영 확인 | 커밋 sha까지 | `ls-remote` + Pages 실행 성공 + 공개판 md5(CDN 최대 10분) |

## 12. 내역 정리 — 금융사 내역 파일 → CSV 행 (`scripts/pc_import.py`, 설계안 A)

설계 근거: 2026-10-26 내역 자동화 탐색 기준선 3-0 · 3-A(요약은 `audit/last-audit.md` 맨 위 절). 손으로 6곳 앱을 보며 적던 일을
"파일 4개 받기 → scan → 확인 필요 답 → apply"로 바꾼다. 그 뒤는 지금 흐름(①부터) 그대로다.

**내역 받기(파일 4개, 기간이 겹치게 — `scripts/pc_fetch.py`, 2026-10-28 내역 받기 설계안 A)** — 로그인은 사용자, 그 뒤는 `pc_fetch`:
`$FETCH`(dry-run — 출처별 받는 기간·근거·새 폴더 이름·자격 증명 **유무**만) → `$FETCH --run`. 전용 크롬(`D:\household\chrome-profile-fetch`)에
신한카드 → 현대카드 → 신한은행이 차례로 뜨면 사이트마다 사용자가 로그인한다(신한카드 SOL페이 앱 인증 · 현대카드 간편번호 · 신한은행 공동인증서 —
스크립트는 그동안 누르기·입력·확인창 답 0, 뜨는 안내창은 사용자가 닫는다). 시작 때 찍히는 안내대로 카카오뱅크 앱 '거래내역 다운로드 → 받기'를
누르면 끝에 네이버 메일에서 첨부를 읽기 전용(IMAP)으로 가져와 암호를 풀고 검사한다. 검사를 다 통과한 파일만 `D:\household\uploads\내역\<받은 날 YYYYMMDD>[_N]\`
새 폴더 하나로 폴더째 옮긴다(받은 원본·summary는 `work\받기\<회차>\` — inbox에는 내역 파일만).
**받지 못한 출처**는 끝 표에 찍힌다 — 그 출처만 지금처럼 손으로 받아 **같은 새 폴더**에 받은 이름 그대로 넣는다: 현대카드(웹 이용내역 'xls 파일 다운받기') ·
신한카드(웹 '엑셀 저장') · 신한은행(거래내역조회 '파일저장' → '전체' → '엑셀저장') · 카카오뱅크(메일 첨부를 엑셀에서 열어 암호 없이 다른 이름으로 저장한
**`_v.xlsx`만** — 암호 원본은 넣지 않는다). 메일(IMAP)이 안 되면 `$FETCH --run --mail-only`: 첨부를 `D:\household\uploads\받은메일\`에 저장해 두면
거기서 수거한다(새 폴더 규칙은 같다).
비밀번호(네이버 애플리케이션 비밀번호·카카오뱅크 엑셀 암호)는 대화·명령줄·설정·로그 어디에도 두지 않는다 — 처음 한 번 사용자가 **Claude 앱 밖의 별도
cmd 창**에서 `cmdkey /generic:household/naver-imap /user:<네이버 아이디> /pass`와 `cmdkey /generic:household/kakaobank-xlsx /user:xlsx /pass`를
`/pass` 뒤에 값을 붙이지 않고 쳐서 뜨는 프롬프트에 입력한다(Windows 자격 증명 관리자 — `pc_fetch`는 읽기만). Claude는 대신 치지도 묻지도 않고,
그 직후 터미널 판을 읽지 않는다. 네이버 쪽은 2단계 인증 + 애플리케이션 비밀번호 + 메일 설정 › POP3/IMAP에서 IMAP 사용 켜기가 먼저다.

**지금 운영(2026-10-28 저녁 다시 켬 — 카카오뱅크는 메일 자동)**:
- 낮에 손 길로 꺼 두었던 운영 `rules\받기.toml`의 `[mail]` 블록을 같은 날 다시 켰다. 위 처음 한 번 설정(네이버 2단계 인증 · 애플리케이션 비밀번호 · IMAP 사용 · cmdkey 2건)은 끝났다.
  리허설(`--no-web` · 리허설 inbox)에서 자동으로 푼 파일과 손 `_v.xlsx`가 10-01~10-27 거래 89/89로 같았다(건수·합계·첫/끝 잔액·거래키).
- 카카오뱅크 첨부(agile · SHA1 · 128비트)는 맞는 암호에도 msoffcrypto의 암호 검증(verifier)·무결성(HMAC)이 실패한다. 그래서 `decrypt_xlsx`는 검증 없이 풀고,
  틀린 암호는 msoffcrypto `decrypt()`가 '풀린 결과가 zip 아님'으로 `InvalidKeyError`를 내 막는다(실측).
- 사용자는 앱에서 기간을 **`--run` 시작 때 찍히는 `[카카오뱅크]` 줄(또는 dry-run 표)의 버튼**(1개월·3개월·12개월 중 권장 시작을 덮는 가장 짧은 것 —
  셋 다 못 덮으면 직접 입력) 그대로 골라 '받기'만 누른다. 예금이자 같은 0원 행은 `계좌.toml` 카카오뱅크 `exclude_rows`가 빼고(scan 보고 '뺀 행' 열),
  카뱅이 '받지 못함'이고 사유가 '메일 없음'·'IMAP 연결 끊김'이면(메일이 늦게 옴) 손 하향 길 전에 `$FETCH --run --no-web`(메일만 다시 수거 · 웹 3곳은 열지 않음 ·
  새 폴더 규칙 같음)를 먼저 한다 — 그래도 안 되면 위 '받지 못한 출처'대로 손으로 `_v.xlsx`만 넣는다.
- 메일 수거는 시도(기본 4번 · 60초 간격)마다 **새로 접속**한다(2026-10-30 운영 실측 — 첫 조회 0통 뒤 60초 대기 중 서버가 연결을 끊어 둘째 조회가 abort →
  '받지 못함'. 곧바로 `--run --no-web`로는 받음). 접속 뒤 끊김은 그 시도만 버리고 다음 시도에서 새로 접속한다.
- 네이버 IMAP/SMTP는 90일 넘게 안 쓰면 '사용 안 함'으로 바뀐다 — 석 달 넘게 쉬었다가 IMAP 로그인이 실패하면 네이버 메일 환경설정에서 다시 켠다.
- 다시 손 길로 돌리려면 `[mail]` 블록을 주석으로 한다(그러면 `--run`은 웹 3곳만).

**`/q-household-run` 진입**: 인자 없이 부르면(또는 "가계부 업데이트") ① 받기부터 ⑤까지 한 번에 잇는다. 멈추는 곳은 로그인 · 카뱅 앱 '받기' · 받지 못한 출처 파일 넣기 · 확인 필요 답 · apply 확정 · "배포 <승인 코드>"뿐이다. "내역 정리"는 ② scan부터, "리포트만"은 ⑤(흐름 S0)부터 시작한다.

`FETCH="$PY /d/household/household-report/scripts/pc_fetch.py"` · `IMP="$PY /d/household/household-report/scripts/pc_import.py"`

| 단계 | 명령 | 하는 일 · 멈추는 곳 | 쓰기 |
|---|---|---|---|
| ① 받기 | `$FETCH` → `$FETCH --run` | 받는 기간 = 출처마다 `max(계좌.toml start_date, 지난 받은 끝 − 30일) ~ 오늘`(원장 period + inbox 파일 읽기만 · 현대카드 90일·신한카드 1년 상한에서 자르면 경고) → 사이트마다 사용자 로그인 → 허용 클릭·기간 칸만(금지 문구·요소 ≠ 1이면 누르지 않음) → 조회 직전·직후 '전체' 선택·요청 필터 값 대조 → 화면 '총 N건' = 조회 응답 건수 = 파일 행 = 파일 안 건수 → 출처 서명·식별 값·기간 검사(`계좌.toml` 재사용) → 카뱅 메일 수거(TLS 검증·읽기 전용) → 복호화 → 검사 → 통과분만 새 폴더(폴더째 이동 · 대상 있으면 실패). 한 출처 실패는 그 출처만(종료 2) | 회차 폴더 · inbox 새 폴더 1개 |
| ② scan | `$IMP scan` | `uploads\내역\` 모든 폴더를 매번 다시 읽는다 → 출처 서명(열 이름·값 꼴·역할·식별 값 넷 다) → 시작일(`계좌.toml` start_date) 전 거래 버림 → 카드대금·이체·취소·할부·수입 판정 → 손 행과 짝 → 분류 규칙 → 잔액 대조 → 회차 폴더에 `제안.csv`·`확인필요.xlsx`·`보고.md`·새 CSV 초안·`stamp.json`. 전후 md5로 쓰기 0을 확인한다 | 회차 폴더만 |
| ③ 답 | `확인필요.xlsx`의 노란 칸(처리·분류…)을 채우거나, 대화 답을 `$IMP answer --run <회차> --item <번호> --set 처리=추가 --set 대분류=…` | 지난 회차 답은 거래키로 이어받는다. 엑셀이 열려 있으면 answer는 멈춘다. 규칙 칸(`추가`·`할부` 답): **저장** = 다음부터 같은 가맹점 자동 · **매번** = 다음에도 묻되 이 답을 제안 칸에 채움('매번' 가맹점의 기본 — 답이 지난번과 다를 때만 규칙 한 줄) · **이번만** = 규칙 0. 그 밖의 값은 answer가 멈추고 apply는 '답 오류'로 막힌다 | 회차 폴더만 |
| ④ apply | `$IMP apply --run <회차>` (dry-run) → 사용자 확인 → `… --confirm` | 같은 프로세스에서 ②를 다시 계산해 입력 md5가 scan 때와 같을 때만. 배타 열기 → 백업 복사(md5) → 새 CSV 임시 → 쓰기 전 검사 → `uploads\가계부_<회차>.csv` → 옛 CSV를 `uploads\backup\<회차>\`로 → 분류규칙.csv → 원장 → `runs\log.md` 한 줄 | uploads · rules · log |
| ⑤ | `/q-household-run` ①부터 | uploads에 CSV 한 파일(새 판). review·배포 승인은 그대로 | — |

- **플랫폼 결제의 고정 금액**: `계좌.toml` `[[platform_fixed]]`(가맹점 이름 일부 · 카드 · 금액 · 분류 값)에 가맹점·카드·청구 금액이 정확히 맞는
  카드 결제(일시불)만 묻지 않고 그 값으로 자동 행을 쓴다(예: 쿠팡 월 회비). 1원이라도 다르면 지금처럼 묻는다. 분류 값이 CSV에 없는 이름이면
  자동 행 없이 묻고 메모 'platform_fixed 이름 확인'. 분류규칙.csv에는 아무것도 안 생긴다.
- **확인 필요로만 올라오는 것**(자동 행 0): 규칙 없는 가맹점 · '매번' 가맹점 · 플랫폼 결제(규칙 저장 불가 — 위 고정 금액 칸은 예외) · 같은 키 다른 원문 · 입금(항목명) · 해외 ·
  손 행과 날짜 차·모호·의심·경로 다름·날짜 오타 · 취소(원거래가 CSV에 있음 — 삭제? · 원거래가 취소 시각보다 나중이면 짝 없는 취소) ·
  할부(손 행 전액 1행 vs 월별) · 본인 이름처럼 둘 이상 계좌에 걸리는 이체(카드값 이체 창 `[[card_transfer]]` 안이면 제안 `카드대금(카드명)` —
  그 답은 행 0·원장 '카드대금 이체'·카드대금 대조의 관측 출금. answer로 적을 때는 괄호 때문에 `--set '처리=카드대금(현대카드)'`처럼
  따옴표) · `auto_only_with_income` 쌍인데 같은 날 같은 금액 자동 수입이 없는 이체
  (제안 '제외' — 답 '이체'면 이체 한 행) · 파일이 있는 계좌에서 온 입금 · 내역에 없는 CSV 행 · 사라진 행 · 카드대금 출금이 없는 달.
- **답하지 않은 항목**은 원장에 '보류'로 남아 다음 회차(파일이 없어도)에 다시 올라온다. apply를 막지 않는다.
- **커플통장 선이체**(생활비 → 커플통장으로 다음 달 몫을 미리 보냄): 리포트는 이체 행 비고 `N월분`을 보고 그 달 실질 지출로 센다
  (저장소 SKILL.md "내여자 실질 지출"). 지금 `answer`·`확인필요.xlsx`는 이체 행 비고를 빈칸만 받는다(`--set 비고=N월분`은 답 오류) —
  비고 없이 답하면 보낸 달로 셈해진다. apply 뒤 CSV 비고를 손으로 고치면 다음 scan이 그 거래를 '사라진 행'으로 다시 묻는다.
- **apply를 막는 것**(풀려면 사유와 함께 명시): 출처 실패 → `--skip <출처|파일>`(실패에만) · 파일 기간 공백 → `--ack-gap <출처> <사유>` ·
  카드대금 ≠ 청구 주기 카드 행 합계(또는 이용기간을 파일이 다 덮지 않음) → `--ack-card <카드> <사유>` · 잔액 대조 잔차가 기준 차액과 다름
  (첫 회차는 기준 차액 정하기) → `--ack-balance <계좌> <사유>` · 쓰기 전 검사의 경고 종류별 건수 증가 → `--ack-warning <종류> <사유>` ·
  답 오류 · 자동·짝 행 거래가 파일에서 사라짐/바뀜(답 필요).
  - 잔액 공식을 고친 코드로 처음 scan하면 잔차가 기준 차액과 고친 항만큼 다르다(예: 통장 밖 칸을 칸 이름마다 한 번만 빼게 고친 판).
    `보고.md` 잔액 대조 절에서 그 차이가 바뀐 항과 같은지 확인한 뒤, 다음 apply에 `--ack-balance <계좌> <사유>`를 붙여 새 기준 차액을 정한다
    (기준만 바꾸려고 따로 apply하지 않는다). 차이가 바뀐 항과 다르면 ack하지 않고 그 차이부터 본다.
- 같은 날 apply한다(scan 날과 다르면 scan부터). 가장 최근 scan 회차만 apply한다.
- 실 내역·rules·원장·회차 폴더·백업은 모두 clone 밖이다. `pc_import.py`는 그 경로가 clone 안이면 시작하지 않는다.
  저장소 시험 fixture는 텍스트만이고, `pc_run.py`의 코드 회차 검사가 커밋의 줄을 uploads·rules에서 뽑은 실값과 대조한다.

**처음 한 번(받기)**: `rules\받기.toml`(예시 `pc/import/받기.toml.example` — 사이트마다 허용 동작·요소 id·조회 요청 칸·화면 대조 글자,
메일 조건, 폴더) · 위 cmdkey 2건(사용자가 Claude 앱 밖 창에서) · `msoffcrypto-tool`(카뱅 복호화 — 설치는 사용자에게 먼저 묻는다).
현대카드는 이 PC에 보안 프로그램(nProtect Online Security)이 설치돼 있어야 한다.

**처음 한 번**: `rules\계좌.toml`을 실물 파일을 보고 만든다(예시 `pc/import/계좌.toml.example` — 출처마다 열 이름·값 서명·역할·식별 값,
이체 적요·카드대금 적요·급여 패턴, 카드 청구 계좌·결제일·이용기간). `분류규칙.csv`·`원장.sqlite`는 첫 apply가 만든다.
통장 밖 칸(세이프박스 등) `[[internal]]`은 같은 칸을 적요 칸마다 여러 줄(같은 `name`)로 둘 수 있다 — 잔액 대조는 칸 이름마다 한 번
(`[[internal_balance]]` 시작 잔액 + 그 뒤 그 칸 줄에 맞는 이동).

**리허설**: `$PY <개발 clone>/scripts/pc_import.py --rehearsal init` → `… --rehearsal scan` → `… --rehearsal apply --run <회차> --confirm`.
내역 파일은 `uploads\내역\`에서 읽기만 하고, CSV·rules·원장·회차 폴더는 `work-rehearsal\내역\` 사본에만 쓴다.
받기 리허설은 `paths.inbox_root`만 `work-rehearsal\받기\inbox`로 바꾼 설정 사본으로 `$PY <개발 clone>/scripts/pc_fetch.py --config <사본> --run`
(받는 기간은 운영 inbox·원장을 읽기만 해서 그대로 계산) → `… compare --a <리허설 새 폴더> --b <손으로 받은 폴더들>`로 출처별 거래 건수·합계·잔액·거래키를 대조한다.
