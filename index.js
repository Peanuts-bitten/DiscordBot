// === [001] 📦 Imports & Setup ===
import {
  Client, GatewayIntentBits, Partials, EmbedBuilder,
  PermissionsBitField, ButtonBuilder, ButtonStyle,
  ActionRowBuilder
} from 'discord.js';
import dotenv from 'dotenv';
import Database from 'better-sqlite3';
import fetch from 'node-fetch';
import { keepAlive } from './server.js';

dotenv.config();

keepAlive();

// === [020] 💾 Database Initialization ===
let db;
try {
  db = new Database('./bot_data.db', { 
    verbose: console.log,
    fileMustExist: false
  });
} catch (error) {
  console.error('Failed to initialize database:', error);
  process.exit(1);
}
db.exec(`
  CREATE TABLE IF NOT EXISTS levels (
    user_id TEXT,
    guild_id TEXT,
    xp INTEGER,
    level INTEGER,
    PRIMARY KEY (user_id, guild_id)
  );
  
  CREATE TABLE IF NOT EXISTS economy (
    user_id TEXT PRIMARY KEY,
    wallet INTEGER DEFAULT 0,
    bank INTEGER DEFAULT 0
  );
  
  CREATE TABLE IF NOT EXISTS inventory (
    user_id TEXT,
    item TEXT,
    quantity INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, item)
  );
  
  CREATE TABLE IF NOT EXISTS cooldowns (
    user_id TEXT,
    command TEXT,
    expires_at INTEGER,
    PRIMARY KEY (user_id, command)
  );
  
  CREATE TABLE IF NOT EXISTS scheduled_unbans (
    guild_id TEXT,
    user_id TEXT,
    unban_at INTEGER,
    PRIMARY KEY (guild_id, user_id)
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

// === [030] 🗄️ Database Functions ===

// === [035] 📈 Game Data ===
const stocks = {
  "MEME": { price: Math.floor(Math.random() * 100) + 100 },
  "CHAOS": { price: Math.floor(Math.random() * 200) + 50 },
  "BOTCOIN": { price: Math.floor(Math.random() * 500) + 10 }
};

// === [040] 🔐 Environment Variables ===
const MOD_LOG_CHANNEL_ID = process.env.MOD_LOG_CHANNEL_ID;
const GUILD_ID = process.env.GUILD_ID;

// === [050] 🎮 Discord Client ===
const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.GuildMembers,
    GatewayIntentBits.MessageContent,
    GatewayIntentBits.GuildMessageReactions
  ],
  partials: [
    Partials.Channel,
    Partials.Message,
    Partials.Reaction
  ]
});

// === [055] 🔧 Slash Commands Setup ===
const commands = [
  {
    name: 'craft',
    description: 'Craft an item using two ingredients',
    options: [
      {
        name: 'item1',
        description: 'First item to craft with',
        type: 3, // STRING
        required: true
      },
      {
        name: 'item2',
        description: 'Second item to craft with',
        type: 3, // STRING
        required: true
      }
    ]
  },
  {
    name: 'stocks',
    description: 'View current stock prices'
  },
  {
    name: 'buy',
    description: 'Buy stocks',
    options: [
      {
        name: 'symbol',
        description: 'Stock symbol to buy',
        type: 3,
        required: true
      },
      {
        name: 'amount',
        description: 'Amount of shares to buy',
        type: 4,
        required: true
      }
    ]
  },
  {
    name: 'lootbox',
    description: 'Open a loot box'
  },
  {
    name: 'auction',
    description: 'Start an auction',
    options: [
      {
        name: 'item',
        description: 'Item to auction',
        type: 3,
        required: true
      }
    ]
  }
];

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
  const row = db.prepare('SELECT expires_at FROM cooldowns WHERE user_id = ? AND command = ?').get(userId, command);
  if (row && row.expires_at > now) return row.expires_at - now;
  db.prepare('INSERT OR REPLACE INTO cooldowns (user_id, command, expires_at) VALUES (?, ?, ?)').run(userId, command, now + seconds);
  return 0;
}

function addItem(userId, item, qty = 1) {
  db.prepare(`INSERT INTO inventory (user_id, item, quantity) 
              VALUES (?, ?, ?)
              ON CONFLICT(user_id, item) DO UPDATE SET 
              quantity = quantity + ?`).run(userId, item, qty, qty);
}

// === [110] 🔨 Scheduled Unban Logic ===
async function scheduleUnban(guildId, userId, unbanAt) {
  const delay = unbanAt - Math.floor(Date.now() / 1000);
  if (delay > 0) await new Promise(r => setTimeout(r, delay * 1000));
  const guild = client.guilds.cache.get(guildId);
  if (guild) {
    try {
      await guild.members.unban(userId);
      db.prepare('DELETE FROM scheduled_unbans WHERE guild_id = ? AND user_id = ?').run(guildId, userId);
      const ch = guild.channels.cache.get(MOD_LOG_CHANNEL_ID);
      if (ch) ch.send({ embeds: [makeEmbed("♻️ Auto Unban", `<@${userId}> was automatically unbanned.`)] });
    } catch (error) {
      console.error(`Failed to unban ${userId} from ${guildId}:`, error);
    }
  }
}

function scheduleUnbansFromDB() {
  const unbans = db.prepare('SELECT guild_id, user_id, unban_at FROM scheduled_unbans').all();
  for (const unban of unbans) {
    scheduleUnban(unban.guild_id, unban.user_id, unban.unban_at);
  }
}

async function tempBan(guild, userId, duration, reason) {
  const unbanAt = Math.floor(Date.now() / 1000) + duration;
  await guild.members.ban(userId, { reason: `${reason} (Unban scheduled in ${duration} seconds)` });
  db.prepare('INSERT OR REPLACE INTO scheduled_unbans (guild_id, user_id, unban_at) VALUES (?, ?, ?)')
    .run(guild.id, userId, unbanAt);
  scheduleUnban(guild.id, userId, unbanAt);
}
}

// === [130] 🚀 Bot Ready Event ===
client.once('ready', async () => {
  console.log(`✅ Logged in as ${client.user.tag}`);
  
  try {
    console.log('Started refreshing application (/) commands.');
    await client.application.commands.set(commands);
    console.log('Successfully reloaded application (/) commands.');
  } catch (error) {
    console.error('Error refreshing commands:', error);
  }
  
  scheduleUnbansFromDB();
});

// === [135] ⚡ Interaction Handler ===
client.on('interactionCreate', async interaction => {
  if (!interaction.isChatInputCommand()) return;

  const { commandName } = interaction;

  try {
    switch (commandName) {
      case 'craft': {
        const item1 = options.getString('item1');
        const item2 = options.getString('item2');
        const combo = `${item1}+${item2}`;
        const cursedResults = {
          "Golden Banana+Cursed Sock": "Banana Sock of Shame",
          "Epic Sandwich+Mystery Box": "Lunchbox of Chaos",
          "Cursed Sock+Mystery Box": "Unholy Bundle"
        };
        const result = cursedResults[combo] || "Pile of Useless Junk";
        addItem(interaction.user.id, result);
        await interaction.reply(`You crafted a **${result}** from ${item1} and ${item2}.`);
        break;
      }
      
      case 'stocks': {
        const list = Object.entries(stocks)
          .map(([name, data]) => `• ${name}: ${data.price} coins`)
          .join('\n');
        await interaction.reply(`📈 Current Stock Prices:\n${list}`);
        break;
      }
      
      case 'buy': {
        try {
          const symbol = interaction.options.getString('symbol', true).toUpperCase();
          const amount = interaction.options.getInteger('amount', true);
          
          if (!stocks[symbol]) {
            await interaction.reply({ content: "Invalid stock symbol!", ephemeral: true });
            return;
          }

          const cost = stocks[symbol].price * amount;
          const getWallet = db.prepare('SELECT wallet FROM economy WHERE user_id = ?');
          const wallet = getWallet.get(interaction.user.id)?.wallet || 0;

          if (wallet < cost) {
            await interaction.reply({ content: "You can't afford that!", ephemeral: true });
            return;
          }

          const updateWallet = db.prepare('UPDATE economy SET wallet = wallet - ? WHERE user_id = ?');
          updateWallet.run(cost, interaction.user.id);
          await interaction.reply(`You bought ${amount} shares of ${symbol} for ${cost} coins.`);
        } catch (error) {
          console.error('Error in buy command:', error);
          await interaction.reply({ content: 'There was an error processing your purchase!', ephemeral: true });
        }
        break;
      }
      
      case 'lootbox': {
        const loot = ['🧦 Cursed Sock', '🍌 Golden Banana', '📦 Mystery Box', '🥪 Epic Sandwich'];
        const item = loot[Math.floor(Math.random() * loot.length)];
        addItem(interaction.user.id, item);
        await interaction.reply(`You opened a loot box and found a **${item}**! 🎉`);
        break;
      }
      
      case 'auction': {
        const item = options.getString('item');
        const embed = makeEmbed("🔥 Cursed Auction", `Item: **${item}**\nStarting bid: 100 coins\nReact with 💰 to bid!`);
        const auctionMsg = await interaction.reply({ embeds: [embed], fetchReply: true });
        await auctionMsg.react('💰');
        setTimeout(() => {
          interaction.channel.send(`⏳ Auction for **${item}** has ended. Winner: TBD (feature coming soon!)`);
        }, 30000);
        break;
      }
    }
  } catch (error) {
    console.error('Error handling command:', error);
    if (!interaction.replied) {
      await interaction.reply({ 
        content: 'There was an error executing this command!', 
        ephemeral: true 
      });
    }
  }
});

// === [140] 🎱 8Ball Replies ===
const eightBallReplies = [
  "Yes.", "No.", "Definitely.", "Absolutely not.", "Ask again later.",
  "I'm not sure.", "Without a doubt.", "Better not tell you now."
];

// === [150] 💬 Message Handler ===
client.on('messageCreate', async (msg) => {
  // Ignore messages from bots and DMs
  if (msg.author.bot || !msg.guild || !msg.member) return;

  // Begin transaction for all database operations
  const transaction = db.transaction(() => {
    // === [151] 📊 Leveling System ===
    const xpGain = Math.floor(Math.random() * 10) + 5;
    const stmt = db.prepare('SELECT xp, level FROM levels WHERE user_id = ? AND guild_id = ?');
    const userData = stmt.get(msg.author.id, msg.guild.id) || { xp: 0, level: 1 };
    
    const newXP = userData.xp + xpGain;
    const needed = getXPNeeded(userData.level);
    
    const updateLevel = db.prepare('INSERT OR REPLACE INTO levels (user_id, guild_id, xp, level) VALUES (?, ?, ?, ?)');
    
    if (newXP >= needed) {
      const newLevel = userData.level + 1;
      updateLevel.run(msg.author.id, msg.guild.id, newXP - needed, newLevel);
      await msg.channel.send(`🎉 ${msg.author} leveled up to ${newLevel}!`);
    } else {
      updateLevel.run(msg.author.id, msg.guild.id, newXP, userData.level);
    }

    // === [152] 💰 Economy Setup ===
    const setupEconomy = db.prepare('INSERT OR IGNORE INTO economy (user_id, wallet, bank) VALUES (?, 0, 0)');
    setupEconomy.run(msg.author.id);

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
    if (cmd === '!stocks') {
      const list = Object.entries(stocks)
        .map(([name, data]) => `• ${name}: ${data.price} coins`)
        .join('\n');
      msg.channel.send(`📈 Current Stock Prices:\n${list}`);
    }

    if (cmd === '!buy') {
      const symbol = args[0]?.toUpperCase();
      const amount = parseInt(args[1]);
      if (!symbol || !stocks[symbol] || isNaN(amount)) {
        await msg.reply("Usage: !buy [symbol] [amount]");
        return;
      }

      const cost = stocks[symbol].price * amount;
      const getWallet = db.prepare('SELECT wallet FROM economy WHERE user_id = ?');
      const wallet = getWallet.get(msg.author.id)?.wallet || 0;
      
      if (wallet < cost) {
        await msg.reply("You can't afford that!");
        return;
      }

      const updateWallet = db.prepare('UPDATE economy SET wallet = wallet - ? WHERE user_id = ?');
      updateWallet.run(cost, msg.author.id);
      await msg.channel.send(`${msg.author} bought ${amount} shares of ${symbol} for ${cost} coins.`);
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
  });

  try {
    // Execute all database operations in a transaction
    transaction();
  } catch (error) {
    console.error('Error in message handler:', error);
    msg.channel.send('There was an error processing your command!').catch(() => {});
  }
});

client.login(process.env.TOKEN); // logs in the bot