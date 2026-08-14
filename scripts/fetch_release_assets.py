#!/usr/bin/env python3
"""Fetch the exact published Accepted Release assets used by the Pages build."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request


SOURCE_REPOSITORY = "Blue-jp/tridentine_calendar"
API_ROOT = "https://api.github.com"
USER_AGENT = "tridentine-calendar-ja-pages"
TAG_PATTERN = re.compile(r"ja-localization-accepted-[0-9]{8}\Z")


class FetchError(RuntimeError):
    """Raised when release provenance or an asset cannot be trusted."""


def api_json(path: str) -> dict:
    request = urllib.request.Request(
        API_ROOT + path,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode("utf-8", errors="strict"))
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise FetchError(f"GitHub API request failed for {path}") from exc


def public_download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            if response.status != 200:
                raise FetchError(f"Asset download returned HTTP {response.status}")
            return response.read()
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise FetchError("Public Release asset download failed") from exc


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def required_asset_names(accepted_tag: str) -> tuple[str, ...]:
    stamp = accepted_tag.rsplit("-", 1)[-1]
    return (
        "tridentine-calendar-ja-accepted-html.ics",
        "tridentine-calendar-ja-accepted-plain.ics",
        f"tridentine-calendar-ja-html-by-year-{stamp}.zip",
        f"tridentine-calendar-ja-plain-by-year-{stamp}.zip",
        "RELEASE_ASSET_SHA256SUMS.txt",
    )


def peel_tag(tag: str) -> tuple[str, str]:
    encoded_tag = urllib.parse.quote(tag, safe="")
    reference = api_json(
        f"/repos/{SOURCE_REPOSITORY}/git/ref/tags/{encoded_tag}"
    )
    target = reference["object"]
    tag_object = target["sha"]

    for _ in range(8):
        if target["type"] == "commit":
            return tag_object, target["sha"]
        if target["type"] != "tag":
            raise FetchError("Accepted tag does not resolve to a commit")
        annotated = api_json(
            f"/repos/{SOURCE_REPOSITORY}/git/tags/{target['sha']}"
        )
        target = annotated["object"]
    raise FetchError("Accepted tag nesting exceeds the validation limit")


def prepare_output(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise FetchError("Output directory is not empty")
    path.mkdir(parents=True, exist_ok=True)


def fetch(accepted_tag: str, output: Path) -> dict:
    if not TAG_PATTERN.fullmatch(accepted_tag):
        raise FetchError("accepted_tag does not match the Accepted tag format")

    prepare_output(output)
    encoded_tag = urllib.parse.quote(accepted_tag, safe="")
    release = api_json(
        f"/repos/{SOURCE_REPOSITORY}/releases/tags/{encoded_tag}"
    )
    if release.get("tag_name") != accepted_tag:
        raise FetchError("Release tag_name does not match accepted_tag")
    if release.get("draft") is not False:
        raise FetchError("Accepted Release is still a draft")
    if release.get("prerelease") is not False:
        raise FetchError("Accepted Release is marked as a prerelease")
    if not release.get("published_at"):
        raise FetchError("Accepted Release has no published_at value")

    assets = release.get("assets", [])
    by_name = {asset["name"]: asset for asset in assets}
    if len(by_name) != len(assets):
        raise FetchError("Release contains duplicate asset names")
    required_assets = required_asset_names(accepted_tag)
    missing = sorted(set(required_assets) - set(by_name))
    if missing:
        raise FetchError("Release is missing required assets: " + ", ".join(missing))

    tag_object, source_commit = peel_tag(accepted_tag)
    downloaded = {}
    for name in required_assets:
        asset = by_name[name]
        data = public_download(asset["browser_download_url"])
        digest = sha256(data)
        if len(data) != asset["size"]:
            raise FetchError(f"GitHub asset size mismatch for {name}")
        api_digest = asset.get("digest")
        if api_digest and api_digest != "sha256:" + digest:
            raise FetchError(f"GitHub asset digest mismatch for {name}")
        destination = output / name
        if destination.exists():
            raise FetchError(f"Refusing to overwrite {name}")
        destination.write_bytes(data)
        downloaded[name] = {
            "asset_id": asset["id"],
            "size": len(data),
            "sha256": digest,
            "api_digest": api_digest,
        }
        print(f"[fetch] verified {name} ({len(data)} bytes)")

    metadata = {
        "source_repository": SOURCE_REPOSITORY,
        "accepted_tag": accepted_tag,
        "tag_object": tag_object,
        "source_commit": source_commit,
        "release_id": release["id"],
        "release_url": release["html_url"],
        "published_at": release["published_at"],
        "assets": downloaded,
    }
    (output / "release_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
        errors="strict",
        newline="\n",
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--accepted-tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metadata = fetch(args.accepted_tag, args.output)
    print(
        "[fetch] release provenance verified: "
        f"{metadata['accepted_tag']} -> {metadata['source_commit']}"
    )


if __name__ == "__main__":
    main()
