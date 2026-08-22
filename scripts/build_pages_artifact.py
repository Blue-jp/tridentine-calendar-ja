#!/usr/bin/env python3
"""Build an ephemeral Pages artifact from validated Release assets."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import zipfile

from validate_release_assets import (
    ValidationError,
    YEARS,
    audit_ics,
    load_json,
    release_asset_names,
    require_clean_ics,
    sha256,
)


TEST_FEED_UID = "subscription-refresh-test-1@blue-jp.github.io"
TEST_FEED_DTSTAMP = "20260814T124108Z"
TEST_FEED_SUMMARY = "購読更新テスト v2"
TEST_FEED_DESCRIPTION = "固定URL購読の自動更新確認用テストです。v2へ更新されました。"
TEST_FEED_REQUIRED_LINES = {
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Blue-jp//Subscription Refresh Test//EN",
    "CALSCALE:GREGORIAN",
    "BEGIN:VEVENT",
    "END:VEVENT",
    "END:VCALENDAR",
}


def prepare_output(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise ValidationError("Pages output directory is not empty")
    path.mkdir(parents=True, exist_ok=True)


def copy_bytes(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())


def canonicalize_test_feed(data: bytes) -> bytes:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValidationError("Test feed source is not UTF-8") from exc
    if text.startswith("\ufeff"):
        raise ValidationError("Test feed source has a UTF-8 BOM")

    normalized = text.replace("\r\n", "\n")
    if "\r" in normalized:
        raise ValidationError("Test feed source contains a bare CR")
    lines = normalized.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not TEST_FEED_REQUIRED_LINES.issubset(lines):
        raise ValidationError("Test feed source calendar envelope mismatch")

    audit = audit_ics(data, 2026)
    require_clean_ics(audit, 1)
    event = audit["events"][0]
    expected = {
        "UID": TEST_FEED_UID,
        "DTSTAMP": TEST_FEED_DTSTAMP,
        "DTSTART": "20260816",
        "DTEND": "20260817",
        "SUMMARY": TEST_FEED_SUMMARY,
        "DESCRIPTION": TEST_FEED_DESCRIPTION,
        "SEQUENCE": "1",
    }
    for key, value in expected.items():
        if event.get(key) != [value]:
            raise ValidationError(f"Test feed source {key} mismatch")
    if "DTSTART;VALUE=DATE:20260816" not in lines:
        raise ValidationError(
            "Test feed source DTSTART is not an all-day date"
        )
    if "DTEND;VALUE=DATE:20260817" not in lines:
        raise ValidationError("Test feed source DTEND is not an all-day date")

    return ("\r\n".join(lines) + "\r\n").encode("utf-8", errors="strict")


def build(repository: Path, assets: Path, output: Path, accepted_tag: str) -> dict:
    metadata = load_json(assets / "release_metadata.json")
    validation = load_json(assets / "validation.json")
    if validation.get("status") != "passed":
        raise ValidationError("Release assets have not passed validation")
    if metadata.get("accepted_tag") != accepted_tag:
        raise ValidationError("Build tag does not match release metadata")

    prepare_output(output)
    for relative in (
        "index.html",
        "404.html",
        "LICENSE.txt",
        "images/eucharistic-header.png",
    ):
        copy_bytes(repository / relative, output / relative)
    copy_bytes(
        repository / "calendar" / "index.html",
        output / "calendar" / "index.html",
    )
    test_feed = output / "test" / "subscription-test.ics"
    test_feed.parent.mkdir(parents=True, exist_ok=True)
    test_feed.write_bytes(
        canonicalize_test_feed(
            (repository / "test" / "subscription-test.ics").read_bytes()
        )
    )

    names = release_asset_names(accepted_tag)
    google_path = output / "calendar" / "google.ics"
    plain_path = output / "calendar" / "plain.ics"
    copy_bytes(assets / names["html"], google_path)
    copy_bytes(assets / names["plain"], plain_path)

    by_year = output / "calendar" / "by-year"
    by_year.mkdir(parents=True)
    for form in ("html", "plain"):
        folder = f"{form}_by_calendar_year"
        with zipfile.ZipFile(assets / names[f"{form}_zip"]) as archive:
            for year in YEARS:
                entry = (
                    f"{folder}/tridentine-calendar-ja-{year}-accepted-{form}.ics"
                )
                (by_year / f"{year}-{form}.ics").write_bytes(
                    archive.read(entry)
                )

    provenance = {
        "source_repository": metadata["source_repository"],
        "accepted_tag": metadata["accepted_tag"],
        "tag_object": metadata["tag_object"],
        "source_commit": metadata["source_commit"],
        "release_id": metadata["release_id"],
        "release_url": metadata["release_url"],
        "deployed_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "html_sha256": sha256(google_path.read_bytes()),
        "plain_sha256": sha256(plain_path.read_bytes()),
        "calendar_years": list(YEARS),
    }
    provenance_path = output / "calendar" / "provenance.json"
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
        errors="strict",
        newline="\n",
    )

    checksum_paths = [google_path, plain_path]
    checksum_paths.extend(sorted(by_year.glob("*.ics")))
    checksum_paths.append(provenance_path)
    checksum_lines = []
    for path in checksum_paths:
        relative = path.relative_to(output).as_posix()
        checksum_lines.append(f"{sha256(path.read_bytes())}  {relative}")
    (output / "calendar" / "checksums.txt").write_text(
        "\n".join(checksum_lines) + "\n",
        encoding="ascii",
        errors="strict",
        newline="\n",
    )
    return provenance


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--accepted-tag", required=True)
    args = parser.parse_args()
    provenance = build(
        args.repository, args.assets, args.output, args.accepted_tag
    )
    print(
        "[build] Pages artifact created for "
        f"{provenance['accepted_tag']} ({len(provenance['calendar_years'])} years)"
    )


if __name__ == "__main__":
    main()
