import asyncio
import logging
import os
import time
from collections import defaultdict
from typing import Dict, List, Literal, Optional, Tuple

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
REMINDER_MINUTES_BEFORE = int(os.getenv("REMINDER_MINUTES_BEFORE", "60"))

# Window around the target reminder time (e.g., 45m to 65m before start for a 60m target)
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
    platform_label = "AtCoder Beginner Contest" if platform == "AtCoder" else "Codeforces"

    title_prefix = "🧪 [TEST] " if is_test else "⏰ "
    if len(contests) == 1:
        embed = discord.Embed(
            title=f"{title_prefix}{first.name}",
            url=first.url,
            description=(
                f"An **{platform_label}** is starting in **~1 hour**!"
                if platform == "AtCoder" and not is_test
                else (
                    f"A **{platform_label}** competition is starting in **~1 hour**!"
                    if not is_test
                    else f"This is a test alert using the next upcoming **{platform_label}**."
                )
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
                f"**{len(contests)} {platform}** divisions are starting in **~1 hour**!"
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

    embed.set_footer(text=f"{platform_label} • 1-Hour Contest Reminder")
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

        platform = contests[0].platform
        role_id = config.role_id_for_platform(platform)
        role_mention = f"<@&{role_id}>"
        embed = build_contest_embed(contests, is_test=is_test)
        allowed_mentions = discord.AllowedMentions(roles=True, everyone=True, users=False)

        try:
            await channel.send(
                content=f"{role_mention} Competition starting in 1 hour!",
                embed=embed,
                allowed_mentions=allowed_mentions,
            )
            logger.info(
                "Sent %sreminder for %s to guild %s (channel %s, role %s)",
                "TEST " if is_test else "",
                [c.key for c in contests],
                config.guild_id,
                config.channel_id,
                role_id,
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
    description="Configure the channel and roles to ping 1 hour before Codeforces & AtCoder Beginner Contests.",
)
@app_commands.describe(
    channel="The text channel where contest reminders will be posted",
    codeforces_role="The role to ping 1 hour before Codeforces contests",
    atcoder_role="The role to ping 1 hour before AtCoder Beginner Contests (ABC)",
)
@app_commands.default_permissions(manage_guild=True)
async def setup_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    codeforces_role: discord.Role,
    atcoder_role: discord.Role,
) -> None:
    if not interaction.guild_id:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return

    bot.db.set_guild_config(
        guild_id=interaction.guild_id,
        channel_id=channel.id,
        codeforces_role_id=codeforces_role.id,
        atcoder_role_id=atcoder_role.id,
    )

    await interaction.response.send_message(
        f"✅ **Contest reminders configured!**\n"
        f"• **Channel:** {channel.mention}\n"
        f"• **Codeforces Role:** {codeforces_role.mention}\n"
        f"• **AtCoder Beginner Contest Role:** {atcoder_role.mention}\n"
        f"• **Timing:** 1 hour before each contest starts\n\n"
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
            "⚠️ **Not configured yet.** Use `/setup` to pick a channel and roles!",
            ephemeral=True,
        )
        return

    contests = await bot.fetcher.get_upcoming_contests()
    next_contest_line = "None found"
    if contests:
        nxt = contests[0]
        reminder_ts = nxt.start_time_unix - (REMINDER_MINUTES_BEFORE * 60)
        role_id = config.role_id_for_platform(nxt.platform)
        next_contest_line = (
            f"**[{nxt.name}]({nxt.url})** ({nxt.platform})\n"
            f"• Starts: <t:{nxt.start_time_unix}:F> (<t:{nxt.start_time_unix}:R>)\n"
            f"• 1h Ping Scheduled: <t:{reminder_ts}:R> (will ping <@&{role_id}>)"
        )

    embed = discord.Embed(
        title="⚙️ Contest Reminder Status",
        color=0x57F287,
    )
    embed.add_field(name="Channel", value=f"<#{config.channel_id}>", inline=True)
    embed.add_field(
        name="Codeforces Role", value=f"<@&{config.codeforces_role_id}>", inline=True
    )
    embed.add_field(
        name="AtCoder (ABC) Role", value=f"<@&{config.atcoder_role_id}>", inline=True
    )
    embed.add_field(name="Next Contest", value=next_contest_line, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="upcoming",
    description="Show upcoming Codeforces and AtCoder Beginner Contests.",
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
        await interaction.followup.send(
            "No upcoming Codeforces or AtCoder Beginner Contests found right now."
        )
        return

    lines: List[str] = []
    for c in contests[:count]:
        badge = "🟦 **CF**" if c.platform == "Codeforces" else "⬛ **ABC**"
        lines.append(
            f"{badge} • **[{c.name}]({c.url})**\n"
            f"└ Starts <t:{c.start_time_unix}:F> (<t:{c.start_time_unix}:R>) • Duration: `{c.formatted_duration}`"
        )

    embed = discord.Embed(
        title="📅 Upcoming Codeforces & AtCoder Beginner Contests",
        description="\n\n".join(lines),
        color=0x5865F2,
    )
    embed.set_footer(text=f"Showing next {len(lines)} of {len(contests)} upcoming contests")
    await interaction.followup.send(embed=embed)


@bot.tree.command(
    name="test-ping",
    description="Send a test contest reminder to the configured channel and role right now.",
)
@app_commands.describe(
    platform="Which platform's role to test (Codeforces or AtCoder). Defaults to the next contest's platform."
)
@app_commands.default_permissions(manage_guild=True)
async def test_ping_command(
    interaction: discord.Interaction,
    platform: Optional[Literal["Codeforces", "AtCoder"]] = None,
) -> None:
    if not interaction.guild_id:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return

    config = bot.db.get_guild_config(interaction.guild_id)
    if not config:
        await interaction.response.send_message(
            "⚠️ Please run `/setup` first to choose a channel and roles.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    contests = await bot.fetcher.get_upcoming_contests()
    if platform:
        contests = [c for c in contests if c.platform == platform]

    if not contests:
        chosen_platform = platform or "AtCoder"
        sample = Contest(
            key=f"sample:{chosen_platform.lower()}",
            platform=chosen_platform,
            name=(
                "AtCoder Beginner Contest (Sample Test)"
                if chosen_platform == "AtCoder"
                else "Codeforces Round (Div. 2) (Sample Test)"
            ),
            url=(
                "https://atcoder.jp/contests/"
                if chosen_platform == "AtCoder"
                else "https://codeforces.com/contests"
            ),
            start_time_unix=int(time.time()) + 3600,
            duration_seconds=6000 if chosen_platform == "AtCoder" else 7200,
            rated_range=" - 1999" if chosen_platform == "AtCoder" else None,
        )
        target_contests = [sample]
    else:
        target_contests = [contests[0]]

    role_id = config.role_id_for_platform(target_contests[0].platform)
    ok = await bot._send_reminder(config, target_contests, is_test=True)
    if ok:
        await interaction.followup.send(
            f"✅ Sent a **{target_contests[0].platform}** test reminder to <#{config.channel_id}> pinging <@&{role_id}>!",
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
