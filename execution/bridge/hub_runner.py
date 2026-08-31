"""
hub_runner.py -- orchestrate one Hub-native scoring run.

spawn.spawn_server (REST) -> db_executor.run_repo_executions (Jupyter API)
-> spawn.teardown_server (always).
"""

from . import spawn as _spawn
from .db_executor import run_repo_executions


def run_execution_on_hub(*, token, repo, notebook_paths, exec_db,
                         hub=_spawn.HUB, system=_spawn.DEFAULT_SYSTEM,
                         flavor=_spawn.DEFAULT_FLAVOR, cell_timeout=300.0,
                         startup_timeout=120.0, verify=True,
                         on_status=None, on_log=None):
    """Spawn a server, execute notebook_paths via the bridge, tear down.

    Writes execution tables into exec_db and returns the run_repo_executions
    summary. The server is ALWAYS torn down, even on error.
    """
    def log(msg):
        print(f"[hub_runner] {msg}", flush=True)
        if on_log:
            on_log(msg)

    log(f"Spawning server for {repo} ...")
    info = _spawn.spawn_server(
        token, repo, hub=hub, system=system, flavor=flavor,
        verify=verify, on_status=on_status)
    log(f"Server running ({info['servername']}). Executing "
        f"{len(notebook_paths)} notebook(s) ...")

    try:
        summary = run_repo_executions(
            server_base=info["server_base"], token=token, repo=repo,
            notebook_paths=notebook_paths, db_file=exec_db,
            cell_timeout=cell_timeout, startup_timeout=startup_timeout,
            verify=verify)
        log(f"Execution done: run_status={summary.get('run_status')}, "
            f"{len(summary.get('notebooks', []))} notebook(s).")
        return summary
    finally:
        try:
            _spawn.teardown_server(token, info["delete_url"], hub=hub, verify=verify)
            log("Server torn down.")
        except Exception as e:
            log(f"WARNING: teardown failed: {e}")
