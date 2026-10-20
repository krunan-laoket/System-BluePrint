"""ตรวจความสอดคล้องเชิงกลไกของชุดเอกสาร system-blueprint

ใช้: python check_blueprint.py <โฟลเดอร์ *-blueprint>

ตรวจ:
  - เลขอ้างอิงที่ถูกอ้างถึง แต่ไม่มีในไฟล์หลักของตัวอักษรนั้น (ผิดพลาด)
  - รายการในไฟล์หลักที่ไม่มีไฟล์อื่นอ้างถึงเลย (เตือน)
  - ตารางเกิน 5 คอลัมน์ (ผิดพลาด)
  - บล็อกโค้ดในเอกสารหลัก (ผิดพลาด)
  - ป้าย [รอตัดสินใจ] ที่ไม่มีเลข T-xx (ผิดพลาด)
  - นับป้ายสถานะ และสัดส่วนข้อสมมติ สำหรับใส่ใน 00-readme

ใช้ไลบรารีมาตรฐานเท่านั้น ออกจากโปรแกรมด้วยรหัส 1 ถ้ามีข้อผิดพลาด
"""

import re
import sys
from pathlib import Path

# ไฟล์หลักของแต่ละตัวอักษร: (ฉบับเต็ม, ฉบับย่อ)
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
SHOULD_BE_REFERENCED = {"U", "F", "D", "I", "G"}

ID_RE = re.compile(r"(?<![A-Za-z0-9-])([GUFPBDQICRAT])-(\d{2,3})(?![0-9])")
LABEL_RE = re.compile(r"\[(ยืนยันแล้ว|ข้อสมมติ|รอตัดสินใจ)([^\]]*)\]")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
SKIP_PREFIXES = ("99", "09a")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    if len(sys.argv) != 2:
        print("ใช้: python check_blueprint.py <โฟลเดอร์ *-blueprint>")
        return 2
    folder = Path(sys.argv[1])
    files = sorted(p for p in folder.glob("*.md"))
    if not files:
        print(f"ไม่พบไฟล์ .md ใน {folder}")
        return 2

    by_num = {p.name[:2]: p for p in files if not p.name.startswith(SKIP_PREFIXES)}
    full_mode = "05" in by_num or "07" in by_num or "11" in by_num or "12" in by_num
    mode_idx = 0 if full_mode else 1

    errors, warnings = [], []
    defined = {}  # id -> file
    refs = {}  # id -> set(files)
    labels = {"ยืนยันแล้ว": 0, "ข้อสมมติ": 0, "รอตัดสินใจ": 0}

    for num, path in by_num.items():
        text = path.read_text(encoding="utf-8")
        in_table = False
        in_fence = False
        for lineno, line in enumerate(text.splitlines(), 1):
            where = f"{path.name}:{lineno}"

            if FENCE_RE.match(line):
                if not in_fence:
                    errors.append(f"{where} มีบล็อกโค้ด (```) ในเอกสารหลัก")
                in_fence = not in_fence

            stripped = line.strip()
            if stripped.startswith("|"):
                if not in_table:
                    cols = len(stripped.strip("|").split("|"))
                    if cols > 5:
                        errors.append(f"{where} ตารางมี {cols} คอลัมน์ (เกิน 5)")
                in_table = True
            else:
                in_table = False

            for m in LABEL_RE.finditer(line):
                kind, rest = m.group(1), m.group(2)
                labels[kind] += 1
                if kind == "รอตัดสินใจ" and not re.search(r"T-\d{2,3}", rest):
                    errors.append(f"{where} ป้าย [รอตัดสินใจ] ไม่มีเลข T-xx")

            for m in ID_RE.finditer(line):
                rid = f"{m.group(1)}-{m.group(2)}"
                home = HOME[m.group(1)][mode_idx]
                if num == home:
                    defined.setdefault(rid, where)
                else:
                    refs.setdefault(rid, set()).add(path.name)

    for rid, srcs in sorted(refs.items()):
        letter = rid[0]
        home = HOME[letter][mode_idx]
        if rid in defined:
            continue
        if home not in by_num:
            errors.append(f"{rid} ถูกอ้างใน {', '.join(sorted(srcs))} แต่ไม่มีไฟล์หลัก {home}")
        else:
            errors.append(
                f"{rid} ถูกอ้างใน {', '.join(sorted(srcs))} แต่ไม่มีในไฟล์หลัก {by_num[home].name}"
            )

    for rid, where in sorted(defined.items()):
        if rid[0] in SHOULD_BE_REFERENCED and rid not in refs:
            warnings.append(f"{rid} ({where}) ไม่มีไฟล์อื่นอ้างถึงเลย ตรวจว่าตกหล่นหรือไม่")

    total = sum(labels.values())
    print(f"โหมดที่ตรวจพบ: {'ฉบับเต็ม' if full_mode else 'ฉบับย่อ'} · ไฟล์ที่ตรวจ {len(by_num)} ไฟล์")
    print(f"ป้ายสถานะ: ยืนยันแล้ว {labels['ยืนยันแล้ว']} · ข้อสมมติ {labels['ข้อสมมติ']} · รอตัดสินใจ {labels['รอตัดสินใจ']}")
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
