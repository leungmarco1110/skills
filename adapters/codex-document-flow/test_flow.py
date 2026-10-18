import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).with_name("flow.py")
spec = importlib.util.spec_from_file_location("document_flow", MODULE)
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, body in {
            "request.txt": "Create the synthetic document. Preserve the template layout.",
            "base.bin": "SYNTHETIC TEMPLATE BYTES",
            "payload.json": '{"synthetic": true}',
            "task.md": "Synthetic task instructions.",
            "method.md": "Synthetic selected pack method.",
            "reference.md": "Synthetic selected reference.",
            "source.txt": "SYNTHETIC SOURCE DOCUMENT",
            "final.bin": "SYNTHETIC FINAL ARTIFACT",
            "trace.txt": "SYNTHETIC read observations; no real runtime was observed.",
            "review.txt": "SYNTHETIC semantic and visual review declaration.",
        }.items():
            (self.root / name).write_text(body)
        self.policy = {
            "name": "synthetic-document", "version": "1",
            "task_skill": "task.md", "required_source_roles": ["source_document"],
            "pack_methods": [{"name": "method", "path": "method.md"},
                             {"name": "reference", "path": "reference.md"}],
            "required_checks": [
                {"id": "content", "kind": "artifact", "sheets": ["Main", "Notes"]},
                {"id": "layout", "kind": "visual", "sheets": ["Main", "Notes"]},
            ],
        }
        self.intake = {
            "task_id": "synthetic-001", "mode": "new", "policy": "policy.json",
            "requests": [{"path": "request.txt", "locator": "synthetic://chat/1"}],
            "base": {"role": "template", "path": "base.bin", "locator": "synthetic://template/1"},
            "preserved_scope": [{"request_id": "request:0", "quote": "Preserve the template layout."}],
            "payload": "payload.json", "payload_locator": "synthetic://packet/1",
            "sources": [{"role": "source_document", "path": "source.txt",
                         "locator": "synthetic://source/1"}],
        }
        self.save("policy.json", self.policy)
        self.save("intake.json", self.intake)
        self.bundle = flow.prepare(self.root / "intake.json")
        self.save("bundle.json", self.bundle)
        artifact = flow.fingerprint(self.root / "final.bin")
        trace = {"evidence": flow.fingerprint(self.root / "trace.txt"),
                 "locator": "synthetic://trace/read"}
        review = {"evidence": flow.fingerprint(self.root / "review.txt"),
                  "locator": "synthetic://review/result"}
        self.evidence = {
            "binding_sha256": self.bundle["binding_sha256"],
            "observed_reads": [
                dict(trace, input_id=x["id"], input_sha256=x["sha256"])
                for x in self.bundle["inputs"] if x["executor_read"]
            ],
            "readback": dict(trace, status="complete"),
            "semantic_review": dict(review, verdict="pass", reviewer="synthetic-reviewer",
                                    basis="Synthetic test assertion only.",
                                    artifact_sha256=artifact["sha256"]),
            "artifact": artifact,
            "checks": [
                dict(review, id=c["id"], verdict="pass", reviewer="synthetic-reviewer",
                     artifact_sha256=artifact["sha256"], sheets=c["sheets"])
                for c in self.policy["required_checks"]
            ],
        }
        self.save("evidence.json", self.evidence)

    def save(self, name, data):
        (self.root / name).write_text(json.dumps(data))

    def result(self):
        self.save("evidence.json", self.evidence)
        return flow.check(self.root / "bundle.json", self.root / "evidence.json")

    def test_complete_declarations_are_not_authenticated_truth(self):
        result = self.result()
        self.assertEqual(result["status"], "evidence_complete_declaration")
        self.assertTrue(result["structure_bound"])
        self.assertTrue(result["visual_verified"]["complete_declaration"])
        self.assertFalse(result["identity_authenticated"])
        self.assertFalse(result["semantic_or_visual_truth_verified_by_this_tool"])

    def test_no_evidence_and_prompt_mentions_cannot_prove_reads(self):
        result = flow.check(self.root / "bundle.json")
        self.assertTrue(result["structure_bound"])
        self.assertTrue(result["visual_verified"]["required"])
        self.assertFalse(result["visual_verified"]["complete_declaration"])
        self.assertFalse(result["delivery_evidence_complete_declaration"])
        self.assertTrue(result["observed_reads"]["missing"])
        self.evidence["observed_reads"] = []
        result = self.result()
        self.assertIn("missing observed-read declarations", result["issues"])
        self.assertFalse(result["delivery_evidence_complete_declaration"])

    def test_incomplete_readback_remains_usable_but_not_complete(self):
        self.evidence["readback"] = {"status": "incomplete", "reason": "Synthetic page unavailable"}
        result = self.result()
        self.assertTrue(result["structure_bound"])
        self.assertTrue(result["semantic_review_claim"])
        self.assertFalse(result["delivery_evidence_complete_declaration"])

    def test_every_bound_input_mutation_invalidates_structure(self):
        for ref in self.bundle["inputs"]:
            with self.subTest(input_id=ref["id"]):
                path = Path(ref["path"])
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b"\nSYNTHETIC MUTATION")
                    self.assertFalse(self.result()["structure_bound"])
                finally:
                    path.write_bytes(original)

    def test_final_artifact_and_evidence_mutations_invalidate_completion(self):
        for name in ("final.bin", "trace.txt", "review.txt"):
            with self.subTest(file=name):
                path = self.root / name
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b" CHANGED")
                    result = self.result()
                    self.assertTrue(result["structure_bound"])
                    self.assertFalse(result["delivery_evidence_complete_declaration"])
                finally:
                    path.write_bytes(original)

    def test_missing_empty_or_unhashed_evidence_cannot_pass(self):
        original = copy.deepcopy(self.evidence)
        for change in ("missing", "empty", "no_hash"):
            with self.subTest(change=change):
                self.evidence = copy.deepcopy(original)
                proof = self.evidence["checks"][0]["evidence"]
                if change == "missing":
                    proof["path"] = str(self.root / "absent")
                elif change == "empty":
                    (self.root / "empty").write_text(" \n")
                    proof["path"] = str(self.root / "empty")
                else:
                    proof.pop("sha256")
                self.assertFalse(self.result()["delivery_evidence_complete_declaration"])

    def test_required_sheet_coverage_and_check_set_are_enforced(self):
        original = copy.deepcopy(self.evidence)
        self.evidence["checks"][1]["sheets"] = ["Main"]
        self.assertFalse(self.result()["delivery_evidence_complete_declaration"])
        self.evidence = copy.deepcopy(original)
        self.evidence["checks"].pop()
        self.assertFalse(self.result()["delivery_evidence_complete_declaration"])

    def test_semantic_and_check_claims_bind_the_final_hash(self):
        self.evidence["semantic_review"]["artifact_sha256"] = "0" * 64
        self.assertFalse(self.result()["delivery_evidence_complete_declaration"])
        self.evidence["semantic_review"]["artifact_sha256"] = self.evidence["artifact"]["sha256"]
        self.evidence["checks"][0]["artifact_sha256"] = "0" * 64
        self.assertFalse(self.result()["delivery_evidence_complete_declaration"])

    def test_revise_requires_original_and_sourced_preservation_quote(self):
        self.intake["mode"] = "revise"
        self.save("intake.json", self.intake)
        with self.assertRaises(flow.Invalid):
            flow.prepare(self.root / "intake.json")
        self.intake["base"]["role"] = "original"
        self.save("intake.json", self.intake)
        self.assertEqual(flow.prepare(self.root / "intake.json")["mode"], "revise")
        self.intake["preserved_scope"][0]["quote"] = "An invented scope."
        self.save("intake.json", self.intake)
        with self.assertRaises(flow.Invalid):
            flow.prepare(self.root / "intake.json")

    def test_required_source_roles_cannot_be_omitted(self):
        self.intake["sources"] = []
        self.save("intake.json", self.intake)
        with self.assertRaises(flow.Invalid):
            flow.prepare(self.root / "intake.json")

    def test_no_policy_checks_is_not_a_vacuous_pass(self):
        self.policy["required_checks"] = []
        self.save("policy.json", self.policy)
        with self.assertRaises(flow.Invalid):
            flow.prepare(self.root / "intake.json")

    def test_policy_can_require_text_checks_without_excel_or_visual_checks(self):
        self.policy["required_checks"] = [{"id": "content", "kind": "artifact", "sheets": []}]
        self.save("policy.json", self.policy)
        self.bundle = flow.prepare(self.root / "intake.json")
        self.save("bundle.json", self.bundle)
        self.evidence["binding_sha256"] = self.bundle["binding_sha256"]
        self.evidence["checks"] = self.evidence["checks"][:1]
        self.evidence["checks"][0]["sheets"] = []
        result = self.result()
        self.assertTrue(result["delivery_evidence_complete_declaration"])
        self.assertFalse(result["visual_verified"]["required"])

    def test_bundle_derivative_tampering_even_with_rehashed_binding_is_rejected(self):
        edited = copy.deepcopy(self.bundle)
        edited["canonical_requests"][0]["raw_text"] = "Different synthetic request."
        edited.pop("binding_sha256")
        edited["binding_sha256"] = flow.digest(flow.canonical(edited))
        self.save("bundle.json", edited)
        self.assertFalse(self.result()["structure_bound"])

    def test_cli_malformed_input_reference_returns_structured_error(self):
        for bad in (None, 1, "not an object"):
            with self.subTest(value=bad):
                edited = copy.deepcopy(self.bundle)
                edited["inputs"] = [bad]
                edited.pop("binding_sha256")
                edited["binding_sha256"] = flow.digest(flow.canonical(edited))
                self.save("malformed.json", edited)
                run = subprocess.run([sys.executable, str(MODULE), "check",
                                      str(self.root / "malformed.json")],
                                     capture_output=True, text=True)
                self.assertEqual(run.returncode, 2)
                result = json.loads(run.stdout)
                self.assertFalse(result["structure_bound"])
                self.assertIn("input references must be objects", result["issues"])
                self.assertEqual(run.stderr, "")

    def test_cli_prepare_has_no_execution_and_refuses_existing_outputs(self):
        prompt = self.root / "prompt.txt"
        out = self.root / "fresh.json"
        command = [sys.executable, str(MODULE), "prepare",
                   str(self.root / "intake.json"), str(out), str(prompt)]
        run = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(json.loads(run.stdout)["status"], "prepared_only")
        self.assertIn("Create the synthetic document.", prompt.read_text())
        self.assertIn("method:reference", prompt.read_text())
        before = out.read_bytes()
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)
        self.assertEqual(out.read_bytes(), before)
        self.assertEqual((self.root / "final.bin").read_text(), "SYNTHETIC FINAL ARTIFACT")

    def test_cli_missing_evidence_fails_and_duplicate_json_keys_rejected(self):
        run = subprocess.run([sys.executable, str(MODULE), "check", str(self.root / "bundle.json")],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(json.loads(run.stdout)["status"], "incomplete")
        (self.root / "duplicate.json").write_text('{"x": 1, "x": 2}')
        with self.assertRaises(flow.Invalid):
            flow.read_json(self.root / "duplicate.json")


if __name__ == "__main__":
    unittest.main()
