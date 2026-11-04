"""사이트가 읽는 JSON과 독립 HTML을 만든다.

사용법:
    python3 -I pipeline/07_site_data.py

입력:  data/groups.csv, data/companies.csv, data/built/{person_years,events,group_summary,classify_rows,classify_persons}.csv,
       data/built/unreg_pay_<PAY_YEAR>.csv
출력:
    docs/data/core.json     그룹·회사 지표, 벤치마크(전체·중앙값·상위 10대·순위), 연도별 추이, 분류 집계 (첫 화면용)
    docs/data/people.json   인물별 연도 경력·사건·기능, 사람별 출신·학력 (회사 명단, 인물, 검색용)
    docs/index.html         site/index.html을 doctype·head·body를 갖춘 독립 HTML로 감싼 것 (GitHub Pages 등에 그대로 올린다)
    docs/.nojekyll          GitHub Pages가 파일을 그대로 서비스하게 한다

site/index.html은 아티팩트 게시용 원본(문서 골격 없이 본문만)이다. 화면을 고칠 때는 site/index.html을 고치고 이 스크립트를 다시 돌린다.
모든 숫자는 data/built의 파이프라인 결과에서 온다. 여기서 하는 일은 집계와 비교 기준 계산뿐이다.
"""
import collections
import csv
import datetime
import json
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILT = ROOT / "data" / "built"
DOCS = ROOT / "docs"
OUT = DOCS / "data"
SITE_SRC = ROOT / "site" / "index.html"

YEARS = [str(y) for y in range(2018, 2027)]
LAST = YEARS[-1]          # 단면 지표 기준(2026 = 반기보고서, 6월 말)
FLOW_START, FLOW_END = "2020", "2025"   # 연간 흐름(신규·승진·이탈) 평균 구간
PAY_YEAR = "2025"         # 미등기임원 보수 연도(연간)
PARTIAL = {"2026": "2026-06-30"}  # 연말이 아닌 기준일
MIN_N = 30                # 비율 비교에 넣는 최소 경영진 수
SITE_OWNER = ""           # 만든 곳(운영 주체). 정해지면 채운다. 비어 있으면 화면에 나오지 않는다
SITE_CONTACT = ""         # 정정 요청 연락처. 정해지면 채운다
EXEC = ("미등기", "사내이사", "집행임원")
REG = ["미등기", "사내이사", "집행임원", "사외이사", "기타비상무이사", "감사", "등기"]
RANKS = ["회장", "부회장", "사장", "부사장", "전무", "상무", "이사(미등기)", "이사대우",
         "직급비공개", "전문임원", "고문", "이사", "감사", "기타"]
EVENTS = ["신규", "승진", "이탈", "계열사이동", "등기전환", "직급체계변경", "공시확대", "공시축소", "대상편입", "관찰종료", "복귀", "직급하향"]
FUNCS = ["최고경영", "사업부문장", "영업·마케팅", "연구개발·기술", "생산·품질·안전", "전략·기획", "재무·IR", "인사·조직",
         "법무·준법·감사", "대외·홍보·ESG", "IT·디지털", "구매·SCM", "금융업무", "해외", "경영지원", "고문·자문", "미분류", "이사회(비상근)"]
ORIGINS = ["오너일가", "관료·공공", "법조", "학계", "회계·세무", "컨설팅", "군", "언론", "타 그룹 출신", "외국계 기업",
           "외부 기업·기관", "그룹 내부", "미상(경력 짧음)", "미상"]
EXTERNAL = {"관료·공공", "법조", "학계", "회계·세무", "컨설팅", "군", "언론", "타 그룹 출신", "외국계 기업", "외부 기업·기관"}
DEGREES = ["박사", "석사", "학사", "학사(추정)", ""]
FIELDS = ["공학", "경영·경제", "법학", "자연과학", "의약", "인문·사회", ""]
# 화면에 쓰는 그룹 이름: 공정위는 영문 약칭을 한글로 적는다(엘지, 에스케이). 사람들이 아는 표기로 바꾸고 공정위 표기는 ftc_name에 둔다.
LABEL = {"에스케이": "SK", "엘지": "LG", "지에스": "GS", "케이티": "KT", "엘에스": "LS", "씨제이": "CJ", "에이치엠엠": "HMM",
         "에쓰-오일": "S-OIL", "케이씨씨": "KCC", "에이치디씨": "HDC", "케이티앤지": "KT&G", "엘엑스": "LX", "오씨아이": "OCI",
         "한국앤컴퍼니그룹": "한국앤컴퍼니", "교보생명보험": "교보생명"}
NO_DISCLOSURE = {"쿠팡": "국내 소속회사가 사업보고서를 내지 않아 임원 명단 공시가 없습니다",
                 "부영": "소속회사가 사업보고서를 내지 않아 임원 명단 공시가 없습니다",
                 "한국지엠": "한국지엠㈜이 감사보고서만 내고 사업보고서를 내지 않아 임원 명단 공시가 없습니다"}


def read(path):
    return list(csv.DictReader(open(path, encoding="utf-8-sig")))


def pct(a, b):
    return round(100 * a / b, 1) if b else None


# 공시 원문에서 붙어 나온 직위·담당 업무 띄어쓰기: 직책 약어 앞뒤, 연달아 붙은 '위원' 사이만 띄운다(회사 이름 'LG전자' 등은 건드리지 않는다)
_ROLE_ABBR = r"(?:CEO|CFO|COO|CTO|CHO|CSO|CRO|CIO|CMO|CDO|CPO|CCO|CISO|CPRO|ESG|IR|HR|PR)"
def spaced(text):
    t = re.sub(rf"(?<=[가-힣)])(?={_ROLE_ABBR}(?![A-Za-z]))", " ", text)
    t = re.sub(rf"(?<![A-Za-z])({_ROLE_ABBR})(?=[가-힣(])", r"\1 ", t)
    t = re.sub(r"(위원회|위원장|위원(?!회))(?=[가-힣]{1,12}위원)", r"\1 ", t)
    return re.sub(r"[ \t]{2,}", " ", t)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    groups = sorted(read(ROOT / "data" / "groups.csv"), key=lambda g: int(g["rank"]))
    comps = read(ROOT / "data" / "companies.csv")
    py = read(BUILT / "person_years.csv")
    ev = read(BUILT / "events.csv")
    gs = {g["group"]: g for g in read(BUILT / "group_summary.csv")}
    pay = {p["corp_code"]: p for p in read(BUILT / f"unreg_pay_{PAY_YEAR}.csv")}
    cls_rows = {(r["person_id"], r["year"], r["corp_code"]): r for r in read(BUILT / "classify_rows.csv")}
    cls_p = {r["person_id"]: r for r in read(BUILT / "classify_persons.csv")}
    name2corp = {r["company"]: r["corp_code"] for r in py}
    assets = {}
    ap = BUILT / f"assets_{PAY_YEAR}.csv"
    if ap.exists():
        assets = {r["corp_code"]: int(r["assets_krw"]) for r in read(ap) if r["assets_krw"]}
    corp_group = {c["corp_code"]: c["group"] for c in comps}
    for r in py:
        r["function"] = cls_rows.get((r["person_id"], r["year"], r["corp_code"]), {}).get("function", "미분류")

    # ---------- 연도별 인원과 사건 ----------
    c_heads, g_heads, src = collections.Counter(), collections.defaultdict(set), {}
    for r in py:
        if r["reg_class"] in EXEC:
            c_heads[(r["corp_code"], r["year"])] += 1
            g_heads[(r["group"], r["year"])].add(r["person_id"])
        src[(r["corp_code"], r["year"])] = r["source_url"]
    observed = collections.defaultdict(set)
    for (cc, y) in src:
        observed[cc].add(y)
    const_corps = {cc for cc, ys in observed.items() if set(YEARS) <= ys}  # 9개 시점 모두 관찰된 회사

    c_ev, g_ev = collections.Counter(), collections.Counter()
    new_by_group = collections.defaultdict(set)   # 그룹별 FLOW 구간 신규 인물
    for e in ev:
        cc = name2corp.get(e["company"])
        if not cc:
            continue
        g = corp_group.get(cc)
        c_ev[(cc, e["year"], e["event"])] += 1
        g_ev[(g, e["year"], e["event"])] += 1
        if e["event"] == "신규" and FLOW_START <= e["year"] <= FLOW_END:
            new_by_group[g].add(e["person_id"])

    # 연도별 직급 비공개 인원(승진을 셀 수 있는지), 2020~2025 신규 임원의 선임 시점 나이
    hid_g, hid_c = collections.Counter(), collections.Counter()
    seen_hid = set()
    for r in py:
        if r["reg_class"] in EXEC and r["position_norm"] == "직급비공개":
            hid_c[(r["corp_code"], r["year"])] += 1
            if (r["group"], r["year"], r["person_id"]) not in seen_hid:
                seen_hid.add((r["group"], r["year"], r["person_id"]))
                hid_g[(r["group"], r["year"])] += 1
    age_at = {(r["person_id"], r["year"]): int(r["age"]) for r in py}
    new_age_g = collections.defaultdict(list)
    for e in ev:
        if e["event"] == "신규" and FLOW_START <= e["year"] <= FLOW_END and (e["person_id"], e["year"]) in age_at:
            new_age_g[corp_group.get(name2corp.get(e["company"]))].append(age_at[(e["person_id"], e["year"])])
    # 회사-연도별 명단 기준일
    asof = {}
    for y in YEARS:
        p_ = BUILT / f"executives_{y}.csv"
        if p_.exists():
            for r in read(p_):
                asof.setdefault((r["corp_code"], y), r["base_date"])

    RANK_ORDER = ["회장", "부회장", "사장", "부사장", "전무", "상무", "이사(미등기)", "이사대우", "직급비공개", "전문임원"]

    def rank_age(rs):
        out = []
        for rk in RANK_ORDER:
            ages = [int(r["age"]) for r in rs if r["position_norm"] == rk]
            if ages:
                out.append({"rank": rk, "n": len(ages), "avg": round(statistics.mean(ages), 1), "min": min(ages)})
        return out

    # 승진 소요 연수: 2019년 이후 '상무'로 처음 공시(신규)된 사람이 첫 승진까지 걸린 해. 직급 체계 변경은 승진이 아니다
    first_sangmu = {}
    for e in ev:
        if e["event"] == "신규" and e["to"] == "상무" and e["year"] >= "2019":
            first_sangmu.setdefault(e["person_id"], (int(e["year"]), corp_group.get(name2corp.get(e["company"]))))
    first_up = {}
    for e in sorted(ev, key=lambda e: e["year"]):
        if e["event"] == "승진" and e["from"] == "상무" and e["person_id"] in first_sangmu:
            first_up.setdefault(e["person_id"], int(e["year"]))
    promo_raw = collections.defaultdict(lambda: {"new": 0, "years": []})
    for pid, (y0, gname) in first_sangmu.items():
        promo_raw[gname]["new"] += 1
        if pid in first_up:
            promo_raw[gname]["years"].append(first_up[pid] - y0)
    # 그룹끼리 비교할 수 있게 관찰 기간이 같은 코호트로 낸다: 2019~2021년 신규 상무 중 4년 안에 승진한 비율
    cohort = collections.defaultdict(lambda: [0, 0])
    for pid, (y0, gname) in first_sangmu.items():
        if 2019 <= y0 <= 2021:
            cohort[gname][0] += 1
            if pid in first_up and first_up[pid] - y0 <= 4:
                cohort[gname][1] += 1
    promo_g = {gname: {"new": v["new"], "n": len(v["years"]), "median": statistics.median(v["years"]) if len(v["years"]) >= 5 else None,
                       "share": pct(len(v["years"]), v["new"]),
                       "cohort_n": cohort[gname][0], "cohort_up": cohort[gname][1],
                       "cohort_pct": pct(cohort[gname][1], cohort[gname][0]) if cohort[gname][0] >= 20 else None}
               for gname, v in promo_raw.items()}
    all_years = [y for v in promo_raw.values() for y in v["years"]]
    promo_all = {"new": sum(v["new"] for v in promo_raw.values()), "n": len(all_years),
                 "median": statistics.median(all_years) if all_years else None,
                 "cohort_n": sum(v[0] for v in cohort.values()), "cohort_up": sum(v[1] for v in cohort.values()),
                 "cohort_pct": pct(sum(v[1] for v in cohort.values()), sum(v[0] for v in cohort.values()))}

    def board_x(cs):
        """이사회 구성: 사외이사 비율(그룹 합), 법정 요건 대상(자산 2조 원 이상 비금융 상장사) 중 사외이사 과반·여성 이사 있는 회사."""
        size = lambda c: c["board"]["사내이사"] + c["board"]["사외이사"] + c["board"]["기타비상무이사"]
        tot_out = sum(c["board"]["사외이사"] for c in cs)
        tot_dir = sum(size(c) for c in cs)
        mand = [c for c in cs if c.get("mandatory") and size(c) > 0]
        major = [c for c in mand if c["board"]["사외이사"] * 2 > size(c)]
        fem = [c for c in mand if c.get("_fem_dir")]
        fem_seats = sum(c.get("_fem_dir_n", 0) for c in cs)
        return {"out_ratio": pct(tot_out, tot_dir), "dir_n": tot_dir, "fem_dir_n": fem_seats,
                "fem_dir": pct(fem_seats, tot_dir) if tot_dir >= 10 else None, "n_mand": len(mand), "major_n": len(major), "fem_n": len(fem),
                "major_pct": pct(len(major), len(mand)) if len(mand) >= 2 else None, "fem_pct": pct(len(fem), len(mand)) if len(mand) >= 2 else None,
                "no_major": [c["id"] for c in mand if c not in major], "no_fem": [c["id"] for c in mand if c not in fem]}

    last_rows = [r for r in py if r["year"] == LAST]
    by_c, by_g = collections.defaultdict(list), collections.defaultdict(dict)
    board_c, board_g = collections.defaultdict(list), collections.defaultdict(dict)
    for r in last_rows:
        by_c[r["corp_code"]].append(r)
        if r["reg_class"] in EXEC:
            by_g[r["group"]][r["person_id"]] = r
        if r["reg_class"] == "사외이사":
            board_c[r["corp_code"]].append(r)
            board_g[r["group"]][r["person_id"]] = r

    # 직전 시점(전년 말) 단면: 카드의 '전년 말 대비'용
    PREV = YEARS[-2]
    prev_g = collections.defaultdict(dict)
    for r in py:
        if r["year"] == PREV and r["reg_class"] in EXEC:
            prev_g[r["group"]][r["person_id"]] = r

    def prev_kpi(rs):
        if not rs:
            return None
        return {"year": PREV, "n": len(rs), "avg_age": round(statistics.mean(int(r["age"]) for r in rs), 1),
                "female": pct(sum(r["sex"] == "여" for r in rs), len(rs))}

    def kpis(rs):
        ex = [r for r in rs if r["reg_class"] in EXEC]
        if not ex:
            return None
        ages = [int(r["age"]) for r in ex]
        n = len(ex)
        hist = collections.Counter(min(max(int(r["age"]) // 5 * 5, 35), 70) for r in ex)
        fem = sum(r["sex"] == "여" for r in ex)
        b80 = sum(r["birth_ym"].startswith("198") for r in ex)
        return {
            "n": n, "avg_age": round(statistics.mean(ages), 1), "min_age": min(ages),
            "female_n": fem, "female": pct(fem, n),
            "b70": pct(sum(r["birth_ym"].startswith("197") for r in ex), n),
            "b80_n": b80, "b80": pct(b80, n),
            "ranks": dict(collections.Counter(r["position_norm"] for r in ex)),
            "func": dict(collections.Counter(r["function"] for r in ex)),
            "age_hist": {str(k): v for k, v in sorted(hist.items())},
        }

    def person_mix(pids):
        ps = [cls_p[p] for p in pids if p in cls_p]
        info = [p for p in ps if p["edu_degree"]]
        return {
            "n": len(ps),
            "origin": dict(collections.Counter(p["origin"] for p in ps)),
            "edu_n": len(info),
            "grad": sum(p["edu_degree"] in ("박사", "석사") for p in info),
            "est": sum(p["edu_degree"] == "학사(추정)" for p in info),
            "phd": sum(p["edu_degree"] == "박사" for p in info),
            "overseas": sum(bool(p["edu_overseas"]) for p in info),
            "sky": sum(any(s in p["edu_top_school"] for s in ("서울대", "연세대", "고려대")) for p in info),
            "field": dict(collections.Counter(p["edu_field"] for p in info if p["edu_field"])),
        }

    def board_stats(rs):
        rs = list(rs)
        if not rs:
            return {"n": 0, "origin": {}}
        ages = [int(r["age"]) for r in rs]
        mix = person_mix([r["person_id"] for r in rs])
        fem = sum(r["sex"] == "여" for r in rs)
        return {"n": len(rs), "female": pct(fem, len(rs)), "female_n": fem,
                "avg_age": round(statistics.mean(ages), 1), "origin": mix["origin"]}

    def ext_hire(pids):
        """신규 임원 출신: 외부(관료·법조·학계·타그룹·외부기업 등) / 내부 / 판단 불가."""
        c = collections.Counter()
        for p in pids:
            o = cls_p.get(p, {}).get("origin", "미상")
            c["외부" if o in EXTERNAL else "내부" if o in ("그룹 내부", "오너일가") else "판단 불가"] += 1
        n = sum(c.values())
        known = c["외부"] + c["내부"]
        enough = known >= 30 and known >= 0.5 * n
        extreme = enough and c["외부"] in (0, known)   # 0%나 100%는 편입 전 경력 오판 가능성이 있어 사람 검수 전까지 비교하지 않는다
        return {"n": n, "ext": c["외부"], "int": c["내부"], "unk": c["판단 불가"],
                "known": known, "known_pct": pct(known, n), "extreme": extreme,
                "ext_pct": pct(c["외부"], known) if enough and not extreme else None,
                "ext_pct_raw": pct(c["외부"], known) if known else None, "unk_pct": pct(c["판단 불가"], n)}

    flow_years = [y for y in YEARS if FLOW_START <= y <= FLOW_END]

    def new_rate_series(heads_by_year, ev_counter, key):
        out = []
        for y in YEARS:
            prev = str(int(y) - 1)
            if y > FLOW_END or y < FLOW_START or prev not in YEARS or not heads_by_year.get(prev):
                out.append(None)
                continue
            out.append(pct(ev_counter[(key, y, "신규")], heads_by_year[prev]))
        return out

    # ---------- 회사 ----------
    companies = []
    for c in comps:
        cc = c["corp_code"]
        rs = by_c.get(cc, [])
        p = pay.get(cc, {})
        companies.append({
            "id": cc, "name": c["company"], "group": LABEL.get(c["group"], c["group"]), "market": c["market"], "fin": c["financial"] == "Y",
            "heads": [c_heads[(cc, y)] if (cc, y) in src else None for y in YEARS],
            "ev": {e: [c_ev[(cc, y, e)] for y in YEARS] for e in ("신규", "승진", "이탈", "공시확대", "직급체계변경")},
            "board": {cls: sum(r["reg_class"] == cls for r in rs) for cls in ("사내이사", "사외이사", "기타비상무이사", "감사")},
            "out": board_stats(board_c.get(cc, [])),
            "k": kpis(rs),
            "pay": {"count": int(p["pay_count"]) if p.get("pay_count") else None,
                    "avg": float(p["pay_avg_mn"]) if p.get("pay_avg_mn") else None,
                    "total": float(p["pay_total_mn"]) if p.get("pay_total_mn") else None} if p else None,
            "src": {y: src[(cc, y)] for y in YEARS if (cc, y) in src},
            "asof": {y: asof.get((cc, y)) for y in YEARS if (cc, y) in src},
            "asof_odd": [y for y in YEARS if (cc, y) in src and asof.get((cc, y)) and asof.get((cc, y))[5:] != (PARTIAL[y][5:] if y in PARTIAL else "12-31")],
            "hidden_by_year": [pct(hid_c[(cc, y)], c_heads[(cc, y)]) if c_heads[(cc, y)] else None for y in YEARS],
            "assets_tn": round(assets[cc] / 1e12, 2) if cc in assets else None,
            "mandatory": bool(cc in assets and assets[cc] >= 2e12 and c["financial"] != "Y" and not c["market"].startswith("비상장")),
            "_fem_dir_n": sum(r["sex"] == "여" and r["reg_class"] in ("사내이사", "사외이사", "기타비상무이사") for r in rs),
            "_fem_dir": any(r["sex"] == "여" and r["reg_class"] in ("사내이사", "사외이사", "기타비상무이사") for r in rs),
            "const": cc in const_corps,
        })

    # ---------- 그룹 ----------
    group_list = []
    for g in groups:
        name, rank = g["group"], int(g["rank"])
        if rank > 50:
            continue
        base = {"rank": rank, "name": LABEL.get(name, name), "ftc_name": name, "owner": g["owner"], "assets": float(g["assets_bn_krw"]), "n_aff": int(g["n_affiliates"])}
        if name not in gs:
            group_list.append({**base, "has_data": False, "reason": NO_DISCLOSURE.get(name, "임원 명단 공시가 없습니다"),
                               "companies": []})
            continue
        s = gs[name]
        heads = {y: len(g_heads[(name, y)]) for y in YEARS}
        group_list.append({
            **base, "has_data": True,
            "s": {kk: (float(v) if v not in ("", None) and kk != "group" else v) for kk, v in s.items()},
            "heads": [heads[y] or None for y in YEARS],
            "ev": {e: [g_ev[(name, y, e)] for y in YEARS] for e in ("신규", "승진", "이탈", "공시확대", "직급체계변경")},
            "new_rate": new_rate_series(heads, g_ev, name),
            "k": kpis(list(by_g[name].values())), "k_prev": prev_kpi(list(prev_g[name].values())), "mix": person_mix(list(by_g[name].keys())),
            "out": board_stats(board_g[name].values()),
            "boardx": board_x([c for c in companies if c["group"] == LABEL.get(name, name)]),
            "hidden_by_year": [pct(hid_g[(name, y)], len(g_heads[(name, y)])) if g_heads[(name, y)] else None for y in YEARS],
            "_new_ages": new_age_g[name],
            "rank_age": rank_age(list(by_g[name].values())),
            "promo": promo_g.get(name),
            "new_age": round(statistics.mean(new_age_g[name]), 1) if len(new_age_g[name]) >= 10 else None,
            "hire": ext_hire(new_by_group[name]),
            "companies": [c["id"] for c in companies if c["group"] == LABEL.get(name, name)],
            "unlisted": [c["id"] for c in companies if c["group"] == LABEL.get(name, name) and c["market"].startswith("비상장")],
        })

    # ---------- 비교 기준: 전체(풀링), 중앙값, 상위 10대 평균, 순위 ----------
    data_groups = [g for g in group_list if g["has_data"]]
    big = [g for g in data_groups if g["k"] and g["k"]["n"] >= MIN_N]
    pooled_exec = [r for d in by_g.values() for r in d.values()]
    pooled_board = [r for d in board_g.values() for r in d.values()]
    all_heads = {y: sum(len(g_heads[(g["ftc_name"], y)]) for g in data_groups) for y in YEARS}
    all_ev = collections.Counter()
    for (g, y, e), v in g_ev.items():
        all_ev[("전체", y, e)] += v

    def gval(g, m):
        k, s, mix, out, hire = g["k"] or {}, g["s"], g["mix"], g["out"], g["hire"]
        on = out.get("n", 0)
        return {
            "executives": k.get("n"), "avg_age": k.get("avg_age"), "female": k.get("female"), "b80": k.get("b80"),
            "rank_hidden": pct(k.get("ranks", {}).get("직급비공개", 0), k.get("n") or 0),
            "new_rate": s.get("new_rate_pct") or None, "exit_rate": s.get("exit_rate_pct") or None,
            "new_age": g.get("new_age"), "pay": s.get("unreg_pay_avg_mn") or None,
            "ext_hire": hire.get("ext_pct"),
            "grad": pct(mix["grad"], mix["edu_n"]) if mix["edu_n"] >= 30 and mix["edu_n"] >= 0.5 * mix["n"] and mix["est"] <= 0.5 * mix["edu_n"] else None,
            "overseas": pct(mix["overseas"], mix["edu_n"]) if mix["edu_n"] >= 30 and mix["edu_n"] >= 0.5 * mix["n"] else None,
            "out_n": on or None, "out_female": out.get("female") if on >= 5 else None,
            "out_gov": pct(sum(out["origin"].get(o, 0) for o in ("관료·공공", "법조")), on) if on >= 5 else None,
            "out_acad": pct(out["origin"].get("학계", 0), on) if on >= 5 else None,
            "out_ratio": g["boardx"]["out_ratio"], "out_major": g["boardx"]["major_pct"], "fem_board": g["boardx"]["fem_pct"],
            "fem_dir": g["boardx"]["fem_dir"],
        }.get(m)

    METRICS = ["out_ratio", "fem_dir",
               "executives", "avg_age", "female", "b80", "rank_hidden", "new_rate", "exit_rate", "new_age", "pay",
               "ext_hire", "grad", "overseas", "out_n", "out_female", "out_gov", "out_acad"]
    pm = person_mix([r["person_id"] for r in pooled_exec])
    pooled_hire = ext_hire(set().union(*new_by_group.values()))
    ob = board_stats(pooled_board)
    tot_pay = [(float(p["pay_total_mn"]), int(p["pay_count"])) for p in pay.values() if p.get("pay_total_mn") and p.get("pay_count")]
    pooled = {
        "executives": len(pooled_exec), "avg_age": round(statistics.mean(int(r["age"]) for r in pooled_exec), 1),
        "female": pct(sum(r["sex"] == "여" for r in pooled_exec), len(pooled_exec)),
        "b80": pct(sum(r["birth_ym"].startswith("198") for r in pooled_exec), len(pooled_exec)),
        "rank_hidden": pct(sum(r["position_norm"] == "직급비공개" for r in pooled_exec), len(pooled_exec)),
        "new_rate": round(statistics.mean(pct(all_ev[("전체", y, "신규")], all_heads[str(int(y) - 1)]) for y in flow_years), 1),
        "exit_rate": round(statistics.mean(pct(all_ev[("전체", y, "이탈")], all_heads[str(int(y) - 1)]) for y in flow_years), 1),
        "new_age": None,
        "pay": round(sum(a for a, _ in tot_pay) / sum(b for _, b in tot_pay)) if tot_pay else None,
        "ext_hire": None, "grad": pct(pm["grad"], pm["edu_n"]), "overseas": pct(pm["overseas"], pm["edu_n"]),
        "out_n": ob["n"], "out_female": ob.get("female"),
        "out_gov": pct(sum(ob["origin"].get(o, 0) for o in ("관료·공공", "법조")), ob["n"]),
        "out_acad": pct(ob["origin"].get("학계", 0), ob["n"]),
        "out_ratio": None, "fem_dir": None,   # 아래에서 전체 회사로 채운다
    }
    suff = [g["hire"] for g in data_groups if g["hire"].get("ext_pct") is not None]
    pooled["ext_hire"] = pct(sum(h["ext"] for h in suff), sum(h["known"] for h in suff)) if suff else None   # 근거 충분 그룹만 모은 값
    bx_all = board_x(companies)
    pooled["out_ratio"], pooled["fem_dir"] = bx_all["out_ratio"], bx_all["fem_dir"]
    all_new_ages = [a for g in data_groups for a in g.pop("_new_ages", [])]
    pooled["new_age"] = round(statistics.mean(all_new_ages), 1) if all_new_ages else None
    top10 = [g for g in data_groups if g["rank"] <= 10]
    bench = {}
    for m in METRICS:
        vals = [(g, gval(g, m)) for g in big]
        vals = [(g, v) for g, v in vals if v is not None]
        t10 = [gval(g, m) for g in top10 if gval(g, m) is not None]
        bench[m] = {"all": pooled.get(m), "median": round(statistics.median(v for _, v in vals), 1) if vals else None,
                    "top10": round(statistics.mean(t10), 1) if t10 else None, "n": len(vals)}
        for i, (g, v) in enumerate(sorted(vals, key=lambda x: -x[1]), 1):
            g.setdefault("rank_of", {})[m] = i
    for g in data_groups:
        g["v"] = {m: gval(g, m) for m in METRICS}
        g["small"] = not (g["k"] and g["k"]["n"] >= MIN_N)
        g.setdefault("rank_of", {})

    const_set = collections.defaultdict(set)
    for r in py:
        if r["reg_class"] in EXEC and r["corp_code"] in const_corps:
            const_set[r["year"]].add((r["group"], r["person_id"]))
    const_heads = [len(const_set[y]) for y in YEARS]
    for c in companies:
        c.pop("_fem_dir", None)
        c.pop("_fem_dir_n", None)
    core = {
        "meta": {"years": YEARS, "as_of": PARTIAL.get(LAST, f"{LAST}-12-31"), "flow": [FLOW_START, FLOW_END], "flow_end": FLOW_END,
                 "pay_year": PAY_YEAR, "partial": PARTIAL, "min_n": MIN_N, "built": datetime.date.today().isoformat(),
                 "owner": SITE_OWNER, "contact": SITE_CONTACT,
                 "n_people": len({r["person_id"] for r in py}), "n_rows": len(py), "n_companies": len(companies),
                 "n_groups_data": len(data_groups), "n_const": len(const_corps),
                 "execs_last": sum(len(v) for v in by_g.values()),
                 "funcs": FUNCS, "origins": ORIGINS, "external": sorted(EXTERNAL),
                 "promo_all": promo_all,
                "asof_check": {"n": sum(len(c["asof"]) for c in companies), "odd": sum(len(c["asof_odd"]) for c in companies),
                               "blank": sum(1 for c in companies for y, v in c["asof"].items() if not v)},
                "const_expand": [sum(c_ev[(cc, y, "공시확대")] for cc in const_corps) for y in YEARS],
                "validation": {"api_reg_match": "99.2%", "pay_count_gap": "+0.5~+4.6%", "start_year_match": "92.0% (±1년 98.1%)",
                                "classify_sample": "기능 150행, 학력·출신 120명", "classify": {"기능": "94.0%", "최종 학위": "90.8%", "전공": "90.8%", "해외 학위": "94.2%", "출신": "92.5%"}}},
        "bench": bench,
        "total": {"heads": [all_heads[y] or None for y in YEARS], "const_heads": const_heads,
                  "k_prev": prev_kpi([r for d in prev_g.values() for r in d.values()]),
                  "ev": {e: [all_ev[("전체", y, e)] for y in YEARS] for e in ("신규", "승진", "이탈", "공시확대", "직급체계변경")},
                  "new_rate": new_rate_series(all_heads, all_ev, "전체"),
                  "func": dict(collections.Counter(r["function"] for r in pooled_exec)),
                  "mix": pm, "out": ob, "hire": pooled_hire},
        "groups": group_list, "companies": companies,
    }
    # ---------- 인물 ----------
    cidx = {c["id"]: i for i, c in enumerate(companies)}
    gidx = {g["ftc_name"]: i for i, g in enumerate(group_list)}
    rows_by_p = collections.defaultdict(list)
    for r in py:
        rows_by_p[r["person_id"]].append(r)
    ev_by_p = collections.defaultdict(list)
    for e in ev:
        cc = name2corp.get(e["company"])
        ev_by_p[e["person_id"]].append([int(e["year"]), EVENTS.index(e["event"]), cidx.get(cc, -1), e["from"], e["to"]])
    people = []
    for pid, rs in rows_by_p.items():
        rs.sort(key=lambda r: (r["year"], r["company"]))
        last = rs[-1]
        cp = cls_p.get(pid, {})
        people.append([
            int(pid[1:]), last["name"], last["birth_ym"], 1 if last["sex"] == "여" else 0, gidx.get(last["group"], -1),
            [[int(r["year"]), cidx.get(r["corp_code"], -1), REG.index(r["reg_class"]) if r["reg_class"] in REG else 6,
              spaced(r["position"].replace("\n", " ").strip()), RANKS.index(r["position_norm"]) if r["position_norm"] in RANKS else len(RANKS) - 1,
              spaced(r["duty"].replace("\n", " ").strip())[:60], FUNCS.index(r["function"]) if r["function"] in FUNCS else FUNCS.index("미분류")]
             for r in rs],
            ev_by_p.get(pid, []),
            "\n".join(l.strip() for l in last["career"].split("\n") if l.strip())[:500],
            last["education"].replace("\n", " ").strip()[:80],
            ORIGINS.index(cp["origin"]) if cp.get("origin") in ORIGINS else ORIGINS.index("미상"),
            cp.get("origin_evidence", "")[:80],
            [DEGREES.index(cp.get("edu_degree", "")) if cp.get("edu_degree", "") in DEGREES else 4,
             FIELDS.index(cp.get("edu_field", "")) if cp.get("edu_field", "") in FIELDS else 6,
             1 if cp.get("edu_overseas") else 0, cp.get("edu_top_school", "")],
        ])
    people_txt = json.dumps({"reg": REG, "ranks": RANKS, "events": EVENTS, "funcs": FUNCS, "origins": ORIGINS,
                             "degrees": DEGREES, "fields": FIELDS, "people": people}, ensure_ascii=False, separators=(",", ":"))
    (OUT / "people.json").write_text(people_txt, encoding="utf-8")
    (OUT / "core.json").write_text(json.dumps(core, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    build_html()
    for f in ("core.json", "people.json"):
        print(f"{f}: {(OUT / f).stat().st_size / 1e6:.1f}MB")


def build_html():
    """site/index.html(본문 조각) → docs/index.html(완전한 HTML 문서)."""
    src = SITE_SRC.read_text(encoding="utf-8")
    cut = src.index("</style>") + len("</style>")
    head = "\n".join(line for line in src[:cut].splitlines() if not line.startswith(("<meta charset", '<meta name="viewport"')))
    doc = ('<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
           '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
           '<meta name="robots" content="noindex">\n'
           f"{head}\n</head>\n<body>\n{src[cut:].strip()}\n</body>\n</html>\n")
    (DOCS / "index.html").write_text(doc, encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")


if __name__ == "__main__":
    main()
