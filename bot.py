# bot.py — full mega bot with leveling, giveaways, moderation, mod-logs, sqlite persistence, slash & prefix commands
import os
import json
import random
import sqlite3
import asyncio
import re
import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import View, Button

# Optional: web.keep_alive() if you have web.py
HAS_WEB = False
try:
    import web
    HAS_WEB = True
except Exception:
    web = None
    HAS_WEB = False

load_dotenv()

# ====== ENV / CONFIG ======
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
GUILD_ID = int(os.getenv("GUILD_ID") or 0)
MOD_LOG_CHANNEL_ID = int(os.getenv("MOD_LOG_CHANNEL_ID") or 0) if os.getenv("MOD_LOG_CHANNEL_ID") else None
DB_FILE = os.getenv("DB_FILE", "bot_data.db")

# Role names (exact)
OWNER_ROLES = ["👑 Owner", "👑 Co Owner", "👑 Server Partner"]
HIGHEST_STAFF = ["🌸 ๖ۣMighty Children"]
MANAGER_ROLES = ["Server Manager", "Head administrator", "Administrator"]
MOD_ROLES = ["Head Moderator", "Senior Moderator", "Moderator", "Junior Moderator"]
GIVEAWAY_ROLES = ["🎉 𝐆𝐢𝐯𝐞𝐚𝐰𝐚𝐲 𝐓𝐞𝐚𝐦", "Hoster", "Giveaway Team"]
BREAK_ROLES = ["Staff On Break"]
HELPER_ROLES = ["Helper"]

# XP config
XP_MIN = 5
XP_MAX = 12
XP_COOLDOWN = 60  # seconds per user

# Duration regex (e.g. 1d2h30m -> we parse repeated)
DURATION_RE = re.compile(r"(\d+)([smhd])", flags=re.I)

# ====== UTILS ======
def parse_duration_to_seconds(text: str) -> int:
    """Parse durations like '1d2h30m' into seconds."""
    if not text:
        return 0
    total = 0
    for m in DURATION_RE.finditer(text):
        v = int(m.group(1))
        u = m.group(2).lower()
        if u == "s":
            total += v
        elif u == "m":
            total += v * 60
        elif u == "h":
            total += v * 3600
        elif u == "d":
            total += v * 86400
    return total

def make_embed(title=None, description=None, color=0x2F3136):
    e = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
    return e

def has_any_role_by_name(member: discord.Member, names):
    if not member:
        return False
    for r in member.roles:
        if r.name in names:
            return True
    return False

def check_permissions(member: discord.Member, kind: str) -> bool:
    """Role-based permission checks per requested policy."""
    if has_any_role_by_name(member, BREAK_ROLES):
        return False
    if kind == "owner":
        return has_any_role_by_name(member, OWNER_ROLES)
    if kind == "admin":
        return has_any_role_by_name(member, OWNER_ROLES + HIGHEST_STAFF + MANAGER_ROLES)
    if kind == "mod":
        return has_any_role_by_name(member, OWNER_ROLES + HIGHEST_STAFF + MANAGER_ROLES + MOD_ROLES)
    if kind == "giveaway":
        return has_any_role_by_name(member, GIVEAWAY_ROLES + OWNER_ROLES + HIGHEST_STAFF + MANAGER_ROLES)
    if kind == "announce":
        return has_any_role_by_name(member, OWNER_ROLES + HIGHEST_STAFF)
    return False

# ====== SQLITE DB ======
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # levels table
    c.execute("""
    CREATE TABLE IF NOT EXISTS levels(
        user_id TEXT NOT NULL,
        guild_id INTEGER NOT NULL,
        xp INTEGER NOT NULL DEFAULT 0,
        level INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(user_id, guild_id)
    )""")
    # scheduled unbans
    c.execute("""
    CREATE TABLE IF NOT EXISTS scheduled_unbans(
        guild_id INTEGER NOT NULL,
        user_id TEXT NOT NULL,
        unban_at INTEGER NOT NULL,
        PRIMARY KEY(guild_id, user_id)
    )""")
    # giveaways persisted
    c.execute("""
    CREATE TABLE IF NOT EXISTS giveaways(
        message_id INTEGER PRIMARY KEY,
        channel_id INTEGER,
        guild_id INTEGER,
        host_id TEXT,
        prize TEXT,
        ends_at INTEGER,
        winners INTEGER,
        participants TEXT
    )""")
    conn.commit()
    conn.close()

def db_conn():
    return sqlite3.connect(DB_FILE)

# ====== DISCORD BOT SETUP ======
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# start web keep_alive if available
if HAS_WEB and web:
    try:
        web.keep_alive()
        print("web.keep_alive() started")
    except Exception as e:
        print("web.keep_alive() error:", e)

# ====== PRESENCE UPDATER ======
@tasks.loop(seconds=30)
async def presence_updater():
    try:
        g = bot.get_guild(GUILD_ID) if GUILD_ID else None
        if g:
            await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=f"{g.member_count} members"))
    except Exception:
        pass

# ====== XP / LEVELING ======
_last_xp = {}  # uid -> next allowed timestamp

def xp_to_level(xp: int) -> int:
    # basic linear formula: each 100 XP -> next level
    return xp // 100 + 1

async def grant_xp_for_message(message: discord.Message):
    if message.author.bot or not isinstance(message.author, discord.Member):
        return
    uid = str(message.author.id)
    now_ts = int(time.time())
    next_allowed = _last_xp.get(uid, 0)
    if now_ts < next_allowed:
        return
    # give XP
    gained = random.randint(XP_MIN, XP_MAX)
    _last_xp[uid] = now_ts + XP_COOLDOWN

    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT xp, level FROM levels WHERE user_id=? AND guild_id=?", (uid, message.guild.id))
    row = c.fetchone()
    if row:
        xp, lvl = row
        xp += gained
        new_lvl = xp_to_level(xp)
        if new_lvl > lvl:
            # level up (we'll send a single subtle embed)
            c.execute("UPDATE levels SET xp=?, level=? WHERE user_id=? AND guild_id=?", (xp, new_lvl, uid, message.guild.id))
            conn.commit()
            conn.close()
            try:
                embed = make_embed("🎉 Level Up!", f"{message.author.mention} reached **Level {new_lvl}**!", color=0x57F287)
                await message.channel.send(embed=embed)
            except Exception:
                pass
            return
        else:
            c.execute("UPDATE levels SET xp=?, level=? WHERE user_id=? AND guild_id=?", (xp, lvl, uid, message.guild.id))
    else:
        xp = gained
        lvl = xp_to_level(xp)
        c.execute("INSERT INTO levels(user_id, guild_id, xp, level) VALUES(?,?,?,?)", (uid, message.guild.id, xp, lvl))
    conn.commit()
    conn.close()

# hook into on_message
@bot.event
async def on_message(message):
    await grant_xp_for_message(message)
    await bot.process_commands(message)

# slash: rank & leaderboard
@tree.command(name="rank", description="Check a member's level and XP")
@app_commands.describe(member="Member to check (optional)")
async def slash_rank(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    uid = str(member.id)
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT xp, level FROM levels WHERE user_id=? AND guild_id=?", (uid, interaction.guild.id))
    row = c.fetchone()
    conn.close()
    if not row:
        await interaction.response.send_message(embed=make_embed("No Data", f"{member.display_name} has no XP yet."), ephemeral=True)
        return
    xp, lvl = row
    needed = 100  # per-level fixed in this setup
    embed = make_embed(f"📈 {member.display_name}'s Rank", f"**Level:** {lvl}\n**XP:** {xp}/{needed}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="leaderboard", description="Show top users by level")
async def slash_leaderboard(interaction: discord.Interaction):
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT user_id, xp, level FROM levels WHERE guild_id=? ORDER BY level DESC, xp DESC LIMIT 10", (interaction.guild.id,))
    rows = c.fetchall()
    conn.close()
    if not rows:
        await interaction.response.send_message(embed=make_embed("No Data", "No XP yet."), ephemeral=True)
        return
    desc_lines = []
    for i, (uid, xp, lvl) in enumerate(rows, start=1):
        try:
            user = await bot.fetch_user(int(uid))
            name = user.display_name if hasattr(user, "display_name") else str(user)
        except Exception:
            name = f"User {uid}"
        desc_lines.append(f"**{i}. {name}** — Level {lvl} ({xp} XP)")
    await interaction.response.send_message(embed=make_embed("🏆 Leaderboard", "\n".join(desc_lines)), ephemeral=True)

# ====== SCHEDULED UNBANS ======
async def schedule_unban_task(guild_id: int, user_id: int, unban_at_ts: int):
    delay = unban_at_ts - int(time.time())
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        guild = bot.get_guild(guild_id)
        if guild:
            await guild.unban(discord.Object(id=user_id))
            # log
            ch = guild.get_channel(MOD_LOG_CHANNEL_ID) if MOD_LOG_CHANNEL_ID else None
            if ch:
                await ch.send(embed=make_embed("♻️ Auto Unban", f"<@{user_id}> was automatically unbanned (scheduled)."))
    except Exception as e:
        print("Auto-unban error:", e)
    # remove from DB
    conn = db_conn()
    c = conn.cursor()
    c.execute("DELETE FROM scheduled_unbans WHERE guild_id=? AND user_id=?", (guild_id, str(user_id)))
    conn.commit()
    conn.close()

async def schedule_unbans_from_db():
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT guild_id, user_id, unban_at FROM scheduled_unbans")
    rows = c.fetchall()
    conn.close()
    for guild_id, user_id, unban_at in rows:
        asyncio.create_task(schedule_unban_task(guild_id, int(user_id), int(unban_at)))

# ====== MODERATION SLASH COMMANDS ======
@tree.command(name="ban", description="Ban a member (supports duration like 1h, 30m, 2d)")
@app_commands.describe(member="Member to ban", duration="Optional duration like 30m, 1h, 2d", reason="Reason")
async def slash_ban(interaction: discord.Interaction, member: discord.Member, duration: str = None, reason: str = "No reason provided"):
    await interaction.response.defer(ephemeral=True)
    if not check_permissions(interaction.user, "admin"):
        return await interaction.followup.send(embed=make_embed("⛔ Permission denied", "You can't use /ban."), ephemeral=True)
    try:
        await member.ban(reason=reason)
        embed = make_embed("🔨 Member Banned", f"{member} was banned by {interaction.user}.\nReason: {reason}\nDuration: {duration or 'Permanent'}", color=0xED4245)
        await interaction.followup.send(embed=embed)
        # mod log
        if MOD_LOG_CHANNEL_ID:
            ch = interaction.guild.get_channel(MOD_LOG_CHANNEL_ID)
            if ch:
                await ch.send(embed=embed)
        # schedule unban if needed
        if duration:
            seconds = parse_duration_to_seconds(duration)
            if seconds > 0:
                unban_at = int(time.time()) + seconds
                conn = db_conn()
                c = conn.cursor()
                c.execute("INSERT OR REPLACE INTO scheduled_unbans(guild_id, user_id, unban_at) VALUES(?,?,?)",
                          (interaction.guild.id, str(member.id), unban_at))
                conn.commit()
                conn.close()
                asyncio.create_task(schedule_unban_task(interaction.guild.id, member.id, unban_at))
    except Exception as e:
        await interaction.followup.send(embed=make_embed("❌ Error", str(e)), ephemeral=True)

@tree.command(name="unban", description="Unban a user by ID")
@app_commands.describe(user_id="User ID to unban")
async def slash_unban(interaction: discord.Interaction, user_id: int):
    await interaction.response.defer(ephemeral=True)
    if not check_permissions(interaction.user, "admin"):
        return await interaction.followup.send(embed=make_embed("⛔ Permission denied", "You can't use /unban."), ephemeral=True)
    try:
        await interaction.guild.unban(discord.Object(id=user_id))
        # remove scheduled
        conn = db_conn()
        c = conn.cursor()
        c.execute("DELETE FROM scheduled_unbans WHERE guild_id=? AND user_id=?", (interaction.guild.id, str(user_id)))
        conn.commit()
        conn.close()
        embed = make_embed("✅ Unbanned", f"<@{user_id}> has been unbanned.", color=0x57F287)
        await interaction.followup.send(embed=embed)
        if MOD_LOG_CHANNEL_ID:
            ch = interaction.guild.get_channel(MOD_LOG_CHANNEL_ID)
            if ch:
                await ch.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(embed=make_embed("❌ Error", str(e)), ephemeral=True)

@tree.command(name="kick", description="Kick a member")
@app_commands.describe(member="Member to kick", reason="Reason")
async def slash_kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await interaction.response.defer(ephemeral=True)
    if not check_permissions(interaction.user, "mod"):
        return await interaction.followup.send(embed=make_embed("⛔ Permission denied", "You can't use /kick."), ephemeral=True)
    try:
        await member.kick(reason=reason)
        embed = make_embed("👢 Member Kicked", f"{member} was kicked.\nReason: {reason}", color=0xF1C40F)
        await interaction.followup.send(embed=embed)
        if MOD_LOG_CHANNEL_ID:
            ch = interaction.guild.get_channel(MOD_LOG_CHANNEL_ID)
            if ch:
                await ch.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(embed=make_embed("❌ Error", str(e)), ephemeral=True)

@tree.command(name="mute", description="Mute (timeout) a user for a duration like 10m or 1h")
@app_commands.describe(member="Member to mute", duration="Duration (e.g., 10m)", reason="Reason")
async def slash_mute(interaction: discord.Interaction, member: discord.Member, duration: str = "10m", reason: str = "No reason provided"):
    await interaction.response.defer(ephemeral=True)
    if not check_permissions(interaction.user, "mod"):
        return await interaction.followup.send(embed=make_embed("⛔ Permission denied", "You can't use /mute."), ephemeral=True)
    seconds = parse_duration_to_seconds(duration)
    if seconds <= 0:
        return await interaction.followup.send(embed=make_embed("❌ Invalid duration", "Use formats like 10m, 1h."), ephemeral=True)
    try:
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        await member.timeout(until=until, reason=reason)
        embed = make_embed("🔇 Muted", f"{member} muted for {duration}.\nReason: {reason}")
        await interaction.followup.send(embed=embed)
        if MOD_LOG_CHANNEL_ID:
            ch = interaction.guild.get_channel(MOD_LOG_CHANNEL_ID)
            if ch:
                await ch.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(embed=make_embed("❌ Error", str(e)), ephemeral=True)

@tree.command(name="unmute", description="Remove timeout from a user")
@app_commands.describe(member="Member to unmute")
async def slash_unmute(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    if not check_permissions(interaction.user, "mod"):
        return await interaction.followup.send(embed=make_embed("⛔ Permission denied", "You can't use /unmute."), ephemeral=True)
    try:
        await member.timeout(until=None, reason=f"Unmuted by {interaction.user}")
        embed = make_embed("🔊 Unmuted", f"{member.mention} has been unmuted.")
        await interaction.followup.send(embed=embed)
        if MOD_LOG_CHANNEL_ID:
            ch = interaction.guild.get_channel(MOD_LOG_CHANNEL_ID)
            if ch:
                await ch.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(embed=make_embed("❌ Error", str(e)), ephemeral=True)

@tree.command(name="warn", description="Warn a member (increases warn count)")
@app_commands.describe(member="Member to warn", reason="Reason")
async def slash_warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await interaction.response.defer(ephemeral=True)
    # simple warn counter in levels table for convenience
    conn = db_conn()
    c = conn.cursor()
    # ensure user row in levels (avoid separate warns table for brevity)
    c.execute("INSERT OR IGNORE INTO levels(user_id, guild_id, xp, level) VALUES(?,?,0,1)", (str(member.id), interaction.guild.id))
    # store warns in a separate simple table? We'll use a lightweight approach: store warns in giveaways table? No — create warns table:
    c.execute("""CREATE TABLE IF NOT EXISTS warns(
        guild_id INTEGER,
        user_id TEXT,
        warns INTEGER,
        PRIMARY KEY(guild_id, user_id)
    )""")
    c.execute("SELECT warns FROM warns WHERE guild_id=? AND user_id=?", (interaction.guild.id, str(member.id)))
    row = c.fetchone()
    if row:
        warns = row[0] + 1
        c.execute("UPDATE warns SET warns=? WHERE guild_id=? AND user_id=?", (warns, interaction.guild.id, str(member.id)))
    else:
        warns = 1
        c.execute("INSERT INTO warns(guild_id, user_id, warns) VALUES(?,?,?)", (interaction.guild.id, str(member.id), warns))
    conn.commit()
    conn.close()
    embed = make_embed("⚠️ Warn Issued", f"{member} was warned by {interaction.user}\nReason: {reason}\nTotal warns: {warns}")
    await interaction.followup.send(embed=embed)
    if MOD_LOG_CHANNEL_ID:
        ch = interaction.guild.get_channel(MOD_LOG_CHANNEL_ID)
        if ch:
            await ch.send(embed=embed)

# ====== ANNOUNCE (restricted) ======
@tree.command(name="announce", description="Send an announcement embed (restricted)")
@app_commands.describe(channel="Target channel", message="Announcement text", ping_everyone="Ping @everyone")
async def slash_announce(interaction: discord.Interaction, channel: discord.TextChannel = None, message: str = None, ping_everyone: bool = False):
    await interaction.response.defer(ephemeral=True)
    if not check_permissions(interaction.user, "announce"):
        return await interaction.followup.send(embed=make_embed("⛔ Permission denied", "You can't use /announce."), ephemeral=True)
    target = channel or interaction.channel
    content = "@everyone" if ping_everyone else None
    embed = make_embed("📢 Announcement", message or "No message provided", color=0x2ECC71)
    embed.set_footer(text=f"By {interaction.user.display_name}")
    try:
        await target.send(content=content, embed=embed)
        await interaction.followup.send(embed=make_embed("✅ Sent", f"Announcement posted in {target.mention}"), ephemeral=True)
        if MOD_LOG_CHANNEL_ID:
            ch = interaction.guild.get_channel(MOD_LOG_CHANNEL_ID)
            if ch:
                await ch.send(embed=make_embed("Announcement Sent", f"By {interaction.user} in {target.mention}"))
    except Exception as e:
        await interaction.followup.send(embed=make_embed("❌ Error sending announcement", str(e)), ephemeral=True)

# ====== GIVEAWAYS (persisted) ======
class JoinButton(Button):
    def __init__(self, message_id):
        super().__init__(style=discord.ButtonStyle.primary, label="🎉 Join", custom_id=f"join_gw:{message_id}")
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        conn = db_conn()
        c = conn.cursor()
        c.execute("SELECT participants FROM giveaways WHERE message_id=?", (self.message_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return await interaction.response.send_message("Giveaway not found or ended.", ephemeral=True)
        participants_json = row[0] or "[]"
        participants = json.loads(participants_json)
        uid = str(interaction.user.id)
        if uid in participants:
            conn.close()
            return await interaction.response.send_message("You already joined.", ephemeral=True)
        participants.append(uid)
        c.execute("UPDATE giveaways SET participants=? WHERE message_id=?", (json.dumps(participants), self.message_id))
        conn.commit()
        conn.close()
        # update embed footer count
        try:
            ch = interaction.guild.get_channel(interaction.channel.id)
            msg = await ch.fetch_message(self.message_id)
            embed = msg.embeds[0]
            embed.set_footer(text=f"Participants: {len(participants)}")
            await msg.edit(embed=embed)
        except Exception:
            pass
        await interaction.response.send_message(f"✅ Joined the giveaway! Participants: {len(participants)}", ephemeral=True)

@tree.command(name="gstart", description="Start a giveaway (giveaway team + staff)")
@app_commands.describe(channel="Channel to post giveaway", duration="Duration (e.g., 30m, 1h)", prize="Prize text", winners="Number of winners")
async def gstart(interaction: discord.Interaction, channel: discord.TextChannel, duration: str, prize: str, winners: int = 1):
    await interaction.response.defer(ephemeral=True)
    if not check_permissions(interaction.user, "giveaway"):
        return await interaction.followup.send(embed=make_embed("⛔ Permission denied", "You can't start giveaways."), ephemeral=True)
    seconds = parse_duration_to_seconds(duration)
    if seconds <= 0:
        return await interaction.followup.send(embed=make_embed("❌ Invalid duration", "Use 30m, 1h, 2d"), ephemeral=True)
    ends_at = int(time.time()) + seconds
    embed = make_embed("🎉 Giveaway Started!", f"**Prize:** {prize}\n**Host:** {interaction.user.mention}\n**Ends in:** {duration}\n**Winners:** {winners}", color=0xFFD700)
    embed.set_footer(text="Participants: 0")
    msg = await channel.send(embed=embed)
    # persist
    conn = db_conn()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO giveaways(message_id, channel_id, guild_id, host_id, prize, ends_at, winners, participants) VALUES(?,?,?,?,?,?,?,?)",
              (msg.id, channel.id, interaction.guild.id, str(interaction.user.id), prize, ends_at, winners, json.dumps([])))
    conn.commit()
    conn.close()
    # add button view
    view = View(timeout=None)
    view.add_item(JoinButton(msg.id))
    await msg.edit(view=view)
    await interaction.followup.send(embed=make_embed("✅ Giveaway Created", f"Giveaway ID: `{msg.id}` — ends in {duration}"), ephemeral=True)
    # schedule finish
    asyncio.create_task(handle_giveaway_end(msg.id, ends_at))

async def handle_giveaway_end(message_id: int, ends_at_ts: int):
    delay = ends_at_ts - int(time.time())
    if delay > 0:
        await asyncio.sleep(delay)
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT channel_id, guild_id, host_id, prize, winners, participants FROM giveaways WHERE message_id=?", (message_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return
    channel_id, guild_id, host_id, prize, winners, participants_json = row
    participants = json.loads(participants_json or "[]")
    guild = bot.get_guild(guild_id)
    channel = guild.get_channel(channel_id) if guild else None
    if not channel:
        c.execute("DELETE FROM giveaways WHERE message_id=?", (message_id,))
        conn.commit()
        conn.close()
        return
    if not participants:
        await channel.send(embed=make_embed("⚠️ Giveaway ended", f"No participants for **{prize}**.", color=0xE74C3C))
    else:
        winners = int(winners or 1)
        winners = min(winners, len(participants))
        chosen = random.sample(participants, winners)
        mentions = " ".join(f"<@{uid}>" for uid in chosen)
        await channel.send(embed=make_embed("🎉 Giveaway Winners", f"**Prize:** {prize}\n**Winners:** {mentions}"))
    # cleanup
    c.execute("DELETE FROM giveaways WHERE message_id=?", (message_id,))
    conn.commit()
    conn.close()

@tree.command(name="gend", description="End a giveaway early (message id)")
@app_commands.describe(message_id="Giveaway message ID")
async def gend(interaction: discord.Interaction, message_id: int):
    await interaction.response.defer(ephemeral=True)
    if not check_permissions(interaction.user, "giveaway"):
        return await interaction.followup.send(embed=make_embed("⛔ Permission denied", "You can't end giveaways."), ephemeral=True)
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT channel_id, prize, participants, winners FROM giveaways WHERE message_id=?", (message_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return await interaction.followup.send(embed=make_embed("❌ Not found", "Giveaway not found."), ephemeral=True)
    channel_id, prize, participants_json, winners = row
    participants = json.loads(participants_json or "[]")
    channel = bot.get_channel(channel_id)
    if not channel:
        c.execute("DELETE FROM giveaways WHERE message_id=?", (message_id,))
        conn.commit()
        conn.close()
        return await interaction.followup.send(embed=make_embed("✅ Ended", "Giveaway ended (channel missing)."), ephemeral=True)
    if not participants:
        await channel.send(embed=make_embed("⚠️ Giveaway Ended", "No participants joined."))
        c.execute("DELETE FROM giveaways WHERE message_id=?", (message_id,))
        conn.commit()
        conn.close()
        return await interaction.followup.send(embed=make_embed("✅ Ended", "Giveaway ended (no participants)."), ephemeral=True)
    winners = int(winners or 1)
    winners = min(len(participants), winners)
    chosen = random.sample(participants, winners)
    mentions = ", ".join(f"<@{uid}>" for uid in chosen)
    await channel.send(embed=make_embed("🎉 Giveaway Ended Early", f"**Prize:** {prize}\n**Winners:** {mentions}"))
    c.execute("DELETE FROM giveaways WHERE message_id=?", (message_id,))
    conn.commit()
    conn.close()
    await interaction.followup.send(embed=make_embed("✅ Ended", "Giveaway ended and winners announced."), ephemeral=True)

@tree.command(name="greroll", description="Reroll giveaway winners")
@app_commands.describe(message_id="Giveaway message ID")
async def greroll(interaction: discord.Interaction, message_id: int):
    await interaction.response.defer(ephemeral=True)
    if not check_permissions(interaction.user, "giveaway"):
        return await interaction.followup.send(embed=make_embed("⛔ Permission denied", "You can't reroll giveaways."), ephemeral=True)
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT channel_id, prize, participants, winners FROM giveaways WHERE message_id=?", (message_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return await interaction.followup.send(embed=make_embed("❌ Not found", "Giveaway not found."), ephemeral=True)
    channel_id, prize, participants_json, winners = row
    participants = json.loads(participants_json or "[]")
    if not participants:
        return await interaction.followup.send(embed=make_embed("❌ No participants", "There are no entries to reroll."), ephemeral=True)
    winners = int(winners or 1)
    winners = min(winners, len(participants))
    chosen = random.sample(participants, winners)
    mentions = ", ".join(f"<@{uid}>" for uid in chosen)
    channel = bot.get_channel(channel_id)
    if channel:
        await channel.send(embed=make_embed("🔁 Giveaway Rerolled", f"New winner(s): {mentions}"))
    await interaction.followup.send(embed=make_embed("✅ Rerolled", f"New winners: {mentions}"), ephemeral=True)

@tree.command(name="glist", description="List active giveaways")
async def glist(interaction: discord.Interaction):
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT message_id, prize, ends_at, winners, participants FROM giveaways WHERE guild_id=?", (interaction.guild.id,))
    rows = c.fetchall()
    conn.close()
    if not rows:
        return await interaction.response.send_message(embed=make_embed("No Active Giveaways", "There are no active giveaways."), ephemeral=True)
    embed = make_embed("🎁 Active Giveaways")
    for mid, prize, ends_at, winners, participants in rows:
        remaining = max(0, int(ends_at - time.time()))
        embed.add_field(name=f"ID: {mid}", value=f"Prize: {prize}\nWinners: {winners}\nEnds in: {remaining}s", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ====== INVITE TRACKER (best-effort snapshot) ======
_invite_snapshot = {}  # guild_id -> {code: uses}
async def snapshot_invites():
    for g in bot.guilds:
        try:
            invites = await g.invites()
            _invite_snapshot[g.id] = {inv.code: inv.uses for inv in invites}
        except Exception:
            _invite_snapshot[g.id] = {}

@bot.event
async def on_ready():
    init_db()
    print(f"✅ Logged in as {bot.user} (id: {bot.user.id})")
    # sync commands faster in guild if GUILD_ID provided
    try:
        if GUILD_ID:
            await tree.sync(guild=discord.Object(id=GUILD_ID))
        else:
            await tree.sync()
        print("Slash commands synced.")
    except Exception as e:
        print("Sync error:", e)
    # start background jobs
    if not presence_updater.is_running():
        presence_updater.start()
    await snapshot_invites()
    asyncio.create_task(schedule_unbans_from_db())

@bot.event
async def on_guild_join(guild):
    await snapshot_invites()

@bot.event
async def on_member_join(member):
    # invite detection (best-effort)
    try:
        invites = await member.guild.invites()
        prev = _invite_snapshot.get(member.guild.id, {})
        used = None
        for inv in invites:
            prev_uses = prev.get(inv.code, 0)
            if inv.uses > prev_uses:
                used = inv
                break
        _invite_snapshot[member.guild.id] = {inv.code: inv.uses for inv in invites}
        if MOD_LOG_CHANNEL_ID:
            ch = member.guild.get_channel(MOD_LOG_CHANNEL_ID)
            if ch:
                if used:
                    await ch.send(embed=make_embed("Member Joined", f"{member.mention} joined via {used.inviter}\nInvite: `{used.code}`"))
                else:
                    await ch.send(embed=make_embed("Member Joined", f"{member.mention} joined (invite unknown)"))
    except Exception:
        pass
    try:
        await presence_updater()
    except Exception:
        pass

@bot.event
async def on_member_remove(member):
    try:
        if MOD_LOG_CHANNEL_ID:
            ch = member.guild.get_channel(MOD_LOG_CHANNEL_ID)
            if ch:
                await ch.send(embed=make_embed("Member Left", f"{member.mention} left the server."))
    except Exception:
        pass
    try:
        await presence_updater()
    except Exception:
        pass

# ====== PREFIX FALLBACK COMMANDS (small set) ======
@bot.command()
async def ping(ctx):
    await ctx.send(f"Pong! {round(bot.latency*1000)}ms")

@bot.command()
async def say(ctx, *, text: str):
    await ctx.message.delete()
    await ctx.send(text)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int = 10):
    await ctx.channel.purge(limit=amount+1)
    await ctx.send(f"Cleared {amount} messages.", delete_after=5)

@bot.command()
async def rank_cmd(ctx, member: discord.Member = None):
    member = member or ctx.author
    uid = str(member.id)
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT xp, level FROM levels WHERE user_id=? AND guild_id=?", (uid, ctx.guild.id))
    row = c.fetchone()
    conn.close()
    if not row:
        return await ctx.send("No data.")
    xp, lvl = row
    await ctx.send(f"{member.display_name} — Level {lvl} ({xp} XP)")

# ====== RUN ======
if __name__ == "__main__":
    init_db()
    # start web keeper if present
    if HAS_WEB and web:
        try:
            web.keep_alive()
        except Exception as e:
            print("web.keep_alive error:", e)
    bot.run(DISCORD_TOKEN)

