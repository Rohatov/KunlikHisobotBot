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
   documents with no caption text underneath. `<date>` is the report
   date written **inside** the worksheet (read from a configured cell or
   auto-detected), not the day the bot runs — so a report for 1 September
   that is sent on 3 September is still named `savdo_2026-09-01.pdf`.
   Today's date is used only if no date can be found in the worksheet.
4. Sends both PDFs as separate Telegram documents to the configured chat.

Any of the configured administrators can trigger the exact same flow on
demand with a `📄 Hisobotni yuborish` button or the `/report` command, in
addition to the daily schedule. All bot-facing text is in Uzbek.

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
  `qoldiq_<date>.pdf`), no caption text. The date comes from the
  worksheet itself, so the filename always matches the report's date.
- Delivery to a Telegram channel or group as documents (not photos).
- Daily scheduler (APScheduler) with configurable time and timezone.
- Admin-only manual trigger — both a button and the `/report` command.
  Any number of admins, one `ADMINn_ID` line each in `.env`.
- `/status` command **and** button showing schedule, last run, next run,
  and for each worksheet of the last run the file name that was sent and
  which cell its date came from.
- Silent on success (no "sent successfully" chat spam) — the PDFs landing
  in the channel are the confirmation; only failures are messaged to the
  admin, including the case where a worksheet's date could not be read
  and today's date had to be used in the file name. Check `/status` any
  time to see the last run's outcome.
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

# Admins: one line per admin, numbered ADMIN1_ID, ADMIN2_ID, ADMIN3_ID, ...
ADMIN1_ID=123456789
ADMIN2_ID=987654321

# Google Sheets
GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit
GOOGLE_SERVICE_ACCOUNT_FILE=/home/USERNAME/.config/telegram-sheets-bot/service-account.json

# Worksheet identifiers (Google sheetId / gid — not names)
WORKSHEET_1_ID=123456789
WORKSHEET_2_ID=987654321

# Cell holding each worksheet's report date (defaults: E4 / B1; "auto" = scan)
WORKSHEET_1_DATE_CELL=E4
WORKSHEET_2_DATE_CELL=B1

# Scheduler
TIMEZONE=Asia/Tashkent
SCHEDULE_HOUR=12
SCHEDULE_MINUTE=0
```

**Finding a worksheet's `sheetId`:** open the spreadsheet in a browser,
click the tab you want, and read the `gid=<number>` value from the URL —
that number is the `sheetId`.

**Report date in the filename (`WORKSHEET_N_DATE_CELL`):** the `<date>` in
`savdo_<date>.pdf` / `qoldiq_<date>.pdf` is read from the worksheet, not
from the clock, because reports are often sent a day or two after their
business date. By default the Savdo date is read from cell **`E4`** and
the Qoldiq date from cell **`B1`** (the cells used in the production
spreadsheet; leaving the variables empty keeps these defaults). If the
layout changes, set `WORKSHEET_1_DATE_CELL` / `WORKSHEET_2_DATE_CELL` to
the new A1 reference. Setting a variable to `auto` makes the bot scan the
top-left `A1:Z40` block of that worksheet row by row instead, mirroring
what a reader sees in the PDF:

- hidden rows and columns are skipped (they are not printed);
- the first cell whose *displayed* text is a full date wins — e.g.
  `01.09.2026`, `2026-09-01`, `1-sentyabr`, `2 сентября 2026`,
  `Sep 1, 2026` — whether it is typed text or a real date value;
- a cell that holds a date value but only shows a fragment of it (a
  header formatted as `2026` or a month number `8`, typical for
  `=EOMONTH(...)`-style helper cells) is used only if nothing better is
  found;
- when a cell both displays a date and holds a real date value, the value
  is trusted over the text, so locale displays like `9/1/2026` are safe.

Numeric text dates are read day-first (`dd.mm.yyyy`); a missing year means
the current year. Each run logs which cell was chosen, e.g.
`Worksheet ID 123 report date: 2026-09-01 (cell D3 displays '01.09.2026')`.
If no date is found (logged as a warning), today's date is used so the
report is still delivered.

**Admins (`ADMINn_ID`):** every Telegram user allowed to use `/status`,
`/report` and the buttons is listed as its own numbered variable —
`ADMIN1_ID`, `ADMIN2_ID`, `ADMIN3_ID`, ... The numbering only needs to be
unique, gaps are fine, and blank entries are ignored. To add an admin, add
one more `ADMIN<n>_ID=<numeric id>` line and restart the bot; to remove
one, delete their line. At least one admin is required. The original
single `ADMIN_TELEGRAM_ID` variable still works and is merged with the
numbered ones, so existing deployments need no change. Everyone else gets
the "⛔ Sizda ushbu botdan foydalanish huquqi yo'q." reply.

**Finding your Telegram IDs:** message
[@userinfobot](https://t.me/userinfobot) for each admin's `ADMINn_ID`;
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

### Bot commands (admins only)

| Command    | Description (o'zbekcha)                                             |
|------------|-----------------------------------------------------------------------|
| `/start`   | Botni ishga tushirish, "Hisobotni yuborish" va "Status" tugmalarini ko'rsatadi |
| `/status`  | Bot holati, jadval vaqti, oxirgi va keyingi ishga tushish vaqtini ko'rsatadi |
| `/report`  | Ikkala hisobotni (Savdo, Qoldiq) darhol qo'lda yaratib, kanal/guruhga yuboradi (tugma bilan bir xil amal) |

`/start` shows both actions as buttons (📄 Hisobotni yuborish, ℹ️ Status),
so the admin normally never needs to type `/status` or `/report` by hand.
On a successful manual run, the bot sends **no** confirmation message —
the PDFs appearing in the channel are the confirmation. It only messages
the admin back if something went wrong (lock conflict, PDF generation, or
delivery failure); check `/status` any time to see the last run's outcome.
Non-admin users get an "unauthorized" reply and the attempt is logged.

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

## 9. "Hisobotni yuborish" button inside Google Sheets

A Google Apps Script that duplicates `/report`'s behavior directly from
inside the spreadsheet is provided at
[`google-apps-script/Code.gs`](google-apps-script/Code.gs). It exports the
same two worksheets to `savdo_<date>.pdf` / `qoldiq_<date>.pdf` (no
caption) and posts them straight to Telegram — independent of the Python
bot process, since Apps Script cannot call into a server that has no
public HTTPS endpoint.

There are two ways to trigger it, and which one to use depends on who
needs to click it:

- **Drawing + "Assign script"** (`sendReportToTelegram`) — only works for
  people with **Edit** access who individually authorize the script.
  Viewers cannot run it at all; this is a hard Google limitation, not a
  bug.
- **Web App link** (`doGet`) — runs under the deploying owner's identity
  for *everyone* who opens the link, including read-only Viewers, with no
  per-user authorization prompt. **Use this one if anyone with view access
  to the sheet should be able to trigger it.**

### Setup (common steps)

1. Open the spreadsheet → **Extensions → Apps Script**.
2. Replace the contents of `Code.gs` with
   [`google-apps-script/Code.gs`](google-apps-script/Code.gs) from this repo.
3. In the Apps Script editor, open **Project Settings (⚙️) → Script
   Properties** and add:
   - `TELEGRAM_BOT_TOKEN` — same value as `.env`'s `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID` — same value as `.env`'s `TELEGRAM_CHANNEL_ID`
   - `WORKSHEET_1_ID` — same value as `.env`'s `WORKSHEET_1_ID`
   - `WORKSHEET_2_ID` — same value as `.env`'s `WORKSHEET_2_ID`
   - `WORKSHEET_1_DATE_CELL` / `WORKSHEET_2_DATE_CELL` — *optional*, same
     meaning as in `.env` (cell holding the report date; defaults `E4` for
     Savdo and `B1` for Qoldiq when omitted, `auto` to scan). The script
     names the PDFs by the date inside the worksheet exactly like the bot
     does.
4. Save (Ctrl+S).
5. Optional check: select `debugReportDates` in the function dropdown,
   click **Run**, and open **Executions** — it logs which date (and thus
   which filename) will be used for each worksheet without sending
   anything to Telegram.

### Option A — Web App link (works for Viewers too, recommended)

1. Click **Deploy → New deployment**.
2. Click the ⚙️ next to "Select type" → **Web app**.
3. Set **Execute as: Me** (your account) and **Who has access: Anyone**.
4. Click **Deploy**, then **Authorize access** and grant permission — this
   is a *one-time* consent from you as the owner; nobody else will ever
   see an authorization prompt.
5. Copy the generated URL (ends with `/exec`).
6. Turn it into a clickable button, either way:
   - **Text cell (simplest, no image needed):** click an empty cell, type
     `📄 Hisobotni yuborish`, select it, press **Ctrl+K** (or **Insert →
     Link**), paste the `/exec` URL, **Apply**. Optionally make it look
     like a button: bold the text, set a background color and borders
     (**Format → ...**), and widen the row/column.
   - **Image:** **Insert → Image → Image over cells**, upload/pick an
     icon, click the inserted image once → the link (🔗) icon above it →
     paste the `/exec` URL → **Apply**.

Now anyone who can open the sheet (Viewer or Editor) can click it — Sheets
shows a small preview card with the link, click through it once — which
opens the URL in a new tab, runs the export-and-send flow as you, and
shows a simple "✅ Hisobot yuborildi" / "❌ Xatolik" confirmation page.

> Since "Anyone" with the link can trigger sending, keep the `/exec` URL
> itself only where the intended people can see it (e.g. only inside this
> sheet). Use **"Anyone with Google account"** instead of **"Anyone"** in
> step 3 if you want to require the clicker to at least be signed in.

### Option B — Drawing + Assign script (Editors only)

1. **Insert → Drawing**, add a text box/shape labeled
   `📄 Hisobotni yuborish`, then **Save and Close**.
2. Click the drawing once → the ⋮ menu in its corner → **Assign script**
   → type `sendReportToTelegram` → OK.
3. The first time each editor clicks it, Google shows an authorization
   prompt they must accept ("Review permissions" → their account →
   **Advanced** → "Go to ... (unsafe)" → **Allow**). If that dialog is
   blocked by a popup blocker, the click will appear to hang/"load"
   forever — allow popups for `script.google.com` and try again.

### Option C — Custom menu item (Editors only, same limitation as B)

`Code.gs` also defines `onOpen()`, which adds a **📄 Hisobot** menu next to
**Help** in the top menu bar, with a **Hisobotni yuborish** item that runs
`sendReportToTelegram`. It appears automatically for anyone who opens the
sheet — but clicking the item itself is a normal bound-script function
call, not a plain hyperlink, so it hits the exact same wall as Option B:
Google requires **Edit** access to run it at all, and Viewers get "You do
not have permission to run this script." There is no way to make a Sheets
menu item itself open an external URL, so **the top menu bar cannot host
a Viewer-usable trigger** — only a hyperlink (cell or image, Option A) can,
since following a link is a plain browser navigation that never asks
Apps Script for authorization.

### Why it got stuck "loading" for you

Drawing-assigned functions run under the *clicking user's own*
authorization. The first click for any account needs an interactive
consent popup; if that popup is blocked, opened behind the window, or
never completed, the sheet just shows a spinner indefinitely — this
happens even for the file owner. It's also fundamentally unusable for
Viewer-only accounts, since Google never lets a Viewer grant that consent
at all. The Web App method (Option A) sidesteps both problems, since only
you authorize once, at deploy time.

## 10. Troubleshooting

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

**PDF filename shows the wrong date (e.g. the send date instead of the
report's date)**
First make sure the running bot actually has this version: after a
`git pull`, restart the service (`sudo systemctl restart
telegram-sheets-bot`) — a bot process started from older code keeps
naming files by the send date. With the current version, `/report`
replies with a `⚠️` message whenever a file had to use today's date, and
`/status` lists each file of the last run together with the cell its date
came from (e.g. `savdo_2026-09-01.pdf — yuborildi (sana E4 katagidan)`).

The date is read from a fixed cell of each worksheet (`E4` for Savdo,
`B1` for Qoldiq unless overridden). If that cell is empty or does not
contain a date, the log says `No date found in worksheet ID … (cell E4,
displays '…'); falling back to today's date` and the admin is told the
reason. If the sheet layout moved the date to
another cell, click the cell that shows the report date in Sheets, read
its address from the name box (top-left), and set `WORKSHEET_1_DATE_CELL`
/ `WORKSHEET_2_DATE_CELL` to it in `.env` (restart the bot) and in Script
Properties. The bot's log line `Worksheet ID … report date: … (cell E4
displays '…')` shows exactly which cell and value were used; for the Apps
Script button, run `debugReportDates` and read the same information in
**Executions**.

**`/report` warns `… bugungi sana bilan yuborildi, chunki Google Sheets API
xatosi (HTTP 403)` although the PDFs themselves arrive**
The PDFs and the startup check use endpoints that accept the bot's
read-only scopes, so a 403 that hits *only* the date read means the date
lookup is using a Sheets API method those scopes are not allowed to call.
Versions before 2026-09-04 read the cell with `spreadsheets.getByDataFilter`,
which Google authorises only for the full `spreadsheets`/`drive` scopes;
the current version reads it with `spreadsheets.get` and an A1 range built
from the tab's name (`'Savdo'!E4`), which works with `spreadsheets.readonly`.
`git pull` and restart the service. If the warning persists on the current
version, the log line `Could not read the date from worksheet ID … (HTTP
403: …)` carries Google's reason: usually the Sheets API is disabled in the
Service Account's Cloud project (section 4, step 2) or the spreadsheet is
no longer shared with the Service Account.

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

**Apps Script: `❌ savdo: Address unavailable: https://api.telegram.org/...`
(or `DNS error`, `Timeout`) while the other worksheet was sent fine**
This is a transient network failure inside Google's `UrlFetchApp`, not a
problem with the bot token, the chat ID or the worksheet — the very next
request usually succeeds. The script now retries each Telegram/export
request up to 4 times with a short back-off, and only reports a failure if
all attempts fail; just press the button again in that case. Older
versions of the script printed the raw error, which contained the full
Telegram URL **including the bot token**, on the Web App result page. If
anyone besides you may have seen such a message, revoke the token in
[@BotFather](https://t.me/BotFather) (`/revoke`) and update both `.env`
and the Script Properties with the new one.

**Apps Script says "Sozlamalar to'liq emas" even though the Script
Properties look correct**
`PropertiesService.getScriptProperties()` matches key names exactly,
including case and spaces — a property saved as `TELEGRAM_CHANNEL_ID`
(matching `.env`'s name) will **not** be found, since the script looks up
`TELEGRAM_CHAT_ID`. Run the `debugProperties` function from
[`google-apps-script/Code.gs`](google-apps-script/Code.gs) (select it from
the function dropdown in the Apps Script editor → **Run** → check
**Executions**/**View → Logs**) — it prints every key it actually finds
and flags any of the four required ones that are missing, so you can spot
a typo immediately. Also remember: if you edit `Code.gs` **after**
creating a Web App deployment, you must go to **Deploy → Manage
deployments → ✏️ → New version → Deploy** for the `/exec` URL to run the
updated code — editing Script Properties, though, takes effect immediately
without redeploying.

## Project structure

```text
telegram-sheets-bot/
├── app/
│   ├── __init__.py
│   ├── main.py            # entry point
│   ├── config.py          # env loading + validation
│   ├── bot.py              # Telegram handlers & app wiring
│   ├── scheduler.py       # APScheduler daily job
│   ├── sheets_service.py  # Google auth, metadata, report date, PDF export
│   ├── sheet_date.py      # finding/parsing the report date inside a worksheet
│   ├── pdf_service.py     # temp file naming/cleanup
│   ├── report_service.py  # shared generate-and-send core + lock
│   ├── authorization.py   # admin-only guard
│   ├── exceptions.py
│   └── logging_config.py
├── deploy/
│   └── telegram-sheets-bot.service
├── google-apps-script/
│   └── Code.gs             # optional in-sheet "Hisobotni yuborish" button
├── tests/
│   └── test_sheet_date.py  # offline tests (python -m unittest discover tests)
├── logs/
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```
