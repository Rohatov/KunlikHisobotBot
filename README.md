# Telegram Sheets Bot

A production-ready Telegram bot that exports two specific worksheets from a
private Google Spreadsheet to PDF (preserving native Sheets formatting) and
posts them to a Telegram channel or group — automatically every day at a
configured time, and on demand via an admin-only button or command.

## 1. What it does

Every day at a configured time (default `12:00 Asia/Tashkent`), the bot:

1. Authenticates to Google Sheets with a Service Account (no browser, no
   interactive OAuth).
2. Locates two worksheets inside the configured spreadsheet by their
   Google `sheetId` (gid) — not by tab name or position, so renaming or
   reordering tabs never breaks it.
3. Exports each worksheet to its own PDF using Google's native Sheets
   export, preserving fonts, borders, colors, merged cells, column widths
   and row heights. Files are named `savdo_<date>.pdf` and
   `qoldiq_<date>.pdf` (e.g. `savdo_2026-08-30.pdf`), sent as plain
   documents with no caption text underneath.
4. Sends both PDFs as separate Telegram documents to the configured chat.

An authorized administrator can trigger the exact same flow on demand with
a `📄 Hisobotni yuborish` button or the `/report` command, in addition to
the daily schedule. All bot-facing text is in Uzbek.

**Group chats work too:** `TELEGRAM_CHANNEL_ID` isn't limited to channels —
it's just the destination chat ID for `bot.send_document`, which behaves
identically for channels, supergroups, and basic groups. Add the bot to
the group with permission to send messages/files, put the group's numeric
ID (negative number, e.g. `-1001234567890` for a supergroup) in
`TELEGRAM_CHANNEL_ID`, and no code changes are needed.

## 2. Features

- Private Google Sheets support via a Service Account (Viewer access).
- No OAuth flow, no browser, no interactive login required at runtime.
- Worksheets identified by immutable `sheetId`, resilient to renames.
- Two worksheets exported and sent as **separate** PDFs (`savdo_<date>.pdf`,
  `qoldiq_<date>.pdf`), no caption text.
- Delivery to a Telegram channel or group as documents (not photos).
- Daily scheduler (APScheduler) with configurable time and timezone.
- Admin-only manual trigger — both a button and the `/report` command.
- `/status` command showing schedule, last run, and next run.
- Execution lock preventing overlapping report generations.
- All bot-facing messages in Uzbek.
- Structured logging to stdout (for `journalctl`) and rotating log files.
- No secrets ever logged or exposed in Telegram messages.
- Ready-to-use systemd service for continuous operation on Linux.

## 3. Prerequisites

- Python 3.11+
- A Google Cloud project with the **Google Sheets API** enabled.
- A Google Service Account with a downloaded JSON key.
- The target Google Spreadsheet shared with the Service Account's email
  as **Viewer**.
- A Telegram bot token (from [@BotFather](https://t.me/BotFather)).
- The bot added to your target Telegram channel as an **administrator**
  with permission to post messages.

## 4. Google Service Account setup

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
   and create (or select) a project.
2. Enable the **Google Sheets API** for that project
   (APIs & Services → Library → "Google Sheets API" → Enable).
3. Create a Service Account
   (APIs & Services → Credentials → Create Credentials → Service Account).
4. Open the new Service Account → Keys → Add Key → **Create new key** →
   JSON. This downloads a `service-account.json` file.
5. Store that file **outside** the git repository, e.g.:
   ```bash
   mkdir -p ~/.config/telegram-sheets-bot
   mv ~/Downloads/service-account.json ~/.config/telegram-sheets-bot/service-account.json
   chmod 600 ~/.config/telegram-sheets-bot/service-account.json
   ```
6. Open the JSON file and copy the `client_email` value
   (looks like `xxxx@xxxx.iam.gserviceaccount.com`).
7. Open your Google Spreadsheet → **Share** → paste that email address →
   grant **Viewer** access.

## 5. Installation

```bash
git clone <your-repo-url> telegram-sheets-bot
cd telegram-sheets-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## 6. Configuration

Edit `.env` (see [`.env.example`](.env.example) for the full template):

```env
# Telegram
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHANNEL_ID=-1001234567890
ADMIN_TELEGRAM_ID=123456789

# Google Sheets
GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit
GOOGLE_SERVICE_ACCOUNT_FILE=/home/USERNAME/.config/telegram-sheets-bot/service-account.json

# Worksheet identifiers (Google sheetId / gid — not names)
WORKSHEET_1_ID=123456789
WORKSHEET_2_ID=987654321

# Scheduler
TIMEZONE=Asia/Tashkent
SCHEDULE_HOUR=12
SCHEDULE_MINUTE=0
```

**Finding a worksheet's `sheetId`:** open the spreadsheet in a browser,
click the tab you want, and read the `gid=<number>` value from the URL —
that number is the `sheetId`.

**Finding your Telegram IDs:** message
[@userinfobot](https://t.me/userinfobot) for your own `ADMIN_TELEGRAM_ID`;
for `TELEGRAM_CHANNEL_ID`, add the bot as admin to the channel and forward
a channel message to [@userinfobot](https://t.me/userinfobot), or check
`getUpdates` on the Bot API after posting in the channel.

The application validates all of the above at startup and refuses to
start with a clear error message if anything is missing or invalid.

## 7. Running manually

```bash
source venv/bin/activate
python -m app.main
```

On startup the bot verifies Google Sheets connectivity and that both
configured `sheetId` values exist, then starts polling Telegram and
registers the daily scheduler job. Logs are written to `logs/app.log`
and to stdout.

### Bot commands (admin only)

| Command    | Description (o'zbekcha)                                             |
|------------|-----------------------------------------------------------------------|
| `/start`   | Botni ishga tushirish, qisqacha ma'lumot va "Hisobotni yuborish" tugmasini ko'rsatadi |
| `/status`  | Bot holati, jadval vaqti, oxirgi va keyingi ishga tushish vaqtini ko'rsatadi |
| `/report`  | Ikkala hisobotni (Savdo, Qoldiq) darhol qo'lda yaratib, kanal/guruhga yuboradi (tugma bilan bir xil amal) |

Send `/start` to the bot from the admin account to see the manual
trigger button, or run `/report` directly to send both PDFs immediately
without opening the menu. Non-admin users get an "unauthorized" reply and
the attempt is logged.

## 8. Systemd deployment

An example unit file is provided at
[`deploy/telegram-sheets-bot.service`](deploy/telegram-sheets-bot.service).
Copy it, replacing `USERNAME` and the paths with your own:

```bash
sudo cp deploy/telegram-sheets-bot.service /etc/systemd/system/telegram-sheets-bot.service
sudo nano /etc/systemd/system/telegram-sheets-bot.service   # adjust User/paths
sudo systemctl daemon-reload
sudo systemctl enable telegram-sheets-bot
sudo systemctl start telegram-sheets-bot
sudo systemctl status telegram-sheets-bot
journalctl -u telegram-sheets-bot -f
```

The service restarts automatically on failure and starts on boot. Logs
are available both via `journalctl` and in `logs/app.log` (with rotation).

## 9. Troubleshooting

**Service Account cannot access the spreadsheet (`SheetsAccessError` /
HTTP 403 at startup)**
Confirm the spreadsheet is shared with the exact `client_email` from your
Service Account JSON, with at least Viewer access.

**Invalid `sheetId` (`WorksheetNotFoundError`)**
Open each tab in a browser and re-check the `gid=` value in the URL.
Tab names/positions are irrelevant — only the `gid` number matters.

**Bot cannot send to the channel/group**
Make sure the bot account itself has been added to the channel as an
**administrator** with "Post Messages" permission (for a group, it just
needs to be a member allowed to send messages/files — admin rights are
not required), and that `TELEGRAM_CHANNEL_ID` is the numeric chat ID
(usually starts with `-100` for channels/supergroups), not an `@username`.

**Scheduler fires at the wrong time**
Check `TIMEZONE` is a valid IANA name (e.g. `Asia/Tashkent`) and that
`SCHEDULE_HOUR`/`SCHEDULE_MINUTE` are in 24-hour, local-to-that-timezone
values. Use `/status` to confirm the computed "Next run" time.

**Missing credential file at startup**
The error message will show the exact path the app looked for. Confirm
`GOOGLE_SERVICE_ACCOUNT_FILE` in `.env` is an absolute path and the file
is readable by the user running the service.

**"A report is already being generated" when pressing the button**
This is the built-in execution lock — a scheduled or another manual run
is already in progress. Wait for it to finish; the bot will not run two
report generations concurrently.

## Project structure

```text
telegram-sheets-bot/
├── app/
│   ├── __init__.py
│   ├── main.py            # entry point
│   ├── config.py          # env loading + validation
│   ├── bot.py              # Telegram handlers & app wiring
│   ├── scheduler.py       # APScheduler daily job
│   ├── sheets_service.py  # Google auth, metadata, PDF export
│   ├── pdf_service.py     # temp file naming/cleanup
│   ├── report_service.py  # shared generate-and-send core + lock
│   ├── authorization.py   # admin-only guard
│   ├── exceptions.py
│   └── logging_config.py
├── deploy/
│   └── telegram-sheets-bot.service
├── logs/
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```
