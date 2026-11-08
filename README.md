# AtCoder & Codeforces 3-Hour Discord Reminder Bot

A multi-server Discord bot that automatically pings a configured role **3 hours before** every upcoming **AtCoder** and **Codeforces** competition.

---

## 1. Invite the Bot to Your Servers

Click this pre-configured OAuth2 link (using your Application ID `911434130259050526`) to invite the bot to any server you own or manage:

👉 **[Invite Bot to Discord Server](https://discord.com/oauth2/authorize?client_id=911434130259050526&permissions=2147634176&scope=bot%20applications.commands)**

*(Includes permissions: View Channels, Send Messages, Embed Links, Mention `@everyone`/`@here`/All Roles, and Use Slash Commands).*

---

## 2. Add Your Bot Token

1. Go to your bot's page in the Discord Developer Portal:
   👉 **[https://discord.com/developers/applications/911434130259050526/bot](https://discord.com/developers/applications/911434130259050526/bot)**
2. Click **Reset Token** (or copy your existing Bot Token).
3. Open `.env` in this folder and replace `your_bot_token_here`:
   ```env
   DISCORD_BOT_TOKEN=your_actual_bot_token_here
   ```

---

## 3. Run the Bot

A Python virtual environment (`.venv`) is already set up with all dependencies installed. Run:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python bot.py
```

---

## 4. Configure Each Server (Slash Commands)

Once the bot is online in your servers, run:

- **`/setup channel:#announcements role:@CompetitiveProgrammers`**
  Sets the channel and role to ping 3 hours before every AtCoder & Codeforces contest in that server.
- **`/test-ping`**
  Immediately sends a test alert to your configured channel & role using the next upcoming contest so you can verify permissions.
- **`/upcoming`**
  Displays the next upcoming AtCoder and Codeforces contests with local start times and live countdowns.
- **`/status`**
  Shows the current server's configured channel, role, and when the next 3-hour ping will fire.
- **`/disable`**
  Turns off reminders in the current server.
