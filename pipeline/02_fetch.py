"""OpenDART에서 사업보고서의 임원 섹션과 등기임원 정형 데이터를 받는다.

사용법:
    python3 -I pipeline/02_fetch.py data/companies.csv --years 2018-2025 --workers 3
    python3 -I pipeline/02_fetch.py data/companies.csv --years 2026 --period H1   # 올해 반기보고서(최신 명단)

입력 CSV 열: group, company, stock_code (corp_code 열이 있으면 그것을 쓴다)
결과:
    data/raw/corpcode/CORPCODE.xml              종목코드 → DART 고유번호 매핑 (한 번만 받음)
    data/raw/sections/<rcept_no>.xml            원문 중 'VIII-1. 임원 및 직원 등의 현황' 섹션만 (utf-8)
    data/raw/docs/<rcept_no>/<rcept_no>.xml     섹션을 못 찾은 보고서만 원문 전체를 남긴다 (점검용)
    data/raw/api/<corp_code>_<year>_exctv.json  등기임원 정형 API (대조용)
    data/raw/reports_<year>.csv                 회사별로 고른 보고서 목록 (이번 실행 회사만 갱신, 나머지는 유지)

원문 전체(보고서당 5~18MB)는 저장하지 않는다. 300개사 × 8개년이면 20GB를 넘기 때문이다.
보고서 선택: 해당 사업연도 사업보고서 중 최근 접수본(정정 포함)부터 시도하고, 원문을 받을 수 없으면 앞 접수본.
인증키는 환경변수 DART_API_KEY에서 읽는다. 이미 받은 것은 다시 받지 않는다.
"""
import argparse
import concurrent.futures as cf
import csv
import io
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
API = "https://opendart.fss.or.kr/api"
MIN_INTERVAL = 0.5  # 모든 스레드를 합친 호출 간격(초). 0.15초 수준에서 IP 일시 차단을 겪었다.
_lock = threading.Lock()
_last = [0.0]
REPORT_FIELDS = ["group", "company", "stock_code", "corp_code", "corp_name", "rcept_no", "report_nm", "rcept_dt", "status"]


def key():
    return os.environ.get("DART_API_KEY") or sys.exit("DART_API_KEY 환경변수가 없다.")


def get(endpoint, **params):
    params["crtfc_key"] = key()
    url = f"{API}/{endpoint}?{urllib.parse.urlencode(params)}"
    for attempt in range(4):
        with _lock:
            wait = _last[0] + MIN_INTERVAL - time.time()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.read()
        except Exception as e:  # 네트워크 오류는 세 번까지 재시도
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))


def decode(data):
    """옛 서식은 utf-8로 선언하고 실제로는 EUC-KR(cp949)인 경우가 있다."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp949", errors="replace")


def cut_section(xml):
    m = re.search(r"<TITLE[^>]*>[^<]*임원\s*및\s*직원\s*(?:등의?|의)?\s*현황", xml)
    if not m:
        return None
    end = xml.find("<TITLE", m.end())
    return xml[m.start(): end if end > 0 else len(xml)]


def corp_codes():
    path = RAW / "corpcode" / "CORPCODE.xml"
    if not path.exists():
        print("고유번호 목록 다운로드")
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(get("corpCode.xml"))) as z:
            path.write_bytes(z.read("CORPCODE.xml"))
    out = {}
    for el in ET.parse(path).getroot().iter("list"):
        sc = (el.findtext("stock_code") or "").strip()
        if sc:
            out[sc] = (el.findtext("corp_code").strip(), el.findtext("corp_name").strip())
    return out


def get_section(rno):
    """임원 섹션을 sections/에 저장한다. 결과: 'ok' | 'no_doc'(API에 원문 없음) | 'no_section'."""
    sec_path = RAW / "sections" / f"{rno}.xml"
    if sec_path.exists():
        return "ok"
    full = RAW / "docs" / rno / f"{rno}.xml"
    if full.exists():  # 파일럿 때 받은 원문 전체가 있으면 재사용
        xml = decode(full.read_bytes())
    else:
        data = get("document.xml", rcept_no=rno)
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                # 본문은 <접수번호>.xml이다. 정정본 중에는 첨부(감사보고서)만 있고 본문이 없는 경우가 있다 → 앞 접수본으로
                names = [n for n in z.namelist() if n == f"{rno}.xml"]
                xml = decode(z.read(names[0]))
        except (zipfile.BadZipFile, IndexError):
            return "no_doc"
    sec = cut_section(xml)
    if sec is None:
        full.parent.mkdir(parents=True, exist_ok=True)
        if not full.exists():
            full.write_text(xml, encoding="utf-8")
        return "no_section"
    sec_path.parent.mkdir(parents=True, exist_ok=True)
    sec_path.write_text(sec, encoding="utf-8")
    return "ok"


# 보고서 종류: FY=사업보고서(12월 말 기준, 다음 해 3월 제출), H1=반기보고서(6월 말 기준, 8월 제출)
PERIODS = {"FY": {"ty": "A001", "mark": "{y}.12", "reprt": "11011", "lag": 1},
           "H1": {"ty": "A002", "mark": "{y}.06", "reprt": "11012", "lag": 0}}
PERIOD = PERIODS["FY"]


def fetch_company(c, years):
    """한 회사의 여러 해 보고서를 받는다. 결과: {year: report row}."""
    lo, hi = min(years), max(years)
    data = json.loads(get("list.json", corp_code=c["corp_code"], bgn_de=f"{lo + PERIOD['lag']}0101",
                          end_de=f"{hi + PERIOD['lag']}1231", pblntf_detail_ty=PERIOD["ty"], page_count=100))
    lst = data.get("list", []) if data.get("status") == "000" else []
    out = {}
    for y in years:
        cands = sorted((x for x in lst if "(" + PERIOD["mark"].format(y=y) + ")" in x["report_nm"]), key=lambda x: x["rcept_no"], reverse=True)
        status, rep = "no_report", None
        for cand in cands:
            status = get_section(cand["rcept_no"])
            if status != "no_doc":
                rep = cand
                break
        if rep is None:
            if cands:
                out[y] = {**c, "rcept_no": "", "report_nm": "", "rcept_dt": "", "status": status}
            continue
        apipath = RAW / "api" / f"{c['corp_code']}_{y}_exctv.json"
        if not apipath.exists():
            apipath.parent.mkdir(parents=True, exist_ok=True)
            apipath.write_bytes(get("exctvSttus.json", corp_code=c["corp_code"], bsns_year=str(y), reprt_code=PERIOD["reprt"]))
        out[y] = {**c, "rcept_no": rep["rcept_no"], "report_nm": rep["report_nm"],
                  "rcept_dt": rep["rcept_dt"], "status": status}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("companies")
    ap.add_argument("--years", default="2025", help="예: 2025 또는 2018-2025")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--period", default="FY", choices=list(PERIODS), help="FY=사업보고서, H1=반기보고서(그해 최신 명단)")
    a = ap.parse_args()
    lo, hi = (map(int, a.years.split("-")) if "-" in a.years else (int(a.years), int(a.years)))
    years = list(range(lo, hi + 1))
    global PERIOD
    PERIOD = PERIODS[a.period]

    codes = corp_codes()
    comps = []
    for r in csv.DictReader(open(a.companies, encoding="utf-8")):
        sc = r["stock_code"].zfill(6)
        cc = r.get("corp_code") or codes.get(sc, ("", ""))[0]
        if not cc:
            print(f"[건너뜀] {r['company']} {sc}: 고유번호 없음")
            continue
        comps.append({"group": r["group"], "company": r["company"], "stock_code": sc,
                      "corp_code": cc, "corp_name": codes.get(sc, ("", r["company"]))[1]})

    results = {y: [] for y in years}
    done = 0
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(fetch_company, c, years): c for c in comps}
        for fut in cf.as_completed(futs):
            c = futs[fut]
            done += 1
            try:
                for y, row in fut.result().items():
                    results[y].append(row)
            except Exception as e:
                print(f"[오류] {c['company']}: {e}")
            if done % 20 == 0 or done == len(comps):
                print(f"  {done}/{len(comps)}개 회사 완료", flush=True)

    mine = {c["corp_code"] for c in comps}
    for y in years:
        path = RAW / f"reports_{y}.csv"
        keep = []
        if path.exists():  # 이번에 다루지 않은 회사의 기존 행은 유지
            keep = [r for r in csv.DictReader(open(path, encoding="utf-8")) if r["corp_code"] not in mine]
        rows = keep + results[y]
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=REPORT_FIELDS, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "ok" if k == "status" else "") for k in REPORT_FIELDS})
        st = {}
        for r in results[y]:
            st[r["status"]] = st.get(r["status"], 0) + 1
        print(f"{y}: {st}")


if __name__ == "__main__":
    main()
