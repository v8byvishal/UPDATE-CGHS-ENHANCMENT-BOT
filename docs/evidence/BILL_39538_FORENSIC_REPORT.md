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
| commit | **`194c3e6`** (parent `0f27b31`) |
| tag | **`bill-format-39538-v4`** |
| pushed | yes — branch and tag both on `origin` |
| ZIP | `CGHS_AUTOMATION_SPEED_HARDENED_BUILD_FINAL.zip` |
| size | 558,369 bytes · 66 files · no wrapper directory |
| **ZIP SHA-256** | **`17771ab0d372d95df68c87f31fbb82fa1f136aa156e7b6c7b082260fdc7cefd4`** |
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
