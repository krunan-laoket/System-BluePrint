#!/usr/bin/env python3
"""แก้ข้อความหลายจุดหลายไฟล์ในชุดเอกสาร โดยตรวจจำนวนจุดที่แก้ก่อนเขียนไฟล์

ใช้เมื่อต้องเปลี่ยนคำ ตัวเลข หรือประโยคเดียวกันในหลายไฟล์ เช่น หลังรีวิว หรือเมื่อผู้ใช้สั่งเปลี่ยนคำที่ใช้
ถ้ารายการใดหาไม่เจอ หรือจำนวนจุดไม่ตรงกับที่ระบุ สคริปต์จะไม่เขียนไฟล์ใดเลย เพื่อไม่ให้แก้ไปครึ่งเดียว

วิธีใช้:
    python replace_checked.py <โฟลเดอร์โครงการ> <ไฟล์รายการแก้.json> [--dry-run]

ไฟล์รายการแก้เป็น JSON แบบ UTF-8 เป็นรายการของ
    {"file": "docs/03-flows.md", "find": "ข้อความเดิม", "replace": "ข้อความใหม่", "count": 2}

    file     ตำแหน่งไฟล์จากโฟลเดอร์โครงการ หรือ "*" หมายถึง README.md และทุกไฟล์ .md ใน docs/ และ plans/
             ยกเว้นบทบาท source ใน blueprint.json (ชุดเก่าที่ไม่มีทะเบียนยกเว้นชื่อ 00-)
    find     ข้อความเดิม ต้องตรงทุกตัวอักษร
    replace  ข้อความใหม่
    count    จำนวนจุดที่ต้องพบ ถ้าไม่ระบุ: ไฟล์เดียวต้องพบ 1 จุดพอดี ส่วน "*" ต้องพบอย่างน้อย 1 จุดรวมทุกไฟล์

รายการถูกทำตามลำดับ รายการหลังเห็นผลของรายการก่อน
--dry-run แสดงว่าจะแก้อะไรโดยไม่เขียนไฟล์

Python 3.9+ ใช้ไลบรารีมาตรฐาน
รหัสออก: 0 = สำเร็จ, 1 = ตรวจรายการไม่ผ่านหรือเขียนไม่สำเร็จ, 2 = เรียกใช้ผิด
"""
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def all_docs(root):
    files = []
    if (root / "README.md").is_file():
        files.append(root / "README.md")
    for sub in ("docs", "plans"):
        if (root / sub).is_dir():
            files.extend(sorted((root / sub).rglob("*.md")))
    manifest = root / "blueprint.json"
    if manifest.is_file():
        config = json.loads(manifest.read_text(encoding="utf-8"))
        source = config["documents"]["source"]
        if not isinstance(source, str):
            raise ValueError("source ในทะเบียนต้องเป็น path")
        source = (root / source).resolve()
        source.relative_to(root)
        return [f for f in files if f.resolve() != source]
    return [f for f in files if not f.name.startswith("00-")]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if len(args) != 2 or flags - {"--dry-run"}:
        print(__doc__)
        return 2
    root = Path(args[0]).resolve()
    if not root.is_dir():
        print(f"ไม่พบโฟลเดอร์โครงการ: {root}")
        return 2
    try:
        edits = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"อ่านไฟล์รายการแก้ไม่ได้: {e}")
        return 2
    if not isinstance(edits, list):
        print("ไฟล์รายการแก้ต้องเป็นรายการ (list)")
        return 2

    content = {}  # path -> ข้อความปัจจุบันในหน่วยความจำ

    def load(p):
        if p not in content:
            content[p] = p.read_text(encoding="utf-8")
        return content[p]

    problems = []
    report = []
    for n, e in enumerate(edits, 1):
        if not isinstance(e, dict):
            problems.append(f"รายการที่ {n}: ต้องเป็น object")
            continue
        find = e.get("find", "")
        repl = e.get("replace")
        target = e.get("file", "")
        if (not isinstance(find, str) or not find or not isinstance(repl, str)
                or not isinstance(target, str) or not target):
            problems.append(f"รายการที่ {n}: file/find ต้องเป็นข้อความไม่ว่าง และ replace ต้องเป็นข้อความ")
            continue
        expected = e.get("count")
        if expected is not None and (type(expected) is not int or expected < 0):
            problems.append(f"รายการที่ {n}: count ต้องเป็นจำนวนเต็มตั้งแต่ 0")
            continue
        if target == "*":
            try:
                paths = all_docs(root)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                problems.append(f"รายการที่ {n}: อ่านทะเบียนไม่ได้: {exc}")
                continue
        else:
            p = (root / target).resolve()
            if not p.is_file():
                problems.append(f"รายการที่ {n}: ไม่พบไฟล์ {target}")
                continue
            paths = [p]
        try:
            for p in paths:
                p.resolve().relative_to(root)
                if p.suffix != ".md":
                    raise ValueError("แก้ได้เฉพาะไฟล์ Markdown")
            hits = {p: load(p).count(find) for p in paths}
        except (OSError, ValueError) as exc:
            problems.append(f"รายการที่ {n}: path อยู่นอกโครงการหรืออ่านไม่ได้: {exc}")
            continue
        total = sum(hits.values())
        label = find if len(find) <= 40 else find[:37] + "..."
        if expected is None:
            ok = total >= 1 if target == "*" else total == 1
            want = "อย่างน้อย 1" if target == "*" else "1"
        else:
            ok = total == expected
            want = str(expected)
        if not ok:
            problems.append(f"รายการที่ {n}: \"{label}\" พบ {total} จุด แต่ต้องการ {want} จุด ({target})")
            continue
        for p, c in hits.items():
            if c:
                content[p] = content[p].replace(find, repl)
                report.append(f"รายการที่ {n}: {p.relative_to(root).as_posix()} แก้ {c} จุด  \"{label}\"")

    for line in report:
        print(line)
    if problems:
        print("\nไม่ได้เขียนไฟล์ใด เพราะมีรายการที่ไม่ตรง")
        for line in problems:
            print("  " + line)
        return 1
    if "--dry-run" in flags:
        print("\nทดลองเท่านั้น ไม่ได้เขียนไฟล์")
        return 0
    changed = 0
    try:
        for p, text in content.items():
            if text != p.read_text(encoding="utf-8"):
                # Path.write_text(newline=...) requires Python 3.10; open supports 3.9.
                with p.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                changed += 1
    except OSError as exc:
        print(f"เขียนไม่สำเร็จหลังแก้ {changed} ไฟล์: {exc}; ตรวจไฟล์ที่แก้แล้วก่อนลองใหม่")
        return 1
    print(f"\nเขียนไฟล์แล้ว {changed} ไฟล์ จาก {len(edits)} รายการแก้")
    return 0


if __name__ == "__main__":
    sys.exit(main())

