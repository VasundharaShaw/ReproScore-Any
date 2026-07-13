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

## Scoring

Scoring uses the [ReproScore](https://github.com/myVSR/reproscore) rubric (Sheeba Samuel, TU Chemnitz), vendored at `pipeline/reproscore/`. Every score is on a 0–100 scale.

| Metric | Meaning |
| --- | --- |
| `rrs` | **Repository Reproducibility Score** — the headline composite |
| `score_E` | Environment: are dependencies declared and installable? |
| `score_A` | Availability: are data and assets actually present? |
| `score_D` | Documentation: is there enough to run the thing? |
| `score_C` | Code quality and structure |
| `score_S` | Successful execution |
| `ros` | Reproducibility of Outputs Score |
| `rcs` | Reproducibility of Code Score |

The rubric weights live in `config/default_rubric.yaml` and can be edited without touching pipeline code.

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

## Validation

The pipeline has been run across disciplines to confirm it is not field-bound:

| Repository | Field | Outcome |
| --- | --- | --- |
| `caravangelo/inflation-easy` | Astrophysics | `SUCCESS_WITH_ERRORS`, RRS ≈ 42 |
| `alecarones/broom` | Astrophysics | `SUCCESS`, RRS ≈ 62 |
| `theislab/single-cell-tutorial` | Biomedicine | Scored successfully |

Two failure modes dominate: **data files not committed to the repository** (`FILE_ERROR`) and **non-pip system dependencies** such as LaTeX or compilers (`OTHER_ERROR`).

## Origins

ReproScore-Any generalises [Reproducibility_Astro](https://github.com/VasundharaShaw/Reproducibility_Astro), an astrophysics-specific reproducibility pipeline, by removing all discipline-bound logic. Both are developed as part of the **Jupyter4NFDI** initiative.

## Acknowledgements

- **Sheeba Samuel** (TU Chemnitz) — the ReproScore framework
- **Tim Kreuzer** — Jupyter4NFDI infrastructure
- **FIZ Karlsruhe** and the **NFDI** JupyterHub service

## License

Apache 2.0 — see [LICENSE](LICENSE).
