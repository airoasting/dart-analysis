"""회사별 직원 수·직원 급여, 등기임원 보수, 개인별 보수(5억 원 이상)를 연도별로 받아 정리한다.

사용법:
    python3 -I pipeline/10_pay_staff.py --fetch      # OpenDART에서 받기(이미 받은 것은 건너뜀) + 정리
    python3 -I pipeline/10_pay_staff.py              # 받아 둔 원문으로 정리만

받는 API (OpenDART 정기보고서 주요정보, 회사-연도마다 1건씩)
    empSttus               직원 현황. 2018~2025 사업보고서(12월 말) + 2026 반기보고서(6월 말)
    hmvAuditAllSttus       이사·감사 전체의 보수 지급금액(인원, 총액, 회사가 계산한 1인 평균). 사업보고서만
    hmvAuditIndvdlBySttus  이사·감사 개인별 보수(5억 원 이상). 사업보고서만
    indvdlByPay            개인별 보수 5억 원 이상 상위 5명(미등기 포함). 사업보고서만
    반기보고서는 보수 API에 자료가 없거나 반년치라 보수는 연간(2018~2025)만 쓴다.

대상 회사-연도는 data/raw/reports_<연도>.csv에 보고서가 있는 행만 (02_fetch.py 결과).
원문 사본: data/raw/paystaff/<corp_code>_<연도>_<API>.json
결과 (data/built/)
    staff.csv        회사-연도 직원 수(정규·기간제·남·여), 평균 근속, 1인 평균 급여, 급여 총액
    board_pay.csv    회사-연도 등기임원(이사·감사) 인원, 보수 총액, 1인 평균
    indiv_pay.csv    개인별 보수 5억 원 이상(두 API를 이름으로 합침) + 같은 회사·연도 명단의 person_id
    pay_staff_report.md  수집·정리 점검표
금액 단위: 원 → 백만 원. 직원 급여는 연간 자료만 쓴다(반기는 반년치라 비교하지 않는다).
"""
import argparse
import collections
import csv
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import concurrent.futures as cf
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
CACHE = RAW / "paystaff"
BUILT = ROOT / "data" / "built"
API = "https://opendart.fss.or.kr/api"
MIN_INTERVAL = 0.5
FY_YEARS = list(range(2018, 2026))
H1_YEAR = 2026
PAY_APIS = ["hmvAuditAllSttus", "hmvAuditIndvdlBySttus", "indvdlByPay"]
_lock = threading.Lock()
_last = [0.0]


def get(endpoint, **params):
    params["crtfc_key"] = os.environ.get("DART_API_KEY") or sys.exit("DART_API_KEY 환경변수가 없다.")
    url = f"{API}/{endpoint}.json?{urllib.parse.urlencode(params)}"
    for attempt in range(4):
        with _lock:
            wait = _last[0] + MIN_INTERVAL - time.time()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except Exception:
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))


def jobs():
    """(corp_code, year, api, reprt_code, report row) 목록."""
    out = []
    for y in FY_YEARS + [H1_YEAR]:
        path = RAW / f"reports_{y}.csv"
        if not path.exists():
            continue
        for r in csv.DictReader(open(path, encoding="utf-8")):
            if not r["rcept_no"]:
                continue
            if y == H1_YEAR:
                out.append((r["corp_code"], y, "empSttus", "11012", r))
            else:
                for api in ["empSttus"] + PAY_APIS:
                    out.append((r["corp_code"], y, api, "11011", r))
    return out


def cache_path(cc, y, api):
    return CACHE / f"{cc}_{y}_{api}.json"


def fetch_all(js):
    CACHE.mkdir(parents=True, exist_ok=True)
    todo = [j for j in js if not cache_path(j[0], j[1], j[2]).exists()]
    print(f"받을 것 {len(todo)}건 / 전체 {len(js)}건 (예상 {len(todo) * MIN_INTERVAL / 60:.0f}분)", flush=True)

    def one(j):
        cc, y, api, rc, _ = j
        data = get(api, corp_code=cc, bsns_year=str(y), reprt_code=rc)
        st = json.loads(data).get("status")
        if st in ("020", "800", "901"):  # 사용 한도 초과·점검·키 오류: 저장하지 않고 멈춘다
            raise SystemExit(f"OpenDART 응답 {st}: {json.loads(data).get('message')}")
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


def num(s):
    """'1,234' '-' '(12)' '12.3' → float | None."""
    if s is None:
        return None
    s = str(s).strip().replace(",", "").replace(" ", "")
    if s in ("", "-", "−"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    m = re.match(r"^-?\d+(\.\d+)?", s)
    if not m:
        return None
    v = float(m.group(0))
    return -v if neg else v


TOTAL_RE = re.compile(r"합\s*계|총\s*계|^\s*계\s*$|^\s*전\s*체\s*$")   # '성별합계', '합 계', '총계' 등


def staff_row(rows):
    """직원 현황 행(사업부문 × 성별)을 회사 합계 한 줄로 줄인다.

    회사가 '합계'·'계' 행을 따로 두면 그 행만 쓰고(중복 합산 방지), 없으면 모든 행을 더한다.
    평균 근속·1인 평균 급여는 인원 가중 평균. 급여 총액이 있으면 총액 ÷ 인원을 우선한다.
    """
    if not rows:
        return None
    tot = [r for r in rows if TOTAL_RE.search(r.get("fo_bbm") or "") or TOTAL_RE.search(r.get("sexdstn") or "")]
    use = tot if tot else rows
    # 성별이 '합계'인 행이 있으면 그것만 (남·여 행과 겹침)
    sx_tot = [r for r in use if TOTAL_RE.search(r.get("sexdstn") or "")]
    if sx_tot:
        use = sx_tot
    emp = reg = con = male = female = 0.0
    tenure_w = sal_w = sal_n = ten_n = 0.0
    total_sal = 0.0
    has_total_sal = False
    for r in use:
        n = num(r.get("sm"))
        if n is None:
            n = (num(r.get("rgllbr_co")) or 0) + (num(r.get("cnttk_co")) or 0)
        emp += n
        reg += num(r.get("rgllbr_co")) or 0
        con += num(r.get("cnttk_co")) or 0
        sx = (r.get("sexdstn") or "").strip()
        if sx.startswith("남"):
            male += n
        elif sx.startswith("여"):
            female += n
        t = num(r.get("avrg_cnwk_sdytrn"))
        if t is not None and n:
            tenure_w += t * n
            ten_n += n
        ts = num(r.get("fyer_salary_totamt"))
        if ts:
            total_sal += ts
            has_total_sal = True
        a = num(r.get("jan_salary_am"))
        if a is not None and n:
            sal_w += a * n
            sal_n += n
    # 성별이 '합계'로만 나온 경우 남·여 수는 원문 남·여 행에서 따로 센다
    if sx_tot:
        for r in rows:
            sx = (r.get("sexdstn") or "").strip()
            if r in sx_tot or (tot and r not in tot and not TOTAL_RE.search(r.get("fo_bbm") or "")):
                continue
            n = num(r.get("sm")) or 0
            if sx.startswith("남"):
                male += n
            elif sx.startswith("여"):
                female += n
    if not emp:
        return None
    # 1인 평균 급여: 회사가 적은 '1인 평균 급여액'(기중 평균 인원 기준, 언론이 인용하는 공식 값)을 인원 가중으로 우선 쓴다.
    # 기말 인원으로 나눈 '총액 ÷ 인원'은 기중 입퇴사가 많은 회사(예: CJ CGV)에서 크게 틀리기 때문이다.
    # 다만 1인 평균 칸에 월 급여를 적은 회사(예: 에스엠 2018 522만 원)가 있어, 공시 평균 × 12가 '총액 ÷ 인원'과 비슷하면 '총액 ÷ 인원'을 쓴다.
    # 단위(원·천 원·백만 원)는 값 크기로 맞추고, 연 500만~10억 원 밖이면 다른 쪽 값으로, 둘 다 밖이면 비운다.
    ok = lambda v: v is not None and 5e6 <= v <= 1e9
    stated = sal_w / sal_n if sal_n else None
    sc_stated = sc_total = 1
    if stated:
        sc_stated = unit_scale(stated)
        stated *= sc_stated
    by_total = None
    if has_total_sal and total_sal and emp:
        sc_total = unit_scale(total_sal / emp)
        total_sal *= sc_total
        by_total = total_sal / emp
    monthly = stated and by_total and stated < 2e7 and 0.6 <= stated * 12 / by_total <= 1.6
    avg_sal, scaled = None, False
    if ok(stated) and not monthly:
        avg_sal, scaled = stated, sc_stated != 1
    elif ok(by_total):
        avg_sal, scaled = by_total, sc_total != 1
    # 단위를 값 크기로 바꾼 결과가 직원 150명 넘는 회사에서 1인 3억 원을 넘으면 단위 추정이 틀린 것으로 보고 비운다
    # (예: 폰드그룹 2023 719,000 → 분할 직후 며칠치라 단위를 알 수 없다). 공시 단위 그대로인 값은 높아도 둔다(두나무 2021 3.93억 원).
    if avg_sal and scaled and avg_sal > 3e8 and emp > 150:
        avg_sal = None
    return {"employees": int(round(emp)), "regular": int(round(reg)), "contract": int(round(con)),
            "male": int(round(male)) or "", "female": int(round(female)) or "",
            "avg_tenure": round(tenure_w / ten_n, 1) if ten_n else "",
            "avg_salary_mn": round(avg_sal / 1e6, 1) if avg_sal else "",
            "salary_total_mn": round(total_sal / 1e6) if avg_sal and has_total_sal and total_sal else "",
            "rcept_no": rows[0].get("rcept_no", "")}


def unit_scale(per_person):
    """OpenDART는 공시의 단위 표기대로 원으로 바꿔 준다. 단위 표기를 잘못 적은 공시만 값 크기로 바로잡는다.
    1인당 값이 1천 미만이면 백만 원, 100만 미만이면 천 원을 원으로 적은 것이고, 100억 원 초과면 원을 천 원으로 적은 것이다."""
    if per_person > 1e10:   # 1인 100억 원 초과: 원 단위 값에 '천원' 단위를 붙인 오기(예: 동양건설산업 2019 직원 급여 총액)
        return 1e-3
    return 1e6 if per_person < 1_000 else 1e3 if per_person < 1_000_000 else 1


def indiv_amount(v, api):
    """개인별 보수의 단위 오기를 고치고, 5억 원 미만은 뺀다. 결과: (원 | None, 보정 메모).

    - 1천억 원 초과: 숫자 끝 '000'이 더 붙은 오기(예: HD현대중공업 2022 등기임원 표 1,149,100,000,000) → ÷1,000
    - 10만 미만: 백만 원 단위(예: 애경산업 2019 1,641) → ×1,000,000
    - 상위 5명 표(indvdlByPay, 5억 원 이상만 싣는 표)에서 1천만 미만: 천 원 단위(예: 미래에셋벤처투자 2018 1,218,167) → ×1,000
    - 2018~2020 등기임원 개인별 표는 5억 원 미만 이사도 실었다(사외이사 8천만 원 등). 화면 기준(5억 원 이상)에 맞춰 뺀다.
    """
    if v is None or v <= 0:
        return None, ""
    fix = ""
    if v > 1e11:
        v, fix = v / 1e3, "오기 보정 ÷1,000"
    elif v < 1e5:
        v, fix = v * 1e6, "단위 보정 ×1,000,000"
    elif api == "indvdlByPay" and v < 1e7:
        v, fix = v * 1e3, "단위 보정 ×1,000"
    if v < 5e8:
        return None, ""
    return v, fix


def norm_name(s):
    return re.sub(r"[\s·.()]", "", s or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    a = ap.parse_args()
    js = jobs()
    if a.fetch:
        fetch_all(js)

    # 같은 회사·연도 명단에서 이름 → person_id (동명이인은 비워 둔다)
    pid = collections.defaultdict(list)
    for r in csv.DictReader(open(BUILT / "person_years.csv", encoding="utf-8-sig")):
        pid[(r["corp_code"], int(r["year"]), norm_name(r["name"]))].append(r["person_id"])

    reports = {(j[0], j[1]): j[4] for j in js}
    staff, board, indiv = [], [], []
    miss = collections.Counter()
    for (cc, y), rep in sorted(reports.items(), key=lambda x: (x[0][1], x[0][0])):
        base = {"group": rep["group"], "company": rep["company"], "corp_code": cc, "year": y,
                "period": "H1" if y == H1_YEAR else "FY",
                "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rep['rcept_no']}"}
        emp = load(cc, y, "empSttus")
        if emp is None:
            miss["empSttus 미수집"] += 1
        s = staff_row(emp or [])
        if s:
            if base["period"] == "H1":
                s["avg_salary_mn"] = s["salary_total_mn"] = ""  # 반년치라 연간과 비교하지 않는다
            src = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={s.pop('rcept_no')}" if s.get("rcept_no") else base["source_url"]
            staff.append({**base, **s, "source_url": src})
        elif emp is not None:
            miss["직원 현황 없음"] += 1
        if y == H1_YEAR:
            continue
        al = load(cc, y, "hmvAuditAllSttus")
        if al is None:
            miss["hmvAuditAllSttus 미수집"] += 1
        elif al:
            n = sum(num(r.get("nmpr")) or 0 for r in al)
            t = sum(num(r.get("mendng_totamt")) or 0 for r in al)
            avg = None
            if len(al) == 1:
                avg = num(al[0].get("jan_avrg_mendng_am"))
            if avg is None and n:
                avg = t / n
            if n and t:
                # 예: 남해화학 2019 총액 835,553 '백만원'(유형별 표는 같은 숫자를 '천원'), 동양건설산업 2023 → 원을 천 원으로 적은 오기
                sc = unit_scale(t / n)
                t *= sc
                avg = avg * unit_scale(avg) if avg else None
                if sc != 1:
                    miss[f"등기임원 보수 단위 보정 ×{sc:g}"] += 1
            if n and t:
                board.append({**base, "reg_count": int(n), "reg_total_mn": round(t / 1e6),
                              "reg_avg_mn": round(avg / 1e6) if avg else ""})
        people = {}
        for api, kind in (("hmvAuditIndvdlBySttus", "등기"), ("indvdlByPay", "상위5")):
            for r in load(cc, y, api) or []:
                amt, fix = indiv_amount(num(r.get("mendng_totamt")), api)
                nm = (r.get("nm") or "").strip()
                if not nm or not amt or TOTAL_RE.search(nm) or nm in ("-", "계"):   # '합계' 행(예: 삼성카드 2018)은 사람이 아니다
                    continue
                if fix:
                    miss[f"개인별 보수 {fix}"] += 1
                k = norm_name(nm)
                cur = people.get(k)
                if cur is None:
                    people[k] = {**base, "name": nm, "title": re.sub(r"\s+", " ", r.get("ofcps") or "").strip(),
                                 "pay_mn": round(amt / 1e6), "basis": kind, "note": fix}
                else:
                    v = round(amt / 1e6)
                    if fix and fix not in cur["note"]:
                        cur["note"] = "; ".join(x for x in (cur["note"], fix) if x)
                    if v != cur["pay_mn"]:
                        # 두 표의 금액이 다르면(예: 현대차 2020 정몽구 등기 표 732, 상위 5명 표 55,015백만 원) 큰 값을 쓰고 작은 값을 적어 둔다
                        lo, hi = sorted((v, cur["pay_mn"]))
                        lo_kind = kind if v == lo else cur["basis"]
                        cur["note"] = "; ".join(x for x in (cur["note"], f"{lo_kind} 표는 {lo:,}백만 원") if x)
                        cur["pay_mn"] = hi
                    if kind not in cur["basis"]:
                        cur["basis"] += "+" + kind
        for k, p in people.items():
            # 연중 퇴임자는 그해 말 명단에 없고 전년 말 명단에 있다 → 같은 회사 전년, 다음 해 순으로 찾는다
            ids = pid.get((cc, y, k)) or pid.get((cc, y - 1, k)) or pid.get((cc, y + 1, k)) or []
            p["person_id"] = ids[0] if len(set(ids)) == 1 else ""
            indiv.append(p)

    def write(name, rows, fields):
        with open(BUILT / name, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    key = ["group", "company", "corp_code", "year", "period"]
    write("staff.csv", staff, key + ["employees", "regular", "contract", "male", "female", "avg_tenure",
                                     "avg_salary_mn", "salary_total_mn", "source_url"])
    write("board_pay.csv", board, key + ["reg_count", "reg_total_mn", "reg_avg_mn", "source_url"])
    write("indiv_pay.csv", indiv, key + ["name", "title", "pay_mn", "basis", "note", "person_id", "source_url"])

    by_y = lambda rows: collections.Counter(r["year"] for r in rows)
    sy, by, iy = by_y(staff), by_y(board), by_y(indiv)
    iy_m = collections.Counter(r["year"] for r in indiv if r["person_id"])
    rep_y = collections.Counter(y for (_, y) in reports)
    md = ["# 직원·보수 수집 점검\n",
          "| 연도 | 보고서 | 직원 현황 | 등기임원 보수 | 개인별 보수(5억+) | 그중 명단 연결 |", "|---|---|---|---|---|---|"]
    for y in sorted(rep_y):
        md.append(f"| {y}{' 반기' if y == H1_YEAR else ''} | {rep_y[y]} | {sy[y]} | {by[y] or '-'} | {iy[y] or '-'} | {iy_m[y] or '-'} |")
    md.append("\n누락·보정: " + (", ".join(f"{k} {v}" for k, v in miss.items()) or "없음"))
    no_sal = sum(1 for r in staff if r["period"] == "FY" and not r["avg_salary_mn"])
    md.append(f"\n연간 직원 현황 중 1인 평균 급여를 못 읽은 회사-연도: {no_sal}")
    (BUILT / "pay_staff_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
