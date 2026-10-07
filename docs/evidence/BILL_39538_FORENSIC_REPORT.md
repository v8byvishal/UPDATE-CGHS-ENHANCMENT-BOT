# Forensic report — example-format bill `39538.pdf`

Scope: §1–§26 of the forensic directive. This report is **evidence-derived**. Every
number below was read out of the committed PDF by script; nothing is recalled,
inferred from conversation, or reconstructed from a fixture.

`39538.pdf` is treated as a **specimen of a document format**, not as a case. No
value in this report is permitted into production logic as a special case
(§2, §18, §22). The production rules that result are structural only.

---

## A. Git bill provenance

| field | value |
|---|---|
| located by | `git ls-tree -r --name-only origin/main` |
| Git path | `39538.pdf` (repository root on `origin/main`) |
| commit | `b5987f4` — *"Add files via upload"* |
| blob (SHA-1) | `423a9ea630136c55d1e06ea07f1bdf39982d6ce3` |
| size | 239,002 bytes |
| **SHA-256** | **`1825f727981314ec5b6241f967ee9e26ee1bd76e4cbac96b5ceb4404da04993a`** |
| extraction | `git cat-file -p 423a9ea… > 39538.pdf` |
| round-trip check | `git hash-object` on the extracted file returns `423a9ea…` → byte-exact |
| in-repo copy | `tests/fixtures/39538.pdf`, same SHA-256, picked up automatically by `_find_bill("39538")` |
| page count | **95** |
| producer | Crystal Reports (`PDF 1.7`, creator `Crystal Reports`, producer `Powered By Crystal`) |
| extracted text | 172,588 chars, **0 empty pages** |

The PDF was **not** recreated, substituted, or approximated. Earlier rounds
reported `EVIDENCE_MISSING` for the bill; that status is now **withdrawn** —
the bill is in Git and was read in full.

---

## B. Bill format forensics

### B.1 Page classes

| pages | class |
|---|---|
| 1 | Service Summary table (**no caption**) |
| 2 | payment/discount block |
| 3–90 | detailed sections, **payer-payable region** |
| 90 | Bed Details + `Payer Payable Total :` |
| 91–94 | detailed sections, **patient-payable region** |
| 94 | `Patient Payable  Total :` + `Grand Total :` |
| 95 | legend / notes appendix |

### B.2 The two payable regions — the key structural discovery

The document is partitioned into two regions with **perfectly symmetric** grammar.
Each phrase occurs exactly twice: once as a *column header* opening the region,
once as a *closing total*.

```
Payer Payable        (p3,  column header)   → region opens
Payer Payable Total : 1,248,055.00  (p90)   → region closes
Patient Payable      (p91, column header)   → region opens
Patient Payable  Total : 31,599.00  (p94)   → region closes   [NB: two spaces]
Grand Total :         1,279,654.00  (p94)
```

Arithmetic validation (independent of any parser):

```
sum of the 13 payer-region department totals = 1,248,054.37
stated  Payer Payable Total                  = 1,248,055.00   (delta 0.63 = rupee rounding)
1,248,055 + 31,599                           = 1,279,654      = stated Grand Total ✓
```

**This region partition is the structural meaning of "Patient Payable excluded"**
in the locked DRUG100 rule. It is not a heuristic about nearby words.

### B.3 Department grammar

A department header is a line of the exact form `<Name>(<999311> )`.
`999311` is the SAC service-accounting code for healthcare — a **format constant**
of this hospital's bill, in the same class as the literal `Dept Sub Total`. It is
not patient, case, or amount data.

* 27 header occurrences = **13 summary rows + 14 detail headers**
* 13 departments; 14 detail headers because **IP Pharmacy appears twice**
* a department name may itself contain parentheses: `Hospital services (others)(999311 )`
* sub-headers inside a section are *subsection* labels, not departments:
  a date (`13-Aug-2026`), a discipline (`Microbiology`, `Operation Theatre`),
  or an order reference (`CBC              (261584 )` — a 6-digit order number, **not** `999311`)

### B.4 Subtotal / total grammar

Crystal emits the label and its amount on separate lines, and the **side differs**:

```
 742,104.38        ← value precedes  "Dept Sub Total"
Dept Sub Total :
Dept Total :
 742,104.38        ← value follows   "Dept Total"
```

Counts in the document: **64× `Dept Sub Total`**, **14× `Dept Total`**.
`Dept Total` is the department's roll-up; `Dept Sub Total` closes one subsection.
Where a department has several subsections the subtotals **sum** to the total —
proven by OT Pharmacy below. The existing `_SUBTOTAL_LABEL` matches
`Dept\s*Sub\s*Total` only, so a `Dept Total` echo is correctly never double-counted.

### B.5 Summary / detail grammar

The first page carries **no `Service Summary` caption** — the literal string
occurs **0 times** in the document. The table is identified by its row shape:

```
<Name>(999311 )     ← department header line
 9,450.00           ← amount line
 1                  ← serial-number line
```

This three-line shape separates the summary from the detail with **no overlap**:
13 headers satisfy it (all page 1), 14 do not (every detail header). A detail
header is always followed by a subsection label or a row, never by amount+serial.

### B.6 Wrapping rules (§12)

CGHS alias codes are printed in a ~7-character column and wrap. The logical cell
is `CGHS-<FAMILY> <REST>-<YEAR>`; the first break falls on the space, later
breaks are hard:

```
CGHS-B      CGHS-CI     CGHS-L        CGHS-L
C002-20     003-202     B-103-2       B042+0
25          5           025           43+044-
                                      2025
→ CGHS-B C002-2025   CGHS-CI 003-2025   CGHS-L B-103-2025   CGHS-L B042+043+044-2025
```

456 of 457 cells span 3 physical lines; exactly **one** spans 4 — the compound
`CGHS-L B042+043+044-2025` (VIRAL MARKER PROFILE). Reconstruction is terminated
deterministically by the trailing 4-digit year, not by a line count.

---

## C. Code inventory

**457 CGHS code occurrences · 52 distinct logical cells · 411 EXECUTABLE · 48 REVIEW_REQUIRED
· 0 resolved codes outside the 1998 registry** (full machine-readable dump:
`docs/evidence/39538_code_inventory.json`).

Every `final_code` produced was checked for membership in `CGHS_CODE_REGISTRY`
(1998 records). **No hallucinated code exists.**

Representative rows (count · raw cell · resolved · status):

| raw cell | n | → | status | service |
|---|--:|---|---|---|
| `CGHS-C N002-2025` | 86 | `CN002` | EXECUTABLE | IP CONSULTATION CHARGES |
| `CGHS-P T005-2025` | 53 | `PT005` | EXECUTABLE | LIMB PHYSIO-EXERCISES PROGRAM |
| `CGHS-L B055-2025` | 39 | `LB055` | EXECUTABLE | GLUCOMETER |
| `CGHS-L B120-2025` | 36 | `LB120` | EXECUTABLE | ABG sampling |
| `CGHS-RI 034-2025` | 33 | `RI034` | EXECUTABLE | BED SIDE X-RAY |
| `CGHS-P T004-2025` | 19 | `PT004` | EXECUTABLE | CHEST PHYSIOTHERAPY-ICU |
| `CGHS-B C002-2025` | 3 | `BC002` | EXECUTABLE | PACKED CELLS - BLOOD UNITS |
| `CGHS-L B-103-2025` | 2 | `LB103` | EXECUTABLE | CK (CPK) CREATINE KINASE — *extra hyphen absorbed* |
| `CGHS-L B042+043+044-2025` | 1 | `LB042`+`LB043`+`LB044` | EXECUTABLE ×3 | VIRAL MARKER PROFILE — *compound split* |
| `CGHS-C C003-2025` | 26 | — | **REVIEW_REQUIRED** | VENTILATOR CHARGES FULL DAY |
| `CGHS-C C002-2025` | 7 | — | **REVIEW_REQUIRED** | OXYGEN FULL DAY |
| `CGHS-NI 001-2025` | 3 | — | **REVIEW_REQUIRED** | EEG ROUTINE |

### C.1 The REVIEW states are correct, not defects

The 48 review occurrences are all `CGHS-C C0xx` plus `CGHS-NI 001`.
`resolve_cghs_codes` refuses them with
`UNRESOLVED_MAPPING: C003 has no approved target code; inventing CC003 is forbidden`.
That is exactly the fail-closed behaviour §11 requires. **Not changed.**

### C.2 `NEEDS_MORE_EVIDENCE` — one asymmetry, reported not fixed

`CGHS-NI 001-2025` → REVIEW (`category 'NI' with token '001' matches no locked rule`),
while the grammatically identical `CGHS-CI 001-2025` → `CI001` EXECUTABLE, and
`NI001` **is** present in the 1998 registry.

* blocked rule: the locked category-composition table in `cghs/rules.py`
* evidence needed: operator confirmation that `CGHS-NI <nnn>` → `NI<nnn>` is an approved
  composition, as already locked for `CI` and `RI`
* **not changed** — approving a family is a policy decision reserved by §11, and the
  present behaviour is visible and fail-closed, never silently wrong.

---

## D. Rule matrix (§14 re-validation against the real bill)

Canonical owner for every rule below is `cghs/rules.py`. No second engine exists;
`cghs/parsing.py` consumes, it does not re-implement.

| rule | PDF evidence | expected | pre-fix | verdict |
|---|---|---|---|---|
| CC001 (ICU room rent rows) | 29 `ROOM RENT(ICU )` rows, p87–89 | 29 | 29 | **no defect** |
| CN002 (`icu*3 + ward*2`) | 29 ICU, 0 ward | 87 | 87 | **no defect** |
| CC002 (oxygen) | 7 oxygen rows 24/24/12/12/12/12/12 | 108 | 108 | **no defect** |
| CC003 ventilator | 26 rows, `CGHS-C C003` | REVIEW | REVIEW (absent from plan) | **no defect** |
| BC002 / PT004 / PT005 | blood & physio rows | 3 / 19 / 53 | 3 / 19 / 53 | **no defect** |
| compound `+` codes | `CGHS-L B042+043+044-2025` | LB042+LB043+LB044 | all 3, EXECUTABLE | **no defect** |
| IP Pharmacy main subtotal | p74 `742,104.38` | 742,104.38 | 742,104.38 | **no defect** |
| IP Pharmacy second section | p94 `31,599.22` | excluded | excluded | **no defect** |
| OT Pharmacy dated blocks | p79–80, 5 blocks | 1,790.80 | 1,790.80 | **no defect** |
| DRUG100 | locked rule | 743,895.18 | 743,895.18 | **no defect** |
| CNSU100 | OT 10,022.00 + Ward 11,874.44 | 21,896.44 | 21,896.44 | **no defect** |
| **Service Summary read** | p1 IP 773,703.60 / OT 1,790.80 | both | **`None` / `None`** | **DEFECT 1 + 2** |
| **Consultation section** | p3–9 | 29,750.00 | **49,540.00** | **DEFECT 3** |
| **Equipment section** | p9–13 | 79,920.00 | **81,180.00** | **DEFECT 4** |
| **Ward Consumables section** | p89 | 11,874.44 | **0.00** | **DEFECT 5** |

### D.1 DRUG100 — the locked rule, resolved from evidence (§9)

Contract (`cghs/parsing.py` docstring, line 19):
`DRUG100 = IP Pharmacy subtotal + OT Pharmacy subtotal (Patient Payable excluded)`

The only phrase that needed interpretation was *"Patient Payable excluded"*.
§B.2 settles it **structurally**: the bill is literally partitioned into a payer-payable
and a patient-payable region, and the second IP Pharmacy section lies wholly inside
the patient-payable region, closing with its own `Patient Payable  Total :`.

```
IP Pharmacy #1  (p23–74, payer region)     742,104.38   INCLUDED
IP Pharmacy #2  (p91–94, patient region)    31,599.22   EXCLUDED  → Patient Payable Total 31,599.00
OT Pharmacy     (p79–80, payer region, 5 dated blocks)
                223.85 + 447.70 + 447.70 + 447.70 + 223.85 = 1,790.80   ALL INCLUDED
DRUG100 = 742,104.38 + 1,790.80 = 743,895.18
```

Cross-checks from the document itself:

```
742,104.38 + 31,599.22 = 773,703.60  = Service Summary "IP Pharmacy"  ✓
            1,790.80               = Service Summary "OT Pharmacy"  ✓
```

**There is no `RULE_AMBIGUITY`.** The rule is determinate once the region model is
applied. The Service Summary aggregates *across* regions, which is precisely why a
summary-vs-detail equality gate was wrong and must stay removed.

**DRUG100 for this bill does not change.** The fix changes how the figure is
*derived* — so that the same code stays correct on the next bill of this format —
and repairs four other departments that are wrong today.

---

## E. Defects, root causes, and why they generalize

All five were reproduced by running **current production code** against the real PDF
before any edit (§21).

**DEFECT 1 — Service Summary span collapses.**
`_service_summary_span()` defines the summary as "everything before the first
department header". In this format the summary table *is itself made of department
headers*, so the span ends at its own first row → `(0, 790)`, covering only the
patient-metadata block. Root cause is the span boundary, not the caption fallback.

**DEFECT 2 — summary amount must be on the label's line.**
`extract_service_summary_amount()` reads amounts only from the line that matched the
department. Crystal puts the amount on the **next** line. Even with a correct span it
returns `None`.
*Combined effect of 1+2:* `ip_summary` and `ot_summary` are `None`, so the
773,703.60 / 1,790.80 cross-check silently vanishes from provenance.

**DEFECT 3 — sections anchored on a name, not a header.**
`collect_dept_subtotals()` ran `re.finditer(dept_pattern, text)` over the whole
document, so the **row description** `PHYSIOTHERAPY CONSULTATION` (p81) fabricated a
"Consultation section" that swallowed Physiotherapy's `19,790.00`
→ Consultation 29,750.00 → **49,540.00**.
*Generalization risk:* any future bill with a row mentioning a department name
corrupts that department — including a row naming a pharmacy.

**DEFECT 4 — header regex cannot express a parenthesised name.**
`[A-Za-z][A-Za-z\s]*\(\s*999311\s*\)` cannot match `Hospital services (others)(999311 )`,
so that boundary did not exist and Equipment's slice ran straight through it,
absorbing `1,260.00` → **81,180.00**. The same `\s`-greedy class also swallowed the
preceding line (`'Service \nBlood Bank Procedure(999311 )'`).

**DEFECT 5 — the most dangerous: blanket payable tokens.**
Exclusion tested the slice for `Patient Payable|Grand Total|**Payer Payable**`.
The payer region always *closes* with `Payer Payable Total :`, so the **last payer-side
department of any bill in this format is excluded** — with a false reason
("the locked rule excludes Patient Payable"). Here that is Ward Consumables:
11,874.44 → **0.00**.
*Impact today:* CNSU100 is unaffected because it is computed by
`extract_consumables_total`, a different function, which reads 21,896.44 correctly.
*Impact tomorrow:* if a same-format bill ends its payer region with IP or OT Pharmacy,
**DRUG100 is silently under-reported**. This is the strongest argument for the fix.

---

## F. The fix (structural, no case data)

One canonical owner, `cghs/rules.py`. No new module, no second engine, no value from
this bill in production code.

1. **`_DEPT_HEADER_RE`** — line-anchored, tolerates balanced parentheses inside the
   department name. 25 → 27 matches; all 13 departments now recognised.
2. **`_is_summary_row()`** — the proven three-line shape (header / amount / serial).
3. **`_service_summary_span()`** — ends at the first header that is *not* a summary
   row, instead of the first header. Unchanged for single-line summaries.
4. **`extract_service_summary_amount()`** — falls through to the next line when the
   label line carries no amount. Exact match on a whole-line amount; no fuzzy matching.
5. **`collect_dept_subtotals()`** — iterates **department headers** and keeps those whose
   header text matches the requested department, instead of matching the name anywhere.
6. **Exclusion test** — replaced by the region model: a section is patient-payable iff
   its header names a patient-payable department, **or** its slice carries a
   `Patient Payable … Total` marker, **or** it begins after the document's
   `Payer Payable Total` close. `Grand Total` and the bare `Payer Payable` token are gone.

Preserved exactly: no magnitude ceiling, label-first/value-first precedence,
position de-duplication, per-subtotal provenance with reasons, and the removal of the
summary-vs-detail gate.

---

## G. Status ledger

| item | status |
|---|---|
| bill obtained from Git, hash recorded | **PROVEN** |
| entire 95-page document inspected | **PROVEN** |
| region model (payer / patient) | **PROVEN** (arithmetically closed) |
| summary-row grammar | **PROVEN** (13 / 14 clean split) |
| subtotal & total grammar | **PROVEN** |
| code wrap reconstruction | **PROVEN** (457/457 cells) |
| DRUG100 rule meaning | **PROJECT_LOCKED** + **PROVEN** |
| `CGHS-C C0xx` stays REVIEW | **PROJECT_LOCKED** |
| `CGHS-NI 001` → `NI001` | **NEEDS_MORE_EVIDENCE** — reported, not implemented |
| closing-total double-count guard (earlier §3D risk) | **NEEDS_MORE_EVIDENCE** — shape does not occur in this bill; not implemented |
| `LIVE_PORTAL` | **NOT_VERIFIED** — no Windows authenticated portal run was performed |
| Node regression | **N/A** — no `package.json`; project is Python-only |

---

## H. Old vs new — measured on the real document

### H.1 Correctness

| reading | pre-fix | post-fix | truth (from the PDF) |
|---|---|---|---|
| Service Summary · IP Pharmacy | **`None`** | 773,703.60 | 773,703.60 |
| Service Summary · OT Pharmacy | **`None`** | 1,790.80 | 1,790.80 |
| `_service_summary_span` | **`(0, 790)`** | covers the whole table | — |
| Consultation | **49,540.00** | 29,750.00 | 29,750.00 |
| Equipment | **81,180.00** | 79,920.00 | 79,920.00 |
| Ward Consumables | **0.00** | 11,874.44 | 11,874.44 |
| IP Pharmacy (payer) | 742,104.38 | 742,104.38 | 742,104.38 |
| IP Pharmacy (patient) | 31,599.22 excluded | 31,599.22 excluded | excluded |
| OT Pharmacy | 1,790.80 | 1,790.80 | 1,790.80 |
| **DRUG100** | **743,895.18** | **743,895.18** | **743,895.18** |
| CNSU100 | 21,896.44 | 21,896.44 | 21,896.44 |
| CC001 / CN002 / CC002 | 29 / 87 / 108 | 29 / 87 / 108 | 29 / 87 / 108 |

DRUG100 was already correct for this bill. The repair is to the *derivation*,
so the same code stays correct on the next bill of this format — and it fixes
three departments that were wrong today.

The exclusion **reason** also changed from false to true. Ward Consumables was
previously excluded with
`"section carries 'Payer Payable' and the locked rule excludes Patient Payable"`
— the payer total is precisely what must be **included**.

### H.2 Performance (`reconcile_pharmacy`, real 95-page bill, 172,588 chars)

| build | mode | mean ms | p50 | p95 | worst |
|---|---|---|---|---|---|
| pre-fix | cold | 27.14 | 26.82 | 28.15 | 29.78 |
| pre-fix | warm | 26.81 | 26.17 | 26.88 | 35.24 |
| **post-fix** | **cold** | **9.56** | 9.35 | 9.96 | 10.62 |
| **post-fix** | **warm** | **3.99** | 3.97 | 4.08 | 4.13 |

**Batch of 20 distinct documents** (the cache cannot help across different bills):
**541.9 ms → 199.1 ms**, average per bill **27.10 ms → 9.95 ms = 2.72× faster**.

Full-document regex passes per `reconcile_pharmacy`: department-header scans
**6 → 1**, subtotal-label scans **59 → 3**.

An intermediate measurement is reported for honesty: the correctness fix alone,
before the index was added, was **slower** (25.70 → 30.73 ms) because
`_is_summary_row` re-scanned per header. That regression is what motivated
`_document_sections`; it was measured, not assumed.

No safety operation was removed to achieve this. No sleep, poll, commit
verification, duplicate guard or fail-closed path was touched — the change is
confined to a memoised index of section boundaries, proven result-identical by
`test_section_index_is_result_identical_cold_and_warm`,
`test_section_index_does_not_leak_between_documents`,
`test_section_index_is_built_once_per_document` and
`test_section_index_cache_is_bounded`.

### H.3 Regression

| suite | result |
|---|---|
| focused — `test_real_format_bills.py` | **93 passed** (was 56) |
| focused — `test_performance_caching.py` | **21 passed** (was 17) |
| parser/rules — `test_parser_rules.py` | **70 passed** |
| code rules — `test_cghs_code_rules.py` | **155 passed** |
| **full Python suite** | **684 passed, 0 failed** (was 643) |
| real `39538.pdf` regression | **ACTIVE and PASSING** — no longer `ENVIRONMENT_BLOCKED` |
| Node suite | **N/A** — no `package.json`; the project is Python-only |

**Pre-fix proof (§21):** restoring only `cghs/rules.py` from `0f27b31` makes
**26** of the new tests fail; restoring the fix returns **0** failures.

### H.4 Files changed

| file | role |
|---|---|
| `cghs/rules.py` | **the only production change** — header regex, summary span, summary amount reader, section collector, region-based exclusion, `_document_sections` index |
| `tests/test_real_format_bills.py` | +37 tests (real PDF + generalisation); `FINDING_DEPT_SUBTOTAL_VALUE_FIRST` closed, original text preserved verbatim |
| `tests/test_performance_caching.py` | +4 cache-safety tests |
| `tests/test_parser_rules.py` | declared baseline deviation updated to describe the new behaviour truthfully |
| `tests/fixtures/39538.pdf` | the evidence bill, byte-identical to the Git blob |
| `docs/evidence/BILL_39538_FORENSIC_REPORT.md`, `39538_code_inventory.json` | this report and the machine-readable inventory |

Untouched: `cghs/dom.py`, `cghs/controllers.py`, `cghs/orchestrator.py`,
`cghs/session.py`, `cghs/tabs.py`, `cghs/locators.py`, `ui_theme.py`, `app.py`,
and the 1998-record master registry.

### H.5 Residual §21 note

`cghs/dom.py:132` and `cghs/orchestrator.py:735` still mention `39538` in
**comments only** (no amounts, no executable effect). They are portal-layer
files and the standing rule is not to modify them without a proven regression,
so they are reported rather than edited. `cghs/rules.py` is now free of case
identity.

---

## I. Artifact

| field | value |
|---|---|
| branch | `arena/01a10125-update-cghs-enhancment-bot` |
| commit | **`194c3e6`** = the fix; **`591cb0e`** = this report (branch HEAD) |
| tag | **`bill-format-39538-v4`** |
| pushed | yes — branch and tag both on `origin` |
| ZIP | `CGHS_AUTOMATION_SPEED_HARDENED_BUILD_FINAL.zip` |
| size | 66 files · no wrapper directory · built with `git archive HEAD` |
| **ZIP SHA-256** | published with the delivery and in the sidecar `CGHS_AUTOMATION_SPEED_HARDENED_BUILD_FINAL.zip.sha256`. It cannot be embedded here: the report is inside the archive, so stating the hash in the report would change it. |
| extraction check | extracted to a clean directory; `tests/fixtures/39538.pdf` SHA-256 matches; **full suite 684 passed / 0 failed from inside the extracted tree** |
| regenerate | `git archive --format=zip -o <name> HEAD` |

## J. Acceptance gate (§24)

| gate | state |
|---|---|
| bill obtained directly from Git | ✅ `origin/main` `b5987f4`, blob `423a9ea6` |
| Git source / hash recorded | ✅ SHA-256 `1825f727…`, round-trip verified |
| entire bill inspected | ✅ 95/95 pages, 0 empty |
| format model is evidence-derived | ✅ regions, headers, subtotals, summary, wraps |
| all observed code forms inventoried | ✅ 457 occurrences, 52 distinct |
| official registry remains canonical | ✅ untouched, 1998 records |
| no hallucinated code exists | ✅ every resolved code is a registry record |
| repeated sections handled structurally | ✅ IP Pharmacy ×2 kept distinguishable with provenance |
| multi-subtotal departments handled structurally | ✅ OT Pharmacy 5 dated blocks |
| summary / detail not confused | ✅ summary span ends at first detailed header |
| Patient Payable not substituted for detail | ✅ excluded by region, reason reported |
| pharmacy extraction correct | ✅ 742,104.38 / 31,599.22 / 1,790.80 |
| DRUG100 follows the locked rule | ✅ 743,895.18, no REVIEW state |
| CNSU100 remains safe | ✅ 21,896.44 unchanged |
| no arbitrary amount ceiling | ✅ none; parametrised tests up to 1,250,000.00 |
| `c844823` fixes intact | ✅ asserted by `test_c844823_…` |
| previous portal safety intact | ✅ no portal file modified |
| cross-file data flow verified | ✅ §D; one canonical owner, no second engine |
| production path has regression coverage | ✅ `CGHSParsingEngine().parse()` on the real PDF |
| real example bill passes | ✅ |
| generalised same-format fixtures pass | ✅ altered identity, amounts, dates, page breaks, section counts |
| performance measured | ✅ cold/warm/batch, p50/p95/worst |
| optimisation preserves safety | ✅ result identity asserted by 4 tests |
| package matches tested source | ✅ `git archive HEAD` |
| ZIP extract-verified | ✅ 684 passed inside the extracted tree |
| **`LIVE_PORTAL` honestly reported** | ✅ **`NOT_VERIFIED`** |

## K. Outstanding — `EVIDENCE_MISSING` / `NEEDS_MORE_EVIDENCE`

Nothing was guessed to close these.

1. **`CGHS-NI <nnn>` composition** — `CGHS-NI 001-2025` stays `REVIEW_REQUIRED`
   though `NI001` is a registry record and `CGHS-CI 001-2025` → `CI001` works.
   Needs: operator approval that `NI` is a locked composition family.
   Blocked: the category ladder in `cghs/rules.py`. 3 occurrences in this bill.
2. **Closing-total double count** — a department printing per-date subtotals
   *and* a closing section-level `Dept Sub Total` under the same label would be
   counted twice. This shape does **not** occur in 39538 (OT Pharmacy closes with
   `Dept Total`), so there is no evidence to design against. Not implemented.
3. **`LIVE_PORTAL` = `NOT_VERIFIED`** — no Windows authenticated portal run was
   performed in this environment. Unit and fake-portal tests cannot establish it.
4. **Other bills (40343, 39078, 40337, D1–D5)** — still absent; those tests skip
   loudly as `ENVIRONMENT_BLOCKED`. Only 39538 is present.

---

# ROUND 15 — DEFECT-FIX AND INTEGRATION AUDIT

Scope: a fresh audit of six *suspected* defects plus the cross-file data flow.
Nothing below was accepted from the earlier rounds without re-proving it.
Two of the six suspected defects were **not reproducible** and were therefore
**not changed**.

## L. Provenance re-verified (§3)

| property | value |
|---|---|
| Git path (origin/main `b5987f4`) | `39538.pdf` |
| in-repo regression fixture | `tests/fixtures/39538.pdf` |
| blob (identical for both paths) | `423a9ea630136c55d1e06ea07f1bdf39982d6ce3` |
| bytes | 239,002 |
| SHA-256 | `1825f727981314ec5b6241f967ee9e26ee1bd76e4cbac96b5ceb4404da04993a` |
| pages | 95 |
| producer | Crystal Reports, PDF 1.7 |

The specimen remains a **format specimen**. No bill id, patient, IP number,
page number, date, quantity or amount from it appears in production logic.

## M. Defect ledger

| # | suspected defect | reproduced? | action |
|---|---|---|---|
| 1 | `PortalSession.verify_identity_unchanged()` fail-open | **YES** (3 paths) | fixed, fail-closed |
| 2 | `extract_service_rows_from_pages()` gives `Name :` as `service_name` | **NO** | `NOT_PROVEN — NO CHANGE MADE` |
| 3 | `collect_dept_subtotals()` silent `except Exception: pass` | **YES** (3 paths) | fixed, fail-closed |
| 4 | `extract_consumables_total()` heuristics | **YES** (3 defects + 1 crash) | fixed, delegated to the canonical model |
| 5 | builder omits `tests/fixtures/39538.pdf` | **YES** | fixed, shipped + hash-verified |
| 6 | build/dependency irreproducibility | **YES** | fixed, single pinned manifest |
| 7 | harness hands production a stubbed `fitz` (found during §16) | **YES** | test harness fixed |
| — | `parse_row_quantity()` `< 30` ceiling | **NO** | `NOT_PROVEN — NO CHANGE MADE` |
| — | oxygen FULL/HALF parsing | **NO** | `NOT_PROVEN — NO CHANGE MADE` |

### M.1 Defect 1 — portal identity was fail-open (`cghs/session.py`)

Reproduced with a scripted driver against the real method. Three approvals
that no evidence supported:

| case | pre-fix | post-fix |
|---|---|---|
| C — probe answers, every patient field EMPTY, URL/title identical | `True, "patient evidence matched"` | `False, "patient identity could not be re-read …"` |
| C2 — ip + bill unreadable, name re-read and matching | `True, "patient evidence matched"` | `True, "patient evidence matched on patient_name (ip_case, bill_number unreadable this time)"` |
| D — `ProbeUnsupported`, context had patient evidence, URL/title identical | `True, "signature fallback matched"` | `False, "… a URL/title match is not evidence of a patient"` |

Root cause: the comparison loop was `if expected and actual and …`, so when
every field came back empty **not one comparison ran** and the function
returned a match it had never made; and the `ProbeUnsupported` branch treated
URL + title as sufficient even for a context verified against real patient
evidence.

Rule now: when the verified context carried patient evidence, **at least one
identity field must be re-read and match, and no re-read field may
contradict**. Which fields carried the proof — and which were unreadable — is
named in the reason, so weaker evidence is *disclosed*, not silently accepted.

**C2 is a deliberate decision, not an oversight.** The directive requires a
fail when "patient/bill/IP identity evidence *cannot be re-read*"; in C2 the
name **is** re-read and matches, so it passes with the degradation disclosed.
A stricter variant ("IP or bill specifically must be re-readable") is
`NOT_PROVEN` — no project spec or portal evidence ranks the fields, and
inventing that ranking would be a guessed business rule.

No new identity source was introduced: the same `["signature", "patient"]`
probe is used. No cookie, token, profile, login or MFA automation
(`test_the_identity_gate_never_invents_a_dom_source`, which strips docstrings
and comments before checking, because `session.py` legitimately *documents*
that it does none of these).

Gates A–F all covered: `tests/test_tab_frame_safety.py`, 11 new tests.

### M.2 Defect 2 — `NOT_PROVEN`, service-name provenance

The claim was that page-header text such as `Name :` becomes a row's
`service_name`. Measured on all 95 pages of the real document:

| measurement | result |
|---|---|
| service rows extracted | 459 |
| rows whose `service_name` contains header material (`Name :`, `IP No`, `Bill No`, `Order No`, `Page n of m`) | **0** |
| rows with an empty `service_name` | **0** |
| rows whose `service_name` is pure numeric noise | **0** |
| raw occurrences carrying header material in `source_service_name` | **0** |
| department headers contaminated by page-header text | **0** |
| department-header candidates rejected by the `len < 80` guard | **0** |
| **minimum `|y0(header) − y0(CGHS row)|` across all pages** | **20.13 pt** vs a 12 pt cluster threshold |

The clustering cannot merge the two on this document — the margin is 67 %.
The only non-service row that reaches the list is the
`TPA/Corporate(1): CENTRAL GOVERNMENT HEALTH SCHEME(CGHS)` banner, and it
resolves to **zero** codes, so nothing enters the plan.

`service_name = combined.split("CGHS")[0]` is still structurally weak — it
carries amounts and serials — but **no defect follows from it on the available
evidence**, so it was not changed. Evidence needed to reopen: a same-format
bill where a header block and a service row fall within 12 pt of each other,
or any row whose resolved occurrence carries header text.

### M.3 Defect 3 — a parser failure became a silent wrong number (`cghs/rules.py`)

`collect_dept_subtotals()` wrapped its **whole body** in
`except Exception: pass` and returned whatever it had collected so far.

| scenario | pre-fix | post-fix |
|---|---|---|
| invalid department pattern | `[]` → `extract_dept_subtotal` = **0.00** | `re.error` propagates |
| `text=None` | `[]` | `TypeError` propagates |
| internal defect mid-loop | **partial** sum returned as complete | exception propagates |

The consequential one: with an injected fault in `_subtotal_at`,
`reconcile_pharmacy` published

```
DRUG100 total = 742,104.38      (truth: 743,895.18 — the whole OT Pharmacy department lost)
reason        = "... + OT Pharmacy subtotal (0.00) = 742,104.38 [742,104.38 = 742,104.38]"
```

— a wrong figure, self-described as reconciled, with no REVIEW state. That is
precisely the failure mode the project forbids.

Recoverable **data** conditions are unchanged and still handled where they
occur: a label with no amount, a non-numeric amount and a non-positive amount
are each skipped with provenance. An unexpected failure now surfaces; in the
production path `cghs/parsing.py` turns it into a **visible** `rejected` entry
(counted in the UI as "N flagged" and written to the audit report), so DRUG100
is *absent and explained* rather than *present and wrong*.

A gate test (`test_no_broad_exception_handler_remains_in_the_subtotal_readers`)
parses the AST of the four subtotal readers and fails on any bare `except:` or
`except Exception:`.

### M.4 Defect 4 — consumables was a second, divergent section model

`extract_consumables_total()` scanned every `Dept Sub Total` globally, searched
5,000 characters backwards for a department-ish word, and de-duplicated on a
`(position // 500, amount)` bucket. Cases A–H were run against the grammar
proven from the real PDF:

| case | pre-fix | post-fix | truth |
|---|---|---|---|
| REAL 39538 | 21,896.44 | 21,896.44 | 21,896.44 |
| A OT Consumables only | 10,022.00 | 10,022.00 | 10,022.00 |
| B Ward Consumables only | 11,874.44 | 11,874.44 | 11,874.44 |
| C repeated same-name dept | 1,200.00 | 1,200.00 | 1,200.00 |
| **D two depts, equal subtotals** | **2,500.00** | 5,000.00 | 5,000.00 |
| **D2 one dept, two equal dated subtotals** | **2,500.00** | 5,000.00 | 5,000.00 |
| E page breaks inside a dept | 11,874.44 | 11,874.44 | 11,874.44 |
| F unrelated dept immediately before | 10,022.00 | 10,022.00 | 10,022.00 |
| G three consumable families | 22,346.44 | 22,346.44 | 22,346.44 |
| **H consumables in the Patient Payable region** | **20,021.00** | 10,022.00 | 10,022.00 |

Plus a latent crash: the `elif last_header` branch unpacked three-tuples into
two names and raised `ValueError: too many values to unpack`, which
`cghs/parsing.py` swallowed into a rejection — **CNSU100 silently vanished**.

Root cause of D/D2: the dedup key was the *amount*, not the position. Root
cause of H: the region model that `collect_dept_subtotals` already applied to
pharmacy **did not exist on this path**, so two readers gave two different
answers about the same document.

Fix: `extract_consumables_total` now **delegates to
`collect_dept_subtotals(text, r'Consumable')`** — header-anchored, section
scoped, region aware, position-deduplicated. The duplicate business logic is
*removed*, not patched. One section model, one answer to "which money is
patient-payable". The `(label, value)` detail contract is preserved, and the
label is now the real department header (`OT Consumables(999311 )`) instead of
a 40-character text snippet, so provenance improved.
`collect_dept_subtotals` gained one additive key, `department`.
A gate test asserts the 5,000-window and the private `999311` discovery are
gone and that `collect_dept_subtotals` is called.

### M.5 Defects 5 & 6 — the artifact did not match the tested source

Proven by running `tools/build_package.py` in a clean worktree:

| | builder ZIP (pre-fix) | `git archive` ZIP (what shipped) |
|---|---|---|
| entries | 56 | 66 |
| `tests/fixtures/39538.pdf` | **ABSENT** | present |
| builder verdict | **`PACKAGING: PASS`** | n/a |

And the consequence, measured by running the suite from each extracted tree:

| tree | skipped | real-39538 tests skipped |
|---|---|---|
| repository | 8 | 0 |
| **extracted builder artifact (pre-fix)** | **31** | **23** |
| extracted builder artifact (post-fix) | 8 | **0** |

All 23 reported `ENVIRONMENT_BLOCKED: 39538.pdf is not in the repository` and
the artifact still looked green. A green run that has silently stopped
exercising the real document is a false pass, not a pass.

Fixes:
* `INCLUDE_GLOBS` gains `tests/fixtures/*.pdf`;
* a new `REQUIRED_FIXTURES` map (path → SHA-256) is enforced **at build time**
  (workspace presence, hash, *and* that some glob actually selects it) and
  **at verify time** (present in the archive, hash of the archived bytes);
* `requirements.txt` is created as the **single authoritative manifest** —
  it was referenced by `INCLUDE_GLOBS` but did not exist, so the glob silently
  matched nothing;
* `BUILD_WINDOWS.cmd` no longer carries its own list. It previously ran
  `pip install --upgrade pyinstaller selenium PyQt5 PyMuPDF`, i.e. whatever was
  newest on build day, so the EXE was never built against the tested versions.
  It now installs `-r requirements.txt`, fails clearly if the manifest is
  missing, and fails clearly if a declared dependency is not importable
  afterwards.

**Pins are measured, not guessed.** `PyMuPDF==1.28.2`, `selenium==4.50.0`,
`pytest==9.1.1` are the versions this suite actually ran against, and a test
(`test_the_manifest_pins_the_versions_the_tests_actually_ran_against`) compares
every pin against `importlib.metadata.version()` so a stale pin fails the suite.

**`PyQt5` and `pyinstaller` are `NOT_PROVEN`.** Neither is installable in the
headless Linux environment that ran the suite, so no version is asserted for
them; they carry documented minimum floors and an explicit `NOT_PROVEN` marker.
Evidence needed to close: a real `BUILD_WINDOWS.cmd` run on the target Windows
host, whose resulting versions replace those two lines.

### M.6 Defect 7 — the harness could hand production a stub (§16)

`tests/support/legacy_loader` installs a **stub module under the name `fitz`**
so the `c3ccdf3` baseline imports without PyMuPDF. Two call sites still used
`pytest.importorskip("fitz")`, which therefore returns that stub.

Proven, not argued: placing a real PDF at the `40343` slot made the gate
reachable and the run died with

```
TypeError: 'open' object is not iterable      (cghs/parsing.py:223)
```

— a **harness** defect reported as a **parser** defect. It was latent only
because none of those eight bills is in the repository yet; `_find_bill` is
explicitly designed to pick them up the moment they appear.

Fixed by using the established `_real_fitz_installed()` pattern at both sites.
Re-run with the same temporary file, the parse then succeeded
(`DRUG100 = 743,895.18`) and the test correctly failed its *identity*
assertion, because the file was 39538's bytes at the 40343 slot — the test
doing exactly its job. The temporary file was removed; only `39538.pdf`
remains in `tests/fixtures/`.

A new guard builds the forbidden literal at runtime (so it cannot match its own
source) and fails if the trap is reintroduced — verified by reintroducing it.

### M.7 `NOT_PROVEN` — quantity semantics (§12)

| measurement on the real bill | result |
|---|---|
| rows where the `qty ref amount` column pattern yields ≥ 30 | **0** |
| candidates in the 30–49 dead band (`< 50` filter accepts, `< 30` return rejects) | **0** |
| observed `parse_row_quantity` results | 1 (×425), 2 (×9), 3 (×17), 4 (×7), 5 (×1) |
| OXYGEN rows | 7 → 24 (FULL DAY ×2), 12 (HALF DAY ×5) — correct |

No evidence exists that quantities ≥ 30 are legitimate in this format, and no
oxygen row is misparsed. **`NOT_PROVEN — NO CHANGE MADE`** for both the `< 30`
ceiling and the FULL/HALF parsing. Evidence needed: a same-format bill with a
genuine quantity ≥ 30, or a project specification stating the valid range.

## N. Code and rule safety re-run (§13)

| property | result |
|---|---|
| resolved occurrences | 459 |
| distinct executable codes | 52 |
| EXECUTABLE / REVIEW_REQUIRED | 430 / 29 |
| **EXECUTABLE codes outside the 1998 registry** | **none** |
| REVIEW tokens | `C` + `C003` ×26, `NI` + `001` ×3 |
| any REVIEW leaking a `final_code` | **no** — `final_code` stays `None` |
| `C003` executable anywhere | **no** |

`CGHS-NI 001` remains `REVIEW_REQUIRED`. The 1998 master registry was not
modified. No blanket `C→CC`, no wildcard family, no invented range, no
nearest-code substitution.

## O. Cross-file data flow (§11)

`PDF → parsing → rules → registry → aggregation → EnhancementPlan → portal
mapping` verified end to end on the real document:

| edge | result |
|---|---|
| `CN002 / CC001 / CC002 / BC002 / PT004 / PT005` | `87 / 29 / 108 / 3 / 19 / 53` — unchanged from baseline |
| `DRUG100` item amount == `reconcile_pharmacy` total | ✅ 743,895.18 |
| `CNSU100` item amount == `extract_consumables_total` | ✅ 21,896.44 |
| every item code registry-known or a declared amount code | ✅ |
| items missing provenance | **none** |
| `DRUG100` → portal target | `drugs(DRGU100-None)` — canonical code stays `DRUG100`; the deviation is confined to the portal option string |
| `CNSU100` → portal target | `consumables(CNSU100-None)` |
| rejections | 117, all **visible** (UI "flagged" count + audit report), none swallowed |
| duplicate business logic | **none** — consumables no longer owns a second section model |
| patient name | parsed for display only; never an input to any rule |

## P. Performance, measured (§14)

Real 95-page bill, same machine, same run. `extract_consumables_total` is the
only hot path the change touched.

| metric | OLD (`19ddbf1`) | NEW | change |
|---|---|---|---|
| `extract_consumables_total` cold | 121.774 ms | **5.541 ms** | **22.0× faster** |
| `extract_consumables_total` warm | 124.515 ms | **0.162 ms** | **768× faster** |
| `reconcile_pharmacy` cold | 9.851 ms | 9.677 ms | unchanged (−1.8 %) |
| `reconcile_pharmacy` warm | 4.044 ms | 4.130 ms | unchanged (+2.1 %, within noise) |
| **full `parse_document`** | **169.09 ms** | **51.33 ms** | **3.29× faster** |

Values identical in both builds: `DRUG100 = 743,895.18`, `CNSU100 = 21,896.44`.

This speedup was **not** an optimisation goal and no safety operation was
removed to obtain it. It is a side effect of deleting duplicated work: the old
reader ran a 5,000-character backward slice plus four regex scans plus a
department-header `finditer` **for every one of the 64 subtotal labels** in the
document; the new one reuses the already-cached `_document_sections` index.
The old reader's "warm" figure being marginally *slower* than its "cold" figure
is reported as measured — it never used the cache, so the difference is noise.

## Q. Files changed (§18)

| file | why changed | defect proving it | test proving the fix | consumed by |
|---|---|---|---|---|
| `cghs/session.py` | identity gate was fail-open | §M.1 C, C2, D | 11 tests in `test_tab_frame_safety.py` | `cghs/controllers.py:1445` (`reconcile`) |
| `cghs/rules.py` | blanket `except` + duplicate consumables section model | §M.3, §M.4 | 5 in `test_parser_rules.py`, 11 in `test_real_format_bills.py` | `cghs/parsing.py`, `app.py` |
| `tools/build_package.py` | fixture omitted, no fixture verification | §M.5 | 4 in `test_packaging.py` | `BUILD_WINDOWS.cmd`, delivery |
| `BUILD_WINDOWS.cmd` | unpinned `--upgrade` install | §M.5 | `test_the_windows_build_consumes_the_manifest…` | Windows build |
| `requirements.txt` *(new)* | no authoritative manifest existed | §M.5 | 2 in `test_packaging.py` | `BUILD_WINDOWS.cmd`, builder |
| `tests/test_real_format_bills.py` | consumables regressions + harness stub trap | §M.4, §M.6 | self | — |
| `tests/test_parser_rules.py` | fail-closed regressions + truthful deviation record | §M.3 | self | — |
| `tests/test_tab_frame_safety.py` | identity gates A–F | §M.1 | self | — |
| `tests/test_packaging.py` | fixture + manifest gates | §M.5 | self | — |

### Files deliberately NOT changed

| file | why |
|---|---|
| `cghs/parsing.py` | defect 2 `NOT_PROVEN`; its `except Exception` around DRUG100/CNSU100 is the **visible** rejection channel, not a silent swallow |
| `cghs/dom.py`, `cghs/controllers.py`, `cghs/orchestrator.py`, `cghs/tabs.py`, `cghs/locators.py`, `cghs/txstate.py`, `cghs/telemetry.py` | no defect reproduced; portal layer untouched |
| `app.py`, `ui_theme.py` | §17 — no UI change |
| the CGHS 1998 master registry | forbidden, and nothing required it |
| `parse_row_quantity`, `parse_oxygen_quantity` | `NOT_PROVEN` (§M.7) |

## R. Testing (§15)

| stage | result |
|---|---|
| pre-fix reproduction | **19 tests fail** against `19ddbf1` production with the new tests in place |
| post-fix | **0 fail** |
| full Python regression | **723 collected, 0 failed, 8 skipped** (was 684 / 0 / 8) |
| skips | all 8 are `ENVIRONMENT_BLOCKED` for 40343 / 39078 / 40337 / D1–D5, which are genuinely absent |
| XFAIL / XPASS | 0 |
| real-PDF regression | **ACTIVE** — runs `CGHSParsingEngine().parse()` on the committed 95-page document |
| extracted-artifact regression | passes, and now with **0** real-39538 skips (was 23) |

New tests: 11 identity + 6 fail-closed + 13 consumables/harness + 7 packaging
and dependency = **39** (723 − 684).

## S. Acceptance gate (§19)

| gate | status |
|---|---|
| Git example PDF verified | ✅ blob, bytes, SHA-256, 95 pages |
| complete 39538 document inspected | ✅ all 95 pages |
| no case-specific production hard-coding | ✅ |
| portal identity fallback is fail-closed | ✅ §M.1 |
| patient-context safety regression passes | ✅ gates A–F |
| service-name provenance structurally correct | ⚠️ **`NOT_PROVEN`** — 0/459 contaminated; no change made |
| no silent broad exception hides parser failure | ✅ §M.3 + AST gate |
| consumables logic proven section-safe | ✅ cases A–H |
| pharmacy logic remains correct | ✅ 743,895.18 |
| 1998 registry remains canonical | ✅ untouched |
| no invented CGHS mapping | ✅ 0 executable codes outside the registry |
| cross-file data flow verified | ✅ §O |
| quantity behaviour proven or `NOT_PROVEN` | ✅ explicitly `NOT_PROVEN` |
| package builder includes the real-bill fixture | ✅ |
| package verifier checks required fixtures | ✅ presence + SHA-256 |
| dependencies / build reproducible | ✅ for the 3 tested pins; ⚠️ `NOT_PROVEN` for PyQt5 + pyinstaller |
| full regression passes | ✅ 723 / 0 |
| extracted artifact regression passes | ✅ |
| performance measured | ✅ §P |
| no safety mechanism removed for speed | ✅ |
| ZIP matches tested source | ✅ built from the committed tree, reopened and hash-verified |
| **`LIVE_PORTAL` honest** | ✅ **`NOT_VERIFIED`** |

## T. Outstanding after round 15

Carried forward unchanged from §K (1–4), plus:

5. **Service-name provenance** — `NOT_PROVEN`. Needs a same-format bill where a
   header block and a service row fall within 12 pt, or any occurrence carrying
   header text. Current margin on 39538 is 20.13 pt.
6. **Quantity ≥ 30** — `NOT_PROVEN`. Needs a bill with a genuine quantity ≥ 30
   or a written specification of the valid range.
7. **`PyQt5` / `pyinstaller` pins** — `NOT_PROVEN`. Needs one real
   `BUILD_WINDOWS.cmd` run on the target Windows host.
8. **Identity evidence ranking** — `NOT_PROVEN`. Whether IP/bill specifically
   (rather than any one identity field) must be re-readable is a policy
   decision with no supporting evidence; C2 currently passes with the
   degradation disclosed in the reason string.

**`LIVE_PORTAL` = `NOT_VERIFIED`.** No Windows authenticated portal run was
performed. Nothing in this round changes that, and no unit or fake-portal test
can establish it.
