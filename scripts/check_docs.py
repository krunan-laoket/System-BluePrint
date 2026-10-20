#!/usr/bin/env python3
"""ตรวจชุดเอกสารความต้องการและออกแบบระบบ (README.md, docs/, plans/) ด้วยกฎที่เครื่องตรวจได้

วิธีใช้:
    python check_docs.py <โฟลเดอร์โครงการ> [--render]

--render  ลองวาดผัง Mermaid ทุกผังด้วย Chrome หรือ Edge ในเครื่อง (ต้องมีอินเทอร์เน็ต)

Python 3.9+ ใช้ไลบรารีมาตรฐาน ต้องมี blueprint.json ตาม references/file-set.md
รหัสออก: 0 = ไม่พบข้อผิดพลาดเชิงกลไก (อาจมีคำเตือน/สิ่งที่ไม่ได้ตรวจ), 1 = พบข้อผิดพลาด, 2 = เรียกใช้ผิด
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

errors = []    # (file, line, message)
warnings = []  # (file, line, message)
notes = []     # ข้อความแจ้งทั่วไป
BASE_ROLES = {"source", "overview", "features", "flows", "decisions", "development_plan", "acceptance"}
TECH_ROLES = {"architecture", "data_model", "api"}
DECISION_STATUSES = {"ตัดสินใจแล้ว", "ใช้ค่าที่เสนอไปก่อน รอยืนยันกับลูกค้า", "รอตัดสินใจ"}


def err(f, line, msg):
    errors.append((str(f), line, msg))


def warn(f, line, msg):
    warnings.append((str(f), line, msg))


# ---------- อ่านไฟล์และแยกส่วนที่เป็นโค้ด ----------

FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
NUM_TOKEN = re.compile(r"^(\d+(?:\.\d+)*)\.?(?=\s|$|:)")
LIST_NUM = re.compile(r"^\s*[-*]\s+(\d+(?:\.\d+)+)\s")


class Doc:
    def __init__(self, path, root):
        self.path = path
        self.rel = path.relative_to(root).as_posix()
        self.text = path.read_text(encoding="utf-8")
        self.lines = self.text.splitlines()
        self.in_code = []          # แต่ละบรรทัดอยู่ในบล็อกโค้ดหรือไม่
        self.headings = []         # (line_no, level, text)
        self.mermaid = []          # (start_line, code)
        self.fence_count = 0
        self.role = None
        self._scan()

    def _scan(self):
        inside = False
        lang = ""
        buf = []
        start = 0
        marker = ""
        length = 0
        for i, line in enumerate(self.lines, 1):
            fence = FENCE.match(line)
            if fence:
                if not inside:
                    self.fence_count += 1
                    inside = True
                    marker = fence.group(1)[0]
                    length = len(fence.group(1))
                    lang = fence.group(2).strip().lower()
                    buf = []
                    start = i
                elif fence.group(1)[0] == marker and len(fence.group(1)) >= length and not fence.group(2).strip():
                    self.fence_count += 1
                    if lang == "mermaid":
                        self.mermaid.append((start, "\n".join(buf)))
                    inside = False
                    lang = ""
                else:
                    buf.append(line)
                self.in_code.append(True)
                continue
            self.in_code.append(inside)
            if inside:
                buf.append(line)
            else:
                m = HEADING.match(line)
                if m:
                    self.headings.append((i, len(m.group(1)), m.group(2)))

    def prose_lines(self):
        """บรรทัดที่ไม่ได้อยู่ในบล็อกโค้ด"""
        for i, line in enumerate(self.lines, 1):
            if not self.in_code[i - 1]:
                yield i, line

    def heading_numbers(self):
        nums = set()
        for _, _, text in self.headings:
            m = NUM_TOKEN.match(text)
            if m:
                nums.add(m.group(1))
        return nums

    def referable_numbers(self):
        """เลขที่ไฟล์อื่นอ้างถึงได้: เลขหัวข้อ และเลขข้อของรายการย่อย เช่น '- 1.2.5 ...'"""
        nums = self.heading_numbers()
        for _, line in self.prose_lines():
            m = LIST_NUM.match(line)
            if m:
                nums.add(m.group(1))
        return nums


def strip_inline_code(line):
    return re.sub(r"`[^`]*`", " ", line)


# ---------- การตรวจแต่ละเรื่อง ----------

def check_structure(root):
    readme = root / "README.md"
    docs = root / "docs"
    plans = root / "plans"
    if not readme.is_file():
        err("README.md", 0, "ไม่พบ README.md")
    if not docs.is_dir():
        err("docs/", 0, "ไม่พบโฟลเดอร์ docs/")
    elif not list(docs.glob("*.md")):
        err("docs/", 0, "โฟลเดอร์ docs/ ไม่มีไฟล์ .md")
    if not plans.is_dir():
        err("plans/", 0, "ไม่พบโฟลเดอร์ plans/")
    elif not list(plans.glob("*.md")):
        err("plans/", 0, "โฟลเดอร์ plans/ ไม่มีไฟล์ .md")


def check_manifest(root):
    """Return validated role/path pairs; report malformed data without guessing a mode."""
    path = root / "blueprint.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        err("blueprint.json", 0, f"อ่านทะเบียนไม่ได้: {e}; สร้างตาม references/file-set.md")
        return {}
    if not isinstance(config, dict):
        err("blueprint.json", 0, "ทะเบียนต้องเป็น JSON object")
        return {}
    if type(config.get("schema_version")) is not int or config["schema_version"] != 1:
        err("blueprint.json", 0, "schema_version ต้องเป็นจำนวนเต็ม 1")
    mode = config.get("mode")
    if mode not in ("business", "full"):
        err("blueprint.json", 0, "mode ต้องเป็น business หรือ full")
    documents = config.get("documents")
    if not isinstance(documents, dict):
        err("blueprint.json", 0, "documents ต้องเป็น object บทบาท: path")
        documents = {}
    omissions = config.get("omissions", {})
    if not isinstance(omissions, dict):
        err("blueprint.json", 0, "omissions ต้องเป็น object")
        omissions = {}
    if set(omissions) - {"api"}:
        err("blueprint.json", 0, "เว้นได้เฉพาะบทบาท api ใน full พร้อมเหตุผล")
    if omissions and (mode != "full" or "api" in documents):
        err("blueprint.json", 0, "omissions.api ใช้เฉพาะ full ที่ไม่มีบทบาท api")
    if "api" in omissions and (not isinstance(omissions["api"], str) or not omissions["api"].strip()):
        err("blueprint.json", 0, "omissions.api ต้องมีเหตุผลเป็นข้อความที่ไม่ว่าง")
    required = BASE_ROLES | ({"architecture", "data_model"} if mode == "full" else set())
    if mode == "full" and "api" not in omissions:
        required.add("api")
    for role in sorted(required - set(documents)):
        err("blueprint.json", 0, f"ขาดบทบาทเอกสารจำเป็น: {role}")
    if mode == "business" and TECH_ROLES & set(documents):
        err("blueprint.json", 0, "business ต้องไม่มีบทบาท architecture, data_model หรือ api")
    plans = config.get("plans")
    if not isinstance(plans, list) or not plans:
        err("blueprint.json", 0, "plans ต้องเป็นรายการ path ของแผนอย่างน้อยหนึ่งไฟล์")
        plans = []
    registered = set()
    valid_roles = {}

    def register(value, folder):
        if not isinstance(value, str) or not value.strip():
            err("blueprint.json", 0, "path ต้องเป็นข้อความที่ไม่ว่าง")
            return False
        parts = PurePosixPath(value).parts
        if ("\\" in value or value != PurePosixPath(value).as_posix() or
                len(parts) < 2 or parts[0] != folder or ".." in parts or
                not value.endswith(".md")):
            err("blueprint.json", 0, f"path ไม่ถูกต้องสำหรับ {folder}/: {value}")
            return False
        try:
            (root / value).resolve().relative_to(root.resolve())
        except ValueError:
            err("blueprint.json", 0, f"path ชี้ออกนอกโครงการ: {value}")
            return False
        if value in registered:
            err("blueprint.json", 0, f"path ในทะเบียนซ้ำ: {value}")
        registered.add(value)
        if not (root / value).is_file():
            err("blueprint.json", 0, f"ไม่พบไฟล์ในทะเบียน: {value}")
        if folder == "docs" and not re.fullmatch(r"docs/\d\d-[a-z0-9][a-z0-9-]*\.md", value):
            err("blueprint.json", 0, f"ชื่อ docs ต้องเป็นเลข 2 หลัก-ชื่อภาษาอังกฤษ.md: {value}")
        return True

    for role, value in documents.items():
        if not isinstance(role, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", role):
            err("blueprint.json", 0, f"ชื่อบทบาทไม่ถูกต้อง: {role}")
        if register(value, "docs"):
            valid_roles[role] = value
            if mode == "business" and re.search(r"-(architecture|data-model|api-spec)\.md$", value):
                err("blueprint.json", 0, f"business ไม่ควรมีไฟล์เทคนิค: {value}")
    for value in plans:
        register(value, "plans")
    for folder in ("docs", "plans"):
        for p in (root / folder).rglob("*.md"):
            if p.relative_to(root).as_posix() not in registered:
                err("blueprint.json", 0, f"ไฟล์ไม่ได้อยู่ในทะเบียน: {p.relative_to(root).as_posix()}")
    return valid_roles


def check_numbering(root):
    docs = root / "docs"
    if not docs.is_dir():
        return
    seen = {}
    for p in sorted(docs.glob("*.md")):
        m = re.match(r"^(\d\d)-[a-z0-9][a-z0-9-]*\.md$", p.name)
        if not m:
            err(f"docs/{p.name}", 0, "ชื่อไฟล์ไม่ได้อยู่ในรูปแบบ เลข 2 หลัก-ชื่อตัวพิมพ์เล็กคั่นด้วยขีดกลาง.md")
            continue
        seen.setdefault(int(m.group(1)), []).append(p.name)
    if not seen:
        return
    for n, names in sorted(seen.items()):
        if len(names) > 1:
            err("docs/", 0, f"เลขนำหน้า {n:02d} ซ้ำกัน: {', '.join(names)}")
    nums = sorted(seen)
    if nums[0] != 0:
        err("docs/", 0, f"เลขนำหน้าไฟล์ควรเริ่มที่ 00 แต่เริ่มที่ {nums[0]:02d}")
    for a, b in zip(nums, nums[1:]):
        if b != a + 1:
            err("docs/", 0, f"เลขนำหน้าไฟล์ข้ามจาก {a:02d} ไป {b:02d}")


def check_fences(docs):
    known = ("flowchart", "graph", "sequencediagram", "statediagram", "erdiagram", "classdiagram",
             "gantt", "journey", "pie", "mindmap", "timeline", "quadrantchart", "gitgraph",
             "c4context", "requirementdiagram", "block-beta", "sankey-beta", "xychart-beta")
    for d in docs:
        if d.fence_count % 2 != 0:
            err(d.rel, 0, "บล็อกโค้ด (```) เปิดปิดไม่ครบคู่")
        for start, code in d.mermaid:
            first = next((l.strip() for l in code.splitlines() if l.strip() and not l.strip().startswith("%%")), "")
            if not first:
                err(d.rel, start, "ผัง Mermaid ว่างเปล่า")
            elif not first.lower().replace(" ", "").startswith(known):
                warn(d.rel, start, f"บรรทัดแรกของผัง Mermaid ไม่ใช่ชนิดผังที่รู้จัก: {first[:40]}")


FILE_TOKEN = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.md)(?![\w-])")
MD_LINK = re.compile(r"!?\[[^\]]*\]\((?:<([^>]+)>|([^\s)]+))(?:\s+\"[^\"]*\")?\)")


def resolve(root, doc, token):
    token = token.split("#")[0]
    if not token:
        return True
    if "/" in token:
        return any((b / token).is_file() for b in (root, doc.path.parent))
    bases = [root, doc.path.parent, root / "docs", root / "plans"]
    return any((b / token).is_file() for b in bases)


def is_source_doc(d):
    return d.role == "source"


def check_file_refs(root, docs):
    # ชื่อไฟล์ต้นทางของลูกค้าที่บันทึกไว้ใน 00-source.md ไม่ต้องมีอยู่ในโครงการ
    source_names = set()
    for d in docs:
        if is_source_doc(d):
            for m in FILE_TOKEN.finditer(d.text):
                source_names.add(Path(m.group(1)).name)
    for d in docs:
        if is_source_doc(d):
            continue
        for i, line in d.prose_lines():
            links = list(MD_LINK.finditer(line))
            for m in FILE_TOKEN.finditer(line):
                if any(link.start() <= m.start() < link.end() for link in links):
                    continue  # labels are not paths; actual Markdown targets are checked below
                if re.search(r"[a-z][a-z0-9+.-]*://[^\s]*$", line[:m.start()], re.I):
                    continue
                tok = m.group(1)
                if any(c in tok for c in "<>*{}"):
                    continue
                if Path(tok).name in source_names and not re.match(r"^(docs|plans)/", tok):
                    continue
                if not resolve(root, d, tok):
                    err(d.rel, i, f"อ้างถึงไฟล์ที่ไม่มีอยู่: {tok}")
            for m in links:
                target = unquote(m.group(1) or m.group(2))
                try:
                    parts = urlsplit(target)
                except ValueError:
                    err(d.rel, i, f"รูปแบบลิงก์ไม่ถูกต้อง: {target}")
                    continue
                if parts.scheme or parts.netloc or not parts.path:
                    continue
                # Markdown links resolve relative to the referring file, not to docs/ or the project root.
                if not (d.path.parent / parts.path).is_file():
                    err(d.rel, i, f"ลิงก์ชี้ไปยังไฟล์ที่ไม่มีอยู่: {target}")


SECTION_REF = re.compile(r"((?:[\w.-]+/)*\d\d-[a-z0-9-]+\.md)`?\s+(?:ข้อ|หัวข้อที่|section|Section)\s+")
NUMBER = re.compile(r"(\d+(?:\.\d+)*)(?![\d.]*-)")
SEP = re.compile(r"\s*(?:,|และ|ถึง|and|to|-|–)\s*")


def parse_numbers(text):
    """อ่านเลขหัวข้อที่ตามหลังคำว่า ข้อ เช่น '3, 4 และ 6' หรือ '1 ถึง 3'"""
    out = []
    pos = 0
    while True:
        m = NUMBER.match(text, pos)
        if not m:
            break
        out.append(m.group(1).rstrip("."))
        pos = m.end()
        s = SEP.match(text, pos)
        if not s:
            break
        pos = s.end()
    return out


def check_section_refs(root, docs):
    by_name = {}
    for d in docs:
        by_name.setdefault(d.path.name, d)
    flows = [d for d in docs if d.role == "flows"]
    decisions = [d for d in docs if d.role == "decisions"]
    flow_nums = set()
    for d in flows:
        for _, _, text in d.headings:
            flow_nums.update(re.findall(r"\bFlow\s+(\d+)", text))
    decision_nums = set()
    for d in decisions:
        decision_nums |= d.heading_numbers()

    for d in docs:
        if is_source_doc(d):
            continue
        for i, line in d.prose_lines():
            for m in SECTION_REF.finditer(line):
                target = by_name.get(Path(m.group(1)).name)
                if not target:
                    continue  # ไฟล์ไม่มีอยู่ ถูกรายงานแล้วในการตรวจไฟล์
                have = target.referable_numbers()
                for n in parse_numbers(line[m.end():m.end() + 60]):
                    if n not in have:
                        err(d.rel, i, f"อ้างถึง {target.path.name} ข้อ {n} แต่ไฟล์นั้นไม่มีหัวข้อเลข {n}")
            for n in re.findall(r"\bFlow\s+(\d+)", line):
                if n not in flow_nums:
                    err(d.rel, i, f"อ้างถึง Flow {n} แต่ไฟล์ลำดับการทำงานไม่มีหัวข้อ Flow {n}")
            for n in re.findall(r"ประเด็นที่\s+(\d+)", line):
                if n not in decision_nums:
                    err(d.rel, i, f"อ้างถึงประเด็นที่ {n} แต่ไฟล์ประเด็นตัดสินใจไม่มีหัวข้อเลข {n}")


def check_readme_lists_files(root, docs):
    readme = next((d for d in docs if d.rel == "README.md"), None)
    if not readme:
        return
    for sub in ("docs", "plans"):
        folder = root / sub
        if not folder.is_dir():
            continue
        for p in sorted(folder.rglob("*.md")):
            rel = p.relative_to(root).as_posix()
            if rel not in readme.text and p.name not in readme.text:
                err("README.md", 0, f"README ไม่ได้กล่าวถึงไฟล์ {rel}")
    for i, line in enumerate(readme.lines, 1):
        if not readme.in_code[i - 1]:
            continue
        for m in FILE_TOKEN.finditer(line):
            tok = m.group(1)
            if any(c in tok for c in "<>*{}"):
                continue
            if not resolve(root, readme, tok):
                err("README.md", i, f"แผนผังหรือบล็อกโค้ดใน README อ้างถึงไฟล์ที่ไม่มีอยู่จริง: {tok}")
    if "blueprint.json" not in readme.text:
        err("README.md", 0, "README ไม่ได้กล่าวถึงทะเบียน blueprint.json")


CODE = re.compile(r"(?<![\w-])([A-Z]{1,4}(?:-[A-Z]{1,3})?)-([A-Z]?\d{1,4})(?![\w-])")
NON_ID_CONTEXT_RE = re.compile(r"(ห้อง|อาคาร|ตึก|ชั้น|รุ่น|โซน|ล็อก|แบบฟอร์ม|เลขที่|มาตรฐาน|รหัสไปรษณีย์)\s*$")
KNOWN_TECH_PREFIXES = {"ISO", "IEC", "UTF", "SHA", "AES", "RSA", "TLS", "SSL", "HTTP", "RFC", "WCAG", "PDPA", "COVID"}


def check_invented_codes(docs):
    found = {}  # prefix -> {code: (file, line)}
    for d in docs:
        if is_source_doc(d):
            continue
        for i, line in d.prose_lines():
            clean = strip_inline_code(line)
            for m in CODE.finditer(clean):
                prefix = m.group(1)
                if prefix in KNOWN_TECH_PREFIXES:
                    continue
                if NON_ID_CONTEXT_RE.search(clean[:m.start()]):
                    continue
                found.setdefault(prefix, {}).setdefault(m.group(0), (d.rel, i))
    for prefix, codes in sorted(found.items()):
        if len(codes) >= 3:
            sample = sorted(codes)[:4]
            f, line = codes[sample[0]]
            warn(f, line, f"พบรหัสขึ้นต้นด้วย {prefix}- จำนวน {len(codes)} รหัส เช่น {', '.join(sample)} "
                          f"ถ้าเป็นรหัสที่ตั้งขึ้นเอง ให้เปลี่ยนเป็นเรียกด้วยชื่อหรือเลขหัวข้อ")


LEFTOVER = re.compile(r"\b(TODO|TBD|FIXME|Lorem ipsum)\b")
PLACEHOLDER = re.compile(r"<([^<>\n/][^<>\n]{1,60})>")
HTML_TAGS = {"br", "b", "i", "u", "em", "strong", "sub", "sup", "kbd", "span", "div", "p", "a", "img",
             "details", "summary", "table", "tr", "td", "th", "thead", "tbody", "ul", "ol", "li", "hr", "code", "pre"}


def check_leftovers(docs):
    for d in docs:
        if is_source_doc(d):
            continue  # ต้นทางเก็บตามที่ได้รับ ไม่แก้ placeholder ที่เป็นส่วนหนึ่งของต้นฉบับ
        for i, line in enumerate(d.lines, 1):
            if re.search(r"\{\{[a-z_]+\}\}", line):
                err(d.rel, i, "ยังไม่ได้แทนชื่อบทบาท {{role}} ด้วย path จริงจากทะเบียน")
        for i, line in d.prose_lines():
            clean = strip_inline_code(line)
            m = LEFTOVER.search(clean)
            if m:
                warn(d.rel, i, f"พบข้อความค้าง: {m.group(1)}")
            for p in PLACEHOLDER.finditer(clean):
                name = p.group(1).strip().split()[0].lower().rstrip("/")
                if name in HTML_TAGS:
                    continue
                warn(d.rel, i, f"พบช่องที่อาจยังไม่ได้แทนค่า: <{p.group(1)}>")


def check_empty_sections(docs):
    for d in docs:
        text = re.sub(r"<!--.*?-->", "", d.text, flags=re.S)
        body_lines = [line for line in text.splitlines()
                      if line.strip() and not HEADING.match(line)
                      and line.strip() not in ("---", "***", "___")
                      and not FENCE.match(line)]
        if not body_lines:
            err(d.rel, 0, "เอกสารว่างหรือมีแต่หัวข้อ ไม่มีเนื้อหา")
        hs = d.headings
        for idx, (line_no, level, text) in enumerate(hs):
            end = hs[idx + 1][0] - 1 if idx + 1 < len(hs) else len(d.lines)
            body = [l for l in d.lines[line_no:end] if l.strip() and l.strip() != "---"]
            if body:
                continue
            if idx + 1 < len(hs) and hs[idx + 1][1] > level:
                continue  # หัวข้อที่มีแต่หัวข้อย่อย ถือว่ามีเนื้อหา
            if line_no <= 3:
                continue  # ชื่อเอกสารและชื่อรองที่ต้นไฟล์
            warn(d.rel, line_no, f"หัวข้อไม่มีเนื้อหา: {text}")


PENDING_MARK = re.compile(r"\([^()\n]*รอยืนยัน\)")  # (รอยืนยัน) หรือ (จำนวนรูปรอยืนยัน) ไม่รวมชื่อสถานะเช่น รอยืนยันผล
CLIENT_MARK = re.compile(r"\(ลูกค้ากำหนด\)")


def check_pending_marks(docs):
    decisions = [d for d in docs if d.role == "decisions"]
    marked = [
        d for d in docs
        if (PENDING_MARK.search(d.text) or CLIENT_MARK.search(d.text))
        and d.rel != "README.md"
        and d not in decisions
        and not is_source_doc(d)
    ]
    if marked and not decisions:
        warn("docs/", 0, "มีเครื่องหมาย \"(รอยืนยัน)\" หรือ \"(ลูกค้ากำหนด)\" ในเอกสาร แต่ไม่พบไฟล์ประเด็นตัดสินใจ (ชื่อไฟล์ที่มีคำว่า decisions)")
        return
    names = [d.path.name for d in decisions]
    for d in marked:
        if not any(n in d.text for n in names) and "ประเด็นที่" not in d.text:
            warn(d.rel, 0, "ไฟล์นี้มีเครื่องหมาย \"(รอยืนยัน)\" หรือ \"(ลูกค้ากำหนด)\" แต่ไม่ได้ชี้ไปที่ไฟล์ประเด็นตัดสินใจหรือเลขประเด็น ผู้อ่านจะหารายละเอียดไม่เจอ")


ROW = re.compile(r"^\s*\|(.+)\|\s*$")
SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}")


def table_rows(doc, start, end):
    """แถวข้อมูลของตารางในช่วงบรรทัด ไม่รวมหัวตารางและเส้นคั่น"""
    rows = []
    lines = doc.lines[start:end]
    for i, line in enumerate(lines):
        if doc.in_code[start + i] or not ROW.match(line) or SEPARATOR.match(line):
            continue
        is_header = i + 1 < len(lines) and SEPARATOR.match(lines[i + 1])
        if not is_header:
            rows.append([c.strip() for c in ROW.match(line).group(1).split("|")])
    return rows


def check_wide_tables(docs):
    """ตารางที่คอลัมน์มากเกินไปอาจล้นหน้ากระดาษเมื่อคัดลอกลง Word/PDF"""
    for d in docs:
        if is_source_doc(d):
            continue
        warned_table = False
        for i, line in d.prose_lines():
            clean = line.strip().replace("\\|", "")
            if not (clean.startswith("|") and clean.endswith("|")):
                warned_table = False
                continue
            cols = len(clean[1:-1].split("|"))
            if cols > 6 and not warned_table:
                warn(d.rel, i, f"ตารางมี {cols} คอลัมน์ (เกิน 6 คอลัมน์) อาจล้นหน้ากระดาษเมื่อวางลง Word พิจารณาแยกตารางหรือยุบคอลัมน์")
                warned_table = True


def check_decision_summary(docs):
    """ตารางสรุปประเด็นต้องมีเลขตรงกับหัวข้อประเด็นจริง"""
    for d in docs:
        if d.role != "decisions":
            continue
        heading_nums = {n for n in d.heading_numbers() if n.isdigit()}
        first_numbered = min((l for l, _, t in d.headings if NUM_TOKEN.match(t)), default=len(d.lines) + 1)
        summary_nums = {r[0] for r in table_rows(d, 0, first_numbered - 1) if r and r[0].isdigit()}
        if not heading_nums and not summary_nums:
            continue  # ไม่มีประเด็นจริง ต้องอธิบายไว้ในเนื้อหาแทนการสร้างประเด็นปลอม
        if not summary_nums:
            err(d.rel, 0, "มีหัวข้อประเด็น แต่ไม่มีตารางสรุปเลขประเด็น")
        for n in sorted(heading_nums - summary_nums, key=int):
            err(d.rel, 0, f"ประเด็นที่ {n} มีหัวข้อ แต่ไม่อยู่ในตารางสรุปประเด็น")
        for n in sorted(summary_nums - heading_nums, key=int):
            err(d.rel, 0, f"ตารางสรุปประเด็นมีประเด็นที่ {n} แต่ไม่มีหัวข้อของประเด็นนั้น")


def check_decision_statuses(docs):
    for d in docs:
        if d.role != "decisions":
            continue
        summary = {}
        for row in table_rows(d, 0, len(d.lines)):
            if len(row) >= 3 and row[0].isdigit():
                summary[row[0]] = row[2].strip("*` ")
        headings = [(line, text) for line, _, text in d.headings
                    if NUM_TOKEN.match(text) and NUM_TOKEN.match(text).group(1).isdigit()]
        for idx, (line, text) in enumerate(headings):
            number = NUM_TOKEN.match(text).group(1)
            end = headings[idx + 1][0] - 1 if idx + 1 < len(headings) else len(d.lines)
            block = "\n".join(d.lines[line:end])
            match = re.search(r"^\*\*สถานะ:\*\*\s*([^\n(]+)", block, re.M)
            status = match.group(1).strip("*` ") if match else ""
            if status not in DECISION_STATUSES:
                err(d.rel, line, f"ประเด็นที่ {number} ไม่มีสถานะที่กำหนด หรือใช้สถานะไม่ถูกต้อง: {status or '(ไม่มี)'}")
            if number in summary and summary[number] != status:
                err(d.rel, line, f"สถานะประเด็นที่ {number} ไม่ตรงกับตารางสรุป")
            if "**ทางเลือกและผลกระทบ**" not in block:
                err(d.rel, line, f"ประเด็นที่ {number} ขาดหัวข้อ **ทางเลือกและผลกระทบ**")
            for label in ("ที่มา", "ค่าที่ใช้ในเอกสาร / คำแนะนำของทีม", "ผลกระทบเมื่อคำตอบเปลี่ยน"):
                field = re.search(r"^\*\*" + re.escape(label) + r":\*\*\s*([^\n]+)", block, re.M)
                value = field.group(1).strip() if field else ""
                if not value or value == "-":
                    err(d.rel, line, f"ประเด็นที่ {number} ขาดรายละเอียด {label}")
            if status == "ตัดสินใจแล้ว":
                for label in ("ผู้ยืนยัน", "วันที่ยืนยัน", "หลักฐานยืนยัน"):
                    field = re.search(r"^\*\*" + label + r":\*\*\s*([^\n]+)", block, re.M)
                    value = field.group(1).strip() if field else ""
                    if not value or value in {"-", "รอระบุ", "ยังไม่ระบุ", "ยังไม่ยืนยัน", "ไม่ทราบ"}:
                        err(d.rel, line, f"ประเด็นที่ {number} ตัดสินใจแล้วแต่ขาด {label}")


def check_duplicate_numbers(docs):
    for d in docs:
        if is_source_doc(d):
            continue
        seen = set()
        for line, _, title in d.headings:
            m = NUM_TOKEN.match(title)
            if m:
                if m.group(1) in seen:
                    err(d.rel, line, f"เลขหัวข้อซ้ำ: {m.group(1)}")
                seen.add(m.group(1))


def actual_counts(docs):
    """นับของจริงให้ผู้เขียนเทียบกับตัวเลขที่สรุปไว้ในเอกสาร"""
    out = []
    total_pending_marks = 0
    for d in docs:
        if not is_source_doc(d) and d.rel != "README.md":
            total_pending_marks += len(PENDING_MARK.findall(d.text))
        role = d.role
        if role == "flows":
            flows = {n for _, _, t in d.headings for n in re.findall(r"\bFlow\s+(\d+)", t)}
            if flows:
                out.append(f"{d.rel}: Flow {len(flows)} เรื่อง")
        if role == "decisions":
            nums = [n for n in d.heading_numbers() if n.isdigit()]
            statuses = {}
            unresolved = 0
            for line in d.lines:
                m = re.match(r"^\*\*สถานะ:\*\*\s*(.+?)(?:\s*\(|$)", line.strip())
                if m:
                    key = m.group(1).strip()
                    statuses[key] = statuses.get(key, 0) + 1
                    if "ตัดสินใจแล้ว" not in key:
                        unresolved += 1
            if nums:
                detail = ", ".join(f"{k} {v}" for k, v in sorted(statuses.items()))
                ratio = (unresolved / len(nums)) * 100
                extra = f" — รอยืนยัน/รอตัดสินใจ {ratio:.0f}%"
                out.append(f"{d.rel}: ประเด็น {len(nums)} เรื่อง" + (f" ({detail}){extra}" if detail else ""))
        if role == "screens":
            hs = d.headings
            total = 0
            parts = []
            for idx, (line_no, level, text) in enumerate(hs):
                end = hs[idx + 1][0] - 1 if idx + 1 < len(hs) else len(d.lines)
                rows = [r for r in table_rows(d, line_no, end) if r and r[0].isdigit()]
                if rows:
                    parts.append(f"{text[:30]} {len(rows)}")
                    total += len(rows)
            if total:
                out.append(f"{d.rel}: แถวที่มีเลขลำดับ {total} แถว ({'; '.join(parts)})")
        if role == "api":
            methods = sum(1 for line in d.lines if re.match(r"^\s*\|\s*(GET|POST|PUT|PATCH|DELETE)\b", line))
            if methods:
                out.append(f"{d.rel}: แถว API {methods} แถว")
        if role == "data_model":
            entities = [t for _, level, t in d.headings if level == 3 and re.match(r"^[A-Z][A-Z0-9_]+\b", t)]
            if entities:
                out.append(f"{d.rel}: Entity {len(entities)} รายการ")
    if total_pending_marks:
        out.append(f"เครื่องหมาย (..รอยืนยัน) ทั้งชุด: {total_pending_marks} จุด")
    return out


# ---------- ลองวาดผัง Mermaid ----------

def find_browser():
    candidates = [
        os.environ.get("CHROME_PATH", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "msedge", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def render_mermaid(docs):
    blocks = [(d.rel, start, code) for d in docs for start, code in d.mermaid]
    if not blocks:
        notes.append("ไม่มีผัง Mermaid ให้วาด")
        return
    browser = find_browser()
    if not browser:
        notes.append("ข้ามการวาดผัง Mermaid: ไม่พบ Chrome หรือ Edge ในเครื่อง")
        return
    parts = ['<!doctype html><meta charset="utf-8"><body>']
    for n, (_, _, code) in enumerate(blocks):
        parts.append(f'<pre class="m" id="d{n}">{html.escape(code)}</pre>')
    parts.append("""<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>
(async () => {
  const out = document.createElement('div'); out.id = 'result';
  if (typeof mermaid === 'undefined') { out.textContent = 'NOLIB'; document.body.prepend(out); return; }
  mermaid.initialize({ startOnLoad: false });
  const res = [];
  for (const el of document.querySelectorAll('pre.m')) {
    try { await mermaid.parse(el.textContent); await mermaid.render('g' + el.id, el.textContent); res.push(el.id + '=ok'); }
    catch (e) { res.push(el.id + '=ERR ' + String(e).replace(/\\s+/g, ' ').slice(0, 160)); }
  }
  out.textContent = res.join('||'); document.body.prepend(out);
})();
</script>""")
    tmp = Path(tempfile.mkdtemp(prefix="check_docs_"))
    page = tmp / "mermaid.html"
    page.write_text("\n".join(parts), encoding="utf-8")
    try:
        proc = subprocess.run(
            [browser, "--headless=new", "--disable-gpu", "--virtual-time-budget=25000",
             f"--user-data-dir={tmp / 'profile'}", "--dump-dom", page.as_uri()],
            capture_output=True, timeout=120)
        dom = proc.stdout.decode("utf-8", errors="replace")
    except Exception as e:
        notes.append(f"ข้ามการวาดผัง Mermaid: เรียกเบราว์เซอร์ไม่สำเร็จ ({e})")
        return
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    m = re.search(r'<div id="result">(.*?)</div>', dom, re.S)
    if not m:
        notes.append("ข้ามการวาดผัง Mermaid: เบราว์เซอร์ไม่ส่งผลกลับ อาจไม่มีอินเทอร์เน็ต")
        return
    result = html.unescape(m.group(1))
    if result == "NOLIB":
        notes.append("ข้ามการวาดผัง Mermaid: โหลดตัววาดผังจากอินเทอร์เน็ตไม่สำเร็จ")
        return
    ok = 0
    received = set()
    for item in result.split("||"):
        key, _, status = item.partition("=")
        if not re.fullmatch(r"d\d+", key) or int(key[1:]) >= len(blocks):
            err("Mermaid", 0, "ผลจากเบราว์เซอร์ไม่ถูกต้องหรือไม่ครบ")
            continue
        n = int(key[1:])
        if n in received:
            err("Mermaid", 0, f"ผลการวาดผัง d{n} ซ้ำ")
            continue
        received.add(n)
        rel, start, _ = blocks[n]
        if status == "ok":
            ok += 1
        else:
            err(rel, start, f"ผัง Mermaid วาดไม่ได้: {status[4:]}")
    for n in set(range(len(blocks))) - received:
        rel, start, _ = blocks[n]
        err(rel, start, "ไม่มีผลตรวจการวาดผังนี้จากเบราว์เซอร์")
    notes.append(f"วาดผัง Mermaid ได้ {ok} จาก {len(blocks)} ผัง")


# ---------- หลัก ----------

def main():
    errors.clear()
    warnings.clear()
    notes.clear()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if len(args) != 1 or flags - {"--render"}:
        print(__doc__)
        return 2
    root = Path(args[0]).resolve()
    if not root.is_dir():
        print(f"ไม่พบโฟลเดอร์: {root}")
        return 2

    check_structure(root)
    roles = check_manifest(root)
    check_numbering(root)

    paths = []
    if (root / "README.md").is_file():
        paths.append(root / "README.md")
    for sub in ("docs", "plans"):
        if (root / sub).is_dir():
            paths.extend(sorted((root / sub).rglob("*.md")))
    docs = []
    by_path = {path: role for role, path in roles.items()}
    for p in paths:
        try:
            p.resolve().relative_to(root)
            d = Doc(p, root)
        except (OSError, ValueError) as e:
            err(p.relative_to(root).as_posix(), 0, f"อ่านเอกสารไม่ได้หรือชี้ออกนอกโครงการ: {e}")
            continue
        d.role = by_path.get(d.rel)
        docs.append(d)

    check_fences(docs)
    check_file_refs(root, docs)
    check_section_refs(root, docs)
    check_readme_lists_files(root, docs)
    check_invented_codes(docs)
    check_leftovers(docs)
    check_empty_sections(docs)
    check_pending_marks(docs)
    check_wide_tables(docs)
    check_decision_summary(docs)
    check_decision_statuses(docs)
    check_duplicate_numbers(docs)
    if "--render" in flags:
        render_mermaid(docs)

    total_lines = sum(len(d.lines) for d in docs)
    total_mermaid = sum(len(d.mermaid) for d in docs)
    print(f"ตรวจ {len(docs)} ไฟล์ {total_lines} บรรทัด ผัง Mermaid {total_mermaid} ผัง")
    for n in notes:
        print(f"  หมายเหตุ: {n}")

    counts = actual_counts(docs)
    if counts:
        print("\nจำนวนที่นับได้จริง (ใช้เทียบกับตัวเลขที่สรุปไว้ในเอกสาร)")
        for c in counts:
            print(f"  {c}")

    def show(title, items):
        print(f"\n{title} ({len(items)})")
        for f, line, msg in sorted(items):
            loc = f"{f}:{line}" if line else f
            print(f"  {loc}  {msg}")

    show("ผิดพลาด", errors)
    show("เตือน", warnings)
    print("\nผล: " + ("ไม่ผ่าน ต้องแก้รายการระดับผิดพลาด" if errors else "ผ่านการตรวจเชิงกลไก ต้องรีวิวเนื้อหาและสิ่งที่ยังไม่ได้ตรวจแยกต่างหาก"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

