#!/usr/bin/env python3

import datetime as dt
import json
import os
import pathlib
import re
import sys
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
USERNAME = os.environ.get("GITHUB_USERNAME", "dss-time")
TOKEN = os.environ.get("GITHUB_TOKEN")


def request_json(url: str, *, method: str = "GET", payload=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "dss-time-profile-updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = None
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, headers=headers, method=method, data=data)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def graphql(query: str, variables: dict):
    payload = {"query": query, "variables": variables}
    data = request_json("https://api.github.com/graphql", method="POST", payload=payload)
    if "errors" in data:
        raise RuntimeError(json.dumps(data["errors"], ensure_ascii=False))
    return data["data"]


def get_user_created_year() -> int:
    data = request_json(f"https://api.github.com/users/{USERNAME}")
    created_at = data["created_at"]
    return dt.datetime.fromisoformat(created_at.replace("Z", "+00:00")).year


def get_year_stats(year: int) -> dict:
    start = dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc)
    end = dt.datetime(year + 1, 1, 1, tzinfo=dt.timezone.utc)
    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          totalIssueContributions
          totalPullRequestContributions
          totalPullRequestReviewContributions
          contributionCalendar {
            totalContributions
          }
        }
      }
    }
    """
    data = graphql(
        query,
        {
            "login": USERNAME,
            "from": start.isoformat(),
            "to": end.isoformat(),
        },
    )
    collection = data["user"]["contributionsCollection"]
    return {
        "year": year,
        "commits": int(collection["totalCommitContributions"]),
        "issues": int(collection["totalIssueContributions"]),
        "prs": int(collection["totalPullRequestContributions"]),
        "reviews": int(collection["totalPullRequestReviewContributions"]),
        "all": int(collection["contributionCalendar"]["totalContributions"]),
    }


def render_bar(value: int, maximum: int) -> str:
    if value <= 0:
        return "·"
    width = max(1, round(value / maximum * 12))
    return "█" * width


def render_table(stats: list[dict]) -> str:
    max_commits = max((item["commits"] for item in stats), default=1)
    lines = [
        "| 年份 | Commit | 全部贡献 | 其他贡献(PR / Review / Issue) |",
        "| --- | ---: | ---: | --- |",
    ]
    for item in stats:
        others = item["prs"] + item["reviews"] + item["issues"]
        lines.append(
            f"| {item['year']} | {item['commits']} {render_bar(item['commits'], max_commits)} | "
            f"{item['all']} | {others} |"
        )
    lines.append("")
    lines.append(
        f"_最后更新：{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}（按 GitHub 贡献统计生成）_"
    )
    return "\n".join(lines)


def update_readme(block: str):
    content = README.read_text(encoding="utf-8")
    pattern = re.compile(
        r"<!-- yearly-activity:start -->.*?<!-- yearly-activity:end -->",
        re.S,
    )
    replacement = (
        "<!-- yearly-activity:start -->\n"
        f"{block}\n"
        "<!-- yearly-activity:end -->"
    )
    updated = pattern.sub(replacement, content)
    if updated == content:
        raise RuntimeError("README markers not found")
    README.write_text(updated, encoding="utf-8")


def main():
    if not TOKEN:
        raise RuntimeError("GITHUB_TOKEN is required")

    current_year = dt.datetime.now().year
    created_year = get_user_created_year()
    stats = [get_year_stats(year) for year in range(current_year, created_year - 1, -1)]
    update_readme(render_table(stats))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"update failed: {exc}", file=sys.stderr)
        sys.exit(1)
