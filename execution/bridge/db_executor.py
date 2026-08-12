"""
db_executor.py -- Design-A Hub-native execution backend.

Replaces the bash/papermill execution engine for the Hub fairjupyter service.
Given a *live, already-spawned* Jupyter server (base URL) and a user token, it:

  1. opens a repository_runs row (RUNNING),
  2. for each notebook: loads it via the Contents API, runs every code cell in
     order over ONE kernel WebSocket (fresh kernel per notebook, continue past
     cell errors), collects outputs, derives the execution outcome, and writes
     one notebook_executions row,
  3. finalizes the repository_runs row (SUCCESS / failure code).

It writes the execution DB (execution/output/db/db.sqlite) using the exact
schema from execution/src/db.sh, so pipeline/import_execution.py pulls it into
output/db/db.sqlite and pipeline/score.py scores it with no downstream change.

Outcome contract (must match pipeline/score.py):
  - X  (execution_success): a notebook is "clean" iff execution_status == "SUCCESS".
    Any runtime error -> "SUCCESS_WITH_ERRORS" -> counts as a fail for X.
  - E' (import success):    error_category == "DEPENDENCY_ERROR" flags a dep failure.
  - notebook_exec_rate:     a fully-run notebook needs executed_cells == total_code_cells,
                            so an errored-but-run cell still counts as executed.
  - run_status == "SUCCESS": the environment built and the repo ran (the I signal),
                            independent of per-notebook X.

Spawn/teardown of the server is NOT this module's job (the bridge proves that
separately); this module starts *after* a server is running and reachable.

Requires: requests, websocket-client, nbformat  (nbformat in base conda;
websocket-client 1.9.0 in the gradio env).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone

import requests
import websocket  # websocket-client
import nbformat

from .determinism import compute_determinism, insert_metrics


# ---------------------------------------------------------------------------
# VENDORED verbatim from execution/analysis/nbprocess/summary.py (Sheeba Samuel).
# These three functions define the ROS-critical error vocabulary. Keep them
# byte-identical to summary.py. Unify into a shared module in a later cleanup
# pass (do not fork the mapping).
# ---------------------------------------------------------------------------
def categorize_error_type(error_type: str) -> str:
    if not error_type:
        return "UNKNOWN_ERROR"

    error_type = error_type.strip()

    mapping = {
        "ModuleNotFoundError": "DEPENDENCY_ERROR",
        "ImportError": "DEPENDENCY_ERROR",
        "FileNotFoundError": "FILE_ERROR",
        "PermissionError": "FILE_ERROR",
        "KeyError": "DATA_ERROR",
        "ValueError": "DATA_ERROR",
        "TypeError": "CODE_ERROR",
        "AttributeError": "CODE_ERROR",
        "NameError": "CODE_ERROR",
        "SyntaxError": "CODE_ERROR",
        "MemoryError": "RESOURCE_ERROR",
        "TimeoutError": "RESOURCE_ERROR",
        "ConnectionError": "NETWORK_ERROR",
        "HTTPError": "NETWORK_ERROR",
        "KernelDeadError": "EXECUTION_ENVIRONMENT_ERROR",
        "CalledProcessError": "EXECUTION_ENVIRONMENT_ERROR",
    }

    return mapping.get(error_type, "OTHER_ERROR")


def extract_error_from_notebook(notebook):
    """Extract runtime errors from an executed notebook (list of dicts)."""
    errors = []

    if not hasattr(notebook, "cells"):
        return errors

    for i, cell in enumerate(notebook.cells):
        if cell.cell_type != "code":
            continue

        for output in cell.get("outputs", []):
            if output.get("output_type") == "error":
                error_type = output.get("ename")
                error_message = output.get("evalue")
                traceback = "\n".join(output.get("traceback", []))

                errors.append({
                    "cell_index": i,
                    "error_type": error_type,
                    "error_message": error_message,
                    "traceback": traceback,
                })

    return errors


def sanitize_error_message(msg: str, max_len: int = 500):
    if not msg:
        return None
    return msg.strip().replace("\n", " ")[:max_len]
# ---------------------------------------------------------------------------
# end vendored block
# ---------------------------------------------------------------------------


# --- execution DB schema (verbatim from execution/src/db.sh; idempotent) ----
_SCHEMA = """
CREATE TABLE IF NOT EXISTS repositories (id INTEGER PRIMARY KEY AUTOINCREMENT, repository TEXT, notebooks TEXT, setups TEXT, requirements TEXT, notebooks_count INTEGER, setups_count INTEGER, requirements_count INTEGER);
CREATE TABLE IF NOT EXISTS notebooks (id INTEGER PRIMARY KEY AUTOINCREMENT, repository_id INTEGER, name TEXT, language TEXT, FOREIGN KEY (repository_id) REFERENCES repositories(id));
CREATE TABLE IF NOT EXISTS repository_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, repository_id INTEGER NOT NULL, url TEXT, run_status TEXT NOT NULL, error_message TEXT, started_at TEXT, finished_at TEXT, duration_seconds FLOAT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (repository_id) REFERENCES repositories(id));
CREATE TABLE IF NOT EXISTS notebook_executions (id INTEGER PRIMARY KEY AUTOINCREMENT, repository_run_id INTEGER NOT NULL, repository_id INTEGER NOT NULL, notebook_id INTEGER NOT NULL, notebook_name TEXT, url TEXT, execution_status TEXT, execution_duration FLOAT, total_code_cells INTEGER, executed_cells INTEGER, error_type TEXT, error_category TEXT, error_message TEXT, error_cell_index INTEGER, error_count INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(repository_run_id, notebook_id), FOREIGN KEY (repository_run_id) REFERENCES repository_runs(id), FOREIGN KEY (repository_id) REFERENCES repositories(id), FOREIGN KEY (notebook_id) REFERENCES notebooks(id));
CREATE TABLE IF NOT EXISTS notebook_reproducibility_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, repository_run_id INTEGER NOT NULL, notebook_execution_id INTEGER NOT NULL, repository_id INTEGER NOT NULL, notebook_id INTEGER NOT NULL, total_code_cells INTEGER, identical_cells_count INTEGER, different_cells_count INTEGER, nondeterministic_cells_count INTEGER, identical_cells TEXT, different_cells TEXT, nondeterministic_cells TEXT, reproducibility_score REAL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(repository_run_id, notebook_id), FOREIGN KEY (repository_run_id) REFERENCES repository_runs(id), FOREIGN KEY (notebook_execution_id) REFERENCES notebook_executions(id), FOREIGN KEY (repository_id) REFERENCES repositories(id), FOREIGN KEY (notebook_id) REFERENCES notebooks(id));
"""


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _norm_repo_path(repo: str) -> str:
    """owner/repo (strip scheme/.git), matching repo.sh get_or_create_repo_id."""
    p = repo.strip()
    for pre in ("https://github.com/", "http://github.com/", "git@github.com:"):
        if p.startswith(pre):
            p = p[len(pre):]
    if p.endswith(".git"):
        p = p[:-4]
    return p.strip("/")


# ---------------------------------------------------------------------------
# Outcome derivation (pure; unit-tested offline, no network)
# ---------------------------------------------------------------------------
def derive_outcome(executed_nb, total_code_cells, executed_cells,
                   transport_failed=False, fail_reason=None, duration=None):
    """Map an executed notebook to notebook_executions column values.

    Follows summary.py's rule exactly:
      - transport/infra failure -> execution_status FAIL, error_category "ERROR"
      - else runtime errors present -> SUCCESS_WITH_ERRORS, category from 1st error
      - else -> SUCCESS
    (For FAIL we also fill error_type/message for diagnostics; score.py ignores
    error_type, so this stays contract-safe.)
    """
    runtime_errors = extract_error_from_notebook(executed_nb)
    error_count = len(runtime_errors)
    error_type = error_category = error_message = error_cell_index = None

    if transport_failed:
        execution_status = "FAIL"
        error_category = "ERROR"
        if runtime_errors:
            first = runtime_errors[0]
            error_type = first.get("error_type")
            error_message = sanitize_error_message(first.get("error_message"))
            error_cell_index = first.get("cell_index")
        elif fail_reason:
            error_type = fail_reason
    elif error_count > 0:
        first = runtime_errors[0]
        error_type = first.get("error_type")
        error_message = sanitize_error_message(first.get("error_message"))
        error_cell_index = first.get("cell_index")
        error_category = categorize_error_type(error_type)
        execution_status = "SUCCESS_WITH_ERRORS"
    else:
        execution_status = "SUCCESS"

    return {
        "execution_status": execution_status,
        "execution_duration": duration,
        "total_code_cells": total_code_cells,
        "executed_cells": executed_cells,
        "error_type": error_type,
        "error_category": error_category,
        "error_message": error_message,
        "error_cell_index": error_cell_index,
        "error_count": error_count,
    }


# ---------------------------------------------------------------------------
# Jupyter kernel over WebSocket
# ---------------------------------------------------------------------------
class _KernelSession:
    """One fresh kernel + WebSocket on a running Hub server.

    Uses `Authorization: token <TOKEN>` for both HTTP and WS (token auth exempts
    XSRF, per the bridge probes), and the same user token the bridge proved
    reaches the server API through the Hub proxy.
    """

    def __init__(self, server_base, token, kernel_name="python3", verify=True):
        self.server_base = server_base.rstrip("/") + "/"
        self.token = token
        self.kernel_name = kernel_name
        self.verify = verify
        self.session = uuid.uuid4().hex
        self.kid = None
        self.ws = None
        self._h = {"Authorization": f"token {token}"}

    def _api(self, path):
        return self.server_base + "api/" + path.lstrip("/")

    def start(self, startup_timeout=120.0):
        r = requests.post(self._api("kernels"),
                          headers=self._h, json={"name": self.kernel_name},
                          verify=self.verify, timeout=60)
        r.raise_for_status()
        self.kid = r.json()["id"]

        ws_base = self.server_base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        ws_url = f"{ws_base}api/kernels/{self.kid}/channels?session_id={self.session}"
        self.ws = websocket.create_connection(
            ws_url,
            header=[f"Authorization: token {self.token}"],
            enable_multithread=True,
        )
        self.ws.settimeout(1.0)
        self._wait_ready(startup_timeout)
        return self

    def _send(self, msg_type, content, channel="shell"):
        mid = uuid.uuid4().hex
        msg = {
            "header": {
                "msg_id": mid, "username": "reproscore", "session": self.session,
                "msg_type": msg_type, "version": "5.3",
                "date": datetime.now(timezone.utc).isoformat(),
            },
            "parent_header": {}, "metadata": {}, "content": content, "channel": channel,
        }
        self.ws.send(json.dumps(msg))
        return mid

    def _wait_ready(self, timeout):
        deadline = time.time() + timeout
        mid = self._send("kernel_info_request", {})
        while time.time() < deadline:
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                # re-poke occasionally in case the kernel wasn't listening yet
                mid = self._send("kernel_info_request", {})
                continue
            if not raw:
                continue
            m = json.loads(raw)
            if (m.get("channel") == "shell"
                    and m.get("header", {}).get("msg_type") == "kernel_info_reply"
                    and m.get("parent_header", {}).get("msg_id") == mid):
                return
        raise TimeoutError("kernel did not become ready")

    def execute(self, code, cell_timeout=300.0):
        """Run one cell. Return (outputs, executed, timed_out)."""
        content = {
            "code": code, "silent": False, "store_history": True,
            "user_expressions": {}, "allow_stdin": False, "stop_on_error": False,
        }
        mid = self._send("execute_request", content)

        outputs = []
        got_reply = idle = False
        deadline = time.time() + cell_timeout
        while time.time() < deadline and not (got_reply and idle):
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except (websocket.WebSocketConnectionClosedException, ConnectionError):
                # kernel died mid-cell
                return outputs, False, False
            if not raw:
                continue
            m = json.loads(raw)
            if m.get("parent_header", {}).get("msg_id") != mid:
                continue
            ch = m.get("channel")
            mt = m["header"]["msg_type"]
            if ch == "iopub":
                c = m.get("content", {})
                if mt == "stream":
                    outputs.append({"output_type": "stream",
                                    "name": c.get("name", "stdout"),
                                    "text": c.get("text", "")})
                elif mt == "execute_result":
                    outputs.append({"output_type": "execute_result",
                                    "data": c.get("data", {}),
                                    "metadata": c.get("metadata", {}),
                                    "execution_count": c.get("execution_count")})
                elif mt == "display_data":
                    outputs.append({"output_type": "display_data",
                                    "data": c.get("data", {}),
                                    "metadata": c.get("metadata", {})})
                elif mt == "error":
                    outputs.append({"output_type": "error",
                                    "ename": c.get("ename"),
                                    "evalue": c.get("evalue"),
                                    "traceback": c.get("traceback", [])})
                elif mt == "status" and c.get("execution_state") == "idle":
                    idle = True
            elif ch == "shell" and mt == "execute_reply":
                got_reply = True

        timed_out = not (got_reply and idle)
        return outputs, (got_reply and idle), timed_out

    def close(self):
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass
        try:
            if self.kid is not None:
                requests.delete(self._api(f"kernels/{self.kid}"),
                                headers=self._h, verify=self.verify, timeout=30)
        except Exception:
            pass


def _load_notebook(server_base, token, path, verify=True):
    base = server_base.rstrip("/") + "/"
    r = requests.get(base + "api/contents/" + path.lstrip("/"),
                     headers={"Authorization": f"token {token}"},
                     params={"type": "notebook", "content": "1"},
                     verify=verify, timeout=60)
    r.raise_for_status()
    model = r.json()
    if model.get("type") != "notebook" or model.get("content") is None:
        raise ValueError(f"not a notebook: {path}")
    return nbformat.reads(json.dumps(model["content"]), as_version=4)


def _execute_notebook(server_base, token, path, kernel_name="python3",
                      cell_timeout=300.0, startup_timeout=120.0, verify=True):
    """Run one notebook in a fresh kernel. Return (original_nb, executed_nb,
    total, executed, transport_failed, fail_reason, duration).

    original_nb is the committed notebook (author's stored outputs), preserved
    before execution overwrites cell outputs; the diff for the determinism
    metric (Phase 2) is committed-vs-reexecuted."""
    t0 = time.time()
    nb = _load_notebook(server_base, token, path, verify=verify)
    original_nb = copy.deepcopy(nb)  # committed outputs, kept for the diff

    code_cells = [c for c in nb.cells
                  if c.cell_type == "code" and c.get("source", "").strip()]
    total = len(code_cells)
    executed = 0
    transport_failed = False
    fail_reason = None

    kern = _KernelSession(server_base, token, kernel_name=kernel_name, verify=verify)
    try:
        kern.start(startup_timeout=startup_timeout)
    except Exception as e:
        return (original_nb, nb, total, 0, True,
                f"KernelStartError:{type(e).__name__}", time.time() - t0)

    try:
        for cell in code_cells:
            outputs, ran, timed_out = kern.execute(cell.source, cell_timeout=cell_timeout)
            cell["outputs"] = outputs
            if ran:
                executed += 1
            else:
                # kernel died or cell timed out -> notebook-level FAIL, stop here
                transport_failed = True
                fail_reason = "TimeoutError" if timed_out else "KernelDeadError"
                break
    finally:
        kern.close()

    return original_nb, nb, total, executed, transport_failed, fail_reason, time.time() - t0


# ---------------------------------------------------------------------------
# DB writes (execution schema)
# ---------------------------------------------------------------------------
def _ensure_schema(con):
    con.executescript(_SCHEMA)


def _get_or_create_repository(con, repo_path, notebook_paths):
    row = con.execute(
        "SELECT id FROM repositories WHERE repository = ? LIMIT 1", (repo_path,)
    ).fetchone()
    if row:
        return row[0]
    cur = con.execute(
        """INSERT INTO repositories
               (repository, notebooks, setups, requirements,
                notebooks_count, setups_count, requirements_count)
           VALUES (?, ?, '', '', ?, 0, 0)""",
        (repo_path, ";".join(notebook_paths), len(notebook_paths)),
    )
    return cur.lastrowid


def _ensure_notebook(con, repository_id, name):
    row = con.execute(
        "SELECT id FROM notebooks WHERE repository_id = ? AND name = ? LIMIT 1",
        (repository_id, name),
    ).fetchone()
    if row:
        return row[0]
    cur = con.execute(
        "INSERT INTO notebooks (repository_id, name, language) VALUES (?, ?, 'python')",
        (repository_id, name),
    )
    return cur.lastrowid


def _open_run(con, repository_id, url):
    cur = con.execute(
        """INSERT INTO repository_runs (repository_id, url, run_status, started_at)
           VALUES (?, ?, 'RUNNING', ?)""",
        (repository_id, url, _now_iso()),
    )
    return cur.lastrowid


def _finalize_run(con, run_id, status, error_message, duration):
    con.execute(
        """UPDATE repository_runs
           SET run_status = ?, error_message = ?, finished_at = ?, duration_seconds = ?
           WHERE id = ?""",
        (status, error_message, _now_iso(), duration, run_id),
    )


def _insert_execution(con, run_id, repository_id, notebook_id, notebook_name, url, outcome):
    cur = con.execute(
        """INSERT INTO notebook_executions
               (repository_run_id, repository_id, notebook_id, notebook_name, url,
                execution_status, execution_duration, total_code_cells, executed_cells,
                error_type, error_category, error_message, error_cell_index, error_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, repository_id, notebook_id, notebook_name, url,
         outcome["execution_status"], outcome["execution_duration"],
         outcome["total_code_cells"], outcome["executed_cells"],
         outcome["error_type"], outcome["error_category"], outcome["error_message"],
         outcome["error_cell_index"], outcome["error_count"]),
    )
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def run_repo_executions(*, server_base, token, repo, notebook_paths, db_file,
                        url=None, kernel_name="python3", cell_timeout=300.0,
                        startup_timeout=120.0, verify=True, compute_metrics=True):
    """Execute every notebook and write the tables. Returns a summary dict.

    With compute_metrics=True (Phase 2), also diffs each committed notebook
    against its re-executed form and writes notebook_reproducibility_metrics
    (the output-determinism signal score.py reads for Delta)."""
    repo_path = _norm_repo_path(repo)
    url = url or f"https://github.com/{repo_path}"
    notebook_paths = [p.strip() for p in notebook_paths if p.strip()]

    os.makedirs(os.path.dirname(os.path.abspath(db_file)), exist_ok=True)
    con = sqlite3.connect(db_file)
    con.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(con)

    repository_id = _get_or_create_repository(con, repo_path, notebook_paths)
    nb_ids = {p: _ensure_notebook(con, repository_id, p) for p in notebook_paths}
    run_id = _open_run(con, repository_id, url)
    con.commit()

    t0 = time.time()
    any_kernel_started = False
    results = []

    for path in notebook_paths:
        try:
            original_nb, nb, total, executed, transport_failed, fail_reason, dur = \
                _execute_notebook(
                    server_base, token, path, kernel_name=kernel_name,
                    cell_timeout=cell_timeout, startup_timeout=startup_timeout,
                    verify=verify)
        except Exception as e:
            # notebook could not even be loaded / kernel unreachable
            outcome = derive_outcome(nbformat.v4.new_notebook(), 0, 0,
                                     transport_failed=True,
                                     fail_reason=f"{type(e).__name__}", duration=None)
            _insert_execution(con, run_id, repository_id, nb_ids[path], path, url, outcome)
            results.append((path, outcome["execution_status"],
                            outcome["error_category"], None))
            continue

        if executed > 0 or not transport_failed:
            any_kernel_started = True

        outcome = derive_outcome(nb, total, executed,
                                 transport_failed=transport_failed,
                                 fail_reason=fail_reason, duration=round(dur, 3))
        nb_exec_id = _insert_execution(
            con, run_id, repository_id, nb_ids[path], path, url, outcome)

        repro = None
        if compute_metrics:
            try:
                metrics = compute_determinism(original_nb, nb)
                insert_metrics(con, run_id, nb_exec_id, repository_id,
                               nb_ids[path], metrics)
                repro = metrics["reproducibility_score"]
            except Exception:
                # determinism is best-effort; never fail the run over it
                repro = None

        results.append((path, outcome["execution_status"],
                        outcome["error_category"], repro))

    total_dur = round(time.time() - t0, 3)
    if any_kernel_started:
        _finalize_run(con, run_id, "SUCCESS", "Repository executed successfully", total_dur)
        run_status = "SUCCESS"
    else:
        _finalize_run(con, run_id, "EXECUTION_BACKEND_UNREACHABLE",
                      "No kernel could be started on the spawned server", total_dur)
        run_status = "EXECUTION_BACKEND_UNREACHABLE"

    con.commit()
    con.close()

    return {
        "repository_id": repository_id,
        "run_id": run_id,
        "run_status": run_status,
        "duration_seconds": total_dur,
        "notebooks": [
            {"notebook": p, "execution_status": s, "error_category": c,
             "reproducibility_score": r}
            for (p, s, c, r) in results
        ],
    }


# ---------------------------------------------------------------------------
# CLI (probe/testing)
# ---------------------------------------------------------------------------
def _main():
    ap = argparse.ArgumentParser(description="Design-A DB-writing WebSocket executor")
    ap.add_argument("--server-base", required=True,
                    help="Running server base URL, e.g. https://hub.nfdi-jupyter.de/user/<user>/<srv>/")
    ap.add_argument("--token", default=os.environ.get("REPROSCORE_TOKEN"),
                    help="User token (or set REPROSCORE_TOKEN)")
    ap.add_argument("--repo", required=True, help="owner/repo")
    ap.add_argument("--notebooks", nargs="+", required=True,
                    help="relative notebook paths inside the repo")
    ap.add_argument("--db", required=True, help="path to execution/output/db/db.sqlite")
    ap.add_argument("--kernel-name", default="python3")
    ap.add_argument("--cell-timeout", type=float, default=300.0)
    ap.add_argument("--startup-timeout", type=float, default=120.0)
    ap.add_argument("--no-verify", action="store_true", help="disable TLS verification")
    ap.add_argument("--no-metrics", action="store_true",
                    help="skip Phase 2 determinism (notebook_reproducibility_metrics)")
    args = ap.parse_args()

    if not args.token:
        ap.error("no token: pass --token or set REPROSCORE_TOKEN")

    summary = run_repo_executions(
        server_base=args.server_base, token=args.token, repo=args.repo,
        notebook_paths=args.notebooks, db_file=args.db,
        kernel_name=args.kernel_name, cell_timeout=args.cell_timeout,
        startup_timeout=args.startup_timeout, verify=not args.no_verify,
        compute_metrics=not args.no_metrics,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    _main()
