#!/usr/bin/env python3
"""Minimal Gitea webhook listener for Discord bot auto-deploys.

Runs inside the deployer container (see deploy/Dockerfile). Verifies
X-Gitea-Signature (HMAC-SHA256 of the raw body with WEBHOOK_SECRET), checks
the pushed ref is the deploy branch, and runs deploy/deploy.sh in the
background. LAN-only: the port is published on the NAS, never forwarded.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import subprocess
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

LISTEN_HOST = os.getenv("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "9000"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
DEPLOY_BRANCH = os.getenv("DEPLOY_BRANCH", "main")
EXPECTED_REPO = os.getenv("DEPLOY_REPO", "")  # example: "ratthew/discordbot"
REPO_DIR = Path(os.getenv("REPO_DIR", str(Path(__file__).resolve().parents[1]))).resolve()
DEPLOY_SCRIPT = Path(os.getenv("DEPLOY_SCRIPT", str(REPO_DIR / "deploy" / "deploy.sh"))).resolve()


def _verify_signature(secret: str, payload: bytes, header_value: str | None) -> bool:
    if not header_value:
        return False
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, header_value)


def _deploy_in_background(context: dict[str, str]) -> None:
    env = os.environ.copy()
    env.update(context)
    try:
        # Run a private copy: bash reads scripts incrementally, and deploy.sh
        # git-resets the very checkout it lives in — executing the repo copy
        # directly means a deploy that changes deploy.sh corrupts itself
        # mid-run (this happened; see 24ef7ef).
        with tempfile.NamedTemporaryFile("wb", suffix=".sh", delete=False) as tmp:
            shutil.copyfileobj(open(DEPLOY_SCRIPT, "rb"), tmp)
            script_copy = tmp.name
        try:
            subprocess.run(
                ["/bin/bash", script_copy],
                cwd=str(REPO_DIR),
                env=env,
                check=True,
                timeout=900,
            )
        finally:
            os.unlink(script_copy)
    except Exception as exc:  # noqa: BLE001
        print(f"[webhook] deploy failed: {exc}")


class Handler(BaseHTTPRequestHandler):
    server_version = "discordbot-webhook/2.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[webhook] {self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/webhook":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        if not WEBHOOK_SECRET:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "WEBHOOK_SECRET missing")
            return

        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)

        if not _verify_signature(WEBHOOK_SECRET, payload, self.headers.get("X-Gitea-Signature")):
            self.send_error(HTTPStatus.FORBIDDEN, "signature check failed")
            return

        event = self.headers.get("X-Gitea-Event", "")
        if event != "push":
            self.send_response(HTTPStatus.ACCEPTED)
            self.end_headers()
            self.wfile.write(b"ignored non-push event\n")
            return

        try:
            body = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_error(HTTPStatus.BAD_REQUEST, "invalid json")
            return

        ref = body.get("ref", "")
        if ref != f"refs/heads/{DEPLOY_BRANCH}":
            self.send_response(HTTPStatus.ACCEPTED)
            self.end_headers()
            self.wfile.write(f"ignored ref {ref}\n".encode("utf-8"))
            return

        if EXPECTED_REPO:
            full_name = (body.get("repository") or {}).get("full_name", "")
            if full_name != EXPECTED_REPO:
                self.send_error(HTTPStatus.FORBIDDEN, "repo mismatch")
                return

        context = {
            "DEPLOY_AFTER_SHA": str(body.get("after", "")),
            "DEPLOY_REF": str(ref),
        }
        Thread(target=_deploy_in_background, args=(context,), daemon=True).start()

        self.send_response(HTTPStatus.ACCEPTED)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"deploy triggered\n")


def main() -> None:
    if not DEPLOY_SCRIPT.exists():
        raise SystemExit(f"Deploy script not found: {DEPLOY_SCRIPT}")
    print(f"[webhook] listening on {LISTEN_HOST}:{LISTEN_PORT}")
    print(f"[webhook] repo={REPO_DIR} branch={DEPLOY_BRANCH}")
    with ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
