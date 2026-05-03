# Text-to-SQL Research Notes — Papers A through G

Reference document capturing the literature review that informed the
`feature/self-sustaining-nl2sql` architecture. Each paper was analyzed for
transferable ideas given the project's hard constraints:

- **LLM-free** at inference time
- **Lightweight** Docker image (~300 MB)
- **Self-sustaining** — improves via `history.db` without external retraining

The recommendation has been stable since **Paper B** (paper 2 of 7); subsequent
papers added refinements (C, D) or corroborated existing ideas (E, F, G).

---

## Quick reference table

| Paper | Year | Source | Core idea | New contribution to our plan |
|---|---|---|---|---|
| **A** | 2023 | SQL-PaLM (Sun et al., arXiv 2306.00739) | LLM prompt engineering for text-to-SQL | DB content value matching (T1a) |
| **B** | 2017 | SQLizer (Yaghmazadeh et al., OOPSLA) | Sketch IR + type inhabitation + repair loop | **Core architecture** — sketch + repair + top-K |
| **C** | 2022 | Survey (Kumar et al., arXiv 2208.04415) | Survey of 24 neural text-to-SQL systems | MISP interactive disambiguation + execution-guided decoding |
| **D** | 2025 | SQL-R1 (Ma et al., NeurIPS) | RL-trained NL2SQL reasoning model (GRPO) | Multi-component scoring recipe |
| **E** | 2015 | Sathick & Jaya, IJST | Java/SQL Server NL→SQL system | Independently confirms MISP idea |
| **F** | 2018 | Bhalla & Gupta, Stanford CS | SQLNet reimplementation with column attention | None new (column attention ≈ our matcher) |
| **G** | 2023 | Jeong et al., MDPI Entropy | Hybrid sketch + generation decoder | SER metric (small instrumentation) |

---

## Conclusion — what we take from each paper

Out of seven papers, **four contributed concrete ideas** to the implementation;
**three were corroborative only**. Full attribution by paper:

### 🟢 Paper B — SQLizer → THE FOUNDATION

The single most influential paper. Provides the entire architectural backbone.

| Idea | Lands in |
|---|---|
| Explicit query-sketch IR with typed holes | `app/sketch.py` (new) |
| Quantitative type inhabitation — enumerate fillers, score each | `app/sketch.py` |
| Refinement / repair loop (fault localization → repair tactic → retry) | `app/repair.py` (new) |
| Repair tactics: add JOIN, swap aggregate ↔ column, add predicate, change table | `app/repair.py` |
| Top-K ranked candidates instead of single SQL | `app/generator.py` |
| Fault-aware error messages (which sketch position broke) | builder errors |

→ **Stage 1**, ~500 lines. Without B, we have no architecture.

### 🟢 Paper A — SQL-PaLM → VALUE PRECISION

| Idea | Lands in |
|---|---|
| **DB content matching (T1a)** — for each NL keyword, ILIKE + LCS against actual DB cell values | `app/nlp/values_db.py` (new) |
| Resolves `"shoes"` → `"Running shoes"`, `"california"` → `"California"`, `"acme"` → `"Acme Corp Pvt Ltd"` | Plugs into sketch's value-hole scoring |

→ **Stage 3**, ~150 lines. A.T1b (hand-curated hints) was **rejected** —
replaced by C's MISP loop which captures the same information organically.

### 🟢 Paper C — Survey → CLOSES THE LEARNING LOOP

| Idea | Source within survey | Lands in |
|---|---|---|
| **Interactive disambiguation (MISP)** — when top-1/top-2 confidence are close, ask the user to pick | MISP entry | UI button + `POST /api/teach` |
| **Execution-guided validation** — execute top-K read-only, discard errors, re-rank | PointerSQL, SeaD entries | `app/generator.py` post-repair |

→ **Stages 2 & 4**, ~140 lines. MISP is what makes the system **truly
self-sustaining** — every user click writes labeled data to `history.db`
for the next retrain.

### 🟢 Paper D — SQL-R1 → SCORING RECIPE

| Idea | Lands in |
|---|---|
| **Multi-component scoring** — Format + Execution + Result-plausibility + Length penalty | `app/generator.py` confidence function |
| Ablation: removing any component drops accuracy 0.7–2.7 % (additive, non-redundant signals) | Validates the 4-component approach |

→ Refines **Stage 2**, ~30 lines. Replaces a single confidence number with
the 4-signal sum.

### 🟡 Paper G — Jeong → METRIC ONLY

| Idea | Lands in |
|---|---|
| **Syntactic Error Rate (SER)** — log parse failures + validation failures + execution errors as separate counters | `/api/health` extension |

→ **Bonus**, ~15 lines. G's main idea (hybrid sketch + generation decoder)
corroborates Paper B but isn't a separately adopted item.

### ⚪ Papers E & F — corroboration only, nothing adopted

| Paper | What it confirmed | New code? |
|---|---|---|
| **E** Sathick 2015 | Independent confirmation of MISP idea (user picks from intermediate queries) | None — strengthens C's adoption |
| **F** Bhalla 2018 | Column attention ≈ our rapidfuzz + WordNet matcher | None — already covered |

---

### Final attribution at a glance

```
Stage 1: Sketch IR + repair loop + top-K            ← Paper B           ~500 lines
Stage 2: Execution-guided validation                ← Paper C
         + multi-component scoring                  ← Paper D            ~80 lines
Stage 3: DB content value matching                  ← Paper A.T1a       ~150 lines
Stage 4: Interactive top-K disambiguation UI        ← Paper C (MISP)
                                                       confirmed by E    ~60 lines
Bonus:   Syntactic Error Rate metric                ← Paper G            ~15 lines
                                                                       ─────────
                                                                  total: ~810 lines
```

### One-line summary per paper

- **B** gives us the **architecture** (sketch + repair).
- **A** gives us **value precision** (real DB values, not guesses).
- **C** gives us the **user feedback loop** (interactive disambiguation + execution validation).
- **D** gives us the **scoring formula** (4-signal confidence).
- **G** gives us **observability** (SER metric).
- **E, F** add nothing — but their independent agreement with B and C raises
  confidence we picked the right primitives.

### LLM ↔ non-LLM mapping

Each adopted idea is the **non-neural essence** of its LLM-era source:

| Idea pattern | LLM version | Our LLM-free version |
|---|---|---|
| Sketch fill | Neural slot decoder | Symbolic enumeration with rapidfuzz scoring |
| Repair | LLM retry with feedback | Deterministic tactic application |
| Value matching | Embedding similarity | ILIKE + longest common subsequence |
| Confidence | Softmax / RL reward | 4-component additive score |
| User loop | RLHF | UI click → history.db → retrain |
| Validation | Ground-truth comparison | Heuristic plausibility (rows > 0, sane size) |

The bet: for a fixed schema with curated synonyms (which the schema DSL
already provides), this stack hits the same accuracy ceiling LLMs do —
deterministic output, ~300 MB image, zero external API calls.

---

## Paper A — SQL-PaLM (arXiv 2306.00739)

**Citation**: Sun, R. et al. *SQL-PaLM: Improved Large Language Model Adaptation for Text-to-SQL (extended)*. 2023.

**Core idea**: Adapt PaLM-2 for text-to-SQL via few-shot prompting and instruction fine-tuning. Investigates input representation, training-data diversity, synthetic data, query-specific DB content, column selection, and test-time refinement.

**Stack**: PaLM-2 Unicorn LLM. Concise schema serialization. Execution-based consistency decoding. Hard vs soft column selection.

**What we adopted (T1a — DB content matching)**:
For each NL keyword, do a longest-common-subsequence match against actual
DB cell values using `ILIKE … LIMIT 5` per likely-relevant column. Resolves
`"shoes"` → `"Running shoes"` (real DB value), `"california"` → `"California"`.

**What we rejected**:
- Synthetic data via LLM (we can't generate)
- Fine-tuning / LoRA (LLM-only)
- Program-aided column selection (requires preliminary LLM SQL)
- Consistency decoding via sampling (we're deterministic)

**T1b (hand-curated hints) reshaped**: instead of static `H` block, capture
hints organically via interactive corrections (history-driven). The
sketch + repair loop absorbs most of what hints would cover.

---

## Paper B — SQLizer (OOPSLA 2017)

**Citation**: Yaghmazadeh, N., Wang, Y., Dillig, I., Dillig, T. *SQLizer: Query Synthesis from Natural Language*. PACMPL OOPSLA 2017.
*(Analyzed via the author's conference talk transcript)*

**Core idea**: Database-agnostic, **non-ML symbolic** approach. Pipeline:

1. **Semantic parsing** → query *sketch* (SELECT/FROM/WHERE skeleton with
   `?` holes, each hole carries an NL hint)
2. **Quantitative type inhabitation** → enumerate every valid filler from
   the schema; score each via hint-similarity + schema signals (PK/FK,
   DB content, column names)
3. **Refinement loop** → if no completion meets the confidence threshold:
   - **fault localization** identifies the faulty hole/clause
   - apply a **repair tactic** (add predicate, add JOIN, swap aggregate ↔
     column, change table)
   - retry until convergence or iteration cap
4. **Global top-K ranking** across all sketches + repairs

**Results**: ~90% top-5, ~81–85% top-1 across 3 databases. 1.2 s avg.
Database-agnostic, no DB-specific training.

**What we adopted (entire backbone)**:
- Explicit query-sketch IR with typed holes
- Confidence-scored completions
- Repair loop with fault localization + repair tactics
- Top-K output (not single SQL)
- Fault-aware error messages instead of "could not build"

**Why it dominates the plan**: B is the **only paper of the seven that
shares all our constraints** (LLM-free, deterministic, lightweight). It
also fixes the single biggest structural gap in our current code: today
we fail-fast on first generation error; B's loop tries variants until one
satisfies confidence + executability.

---

## Paper C — Survey (arXiv 2208.04415)

**Citation**: Kumar, A., Nagarkar, P., Nalhe, P., Vijayakumar, S. *Deep Learning Driven Natural Languages Text to SQL Query Conversion: A Survey*. 2022.

**Core idea**: Survey of 24 neural text-to-SQL systems (2018–2022) across
11 datasets (Spider, WikiSQL, ATIS, GeoQuery, …).

**Highlights catalogued**:
- SQLNet, TypeSQL, F-SQL, RYANSQL — sketch + slot-filling
- IRNet — SemQL intermediate representation
- BRIDGE — fuzzy NL ↔ DB cell value matching with anchor texts
- RAT-SQL — schema-as-graph, relation-aware self-attention
- PointerSQL, SeaD — execution-guided decoding (reject error-producing partials)
- SyntaxSQLNet — 9 specialized prediction modules
- **MISP** — interactive: ask user binary questions for low-confidence parts
- ValueNet — NER + heuristics for DB value candidates
- Seq2SQL, Seq2Tree — RL / grammar-constrained decoding

**What we adopted**:

1. **MISP-style interactive disambiguation**. When top-1 and top-2
   confidence are within a small margin, the UI shows both candidates and
   asks the user to pick. Each click writes to `history.db` and feeds the
   next classifier retrain. This is the **closing of the self-sustaining
   loop** — every ambiguity becomes labeled training data.

2. **Execution-guided validation** (PointerSQL / SeaD pattern). After the
   sketch+repair loop emits top-K, execute each candidate read-only,
   discard those that error, re-rank by row-count plausibility.

**Corroborates** (no new contribution but raises confidence in adopted ideas):
- Sketch-based synthesis is mainstream — SQLNet, TypeSQL, F-SQL, RYANSQL,
  *and* SQLizer all converge on it
- DB content matching is mainstream — BRIDGE, TypeSQL-content-sensitive,
  ValueNet, F-SQL, SQL-PaLM all use some form

**What we rejected**:
- Graph-based schema encoding (RAT-SQL) — heavy ML
- Pretraining (GRAPPA), RL with execution reward (Seq2SQL) — LLM-only
- BERT/Transformer encoders — out of scope

---

## Paper D — SQL-R1 (NeurIPS 2025)

**Citation**: Ma, P. et al. *SQL-R1: Training Natural Language to SQL Reasoning Model By Reinforcement Learning*. arXiv 2504.08600.

**Core idea**: Take a 7B/14B coder LLM (Qwen2.5-Coder), SFT cold-start on
synthetic SQL pairs, then RL with GRPO and a 4-component reward. Generate
N candidates at T=0.8, vote by executed result. Achieves 88.7% Spider /
67.1% BIRD on a 14B model.

**Cost**: 8 × 80 GB GPUs, weeks of training, 200 K – 2.5 M synthetic pairs.

**95% not applicable** to us (LLM, RL, training-heavy). But:

**What we adopted (multi-component scoring recipe)**:

The 4-component reward function in §2.3.3:
- **Format reward** — does output parse / have required structure?
- **Execution reward** — does the SQL run without error?
- **Result reward** — does the query result match (or look plausible)?
- **Length reward** — prefer compact SQL when tied on the rest

Their ablation (Table 5) shows removing any one component drops accuracy
0.7–2.7 % — they're additive, non-redundant signals.

For us, this becomes the **confidence function** for picking among
top-K candidates after Paper B's repair loop:

```
confidence(sql) = w_f * parses_cleanly(sql)
                + w_e * executes_without_error(sql)
                + w_r * result_plausibility(sql)   # rows > 0, non-null
                + w_l * simplicity_bonus(sql)
```

We replace the ground-truth check (which they have, we don't) with
heuristics: zero rows = probably wrong filter, all-null projection = bad
column choice, 10M rows returned = missing WHERE / LIMIT.

**What we rejected**:
- GRPO RL training, SFT, all training apparatus
- Reasoning-trace scaffolding (`<think>...</think>`) — only meaningful with an LLM
- Synthetic data at scale

---

## Paper E — Sathick & Jaya (IJST 2015)

**Citation**: Sathick, K. J., Jaya, A. *Natural language to SQL Generation for Semantic Knowledge Extraction in Social Web Sources*. Indian Journal of Science and Technology, Vol 8(1), Jan 2015.

**Honest assessment**: **Weakest of the seven.** Dated, narrow, methodologically thin.

**What it describes**: Java + SQL Server app for querying social-media data
(Facebook/Twitter/LinkedIn pulled via R-tool). 5-stage textbook NLP
pipeline (morphological → syntactic → semantic → discourse → pragmatic).
Hand-curated dictionary of synonyms. PK-FK auto-JOIN. String template
substitution into SQL.

**Evaluation**: precision/recall on a *single example query*. Precision
collapses to 0.0014 at recall 1.0 — the system retrieves a swamp of
irrelevant content to attain full recall.

**Architecturally subsumed**: every feature listed has a stronger
equivalent in our current code (NLTK WordNet > Porter stemming, sqlglot
AST > string templates, FK BFS > simple PK-FK, etc.).

**Only contribution**: the system asks the user to pick from "intermediate
queries". This is the **same idea as MISP** (Paper C) — independent
confirmation in 2015 of an idea formalized later. Strengthens the case
for adopting it but doesn't add anything mechanically new.

---

## Paper F — Bhalla & Gupta (Stanford CS, ~2018)

**Citation**: Bhalla, I., Gupta, A. *Generating SQL queries from natural language*. Stanford CS course project.

**Honest assessment**: Course project, narrow scope, second-weakest of seven.

**What it describes**: Reimplements SQLNet on WikiSQL with two tweaks:
1. Predict aggregate operator from question alone (not from predicted SELECT column)
2. Train SELECT and aggregate losses independently rather than mixed

Architecture: Bi-LSTM + GloVe + column attention + pointer network.
Improvement: 1.5–2.5 % over SQLNet baseline on WikiSQL **SELECT and
aggregate only**. No WHERE, no JOIN, no nested.

**Transferable to us**: nothing genuinely new.
- Column attention conceptually = our per-column rapidfuzz + WordNet score
- "Aggregate from question alone" already in our intent classifier
- Independent loss optimization → not applicable (we don't train an NN)

---

## Paper G — Jeong et al. (Entropy 2023)

**Citation**: Jeong, G. et al. *Improving Text-to-SQL with a Hybrid Decoding Method*. Entropy 25(3), 513, 2023.

**Core idea**: Combine sketch-based decoding (slot-filling) with
generation-based decoding (sequential token generation). Generate SQL
tokens sequentially, but each step's vocabulary is **type-masked** by
slot type. Pointer Network for column slots; Token Generation Layer for
non-column slots; **BIO sequence labeling** for WHERE values (one-shot
extraction, no copy mechanism).

Defines slot types: `sel_col`, `sel_agg`, `sel_cont`, `wh_col`, `wh_op`,
`wh_logic`, `wh_val`. Generation order ≠ written order; sort post-hoc.

**Strong corroboration** of Paper B: hybrid (sketch + sequential) is a
well-validated architecture. Paper G is independent confirmation that
type-masked sequential decoding beats either pure sketch or pure
generation.

**Maps onto our plan**:

| G's idea | Our equivalent |
|---|---|
| Type-masked decoding | Already in `builder.py` — switching on intent only emits valid AST shapes |
| BIO sequence labeling for values | `values.py` + `implicit.py` — span extraction in spirit |
| Hybrid sketch + generation | = Paper B's sketch + repair |
| Pointer Network for columns | rapidfuzz + WordNet scoring |
| Sorting generation order → written order | sqlglot AST handles this automatically |

**Only new contribution**: **Syntactic Error Rate (SER)** as an evaluation
metric. We can add it cheaply — count `validate_and_rewrite` failures and
expose at `/api/health`. ~15 lines.

---

## Where the seven papers converge

After analyzing all seven, the field has effectively converged on a small
set of architectural primitives:

1. **Sketch / IR with typed holes** (B, C-SQLNet/TypeSQL/F-SQL/RYANSQL, G)
2. **Schema linking via string + embedding match** (B, C-IRNet/RAT-SQL, F-attention)
3. **DB content / value matching** (A, C-BRIDGE/TypeSQL/ValueNet)
4. **Execution-guided validation / refinement** (B-repair, C-PointerSQL/SeaD, D-self-consistency)
5. **Multi-signal scoring** (B-confidence, D-4-component reward)
6. **Top-K + interactive disambiguation** (B, C-MISP, E)

Every one of these maps cleanly onto our LLM-free constraints when
reduced to its non-neural essence.

---

## Final implementation plan (Option 3+)

```
Stage 1 — Sketch IR + repair loop + top-K       (B, corroborated by C/G)
            ~500 lines + tests
            • app/sketch.py    — explicit dataclass IR with typed holes
            • app/repair.py    — repair tactics (add_join, swap_aggregate,
                                  add_predicate, change_table, swap_intent)
            • app/builder.py   — refactored to consume sketches
            • app/generator.py — drives the refinement loop (max 5 iter,
                                  ~200 ms cap)
            • UI               — show top-K candidates when confidence is close

Stage 2 — Execution-guided validation + multi-component scoring  (C + D)
            ~80 lines + tests
            • For each top-K candidate, execute read-only
            • Score: format + executes + result-plausibility + simplicity
            • Re-rank survivors; drop errors

Stage 3 — DB content value matching              (A.T1a)
            ~150 lines + tests
            • app/nlp/values_db.py — ILIKE + LCS over likely text columns
            • Resolves "shoes" → "Running shoes" type cases
            • Plugs into sketch's value-hole scoring
            • Session-cached

Stage 4 — Interactive top-K disambiguation UI    (C.MISP, corroborated E)
            ~60 lines + endpoint
            • UI shows alternatives when top-1/top-2 confidence within 10%
            • User click → POST /api/teach → history.db
            • Next /api/train picks it up automatically

Bonus  — Syntactic Error Rate metric             (G)
            ~15 lines
            • Counters at /api/health: parse_errors, validation_failures,
              execution_errors. Useful for measuring per-stage impact.

Total: ~810 lines + tests. No new heavy deps. Image stays ~300 MB.
       Self-sustaining loop closed via interactive corrections.
```

---

## What was rejected (and why)

| Idea | Source | Why rejected |
|---|---|---|
| LLM in hot path | A, D, much of C | Violates core constraint — entire reason for this branch |
| Fine-tuning / LoRA / RL training | A, D | Requires GPUs + training pipeline + base LLM |
| Synthetic data via LLM | A, D, C-GRAPPA | Requires LLM for generation |
| Graph encoders / RAT-attention | C-RAT-SQL | Heavy ML, training-required |
| BERT / Transformer encoders | C, G | Adds 200+ MB image + training step |
| Few-shot prompting / CoT / consistency decoding | A, D | LLM-only |
| Hand-curated hints (T1b raw form) | A | Reshaped as history-driven (auto-captured via MISP) |
| Sequence-to-sequence neural decoder | C-Seq2SQL, F | We don't train neural nets |

---

## Already shipped on `feature/self-sustaining-nl2sql`

Branch contains the LLM-free baseline plus three early refinements:

- **Commit `35a378e`**: initial self-sustaining nl2sql
  - FastAPI service, sqlglot validator, read-only Postgres executor
  - TF-IDF intent classifier + LinearSVC, retrains from `history.db`
  - rapidfuzz + WordNet schema matcher
  - Template SQL builder via sqlglot AST
  - 45 tests pass

- **Commit `1bf17db`**: implicit values, cross-table WHERE, "total X" intent
  - `app/nlp/implicit.py` — unquoted-literal detection (`org swaraj`)
  - Cross-table WHERE with auto-join
  - Regex+seed override for `total <plural>` → count
  - 54 tests pass

The four-stage plan above is what comes next.

---

## Decision history

- After Paper A: considered T1a + T1b. Reshaped T1b as history-driven.
- After Paper B: switched recommendation to "sketch + repair as foundation".
  This is the version that has remained stable.
- After Paper C: added MISP + execution-guided to the plan.
- After Paper D: refined the scoring function to multi-component.
- After Papers E, F, G: no plan changes; corroborations only.

The recommendation has been **stable for 4 papers** (since Paper D). At
this point, additional surveys are unlikely to change the plan unless
they introduce a fundamentally novel mechanism not seen in A–G (e.g.,
constraint solving, formal verification, type inference, retrieval-
augmented sketch synthesis).
