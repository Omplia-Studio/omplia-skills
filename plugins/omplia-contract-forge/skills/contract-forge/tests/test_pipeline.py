#!/usr/bin/env python3
"""
End-to-end test of the deterministic engine. No AI involved.
Run: python tests/test_pipeline.py   (or: pytest tests/)

Proves the parts that MUST be reliable actually are:
- ingest reads docx + pdf + Spanish txt
- all dates are normalized to ISO (filtering and recency depend on it)
- the hard filter never returns the wrong type or jurisdiction
- recency ordering holds
- the feedback ledger boosts previously chosen documents (the learning loop)
- the validation gate fails closed on invented, unsourced or unfaithful clauses
"""
import os, sys, json, re, tempfile
from pathlib import Path

# State must never be written inside the skill folder; tests get their own dir.
os.environ.setdefault(
    "CONTRACTFORGE_DATA", os.path.join(tempfile.gettempdir(), "cf_test_data"))

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import lib, ingest, query, validate, log_feedback  # noqa


def setup():
    if lib.LEDGER_PATH.exists():
        lib.LEDGER_PATH.unlink()  # stale feedback would skew ranking tests
    res = ingest.ingest_folder(ROOT / "sample_contracts", rebuild=True)
    assert res["ingested"] >= 5, "should ingest the sample docx, pdf and Spanish txt"
    return res


def setup_module(_=None):  # pytest hook; the __main__ runner calls setup() itself
    setup()


def test_ingest_reads_docx_and_pdf():
    idx = lib.load_index()
    exts = {Path(d["path"]).suffix for d in idx}
    assert ".docx" in exts and ".pdf" in exts, f"expected docx and pdf, got {exts}"


def test_dates_are_iso():
    for d in lib.load_index():
        assert re.fullmatch(r"20\d{2}-\d{2}-\d{2}", d["date"]), \
            f"non-ISO date {d['date']!r} in {d['path']}"
        assert d["date_source"] in ("text", "file_mtime")


def test_hard_filter_is_exact():
    r = query.query(type_="Employment Agreement", juris="Delaware", need="cto equity")
    for c in r["candidates"]:
        assert c["type"] == "Employment Agreement"
        assert c["jurisdiction"] == "Delaware"
    # the New York credit agreement and the California employment must be excluded
    paths = " ".join(c["path"] for c in r["candidates"])
    assert "credit" not in paths and "CA" not in paths
    assert "excluded_needs_ocr" in r["funnel"]


def test_recency_ranks_newest_first():
    r = query.query(type_="Employment Agreement", juris="Delaware", need="cto equity ip non-compete")
    dates = [c["date"] for c in r["candidates"]]
    assert dates and dates[0].startswith("2025"), f"newest should rank first, got {dates}"


def test_spanish_contract_classified():
    idx = lib.load_index()
    es = next(d for d in idx if "servicios_meridia" in d["path"])
    assert es["type"] == "Master Services", es["type"]
    assert es["jurisdiction"] == "Spain", es["jurisdiction"]
    assert es["date"] == "2025-02-05", es["date"]
    assert es["date_source"] == "text"
    assert es["clause_count"] >= 5, f"CLÁUSULA headings should segment, got {es['clause_count']}"


def test_feedback_boost_prefers_chosen_doc():
    idx = lib.load_index()
    older = next(d for d in idx
                 if d["type"] == "Employment Agreement" and d["date"].startswith("2023"))
    try:
        # With no need and no feedback, recency puts 2025 first.
        before = query.query(type_="Employment Agreement", juris="Delaware")
        assert before["candidates"][0]["date"].startswith("2025")
        # The user chose the 2023 doc in a past draft; it must outrank recency now.
        log_feedback.log({"type": "Employment Agreement", "jurisdiction": "Delaware",
                          "client": "Test", "chosen_label": "Market",
                          "source_doc_ids": [older["doc_id"]],
                          "preferred_clause_ids": [older["clauses"][0]["clause_id"]]})
        after = query.query(type_="Employment Agreement", juris="Delaware")
        assert after["candidates"][0]["doc_id"] == older["doc_id"], \
            "preferred doc should rank first"
        assert after["candidates"][0]["preference_boost"] > 0
        assert after["candidates"][0]["clauses"][0]["preferred"] is True
        assert after["funnel"]["feedback_entries"] == 1
        # Same-type scoping: feedback must not leak into other types.
        lease = query.query(type_="Credit Agreement")
        for c in lease["candidates"]:
            assert c["preference_boost"] == 0.0
    finally:
        if lib.LEDGER_PATH.exists():
            lib.LEDGER_PATH.unlink()


def test_gate_passes_real_source():
    idx = lib.load_index()
    de = next(d for d in idx if d["type"] == "Employment Agreement" and d["jurisdiction"] == "Delaware")
    src = de["clauses"][3]
    # Faithful reuse: same clause text with client details substituted.
    draft = {"clauses": [{"heading": src["heading"],
                          "text": src["text"].replace("Acme", "Nimbus Robotics"),
                          "source_id": src["clause_id"]}]}
    res = validate.validate(draft)
    assert res["ok"] is True, res


def test_gate_fails_invented_or_unsourced():
    bad = {"clauses": [
        {"heading": "Invented", "text": "x", "source_id": "doc_FAKE#c9"},
        {"heading": "Unsourced", "text": "y"},
    ]}
    res = validate.validate(bad)
    assert res["ok"] is False
    assert res["unknown_source_ids"] and res["unsourced"]


def test_gate_fails_real_source_invented_text():
    idx = lib.load_index()
    de = next(d for d in idx if d["type"] == "Employment Agreement" and d["jurisdiction"] == "Delaware")
    real_id = de["clauses"][3]["clause_id"]
    draft = {"clauses": [{"heading": "Arbitration",
                          "text": "Any dispute shall be settled by binding arbitration "
                                  "in Geneva under ICC rules with three arbitrators.",
                          "source_id": real_id}]}
    res = validate.validate(draft)
    assert res["ok"] is False, "real source_id + invented text must fail"
    assert res["low_fidelity"] and res["low_fidelity"][0]["source_id"] == real_id


def test_pdf_and_docx_read_without_third_party_libs():
    """Zero-dependency guarantee: docx and pdf extraction must work with the
    stdlib alone. Simulate a bare environment by blocking the optional libs."""
    import importlib, sys
    blocked = {m: sys.modules.pop(m, None) for m in ("pdfplumber", "docx")}
    sys.modules["pdfplumber"] = None  # forces ImportError on import
    sys.modules["docx"] = None
    try:
        importlib.reload(lib)
        pdf_text, pdf_ocr = lib.extract_text(ROOT / "sample_contracts" / "employment_globex_CA_2024.pdf")
        assert "EMPLOYMENT" in pdf_text and not pdf_ocr, "stdlib PDF fallback failed"
        docx_text, _ = lib.extract_text(ROOT / "sample_contracts" / "employment_acme_DE_2025.docx")
        assert "Chief Technology Officer" in docx_text, "stdlib docx reader failed"
    finally:
        for m, mod in blocked.items():
            if mod is not None: sys.modules[m] = mod
            else: sys.modules.pop(m, None)
        importlib.reload(lib)


if __name__ == "__main__":
    setup()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
