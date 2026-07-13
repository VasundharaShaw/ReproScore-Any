import os, shutil, sqlite3, subprocess, sys, tempfile, time, traceback, json
from pathlib import Path
import gradio as gr

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
NOTEBOOK_TIMEOUT = 120
MAX_NOTEBOOKS = 5

# ---------------------------------------------------------------------------
# Score legend — shown in its own tab and summarised under every result.
# Content derived directly from pipeline/reproscore/src/scoring/{rrs,ros,rcs}.py
# ---------------------------------------------------------------------------

SCORE_LEGEND_MD = """
## How to read these scores

ReproScore reports **three** numbers, on **two tiers**. They answer different
questions and are not interchangeable.

| Score | Tier | Question it answers |
|---|---|---|
| **RRS** | Static | Is this repository *set up* to be reproducible? |
| **ROS** | Execution | Did it *actually run*? |
| **RCS** | Composite | Blend of the two, weighted by how much execution evidence exists |

---

### RRS — Reproducibility Readiness Score (0–100)

Computed from the repository's files alone. No code is run. 26 sub-metrics are
grouped into five categories:

| | Category | Weight | What it looks for |
|---|---|---|---|
| **E** | Environment Specification | **0.30** | Lockfiles, pinned dependencies, container spec, environment bootstrap, declared Python version |
| **A** | Data Accessibility | **0.25** | Data described, a pointer to where data lives, acquisition script, workflow orchestration |
| **D** | Documentation | **0.20** | README structure, install instructions, usage examples, inline explanation, entry point, docstrings, licence/citation metadata |
| **C** | Code Portability | **0.15** | No absolute paths, resolvable imports, no hardcoded credentials, no silently swallowed errors |
| **S** | Reproducibility Signals | **0.10** | Random seeds set, notebooks in linear execution order, tests, expected outputs, CI, externalised config, hardware requirements |

**Partial credit is deliberately cheap.** Each category passes through a gate
before it is weighted. Above a threshold τ the contribution is linear; below τ it
is compressed super-linearly. A category scoring half of τ contributes
considerably *less* than half. Thresholds: E τ=40, A τ=30, S τ=30, C τ=25, D τ=20.

**Three hard penalties** are then subtracted from the total:

| Trigger | Penalty |
|---|---|
| Environment score below 10 | **−20** |
| Data score below 10 | **−15** |
| Seed coverage below 50% | **−10** |

A repository with neither an environment specification nor a data pointer
therefore starts 35 points down. Low RRS values are common and are not an error.

---

### ROS — Reproducibility Outcome Score (0–100)

Computed only where sandboxed execution evidence exists. Six probes:

| Probe | Weight |
|---|---|
| Install success | 0.30 |
| Execution success | 0.25 |
| Output determinism | 0.20 |
| Notebook execution rate | 0.10 |
| Import success rate | 0.10 |
| Test pass rate | 0.05 |

ROS normalises over whichever probes are available, so a partial run still yields
a comparable 0–100 figure.

---

### RCS — Reproducibility Composite Score (0–100)

`RCS = (1 − α) · RRS + α · ROS`

α scales with how much execution evidence was collected and is **capped at 0.70**.
Two consequences worth stating plainly:

- **With no execution evidence, RCS is identical to RRS.** This is by design, not a bug.
- **Even under full execution coverage, RRS never falls below 30% of the composite.**
  Running successfully cannot fully redeem a badly specified repository.

---

### Colour bands

| Band | Meaning |
|---|---|
| 🟢 **60–100** | Strong |
| 🟡 **30–59** | Partial |
| 🔴 **0–29** | Weak |

These bands are **interpretive aids, not validated thresholds.** They are useful
for triage and comparison; they are not a pass/fail line.

---

### The most important caveat

**A high RRS does not predict that a repository will run.** Readiness and outcome
are measured separately precisely because they diverge — a well-documented,
fully-pinned repository can still fail on a missing data file or an
unavailable system library, and a scruffy repository with no README can run
first time. Read RRS and ROS as two independent findings, not as an estimate and
its confirmation.
"""

# Short version appended beneath each result table.
RESULT_FOOTNOTE_MD = """
---
🟢 60–100 · 🟡 30–59 · 🔴 0–29 — interpretive bands, not pass/fail thresholds.

**RRS** = how the repository is *set up* (static, 26 sub-metrics).
**ROS** = whether it *ran* (execution probes).
**RCS** = blend of the two.
See the **ℹ️ How to read these scores** tab for the full rubric.
"""

NO_EXECUTION_EVIDENCE_NOTE = """
> **Note on ROS and RCS.** This scorer computes RRS by static analysis only — it
> does not feed notebook execution results back into the outcome score. ROS is
> therefore reported as `N/A`, and RCS collapses to RRS, which is the defined
> behaviour when no execution evidence is available. Notebook execution results
> are shown separately in the **📓 Notebooks** tab.
"""


def validate_github_url(url):
    url = url.strip().rstrip("/")
    if not url or "github.com" not in url:
        return None
    if not url.startswith("http"):
        url = "https://" + url
    return url

def run_pipeline(github_url, progress=gr.Progress()):
    logs = []
    tmpdir = None
    try:
        url = validate_github_url(github_url)
        if not url:
            return "❌ Please enter a valid GitHub repository URL.", "", [], ""
        repo_name = url.rstrip("/").split("/")[-1].removesuffix(".git")
        logs.append(f"🚀 Starting pipeline for: {url}")
        progress(0.05, desc="Cloning repository...")
        tmpdir = Path(tempfile.mkdtemp())
        repo_dir = tmpdir / repo_name
        r = subprocess.run(["git","clone","--depth","1",url,str(repo_dir)],
            capture_output=True, text=True, timeout=60,
            env={**os.environ,"GIT_TERMINAL_PROMPT":"0"})
        if r.returncode != 0:
            logs.append(f"❌ Clone failed: {r.stderr[:300]}")
            return "\n".join(logs),"",[],""
        logs.append("✅ Clone complete.")
        progress(0.2, desc="Discovering notebooks...")
        nbs = sorted(p for p in repo_dir.rglob("*.ipynb")
            if ".ipynb_checkpoints" not in p.parts and not p.stem.endswith("_output"))
        if not nbs:
            logs.append("⚠️ No notebooks found.")
            return "\n".join(logs),"",[],""
        if len(nbs) > MAX_NOTEBOOKS:
            logs.append(f"⚠️ Capping at {MAX_NOTEBOOKS} notebooks.")
            nbs = nbs[:MAX_NOTEBOOKS]
        logs.append(f"📓 Found {len(nbs)} notebook(s).")
        progress(0.3, desc="Running RRS static analysis...")
        db_path = tmpdir / "_score.sqlite"
        con = sqlite3.connect(db_path)
        con.execute("""CREATE TABLE IF NOT EXISTS repo_targets (
            id INTEGER PRIMARY KEY, repository TEXT, notebooks TEXT, setups TEXT,
            requirements TEXT, notebooks_count INTEGER DEFAULT 0,
            setups_count INTEGER DEFAULT 0, requirements_count INTEGER DEFAULT 0,
            rrs REAL, score_E REAL, score_A REAL, score_D REAL, score_C REAL,
            score_S REAL, ros REAL, rcs REAL, paper_doi TEXT)""")
        con.execute("INSERT INTO repo_targets (repository) VALUES (?)",(repo_name,))
        repo_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.commit(); con.close()
        score_script = REPO_ROOT / "pipeline" / "score.py"
        subprocess.run([sys.executable,str(score_script),
            "--repo-dir",str(repo_dir),"--repo-id",str(repo_id),"--db",str(db_path)],
            capture_output=True, text=True, timeout=60)
        con = sqlite3.connect(db_path)
        row = con.execute("SELECT rrs,score_E,score_A,score_D,score_C,score_S,ros,rcs FROM repo_targets WHERE id=?",(repo_id,)).fetchone()
        con.close()
        keys = ["rrs","score_E","score_A","score_D","score_C","score_S","ros","rcs"]
        scores = {k: round(v,1) if v is not None else None for k,v in zip(keys,row)} if row else {}
        logs.append(f"✅ RRS={scores.get('rrs')} ROS={scores.get('ros')} RCS={scores.get('rcs')}")
        nb_results = []
        for i,nb in enumerate(nbs):
            progress(0.4+0.5*(i/max(len(nbs),1)), desc=f"Executing {nb.name}...")
            logs.append(f"▶️ Executing {nb.name}...")
            out = nb.parent / f"{nb.stem}_output.ipynb"
            start = time.monotonic()
            subprocess.run([sys.executable,"-m","jupyter","nbconvert",
                "--to","notebook","--execute","--allow-errors",
                f"--ExecutePreprocessor.timeout={NOTEBOOK_TIMEOUT}",
                "--ExecutePreprocessor.kernel_name=python3",
                str(nb),"--output",str(out)],
                capture_output=True, text=True, timeout=NOTEBOOK_TIMEOUT+10)
            duration = round(time.monotonic()-start,1)
            if not out.exists():
                logs.append(f"  ❌ Failed ({duration}s)")
                nb_results.append([nb.name,"FAILED",f"{duration}s","—","—","0%"])
                continue
            nb_data = json.loads(out.read_text(errors="replace"))
            total = sum(1 for c in nb_data.get("cells",[]) if c.get("cell_type")=="code")
            errors = sum(1 for c in nb_data.get("cells",[]) if c.get("cell_type")=="code"
                for o in c.get("outputs",[]) if o.get("output_type")=="error")
            nb_orig = json.loads(nb.read_text(errors="replace"))
            orig_cells = [c for c in nb_orig.get("cells",[]) if c.get("cell_type")=="code"]
            exec_cells = [c for c in nb_data.get("cells",[]) if c.get("cell_type")=="code"]
            n = min(len(orig_cells),len(exec_cells))
            identical = sum(1 for o,e in zip(orig_cells,exec_cells) if str(o.get("outputs",""))==str(e.get("outputs","")))
            score = round(identical/n*100,1) if n>0 else 0
            status = "SUCCESS_WITH_ERRORS" if errors else "SUCCESS"
            logs.append(f"  {'⚠️' if errors else '✅'} {status} — {total} cells, {errors} errors ({duration}s)")
            nb_results.append([nb.name, status, f"{duration}s", str(total), str(errors), f"{score}%"])

        def fmt(v):
            if v is None: return "N/A"
            return f"🟢 {v}" if v>=60 else f"🟡 {v}" if v>=30 else f"🔴 {v}"

        summary = f"""## 📊 Results for `{repo_name}`

### Scores

| Metric | Score | What it measures |
|---|---|---|
| **RRS** — Readiness | {fmt(scores.get('rrs'))} | How the repository is *set up* (static, 26 sub-metrics) |
| **ROS** — Outcome | {fmt(scores.get('ros'))} | Whether it *ran* (execution probes) |
| **RCS** — Composite | {fmt(scores.get('rcs'))} | Blend, weighted by execution evidence (α ≤ 0.70) |

### RRS Categories

| Category | Weight | Score | What it looks for |
|---|---|---|---|
| **E** — Environment | 0.30 | {fmt(scores.get('score_E'))} | Lockfiles, pinned deps, container spec, Python version |
| **A** — Data | 0.25 | {fmt(scores.get('score_A'))} | Data described, pointer to data, acquisition script |
| **D** — Documentation | 0.20 | {fmt(scores.get('score_D'))} | README, install steps, usage examples, entry point |
| **C** — Code Portability | 0.15 | {fmt(scores.get('score_C'))} | No absolute paths, resolvable imports, no secrets |
| **S** — Repro Signals | 0.10 | {fmt(scores.get('score_S'))} | Seeds, execution order, tests, CI, expected outputs |

Categories are gated before weighting — partial credit is deliberately cheap —
and hard penalties apply if Environment or Data score below 10.

### Notebooks: {len(nb_results)} executed
"""
        if scores.get("ros") is None:
            summary += NO_EXECUTION_EVIDENCE_NOTE
        summary += RESULT_FOOTNOTE_MD

        logs.append("🏁 Done!")
        progress(1.0, desc="Done!")
        return summary, "\n".join(logs), nb_results, url
    except Exception as e:
        logs.append(f"❌ Error: {e}\n{traceback.format_exc()}")
        return "\n".join(logs), "\n".join(logs), [], ""
    finally:
        if tmpdir and tmpdir.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)


with gr.Blocks(title="ReproScore") as demo:
    gr.HTML(
        "<div style='text-align:center;padding:1rem'>"
        "<h1>🔬 ReproScore</h1>"
        "<p>Score any GitHub repository for Jupyter notebook reproducibility.</p>"
        "<p style='font-size:0.9em;opacity:0.75;margin-top:0.4rem'>"
        "Three scores: <b>RRS</b> (is it set up to be reproducible?) · "
        "<b>ROS</b> (did it run?) · <b>RCS</b> (composite). "
        "See <i>How to read these scores</i> below."
        "</p></div>"
    )
    with gr.Row():
        with gr.Column(scale=4):
            url_input = gr.Textbox(label="GitHub Repository URL", placeholder="https://github.com/owner/repo")
        with gr.Column(scale=1, min_width=120):
            run_btn = gr.Button("🚀 Score Repo", variant="primary")
    gr.Examples(
        examples=[["https://github.com/caravangelo/inflation-easy"],
                  ["https://github.com/alecarones/broom"]],
        inputs=url_input,
    )
    with gr.Tabs():
        with gr.TabItem("📊 Results"):
            results_md = gr.Markdown("*Submit a repository URL to see results.*")
        with gr.TabItem("📓 Notebooks"):
            nb_table = gr.Dataframe(
                headers=["Notebook","Status","Duration","Cells","Errors","Repro Score"],
                interactive=False,
            )
        with gr.TabItem("ℹ️ How to read these scores"):
            gr.Markdown(SCORE_LEGEND_MD)
        with gr.TabItem("📋 Logs"):
            logs_box = gr.Textbox(label="Logs", lines=20, interactive=False)
    repo_state = gr.State("")
    run_btn.click(
        fn=run_pipeline,
        inputs=[url_input],
        outputs=[results_md, logs_box, nb_table, repo_state],
    )


if __name__ == "__main__":
    root_path = os.environ.get("GRADIO_ROOT_PATH", "")
    demo.launch(server_name="0.0.0.0", server_port=7860, root_path=root_path)
