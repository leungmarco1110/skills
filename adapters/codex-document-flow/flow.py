#!/usr/bin/env python3
"""Prepare a document task and check byte-bound evidence declarations. No execution."""
import argparse
import hashlib
import json
from pathlib import Path


class Invalid(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise Invalid(message)


def text(value, label):
    require(isinstance(value, str) and bool(value.strip()), label + " must be nonempty text")
    return value


def items(value, label, nonempty=True):
    require(isinstance(value, list) and (bool(value) or not nonempty), label + " must be a list")
    return value


def strings(value, label, nonempty=True):
    result = [text(x, label) for x in items(value, label, nonempty)]
    require(len(set(result)) == len(result), label + " contains duplicates")
    return result


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON key: " + key)
            result[key] = value
        return result
    value = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique)
    require(isinstance(value, dict), "JSON root must be an object")
    return value


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def resolve(value, root):
    path = Path(text(value, "path"))
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def fingerprint(path):
    path = Path(path).resolve()
    require(path.is_file(), "missing regular file: " + str(path))
    data = path.read_bytes()
    require(bool(data.strip()), "empty file: " + str(path))
    return {"path": str(path), "sha256": digest(data)}


def add_input(result, input_id, path, locator, executor_read=True):
    ref = fingerprint(path)
    result.append(dict(ref, id=input_id, locator=text(locator, "locator"),
                       executor_read=executor_read))


def validate_checks(policy):
    checks = items(policy.get("required_checks"), "required_checks")
    seen = set()
    for check in checks:
        require(isinstance(check, dict), "check must be an object")
        name = text(check.get("id"), "check id")
        require(name not in seen, "duplicate check id")
        seen.add(name)
        require(check.get("kind") in ("artifact", "visual"), "unknown check kind")
        strings(check.get("sheets"), "check sheets", nonempty=False)
    return checks


def prepare(intake_path):
    intake_path = Path(intake_path).resolve()
    intake = read_json(intake_path)
    task_id = text(intake.get("task_id"), "task_id")
    require(intake.get("mode") in ("new", "revise"), "mode must be new or revise")
    policy_path = resolve(intake.get("policy"), intake_path.parent)
    policy = read_json(policy_path)
    policy_name = text(policy.get("name"), "policy name")
    policy_version = text(policy.get("version"), "policy version")
    checks = validate_checks(policy)
    inputs = []
    add_input(inputs, "intake", intake_path, "intake file", False)
    add_input(inputs, "policy", policy_path, policy_name, False)
    requests = []
    for index, request in enumerate(items(intake.get("requests"), "requests")):
        require(isinstance(request, dict), "request must be an object")
        path = resolve(request.get("path"), intake_path.parent)
        locator = text(request.get("locator"), "request locator")
        add_input(inputs, "request:" + str(index), path, locator)
        requests.append({"id": "request:" + str(index), "locator": locator,
                         "raw_text": path.read_text(encoding="utf-8")})
    base = intake.get("base")
    require(isinstance(base, dict), "sourced base required")
    expected_role = "template" if intake["mode"] == "new" else "original"
    require(base.get("role") == expected_role, "base role must be " + expected_role)
    add_input(inputs, "base", resolve(base.get("path"), intake_path.parent),
              text(base.get("locator"), "base locator"))
    scope = items(intake.get("preserved_scope"), "preserved_scope")
    for entry in scope:
        require(isinstance(entry, dict), "scope entry must be an object")
        request_id = entry.get("request_id")
        matches = [x for x in requests if x["id"] == request_id]
        quote = text(entry.get("quote"), "preserved scope quote")
        require(len(matches) == 1 and quote in matches[0]["raw_text"],
                "preserved scope must quote a canonical request")
    required_roles = strings(policy.get("required_source_roles", []), "required source roles", False)
    source_roles = set()
    for index, source in enumerate(items(intake.get("sources", []), "sources", False)):
        require(isinstance(source, dict), "source must be an object")
        role = text(source.get("role"), "source role")
        source_roles.add(role)
        add_input(inputs, "source:" + str(index),
                  resolve(source.get("path"), intake_path.parent),
                  role + ": " + text(source.get("locator"), "source locator"))
    require(set(required_roles).issubset(source_roles), "required source roles missing")
    add_input(inputs, "payload", resolve(intake.get("payload"), intake_path.parent),
              text(intake.get("payload_locator"), "payload locator"))
    add_input(inputs, "task_skill", resolve(policy.get("task_skill"), policy_path.parent),
              policy_name + " task skill")
    method_names = set()
    for method in items(policy.get("pack_methods"), "pack_methods"):
        require(isinstance(method, dict), "pack method must be an object")
        name = text(method.get("name"), "method name")
        require(name not in method_names, "duplicate method name")
        method_names.add(name)
        add_input(inputs, "method:" + name,
                  resolve(method.get("path"), policy_path.parent), name)
    # Re-read every reference to catch ordinary changes during preparation.
    for ref in inputs:
        require(fingerprint(ref["path"])["sha256"] == ref["sha256"],
                "input changed during preparation: " + ref["id"])
    bundle = {"schema": 1, "task_id": task_id, "mode": intake["mode"],
              "policy": {"name": policy_name, "version": policy_version},
              "inputs": inputs, "canonical_requests": requests,
              "preserved_scope": scope, "required_checks": checks}
    bundle["binding_sha256"] = digest(canonical(bundle))
    return bundle


def prompt_for(bundle):
    return "\n".join([
        "Document-task preparation only; this text does not dispatch, build, upload, or authorize actions.",
        "Use the current user's authorization and governing instructions. Resolve source conflicts before work.",
        "The JSON below binds canonical request text/locators, existing payload, sourced base, and selected skills.",
        "Read the executor_read inputs explicitly; a prompt mention is not evidence of a read.",
        "Preserve the quoted scope. Follow the named task policy; do not invent universal document checks.",
        "After authorized work, record observed-read evidence, semantic review, and each required check",
        "against the final artifact hash and specified sheets. Incomplete readback remains incomplete.",
        "Review records are declarations, not authenticated identity or automatic proof of correctness.",
        "",
        json.dumps(bundle, indent=2, ensure_ascii=False),
        "",
    ])


def verify_ref(ref, root):
    require(isinstance(ref, dict), "file reference must be an object")
    sha = text(ref.get("sha256"), "sha256")
    require(len(sha) == 64 and all(x in "0123456789abcdef" for x in sha), "invalid sha256")
    actual = fingerprint(resolve(ref.get("path"), root))
    require(actual["sha256"] == sha, "file bytes changed: " + actual["path"])
    return actual


def proof(record, root):
    require(isinstance(record, dict), "evidence record must be an object")
    text(record.get("locator"), "evidence locator")
    return verify_ref(record.get("evidence"), root)


def check(bundle_path, evidence_path=None):
    bundle_path = Path(bundle_path).resolve()
    bundle = read_json(bundle_path)
    binding = bundle.pop("binding_sha256", None)
    issues = []
    try:
        require(bundle.get("schema") == 1, "unsupported bundle schema")
        require(binding == digest(canonical(bundle)), "bundle binding changed")
        # Reconstruct from original intake, rather than trusting editable derived fields.
        inputs = items(bundle.get("inputs"), "inputs")
        require(all(isinstance(ref, dict) for ref in inputs), "input references must be objects")
        intake_ref = next((x for x in inputs if x.get("id") == "intake"), None)
        require(intake_ref is not None, "intake binding missing")
        require(prepare(intake_ref["path"]) == dict(bundle, binding_sha256=binding),
                "bound input bytes or derived structure changed")
        structure_bound = True
    except (Invalid, OSError, ValueError, KeyError, TypeError) as exc:
        structure_bound = False
        issues.append(str(exc))

    result = {"status": "incomplete", "structure_bound": structure_bound,
              "observed_reads": {"complete_declaration": False, "missing": []},
              "readback_complete_declaration": False,
              "semantic_review_claim": False,
              "artifact_verified": {"complete_declaration": False},
              "visual_verified": {"required": False, "complete_declaration": False},
              "delivery_evidence_complete_declaration": False,
              "identity_authenticated": False,
              "semantic_or_visual_truth_verified_by_this_tool": False, "issues": issues}
    if not structure_bound:
        return result
    result["visual_verified"]["required"] = any(
        entry["kind"] == "visual" for entry in bundle["required_checks"])
    required_reads = {x["id"]: x for x in bundle["inputs"] if x["executor_read"]}
    if evidence_path is None:
        result["observed_reads"]["missing"] = sorted(required_reads)
        issues.append("no evidence supplied")
        return result
    evidence_path = Path(evidence_path).resolve()
    root = evidence_path.parent
    try:
        evidence = read_json(evidence_path)
        require(evidence.get("binding_sha256") == binding, "evidence names another binding")
        read_ids = set()
        for entry in items(evidence.get("observed_reads"), "observed_reads", False):
            require(isinstance(entry, dict), "read entry must be an object")
            input_id = text(entry.get("input_id"), "input_id")
            require(input_id in required_reads and input_id not in read_ids, "unknown/duplicate observed read")
            require(entry.get("input_sha256") == required_reads[input_id]["sha256"], "read hash mismatch")
            proof(entry, root)
            read_ids.add(input_id)
        missing = sorted(set(required_reads) - read_ids)
        result["observed_reads"] = {"complete_declaration": not missing, "missing": missing}
        if missing:
            issues.append("missing observed-read declarations")
        readback = evidence.get("readback")
        require(isinstance(readback, dict) and readback.get("status") in ("complete", "incomplete"),
                "readback status required")
        if readback["status"] == "incomplete":
            text(readback.get("reason"), "incomplete readback reason")
            issues.append("readback incomplete")
        else:
            proof(readback, root)
            result["readback_complete_declaration"] = True
        semantic = evidence.get("semantic_review")
        require(isinstance(semantic, dict) and semantic.get("verdict") == "pass",
                "passing semantic review declaration required")
        text(semantic.get("reviewer"), "semantic reviewer declaration")
        text(semantic.get("basis"), "semantic review basis")
        proof(semantic, root)
        artifact = verify_ref(evidence.get("artifact"), root)
        require(semantic.get("artifact_sha256") == artifact["sha256"],
                "semantic review is not bound to final artifact")
        result["semantic_review_claim"] = True
        declarations = items(evidence.get("checks"), "checks")
        by_id = {}
        for entry in declarations:
            require(isinstance(entry, dict), "check declaration must be an object")
            name = text(entry.get("id"), "check id")
            require(name not in by_id, "duplicate check declaration")
            by_id[name] = entry
        required = bundle["required_checks"]
        require(set(by_id) == {x["id"] for x in required}, "required check set mismatch")
        for expected in required:
            entry = by_id[expected["id"]]
            require(entry.get("verdict") == "pass", "check did not pass: " + expected["id"])
            require(entry.get("artifact_sha256") == artifact["sha256"], "check artifact hash mismatch")
            actual_sheets = strings(entry.get("sheets"), "checked sheets", False)
            require(set(expected["sheets"]).issubset(actual_sheets), "required sheet coverage missing")
            text(entry.get("reviewer"), "check reviewer declaration")
            proof(entry, root)
        kinds = {entry["kind"] for entry in required}
        result["artifact_verified"]["complete_declaration"] = "artifact" in kinds
        result["visual_verified"] = {"required": "visual" in kinds,
                                    "complete_declaration": "visual" in kinds}
        if not issues:
            result["delivery_evidence_complete_declaration"] = True
            result["status"] = "evidence_complete_declaration"
    except (Invalid, OSError, ValueError, KeyError, TypeError) as exc:
        issues.append(str(exc))
    return result


def write_new(path, data):
    with Path(path).open("x", encoding="utf-8") as handle:
        handle.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("prepare")
    make.add_argument("intake")
    make.add_argument("bundle")
    make.add_argument("prompt")
    verify = sub.add_parser("check")
    verify.add_argument("bundle")
    verify.add_argument("evidence", nargs="?")
    fp = sub.add_parser("fingerprint")
    fp.add_argument("path")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            require(not Path(args.bundle).exists() and not Path(args.prompt).exists(),
                    "output already exists; use new run paths")
            require(Path(args.bundle).resolve() != Path(args.prompt).resolve(), "output paths must differ")
            bundle = prepare(args.intake)
            write_new(args.bundle, json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")
            write_new(args.prompt, prompt_for(bundle))
            print(json.dumps({"status": "prepared_only", "binding_sha256": bundle["binding_sha256"]}))
            return 0
        if args.command == "fingerprint":
            print(json.dumps(fingerprint(args.path)))
            return 0
        result = check(args.bundle, args.evidence)
        print(json.dumps(result, indent=2))
        return 0 if result["delivery_evidence_complete_declaration"] else 2
    except (Invalid, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
