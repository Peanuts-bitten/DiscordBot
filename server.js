import express from 'express';
import fetch from 'node-fetch';
import { config } from 'dotenv';

config();
const app = express();

const CLIENT_ID = process.env.CLIENT_ID;
const CLIENT_SECRET = process.env.CLIENT_SECRET;
const BOT_TOKEN = process.env.DISCORD_TOKEN;
const GUILD_ID = process.env.GUILD_ID;
const REDIRECT_URI = process.env.REDIRECT_URI || 'https://your-app.onrender.com/callback';

app.get('/ping', (_, res) => res.status(200).send('pong'));

app.get('/', (_, res) => {
  const oauthUrl = `https://discord.com/api/oauth2/authorize?client_id=${CLIENT_ID}&redirect_uri=${REDIRECT_URI}&response_type=code&scope=identify%20role_connections.write%20guilds.members.read`;
  res.send(`<a href="${oauthUrl}">Login with Discord</a>`);
});

app.get('/callback', async (req, res) => {
  const code = req.query.code;
  if (!code) return res.status(400).send('No code provided');

  try {
    const tokenRes = await fetch('https://discord.com/api/oauth2/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        client_id: CLIENT_ID,
        client_secret: CLIENT_SECRET,
        grant_type: 'authorization_code',
        code,
        redirect_uri: REDIRECT_URI
      })
    });

    const tokenData = await tokenRes.json();
    const userToken = tokenData.access_token;
    if (!userToken) return res.status(500).send('No access token returned');

    const userRes = await fetch('https://discord.com/api/v10/users/@me', {
      headers: { Authorization: `Bearer ${userToken}` }
    });
    const userInfo = await userRes.json();
    const userId = userInfo.id;

    const memberRes = await fetch(`https://discord.com/api/v10/guilds/${GUILD_ID}/members/${userId}`, {
      headers: { Authorization: `Bot ${BOT_TOKEN}` }
    });
    const member = await memberRes.json();

    const rolesRes = await fetch(`https://discord.com/api/v10/guilds/${GUILD_ID}/roles`, {
      headers: { Authorization: `Bot ${BOT_TOKEN}` }
    });
    const rolesList = await rolesRes.json();
    const roleMap = Object.fromEntries(rolesList.map(r => [r.id, r.name]));

    const userRoleNames = member.roles.map(rid => roleMap[rid]).filter(Boolean);

    const is_owner = userRoleNames.some(r => ["👑 Owner", "👑 Server Partner", "👑 Co Owner"].includes(r));
    const is_admin = userRoleNames.some(r => ["🌸 ๖ۣMighty Children", "Server Manager", "Head administrator", "Administrator"].includes(r));
    const is_mod = userRoleNames.some(r => ["Head Moderator", "Senior Moderator", "Moderator", "Junior Moderator"].includes(r));
    const is_giveaway = userRoleNames.includes("🎉 𝐆𝐢𝐯𝐞𝐚𝐰𝐚𝐲 𝐓𝐞𝐚𝐦") || userRoleNames.includes("🎉 Giveaway Hoster");

    const metadata = {
      platform_name: "My Bot",
      metadata: {
        is_owner: String(is_owner).toLowerCase(),
        is_admin: String(is_admin).toLowerCase(),
        is_mod: String(is_mod).toLowerCase(),
        is_giveaway: String(is_giveaway).toLowerCase()
      }
    };

    const putRes = await fetch(`https://discord.com/api/v10/users/@me/applications/${CLIENT_ID}/role-connection`, {
      method: 'PUT',
      headers: {
        Authorization: `Bearer ${userToken}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(metadata)
    });

    res.send(`✅ Linked Role metadata updated for ${userInfo.username}.`);
  } catch (err) {
    res.status(500).send(`Error: ${err.message}`);
  }
});

export function keepAlive() {
  const port = process.env.PORT || 10000;
  app.listen(port, () => console.log(`🌐 Web server running on port ${port}`));
}