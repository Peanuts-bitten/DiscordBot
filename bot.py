# bot.py — full-power bot (leveling, giveaways, moderation, mod-log, slash commands)
import os
import json
import random
import asyncio
import re
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv

import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import View, Button

# Try to import web.keep_alive if web.py exists
try:
    import web
    HAS_WEB = True
except Exception:
    web = None
    HAS_WEB = False

load_dotenv()

# Environment variables (make sure these exist in .env or Render)
TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
GUILD_ID = int(os.getenv("GUILD_ID") or 0)
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID") or 0) if os.getenv("LOG_CHANNEL_ID") else None
MOD_LOG_CHANNEL_ID = int(os.getenv("MOD_LOG_CHANNEL_ID") or 0) if os.getenv("MOD_LOG_CHANNEL_ID") else None
DEVELOPER_ID = int(os.getenv("DEVELOPER_ID") or 0)

# Role name groups (exact text must match your server roles)
OWNER_ROLES = ["👑 Owner", "👑 Co Owner", "👑 Server Partner"]
HIGHEST_STAFF = ["🌸 ๖ۣMighty Children"]
MANAGER_ROLES = ["Server Manager", "Head administrator", "Administrator"]
MOD_ROLES = ["Head Moderator", "Senior Moderator", "Moderator", "Junior Moderator"]
GIVEAWAY_ROLES = ["🎉 𝐆𝐢𝐯𝐞𝐚𝐰𝐚𝐲 𝐓𝐞𝐚𝐦", "Hoster", "Giveaway Team"]
BREAK_ROLES = ["Staff On Break"]
HELPER_ROLES = ["Helper"]

# ---------- Helpers ----------
def make_embed(title=None, description=None, color=0x2F3136):
    e = discord.Embed(title=title, description=description, color=color)
    e.timestamp = datetime.utcnow()
    return e

def has_any_role_by_name(member: discord.Member, role_names):
    if not member:
        return False
    for r in member.roles:
        if r.name in role_names:
            return True
    return False

def check_permissions(member: discord.Member, kind: str) -> bool:
    # Staff on break blocks actions
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

# Duration parser
DURATION_RE = re.compile(r"(\d+)([smhd])", flags=re.I)
def parse_duration_to_seconds(text: str) -> int:
    if not text or not isinstance(text, str):
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

# ---------- Persistence: levels ----------
LEVELS_FILE = "levels.json"
def load_levels():
    if not os.path.exists(LEVELS_FILE):
        return {}
    try:
        with open(LEVELS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_levels(data):
    try:
        with open(LEVELS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print("Failed to save levels:", e)

levels_db = load_levels()
xp_cooldown = {}  # user_id -> next allowed timestamp

# In-memory giveaways: {message_id: {...}}
giveaways = {}

# ---------- Bot setup ----------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Start keep-alive if web.py present
if HAS_WEB and web:
    try:
        web.keep_alive()
    except Exception as e:
        print("web.keep_alive() failed:", e)

# ---------- Presence updater ----------
@tasks.loop(seconds=30)
async def presence_updater():
    try:
        g = bot.get_guild(GUILD_ID) if GUILD_ID else None
        if g:
            await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=f"{g.member_count} members"))
    except Exception:
        pass

# ---------- Startup ----------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (id: {bot.user.id})")
    try:
        if GUILD_ID:
            await tree.sync(guild=discord.Object(id=GUILD_ID))
        else:
            await tree.sync()
    except Exception as e:
        print("Sync error:", e)
    if not presence_updater.is_running():
        presence_updater.start()

@bot.event
async def on_member_join(member):
    try:
        await presence_updater()
    except Exception:
        pass

@bot.event
async def on_member_remove(member):
    try:
        await presence_updater()
    except Exception:
        pass

# ---------- Leveling (Mee6-like, silent) ----------
XP_MIN = 15
XP_MAX = 25
XP_COOLDOWN = 60  # seconds
def xp_for_next_level(level: int) -> int:
    return 100 * level

async def grant_xp_from_message(message: discord.Message):
    if message.author.bot:
        return
    uid = str(message.author.id)
    now_ts = time.time()
    if uid in xp_cooldown and xp_cooldown[uid] > now_ts:
        return
    xp_cooldown[uid] = now_ts + XP_COOLDOWN

    if uid not in levels_db:
        levels_db[uid] = {"xp": 0, "level": 1}

    gained = random.randint(XP_MIN, XP_MAX)
    levels_db[uid]["xp"] += gained

    current_level = levels_db[uid]["level"]
    needed = xp_for_next_level(current_level)
    leveled = False
    while levels_db[uid]["xp"] >= needed:
        levels_db[uid]["xp"] -= needed
        levels_db[uid]["level"] += 1
        leveled = True
        current_level = levels_db[uid]["level"]
        needed = xp_for_next_level(current_level)

    save_levels(levels_db)

    # Send a single subtle embed in the channel on level-up (you asked no spam, but you requested some reaction)
    if leveled:
        try:
            embed = make_embed(title="🎉 Level Up!", description=f"{message.author.mention} reached *Level {levels_db[uid]['level']}* 🎊", color=0x57F287)
            await message.channel.send(embed=embed)
        except Exception:
            pass

# Hook into on_message for XP
@bot.event
async def on_message(message: discord.Message):
    await grant_xp_from_message(message)
    await bot.process_commands(message)

# ---------- Mod log helper ----------
async def send_mod_log(embed: discord.Embed):
    ch_id = MOD_LOG_CHANNEL_ID or LOG_CHANNEL_ID
    if not ch_id:
        return
    ch = bot.get_channel(ch_id)
    if ch:
        try:
            await ch.send(embed=embed)
        except Exception:
            pass

# ---------- Slash & Prefix Commands ----------
@tree.command(name="ping", description="Check latency")
async def slash_ping(interaction: discord.Interaction):
    await interaction.response.send_message(embed=make_embed("🏓 Pong!", f"Latency: {round(bot.latency*1000)}ms"))

@tree.command(name="developerbadge", description="How to get the Active Developer Badge")
async def slash_developerbadge(interaction: discord.Interaction):
    await interaction.response.send_message(embed=make_embed("👨‍💻 Active Developer Badge",
        "Add a slash command to your app, use it in a server, and then claim at: https://discord.com/developers/active-developer"
    ), ephemeral=True)

@tree.command(name="rank", description="Check a member's level and XP")
@app_commands.describe(member="Member to check (optional)")
async def slash_rank(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    uid = str(member.id)
    data = levels_db.get(uid)
    if not data:
        return await interaction.response.send_message(embed=make_embed("No Data", f"{member.display_name} has no XP yet."), ephemeral=True)
    lvl = data.get("level", 1)
    xp = data.get("xp", 0)
    needed = xp_for_next_level(lvl)
    embed = make_embed(f"📈 {member.display_name}'s Rank", f"*Level:* {lvl}\n**XP:** {xp}/{needed}", color=0x3498db)
    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="leaderboard", description="Show top users by level")
async def slash_leaderboard(interaction: discord.Interaction):
    if not levels_db:
        return await interaction.response.send_message(embed=make_embed("Empty", "No XP data yet."), ephemeral=True)
    sorted_users = sorted(levels_db.items(), key=lambda x: (x[1].get("level",1), x[1].get("xp",0)), reverse=True)[:10]
    desc_lines = []
    for i, (uid, stats) in enumerate(sorted_users, start=1):
        try:
            user = await bot.fetch_user(int(uid))
            desc_lines.append(f"*{i}. {user.display_name}* — Level {stats.get('level',1)} ({stats.get('xp',0)} XP)")
        except Exception:
            desc_lines.append(f"*{i}. Unknown* — Level {stats.get('level',1)} ({stats.get('xp',0)} XP)")
    embed = make_embed("🏆 Leaderboard", "\n".join(desc_lines) or "No data", color=0x9B59B6)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------- Moderation slash commands ----------
@app_commands.checks.has_permissions(ban_members=True)
@tree.command(name="ban", description="Ban a member (supports duration like 30m, 1h, 2d)")
@app_commands.describe(member="Member to ban", duration="Optional duration like 30m, 1h, 2d", reason="Reason")
async def slash_ban(interaction: discord.Interaction, member: discord.Member, duration: str = None, reason: str = "No reason provided"):
    await interaction.response.defer(ephemeral=True)
    if not check_permissions(interaction.user, "admin"):
        return await interaction.followup.send(embed=make_embed("⛔ Permission denied", "You can't use /ban."), ephemeral=True)
    try:
        await member.ban(reason=reason)
        embed = make_embed("🔨 Member Banned", f"{member} was banned by {interaction.user}.\nReason: {reason}\nDuration: {duration or 'Permanent'}", color=0xED4245)
        await interaction.followup.send(embed=embed)
        await send_mod_log(embed)
        if duration:
            seconds = parse_duration_to_seconds(duration)
            if seconds > 0:
                async def unban_later(guild_id, user_id, wait):
                    await asyncio.sleep(wait)
                    try:
                        g = bot.get_guild(guild_id)
                        user = await bot.fetch_user(user_id)
                        await g.unban(user)
                        unban_embed = make_embed("♻️ Auto Unban", f"{user} was automatically unbanned after {duration}.")
                        await send_mod_log(unban_embed)
                    except Exception:
                        pass
                bot.loop.create_task(unban_later(interaction.guild.id, member.id, seconds))
    except Exception as e:
        await interaction.followup.send(embed=make_embed("❌ Error", str(e)), ephemeral=True)

@tree.command(name="unban", description="Unban a user by ID")
@app_commands.describe(user_id="User ID to unban")
async def slash_unban(interaction: discord.Interaction, user_id: int):
    await interaction.response.defer(ephemeral=True)
    if not check_permissions(interaction.user, "admin"):
        return await interaction.followup.send(embed=make_embed("⛔ Permission denied", "You can't use /unban."), ephemeral=True)
    try:
        user = await bot.fetch_user(user_id)
        await interaction.guild.unban(user)
        embed = make_embed("✅ Unbanned", f"{user.mention} has been unbanned.", color=0x57F287)
        await interaction.followup.send(embed=embed)
        await send_mod_log(embed)
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
        embed = make_embed("👢 Member Kicked", f"{member} was kicked.\nReason: {reason}")
        await interaction.followup.send(embed=embed)
        await send_mod_log(embed)
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
        until = datetime.utcnow() + timedelta(seconds=seconds)
        await member.timeout(until=until, reason=reason)
        embed = make_embed("🔇 Muted", f"{member} muted for {duration}.\nReason: {reason}")
        await interaction.followup.send(embed=embed)
        await send_mod_log(embed)
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
        await send_mod_log(embed)
    except Exception as e:
        await interaction.followup.send(embed=make_embed("❌ Error", str(e)), ephemeral=True)

# ---------- Announcement command (restricted) ----------
@tree.command(name="announce", description="Send an announcement embed")
@app_commands.describe(channel="Channel to announce in", message="Announcement message", ping_everyone="Ping @everyone")
async def slash_announce(interaction: discord.Interaction, channel: discord.TextChannel = None, message: str = None, ping_everyone: bool = False):
    await interaction.response.defer(ephemeral=True)
    if not check_permissions(interaction.user, "announce"):
        return await interaction.followup.send(embed=make_embed("⛔ Permission denied", "You can't use /announce."), ephemeral=True)
    target = channel or interaction.channel
    mention = "@everyone" if ping_everyone else ""
    embed = make_embed("📢 Announcement", message or "No message provided", color=0x2ECC71)
    embed.set_footer(text=f"By {interaction.user.display_name}")
    try:
        await target.send(content=mention, embed=embed)
        await interaction.followup.send(embed=make_embed("✅ Sent", f"Announcement posted in {target.mention}"), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(embed=make_embed("❌ Error sending announcement", str(e)), ephemeral=True)

# ---------- Giveaway system ----------
class GiveawayView(View):
    def __init__(self, gid):
        super().__init__(timeout=None)
        self.gid = gid

    @discord.ui.button(label="🎉 Join Giveaway", style=discord.ButtonStyle.green)
    async def join(self, interaction: discord.Interaction, button: Button):
        g = giveaways.get(self.gid)
        if not g:
            return await interaction.response.send_message("❌ Giveaway not found or already ended.", ephemeral=True)
        if interaction.user.id in g["participants"]:
            return await interaction.response.send_message("⚠️ You already joined.", ephemeral=True)
        g["participants"].add(interaction.user.id)
        # update embed participant count
        try:
            ch = bot.get_channel(g["channel_id"])
            msg = await ch.fetch_message(g["message_id"])
            embed = msg.embeds[0]
            embed.set_footer(text=f"Participants: {len(g['participants'])}")
            await msg.edit(embed=embed)
        except Exception:
            pass
        await interaction.response.send_message(f"✅ Joined the giveaway ({len(g['participants'])} participants).", ephemeral=True)

@tree.command(name="gstart", description="Start a giveaway (Giveaway team & staff)")
@app_commands.describe(duration="Duration (30m, 1h, 2d)", prize="Prize text", winners="Number of winners")
async def gstart(interaction: discord.Interaction, duration: str, prize: str, winners: int = 1):
    await interaction.response.defer(ephemeral=True)
    if not check_permissions(interaction.user, "giveaway"):
        return await interaction.followup.send(embed=make_embed("⛔ Permission denied", "You can't start giveaways."), ephemeral=True)
    seconds = parse_duration_to_seconds(duration)
    if seconds <= 0:
        return await interaction.followup.send(embed=make_embed("❌ Invalid duration", "Use 30m, 1h, 2d"), ephemeral=True)
    embed = make_embed("🎉 Giveaway Started!", f"*Prize:* {prize}\n**Host:** {interaction.user.mention}\n**Ends in:** {duration}\n**Winners:** {winners}", color=0xFFD700)
    embed.set_footer(text="Participants: 0")
    view = GiveawayView(None)
    msg = await interaction.channel.send(embed=embed, view=view)
    gid = msg.id
    view.gid = gid
    giveaways[gid] = {
        "prize": prize,
        "host_id": interaction.user.id,
        "winners": max(1, int(winners)),
        "ends_at": asyncio.get_event_loop().time() + seconds,
        "participants": set(),
        "message_id": gid,
        "channel_id": interaction.channel.id
    }
    await interaction.followup.send(embed=make_embed("✅ Giveaway Created", f"Giveaway ID: {gid} — ends in {duration}"), ephemeral=True)
    async def end_task(gid, delay):
        await asyncio.sleep(delay)
        g = giveaways.get(gid)
        if not g:
            return
        participants = list(g["participants"])
        ch = bot.get_channel(g["channel_id"])
        if not ch:
            giveaways.pop(gid, None)
            return
        if not participants:
            await ch.send(embed=make_embed("⚠️ Giveaway ended", "No participants joined."))
            giveaways.pop(gid, None)
            return
        winners_ids = random.sample(participants, min(len(participants), g["winners"]))
        winners_mentions = ", ".join(f"<@{uid}>" for uid in winners_ids)
        end_embed = make_embed("🎉 Giveaway Ended!", f"*Prize:* {g['prize']}\n**Winners:** {winners_mentions}\n**Hosted by:** <@{g['host_id']}>", color=0x57F287)
        await ch.send(embed=end_embed)
        giveaways.pop(gid, None)
    bot.loop.create_task(end_task(gid, seconds))

@tree.command(name="gend", description="End a giveaway early (message id)")
@app_commands.describe(message_id="Giveaway message ID")
async def gend(interaction: discord.Interaction, message_id: int):
    await interaction.res

