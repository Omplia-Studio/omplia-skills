---
name: contract-forge
description: >-
  Draft a new contract from a folder of existing contracts plus public filings,
  grounded only in real sources, never invented. Use this skill WHENEVER the user
  wants to create, draft, assemble, or template a contract or agreement from their
  existing documents, find and reuse clauses, or generate a contract for a new
  client based on prior contracts. Trigger it for any mention of "draft a
  contract", "use my old contracts as a template", "build an agreement for this
  client", "find a clause", or pointing the agent at a folder of contracts. Always
  prefer this skill over free-form drafting when contract documents are involved,
  because it enforces provenance and blocks hallucinated clauses.
---

# Omplia ContractForge

Assemble the best possible contract draft for a new client from a folder of
existing contracts (.docx, .pdf, .txt, .md), with every clause traceable to a
real source. The deterministic engine does retrieval, filtering, ranking and
validation. You (the agent) do the judgement: asking questions and drafting on
top of retrieved material. You never invent legal content.

The corpus may be in English, Spanish or both: classification, jurisdiction
detection (incl. `Spain`), dates ("12 de marzo de 2025") and clause headings
(CLÁUSULA PRIMERA.-) are handled bilingually, and accents are normalized for
matching.

## The one rule

You are not the source of legal truth. Every clause in any draft you produce
must come from a clause that exists in the index, cited by its `source_id`.
A draft is only finished after `validate.py` returns `ok: true`. If it returns
`ok: false`, the draft is rejected. Show the validator output to the user. Do
not paraphrase a clause into something the source does not say.

## Setup

Zero required dependencies: .docx and .pdf extraction runs on the Python
standard library, so the engine works out of the box under any agent. If
`pdfplumber` is installed it is picked up automatically for higher-quality PDF
extraction; you may install it (`pip install pdfplumber`) when the environment
allows, but never treat it as a requirement and never block the workflow on
it. PDFs the stdlib fallback cannot read are flagged `needs_ocr`, never
silently mis-read.

State (index, ledger, audit) is the user's persistent brain. It must survive
sessions and agent switches, so it lives OUTSIDE the skill and outside any git
repo: by default in `~/.omplia/contract-forge`. Set `CONTRACTFORGE_DATA` to
override (for example, next to the user's contracts). Never commit state to a
repository.

All scripts print JSON to stdout.

## Workflow

Follow these stages in order. Stages 1, 3 and 6 require the human. Do not skip a
human checkpoint.

### 1. Build or refresh the index (once per folder)
Ask the user which folder holds their contracts, then run:
```
python scripts/ingest.py <folder> --rebuild
```
Report how many were ingested and **surface any files flagged `needs_ocr`** (scanned
PDFs). Those are not searchable until OCR is added. Tell the user, do not pretend
they were read. Also **surface `dated_by_mtime`**: those files had no date in their
text, so the file-modified time was used — if the folder was copied, that date is
the copy date and recency ranking is wrong for them; ask the user to confirm. The classification of each file is written to
`classification_audit.jsonl` in the data dir. If the user cares about precision, offer to
review that file with them and correct any wrong type or jurisdiction before
trusting the index.

### 2. Define the need
Capture, in plain terms: document type, jurisdiction, the new client, and what
they need. Map type and jurisdiction to the exact values used in the index
(see `references/contracts.md` for the controlled vocabulary).

### 3. Retrieve candidates (deterministic, no guessing)
```
python scripts/query.py --type "<type>" --juris "<jurisdiction>" \
    --date-after 2024-01-01 --need "<plain description of need>" --top 3
```
Show the `funnel` (how many in corpus, after type, after jurisdiction, excluded
for OCR, returned) so the user sees the narrowing. Present the candidates with
their date and source path. Candidates carry a `preference_boost` and clauses a
`preferred: true` flag when past feedback favored them — prefer those clauses
when drafting, and say so. Let the user confirm or adjust which ones to build
from. The `clause_id`s in the result are the only valid `source_id`s you may
cite later.

### 4. Clarify before drafting
Ask sharp, specific questions targeted at the gaps that would block a good draft
for this type and jurisdiction. If an answer that matters is missing or vague,
ask again or flag the gap explicitly. Do not fill a material gap with an
assumption. If you must note a default, label it clearly for the user.

### 5. Assemble alternatives
Produce two alternative drafts (for example market-standard and
client-protective). Build each clause from the chosen candidates' clauses. For
every clause you output, record the `source_id` of the source clause it is based
on. Keep the new client's details where they belong. Mark the output clearly as
a working draft, not a final contract.

### 6. Validate, then human review
For each alternative, write it as JSON and run the gate:
```
echo '<draft-json>' | python scripts/validate.py
```
The draft JSON shape:
```json
{"clauses": [{"heading": "...", "text": "...", "source_id": "doc_xxx#c3"}]}
```
If `ok` is false, fix the offending clauses and re-run. The gate checks two
things: every clause cites a real `source_id`, AND its text is faithful to that
source clause (`low_fidelity` lists clauses whose words do not come from the
cited source — rewriting a real clause into something else fails too). Client
names, dates and amounts may change. Only present drafts that passed. The user
reviews, edits and chooses. The decision is theirs.

### 7. Log the decision (the learning signal)
After the user picks and edits, record what happened:
```
echo '{"type":"...","jurisdiction":"...","client":"...","chosen_label":"...","source_doc_ids":["doc_xxx"],"edited":true,"preferred_clause_ids":["doc_xxx#c2"]}' \
    | python scripts/log_feedback.py
```
This appends to the feedback ledger (`feedback.jsonl` in the data dir). It is a
transparent, reversible record, not model training — and it is LIVE: `query.py`
reads it on every retrieval and boosts the documents and clauses chosen before
(same-type only). Always fill `type` and `preferred_clause_ids`; that is how the
corpus gets better with every contract. Skipping this step throws the learning
away.

## Public sources (optional)

When the internal corpus is thin or the user wants to compare against market
standard, you may search public filings (for example SEC EDGAR material-contract
exhibits) using your web tools. Treat anything public as **reference only**:
never merge it silently into the index, always show its origin, and keep it
visibly separate from the user's internal, approved material.

## What is deterministic vs what is you

The scripts are pure code: ingest, filter, rank, validate, log. They do not call
a model and they are covered by `tests/`. You provide the judgement: questions
and drafting. The split is the whole point. If you ever feel the urge to answer
a retrieval or validation question from your own reading instead of running the
script, run the script. The script is the source of truth.

See `references/contracts.md` for the exact input and output of every script.
