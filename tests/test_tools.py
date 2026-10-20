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
        number = len(self.manifest["documents"])
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
        result = subprocess.run([sys.executable, str(CHECK), str(self.root)], capture_output=True, text=True)
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
        readme.write_text(readme.read_text() + "\n[Source](00-source.md)\n", encoding="utf-8")
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
        p.write_text(p.read_text().replace("| 1 | Duration | รอตัดสินใจ", "| 1 | Duration | ตัดสินใจแล้ว"), encoding="utf-8")
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

    def test_shorter_fence_cannot_close_longer_fence(self):
        self.document("overview").write_text("# Overview\nContract:\n````json\n{}\n```\n", encoding="utf-8")
        self.run_check(1)


class ReplacementTests(ProjectCase):
    def run_replace(self, edits, *flags):
        edit_path = self.root / "edits.json"
        edit_path.write_text(json.dumps(edits), encoding="utf-8")
        return subprocess.run([sys.executable, str(REPLACE), str(self.root), str(edit_path), *flags], capture_output=True, text=True)

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
        self.assertIn("asset", self.document("overview").read_text())


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
