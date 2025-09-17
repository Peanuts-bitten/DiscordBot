# bot.py
import discord
from discord.ext import commands
from discord import app_commands
import os
from dotenv import load_dotenv
import asyncio
import random

import web  # your keep_alive file

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))

# --- Role Permissions ---
OWNER_ROLES = ["👑 Owner", "👑 Server Partner", "👑 Co Owner"]
MIGHTY_ROLES = ["🌸 ๖ۣMighty Children"]
ADMIN_ROLES = ["Server Manager", "Head administrator", "Administrator"]
MOD_ROLES = ["Head Moderator", "Senior Moderator", "Moderator", "Junior Moderator"]
HELPER_ROLES = ["Helper", "Staff On Break"]
GIVEAWAY_ROLES = ["🎉 𝐆𝐢𝐯𝐞𝐚𝐰𝐚𝐲 𝐓𝐞𝐚𝐦"]

# --- Bot Setup ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

giveaways = {}  # store ongoing giveaways


def has_role(user: discord.Member, role_list: list[str]) -> bool:
    return any(r.name in role_list for r in user.roles)


# --- Status Update ---
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"🔗 Synced {len(synced)} slash commands")
    except Exception as e:
        print("❌ Sync failed:", e)

    # Set initial status
    guild = bot.get_guild(GUILD_ID)
    if guild:
        await bot.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"{guild.member_count} members"
        ))


@bot.event
async def on_member_join(member):
    guild = bot.get_guild(GUILD_ID)
    if guild:
        await bot.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"{guild.member_count} members"
        ))


@bot.event
async def on_member_remove(member):
    guild = bot.get_guild(GUILD_ID)
    if guild:
        await bot.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"{guild.member_count} members"
        ))


# --- Slash Commands ---
@bot.tree.command(name="developerbadge", description="Learn how to get the Active Developer Badge")
async def developerbadge(interaction: discord.Interaction):
    embed = discord.Embed(
        title="👨‍💻 Discord Active Developer Badge",
        description=(
            "This badge is for bot developers who have an active application with at least one slash command.\n\n"
            "✅ Add a slash command to your bot\n"
            "✅ Use it at least once in a server\n"
            "✅ Claim it here: [Active Developer Portal](https://discord.com/developers/active-developer)"
        ),
        color=0x5865F2
    )
    embed.set_thumbnail(url="https://cdn.discordapp.com/emojis/1044112952782237776.png")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- Giveaway ---
class JoinButton(discord.ui.View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id
        giveaways[self.message_id] = []

    @discord.ui.button(label="🎉 Join Giveaway", style=discord.ButtonStyle.green)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in giveaways[self.message_id]:
            await interaction.response.send_message("❌ You already joined!", ephemeral=True)
        else:
            giveaways[self.message_id].append(interaction.user.id)
            await interaction.response.send_message(
                f"✅ You joined! Current participants: **{len(giveaways[self.message_id])}**",
                ephemeral=True
            )


@bot.tree.command(name="giveaway", description="Start a giveaway")
async def giveaway(interaction: discord.Interaction, prize: str, duration: int):
    if not has_role(interaction.user, OWNER_ROLES + MIGHTY_ROLES + ADMIN_ROLES + GIVEAWAY_ROLES):
        return await interaction.response.send_message("❌ You don’t have permission.", ephemeral=True)

    embed = discord.Embed(
        title="🎉 Giveaway Started!",
        description=f"**Prize:** {prize}\n⏳ Ends in {duration} seconds\n👤 Hosted by: {interaction.user.mention}",
        color=0x2ecc71
    )
    msg = await interaction.channel.send(embed=embed, view=JoinButton(interaction.id))
    await interaction.response.send_message("✅ Giveaway started!", ephemeral=True)

    await asyncio.sleep(duration)

    participants = giveaways.get(interaction.id, [])
    if not participants:
        return await interaction.channel.send("❌ No participants, giveaway canceled.")

    winner_id = random.choice(participants)
    winner = await bot.fetch_user(winner_id)

    end_embed = discord.Embed(
        title="🎉 Giveaway Ended!",
        description=f"**Prize:** {prize}\n🏆 Winner: {winner.mention}\n👤 Hosted by: {interaction.user.mention}\n"
                    f"👥 Participants: {len(participants)}",
        color=0xe74c3c
    )
    await msg.edit(embed=end_embed, view=None)
    await interaction.channel.send(f"🎊 Congratulations {winner.mention}! You won **{prize}**")


# --- Moderation ---
@bot.tree.command(name="ban", description="Ban a member")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    if not has_role(interaction.user, OWNER_ROLES + MIGHTY_ROLES + ADMIN_ROLES):
        return await interaction.response.send_message("❌ You don’t have permission to ban.", ephemeral=True)

    await member.ban(reason=reason)
    embed = discord.Embed(
        title="🔨 Member Banned",
        description=f"👤 {member.mention}\n🛠 By: {interaction.user.mention}\n📄 Reason: {reason}",
        color=0xe74c3c
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="unban", description="Unban a member by ID")
async def unban(interaction: discord.Interaction, user_id: str):
    if not has_role(interaction.user, OWNER_ROLES + MIGHTY_ROLES + ADMIN_ROLES):
        return await interaction.response.send_message("❌ You don’t have permission to unban.", ephemeral=True)

    user = await bot.fetch_user(int(user_id))
    await interaction.guild.unban(user)
    embed = discord.Embed(
        title="✅ Member Unbanned",
        description=f"👤 {user.mention}\n🛠 By: {interaction.user.mention}",
        color=0x2ecc71
    )
    await interaction.response.send_message(embed=embed)


# --- Announce ---
@bot.tree.command(name="announce", description="Send an announcement")
async def announce(interaction: discord.Interaction, title: str, message: str, channel: discord.TextChannel = None):
    if not has_role(interaction.user, OWNER_ROLES + MIGHTY_ROLES):
        return await interaction.response.send_message("❌ You don’t have permission to announce.", ephemeral=True)

    embed = discord.Embed(title=title, description=message, color=0x3498db)
    embed.set_footer(text=f"Announcement by {interaction.user}")

    target_channel = channel or interaction.channel
    await target_channel.send(embed=embed)
    await interaction.response.send_message("✅ Announcement sent!", ephemeral=True)


# --- Keep Alive & Run ---
web.keep_alive()
bot.run(TOKEN)
