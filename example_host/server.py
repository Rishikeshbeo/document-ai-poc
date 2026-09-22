"""A sample application that embeds the Document AI panel.

This is *not* part of the service. It stands in for your software, and it runs
on its own origin so the integration is the real cross-origin one rather than
the same-origin shortcut the bundled /host page takes.

    python example_host/server.py            # http://localhost:8090

It does exactly two things, which is the whole integration:

  1. exchanges its API key for a short-lived token, server to server, so the
     key never reaches a browser;
  2. mounts an iframe with that token and listens for the data.

Standard library only — no dependencies, nothing to install.
"""

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env(path=os.path.join(HERE, ".env")):
    """Read KEY=value lines from .env, without overriding the real environment.

    Your own software would take the key from its secrets store. A file next to
    this sample is the equivalent for a laptop, and it means the API key is not
    retyped on the command line every time. The service itself deliberately
    reads no such file — this is the sample host, not the service.
    """
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip('"').strip("'")
        # An explicit environment variable wins, so a one-off override still works.
        if name and name not in os.environ:
            os.environ[name] = value


# example_host/.env first, then the project .env that docker compose reads, so
# a port set in one place works whether this runs in a container or not.
load_env()
load_env(os.path.join(HERE, os.pardir, ".env"))

# Where the Document AI service is, and the key it issued for this application.
DOCAI_URL = os.environ.get("DOCAI_URL", "http://localhost:8077").rstrip("/")

# What the *browser* should dial for the service. Inside Docker that is the
# published host port, not the container's, so it cannot be guessed from here —
# compose passes SERVICE_URL, and SERVICE_PORT alone is enough otherwise. This
# is substituted into index.html as it is served, which is what makes the port
# a single setting in .env rather than a string repeated in the markup.
SERVICE_URL = os.environ.get("SERVICE_URL", "").rstrip("/") or \
    f"http://localhost:{os.environ.get('SERVICE_PORT', '8077')}"
DOCAI_KEY = os.environ.get("DOCAI_KEY", "dk_test_demo_key")
PORT = int(os.environ.get("PORT", "8090"))

# Localhost by default, so running this on a laptop exposes nothing to the
# network. In a container that would be unreachable from outside it, so
# docker-compose sets BIND=0.0.0.0.
BIND = os.environ.get("BIND", "127.0.0.1")

# In your software this comes from the signed-in user's session. The panel
# never asks anyone to log in; it inherits this.
COMPANY_ID = os.environ.get("COMPANY_ID", "acme-gmbh")
USER_REF = os.environ.get("USER_REF", "j.moreau")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            # Served verbatim. index.html carries the service URL, the API key
            # and the company in its own markup, so this hands over bytes and
            # nothing else — any static file host would do the same job.
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                page = f.read()
            # The only substitution: where the browser reaches the service.
            # The API key stays in the markup, as it is meant to.
            page = page.replace(b"__SERVICE_URL__", SERVICE_URL.encode())
            self._send(200, page, "text/html; charset=utf-8")
        else:
            self._send(404, b"Not found", "text/plain")

    def do_POST(self):
        if self.path != "/session":
            return self._send(404, b'{"error":"Not found"}', "application/json")

        # ---- the only server-side call the integration needs ----
        body = json.dumps({"company_id": COMPANY_ID, "user_ref": USER_REF}).encode()
        request = urllib.request.Request(
            f"{DOCAI_URL}/api/session",
            data=body,
            method="POST",
            headers={"X-API-Key": DOCAI_KEY, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            return self._send(e.code, json.dumps({"error": detail}).encode(),
                              "application/json")
        except Exception as e:  # service not running, wrong port, no network
            return self._send(
                502,
                json.dumps({"error": f"Could not reach Document AI at {DOCAI_URL}: {e}"}).encode(),
                "application/json",
            )

        # Hand the browser only the token, never the key.
        self._send(200, json.dumps({
            "token": payload["token"],
            "expires_in": payload.get("expires_in"),
            "application": payload.get("application", {}).get("name", ""),
        }).encode(), "application/json")

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} {fmt % args}")


if __name__ == "__main__":
    try:
        server = ThreadingHTTPServer((BIND, PORT), Handler)
    except OSError as e:
        if e.errno != 98:
            raise
        # A traceback for "something is already listening" tells you nothing
        # you can act on.
        print(f"\n  Port {PORT} is already in use — most likely this sample is "
              f"already running.\n"
              f"  Open http://localhost:{PORT} to check, or start on another port:\n"
              f"      PORT=8091 python example_host/server.py\n")
        raise SystemExit(1)

    print(f"\n  Example application on http://localhost:{PORT}")
    print(f"  Panel served from {SERVICE_URL}")
    print(f"  Embedding the panel from {DOCAI_URL}")
    print(f"  Signed in as {USER_REF} at company {COMPANY_ID}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.\n")
