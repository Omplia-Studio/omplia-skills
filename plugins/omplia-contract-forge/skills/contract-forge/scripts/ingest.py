#!/usr/bin/env python3
"""
ingest.py — build the index from a folder of contracts. OFFLINE / batch.

Usage:
    python scripts/ingest.py <folder> [--rebuild]

Output (under the data dir, default ./.contractforge, see lib.DATA_DIR):
    index.jsonl                  one JSON record per document
    classification_audit.jsonl   every classification, for human review

The index is ALWAYS rebuilt from the folder (idempotent — doc_ids are
content-hashed and stable across runs, so feedback keeps pointing at the
same documents). --rebuild is accepted for compatibility.

This is the ONLY place AI may touch the engine. By default it uses the
deterministic baseline classifier (no AI) so the PoC runs offline. An AI
classifier can be plugged in at the marked seam; its result is written to
the audit file so a human can verify and correct before the index is trusted.
The runtime (query.py) never re-classifies.
"""
from __future__ import annotations
import sys, json, argparse
from pathlib import Path
import lib

SUPPORTED = {".docx", ".pdf", ".txt", ".md"}


def ai_classify(text: str):
    """Seam for an AI classifier. Returns None in the PoC (uses baseline).
    In production this calls the model ONCE per doc, offline, and the result
    is audited before being trusted."""
    return None


def ingest_folder(folder: Path, rebuild: bool) -> dict:
    folder = Path(folder)
    files = [p for p in folder.rglob("*") if p.suffix.lower() in SUPPORTED]
    records, audit = [], []
    for path in sorted(files):
        text, needs_ocr = lib.extract_text(path)
        doc_id = lib.doc_id_for(path, text)
        cls = ai_classify(text) or lib.classify_baseline(text)
        date, date_source = lib.extract_date(text, path.stat().st_mtime)
        clauses = lib.segment_clauses(doc_id, text)
        flat = " ".join(text.split())
        summary = flat[:160]
        rec = {
            "doc_id": doc_id,
            "path": str(path),
            "type": cls["type"],
            "jurisdiction": cls["jurisdiction"],
            "date": date,
            "date_source": date_source,
            "summary": summary,
            "search_text": flat[:5000],
            "needs_ocr": needs_ocr,
            "clause_count": len(clauses),
            "clauses": clauses,
            "classification_method": cls.get("method", "baseline"),
        }
        records.append(rec)
        audit.append({"doc_id": doc_id, "path": str(path), "type": cls["type"],
                      "jurisdiction": cls["jurisdiction"], "method": cls.get("method", "baseline"),
                      "date": date, "date_source": date_source, "needs_ocr": needs_ocr})

    lib.INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(lib.INDEX_PATH, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    with open(lib.AUDIT_PATH, "w") as f:
        for a in audit:
            f.write(json.dumps(a) + "\n")

    flagged = [r["path"] for r in records if r["needs_ocr"]]
    mtime_dated = [r["path"] for r in records if r["date_source"] == "file_mtime"]
    return {"ingested": len(records), "index": str(lib.INDEX_PATH),
            "needs_ocr": flagged, "dated_by_mtime": mtime_dated,
            "audit": str(lib.AUDIT_PATH)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()
    result = ingest_folder(args.folder, args.rebuild)
    print(json.dumps(result, indent=2))
    if result["needs_ocr"]:
        print(f"\n⚠  {len(result['needs_ocr'])} file(s) need OCR (scanned PDFs). "
              "They are indexed with empty text and will not surface as candidates "
              "until OCR is added.", file=sys.stderr)
    if result["dated_by_mtime"]:
        print(f"\n⚠  {len(result['dated_by_mtime'])} file(s) had no date in their text; "
              "their file-modified time was used. If this folder was copied, those "
              "dates are the copy date and recency ranking will be wrong for them. "
              "Confirm dates with the user.", file=sys.stderr)
