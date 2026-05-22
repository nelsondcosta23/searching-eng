"""Notify an external endpoint when the Cloudflare quick tunnel URL changes.

This script can be run after the Cloudflare tunnel starts, or whenever the
quick tunnel URL is known. It will POST a small JSON payload to the callback
URL configured in the environment.
"""

from __future__ import annotations

import argparse
from typing import Optional
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_CONTAINER = "cloudflare_tunnel"
LOG_PATTERN = re.compile(r"https://[\w\-]+\.trycloudflare\.com")
DEFAULT_STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tmp', 'last_tunnel_url.txt')


def parse_logs_for_tunnel_url(log_text: str) -> Optional[str]:
    matches = LOG_PATTERN.findall(log_text)
    return matches[-1] if matches else None


def load_last_tunnel_url(state_path: str) -> Optional[str]:
    try:
        with open(state_path, 'r', encoding='utf-8') as f:
            value = f.read().strip()
            return value if value else None
    except FileNotFoundError:
        return None
    except OSError as exc:
        print(f"Could not read tunnel state file '{state_path}': {exc}")
        return None


def save_last_tunnel_url(state_path: str, tunnel_url: str) -> None:
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, 'w', encoding='utf-8') as f:
        f.write(tunnel_url)


def get_docker_logs(container: str, tail: int = 200) -> str:
    try:
        result = subprocess.run(
            ["docker", "logs", container, f"--tail={tail}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # cloudflared writes to stderr
            text=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Unable to read docker logs for container '{container}': {exc}")


def read_tunnel_url_from_file(log_path: str) -> Optional[str]:
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError(f"Unable to read tunnel log file '{log_path}': {exc}")
    return parse_logs_for_tunnel_url(text)


def monitor_tunnel_log(log_path: str, interval: float = 2.0, timeout: int = 0) -> None:
    last_url = load_last_tunnel_url(os.environ.get("TUNNEL_UPDATE_STATE_PATH", DEFAULT_STATE_FILE))
    start_time = time.time()
    while True:
        url = read_tunnel_url_from_file(log_path)
        if url and url != last_url:
            print(f"Detected new tunnel URL in log: {url}")
            webhook_url = os.environ.get("TUNNEL_UPDATE_WEBHOOK_URL", "").strip()
            secret = (
                os.environ.get("TUNNEL_UPDATE_WEBHOOK_SECRET", "").strip()
                or os.environ.get("EXTERNAL_WEBHOOK_SECRET", "").strip()
            )
            if not webhook_url:
                print("TUNNEL_UPDATE_WEBHOOK_URL is not configured. Skipping notification.")
            else:
                success = send_webhook(webhook_url, secret, url)
                if success:
                    save_last_tunnel_url(os.environ.get("TUNNEL_UPDATE_STATE_PATH", DEFAULT_STATE_FILE), url)
                    last_url = url
        if timeout and time.time() - start_time >= timeout:
            print(f"Watch timeout reached after {timeout}s.")
            break
        time.sleep(interval)


def watch_tunnel_stdin(timeout: int = 0) -> None:
    last_url = load_last_tunnel_url(os.environ.get("TUNNEL_UPDATE_STATE_PATH", DEFAULT_STATE_FILE))
    start_time = time.time()
    while True:
        line = sys.stdin.readline()
        if not line:
            if timeout and time.time() - start_time >= timeout:
                print(f"Watch timeout reached after {timeout}s.")
                break
            # stdin closed (EOF) — docker logs -f ended because the tunnel
            # container restarted. Exit so Docker restarts this container and
            # re-attaches to the new tunnel logs.
            print("stdin closed (tunnel container likely restarted). Exiting to trigger container restart.")
            sys.exit(0)
        url = parse_logs_for_tunnel_url(line)
        if url and url != last_url:
            print(f"Detected new tunnel URL in stdin: {url}")
            webhook_url = os.environ.get("TUNNEL_UPDATE_WEBHOOK_URL", "").strip()
            secret = (
                os.environ.get("TUNNEL_UPDATE_WEBHOOK_SECRET", "").strip()
                or os.environ.get("EXTERNAL_WEBHOOK_SECRET", "").strip()
            )
            if not webhook_url:
                print("TUNNEL_UPDATE_WEBHOOK_URL is not configured. Skipping notification.")
            else:
                success = send_webhook(webhook_url, secret, url)
                if success:
                    save_last_tunnel_url(os.environ.get("TUNNEL_UPDATE_STATE_PATH", DEFAULT_STATE_FILE), url)
                    last_url = url
        if timeout and time.time() - start_time >= timeout:
            print(f"Watch timeout reached after {timeout}s.")
            break


def send_webhook(webhook_url: str, secret: str, tunnel_url: str) -> bool:
    payload = {
        "event": "tunnel_updated",
        "base_url": tunnel_url,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "SearchingEng-TunnelNotifier/1.0",
    }
    if secret:
        headers["X-Webhook-Secret"] = secret

    req = urllib.request.Request(webhook_url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            status = response.getcode()
            print(f"Webhook sent successfully: HTTP {status}")
            return 200 <= status < 300
    except urllib.error.HTTPError as exc:
        print(f"Webhook HTTP error: {exc.code} {exc.reason}")
    except urllib.error.URLError as exc:
        print(f"Webhook URL error: {exc.reason}")
    except Exception as exc:
        print(f"Unexpected error while sending webhook: {exc}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Send Cloudflare tunnel update notifications.")
    parser.add_argument(
        "--tunnel-url",
        help="Explicit Cloudflare tunnel URL to notify. If omitted, the script tries to read docker logs.",
    )
    parser.add_argument(
        "--webhook-url",
        help="Override the TUNNEL_UPDATE_WEBHOOK_URL environment variable.",
    )
    parser.add_argument(
        "--secret",
        help="Override the TUNNEL_UPDATE_WEBHOOK_SECRET environment variable.",
    )
    parser.add_argument(
        "--state-path",
        default=os.environ.get("TUNNEL_UPDATE_STATE_PATH", DEFAULT_STATE_FILE),
        help="Path to the file that stores the last notified tunnel URL.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Always send the webhook even if the tunnel URL has not changed.",
    )
    parser.add_argument(
        "--container",
        default=os.environ.get("CLOUDFLARE_TUNNEL_CONTAINER", DEFAULT_CONTAINER),
        help="Docker container name to inspect for Cloudflare logs.",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=200,
        help="How many log lines to read from the Cloudflare tunnel container.",
    )
    parser.add_argument(
        "--log-path",
        help="Path to a log file containing the Cloudflare tunnel output.",
    )
    parser.add_argument(
        "--watch-log",
        action="store_true",
        help="Watch the log file and notify when the tunnel URL appears/changes.",
    )
    parser.add_argument(
        "--watch-stdin",
        action="store_true",
        help="Watch stdin and notify when the tunnel URL appears/changes.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between log file checks in watch mode.",
    )
    parser.add_argument(
        "--watch-timeout",
        type=int,
        default=0,
        help="Timeout in seconds for watch mode (0 = indefinite).",
    )
    args = parser.parse_args()

    webhook_url = args.webhook_url or os.environ.get("TUNNEL_UPDATE_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("TUNNEL_UPDATE_WEBHOOK_URL is not configured. Set it in .env or pass --webhook-url.")
        return 1

    secret = (
        args.secret
        or os.environ.get("TUNNEL_UPDATE_WEBHOOK_SECRET", "").strip()
        or os.environ.get("EXTERNAL_WEBHOOK_SECRET", "").strip()
    )
    state_path = args.state_path
    tunnel_url = args.tunnel_url

    if args.watch_log:
        if not args.log_path:
            print("--watch-log requires --log-path")
            return 1
        print(f"Watching log file '{args.log_path}' for Cloudflare tunnel URL changes...")
        monitor_tunnel_log(args.log_path, interval=args.poll_interval, timeout=args.watch_timeout)
        return 0

    if args.watch_stdin:
        print("Watching stdin for Cloudflare tunnel URL changes...")
        watch_tunnel_stdin(timeout=args.watch_timeout)
        return 0

    elif args.log_path:
        print(f"Reading tunnel URL from log file '{args.log_path}'...")
        try:
            tunnel_url = read_tunnel_url_from_file(args.log_path)
        except RuntimeError as exc:
            print(exc)
            return 1

    if not tunnel_url:
        print(f"Reading logs from Docker container '{args.container}' to discover the tunnel URL...")
        try:
            logs = get_docker_logs(args.container, tail=args.tail)
            tunnel_url = parse_logs_for_tunnel_url(logs)
        except RuntimeError as exc:
            print(exc)
            return 1

    if not tunnel_url:
        print("Could not find the Cloudflare tunnel URL. Pass --tunnel-url manually or use --log-path.")
        return 1

    if not args.force:
        last_url = load_last_tunnel_url(state_path)
        if last_url == tunnel_url:
            print(f"Tunnel URL unchanged (still {tunnel_url}). Skipping webhook notification.")
            return 0

    print(f"Notifying webhook with tunnel URL: {tunnel_url}")
    success = send_webhook(webhook_url, secret, tunnel_url)
    if success:
        try:
            save_last_tunnel_url(state_path, tunnel_url)
        except Exception as exc:
            print(f"Warning: could not save tunnel URL state: {exc}")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
