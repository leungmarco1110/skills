# Codex document-flow adapter (local preparation)

A small Python 3 standard-library helper for binding a conversational document
request to its task policy, existing packet, selected skills, sources and final
review declarations. It does **not** dispatch a task, call a model, run validators,
build documents, upload files, install skills, or change runtime hooks.

This is an optional adapter alongside the pack. It does not replace task-specific
builders, business rules, authorization, or reviewers. No live sender integration
or document-quality improvement has been established by its unit tests.

## Prepare

Create a private intake JSON. Paths are relative to that intake file; policy paths
are relative to the policy file. Each request file contains the original text,
not a rewritten summary. The following names/content are synthetic:

~~~json
{
  "task_id": "synthetic-001",
  "mode": "new",
  "policy": "policy.json",
  "requests": [{"path": "request.txt", "locator": "synthetic://chat/1"}],
  "base": {"role": "template", "path": "template.bin", "locator": "synthetic://template/1"},
  "preserved_scope": [{"request_id": "request:0", "quote": "Preserve the template layout."}],
  "payload": "packet.json",
  "payload_locator": "synthetic://packet/1",
  "sources": [{"role": "source_document", "path": "source.txt", "locator": "synthetic://source/1"}]
}
~~~

For a revision use mode "revise" and base role "original", pointing to the exact
existing artifact. A new document requires a sourced template. Preservation quotes
must occur literally in a canonical request. Locators and roles are declarations:
the helper cannot establish who spoke, which commercial version is approved, or
whether the quote exhaustively captures the intended scope.

A named task policy selects local instructions and the actual check requirements:

~~~json
{
  "name": "synthetic-document",
  "version": "1",
  "task_skill": "task.md",
  "pack_methods": [
    {"name": "task-contract", "path": "selected-method.md"},
    {"name": "method-reference", "path": "selected-reference.md"}
  ],
  "required_source_roles": ["source_document"],
  "required_checks": [
    {"id": "content", "kind": "artifact", "sheets": ["Main", "Notes"]},
    {"id": "layout", "kind": "visual", "sheets": ["Main", "Notes"]}
  ]
}
~~~

Use existing approved task policies, skills and packet checkers. Include every
source whose bytes must invalidate the preparation in "sources"; payload-internal
references are **not** automatically traversed. Source completeness needs semantic
review. For a non-spreadsheet route, sheets can be empty and visual checks can be
omitted. There is no universal native Excel requirement. A policy must name at
least one check and one pack method; unconfigured checks are not implied.

~~~sh
python3 flow.py prepare intake.json run-001.bundle.json run-001.prompt.txt
python3 flow.py check run-001.bundle.json
~~~

Preparation records hashes of the intake, policy, request files, sourced base,
payload, listed sources, task skill, selected methods and references. It preserves
raw request text in the generated prompt. Store generated materials privately.
Existing output paths are refused; use new paths per run.

## Bind evidence after authorized work

"fingerprint" prints a file reference, not a quality verdict:

~~~sh
python3 flow.py fingerprint final-artifact.bin
python3 flow.py fingerprint review-record.txt
python3 flow.py check run-001.bundle.json run-001.evidence.json
~~~

The evidence JSON names the bundle's "binding_sha256" and contains:

| Field | Required declaration |
|---|---|
| observed_reads | One entry per executor_read input: input_id, input_sha256, evidence file reference and locator. A prompt mention is never synthesized into a read. |
| readback | status complete plus evidence/locator, or status incomplete plus reason. Incomplete is preserved and cannot produce completion. |
| semantic_review | verdict pass, reviewer, basis, final artifact_sha256, evidence/locator. |
| artifact | Final file path and sha256. |
| checks | Exactly the configured IDs, each with verdict pass, reviewer, final artifact_sha256, checked sheets, evidence/locator. |

Each evidence JSON covers one final artifact. A multi-output task needs separate
per-artifact evidence files and check records; one artifact's result does not
establish completion of the other outputs.

Every "evidence" is an object containing "path" and "sha256", alongside a nonempty
"locator" in its parent record. Evidence paths are relative to the evidence JSON.
A complete synthetic example is constructed in test_flow.py. Existing handoff and
packet validators can supply records for policy-named checks; this helper never
imports or executes a command from a policy, packet or evidence record.

The checker reconstructs preparation from the current intake and rejects changed
bound bytes or derived fields. It also checks current artifact and evidence file
hashes. Missing/empty files, missing reads/checks, wrong hashes, incomplete readback
and incomplete required sheet coverage cannot produce completion.

## Read the result literally

"structure_bound" means the declared files/derived structure still match.
"observed_reads", "semantic_review_claim", "artifact_verified" and
"visual_verified" remain distinct **declarations**. The highest status is
"evidence_complete_declaration", never ready/approved/published. Exit 0 means that
the configured declaration set is structurally complete; exit 2 means incomplete
or invalid.

The helper does not authenticate reviewers, validate source meaning, inspect
spreadsheet formulas/layout, prove actual reads, or prove any check was run.
A forged record with rewritten hashes is not prevented. There is no signature or
trusted runtime. It checks the files as they exist during that check, not future
changes; recheck after edits and at the actual delivery boundary. Acceptance still
belongs to the applicable task process and reviewer.

## Tests and method

~~~sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s adapters/codex-document-flow -p 'test_*.py' -v
~~~

Run the command from the repository root. Tests use only temporary synthetic data.
They cover valid declarations and failure cases for source/policy/skill/payload/base
mutations, modified final files/proofs, absent evidence, missing reads, incomplete
readback, missing source roles, invented preservation quotes, missing checks/sheets,
wrong final hashes, bundle tampering and output overwrite.

These tests validate deterministic binding behavior, not model effectiveness.
The adapter applies skill-authoring's trigger/steps/done and explicit-load methods,
and ground-truth-gates' positive-plus-negative verification discipline. Any claim
of better model output requires separate target-runtime and target-tier evidence.
