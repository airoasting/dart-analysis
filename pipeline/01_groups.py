"""공정위 지정 자료로 그룹 순위와 그룹별 상장 계열사 목록을 만든다.

사용법:
    python3 -I pipeline/01_groups.py --top 50 --fill-unlisted

입력 (공정위 2026-04-30 보도자료 별첨, data/source/ftc_2026/):
    designation_2026.xlsx   (별첨2) 공시대상기업집단 지정 현황: 순위, 집단명, 동일인, 계열회사 수, 공정자산
    affiliates_2026.xlsx    (별첨5) 계열회사 현황: 집단별 소속회사명, 법인등록번호

매칭 방법:
    공정위 자료에는 상장 여부가 없고 회사명이 한글 표기(예: 에스케이하이닉스(주))라 DART 이름과 다르다.
    그래서 DART 상장사(종목코드가 있는 회사) 전체의 법인등록번호를 company.json API로 받아(캐시)
    공정위 법인등록번호와 맞춘다.

출력:
    data/groups.csv          102개 집단 전체 (rank, group, owner, n_affiliates, assets_bn_krw, top)
    data/companies.csv       상위 N개 집단의 상장 계열사 (02_fetch.py 입력 형식 + 부가 열)
    data/raw/company/<corp_code>.json   회사 정보 캐시
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "source" / "ftc_2026"
RAW = ROOT / "data" / "raw"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
MARKET = {"Y": "유가증권", "K": "코스닥", "N": "코넥스", "E": "기타"}


def read_xlsx(path):
    """표준 라이브러리만으로 첫 시트를 [{열문자: 값}] 목록으로 읽는다."""
    z = zipfile.ZipFile(path)
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{NS}si"):
            ss.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
    sheet = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]
    rows = []
    for row in ET.fromstring(z.read(sheet)).iter(f"{NS}row"):
        vals = {}
        for c in row.iter(f"{NS}c"):
            col = re.match(r"[A-Z]+", c.get("r")).group(0)
            v, t = c.find(f"{NS}v"), c.get("t")
            if t == "inlineStr":
                vals[col] = "".join(x.text or "" for x in c.iter(f"{NS}t"))
            elif v is None:
                vals[col] = ""
            else:
                vals[col] = ss[int(v.text)] if t == "s" else v.text
        rows.append(vals)
    return rows


def groups():
    out = []
    for r in read_xlsx(SRC / "designation_2026.xlsx"):
        if re.fullmatch(r"\d+", (r.get("B") or "").strip()) and r.get("E"):
            out.append({"rank": int(r["B"]), "group": r["E"].strip(), "owner": r.get("F", "").strip(),
                        "n_affiliates": r.get("G", ""), "assets_bn_krw": round(float(r.get("I") or 0), 1)})
    return sorted(out, key=lambda g: g["rank"])


def affiliates():
    out = []
    for r in read_xlsx(SRC / "affiliates_2026.xlsx"):
        if not re.fullmatch(r"\d+", (r.get("B") or "").strip()):
            continue
        name = (r.get("F") or r.get("H") or "").strip()
        jno = re.sub(r"\D", "", r.get("I") or "")
        if name and jno:
            out.append({"rank": int(r["B"]), "group": r["C"].strip(), "ftc_name": name, "jurir_no": jno,
                        "financial": "Y" if r.get("H") and not r.get("F") else ""})
    return out


def company_info(corp_code, key):
    path = RAW / "company" / f"{corp_code}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    url = "https://opendart.fss.or.kr/api/company.json?" + urllib.parse.urlencode(
        {"crtfc_key": key, "corp_code": corp_code})
    data = {"status": "err"}
    for attempt in range(5):  # 서버가 연결을 끊는 경우가 있어 간격을 늘려 가며 재시도
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read())
            break
        except Exception as e:
            print(f"  재시도 {corp_code} ({attempt + 1}/5): {e}")
            time.sleep(5 * 2 ** attempt)
    else:
        print(f"  [실패] {corp_code}: 건너뜀 (다시 돌리면 이어서 받는다)")
    time.sleep(0.8)  # 2026-10-31 초당 수 건 속도에서 OpenDART가 IP를 일시 차단했다. 천천히 받는다.
    if data.get("status") == "000":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    elif data.get("status") == "020":  # 요청 한도 초과
        sys.exit("OpenDART 요청 한도 초과. 내일 다시 돌리면 캐시된 곳부터 이어서 받는다.")
    return data


def norm_name(n, translit=False):
    n = re.sub(r"\(주\)|주식회사|\(유\)|㈜|\s", "", n).upper()
    if translit:  # 공정위는 영문 약칭을 한글로 적는다 (에스케이하이닉스 → SK하이닉스)
        for k, v in [("에스케이", "SK"), ("엘지", "LG"), ("지에스", "GS"), ("씨제이", "CJ"), ("케이티앤지", "KT&G"),
                     ("케이티", "KT"), ("에이치디씨", "HDC"), ("에이치디", "HD"), ("에이치엘", "HL"), ("에이치엠엠", "HMM"),
                     ("디엘", "DL"), ("엘에스", "LS"), ("케이씨씨", "KCC"), ("디비", "DB"), ("엘엑스", "LX"), ("오씨아이", "OCI")]:
            n = n.replace(k, v)
    return n


def jurir_map(key, priority_names=(), offline=False):
    """DART에서 종목코드가 있는 모든 회사의 법인등록번호 → 회사 정보.
    이름이 공정위 계열사명과 같은 회사를 먼저 조회한다. offline이면 캐시만 쓴다."""
    tree = ET.parse(RAW / "corpcode" / "CORPCODE.xml")
    listed = [(el.findtext("corp_code").strip(), el.findtext("corp_name").strip())
              for el in tree.getroot().iter("list") if (el.findtext("stock_code") or "").strip()]
    pri = set(priority_names)
    listed.sort(key=lambda x: norm_name(x[1]) not in pri)
    out = {}
    for i, (cc, _) in enumerate(listed, 1):
        if offline and not (RAW / "company" / f"{cc}.json").exists():
            continue
        d = company_info(cc, key)
        if i % 250 == 0:
            print(f"  회사 정보 {i}/{len(listed)}")
        jno = re.sub(r"\D", "", d.get("jurir_no", "") or "")
        if d.get("status") == "000" and jno and d.get("corp_cls") in ("Y", "K", "N"):
            out[jno] = d
    return out


def unlisted_filers(aff_list, key, since="20250101"):
    """상장사가 없는 그룹: 비상장이지만 사업보고서를 내는 소속회사(주주 500명 이상, 공모 회사채 발행 등)를 찾는다.
    이름으로 DART 고유번호 후보를 찾고, 법인등록번호로 확인한 뒤, 최근 사업보고서 제출 이력이 있는 회사만 고른다."""
    allc = [(el.findtext("corp_code").strip(), el.findtext("corp_name").strip())
            for el in ET.parse(RAW / "corpcode" / "CORPCODE.xml").getroot().iter("list")]
    idx = {}
    for cc, nm in allc:
        idx.setdefault(norm_name(nm), []).append(cc)
    out = []
    for x in aff_list:
        for cc in idx.get(norm_name(x["ftc_name"]), []):
            d = company_info(cc, key)
            if re.sub(r"\D", "", d.get("jurir_no", "") or "") != x["jurir_no"]:
                continue
            url = "https://opendart.fss.or.kr/api/list.json?" + urllib.parse.urlencode(
                {"crtfc_key": key, "corp_code": cc, "bgn_de": since, "pblntf_detail_ty": "A001", "page_count": 10})
            with urllib.request.urlopen(url, timeout=30) as resp:
                lst = json.loads(resp.read())
            time.sleep(0.8)
            if lst.get("status") == "000" and any("사업보고서" in r["report_nm"] for r in lst.get("list", [])):
                out.append((x, d))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--offline", action="store_true", help="API를 부르지 않고 캐시된 회사 정보만으로 매칭")
    ap.add_argument("--fill-unlisted", action="store_true",
                    help="상장사가 없는 그룹은 사업보고서를 내는 비상장 소속회사로 채운다 (50대 그룹을 모두 담기 위해)")
    a = ap.parse_args()
    key = os.environ.get("DART_API_KEY") or sys.exit("DART_API_KEY 환경변수가 없다.")

    gs = groups()
    with open(ROOT / "data" / "groups.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["rank", "group", "owner", "n_affiliates", "assets_bn_krw", "top"])
        w.writeheader()
        for g in gs:
            w.writerow({**g, "top": "Y" if g["rank"] <= a.top else ""})

    aff = affiliates()
    names = {norm_name(x["ftc_name"], t) for x in aff if x["rank"] <= a.top for t in (False, True)}
    jm = jurir_map(key, names, a.offline)
    rows, per_group, excluded = [], {}, []
    for x in aff:
        if x["rank"] > a.top:
            continue
        d = jm.get(x["jurir_no"])
        per_group.setdefault((x["rank"], x["group"]), [0, 0])[0] += 1
        if not d:
            continue
        name = d["stock_name"] or d["corp_name"]
        if "리츠" in name or "코크렙" in name:  # 위탁관리 리츠: 임원이 법인(자산관리회사)이라 사람 임원 명단이 없다
            excluded.append(f"{x['group']} {name}")
            continue
        per_group[(x["rank"], x["group"])][1] += 1
        rows.append({"group": x["group"], "company": d["stock_name"] or d["corp_name"], "stock_code": d["stock_code"],
                     "rank": x["rank"], "corp_code": d["corp_code"], "market": MARKET.get(d["corp_cls"], ""),
                     "ftc_name": x["ftc_name"], "jurir_no": x["jurir_no"], "financial": x["financial"]})
    if a.fill_unlisted:
        empty = {(rk, g) for (rk, g), (n, k) in per_group.items() if k == 0}
        cand = [x for x in aff if (x["rank"], x["group"]) in empty]
        for x, d in unlisted_filers(cand, key):
            per_group[(x["rank"], x["group"])][1] += 1
            rows.append({"group": x["group"], "company": d["stock_name"] or d["corp_name"], "stock_code": "",
                         "rank": x["rank"], "corp_code": d["corp_code"], "market": "비상장(사업보고서 제출)",
                         "ftc_name": x["ftc_name"], "jurir_no": x["jurir_no"], "financial": x["financial"]})
            print(f"  비상장 사업보고서 제출사 추가: {x['group']} {d['corp_name']}")
    rows.sort(key=lambda r: (r["rank"], r["company"]))
    with open(ROOT / "data" / "companies.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"그룹 {len(gs)}개 → data/groups.csv")
    print(f"상위 {a.top}개 그룹 상장 계열사 {len(rows)}개 → data/companies.csv")
    print(f"제외(리츠) {len(excluded)}개: {', '.join(excluded)}")
    for (rk, g), (n, k) in sorted(per_group.items()):
        print(f"  {rk:>3} {g}: 소속 {n}, 상장 {k}")


if __name__ == "__main__":
    main()
