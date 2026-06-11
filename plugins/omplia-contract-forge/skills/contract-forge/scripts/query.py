#!/usr/bin/env python3
"""
query.py — two-stage retrieval. PURE, no AI.

Stage 1 (hard filter, deterministic): type, jurisdiction, date.
Stage 2 (rank): lexical similarity to the stated need + preference boost
from the feedback ledger (docs/clauses the user chose before rank higher),
then recency as tiebreaker. This is where the corpus "learns": transparent
arithmetic over feedback.jsonl, not a model.

Usage:
    python scripts/query.py --type "Employment Agreement" --juris Delaware \
        --need "CTO equity vesting strong IP assignment" --top 3

Output (JSON to stdout):
    { funnel: {corpus, after_type, after_juris, excluded_needs_ocr,
               feedback_entries, returned},
      candidates: [ {doc_id, path, type, jurisdiction, date, score,
                     preference_boost, summary,
                     clauses:[{clause_id, heading, preferred}]} ] }
"""
from __future__ import annotations
import argparse, json
import lib


def query(type_=None, juris=None, date_after=None, need="", top=3):
    index = lib.load_index()
    corpus = len(index)

    # Stage 1: hard filters. A document that meets the criteria is NEVER dropped.
    after_type = [d for d in index if not type_ or d["type"] == type_]
    after_juris = [d for d in after_type
                   if not juris or juris == "Any" or d["jurisdiction"] == juris]
    after_date = [d for d in after_juris
                  if not date_after or (d.get("date", "") >= date_after)]
    survivors = [d for d in after_date if not d.get("needs_ocr")]
    excluded_ocr = len(after_date) - len(survivors)

    # Stage 2: similarity + preference boost, recency as tiebreaker.
    feedback = lib.load_feedback()
    doc_weights, preferred_clauses = lib.preference_weights(feedback, type_)

    def parts(d):
        sim = lib.cosine(need, d.get("search_text", d.get("summary", ""))) if need else 0.0
        boost = lib.preference_boost(doc_weights.get(d["doc_id"], 0))
        return round(sim, 4), round(boost, 4)

    ranked = sorted(survivors,
                    key=lambda d: (sum(parts(d)), d.get("date", "")),
                    reverse=True)[:top]

    candidates = []
    for d in ranked:
        sim, boost = parts(d)
        candidates.append({
            "doc_id": d["doc_id"],
            "path": d["path"],
            "type": d["type"],
            "jurisdiction": d["jurisdiction"],
            "date": d["date"],
            "score": sim if need else None,
            "preference_boost": boost,
            "summary": d.get("summary", ""),
            "clauses": [{"clause_id": c["clause_id"], "heading": c["heading"],
                         "preferred": c["clause_id"] in preferred_clauses}
                        for c in d.get("clauses", [])],
        })

    return {
        "funnel": {"corpus": corpus, "after_type": len(after_type),
                   "after_juris": len(after_juris),
                   "excluded_needs_ocr": excluded_ocr,
                   "feedback_entries": len(feedback),
                   "returned": len(candidates)},
        "candidates": candidates,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", dest="type_", default=None)
    ap.add_argument("--juris", default=None)
    ap.add_argument("--date-after", default=None)
    ap.add_argument("--need", default="")
    ap.add_argument("--top", type=int, default=3)
    args = ap.parse_args()
    print(json.dumps(query(args.type_, args.juris, args.date_after, args.need, args.top), indent=2))
