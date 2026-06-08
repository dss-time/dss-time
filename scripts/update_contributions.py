#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


API_URL = "https://api.github.com/graphql"

USERNAME = (
    os.getenv("GITHUB_USERNAME")
    or os.getenv("GITHUB_REPOSITORY_OWNER")
    or "dss-time"
)

# PROFILE_TOKEN is recommended if you want the README number to match your GitHub profile page.
# GITHUB_TOKEN can only see what the workflow token is allowed to see.
TOKEN = (
    os.getenv("PROFILE_TOKEN")
    or os.getenv("GH_TOKEN")
    or os.getenv("GITHUB_TOKEN")
)

README_PATH = Path(os.getenv("README_PATH", "README.md"))

START_MARKER = "<!-- CONTRIBUTIONS:START -->"
END_MARKER = "<!-- CONTRIBUTIONS:END -->"

START_YEAR = int(os.getenv("START_YEAR", "2021"))
SIGNAL_WIDTH = int(os.getenv("SIGNAL_WIDTH", "18"))
README_TIMEZONE = os.getenv("README_TIMEZONE", "UTC")

# total = GitHub profile contribution graph total, usually the number shown like "840 contributions in 2022".
# commit = totalCommitContributions only.
COUNT_MODE = os.getenv("COUNT_MODE", "total").strip().lower()

# Keep your original README format by default.
TABLE_VALUE_LABEL = os.getenv("TABLE_VALUE_LABEL", "COMMITS")


def github_graphql(query: str, variables: dict) -> dict:
    if not TOKEN:
        raise RuntimeError("Missing token: please set PROFILE_TOKEN, GH_TOKEN, or GITHUB_TOKEN.")

    body = json.dumps(
        {
            "query": query,
            "variables": variables,
        }
    ).encode("utf-8")

    request = Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "dss-time-readme-updater",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"GitHub API request failed: {error}") from error

    if payload.get("errors"):
        messages = "; ".join(
            item.get("message", str(item)) for item in payload["errors"]
        )
        raise RuntimeError(f"GitHub GraphQL error: {messages}")

    return payload["data"]


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def get_years() -> list[int]:
    now_year = datetime.now(timezone.utc).year

    if START_YEAR > now_year:
        raise RuntimeError("START_YEAR cannot be greater than current year.")

    return list(range(now_year, START_YEAR - 1, -1))


def get_contribution_count(year: int) -> int:
    now = datetime.now(timezone.utc)

    start = datetime(year, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    if year == now.year:
        end = now
    else:
        end = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          restrictedContributionsCount
          contributionCalendar {
            totalContributions
          }
        }
      }
    }
    """

    data = github_graphql(
        query,
        {
            "login": USERNAME,
            "from": iso_utc(start),
            "to": iso_utc(end),
        },
    )

    user = data.get("user")
    if not user:
        raise RuntimeError(f"GitHub user not found: {USERNAME}")

    collection = user["contributionsCollection"]

    if COUNT_MODE == "commit":
        return int(collection["totalCommitContributions"])

    if COUNT_MODE == "total":
        return int(collection["contributionCalendar"]["totalContributions"])

    raise RuntimeError("COUNT_MODE must be either 'total' or 'commit'.")


def make_signal(count: int, max_count: int) -> str:
    if count <= 0 or max_count <= 0:
        return "."

    bar_len = round((count / max_count) * SIGNAL_WIDTH)
    bar_len = max(1, min(SIGNAL_WIDTH, bar_len))

    return "█" * bar_len


def get_updated_time() -> str:
    now = datetime.now(timezone.utc)

    if README_TIMEZONE.upper() == "UTC":
        target_time = now
    else:
        if ZoneInfo is None:
            raise RuntimeError("zoneinfo is not available in this Python runtime.")

        try:
            target_time = now.astimezone(ZoneInfo(README_TIMEZONE))
        except Exception as error:
            raise RuntimeError(f"Invalid README_TIMEZONE: {README_TIMEZONE}") from error

    return target_time.strftime("%Y-%m-%d %H:%M")


def render_block(rows: list[tuple[int, int]]) -> str:
    max_count = max((count for _, count in rows), default=0)

    lines = [
        f"| YEAR | {TABLE_VALUE_LABEL} | SIGNAL |",
        "|---:|---:|:---|",
    ]

    for year, count in rows:
        signal = make_signal(count, max_count)
        lines.append(f"| {year} | {count} | {signal} |")

    # The two spaces after the updated line are intentional.
    # They force a visible line break in GitHub Markdown.
    lines += [
        "",
        f"updated  {get_updated_time()}  ",
        "source   github contributions",
    ]

    return "\n".join(lines)


def replace_contribution_area(content: str, new_block: str) -> str:
    new_area = f"{START_MARKER}\n{new_block}\n{END_MARKER}"

    if START_MARKER in content and END_MARKER in content:
        pattern = re.compile(
            rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}",
            re.DOTALL,
        )
        return pattern.sub(new_area, content, count=1)

    old_area_pattern = re.compile(
        r"(?ms)^ *\| *YEAR *\| *(COMMITS|CONTRIBUTIONS) *\| *SIGNAL *\|.*?"
        r"^ *source +github contributions *$"
    )

    if old_area_pattern.search(content):
        return old_area_pattern.sub(new_block, content, count=1)

    if content.strip():
        return content.rstrip() + "\n\n" + new_area + "\n"

    return "# dss-time\n\n" + new_area + "\n"


def main() -> int:
    years = get_years()
    rows = [(year, get_contribution_count(year)) for year in years]

    new_block = render_block(rows)

    old_content = ""
    if README_PATH.exists():
        old_content = README_PATH.read_text(encoding="utf-8")

    new_content = replace_contribution_area(old_content, new_block)
    README_PATH.write_text(new_content, encoding="utf-8")

    print(f"Updated: {README_PATH}")
    print(f"GitHub user: {USERNAME}")
    print(f"Count mode: {COUNT_MODE}")
    print(f"Years: {', '.join(str(year) for year, _ in rows)}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
