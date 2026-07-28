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

Scoring uses the [ReproScore](https://github.com/myVSR/reproscore) framework (Sheeba Samuel, TU Chemnitz), vendored at `pipeline/reproscore/`. All scores are on a 0–100 scale.

ReproScore exists to separate two things that automated reproducibility tools routinely conflate: what a repository *contains* and whether it *runs*. The framework calls this the **readiness–outcome conflation**, and resolves it by refusing to collapse the two into a single number.

| Score | Tier | Question it answers | Availability | Status |
| --- | --- | --- | --- | --- |
| **RRS** | 1 — static | Is this repository *set up* to be reproducible? | Always | Validated |
| **ROS** | 2 — execution | Did it *actually run*? | Only with execution evidence | Partially validated |
| **RCS** | Composite | A blend of the two, weighted by how much execution evidence exists | Always (collapses to RRS) | Design proposal |

The status column is not a formality. In the published evaluation, RRS is the primary empirical contribution, evaluated on 423 GitHub repositories. ROS and RCS are an architectural extension: the blending formula has been validated only partially and not at scale, and the coverage cap and per-component weights are design choices awaiting calibration against an independent execution dataset. Read them accordingly.

---

### RRS — Reproducibility Readiness Score

Computed from the repository's files alone. No code is run. 26 sub-metrics are grouped into five categories:

| Symbol | DB column | Category | Weight | τ | k | What it looks for |
| --- | --- | --- | --- | --- | --- | --- |
| **E** | `score_E` | Environment Specification | **0.30** | 40 | 1.5 | Lockfiles, pinned dependencies, container spec, environment bootstrap, declared runtime version |
| **A** | `score_A` | Data Accessibility | **0.25** | 30 | 1.5 | Data described, a pointer to where data lives, acquisition script, workflow orchestration |
| **D** | `score_D` | Documentation | **0.20** | 20 | 1.2 | README structure, install instructions, usage examples, inline explanation, entry point, docstrings, reuse metadata |
| **C** | `score_C` | Code Portability | **0.15** | 25 | 1.2 | No absolute paths, resolvable imports, no hardcoded credentials, no silently swallowed errors |
| **S** | `score_S` | Reproducibility Signals | **0.10** | 30 | 1.2 | Random seeds, linear notebook execution order, tests, expected outputs, CI, externalised config, hardware requirements |

Weights are expert-informed defaults, not fitted parameters. E and A carry the most weight because dependency problems and inaccessible data are the two dominant execution failure modes, and they use the steeper gate (k = 1.5); D, C and S are quality categories and use a more lenient gate (k = 1.2).

Two things to keep in mind when reading these:

**E and A measure declaration, not truth.** `score_E` asks whether dependencies are *declared*, not whether they install. `score_A` asks whether data is *described and pointed to*, not whether the files are present. Whether any of it actually works is what ROS measures.

**The `score_*` columns store raw scores** — the category's own 0–100 value, before gating and before weighting. They will **not** sum to `rrs`. To reconstruct the total you must apply the gate, multiply by the weight, and subtract any penalties.

#### The gate

Each category's raw 0–100 score passes through a gate before being weighted:

```
gate(x, τ, k) = x / 100                  if x ≥ τ
              = (x/τ)^k · (τ/100)        if x < τ
```

Above τ the gate is the identity. Below τ the contribution is compressed super-linearly, so partial credit is deliberately cheap: a repository with almost no environment specification should not score comparably to one with a partial specification.

**The gate is a policy statement, not a performance mechanism.** Sweeping its parameters in the published evaluation moves discrimination by about 0.008 AUC — essentially nothing, in part because many sub-metrics return binary values. It is retained because it encodes a curation stance about sub-threshold failures, and that stance is worth making explicit and configurable rather than burying it in a linear sum.

**Contribution to RRS = gate(raw, τ, k) × weight × 100.** The five contributions sum to the pre-penalty total.

#### Hard penalties

Three penalties are then subtracted:

| Trigger | Penalty |
| --- | --- |
| Environment raw score below 10 | **−20** |
| Data raw score below 10 | **−15** |
| Seed coverage below 50% | **−10** |

These fire on conditions under which reproduction is close to impossible — no environment specification at all, or no data artefacts at all. A repository with neither starts 35 points down. **Low RRS values are common and are not an error.**

Penalties are computed at scoring time and logged, but are not currently persisted to the database. If `rrs` is lower than the weighted category contributions imply, a penalty is the reason.

#### What RRS is for

RRS is a **diagnostic instrument, not a predictor.** Its purpose is to tell you *which dimension* of readiness is deficient so that remediation can be targeted — low E means underspecification, low A means missing data pointers, low C means portability defects. The per-category profile is the useful output; the composite RRS is a structural completeness summary only.

It does **not** predict whether the repository will execute, and was not designed to. See below.

---

### The readiness–outcome gap

This is the finding that motivates the whole two-tier design, so it is worth stating with the evidence attached rather than as an assertion.

Across 423 repositories, the environment category was by far the strongest discriminator of *failure mode* (Kruskal–Wallis H = 96.89, p < 0.001) while showing essentially **no** correlation with binary execution success (r_pb = −0.014, p = 0.767). The mechanism is a detection paradox: repositories that fail at install time score *highest* on environment specification, because they do specify their environment — just with conflicting versions. Repositories that succeed often score lower, because their implicit dependencies happen to resolve without ever being declared. The two effects cancel across a binary success label.

The practical consequence: **a high RRS does not predict that a repository will run, and a low RRS does not predict that it won't.** Readiness and outcome are separate quantities. Read RRS and ROS as two independent findings, not as an estimate and its confirmation.

---

### ROS — Reproducibility Outcome Score

Computed only where sandboxed execution evidence exists. Six probes:

| Probe | Symbol | Weight |
| --- | --- | --- |
| Install success | I | 0.30 |
| Execution success | X | 0.25 |
| Output determinism | Δ | 0.20 |
| Notebook execution rate | N | 0.10 |
| Import success rate | E′ | 0.10 |
| Test pass rate | T | 0.05 |

All probes are optional. ROS normalises over whichever subset is available, so a partial run still yields a comparable 0–100 figure. When no probe is available, ROS is undefined and the system falls back to RCS = RRS.

The current execution backend emits five of the six probes — I, X, Δ, N, and E′. **T (test pass rate) is not yet produced by the pipeline**, so ROS is presently normalised over those five. This is a deliberate gap, not a defect: adding a test signal later is non-breaking, as the scorer picks T up automatically once the pipeline emits it.

Install success and import success are deliberately kept apart: the first tests whether the package manager can resolve dependencies, the second whether the code can import them at runtime. These diverge in practice, and collapsing them would hide a common failure mode.

---

### RCS — Reproducibility Composite Score

```
RCS = RRS                              if ROS is undefined
RCS = (1 − α) · RRS + α · ROS          otherwise
```

α is the **coverage weight**: it scales with the fraction of execution evidence actually collected, and communicates how much of the composite rests on execution rather than inspection.

- **Capped at α_max = 0.70.** Even under full execution coverage, RRS never falls below 30% of the composite. Running successfully cannot fully redeem a badly specified repository — execution says nothing about data documentation, README completeness, or portability.
- **Floored at α_min = 0.10** whenever *any* probe is available, so a sliver of execution evidence still registers.
- **With no execution evidence at all, α = 0 and RCS is exactly RRS.** This is by design, not a degenerate case.

---

### Interpreting a score

| Band | Reading |
| --- | --- |
| 🟢 **60–100** | Strong |
| 🟡 **30–59** | Partial |
| 🔴 **0–29** | Weak |

These bands are **interpretive aids, not validated thresholds.** They are useful for triage and comparison. They are not a pass/fail line, and — see the readiness–outcome gap above — they carry no claim about whether the repository will execute.

---

## Community rubrics

Assessment priorities differ across communities. A bioinformatics archive may weigh data accessibility far above CI configuration; a software-centric repository may prioritise portability. ReproScore externalises these priorities into **named, versioned YAML profiles** rather than embedding them in code.

Profiles live in `config/rubrics/`:

| Profile | E | A | D | C | S |
| --- | --- | --- | --- | --- | --- |
| `default.yaml` | 0.30 | 0.25 | 0.20 | 0.15 | 0.10 |
| `bioinformatics-v1.yaml` | 0.35 | **0.40** | 0.10 | 0.05 | 0.10 |

A profile may override category weights, gate parameters, penalties, ROS component weights, and the RCS coverage bounds. Anything it omits falls back to the default. Category weights are validated to sum to 1.0 ± 0.01 at load time.

The point is auditability. An institution that publishes its rubric has published a **citable curation policy**: the same profile version applied to the same repository yields the same score, reorderings are attributable to a stated priority, and revisions leave a version history. That is a different kind of object from a scoring heuristic buried in source code.

```python
from src.scoring.rubric import load_rubric
rubric = load_rubric("config/rubrics/bioinformatics-v1.yaml")
```

> **Note:** the pipeline CLI currently always scores under `default.yaml`. Alternative profiles are loadable from Python but not yet selectable from `run.sh`.

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

**RRS = 28.97 − 15 = 13.97**  (the pipeline stores this rounded to 13.7)

Running broom's four notebooks through the execution backend fills in the outcome tier. The environment builds (I = 100) and all four notebooks are attempted (N = 100), but none completes without error (X = 0) and every one fails on a dependency import (E′ = 0). Output determinism is low (Δ = 11.38), though here that is a consequence of the notebooks aborting early rather than genuine run-to-run drift — cells that never execute cannot match their committed outputs. Over the five available probes this gives **ROS = 44.5** (α = 0.665; T is not emitted), and the composite is **RCS = (1 − 0.665) · 13.97 + 0.665 · 44.5 = 34.18**.

The instructive part is that broom scores low on *both* tiers. RRS = 13.97 flags a repository that is statically under-specified — dependencies undeclared, data undescribed. ROS = 44.5 then confirms, from an actual run, that the notebooks fail, and fail on exactly the dependency imports RRS predicted from the static evidence. This is the *concordant* case: readiness and outcome agree the repository is in trouble, and agree for the same underlying reason. The readiness–outcome gap — high readiness masking a broken run, or a low-readiness repository that nonetheless executes — is the case where the two tiers *disagree*. broom is not that case; it is the baseline where they line up. Both readings are useful, and neither substitutes for the other.

---

## Limitations

Inherited from the published evaluation, and worth stating plainly:

- **Corpus scope.** The 423-repository evaluation used Python/Jupyter repositories drawn from biomedical publications. Generalisability to script-only, R, or compiled-language repositories is untested, and sub-metric applicability varies by domain and programming paradigm. ReproScore-Any removes the pipeline's field-specific logic, but the *rubric's* validation scope is unchanged by that.
- **Notebook-specific sub-metrics.** `notebook_exec_order` and `inline_explanation_density` are defined in terms of notebooks and do not transfer cleanly to non-notebook repositories.
- **Static coverage.** Sub-metrics detect artefact *presence*, not semantic correctness. A pinned `requirements.txt` with conflicting constraints scores well on E and still fails at install. The two-tier architecture addresses this structurally rather than by improving the static approximation.
- **Feature expressivity.** Roughly ten of the 26 sub-metrics return binary values for the large majority of repositories, which limits what the gate can do.
- **RCS calibration.** α_max and the per-component ROS weights are design choices, not fitted values.

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
config/               Pipeline configuration
config/rubrics/       Community rubric profiles (default, bioinformatics-v1)
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

## Citing

The scoring framework is described in:

> Samuel, S., Mietchen, D., Kim, J., Ahmed, W., Gaedke, M. *ReproScore: Separating Readiness from Outcome in Research Software Reproducibility Assessment.*

Reference implementation, rubric profiles and per-repository provenance records: https://doi.org/10.5281/zenodo.20154206 · [Software Heritage archive](https://archive.softwareheritage.org/swh:1:rev:5b4de0124a482d6266c39b0538a8e8da75cd68fa;origin=https://github.com/myVSR/reproscore)

This repository (ReproScore-Any) is a field-agnostic pipeline built around that framework; the rubric implementation under `pipeline/reproscore/` is vendored from it.

### Foundational work

ReproScore and this pipeline build directly on the reproducibility studies and infrastructure of Sheeba Samuel and Daniel Mietchen:

- Samuel, S., Mietchen, D., Kim, J., Ahmed, W., Gaedke, M. *ReproScore: Separating Readiness from Outcome in Research Software Reproducibility Assessment.* — Zenodo: https://doi.org/10.5281/zenodo.20154206
- Samuel, S., Mietchen, D. *Computational reproducibility of Jupyter notebooks from biomedical publications.* GigaScience **13**, giad113 (2024). https://doi.org/10.1093/gigascience/giad113
- Samuel, S., Mietchen, D. *FAIR Jupyter: A Knowledge Graph Approach to Semantic Sharing and Granular Exploration of a Computational Notebook Reproducibility Dataset.* Transactions on Graph Data and Knowledge **2**(2), 4:1–4:24 (2024). https://doi.org/10.4230/TGDK.2.2.4
- Samuel, S., Mietchen, D. *Dataset of a Study of Computational Reproducibility of Jupyter Notebooks from Biomedical Publications.* Zenodo (2023). https://doi.org/10.5281/zenodo.8226725

The `notebook_exec_order` and `markdown_code_ratio` sub-metrics are grounded in the FAIR Jupyter knowledge graph; the evaluation corpus derives from the biomedical reproducibility dataset above.

### Project context

- Gey, R., Mietchen, D., Karras, O., Wittenborg, T., Schubotz, M., Bumberger, J. *find.software: Foundations for interdisciplinary discovery of (research) software.* Research Ideas and Outcomes **11**, e179253 (2025). https://doi.org/10.3897/rio.11.e179253
- Hagemeier, B., Bleier, A., Flemisch, B., Reuter, K., Dogaru, G., Mietchen, D., Lieber, M. *Jupyter4NFDI — Proposal for the Integration Phase of Base4NFDI* (2025). https://doi.org/10.5281/zenodo.17867919

---

## Origins

ReproScore-Any generalises [Reproducibility_Astro](https://github.com/VasundharaShaw/Reproducibility_Astro), an astrophysics-specific reproducibility pipeline, by removing all discipline-bound logic. Both are developed as part of the **Jupyter4NFDI** initiative.

## Acknowledgements

- **Sheeba Samuel** (TU Chemnitz) — the ReproScore framework
- **Tim Kreuzer** — Jupyter4NFDI infrastructure
- **FIZ Karlsruhe** and the **NFDI** JupyterHub service

## License

Apache 2.0 — see [LICENSE](LICENSE).
