import asyncio
import logging
import os
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import aiohttp
import certifi
from bs4 import BeautifulSoup

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

logger = logging.getLogger("contest_bot.contests")

CODEFORCES_API_URL = "https://codeforces.com/api/contest.list?gym=false"
ATCODER_CONTESTS_URL = "https://atcoder.jp/contests/"


@dataclass(frozen=True)
class Contest:
    key: str  # Unique identifier, e.g. "cf:2287" or "ac:abc484"
    platform: str  # "Codeforces" or "AtCoder"
    name: str
    url: str
    start_time_unix: int
    duration_seconds: int
    rated_range: Optional[str] = None

    @property
    def end_time_unix(self) -> int:
        return self.start_time_unix + self.duration_seconds

    @property
    def formatted_duration(self) -> str:
        total_minutes = self.duration_seconds // 60
        days, rem_minutes = divmod(total_minutes, 1440)
        hours, minutes = divmod(rem_minutes, 60)
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0 or not parts:
            parts.append(f"{minutes:02d}m" if hours > 0 else f"{minutes}m")
        return " ".join(parts)


async def fetch_codeforces_contests(session: aiohttp.ClientSession) -> List[Contest]:
    contests: List[Contest] = []
    try:
        async with session.get(CODEFORCES_API_URL, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.warning("Codeforces API returned HTTP %s", resp.status)
                return []
            data = await resp.json()
            if data.get("status") != "OK":
                logger.warning("Codeforces API status not OK: %s", data.get("comment"))
                return []

            now = int(time.time())
            for item in data.get("result", []):
                if item.get("phase") != "BEFORE":
                    continue
                start_time = item.get("startTimeSeconds")
                duration = item.get("durationSeconds", 0)
                contest_id = item.get("id")
                name = item.get("name", "Codeforces Contest")
                if not start_time or start_time <= now:
                    continue

                contests.append(
                    Contest(
                        key=f"cf:{contest_id}",
                        platform="Codeforces",
                        name=name,
                        url=f"https://codeforces.com/contests/{contest_id}",
                        start_time_unix=int(start_time),
                        duration_seconds=int(duration),
                    )
                )
    except Exception as exc:
        logger.error("Failed to fetch Codeforces contests: %s", exc)
    return contests


def _parse_atcoder_html(html: str) -> List[Contest]:
    contests: List[Contest] = []
    soup = BeautifulSoup(html, "html.parser")
    upcoming_div = soup.find("div", id="contest-table-upcoming")
    if not upcoming_div:
        return contests

    tbody = upcoming_div.find("tbody")
    if not tbody:
        return contests

    now = int(time.time())
    for row in tbody.find_all("tr"):
        cols = row.find_all("td")
        if len(cols) < 3:
            continue

        # 1. Start Time
        time_tag = cols[0].find("time")
        if not time_tag or not time_tag.text:
            continue
        raw_time = time_tag.text.strip()
        try:
            dt = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S%z")
            start_unix = int(dt.astimezone(timezone.utc).timestamp())
        except ValueError:
            continue

        if start_unix <= now:
            continue

        # 2. Contest Name & URL
        link_tag = cols[1].find("a")
        if not link_tag or not link_tag.get("href"):
            continue
        href = str(link_tag.get("href")).strip()
        contest_slug = href.rstrip("/").split("/")[-1]
        name = link_tag.text.strip()
        url = f"https://atcoder.jp{href}" if href.startswith("/") else href

        # Only include AtCoder Beginner Contests (ABC)
        if not (
            contest_slug.lower().startswith("abc")
            or "beginner contest" in name.lower()
        ):
            continue

        # 3. Duration (HH:MM)
        duration_text = cols[2].text.strip()
        duration_seconds = 0
        if ":" in duration_text:
            try:
                h_str, m_str = duration_text.split(":", 1)
                duration_seconds = int(h_str) * 3600 + int(m_str) * 60
            except ValueError:
                duration_seconds = 0

        # 4. Rated Range
        rated_range = cols[3].text.strip() if len(cols) >= 4 else None
        if rated_range == "-":
            rated_range = None

        contests.append(
            Contest(
                key=f"ac:{contest_slug}",
                platform="AtCoder",
                name=name,
                url=url,
                start_time_unix=start_unix,
                duration_seconds=duration_seconds,
                rated_range=rated_range,
            )
        )
    return contests


async def fetch_atcoder_contests(session: aiohttp.ClientSession) -> List[Contest]:
    try:
        headers = {
            "User-Agent": "DiscordContestReminderBot/1.0 (+https://github.com/)"
        }
        async with session.get(
            ATCODER_CONTESTS_URL,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning("AtCoder page returned HTTP %s", resp.status)
                return []
            html = await resp.text()
            return _parse_atcoder_html(html)
    except Exception as exc:
        logger.error("Failed to fetch AtCoder contests: %s", exc)
        return []


class ContestFetcher:
    """Fetches and caches upcoming Codeforces and AtCoder contests."""

    def __init__(self, cache_ttl_seconds: int = 600) -> None:
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cached_contests: List[Contest] = []
        self._last_fetch_time: float = 0.0
        self._lock = asyncio.Lock()

    async def get_upcoming_contests(self, force_refresh: bool = False) -> List[Contest]:
        async with self._lock:
            now = time.time()
            if (
                not force_refresh
                and self._cached_contests
                and (now - self._last_fetch_time) < self.cache_ttl_seconds
            ):
                return [c for c in self._cached_contests if c.start_time_unix > now]

            connector = aiohttp.TCPConnector(ssl=SSL_CONTEXT)
            async with aiohttp.ClientSession(connector=connector) as session:
                cf_contests, ac_contests = await asyncio.gather(
                    fetch_codeforces_contests(session),
                    fetch_atcoder_contests(session),
                )

            combined = cf_contests + ac_contests
            combined.sort(key=lambda c: (c.start_time_unix, c.platform, c.key))

            # Only update cache if at least one source succeeded or cache was empty
            if combined or not self._cached_contests:
                self._cached_contests = combined
                self._last_fetch_time = now

            return [c for c in self._cached_contests if c.start_time_unix > now]
