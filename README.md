# 1960 Roman Liturgical Calendar – Japanese Edition

This repository publishes the **1960年ローマ典礼暦・日本語版** for calendar subscription and manual download. It is based on the Roman liturgical calendar revised in 1960 and included in the 1962 edition of the *Roman Missal*, with proper liturgical days for Japan.

- Public site: <https://blue-jp.github.io/tridentine-calendar-ja/>
- Add the public Google Calendar: [Google Calendarに追加](https://calendar.google.com/calendar/u/2?cid=ZTg0NmRjNjIwODBmYzI4MGVkZGI5NjVlNjdkN2E4MDExMzlmMWNmMDYyMGMyYmZmMjEzZTk0NjhiNTk5NzI3M0Bncm91cC5jYWxlbmRhci5nb29nbGUuY29t)
- Accepted source tag: `ja-localization-accepted-20260814`
- Calendar years: 2024–2034

Only published releases that have completed the Blue-jp acceptance process are deployed. Development branches are never deployed directly.

## Subscription URLs

- HTML description: <https://blue-jp.github.io/tridentine-calendar-ja/calendar/google.ics>
- Plain description: <https://blue-jp.github.io/tridentine-calendar-ja/calendar/plain.ics>

Google Calendar users should add the public Google Calendar from the site. Apple Calendar and other iCalendar-compatible applications can subscribe to the stable ICS URLs above. Refresh timing depends on the calendar application.

## 日本語

Google Calendarでは、公開Google Calendarを直接追加する方法を推奨します。一度追加すれば、配布側で更新された内容が購読カレンダーにも反映され、通常はICSの再インポートは不要です。実地試験では追加・更新とも即時反映を確認しましたが、常時の即時反映を保証するものではありません。

Apple CalendarなどのiCalendar対応アプリでは、上記の固定ICS URLを購読できます。更新の反映には、アプリ側の再取得タイミングによる時間差が生じる場合があります。

典礼色は各予定の説明欄に記載されています。Google Calendarではカレンダー全体を一色で表示します。

### 既知の典礼上の制限事項

12月4日の「福者イエロニモ・デ・アンジェリス、福者シモン遠甫等殉教者」の1960年以後の正式等級は、典拠がないため確定していません。現在の内部rankはローダー既定値であり、典礼上の確定値ではありません。

## Attribution and license

- Upstream project: [joe-antognini/tridentine_calendar](https://github.com/joe-antognini/tridentine_calendar)
- Japanese development fork: [Blue-jp/tridentine_calendar](https://github.com/Blue-jp/tridentine_calendar)
- License: [MIT License](LICENSE.txt)

Not every Japanese localization change has necessarily been merged into the upstream project.

## Deployment

The Pages workflow accepts an exact published Accepted tag through `workflow_dispatch`. It downloads that Release's public assets, verifies checksums and provenance, builds an ephemeral Pages artifact, validates the output, and deploys it with GitHub's official Pages Actions.

Generated production ICS files and provenance files are not committed to this repository.
