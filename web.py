# web.py -- OAuth + Linked Roles + metadata registration + keep-alive
from flask import Flask, request, jsonify, redirect
from threading import Thread
import requests
import os
from dotenv import load_dotenv

load_dotenv()
app = Flask(_name_)

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://your-app.onrender.com/callback")

API = "https://discord.com/api/v10"

# Metadata definitions to register (BOOLEAN_EQUALS type=7)
METADATA_DEFS = [
    {"key": "is_owner",  "name": "Owner",     "description": "Owner / Co-Owner / Server Partner", "type": 7},
    {"key": "is_admin",  "name": "Admin",     "description": "Server Manager / Administrator",     "type": 7},
    {"key": "is_mod",    "name": "Moderator", "description": "Any moderator role",                 "type": 7},
    {"key": "is_hoster", "name": "Hoster",    "description": "Giveaway / Hoster team",              "type": 7},
]

def register_metadata_once():
    """Register Linked Roles metadata for your application (one-time operation).
       This uses Bot token to PUT the metadata definitions to Discord.
       It will silently fail if CLIENT_ID/BOT_TOKEN not set.
    """
    if not CLIENT_ID or not BOT_TOKEN:
        print("CLIENT_ID or BOT_TOKEN not set — skipping metadata registration.")
        return
    url = f"{API}/applications/{CLIENT_ID}/role-connections/metadata"
    headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
    try:
        r = requests.put(url, headers=headers, json=METADATA_DEFS, timeout=15)
        print("Metadata register status:", r.status_code, r.text)
    except Exception as e:
        print("Metadata registration failed:", e)

# Register metadata at start (safe to call repeatedly)
register_metadata_once()

@app.route("/")
def index():
    if not CLIENT_ID:
        return "CLIENT_ID not configured in environment.", 500
    oauth_url = (
        "https://discord.com/api/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify%20role_connections.write%20guilds.members.read"
    )
    return f'<a href="{oauth_url}">Login with Discord (Link Roles)</a>'

@app.route("/ping")
def ping():
    return jsonify({"status": "ok"}), 200

@app.route("/callback")
def callback():
    # OAuth2 callback: exchanges code -> token, inspects user roles and updates role-connection metadata
    code = request.args.get("code")
    if not code:
        return "Missing 'code' in query string.", 400

    # Exchange code for token
    token_url = f"{API}/oauth2/token"
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        t = requests.post(token_url, data=data, headers=headers, timeout=15)
    except Exception as e:
        return f"Token exchange error: {e}", 500
    if t.status_code != 200:
        return f"Token exchange failed: {t.status_code} {t.text}", 500
    token_data = t.json()
    user_token = token_data.get("access_token")
    if not user_token:
        return f"No access token returned: {token_data}", 500

    # Get basic user info
    me = requests.get(f"{API}/users/@me", headers={"Authorization": f"Bearer {user_token}"}, timeout=15)
    if me.status_code != 200:
        return f"Failed to fetch user info: {me.status_code} {me.text}", 500
    user_info = me.json()
    user_id = user_info.get("id")

    # Fetch member from guild using Bot token
    if not BOT_TOKEN or not GUILD_ID:
        return "BOT_TOKEN or GUILD_ID not configured on server.", 500

    member_req = requests.get(
        f"{API}/guilds/{GUILD_ID}/members/{user_id}",
        headers={"Authorization": f"Bot {BOT_TOKEN}"},
        timeout=15
    )
    if member_req.status_code != 200:
        return f"Failed to fetch member data: {member_req.status_code} {member_req.text}", 500
    member = member_req.json()

    # Fetch roles to map IDs -> names
    roles_req = requests.get(f"{API}/guilds/{GUILD_ID}/roles", headers={"Authorization": f"Bot {BOT_TOKEN}"}, timeout=15)
    if roles_req.status_code != 200:
        return f"Failed to fetch roles: {roles_req.status_code} {roles_req.text}", 500
    roles_list = roles_req.json()
    role_map = {r["id"]: r["name"] for r in roles_list}

    user_role_names = [role_map.get(rid) for rid in member.get("roles", []) if rid in role_map]

    # Determine flags — adjust the names below to exactly match your server role names
    is_owner  = any(r in user_role_names for r in ["👑 Owner", "👑 Co Owner", "👑 Server Partner"])
    is_admin  = any(r in user_role_names for r in ["🌸 ๖ۣMighty Children", "Server Manager", "Head administrator", "Administrator"])
    is_mod    = any(r in user_role_names for r in ["Head Moderator", "Senior Moderator", "Moderator", "Junior Moderator"])
    is_hoster = any(r in user_role_names for r in ["🎉 𝐆𝐢𝐯𝐞𝐚𝐰𝐚𝐲 𝐓𝐞𝐚𝐦", "Hoster", "Giveaway Team"])

    metadata = {
        "platform_name": "My Bot",
        "metadata": {
            "is_owner": str(is_owner).lower(),
            "is_admin": str(is_admin).lower(),
            "is_mod": str(is_mod).lower(),
            "is_hoster": str(is_hoster).lower()
        }
    }

    # Update role-connection metadata for the user (user must be authenticated)
    put_url = f"{API}/users/@me/applications/{CLIENT_ID}/role-connection"
    put_resp = requests.put(put_url, json=metadata, headers={"Authorization": f"Bearer {user_token}"}, timeout=15)
    if put_resp.status_code not in (200, 204):
        return f"Failed to update linked role metadata: {put_resp.status_code} {put_resp.text}", 500

    return f"✅ Linked Role metadata updated for {user_info.get('username')}."

def run():
    port = int(os.environ.get("PORT", 5000))
    # debug True helps during development; Render will capture logs
    app.run(host="0.0.0.0", port=port, debug=True)

def keep_alive():
    t = Thread(target=run, daemon=True)
    t.start()

if _name_ == "_main_":
    run()
