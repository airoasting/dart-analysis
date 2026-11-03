"""분류 사전 v1을 적용한다: 기능(사람-연도), 학력·출신(사람).

사용법:
    python3 -I pipeline/08_classify.py

입력:  pipeline/taxonomy_rules.json (규칙), data/groups.csv, data/built/person_years.csv
출력:
    data/built/classify_rows.csv      person_id, year, corp_code, function, function_kw, function_conf
    data/built/classify_persons.csv   사람별 학력(최종 학위, 전공 계열, 해외 학위, 주요 국내 대학)과 출신(주 출신, 태그, 다른 그룹 언급)
    data/built/classify_report.md     축별 채움률과 분포, 그룹별 요약

원칙:
  - 모든 분류값에는 근거 키워드(_kw)를 함께 저장한다. 근거가 없으면 '미분류'이고 지어내지 않는다.
  - 규칙 순서가 우선순위다. 기준은 taxonomy.md.
"""
import collections
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILT = ROOT / "data" / "built"
R = json.load(open(ROOT / "pipeline" / "taxonomy_rules.json", encoding="utf-8"))
EXEC = ("미등기", "사내이사", "집행임원")
BOUND = set(R["latin_tokens_need_boundary"])


def norm(t):
    return re.sub(r"\s+", "", (t or "").upper())


def find(text_up, text_ns, kw):
    """kw가 있으면 위치를, 없으면 -1. 경계가 필요한 영문 약어는 띄어쓰기를 살린 원문에서 앞뒤가 영문이 아닐 때만."""
    k = kw.upper()
    if k in BOUND or re.fullmatch(r"[A-Z0-9&.\- ]+", k):  # 영문 표현은 길이와 관계없이 단어 경계로
        m = re.search(rf"(?<![A-Z]){re.escape(k)}(?![A-Z])", text_up)
        return m.start() if m else -1
    return text_ns.find(k.replace(" ", ""))


def first_category(text, cats, weak=()):
    """규칙 순서대로 처음 맞는 범주. (key, keyword, conf)
    weak(전략·혁신·기술 같은 수식어)는 다른 구체적 키워드가 하나도 없을 때만 쓴다: 'B2C유통전략' → 영업·마케팅."""
    up, ns = (text or "").upper(), norm(text)
    weak = set(weak)
    generic = set(R.get("function_generic", []))
    # 1) 구체적 기능 키워드 → 2) 수식어(전략·혁신·기술…) → 3) 일반 직함(경영지원, 사업부문장)
    for stage in ("specific", "weak", "generic"):
        for c in cats:
            if (c["key"] in generic) != (stage == "generic"):
                continue
            for kw in c["kw"]:
                if (kw in weak) != (stage == "weak"):
                    continue
                if find(up, ns, kw) >= 0:
                    return c["key"], kw, ("medium" if stage != "specific" else c.get("conf", "high"))
    return None, "", ""


COMMITTEE = re.compile(r"[가-힣A-Za-z·ㆍ&]*위원회\s*(위원장|위원|의장)?|[가-힣A-Za-z]*(산하|소속)")


def function_of(duty, position, position_norm):
    if any(k in norm(position) for k in R["function_top_position"]):
        return "최고경영", position.strip(), "high"
    byp = R["function_by_position"].get(position_norm)
    if byp:
        return byp, position_norm, "high"
    text = COMMITTEE.sub(" ", duty or "").strip() or (duty or "")  # 위원회 겸직·소속 표시는 보직이 아니다
    fn, kw, conf = first_category(text, R["function"], R.get("function_weak", []))
    if not fn:
        fn, kw, conf = first_category(position, R["function"], R.get("function_weak", []))
    return (fn, kw, conf) if fn else ("미분류", "", "")


def earliest_category(text, cats):
    """문구에서 가장 먼저 나오는 범주 (전공 계열용: 보통 학부 전공이 먼저 나온다)."""
    up, ns = (text or "").upper(), norm(text)
    best = (10 ** 9, None, "")
    for c in cats:
        for kw in c["kw"]:
            pos = find(up, ns, kw)
            if 0 <= pos < best[0]:
                best = (pos, c["key"], kw)
    return best[1], best[2]


SCHOOL_RE = re.compile(
    r"대학교|대학원|대학|UNIV|COLLEGE|INSTITUTE|SCHOOL|MBA|PH\.?D|학사|석사|박사|졸업|學|碩|博"
    r"|[가-힣]{2,6}대(?=\s|$|[/,(·)]|학사|석사|박사|졸업|[가-힣]{1,6}(학과|학부|공학|경영|경제|법학|학))"
    r"|[가-힣]{1,6}대\s*\(?[가-힣]{0,8}(학사|석사|박사|졸업|학과|학부|공학|경영|경제|법학|수학|물리|화학)", re.I)
NOT_SCHOOL = ("현대", "시대", "세대", "확대", "극대", "증대", "강대", "거대", "군대", "부대", "무대", "반대", "상대",
              "절대", "최대", "초대", "역대", "당대", "년대", "연대보증", "대대", "기대", "교대근무")
SEG_SPLIT = re.compile(r"[\n/,·•;|]|\s-\s|現\)?|前\)?|\(현\)|\(전\)|현\)|전\)|(?<=\))")


def edu_segments(texts):
    segs = []
    for t in texts:
        for s in SEG_SPLIT.split(t or ""):
            s = (s or "").strip()
            if s and SCHOOL_RE.search(s):
                m = SCHOOL_RE.search(s)
                if m and any(m.group(0).startswith(n) or m.group(0).endswith(n) for n in NOT_SCHOOL) and not re.search(
                        r"학사|석사|박사|졸업|대학교|UNIV|MBA", s, re.I):
                    continue
                segs.append(re.sub(r"박사\s*후|박사후연구원|POST\s*-?DOC\w*", "", s, flags=re.I))
    return segs


def education(texts):
    segs = edu_segments(texts)
    segs = [s for s in segs if "중퇴" not in s] or segs  # 중퇴 조각은 학위로 보지 않는다
    joined = " / ".join(segs)
    up, ns = joined.upper(), norm(joined)
    degree, degree_kw = None, ""
    for level in ("박사수료", "박사", "석사", "학사"):
        for kw in R["degree"][level]:
            if find(up, ns, kw) >= 0:
                if level == "박사수료":  # 수료는 학위가 아니다. 박사 학위 표현이 따로 없으면 석사로 본다
                    pass
                    if not any(find(up, ns.replace(kw.upper(), ""), k) >= 0 for k in ("박사", "PH.D", "PHD")):
                        degree, degree_kw = "석사", kw
                        break
                    continue
                degree, degree_kw = level, kw
                break
        if degree:
            break
    if degree == "석사" and re.search(r"석사\s*(과정)?\s*수료|석사과정", joined) and not re.search(r"석사(?!\s*(과정)?\s*수료|과정)", joined):
        degree, degree_kw = "학사", "석사수료"
    if not degree and segs:
        degree, degree_kw = "학사(추정)", "학교명만 있음"
    # 전공: 학사·학부 조각이 있으면 그 조각의 전공을, 없으면 가장 먼저 나오는 전공
    bach = [s for s in segs if re.search(r"학사|學士|\(學\)|학부|B\.?S|B\.?A|졸업", s, re.I)]
    field, field_kw = (None, "")
    if bach:
        field, field_kw = earliest_category(" / ".join(bach), R["field"])
    if not field and segs:
        field, field_kw = earliest_category(joined, R["field"])
    overseas = ""
    for kw in R["overseas_school"]:
        if find(up, ns, kw) >= 0:
            overseas = kw
            break
    if not overseas:  # 목록 밖 해외 학교: 영문 학교명(대학교 같은 한글 학교명 없이)과 학위 표현이 함께 있는 조각
        for s_ in segs:
            if re.search(r"[A-Za-z]{4,}", s_) and re.search(r"MBA|PH\.?D|M\.?S|B\.?S|석사|박사|학사|대학원", s_, re.I) \
                    and not re.search(r"[가-힣]{2,}(대학교|대학|대)(?![가-힣])", s_) and not re.search(r"KAIST|POSTECH|SKKU|KDI", s_, re.I):
                overseas = "영문 학교명"
                break
    tops = []
    for school, kws in R["domestic_top"].items():
        pos = min([p for p in (find(up, ns, k) for k in kws) if p >= 0], default=-1)
        if pos >= 0:
            tops.append((pos, school))
    return {
        "edu_degree": degree or "", "edu_degree_kw": degree_kw, "edu_field": field or "", "edu_field_kw": field_kw,
        "edu_overseas": "Y" if overseas else "", "edu_overseas_kw": overseas,
        "edu_top_school": "|".join(s for _, s in sorted(tops)), "edu_text": joined[:200],
    }


def _company_core(name):
    n = re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사|\(합\)|\(재\)|\s", "", name or "")
    return n


def build_group_tokens():
    """그룹 → 표현 목록. 규칙 파일의 브랜드 + 보강 표현 + 공정위 소속회사 이름(4자 이상)."""
    toks = {g: list(ts) for g, ts in R["group_tokens"].items()}
    for g, ts in R.get("group_tokens_extra", {}).items():
        toks.setdefault(g, []).extend(ts)
    aff_path = ROOT / "data" / "affiliates_2026.csv"
    if aff_path.exists():
        for a in csv.DictReader(open(aff_path, encoding="utf-8")):
            core = _company_core(a["ftc_name"])
            if len(core) >= 4 and not re.search(r"사모|투자회사|투자조합|펀드|제\d+호|위탁관리|유동화|신탁", core):
                toks.setdefault(a["group"], []).append(core)
    flat = sorted({(t.upper(), g) for g, ts in toks.items() for t in ts}, key=lambda x: -len(x[0]))
    return flat


GROUP_TOKENS = None


def group_mentions(text, own_group):
    """경력 문구에 나오는 그룹. 긴 표현부터 맞추고 맞은 자리는 지워서 KT&G가 KT로 잡히지 않게 한다."""
    global GROUP_TOKENS
    if GROUP_TOKENS is None:
        GROUP_TOKENS = build_group_tokens()
    up = (text or "").upper()
    ns_up = re.sub(r"\s+", "", up)
    found = collections.OrderedDict()
    for tok, g in GROUP_TOKENS:
        latin = re.fullmatch(r"[A-Z0-9&.\- ]+", tok) is not None
        if latin:
            pat = rf"(?<![A-Z]){re.escape(tok)}(?![A-Z])"
            m = re.search(pat, up)
            if m:
                found.setdefault(g, tok)
                up = re.sub(pat, " " * len(tok), up)
        else:
            t = tok.replace(" ", "")
            if t in ns_up:
                found.setdefault(g, tok)
                ns_up = ns_up.replace(t, "#" * len(t))
                up = up.replace(tok, " " * len(tok))
    others = [(g, t) for g, t in found.items() if g != own_group]
    return own_group in found, others


ORIGIN_SPLIT = re.compile(r"[\n/,·•;|]|\s-\s|^-|(?<=[가-힣a-zA-Z)\]])-(?=\s?[가-힣A-Za-z(])|現\)?|前\)?|\(현\)|\(전\)|현\)|전\)|겸\)")


def segments(text):
    """출신 판정용 조각. 괄호 뒤에서는 자르지 않는다: '(주)현대백화점'이 '(주)'와 '현대백화점'으로 갈라지지 않게."""
    return [x.strip() for x in ORIGIN_SPLIT.split(text or "") if x and len(x.strip()) >= 3]


def origin(texts, relation_texts, name, group, owners):
    text = " / ".join(t for t in texts if t)
    rel = norm(" ".join(relation_texts))
    for x in R.get("owner_relation_strip", []):
        rel = rel.replace(x, "")
    tags, evidence = [], []
    if owners.get(group) and re.sub(r"\s+", "", name) == owners[group]:
        tags.append("오너일가"); evidence.append("공정위 동일인")
    elif any(k in rel for k in R["owner_relation"]):
        k = next(k for k in R["owner_relation"] if k in rel)
        tags.append("오너일가"); evidence.append(f"관계:{k}")

    cats = {c["key"]: c["kw"] for c in R["origin"]}
    ranks = R.get("origin_committee_rank", [])
    weak_academic = R.get("origin_weak", {}).get("학계", [])
    segs = segments(text)
    seg_tags = collections.OrderedDict()
    external_orgs = []
    for sg in segs:
        up_, ns_ = sg.upper(), norm(sg)
        own_here, others_here = group_mentions(sg, group)
        hit_here = False
        for key, kws in cats.items():
            kw = next((k for k in kws if find(up_, ns_, k) >= 0), None)
            if not kw:
                continue
            if key == "관료·공공" and "위원" in ns_ and not any(rk in ns_ for rk in ranks):
                continue  # 정부 위원회 위촉 위원은 관료 경력으로 보지 않는다
            if key == "법조" and kw == "변호사" and (own_here or "사내" in ns_):
                continue  # 사내변호사
            if key == "학계" and any(w in ns_ for w in weak_academic) and not re.search(r"(?<!초빙|겸임|객원|자문|외래)교수", ns_):
                seg_tags.setdefault("학계(겸임)", kw)
                hit_here = True
                continue
            seg_tags.setdefault(key, kw)
            hit_here = True
        if not hit_here and not own_here and not others_here and any(find(up_, ns_, m) >= 0 for m in R.get("origin_org_markers", [])) \
                and not re.search(r"대학교|대학원|학사|석사|박사|졸업|UNIV|COLLEGE", sg, re.I):
            external_orgs.append(sg[:30])
    for key, kw in seg_tags.items():
        if key == "학계(겸임)":
            continue
        tags.append(key); evidence.append(kw)
    own, others = group_mentions(text, group)
    if others:
        tags.append("타 그룹 출신"); evidence.append(",".join(t for _, t in others))
    if external_orgs:
        tags.append("외부 기업·기관"); evidence.append(external_orgs[0])
    if "학계(겸임)" in seg_tags and not any(t for t in tags if t != "오너일가"):
        tags.append("학계"); evidence.append(seg_tags["학계(겸임)"])
    order = ["오너일가", "관료·공공", "법조", "학계", "회계·세무", "컨설팅", "군", "언론", "타 그룹 출신", "외국계 기업"]
    primary = next((o for o in order if o in tags), None)
    if not primary:
        # 자기 그룹 이름이 나오면 그룹 내부. 외부 기업·기관은 자기 그룹 이름 없이 바깥 회사·기관만 나올 때
        if own:
            primary = "그룹 내부"
        elif "외부 기업·기관" in tags:
            primary = "외부 기업·기관"
        else:
            primary = "미상" if not text.strip() or text.strip() == "-" else "미상(경력 짧음)"
    return {"origin": primary, "origin_tags": "|".join(tags), "origin_evidence": "|".join(evidence),
            "other_groups": "|".join(g for g, _ in others)}


def main():
    py = list(csv.DictReader(open(BUILT / "person_years.csv", encoding="utf-8-sig")))
    owners = {g["group"]: re.sub(r"\s+", "", g["owner"]) for g in csv.DictReader(open(ROOT / "data" / "groups.csv", encoding="utf-8"))}
    rel_by = collections.defaultdict(list)
    for r in csv.DictReader(open(BUILT / "executives_2025.csv", encoding="utf-8-sig")):
        pass  # relation은 person_years에 없으므로 연도별 원본에서 모은다
    for y in range(2018, 2026):
        for r in csv.DictReader(open(BUILT / f"executives_{y}.csv", encoding="utf-8-sig")):
            if r["relation"].strip() not in ("", "-"):
                rel_by[(r["corp_code"], r["name_key"], r["birth_ym"])].append(r["relation"])

    # 1) 기능: 사람-연도 단위
    rows_out = []
    for r in py:
        if r["reg_class"] not in EXEC:
            fn, kw, conf = "이사회(비상근)", r["reg_class"], "high"
        else:
            fn, kw, conf = function_of(r["duty"], r["position"], r["position_norm"])
        rows_out.append({"person_id": r["person_id"], "year": r["year"], "corp_code": r["corp_code"],
                         "function": fn, "function_kw": kw, "function_conf": conf})
    with open(BUILT / "classify_rows.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        w.writeheader()
        w.writerows(rows_out)

    # 2) 학력·출신: 사람 단위 (모든 연도의 학력·경력 문구를 모은다)
    by_p = collections.defaultdict(list)
    for r in py:
        by_p[r["person_id"]].append(r)
    persons = []
    for pid, rs in by_p.items():
        rs.sort(key=lambda r: r["year"])
        last = rs[-1]
        texts = list(dict.fromkeys([r["education"] for r in rs] + [r["career"] for r in rs]))
        rels = []
        for r in rs:
            rels += rel_by.get((r["corp_code"], re.sub(r"\s+", "", r["name"]), r["birth_ym"]), [])
        e = education(texts)
        o = origin([r["career"] for r in rs], rels, last["name"], last["group"], owners)
        board = "사외이사" if any(r["reg_class"] == "사외이사" for r in rs) else ("경영진" if any(r["reg_class"] in EXEC for r in rs) else "기타")
        persons.append({"person_id": pid, "name": last["name"], "group": last["group"], "role": board, **e, **o})
    with open(BUILT / "classify_persons.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(persons[0].keys()))
        w.writeheader()
        w.writerows(persons)

    # 3) 리포트
    md = ["# 분류 리포트 (분류 사전 v1)\n", "자동 생성: `pipeline/08_classify.py`. 기준은 `taxonomy.md`.\n"]
    ex_rows = [r for r in rows_out if r["function"] != "이사회(비상근)"]
    last_year = max(r["year"] for r in rows_out)
    ex_last = [r for r in ex_rows if r["year"] == last_year]
    c = collections.Counter(r["function"] for r in ex_last)
    md += [f"## 기능 (경영진, {last_year} 최신 보고서 기준일 {len(ex_last):,}행)\n", "| 기능 | 인원 | 비율 |", "|---|---|---|"]
    md += [f"| {k} | {v:,} | {v / len(ex_last):.1%} |" for k, v in c.most_common()]
    for role in ("경영진", "사외이사"):
        ps = [p for p in persons if p["role"] == role]
        md += [f"\n## 학력 ({role} {len(ps):,}명)\n"]
        cov = sum(1 for p in ps if p["edu_degree"])
        md.append(f"- 학력 정보가 있는 사람: {cov:,}명 ({cov / len(ps):.1%})")
        md.append("- 최종 학위: " + ", ".join(f"{k or '없음'} {v:,}" for k, v in collections.Counter(p["edu_degree"] for p in ps).most_common()))
        md.append("- 전공 계열: " + ", ".join(f"{k or '없음'} {v:,}" for k, v in collections.Counter(p["edu_field"] for p in ps).most_common()))
        md.append(f"- 해외 학위 언급: {sum(1 for p in ps if p['edu_overseas']):,}명")
        tops = collections.Counter(s for p in ps for s in p["edu_top_school"].split("|") if s)
        md.append("- 주요 국내 대학 언급: " + ", ".join(f"{k} {v:,}" for k, v in tops.most_common()))
        md += [f"\n## 출신 ({role} {len(ps):,}명)\n", "| 주 출신 | 인원 | 비율 |", "|---|---|---|"]
        md += [f"| {k} | {v:,} | {v / len(ps):.1%} |" for k, v in collections.Counter(p["origin"] for p in ps).most_common()]
    flows = collections.Counter((og, p["group"]) for p in persons if p["role"] == "경영진" for og in p["other_groups"].split("|") if og)
    md += ["\n## 다른 그룹 이름이 경력에 나오는 경영진 (상위 20개 흐름: 경력에 나온 그룹 → 현재 그룹)\n", "| 경력에 나온 그룹 | 현재 그룹 | 인원 |", "|---|---|---|"]
    md += [f"| {a} | {b} | {n} |" for (a, b), n in flows.most_common(20)]
    (BUILT / "classify_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"기능 {len(rows_out):,}행, 사람 {len(persons):,}명 → data/built/classify_*.csv")


if __name__ == "__main__":
    main()
