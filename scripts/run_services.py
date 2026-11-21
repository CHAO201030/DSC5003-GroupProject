#!/usr/bin/env python
"""Start the three CloudDisk processes (search, semantic, gateway) together.

Run inside the DSC5003 environment from the project root:

    python scripts/run_services.py

Ctrl+C stops all child processes.  For development you may also run the
three services in separate terminals (see README).
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.config.settings import Settings  # noqa: E402


def wait_for_port(host: str, port: int, timeout: float, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"进程提前退出: {process.args}")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.4)
    raise TimeoutError(f"等待 {host}:{port} 超时")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all CloudDisk services")
    parser.add_argument("--startup-timeout", type=float, default=240.0)
    parser.add_argument("--gateway-port", type=int, default=None)
    args = parser.parse_args()

    settings = Settings.from_env()
    settings.ensure_directories()
    gateway_port = args.gateway_port or settings.gateway_port
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    commands = [
        ("search", [sys.executable, "-m", "services.search.server"]),
        ("semantic", [sys.executable, "-m", "services.semantic.server"]),
        (
            "gateway",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "services.gateway.main:app",
                "--host",
                settings.gateway_host,
                "--port",
                str(gateway_port),
            ],
        ),
    ]

    processes: list[tuple[str, subprocess.Popen]] = []
    stopping = {"value": False}

    def shutdown(*_args):
        if stopping["value"]:
            return
        stopping["value"] = True
        print("\n[run] stopping services ...")
        for name, process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for name, process in reversed(processes):
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
        print("[run] stopped")

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        for name, command in commands[:2]:
            print(f"[run] starting {name}: {' '.join(command)}")
            processes.append(
                (name, subprocess.Popen(command, cwd=PROJECT_ROOT, env=env))
            )
        wait_for_port(settings.search_host, settings.search_port, args.startup_timeout, processes[0][1])
        wait_for_port(
            settings.semantic_host, settings.semantic_port, args.startup_timeout, processes[1][1]
        )
        print("[run] search and semantic services are ready")

        print(f"[run] starting gateway: {' '.join(commands[2][1])}")
        gateway = subprocess.Popen(commands[2][1], cwd=PROJECT_ROOT, env=env)
        processes.append(("gateway", gateway))
        wait_for_port("127.0.0.1", gateway_port, args.startup_timeout, gateway)

        print(
            f"\n[run] CloudDisk ready:\n"
            f"      Web UI : http://127.0.0.1:{gateway_port}/\n"
            f"      API    : http://127.0.0.1:{gateway_port}/api/docs\n"
            f"      Search : {settings.search_host}:{settings.search_port}\n"
            f"      Semantic: {settings.semantic_host}:{settings.semantic_port}\n"
            f"      Press Ctrl+C to stop.\n"
        )

        while True:
            for name, process in processes:
                code = process.poll()
                if code is not None:
                    print(f"[run] {name} exited with code {code}; stopping all")
                    shutdown()
                    return code
            time.sleep(1.0)
    except Exception as exc:
        if stopping["value"]:
            # Ctrl+C while a service was still starting: the signal handler
            # already performed a clean shutdown.
            return 0
        print(f"[run] error: {exc}", file=sys.stderr)
        shutdown()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
