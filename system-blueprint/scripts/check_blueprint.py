"""ตรวจความสอดคล้องเชิงกลไกของชุดเอกสาร system-blueprint

ใช้: python check_blueprint.py <โฟลเดอร์ *-blueprint>

ตรวจ:
  - ไฟล์ที่ต้องมีตามโหมด (ฉบับย่อ/ฉบับเต็ม) หายไป (ผิดพลาด) และไฟล์ที่ไม่รู้จัก (เตือน)
  - เลขอ้างอิงที่ถูกอ้างถึง แต่ไม่ได้ประกาศในไฟล์หลักของตัวอักษรนั้น (ผิดพลาด)
    "ประกาศ" = เลขอยู่ต้นช่องแรกของแถวตาราง หรือต้นหัวข้อ (#) ในไฟล์หลัก
    การเอ่ยถึงที่อื่นในไฟล์หลักเองนับเป็นการอ้างถึง
  - เลขเดียวกันประกาศซ้ำ (ผิดพลาด)
  - รายการที่ควรถูกอ้างถึงแต่ไม่มีไฟล์อื่นอ้างเลย (เตือน)
  - ตารางเกิน 5 คอลัมน์ ตรวจทุกแถว รวมตารางที่ไม่มี | นำหน้า (ผิดพลาด)
  - บล็อกโค้ดในเอกสารหลัก (ผิดพลาด)
  - ป้ายสถานะผิดรูปแบบ หรือ [รอตัดสินใจ] ที่ไม่มีเลข T-xx (ผิดพลาด)
  - รายการที่ประกาศแล้วไม่มีป้ายสถานะในบรรทัดเดียวกัน (เตือน)
  - นับป้ายสถานะ และสัดส่วนข้อสมมติ สำหรับใส่ใน 00-readme
    (ไม่นับใน 00 และ 14 เพราะเป็นคำอธิบายป้าย ไม่ใช่รายการของระบบ)

ใช้ไลบรารีมาตรฐานเท่านั้น ออกจากโปรแกรมด้วยรหัส 1 ถ้ามีข้อผิดพลาด
"""

import re
import sys
from pathlib import Path

FILES = {
    "00": "00-readme.md",
    "01": "01-overview.md",
    "02": "02-users-and-roles.md",
    "03": "03-features.md",
    "04": "04-workflow.md",
    "05": "05-data.md",
    "06": "06-quality-requirements.md",
    "07": "07-integrations.md",
    "08": "08-constraints-and-rules.md",
    "09": "09-system-diagram.md",
    "10": "10-screens.md",
    "11": "11-risks-and-assumptions.md",
    "12": "12-acceptance-criteria.md",
    "13": "13-open-decisions.md",
    "14": "14-glossary.md",
    "15": "15-summary.md",
}
SHORT_ONLY_MISSING = {"05", "07", "10", "11", "12"}  # ไฟล์ที่ฉบับย่อไม่จัดทำ
APPENDIX_RE = re.compile(r"^(99-|09a-)")

# ไฟล์หลักของแต่ละตัวอักษร: (ฉบับเต็ม, ฉบับย่อ) ใช้ฉบับเต็มถ้าไฟล์นั้นมีอยู่
# จึงรองรับชุดที่กำลังอัปเกรดค้างครึ่งทางด้วย
HOME = {
    "G": ("01", "01"),
    "U": ("02", "02"),
    "F": ("03", "03"),
    "P": ("04", "04"),
    "B": ("04", "04"),
    "D": ("05", "08"),
    "Q": ("06", "06"),
    "I": ("07", "08"),
    "C": ("08", "08"),
    "R": ("11", "08"),
    "A": ("12", "15"),
    "T": ("13", "13"),
}
# ตัวอักษรที่ควรถูกอ้างจากไฟล์อื่นอย่างน้อยหนึ่งครั้ง
SHOULD_BE_REFERENCED = {"G", "U", "F", "P", "D", "Q", "I", "A", "T"}
# ตัวอักษรที่บรรทัดประกาศไม่ต้องมีป้าย (T มีสถานะในเนื้อหัวข้อ)
NO_LABEL_NEEDED = {"T"}
# ไฟล์ที่ป้ายเป็นคำอธิบาย ไม่นับสัดส่วน
NOT_COUNTED = {"00", "14"}

ID_RE = re.compile(r"(?<![A-Za-z0-9-])([GUFPBDQICRAT])-(\d{2,3})(?![0-9])")
BAD_ID_RE = re.compile(r"(?<![A-Za-z0-9-])([GUFPBDQICRAT])-(\d)(?![0-9])")
# คำนำหน้าที่บอกว่าเป็นรหัสของผู้ใช้ ไม่ใช่เลขอ้างอิงของเอกสาร
NON_ID_CONTEXT_RE = re.compile(r"(ห้อง|อาคาร|ตึก|ชั้น|รุ่น|โซน|ล็อก|แบบฟอร์ม|เลขที่)\s*$")
LABEL_RE = re.compile(r"\[(ยืนยันแล้ว|ข้อสมมติ|รอตัดสินใจ)([^\]]*)\]")
LABEL_LIKE_RE = re.compile(r"[\[(（]\s*(ยืนยัน|ข้อสมมติ|สมมติ|รอตัดสินใจ|รอการตัดสินใจ)[^\])）]*[\])）]")
T_LIST_RE = re.compile(r"^\s+T-\d{2,3}(\s*,\s*T-\d{2,3})*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
HEADING_RE = re.compile(r"^#{1,6}\s+")


def cells(row: str) -> list:
    row = row.strip().replace("\\|", "")
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return row.split("|")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    if len(sys.argv) != 2:
        print("ใช้: python check_blueprint.py <โฟลเดอร์ *-blueprint>")
        return 2
    folder = Path(sys.argv[1])
    files = sorted(folder.glob("*.md"))
    if not files:
        print(f"ไม่พบไฟล์ .md ใน {folder}")
        return 2

    errors, warnings = [], []
    known_names = {v: k for k, v in FILES.items()}
    by_num = {}
    for p in files:
        if APPENDIX_RE.match(p.name):
            continue
        if p.name in known_names:
            by_num[known_names[p.name]] = p
        else:
            warnings.append(f"{p.name} ไม่ใช่ชื่อไฟล์มาตรฐานของชุด จึงไม่ได้ตรวจ")

    present_full_only = SHORT_ONLY_MISSING & by_num.keys()
    if not present_full_only:
        mode = "ฉบับย่อ"
        required = set(FILES) - SHORT_ONLY_MISSING
    elif present_full_only == SHORT_ONLY_MISSING:
        mode = "ฉบับเต็ม"
        required = set(FILES)
    else:
        mode = "อัปเกรดค้างอยู่"
        required = set(FILES)
    for num in sorted(required - by_num.keys()):
        errors.append(f"ไม่พบไฟล์ {FILES[num]} ซึ่ง{mode if mode != 'อัปเกรดค้างอยู่' else 'ฉบับเต็ม'}ต้องมี")

    def home_of(letter: str) -> str:
        full, short = HOME[letter]
        return full if full in by_num else short

    defined = {}  # id -> where
    refs = {}  # id -> set(files)
    undefined_soft = set()  # id ที่ดูเป็นรหัสของผู้ใช้
    decided_t = set()
    labels = {"ยืนยันแล้ว": 0, "ข้อสมมติ": 0, "รอตัดสินใจ": 0}

    for num, path in sorted(by_num.items()):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        fence = None
        in_decided = False
        in_bare_table = False
        for idx, line in enumerate(lines):
            lineno = idx + 1
            where = f"{path.name}:{lineno}"

            m = FENCE_RE.match(line)
            if m:
                if fence is None:
                    fence = m.group(1)
                    errors.append(f"{where} มีบล็อกโค้ด ({fence}) ในเอกสารหลัก")
                elif m.group(1) == fence:
                    fence = None
                continue

            stripped = line.strip()
            is_heading = bool(HEADING_RE.match(stripped))
            if is_heading and num == "13":
                in_decided = "ตัดสินใจแล้ว" in stripped and not re.search(r"T-\d", stripped)

            # ตาราง: ตรวจทุกแถว ทั้งแบบมีและไม่มี | นำหน้า
            nxt = lines[idx + 1] if idx + 1 < len(lines) else ""
            if "|" not in stripped:
                in_bare_table = False
            elif SEP_RE.match(stripped) and not stripped.startswith("|"):
                in_bare_table = True
            is_row = stripped.startswith("|") or (
                "|" in stripped and (in_bare_table or SEP_RE.match(nxt))
            )
            if is_row and not SEP_RE.match(stripped):
                n = len(cells(stripped))
                if n > 5:
                    errors.append(f"{where} แถวตารางมี {n} คอลัมน์ (เกิน 5)")
            if SEP_RE.match(stripped) and not stripped.startswith("|"):
                warnings.append(f"{where} ตารางไม่มี | นำหน้า อาจแสดงผลเพี้ยนเมื่อวางลง Word")

            line_labels = list(LABEL_RE.finditer(line))
            canonical_spans = []
            for lm in line_labels:
                kind, rest = lm.group(1), lm.group(2)
                ok = (kind != "รอตัดสินใจ" and rest == "") or (
                    kind == "รอตัดสินใจ" and T_LIST_RE.match(rest)
                )
                if kind == "รอตัดสินใจ" and rest.strip() == "T-xx" and num in NOT_COUNTED:
                    canonical_spans.append(lm.span())  # คำอธิบายป้ายใน README/ศัพท์
                    continue
                if ok:
                    canonical_spans.append(lm.span())
                    if num not in NOT_COUNTED:
                        labels[kind] += 1
                elif kind == "รอตัดสินใจ" and not re.search(r"T-\d{2,3}", rest):
                    errors.append(f"{where} ป้าย [รอตัดสินใจ] ไม่มีเลข T-xx")
                    canonical_spans.append(lm.span())
                else:
                    errors.append(f"{where} ป้ายผิดรูปแบบ: {lm.group(0)}")
                    canonical_spans.append(lm.span())
            for bm in LABEL_LIKE_RE.finditer(line):
                if not any(s <= bm.start() < e for s, e in canonical_spans):
                    errors.append(f"{where} ป้ายผิดรูปแบบ: {bm.group(0)} (ใช้ [ยืนยันแล้ว] [ข้อสมมติ] [รอตัดสินใจ T-xx])")

            for bm in BAD_ID_RE.finditer(line):
                warnings.append(f"{where} {bm.group(0)} ดูเหมือนเลขอ้างอิงแต่ไม่ใช่เลขสองหลัก")

            first_cell = cells(stripped)[0].strip() if stripped.startswith("|") else ""
            for m in ID_RE.finditer(line):
                letter = m.group(1)
                rid = f"{letter}-{m.group(2)}"
                is_decl_pos = (
                    num == home_of(letter)
                    and (
                        (first_cell and re.match(rf"^\**{re.escape(rid)}(?![0-9])", first_cell))
                        or (is_heading and re.match(rf"^#{{1,6}}\s+\**{re.escape(rid)}(?![0-9])", stripped))
                    )
                )
                if is_decl_pos:
                    if rid in defined:
                        errors.append(f"{where} {rid} ประกาศซ้ำ (ครั้งแรกที่ {defined[rid]}) ห้ามใช้เลขซ้ำ")
                        continue
                    defined[rid] = where
                    if in_decided and letter == "T":
                        decided_t.add(rid)
                    if (
                        letter not in NO_LABEL_NEEDED
                        and not line_labels
                        and not LABEL_LIKE_RE.search(line)
                        and "ยกเลิก" not in line
                    ):
                        warnings.append(f"{where} {rid} ไม่มีป้ายสถานะในบรรทัดที่ประกาศ")
                else:
                    if NON_ID_CONTEXT_RE.search(line[: m.start()]):
                        undefined_soft.add(rid)
                    refs.setdefault(rid, set()).add(path.name)

    for rid, srcs in sorted(refs.items()):
        if rid in defined:
            continue
        home = home_of(rid[0])
        src = ", ".join(sorted(srcs))
        if rid in undefined_soft:
            warnings.append(
                f"{rid} ใน {src} ไม่ได้ประกาศในไฟล์หลัก ถ้าเป็นรหัสของผู้ใช้ (เช่น เลขห้อง) ไม่ต้องแก้"
            )
        elif home not in by_num:
            errors.append(f"{rid} ถูกอ้างใน {src} แต่ไม่มีไฟล์หลัก {FILES[home]}")
        else:
            errors.append(f"{rid} ถูกอ้างใน {src} แต่ไม่ได้ประกาศในไฟล์หลัก {FILES[home]}")

    for rid, where in sorted(defined.items()):
        if rid[0] not in SHOULD_BE_REFERENCED or rid in decided_t:
            continue
        # เกณฑ์เสร็จ (A) อ้างถึงได้จากเมทริกซ์สอบกลับที่อยู่ในไฟล์เดียวกัน
        own = set() if rid[0] == "A" else {where.split(":")[0]}
        if not (refs.get(rid, set()) - own):
            warnings.append(f"{rid} ({where}) ไม่มีไฟล์อื่นอ้างถึงเลย ตรวจว่าตกหล่นหรือไม่")

    total = sum(labels.values())
    print(f"โหมดที่ตรวจพบ: {mode} · ไฟล์ที่ตรวจ {len(by_num)} ไฟล์")
    if mode == "อัปเกรดค้างอยู่":
        print("  ไฟล์ฉบับเต็มยังไม่ครบ: " + ", ".join(FILES[n] for n in sorted(SHORT_ONLY_MISSING - by_num.keys())))
    print(f"ป้ายสถานะ (ไม่นับ 00, 14): ยืนยันแล้ว {labels['ยืนยันแล้ว']} · ข้อสมมติ {labels['ข้อสมมติ']} · รอตัดสินใจ {labels['รอตัดสินใจ']}")
    if total:
        pct = 100 * labels["ข้อสมมติ"] / total
        flag = "  ← เกิน 40% ต้องเขียนคำเตือนใน 00-readme" if pct > 40 else ""
        print(f"สัดส่วนข้อสมมติ: {pct:.0f}%{flag}")

    print(f"\nผิดพลาด {len(errors)} รายการ")
    for e in errors:
        print(f"  - {e}")
    print(f"\nเตือน {len(warnings)} รายการ")
    for w in warnings:
        print(f"  - {w}")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
