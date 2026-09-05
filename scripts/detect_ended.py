"""
최근 2개월 이상 결제가 없는 '고정' 항목을 찾아 보고한다.

사용법:
    python3 scripts/detect_ended.py 가계부3.csv

결과는 대화로만 전달하고 리포트 화면에는 넣지 않는다 (사용자 요청).

규칙·상수는 pipeline.py에서 그대로 import 한다. 여기에 따로 적어두면
pipeline.py를 고쳤을 때 둘이 어긋나기 때문이다.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import (  # noqa: E402
    ENDED_FIXED_ITEMS,
    MANUALLY_EXCLUDED_FIXED_ITEMS,
    load_csv,
)

GAP_THRESHOLD = 2  # 이 개월 수 이상 공백이면 종료 추정


def build_fixed_keys(df):
    """pipeline.py의 매달 예상 고정지출과 동일한 키 체계로 고정 항목을 집계한다."""
    core = df[df["구분"].isin(["수입", "지출"])]
    months = sorted(core["월"].unique())
    if len(months) < 2:
        raise SystemExit("완성된 달이 없어 종료 추정을 할 수 없습니다.")

    # 최신 월은 항상 진행 중으로 간주해 제외
    completed_months = months[:-1]
    last_completed = pd.to_datetime(completed_months[-1] + "-01")

    fixed = core[(core["구분"] == "지출")
                 & (core["월"].isin(completed_months))
                 & (core["고정여부"] == "고정")].copy()

    non_app = fixed[fixed["소분류"] != "어플/멤버쉽"].copy()
    non_app["키"] = non_app["대분류"] + " / " + non_app["소분류"]

    # 여러 구독이 한 소분류에 섞여 있어 세부내용 단위로 따로 본다
    app = fixed[fixed["소분류"] == "어플/멤버쉽"].copy()
    app["키"] = "문화생활비 / 어플·멤버쉽 / " + app["세부내용"]

    g = pd.concat([non_app, app], ignore_index=True).groupby("키").agg(
        마지막월=("월", "max"), 등장월수=("월", "nunique"), 합계=("금액", "sum"),
    ).reset_index()

    last_dt = pd.to_datetime(g["마지막월"] + "-01")
    g["공백"] = ((last_completed.year - last_dt.dt.year) * 12
                + (last_completed.month - last_dt.dt.month))
    return g, completed_months, months[-1]


def main():
    if len(sys.argv) < 2:
        raise SystemExit("사용법: python3 scripts/detect_ended.py <csv파일명>")

    df = load_csv(sys.argv[1])
    g, completed_months, in_progress = build_fixed_keys(df)

    already = set(ENDED_FIXED_ITEMS) | set(MANUALLY_EXCLUDED_FIXED_ITEMS)
    stale = g[g["공백"] >= GAP_THRESHOLD].sort_values("공백", ascending=False)

    new = stale[~stale["키"].isin(already)]
    known = stale[stale["키"].isin(already)]

    print(f"기준: 완성된 {len(completed_months)}개월 "
          f"({completed_months[0]}~{completed_months[-1]}), "
          f"진행 중인 {in_progress}은 제외")
    print()

    if new.empty:
        print("확인이 필요한 고정 항목 없음 — 모두 최근 결제 기록이 있습니다.")
    else:
        print(f"[확인 필요] 최근 {GAP_THRESHOLD}개월 이상 결제가 없는 고정 항목 "
              f"{len(new)}건:")
        for _, r in new.iterrows():
            print(f"  - {r['키']}: 마지막 {r['마지막월']} "
                  f"({int(r['공백'])}개월 공백, 누적 {round(r['합계']):,}원)")
        print()
        print("  실제로 종료된 항목이 있으면 pipeline.py의 ENDED_FIXED_ITEMS에 추가하고,")
        print("  SKILL.md의 종료 항목 목록에도 같이 반영할 것.")

    if not known.empty:
        print()
        print(f"[이미 제외 처리됨] {len(known)}건 (조치 불필요):")
        for _, r in known.iterrows():
            reason = ("제외 요청" if r["키"] in MANUALLY_EXCLUDED_FIXED_ITEMS
                      else "종료 확인")
            print(f"  - {r['키']}: 마지막 {r['마지막월']} ({reason})")


if __name__ == "__main__":
    main()
