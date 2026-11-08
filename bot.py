import asyncio
import logging
import os
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

from contests import Contest, ContestFetcher
from database import Database, GuildConfig

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("contest_bot")

APPLICATION_ID = os.getenv("DISCORD_APPLICATION_ID", "911434130259050526")
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
REMINDER_MINUTES_BEFORE = int(os.getenv("REMINDER_MINUTES_BEFORE", "180"))

# Window around the target reminder time (e.g., 165m to 185m before start for a 180m target)
WINDOW_UPPER_SECONDS = (REMINDER_MINUTES_BEFORE + 5) * 60
WINDOW_LOWER_SECONDS = max(60, (REMINDER_MINUTES_BEFORE - 15) * 60)

PLATFORM_COLORS = {
    "Codeforces": 0x1F8ACB,  # Codeforces Blue
    "AtCoder": 0x222222,     # AtCoder Dark
}


def build_contest_embed(contests: List[Contest], is_test: bool = False) -> discord.Embed:
    """Builds a Discord Embed for one or more simultaneous contests on the same platform."""
    first = contests[0]
    platform = first.platform
    color = PLATFORM_COLORS.get(platform, 0x5865F2)

    title_prefix = "🧪 [TEST] " if is_test else "⏰ "
    if len(contests) == 1:
        embed = discord.Embed(
            title=f"{title_prefix}{first.name}",
            url=first.url,
            description=(
                f"A **{platform}** competition is starting in **~3 hours**!"
                if not is_test
                else f"This is a test alert using the next upcoming **{platform}** competition."
            ),
            color=color,
        )
        embed.add_field(
            name="Start Time",
            value=f"<t:{first.start_time_unix}:F> (<t:{first.start_time_unix}:R>)",
            inline=False,
        )
        embed.add_field(
            name="Duration",
            value=first.formatted_duration,
            inline=True,
        )
        if first.rated_range:
            embed.add_field(
                name="Rated Range",
                value=first.rated_range,
                inline=True,
            )
        embed.add_field(
            name="Link",
            value=f"[Open Contest Page]({first.url})",
            inline=True,
        )
    else:
        # Multiple divisions starting at the exact same timestamp (e.g., CF Div. 1 + Div. 2)
        embed = discord.Embed(
            title=f"{title_prefix}{platform} Contests Starting Soon!",
            description=(
                f"**{len(contests)} {platform}** divisions are starting in **~3 hours**!"
                if not is_test
                else f"This is a test alert for upcoming simultaneous **{platform}** divisions."
            ),
            color=color,
        )
        embed.add_field(
            name="Start Time",
            value=f"<t:{first.start_time_unix}:F> (<t:{first.start_time_unix}:R>)",
            inline=False,
        )
        rounds_lines = [
            f"• **[{c.name}]({c.url})** ({c.formatted_duration})" for c in contests
        ]
        embed.add_field(
            name="Contests",
            value="\n".join(rounds_lines),
            inline=False,
        )

    embed.set_footer(text=f"{platform} • 3-Hour Contest Reminder")
    return embed


class ContestBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True

        app_id = int(APPLICATION_ID) if APPLICATION_ID.isdigit() else None
        super().__init__(intents=intents, application_id=app_id)

        self.tree = app_commands.CommandTree(self)
        self.db = Database()
        self.fetcher = ContestFetcher(cache_ttl_seconds=600)

    async def setup_hook(self) -> None:
        await self.tree.sync()
        logger.info("Synced slash commands globally.")
        self.check_contests_loop.start()

    async def on_ready(self) -> None:
        invite_url = (
            f"https://discord.com/oauth2/authorize?client_id={APPLICATION_ID}"
            "&permissions=2147634176&scope=bot%20applications.commands"
        )
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id if self.user else "?")
        logger.info("Connected to %d server(s).", len(self.guilds))
        logger.info("Invite URL: %s", invite_url)

    @tasks.loop(seconds=60)
    async def check_contests_loop(self) -> None:
        try:
            contests = await self.fetcher.get_upcoming_contests()
            if not contests:
                return

            now = int(time.time())
            due_contests: List[Contest] = []
            for contest in contests:
                time_until = contest.start_time_unix - now
                if WINDOW_LOWER_SECONDS <= time_until <= WINDOW_UPPER_SECONDS:
                    due_contests.append(contest)

            if not due_contests:
                return

            guild_configs = self.db.get_all_guild_configs()
            if not guild_configs:
                return

            # Group simultaneous contests by (platform, start_time_unix)
            grouped: Dict[Tuple[str, int], List[Contest]] = defaultdict(list)
            for c in due_contests:
                grouped[(c.platform, c.start_time_unix)].append(c)

            for config in guild_configs:
                for (_platform, _start_ts), group in grouped.items():
                    unannounced = [
                        c for c in group if not self.db.has_announced(config.guild_id, c.key)
                    ]
                    if not unannounced:
                        continue

                    sent = await self._send_reminder(config, unannounced, is_test=False)
                    if sent:
                        for c in unannounced:
                            self.db.mark_announced(config.guild_id, c.key)

        except Exception as exc:
            logger.error("Error in check_contests_loop: %s", exc, exc_info=True)

    @check_contests_loop.before_loop
    async def before_check_contests_loop(self) -> None:
        await self.wait_until_ready()

    async def _send_reminder(
        self,
        config: GuildConfig,
        contests: List[Contest],
        is_test: bool = False,
    ) -> bool:
        guild = self.get_guild(config.guild_id)
        if not guild:
            return False

        channel = guild.get_channel(config.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            logger.warning(
                "Channel %s not found or not messageable in guild %s",
                config.channel_id,
                config.guild_id,
            )
            return False

        role_mention = f"<@&{config.role_id}>"
        embed = build_contest_embed(contests, is_test=is_test)
        allowed_mentions = discord.AllowedMentions(roles=True, everyone=True, users=False)

        try:
            await channel.send(
                content=f"{role_mention} Competition starting in 3 hours!",
                embed=embed,
                allowed_mentions=allowed_mentions,
            )
            logger.info(
                "Sent %sreminder for %s to guild %s (channel %s)",
                "TEST " if is_test else "",
                [c.key for c in contests],
                config.guild_id,
                config.channel_id,
            )
            return True
        except discord.DiscordException as exc:
            logger.error(
                "Failed to send message in guild %s channel %s: %s",
                config.guild_id,
                config.channel_id,
                exc,
            )
            return False


bot = ContestBot()


@bot.tree.command(
    name="setup",
    description="Configure the channel and role to ping 3 hours before AtCoder & Codeforces contests.",
)
@app_commands.describe(
    channel="The text channel where contest reminders will be posted",
    role="The role to ping when a contest is 3 hours away",
)
@app_commands.default_permissions(manage_guild=True)
async def setup_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    role: discord.Role,
) -> None:
    if not interaction.guild_id:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return

    bot.db.set_guild_config(
        guild_id=interaction.guild_id,
        channel_id=channel.id,
        role_id=role.id,
    )

    # Sync command tree to this guild as well for instant availability
    await interaction.response.send_message(
        f"✅ **Contest reminders configured!**\n"
        f"• **Channel:** {channel.mention}\n"
        f"• **Role to Ping:** {role.mention}\n"
        f"• **Timing:** 3 hours before every AtCoder & Codeforces competition\n\n"
        f"*(Use `/test-ping` to send a test notification right now, or `/upcoming` to view scheduled contests.)*",
        ephemeral=True,
    )


@bot.tree.command(
    name="status",
    description="Check this server's current contest reminder settings.",
)
async def status_command(interaction: discord.Interaction) -> None:
    if not interaction.guild_id:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return

    config = bot.db.get_guild_config(interaction.guild_id)
    if not config:
        await interaction.response.send_message(
            "⚠️ **Not configured yet.** Use `/setup` to pick a channel and role!",
            ephemeral=True,
        )
        return

    contests = await bot.fetcher.get_upcoming_contests()
    next_contest_line = "None found"
    if contests:
        nxt = contests[0]
        reminder_ts = nxt.start_time_unix - (REMINDER_MINUTES_BEFORE * 60)
        next_contest_line = (
            f"**[{nxt.name}]({nxt.url})** ({nxt.platform})\n"
            f"• Starts: <t:{nxt.start_time_unix}:F> (<t:{nxt.start_time_unix}:R>)\n"
            f"• 3h Ping Scheduled: <t:{reminder_ts}:R>"
        )

    embed = discord.Embed(
        title="⚙️ Contest Reminder Status",
        color=0x57F287,
    )
    embed.add_field(name="Channel", value=f"<#{config.channel_id}>", inline=True)
    embed.add_field(name="Role to Ping", value=f"<@&{config.role_id}>", inline=True)
    embed.add_field(name="Next Contest", value=next_contest_line, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="upcoming",
    description="Show upcoming AtCoder and Codeforces competitions.",
)
@app_commands.describe(limit="Number of upcoming contests to show (1-15, default 8)")
async def upcoming_command(
    interaction: discord.Interaction,
    limit: Optional[int] = 8,
) -> None:
    await interaction.response.defer()
    count = max(1, min(limit or 8, 15))
    contests = await bot.fetcher.get_upcoming_contests()

    if not contests:
        await interaction.followup.send("No upcoming AtCoder or Codeforces contests found right now.")
        return

    lines: List[str] = []
    for c in contests[:count]:
        badge = "🟦 **CF**" if c.platform == "Codeforces" else "⬛ **AC**"
        lines.append(
            f"{badge} • **[{c.name}]({c.url})**\n"
            f"└ Starts <t:{c.start_time_unix}:F> (<t:{c.start_time_unix}:R>) • Duration: `{c.formatted_duration}`"
        )

    embed = discord.Embed(
        title="📅 Upcoming AtCoder & Codeforces Contests",
        description="\n\n".join(lines),
        color=0x5865F2,
    )
    embed.set_footer(text=f"Showing next {len(lines)} of {len(contests)} upcoming contests")
    await interaction.followup.send(embed=embed)


@bot.tree.command(
    name="test-ping",
    description="Send a test contest reminder to the configured channel and role right now.",
)
@app_commands.default_permissions(manage_guild=True)
async def test_ping_command(interaction: discord.Interaction) -> None:
    if not interaction.guild_id:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return

    config = bot.db.get_guild_config(interaction.guild_id)
    if not config:
        await interaction.response.send_message(
            "⚠️ Please run `/setup` first to choose a channel and role.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    contests = await bot.fetcher.get_upcoming_contests()
    if not contests:
        # Fallback sample contest if offline
        sample = Contest(
            key="ac:sample",
            platform="AtCoder",
            name="AtCoder Beginner Contest (Sample Test)",
            url="https://atcoder.jp/contests/",
            start_time_unix=int(time.time()) + 10800,
            duration_seconds=6000,
            rated_range=" - 1999",
        )
        target_contests = [sample]
    else:
        target_contests = [contests[0]]

    ok = await bot._send_reminder(config, target_contests, is_test=True)
    if ok:
        await interaction.followup.send(
            f"✅ Sent a test reminder to <#{config.channel_id}> pinging <@&{config.role_id}>!",
            ephemeral=True,
        )
    else:
        await interaction.followup.send(
            f"❌ Failed to send message to <#{config.channel_id}>. "
            "Check that the bot has **View Channel**, **Send Messages**, **Embed Links**, and **Mention @everyone, @here, and All Roles** permissions in that channel.",
            ephemeral=True,
        )


@bot.tree.command(
    name="disable",
    description="Disable contest reminders in this server.",
)
@app_commands.default_permissions(manage_guild=True)
async def disable_command(interaction: discord.Interaction) -> None:
    if not interaction.guild_id:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return

    removed = bot.db.remove_guild_config(interaction.guild_id)
    if removed:
        await interaction.response.send_message(
            "🛑 Contest reminders have been disabled for this server.", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "Contest reminders were not enabled in this server.", ephemeral=True
        )


def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "your_bot_token_here":
        print(
            "\n[!] ERROR: DISCORD_BOT_TOKEN is not set in .env!\n"
            f"1. Open https://discord.com/developers/applications/{APPLICATION_ID}/bot\n"
            "2. Click 'Reset Token' (or copy your Bot Token)\n"
            "3. Paste it into .env as DISCORD_BOT_TOKEN=...\n"
        )
        return

    bot.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
