#!/usr/bin/env python3
"""
validate.py — THE GATE. PURE, no AI. This is what keeps the agent honest.

Reads a draft JSON from stdin:
    {"clauses": [{"heading": "...", "text": "...", "source_id": "doc_xxx#c1"}]}

A draft is valid only if EVERY clause:
  1. cites a source_id that actually exists in the index, AND
  2. its text is faithful to that source clause (containment of the draft's
     content words in the source, >= --min-fidelity, default 0.6). Citing a
     real clause and writing invented text is rejected too.

Client names, dates and amounts may change (digits are ignored and the
threshold tolerates substitutions); whole-cloth rewording fails.

Exit code 0 = pass, 1 = fail. Result JSON printed to stdout either way.
The final deliverable must depend on this passing. Show its output to the user.
"""
from __future__ import annotations
import sys, json, argparse
import lib

MIN_FIDELITY = 0.6


def validate(draft: dict, min_fidelity: float = MIN_FIDELITY) -> dict:
    index = lib.load_index()
    known = lib.all_clause_ids(index)
    source_text = lib.clause_text_by_id(index)
    clauses = draft.get("clauses", [])
    unsourced, unknown, low_fidelity = [], [], []
    for i, c in enumerate(clauses):
        sid = c.get("source_id")
        head = c.get("heading", f"clause {i}")
        if not sid:
            unsourced.append(head)
        elif sid not in known:
            unknown.append({"heading": head, "source_id": sid})
        else:
            f = lib.fidelity(c.get("text", ""), source_text.get(sid, ""))
            if f < min_fidelity:
                low_fidelity.append({"heading": head, "source_id": sid,
                                     "fidelity": round(f, 2)})
    ok = not unsourced and not unknown and not low_fidelity and bool(clauses)
    return {
        "ok": ok,
        "clause_count": len(clauses),
        "min_fidelity": min_fidelity,
        "unsourced": unsourced,            # clauses with no provenance
        "unknown_source_ids": unknown,     # cited a source that does not exist (likely invented)
        "low_fidelity": low_fidelity,      # cited a real source but the text does not come from it
        "message": "PASS: every clause traces to a real source and stays faithful to it."
        if ok else "FAIL: draft contains clauses without valid, faithful provenance. Reject.",
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-fidelity", type=float, default=MIN_FIDELITY,
                    help="minimum draft-vs-source word containment (0-1)")
    args = ap.parse_args()
    raw = sys.stdin.read()
    try:
        draft = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "message": f"invalid draft JSON: {e}"}))
        sys.exit(1)
    result = validate(draft, args.min_fidelity)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["ok"] else 1)
