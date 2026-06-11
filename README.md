# Omplia ContractForge

A Claude/agent skill that drafts a new contract from a folder of your existing
contracts, with **every clause traceable to a real source** and hallucinated
clauses blocked by a validation gate. Bilingual (English/Spanish). Zero
required dependencies.

## How it works

**Deterministic engine** (`scripts/`, pure Python, tested, no AI): ingest and
extract `.docx` / `.pdf` / `.txt` / `.md`, classify type and jurisdiction
(EN/ES), hard-filter and rank candidates, enforce provenance (a draft clause
must cite a real indexed clause AND stay faithful to its text), and keep a
feedback ledger that live-boosts future retrieval.

**The agent** (guided by `SKILL.md`): asks the clarifying questions and drafts
on top of retrieved clauses. It never invents legal content — `validate.py`
rejects any clause not tied to a real source.

## Install as a Claude plugin

```
/plugin marketplace add Omplia-Studio/omplia-skills
/plugin install omplia-contract-forge@omplia-skills
```

Works in Claude Code and any Claude surface that supports plugins. The skill
triggers automatically on requests like "draft a contract for this client from
my old contracts".

**Claude on the web, Claude Desktop and Cowork**: open the Customize menu →
**Plugins** → Personal plugins → **+** → **Add marketplace** → add this GitHub
repo (`Omplia-Studio/omplia-skills`), then install **Omplia ContractForge**.
Skills installed through a plugin work across chat, Desktop and Cowork.

## Install in other agents (Codex, Gemini CLI, Cursor, Copilot)

`SKILL.md` is an open standard (Agent Skills), and this skill is deliberately
portable: zero dependencies, and state lives in `~/.omplia/contract-forge`
regardless of which agent runs it. Install = copy the skill folder into your
agent's skills directory:

```
git clone --depth 1 https://github.com/Omplia-Studio/omplia-skills /tmp/omplia-skills
SRC=/tmp/omplia-skills/plugins/omplia-contract-forge/skills/contract-forge

cp -r "$SRC" ~/.codex/skills/contract-forge      # Codex CLI
cp -r "$SRC" ~/.gemini/skills/contract-forge     # Gemini CLI
cp -r "$SRC" .cursor/skills/contract-forge       # Cursor (per project)
```

Project-scoped variants (`.codex/skills/`, `.gemini/skills/`) work the same
way.

## Run the engine directly (any agent, or no AI at all)

No installs needed — Python 3.10+ standard library only:

```
python tests/test_pipeline.py        # 10/10 should pass
python scripts/ingest.py sample_contracts --rebuild
python scripts/query.py --type "Employment Agreement" --juris Delaware \
    --need "CTO equity vesting IP assignment non-compete" --top 3
echo '{"clauses":[{"heading":"x","text":"y","source_id":"doc_FAKE#c1"}]}' \
    | python scripts/validate.py     # FAIL: invented source
```

(Paths above relative to `plugins/omplia-contract-forge/skills/contract-forge/`.)

Optional: `pip install pdfplumber` upgrades PDF extraction quality for complex
layouts. Never required — the stdlib fallback reads digitally generated PDFs,
and anything unreadable is flagged `needs_ocr`, never silently mis-read.

## Where state lives (the "brain")

This repository only distributes code. The runtime state — index, feedback
ledger, classification audit — is the user's persistent brain: it survives
sessions and agent switches and is **never stored in this repo or inside the
installed skill**. Default location: `~/.omplia/contract-forge`. Override with
the `CONTRACTFORGE_DATA` environment variable.

## What is real and what is a stand-in

Production-shaped: folder ingest (docx/pdf/txt/md), bilingual EN/ES
classification with ISO date normalization, two-stage filtering, provenance
ids, the two-part validation gate (source must exist AND the draft text must
be faithful to it), the live feedback boost, the audit file.

Stand-ins with a clean seam to swap (see `references/contracts.md`): lexical
ranking instead of embeddings; keyword classifier baseline instead of an AI
model; public filings via the agent's web tools instead of a hosted connector.

Known gap: scanned (image) PDFs need OCR. They are flagged `needs_ocr` at
ingest and excluded from candidates — OCR itself is not included.

## Layout

```
.claude-plugin/marketplace.json          this repo is an installable marketplace
plugins/omplia-contract-forge/
  .claude-plugin/plugin.json
  skills/contract-forge/
    SKILL.md                 the loop and guardrails the agent follows
    scripts/                 the deterministic engine (stdlib only)
    references/contracts.md  exact JSON I/O of every script
    sample_contracts/        fictional docx/pdf/txt samples, run end to end
    tests/test_pipeline.py   proves the deterministic core (10 tests)
```

## Disclaimer

This tool assembles drafts from documents you provide. It is not a lawyer and
its output is not legal advice; a qualified professional must review any
contract before use.

## License

MIT
