#!/usr/bin/env python3
"""Validate the complete static Pages artifact before deployment."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import zipfile

from validate_release_assets import (
    EXPECTED_EVENTS,
    INTEGRATED_EVENT_COUNT,
    ValidationError,
    YEARS,
    audit_ics,
    identity_map,
    load_json,
    parse_checksum_manifest,
    release_asset_names,
    require_clean_ics,
    sha256,
    validate_st_barbara,
    validate_uuid5,
)


PRIVACY_MARKERS = (
    "c:\\users\\",
    "c:/users/",
    "appdata",
    "local\\temp",
    "local/temp",
    "ju" "mpf",
    "credential",
    " token",
    "secret",
    "private key",
    "authorization:",
    "bearer ",
    "x-amz-",
    "x-goog-",
    "?signature=",
    "?sig=",
)
EMAIL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)
TEST_FEED_UID = "subscription-refresh-test-1@blue-jp.github.io"
TEST_FEED_DTSTAMP = "20260814T114306Z"
TEST_FEED_SUMMARY = "購読更新テスト v1"


def email_scan_text(label: str, text: str) -> str:
    if not label.lower().endswith(".ics"):
        return text
    logical_lines = []
    for line in text.splitlines():
        if line.startswith((" ", "\t")) and logical_lines:
            logical_lines[-1] += line[1:]
        else:
            logical_lines.append(line)
    return "\n".join(
        line
        for line in logical_lines
        if line.partition(":")[0].split(";", 1)[0].upper() != "UID"
    )


def privacy_findings(label: str, text: str) -> list[str]:
    lowered = text.lower()
    findings = [f"{label}: {marker}" for marker in PRIVACY_MARKERS if marker in lowered]
    findings.extend(
        f"{label}: email"
        for _ in EMAIL_PATTERN.findall(email_scan_text(label, text))
    )
    for variable in (
        "HOSTNAME",
        "COMPUTERNAME",
        "USERNAME",
        "RUNNER_TEMP",
        "RUNNER_WORKSPACE",
        "USERPROFILE",
    ):
        value = os.environ.get(variable)
        if value and len(value) >= 3 and value.lower() in lowered:
            findings.append(f"{label}: environment value")
    return findings


def expected_site_files() -> set[str]:
    files = {
        "index.html",
        "404.html",
        "LICENSE.txt",
        "calendar/index.html",
        "calendar/google.ics",
        "calendar/plain.ics",
        "calendar/provenance.json",
        "calendar/checksums.txt",
        "test/subscription-test.ics",
    }
    for year in YEARS:
        files.add(f"calendar/by-year/{year}-html.ics")
        files.add(f"calendar/by-year/{year}-plain.ics")
    return files


def validate_text_files(site: Path) -> None:
    findings = []
    for path in sorted(path for path in site.rglob("*") if path.is_file()):
        relative = path.relative_to(site).as_posix()
        data = path.read_bytes()
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValidationError(f"Public file is not UTF-8: {relative}") from exc
        if data.startswith(b"\xef\xbb\xbf"):
            raise ValidationError(f"Public file has a UTF-8 BOM: {relative}")
        if "\ufffd" in text:
            raise ValidationError(f"Public file contains U+FFFD: {relative}")
        findings.extend(privacy_findings(relative, text))
    if findings:
        raise ValidationError("Privacy scan failed: " + "; ".join(findings))


def validate_test_feed(site: Path) -> None:
    path = site / "test" / "subscription-test.ics"
    data = path.read_bytes()
    audit = audit_ics(data, 2026)
    require_clean_ics(audit, 1)
    event = audit["events"][0]
    expected = {
        "UID": TEST_FEED_UID,
        "DTSTAMP": TEST_FEED_DTSTAMP,
        "DTSTART": "20260816",
        "DTEND": "20260817",
        "SUMMARY": TEST_FEED_SUMMARY,
        "DESCRIPTION": "固定URL購読の自動更新確認用テストです。",
        "SEQUENCE": "0",
    }
    for key, value in expected.items():
        if event.get(key) != [value]:
            raise ValidationError(f"Test feed {key} mismatch")

    text = data.decode("utf-8", errors="strict")
    lines = set(text.split("\r\n"))
    required_lines = {
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Blue-jp//Subscription Refresh Test//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        "END:VEVENT",
        "END:VCALENDAR",
    }
    if not required_lines.issubset(lines):
        raise ValidationError("Test feed calendar envelope mismatch")
    if "DTSTART;VALUE=DATE:20260816\r\n" not in text:
        raise ValidationError("Test feed DTSTART is not an all-day date")
    if "DTEND;VALUE=DATE:20260817\r\n" not in text:
        raise ValidationError("Test feed DTEND is not an all-day date")


def validate(
    site: Path, assets: Path, accepted_tag: str
) -> dict:
    if not site.is_dir():
        raise ValidationError("Pages artifact directory does not exist")
    actual = {
        path.relative_to(site).as_posix()
        for path in site.rglob("*")
        if path.is_file()
    }
    if actual != expected_site_files():
        raise ValidationError("Pages artifact file set is unexpected")
    if any(path.is_symlink() for path in site.rglob("*")):
        raise ValidationError("Pages artifact contains a symlink")

    metadata = load_json(assets / "release_metadata.json")
    names = release_asset_names(accepted_tag)
    google = (site / "calendar" / "google.ics").read_bytes()
    plain = (site / "calendar" / "plain.ics").read_bytes()
    if google != (assets / names["html"]).read_bytes():
        raise ValidationError("google.ics differs from the Release asset")
    if plain != (assets / names["plain"]).read_bytes():
        raise ValidationError("plain.ics differs from the Release asset")

    integrated = {}
    for form, data in (("html", google), ("plain", plain)):
        audit = audit_ics(data)
        require_clean_ics(audit, INTEGRATED_EVENT_COUNT)
        integrated[form] = audit
    if identity_map(integrated["html"]["events"]) != identity_map(
        integrated["plain"]["events"]
    ):
        raise ValidationError("Deployed integrated UID mapping differs")

    archives = {}
    for form in ("html", "plain"):
        archive = zipfile.ZipFile(assets / names[f"{form}_zip"])
        archives[form] = archive
    try:
        for year in YEARS:
            per_form = {}
            for form in ("html", "plain"):
                folder = f"{form}_by_calendar_year"
                entry = (
                    f"{folder}/tridentine-calendar-ja-{year}-accepted-{form}.ics"
                )
                deployed = (
                    site / "calendar" / "by-year" / f"{year}-{form}.ics"
                ).read_bytes()
                if deployed != archives[form].read(entry):
                    raise ValidationError(
                        f"Deployed year file differs from ZIP source: {year} {form}"
                    )
                audit = audit_ics(deployed, year)
                require_clean_ics(audit, EXPECTED_EVENTS[year])
                validate_st_barbara(audit["events"], year)
                if year in (2031, 2032):
                    validate_uuid5(audit["events"], year)
                per_form[form] = audit
            if identity_map(per_form["html"]["events"]) != identity_map(
                per_form["plain"]["events"]
            ):
                raise ValidationError(f"Deployed UID mapping differs for {year}")
    finally:
        for archive in archives.values():
            archive.close()

    provenance_path = site / "calendar" / "provenance.json"
    provenance = load_json(provenance_path)
    expected_provenance = {
        "source_repository": metadata["source_repository"],
        "accepted_tag": accepted_tag,
        "tag_object": metadata["tag_object"],
        "source_commit": metadata["source_commit"],
        "release_id": metadata["release_id"],
        "release_url": metadata["release_url"],
        "html_sha256": sha256(google),
        "plain_sha256": sha256(plain),
        "calendar_years": list(YEARS),
    }
    for key, value in expected_provenance.items():
        if provenance.get(key) != value:
            raise ValidationError(f"Provenance mismatch: {key}")
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
        provenance.get("deployed_at_utc", ""),
    ):
        raise ValidationError("Invalid provenance deployment timestamp")

    checksums = parse_checksum_manifest(
        (site / "calendar" / "checksums.txt").read_bytes()
    )
    expected_checksum_files = {
        "calendar/google.ics",
        "calendar/plain.ics",
        "calendar/provenance.json",
    }
    for year in YEARS:
        expected_checksum_files.add(f"calendar/by-year/{year}-html.ics")
        expected_checksum_files.add(f"calendar/by-year/{year}-plain.ics")
    if set(checksums) != expected_checksum_files:
        raise ValidationError("Public checksum file set is unexpected")
    for relative, expected_digest in checksums.items():
        if sha256((site / relative).read_bytes()) != expected_digest:
            raise ValidationError(f"Public checksum mismatch: {relative}")

    root_html = (site / "index.html").read_text(
        encoding="utf-8", errors="strict"
    )
    calendar_html = (site / "calendar" / "index.html").read_text(
        encoding="utf-8", errors="strict"
    )
    required_root = (
        "Subscription testing",
        "購読テスト中",
        "2026-08-14",
        "2024",
        "2034",
        "joe-antognini/tridentine_calendar",
    )
    if any(value not in root_html for value in required_root):
        raise ValidationError("Root page is missing required testing text")
    if "Testing / テスト中" not in calendar_html:
        raise ValidationError("Calendar page is missing its testing status")
    if "Generally available" in root_html or "正式公開" in root_html:
        raise ValidationError("Site incorrectly claims general availability")
    if (
        "subscription-test.ics" in root_html
        or "subscription-test.ics" in calendar_html
    ):
        raise ValidationError(
            "Public navigation links to the private test feed"
        )

    validate_test_feed(site)
    validate_text_files(site)
    return {
        "status": "passed",
        "accepted_tag": accepted_tag,
        "source_commit": metadata["source_commit"],
        "files": len(actual),
        "integrated_events": INTEGRATED_EVENT_COUNT,
        "year_files": len(YEARS) * 2,
        "test_feed_events": 1,
        "privacy_findings": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--accepted-tag", required=True)
    args = parser.parse_args()
    result = validate(args.site, args.assets, args.accepted_tag)
    print(
        "[validate-pages] passed: "
        f"{result['files']} files, {result['privacy_findings']} privacy findings"
    )


if __name__ == "__main__":
    main()
