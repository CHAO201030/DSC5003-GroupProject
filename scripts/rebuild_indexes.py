#!/usr/bin/env python
"""Retry failed indexes and/or rebuild keyword/semantic indexes via HTTP.

Examples:

    python scripts/rebuild_indexes.py --retry-failed
    python scripts/rebuild_indexes.py --kinds all
    python scripts/rebuild_indexes.py --kinds semantic --background
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="CloudDisk index maintenance CLI")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="demo")
    parser.add_argument("--password", default="demo123456")
    parser.add_argument("--kinds", choices=["keyword", "semantic", "all"], default="all")
    parser.add_argument("--retry-failed", action="store_true", help="retry per-file failures first")
    parser.add_argument("--background", action="store_true", help="run the rebuild in the background")
    parser.add_argument("--skip-rebuild", action="store_true", help="only print status")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    with httpx.Client(timeout=300.0) as client:
        login = client.post(
            f"{base_url}/api/auth/login",
            json={"username": args.username, "password": args.password},
        )
        if login.status_code != 200:
            print(f"登录失败: {login.status_code} {login.text}", file=sys.stderr)
            return 2
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        if args.retry_failed:
            listing = client.get(
                f"{base_url}/api/index/files",
                params={"status_filter": "failed", "page_size": 200},
                headers=headers,
            )
            listing.raise_for_status()
            items = listing.json()["items"]
            print(f"发现 {len(items)} 个失败文件，逐个重试…")
            for item in items:
                response = client.post(
                    f"{base_url}/api/index/{item['file_id']}/retry", headers=headers
                )
                if response.status_code == 200:
                    status = response.json()["index"]
                    print(
                        f"  #{item['file_id']} {item['name']}: "
                        f"keyword={status['keyword_status']} semantic={status['semantic_status']}"
                    )
                else:
                    print(f"  #{item['file_id']} 重试失败: {response.status_code} {response.text[:200]}")

        if not args.skip_rebuild:
            response = client.post(
                f"{base_url}/api/index/rebuild",
                headers=headers,
                json={"kinds": args.kinds, "background": args.background},
            )
            if response.status_code != 200:
                print(f"重建失败: {response.status_code} {response.text}", file=sys.stderr)
                return 1
            print(json.dumps(response.json(), ensure_ascii=False, indent=2))

        status = client.get(f"{base_url}/api/index/status", headers=headers)
        status.raise_for_status()
        print("\n当前索引概览：")
        print(json.dumps(status.json(), ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
