"""연도별 임원 명단을 이어 붙여 같은 사람을 연결하고, 신규·승진·이탈·이동을 계산한다.

사용법:
    python3 -I pipeline/05_link.py --years 2018-2025

입력:  data/built/executives_<year>.csv (03_parse.py 결과)
출력:
    data/built/person_years.csv   한 행 = 한 사람 × 한 해 × 한 회사 (person_id 부여)
    data/built/persons.csv        한 행 = 한 사람 (처음·마지막 등장, 직급 경로)
    data/built/events.csv         한 행 = 한 사건 (신규, 승진, 이탈, 계열사 이동, 등기 전환)
    data/built/link_report.md     연결 규칙별 건수, 검수할 목록, 회사×연도 지표

연결 규칙:
  1. 기본 키는 '공백 뺀 이름 + 출생연월 + 그룹'. 그룹을 넘는 연결은 하지 않는다.
     (이름·출생연월이 같은 다른 그룹 임원이 실제로 있었다. 그룹 간 이직은 경력 문구로 따로 판정할 과제다.)
  2. 같은 회사에서 이름과 출생연도는 같고 출생월만 다른 두 기록이, 같은 해에 함께 나온 적이 없으면 같은 사람으로 본다(link_type=출생월보정).
  3. 같은 키가 같은 해 두 회사에 나오면 겸직으로 기록한다(겸직 목록은 리포트에서 검수).

용어:
  - 신규: 관찰 범위 첫해(2018) 이후, 대상 회사 어디에도 없다가 처음 나온 경우.
  - 이탈: 다음 해 대상 회사 어디에도 없는 경우. 퇴임일 수도 있고 대상 밖 계열사로 옮긴 것일 수도 있다.
  - 대상편입 / 관찰종료: 회사가 새로 상장·그룹 편입된 첫해의 등장, 회사가 상장폐지·합병으로 사라진 해의 퇴장.
    회사 단위 변화라 신규·이탈로 세지 않는다.
  - 공시확대 / 공시축소: 회사 경영진 수가 한 해에 1.5배+5명 넘게 늘거나(전년 0명 포함) 줄면,
    그해 처음 나오거나 사라진 사람은 공시 범위 변화로 본다(예: 카카오 2024 '성과리더'까지 공시).
  - 직급 체계 변경: 어떤 회사에서 한 직급이 한 해 만에 사라지면(전년 10명 이상 → 0명) 그 해 그 직급에서의 변동은 승진이 아니라 체계 변경으로 기록한다.
"""
import argparse
import collections
import csv
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILT = ROOT / "data" / "built"
RANK = {"이사대우": 1, "이사(미등기)": 2, "상무": 3, "전무": 4, "부사장": 5, "사장": 6, "부회장": 7, "회장": 8}
EXEC = ("미등기", "사내이사", "집행임원")  # 경영진. 사외이사·기타비상무이사는 연결은 하되 경영진 지표에서 뺀다.


def load(years):
    rows = []
    for y in years:
        p = BUILT / f"executives_{y}.csv"
        if not p.exists():
            print(f"[없음] {p.name}")
            continue
        for r in csv.DictReader(open(p, encoding="utf-8-sig")):
            r["year"] = y
            rows.append(r)
    return rows


def assign_ids(rows):
    """기본 키 + 출생월 보정으로 person_id를 붙인다."""
    for r in rows:
        r["pkey"] = f"{r['name_key']}|{r['birth_ym']}|{r['group']}"
        r["link_type"] = "정확"
    # 출생월 보정: 같은 회사, 같은 이름, 같은 출생연도, 서로 다른 출생월, 같은 해 공존 없음
    by = collections.defaultdict(lambda: collections.defaultdict(set))
    group = {r["corp_code"]: r["group"] for r in rows}
    for r in rows:
        by[(r["corp_code"], r["name_key"], r["birth_ym"][:4])][r["birth_ym"]].add(r["year"])
    remap, fixes = {}, []
    for (corp, name, _), variants in by.items():
        if len(variants) < 2:
            continue
        keys = sorted(variants, key=lambda k: -len(variants[k]))  # 가장 많이 나온 표기를 대표로
        main = keys[0]
        for k in keys[1:]:
            if variants[k] & variants[main]:
                continue  # 같은 해 둘 다 있으면 다른 사람
            g = group[corp]
            remap[f"{name}|{k}|{g}"] = f"{name}|{main}|{g}"
            fixes.append((corp, name, k, main, sorted(variants[k])))
    for r in rows:
        if r["pkey"] in remap:
            r["pkey"] = remap[r["pkey"]]
            r["link_type"] = "출생월보정"
    ids = {k: f"P{i:05d}" for i, k in enumerate(sorted({r["pkey"] for r in rows}), 1)}
    for r in rows:
        r["person_id"] = ids[r["pkey"]]
    return fixes


def rank_system_changes(rows):
    """(회사, 연도, 직급): 그 해에 사라진 직급."""
    cnt = collections.Counter((r["corp_code"], r["year"], r["position_norm"]) for r in rows if r["reg_class"] in EXEC)
    years = sorted({r["year"] for r in rows})
    out = set()
    for corp in {r["corp_code"] for r in rows}:
        for prev, cur in zip(years, years[1:]):
            for rank in RANK:
                if cnt[(corp, prev, rank)] >= 10 and cnt[(corp, cur, rank)] == 0:
                    out.add((corp, cur, rank))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2018-2025")
    a = ap.parse_args()
    lo, hi = map(int, a.years.split("-"))
    years = [str(y) for y in range(lo, hi + 1)]
    rows = load(years)
    years = sorted({r["year"] for r in rows})
    fixes = assign_ids(rows)
    sysch = rank_system_changes(rows)
    company_name = {r["corp_code"]: r["company"] for r in rows}
    observed = {(r["corp_code"], r["year"]) for r in rows}  # 보고서를 관찰한 회사-연도
    # 회사-연도 경영진 수. 한 해에 1.5배+5명 넘게 뛰면 공시 범위가 넓어진 것으로 본다(카카오 2024: 19 → 63명)
    exec_n = collections.Counter((r["corp_code"], r["year"]) for r in rows if r["reg_class"] in EXEC)

    def jump(corp, prev_year, year):
        a_, b_ = exec_n[(corp, prev_year)], exec_n[(corp, year)]
        return a_ == 0 or b_ > 1.5 * a_ + 5

    def drop(corp, year, next_year):
        a_, b_ = exec_n[(corp, year)], exec_n[(corp, next_year)]
        return a_ > 1.5 * b_ + 5
    company_first = {}
    for c, y in observed:
        company_first[c] = min(company_first.get(c, y), y)
    group_of = {r["corp_code"]: r["group"] for r in rows}

    # 사람 × 연도 → 그 해 기록들 (겸직이면 여러 개)
    py = collections.defaultdict(list)
    for r in rows:
        py[(r["person_id"], r["year"])].append(r)
    concurrent = [(pid, y, [x["company"] for x in rs]) for (pid, y), rs in py.items()
                  if len({x["corp_code"] for x in rs}) > 1]

    def main_rec(rs):  # 겸직이면 직급이 가장 높은 기록을 대표로
        return max(rs, key=lambda x: (RANK.get(x["position_norm"], 0), x["reg_class"] != "미등기"))

    events = []
    persons = {}
    for pid in sorted({r["person_id"] for r in rows}):
        seq = [(y, main_rec(py[(pid, y)])) for y in years if (pid, y) in py]
        first_y, first = seq[0]
        last_y, last = seq[-1]
        prev_y = None
        prev = None
        for y, cur in seq:
            yi = years.index(y)
            if prev is None:
                if yi > 0:
                    # 회사를 전년에도 관찰했을 때만 신규. 회사가 새로 상장·편입된 첫해의 등장은 '대상편입'
                    if (cur["corp_code"], years[yi - 1]) not in observed:
                        kind = "대상편입"
                    elif jump(cur["corp_code"], years[yi - 1], y):
                        kind = "공시확대"
                    else:
                        kind = "신규"
                    events.append((pid, y, cur["corp_code"], kind, "", cur["position_norm"]))
            else:
                gap = years[years.index(prev_y) + 1: yi]
                if gap and any((prev["corp_code"], g) in observed for g in gap):  # 회사는 있는데 사람이 빈 해 → 이탈 후 복귀
                    events.append((pid, gap[0], prev["corp_code"], "이탈", prev["position_norm"], ""))
                    events.append((pid, y, cur["corp_code"], "복귀", "", cur["position_norm"]))
                else:
                    if cur["corp_code"] != prev["corp_code"]:
                        kind = "계열사이동" if group_of[cur["corp_code"]] == group_of[prev["corp_code"]] else "그룹간이동"
                        events.append((pid, y, cur["corp_code"], kind, company_name[prev["corp_code"]], company_name[cur["corp_code"]]))
                    a_, b_ = RANK.get(prev["position_norm"]), RANK.get(cur["position_norm"])
                    if a_ and b_ and a_ != b_:
                        if (cur["corp_code"], y, prev["position_norm"]) in sysch:
                            kind = "직급체계변경"
                        else:
                            kind = "승진" if b_ > a_ else "직급하향"
                        events.append((pid, y, cur["corp_code"], kind, prev["position_norm"], cur["position_norm"]))
                    if prev["reg_class"] == "미등기" and cur["reg_class"] == "사내이사":
                        events.append((pid, y, cur["corp_code"], "등기전환", "미등기", "사내이사"))
            prev_y, prev = y, cur
        if last_y != years[-1]:
            nxt = years[years.index(last_y) + 1]
            # 회사를 다음 해에도 관찰했을 때만 이탈. 회사가 상장폐지·합병·그룹 이탈로 사라졌으면 '관찰종료'
            if (last["corp_code"], nxt) not in observed:
                kind = "관찰종료"
            elif drop(last["corp_code"], last_y, nxt):
                kind = "공시축소"
            else:
                kind = "이탈"
            events.append((pid, nxt, last["corp_code"], kind, last["position_norm"], ""))
        persons[pid] = {
            "person_id": pid, "name": first["name"], "birth_ym": first["birth_ym"], "sex": first["sex"],
            "link_type": "출생월보정" if any(r["link_type"] == "출생월보정" for _, r in seq) else "정확",
            "first_year": first_y,
            "left_censored": "Y" if first_y == years[0] or first_y == company_first[first["corp_code"]] else "",
            "last_year": last_y, "right_censored": "Y" if last_y == years[-1] else "",
            "n_years": len(seq),
            "first_company": first["company"], "first_position": first["position_norm"],
            "last_company": last["company"], "last_position": last["position_norm"],
            "rank_path": ">".join(dict.fromkeys(r["position_norm"] for _, r in seq)),
            "companies": "|".join(dict.fromkeys(r["company"] for _, r in seq)),
            "exec": "Y" if any(r["reg_class"] in EXEC for _, r in seq) else "",
        }

    # 저장
    pyf = ["person_id", "year", "group", "company", "corp_code", "reg_class", "name", "sex", "birth_ym", "age",
           "position", "position_norm", "duty", "career", "education", "tenure_raw", "tenure_start", "link_type", "source_url"]
    with open(BUILT / "person_years.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=pyf, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["person_id"], r["year"])))
    with open(BUILT / "persons.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(next(iter(persons.values())).keys()))
        w.writeheader()
        w.writerows(persons.values())
    with open(BUILT / "events.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["person_id", "name", "year", "company", "event", "from", "to"])
        for pid, y, corp, kind, fr, to in sorted(events, key=lambda e: (e[1], e[2], e[3])):
            w.writerow([pid, persons[pid]["name"], y, company_name[corp], kind, fr, to])

    # 리포트
    md = [f"# 연도 연결 리포트 ({years[0]}~{years[-1]})\n",
          "자동 생성: `pipeline/05_link.py`. '이탈'은 퇴임 또는 대상 밖 계열사 이동을 뜻한다.\n",
          f"- 사람-연도-회사 행: {len(rows):,}",
          f"- 고유 인물: {len(persons):,} (경영진 이력 있음 {sum(p['exec'] == 'Y' for p in persons.values()):,})",
          f"- 출생월 보정 연결: {len(fixes)}건",
          f"- 같은 해 두 회사 동시 등장(겸직 후보): {len(concurrent)}건",
          f"- 직급 체계 변경 감지: {sorted((company_name[c], y, r) for c, y, r in sysch)}\n"]

    ev = collections.Counter((e[2], e[1], e[3]) for e in events)
    headc = collections.Counter((r["corp_code"], r["year"]) for r in rows if r["reg_class"] in EXEC)
    md += ["## 회사 × 연도: 경영진 수 / 신규 / 승진 / 이탈\n",
           "| 회사 | " + " | ".join(years) + " |", "|---|" + "---|" * len(years)]
    for corp in sorted(company_name, key=lambda c: (group_of[c], company_name[c])):
        cells = []
        for y in years:
            n = headc[(corp, y)]
            if not n:
                cells.append("-")
                continue
            cells.append(f"{n} / {ev[(corp, y, '신규')]} / {ev[(corp, y, '승진')]} / {ev[(corp, y, '이탈')]}")
        md.append(f"| {company_name[corp]} | " + " | ".join(cells) + " |")

    # 신규 임원 나이, 상무 → 다음 직급 소요 연수
    md += ["\n## 신규 임원 평균 나이 (관찰 범위 안에서 처음 등장한 경영진)\n", "| 회사 | 인원 | 평균 나이 | 중앙값 |", "|---|---|---|---|"]
    age_of = {(r["person_id"], r["year"]): int(r["age"]) for r in rows}
    for corp in sorted(company_name, key=lambda c: (group_of[c], company_name[c])):
        ages = [age_of[(e[0], e[1])] for e in events if e[2] == corp and e[3] == "신규" and (e[0], e[1]) in age_of
                and persons[e[0]]["exec"] == "Y"]
        if ages:
            md.append(f"| {company_name[corp]} | {len(ages)} | {statistics.mean(ages):.1f} | {statistics.median(ages):.0f} |")

    md += ["\n## 상무로 신규 선임된 뒤 첫 승진까지 걸린 해 (승진한 사람만)\n", "| 회사 | 승진 인원 | 평균 연수 | 같은 기간 신규 상무 |", "|---|---|---|---|"]
    for corp in sorted(company_name, key=lambda c: (group_of[c], company_name[c])):
        starts = {e[0]: int(e[1]) for e in events if e[2] == corp and e[3] == "신규" and e[5] == "상무"}
        gaps = []
        for pid, y0 in starts.items():
            pr = [int(e[1]) for e in events if e[0] == pid and e[3] == "승진" and e[4] == "상무"]
            if pr:
                gaps.append(min(pr) - y0)
        if starts:
            md.append(f"| {company_name[corp]} | {len(gaps)} | {statistics.mean(gaps):.1f} | {len(starts)} |" if gaps
                      else f"| {company_name[corp]} | 0 | - | {len(starts)} |")

    md += ["\n## 검수 목록: 출생월 보정 (처음 30건)\n", "| 회사 | 이름 | 바뀐 표기 | 대표 표기 | 해당 연도 |", "|---|---|---|---|---|"]
    md += [f"| {company_name[c]} | {n} | {k} | {m} | {','.join(ys)} |" for c, n, k, m, ys in fixes[:30]]
    md += ["\n## 검수 목록: 같은 해 두 회사 동시 등장 (처음 30건)\n", "| 인물 | 연도 | 회사 |", "|---|---|---|"]
    md += [f"| {persons[pid]['name']} ({persons[pid]['birth_ym']}) | {y} | {', '.join(cs)} |" for pid, y, cs in concurrent[:30]]
    (BUILT / "link_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"인물 {len(persons):,}명, 사건 {len(events):,}건 → data/built/")


if __name__ == "__main__":
    main()
