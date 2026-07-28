# Containing the Reproducibility Gap

**Automated Repository-Level Reproducibility Assessment for Scholarly Jupyter Notebooks**

[![License: GPL-3.0](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Platform: NFDI JupyterHub](https://img.shields.io/badge/platform-NFDI%20JupyterHub-orange.svg)](https://hub.nfdi-jupyter.de)

---

## Overview

This pipeline automatically clones GitHub repositories containing Jupyter notebooks, re-executes them in isolated Python environments, and measures how reproducible the results are. It is designed to run on the **[NFDI JupyterHub](https://hub.nfdi-jupyter.de)** — no local Docker installation needed.

Results are stored in a SQLite database for downstream analysis.

### What it does

1. **Clones** a GitHub repository containing Jupyter notebooks
2. **Detects** the required Python version from the repo's metadata
3. **Creates** an isolated pyenv + venv environment per repository
4. **Executes** each notebook via `nbconvert`
5. **Compares** original vs. re-executed outputs
6. **Stores** cell-level reproducibility scores in a SQLite database

---

## Running on NFDI JupyterHub (recommended)

1. Go to [hub.nfdi-jupyter.de](https://hub.nfdi-jupyter.de/hub/home)
2. Click **Start Server** and choose **Repo2docker (Binder)**
3. Fill in the form:
   - **Repository URL**: `https://github.com/VasundharaShaw/CPRPMC_test`
   - **Git ref**: `main`
   - **Flavor**: `4GB RAM, 1 vCPU` (minimum recommended)
4. Click **Start** — the environment will build automatically
5. Once JupyterLab opens, launch a terminal and run:

```bash
cd /home/jovyan
bash run.sh
```

---

## Repository Structure

```
CPRPMC_test/
├── run.sh                   # Single entry point — checks deps, launches pipeline
├── binder/                  # repo2docker configuration
├── config/
│   └── config.sh            # Pipeline configuration (paths, settings)
├── data/
│   └── db.sqlite            # Source database (5241 repositories from Sheeba's study)
├── input/                   # Input repo lists for batch mode
├── output/                  # All pipeline outputs (created at runtime)
│   ├── cloned_repos/        # Cloned repositories
│   ├── db/                  # Pipeline results database (output/db/db.sqlite)
│   ├── logs/                # Per-repo execution logs
│   └── comparisons/         # JSON comparison reports
├── src/                     # Shell library functions
│   ├── pyenv.sh             # Python version detection + venv isolation
│   ├── repo.sh              # Repository cloning and processing
│   ├── requirements.sh      # Dependency extraction
│   ├── notebooks.sh         # Notebook execution and comparison logic
│   ├── db.sh                # Database operations + schema
│   ├── checks.sh            # Pre-flight validation
│   └── logging.sh           # Logging utilities
├── pipeline/
│   └── main.sh              # Main orchestrator
├── analysis/
│   ├── compare_notebook.py  # Output comparison script
│   ├── analyse_reporesults.ipynb  # Explore results interactively
│   └── nbprocess/           # Notebook processing utilities
├── docs/
│   ├── QUICKSTART.md
│   └── architecture.md
└── tests/
    └── test_pipeline.sh     # Smoke test
```

---

## Usage

Run from `/home/jovyan`:

```bash
bash run.sh
```

`run.sh` checks all dependencies first, then launches the pipeline. You will be prompted to choose a mode:

### Mode 1 — Single repository

Enter a GitHub repository URL directly. You will be asked for:

- **GitHub repo URL** — e.g. `https://github.com/example/repo`
- **Notebook paths** — semicolon-separated paths to `.ipynb` files within the repo
- **Setup paths** *(optional)* — paths to `setup.py` files
- **Requirements paths** *(optional)* — paths to `requirements.txt` files

### Mode 2 — Batch mode

Processes repositories from `data/db.sqlite` (the source database containing 5241 repositories from Sheeba Samuel's original reproducibility study). Results are written to `output/db/db.sqlite` — the source database is never modified.

---

## How Environment Isolation Works

Each repository gets its own isolated Python environment via **pyenv + venv**:

1. Detects the required Python version by checking (in order):
   - `binder/runtime.txt`
   - `runtime.txt`
   - `.python-version`
   - `setup.py` / `setup.cfg` `python_requires` field
   - Falls back to Python 3.10
2. Installs that Python version via pyenv if not already present
3. Creates a fresh virtual environment under `~/.repo_venvs/<repo-name>/`
4. Installs the repo's dependencies into the venv
5. Executes all notebooks inside the venv
6. Cleans up the venv after execution

---

## Configuration

Edit `config/config.sh` or set environment variables before running:

```bash
export TARGET_COUNT=20    # Override batch size (default: 10)
bash run.sh
```

See `.env.example` for all available options.

---

## Database

The pipeline uses two separate SQLite databases:

| Database | Path | Purpose |
|---|---|---|
| Source DB | `data/db.sqlite` | Sheeba's original study — 5241 repositories. **Read-only, never modified.** |
| Output DB | `output/db/db.sqlite` | Created fresh by the pipeline. Stores all execution results. |

The output database is created automatically on first run. The following tables are written to `output/db/db.sqlite`:

| Table | Description |
|---|---|
| `repositories` | Repository metadata (URL, notebook count, requirements) |
| `repository_runs` | Per-run status, timestamps, duration |
| `notebook_executions` | Per-notebook execution results and errors |
| `notebook_reproducibility_metrics` | Cell-level reproducibility scores |

### Example queries

```sql
-- Repositories with highest average reproducibility score
SELECT r.repository, AVG(nrm.reproducibility_score) AS avg_score
FROM repositories r
JOIN notebook_reproducibility_metrics nrm ON r.id = nrm.repository_id
GROUP BY r.id
ORDER BY avg_score DESC
LIMIT 10;

-- Most common failure types
SELECT error_type, COUNT(*) AS count
FROM notebook_executions
WHERE execution_status NOT IN ('SUCCESS', 'SUCCESS_WITH_ERRORS')
GROUP BY error_type
ORDER BY count DESC;

-- Success rate by whether repo had a requirements.txt
SELECT
    CASE WHEN requirements IS NOT NULL THEN 'With requirements.txt'
         ELSE 'Without requirements.txt' END AS req_status,
    COUNT(*) AS total,
    SUM(CASE WHEN run_status = 'SUCCESS' THEN 1 ELSE 0 END) AS successful
FROM repositories r
JOIN repository_runs rr ON r.id = rr.repository_id
GROUP BY req_status;
```

To explore results interactively, open `analysis/analyse_reporesults.ipynb` in JupyterLab.

---

## Related Publications

- Samuel, S., & Mietchen, D. (2024). Computational reproducibility of Jupyter notebooks from biomedical publications. *GigaScience*, 13, giad113. [DOI: 10.1093/gigascience/giad113](https://doi.org/10.1093/gigascience/giad113)
- Samuel, S., & Mietchen, D. (2024). FAIR Jupyter: A Knowledge Graph Approach to Semantic Sharing and Granular Exploration of a Computational Notebook Reproducibility Dataset. *TGDK*, 2(2), 4:1–4:24. [DOI: 10.4230/TGDK.2.2.4](https://doi.org/10.4230/TGDK.2.2.4)
- Samuel, S., & Mietchen, D. (2023). Dataset of a study of computational reproducibility of Jupyter notebooks from biomedical publications. *Zenodo*. [DOI: 10.5281/zenodo.8226725](https://doi.org/10.5281/zenodo.8226725)

---

## Acknowledgments

Supported by the **Jupyter4NFDI** project (DFG 567156310), **find.software** (DFG 567156310), **MaRDI** (DFG 460135501), **SeDOA** (DFG 556323977), and **HYP*MOL** (DFG 514664767).

---

## Contact

- **GitHub Issues**: [open an issue](https://github.com/VasundharaShaw/CPRPMC_test/issues)
- **Email**: sheeba.samuel@informatik.tu-chemnitz.de
- **Research Group**: [Distributed and Self-organizing Systems, TU Chemnitz](https://vsr.informatik.tu-chemnitz.de/)
