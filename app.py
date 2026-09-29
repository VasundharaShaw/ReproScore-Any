import os, shutil, sqlite3, subprocess, sys, tempfile, time, traceback, json
import io, zipfile
from datetime import datetime, timezone
import requests
from pathlib import Path
import gradio as gr

# --- OAuth wrapper additions (imports) ---
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
try:
    from jupyterhub.services.auth import HubOAuth
except Exception:  # jupyterhub not installed in some environments (e.g. HF Space)
    HubOAuth = None
# --- end additions ---

# --- Hub-native bridge (optional; present on Hub, no-op on HF) ---
_BRIDGE_IMPORT_ERROR = None
try:
    from execution.bridge.hub_runner import run_execution_on_hub
    from pipeline.import_execution import import_execution
except Exception as _e:
    import traceback as _tb
    run_execution_on_hub = None
    import_execution = None
    _BRIDGE_IMPORT_ERROR = _tb.format_exc()
# --- end bridge imports ---

# --- CSV/PDF export ---
_EXPORT_IMPORT_ERROR = None
try:
    from pipeline.export import to_csv, to_pdf
except Exception:
    to_csv = to_pdf = None
    _EXPORT_IMPORT_ERROR = traceback.format_exc()
# --- end export imports ---

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


# (GitHub Actions backend removed - Hub-native execution only.)


def _fmt(v):
    if v is None:
        return "N/A"
    return f"🟢 {v}" if v >= 60 else (f"🟡 {v}" if v >= 30 else f"🔴 {v}")


def _nb_rows_from_json(notebooks):
    rows = []
    for nb in notebooks:
        repro = nb.get("repro")
        repro_str = f"{repro * 100:.1f}%" if isinstance(repro, (int, float)) else "—"
        dur = nb.get("duration")
        dur_str = f"{dur:.1f}s" if isinstance(dur, (int, float)) else "—"
        rows.append([nb.get("notebook", "—"), nb.get("status", "—"), dur_str,
                     str(nb.get("cells", "—")), str(nb.get("errors", "—")), repro_str])
    return rows


def build_summary(repo_name, scores, nb_count, ros_pending):
    ros_disp = "⏳ Running on JupyterHub…" if ros_pending else _fmt(scores.get("ros"))
    rcs_disp = "⏳ Running on JupyterHub…" if ros_pending else _fmt(scores.get("rcs"))
    summary = f"""## 📊 Results for `{repo_name}`

### Scores

| Metric | Score | What it measures |
|---|---|---|
| **RRS** — Readiness | {_fmt(scores.get('rrs'))} | How the repository is *set up* (static, 26 sub-metrics) |
| **ROS** — Outcome | {ros_disp} | Whether it *ran* (execution probes) |
| **RCS** — Composite | {rcs_disp} | Blend, weighted by execution evidence (α ≤ 0.70) |

### RRS Categories

| Category | Weight | Score | What it looks for |
|---|---|---|---|
| **E** — Environment | 0.30 | {_fmt(scores.get('score_E'))} | Lockfiles, pinned deps, container spec, Python version |
| **A** — Data | 0.25 | {_fmt(scores.get('score_A'))} | Data described, pointer to data, acquisition script |
| **D** — Documentation | 0.20 | {_fmt(scores.get('score_D'))} | README, install steps, usage examples, entry point |
| **C** — Code Portability | 0.15 | {_fmt(scores.get('score_C'))} | No absolute paths, resolvable imports, no secrets |
| **S** — Repro Signals | 0.10 | {_fmt(scores.get('score_S'))} | Seeds, execution order, tests, CI, expected outputs |

Categories are gated before weighting — partial credit is deliberately cheap —
and hard penalties apply if Environment or Data score below 10.

### Notebooks: {nb_count} executed
"""
    if (not ros_pending) and scores.get("ros") is None:
        summary += NO_EXECUTION_EVIDENCE_NOTE
    summary += RESULT_FOOTNOTE_MD
    return summary


EXPORT_MAX_AGE_S = 3600  # export files kept 1 h so users can still download them


def _cleanup_old_exports():
    """Delete reproscore_export_* temp dirs older than EXPORT_MAX_AGE_S."""
    cutoff = time.time() - EXPORT_MAX_AGE_S
    for d in Path(tempfile.gettempdir()).glob("reproscore_export_*"):
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError as e:
            print(f"[export] cleanup skipped {d}: {e!r}", flush=True)


def run_pipeline(github_url, progress=gr.Progress(), request: gr.Request = None):
    logs = []
    tmpdir = None
    try:
        url = validate_github_url(github_url)
        if not url:
            return "❌ Please enter a valid GitHub repository URL.", "", [], "", None, None
        repo_name = url.rstrip("/").split("/")[-1].removesuffix(".git")
        repo_slug = "/".join(url.rstrip("/").removesuffix(".git").split("/")[-2:])
        logs.append(f"🚀 Starting: {url}")

        progress(0.05, desc="Cloning repository (RRS)...")
        tmpdir = Path(tempfile.mkdtemp())
        repo_dir = tmpdir / repo_name
        r = subprocess.run(["git", "clone", "--depth", "1", url, str(repo_dir)],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if r.returncode != 0:
            logs.append(f"❌ Clone failed: {r.stderr[:300]}")
            return "\n".join(logs), "\n".join(logs), [], "", None, None
        logs.append("✅ Clone complete.")
        _c = subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True)
        repo_commit = _c.stdout.strip() if _c.returncode == 0 else None

        # ---- RRS: local static analysis, instant ----
        progress(0.15, desc="Running RRS static analysis...")
        db_path = tmpdir / "_score.sqlite"
        con = sqlite3.connect(db_path)
        con.execute("""CREATE TABLE IF NOT EXISTS repo_targets (
            id INTEGER PRIMARY KEY, repository TEXT, notebooks TEXT, setups TEXT,
            requirements TEXT, notebooks_count INTEGER DEFAULT 0,
            setups_count INTEGER DEFAULT 0, requirements_count INTEGER DEFAULT 0,
            rrs REAL, score_E REAL, score_A REAL, score_D REAL, score_C REAL,
            score_S REAL, ros REAL, rcs REAL, paper_doi TEXT)""")
        con.execute("INSERT INTO repo_targets (repository) VALUES (?)", (repo_slug,))
        repo_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.commit(); con.close()
        score_script = REPO_ROOT / "pipeline" / "score.py"
        subprocess.run([sys.executable, str(score_script),
            "--repo-dir", str(repo_dir), "--repo-id", str(repo_id), "--db", str(db_path)],
            capture_output=True, text=True, timeout=60)
        con = sqlite3.connect(db_path)
        row = con.execute("SELECT rrs,score_E,score_A,score_D,score_C,score_S "
                          "FROM repo_targets WHERE id=?", (repo_id,)).fetchone()
        con.close()
        keys = ["rrs", "score_E", "score_A", "score_D", "score_C", "score_S"]
        scores = {k: round(v, 1) if v is not None else None
                  for k, v in zip(keys, row)} if row else {}
        scores["ros"] = None
        scores["rcs"] = None
        logs.append(f"✅ RRS={scores.get('rrs')}")

        # ---- Tier 2: real ROS/RCS via Hub-native execution ----
        # Auth provider -> user token (cookie) -> REST spawn -> Jupyter API -> teardown.
        notebooks = []
        hub_token = None
        def _auth_log(msg):
            print(msg, flush=True)
            logs.append(msg)
        if auth is None:
            _auth_log("[auth] auth is None — HubOAuth/_HUB_API_TOKEN not configured in pod")
        elif request is None:
            _auth_log("[auth] request is None — run_pipeline got no gr.Request")
        else:
            _t = request.cookies.get(TOKEN_COOKIE)
            if not _t:
                _auth_log(f"[auth] cookie {TOKEN_COOKIE!r} missing; cookies={sorted(request.cookies)}")
            else:
                _auth_log(f"[auth] cookie present len={len(_t)}")
                try:
                    _u = auth.user_for_token(_t)
                except Exception as e:
                    _auth_log(f"[auth] user_for_token raised: {e!r}")
                    _u = None
                if _u is None:
                    _auth_log("[auth] user_for_token returned None — token not recognized")
                else:
                    hub_token = _t
                    _auth_log(f"[auth] authenticated as {_u}")

        if hub_token and (run_execution_on_hub is None or import_execution is None):
            logs.append(f"[exec] bridge import failed at startup: {_BRIDGE_IMPORT_ERROR}")
        if hub_token and run_execution_on_hub is not None and import_execution is not None:
            try:
                nb_paths = sorted(
                    str(p.relative_to(repo_dir))
                    for p in repo_dir.rglob("*.ipynb")
                    if ".ipynb_checkpoints" not in p.parts)
                if not nb_paths:
                    raise RuntimeError("no notebooks found in repo")
                logs.append(f"Executing on JupyterHub ({len(nb_paths)} notebook(s)) ...")
                exec_db = tmpdir / "_exec.sqlite"
                def _st(elapsed, status):
                    progress(min(0.30 + elapsed / 300.0 * 0.30, 0.60),
                             desc=f"Spawning server ({status}, {elapsed}s)...")
                summary = run_execution_on_hub(
                    token=hub_token, repo=repo_slug, notebook_paths=nb_paths,
                    exec_db=str(exec_db), on_status=_st, on_log=logs.append)
                progress(0.85, desc="Importing execution evidence...")
                import_execution(str(exec_db), str(db_path))
                progress(0.90, desc="Scoring ROS/RCS...")
                subprocess.run([sys.executable, str(score_script),
                    "--repo-dir", str(repo_dir), "--repo-id", str(repo_id),
                    "--db", str(db_path)], capture_output=True, text=True, timeout=120)
                con = sqlite3.connect(db_path)
                _row = con.execute("SELECT ros, rcs FROM repo_targets WHERE id=?",
                                   (repo_id,)).fetchone()
                con.close()
                if _row:
                    scores["ros"] = round(_row[0], 1) if _row[0] is not None else None
                    scores["rcs"] = round(_row[1], 1) if _row[1] is not None else None
                notebooks = [
                    {"notebook": n.get("notebook"), "status": n.get("execution_status"),
                     "repro": n.get("reproducibility_score")}
                    for n in summary.get("notebooks", [])]
                logs.append(f"ROS={scores.get('ros')} RCS={scores.get('rcs')} "
                            f"- {len(notebooks)} notebook(s) [Hub].")
            except Exception as e:
                logs.append(f"Hub execution failed: {e}. Showing RRS only.")
        else:
            logs.append("No JupyterHub login token - showing RRS only. "
                        "Log in to run execution (ROS/RCS).")

        nb_rows = _nb_rows_from_json(notebooks)

        # ---- CSV/PDF export ----
        # Written to their own tempdir: tmpdir is deleted in `finally`,
        # and gr.File needs the files to exist after this function returns.
        csv_path = pdf_path = None
        if to_csv is None or to_pdf is None:
            logs.append(f"[export] import failed at startup: {_EXPORT_IMPORT_ERROR}")
        else:
            try:
                _cleanup_old_exports()
                export_dir = Path(tempfile.mkdtemp(prefix="reproscore_export_"))
                stem = repo_slug.replace("/", "_")
                result = {"repo": repo_slug, "commit": repo_commit,
                          "rubric": "default", **scores}
                csv_path = to_csv(result, str(export_dir / f"{stem}_reproscore.csv"))
                pdf_path = to_pdf(result, str(export_dir / f"{stem}_reproscore.pdf"))
                logs.append(f"[export] wrote {csv_path} and {pdf_path}")
            except Exception as e:
                logs.append(f"[export] failed: {e!r}\n{traceback.format_exc()}")
                csv_path = pdf_path = None

        progress(1.0, desc="Done!")
        logs.append("🏁 Done!")
        return build_summary(repo_name, scores, len(nb_rows), ros_pending=False), \
              "\n".join(logs), nb_rows, url, csv_path, pdf_path
    except Exception as e:
        logs.append(f"❌ Error: {e}\n{traceback.format_exc()}")
        return "\n".join(logs), "\n".join(logs), [], "", None, None
    finally:
        if tmpdir and tmpdir.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# OAuth token-read wrapper (minimal — proves the visiting user's token/scopes).
# Does NOT spawn anything yet. GActions path above is untouched.
# ---------------------------------------------------------------------------
SERVICE_PREFIX = os.environ.get("JUPYTERHUB_SERVICE_PREFIX", "/")
_HUB_API_TOKEN = os.environ.get("JUPYTERHUB_API_TOKEN")
TOKEN_COOKIE = "reproscore-token"

# auth is None when not running under JupyterHub (e.g. HF Space) — the app then
# behaves exactly as before, and the debug tab reports that cleanly.
auth = None
if HubOAuth is not None and _HUB_API_TOKEN:
    auth = HubOAuth(api_token=_HUB_API_TOKEN, cache_max_age=60)


def _login_path():
    base = SERVICE_PREFIX.rstrip("/")
    return (base + "/login") if base else "/login"


def show_my_scopes(request: gr.Request):
    """Read the visiting user's OAuth token off the cookie and report its scopes."""
    if auth is None:
        return ("Not running under JupyterHub (no JUPYTERHUB_API_TOKEN). "
                "The OAuth path is inactive here; this is expected on the HF Space.")
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return (f"No token yet. Visit  {_login_path()}  once in this browser to "
                f"run the OAuth login, then come back and click again.")
    user = auth.user_for_token(token)
    if not user:
        return "Token present but invalid or expired. Re-run login."
    scopes = user.get("scopes", [])
    lines = [f"user: {user.get('name')}", "",
             f"token: {token}", "",
             "scopes:"]
    lines += [f"  {s}" for s in sorted(scopes)]
    want = {"servers", "access:servers"}
    have = {s.split("!")[0] for s in scopes}
    lines += ["", "spawn-capable: " + ("YES" if want & have else "NO — server scopes missing")]
    return "\n".join(lines)


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
            with gr.Row():
                csv_file = gr.File(label="⬇️ Download CSV", interactive=False)
                pdf_file = gr.File(label="⬇️ Download PDF", interactive=False)
        with gr.TabItem("📓 Notebooks"):
            nb_table = gr.Dataframe(
                headers=["Notebook","Status","Duration","Cells","Errors","Repro Score"],
                interactive=False,
            )
        with gr.TabItem("ℹ️ How to read these scores"):
            gr.Markdown(SCORE_LEGEND_MD)
        with gr.TabItem("📋 Logs"):
            logs_box = gr.Textbox(label="Logs", lines=20, interactive=False)
        with gr.TabItem("🔑 Auth (debug)"):
            gr.Markdown(
                "Temporary debug tab. Reads the visiting user's Hub OAuth token "
                "from the `reproscore-token` cookie and shows its scopes. "
                "Proves whether the token that reaches this service can spawn a server."
            )
            scopes_btn = gr.Button("Show my Hub token scopes")
            scopes_out = gr.Textbox(label="Token scopes", lines=14, interactive=False)
    repo_state = gr.State("")
    run_btn.click(
        fn=run_pipeline,
        inputs=[url_input],
        outputs=[results_md, logs_box, nb_table, repo_state, csv_file, pdf_file],
    )
    scopes_btn.click(fn=show_my_scopes, inputs=None, outputs=[scopes_out])


demo.queue()

# ---------------------------------------------------------------------------
# FastAPI wrapper owns the OAuth dance; Gradio is mounted underneath and only
# reads the token cookie. When auth is None, this still runs as a plain app.
# ---------------------------------------------------------------------------
fastapi_app = FastAPI()


@fastapi_app.get(SERVICE_PREFIX + "oauth_callback")
async def oauth_callback(request: Request):
    if auth is None:
        return Response("OAuth not configured", status_code=404)
    code = request.query_params.get("code")
    if code is None:
        return Response("Forbidden", status_code=403)
    arg_state = request.query_params.get("state")
    cookie_state = request.cookies.get(auth.state_cookie_name)
    if arg_state is None or arg_state != cookie_state:
        return Response("Forbidden", status_code=403)
    token = auth.token_for_code(code)
    next_url = auth.get_next_url(cookie_state) or SERVICE_PREFIX
    resp = RedirectResponse(next_url, status_code=302)
    resp.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="lax")
    return resp


@fastapi_app.get(_login_path())
async def login(request: Request):
    if auth is None:
        return Response("OAuth not configured", status_code=404)
    state = auth.generate_state(next_url=SERVICE_PREFIX)
    resp = RedirectResponse(auth.login_url + f"&state={state}", status_code=302)
    resp.set_cookie(auth.state_cookie_name, state, httponly=True, samesite="lax")
    return resp


_mount_path = SERVICE_PREFIX.rstrip("/") or "/"
app = gr.mount_gradio_app(fastapi_app, demo, path=_mount_path)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)
