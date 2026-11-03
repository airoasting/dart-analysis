"""그룹 단위 비교표를 만든다 (2025 단면 + 2018~2025 흐름 + 미등기임원 보수).

사용법:
    python3 -I pipeline/06_group_report.py --year 2026 --flow-end 2025 --pay-year 2025

입력:  data/groups.csv, data/built/executives_<year>.csv, unreg_pay_<year>.csv,
       person_years.csv, events.csv, persons.csv (05_link.py 결과)
출력:  data/built/group_summary.csv, data/built/group_summary.md

지표 정의:
  - 경영진: 사내이사 + 미등기 + 집행임원. 사외이사·기타비상무이사·감사는 뺀다.
  - 그룹 인원은 계열사 간 겸직자를 한 명으로 센다(같은 그룹 안에서 person_id 기준).
  - 미등기 1인 평균 보수: 회사별 '미등기임원 보수 현황'의 총액 합 ÷ 인원 합 (백만원).
  - 신규율·이탈률: 2020~2025 연평균, 전년 그룹 경영진 수 대비. 이탈은 퇴임과 대상 밖 계열사 이동을 함께 센다.
"""
import argparse
import collections
import csv
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILT = ROOT / "data" / "built"
EXEC = ("미등기", "사내이사", "집행임원")


def read(name):
    return list(csv.DictReader(open(BUILT / name, encoding="utf-8-sig")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", default="2026", help="단면 지표(인원·나이·여성) 기준 연도. 2026은 반기보고서(6월 말)")
    ap.add_argument("--flow-end", default="2025", help="연간 흐름(신규율·이탈률) 마지막 연도. 반기 자료는 기간이 반년이라 넣지 않는다")
    ap.add_argument("--pay-year", default="2025", help="미등기임원 보수 연도. 반기 보수는 반년치라 연간 자료를 쓴다")
    a = ap.parse_args()
    y = a.year
    groups = {g["group"]: g for g in csv.DictReader(open(ROOT / "data" / "groups.csv", encoding="utf-8"))}
    py = read("person_years.csv")
    ev = read("events.csv")
    pay = read(f"unreg_pay_{a.pay_year}.csv")
    company_group = {r["company"]: r["group"] for r in py}

    # 그룹 × 연도 경영진(고유 인물)
    heads = collections.defaultdict(set)
    rec_y = collections.defaultdict(dict)
    for r in py:
        if r["reg_class"] in EXEC:
            heads[(r["group"], r["year"])].add(r["person_id"])
            if r["year"] == y:
                rec_y[r["group"]][r["person_id"]] = r

    # 사건: 그룹 단위로 셀 때 계열사 이동은 신규·이탈이 아니다 (05_link가 이미 그룹 안에서 연결)
    evc = collections.Counter((company_group.get(e["company"]), e["year"], e["event"]) for e in ev)
    new_age = collections.defaultdict(list)
    age_of = {(r["person_id"], r["year"]): int(r["age"]) for r in py}
    for e in ev:
        if e["event"] == "신규" and (e["person_id"], e["year"]) in age_of:
            new_age[company_group.get(e["company"])].append(age_of[(e["person_id"], e["year"])])

    paysum = collections.defaultdict(lambda: [0.0, 0])
    for p in pay:
        if p["pay_count"] and p["pay_total_mn"]:
            paysum[p["group"]][0] += float(p["pay_total_mn"])
            paysum[p["group"]][1] += int(p["pay_count"])

    out = []
    for g, recs in rec_y.items():
        rs = list(recs.values())
        n = len(rs)
        ages = [int(r["age"]) for r in rs]
        yrs = [str(v) for v in range(2020, int(a.flow_end) + 1)]
        nr = [evc[(g, v, "신규")] / len(heads[(g, str(int(v) - 1))]) for v in yrs if heads[(g, str(int(v) - 1))]]
        xr = [evc[(g, v, "이탈")] / len(heads[(g, str(int(v) - 1))]) for v in yrs if heads[(g, str(int(v) - 1))]]
        ranks = collections.Counter(r["position_norm"] for r in rs)
        ps = paysum.get(g)
        out.append({
            "rank": int(groups[g]["rank"]) if g in groups else 999, "group": g,
            "companies": len({r["company"] for r in rs}), "executives": n,
            "avg_age": round(statistics.mean(ages), 1), "female_pct": round(100 * sum(r["sex"] == "여" for r in rs) / n, 1),
            "born_1970s_pct": round(100 * sum(r["birth_ym"].startswith("197") for r in rs) / n, 1),
            "born_1980s_pct": round(100 * sum(r["birth_ym"].startswith("198") for r in rs) / n, 1),
            "rank_hidden_pct": round(100 * ranks.get("직급비공개", 0) / n, 1),
            "new_rate_pct": round(100 * statistics.mean(nr), 1) if nr else "",
            "exit_rate_pct": round(100 * statistics.mean(xr), 1) if xr else "",
            "new_exec_avg_age": round(statistics.mean(new_age[g]), 1) if new_age[g] else "",
            "unreg_pay_avg_mn": round(ps[0] / ps[1]) if ps and ps[1] else "",
        })
    out.sort(key=lambda r: r["rank"])

    with open(BUILT / "group_summary.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)

    md = [f"# 50대 그룹 경영진 비교 (단면 {y} 최신 보고서 기준일, 흐름 2020~{a.flow_end}, 보수 {a.pay_year})\n",
          "자동 생성: `pipeline/06_group_report.py`. 경영진 = 사내이사 + 미등기 + 집행임원, 그룹 안 겸직자는 1명.",
          f"신규율·이탈률은 2020~{a.flow_end} 연평균. 이탈 = 퇴임 + 대상 밖(비상장) 계열사 이동. 보수는 {a.pay_year}년 미등기임원 1인 평균(백만원).\n",
          "| 순위 | 그룹 | 상장사 | 경영진 | 평균 나이 | 여성 | 70년대생 | 80년대생 | 직급비공개 | 신규율 | 이탈률 | 신규 임원 나이 | 미등기 평균 보수 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in out:
        md.append(f"| {r['rank']} | {r['group']} | {r['companies']} | {r['executives']:,} | {r['avg_age']} | {r['female_pct']}% | "
                  f"{r['born_1970s_pct']}% | {r['born_1980s_pct']}% | {r['rank_hidden_pct']}% | "
                  f"{r['new_rate_pct']}% | {r['exit_rate_pct']}% | {r['new_exec_avg_age']} | {r['unreg_pay_avg_mn']} |")
    tot = sum(r["executives"] for r in out)
    md.append(f"\n합계 경영진 {tot:,}명 (그룹 안 겸직 1명 처리), 그룹 {len(out)}개. 경영진 30명 미만 그룹의 비율 지표는 표본이 작아 참고용이다.")
    (BUILT / "group_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"그룹 {len(out)}개 → data/built/group_summary.md")


if __name__ == "__main__":
    main()
