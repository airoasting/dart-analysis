"""회사별 회계감사인(회계법인)과 감사의견, 감사보수·감사시간을 연도별로 받아 정리한다.

사용법:
    python3 -I pipeline/11_audit.py --fetch      # OpenDART에서 받기(이미 받은 것은 건너뜀) + 정리
    python3 -I pipeline/11_audit.py              # 받아 둔 원문으로 정리만

받는 API (OpenDART 정기보고서 주요정보, 회사-연도마다 1건씩)
    accnutAdtorNmNdAdtOpinion  회계감사인의 명칭 및 감사의견. 보고서 한 건에 당기·전기·전전기 행이 같이 오고
                               같은 행이 두 번씩(연결·별도) 온다. 그해 보고서의 '당기' 행만 쓴다.
    adtServcCnclsSttus         감사용역 체결현황. 감사인, 보수, 감사시간(계약·실제). 역시 '당기' 행만 쓴다.
    2018~2025 사업보고서 + 2026 반기보고서. 반기보고서는 감사가 아니라 검토라 감사의견이 없고, 감사인 이름만 쓴다.

대상 회사-연도는 data/raw/reports_<연도>.csv에 보고서가 있는 행만 (02_fetch.py 결과).
원문 사본: data/raw/audit/<corp_code>_<연도>_<API>.json
결과 (data/built/)
    audit.csv          회사-연도 감사인(원문 이름, 묶음 이름), 감사의견, 강조사항, 핵심감사사항, 감사보수(백만 원), 감사시간
    audit_report.md    수집·정리 점검표(감사인 교체, 적정 아닌 의견, 단위를 알 수 없어 비운 보수)

감사보수 단위: API가 단위를 주지 않는다(공시 표마다 원·천 원·백만 원이 섞인다). 그래서 실제 감사시간으로
나눈 시간당 보수가 상식 범위(3만~150만 원)에 드는 단위 하나만 쓰고, 해당 단위가 없거나 둘 이상이면 비운다.
"""
import argparse
import collections
import csv
import importlib.util
import json
import re
import concurrent.futures as cf
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
CACHE = RAW / "audit"
BUILT = ROOT / "data" / "built"
FY_YEARS = list(range(2018, 2026))
H1_YEAR = 2026
APIS = ["accnutAdtorNmNdAdtOpinion", "adtServcCnclsSttus"]

_spec = importlib.util.spec_from_file_location("pay_staff", Path(__file__).with_name("10_pay_staff.py"))
_ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ps)
get, num = _ps.get, _ps.num
_ps.MIN_INTERVAL = 0.35


def jobs():
    out = []
    for y in FY_YEARS + [H1_YEAR]:
        path = RAW / f"reports_{y}.csv"
        if not path.exists():
            continue
        rc = "11012" if y == H1_YEAR else "11011"
        for r in csv.DictReader(open(path, encoding="utf-8")):
            if r["rcept_no"]:
                for api in APIS:
                    out.append((r["corp_code"], y, api, rc, r))
    return out


def cache_path(cc, y, api):
    return CACHE / f"{cc}_{y}_{api}.json"


def fetch_all(js):
    CACHE.mkdir(parents=True, exist_ok=True)
    todo = [j for j in js if not cache_path(j[0], j[1], j[2]).exists()]
    print(f"받을 것 {len(todo)}건 / 전체 {len(js)}건 (예상 {len(todo) * _ps.MIN_INTERVAL / 60:.0f}분)", flush=True)

    def one(j):
        cc, y, api, rc, _ = j
        data = get(api, corp_code=cc, bsns_year=str(y), reprt_code=rc)
        d = json.loads(data)
        if d.get("status") in ("020", "800", "901"):  # 사용 한도 초과·점검·키 오류: 저장하지 않고 멈춘다
            raise SystemExit(f"OpenDART 응답 {d.get('status')}: {d.get('message')}")
        cache_path(cc, y, api).write_bytes(data)

    done = 0
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        for fut in cf.as_completed([ex.submit(one, j) for j in todo]):
            fut.result()
            done += 1
            if done % 500 == 0:
                print(f"  {done}/{len(todo)}", flush=True)


def load(cc, y, api):
    p = cache_path(cc, y, api)
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return d.get("list", []) if d.get("status") == "000" else []


BIG4 = [("삼일", r"삼일|PwC|Pricewaterhouse"), ("삼정", r"삼정|KPMG"), ("안진", r"안진|Deloitte|딜로이트"), ("한영", r"한영|Ernst|\bEY\b|EY한영")]


def clean_name(s):
    """'삼일회계법인\\n대표공인회계사\\n김영식' → '삼일회계법인'. 원문 이름의 첫 줄에서 대표 이름·공백을 걷는다."""
    s = re.split(r"\n|대표\s*공인회계사|대표이사|\s{2,}", str(s or ""))[0]
    s = re.sub(r"\s+", " ", s).strip(" ,.-")
    if s in ("", "-", "해당사항없음", "해당사항 없음", "N/A"):
        return ""
    # 칸이 밀려 감사의견·기수가 감사인 칸에 들어간 행(삼원강재 2018 '적정', 세아베스틸지주 2021 '제67기')은 감사인으로 보지 않는다
    if re.match(r"^(?:적정|한정|부적정|의견거절|제\s*\d+\s*기|\d)", s):
        return ""
    return s


def std_name(s):
    """묶어 보는 이름: 4대 회계법인은 '삼일'·'삼정'·'안진'·'한영', 그 밖은 '회계법인'·'(유)'·'(주)'·공백을 뗀 이름."""
    for k, pat in BIG4:
        if re.search(pat, s, re.I):
            return k
    t = re.sub(r"\([^)]*\)|회계법인|감사반|유한|주식회사|\*|\s", "", s)   # '영앤진(*)', '서현(구,서일)' 같은 괄호 메모는 뗀다
    return t or s


def period_rows(rows, y, back=0):
    """보고서에서 당기(back=0)·전기(1)·전전기(2) 행만 고른다.
    '(당기)' 표시 → '제N기' 기수(가장 큰 기수 - back) 순서로 본다. 중복 행(연결·별도)은 하나로."""
    rows = [r for r in rows if clean_name(r.get("adtor"))]
    if not rows:
        return [], ""
    yb = lambda r: re.sub(r"\s", "", r.get("bsns_year") or "")
    by = lambda f: [r for r in rows if f(yb(r))]
    tag = ["당기", "전기", "전전기"][back]
    # 기수를 먼저 본다: 감사용역 표는 행마다 '제56기(당기)'처럼 그해 기준 '당기'를 적는 회사가 있어 '당기' 표시만으로는 세 해가 다 잡힌다
    KI = r"제(\d+)(?:\((?:당|전|전전)\))?기"   # '제57기', '제12(당)기', '제 54 (당) 기'
    ks = [int(m.group(1)) for r in rows for m in [re.search(KI, yb(r))] if m]
    cur, how = (by(lambda b: any(int(m.group(1)) == max(ks) - back for m in re.finditer(KI, b))), "기수") if ks else ([], "")
    if not cur:
        cur, how = by(lambda b: re.search(rf"(?<!전){tag}", b)), f"{tag} 표시"
    if not cur and back == 0 and len({clean_name(r["adtor"]) for r in rows}) == 1 and len(rows) <= 2:
        cur, how = rows, "행이 하나뿐"
    seen, out = set(), []
    for r in cur:
        k = tuple(sorted((k, v) for k, v in r.items() if k != "rcept_no"))
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out, how if out else ""


def fee_mn(fee, hours):
    """감사보수(단위 모름) → 백만 원. 실제 감사시간으로 나눈 시간당 보수가 3만~150만 원에 드는 단위가 하나일 때만."""
    if not fee or not hours:
        return None, "보수·시간 없음"
    ok = [u for u in (1, 1e3, 1e6) if 3e4 <= fee * u / hours <= 1.5e6]
    if len(ok) != 1:
        return None, "단위 판정 불가"
    v = fee * ok[0] / 1e6
    if not 10 <= v <= 5e4:   # 상장사 감사보수가 1천만 원 미만이거나 500억 원 초과면 단위 판정이 틀린 것으로 본다
        return None, "범위 밖"
    return round(v, 1), {1: "원", 1e3: "천원", 1e6: "백만원"}[ok[0]]


def opinion_std(t):
    """원문 의견 문구 → 적정·한정·부적정·의견거절. 문구에 없으면 빈칸."""
    t = re.sub(r"\s", "", t or "")
    if not t:
        return ""
    if "거절" in t:
        return "의견거절"
    if "부적정" in t:
        return "부적정"
    if "한정" in t:
        return "한정"
    # '공정'(옛 양식의 적정), '…공정하게 표시하고 있습니다'(적정의견 문구)도 적정으로 본다
    if "적정" in t or "공정하게표시" in t or re.fullmatch(r"(?:-?(?:연결|별도):)?공정(?:-?(?:연결|별도):공정)*", t):
        return "적정"
    return ""


def short(s, n=400):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return "" if s in ("-", "해당사항 없음", "해당사항없음", "없음", "N/A") else s[:n]


def build(js):
    reports = {(j[0], j[1]): j[4] for j in js}
    out, log = [], collections.Counter()
    unit_fail = []
    for (cc, y), rep in sorted(reports.items(), key=lambda x: (x[0][1], x[0][0])):
        op, sv = load(cc, y, APIS[0]), load(cc, y, APIS[1])
        if op is None or sv is None:
            log["미수집"] += 1
            continue
        h1 = y == H1_YEAR
        o_rows, o_how = period_rows(op, y)
        s_rows, s_how = period_rows(sv, y)
        src_note = ""
        if not o_rows and not s_rows and not h1:
            # 그해 보고서 표가 비어 있으면 다음 해·다다음 해 보고서의 전기·전전기 행(같은 감사인이 적은 같은 해 자료)을 쓴다
            for back in (1, 2):
                nxt = (load(cc, y + back, APIS[0]) or []) if y + back <= max(FY_YEARS) else []
                o_rows, o_how = period_rows(nxt, y, back)
                nsv = (load(cc, y + back, APIS[1]) or []) if y + back <= max(FY_YEARS) else []
                s_rows, s_how = period_rows(nsv, y, back)
                if o_rows or s_rows:
                    src_note = f"{y + back}년 보고서의 {['', '전기', '전전기'][back]} 행"
                    o_how = s_how = f"{src_note}"
                    break
        log[f"감사의견 당기 판정: {o_how or '없음'}"] += 1
        log[f"감사용역 당기 판정: {s_how or '없음'}"] += 1
        names = [clean_name(r["adtor"]) for r in o_rows] or [clean_name(r["adtor"]) for r in s_rows]
        names = list(dict.fromkeys(n for n in names if n))
        if not names:
            log["감사인 없음"] += 1
            continue
        if len(names) > 1:
            log["감사인 둘 이상(공동·교체)"] += 1
        op_full = "" if h1 else " / ".join(dict.fromkeys(short(r.get("adt_opinion"), 600) for r in o_rows if short(r.get("adt_opinion"), 600)))
        opinion = op_full[:80]
        fee = hrs = hrs_c = None
        unit = ""
        if s_rows:
            # 감사용역 표는 감사·검토 등 용역 줄이 여럿일 수 있다. 같은 당기 줄의 보수·시간을 더한다.
            # 2019년 이전 양식은 '보수'(mendng)·'총소요시간'(tot_reqre_time)만 있다. '1,170(450)'은 앞 숫자(총액)를 쓴다.
            g = lambda r, k: num(str(r.get(k) or "").replace("\u3000", " ").strip())
            f = sum(g(r, "real_exc_dtls_mendng") or g(r, "adt_cntrct_dtls_mendng") or g(r, "mendng") or 0 for r in s_rows)
            hrs = sum(g(r, "real_exc_dtls_time") or g(r, "tot_reqre_time") or 0 for r in s_rows) or None
            hrs_c = sum(g(r, "adt_cntrct_dtls_time") or 0 for r in s_rows) or None
            # 실제 시간이 계약 시간과 100배 넘게 다르면 천 단위 구분점을 소수점으로 쓴 경우('9.427')라 계약 시간을 쓴다
            if hrs and hrs_c and not (0.01 <= hrs / hrs_c <= 100):
                hrs = hrs_c
            # 감사시간 100~50만 시간 밖이면 칸이 섞인 값(교보증권 2019 '3507901,884')이라 시간과 보수를 비운다
            if hrs and not 100 <= hrs <= 5e5:
                hrs = None
            if hrs_c and not 100 <= hrs_c <= 5e5:
                hrs_c = None
            if not h1:
                fee, unit = fee_mn(f, hrs or hrs_c)
                if fee is None and f:
                    unit_fail.append((rep["company"], y, f, hrs or hrs_c, unit))
                log[f"감사보수 단위: {unit}"] += 1
        out.append({
            "group": rep["group"], "company": rep["company"], "corp_code": cc, "year": y, "period": "H1" if h1 else "FY",
            "auditor": " / ".join(names), "auditor_std": " / ".join(dict.fromkeys(std_name(n) for n in names)),
            "big4": "Y" if all(std_name(n) in dict(BIG4) for n in names) else "",
            "opinion": opinion, "opinion_std": "" if h1 else opinion_std(op_full),
            "emphasis": "" if h1 else short(" / ".join(dict.fromkeys(short(r.get("emphs_matter")) for r in o_rows))),
            "special": "" if h1 else short(" / ".join(dict.fromkeys(short(r.get("adt_reprt_spcmnt_matter")) for r in o_rows))),
            "kam": "" if h1 else short(" / ".join(dict.fromkeys(short(r.get("core_adt_matter"), 600) for r in o_rows)), 600),
            "fee_mn": "" if fee is None else fee, "fee_unit": unit if fee is not None else "",
            "hours": "" if h1 or not hrs else int(hrs), "hours_contract": "" if h1 or not hrs_c else int(hrs_c),
            "rcept_no": rep["rcept_no"], "src_note": src_note,
            "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rep['rcept_no']}",
        })
    return out, log, unit_fail


def report(rows, log, unit_fail):
    by = {(r["corp_code"], r["year"]): r for r in rows}
    changes = []
    for r in rows:
        p = by.get((r["corp_code"], r["year"] - 1))
        if p and p["auditor_std"] != r["auditor_std"]:
            changes.append((r["year"], r["company"], p["auditor"], r["auditor"]))
    bad = [r for r in rows if r["period"] == "FY" and r["opinion_std"] != "적정"]
    L = ["# 회계감사인 수집 점검표", "", f"회사-연도 {len(rows)}건 (11_audit.py가 만든다)", "", "## 처리 건수", "", "| 항목 | 건수 |", "|---|---:|"]
    L += [f"| {k} | {v} |" for k, v in sorted(log.items())]
    L += ["", "## 연도별 4대 회계법인 비중 (사업보고서)", "", "| 연도 | 회사 | 4대 | 비중 |", "|---|---:|---:|---:|"]
    for y in FY_YEARS:
        ys = [r for r in rows if r["year"] == y]
        if ys:
            b = sum(r["big4"] == "Y" for r in ys)
            L.append(f"| {y} | {len(ys)} | {b} | {b / len(ys):.0%} |")
    L += ["", f"## 감사인 교체 {len(changes)}건 (전년과 묶음 이름이 다른 경우)", "", "| 연도 | 회사 | 전년 | 올해 |", "|---|---|---|---|"]
    L += [f"| {y} | {c} | {a} | {b} |" for y, c, a, b in sorted(changes)]
    # 교차 점검: 다음 해 보고서의 전기 행에 적힌 감사인과 같은지
    xs, ok = [], 0
    for r in rows:
        y = r["year"]
        if r["period"] != "FY" or y >= max(FY_YEARS) or r["src_note"]:
            continue
        pr, _ = period_rows(load(r["corp_code"], y + 1, APIS[0]) or [], y, 1)
        if not pr:
            continue
        s2 = " / ".join(dict.fromkeys(std_name(clean_name(x["adtor"])) for x in pr))
        if s2 == r["auditor_std"]:
            ok += 1
        else:
            xs.append((r["company"], y, r["auditor_std"], s2))
    L += ["", f"## 교차 점검: 다음 해 보고서 전기 행과 일치 {ok}건, 불일치 {len(xs)}건", "",
          "불일치는 두 해 공시가 서로 다르게 적은 경우다. 사이트는 그해 보고서(당기 행)를 따른다.", "",
          "| 회사 | 연도 | 그해 보고서 | 다음 해 보고서 |", "|---|---:|---|---|"]
    L += [f"| {c} | {y} | {a_} | {b_} |" for c, y, a_, b_ in xs]
    L += ["", f"## 적정이 아닌 감사의견 {len(bad)}건", "", "| 연도 | 회사 | 감사인 | 의견 |", "|---|---|---|---|"]
    L += [f"| {r['year']} | {r['company']} | {r['auditor']} | {r['opinion_std'] or '판정 못함'}: {r['opinion'][:60] or '(빈칸)'} |" for r in bad]
    L += ["", f"## 단위를 판정하지 못해 비운 감사보수 {len(unit_fail)}건", "", "| 회사 | 연도 | 공시 숫자 | 실제 시간 | 사유 |", "|---|---:|---:|---:|---|"]
    L += [f"| {c} | {y} | {f:,.0f} | {h or ''} | {u} |" for c, y, f, h, u in unit_fail]
    (BUILT / "audit_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    a = ap.parse_args()
    js = jobs()
    if a.fetch:
        fetch_all(js)
    rows, log, unit_fail = build(js)
    with open(BUILT / "audit.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    report(rows, log, unit_fail)
    print(f"audit.csv {len(rows)}행, 점검표 data/built/audit_report.md")
    for k, v in sorted(log.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
