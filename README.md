# ReproScore-Any

[![Live Scorer](https://img.shields.io/badge/Live%20Scorer-Hugging%20Face-yellow)](https://huggingface.co/spaces/AstroRadar/reproscore-any)
[![Website](https://img.shields.io/badge/Website-GitHub%20Pages-blue)](https://vasundharashaw.github.io/ReproScore-Any/)
[![License](https://img.shields.io/badge/License-Apache%202.0-green)](LICENSE)

**A field-agnostic pipeline for measuring the computational reproducibility of Jupyter notebooks in any research repository.**

🌐 **Website:** https://vasundharashaw.github.io/ReproScore-Any/
🚀 **Live scorer:** https://huggingface.co/spaces/AstroRadar/reproscore-any

---

## What it does

Give it a GitHub repository URL. ReproScore-Any will:

1. **Clone** the repository
2. **Build** an isolated environment from whatever dependency declaration it finds (`requirements.txt`, `environment.yml`, `setup.py`, `pyproject.toml`)
3. **Discover** every `.ipynb` in the repo
4. **Execute** each notebook end-to-end and capture per-cell outcomes and error types
5. **Score** the repository against a reproducibility rubric
6. **Record** everything to a SQLite database for downstream analysis

It makes no assumptions about the research field. There is no dependency on any discipline-specific literature database, metadata service, or domain library.

---

## Scoring

Scoring uses the [ReproScore](https://github.com/myVSR/reproscore) rubric (Sheeba Samuel, TU Chemnitz), vendored at `pipeline/reproscore/`. All scores are on a 0–100 scale.

ReproScore reports **three** numbers on **two tiers**. They answer different questions and are not interchangeable.

| Score | Tier | Question it answers | Always available? |
| --- | --- | --- | --- |
| **RRS** | Static | Is this repository *set up* to be reproducible? | Yes |
| **ROS** | Execution | Did it *actually run*? | Only with execution evidence |
| **RCS** | Composite | A blend of the two, weighted by how much execution evidence exists | Yes (collapses to RRS) |

> **The most important caveat.** A high RRS does **not** predict that a repository will run. Readiness and outcome are measured separately precisely because they diverge. Read RRS and ROS as two independent findings, not as an estimate and its confirmation. The validation table below demonstrates this directly.

---

### RRS — Reproducibility Readiness Score

Computed from the repository's files alone. No code is run. 26 sub-metrics are grouped into five categories:

| Symbol | DB column | Category | Weight | τ (gate) | What it looks for |
| --- | --- | --- | --- | --- | --- |
| **E** | `score_E` | Environment Specification | **0.30** | 40 | Lockfiles, pinned dependencies, container spec, environment bootstrap, declared Python version |
| **A** | `score_A` | Data Accessibility | **0.25** | 30 | Data described, a pointer to where data lives, acquisition script, workflow orchestration |
| **D** | `score_D` | Documentation | **0.20** | 20 | README structure, install instructions, usage examples, inline explanation, entry point, docstrings, reuse metadata |
| **C** | `score_C` | Code Portability | **0.15** | 25 | No absolute paths, resolvable imports, no hardcoded credentials, no silently swallowed errors |
| **S** | `score_S` | Reproducibility Signals | **0.10** | 30 | Random seeds, linear notebook execution order, tests, expected outputs, CI, externalised config, hardware requirements |

Two things to keep in mind when reading these:

**E and A measure declaration, not truth.** `score_E` asks whether dependencies are *declared*, not whether they install. `score_A` asks whether data is *described and pointed to*, not whether the files are present. Whether any of it actually works is what ROS measures.

**The `score_*` columns store raw scores** — the category's own 0–100 value, before gating and before weighting. They will **not** sum to `rrs`. To reconstruct the total you must apply the gate, multiply by the weight, and subtract any penalties (see below).

#### The gate

Each category's raw 0–100 score passes through a gate before being weighted:

```
gate(x, τ, k) = x / 100                  if x ≥ τ
              = (x/τ)^k · (τ/100)        if x < τ
```

Above τ the gate is the identity — the category contributes its raw score times its weight. Below τ the contribution is compressed super-linearly, so partial credit is deliberately cheap. The compression only bites when a category falls *well* short of τ; a near-miss is barely penalised.

**Contribution to RRS = gate(raw, τ, k) × weight × 100.** The five contributions sum to the pre-penalty total.

#### Hard penalties

Three penalties are then subtracted from the total:

| Trigger | Penalty |
| --- | --- |
| Environment raw score below 10 | **−20** |
| Data raw score below 10 | **−15** |
| Seed coverage below 50% | **−10** |

A repository with neither an environment specification nor a data pointer therefore starts 35 points down. **Low RRS values are common and are not an error.**

Penalties are computed at scoring time and logged, but are not currently persisted to the database. If `rrs` is lower than the weighted category contributions imply, a penalty is the reason.

---

### ROS — Reproducibility Outcome Score

Computed only where sandboxed execution evidence exists. Six probes:

| Probe | Weight |
| --- | --- |
| Install success | 0.30 |
| Execution success | 0.25 |
| Output determinism | 0.20 |
| Notebook execution rate | 0.10 |
| Import success rate | 0.10 |
| Test pass rate | 0.05 |

ROS normalises over whichever probes are available, so a partial run still yields a comparable 0–100 figure.

---

### RCS — Reproducibility Composite Score

```
RCS = (1 − α) · RRS + α · ROS
```

α scales with how much execution evidence was collected, and is **capped at 0.70**. Two consequences worth stating plainly:

- **With no execution evidence, RCS is identical to RRS.** This is by design, not a bug.
- **Even under full execution coverage, RRS never falls below 30% of the composite.** Running successfully cannot fully redeem a badly specified repository.

---

### Interpreting a score

| Band | Reading |
| --- | --- |
| 🟢 **60–100** | Strong |
| 🟡 **30–59** | Partial |
| 🔴 **0–29** | Weak |

These bands are **interpretive aids, not validated thresholds.** They are useful for triage and comparison. They are not a pass/fail line, and — see the caveat above — they carry no claim about whether the repository will execute.

The rubric weights, gate thresholds, and penalties all live in `config/default_rubric.yaml` and can be edited without touching pipeline code.

---

## Worked example

`alecarones/broom`, scored against the default rubric:

| Category | raw | τ | Above τ? | Weight | Contribution |
| --- | --- | --- | --- | --- | --- |
| E — Environment | 32.8 | 40 | no | 0.30 | 8.89 |
| A — Data | 0.0 | 30 | no | 0.25 | 0.00 |
| D — Documentation | 35.8 | 20 | yes | 0.20 | 7.17 |
| C — Code Portability | 67.2 | 25 | yes | 0.15 | 10.08 |
| S — Repro Signals | 28.6 | 30 | no | 0.10 | 2.83 |

Sum of contributions: **28.97**. Data raw score is 0, below the threshold of 10, so the **−15** data penalty fires.

**RRS = 28.97 − 15 = 13.97**

The story here is not the gate — it cost roughly one point in total. It is that Data Accessibility is zero, which contributes nothing *and* triggers an outright 15-point deduction, while every other category is middling. Meanwhile, all four of broom's notebooks execute cleanly.

---

## Quick start

```bash
git clone https://github.com/VasundharaShaw/ReproScore-Any.git
cd ReproScore-Any
pip install -r requirements.txt
export GITHUB_API_TOKEN=<your_token>   # raises the GitHub API rate limit
bash run.sh
```

Results are written to `output/db/db.sqlite`. The `repo_targets` table holds one row per repository with all scores; `notebook_executions` holds per-notebook execution outcomes.

### Run the web UI locally

```bash
python3 app.py
```

Then open http://localhost:7860.

### Run in Docker

```bash
docker build -t reproscore-any .
docker run -p 7860:7860 reproscore-any
```

Set `GRADIO_ROOT_PATH` if serving behind a path prefix (e.g. a JupyterHub-managed service).

---

## Repository layout

```
pipeline/             Core pipeline (clone, env build, notebook discovery, execution, scoring)
pipeline/reproscore/  Vendored ReproScore rubric implementation
config/               Pipeline configuration and rubric weights
app.py                Gradio web interface (the Live Scorer)
run.sh                Entry point for a full pipeline run
analysis/             Jupyter notebook for exploring results
docs/                 GitHub Pages site (pipeline overview, dashboard, live scorer, usage)
binder/               Binder configuration
Dockerfile            Containerised deployment
```

---

## Validation

The pipeline has been run across disciplines to confirm it is not field-bound:

| Repository | Field | Execution outcome | RRS |
| --- | --- | --- | --- |
| `caravangelo/inflation-easy` | Astrophysics | `SUCCESS_WITH_ERRORS` | 18.5 |
| `alecarones/broom` | Astrophysics | `SUCCESS` | 14.0 |
| `theislab/single-cell-tutorial` | Biomedicine | Scored successfully | — |

**Note the ordering.** `broom` executes cleanly and scores *lower* than `inflation-easy`, which fails on two of its notebooks. This is not a defect in the rubric — it is the two-tier design doing its job. Readiness and outcome are distinct properties, and a repository can be well-organised and broken, or scruffy and functional.

Two failure modes dominate execution: **data files not committed to the repository** (`FILE_ERROR`) and **non-pip system dependencies** such as LaTeX or compilers (`OTHER_ERROR`).

---

## Origins

ReproScore-Any generalises [Reproducibility_Astro](https://github.com/VasundharaShaw/Reproducibility_Astro), an astrophysics-specific reproducibility pipeline, by removing all discipline-bound logic. Both are developed as part of the **Jupyter4NFDI** initiative.

## Acknowledgements

- **Sheeba Samuel** (TU Chemnitz) — the ReproScore framework
- **Tim Kreuzer** — Jupyter4NFDI infrastructure
- **FIZ Karlsruhe** and the **NFDI** JupyterHub service

## License

Apache 2.0 — see [LICENSE](LICENSE).
