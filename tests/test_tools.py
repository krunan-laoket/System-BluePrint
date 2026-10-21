"""Behavioral regressions for delivery checks and scoped document edits (stdlib only)."""

import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
CHECK = SKILL_ROOT / "scripts" / "check_docs.py"
REPLACE = SKILL_ROOT / "scripts" / "replace_checked.py"
EXAMPLE = SKILL_ROOT / "examples" / "lab-equipment-loan"


def run_tool(script, *args):
    # Decode output as UTF-8 explicitly: a Thai Windows console defaults to cp874, macOS to UTF-8.
    return subprocess.run([sys.executable, str(script), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


class ProjectCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blueprint-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "docs").mkdir()
        (self.root / "plans").mkdir()
        self.manifest = {
            "schema_version": 1,
            "mode": "business",
            "documents": {},
            "plans": ["plans/next-step.md"],
            "omissions": {},
        }
        for role, stem, text in (
            ("source", "source", "# Sources\nOwner requested equipment loans in chat.\n"),
            ("overview", "overview", "# Overview\n## 1. Goal\nTrack equipment loans.\n"),
            ("features", "features", "# Features\n## 1. Borrower\n- 1.1 Request an equipment loan.\n"),
            ("flows", "flows", "# Flows\n## Flow 1: Request\nBorrower requests; manager approves.\n"),
            ("decisions", "decisions", "# Decisions\nNo unresolved proposals in this fixture.\n"),
            ("development_plan", "development-plan", "# Plan\n## 1. Phase one\nBuild loan requests.\n"),
            ("acceptance", "acceptance", "# Acceptance\nBorrower can request a loan; manager can approve.\n"),
        ):
            self.add_document(role, stem, text)
        (self.root / "plans/next-step.md").write_text("# Next step\nReview scope with the owner.\n", encoding="utf-8")
        self.save()

    def add_document(self, role, stem, text):
        number = len(set(self.manifest["documents"].values()))
        path = "docs/{:02d}-{}.md".format(number, stem)
        self.manifest["documents"][role] = path
        (self.root / path).write_text(text, encoding="utf-8")

    def document(self, role):
        return self.root / self.manifest["documents"][role]

    def save(self):
        (self.root / "blueprint.json").write_text(json.dumps(self.manifest, ensure_ascii=False), encoding="utf-8")
        paths = list(self.manifest["documents"].values()) + self.manifest["plans"]
        (self.root / "README.md").write_text("# Project\nblueprint.json\n" + "".join("- " + p + "\n" for p in paths), encoding="utf-8")

    def run_check(self, expected):
        result = run_tool(CHECK, self.root)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return result.stdout

    def upgrade(self, api=True):
        self.manifest["mode"] = "full"
        self.add_document("architecture", "architecture", "# Architecture\nApplication handles requests.\n")
        self.add_document("data_model", "data-model", "# Data model\nLoan includes borrower and equipment.\n")
        if api:
            self.add_document("api", "api-spec", "# API\nCreate and inspect equipment loans.\n")
        else:
            self.manifest["omissions"]["api"] = "Offline local application with no API."
        self.save()


class CheckDocsTests(ProjectCase):
    def test_business_requires_no_technical_files(self):
        self.run_check(0)

    def test_upgrade_appends_technical_files_and_keeps_old_names(self):
        original = dict(self.manifest["documents"])
        before = {role: self.document(role).read_bytes() for role in original}
        self.upgrade()
        self.assertEqual(self.manifest["documents"]["api"], "docs/09-api-spec.md")
        for role, name in original.items():
            self.assertEqual(self.manifest["documents"][role], name)
            self.assertEqual(self.document(role).read_bytes(), before[role])
        self.document("features").write_text("# Features\nSee {} ข้อ 1.\n".format(self.manifest["documents"]["overview"]), encoding="utf-8")
        self.run_check(0)

    def test_full_can_explicitly_omit_api(self):
        self.upgrade(api=False)
        self.run_check(0)

    def test_full_cannot_silently_omit_api(self):
        self.upgrade(api=False)
        self.manifest["omissions"] = {}
        self.save()
        self.run_check(1)

    def test_business_rejects_technical_roles(self):
        self.add_document("api", "api-spec", "# API\nCreate loans.\n")
        self.save()
        self.run_check(1)

    def test_missing_manifest_fails(self):
        (self.root / "blueprint.json").unlink()
        self.run_check(1)

    def test_missing_core_role_fails_even_if_readme_matches(self):
        path = self.manifest["documents"].pop("acceptance")
        (self.root / path).unlink()
        self.save()
        self.run_check(1)

    def test_missing_registered_file_fails(self):
        self.document("overview").unlink()
        self.run_check(1)

    def test_empty_and_heading_only_documents_fail(self):
        for content in ("", "  \n", "# Only a title\n## Heading\n"):
            with self.subTest(content=content):
                self.document("overview").write_text(content, encoding="utf-8")
                self.run_check(1)

    def test_unregistered_markdown_fails(self):
        (self.root / "plans/forgotten.md").write_text("# Forgotten\nNeeds review.\n", encoding="utf-8")
        self.run_check(1)

    def test_duplicate_registered_paths_fail(self):
        self.manifest["documents"]["acceptance"] = self.manifest["documents"]["overview"]
        self.save()
        self.run_check(1)

    def test_manifest_path_escape_fails(self):
        self.manifest["documents"]["overview"] = "docs/../../outside.md"
        self.save()
        self.run_check(1)

    def test_malformed_manifest_reports_error_without_traceback(self):
        for value in ([], {"mode": [], "documents": []}, {"schema_version": True, "mode": "business"}):
            with self.subTest(value=value):
                (self.root / "blueprint.json").write_text(json.dumps(value), encoding="utf-8")
                self.run_check(1)

    def test_valid_relative_link_and_external_md_link_pass(self):
        overview = self.document("overview")
        overview.write_text("# Overview\n[Original-name.md](02-features.md)\n[Reference](https://example.com/reference.md)\n", encoding="utf-8")
        self.run_check(0)

    def test_readme_link_does_not_fall_back_to_docs(self):
        readme = self.root / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8") +"\n[Source](00-source.md)\n", encoding="utf-8")
        self.run_check(1)

    def test_malformed_link_is_reported_without_crash(self):
        self.document("overview").write_text("# Overview\n[Reference](http://[invalid)\n", encoding="utf-8")
        self.run_check(1)

    def test_missing_section_flow_and_decision_fail(self):
        for content in ("02-features.md ข้อ 99", "Flow 99", "ประเด็นที่ 99"):
            with self.subTest(content=content):
                self.document("overview").write_text("# Overview\n" + content + "\n", encoding="utf-8")
                self.run_check(1)

    def test_source_quotes_do_not_create_internal_flow_references(self):
        self.document("source").write_text("# Original source\nFlow 99 and ประเด็นที่ 99 belong to the source.\n", encoding="utf-8")
        self.run_check(0)

    def test_role_names_are_not_inferred_from_filenames(self):
        for role, stem in (("source", "input"), ("flows", "journeys"), ("decisions", "choices")):
            previous = self.document(role)
            target = previous.with_name(previous.name[:3] + stem + ".md")
            previous.rename(target)
            self.manifest["documents"][role] = target.relative_to(self.root).as_posix()
        self.document("overview").write_text("# Overview\nSee Flow 1.\n", encoding="utf-8")
        self.save()
        self.run_check(0)

    def decision(self, status, evidence="", details=None):
        if details is None:
            details = (
                "**ที่มา:** Source requirement did not specify loan duration.\n"
                "**ทางเลือกและผลกระทบ**\n"
                "- **ทางเลือก ก:** 7 days\n"
                "- **ทางเลือก ข:** 14 days\n"
                "**ค่าที่ใช้ในเอกสาร / คำแนะนำของทีม:** 7 days\n"
                "**ผลกระทบเมื่อคำตอบเปลี่ยน:** 03-flows.md Flow 1\n"
            )
        self.document("decisions").write_text(
            "# Decisions\n## Summary\n| # | Topic | Status |\n|---|---|---|\n"
            "| 1 | Duration | " + status + " |\n## 1. Duration\n**สถานะ:** " + status + "\n" + details + evidence,
            encoding="utf-8")

    def test_pending_decision_is_valid(self):
        self.decision("ใช้ค่าที่เสนอไปก่อน รอยืนยันกับลูกค้า")
        self.run_check(0)

    def test_decision_missing_required_details_fails(self):
        self.decision("ใช้ค่าที่เสนอไปก่อน รอยืนยันกับลูกค้า", details="**ที่มา:** Chat.\n")
        self.run_check(1)

    def test_readme_code_block_with_wrong_file_or_folder_fails(self):
        readme = self.root / "README.md"
        base = readme.read_text(encoding="utf-8")
        for bad in ("docs/next-step.md", "docs/99-ghost.md"):
            with self.subTest(bad=bad):
                readme.write_text(base + f"\n```text\n{bad}\n```\n", encoding="utf-8")
                self.run_check(1)

    def test_confirmed_decision_needs_evidence(self):
        self.decision("ตัดสินใจแล้ว")
        self.run_check(1)

    def test_confirmed_decision_with_evidence_passes(self):
        self.decision("ตัดสินใจแล้ว", "**ผู้ยืนยัน:** Project owner\n**วันที่ยืนยัน:** 2026-10-20\n**หลักฐานยืนยัน:** Owner explicitly accepted duration proposal in chat.\n")
        self.run_check(0)

    def test_unsupported_status_fails(self):
        self.decision("CONFIRMED_BY_AI")
        self.run_check(1)

    def test_summary_status_mismatch_fails(self):
        self.decision("รอตัดสินใจ")
        p = self.document("decisions")
        p.write_text(p.read_text(encoding="utf-8").replace("| 1 | Duration | รอตัดสินใจ", "| 1 | Duration | ตัดสินใจแล้ว"), encoding="utf-8")
        self.run_check(1)

    def test_summary_cannot_list_decision_without_details(self):
        self.document("decisions").write_text(
            "# Decisions\n| # | Topic | Status |\n|---|---|---|\n| 1 | Duration | รอตัดสินใจ |\n",
            encoding="utf-8")
        self.run_check(1)

    def test_unexpanded_role_in_inline_code_fails(self):
        self.document("overview").write_text("# Overview\nSee `{{architecture}}`.\n", encoding="utf-8")
        self.run_check(1)

    def test_unexpanded_role_inside_code_block_fails(self):
        self.document("overview").write_text("# Overview\n```text\n{{architecture}}\n```\n", encoding="utf-8")
        self.run_check(1)

    def test_placeholders_quoted_in_source_are_preserved(self):
        self.document("source").write_text("# Original form\nUse {{customer_name}} in the source template.\n", encoding="utf-8")
        self.run_check(0)

    def test_duplicate_heading_numbers_fail(self):
        self.document("overview").write_text("# Overview\n## 1. Goal\nFirst.\n## 1. Goal\nSecond.\n", encoding="utf-8")
        self.run_check(1)

    def test_nested_code_fences_use_opening_marker_length(self):
        self.document("overview").write_text("# Overview\nExample contract:\n````markdown\n```json\n{}\n```\n````\n", encoding="utf-8")
        self.run_check(0)

    def test_windows_bom_and_crlf_files_pass(self):
        manifest = self.root / "blueprint.json"
        manifest.write_bytes(b"\xef\xbb\xbf" + manifest.read_bytes())
        self.document("overview").write_bytes("﻿# Overview\r\n## 1. Goal\r\nTrack loans.\r\n".encode("utf-8"))
        self.document("features").write_text("# Features\nSee 01-overview.md ข้อ 1.\n", encoding="utf-8")
        self.run_check(0)

    def overview(self, body):
        self.document("overview").write_text("# Overview\n## 1. Goal\n" + body + "\n", encoding="utf-8")

    def test_thai_glued_to_file_name_is_not_part_of_it(self):
        # Python's \w matches Thai letters; Thai text is written without spaces.
        self.overview("รายละเอียดอยู่ใน02-features.md ข้อ 1.1 และดูใน03-flows.md")
        self.run_check(0)

    def test_thai_glued_missing_references_are_still_errors(self):
        for content in ("ดู 99-ghost.mdและไฟล์อื่น", "ทำตามFlow 99", "ดูใน02-features.md ข้อ 9.9"):
            with self.subTest(content=content):
                self.overview(content)
                self.run_check(1)

    def test_every_number_in_a_reference_list_is_checked(self):
        self.decision("รอตัดสินใจ")
        for content in ("ดู Flow 1, 99", "ดู Flow 1 และ Flow 99", "ประเด็นที่ 1, 99", "ประเด็นที่ 1 และ 99",
                        "02-features.md ข้อ 1 และข้อ 9", "02-features.md หัวข้อ 9.9",
                        "[02-features.md](02-features.md) ข้อ 9.9"):
            with self.subTest(content=content):
                self.overview(content)
                self.run_check(1)
        self.overview("ดู Flow 1 และประเด็นที่ 1 และ 02-features.md ข้อ 1 และข้อ 1.1")
        self.run_check(0)

    def test_mixed_reference_kinds_in_one_list_are_not_confused(self):
        self.decision("รอตัดสินใจ")
        self.overview("ดู Flow 1, ประเด็นที่ 1 และ 02-features.md ข้อ 1.1, Flow 1")
        self.assertIn("ผิดพลาด (0)", self.run_check(0))

    def test_pending_mark_pointing_to_decided_issue_warns(self):
        self.decision("ตัดสินใจแล้ว", "**ผู้ยืนยัน:** Owner\n**วันที่ยืนยัน:** 21 ตุลาคม 2569\n**หลักฐานยืนยัน:** Chat message.\n")
        self.overview("ยืม 7 วัน (รอยืนยัน) ตามประเด็นที่ 1")
        self.assertIn("ซึ่งตัดสินใจแล้ว", self.run_check(0))

    def test_screen_total_must_match_rows(self):
        self.add_document("screens", "screens",
                          "# Screens\n## 1. Borrower\n| # | Screen | Priority |\n|---|---|---|\n"
                          "| 1 | Search | must |\n| 2 | My loans | must |\n"
                          "## 2. Summary\n| Side | Total |\n|---|---|\n| รวม | 3 |\n")
        self.save()
        self.assertIn("หน้าจอรวม 3", self.run_check(0))

    def test_readme_mode_must_match_manifest(self):
        readme = self.root / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "| โหมดเอกสาร | โหมดครบวงจร |\n", encoding="utf-8")
        self.assertIn("README ระบุโหมดครบวงจร", self.run_check(0))

    def test_api_path_in_business_mode_warns(self):
        self.overview("ส่งคำร้องผ่าน `POST /api/v1/complaints`")
        self.assertIn("โหมด business ไม่ควรมี API path", self.run_check(0))

    def test_section_level_pending_mark_counts_as_mark(self):
        self.document("features").write_text(
            "# Features\n## 1. Borrower (ทั้งหมวดรอยืนยัน)\n- 1.1 Request.\n", encoding="utf-8")
        self.assertIn("ไม่ได้ชี้ไปที่ไฟล์ประเด็นตัดสินใจ", self.run_check(0))

    def test_bold_or_dotted_list_numbers_are_referable(self):
        self.document("features").write_text(
            "# Features\n## 1. Borrower\n- **1.2** Cancel.\n- 1.3. Return.\n", encoding="utf-8")
        self.overview("02-features.md ข้อ 1.2 และข้อ 1.3")
        self.run_check(0)

    def test_reference_case_must_match_real_file(self):
        self.overview("ดู 02-FEATURES.md")
        self.run_check(1)

    def test_numbered_option_table_and_extra_summary_column_are_allowed(self):
        self.decision("รอตัดสินใจ", details=(
            "**ที่มา:** Source did not specify.\n"
            "**ทางเลือกและผลกระทบ**\n"
            "| # | ทางเลือก | ระยะเวลา |\n|---|---|---|\n| 1 | 7 days | short |\n| 2 | 14 days | long |\n"
            "**ค่าที่ใช้ในเอกสาร / คำแนะนำของทีม:** 7 days\n"
            "**ผลกระทบเมื่อคำตอบเปลี่ยน:** Flow 1\n"))
        p = self.document("decisions")
        p.write_text(p.read_text(encoding="utf-8")
                     .replace("| # | Topic | Status |\n|---|---|---|", "| # | Topic | ขวางการพัฒนา | สถานะ |\n|---|---|---|---|")
                     .replace("| 1 | Duration | รอตัดสินใจ |", "| 1 | Duration | ใช่ | รอตัดสินใจ |"),
                     encoding="utf-8")
        self.run_check(0)

    def test_non_ascii_digit_in_summary_does_not_crash(self):
        self.document("decisions").write_text(
            "# Decisions\n| # | Topic | Status |\n|---|---|---|\n| ① | Duration | รอตัดสินใจ |\n", encoding="utf-8")
        self.run_check(0)

    def test_legitimate_angle_brackets_and_front_matter_do_not_warn(self):
        self.overview("ดู <https://www.pdpc.or.th> หรือ <dpo@example.com>\n<!-- note -->\n"
                      "ค่าที่ยอมรับ < 3 วินาที และ > 99%\n"
                      "```mermaid\n---\ntitle: Loan\n---\nflowchart TD\nA-->B\n```")
        self.assertIn("เตือน (0)", self.run_check(0))

    def test_unexpanded_role_with_digits_fails(self):
        self.overview("ดู {{integration_line2}}")
        self.run_check(1)

    def test_shorter_fence_cannot_close_longer_fence(self):
        self.document("overview").write_text("# Overview\nContract:\n````json\n{}\n```\n", encoding="utf-8")
        self.run_check(1)


class CompactManifestTests(ProjectCase):
    def compact(self):
        source = self.document("source").read_text(encoding="utf-8")
        # Rebuild only this test's temporary fixture, with unique numbering per shared file.
        for path in (self.root / "docs").glob("*.md"):
            path.unlink()
        self.manifest["document_style"] = "compact"
        self.manifest["documents"] = {
            "source": "docs/00-source.md",
            "overview": "docs/01-scope.md",
            "features": "docs/01-scope.md",
            "flows": "docs/02-flows.md",
            "decisions": "docs/03-decisions.md",
            "acceptance": "docs/04-delivery.md",
            "development_plan": "docs/04-delivery.md",
        }
        for role, content in (
            ("source", source),
            ("overview", "# Scope\n## 1. Goal\nTrack equipment loans.\n## 2. Features\n- 2.1 Request a loan through Flow 1.\n"),
            ("flows", "# Flows\n## Flow 1: Request\nBorrower requests; manager approves. See 01-scope.md ข้อ 2.1.\n"),
            ("decisions", "# Decisions\nNo unresolved proposals in this fixture.\n"),
            ("acceptance", "# Delivery\n## 1. Acceptance\nBorrower requests a loan.\n## 2. Plan\nBuild and review equipment loans.\n"),
        ):
            self.document(role).write_text(content, encoding="utf-8")
        self.save()

    def test_compact_shares_only_approved_roles_and_checks_flow_references(self):
        self.compact()
        self.assertIn("เตือน (0)", self.run_check(0))
        self.assertEqual(len(list((self.root / "docs").glob("*.md"))), 5)
        self.document("overview").write_text("# Scope\nSee Flow 99.\n", encoding="utf-8")
        self.run_check(1)

    def test_standard_still_rejects_compact_shared_paths(self):
        self.compact()
        del self.manifest["document_style"]
        self.save()
        self.run_check(1)

    def test_compact_cannot_hide_source_or_decisions_in_another_role(self):
        self.compact()
        for role in ("source", "decisions", "flows", "screens"):
            with self.subTest(role=role):
                original = self.manifest["documents"].get(role)
                self.manifest["documents"][role] = self.manifest["documents"]["overview"]
                self.save()
                self.assertIn("path ในทะเบียนซ้ำ", self.run_check(1))
                if original is None:
                    del self.manifest["documents"][role]
                else:
                    self.manifest["documents"][role] = original

    def test_compact_still_requires_every_core_role(self):
        self.compact()
        del self.manifest["documents"]["features"]
        self.save()
        self.assertIn("features", self.run_check(1))

    def test_invalid_document_style_is_reported_without_traceback(self):
        for style in ("tiny", None, [], True):
            with self.subTest(style=style):
                self.manifest["document_style"] = style
                self.save()
                self.assertIn("document_style", self.run_check(1))

    def test_full_upgrade_preserves_compact_files_and_references(self):
        self.compact()
        before = dict(self.manifest["documents"])
        self.upgrade()
        self.run_check(0)
        for role, path in before.items():
            self.assertEqual(self.manifest["documents"][role], path)
        self.assertEqual(self.manifest["documents"]["architecture"], "docs/05-architecture.md")

    def test_compact_full_still_requires_api_or_explicit_omission(self):
        self.compact()
        self.upgrade(api=False)
        self.run_check(0)
        del self.manifest["omissions"]["api"]
        self.save()
        self.run_check(1)

    def test_bulk_replacement_preserves_compact_source_and_changes_shared_file_once(self):
        self.compact()
        source_before = self.document("source").read_bytes()
        edits = self.root / "edits.json"
        edits.write_text(json.dumps([{"file": "*", "find": "equipment", "replace": "asset", "count": 2}]), encoding="utf-8")
        result = run_tool(REPLACE, self.root, edits)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.document("source").read_bytes(), source_before)
        self.assertIn("asset", self.document("overview").read_text(encoding="utf-8"))
        self.assertIn("asset", self.document("acceptance").read_text(encoding="utf-8"))


class ReplacementTests(ProjectCase):
    def run_replace(self, edits, *flags):
        edit_path = self.root / "edits.json"
        edit_path.write_text(json.dumps(edits), encoding="utf-8")
        return run_tool(REPLACE, self.root, edit_path, *flags)

    def snapshot(self):
        return {p: p.read_bytes() for p in self.root.rglob("*.md")}

    def test_write_on_current_python_and_preserve_source(self):
        before = self.snapshot()
        result = self.run_replace([{"file": "*", "find": "equipment", "replace": "asset"}])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for p, content in before.items():
            expected = content if p == self.document("source") else content.replace(b"equipment", b"asset")
            self.assertEqual(p.read_bytes(), expected)

    def test_dry_run_preserves_every_file(self):
        before = self.snapshot()
        result = self.run_replace([{"file": "*", "find": "equipment", "replace": "asset"}], "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_later_count_mismatch_prevents_all_writes(self):
        before = self.snapshot()
        result = self.run_replace([
            {"file": "*", "find": "equipment", "replace": "asset"},
            {"file": "README.md", "find": "absent-text", "replace": "x", "count": 1},
        ])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_edit_types_do_not_crash_or_write(self):
        before = self.snapshot()
        for edit in (None, {"file": "*", "find": 1, "replace": "x"}, {"file": "*", "find": "equipment", "replace": "asset", "count": True}):
            with self.subTest(edit=edit):
                result = self.run_replace([edit])
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(self.snapshot(), before)

    def test_source_protection_uses_role_even_when_number_changes(self):
        source = self.document("source")
        source_data = source.read_bytes()
        overview = self.document("overview")
        source.write_bytes(overview.read_bytes())
        overview.write_bytes(source_data)
        self.manifest["documents"]["source"], self.manifest["documents"]["overview"] = (
            self.manifest["documents"]["overview"], self.manifest["documents"]["source"])
        self.save()
        result = self.run_replace([{"file": "*", "find": "equipment", "replace": "asset"}])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.document("source").read_bytes(), source_data)
        self.assertIn("asset", self.document("overview").read_text(encoding="utf-8"))

    def test_line_endings_are_preserved_and_multiline_find_matches_crlf(self):
        crlf = self.document("overview")
        lf = self.document("features")
        crlf.write_bytes(b"# Overview\r\n## 1. Goal\r\nTrack equipment loans.\r\n")
        lf.write_bytes(b"# Features\n## 1. Borrower\n- 1.1 Request an equipment loan.\n")
        result = self.run_replace([{"file": "*", "find": "equipment", "replace": "asset"},
                                   {"file": crlf.relative_to(self.root).as_posix(),
                                    "find": "Goal\nTrack", "replace": "Purpose\nTrack"}])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(crlf.read_bytes(), b"# Overview\r\n## 1. Purpose\r\nTrack asset loans.\r\n")
        self.assertEqual(lf.read_bytes(), b"# Features\n## 1. Borrower\n- 1.1 Request an asset loan.\n")

    def test_wildcard_and_direct_edits_to_same_file_both_apply(self):
        overview = self.document("overview")
        result = self.run_replace([
            {"file": "*", "find": "equipment", "replace": "asset"},
            {"file": "./" + overview.relative_to(self.root).as_posix(), "find": "Goal", "replace": "Purpose"},
        ])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = overview.read_text(encoding="utf-8")
        self.assertIn("asset", text)
        self.assertIn("Purpose", text)

    def test_edit_file_with_bom_is_read(self):
        edit_path = self.root / "edits.json"
        edit_path.write_bytes(b"\xef\xbb\xbf" + json.dumps([{"file": "*", "find": "equipment", "replace": "asset"}]).encode())
        result = run_tool(REPLACE, self.root, edit_path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_one_stray_crlf_does_not_convert_whole_file(self):
        overview = self.document("overview")
        overview.write_bytes(b"# Overview\r\n## 1. Goal\nTrack equipment loans.\nMore.\n")
        result = self.run_replace([{"file": "*", "find": "equipment", "replace": "asset"}])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(overview.read_bytes(), b"# Overview\n## 1. Goal\nTrack asset loans.\nMore.\n")

    def test_wrong_case_target_never_loses_an_edit(self):
        # Windows resolve() folds case to the real name; macOS does not, so the tool must reject the
        # mismatched name there. Either outcome is fine; silently dropping one of the two edits is not.
        before = self.snapshot()
        overview = self.document("overview")
        wrong = overview.relative_to(self.root).as_posix().replace("overview", "OVERVIEW")
        result = self.run_replace([{"file": "*", "find": "equipment", "replace": "asset"},
                                   {"file": wrong, "find": "Goal", "replace": "Purpose"}])
        if result.returncode == 0:
            text = overview.read_text(encoding="utf-8")
            self.assertIn("asset", text)
            self.assertIn("Purpose", text)
        else:
            self.assertEqual(self.snapshot(), before)

    def test_manifest_with_utf8_bom_is_read(self):
        manifest = self.root / "blueprint.json"
        manifest.write_bytes(b"\xef\xbb\xbf" + manifest.read_bytes())
        result = self.run_replace([{"file": "*", "find": "equipment", "replace": "asset"}])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("equipment", self.document("source").read_text(encoding="utf-8"))


class ExampleTests(unittest.TestCase):
    def test_bundled_example_passes_without_errors_or_warnings(self):
        # The example is what the skill tells the AI to imitate, so it must satisfy every rule it teaches.
        result = run_tool(CHECK, EXAMPLE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("เตือน (0)", result.stdout, result.stdout)


class RenderTests(unittest.TestCase):
    def test_partial_browser_results_fail_instead_of_succeeding(self):
        module = runpy.run_path(str(CHECK))
        with tempfile.TemporaryDirectory(prefix="blueprint-render-test-") as temp:
            root = Path(temp)
            p = root / "flows.md"
            p.write_text("# Flows\n```mermaid\nflowchart TD\nA-->B\n```\n```mermaid\nflowchart TD\nC-->D\n```\n", encoding="utf-8")
            doc = module["Doc"](p, root)
            globals_ = module["render_mermaid"].__globals__
            result = subprocess.CompletedProcess([], 0, b'<div id="result">d0=ok</div>', b"")
            with patch.dict(globals_, {"find_browser": lambda: "/fake/browser"}), patch.object(globals_["subprocess"], "run", return_value=result):
                module["render_mermaid"]([doc])
            self.assertTrue(module["errors"], "A missing result for the second diagram must be an error")


if __name__ == "__main__":
    unittest.main()
