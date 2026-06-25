import os, shutil, sqlite3, subprocess, sys, tempfile, time, traceback, json
from pathlib import Path
import gradio as gr

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))
NOTEBOOK_TIMEOUT = 120
MAX_NOTEBOOKS = 5

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
| Metric | Score |
|---|---|
| **RRS** | {fmt(scores.get('rrs'))} |
| **ROS** | {fmt(scores.get('ros'))} |
| **RCS** | {fmt(scores.get('rcs'))} |

### RRS Categories
| Category | Score |
|---|---|
| Environment (E) | {fmt(scores.get('score_E'))} |
| Data (A) | {fmt(scores.get('score_A'))} |
| Documentation (D) | {fmt(scores.get('score_D'))} |
| Code Portability (C) | {fmt(scores.get('score_C'))} |
| Repro Signals (S) | {fmt(scores.get('score_S'))} |

### Notebooks: {len(nb_results)} executed"""
        logs.append("🏁 Done!")
        progress(1.0, desc="Done!")
        return summary, "\n".join(logs), nb_results, url
    except Exception as e:
        logs.append(f"❌ Error: {e}\n{traceback.format_exc()}")
        return "\n".join(logs), "\n".join(logs), [], ""
    finally:
        if tmpdir and tmpdir.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)

with gr.Blocks(title="Reproducibility Astro", ) as demo:
    gr.HTML("<div style='text-align:center;padding:1rem'><h1>🔭 Reproducibility Astro</h1><p>Score any astrophysics GitHub repository for notebook reproducibility.</p></div>")
    with gr.Row():
        with gr.Column(scale=4):
            url_input = gr.Textbox(label="GitHub Repository URL", placeholder="https://github.com/caravangelo/inflation-easy")
        with gr.Column(scale=1, min_width=120):
            run_btn = gr.Button("🚀 Score Repo", variant="primary")
    gr.Examples(examples=[["https://github.com/caravangelo/inflation-easy"],["https://github.com/alecarones/broom"]], inputs=url_input)
    with gr.Tabs():
        with gr.TabItem("📊 Results"):
            results_md = gr.Markdown("*Submit a repository URL to see results.*")
        with gr.TabItem("📓 Notebooks"):
            nb_table = gr.Dataframe(headers=["Notebook","Status","Duration","Cells","Errors","Repro Score"], interactive=False)
        with gr.TabItem("📋 Logs"):
            logs_box = gr.Textbox(label="Logs", lines=20, interactive=False, )
    repo_state = gr.State("")
    run_btn.click(fn=run_pipeline, inputs=[url_input], outputs=[results_md, logs_box, nb_table, repo_state])

if __name__ == "__main__":
    demo.launch()
