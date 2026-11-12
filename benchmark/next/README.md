# Frozen real client questions

The six questions in `docs/development/next/query_manifest.json` are independent
of the original 18-case A–E compatibility cohort. They cover two Invoke, two
Django, and two FastAPI behaviors. Their gold comes from finite source-level
case partitions and heap reasoning, before any current analyzer query runs.

Validate the frozen files and prepare the original pinned distribution bytes:

```bash
python -m benchmark.next.client_query_manifest
python -m benchmark.next.prepare_client_sources --archives /path/to/pinned_archives
```

The preparation command verifies distribution SHA-256 pins, every runtime Python
file, and every extracted source fragment. It never imports, installs, builds,
or executes third-party targets. Existing modified cached source is rejected.
Invoke retains 47 runtime Python files, Django 909, and FastAPI 52.

Each case records a sealed finite initial heap and original function entry,
exact arguments, external models, original source-line mapping, independent
gold, useful-answer predicate and fixed M-tier resource bounds. Invoke callback
bodies are the two synthetic functions in `client_callbacks.py`; they are
hashed inputs, not models inferred from the analyzer. Other source bodies are
preserved byte-for-byte as `.txt` input fragments. Unmodified upstream sources
retain their original annotations and coding conventions.

The slice and full-project tracks use the same entry, input, models and query.
Full-project preparation retains the complete original runtime inventory. At
least `invoke_command_names:q1` must eventually succeed at that scope. Prepared
entries deliberately exclude package initialization and arbitrary user code.
The current registration is not an executable entry adapter; implementation
must support the declared prepared state without quietly substituting module
initialization. The Django `getlist` identity question includes a declared
post-return `append` continuation and is queried at that continuation's exit.

D must answer all six finite scopes without PTA or any Native predecessor. E
must preserve all registered behaviors and meet the same utility predicates.
Whole-project Open status and query-scope closure are reported independently.
A–C remain `not_comparable` until actual entries, extra roots, models and query
projections match. Top, unsupported, uncomputed or truncated answers never
satisfy the registered exact predicates.

`--require-reviewed` additionally rejects pending independent gold review.
Preregistration validation does not certify solver behavior, scale or live LLM
benefit. Actual qualification belongs to N4-EVAL-03 after the source/wheel and
other mandatory gates. External LLM data permission remains disabled.
