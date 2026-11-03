"""사업보고서 원문에서 임원 명단(등기 + 미등기)을 뽑아 정규화한다.

사용법:
    python3 -I pipeline/03_parse.py --year 2025

입력:  data/raw/reports_<year>.csv, data/raw/docs/<rcept_no>/<rcept_no>.xml, data/raw/api/*_exctv.json
출력:
    data/built/executives_<year>.csv      한 행 = 한 회사의 임원 한 명
    data/built/parse_report_<year>.md     회사별 표 선택 근거, 행 수, 필드 채움률, API 대조
    data/built/unreg_pay_<year>.csv       회사별 미등기임원 보수 현황(인원, 총액, 1인 평균, 백만원)과 명단 인원

표를 고르는 규칙 (회사마다 표 모양이 달라 머리글 이름으로 칸을 찾는다):
  1. 'VIII-1. 임원 및 직원 등의 현황' 섹션 안의 표만 본다.
  2. 머리글에 성명(이름), 출생, 직위가 모두 있어야 임원 명단 표로 본다.
  3. 다음 표는 뺀다: 첫 칸이 '구분'인 표(변동 내역), 사외이사 후보 표,
     칸 이름이 퇴임·변동일·사유로 시작하는 표, 바로 앞 문장이 '기준일 이후 변동·신규·퇴임'을 말하는 표.
  4. 등기 여부 칸이 없으면: 앞 문장에 '미등기'가 있거나, 임기만료일 칸이 없거나 값이 대부분 비었거나,
     등기 표 다음에 나오는 두 번째 명단 표면 미등기, 그 밖은 등기로 본다.
"""
import argparse
import collections
import csv
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
BUILT = ROOT / "data" / "built"

# 정규화한 머리글(공백 제거) → 표준 필드. 앞에서부터 먼저 맞는 것을 쓴다.
HEADER_MAP = [
    ("name", ("성명", "이름")),
    ("sex", ("성별",)),
    ("birth", ("출생년월", "출생연월", "생년월", "출생년도", "생년")),
    ("position", ("직위", "직책")),
    ("registered", ("등기임원여부", "등기여부", "등기임원")),
    ("fulltime", ("상근여부", "상근")),
    ("duty", ("담당업무", "주요담당업무", "담당")),
    ("career", ("주요경력", "경력")),
    ("education", ("학력", "최종학력")),
    ("relation", ("최대주주와의관계", "최대주주와관계")),
    ("tenure", ("재직기간",)),
    ("term_end", ("임기만료일", "임기만료")),
    ("appointed", ("선임일", "최초선임일")),
    ("shares", ("소유주식수_의결권있는주식", "소유주식수", "주식수", "보통주")),
]
EXCLUDE_HEADERS = ("사외이사후보자", "사유", "퇴임", "변동일", "선ㆍ해임예정일", "선·해임예정일")  # 선임일 칸은 명단 표에도 있다(카카오)
DITTO = {"〃", '"', "″", "상동", "“", "”", "同", "-〃-"}
FIELDS = ["group", "company", "corp_code", "rcept_no", "base_date", "table_kind",
          "reg_class", "name", "name_key", "sex", "birth_ym", "age", "position", "position_norm",
          "fulltime", "duty", "career", "education", "relation",
          "tenure_raw", "tenure_start", "tenure_months", "term_end", "source_url"]


def cell_text(raw):
    raw = re.sub(r"<BR\s*/?>", "\n", raw, flags=re.I).replace("&cr;", "\n")  # 옛 서식의 줄바꿈 표기
    raw = re.sub(r"</?P\b[^>]*>", "\n", raw, flags=re.I)  # 칸 안의 문단은 줄로 나눈다 (경력 줄바꿈 보존)
    t = html.unescape(re.sub(r"<[^>]+>", "", raw))
    lines = [re.sub(r"\s+", " ", x).strip() for x in t.split("\n")]
    return "\n".join(x for x in lines if x)


def clean_name(t):
    """이름에 붙은 각주 표시를 뗀다: 고관주(*1), 두성국주1), 김경민(주1), 나원균(**)(****), 김근영*"""
    t = re.sub(r"\(\*+\d*\)|\*+\d*|\(?주\d+\)", "", (t or "").replace("\n", " "))
    return re.sub(r"\s+", " ", t).strip()


def norm_label(t):
    return re.sub(r"\s+", "", t)


def grid(rows_html, tag_re):
    """ROWSPAN/COLSPAN을 펼쳐 2차원 표로 만든다."""
    out, carry = [], {}
    for tr in rows_html:
        row, col = [], 0
        cells = re.findall(rf"<({tag_re})\b([^>]*)>(.*?)</\1>", tr, re.S | re.I)
        it = iter(cells)
        while True:
            while col in carry and carry[col][0] > 0:
                row.append(carry[col][1])
                carry[col][0] -= 1
                col += 1
            c = next(it, None)
            if c is None:
                break
            _, attrs, body = c
            cs = int((re.search(r'COLSPAN="(\d+)"', attrs, re.I) or [0, 1])[1])
            rs = int((re.search(r'ROWSPAN="(\d+)"', attrs, re.I) or [0, 1])[1])
            txt = cell_text(body)
            for k in range(cs):
                row.append(txt)
                if rs > 1:
                    carry[col] = [rs - 1, txt]
                col += 1
        out.append(row)
    return out


def header_labels(table):
    thead = re.search(r"<THEAD>(.*?)</THEAD>", table, re.S | re.I)
    if thead:
        hrows = re.findall(r"<TR\b.*?</TR>", thead.group(1), re.S | re.I)
        body = table[thead.end():]
    else:
        trs = re.findall(r"<TR\b.*?</TR>", table, re.S | re.I)
        hrows = [tr for tr in trs if "<TH" in tr.upper()][:2] or trs[:1]
        body = table
        for h in hrows:
            body = body.replace(h, "", 1)
    g = grid(hrows, "TH|TD|TE|TU")
    width = max((len(r) for r in g), default=0)
    labels = []
    for c in range(width):
        parts = []
        for r in g:
            v = norm_label(r[c]) if c < len(r) else ""
            if v and v not in parts:
                parts.append(v)
        labels.append("_".join(parts))
    return labels, body


def map_columns(labels):
    cols = {}
    for i, lab in enumerate(labels):
        for field, keys in HEADER_MAP:
            if field in cols:
                continue
            if any(lab.startswith(k) for k in keys):
                cols[field] = i
                break
    return cols


def parse_birth(t):
    m = re.search(r"(19\d{2}|20\d{2})\D{0,3}(\d{1,2})?", t or "")
    if not m:
        return None
    y, mo = m.group(1), m.group(2)
    return f"{y}-{int(mo):02d}" if mo and 1 <= int(mo) <= 12 else y


def parse_tenure(t):
    t = re.sub(r"\s+", "", t or "")
    if not t or t in ("-",):
        return None
    y = re.search(r"(\d+)년", t)
    m = re.search(r"(\d+)개월", t)
    if y or m:
        return int(y.group(1) if y else 0) * 12 + int(m.group(1) if m else 0)
    if re.fullmatch(r"\d+", t):  # 단위가 '개월'인 표의 숫자만 있는 칸
        return int(t)
    return None


def parse_date(t):
    """'2023.03.15', '2023-03', '2023년 3월' → YYYY-MM."""
    m = re.search(r"(19\d{2}|20\d{2})\s*[.년\-/]\s*(\d{1,2})", t or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}" if m and 1 <= int(m.group(2)) <= 12 else None


def unreg_pay(sec):
    """'미등기임원 보수 현황' 표에서 인원수, 연간급여 총액, 1인 평균(백만원)을 뽑는다."""
    # 같은 문구가 주석에 먼저 나오는 회사가 있어(GS건설) 제목 후보를 차례로 본다
    for i in re.finditer(r"미등기\s*임원\s*(?:의\s*)?보수\s*현황", sec):
        tail = sec[i.end(): i.end() + 8000]
        unit_m = re.search(r"단위\s*:?\s*(백만\s*원|천\s*원|원)", re.sub(r"<[^>]+>", " ", tail[:3000]))
        scale = {"백만원": 1, "천원": 1e-3, "원": 1e-6}.get((unit_m.group(1).replace(" ", "") if unit_m else "백만원"), 1)
        for t in re.findall(r"<TABLE\b.*?</TABLE>", tail, re.S | re.I)[:3]:
            for row in grid(re.findall(r"<TR\b.*?</TR>", t, re.S | re.I), "TD|TE|TU|TH"):
                if row and re.sub(r"\s+", "", row[0]).startswith("미등기") and len(row) >= 4:
                    nums = [re.sub(r"[^\d.]", "", x) for x in row[1:4]]
                    f = [float(x) if re.fullmatch(r"\d+(\.\d+)?", x) else None for x in nums]
                    return {"pay_count": int(f[0]) if f[0] is not None else None,
                            "pay_total_mn": round(f[1] * scale, 1) if f[1] is not None else None,
                            "pay_avg_mn": round(f[2] * scale, 1) if f[2] is not None else None}
    return None


def parse_start(t):
    """'2008.05~', '2024.10.01~', '2023년 5월~기준일 현재', "중임('25.03.27)" 같은 시작일을 YYYY-MM으로."""
    t = re.sub(r"\s+", "", t or "")
    m = re.search(r"(19\d{2}|20\d{2})[.년\-/](\d{1,2})[^~]*~", t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.search(r"'(\d{2})\.(\d{2})\.\d{2}", t)
    if m:
        return f"20{m.group(1)}-{m.group(2)}"
    return None


def months_between(start, base):
    y, m = map(int, start.split("-"))
    by, bm = int(base[:4]), int(base[5:7])
    return (by - y) * 12 + (bm - m)


def norm_position(p):
    p = re.sub(r"\s+", "", p or "")
    for key, val in [("비상무", "이사"), ("사외이사", "이사"), ("명예회장", "회장"), ("부회장", "부회장"), ("회장", "회장"),
                     ("부사장", "부사장"), ("사장", "사장"), ("전무", "전무"),
                     ("상무", "상무"), ("담당", "직급비공개"),
                     ("펠로우", "전문임원"), ("마스터", "전문임원"), ("연구위원", "전문임원"),
                     ("전문위원", "전문임원"), ("위원회", "이사"),
                     ("이사대우", "이사대우"), ("이사", "이사"), ("감사", "감사"),
                     ("전문임원", "전문임원"), ("고문", "고문"), ("자문", "고문")]:
        if key in p:
            return val
    # 직급을 없앤 그룹(SK '임원', CJ '경영리더', '책임리더')이나 보직명만 적는 회사(실장, 본부장)
    if re.search(r"임원|리더|실장|본부장|팀장|센터장|그룹장|부문장|사업부장|단장|소장|총괄|공장장|사업장장|대표|의장|부장"
                 r"|VP|PRESIDENT|DIRECTOR|HEAD|^PD$|^MD$|CFO|CEO|CTO|COO|CHRO|CSO", p.upper()):
        return "직급비공개"
    return "기타"


def norm_reg(v, default):
    v = re.sub(r"\s+", "", v or "")
    if not v:
        return default
    if "미등기" in v or "비등기" in v:
        return "미등기"
    # '독립이사'는 2026년 상법 개정 후 사외이사의 새 이름
    for k, out in [("독립", "사외이사"), ("사외", "사외이사"), ("기타비상무", "기타비상무이사"), ("비상무", "기타비상무이사"),
                   ("사내", "사내이사"), ("감사", "감사"), ("등기", "등기")]:
        if k in v:
            return out
    return default


def read_xml(path):
    """옛 서식(dart3.xsd)은 utf-8로 선언하고 실제로는 EUC-KR(cp949)로 저장된 경우가 있다."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp949", errors="replace")


def section(xml):
    m = re.search(r"<TITLE[^>]*>[^<]*임원\s*및\s*직원\s*(?:등의?|의)?\s*현황", xml)
    if not m:
        return None
    end = xml.find("<TITLE", m.end())
    return xml[m.start(): end if end > 0 else len(xml)]


def parse_doc(xml):
    sec = section(xml)
    if sec is None:
        return [], [("섹션 없음", "")], None
    base = re.search(r'AUNIT="BASE_DT"\s+AUNITVALUE="(\d{8})"', sec)
    base_date = f"{base.group(1)[:4]}-{base.group(1)[4:6]}-{base.group(1)[6:]}" if base else None
    used, skipped, pos = [], [], 0
    for m in re.finditer(r"<TABLE\b.*?</TABLE>", sec, re.S | re.I):
        table = m.group(0)
        before = sec[pos:m.start()]
        pos = m.end()
        labels, body = header_labels(table)
        cols = map_columns(labels)
        if not {"name", "birth", "position"} <= cols.keys():
            continue
        # 표 바로 앞 문장(공백 제거 후 끝 70자)만 본다. 앞 표의 주석 문구(겸직 변동 등)에 걸리지 않게 하려는 것.
        ctx = re.sub(r"\s+", "", " ".join(cell_text(p) for p in re.findall(r"<P[^>]*>(.*?)</P>", before, re.S)))[-70:]
        grp = re.findall(r'<TABLE-GROUP ACLASS="([^"]+)"', sec[:m.start()])
        kind = "표준표" if grp and grp[-1] == "SH5_DRCT_STT" else "일반표"
        head = "|".join(labels)
        reason = None
        if labels and labels[0].startswith("구분"):
            reason = "첫 칸이 구분(변동 내역)"
        elif any(lab.startswith(x) for lab in labels for x in EXCLUDE_HEADERS):  # 칸 이름이 그 단어로 시작할 때만 (예: '재직기간(최초선임일)'은 제외 대상 아님)
            reason = "후보·변동 표"
        elif re.search(r"(기준일|종료일).{0,25}이후|이후.{0,40}(변동|신규|신임|선임|퇴임)|변동(된|사항|현황|내역)|임원의변동", ctx):
            reason = "기준일 이후 변동 표"
        if reason:
            skipped.append((reason, head[:80]))
            continue
        rows = grid(re.findall(r"<TR\b.*?</TR>", body, re.S | re.I), "TD|TE|TU|TH")
        recs, prev = [], {}
        for r in rows:
            # '〃'(위와 같음) 표시는 앞 행의 값을 이어받는다 (HD 계열)
            get = lambda f: (prev.get(f, "") if f in cols and cols[f] < len(r) and r[cols[f]].strip() in DITTO
                             else (r[cols[f]] if f in cols and cols[f] < len(r) else ""))
            name = clean_name(get("name"))
            birth = parse_birth(get("birth"))
            if not name or not birth or norm_label(name) in ("성명", "이름"):
                continue
            recs.append({f: get(f) for f, _ in HEADER_MAP} | {"name": name, "birth_ym": birth})
            prev = recs[-1]
        # 등기 여부 칸이 없을 때의 기본값: 임기만료일 칸이 없거나 앞 문장에 '미등기'가 있으면 미등기
        if "registered" in cols:  # 칸은 있는데 빈 행은 그 표에서 가장 많은 값을 따른다
            vals = collections.Counter(norm_reg(r["registered"], "") for r in recs if r["registered"].strip())
            default = vals.most_common(1)[0][0] if vals else ("미등기" if "term_end" not in cols else "등기")
        else:
            # 등기 여부 칸이 없으면: 앞 문장에 '미등기'가 있거나, 임기만료일 칸이 없거나 값이 대부분 비었거나,
            # 이 섹션에서 이미 명단 표를 하나 썼으면(등기 표 다음의 두 번째 명단) 미등기로 본다.
            filled = sum(1 for r in recs if r["term_end"].strip() not in ("", "-"))
            if "미등기" in ctx or "term_end" not in cols or filled < len(recs) / 2 or used:
                default = "미등기"
            else:
                default = "등기"
        used.append((kind, head, recs, default))
    return used, skipped, base_date


def fill(rows, f):
    n = len(rows)
    k = sum(1 for r in rows if (r.get(f) or "").strip() not in ("", "-"))
    return f"{k / n:.0%}" if n else "-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", default="2025")
    a = ap.parse_args()
    BUILT.mkdir(parents=True, exist_ok=True)
    reports = list(csv.DictReader(open(RAW / f"reports_{a.year}.csv", encoding="utf-8")))
    all_rows, md = [], [f"# 임원 명단 파싱 리포트 ({a.year} 사업보고서)\n",
                        "자동 생성: `pipeline/03_parse.py`. 사람이 확인할 곳은 '확인 필요' 표시.\n"]
    summary = []
    missing, pays = [], []
    for rep in reports:
        rno = rep["rcept_no"]
        sec_path = RAW / "sections" / f"{rno}.xml"
        doc_path = RAW / "docs" / rno / f"{rno}.xml"
        if not rno or rep.get("status", "ok") != "ok" or not (sec_path.exists() or doc_path.exists()):
            missing.append((rep["group"], rep["company"], rep.get("status") or "파일 없음"))
            continue
        xml = sec_path.read_text(encoding="utf-8") if sec_path.exists() else read_xml(doc_path)
        used, skipped, base_date = parse_doc(xml)
        pay = unreg_pay(xml) or {}
        seen, rows, dups = set(), [], 0
        reg_names, all_names = set(), set()  # 앞에서 쓴 등기 표 / 모든 명단 표의 이름
        for kind, head, recs, default in list(used):
            names = {re.sub(r"\s+", "", r["name"]) for r in recs}
            n_reg = sum(1 for r in recs if norm_reg(r["registered"], default) != "미등기")
            if all_names and len(names & all_names) >= 0.5 * len(names):
                # 뒤에 나온 명단 표가 앞 표와 이름이 절반 이상 겹치면 '주총 이후 전체 명단' 같은 재게시로 보고 뺀다 (롯데칠성 2019)
                used.remove((kind, head, recs, default))
                skipped.append(("앞 표와 이름 절반 이상 중복(주총 이후 명단 등)", head[:80]))
                continue
            all_names |= names
            if recs and n_reg > len(recs) / 2:
                if reg_names and len(names & reg_names) >= 0.3 * len(names):
                    # 등기 표가 또 나오고 앞 표와 이름이 많이 겹치면 '주총 이후 예정 명단'으로 보고 뺀다 (KT, 한진칼 등)
                    used.remove((kind, head, recs, default))
                    skipped.append(("주총 이후 예정 등기 명단(앞 표와 중복)", head[:80]))
                    continue
                reg_names |= names
            for r in recs:
                key = (re.sub(r"\s+", "", r["name"]), r["birth_ym"])
                if key in seen:
                    dups += 1
                    continue
                seen.add(key)
                by = int(r["birth_ym"][:4])
                yr = int((base_date or f"{a.year}-12-31")[:4])
                rows.append({
                    "group": rep["group"], "company": rep["company"], "corp_code": rep["corp_code"],
                    "rcept_no": rno, "base_date": base_date, "table_kind": kind,
                    "reg_class": ("집행임원" if norm_reg(r["registered"], default) != "미등기"
                                  and "집행임원" in re.sub(r"\s+", "", r["position"] + r["duty"])
                                  and "이사" not in r["registered"] else norm_reg(r["registered"], default)),
                    "name": r["name"], "name_key": re.sub(r"\s+", "", r["name"]), "sex": {"남성": "남", "여성": "여"}.get(r["sex"].strip(), r["sex"].strip()), "birth_ym": r["birth_ym"], "age": yr - by,
                    "position": r["position"].replace("\n", " "),
                    "position_norm": (lambda pn, rc: "이사(미등기)" if pn == "이사" and rc == "미등기" else pn)(norm_position(r["position"]), norm_reg(r["registered"], default)),
                    "fulltime": r["fulltime"], "duty": r["duty"], "career": r["career"],
                    "education": r["education"], "relation": r["relation"],
                    "tenure_raw": r["tenure"].replace("\n", " "),
                    "tenure_start": parse_start(r["tenure"]) or parse_date(r.get("appointed", "")),
                    "tenure_months": (months_between(parse_start(r["tenure"]), base_date or f"{a.year}-12-31")
                                      if parse_start(r["tenure"]) and "~" in r["tenure"]
                                      else parse_tenure(r["tenure"].split("\n")[0])),
                    "term_end": r["term_end"],
                    "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rno}",
                })
        all_rows += rows

        # 정형 API(등기임원)와 대조
        api_path = RAW / "api" / f"{rep['corp_code']}_{a.year}_exctv.json"
        api = json.loads(api_path.read_text(encoding="utf-8")) if api_path.exists() else {}
        api_list = api.get("list", [])
        api_reg = {re.sub(r"\s+", "", clean_name(x["nm"])) for x in api_list if "미등기" not in x.get("rgist_exctv_at", "")}
        reg = [r for r in rows if r["reg_class"] not in ("미등기", "집행임원")]  # 집행임원은 이사가 아니라 API 등기임원에 없다
        unreg = [r for r in rows if r["reg_class"] == "미등기"]
        parsed_reg = {r["name_key"] for r in reg}
        officers = {r["name_key"] for r in rows if r["reg_class"] == "집행임원"}
        # 집행임원은 회사에 따라 API 등기임원에 들어가기도(넷마블, 휴젤) 빠지기도(한온시스템) 한다 → 어느 쪽이든 일치로 본다
        miss, extra = api_reg - parsed_reg - officers, parsed_reg - api_reg
        ok = not miss and not extra
        summary.append((rep["group"], rep["company"], len(reg), len(api_reg), len(unreg), len(api_list), ok))
        pays.append({"group": rep["group"], "company": rep["company"], "corp_code": rep["corp_code"], "year": a.year,
                     "unreg_listed": len(unreg), **{k: pay.get(k) for k in ("pay_count", "pay_total_mn", "pay_avg_mn")},
                     "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rno}"})

        md.append(f"\n## {rep['company']} ({rep['group']})\n")
        md.append(f"- 보고서: {rep['report_nm']} / 접수번호 {rno} / 기준일 {base_date}")
        for kind, head, recs, default in used:
            md.append(f"- 사용한 표: {kind}(칸 없을 때 {default}), {len(recs)}행. 머리글 `{head[:120]}`")
        for reason, head in skipped:
            md.append(f"- 제외한 표: {reason}. 머리글 `{head}`")
        if dups:
            md.append(f"- 표 간 중복 제거: {dups}명")
        md.append(f"- 등기 {len(reg)}명 (API {len(api_reg)}명) {'일치' if ok else '**확인 필요**'}"
                  + (f", API에만 있음 {sorted(miss)}" if miss else "")
                  + (f", 파싱에만 있음 {sorted(extra)}" if extra else ""))
        md.append(f"- 미등기 {len(unreg)}명 (보수 현황 인원 {pay.get('pay_count')}, 1인 평균 {pay.get('pay_avg_mn')}백만원)")
        md.append(f"- 직급 분포: {dict(collections.Counter(r['position_norm'] for r in rows).most_common())}")
        md.append("- 미등기 채움률: " + ", ".join(
            f"{k} {fill(unreg, f)}" for k, f in [("담당업무", "duty"), ("주요경력", "career"), ("학력", "education"),
                                               ("최대주주관계", "relation"), ("재직기간", "tenure_raw")]))

    tot_reg = sum(s[2] for s in summary)
    tot_un = sum(s[4] for s in summary)
    head = ["\n## 요약\n", "| 그룹 | 회사 | 등기(파싱) | 등기(API) | 미등기 | API 전체 행 | 등기 대조 |", "|---|---|---|---|---|---|---|"]
    head += [f"| {g} | {c} | {r} | {ar} | {u} | {al} | {'일치' if ok else '확인 필요'} |" for g, c, r, ar, u, al, ok in summary]
    head.append(f"| 합계 | | {tot_reg} | {sum(s[3] for s in summary)} | {tot_un} | {sum(s[5] for s in summary)} | |")
    if missing:
        head.append(f"\n파싱하지 못한 회사 {len(missing)}곳: " + ", ".join(f"{c}({s})" for _, c, s in missing))
    md[2:2] = head

    with open(BUILT / f"unreg_pay_{a.year}.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["group", "company", "corp_code", "year", "unreg_listed", "pay_count",
                                          "pay_total_mn", "pay_avg_mn", "source_url"])
        w.writeheader()
        w.writerows(pays)
    out = BUILT / f"executives_{a.year}.csv"
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)
    (BUILT / f"parse_report_{a.year}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"{len(all_rows)}행 (등기 {tot_reg}, 미등기 {tot_un}) → {out}")


if __name__ == "__main__":
    main()
