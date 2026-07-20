"""
service.py — JupyterHub Service entry point for ReproScore-Any.

Serves the same Gradio app as app.py, but behind JupyterHub's OAuth.
JupyterHub is the OAuth provider; this service is the OAuth client.

    python service.py

binds to JUPYTERHUB_SERVICE_URL if set, else 0.0.0.0:7860.
To bind explicitly instead:

    uvicorn service:app --host 0.0.0.0 --port 7860

Two layers of protection, deliberately:

  1. HubOAuthRedirectMiddleware — catches unauthenticated *browser* requests
     and redirects them to the Hub login. Without this, Gradio's
     auth_dependency returns a bare 401 and the user hits a dead end.

  2. auth_dependency — Gradio's own hook. Gates Gradio's internal routes
     (queue, API, file endpoints), which the middleware alone does not
     reliably cover.

JupyterHub injects into the service container:
    JUPYTERHUB_API_TOKEN      — this service's token (OAuth client secret)
    JUPYTERHUB_SERVICE_PREFIX — e.g. /services/reproscore/
    JUPYTERHUB_API_URL        — Hub API base

app.py is unchanged and still runs standalone (HF Space, local, Docker).
"""

import os

import gradio as gr
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from jupyterhub.services.auth import HubOAuth

# The Gradio Blocks object — imported, not launched.
# app.py guards demo.launch() behind __main__, so this import is safe.
from app import demo


# ---------------------------------------------------------------------------
# Paths. PREFIX always carries a trailing slash; MOUNT_PATH never does.
# Keeping these consistent avoids the //assets/... double-slash problem.
# ---------------------------------------------------------------------------

PREFIX = os.environ.get("JUPYTERHUB_SERVICE_PREFIX", "/")
if not PREFIX.endswith("/"):
    PREFIX += "/"

MOUNT_PATH = PREFIX.rstrip("/") or "/"
CALLBACK_PATH = PREFIX + "oauth_callback"
HEALTH_PATH = PREFIX + "health"
TOKEN_COOKIE = "reproscore-token"

# OAuth client identity. Managed services get these injected by the Hub;
# external services do not, so derive them from the service name.
SERVICE_NAME = os.environ.get("JUPYTERHUB_SERVICE_NAME", "")

CLIENT_ID = os.environ.get("JUPYTERHUB_CLIENT_ID") or (
    f"service-{SERVICE_NAME}" if SERVICE_NAME else ""
)

REDIRECT_URI = os.environ.get("JUPYTERHUB_OAUTH_CALLBACK_URL") or CALLBACK_PATH

auth = HubOAuth(
    api_token=os.environ.get("JUPYTERHUB_API_TOKEN", ""),
    api_url=os.environ.get("JUPYTERHUB_API_URL", "http://127.0.0.1:8081/hub/api"),
    oauth_client_id=CLIENT_ID,
    oauth_redirect_uri=REDIRECT_URI,
    cache_max_age=60,
)

if not CLIENT_ID:
    raise RuntimeError(
        "No OAuth client id. Set JUPYTERHUB_CLIENT_ID, or JUPYTERHUB_SERVICE_NAME "
        "so it can be derived as service-<name>."
    )


def _user_for_request(request: Request):
    """Resolve the Hub user from the token cookie, or None."""
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return None
    return auth.user_for_token(token)


def _norm(p: str) -> str:
    return p.rstrip("/")


# ---------------------------------------------------------------------------
# Layer 1: redirect unauthenticated browsers to the Hub login
# ---------------------------------------------------------------------------

class HubOAuthRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = _norm(request.url.path)

        # These must be reachable without a token.
        if path in (_norm(CALLBACK_PATH), _norm(HEALTH_PATH)):
            return await call_next(request)

        if _user_for_request(request):
            return await call_next(request)

        # No valid token. Send the browser to the Hub to log in.
        state = auth.generate_state(next_url=request.url.path)
        response = RedirectResponse(
            f"{auth.login_url}&state={state}",
            status_code=302,
        )
        response.set_cookie(auth.state_cookie_name, state, httponly=True)
        return response


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI()
app.add_middleware(HubOAuthRedirectMiddleware)


@app.get(HEALTH_PATH, include_in_schema=False)
async def health():
    """Unauthenticated liveness probe for Kubernetes."""
    return PlainTextResponse("ok")


@app.get(CALLBACK_PATH, include_in_schema=False)
async def oauth_callback(request: Request):
    """Complete the OAuth handshake: validate state, exchange code for token."""
    code = request.query_params.get("code")
    arg_state = request.query_params.get("state")
    cookie_state = request.cookies.get(auth.state_cookie_name)

    if not code:
        return PlainTextResponse("Forbidden: missing code", status_code=403)
    if not arg_state or arg_state != cookie_state:
        return PlainTextResponse("Forbidden: OAuth state mismatch", status_code=403)

    token = auth.token_for_code(code)
    next_url = auth.get_next_url(cookie_state) or PREFIX

    response = RedirectResponse(next_url, status_code=302)
    response.set_cookie(TOKEN_COOKIE, token, httponly=True, path=PREFIX)
    response.delete_cookie(auth.state_cookie_name)
    return response


# ---------------------------------------------------------------------------
# Layer 2: Gradio's own gate on its internal routes
# ---------------------------------------------------------------------------

def get_hub_user(request: Request):
    """Gradio auth_dependency: return the username, or None to deny."""
    user = _user_for_request(request)
    return user["name"] if user else None


app = gr.mount_gradio_app(
    app,
    demo,
    path=MOUNT_PATH,
    root_path=MOUNT_PATH,
    auth_dependency=get_hub_user,
)


if __name__ == "__main__":
    from urllib.parse import urlparse

    import uvicorn

    url = urlparse(os.environ.get("JUPYTERHUB_SERVICE_URL", "http://0.0.0.0:7860"))
    uvicorn.run(app, host=url.hostname or "0.0.0.0", port=url.port or 7860)
