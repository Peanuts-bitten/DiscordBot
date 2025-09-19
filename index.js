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

  // === [160] Commands ===
  if (cmd === '!beg') {
    const cd = checkCooldown(msg.author.id, 'beg', 45);
    if (cd > 0) return msg.reply(`⏳ Wait ${cd}s before begging again.`);
    const coins = Math.floor(Math.random() * 50) + 1;
    db.prepare("UPDATE economy SET wallet = wallet + ? WHERE user_id=?").run(coins, msg.author.id);
    msg.channel.send(`${msg.author}, someone gave you ${coins} coins. Lucky beggar.`);
  }

  if (cmd === '!work') {
    const cd = checkCooldown(msg.author.id, 'work', 60);
    if (cd > 0) return msg.reply(`⏳ Wait ${cd}s before working again.`);
    const jobs = ["Developer", "Artist", "Chef", "Streamer", "Janitor"];
    const job = jobs[Math.floor(Math.random() * jobs.length)];
    const coins = Math.floor(Math.random() * 100) + 50;
    db.prepare("UPDATE economy SET wallet = wallet + ? WHERE user_id=?").run(coins, msg.author.id);
    msg.channel.send(`${msg.author}, you worked as a ${job} and earned ${coins} coins.`);
  }

  if (cmd === '!balance') {
    const bal = db.prepare("SELECT wallet, bank FROM economy WHERE user_id=?").get(msg.author.id);
    msg.channel.send(`${msg.author} — Wallet: ${bal.wallet} | Bank: ${bal.bank}`);
  }

  if (cmd === '!rank') {
    const lvl = db.prepare("SELECT xp, level FROM levels WHERE user_id=? AND guild_id=?").get(msg.author.id, msg.guild.id);
    msg.channel.send(`${msg.author} — Level ${lvl.level} (${lvl.xp} XP)`);
  }

  if (cmd === '!say') {
    const text = args.join(' ');
    await msg.delete();
    msg.channel.send(text);
  }

if (cmd === '!clear') {
  if (!msg.member.permissions.has(PermissionsBitField.Flags.ManageMessages)) return;
  const amount = parseInt(args[0]) || 10;
  await msg.channel.bulkDelete(amount + 1);
  msg.channel.send(`Cleared ${amount} messages.`).then(m => setTimeout(() => m.delete(), 5000));
}

    msg.channel.send(`Cleared ${amount} messages.`).then(m => setTimeout(() => m.delete(), 5000));
  }

  if (cmd === '!meme') {
    const res = await fetch('https://meme-api.com/gimme');
    const data = await res.json();
    msg.channel.send({ embeds: [makeEmbed(data.title, data.url).setImage(data.url)] });
  }

  if (cmd === '!roast') {
    const target = msg.mentions.users.first();
    if (!target) return msg.reply("Tag someone to roast!");
    const roasts = [
      `${target}, you're the reason shampoo has instructions.`,
      `${target}, if I had a dollar for every smart thing you said, I'd be broke.`,
      `${target}, you're like a cloud. When you disappear, it's a beautiful day.`
    ];
    msg.channel.send(roasts[Math.floor(Math.random() * roasts.length)]);
  }

  if (cmd === '!8ball') {
    const question = args.join(' ');
    if (!question) return msg.reply("Ask a full question!");
    const reply = eightBallReplies[Math.floor(Math.random() * eightBallReplies.length)];
    msg.channel.send(`🎱 ${reply}`);
  }

  if (cmd === '!ban') {
    const member = msg.mentions.members.first();
    const durationArg = args[1];
    const reason = args.slice(2).join(' ') || 'No reason';
    const duration = parseDuration(durationArg);
    if (!member || !duration) return msg.reply("Usage: !ban @user 1h Reason");
    await member.ban({ reason });
    db.prepare("INSERT OR REPLACE INTO scheduled_unbans VALUES (?, ?, ?)").run(
      msg.guild.id,
      member.id,
      Math.floor(Date.now() / 1000) + duration
    );
    msg.channel.send(`${member.user.tag} banned for ${durationArg}. Reason: ${reason}`);
  }

  if (cmd === '!giveaway') {
    const durationArg = args[0];
    const winnerCount = parseInt(args[1]);
    const prize = args.slice(2).join(' ');
    const duration = parseDuration(durationArg);
    if (!duration || !winnerCount || !prize) return msg.reply("Usage: !giveaway 1h 2 Cool Prize");

    const embed = makeEmbed("🎉 Giveaway", `Prize: **${prize}**\nHosted by: ${msg.author}\nEnds in: ${durationArg}`);
    const button = new ButtonBuilder().setCustomId(`give_${msg.id}`).setLabel("Enter").setStyle(ButtonStyle.Success);
    const row = new ActionRowBuilder().addComponents(button);
    const giveawayMsg = await msg.channel.send({ embeds: [embed], components: [row] });

    db.prepare("INSERT INTO giveaways VALUES (?, ?, ?, ?, ?, ?, ?, ?)").run(
      giveawayMsg.id,
      msg.channel.id,
      msg.guild.id,
      msg.author.id,
      prize,
      Math.floor(Date.now() / 1000) + duration,
      winnerCount,
      ''
    );

    setTimeout(() => endGiveaway(giveawayMsg.id), duration * 1000);
  }

  if (cmd === '!reroll') {
    const msgId = args[0];
    if (!msgId) return msg.reply("Usage: !reroll [messageID]");
    endGiveaway(msgId);
  }
});

// === [220] 🎉 Giveaway Button Interaction ===
client.on('interactionCreate', async interaction => {
  if (!interaction.isButton()) return;

  if (interaction.customId.startsWith('give_')) {
    const msgId = interaction.customId.split('_')[1];
    const g = db.prepare("SELECT * FROM giveaways WHERE message_id=?").get(msgId);
    if (!g) return interaction.reply({ content: "Giveaway not found.", ephemeral: true });

    const participants = g.participants ? g.participants.split(',') : [];
    if (participants.includes(interaction.user.id)) {
      return interaction.reply({ content: "You've already entered!", ephemeral: true });
    }

    participants.push(interaction.user.id);
    db.prepare("UPDATE giveaways SET participants=? WHERE message_id=?").run(participants.join(','), msgId);
    interaction.reply({ content: "🎉 You're in!", ephemeral: true });
  }
});

// === [230] 🎁 Giveaway Ending Logic ===
function endGiveaway(msgId) {
  const g = db.prepare("SELECT * FROM giveaways WHERE message_id=?").get(msgId);
  if (!g) return;

  const channel = client.channels.cache.get(g.channel_id);
  if (!channel) return;

  const participants = g.participants ? g.participants.split(',') : [];
  if (participants.length === 0) {
    channel.send(`🎉 Giveaway ended: **${g.prize}**\nNo valid entries.`);
  } else {
    const winners = [];
    while (winners.length < g.winners && participants.length > 0) {
      const i = Math.floor(Math.random() * participants.length);
      winners.push(`<@${participants[i]}>`);
      participants.splice(i, 1);
    }
    channel.send(`🎉 Giveaway ended: **${g.prize}**\nWinners: ${winners.join(', ')}`);
  }

  db.prepare("DELETE FROM giveaways WHERE message_id=?").run(msgId);
}

// === [999] 🛡️ Error Handling ===
process.on('unhandledRejection', err => {
  console.error('🔥 Unhandled promise rejection:', err);
});

process.on('uncaughtException', err => {
  console.error('💥 Uncaught exception:', err);
});




