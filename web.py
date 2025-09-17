# web.py
from flask import Flask, request, jsonify
import requests
import os
from dotenv import load_dotenv
from threading import Thread

load_dotenv()

app = Flask(__name__)

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
BOT_TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
# replace with your Render url + /callback (or set REDIRECT_URI in .env)
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://discordbot-d336ne6mcj7s73a42bu0.onrender.com/callback")

@app.route("/")
def index():
    if not CLIENT_ID:
        return "CLIENT_ID not set in .env", 500
    oauth_url = (
        "https://discord.com/api/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify%20role_connections.write%20guilds.members.read"
    )
    return f'<a href="{oauth_url}">Login with Discord</a>'

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "No code provided", 400

    # Exchange code for user token
    token_url = "https://discord.com/api/oauth2/token"
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    r = requests.post(token_url, data=data, headers=headers)
    if r.status_code != 200:
        return f"Token exchange failed: {r.status_code} {r.text}", 500
    token_data = r.json()
    user_token = token_data.get("access_token")
    if not user_token:
        return "No access token returned", 500

    # Get user info
    me = requests.get("https://discord.com/api/v10/users/@me", headers={"Authorization": f"Bearer {user_token}"})
    if me.status_code != 200:
        return f"Failed to fetch user info: {me.status_code}", 500
    user_info = me.json()
    user_id = user_info.get("id")

    # Fetch member roles from guild using bot token
    if not BOT_TOKEN or not GUILD_ID:
        return "BOT_TOKEN or GUILD_ID missing in server config", 500

    member_req = requests.get(
        f"https://discord.com/api/v10/guilds/{GUILD_ID}/members/{user_id}",
        headers={"Authorization": f"Bot {BOT_TOKEN}"}
    )
    if member_req.status_code != 200:
        return f"Failed to fetch member info: {member_req.status_code}", 500
    member = member_req.json()

    # Map role IDs -> names
    roles_req = requests.get(f"https://discord.com/api/v10/guilds/{GUILD_ID}/roles", headers={"Authorization": f"Bot {BOT_TOKEN}"})
    if roles_req.status_code != 200:
        return f"Failed to fetch roles: {roles_req.status_code}", 500
    roles_list = roles_req.json()
    role_map = {r["id"]: r["name"] for r in roles_list}

    user_role_names = [role_map.get(rid) for rid in member.get("roles", []) if rid in role_map]

    # Decide metadata flags (adjust role names to match your server)
    is_owner = any(r in user_role_names for r in ["👑 Owner", "👑 Server Partner", "👑 Co Owner"])
    is_admin = any(r in user_role_names for r in ["🌸 ๖ۣMighty Children", "Server Manager", "Head administrator", "Administrator"])
    is_mod = any(r in user_role_names for r in ["Head Moderator", "Senior Moderator", "Moderator", "Junior Moderator"])
    is_giveaway = "🎉 𝐆𝐢𝐯𝐞𝐚𝐰𝐚𝐲 𝐓𝐞𝐚𝐦" in user_role_names

    metadata = {
        "platform_name": "My Bot",
        "metadata": {
            "is_owner": "true" if is_owner else "false",
            "is_admin": "true" if is_admin else "false",
            "is_mod": "true" if is_mod else "false",
            "is_giveaway": "true" if is_giveaway else "false"
        }
    }

    # Update role connection metadata for the user
    put_url = f"https://discord.com/api/v10/users/@me/applications/{CLIENT_ID}/role-connection"
    put_resp = requests.put(put_url, json=metadata, headers={"Authorization": f"Bearer {user_token}"})

    if put_resp.status_code not in (200, 204):
        return f"Failed to update linked role metadata: {put_resp.status_code} {put_resp.text}", 500

    return f"✅ Linked Role metadata updated for {user_info.get('username')}."

# keep-alive helper for Replit/Render
def run():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

# optional: allow running web.py directly
if __name__ == "__main__":
    run()

  