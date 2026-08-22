#!/usr/bin/env python3
"""Validate the complete static Pages artifact before deployment."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import zipfile

from build_pages_artifact import (
    TEST_FEED_DESCRIPTION,
    TEST_FEED_DTSTAMP,
    TEST_FEED_REQUIRED_LINES,
    TEST_FEED_SUMMARY,
    TEST_FEED_UID,
    canonicalize_test_feed,
)
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
        "images/eucharistic-header.png",
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
        if path.suffix.lower() == ".png":
            continue
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


def validate_header_image(site: Path) -> None:
    path = site / "images" / "eucharistic-header.png"
    data = path.read_bytes()
    if sha256(data) != (
        "05d8d68a55132fa393a9aa7522a7264078fa3df4505438a1fa4b4328bfd079d6"
    ):
        raise ValidationError("Header image SHA-256 mismatch")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValidationError("Header image is not a PNG")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if (width, height) != (1261, 563):
        raise ValidationError("Header image dimensions mismatch")
    offset = 8
    metadata_chunks = {b"tEXt", b"zTXt", b"iTXt", b"eXIf"}
    while offset < len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        if chunk_type in metadata_chunks:
            raise ValidationError("Header image contains metadata")
        offset += length + 12


def validate_test_feed(site: Path) -> None:
    path = site / "test" / "subscription-test.ics"
    data = path.read_bytes()
    if not data.endswith(b"\r\n"):
        raise ValidationError("Test feed does not end with CRLF")
    without_crlf = data.replace(b"\r\n", b"")
    if b"\r" in without_crlf or b"\n" in without_crlf:
        raise ValidationError("Test feed contains a non-CRLF line ending")

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
            raise ValidationError(f"Test feed {key} mismatch")

    text = data.decode("utf-8", errors="strict")
    lines = text.split("\r\n")
    if not TEST_FEED_REQUIRED_LINES.issubset(lines):
        raise ValidationError("Test feed calendar envelope mismatch")
    if "DTSTART;VALUE=DATE:20260816\r\n" not in text:
        raise ValidationError("Test feed DTSTART is not an all-day date")
    if "DTEND;VALUE=DATE:20260817\r\n" not in text:
        raise ValidationError("Test feed DTEND is not an all-day date")

    logical = "\n".join(lines[:-1]) + "\n"
    lf_artifact = canonicalize_test_feed(logical.encode("utf-8"))
    crlf_artifact = canonicalize_test_feed(
        logical.replace("\n", "\r\n").encode("utf-8")
    )
    if lf_artifact != crlf_artifact or lf_artifact != data:
        raise ValidationError(
            "Test feed line-ending output is not deterministic"
        )


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
    google_calendar_link = (
        "https://calendar.google.com/calendar/u/2?cid="
        "ZTg0NmRjNjIwODBmYzI4MGVkZGI5NjVlNjdkN2E4MDExMzlmMWNmMDYyMGMy"
        "YmZmMjEzZTk0NjhiNTk5NzI3M0Bncm91cC5jYWxlbmRhci5nb29nbGUuY29t"
    )
    apple_calendar_link = (
        "webcal://blue-jp.github.io/tridentine-calendar-ja/"
        "calendar/plain.ics"
    )
    apple_fallback_link = (
        "https://blue-jp.github.io/tridentine-calendar-ja/"
        "calendar/plain.ics"
    )
    html_subscription_link = (
        "https://blue-jp.github.io/tridentine-calendar-ja/"
        "calendar/google.ics"
    )
    accepted_release_link = (
        "https://github.com/Blue-jp/tridentine_calendar/releases/tag/"
        "ja-localization-accepted-20260814"
    )
    required_root = (
        "1960年ローマ典礼暦・日本語版",
        "1960 Roman Liturgical Calendar – Japanese Edition",
        'src="images/eucharistic-header.png"',
        'alt="ご聖体と天使を描いた宗教画"',
        "日本固有の祝日",
        "現時点では",
        "Googleカレンダーをお使いの方",
        "Googleカレンダーに追加",
        google_calendar_link,
        "Apple標準のカレンダーをお使いの方",
        "Apple標準のカレンダーに追加",
        "「照会カレンダーを追加」のURL欄に入力してください",
        "以下のURLをAppleカレンダー",
        "URLをコピー",
        f'data-copy-url="{apple_fallback_link}"',
        "navigator.clipboard.writeText",
        'aria-live="polite"',
        f'href="{apple_calendar_link}"',
        apple_fallback_link,
        "その他のカレンダーアプリ",
        "祝日・記念の解説リンクつきカレンダー",
        "解説リンクなしカレンダー",
        html_subscription_link,
        'id="other-html-copy-status"',
        'id="other-plain-copy-status"',
        "calendar/google.ics",
        "calendar/plain.ics",
        "2024～2034",
        "典礼情報について",
        "他の典礼日が優先される祝日・記念",
        "詳細・ダウンロード",
        "年別カレンダー（ICS）と詳しい利用方法",
        "手動ダウンロードとリリース情報（GitHub）",
        accepted_release_link,
        "Joe Antognini氏による",
        "日本語ローカライズにおける変更",
        'class="license-line"',
        'class="publication-note"',
        'datetime="2026-08-22"',
        "joe-antognini/tridentine_calendar",
        "MIT License",
    )
    required_calendar = (
        "1960年ローマ典礼暦・日本語版",
        "1960 Roman Liturgical Calendar – Japanese Edition",
        'src="../images/eucharistic-header.png"',
        'alt="ご聖体と天使を描いた宗教画"',
        "Googleカレンダーへの追加、固定URL購読",
        "Googleカレンダーをお使いの方",
        "Googleカレンダーに追加",
        google_calendar_link,
        "Apple標準のカレンダーをお使いの方",
        "Apple標準のカレンダーに追加",
        "「照会カレンダーを追加」のURL欄に入力してください",
        "以下のURLをAppleカレンダー",
        "URLをコピー",
        f'data-copy-url="{apple_fallback_link}"',
        "navigator.clipboard.writeText",
        'aria-live="polite"',
        f'href="{apple_calendar_link}"',
        apple_fallback_link,
        "その他のカレンダーアプリで使う",
        "祝日・記念の解説リンクつきカレンダー",
        "解説リンクなしカレンダー",
        html_subscription_link,
        'id="other-html-copy-status"',
        'id="other-plain-copy-status"',
        "google.ics",
        "plain.ics",
        "年別カレンダー（ICS）をダウンロード",
        "2024～2034",
        "解説リンク（HTML）あり",
        "福者シモン遠甫等殉教者",
        "典礼上の確定値ではありません",
        "joe-antognini/tridentine_calendar",
        "MIT License",
    )
    if any(value not in root_html for value in required_root):
        raise ValidationError("Root page is missing required publication text")
    if root_html.index('src="images/eucharistic-header.png"') > root_html.index("<h1>"):
        raise ValidationError("Header image is not positioned before the H1")
    if calendar_html.index('src="../images/eucharistic-header.png"') > calendar_html.index("<h1>"):
        raise ValidationError("Calendar header image is not positioned before the H1")
    if any(value not in calendar_html for value in required_calendar):
        raise ValidationError("Calendar page is missing required publication text")
    for label, text in (("root", root_html), ("calendar", calendar_html)):
        if (
            re.search(r"min-height:\s*(?:4[4-9]|[5-9][0-9])px", text)
            is None
            or "@media (max-width:" not in text
        ):
            raise ValidationError(
                f"{label} page is missing mobile button safeguards"
            )
        if "#3d62ad" not in text or ":focus-visible" not in text:
            raise ValidationError(
                f"{label} page is missing accessible primary-button styling"
            )
        if "apple-fallback { margin-top: 12px; }" not in text:
            raise ValidationError(f"{label} page is missing Apple fallback spacing")
        expected_copy_targets = {
            apple_fallback_link: 2,
            html_subscription_link: 1,
        }
        for copy_url, expected_count in expected_copy_targets.items():
            if text.count(f'data-copy-url="{copy_url}"') != expected_count:
                raise ValidationError(
                    f"{label} page copy target count mismatch"
                )
        if text.count('class="copy-button"') != 3:
            raise ValidationError(f"{label} page copy button count mismatch")
        for status_id in (
            "apple-copy-status",
            "other-html-copy-status",
            "other-plain-copy-status",
        ):
            if (
                text.count(f'id="{status_id}"') != 1
                or text.count(f'aria-describedby="{status_id}"') != 1
            ):
                raise ValidationError(
                    f"{label} page copy status wiring mismatch"
                )
        lowered = text.lstrip().lower()
        if (
            not lowered.startswith("<!doctype html>")
            or text.count("<html") != 1
            or text.count("</html>") != 1
            or text.count("<body>") != 1
            or text.count("</body>") != 1
        ):
            raise ValidationError(f"{label} page structure is invalid")
    for year in YEARS:
        for form in ("html", "plain"):
            if f'href="by-year/{year}-{form}.ics"' not in calendar_html:
                raise ValidationError(
                    f"Calendar page is missing year link: {year} {form}"
                )

    def visible_text(text: str) -> str:
        return re.sub(r"<[^>]+>", "", text)

    root_visible = visible_text(root_html)
    calendar_visible = visible_text(calendar_html)
    publication_text = "2026年8月22日　童貞聖マリアの汚れなき御心　ページ公開"
    if root_visible.count(publication_text) != 1:
        raise ValidationError("Root page publication note count mismatch")
    if publication_text in calendar_visible:
        raise ValidationError("Calendar page unexpectedly contains publication note")
    about_index = root_html.index("<h3>このカレンダーについて</h3>")
    attribution_index = root_html.index("Joe Antognini氏による")
    main_end_index = root_html.index("</main>")
    footer_index = root_html.index("<footer>")
    license_index = root_html.index('<p class="license-line">')
    publication_index = root_html.index('<p class="publication-note">')
    footer_end_index = root_html.index("</footer>")
    if not (
        about_index
        < attribution_index
        < main_end_index
        < footer_index
        < license_index
        < publication_index
        < footer_end_index
    ):
        raise ValidationError("Root page publication note position mismatch")
    accepted_tag = "ja-localization-accepted-20260814"
    known_limitation = "福者シモン遠甫等殉教者"
    if accepted_tag in root_visible or accepted_tag in calendar_visible:
        raise ValidationError("Accepted tag is still displayed on a public page")
    if known_limitation in root_visible:
        raise ValidationError("Root page still displays the known limitation")
    if known_limitation not in calendar_visible:
        raise ValidationError("Calendar page lost the known limitation")
    forbidden_visible_text = (
        "Google Calendarをお使いの方",
        ">Google Calendarに追加</a>",
        ">Apple Calendarに追加</a>",
        ">説明リンク付きICS</a>",
        ">Plain ICS</a>",
        'class="button" href="calendar/google.ics"',
        'class="button" href="calendar/plain.ics"',
        'class="button" href="google.ics"',
        'class="button" href="plain.ics"',
        "実地試験では即時反映を確認しました",
        "Google Calendarではカレンダー全体を一色で表示します",
        "各予定の説明欄には、典礼色と関連する解説リンクを掲載しています",
        "<th>Year</th>",
        "<th>HTML description</th>",
        "<th>Plain description</th>",
    )
    if any(value in root_html or value in calendar_html for value in forbidden_visible_text):
        raise ValidationError("Public page still contains replaced wording")
    forbidden_public_text = (
        "Subscription testing",
        "購読テスト中",
        "Testing / テスト中",
        "一般利用向けの登録案内はまだ開始していません",
    )
    if any(
        value in root_html or value in calendar_html
        for value in forbidden_public_text
    ):
        raise ValidationError("Public page still contains testing text")
    if (
        "subscription-test.ics" in root_html
        or "subscription-test.ics" in calendar_html
    ):
        raise ValidationError(
            "Public navigation links to the private test feed"
        )

    validate_test_feed(site)
    validate_header_image(site)
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
