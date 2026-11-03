"""회사별 자산총액을 받는다 (이사회 법정 요건 대상 판정용).

사용법:
    python3 -I pipeline/09_assets.py --year 2025

사외이사 3명 이상·이사 총수의 과반(상법 제542조의8 제1항 단서)과 이사회 전원을 특정 성으로 구성하지 않을 의무
(자본시장법 제165조의20)는 '최근 사업연도 말 자산총액 2조 원 이상' 상장회사에 적용된다.
OpenDART 단일회사 주요계정(fnlttSinglAcnt) 2025 사업보고서에서 재무상태표 '자산총계'를 별도(OFS) 기준으로 우선, 없으면 연결(CFS)로 읽는다.
금융회사는 금융회사지배구조법이 따로 적용되므로 화면에서 별도로 다룬다.

출력: data/built/assets_<year>.csv  (corp_code, company, assets_krw, basis)
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "built"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", default="2025")
    a = ap.parse_args()
    key = os.environ.get("DART_API_KEY") or sys.exit("DART_API_KEY 환경변수가 없다.")
    cache = ROOT / "data" / "raw" / "fin"
    cache.mkdir(parents=True, exist_ok=True)
    rows = []
    for c in csv.DictReader(open(ROOT / "data" / "companies.csv", encoding="utf-8")):
        path = cache / f"{c['corp_code']}_{a.year}.json"
        if not path.exists():
            url = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json?" + urllib.parse.urlencode(
                {"crtfc_key": key, "corp_code": c["corp_code"], "bsns_year": a.year, "reprt_code": "11011"})
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(url, timeout=30) as r:
                        path.write_bytes(r.read())
                    break
                except Exception as e:
                    time.sleep(5 * (attempt + 1))
            time.sleep(0.6)
        d = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        val, basis = None, ""
        for fs in ("OFS", "CFS"):
            for it in d.get("list", []):
                if it.get("fs_div") == fs and it.get("account_nm", "").replace(" ", "") == "자산총계" and it.get("sj_div") == "BS":
                    try:
                        val, basis = int(it["thstrm_amount"].replace(",", "")), fs
                    except (ValueError, KeyError):
                        pass
                    break
            if val:
                break
        rows.append({"corp_code": c["corp_code"], "company": c["company"], "assets_krw": val or "", "basis": basis})
    with open(OUT / f"assets_{a.year}.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["corp_code", "company", "assets_krw", "basis"])
        w.writeheader()
        w.writerows(rows)
    print(f"{sum(1 for r in rows if r['assets_krw'])}/{len(rows)}곳 자산총액 → data/built/assets_{a.year}.csv")


if __name__ == "__main__":
    main()
