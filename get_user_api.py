import json
import os

from flask import Flask, Response, make_response, redirect, request
from jupyterhub.services.auth import HubOAuth

prefix = os.environ.get("JUPYTERHUB_SERVICE_PREFIX", "/")
auth = HubOAuth(api_token=os.environ["JUPYTERHUB_API_TOKEN"], cache_max_age=60)

app = Flask(__name__)


@app.route(prefix)
def whoami():
    token = auth.get_token(request)
    if not token:
        state = auth.generate_state(next_url=request.path)
        resp = make_response(redirect(auth.login_url + f"&state={state}"))
        resp.set_cookie(auth.state_cookie_name, state)
        return resp

    user = auth.user_for_token(token)
    if not user:
        state = auth.generate_state(next_url=request.path)
        resp = make_response(redirect(auth.login_url + f"&state={state}"))
        resp.set_cookie(auth.state_cookie_name, state)
        return resp

    # this is the logged-in user's OAuth access token
    print(f"[whoami] {user['name']} token: {token}", flush=True)
    return Response(
        json.dumps({"user": user["name"], "token": token}, indent=2),
        mimetype="application/json",
    )


@app.route(prefix + "oauth_callback")
def oauth_callback():
    code = request.args.get("code")
    if code is None:
        return "Forbidden", 403

    arg_state = request.args.get("state")
    cookie_state = request.cookies.get(auth.state_cookie_name)
    if arg_state is None or arg_state != cookie_state:
        return "Forbidden", 403

    token = auth.token_for_code(code)
    next_url = auth.get_next_url(cookie_state) or prefix
    resp = make_response(redirect(next_url))
    resp.set_cookie(auth.cookie_name, token)
    return resp
