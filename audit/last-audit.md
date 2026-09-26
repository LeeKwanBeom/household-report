# 점검 기준선

점검일 2026-09-26 (정기 점검 1회차, 점검표 v1) / **결함 16건 · 개선안 5건 · 인용불가 0건** / 직전 기준선(2026-09-06) 대비: 해결 0 · 미해결 7 · 근거없음 0 · 신규 9

**2026-09-26 수정 회차(같은 날, 별도 세션):** 사용자 채택 — 결함 1~16 전부 · 개선안 1~5 전부 · 점검표 개정안 1~12 전부(→ audit/checklist.md v2). 전부 반영했고 **검증은 다음 세션**에서 받는다. 상세는 아래 "수정 기록". 이월 항목 없음.

점검 대상
- 커밋: `5bf65bc` (2026-09-24 "리포트 갱신", main HEAD). codeload tarball에는 .git이 없어 해시는 GitHub API(인증)로 조회.
- 파일(ls -R, .git 제외): SKILL.md · audit/last-audit.md · build.py · pipeline.py · scripts/deploy.py · scripts/detect_ended.py · index.html · sw.js · manifest.json · favicon.svg · favicon.ico · apple-touch-icon.png · icon-192.png · icon-512.png · icon-maskable-512.png. **install/SKILL.md·audit/checklist.md·README 없음.**
- 행수(wc -l): SKILL.md 273 · pipeline.py 706 · build.py 1479 · scripts/deploy.py 184 · scripts/detect_ended.py 100 · audit/last-audit.md(구) 82 · sw.js 31 · manifest.json 17 · index.html 13000. 아래 "줄"은 전부 이 행수 기준.
- 설치본 부트스트랩: `/mnt/skills/plugins/household-report-update/SKILL.md` 49행(wc -l), md5 `b411b109…` — 점검표 기록과 일치. 절차·명령어·규칙 없음(얇음 유지).
- 실 CSV: 가계부3.csv 640행, 2026-01-01 ~ 2026-09-24 (최종일은 점검일 2일 전). 구분 지출 560 · 수입 68 · 이체 12, 고정 표기 303행.
- 실행한 명령: `python3 pipeline.py 가계부3.csv` → `python3 build.py` → `python3 scripts/detect_ended.py 가계부3.csv`, `deploy.validate_html`(로컬만). 실험은 전부 `/home/claude/exp/` 사본으로, 원본·규칙 상수 미변경.
- 배포 여부 = **없음**(index.html 미업로드). 1차 커밋은 audit/last-audit.md만.

## 직전 기준선 판정

| 항목 | 판정 | 근거(지금 원문) |
|---|---|---|
| #1 카드명을 결제수단에 적으면 잔고에서 조용히 사라짐 | **미해결** | pipeline.py 402-405 `card_tags = set(CARD_TARGETS)` … `if v and v not in known_accounts and v not in card_tags`. 실측: 결제수단="현대카드" 합성 90만원 행 → 총지출 +90만, 생활비 변화 0, 경고 없음 |
| #2 커플통장이 TOP20 밖으로 밀리면 화면에 "0원" | **미해결** | build.py 59-61 `_couple = next((x for x in bundle['subcategory_ranking'] if x['소분류'] == '커플통장'), None)` / 1315 `<li>내여자 / 커플통장 월평균 {won(round(_couple_avg))}원`. 실측: 커플통장 지출 행 28건 중 1건만 남긴 사본 → s13 "월평균 0원" |
| #3 pipeline 경고가 리포트 화면에 안 올라감 | **미해결** | build.py에서 `diagnostics`·`shinhan_unmatched` grep 0회. build.py 1314-1317 `<ol class="action-list">` 안은 하드코딩 2줄뿐 |
| #4 13개월을 넘으면 월 라벨이 중복 | **미해결** | pipeline.py 219 `month_labels = [f"{int(m.split('-')[1])}월" for m in months]`. 실측: 2025-08·09 + 2026-01~09 사본(11개월) → labels에 '8월''9월' 각 2회. 조건은 "13개월 초과"가 아니라 "연도가 다른 같은 월 번호" |
| #5 세부내용에 `};`가 들어가면 배포 검증이 막힘 | **미해결** | scripts/deploy.py 94 `m = re.search(r"const DATA = (\{.*?\});", c, re.S)`. 실측: 세부내용 "실험 };" 사본 → `임베드 JSON 파싱 실패: Unterminated string` (안전 실패, 오진단) |
| #6 build.py에 남은 특정 시점 서술 | **미해결** | build.py 1316 `<li>실업급여·퇴직금 등 일시 수입 유입 시 소비 쏠림 방지용 배정 규칙(예: N% 저축 우선) 검토</li>` |
| #7 죽은 코드 savings_trend_svg | **미해결** | build.py 509 `def savings_trend_svg():` — 파일 내 호출 0회 |
| (통과) build.py 연도 하드코딩 없음 | 통과 | `20[0-9][0-9]` 일치: 22(docstring)·92(주석) 2곳뿐 |
| (통과) ACCOUNTS_START 값·기준일 SKILL.md와 일치 | 통과(대조 대상 소멸) | SKILL.md 90-91 "여기에 숫자를 옮겨 적지 않는다" — 이제 pipeline.py 26-48이 유일 원본 |
| (통과) SUBSCRIPTION_ALIASES/TOP_EXPENSE_EXCLUSIONS/TARGET_COMPARISON_START 사본 없음 | 통과 | build.py에서 '회생'·'가족 용돈'·'400000'·'1000000'·'이모티콘 월구독'·'2026-07' 리터럴 0회, `top_expense_exclusions`·`card_targets`는 bundle에서 읽음(388-389, 138) |
| (통과) 03번 날짜 오름차순 | 통과 | pipeline.py 488 `.sort_values("날짜")`, 산출 DATA card_category_detail 두 카드 모두 정렬 확인 |
| (통과) bundle `[0]`/`[1]` 길이 가드 | 통과 | build.py 335-341 income_summary, 365 _top_first_label, 372 _fv_top_label, 381 _mom_range_label, 269 fixed_vs_target_rows 전부 빈 값 분기 있음 |
| (통과) sw.js network-first | 통과(코드 읽기만) | sw.js 20-30 `fetch(e.request).then(...).catch(() => caches.match(e.request))`. github.io는 이 환경 허용 도메인이 아니라 실측 불가 |
| (통과) detect_ended.py가 pipeline.py에서 import | 통과 | scripts/detect_ended.py 19-23 `from pipeline import (ENDED_FIXED_ITEMS, MANUALLY_EXCLUDED_FIXED_ITEMS, load_csv)`. 실 CSV로 두 쪽 고정 항목 키 집합 33개 동일 확인. 단 키 생성 로직(28-57)은 pipeline.py 333-351의 사본 |

문서↔코드 속도 차이(양방향):
- 코드만 앞섬: warnings_for() 9종(pipeline.py 629-659) vs SKILL.md 5단계 5종(113-115) / HIDDEN_ACCOUNTS(pipeline.py 50-53, 2026-09-10) SKILL.md 언급 0 / 커플통장·주식잔고 계좌화(pipeline.py 35-47, 2026-09-18)에 3단계 이체 확인(SKILL.md 57-72)이 안 따라옴.
- 문서만 앞섬: SKILL.md 46 "컬럼이 다르면 … 중단한다"(코드는 빈 컬럼 생성, pipeline.py 174-176) / SKILL.md 209-210 리모트뷰 안내(코드 동작과 다름, 결함 #7).

## 결함

| # | 심각도 | 파일 | 줄 | 문제 원문(그대로) | 실측/추론 | 왜 틀렸는지 | 수정 방향 | 작업 경로 |
|---|---|---|---|---|---|---|---|---|
| 1 | 상 | pipeline.py | 402-405 | `card_tags = set(CARD_TARGETS)` / `if v and v not in known_accounts and v not in card_tags}` | 실측 | 결제수단이 카드명이거나 **빈칸**인 수입·지출 행은 어느 계좌에도 안 붙어 잔고에서 빠지는데 경고 0. 합성 90만원 행(결제수단="현대카드") → 총지출 +90만·생활비 0·경고 없음. 빈칸("")도 동일. 실 CSV는 8/31 이후 94행 전부 결제수단 기재라 지금은 안 틀림(빈칸 545건은 전부 계좌 추적 전 행). 09-06 #1 + 빈칸 신규 | 카드 태그 제외를 풀어 "비고에 있어야 할 값" 경고로 바꾸고, 빈칸은 `min(as_of)` 이후 행에 한해 건수 경고 | 저장소 pipeline.py |
| 2 | 상 | pipeline.py | 174-176 | `for c in TEXT_COLS:` / `if c not in df.columns:` / `df[c] = ""` | 실측 | 필수 컬럼이 없어도 빈 컬럼을 만들어 진행. 결제수단 컬럼 삭제 사본 → "합계 일치", 경고는 평소 2건, 생활비 잔고가 9월 지출 전부 미차감으로 산출(이체는 소분류로 붙어 "4건 반영"). 고정여부 삭제 → 예상 고정지출 항목 0(stale_rule_keys 경고 7건이 간접 신호). SKILL.md 46 "컬럼이 다르면 무엇이 빠졌는지 알리고 중단한다"는 수동 절차라 코드가 안 막음 | load_csv에서 9개 컬럼 전부 확인, 없으면 ValueError로 중단 | 저장소 pipeline.py |
| 3 | 중상 | pipeline.py | 218 / 467-468 | `months = sorted(core["월"].unique())` / `latest_month = months[-1]` / `latest_date = df["날짜"].max()` | 실측 | 미래 날짜 행 1개(연·월 오타)로 최신 월이 밀린다. 2026-12-31 합성 행 → months에 2026-12 추가, 카드 실적 월 2026-12, 업데이트일 "12월 31일", 완성월 9개(9월이 완성 취급), 경고 없음 | 실행일보다 뒤인 날짜 행 수를 경고(또는 중단) | 저장소 pipeline.py |
| 4 | 중 | pipeline.py | 190-193 / 200-203 | `print(f"  [주의] 날짜를 해석할 수 없는 행 {bad_dates}건을 건너뜁니다.")` / `print(f"  [주의] 알 수 없는 '구분' 값: {unknown}")` | 실측 | [주의]는 로딩 단계 출력이고 warnings_for()·SKILL.md 경고 목록 어디에도 없어 "경고 없음"으로 끝날 수 있다. 날짜 8종 변형(2026/09/20·2026.09.20·20260920·09/20/2026·20/09/2026·"2026-09-20 14:00"·"9월 20일") 중 `2026-9-20` 외 7종이 [주의]만 남기고 탈락(총지출 변화 0). 구분 "지 출"·"支出"·"지출비" 행은 총지출·잔고 모두에서 제외 | 두 [주의]를 diagnostics에 넣어 [경고] 블록으로 출력하고 SKILL.md 5단계 목록에 추가 | 저장소 pipeline.py·SKILL.md |
| 5 | 중 | build.py | 59-61 / 1315 | `_couple = next((x for x in bundle['subcategory_ranking']` … `None)` / `<li>내여자 / 커플통장 월평균 {won(round(_couple_avg))}원 — 지출 상위권 고정 항목이라 적정 수준인지 점검</li>` | 실측 | 09-06 #2 그대로(사본 재현 "0원"). 지금은 TOP20 중 2위지만, 2026-09부터 커플통장 40만원이 지출이 아니라 이체(SKILL.md 48-50, pipeline.py 35-36)라 이 소분류 합계는 더 안 늘고 순위만 내려간다 → 밀리는 것은 시간문제. "지출 상위권 고정 항목" 전제도 낡음 | pipeline에서 계산해 bundle로 넘기거나 이 줄 삭제 | 저장소 build.py(+pipeline.py) |
| 6 | 중 | build.py | 1313-1318 | `<ol class="action-list">` … 하드코딩 2 `<li>` (1315-1316) | 실측 | 09-06 #3·#6 그대로. `diagnostics`·`shinhan_unmatched` build.py 참조 0회. 실 CSV 경고 3건(아래 실측 기록)이 화면 어디에도 없음 | warnings_for() 결과를 bundle에 실어 s13에 렌더, 1316 시점 서술 삭제 | 저장소 pipeline.py·build.py |
| 7 | 중(문서) | SKILL.md | 209-210 | `종료 추정에 다시 걸리면 CSV의` / `리모트뷰 행에 `고정` 표기가 빠진 것이니 목록에 넣지 말고 사용자에게 알린다.` | 실측 | 실 CSV 9월 리모트뷰 행에 `고정` 있음(1건)인데도 detect_ended가 [확인 필요]로 올림(마지막 2026-06, 2개월 공백). 종료 추정은 완성월(1~8월)만 보고 진행 중인 달은 반영 안 함(detect_ended.py 35-37 `completed_months = months[:-1]`). 이 문장대로 안내하면 사용자가 없는 오류를 찾게 됨. 10월 CSV부터는 자연 해소 | "진행 중인 달의 결제는 다음 달 CSV부터 반영되므로 그때까지 걸리는 건 무시"로 고침 | 저장소 SKILL.md |
| 8 | 중(문서) | SKILL.md | 57-59 / 70-72 | `> 이체 건 확인됐어요: 신한은행 680,000원 · 청년미래적금 500,000원` / `**이체 건이 없거나 한쪽만 있으면** — 여기서 멈추고 사용자에게 묻는다:` | 실측 | 확인 대상이 2건인데 pipeline.py 43 desc "매달 1일 생활비에서 40만원 이체"(커플통장)·47(주식잔고)가 추가돼 있다. 커플통장 월 이체가 빠지면 이 절차가 못 잡는다(10월부터). 기대 이체 목록이 코드에 없다는 82행 서술은 지금도 사실 — 이체 전부 삭제 사본은 신한 잔고 음수 경고로 간접 감지되지만 부분 누락은 못 잡음 | 3단계 확인 대상에 커플통장 추가(최소), 코드화는 개선안 #2 | 저장소 SKILL.md |
| 9 | 중하 | pipeline.py | 595-615 | `def validate(bundle):` … `if v != kpi_exp:` | 실측 | 6개 검사 전부 0건 가드 없음. 지출·수입이 전부 0인 bundle → `errors == []`("합계 일치"). 항등식이라 빈 데이터도 통과 | total_expense·total_income이 0이면 실패 처리 | 저장소 pipeline.py |
| 10 | 중하 | pipeline.py | 152-167 | `negative = s.startswith("(") and s.endswith(")")` … `return -v if negative else v` | 실측 | 음수(-x, (x))·0·빈칸 금액이 경고 없이 통과. 지출 "-900,000" 합성 행 → 총지출 -90만, 생활비 +90만, 경고 없음 | 음수·0 금액 행 건수를 경고 | 저장소 pipeline.py |
| 11 | 하 | scripts/deploy.py | 166-168 / 4-6 | `for name in ("SKILL.md", "pipeline.py", "build.py",` / `"scripts/detect_ended.py", "scripts/deploy.py",` / `"audit/last-audit.md"):` | 실측 | 목록 6개 하드코딩. 이번 회차에 생기는 audit/checklist.md·install/SKILL.md는 `--with-code`로 안 올라감. docstring(4-6)에는 `--code-only` 사용법도 없음 | 두 파일 추가 또는 자동 스캔(개선안 #4) | 저장소 scripts/deploy.py |
| 12 | 하 | 저장소 install/ | — | (파일 없음 — ls -R 실측) | 실측 | 부트스트랩 원본 부재. 설치본이 저장소 밖에만 있어 버전 관리·대조가 안 됨 | 설치본(49행, md5 b411b109) 그대로 install/SKILL.md로 | 저장소 install/SKILL.md (내용까지 바꾸면 재업로드) |
| 13 | 하 | pipeline.py | 219 | `month_labels = [f"{int(m.split('-')[1])}월" for m in months]` | 실측 | 09-06 #4 그대로. 연도 없는 라벨을 히트맵·추이·원장 탭이 공용. 2025-08·09 + 2026-01~09 사본에서 '8월''9월' 중복. 2027-01 CSV(13개월)부터 실제 발생 | 연도가 바뀌는 첫 달에만 "27.1월"처럼 연도 접두 | 저장소 pipeline.py |
| 14 | 하 | scripts/deploy.py | 94 | `m = re.search(r"const DATA = (\{.*?\});", c, re.S)` | 실측 | 09-06 #5 그대로. 세부내용 "실험 };" 사본 → 오진단(안전 실패) | build.py가 DATA를 한 줄로 쓰므로 `const DATA = (.*?);\n`로 | 저장소 scripts/deploy.py |
| 15 | 하(문서) | SKILL.md | 113-115 / (없음) | `계좌에 안 붙는 결제수단, 데이터에서 한 번도 안 걸린 제외 규칙 키,` / `기준일 이후 거래가 없는 계좌, 실적이 0인 카드, 신한 고정비 목록에 없는 출금.` | 실측 | 경고 5종만 적혀 있고 코드는 9종(pipeline.py 629-659): 이체 상대 미인식·완성월 없음·잔고 음수·목표 비교 달 없음 4종 누락. HIDDEN_ACCOUNTS(pipeline.py 50-53, 커플통장 화면 비표시·총잔고 제외)는 SKILL.md에 언급 0 | 5단계 목록 9종으로, 리포트 구조 01번에 숨김 계좌 규칙 한 줄 | 저장소 SKILL.md |
| 16 | 최하 | build.py | 509 | `def savings_trend_svg():` | 실측 | 09-06 #7 그대로. 호출 0회 | 삭제 | 저장소 build.py |

결함 1~16 상태(2026-09-26 수정 회차): **전부 수정됨 — 검증 대기.** 결함 3·5·12·13은 원 제안과 다르게 구현했다("수정 기록" 참고).

## 개선안 (최대 5)

| # | 내용 | 이유 | 우선순위 |
|---|---|---|---|
| 1 | 회귀 테스트: 익명화 소형 fixture(20~30행, 이체·고정·카드 태그 포함) + `tests/test_pipeline.py`(잔고·월별 합·예상 고정지출·validate 파괴 6종·경고 발생) | tests/·fixtures 없음. 이번 회차 실측 스크립트를 그대로 옮기면 됨. 이후 수정 회차의 "의도한 차이만 있는지" 검증이 자동화됨 | 1 |
| 2 | 이체 누락 자동 검증: pipeline.py에 `EXPECTED_MONTHLY_TRANSFERS = [(출금, 입금, 금액), …]` 상수(ACCOUNTS_START desc의 "매달 1일 …" 3건이 원본) 두고, 최신 월·직전 완성 월에 없으면 경고 | SKILL.md 82 "이 확인이 유일한 방어선" — 사람이 잊으면 방어선 없음. 부분 누락은 지금 어떤 경고에도 안 걸림(실측) | 2 |
| 3 | 입력 위생 검사 묶음: 완전 중복 행 경고(실 CSV 1쌍 존재) · 미등록 `비고` 태그 경고("현대 카드" 실측: 실적 미반영·경고 없음) · 빈 CSV/이체만/cp949 입력에 원인 메시지(지금은 TypeError·IndexError·UnicodeDecodeError 트레이스백) · 세부내용 HTML 이스케이프(실 CSV '>' 5건 '&' 1건이 그대로 출력) | 결함 3·4·10과 같은 함수(load_csv·warnings_for)에서 한 번에 처리 가능 | 3 |
| 4 | deploy.py: 내용 같으면 PUT 건너뛰기(빈 커밋 방지) · 파일 목록 자동 스캔(hometax push.py `_scan` 방식) · 토큰을 환경변수(`GITHUB_TOKEN`)로도 받기 | 09-18 커밋 4건(2c41f4c·4ff63d1·ced4fc2·a1a0744) API stats total 0 확인 — 같은 파일이 두 번 빈 커밋. 토큰이 argv에 있으면 프로세스 목록·대화 로그에 남음 | 4 |
| 5 | 배포 게이트·확인: 저장소 index.html의 DATA와 새 bundle의 계좌 잔고 차이가 임계치(예: 계좌별 ±300만원)를 넘으면 중단 후 확인 · push 뒤 `commits?per_page=1` sha가 바뀌었는지 확인 | 조용히 틀린 리포트가 배포되는 마지막 관문. github.io는 이 환경에서 접근 불가라 Pages 반영 확인은 커밋 sha까지만 | 5 |

개선안 1~5 상태(2026-09-26 수정 회차): **전부 구현됨 — 검증 대기.** 개선안 3·5는 사용자 지시대로 "경고/확인"이지 중단이 아니다.

## validate·validate_html 검사 생존 확인

| 검사 | 파괴 방법 | 결과 |
|---|---|---|
| validate: 월별 추이 지출 합 | bundle `monthly.expense[0]` +1 (메모리 변조) | FAIL `월별 추이 지출 합(…) != KPI 총지출(…)` |
| validate: 대분류 합 | `category[0].합계` +1 | FAIL |
| validate: 고정/변동 합 | `fixed_variable[0].고정` +1 | FAIL |
| validate: 히트맵 합 | `heatmap.matrix[0][0]` +1 | FAIL |
| validate: 월별 추이 수입 합 | `monthly.income[0]` +1 | FAIL |
| validate: 수입원 합 | `income_breakdown[0].합계` +1 | FAIL |
| validate: 0건 가드 | 지출·수입 전부 0인 bundle | **통과(결함 #9)** |
| validate_html: 크기 | 앞 5,000자만 | FAIL `HTML이 비정상적으로 작습니다` |
| validate_html: 임베드 JSON | `const DATA = {` 뒤에 `,` 삽입 | FAIL `임베드 JSON 파싱 실패` |
| validate_html: script 문법 | `function fmtMan(v) {` → `fmtMan(v {` | FAIL `script 블록 1 문법 오류` (node 있음) |
| validate_html: 자리표시자 | `{REF:s2}` / `{won(1)}` 삽입 | FAIL 각각 `치환되지 않은 자리표시자` |
| validate_html: non-greedy | 세부내용 "실험 };" 사본 빌드 | FAIL(오진단, 결함 #14) |
| validate_html: `</script>` in 세부내용 | 세부내용에 `</script><b>x</b>` | FAIL(script 문법) — 안전 실패 |
| build.py renumber_sections | 실 CSV | 섹션 14개, 번호 01~14 등장 순, `{REF:`·`{won(`·`{bundle[` 잔존 0 |

## 실행 실측 기록

- 실 CSV 640행(지출 560·수입 68·이체 12), 2026-01-01~2026-09-24. pipeline: 정합성 통과, **경고 3건** — 청년미래적금·대여금 "기준일 이후 거래 0건"(청년미래적금은 9/1 이체가 as_of 9/3 이전이라 9월 한정 소음), **커플통장 잔고 음수**. 커플통장 음수는 09-24 배포본(5bf65bc index.html)에도 이미 있었다(DATA 동일). 9월 커플통장 관련 이체: 생활비→커플통장 2건, 커플통장→생활비 4건.
- build: 섹션 14개, id 순서 s1·s2·s3·s5·s6·s-mom·s7·s9·s10·s4·s11·s12·s13·s14 = SKILL.md 표와 일치, 제목 14개 일치. 산출 HTML(비데이터 부분·DATA)이 배포본과 동일 → 저장소 코드 = 배포본을 만든 코드.
- detect_ended: 완성 8개월(2026-01~08), [확인 필요] 1건(리모트뷰 — 결함 #7), [이미 제외 처리됨] 7건.
- 독립 재계산(pandas, pipeline 함수 미사용) 23개 값 대조: KPI 수입·지출, 월별 수입·지출·라벨, 계좌 6개 잔고, 예상 고정지출 총액·월수, 카드 2개 실적, 목표 대비 3개월 실제, 히트맵 합, TOP15 건수·제외 적용, 카드 내역 정렬 → **불일치 0개**. 목표 비교 시작월 auto = 종료 항목 마지막 결제월 다음 달로 정상.
- 데이터 위생: 완전 동일한 행 1쌍(2026-09, 지출) · 결제수단 공란 545건은 전부 8/31 이전 · 세부내용 특수문자 '>' 5건 '&' 1건 · 비고 값은 두 카드명뿐 · 날짜 전부 ISO.
- 1월 실행 사본(-8개월 이동, 2025-05~2026-01): 제목·기간·11번 note·목표 비교 라벨(2025.11월·2025.12월·1월)·전월 대비(12월→1월)·비교 시작월 12→1 처리 전부 정상.
- 월초 실행 사본(8/31 마감, 9월 행 0): 8월이 진행 중 취급 → 예상 고정지출 분모 7개월, 종료 추정 기준 7월(리모트뷰 공백 1로 미검출), 카드 실적 31/31일. 의도된 규칙대로이나 월초 실행 때 한 달 늦어짐(미확정 항목).
- 비정상 입력: 빈 CSV → TypeError 트레이스백 · 이체만 → IndexError · 구분 컬럼 없음 → IndexError · cp949 → UnicodeDecodeError(전부 멈추긴 함) / 1행·지출만·결제수단 전부 미등록·BOM 유무·컬럼 순서·컬럼명 공백 → 정상 진행(미등록은 경고 발생).

## 미확정으로 남긴 것

- 커플통장 잔고 음수가 데이터 오류인지(9월 정산 이체 4건의 방향: 결제수단=커플통장, 소분류=생활비) 실제 상황인지 — 사용자 확인 필요. 이 방향이면 생활비(화면 표시) 잔고가 그만큼 올라간다.
- 2026-09 완전 동일 행 1쌍이 실제 두 건인지.
- 월초 실행 시 전월 미완성 취급(위 실측)을 그대로 둘지, "CSV 최종일이 월말이고 사용자가 확인하면 완성 취급" 옵션을 둘지.
- SKILL.md 62-66 2026-09 예외 서술(9/2·9/6 분할 이체, 8/29 커플통장)은 2026-10-01부터 이력이 됨 — 삭제·이력 절 이동 여부.
- sw.js·GitHub Pages 캐시·배포 반영은 환경 제약(github.io 차단)으로 실측 못 함.
- 세부내용에 `<`가 오면 표가 깨질 가능성(실 CSV에는 아직 없음).

## 점검표 개정안

1. [시작 전 확인 ②] "`/mnt/skills/plugins/` 경로가 지금 환경과 안 맞는다"는 전제가 틀림 — 이 환경의 설치 경로가 정확히 `/mnt/skills/plugins/household-report-update/`다. "ls로 설치 경로를 확인하고 문구와 다를 때만 결함"으로.
2. 커밋 해시: codeload tarball엔 .git이 없다. `curl -H "Authorization: Bearer <토큰>" https://api.github.com/repos/LeeKwanBeom/household-report/commits?per_page=1`을 절차에 명시(비인증 API는 시간당 60회 제한에 실제로 걸렸다).
3. C항 validate() 파괴 실측은 CSV 사본으로 재현 불가(항등식) — "bundle을 메모리에서 변조해 validate() 호출"로 방법 명시. 0건 가드는 "전부 0인 bundle"로.
4. D항 "13개월 초과 사본" → "연도가 다른 같은 월 번호가 있는 사본"으로 조건 정정(11개월로 재현됨).
5. E항 빈 커밋 판정 방법 명시: `commits/{sha}`의 `stats.total == 0`.
6. 실측 출력 마스킹 규칙 추가: pipeline 출력은 `sed -E 's/[0-9]{1,3}(,[0-9]{3})+/[금액]/g'`, 잔고·합계 델타는 숫자 대신 "변화 있음/없음"으로만 출력. 이번 회차 C2·E2 실험에서 델타·node 오류 메시지 속 합계가 마스킹 없이 대화에 노출됐다.
7. G항 판정 기준을 "멈추는지"가 아니라 "멈출 때 원인을 말하는지"로(빈 CSV·이체만·cp949는 트레이스백으로 멈춤).
8. 점검표 본문의 회차 고정 문구("audit/last-audit.md(2026-09-06, 미해결 7건)", "2026-09-26 현재 저장소에 없다")를 "직전 기준선 파일"·"없으면 결함" 같은 일반 문구로.
9. github.io는 이 환경 허용 도메인 밖 — H "배포 후 Pages 반영 확인"·D "sw.js 캐시" 항목에 "코드 읽기/커밋 sha 확인까지만 가능" 명시.
10. 부트스트랩 행수 표기 기준 통일: wc -l 49(파일 끝 개행 없음, 줄 수는 50). 점검표는 49로 적혀 있어 wc -l 기준으로 통일.
11. [특히 이런 걸 찾아줘]에 없었지만 필요했던 것: (a) 실 CSV 데이터 위생(중복·공란·특수문자·비고 태그) 점검, (b) 배포본 index.html DATA와 신규 빌드 대조(저장소 코드 = 배포 코드 확인), (c) 종료 추정이 진행 중인 달의 행을 무시하는 것과 SKILL.md 안내의 정합. 항목으로 추가.
12. 실효 없던 항목: A "인자 순서"·"CSV 컬럼명 9개 한 곳 정의"는 이번에 문제 없었고 코드 구조상 바뀌기 어려움 — 다음 회차부터 "변경 커밋이 있을 때만" 조건부로.

## 다음 점검에서 대조할 것

- 이번 결함 16건 중 사용자가 채택한 번호와 수정 커밋. 채택 안 한 것은 그대로 미해결로 이월.
- SKILL.md 61 "합계 680,038원" 리터럴 vs SHINHAN_FIXED_ITEMS 합계(지금 일치, 상수가 바뀌면 어긋남).
- 10월 CSV에서 리모트뷰가 [확인 필요]에서 빠지는지(결함 #7 자연 해소 확인) / 커플통장 월 이체(10/1)가 기록됐는지(결함 #8).
- 커플통장의 subcategory_ranking 순위(지금 20위 중 2위)가 얼마나 내려갔는지(결함 #5 발현 시점).
- 09-18 같은 빈 커밋(diff 0)이 재발했는지.
- 2027-01 CSV부터 월 라벨 중복(결함 #13) 실제 발생.
- install/SKILL.md와 설치본 md5 일치 여부(채택 시).

## 수정 기록 (2026-09-26 수정 회차)

행 번호를 적은 곳은 수정 후 파일 기준(wc -l): SKILL.md 380 · pipeline.py 892 · build.py 1452 · scripts/deploy.py 289 · scripts/detect_ended.py 103 · audit/checklist.md 192 · install/SKILL.md 49 · tests/test_pipeline.py 358 · tests/fixtures/sample.csv 26. 규칙 상수 값(ACCOUNTS_START·ENDED_FIXED_ITEMS·SHINHAN_FIXED_ITEMS·CARD_TARGETS 등)은 하나도 바꾸지 않았다. index.html은 올리지 않았다.

### 결함

| # | 무엇을 고쳤나(절·함수) | 원 제안과 다른 점·이유 |
|---|---|---|
| 1 | pipeline.py `build_bundle` 진단 블록에 `card_in_payment`(결제수단이 카드명인 행 수·태그)·`blank_payment_after_start`(가장 이른 as_of 이후 결제수단 빈 수입·지출 행 수) 추가, `warnings_for`에 두 경고 | 원 제안은 "카드 태그 제외를 풀기"였으나 그러면 `unknown_payment`와 이중으로 뜬다. 전용 경고가 원인·조치("카드명은 비고에")를 직접 말하므로 그쪽으로. 빈칸은 계좌 추적 전 행이 정상적으로 비어 있어 `min(as_of)` 이후만 센다 |
| 2 | pipeline.py `EXPECTED_COLS`(9개, 유일 정의)·`TEXT_COLS`는 파생. `load_csv`가 빠진 컬럼을 `ValueError`로 중단(기대·실제 목록 출력). `main`이 `ValueError`를 `[중단] …` 한 줄로 찍고 exit 1 | — |
| 3 | pipeline.py `load_csv(path, today=None)`: 실행일보다 뒤인 행을 집계에서 빼고 `load_notices`에 건수·최대 날짜·기준일 기록 → `warnings_for`가 "실행일(…)보다 뒤인 날짜 행 N건(최대 …)" 경고. `today` 인자는 테스트용 | **사용자 지시대로 중단 아님.** 원 제안 "경고 또는 중단"에서 경고+제외로 확정 |
| 4 | `load_csv`의 날짜 해석 불가·알 수 없는 구분 [주의]를 `df.attrs["load_notices"]`에 담고 `build_bundle`이 `diagnostics["load"]`로 실어 `warnings_for`가 [경고] 블록에 출력. [주의] 출력도 유지(detect_ended 경로용). SKILL.md 5단계 목록에 추가 | — |
| 5 | build.py: `_couple`·`_couple_avg` 계산과 s13의 커플통장 줄 삭제 | **사용자 지시대로 pipeline으로 옮기지 않고 삭제**(9월부터 이체라 지표 무의미). SKILL.md "기타"에 삭제 사유 한 줄 |
| 6 | pipeline.py `screen_warnings_for`(감춘 계좌 언급 경고 제외) → `main`이 `bundle["warnings"]`로 저장. build.py `warning_items()`가 s13에 렌더(없으면 "확인할 항목 없음"), 하드코딩 2줄 삭제 | 원 제안에 없던 것: **감춘 계좌(HIDDEN_ACCOUNTS) 경고는 화면에서 뺀다.** 계좌를 화면에서 감춘 지시(2026-09-10)와 충돌하지 않게. 그 경고는 대화로만(SKILL.md 5단계·주의사항에 명시). 실 CSV에서 터미널 6건 중 화면 4건 |
| 7 | SKILL.md "예상 고정지출에서 영구 제외" 리모트뷰 문단 재작성: 재개 달이 진행 중이면 걸려도 무시, 완성된 뒤에도 걸리면 `고정` 누락 | — |
| 8 | SKILL.md 3단계: 확인 대상 세 건(원본 `EXPECTED_MONTHLY_TRANSFERS`), 보고 템플릿 금액을 ○○로, "하나라도 없으면" 묻기, "pipeline 경고가 받치지만 묻기가 첫 방어선". 2026-09 예외 서술은 "이력"으로 이동 | 문서의 금액 리터럴(680,000·500,000 등)을 모두 뺐다 — 단일 출처 규칙에 맞춤 |
| 9 | pipeline.py `validate`: `total_expense == 0`·`total_income == 0`을 [실패]로. 수입·지출 행이 0건이면 그 전에 `build_bundle`이 `ValueError`(이체만) | 원 제안대로. 이체만인 CSV는 validate까지 못 가고 [중단]에서 잡힌다(테스트는 메모리 변조로 0건 가드 확인) |
| 10 | pipeline.py `negative_rows`·`zero_rows` 진단 + 경고 2종 | — |
| 11 | scripts/deploy.py `CODE_GLOBS` + `_scan_code_files()`(SKILL.md·pipeline.py·build.py·scripts/*.py·audit/*.md·install/*.md·tests/*.py·tests/fixtures/*.csv). docstring에 `--code-only`·환경변수 사용법 | 하드코딩 목록을 없앴다(개선안 4와 함께). CSV·data_bundle.json·index.html은 패턴에 없고 테스트로 못 박음 |
| 12 | install/SKILL.md ← 설치본 원문 그대로(md5 b411b109, 49행). **내용 무변경이므로 재업로드 불필요** | — |
| 13 | pipeline.py `build_bundle` 월 라벨: 최신 연도가 아닌 달만 "2025.11월" | 원 제안 "연도가 바뀌는 첫 달에만 접두"가 아니라 build.py `fixed_vs_target_rows`가 이미 쓰는 규칙(최신 연도 아닌 달 전부)으로 통일. 단일 연도 데이터(지금)는 라벨 불변 |
| 14 | scripts/deploy.py `_extract_data`: `^const DATA = (.*?);\s*$`(re.M) 줄 단위. `validate_html`·잔고 급변 검사가 공용 | — |
| 15 | SKILL.md 5단계 경고 목록 19종(코드 `warns.append` 19개와 1:1), 리포트 구조에 01번 숨김 계좌·13번 경고 렌더 규칙 | — |
| 16 | build.py `savings_trend_svg` 삭제 | — |

### 개선안

| # | 무엇을 넣었나 | 원 제안과 다른 점·이유 |
|---|---|---|
| 1 | tests/fixtures/sample.csv(합성 25행: 7~9월, 이체 3종, 카드 태그, 고정 항목) + tests/test_pipeline.py 30개(잔고 독립 계산·예상 고정지출·validate 파괴 6종·0건 가드·컬럼 누락 7종·cp949·빈 CSV·이체만·경고 11종·월 라벨 연도 접두·화면 경고 필터·deploy 정규식·blob sha·잔고 게이트·스캔 제외). `python3 -m unittest discover tests` | pytest가 환경에 없어 unittest. **실 CSV 행은 한 줄도 넣지 않았다** |
| 2 | pipeline.py `EXPECTED_MONTHLY_TRANSFERS = {"start": "2026-09", "items": [(생활비, 신한은행, 680000), (생활비, 청년미래적금, 500000), (생활비, 커플통장, 400000)]}` — start 이후 각 달에 해당 이체 행이 없으면 "이체 행 없음", 합계가 다르면 "합계 ≠ 예상" 경고 | 합계 불일치도 경고에 넣었다(오타 68,000 같은 것을 잡기 위해). 그래서 2026-09 CSV에서는 알려진 예외(신한 분할·커플통장 선출금) 2건이 뜬다 — SKILL.md 이력에 "정상"으로 적음. 커플통장 관련은 감춘 계좌라 화면에는 안 실린다 |
| 3 | 완전 중복 행 경고(건수·묶음 수, **경고만**) · 비고에 카드명 아닌 값 경고 · 빈 CSV/이체만/cp949/컬럼 누락에 `[중단]` 한 줄 메시지(detect_ended.py도 동일) · build.py `esc()`로 세부내용·소분류·대분류·항목 이스케이프, JS `escHtml`로 히트맵 라벨, `DATA_JSON`의 `</`→`<\/` | 사용자 지시대로 중복은 경고만. 이스케이프로 실 CSV의 '>' 5건·'&' 1건 렌더가 `&gt;`·`&amp;`로 바뀐다(화면 표시는 동일) |
| 4 | deploy.py: `push`가 blob sha가 같으면 "변경 없음, 건너뜀"(빈 커밋 방지) · `CODE_GLOBS` 자동 스캔 · 토큰을 `GITHUB_TOKEN` 환경변수로도 | — |
| 5 | deploy.py `check_balance_jump`: 저장소에서 받은 index.html의 DATA와 계좌별 잔고 차이가 `BALANCE_JUMP_THRESHOLD`(3,000,000) 초과면 `[확인 필요]` 목록을 찍고 멈춤 → 사용자 확인 후 `--ack-balance`로 진행. `main`이 push 전후 HEAD sha를 찍음 | **하드 중단 아님**: 확인 후 같은 명령에 플래그만 붙이면 진행. 임계치는 상수라 사용자가 조정. github.io 접근 불가라 반영 확인은 sha까지 |

### 미확정 처리(사용자 결정)

- 월초 실행 시 전월 미완성 취급: 규칙 유지. SKILL.md 계산 규칙에 안내 한 줄.
- SKILL.md 2026-09 예외 서술: 맨 아래 "이력" 절로 이동(금액 리터럴 제거).
- 커플통장 잔고 음수: 코드·CSV 안내 없음. 이력에 기록. 경고는 대화로만(화면 제외).
- 2026-09-07 완전 동일 행 1쌍: 실제 2건. CSV 그대로. 이력에 기록.

### 점검표

- audit/checklist.md **v2**: 개정안 1~12 전부 반영. "되돌리면 안 되는 것" 표를 이번 수정에 맞춰 갱신(이체 경고+묻기, 화면 13번=warnings, load_csv 강제, 미래 날짜, 0건 가드, 잔고 급변 게이트, CODE_GLOBS, 월 라벨, tests). 기준선 형식에 "## 수정 기록" 추가.

### 수정 후 자체 확인(검증 세션에서 다시 본다)

- 실 CSV(가계부3.csv) pipeline → build → detect_ended → tests 재실행: 정합성 통과, 터미널 경고 6건(완전 중복 행 2건 · 매달 이체 확인 2건(9월 예외) · 청년미래적금·대여금 거래 0건 · 커플통장 음수), 화면 경고 4건. detect_ended [확인 필요] 1건(리모트뷰, 예상대로). tests 30/30 OK.
- 수정 전 산출 HTML과 대조: DATA 차이 키 = `diagnostics`(신규 키 9개)·`warnings`(신규)뿐. 비데이터 HTML 차이 = s13 블록 교체 · 세부내용 6행의 `>`/`&` 이스케이프 · JS `escHtml` 3줄. 그 외 동일.
- validate 파괴 6종 + 0건 가드: 전부 FAIL(tests). validate_html 파괴: JSON·JS·`{REF:`·`{won(`·크기 전부 FAIL, 세부내용 `};` 이제 **통과**, 세부내용 `</script>` 통과(raw `</script>` 1개, 표에 `&lt;b&gt;` 렌더), 잔고 급변 사본(합성 500만원 지출) `--ack-balance` 없이 FAIL·있으면 통과.
- 결함 1~4·9·10 합성 사본 재실험: 카드명·빈칸 결제수단 → 경고 / 결제수단·고정여부 컬럼 삭제 → [중단] / 미래 날짜 → 경고 + 최신 월·업데이트일 불변 / 날짜 형식·구분 변형 → 경고 / 이체만·빈 CSV → [중단] / 음수·빈칸 금액 → 경고 / 비고 "현대 카드" → 경고 / cp949 → [중단] / 청년미래적금 이체 삭제 → "이체 행 없음" 경고.
- grep 재확인: SKILL.md에 "화면에 나오지 않으므로"·"유일한 방어선"·"이체 누락을 잡는 검증이 없다"·"한쪽만 있으면"·이체 금액 리터럴 0회. build.py `_couple`·`savings_trend` 0회. deploy.py 하드코딩 목록·non-greedy 정규식 0회. pipeline.py 빈 컬럼 생성 0회.

## 이전 기록 (2026-09-06 원문 보존)

# 지난 점검 기록 — 2026-09-06 (최종 갱신 2026-09-06)

이 파일은 스킬 시작 시 SKILL.md와 함께 읽는다.
해결된 항목은 지우고, 남은 항목만 유지한다.

---

## 미해결 — 코드 쪽 (관범님이 고칠지 선택 예정)

2026-09-06 점검에서 확인됐고 아직 안 고친 것들. 우선순위 순.

### 1. 카드명을 결제수단에 적으면 잔고에서 조용히 사라짐

`pipeline.py`의 `unknown_payment` 진단이 `CARD_TARGETS` 키를 경고 대상에서
일부러 제외한다. 그래서 `결제수단="현대카드"`인 지출 행은 어느 계좌에도 안 붙는데
경고가 하나도 안 뜬다. 정합성 검증은 "합계 일치"로 통과한다.

실측: 90만원 행을 그렇게 넣었더니 총지출에는 반영되고 생활비 잔고는 변화 0,
경고 없음.

고치는 법: 카드 태그 제외를 풀거나, 별도 경고를 추가한다.
화면(build.py)은 안 건드려도 된다.

### 2. 커플통장이 TOP20 밖으로 밀리면 화면에 "0원"

`build.py`의 `_couple`이 `subcategory_ranking`(상위 20개)에서만 찾는다.
못 찾으면 `next(..., None)` → 평균 0. 실측으로 재현했다.
13번 "확인이 필요한 항목"에 명백히 틀린 숫자가 남는다.

### 3. pipeline 경고가 리포트 화면에 안 올라감

`diagnostics`, `shinhan_unmatched`를 `build.py`에서 grep하면 0회다.
pipeline이 계산해서 bundle에 실어 보내는데 build.py가 안 쓴다.
13번 "확인이 필요한 항목"은 하드코딩된 2줄뿐이다.

2번과 함께 고치면 둘 다 없어진다. 화면(build.py)을 건드리는 작업이다.

### 4. 13개월을 넘으면 월 라벨이 중복

`pipeline.py`의 `month_labels`가 연도 없이 `"1월"` 형태로 만든다.
14개월 데이터로 실측: `['1월',...,'12월','1월','2월']` — 1월·2월 중복.
히트맵 컬럼·추이 차트 x축·원장 탭이 전부 이 라벨을 쓴다.

연말·연초를 넘는 것 자체는 이미 처리돼 있다(`_range_label`, `_month_label`,
`TARGET_COMPARISON_START`의 12→1월 처리 전부 정상 확인). CSV가 1년 넘게
누적될 때만 나타난다.

### 5. 세부내용에 `};`가 들어가면 배포 검증이 막힘

`deploy.py`의 `re.search(r"const DATA = (\{.*?\});")`가 non-greedy라
JSON 문자열 안의 `};`에서 먼저 끊긴다. 실측 재현.
안전 실패(잘못된 걸 올리진 않음)라 급하지 않다. 다만 에러 메시지가
"HTML이 깨졌다"처럼 읽혀 원인을 엉뚱한 데서 찾게 된다.

### 6. build.py에 남은 특정 시점 서술

13번 섹션(`s13`):
`실업급여·퇴직금 등 일시 수입 유입 시 소비 쏠림 방지용 배정 규칙 검토`
지난 사건 기반 조언이 하드코딩돼 있다. 나머지 lede는 전부 bundle에서 생성된다.

### 7. 죽은 코드

`build.py`의 `savings_trend_svg()`는 정의만 있고 호출되는 곳이 없다.
월별 저축률 추이 섹션이 화면에서 빠지면서 함수만 남았다.

---

## 이번 점검에서 확인했고 문제 없던 것

같은 걸 다시 파헤치지 않도록 남긴다.

- `build.py` 연도 하드코딩: docstring 외 없음
- `ACCOUNTS_START` 값·기준일: SKILL.md와 일치
- `SUBSCRIPTION_ALIASES` / `TOP_EXPENSE_EXCLUSIONS` / `TARGET_COMPARISON_START`:
  사본 없음. bundle로 화면에 전달되는 구조가 이미 갖춰져 있다
- 03번 카드 내역 날짜 오름차순 정렬: 문서와 일치
- bundle 리스트 `[0]`/`[1]` 접근: `income_summary`, `_top_first_label`,
  `_fv_top_label`, `_mom_range_label`, `fixed_vs_target_rows` 전부 길이 가드 있음.
  남은 구멍은 위 2번뿐
- `sw.js`: network-first라 배포 후 옛 화면이 캐시되는 문제 없음
- `detect_ended.py`: 저장소본은 `pipeline.py`에서 목록을 import한다.
  사본 불일치 문제 없음 (설치본에만 남아 있던 옛 버전의 문제였다)
