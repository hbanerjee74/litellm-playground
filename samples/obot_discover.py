"""Discover MCP catalog entries in Obot and print copy-paste-ready env-var lines.

Hits Obot's `/api/all-mcps/entries` endpoint with the bootstrap token and
prints, for each installed MCP server in the catalog, the connector name and
the matching `OBOT_MCP_SERVER_ID=<slug>` line. Useful when setting up the
mcp_obot_linear_repl sample for the first time, or when you've installed a
new MCP server in Obot's admin UI and need its catalog slug.

Auth scope: this uses `OBOT_BOOTSTRAP_TOKEN` (not the per-user `OBOT_API_KEY`)
because API keys are scoped to `/mcp-connect/*` + `/api/me` and cannot list
catalog state. This mirrors how Studio's production installer would
discover catalog entries — Studio holds the bootstrap-equivalent admin
credential; end users do not.

Usage:
    uv run python samples/obot_discover.py            # all entries
    uv run python samples/obot_discover.py linear     # filter by substring
"""

from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()

    obot_url = os.environ.get("OBOT_URL", "http://localhost:8080")
    bootstrap = os.environ.get("OBOT_BOOTSTRAP_TOKEN")
    if not bootstrap:
        raise SystemExit(
            "OBOT_BOOTSTRAP_TOKEN is not set. See .env.example for how to "
            "generate one with `openssl rand -hex 32` and pass it to Obot "
            "at startup."
        )

    filter_substring = sys.argv[1].lower() if len(sys.argv) > 1 else ""

    response = httpx.get(
        f"{obot_url}/api/all-mcps/entries",
        headers={"Authorization": f"Bearer {bootstrap}"},
        timeout=10.0,
    )
    response.raise_for_status()

    entries = response.json().get("items", [])
    matches = [
        e for e in entries
        if filter_substring in e["manifest"]["name"].lower()
    ]

    if not matches:
        print(
            f"No catalog entries match {filter_substring!r}.\n"
            f"Total entries in catalog: {len(entries)}."
        )
        return

    print(f"# {len(matches)} matching catalog entr{'y' if len(matches) == 1 else 'ies'}:\n")
    for e in sorted(matches, key=lambda x: x["manifest"]["name"].lower()):
        name = e["manifest"]["name"]
        slug = e["id"]
        runtime = e["manifest"].get("runtime", "?")
        print(f"# {name}  (runtime={runtime})")
        print(f"#   MCP URL: {obot_url}/mcp-connect/{slug}")
        print(f"OBOT_MCP_SERVER_ID={slug}")
        print()


if __name__ == "__main__":
    main()
