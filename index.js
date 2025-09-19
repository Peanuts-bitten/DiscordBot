import {
  Client, GatewayIntentBits, Partials, EmbedBuilder,
  PermissionsBitField, ButtonBuilder, ButtonStyle,
  ActionRowBuilder
} from 'discord.js';
import { config } from 'dotenv';
import Database from 'better-sqlite3';
const db = new Database('./data.db');
import fetch from 'node-fetch';
import { keepAlive } from './server.js';

config();
keepAlive();

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.GuildMembers,
    GatewayIntentBits.MessageContent
  ],
  partials: [Partials.Channel]
});

const db = await open({ filename: './data.db', driver: sqlite3.Database });
await db.exec(`
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
`);

const MOD_LOG_CHANNEL_ID = process.env.MOD_LOG_CHANNEL_ID;
const GUILD_ID = process.env.GUILD_ID;

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

async function checkCooldown(userId, command, seconds) {
  const now = Math.floor(Date.now() / 1000);
  const row = await db.get("SELECT expires_at FROM cooldowns WHERE user_id=? AND command=?", userId, command);
  if (row && row.expires_at > now) return row.expires_at - now;
  await db.run("INSERT OR REPLACE INTO cooldowns VALUES (?, ?, ?)", userId, command, now + seconds);
  return 0;
}

async function addItem(userId, item, qty = 1) {
  const row = await db.get("SELECT quantity FROM inventory WHERE user_id=? AND item=?", userId, item);
  if (row) {
    await db.run("UPDATE inventory SET quantity = quantity + ? WHERE user_id=? AND item=?", qty, userId, item);
  } else {
    await db.run("INSERT INTO inventory VALUES (?, ?, ?)", userId, item, qty);
  }
}

async function scheduleUnban(guildId, userId, unbanAt) {
  const delay = unbanAt - Math.floor(Date.now() / 1000);
  if (delay > 0) await new Promise(r => setTimeout(r, delay * 1000));
  const guild = client.guilds.cache.get(guildId);
  if (guild) {
    await guild.members.unban(userId).catch(() => {});
    const ch = guild.channels.cache.get(MOD_LOG_CHANNEL_ID);
    if (ch) ch.send({ embeds: [makeEmbed("♻️ Auto Unban", `<@${userId}> was automatically unbanned.`)] });
  }
  await db.run("DELETE FROM scheduled_unbans WHERE guild_id=? AND user_id=?", guildId, userId);
}

async function scheduleUnbansFromDB() {
  const rows = await db.all("SELECT guild_id, user_id, unban_at FROM scheduled_unbans");
  for (const row of rows) scheduleUnban(row.guild_id, row.user_id, row.unban_at);
}

client.once('ready', () => {
  console.log(`✅ Logged in as ${client.user.tag}`);
  scheduleUnbansFromDB();
});

const eightBallReplies = [
  "Yes.", "No.", "Definitely.", "Absolutely not.", "Ask again later.",
  "I'm not sure.", "Without a doubt.", "Better not tell you now."
];

client.on('messageCreate', async msg => {
  if (msg.author.bot || !msg.guild) return;

  // Leveling system
  const xpGain = Math.floor(Math.random() * 10) + 5;
  const row = await db.get("SELECT xp, level FROM levels WHERE user_id=? AND guild_id=?", msg.author.id, msg.guild.id);
  if (!row) {
    await db.run("INSERT INTO levels VALUES (?, ?, ?, ?)", msg.author.id, msg.guild.id, xpGain, 1);
  } else {
    const newXP = row.xp + xpGain;
    const needed = getXPNeeded(row.level);
    if (newXP >= needed) {
      await db.run("UPDATE levels SET xp=?, level=? WHERE user_id=? AND guild_id=?", newXP - needed, row.level + 1, msg.author.id, msg.guild.id);
      msg.channel.send(`🎉 ${msg.author} leveled up to ${row.level + 1}!`);
    } else {
      await db.run("UPDATE levels SET xp=? WHERE user_id=? AND guild_id=?", newXP, msg.author.id, msg.guild.id);
    }
  }

  // Economy setup
  const eco = await db.get("SELECT wallet, bank FROM economy WHERE user_id=?", msg.author.id);
  if (!eco) await db.run("INSERT INTO economy VALUES (?, ?, ?)", msg.author.id, 0, 0);

  // Commands
  const args = msg.content.trim().split(/ +/);
  const cmd = args.shift().toLowerCase();

  if (cmd === '!beg') {
    const cd = await checkCooldown(msg.author.id, 'beg', 45);
    if (cd > 0) return msg.reply(`⏳ Wait ${cd}s before begging again.`);
    const coins = Math.floor(Math.random() * 50) + 1;
    await db.run("UPDATE economy SET wallet = wallet + ? WHERE user_id=?", coins, msg.author.id);
    msg.channel.send(`${msg.author}, someone gave you ${coins} coins. Lucky beggar.`);
  }

  if (cmd === '!work') {
    const cd = await checkCooldown(msg.author.id, 'work', 60);
    if (cd > 0) return msg.reply(`⏳ Wait ${cd}s before working again.`);
    const jobs = ["Developer", "Artist", "Chef", "Streamer", "Janitor"];
    const job = jobs[Math.floor(Math.random() * jobs.length)];
    const coins = Math.floor(Math.random() * 100) + 50;
    await db.run("UPDATE economy SET wallet = wallet + ? WHERE user_id=?", coins, msg.author.id);
    msg.channel.send(`${msg.author}, you worked as a ${job} and earned ${coins} coins.`);
  }

  if (cmd === '!balance') {
    const bal = await db.get("SELECT wallet, bank FROM economy WHERE user_id=?", msg.author.id);
    msg.channel.send(`${msg.author} — Wallet: ${bal.wallet} | Bank: ${bal.bank}`);
  }

  if (cmd === '!rank') {
    const lvl = await db.get("SELECT xp, level FROM levels WHERE user_id=? AND guild_id=?", msg.author.id, msg.guild.id);
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
    await db.run("INSERT OR REPLACE INTO scheduled_unbans VALUES (?, ?, ?)", msg.guild.id, member.id, Math.floor(Date.now() / 1000) + duration);
    msg.channel.send(`${member.user.tag} banned for ${durationArg}. Reason: ${reason}`);
  }
});

client.on('interactionCreate', async interaction => {
  if (!interaction.isButton()) return;

  if (interaction.customId.startsWith('give_')) {
    const msgId = interaction.customId.split('_')[1];
    const g = await db.get("SELECT * FROM giveaways WHERE message_id=?", msgId);
    if (!g) return interaction.reply({ content: "Giveaway not found.", ephemeral: true });

    const participants = g.participants ? g.participants.split(',') : [];
    if (participants.includes(interaction.user.id)) {
      return interaction.reply({ content: "You've already entered!", ephemeral: true });
    }

    participants.push(interaction.user.id);
    await db.run("UPDATE giveaways SET participants=? WHERE message_id=?", participants.join(','), msgId);
    interaction.reply({ content: "🎉 You're in!", ephemeral: true });
  }
});

async function endGiveaway(msgId) {
  const g = await db.get("SELECT * FROM giveaways WHERE message_id=?", msgId);
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

  await db.run("DELETE FROM giveaways WHERE message_id=?", msgId);
}

client.on('messageCreate', async msg => {
  const args = msg.content.trim().split(/ +/);
  const cmd = args.shift().toLowerCase();

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

    await db.run("INSERT INTO giveaways VALUES (?, ?, ?, ?, ?, ?, ?, ?)", giveawayMsg.id, msg.channel.id, msg.guild.id, msg.author.id, prize, Math.floor(Date.now() / 1000) + duration, winnerCount, '');

    setTimeout(() => endGiveaway(giveawayMsg.id), duration * 1000);
  }

  if (cmd === '!reroll') {
    const msgId = args[0];
    if (!msgId) return msg.reply("Usage: !reroll [messageID]");
    endGiveaway(msgId);
  }
});

client.login(process.env.DISCORD_TOKEN);

