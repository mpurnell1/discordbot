#!/usr/bin/env python3
"""Minimal GitHub webhook listener for Discord bot auto-deploys.

This service verifies X-Hub-Signature-256, checks branch/repo, and triggers
the deploy script. Keep this behind HTTPS/tunnel and a strong WEBHOOK_SECRET.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any


LISTEN_HOST = os.getenv("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "9000"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
DEPLOY_BRANCH = os.getenv("DEPLOY_BRANCH", "main")
EXPECTED_REPO = os.getenv("GITHUB_REPO", "")  # example: "owner/repo"
REPO_DIR = Path(os.getenv("REPO_DIR", str(Path(__file__).resolve().parents[1]))).resolve()
DEPLOY_SCRIPT = Path(os.getenv("DEPLOY_SCRIPT", str(REPO_DIR / "deploy" / "deploy.sh"))).resolve()


def _verify_signature(secret: str, payload: bytes, header_value: str | None) -> bool:
    if not header_value or not header_value.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    return hmac.compare_digest(expected, header_value)


def _deploy_in_background(context: dict[str, str]) -> None:
    env = os.environ.copy()
    env.update(context)
    try:
        subprocess.run(
            ["/bin/bash", str(DEPLOY_SCRIPT)],
            cwd=str(REPO_DIR),
            env=env,
            check=True,
            timeout=600,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[webhook] deploy failed: {exc}")


class Handler(BaseHTTPRequestHandler):
    server_version = "discordbot-webhook/1.0"

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
        if self.path != "/github-webhook":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        if not WEBHOOK_SECRET:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "WEBHOOK_SECRET missing")
            return

        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)

        sig_header = self.headers.get("X-Hub-Signature-256")
        if not _verify_signature(WEBHOOK_SECRET, payload, sig_header):
            self.send_error(HTTPStatus.FORBIDDEN, "signature check failed")
            return

        event = self.headers.get("X-GitHub-Event", "")
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
        expected_ref = f"refs/heads/{DEPLOY_BRANCH}"
        if ref != expected_ref:
            self.send_response(HTTPStatus.ACCEPTED)
            self.end_headers()
            self.wfile.write(f"ignored ref {ref}\n".encode("utf-8"))
            return

        if EXPECTED_REPO:
            full_name = (body.get("repository") or {}).get("full_name", "")
            if full_name != EXPECTED_REPO:
                self.send_error(HTTPStatus.FORBIDDEN, "repo mismatch")
                return

        after_sha = body.get("after", "")
        context = {
            "GITHUB_AFTER_SHA": str(after_sha),
            "GITHUB_REF": str(ref),
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
