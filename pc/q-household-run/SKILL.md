---
name: q-household-run
description: 이 PC(D:\household) Claude Code 탭에서 가계부 리포트를 재계산·확인·배포한다. "가계부 업데이트", "가계부 리포트", "가계부 CSV 넣어줘", "가계부 배포", "household report", "가계부 갱신", "리포트 다시 만들어줘" 같은 말이 나오거나 가계부 CSV를 다룰 때 반드시 이 스킬을 쓴다. 금융사 내역 파일을 CSV 행으로 정리하는 "내역 정리"("카드 내역 넣었어", "통장 내역 정리")도 이 스킬이다. 이 폴더에서는 설치본 household-report-update(웹용) 대신 이 스킬이다.
---

# 가계부 리포트 — 이 PC Code 탭 (/q-household-run)

정본은 저장소 `pc/q-household-run/SKILL.md`, 절차 설명은 `pc/code-tab.md`. 웹(claude.ai)은 저장소 SKILL.md 그대로다.

## 지키는 것

- 설치본 `household-report-update` 스킬을 부르지 않는다. codeload로 받지 않고 `/home/claude`·`/mnt/…` 경로를 만들지 않는다.
- 토큰을 묻거나 출력·저장하지 않는다. `git credential fill`을 치지 않는다. `scripts/deploy.py`(토큰 API)로 올리지 않는다.
- git 쓰기(커밋·push)는 `pc_run.py`만 한다. 손으로 `git add -A`·`commit -a`·`push`·`--force`를 쓰지 않는다. `reset --hard`는 아래 안전망에서 사용자가 버리라고 답했을 때만.
- `D:\household` 루트에서 `git init`·원격 추가 금지. 실 CSV·bundle·report는 clone 밖(`uploads\`·`work\`)에만 있다.
- 파이썬은 `$PY`만(`python3` 금지). `stamp.json`·`deploy.json`을 손으로 쓰거나 고치지 않는다.
- 배포는 사용자가 **"배포 <승인 코드>"**라고 답한 뒤에만. 승인 코드는 `review`가 찍은 것만 쓴다(md5를 직접 계산해 묻지 않는다).

## 매 Bash 호출 첫머리

```bash
export PY=/d/household/.venv/Scripts/python.exe PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never; unset GIT_ASKPASS SSH_ASKPASS
RUN="$PY /d/household/household-report/scripts/pc_run.py"
IMP="$PY /d/household/household-report/scripts/pc_import.py"
```

**리허설** — 사용자가 첫 말에 "리허설"과 개발 clone 경로를 줄 때만: `RUN="$PY <개발 clone>/scripts/pc_run.py --rehearsal"`(경로는 `/d/...` 꼴). 출력 첫 줄이 `[리허설]`이어야 한다. 리허설은 ④ dry-run까지다: 승인 질문은 보이기만 하고, `deploy`를 `--confirm` 없이 돌린 뒤 끝낸다. 운영 회차에서는 쓰지 않는다.

## 0. 처음 한 번 — 운영 clone에 `scripts/pc_run.py`가 없을 때(병합 직후)

운영 clone이 origin/main보다 **뒤에만** 있을 때만 빨리 감는다.

```bash
cd /d/household/household-report && git status --porcelain && git fetch origin && git rev-list --left-right --count HEAD...origin/main
```

status 줄이 하나도 없고 숫자가 `0 N`(N ≥ 1)이면 `git merge --ff-only origin/main` → `$RUN sync`(exclude 줄) → 1로. 그 밖(앞섬·갈라짐·바뀐 파일)이면 멈추고 사용자에게 보인다.

## 내역 정리 — 사용자가 "내역 정리"(내역 파일을 넣었다)고 할 때만, ①의 앞에

절차 설명은 `pc/code-tab.md` 12절. 금융사 사이트를 열거나 로그인·내려받기를 하지 않는다(파일은 사용자가 받아 넣는다).
비밀번호(카카오뱅크 암호 엑셀 포함)를 묻거나 받지 않는다 — 엑셀에서 풀어 저장한 파일만 쓴다.

1. `$IMP scan` — 파일·출처별 건수·확인 필요(종류별 첫 줄들)·카드대금 대조·잔액 대조·apply 막힘을 대화로 전한다.
   `[FAIL]`(설정 없음·CSV 둘 이상 등)이면 멈추고 그 줄을 보인다.
2. 확인 필요를 묻는다(종류별로 묶어서). 사용자가 `확인필요.xlsx`를 채우면 그 파일을, 대화로 답하면
   `$IMP answer --run <회차> --item <번호> --set 처리=… [--set 대분류=…]`로 적는다(열린 엑셀이 있으면 닫아 달라고 한다).
   새 가맹점을 물을 때는 규칙 칸(저장/매번/이번만)도 함께 묻는다 — 같은 가게가 데이트비용·개인 둘 다 되는 곳(카페·식당·편의점 등)은 '매번'을 권한다.
   답하지 않은 항목은 보류로 남아 다음에 다시 올라온다 — 다 답하지 않아도 된다.
   커플통장 선이체(다음 달 몫을 미리 보냄)는 몇 월분인지 묻는다 — 그 이체 행 비고가 `N월분`이어야 리포트가 그 달 지출로 센다. 지금 `answer`는 이체 행 비고를 빈칸만 받아 `--set 비고=N월분`이 답 오류다(내여자 실질 지출 회차 이월) — 비고 없이 답하고, 그 행이 보낸 달로 셈해진다고 사용자에게 알린다.
3. `$IMP apply --run <회차>`(dry-run) — 새 행·지울 행·규칙 추가·막힘을 보인다. 막힘(`--skip`·`--ack-*`)은 사용자에게 사유를 받아서만 붙인다.
4. 사용자가 적용하라고 하면 `$IMP apply --run <회차> [같은 --skip·--ack-*] --confirm` → 아래 흐름 1(S0)부터.
   CSV를 고치고 싶다고 하면 scan부터 다시(새 회차).

## 흐름

1. **S0** `$RUN check` — `[FAIL]`이 있으면 멈추고 그 줄을 보인다. "뒤에만 있다"면 `$RUN sync` 뒤 다시 check. `[주의]`는 알리고 계속한다.
2. **① CSV** — `D:\household\uploads\`의 CSV. 하나면 그것, 여럿이면 이름·수정 시각을 보이고 묻는다.
3. **② build** `$RUN build --csv /d/household/uploads/<파일>` — 마지막에 찍히는 회차 이름을 쓴다. `[FAIL]`이면 멈춘다.
4. **③ review** `$RUN review --run <회차>`
   - `[경고]` 전부 · 종료 추정 · 이체 · 커플통장 정산 줄(받을 금액 · 이체 행 예시 — 찍힌 그대로) · 기준판 대비 변동을 대화로 전한다.
   - exit 2 `[멈춤]`: 질문을 그대로 묻고 답을 기다린다.
     - 이체 질문에 "그대로 진행" → `$RUN review --run <회차> --ack-transfer`
     - CSV를 고친다·새 CSV → 사용자가 uploads에 둔 뒤 ①부터(새 회차 · 새 승인 코드)
     - 종료 항목·시작 잔고 같은 코드 수정 → 코드 회차(`pc/code-tab.md`) — 사용자와 따로 정한다
   - exit 0: "승인 질문" 블록을 그대로 보이고 답을 기다린다.
5. **④⑤ deploy** — 답이 정확히 "배포 <승인 코드>"일 때만:
   `$RUN deploy --run <회차> --confirm <승인 코드>` (review에 `--ack-transfer`를 붙였으면 여기도 붙인다).
   `[FAIL]`이면 멈추고 보인다 — 다시 시도·push하지 않는다. 답이 다르면 배포하지 않는다.
6. **⑥ live** `$RUN live --run <회차>` — Bash `run_in_background`로 돌리고 끝나기를 기다린다(push 뒤 최대 약 12분).
   exit 0 반영 확인 · 3 Pages 성공·CDN 반영 미확인 · 1 실패.
7. **⑦ record** `$RUN record --run <회차>` → 결과 한 줄과 https://leekwanbeom.github.io/household-report/ 를 보고한다.
   "배포 완료"는 record가 그렇게 찍었을 때만 쓴다.

## 막히면

- check "push되지 않은 배포 커밋"(죽음 안전망): 커밋 목록을 보이고 묻는다. 버리라고 하면
  `git -C /d/household/household-report reset --hard origin/main` 뒤 ②부터.
- deploy가 push 결과를 모르거나 거절: 다시 push하지 않는다. `$RUN check`로 원격·지난 회차를 대조해 보고한다.
- 되돌리기는 사용자가 요청할 때만 — `pc/code-tab.md` "되돌리기".
