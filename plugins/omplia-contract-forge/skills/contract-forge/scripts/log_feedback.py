#!/usr/bin/env python3
"""
log_feedback.py — append a decision to the feedback ledger. PURE, no AI.

This is the learning signal. NOT fine-tuning. query.py reads this ledger on
every retrieval and boosts documents and clauses the user chose before
(see lib.preference_weights), so the corpus genuinely improves with use.
It is transparent (a readable file) and reversible (delete a line and its
influence is gone). Always include "type" in the record — preferences only
apply to same-type queries.

Reads a JSON record from stdin, e.g.:
    {"type": "Employment Agreement", "jurisdiction": "Delaware",
     "client": "Nimbus Robotics", "chosen_label": "Client-protective",
     "source_doc_ids": ["doc_abc"], "edited": true,
     "preferred_clause_ids": ["doc_abc#c2"]}

Appends it to memory/feedback.jsonl with a timestamp.
"""
from __future__ import annotations
import sys, json, datetime
import lib


def log(record: dict) -> dict:
    record = dict(record)
    record["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    lib.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(lib.LEDGER_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")
    count = sum(1 for _ in open(lib.LEDGER_PATH))
    return {"logged": True, "ledger": str(lib.LEDGER_PATH), "total_entries": count}


if __name__ == "__main__":
    rec = json.loads(sys.stdin.read())
    print(json.dumps(log(rec), indent=2))
