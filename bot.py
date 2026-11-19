import asyncio
import base64
import io
import json
import os
import re
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Button, Modal, Select, TextInput, View

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# --- CONFIGURATION ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GITHUB_REPO = "walx-x/kosmoskeystxt"
GITHUB_BRANCH = "main"
FILE_PATH = "KosmosPaidKeys.json"

ALLOWED_USERS = ["8y.o", "kosmontop"]
VISITOR_ROLE_NAME = "Visitor"
KOSMO_MEMBER_ROLE_NAME = "KoSMO Member"
MOD_ROLE_NAME = "Mod"

LOG_CHANNEL_NAME = "⮚︱logs❗"
STATUS_CHANNEL_NAME = "⮚︱status"
KEYLIST_CHANNEL_NAME = "⮚︱key-database"  # Channel where the live key database will stay
TICKET_CATEGORY_NAME = "───🎫Tickets ───"

EXPIRY_CHECK_MINUTES = 1  # how often expired keys are removed

GITHUB_ASSETS = "https://raw.githubusercontent.com/walx-x/kosmos-bot/refs/heads/main"
LOGO_IMAGE_URL = f"{GITHUB_ASSETS}/kosmologo.png"
BANNER_IMAGE_URL = f"{GITHUB_ASSETS}/kosmobanner.png"

STATUS_CONFIG = {
    "active": {
        "title": "🟢 STATUS: ACTIVE",
        "color": discord.Color.green(),
        "image": f"{GITHUB_ASSETS}/active.png",
    },
    "updating": {
        "title": "🟡 STATUS: UPDATING",
        "color": discord.Color.gold(),
        "image": f"{GITHUB_ASSETS}/updating.png",
    },
    "down": {
        "title": "🔴 STATUS: DOWN",
        "color": discord.Color.red(),
        "image": f"{GITHUB_ASSETS}/down.png",
    },
    "patched": {
        "title": "🔵 STATUS: PATCHED",
        "color": discord.Color.blue(),
        "image": f"{GITHUB_ASSETS}/patched.png",
    },
}


# --- DURATION PARSER & HELPERS ---
class DurationError(Exception):
    pass


def parse_duration(text: str):
    """Parse duration strings including minutes (m), hours (h), days (d), weeks (w), months (mo), years (y), or lifetime (lt)."""
    text = text.strip().lower()
    if text in ("lt", "lifetime", "inf", "forever"):
        return "Lifetime", None

    match = re.match(r"^(\d+)\s*([a-z]+)$", text)
    if not match:
        raise DurationError(
            "Invalid duration format. Use: 5m (minutes), 1h (hours), 7d (days), 1mo (months), 1y (years), lt (lifetime)."
        )

    val = int(match.group(1))
    unit = match.group(2)
    now = datetime.now(timezone.utc)

    if unit in ("m", "min", "mins", "minute", "minutes"):
        label = f"{val} Minute{'s' if val > 1 else ''}"
        expires_at = now + timedelta(minutes=val)
    elif unit in ("h", "hr", "hrs", "hour", "hours"):
        label = f"{val} Hour{'s' if val > 1 else ''}"
        expires_at = now + timedelta(hours=val)
    elif unit in ("d", "day", "days"):
        label = f"{val} Day{'s' if val > 1 else ''}"
        expires_at = now + timedelta(days=val)
    elif unit in ("w", "wk", "wks", "week", "weeks"):
        label = f"{val} Week{'s' if val > 1 else ''}"
        expires_at = now + timedelta(weeks=val)
    elif unit in ("mo", "mon", "month", "months"):
        label = f"{val} Month{'s' if val > 1 else ''}"
        expires_at = now + timedelta(days=val * 30)
    elif unit in ("y", "yr", "yrs", "year", "years"):
        label = f"{val} Year{'s' if val > 1 else ''}"
        expires_at = now + timedelta(days=val * 365)
    else:
        raise DurationError(
            f"Unknown unit '{unit}'. Available units: m (minutes), h (hours), d (days), w (weeks), mo (months), y (years)."
        )

    return label, expires_at


def describe_duration(label: str) -> str:
    return label if label else "Custom"


def is_expired(entry: dict, now_ts: float) -> bool:
    ts = entry.get("expires_ts")
    if ts is None:
        return False
    return now_ts >= ts


def duration_suggestions(current: str):
    current = current.strip().lower()
    suggestions = [
        ("⚡ 5 Minutes (Testing)", "5m"),
        ("⚡ 15 Minutes (Testing)", "15m"),
        ("⏳ 1 Hour", "1h"),
        ("⏳ 12 Hours", "12h"),
        ("📅 1 Day", "1d"),
        ("📅 7 Days", "7d"),
        ("📅 1 Month", "1mo"),
        ("📅 1 Year", "1y"),
        ("♾️ Lifetime", "lt"),
    ]
    if not current:
        return suggestions
    return [s for s in suggestions if current in s[0].lower() or current in s[1].lower()]


# --- BOT CLASS & PERSISTENT HOOK ---
class KosmoBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.http_session: Optional[aiohttp.ClientSession] = None
        self.status_initialized = False

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

        self.add_view(TicketPanelView())
        self.add_view(SupportPanelView())
        self.add_view(TicketControlView())
        self.add_view(KeyManagementView())

        try:
            synced = await self.tree.sync()
            print(f"[SLASH] Synced {len(synced)} slash commands")
        except Exception as e:
            print(f"[SLASH ERROR] Could not sync slash commands: {e}")

        expire_keys_loop.change_interval(minutes=EXPIRY_CHECK_MINUTES)
        expire_keys_loop.start()

    async def close(self):
        expire_keys_loop.cancel()
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        await super().close()

    async def on_ready(self):
        print(f"[READY] Logged in as {self.user} ({self.user.id})")
        if self.status_initialized:
            return
        self.status_initialized = True

        for guild in self.guilds:
            channel = discord.utils.get(guild.text_channels, name=STATUS_CHANNEL_NAME)
            if not channel:
                continue
            try:
                await remove_previous_status_messages(channel)
                embed = discord.Embed(
                    title="⚪ STATUS: UNKNOWN",
                    description=(
                        "No status has been set yet. Staff can use /status and choose **Active**,"
                        " **Updating**, **Down**, or **Patched** to publish the current status."
                    ),
                    color=discord.Color.light_grey(),
                )
                embed.set_thumbnail(url=LOGO_IMAGE_URL)
                embed.set_footer(text="Use /status to update this message")
                await channel.send(embed=embed)
            except Exception as e:
                print(f"[STATUS INIT ERROR] {e}")


bot = KosmoBot()


async def remove_previous_status_messages(channel: discord.TextChannel):
    """Delete the bot's previous status post(s)."""
    async for message in channel.history(limit=100):
        if message.author == bot.user and message.embeds:
            title = message.embeds[0].title or ""
            if title.startswith(
                ("🟢 STATUS:", "🟡 STATUS:", "🔴 STATUS:", "🔵 STATUS:", "⚪ STATUS:")
            ):
                try:
                    await message.delete()
                except discord.HTTPException as e:
                    print(f"[STATUS DELETE ERROR] {e}")


# --- PERMISSIONS ---
def is_staff_user(user) -> bool:
    return user.name in ALLOWED_USERS


def staff_only():
    async def predicate(ctx: commands.Context) -> bool:
        if not is_staff_user(ctx.author):
            raise commands.CheckFailure("You do not have permission to use this command!")
        return True

    return commands.check(predicate)


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    if isinstance(error, commands.CommandNotFound):
        return
    error = getattr(error, "original", error)

    if isinstance(error, commands.CheckFailure):
        message = f"❌ {error}" if str(error) else "❌ You do not have permission to use this command!"
    elif isinstance(error, commands.MissingRequiredArgument):
        message = f"❌ Missing argument: {error.param.name}. Usage: /{ctx.command.qualified_name} (or !{ctx.command.qualified_name} {ctx.command.signature})"
    elif isinstance(error, (commands.BadArgument, commands.BadUnionArgument)):
        message = f"❌ Invalid argument. Usage: !{ctx.command.qualified_name} {ctx.command.signature}"
    elif isinstance(error, GitHubError):
        message = f"❌ GitHub error: {error}"
    else:
        print(f"[COMMAND ERROR] {ctx.command}: {error!r}")
        message = "❌ Something went wrong. Please check the logs."

    try:
        await ctx.send(message, ephemeral=True, delete_after=None if ctx.interaction else 8)
    except Exception:
        pass


# --- AUTOMATIC ROLE MANAGEMENT ---
@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.roles != after.roles:
        has_visitor = any(role.name == VISITOR_ROLE_NAME for role in after.roles)
        has_kosmo_member = any(role.name == KOSMO_MEMBER_ROLE_NAME for role in after.roles)

        if has_visitor and has_kosmo_member:
            visitor_role = discord.utils.get(after.guild.roles, name=VISITOR_ROLE_NAME)
            if visitor_role:
                try:
                    await after.remove_roles(visitor_role)
                except Exception as e:
                    print(f"[ROLE ERROR] {e}")


# --- ASYNC GITHUB HELPERS ---
class GitHubError(Exception):
    pass


_keys_lock = asyncio.Lock()
_read_cache = {"data": None, "time": 0.0}

GITHUB_URL = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{FILE_PATH}"


def _github_headers():
    if not GITHUB_TOKEN:
        raise GitHubError("GITHUB_TOKEN is not set")
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


async def _fetch_keys():
    try:
        async with bot.http_session.get(
            GITHUB_URL, headers=_github_headers(), params={"ref": GITHUB_BRANCH}
        ) as res:
            if res.status == 200:
                data = await res.json()
                raw = base64.b64decode(data["content"]).decode("utf-8")
                try:
                    keys = json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError:
                    raise GitHubError("the keys file is not valid JSON, refusing to overwrite it")
                if not isinstance(keys, dict):
                    raise GitHubError("the keys file has an unexpected format")
                return keys, data["sha"]
            if res.status == 404:
                return {}, None
            raise GitHubError(f"GET returned HTTP {res.status}")
    except aiohttp.ClientError as e:
        raise GitHubError(f"network error ({e})")
    except asyncio.TimeoutError:
        raise GitHubError("GitHub timed out")


async def _put_keys(keys, sha):
    payload = {
        "message": "Updated keys database via Discord Bot",
        "content": base64.b64encode(json.dumps(keys, indent=4).encode("utf-8")).decode("utf-8"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    try:
        async with bot.http_session.put(
            GITHUB_URL, json=payload, headers=_github_headers()
        ) as res:
            return res.status
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise GitHubError(f"network error ({e})")


async def read_keys(max_age: float = 0.0):
    now = asyncio.get_running_loop().time()
    if max_age and _read_cache["data"] is not None and now - _read_cache["time"] < max_age:
        return _read_cache["data"]
    keys, _ = await _fetch_keys()
    _read_cache["data"], _read_cache["time"] = keys, now
    return keys


async def modify_keys(mutator):
    async with _keys_lock:
        for _ in range(3):
            keys, sha = await _fetch_keys()
            changed, result = mutator(keys)
            if not changed:
                return result
            status = await _put_keys(keys, sha)
            if status in (200, 201):
                _read_cache["data"] = None
                return result
            if status not in (409, 422):
                raise GitHubError(f"PUT returned HTTP {status}")
        raise GitHubError("the file kept changing, please try again")


# --- LIVE KEYLIST DATABASE & STAFF DM HELPERS ---
def build_keylist_embeds(keys_data: dict) -> list[discord.Embed]:
    """Generate clean database embeds representing all current keys."""
    title = "🗄️ KoSMO Key Database (Live)"
    if not keys_data:
        embed = discord.Embed(
            title=title,
            description="*No keys currently registered in the database.*",
            color=discord.Color.purple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="Total keys: 0 • Auto-updates on every change")
        return [embed]

    lines = []
    for k, v in keys_data.items():
        v = v if isinstance(v, dict) else {}
        status = "🔴 Used" if v.get("used") else "🟢 Available"
        rank = v.get("rank", "Standard")
        owner_tag = f" | 👤 {v['assigned_to']}" if v.get("assigned_to") else ""
        lines.append(f"`{k}` | 👑 **{rank}** | {status} | {expiry_text(v)}{owner_tag}")

    chunks, current, size = [], [], 0
    for line in lines:
        if size + len(line) + 1 > 3800 and current:
            chunks.append(current)
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    chunks.append(current)

    embeds = []
    for i, chunk in enumerate(chunks):
        embed = discord.Embed(
            title=title if i == 0 else f"{title} (Page {i + 1})",
            description="\n".join(chunk),
            color=discord.Color.purple(),
            timestamp=datetime.now(timezone.utc),
        )
        if i == len(chunks) - 1:
            embed.set_footer(text=f"Total keys: {len(keys_data)} • Auto-updates on every change")
        embeds.append(embed)
    return embeds


async def update_live_keylist(target_channel: Optional[discord.TextChannel] = None):
    """Automatically refreshes the live key database message in KEYLIST_CHANNEL_NAME or target_channel."""
    try:
        keys_data = await read_keys()
    except GitHubError as e:
        print(f"[LIVE KEYLIST ERROR] Could not fetch keys: {e}")
        return

    embeds = build_keylist_embeds(keys_data)
    channels_to_update = []

    if target_channel:
        channels_to_update.append(target_channel)
    else:
        for guild in bot.guilds:
            ch = discord.utils.get(guild.text_channels, name=KEYLIST_CHANNEL_NAME)
            if ch:
                channels_to_update.append(ch)

    for channel in channels_to_update:
        try:
            existing_msgs = []
            async for msg in channel.history(limit=25):
                if msg.author == bot.user and msg.embeds:
                    if (msg.embeds[0].title or "").startswith("🗄️ KoSMO Key Database"):
                        existing_msgs.append(msg)

            existing_msgs.reverse()  # oldest first

            if len(existing_msgs) == len(embeds):
                for idx, msg in enumerate(existing_msgs):
                    is_last = idx == len(embeds) - 1
                    await msg.edit(
                        embed=embeds[idx], view=KeyManagementView() if is_last else None
                    )
            else:
                for old_msg in existing_msgs:
                    try:
                        await old_msg.delete()
                    except discord.HTTPException:
                        pass
                for idx, emb in enumerate(embeds):
                    is_last = idx == len(embeds) - 1
                    await channel.send(
                        embed=emb, view=KeyManagementView() if is_last else None
                    )
        except Exception as e:
            print(f"[LIVE KEYLIST UPDATE ERROR in #{channel.name}] {e}")


async def notify_staff_key_created(
    key: str, rank: str, duration_line: str, created_by: str, assigned_to: str
):
    """Sends a direct message to all allowed staff users whenever a new key is created."""
    embed = discord.Embed(
        title="🔔 New Key Generated",
        description="A new access key has been added to the database.",
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="🔑 Key", value=f"`{key}`", inline=False)
    embed.add_field(name="👑 Rank", value=f"**{rank}**", inline=True)
    embed.add_field(name="⏳ Duration", value=duration_line, inline=True)
    embed.add_field(name="👤 Created For", value=assigned_to, inline=True)
    embed.add_field(name="🛠️ Generated By", value=created_by, inline=True)
    embed.set_footer(text="KoSMO Hub • Staff Audit Notification")

    notified_ids = set()
    for guild in bot.guilds:
        for member in guild.members:
            if member.name in ALLOWED_USERS and member.id not in notified_ids:
                notified_ids.add(member.id)
                try:
                    await member.send(embed=embed)
                except Exception as e:
                    print(f"[STAFF DM ERROR] Could not DM {member.name}: {e}")


# --- KEY HELPERS ---
def generate_key_string(existing) -> str:
    chars = string.ascii_uppercase + string.digits
    while True:
        key = f"KoSMO-{''.join(secrets.choice(chars) for _ in range(4))}-{''.join(secrets.choice(chars) for _ in range(4))}"
        if key not in existing:
            return key


async def create_key(
    duration_text: str, rank: str = "Standard", assigned_to: Optional[str] = None
):
    label, expires_at = parse_duration(duration_text)

    def mutator(keys):
        key = generate_key_string(keys)
        entry = {
            "hwid": None,
            "used": False,
            "rank": rank,
            "duration": label,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "expires_at": expires_at.isoformat(timespec="seconds") if expires_at else None,
            "expires_ts": int(expires_at.timestamp()) if expires_at else None,
        }
        if assigned_to:
            entry["assigned_to"] = assigned_to
        keys[key] = entry
        return True, key

    key = await modify_keys(mutator)
    return key, label, expires_at


def expiry_text(entry: dict, style: str = "R") -> str:
    ts = entry.get("expires_ts") if isinstance(entry, dict) else None
    return "♾️ Lifetime" if ts is None else f"⏳ <t:{int(ts)}:{style}>"


async def purge_expired():
    def mutator(keys):
        now_ts = datetime.now(timezone.utc).timestamp()
        expired = [k for k, v in keys.items() if is_expired(v, now_ts)]
        for k in expired:
            del keys[k]
        return bool(expired), expired

    return await modify_keys(mutator)


async def send_to_log_channels(embed: discord.Embed):
    for guild in bot.guilds:
        channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
        if channel:
            try:
                await channel.send(embed=embed)
            except Exception as e:
                print(f"[LOG ERROR] {e}")


@tasks.loop(minutes=1)
async def expire_keys_loop():
    try:
        removed = await purge_expired()
    except Exception as e:
        print(f"[EXPIRY ERROR] {e!r}")
        return
    if removed:
        print(f"[EXPIRY] Removed {len(removed)} expired key(s): {', '.join(removed)}")
        embed = discord.Embed(
            title="⌛ Expired Keys Removed",
            description="\n".join(f"{k}" for k in removed[:50])
            + (f"\n…and {len(removed) - 50} more" if len(removed) > 50 else ""),
            color=discord.Color.orange(),
        )
        await send_to_log_channels(embed)
        await update_live_keylist()


@expire_keys_loop.before_loop
async def _before_expire_loop():
    await bot.wait_until_ready()


# --- TICKET SYSTEM VIEWS & SELECTS ---
def has_mod_permission(interaction: discord.Interaction) -> bool:
    mod_role = discord.utils.get(interaction.guild.roles, name=MOD_ROLE_NAME)
    return (mod_role is not None and mod_role in interaction.user.roles) or is_staff_user(
        interaction.user
    )


async def create_ticket_channel(interaction: discord.Interaction, prefix: str, kind: str):
    guild = interaction.guild
    user = interaction.user
    clean_username = user.name.lower().replace(".", "")
    channel_name = f"{prefix}-{clean_username}"

    existing_channel = discord.utils.get(guild.channels, name=channel_name)
    if existing_channel:
        await interaction.followup.send(
            f"❌ You already have an open {kind} ticket: {existing_channel.mention}", ephemeral=True
        )
        return None

    ticket_category = discord.utils.get(guild.categories, name=TICKET_CATEGORY_NAME)
    if not ticket_category:
        try:
            ticket_category = await guild.create_category(TICKET_CATEGORY_NAME)
        except Exception as e:
            print(f"[CATEGORY ERROR] Could not create category: {e}")

    mod_role = discord.utils.get(guild.roles, name=MOD_ROLE_NAME)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user: discord.PermissionOverwrite(
            read_messages=True, send_messages=True, attach_files=True
        ),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }
    if mod_role:
        overwrites[mod_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    channel = await guild.create_text_channel(
        name=channel_name,
        category=ticket_category,
        overwrites=overwrites,
        reason=f"{kind.capitalize()} ticket opened by {user.name}",
    )
    return channel, mod_role


class TicketControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Claim Ticket",
        style=discord.ButtonStyle.green,
        custom_id="btn_claim_ticket",
        emoji="🔒",
    )
    async def claim_button(self, interaction: discord.Interaction, button: Button):
        if not has_mod_permission(interaction):
            await interaction.response.send_message(
                "❌ Only staff members with the **Mod** role can claim tickets!", ephemeral=True
            )
            return
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}"
        button.style = discord.ButtonStyle.secondary
        await interaction.response.edit_message(view=self)
        embed = discord.Embed(
            title="🎫 Ticket Claimed",
            description=f"This ticket is now being handled by {interaction.user.mention}.",
            color=discord.Color.blue(),
        )
        await interaction.channel.send(embed=embed)

    @discord.ui.button(
        label="Close Ticket", style=discord.ButtonStyle.red, custom_id="btn_close_ticket", emoji="⛔"
    )
    async def close_button(self, interaction: discord.Interaction, button: Button):
        if not has_mod_permission(interaction):
            await interaction.response.send_message(
                "❌ You do not have permission to close this ticket!", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "🔒 Generating transcript and closing ticket in 5 seconds..."
        )

        messages = []
        async for message in interaction.channel.history(limit=1000, oldest_first=True):
            timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
            messages.append(
                f"[{timestamp}] {message.author} ({message.author.id}): {message.clean_content}"
            )

        transcript_text = (
            f"=== TRANSCRIPT FOR {interaction.channel.name.upper()} ===\n\n"
            + "\n".join(messages)
        )
        log_channel = discord.utils.get(
            interaction.guild.text_channels, name=LOG_CHANNEL_NAME
        )
        if log_channel:
            embed_log = discord.Embed(
                title="📝 Ticket Closed & Transcribed",
                description=f"**Ticket:** {interaction.channel.name}\n**Closed by:** {interaction.user.mention}",
                color=discord.Color.red(),
            )
            try:
                transcript_file = discord.File(
                    io.BytesIO(transcript_text.encode("utf-8")),
                    filename=f"{interaction.channel.name}_transcript.txt",
                )
                await log_channel.send(embed=embed_log, file=transcript_file)
            except Exception as e:
                print(f"[LOG ERROR] Failed to send transcript: {e}")

        await asyncio.sleep(5)
        await interaction.channel.delete()


class PlanSelectEphemeral(Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="KoSMO STANDARD — 1 Week (€1.49)",
                value="STANDARD 1 Week (€1.49)",
                emoji="⚙️",
            ),
            discord.SelectOption(
                label="KoSMO STANDARD — 1 Month (€2.99)",
                value="STANDARD 1 Month (€2.99)",
                emoji="⚙️",
            ),
            discord.SelectOption(
                label="KoSMO STANDARD — Lifetime (€4.99)",
                value="STANDARD Lifetime (€4.99)",
                emoji="⚙️",
            ),
            discord.SelectOption(
                label="KoSMO PRO — 1 Week (€6.99)", value="PRO 1 Week (€6.99)", emoji="🚀"
            ),
            discord.SelectOption(
                label="KoSMO PRO — 1 Month (€7.99)", value="PRO 1 Month (€7.99)", emoji="🚀"
            ),
            discord.SelectOption(
                label="KoSMO PRO — Lifetime (€11.99)", value="PRO Lifetime (€11.99)", emoji="🚀"
            ),
            discord.SelectOption(
                label="KoSMO VIP — 1 Month (€14.99)", value="VIP 1 Month (€14.99)", emoji="👑"
            ),
            discord.SelectOption(
                label="KoSMO VIP — Lifetime (€24.99)", value="VIP Lifetime (€24.99)", emoji="👑"
            ),
        ]
        super().__init__(placeholder="Select the plan you want to buy...", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        result = await create_ticket_channel(interaction, "buy", "purchase")
        if result is None:
            return
        ticket_channel, mod_role = result
        user = interaction.user

        await interaction.followup.send(
            f"✅ Purchase Ticket created! Check out {ticket_channel.mention}", ephemeral=True
        )

        buy_embed = discord.Embed(
            title="🚀 KoSMO — Purchase Hub",
            description=(
                "Thank you for choosing us!\n\n"
                "**Accepted Payment Methods:**\n"
                "- Crypto (LTC / BTC / USDT) <:emoji_3:1550284088269869127>\n"
                "- Revolut <:emoji_2:1550284071618478220>\n"
                "- PayPal (Friends & Family) <:emoji_1:1550284056145559593>\n"
                "- PayPal Giftcards <:emoji_5:1550284178090631310>\n"
                "- Robux / Roblox Giftcards <:emoji_4:1550284144473407508>\n\n"
                "**How to Order:**\n"
                "A staff member will assist you shortly with payment details!"
            ),
            color=discord.Color.purple(),
        )
        buy_embed.set_footer(text="KoSMO Hub • Fast, Automated & Secure Delivery")

        order_details_embed = discord.Embed(
            title="🛒 User Order Details", color=discord.Color.green()
        )
        order_details_embed.add_field(
            name="Selected Plan", value=f"{self.values[0]}", inline=False
        )

        await ticket_channel.send(
            content=f"Welcome {user.mention}! {mod_role.mention if mod_role else ''}",
            embeds=[buy_embed, order_details_embed],
            view=TicketControlView(),
        )


class PlanStepView(View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(PlanSelectEphemeral())


class TicketPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Buy / Purchase",
        style=discord.ButtonStyle.green,
        custom_id="btn_create_buy_ticket",
        emoji="🛒",
    )
    async def buy_ticket_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(
            content="🛒 Please select your preferred plan below:",
            view=PlanStepView(),
            ephemeral=True,
        )


class SupportPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Get Support",
        style=discord.ButtonStyle.blurple,
        custom_id="btn_create_support_ticket",
        emoji="🛠️",
    )
    async def support_ticket_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        result = await create_ticket_channel(interaction, "support", "support")
        if result is None:
            return
        ticket_channel, mod_role = result
        user = interaction.user

        await interaction.followup.send(
            f"✅ Support Ticket created! Check out {ticket_channel.mention}", ephemeral=True
        )

        support_embed = discord.Embed(
            title="🛠️ KoSMO — Support Center",
            description=(
                f"Welcome {user.mention}!\n\n"
                "Please describe your issue in detail so our support team can assist you as fast"
                " as possible.\n\n"
                "**Information to include:**\n"
                "- Detailed description of your problem\n"
                "- Screenshots or error logs (if applicable)\n"
                "- Your order ID or key (if related to a purchase)\n\n"
                "A staff member will be with you shortly!"
            ),
            color=discord.Color.blue(),
        )
        support_embed.set_footer(text="KoSMO Hub • Official Technical Support")

        await ticket_channel.send(
            content=f"Welcome {user.mention}! {mod_role.mention if mod_role else ''}",
            embed=support_embed,
            view=TicketControlView(),
        )


# --- KEY MANAGEMENT INTERACTIVE VIEWS ---
class DeleteKeySelect(Select):
    def __init__(self, keys_data):
        options = [
            discord.SelectOption(
                label=k,
                value=k,
                description=(
                    "Lifetime"
                    if (v or {}).get("expires_ts") is None
                    else f"{describe_duration((v or {}).get('duration', ''))} key"
                )[:100],
            )
            for k, v in list(keys_data.items())[:25]
        ]
        super().__init__(placeholder="Select a key to delete...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if not is_staff_user(interaction.user):
            await interaction.response.send_message(
                "❌ You do not have permission to perform this action!", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        key_to_delete = self.values[0]

        def mutator(keys):
            if key_to_delete in keys:
                del keys[key_to_delete]
                return True, True
            return False, False

        try:
            deleted = await modify_keys(mutator)
        except GitHubError as e:
            await interaction.followup.send(
                f"❌ Error deleting key from GitHub: {e}", ephemeral=True
            )
            return

        if deleted:
            await interaction.followup.send(
                f"🗑️ Key `{key_to_delete}` successfully deleted!", ephemeral=True
            )
            await update_live_keylist()
        else:
            await interaction.followup.send(
                f"❌ Key `{key_to_delete}` no longer exists.", ephemeral=True
            )


class CreateKeyModal(Modal, title="Create Key"):
    duration = TextInput(
        label="Duration (lt, 5m, 1h, 7d, 1mo, 1y)",
        placeholder="lt, 5m, 1h, 7d, 1mo ...",
        default="lt",
        min_length=1,
        max_length=12,
    )
    rank = TextInput(
        label="Rank (Standard, Pro, VIP)",
        placeholder="Standard / Pro / VIP",
        default="Standard",
        min_length=2,
        max_length=20,
    )
    recipient = TextInput(
        label="For who? (Optional username or note)",
        placeholder="e.g. buyer123 or self",
        required=False,
        max_length=50,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_staff_user(interaction.user):
            await interaction.response.send_message(
                "❌ You do not have permission!", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        assigned = self.recipient.value.strip() if self.recipient.value else interaction.user.name

        try:
            key, label, expires_at = await create_key(
                self.duration.value, self.rank.value, assigned_to=assigned
            )
        except DurationError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return
        except GitHubError as e:
            await interaction.followup.send(
                f"❌ Error saving key to GitHub: {e}", ephemeral=True
            )
            return

        expiry_line = (
            "♾️ Lifetime"
            if expires_at is None
            else f"⏳ {describe_duration(label)} — expires <t:{int(expires_at.timestamp())}:F> (<t:{int(expires_at.timestamp())}:R>)"
        )
        duration_short = (
            "Lifetime"
            if expires_at is None
            else f"{describe_duration(label)} (<t:{int(expires_at.timestamp())}:R>)"
        )

        await interaction.followup.send(
            f"🔑 **New Key Created:** `{key}`\n👑 **Rank:** {self.rank.value}\n👤 **For:** {assigned}\n{expiry_line}",
            ephemeral=True,
        )

        await update_live_keylist()
        await notify_staff_key_created(
            key=key,
            rank=self.rank.value,
            duration_line=duration_short,
            created_by=f"{interaction.user.mention} ({interaction.user.name})",
            assigned_to=assigned,
        )


class KeyManagementView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create Key", style=discord.ButtonStyle.green, custom_id="btn_create_key"
    )
    async def create_key_button(self, interaction: discord.Interaction, button: Button):
        if not is_staff_user(interaction.user):
            await interaction.response.send_message(
                "❌ You do not have permission!", ephemeral=True
            )
            return
        await interaction.response.send_modal(CreateKeyModal())

    @discord.ui.button(
        label="Delete Key", style=discord.ButtonStyle.red, custom_id="btn_delete_key"
    )
    async def delete_key_button(self, interaction: discord.Interaction, button: Button):
        if not is_staff_user(interaction.user):
            await interaction.response.send_message(
                "❌ You do not have permission!", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        try:
            keys_data = await read_keys()
        except GitHubError as e:
            await interaction.followup.send(f"❌ GitHub error: {e}", ephemeral=True)
            return

        if not keys_data:
            await interaction.followup.send("❌ No keys available to delete.", ephemeral=True)
            return

        view = View()
        view.add_item(DeleteKeySelect(keys_data))
        note = (
            "\n*Only the first 25 keys are shown here — use /deletekey to search for any key.*"
            if len(keys_data) > 25
            else ""
        )
        await interaction.followup.send(
            f"Select the key you want to delete:{note}", view=view, ephemeral=True
        )


# --- COMMANDS ---
async def finish_panel_command(ctx: commands.Context):
    if ctx.interaction:
        await ctx.send("✅ Panel sent!", ephemeral=True)
    else:
        try:
            await ctx.message.delete()
        except Exception:
            pass


@bot.hybrid_command(
    name="keylist_channel",
    description="Publish or refresh the live key database in the designated database channel",
)
@app_commands.describe(
    channel="Optional specific channel (defaults to configured KEYLIST_CHANNEL_NAME)"
)
@staff_only()
async def keylist_channel(ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
    await ctx.defer(ephemeral=True)
    target = channel or discord.utils.get(ctx.guild.text_channels, name=KEYLIST_CHANNEL_NAME)

    if not target:
        await ctx.send(
            f"❌ Could not find channel `{KEYLIST_CHANNEL_NAME}`. Please create it or specify a channel argument.",
            ephemeral=True,
        )
        return

    await update_live_keylist(target_channel=target)
    await ctx.send(
        f"✅ Live Key Database published and synced in {target.mention}!", ephemeral=True
    )


@bot.hybrid_command(
    name="status", description="Update the software status in the status channel"
)
@app_commands.describe(state="The new software status")
@app_commands.choices(
    state=[
        app_commands.Choice(name="🟢 Active", value="active"),
        app_commands.Choice(name="🟡 Updating", value="updating"),
        app_commands.Choice(name="🔴 Down", value="down"),
        app_commands.Choice(name="🔵 Patched", value="patched"),
    ]
)
@staff_only()
async def status_command(ctx: commands.Context, state: str):
    await ctx.defer(ephemeral=True)
    if state.lower() not in STATUS_CONFIG:
        await ctx.send(
            "❌ Correct usage: /status or !status active|updating|down|patched",
            ephemeral=True,
            delete_after=None if ctx.interaction else 5,
        )
        return

    state_key = state.lower()
    config = STATUS_CONFIG[state_key]

    status_channel = discord.utils.get(ctx.guild.text_channels, name=STATUS_CHANNEL_NAME)
    if not status_channel:
        await ctx.send(
            f"❌ Unable to find channel {STATUS_CHANNEL_NAME}!",
            ephemeral=True,
            delete_after=None if ctx.interaction else 5,
        )
        return

    await remove_previous_status_messages(status_channel)

    embed = discord.Embed(
        title=config["title"],
        description=f"Software status updated to **{state_key.upper()}**.",
        color=config["color"],
    )
    embed.set_thumbnail(url=LOGO_IMAGE_URL)
    embed.set_image(url=config["image"])
    embed.set_footer(
        text=f"Updated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url
    )

    await status_channel.send(embed=embed)

    if ctx.interaction:
        await ctx.send(
            f"✅ Status set to **{state_key.upper()}** in {status_channel.mention}.", ephemeral=True
        )
    else:
        try:
            await ctx.message.delete()
        except Exception:
            pass


@bot.hybrid_command(
    name="ticketpanel", description="Send the purchase ticket panel in this channel"
)
@staff_only()
async def ticketpanel(ctx: commands.Context):
    await ctx.defer(ephemeral=True)
    embed = discord.Embed(
        title="📩 KoSMO - Purchase Panel",
        description=(
            "Welcome to the **KoSMO Hub** purchase portal!\n\n"
            "Click the button below to select your plan and open a private ticket.\n\n"
            "⏰ **Support Hours:** 24/7 Support\n"
            "⚠️ Please do not create multiple tickets at once."
        ),
        color=discord.Color.purple(),
    )
    embed.set_thumbnail(url=LOGO_IMAGE_URL)
    embed.set_image(url=BANNER_IMAGE_URL)
    embed.set_footer(text="KoSMO Hub • Official Ticket System")

    await ctx.channel.send(embed=embed, view=TicketPanelView())
    await finish_panel_command(ctx)


@bot.hybrid_command(
    name="supportpanel", description="Send the support ticket panel in this channel"
)
@staff_only()
async def supportpanel(ctx: commands.Context):
    await ctx.defer(ephemeral=True)
    embed = discord.Embed(
        title="🛠️ KoSMO - Support Portal",
        description=(
            "Welcome to the **KoSMO Hub** Support Helpdesk!\n\n"
            "Need help or experiencing an issue? Click the button below to open a private support"
            " ticket.\n\n"
            "⏰ **Support Hours:** 24/7 Support\n"
            "⚠️ Please explain your problem clearly inside the ticket."
        ),
        color=discord.Color.blue(),
    )
    embed.set_thumbnail(url=LOGO_IMAGE_URL)
    embed.set_image(url=BANNER_IMAGE_URL)
    embed.set_footer(text="KoSMO Hub • Official Support Panel")

    await ctx.channel.send(embed=embed, view=SupportPanelView())
    await finish_panel_command(ctx)


@bot.hybrid_command(name="genkey", description="Generate an access key and send it by DM")
@app_commands.describe(
    duration="lt = lifetime, or number + m (minutes) / h (hours) / d (days) / mo (months) / y (years). Ex: 5m, 1h, 7d",
    rank="Rank associated with this key (Standard, Pro, VIP)",
    member="Who receives the key (default: you)",
)
@app_commands.choices(
    rank=[
        app_commands.Choice(name="Standard", value="Standard"),
        app_commands.Choice(name="Pro", value="Pro"),
        app_commands.Choice(name="VIP", value="VIP"),
    ]
)
@staff_only()
async def genkey(
    ctx: commands.Context,
    duration: str,
    rank: str = "Standard",
    member: Optional[discord.Member] = None,
):
    await ctx.defer(ephemeral=True)
    target_user = member if member else ctx.author

    try:
        parse_duration(duration)
    except DurationError as e:
        await ctx.send(f"❌ {e}", ephemeral=True)
        return

    msg = await ctx.send("⏳ *Saving key to GitHub...*", ephemeral=True)

    try:
        new_key, label, expires_at = await create_key(
            duration, rank, assigned_to=target_user.name
        )
    except GitHubError as e:
        await msg.edit(content=f"❌ Error saving key to GitHub: {e}")
        return

    embed = discord.Embed(
        title="🚀 Welcome to KoSMO Hub",
        description="Your access key has been successfully generated!",
        color=discord.Color.purple(),
    )
    embed.add_field(name="🔑 Your Key", value=f"`{new_key}`", inline=False)
    embed.add_field(name="👑 Key Rank", value=f"**{rank}**", inline=True)

    if expires_at is None:
        embed.add_field(name="⏳ Duration", value="♾️ **Lifetime**", inline=True)
    else:
        ts = int(expires_at.timestamp())
        embed.add_field(
            name="⏳ Duration",
            value=f"**{describe_duration(label)}**\nExpires <t:{ts}:F> (<t:{ts}:R>)",
            inline=True,
        )

    embed.add_field(
        name="⚠️ Single-Device Warning",
        value="This key binds strictly to **1 device** (HWID Locked) upon first use.",
        inline=False,
    )
    embed.add_field(name="✨ Status", value="Enjoy **KoSMO** and play smart!", inline=False)
    embed.set_thumbnail(url=LOGO_IMAGE_URL)
    embed.set_image(url=BANNER_IMAGE_URL)
    embed.set_footer(text="KoSMO Hub System • Single-Device Authentication")

    duration_line = (
        "Lifetime"
        if expires_at is None
        else f"{describe_duration(label)}, expires <t:{int(expires_at.timestamp())}:R>"
    )

    # Update live key database & notify staff via DM
    await update_live_keylist()
    await notify_staff_key_created(
        key=new_key,
        rank=rank,
        duration_line=duration_line,
        created_by=f"{ctx.author.mention} ({ctx.author.name})",
        assigned_to=f"{target_user.mention} ({target_user.name})",
    )

    try:
        await target_user.send(embed=embed)
        if target_user != ctx.author:
            await msg.edit(
                content=f"✅ Key generated ({rank} | {duration_line}) and sent via DM to {target_user.mention}!"
            )
        else:
            await msg.edit(
                content=f"✅ {ctx.author.mention}, your key is ready ({rank} | {duration_line})! Check your DMs."
            )
    except Exception:
        await msg.edit(
            content=f"✅ Key generated: `{new_key}` ({rank} | {duration_line}). Failed to send DM to {target_user.mention}, check DM settings."
        )


@genkey.autocomplete("duration")
async def genkey_duration_autocomplete(interaction: discord.Interaction, current: str):
    if not is_staff_user(interaction.user):
        return []
    return [
        app_commands.Choice(name=name, value=value)
        for name, value in duration_suggestions(current)[:25]
    ]


@bot.hybrid_command(
    name="setrank", description="Change the rank of an existing access key"
)
@app_commands.describe(key="The target key", rank="The new rank for the key")
@app_commands.choices(
    rank=[
        app_commands.Choice(name="Standard", value="Standard"),
        app_commands.Choice(name="Pro", value="Pro"),
        app_commands.Choice(name="VIP", value="VIP"),
    ]
)
@staff_only()
async def setrank(ctx: commands.Context, key: str, rank: str):
    await ctx.defer(ephemeral=True)
    key = key.strip()

    def mutator(keys):
        if key in keys:
            keys[key]["rank"] = rank
            return True, True
        return False, False

    try:
        updated = await modify_keys(mutator)
    except GitHubError as e:
        await ctx.send(f"❌ GitHub error: {e}", ephemeral=True)
        return

    if updated:
        await ctx.send(
            f"👑 Rank for key `{key}` successfully updated to **{rank}**!", ephemeral=True
        )
        await update_live_keylist()
    else:
        await ctx.send(f"❌ Key `{key}` not found.", ephemeral=True)


@setrank.autocomplete("key")
async def setrank_autocomplete(interaction: discord.Interaction, current: str):
    if not is_staff_user(interaction.user):
        return []
    try:
        keys_data = await read_keys(max_age=10)
    except GitHubError:
        return []

    current = current.lower()
    choices = []
    for k, v in keys_data.items():
        if current in k.lower():
            v = v if isinstance(v, dict) else {}
            r = v.get("rank", "Standard")
            choices.append(app_commands.Choice(name=f"{k} [Rank: {r}]"[:100], value=k))
        if len(choices) >= 25:
            break
    return choices


@bot.hybrid_command(
    name="reduceduration", description="Decrease the remaining duration of an active key"
)
@app_commands.describe(
    key="The key to modify", reduce_by="Amount of time to subtract (e.g. 5m, 1h, 1d)"
)
@staff_only()
async def reduceduration(ctx: commands.Context, key: str, reduce_by: str):
    await ctx.defer(ephemeral=True)
    key = key.strip()

    try:
        _, target = parse_duration(reduce_by)
        if target is None:
            await ctx.send("❌ Cannot use 'lifetime' as a reduction amount!", ephemeral=True)
            return
        now_utc = datetime.now(timezone.utc)
        delta = target - now_utc
    except DurationError as e:
        await ctx.send(f"❌ Invalid reduction time: {e}", ephemeral=True)
        return

    def mutator(keys):
        if key not in keys:
            return False, "not_found"

        entry = keys[key]
        if entry.get("expires_ts") is None:
            return False, "lifetime"

        current_ts = entry["expires_ts"]
        new_ts = current_ts - int(delta.total_seconds())

        now_ts = int(datetime.now(timezone.utc).timestamp())
        if new_ts <= now_ts:
            del keys[key]
            return True, "expired"

        new_expires_at = datetime.fromtimestamp(new_ts, tz=timezone.utc)
        entry["expires_ts"] = new_ts
        entry["expires_at"] = new_expires_at.isoformat(timespec="seconds")
        return True, new_expires_at

    try:
        result = await modify_keys(mutator)
    except GitHubError as e:
        await ctx.send(f"❌ GitHub error: {e}", ephemeral=True)
        return

    if result == "not_found":
        await ctx.send(f"❌ Key `{key}` not found.", ephemeral=True)
    elif result == "lifetime":
        await ctx.send("❌ Cannot reduce duration on a Lifetime key!", ephemeral=True)
    elif result == "expired":
        await ctx.send(
            f"⚠️ Duration reduced beyond remaining time. Key `{key}` has expired and been removed.",
            ephemeral=True,
        )
        await update_live_keylist()
    else:
        ts = int(result.timestamp())
        await ctx.send(
            f"📉 Reduced duration for key `{key}`! New expiration: <t:{ts}:F> (<t:{ts}:R>)",
            ephemeral=True,
        )
        await update_live_keylist()


@reduceduration.autocomplete("key")
async def reduceduration_autocomplete(interaction: discord.Interaction, current: str):
    if not is_staff_user(interaction.user):
        return []
    try:
        keys_data = await read_keys(max_age=10)
    except GitHubError:
        return []

    current = current.lower()
    choices = []
    for k, v in keys_data.items():
        if current in k.lower():
            v = v if isinstance(v, dict) else {}
            tag = (
                "Lifetime"
                if v.get("expires_ts") is None
                else describe_duration(v.get("duration", ""))
            )
            choices.append(app_commands.Choice(name=f"{k} ({tag})"[:100], value=k))
        if len(choices) >= 25:
            break
    return choices


@bot.hybrid_command(
    name="keylist",
    aliases=["kaylist"],
    description="Show all keys with their rank, status, and expiry",
)
@staff_only()
async def keylist(ctx: commands.Context):
    await ctx.defer(ephemeral=True)
    keys_data = await read_keys()
    embeds = build_keylist_embeds(keys_data)

    for i, embed in enumerate(embeds):
        is_last = i == len(embeds) - 1
        await ctx.send(
            embed=embed,
            view=KeyManagementView() if is_last else discord.utils.MISSING,
            ephemeral=True,
        )


@bot.hybrid_command(name="deletekey", description="Delete a key")
@app_commands.describe(key="The key to delete")
@staff_only()
async def deletekey(ctx: commands.Context, key: str):
    await ctx.defer(ephemeral=True)
    key = key.strip()

    def mutator(keys):
        if key in keys:
            del keys[key]
            return True, True
        return False, False

    if await modify_keys(mutator):
        await ctx.send(f"🗑️ Key `{key}` successfully deleted!", ephemeral=True)
        await update_live_keylist()
    else:
        await ctx.send(f"❌ Key `{key}` not found.", ephemeral=True)


@deletekey.autocomplete("key")
async def deletekey_autocomplete(interaction: discord.Interaction, current: str):
    if not is_staff_user(interaction.user):
        return []
    try:
        keys_data = await read_keys(max_age=10)
    except GitHubError:
        return []

    current = current.lower()
    choices = []
    for k, v in keys_data.items():
        if current in k.lower():
            v = v if isinstance(v, dict) else {}
            tag = (
                "Lifetime"
                if v.get("expires_ts") is None
                else describe_duration(v.get("duration", ""))
            )
            choices.append(
                app_commands.Choice(
                    name=f"{k} ({tag}, {'used' if v.get('used') else 'available'})"[:100], value=k
                )
            )
        if len(choices) >= 25:
            break
    return choices


if not DISCORD_TOKEN:
    raise SystemExit(
        "DISCORD_TOKEN is not set. Add it as an environment variable or in a .env file."
    )

if not GITHUB_TOKEN:
    print("[WARNING] GITHUB_TOKEN is not set: key commands will fail until you add it.")

bot.run(DISCORD_TOKEN)
