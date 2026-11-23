# Database-down alert (Telegram) - setup

Built after 2026-11-23, when the Supabase project was suspended over an unpaid invoice. For about
40 minutes every login returned HTTP 500 and every full-access account silently showed as
"View only", because the role lookup could not read the user row. **Users noticed before we did.**
This is the thing that should notice.

It watches `https://tradgo.us/health` and treats BOTH of these as down, because to a person they
are the same thing:

- the site answers but reports `"database":"disconnected"` - what happened on the 23rd
- the site does not answer at all - service stopped, app crashed, host unreachable

Rules: poll every 60s, alert after **2 consecutive** failures (~2 min, inside the 5-minute
requirement), re-alert at most every **30 minutes** while it stays down, and send exactly **one**
RECOVERED message when it comes back.

It reads one public URL and writes one small state file. It never touches the database, the
service, or any Smart Move file.

---

## 1. Create the bot and get the token

In Telegram:

1. Search for **@BotFather** and open it.
2. Send `/newbot`.
3. Give it a display name (anything, e.g. `Trdgo Watchdog`).
4. Give it a username ending in `bot` (e.g. `trdgo_watchdog_bot`).
5. BotFather replies with a token like `8123456789:AAH...`. **That is the token.**

## 2. Get your chat id

Send your new bot any message (it will not reply - that is normal), then open this in a browser,
putting your token in place of `<YOUR_TOKEN>`:

```
https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
```

Find `"chat":{"id":123456789` - that number is your chat id. (Alternatively message
**@userinfobot**, which replies with your id and nothing else.)

A bot cannot message you until you have messaged it first. If `getUpdates` is empty, send the bot
a message and reload.

## 3. Paste the secrets - you do this, on the server

Create this file **yourself**. It is not in the kit, never in the repository, and no installer
touches `C:\SmartMoveRun\`:

**`C:\SmartMoveRun\watchdog_secrets.ps1`**

```powershell
$TelegramBotToken = 'paste-your-token-here'
$TelegramChatId   = 'paste-your-chat-id-here'
```

Exactly those two variable names. Do not paste either value into a chat window - the script reads
them straight from this file, and nothing else ever needs to see them.

If the file is missing or either value is blank, the watchdog prints `NO SECRETS`, exits, and
sends nothing. It never guesses a destination.

## 4. Put the script in place

```powershell
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/syed583/trdgo.us/<COMMIT>/deploy/db_watchdog.ps1" -OutFile "C:\SmartMoveRun\db_watchdog.ps1"
```

## 5. Prove the wiring before trusting it

```powershell
powershell -ExecutionPolicy Bypass -File C:\SmartMoveRun\db_watchdog.ps1 -TestMessage
```

Prints `TEST SENT` and you get a Telegram message. If it prints `TELEGRAM SEND FAILED`, the token
or chat id is wrong - fix the secrets file and run it again. **Do not register the task until this
works**, or the first you will know of a problem is a silence that means nothing.

## 6. Register the scheduled task (every 60 seconds, forever)

```powershell
schtasks /create /tn "TRDGO-DB-Watchdog" /sc minute /mo 1 /ru SYSTEM /rl HIGHEST /f /tr "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\SmartMoveRun\db_watchdog.ps1"
```

Verify:

```powershell
schtasks /query /tn "TRDGO-DB-Watchdog" /fo LIST /v | Select-String -Pattern "TaskName|Next Run Time|Schedule|Status|Last Result"
```

`Last Result: 0` means the check ran. `2` means the secrets file is missing or incomplete.

**A per-minute task is deliberate.** An internal 60-second loop would die with its process and go
quiet exactly when something is wrong; the scheduler restarts this one every minute whatever
happened to the last run.

## Turning it off / removing it

```powershell
schtasks /change /tn "TRDGO-DB-Watchdog" /disable
```

```powershell
schtasks /delete /tn "TRDGO-DB-Watchdog" /f
```

## What it will say

Down:

```
TRDGO IS DOWN  2026-11-23 02:31:04
the site answered but reports database=disconnected, status=error

Users will see HTTP 500 on login, and full-access accounts will show as View only.
Failed checks in a row: 2.
```

Recovered:

```
RECOVERED 2026-11-23 02:58:10
TRDGO is back: the site answers and the database is connected again.
```

## Known limits - worth knowing before you rely on it

- It polls from the VPS itself. If the **VPS** loses its network, the check fails and the alert
  cannot be sent either. It catches the app and the database going down; it cannot report its own
  host being cut off. An outside uptime service is the only thing that covers that, and it is
  worth adding separately.
- `/health` reports the database and the service. It does not prove logins work end to end. A
  failure that breaks only login while the database stays connected would not trigger this.
