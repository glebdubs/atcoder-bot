# Codeforces & AtCoder Beginner Contest 1-Hour Discord Reminder Bot

A multi-server Discord bot that automatically pings separate configured roles **1 hour before** every upcoming **Codeforces** contest and **AtCoder Beginner Contest (ABC)**.

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

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python bot.py
```

---

## 4. Configure Each Server (Slash Commands)

Once the bot is online in your servers, run:

- **`/setup channel:#announcements codeforces_role:@Codeforces atcoder_role:@AtCoderABC`**
  Sets the announcement channel and the two separate roles to ping 1 hour before Codeforces contests and AtCoder Beginner Contests (ABCs).
- **`/test-ping [platform: Codeforces | AtCoder]`**
  Immediately sends a test alert to your configured channel & corresponding role so you can verify permissions.
- **`/upcoming`**
  Displays the next upcoming Codeforces contests and AtCoder Beginner Contests with local start times and live countdowns.
- **`/status`**
  Shows the current server's configured channel, both roles, and when the next 1-hour ping will fire.
- **`/disable`**
  Turns off reminders in the current server.
