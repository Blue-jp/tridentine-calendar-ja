# 1962 Roman Calendar Japanese Localization

This repository distributes subscription files for the Japanese localization of the 1962 Roman Calendar. Only published releases that have completed the Blue-jp acceptance process are deployed. Development branches are never deployed directly.

- Upstream project: [joe-antognini/tridentine_calendar](https://github.com/joe-antognini/tridentine_calendar)
- Japanese development fork: [Blue-jp/tridentine_calendar](https://github.com/Blue-jp/tridentine_calendar)
- Accepted source tag: `ja-localization-accepted-20260814`

Not every Japanese localization change has necessarily been merged into the upstream project. Automatic subscription is currently under testing. The Google Calendar and standard iCalendar URLs are designed to remain stable after testing is complete.

## 日本語

このrepositoryは、1962年版ローマ典礼暦の日本語ローカライズ版を固定URLで配信するための専用repositoryです。Blue-jpで受入確認を完了し、公開されたReleaseだけを配信します。開発途中のbranchを直接配信することはありません。

- 本家project: [joe-antognini/tridentine_calendar](https://github.com/joe-antognini/tridentine_calendar)
- 日本語開発Fork: [Blue-jp/tridentine_calendar](https://github.com/Blue-jp/tridentine_calendar)
- 現在のAccepted source tag: `ja-localization-accepted-20260814`

日本語版の全変更が本家projectへmerge済みとは限りません。固定URLによる自動購読機能は現在テスト中です。Google Calendar向けURLと標準iCalendar向けURLは、テスト完了後も変更しない方針です。

## Deployment

The Pages workflow accepts an exact published Accepted tag through `workflow_dispatch`. It downloads the corresponding public Release assets, verifies checksums and provenance, builds an ephemeral Pages artifact, validates the output, and deploys it with GitHub's official Pages Actions.

Generated ICS files and provenance files are not committed to this repository.
