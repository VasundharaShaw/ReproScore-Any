"""
spawn.py -- JupyterLab spawn wrapper (JupyterHub REST API).

User brings the token (reproscore-token cookie in prod; personal /hub/token in
tests). The SAME token authenticates spawn/poll/teardown here AND the
Jupyter-Server API in db_executor (one-token model).
"""

import os
import time

import requests

_api_url = os.environ.get("JUPYTERHUB_API_URL", "")
# JUPYTERHUB_API_URL is like https://host/hub/api; strip suffix to get base host
_base = _api_url.split("/hub/api")[0] if _api_url else ""
HUB = _base or os.environ.get("REPROSCORE_HUB") or "https://hub.nfdi-jupyter.de"
DEFAULT_SYSTEM = os.environ.get("REPROSCORE_SYSTEM", "JSC-Cloud")
DEFAULT_FLAVOR = os.environ.get("REPROSCORE_FLAVOR", "m1nfdi")


def _auth(token):
    return {"Authorization": f"token {token}"}


def identify(token, *, hub=HUB, verify=True):
    r = requests.get(f"{hub}/hub/api/user", headers=_auth(token),
                     verify=verify, timeout=30)
    r.raise_for_status()
    return r.json()


def can_spawn(user_record):
    scopes = user_record.get("scopes", [])
    families = {s.split("!", 1)[0] for s in scopes}
    return "servers" in families and "access:servers" in families


def spawn_server(token, repo, *, system=DEFAULT_SYSTEM, flavor=DEFAULT_FLAVOR,
                 reporef="HEAD", hub=HUB, timeout=300, poll_interval=10,
                 verify=True, on_status=None):
    user = identify(token, hub=hub, verify=verify)["name"]

    body = {
        "option": "repo2docker",
        "repo2docker": {"repotype": "gh", "repourl": repo, "reporef": reporef},
        "system": system,
        "flavor": flavor,
    }
    r = requests.post(f"{hub}/hub/api/start", headers=_auth(token), json=body,
                      verify=verify, timeout=60)
    r.raise_for_status()
    j = r.json()
    status_url = j["status_url"]
    delete_url = j["delete_url"]
    servername = delete_url.rstrip("/").split("/")[-1]

    t0 = time.time()
    engaged = False
    running = False
    while time.time() - t0 < timeout:
        s = requests.get(status_url, headers=_auth(token), verify=verify, timeout=30)
        s.raise_for_status()
        status = s.json().get("status")
        if on_status:
            on_status(int(time.time() - t0), status)
        if status == "running":
            running = True
            break
        if status == "pending":
            engaged = True
        if status == "stopped" and engaged:
            teardown_server(token, delete_url, hub=hub, verify=verify)
            raise RuntimeError("server stopped after engaging -- spawn failed")
        time.sleep(poll_interval)

    if not running:
        teardown_server(token, delete_url, hub=hub, verify=verify)
        raise TimeoutError(f"spawn did not reach running within {timeout}s")

    u = requests.get(f"{hub}/hub/api/users/{user}", headers=_auth(token),
                     verify=verify, timeout=30)
    u.raise_for_status()
    srv = u.json().get("servers", {}).get(servername)
    if not srv or not srv.get("url"):
        teardown_server(token, delete_url, hub=hub, verify=verify)
        raise RuntimeError(f"server '{servername}' has no url in user record")

    server_base = hub.rstrip("/") + srv["url"]
    return {
        "server_base": server_base,
        "delete_url": delete_url,
        "servername": servername,
        "user": user,
    }


def teardown_server(token, delete_url, *, hub=HUB, verify=True):
    r = requests.delete(delete_url, headers=_auth(token), json={"remove": True},
                        verify=verify, timeout=30)
    if r.status_code not in (200, 202, 204, 404):
        r.raise_for_status()
    return r.status_code
