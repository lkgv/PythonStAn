# Stage two: reviewed W0 checkpoint

W0 is verified against public source `e8d9e0d3d7cdf540622537f5af0490522edde91c`
(tree `13a79f550daf75445f9529e5d5a89d70adabb1c6`). Runtime production bytes
are unchanged in this checkpoint. The supplied 43-card obligations, original46-card
mapping and prior22-card mapping remain in the execution ledger.

| W0 card | Reviewed outcome |
| --- | --- |
| N4-BOOT-01 | Pristine committed source/config original stage scope:1001 passed; all34 findings mapped. |
| N4-BOOT-02 | Independent52 semantic+6 offline L1/L2 and36 domain/Native fixtures frozen; strict product acceptance remains red. |
| N4-QUAL-01 | Full305-file diagnostics captured; no new debt;14 tool controls and12 guard tests passed. |
| N4-EVAL-01 | Six nonliteral client questions across Invoke/Django/FastAPI independently frozen, with input/source/gold/budget and original90-row cohort preserved. |

The product baseline has39 failing semantic/query rows and20 failing domain/Native
nodes. Passing oracle/registration checks does not repair those failures. The baseline
wheel still contains17 retired prototype files and lacks `py.typed`; release qualification
is pending. Full-tree mypy has10,349 diagnostics, Ruff has12,477 and270 files need
formatting. The13 clean frontier modules are package markers, not the final typed core.
The29 direct-SUT test call diagnostics remain explicitly owned by N4-TYPE-01.

Read the review records under `reviews/`, task_board.json, finding_ledger.json and
quality_baseline.md for exact scope. Raw receipts remain under ignored
`artifacts/next/evidence`; reviewed non-sensitive summaries are versioned.

Next ready work is N4-TYPE-01 and N4-API-01 under frozen runtime/API plans. The plans
are design inputs, not completed implementation. Production changes invalidate affected
baseline results and require the appropriate regression/type/architecture checks.
Real LLM calls remain behind offline semantic, TCB, transport, budget and release gates;
no credentials or live requests were used by W0. Core/scale/live/benefit qualifications
are reported separately in the task board and are not yet satisfied.
