---
name: household-report-update
description: 이관범 가계부 CSV를 받아 리포트를 재계산하고 GitHub Pages(leekwanbeom.github.io/household-report)에 배포한다. 사용자가 "가계부 업데이트", "가계부 리포트", "가계부 CSV 넣어줘", "가계부 배포", "household report", "가계부 갱신", "리포트 다시 만들어줘" 같은 말을 하거나, 가계부로 보이는 CSV(날짜/구분/대분류/소분류/세부내용/금액/결제수단/고정여부/비고 컬럼)를 업로드하면 반드시 이 스킬을 사용할 것. 가계부 데이터 분석·계좌 잔고 계산·고정지출 예측·신용카드 실적 확인 요청에도 적용된다.
---

# 가계부 리포트 업데이트

**이 파일은 부트스트랩이다. 실제 내용은 저장소에 있다.**

저장소: `LeeKwanBeom/household-report` (Public — 읽기는 토큰 없이 된다)

## 1. 저장소 받기

작업 디렉토리는 항상 `/home/claude/` 하위를 쓴다.

```bash
mkdir -p /home/claude/gagyebu && cd /home/claude/gagyebu
curl -sL https://codeload.github.com/LeeKwanBeom/household-report/tar.gz/refs/heads/main | tar xz --strip-components=1
ls
```

`raw.githubusercontent.com`으로 받지 말 것. 5분간 캐시되어서 push 직후에 받으면
옛 파일이 내려온다. codeload는 캐시되지 않고 요청 한 번으로 저장소 전체를 받는다.

## 2. 받은 SKILL.md를 따른다 — 이 파일이 아니라

받은 `SKILL.md`를 읽고 그 지시를 따른다. 작업 순서·명령어·옵션·계산 규칙·
화면 구조는 전부 거기 있다. **이 설치본에는 없다.**

`audit/last-audit.md`가 있으면 함께 읽는다. 지난 점검에서 확인된 미해결 항목이 적혀 있다.

## 3. 받기 실패 시

`ls` 결과에 `SKILL.md`, `pipeline.py`, `build.py`, `scripts/`가 다 보여야 성공이다.
하나라도 없으면 **진행하지 말고 멈춘다.**

- 아무것도 안 받아졌다 → 네트워크 또는 저장소 접근 문제. 사용자에게 알린다.
- 파일은 받았는데 `SKILL.md`가 없다 → 저장소에 아직 안 올라간 것이다.
  사용자에게 알리고, 절차를 임의로 지어내지 않는다.

로컬에 남아 있는 옛 파일로 대신 진행하지 않는다. 조용히 틀린 리포트가
배포되는 것보다 멈추는 편이 낫다.

## 4. 이 설치본을 고치지 말 것

`/mnt/skills/plugins/` 아래는 세션마다 패키지에서 다시 풀린다. 여기에 쓴 수정은
다음 세션에 사라지고, 사라진 줄 모른 채 낡은 사본으로 작업하게 된다.

내용을 바꿔야 하면 **저장소의 파일을 고치고 push한다.**
이 파일은 저장소를 가리키는 것 외에 아무 역할도 하지 않는다.