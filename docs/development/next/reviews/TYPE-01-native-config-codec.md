# Independent Native configuration codec review

Reviewer: requirements_audit. The reviewer did not author Config; their separate
implementation lease covers legacy registry consumers. Disposition: accepted narrowly for
the Config codec, not for the Native worker or TYPE-01 parent.

The actual existing Config record retains its defaults and controls. from_dict
checks unknown fields, filters the three existing driver-only fields, validates
exact legacy list/budget constraints before JSON copying, and explicitly constructs
the real record. The supported policy/limit and unsupported-control checks remain
in its actual post-initialization path. No generic kwargs, Any or surrogate solver
configuration replaces the real implementation.

The long construction and validation methods retain one responsibility: checking
and projecting this finite Config record. Their explicit fields support completeness
review while retaining one source of settings.

The export owns its lists and budget dictionary. Wrong-typed path/debug scalars now
fail at the checked boundary; this is a stated bounded input-contract change. The
legacy record still exposes owned mutable list/budget attributes inside a frozen
dataclass. This review does not claim deep immutability or validate arbitrary
external mutation that violates those declared field types.

Independent validation passed all eleven new controls. The reviewer reran the
actual 2214c8a-source differential: sixty-nine valid configurations have identical
complete projections and twelve invalid controls are rejected by both. Ordinary-
import strict checking reports zero diagnostics in the source/test pair; 3,863
imported records remain visible. Ruff and format checks pass. Source hashes match
the author's receipt and were unchanged before/after the independent run. Existing
author evidence separately records 114 Native/control tests; that broader count
is not presented as an independent rerun here.

Evidence: artifacts/next/evidence/reviewer-W1/native-config/receipt.json.

| File | SHA-256 |
| --- | --- |
| pythonstan/analysis/pointer/kcfa/config.py | 2ab0478348b4c9f5542e44fdf30d7a4edb6b6aa5a99cf9f313286edfa673d082 |
| tests/regression/audit3/test_native_config_codec.py | 9dd9139b429d8e85912a1c85ff8af3a2aa15ea07b3329d9f11ad020e688e7709 |
