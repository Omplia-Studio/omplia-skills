#!/usr/bin/env python3
"""
lib.py — shared deterministic helpers for the ContractForge skill.

Everything here is PURE and deterministic: no LLM calls, no randomness.
Same input, same output. The only fuzzy step in the whole engine is the
optional AI classifier at ingest time, which is isolated, offline, and
written to an auditable file (see ingest.py). The runtime never re-classifies.
"""
from __future__ import annotations
import json, re, hashlib, math, os, datetime, unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# State (index, ledger, audit) is the user's persistent "brain". It must
# survive sessions and work the same under ANY agent (Claude, Codex, ...), so
# it lives in the user's home, NEVER inside the skill (may be read-only cache)
# and NEVER inside a git repo (the repo only distributes code, not state).
# Override with CONTRACTFORGE_DATA, e.g. to keep it next to the contracts.
DATA_DIR = Path(os.environ.get("CONTRACTFORGE_DATA") or (Path.home() / ".omplia" / "contract-forge"))
INDEX_PATH = DATA_DIR / "index.jsonl"
LEDGER_PATH = DATA_DIR / "feedback.jsonl"
AUDIT_PATH = DATA_DIR / "classification_audit.jsonl"


def fold(s: str) -> str:
    """Lowercase + strip accents (NFKD). 'Cláusula' and 'clausula' compare
    equal, so Spanish and English corpora share one matching path. All
    keyword/pattern matching below runs on folded text; patterns are written
    accent-free."""
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower())
                   if not unicodedata.combining(c))

# ----------------------------------------------------------------------
# Text extraction — ZERO required dependencies (Python stdlib only).
# .docx is a zip of XML: read word/document.xml directly. Paragraphs are
# walked in document order, so table-cell text is included where it appears.
# .pdf: if pdfplumber happens to be installed it is used (best quality);
# otherwise a stdlib fallback decompresses Flate content streams and pulls
# the text operators (Tj/TJ/'), which handles digitally generated PDFs.
# Scanned/image PDFs (or PDFs the fallback cannot read) yield no text and
# are flagged needs_ocr — flagged, never silently treated as empty contracts.
# ----------------------------------------------------------------------
_DOCX_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

def _docx_text(path: Path) -> str:
    """Read .docx with the stdlib only (zipfile + ElementTree)."""
    import zipfile
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    parts = []
    for p in root.iter(_DOCX_W + "p"):
        runs = []
        for node in p.iter():
            if node.tag == _DOCX_W + "t":
                runs.append(node.text or "")
            elif node.tag in (_DOCX_W + "br", _DOCX_W + "tab"):
                runs.append(" ")
        txt = "".join(runs).strip()
        if txt:
            parts.append(txt)
    return "\n".join(parts)

_PDF_STREAM_RE = re.compile(rb"<<(.*?)>>\s*stream\r?\n(.*?)endstream", re.S)
_PDF_TEXT_OP_RE = re.compile(
    rb"\(((?:[^()\\]|\\.)*)\)\s*(?:Tj|')"   # (text) Tj  /  (text) '
    rb"|\[((?:[^\]\\]|\\.)*)\]\s*TJ"        # [(a) -120 (b)] TJ
    rb"|(T\*|TD|Td|TL|BT)", re.S)
_PDF_STR_RE = re.compile(rb"\(((?:[^()\\]|\\.)*)\)")
_PDF_ESC = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f",
            b"(": b"(", b")": b")", b"\\": b"\\"}

def _pdf_unescape(raw: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(raw):
        c = raw[i:i + 1]
        if c == b"\\" and i + 1 < len(raw):
            nxt = raw[i + 1:i + 2]
            if nxt.isdigit():  # octal \ddd
                j = i + 1
                while j < len(raw) and j < i + 4 and raw[j:j + 1].isdigit():
                    j += 1
                out += bytes([int(raw[i + 1:j], 8) & 0xFF]); i = j; continue
            out += _PDF_ESC.get(nxt, nxt); i += 2; continue
        out += c; i += 1
    return bytes(out)

def _pdf_text_fallback(path: Path) -> str:
    """Stdlib-only PDF text extraction for digitally generated PDFs.
    Returns "" when nothing readable is found (encrypted, scanned, exotic
    encodings) — the caller flags that as needs_ocr."""
    import zlib
    data = path.read_bytes()
    lines: list[str] = []
    for hdr, body in _PDF_STREAM_RE.findall(data):
        body = body.rstrip(b"\r\n")
        if b"FlateDecode" in hdr:
            try:
                body = zlib.decompress(body)
            except Exception:
                continue
        elif b"Filter" in hdr:
            continue  # unsupported filter (DCT/CCITT images, etc.)
        if b"BT" not in body:
            continue  # no text objects in this stream
        line = []
        for m in _PDF_TEXT_OP_RE.finditer(body):
            if m.group(3):  # positioning op => line break
                if line:
                    lines.append("".join(line)); line = []
                continue
            if m.group(1) is not None:
                chunks = [m.group(1)]
            else:
                chunks = [s for s in _PDF_STR_RE.findall(m.group(2))]
            for raw in chunks:
                line.append(_pdf_unescape(raw).decode("latin-1", errors="ignore"))
        if line:
            lines.append("".join(line))
    text = "\n".join(l.strip() for l in lines if l.strip())
    # Sanity check: mostly printable, else treat as unreadable.
    if text and sum(ch.isprintable() or ch == "\n" for ch in text) / len(text) < 0.8:
        return ""
    return text

def extract_text(path: Path) -> tuple[str, bool]:
    """Return (text, needs_ocr). needs_ocr=True means a scanned/image PDF or a
    PDF that could not be read without OCR. No third-party install required."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="ignore"), False
    if ext == ".docx":
        return _docx_text(path), False
    if ext == ".pdf":
        try:
            import pdfplumber  # optional upgrade, never required
        except ImportError:
            pdfplumber = None
        if pdfplumber is not None:
            with pdfplumber.open(str(path)) as pdf:
                joined = "\n".join((page.extract_text() or "") for page in pdf.pages).strip()
        else:
            joined = _pdf_text_fallback(path).strip()
        return joined, (len(joined) < 40)  # almost no text => likely scanned
    raise RuntimeError(f"unsupported file type: {ext}")


def doc_id_for(path: Path, text: str) -> str:
    h = hashlib.sha1()
    h.update(str(Path(path).name).encode())
    h.update(text.encode("utf-8", errors="ignore"))
    return "doc_" + h.hexdigest()[:12]


# ----------------------------------------------------------------------
# Clause segmentation. Deterministic, heading-driven, with a paragraph
# fallback. Each clause carries a stable id and its character offset so a
# draft can always be traced back to the exact source span (provenance).
# ----------------------------------------------------------------------
HEADING_RE = re.compile(
    r"^\s*(?:"
    r"(?:ARTICLE|SECTION|CLAUSE)\s+[\dIVXLC]+\.?"                                   # ARTICLE I, SECTION 3
    r"|(?:ART[IÍ]CULO|CL[AÁ]USULA|ESTIPULACI[OÓ]N|ANEXO)\s+(?:[\dIVXLC]+|\w+)[.\-–—: ]"  # CLÁUSULA PRIMERA.- / ARTÍCULO 3.
    r"|(?:PRIMER|SEGUND|TERCER|CUART|QUINT|SEXT|S[EÉ]PTIM|OCTAV|NOVEN|D[EÉ]CIM)\w*\s*[.\-–—:]"  # PRIMERA.- Objeto
    r"|\d+(?:\.\d+)*\.?\s+[A-ZÁÉÍÓÚÑÜ]"                # 1. Term  / 2.1 Salario
    r"|[A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜ0-9 ,&'\-]{3,40}$"      # ALL CAPS HEADING (incl. accents)
    r")",
    re.MULTILINE,
)

def segment_clauses(doc_id: str, text: str) -> list[dict]:
    matches = list(HEADING_RE.finditer(text))
    clauses = []
    if matches:
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block = text[start:end].strip()
            if not block:
                continue
            heading = block.splitlines()[0].strip()[:80]
            clauses.append(_mk_clause(doc_id, len(clauses), heading, block, start))
    if not clauses:  # fallback: paragraphs
        for i, para in enumerate(p for p in re.split(r"\n\s*\n", text) if p.strip()):
            off = text.find(para)
            heading = para.strip().splitlines()[0][:60]
            clauses.append(_mk_clause(doc_id, i, heading, para.strip(), off))
    return clauses

def _mk_clause(doc_id, idx, heading, body, offset):
    return {
        "clause_id": f"{doc_id}#c{idx}",
        "heading": heading,
        "text": body,
        "offset": offset,
    }


# ----------------------------------------------------------------------
# Baseline classifier (deterministic, keyword-driven). This is the audited
# fallback. The AI classifier in ingest.py can override it, but its output
# is written to an audit file so a human can correct it. Runtime trusts the
# stored value and never re-runs this.
# ----------------------------------------------------------------------
# Keywords are written accent-free and matched against folded text, so one
# list covers both "Cláusula de confidencialidad" and "confidentiality clause".
TYPE_KEYWORDS = {
    "Employment Agreement": ["employment", "employee", "cto", "salary", "at-will", "title and duties",
                             "contrato de trabajo", "trabajador", "salario", "jornada", "periodo de prueba"],
    "Credit Agreement": ["credit agreement", "lender", "borrower", "principal", "interest rate", "facility",
                         "prestamo", "prestamista", "prestatario"],
    "License Agreement": ["license", "licensee", "licensor", "royalt", "grant of license",
                          "licencia", "licenciante", "licenciatario", "regalias"],
    "Lease": ["lease", "lessor", "lessee", "premises", "rent",
              "arrendamiento", "arrendador", "arrendatario", "renta", "inmueble"],
    "Merger Agreement": ["merger", "acquir", "surviving corporation", "closing",
                         "fusion", "absorbente", "absorbida"],
    "Master Services": ["master services", "statement of work", "services agreement", "deliverables",
                        "prestacion de servicios", "prestador", "entregables"],
    "Indemnification": ["indemnif", "hold harmless", "indemnitee",
                        "indemniza", "mantener indemne"],
    "NDA": ["non-disclosure", "nondisclosure", "confidentiality agreement",
            "acuerdo de confidencialidad", "informacion confidencial", "secreto empresarial"],
    "Shareholders Agreement": ["shareholders agreement", "drag along", "tag along",
                               "pacto de socios", "participaciones sociales", "junta de socios"],
    "Operating Agreement": ["operating agreement", "limited liability company", "membership interest", "llc"],
    "Power of Attorney": ["power of attorney", "attorney-in-fact",
                          "poder notarial", "otorga poder", "apoderado"],
}

# Two tiers per jurisdiction: a STRONG governing-law phrasing and a WEAK bare
# mention. All strong patterns are tried first, so a California contract that
# mentions a "New York office" is not misfiled. Patterns are accent-free
# (matched against folded text).
JURIS_PATTERNS = [
    ("Delaware", r"laws of (the )?state of delaware", r"\bdelaware\b"),
    ("New York", r"laws of (the )?state of new york", r"\bnew york\b"),
    ("California", r"laws of (the )?state of california", r"\bcalifornia\b"),
    ("Texas", r"laws of (the )?state of texas", r"\btexas\b"),
    ("England & Wales", r"england and wales|english law", r"\bengland\b"),
    ("Spain", r"legislacion espanola|derecho espanol|ley(es)? espanolas?|laws of spain|spanish law",
     r"\bespana\b|\bspain\b"),
]

def classify_baseline(text: str) -> dict:
    low = fold(text)
    best_type, best_hits = "Unknown", 0
    for t, kws in TYPE_KEYWORDS.items():
        hits = sum(low.count(k) for k in kws)
        if hits > best_hits:
            best_type, best_hits = t, hits
    juris = "Unknown"
    for j, strong, _weak in JURIS_PATTERNS:
        if re.search(strong, low):
            juris = j
            break
    else:
        for j, _strong, weak in JURIS_PATTERNS:
            if re.search(weak, low):
                juris = j
                break
    m = re.search(r"\b(20\d{2})\b", text)
    year = m.group(1) if m else None
    return {"type": best_type, "jurisdiction": juris, "year": year, "method": "baseline"}


_MONTHS = {
    # English
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    # Spanish (accent-free; matched on folded text)
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
_MONTH_ALT = "|".join(_MONTHS)

def extract_date(text: str, fallback_mtime: float) -> tuple[str, str]:
    """Return (iso_date, source). source is "text" when a date was found in the
    document, "file_mtime" when we fell back to the file's modified time.

    ALWAYS returns ISO YYYY-MM-DD so date filtering and recency ranking compare
    consistently (textual dates like "March 12, 2025" used to break both).
    Callers should surface "file_mtime" docs to the user: a copied folder makes
    every mtime the copy date, which silently corrupts recency."""
    low = fold(text)
    # English: March 12, 2025
    m = re.search(rf"\b({_MONTH_ALT})\s+(\d{{1,2}}),?\s+(20\d{{2}})\b", low)
    if m:
        return f"{m.group(3)}-{_MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}", "text"
    # Spanish: 12 de marzo de 2025
    m = re.search(rf"\b(\d{{1,2}})\s+de\s+({_MONTH_ALT})\s+(?:de\s+|del\s+)?(20\d{{2}})\b", low)
    if m:
        return f"{m.group(3)}-{_MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}", "text"
    # ISO: 2025-03-12
    m = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if m:
        return m.group(0), "text"
    # Numeric dd/mm/yyyy (European default; mm/dd only when unambiguous)
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", text)
    if m:
        a, b, year = int(m.group(1)), int(m.group(2)), m.group(3)
        if a <= 31 and b <= 12:
            day, month = a, b
        elif a <= 12 and b <= 31:
            day, month = b, a
        else:
            day, month = None, None
        if day:
            return f"{year}-{month:02d}-{day:02d}", "text"
    return datetime.date.fromtimestamp(fallback_mtime).isoformat(), "file_mtime"


# ----------------------------------------------------------------------
# Lexical similarity (pure). Stand-in for embeddings. Same contract,
# same query => same score. Swap this for precomputed embeddings in prod;
# the query.py interface does not change.
# ----------------------------------------------------------------------
_WORD = re.compile(r"[a-z0-9]+")

def _tf(text: str) -> dict:
    counts: dict[str, int] = {}
    for w in _WORD.findall(fold(text)):
        if len(w) > 2:
            counts[w] = counts.get(w, 0) + 1
    return counts

def cosine(a: str, b: str) -> float:
    ta, tb = _tf(a), _tf(b)
    if not ta or not tb:
        return 0.0
    common = set(ta) & set(tb)
    num = sum(ta[w] * tb[w] for w in common)
    da = math.sqrt(sum(v * v for v in ta.values()))
    db = math.sqrt(sum(v * v for v in tb.values()))
    return num / (da * db) if da and db else 0.0


# ----------------------------------------------------------------------
# Index I/O
# ----------------------------------------------------------------------
def load_index() -> list[dict]:
    if not INDEX_PATH.exists():
        return []
    out = []
    for line in INDEX_PATH.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out

def all_clause_ids(index: list[dict]) -> set[str]:
    ids = set()
    for d in index:
        for c in d.get("clauses", []):
            ids.add(c["clause_id"])
    return ids

def clause_text_by_id(index: list[dict]) -> dict[str, str]:
    out = {}
    for d in index:
        for c in d.get("clauses", []):
            out[c["clause_id"]] = c.get("text", "")
    return out


# ----------------------------------------------------------------------
# Feedback → ranking weights. This closes the learning loop: log_feedback.py
# appends decisions, and query.py calls preference_weights() so documents and
# clauses the user chose before rank higher next time. Pure arithmetic over a
# readable ledger — delete a ledger line and its influence is gone.
# ----------------------------------------------------------------------
def load_feedback() -> list[dict]:
    if not LEDGER_PATH.exists():
        return []
    out = []
    for line in LEDGER_PATH.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a hand-edited bad line must not kill retrieval
    return out

def preference_weights(feedback: list[dict], type_: str | None = None):
    """Return (doc_weights, preferred_clause_ids). A doc chosen as a draft
    source earns +1; a doc whose clause was explicitly preferred earns +2 and
    the clause is flagged. When type_ is given, only same-type decisions count
    (employment feedback must not skew lease retrieval)."""
    doc_weights: dict[str, int] = {}
    preferred: set[str] = set()
    for rec in feedback:
        if type_ and rec.get("type") and rec["type"] != type_:
            continue
        for doc_id in rec.get("source_doc_ids", []):
            doc_weights[doc_id] = doc_weights.get(doc_id, 0) + 1
        for cid in rec.get("preferred_clause_ids", []):
            preferred.add(cid)
            doc_id = cid.split("#")[0]
            doc_weights[doc_id] = doc_weights.get(doc_id, 0) + 2
    return doc_weights, preferred

def preference_boost(weight: int) -> float:
    """Map a raw preference weight to a bounded score bonus. Capped so
    accumulated history can tilt a near-tie but never outvote relevance."""
    return min(0.15, 0.03 * weight)


# ----------------------------------------------------------------------
# Draft-vs-source fidelity. The provenance gate's second half: citing a real
# clause_id is not enough — the drafted text must actually come from it.
# Containment of the draft's content words in the source clause. Tolerates
# client names, dates and amounts changing; catches whole-cloth invention.
# ----------------------------------------------------------------------
def fidelity(draft_text: str, source_text: str) -> float:
    draft = {w for w in _tf(draft_text) if not w.isdigit()}
    source = {w for w in _tf(source_text) if not w.isdigit()}
    if not draft:
        return 0.0
    return len(draft & source) / len(draft)
