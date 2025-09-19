// === [001] 📦 Imports & Setup ===
import {
  Client, GatewayIntentBits, Partials, EmbedBuilder,
  PermissionsBitField, ButtonBuilder, ButtonStyle,
  ActionRowBuilder
} from 'discord.js';
import { config } from 'dotenv';
import Database from 'better-sqlite3';
import fetch from 'node-fetch';
import { keepAlive } from './server.js';

config();
keepAlive();

// === [020] 💾 Database Initialization ===
const db = new Database('./data.db');
db.exec(`
  CREATE TABLE IF NOT EXISTS levels (
    user_id TEXT, guild_id TEXT, xp INTEGER, level INTEGER,
    PRIMARY KEY(user_id, guild_id)
  );
  CREATE TABLE IF NOT EXISTS economy (
    user_id TEXT PRIMARY KEY, wallet INTEGER, bank INTEGER
  );
  CREATE TABLE IF NOT EXISTS inventory (
    user_id TEXT, item TEXT, quantity INTEGER,
    PRIMARY KEY(user_id, item)
  );
  CREATE TABLE IF NOT EXISTS cooldowns (
    user_id TEXT, command TEXT, expires_at INTEGER,
    PRIMARY KEY(user_id, command)
  );
  CREATE TABLE IF NOT EXISTS scheduled_unbans (
    guild_id TEXT, user_id TEXT, unban_at INTEGER,
    PRIMARY KEY(guild_id, user_id)
  );
  CREATE TABLE IF NOT EXISTS giveaways (
    message_id TEXT PRIMARY KEY,
    channel_id TEXT,
    guild_id TEXT,
    host_id TEXT,
    prize TEXT,
    ends_at INTEGER,
    winners INTEGER,
    participants TEXT
  );
`);

// === [040] 🔐 Environment Variables ===
const MOD_LOG_CHANNEL_ID = process.env.MOD_LOG_CHANNEL_ID;
const GUILD_ID = process.env.GUILD_ID;

// === [050] 🎮 Discord Client ===
const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.GuildMembers,
    GatewayIntentBits.MessageContent
  ],
  partials: [Partials.Channel]
});

// === [070] 🎨 Utility Functions ===
function makeEmbed(title, desc, color = 0x5865F2) {
  return new EmbedBuilder().setTitle(title).setDescription(desc).setColor(color);
}

function parseDuration(str) {
  const match = str.match(/^(\d+)([mhdw]?)$/);
  if (!match) return null;
  const [_, num, unit] = match;
  const multipliers = { '': 60, m: 60, h: 3600, d: 86400, w: 604800 };
  return parseInt(num) * (multipliers[unit] || 60);
}

function getXPNeeded(level) {
  return 5 * level ** 2 + 50 * level + 100;
}

function checkCooldown(userId, command, seconds) {
  const now = Math.floor(Date.now() / 1000);
  const row = db.prepare("SELECT expires_at FROM cooldowns WHERE user_id=? AND command=?").get(userId, command);
  if (row && row.expires_at > now) return row.expires_at - now;
  db.prepare("INSERT OR REPLACE INTO cooldowns VALUES (?, ?, ?)").run(userId, command, now + seconds);
  return 0;
}

function addItem(userId, item, qty = 1) {
  const row = db.prepare("SELECT quantity FROM inventory WHERE user_id=? AND item=?").get(userId, item);
  if (row) {
    db.prepare("UPDATE inventory SET quantity = quantity + ? WHERE user_id=? AND item=?").run(qty, userId, item);
  } else {
    db.prepare("INSERT INTO inventory VALUES (?, ?, ?)").run(userId, item, qty);
  }
}

// === [110] 🔨 Scheduled Unban Logic ===
async function scheduleUnban(guildId, userId, unbanAt) {
  const delay = unbanAt - Math.floor(Date.now() / 1000);
  if (delay > 0) await new Promise(r => setTimeout(r, delay * 1000));
  const guild = client.guilds.cache.get(guildId);
  if (guild) {
    db.prepare("DELETE FROM scheduled_unbans WHERE guild_id=? AND user_id=?").run(guildId, userId);
    const ch = guild.channels.cache.get(MOD_LOG_CHANNEL_ID);
    if (ch) ch.send({ embeds: [makeEmbed("♻️ Auto Unban", `<@${userId}> was automatically unbanned.`)] });
  }
}

function scheduleUnbansFromDB() {
  const rows = db.prepare("SELECT guild_id, user_id, unban_at FROM scheduled_unbans").all();
  for (const row of rows) {
    scheduleUnban(row.guild_id, row.user_id, row.unban_at);
  }
}

// === [130] 🚀 Bot Ready Event ===
client.once('ready', () => {
  console.log(`✅ Logged in as ${client.user.tag}`);
  scheduleUnbansFromDB();
});

// === [140] 🎱 8Ball Replies ===
const eightBallReplies = [
  "Yes.", "No.", "Definitely.", "Absolutely not.", "Ask again later.",
  "I'm not sure.", "Without a doubt.", "Better not tell you now."
];

// === [150] 💬 Message Handler ===
client.on('messageCreate', async msg => {
  if (msg.author.bot || !msg.guild) return;

  // === [151] 📊 Leveling System ===
  const xpGain = Math.floor(Math.random() * 10) + 5;
  const row = db.prepare("SELECT xp, level FROM levels WHERE user_id=? AND guild_id=?").get(msg.author.id, msg.guild.id);

  if (!row) {
    db.prepare("INSERT INTO levels VALUES (?, ?, ?, ?)").run(msg.author.id, msg.guild.id, xpGain, 1);
  } else {
    const newXP = row.xp + xpGain;
    const needed = getXPNeeded(row.level);
    if (newXP >= needed) {
      db.prepare("UPDATE levels SET xp=?, level=? WHERE user_id=? AND guild_id=?").run(newXP - needed, row.level + 1, msg.author.id, msg.guild.id);
      msg.channel.send(`🎉 ${msg.author} leveled up to ${row.level + 1}!`);
    } else {
      db.prepare("UPDATE levels SET xp=? WHERE user_id=? AND guild_id=?").run(newXP, msg.author.id, msg.guild.id);
    }
  }

  // === [152] 💰 Economy Setup ===
  const eco = db.prepare("SELECT wallet, bank FROM economy WHERE user_id=?").get(msg.author.id);
  if (!eco) {
    db.prepare("INSERT INTO economy VALUES (?, ?, ?)").run(msg.author.id, 0, 0);
  }

  // === [153] 🧠 Command Parsing ===
  const args = msg.content.trim().split(/ +/);
  const cmd = args.shift().toLowerCase();

// === [160] 🧪 Cursed Crafting System ===
if (cmd === '!craft') {
  const item1 = args[0];
  const item2 = args[1];
  if (!item1 || !item2) return msg.reply("Usage: !craft [item1] [item2]");

  const combo = `${item1}+${item2}`;
  const cursedResults = {
    "Golden Banana+Cursed Sock": "Banana Sock of Shame",
    "Epic Sandwich+Mystery Box": "Lunchbox of Chaos",
    "Cursed Sock+Mystery Box": "Unholy Bundle"
  };

  const result = cursedResults[combo] || "Pile of Useless Junk";
  addItem(msg.author.id, result);
  msg.channel.send(`${msg.author} crafted a **${result}** from ${item1} and ${item2}.`);
}

// === [161] 📈 Parody Stock Market ===
const stocks = {
  "MEME": { price: Math.floor(Math.random() * 100) + 100 },
  "CHAOS": { price: Math.floor(Math.random() * 200) + 50 },
  "BOTCOIN": { price: Math.floor(Math.random() * 500) + 10 }
};

if (cmd === '!stocks') {
  const list = Object.entries(stocks)
    .map(([name, data]) => `• ${name}: ${data.price} coins`)
    .join('\n');
  msg.channel.send(`📈 Current Stock Prices:\n${list}`);
}

if (cmd === '!buy') {
  const symbol = args[0]?.toUpperCase();
  const amount = parseInt(args[1]);
  if (!symbol || !stocks[symbol] || isNaN(amount)) return msg.reply("Usage: !buy [symbol] [amount]");

  const cost = stocks[symbol].price * amount;
  const bal = db.prepare("SELECT wallet FROM economy WHERE user_id=?").get(msg.author.id);
  if (bal.wallet < cost) return msg.reply("You can't afford that!");

  db.prepare("UPDATE economy SET wallet = wallet - ? WHERE user_id=?").run(cost, msg.author.id);
  msg.channel.send(`${msg.author} bought ${amount} shares of ${symbol} for ${cost} coins.`);
}

// === [162] 🎁 Loot Box Drop ===
if (cmd === '!lootbox') {
  const loot = ['🧦 Cursed Sock', '🍌 Golden Banana', '📦 Mystery Box', '🥪 Epic Sandwich'];
  const item = loot[Math.floor(Math.random() * loot.length)];
  addItem(msg.author.id, item);
  msg.channel.send(`${msg.author} opened a loot box and found a **${item}**! 🎉`);
}

// === [163] 🔥 Cursed Auction System ===
if (cmd === '!auction') {
  const item = args.join(' ');
  if (!item) return msg.reply("Usage: !auction [item name]");

  const embed = makeEmbed("🔥 Cursed Auction", `Item: **${item}**\nStarting bid: 100 coins\nReact with 💰 to bid!`);
  const auctionMsg = await msg.channel.send({ embeds: [embed] });
  await auctionMsg.react('💰');

  setTimeout(() => {
    msg.channel.send(`⏳ Auction for **${item}** has ended. Winner: TBD (feature coming soon!)`);
  }, 30000); // 30s auction window
}

}); // closes client.on('messageCreate', ...)

client.login(process.env.TOKEN); // logs in the bot
