# Script contracts

The controlled vocabulary and the exact input/output of every script. Read this
before mapping a user request to a query, or before writing a draft to validate.

## Controlled vocabulary

**Document types** (must match exactly): `Employment Agreement`, `Credit Agreement`,
`License Agreement`, `Lease`, `Merger Agreement`, `Master Services`,
`Indemnification`, `NDA`, `Shareholders Agreement`, `Operating Agreement`,
`Power of Attorney`, `Unknown`.

Spanish documents map to the SAME canonical types (contrato de trabajo →
`Employment Agreement`, pacto de socios → `Shareholders Agreement`, prestación
de servicios → `Master Services`, arrendamiento → `Lease`, poder notarial →
`Power of Attorney`, acuerdo de confidencialidad → `NDA`).

**Jurisdictions**: `Delaware`, `New York`, `California`, `Texas`,
`England & Wales`, `Spain`, `Any`, `Unknown`. Governing-law phrasing is matched
first; a bare place-name mention is only a fallback.

These come from the baseline classifier in `scripts/lib.py`. Extend the keyword
maps there to support more types or jurisdictions.

---

## ingest.py
Build the index from a folder. Offline. The only place AI may classify, and its
output is auditable. Stdlib-only; uses `pdfplumber` automatically if present (optional).

```
python scripts/ingest.py <folder> [--rebuild]
```
Output:
```json
{"ingested": 5, "index": "~/.omplia/contract-forge/index.jsonl",
 "needs_ocr": ["path/to/scanned.pdf"], "dated_by_mtime": ["path/to/undated.docx"],
 "audit": "~/.omplia/contract-forge/classification_audit.jsonl"}
```
Each index record: `doc_id, path, type, jurisdiction, date (always ISO
YYYY-MM-DD), date_source ("text" | "file_mtime"), summary, search_text,
needs_ocr, clause_count, clauses[], classification_method`.
Each clause: `clause_id` (e.g. `doc_ab12#c3`), `heading`, `text`, `offset`.

`dated_by_mtime` files had no date in their text. Surface them: a copied folder
makes every mtime the copy date, which silently corrupts recency ranking.
The index is always rebuilt from the folder; doc_ids are content-hashed and
stable, so feedback survives re-ingestion.

`needs_ocr: true` means a scanned/image PDF with no extractable text. It is
indexed but will not surface as a candidate. Surface these to the user.

---

## query.py
Two-stage retrieval. Pure. Stage 1 hard-filters by type, jurisdiction and date.
Stage 2 ranks survivors by lexical similarity to the need PLUS a preference
boost computed from the feedback ledger (same-type decisions only, capped at
+0.15 so history tilts near-ties but never outvotes relevance), then recency.

```
python scripts/query.py --type "Employment Agreement" --juris Delaware \
    --date-after 2024-01-01 --need "CTO equity vesting IP assignment" --top 3
```
Output:
```json
{"funnel": {"corpus": 5, "after_type": 3, "after_juris": 2,
            "excluded_needs_ocr": 0, "feedback_entries": 7, "returned": 2},
 "candidates": [{"doc_id": "...", "path": "...", "type": "...",
   "jurisdiction": "...", "date": "2025-03-12", "score": 0.14,
   "preference_boost": 0.06, "summary": "...",
   "clauses": [{"clause_id": "doc_xxx#c0", "heading": "...", "preferred": false}]}]}
```
Only the `clause_id`s returned here are valid `source_id`s for a draft.
`preferred: true` marks clauses the user chose in past drafts — favor them.

---

## validate.py
The gate. Pure. Reads a draft JSON from stdin. Exit 0 = pass, 1 = fail.
Optional flag: `--min-fidelity 0.6` (draft-vs-source word containment).

Input:
```json
{"clauses": [{"heading": "Equity", "text": "...", "source_id": "doc_xxx#c3"}]}
```
Output:
```json
{"ok": true, "clause_count": 1, "min_fidelity": 0.6, "unsourced": [],
 "unknown_source_ids": [], "low_fidelity": [],
 "message": "PASS: every clause traces to a real source and stays faithful to it."}
```
Fails if any clause has no `source_id`, a `source_id` that is not in the index
(the signature of an invented clause), or text whose content words do not come
from the cited source clause (`low_fidelity`, with the measured score). Client
names, dates and amounts may change; digits are ignored in the comparison.

---

## log_feedback.py
Append a decision to the ledger. Pure. The learning signal, not training.
Consumed by query.py on every retrieval (preference boost); always set `type`.

Input (stdin):
```json
{"type": "Employment Agreement", "jurisdiction": "Delaware",
 "client": "Nimbus Robotics", "chosen_label": "Client-protective",
 "source_doc_ids": ["doc_xxx"], "edited": true,
 "preferred_clause_ids": ["doc_xxx#c2"]}
```
Output:
```json
{"logged": true, "ledger": "~/.omplia/contract-forge/feedback.jsonl", "total_entries": 7}
```

---

## Swapping pieces for production

- **Embeddings**: `lib.cosine` is lexical. Replace it with precomputed embeddings
  (computed once at ingest, stored per document). `query.py` does not change.
- **AI classifier**: implement `ingest.ai_classify`. It runs once per document,
  offline, and the result is written to the audit file for human review.
- **Hosted public connector**: expose SEC EDGAR (or others) as an MCP server so
  the same tool works across agents, instead of ad hoc web search.
- **Hard enforcement**: run `validate.py` as a step outside the agent so the
  agent is not the final authority on whether a draft is accepted.
