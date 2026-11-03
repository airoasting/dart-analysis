"""파싱 결과로 회사별 요약표를 만든다 (분류 전 1차 지표).

사용법:
    python3 -I pipeline/04_summary.py --year 2025

출력: data/built/summary_<year>.md
"""
import argparse
import collections
import csv
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILT = ROOT / "data" / "built"
RANKS = ["회장", "부회장", "사장", "부사장", "전무", "상무", "이사(미등기)", "이사대우", "직급비공개", "전문임원", "기타"]


def pct(a, b):
    return f"{a / b:.0%}" if b else "-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", default="2025")
    a = ap.parse_args()
    rows = list(csv.DictReader(open(BUILT / f"executives_{a.year}.csv", encoding="utf-8-sig")))
    by = collections.defaultdict(list)
    for r in rows:
        by[(r["group"], r["company"])].append(r)

    md = [f"# 임원 요약 ({a.year} 사업보고서 기준일)\n",
          "자동 생성: `pipeline/04_summary.py`. 나이는 기준일 연도 - 출생연도. 사외이사·기타비상무이사는 경영진 지표에서 뺀다.\n",
          "## 경영진(사내이사 + 미등기 + 집행임원) 구성\n",
          "| 그룹 | 회사 | 경영진 | 평균 나이 | 최연소 | 여성 | 1970년대생 | 1980년대생 | 재직기간 기재율 |",
          "|---|---|---|---|---|---|---|---|---|"]
    rank_md = ["\n## 직급 구성 (경영진)\n",
               "| 회사 | " + " | ".join(RANKS) + " |",
               "|---|" + "---|" * len(RANKS)]
    for (g, c), rs in by.items():
        ex = [r for r in rs if r["reg_class"] in ("미등기", "사내이사", "집행임원")]
        ages = [int(r["age"]) for r in ex]
        n = len(ex)
        b70 = sum(1 for r in ex if r["birth_ym"].startswith("197"))
        b80 = sum(1 for r in ex if r["birth_ym"].startswith("198"))
        fem = sum(1 for r in ex if r["sex"] == "여")
        ten = sum(1 for r in ex if r["tenure_raw"].strip() not in ("", "-"))
        md.append(f"| {g} | {c} | {n} | {statistics.mean(ages):.1f} | {min(ages)} | {pct(fem, n)} | "
                  f"{pct(b70, n)} | {pct(b80, n)} | {pct(ten, n)} |")
        cnt = collections.Counter(r["position_norm"] for r in ex)
        rank_md.append(f"| {c} | " + " | ".join(str(cnt.get(k, 0) or "") for k in RANKS) + " |")

    ex_all = [r for r in rows if r["reg_class"] in ("미등기", "사내이사", "집행임원")]
    md.append(f"| 합계 | | {len(ex_all)} | {statistics.mean(int(r['age']) for r in ex_all):.1f} | "
              f"{min(int(r['age']) for r in ex_all)} | {pct(sum(r['sex'] == '여' for r in ex_all), len(ex_all))} | | | |")
    (BUILT / f"summary_{a.year}.md").write_text("\n".join(md + rank_md) + "\n", encoding="utf-8")
    print(f"→ {BUILT / f'summary_{a.year}.md'}")


if __name__ == "__main__":
    main()
