import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
from datetime import datetime
import asyncio

# ---------- CONFIG ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Channels
CHANNEL_OPEN_TICKET = 0
CHANNEL_COMPLETED_DEALS = 0
CHANNEL_REVIEWS = 0
CHANNEL_PENALTY_BOARD = 0
CHANNEL_AVAILABLE_EXCHANGERS = 0
CHANNEL_DEAL_LOGS = 1489685713749414169
CHANNEL_WEEKLY_COMMISSION = 0
CHANNEL_ANNOUNCEMENTS = 0

# Roles
ROLE_OWNER = 1487024314246107206
ROLE_ADMIN = 1487024413516890225
ROLE_MODERATOR = 1487024595944079471
ROLE_VERIFIED_EXCHANGER = 1487024698746474516
ROLE_CLIENT = 1487024799703109652
ROLE_MEMBER = 1487024877075697665
ROLE_DEVELOPER = 1487305830188585070

GUILD_ID = 1486984795014828064

# Rates
I2C_RATE = 100
C2I_RATE = 98
COMMISSION_PER_DOLLAR = 1

# ---------- DATA ----------
DATA_FILE = "nexchange_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
    else:
        data = {
            "exchangers": {},
            "deals": [],
            "penalties": {},
            "commission_owed": {},
            "rates": {"I2C": I2C_RATE, "C2I": C2I_RATE}
        }

    if "rates" not in data:
        data["rates"] = {"I2C": I2C_RATE, "C2I": C2I_RATE}

    return data

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

# ---------- BOT ----------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- RATE COMMAND ----------
@bot.tree.command(name="set_rate", description="Update exchange rates")
@app_commands.checks.has_any_role(ROLE_OWNER, ROLE_DEVELOPER)
async def set_rate(interaction: discord.Interaction, i2c: float, c2i: float):
    global I2C_RATE, C2I_RATE

    I2C_RATE = i2c
    C2I_RATE = c2i

    data = load_data()
    data["rates"] = {"I2C": I2C_RATE, "C2I": C2I_RATE}
    save_data(data)

    embed = discord.Embed(title="✅ Rates Updated", color=discord.Color.green())
    embed.add_field(name="I2C", value=f"₹{I2C_RATE}/$")
    embed.add_field(name="C2I", value=f"₹{C2I_RATE}/$")

    await interaction.response.send_message(embed=embed)

# ---------- APPLICATION REVIEW ----------
class ApplicationReviewView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_data()
        uid = str(self.user_id)

        if uid in data["exchangers"]:
            data["exchangers"][uid]["verified"] = True
            save_data(data)

            member = interaction.guild.get_member(int(uid))
            role = interaction.guild.get_role(ROLE_VERIFIED_EXCHANGER)

            if member and role:
                await member.add_roles(role)
                try:
                    await member.send("✅ You are approved as exchanger.")
                except:
                    pass

        button.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message("Approved", ephemeral=True)

    @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_data()
        uid = str(self.user_id)

        if uid in data["exchangers"]:
            del data["exchangers"][uid]
            save_data(data)

        member = interaction.guild.get_member(int(uid))
        if member:
            try:
                await member.send("❌ Application rejected.")
            except:
                pass

        button.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message("Rejected", ephemeral=True)

# ---------- REGISTER ----------
class RegisterExchangerModal(discord.ui.Modal, title="Exchanger Registration"):
    limit = discord.ui.TextInput(label="Limit ($)")
    deposit_txn = discord.ui.TextInput(label="Transaction ID")
    shiba_username = discord.ui.TextInput(label="Shiba Username")

    async def on_submit(self, interaction: discord.Interaction):
        limit = float(self.limit.value)
        required_deposit = limit * I2C_RATE * 2

        data = load_data()
        uid = str(interaction.user.id)

        data["exchangers"][uid] = {"verified": False}
        save_data(data)

        channel = interaction.guild.get_channel(CHANNEL_DEAL_LOGS)

        embed = discord.Embed(
            title="📄 Application",
            color=discord.Color.orange(),
            timestamp=datetime.now()
        )

        embed.add_field(name="User", value=interaction.user.mention)
        embed.add_field(name="Limit", value=f"${limit}")
        embed.add_field(name="Deposit", value=f"₹{required_deposit}")
        embed.add_field(name="Txn", value=f"```{self.deposit_txn.value}```", inline=False)
        embed.add_field(name="Shiba", value=self.shiba_username.value, inline=False)

        await channel.send(embed=embed, view=ApplicationReviewView(interaction.user.id))

        await interaction.response.send_message("Applied!", ephemeral=True)

# ---------- READY ----------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    data = load_data()
    global I2C_RATE, C2I_RATE
    I2C_RATE = data["rates"]["I2C"]
    C2I_RATE = data["rates"]["C2I"]

    guild = bot.get_guild(GUILD_ID)
    role = guild.get_role(ROLE_VERIFIED_EXCHANGER)

    for uid, ex in data["exchangers"].items():
        if ex.get("verified"):
            member = guild.get_member(int(uid))
            if member and role not in member.roles:
                await member.add_roles(role)

    bot.add_view(ApplicationReviewView(0))

# ---------- RUN ----------
bot.run(BOT_TOKEN)
