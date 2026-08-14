#!/usr/bin/env python3
"""Validate downloaded Accepted Release assets without executing their code."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import uuid
import zipfile


YEARS = tuple(range(2024, 2035))
EXPECTED_EVENTS = {
    2024: 449,
    2025: 449,
    2026: 449,
    2027: 449,
    2028: 449,
    2029: 449,
    2030: 448,
    2031: 449,
    2032: 449,
    2033: 448,
    2034: 450,
}
INTEGRATED_EVENT_COUNT = 4938
ST_BARBARA_SUMMARY = "› 聖バルバラ"
ST_BARBARA_REQUIRED = ("記念", "典礼色は赤です。")
ST_BARBARA_OLD = (
    "一般ローマ暦では記念です。",
    "聖バルバラの固有ミサの典礼色は赤です。",
)


class ValidationError(RuntimeError):
    """Raised when an asset fails an acceptance invariant."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Invalid JSON document: {path.name}") from exc


def release_asset_names(accepted_tag: str) -> dict[str, str]:
    stamp = accepted_tag.rsplit("-", 1)[-1]
    if not re.fullmatch(r"[0-9]{8}", stamp):
        raise ValidationError("Accepted tag has no YYYYMMDD suffix")
    return {
        "html": "tridentine-calendar-ja-accepted-html.ics",
        "plain": "tridentine-calendar-ja-accepted-plain.ics",
        "html_zip": f"tridentine-calendar-ja-html-by-year-{stamp}.zip",
        "plain_zip": f"tridentine-calendar-ja-plain-by-year-{stamp}.zip",
        "checksums": "RELEASE_ASSET_SHA256SUMS.txt",
    }


def parse_checksum_manifest(data: bytes) -> dict[str, str]:
    try:
        text = data.decode("ascii", errors="strict")
    except UnicodeError as exc:
        raise ValidationError("Checksum manifest is not ASCII") from exc
    entries = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise ValidationError("Malformed checksum manifest line")
        name = parts[1].lstrip("*")
        if name in entries:
            raise ValidationError("Duplicate checksum manifest filename")
        entries[name] = parts[0]
    if not entries:
        raise ValidationError("Checksum manifest is empty")
    return entries


def verify_checksum(path: Path, expected: str) -> None:
    if not path.is_file() or sha256(path.read_bytes()) != expected:
        raise ValidationError(f"Checksum mismatch for {path.name}")


def unsafe_zip_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        name.startswith(("/", "\\"))
        or "\\" in name
        or bool(re.match(r"^[A-Za-z]:", name))
        or ".." in path.parts
    )


def hidden_or_temporary(name: str) -> bool:
    for part in PurePosixPath(name).parts:
        lowered = part.lower()
        if (
            part.startswith(".")
            or part == "__MACOSX"
            or lowered.endswith(("~", ".tmp", ".temp", ".bak"))
        ):
            return True
    return False


def validate_zip_structure(
    path: Path, expected_entries: set[str] | None = None
) -> dict[str, bytes]:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValidationError(f"Invalid ZIP archive: {path.name}") from exc
    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValidationError(f"Duplicate ZIP entry in {path.name}")
        if archive.testzip() is not None:
            raise ValidationError(f"CRC failure in {path.name}")
        for info in infos:
            if unsafe_zip_name(info.filename):
                raise ValidationError(f"Unsafe ZIP path in {path.name}")
            if hidden_or_temporary(info.filename):
                raise ValidationError(f"Temporary ZIP entry in {path.name}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValidationError(f"Symlink ZIP entry in {path.name}")
            kind = stat.S_IFMT(mode)
            if kind and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValidationError(f"Special ZIP entry in {path.name}")
        if expected_entries is not None and set(names) != expected_entries:
            raise ValidationError(f"Unexpected entry set in {path.name}")
        return {
            info.filename: archive.read(info)
            for info in infos
            if not info.is_dir()
        }


def unfold_ics(text: str) -> list[str]:
    unfolded = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def parse_ics_events(data: bytes) -> list[dict[str, list[str]]]:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValidationError("ICS is not valid UTF-8") from exc
    if "BEGIN:VCALENDAR" not in text or "END:VCALENDAR" not in text:
        raise ValidationError("Malformed VCALENDAR envelope")

    events = []
    current = None
    for line in unfold_ics(text):
        if line == "BEGIN:VEVENT":
            if current is not None:
                raise ValidationError("Nested VEVENT")
            current = defaultdict(list)
        elif line == "END:VEVENT":
            if current is None:
                raise ValidationError("Unexpected END:VEVENT")
            events.append(dict(current))
            current = None
        elif current is not None and ":" in line:
            left, value = line.split(":", 1)
            key = left.split(";", 1)[0].upper()
            current[key].append(value)
    if current is not None:
        raise ValidationError("Unclosed VEVENT")
    return events


def first(event: dict[str, list[str]], key: str) -> str:
    values = event.get(key, [])
    return values[0] if values else ""


def unescape_ics(value: str) -> str:
    return (
        value.replace("\\N", "\n")
        .replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def audit_ics(data: bytes, expected_year: int | None = None) -> dict:
    events = parse_ics_events(data)
    uids = [first(event, "UID") for event in events]
    summaries = [first(event, "SUMMARY") for event in events]
    dates = [first(event, "DTSTART")[:8] for event in events]
    return {
        "events": events,
        "event_count": len(events),
        "duplicate_uid": len(uids) - len(set(uids)),
        "empty_uid": sum(not uid for uid in uids),
        "empty_summary": sum(not summary for summary in summaries),
        "year_contamination": (
            sum(not date.startswith(str(expected_year)) for date in dates)
            if expected_year is not None
            else 0
        ),
    }


def require_clean_ics(audit: dict, event_count: int) -> None:
    if audit["event_count"] != event_count:
        raise ValidationError("Unexpected VEVENT count")
    for key in (
        "duplicate_uid",
        "empty_uid",
        "empty_summary",
        "year_contamination",
    ):
        if audit[key] != 0:
            raise ValidationError(f"ICS invariant failed: {key}")


def identity_map(events: list[dict[str, list[str]]]) -> dict[str, tuple[str, str]]:
    return {
        first(event, "UID"): (
            first(event, "DTSTART"),
            first(event, "SUMMARY"),
        )
        for event in events
    }


def validate_st_barbara(events: list[dict[str, list[str]]], year: int) -> None:
    matches = [
        event
        for event in events
        if first(event, "DTSTART").startswith(f"{year}1204")
        and first(event, "SUMMARY") == ST_BARBARA_SUMMARY
    ]
    if len(matches) != 1:
        raise ValidationError(f"St. Barbara event count is not one for {year}")
    description = unescape_ics(first(matches[0], "DESCRIPTION"))
    if any(required not in description for required in ST_BARBARA_REQUIRED):
        raise ValidationError(f"St. Barbara description mismatch for {year}")
    if any(old in description for old in ST_BARBARA_OLD):
        raise ValidationError(f"Old St. Barbara wording remains for {year}")


def validate_uuid5(events: list[dict[str, list[str]]], year: int) -> None:
    found = 0
    for event in events:
        value = first(event, "UID")
        if value.startswith("urn:uuid:"):
            try:
                parsed = uuid.UUID(value[9:])
            except ValueError as exc:
                raise ValidationError(f"Malformed UUID UID in {year}") from exc
            if parsed.version == 5:
                found += 1
    if found == 0:
        raise ValidationError(f"No deterministic UUIDv5 UID found for {year}")


def validate(assets_dir: Path, accepted_tag: str) -> dict:
    metadata = load_json(assets_dir / "release_metadata.json")
    if metadata.get("source_repository") != "Blue-jp/tridentine_calendar":
        raise ValidationError("Unexpected source repository")
    if metadata.get("accepted_tag") != accepted_tag:
        raise ValidationError("Release metadata tag mismatch")
    if not re.fullmatch(r"[0-9a-f]{40}", metadata.get("tag_object", "")):
        raise ValidationError("Missing tag object provenance")
    if not re.fullmatch(r"[0-9a-f]{40}", metadata.get("source_commit", "")):
        raise ValidationError("Missing peeled commit provenance")

    names = release_asset_names(accepted_tag)
    manifest_path = assets_dir / names["checksums"]
    manifest = parse_checksum_manifest(manifest_path.read_bytes())
    for key in ("html", "plain", "html_zip", "plain_zip"):
        name = names[key]
        if name not in manifest:
            raise ValidationError(f"Checksum manifest is missing {name}")
        verify_checksum(assets_dir / name, manifest[name])

    for name, details in metadata.get("assets", {}).items():
        path = assets_dir / name
        if not path.is_file():
            raise ValidationError(f"Downloaded asset is missing: {name}")
        digest = sha256(path.read_bytes())
        if digest != details.get("sha256"):
            raise ValidationError(f"Metadata digest mismatch for {name}")
        api_digest = details.get("api_digest")
        if api_digest and api_digest != "sha256:" + digest:
            raise ValidationError(f"API digest mismatch for {name}")

    integrated = {}
    for form in ("html", "plain"):
        data = (assets_dir / names[form]).read_bytes()
        audit = audit_ics(data)
        require_clean_ics(audit, INTEGRATED_EVENT_COUNT)
        integrated[form] = audit
    if identity_map(integrated["html"]["events"]) != identity_map(
        integrated["plain"]["events"]
    ):
        raise ValidationError("Integrated plain/HTML UID mapping differs")

    archive_data = {}
    for form in ("html", "plain"):
        folder = f"{form}_by_calendar_year"
        expected = {
            f"{folder}/tridentine-calendar-ja-{year}-accepted-{form}.ics"
            for year in YEARS
        }
        expected.update({"README_IMPORT_JA.txt", "MANIFEST_PUBLIC.txt"})
        archive_data[form] = validate_zip_structure(
            assets_dir / names[f"{form}_zip"], expected
        )

    year_results = {}
    for year in YEARS:
        per_form = {}
        for form in ("html", "plain"):
            folder = f"{form}_by_calendar_year"
            entry = (
                f"{folder}/tridentine-calendar-ja-{year}-accepted-{form}.ics"
            )
            data = archive_data[form][entry]
            if len(data) >= 1_000_000:
                raise ValidationError(f"Year-separated ICS exceeds 1 MB: {entry}")
            audit = audit_ics(data, year)
            require_clean_ics(audit, EXPECTED_EVENTS[year])
            validate_st_barbara(audit["events"], year)
            if year in (2031, 2032):
                validate_uuid5(audit["events"], year)
            per_form[form] = audit
        if identity_map(per_form["html"]["events"]) != identity_map(
            per_form["plain"]["events"]
        ):
            raise ValidationError(f"Plain/HTML UID mapping differs for {year}")
        year_results[str(year)] = EXPECTED_EVENTS[year]

    report = {
        "status": "passed",
        "accepted_tag": accepted_tag,
        "source_commit": metadata["source_commit"],
        "integrated_events": INTEGRATED_EVENT_COUNT,
        "calendar_years": list(YEARS),
        "year_event_counts": year_results,
        "duplicate_uids": 0,
        "st_barbara_cases": len(YEARS) * 2,
        "deterministic_uid_years": [2031, 2032],
    }
    (assets_dir / "validation.json").write_text(
        json.dumps(report, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
        errors="strict",
        newline="\n",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--accepted-tag", required=True)
    args = parser.parse_args()
    report = validate(args.assets, args.accepted_tag)
    print(
        "[validate] release assets passed: "
        f"{report['integrated_events']} integrated events, "
        f"{len(report['calendar_years']) * 2} year-separated files"
    )


if __name__ == "__main__":
    main()
