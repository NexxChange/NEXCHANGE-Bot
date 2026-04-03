import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
from datetime import datetime, timedelta
import asyncio


# ============================================================
# NEXCHANGE BOT - COMPLETE CODE
# Made for NexChange Discord Exchange Server
# ============================================================

# ---------- CONFIGURATION ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Channel IDs
CHANNEL_OPEN_TICKET = 0
CHANNEL_COMPLETED_DEALS = 0
CHANNEL_REVIEWS = 0
CHANNEL_PENALTY_BOARD = 0
CHANNEL_AVAILABLE_EXCHANGERS = 0
CHANNEL_DEAL_LOGS = 0
CHANNEL_WEEKLY_COMMISSION = 0
CHANNEL_ANNOUNCEMENTS = 0

# Role IDs
ROLE_OWNER = 1487024314246107206
ROLE_ADMIN = 1487024413516890225
ROLE_MODERATOR = 1487024595944079471
ROLE_VERIFIED_EXCHANGER = 1487024698746474516
ROLE_CLIENT = 1487024799703109652
ROLE_MEMBER = 1487024877075697665

# Guild ID
GUILD_ID = 1486984795014828064

# Rates
I2C_RATE = 100
C2I_RATE = 97
COMMISSION_PER_DOLLAR = 1

# ---------- OPERATIONAL CONTROLS ----------
# FIX: Moved to top so commands defined below can reference it without NameError
operation_status = {
    "I2C": True,
    "C2I": True,
    "accepting_exchangers": True
}

# ---------- DATA STORAGE ----------
DATA_FILE = "nexchange_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {
        "exchangers": {},
        "deals": [],
        "penalties": {},
        "commission_owed": {}
    }

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

# ---------- BOT SETUP ----------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- VIEWS ----------

class ExchangeTypeView(discord.ui.View):
    """Main ticket panel - client selects I2C or C2I"""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="💰 INR 2 Crypto", style=discord.ButtonStyle.primary, custom_id="i2c_button")
    async def i2c_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not operation_status["I2C"]:
            await interaction.response.send_message("❌ INR to Crypto exchanges are currently closed. Please check announcements for updates.", ephemeral=True)
            return
        await interaction.response.send_modal(CreateTicketModal("I2C"))

    @discord.ui.button(label="💸 Crypto 2 INR", style=discord.ButtonStyle.success, custom_id="c2i_button")
    async def c2i_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not operation_status["C2I"]:
            await interaction.response.send_message("❌ Crypto to INR exchanges are currently closed. Please check announcements for updates.", ephemeral=True)
            return
        await interaction.response.send_modal(CreateTicketModal("C2I"))


class ClaimTicketView(discord.ui.View):
    """View for exchangers to claim a ticket"""
    def __init__(self, ticket_data):
        super().__init__(timeout=None)
        self.ticket_data = ticket_data

    @discord.ui.button(label="✅ Claim Ticket", style=discord.ButtonStyle.success, custom_id="claim_ticket")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_data()
        user_id = str(interaction.user.id)

        role = discord.utils.get(interaction.guild.roles, id=ROLE_VERIFIED_EXCHANGER)
        if role not in interaction.user.roles:
            await interaction.response.send_message("❌ You are not a verified exchanger.", ephemeral=True)
            return

        if user_id not in data["exchangers"]:
            await interaction.response.send_message("❌ You are not registered as an exchanger.", ephemeral=True)
            return

        exchanger = data["exchangers"][user_id]

        if not exchanger.get("available", False):
            await interaction.response.send_message("❌ You are currently marked as unavailable. Toggle your status first.", ephemeral=True)
            return

        deal_amount = self.ticket_data.get("amount", 0)
        if deal_amount > exchanger.get("limit", 0):
            await interaction.response.send_message(f"❌ This deal exceeds your current limit of ${exchanger.get('limit', 0)}.", ephemeral=True)
            return

        channel = interaction.channel
        self.ticket_data["exchanger_id"] = user_id
        self.ticket_data["exchanger_name"] = interaction.user.display_name
        self.ticket_data["status"] = "in_progress"
        self.ticket_data["claimed_at"] = datetime.now().isoformat()

        for deal in data["deals"]:
            if deal.get("ticket_id") == self.ticket_data.get("ticket_id"):
                deal.update(self.ticket_data)
                break

        save_data(data)

        button.disabled = True
        await interaction.message.edit(view=self)

        exchange_type = self.ticket_data.get("type")
        amount = self.ticket_data.get("amount")
        client_id = self.ticket_data.get("client_id")

        embed = discord.Embed(
            title="🤝 Exchanger Claimed — Deal In Progress",
            color=discord.Color.blue()
        )
        embed.add_field(name="Exchanger", value=interaction.user.mention, inline=True)
        embed.add_field(name="Client", value=f"<@{client_id}>", inline=True)
        embed.add_field(name="Deal Type", value=exchange_type, inline=True)
        embed.add_field(name="Amount", value=f"${amount}", inline=True)

        if exchange_type == "I2C":
            inr_amount = amount * I2C_RATE
            embed.add_field(
                name="📋 Next Steps",
                value=f"**Exchanger:** Please share your UPI ID in this ticket.\n**Client:** Send ₹{inr_amount:,} to the exchanger's UPI and upload screenshot here.\n**Exchanger:** Verify payment received, then release crypto to client's wallet.",
                inline=False
            )
        else:
            inr_amount = amount * C2I_RATE
            embed.add_field(
                name="📋 Next Steps",
                value=f"**Client:** Please share your crypto wallet address in this ticket.\n**Exchanger:** Send ${amount} USDT to client's wallet.\n**Client:** Confirm receipt, then send ₹{inr_amount:,} to exchanger's UPI.\n**Exchanger:** Confirm INR received.",
                inline=False
            )

        embed.set_footer(text="⚠️ Exchanger must verify ALL payments before releasing funds. Never release based on screenshot alone.")

        await channel.send(embed=embed, view=CompleteOrCancelView(self.ticket_data))
        await interaction.response.send_message("✅ You have claimed this ticket.", ephemeral=True)


class CompleteOrCancelView(discord.ui.View):
    """View for completing or cancelling a deal"""
    def __init__(self, ticket_data):
        super().__init__(timeout=None)
        self.ticket_data = ticket_data

    @discord.ui.button(label="✅ Complete Deal", style=discord.ButtonStyle.success, custom_id="complete_deal")
    async def complete_deal(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_data()
        user_id = str(interaction.user.id)
        exchanger_id = self.ticket_data.get("exchanger_id")

        is_staff = any(r.id in [ROLE_OWNER, ROLE_ADMIN, ROLE_MODERATOR] for r in interaction.user.roles)

        if user_id != exchanger_id and not is_staff:
            await interaction.response.send_message("❌ Only the assigned exchanger or staff can complete this deal.", ephemeral=True)
            return

        await interaction.response.send_modal(CompleteDealModal(self.ticket_data))

    @discord.ui.button(label="❌ Cancel Deal", style=discord.ButtonStyle.danger, custom_id="cancel_deal")
    async def cancel_deal(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_data()
        user_id = str(interaction.user.id)
        is_staff = any(r.id in [ROLE_OWNER, ROLE_ADMIN, ROLE_MODERATOR] for r in interaction.user.roles)

        if not is_staff:
            await interaction.response.send_message("❌ Only Moderators and above can cancel a deal. If you have an issue please ping a moderator in this ticket.", ephemeral=True)
            return

        for deal in data["deals"]:
            if deal.get("ticket_id") == self.ticket_data.get("ticket_id"):
                deal["status"] = "cancelled"
                deal["cancelled_at"] = datetime.now().isoformat()
                break

        save_data(data)

        self.complete_deal.disabled = True
        self.cancel_deal.disabled = True
        await interaction.message.edit(view=self)

        embed = discord.Embed(
            title="❌ Deal Cancelled",
            description="This deal has been cancelled. If you need help open a dispute ticket.",
            color=discord.Color.red()
        )
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("Deal cancelled.", ephemeral=True)


class AvailabilityView(discord.ui.View):
    """Exchanger availability toggle"""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🟢 Go Online", style=discord.ButtonStyle.success, custom_id="go_online")
    async def go_online(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_data()
        user_id = str(interaction.user.id)

        if user_id not in data["exchangers"]:
            await interaction.response.send_message("❌ You are not a registered exchanger.", ephemeral=True)
            return

        data["exchangers"][user_id]["available"] = True
        save_data(data)

        await update_available_exchangers_channel(interaction.guild, data)
        await interaction.response.send_message("✅ You are now **Online** and can receive deals.", ephemeral=True)

    @discord.ui.button(label="🔴 Go Offline", style=discord.ButtonStyle.danger, custom_id="go_offline")
    async def go_offline(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_data()
        user_id = str(interaction.user.id)

        if user_id not in data["exchangers"]:
            await interaction.response.send_message("❌ You are not a registered exchanger.", ephemeral=True)
            return

        data["exchangers"][user_id]["available"] = False
        save_data(data)

        await update_available_exchangers_channel(interaction.guild, data)
        await interaction.response.send_message("✅ You are now **Offline**.", ephemeral=True)


# ---------- MODALS ----------

class CreateTicketModal(discord.ui.Modal):
    """Modal for creating a new exchange ticket"""
    def __init__(self, exchange_type):
        super().__init__(title=f"{'INR to Crypto' if exchange_type == 'I2C' else 'Crypto to INR'} Exchange")
        self.exchange_type = exchange_type

    amount = discord.ui.TextInput(
        label="Amount in USD ($)",
        placeholder="Enter amount e.g. 50",
        required=True,
        max_length=10
    )

    wallet = discord.ui.TextInput(
        label="Your Wallet Address (I2C) / UPI ID (C2I)",
        placeholder="Enter your wallet address or UPI ID",
        required=True,
        max_length=200
    )

    # FIX: on_submit was defined outside the class due to wrong indentation — moved inside
    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = float(self.amount.value)
            if amount <= 0:
                await interaction.response.send_message("❌ Amount must be greater than 0.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("❌ Invalid amount. Please enter a number.", ephemeral=True)
            return

        data = load_data()
        client_id = str(interaction.user.id)

        # Check ticket limits
        unclaimed_tickets = [
            d for d in data["deals"]
            if d.get("client_id") == client_id
            and d.get("status") == "open"
            and d.get("exchanger_id") is None
        ]

        inprogress_tickets = [
            d for d in data["deals"]
            if d.get("client_id") == client_id
            and d.get("status") == "in_progress"
        ]

        if len(unclaimed_tickets) >= 4:
            await interaction.response.send_message(
                "❌ You already have **4 unclaimed tickets** open. Please wait for an exchanger to claim your existing tickets before opening new ones.",
                ephemeral=True
            )
            return

        if len(inprogress_tickets) >= 4:
            await interaction.response.send_message(
                "❌ You already have **4 tickets in progress**. Please complete your existing deals before opening new ones.",
                ephemeral=True
            )
            return

        # Generate ticket ID
        ticket_id = f"NX-{len(data['deals']) + 1001}"

        # Create ticket channel
        guild = interaction.guild
        category = interaction.channel.category

        # FIX: Safe role fetching to prevent NoneType crash if a role doesn't exist
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }

        staff_roles = [
            (ROLE_MODERATOR, True),
            (ROLE_ADMIN, True),
            (ROLE_OWNER, True),
        ]
        for role_id, can_send in staff_roles:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=can_send)

        exchanger_role = guild.get_role(ROLE_VERIFIED_EXCHANGER)
        if exchanger_role:
            overwrites[exchanger_role] = discord.PermissionOverwrite(read_messages=True, send_messages=False)

        ticket_channel = await guild.create_text_channel(
            name=f"{self.exchange_type.lower()}-{ticket_id}",
            overwrites=overwrites,
            category=category
        )

        inr_amount = amount * (I2C_RATE if self.exchange_type == "I2C" else C2I_RATE)

        ticket_data = {
            "ticket_id": ticket_id,
            "type": self.exchange_type,
            "amount": amount,
            "inr_amount": inr_amount,
            "client_id": client_id,
            "client_name": interaction.user.display_name,
            "wallet_or_upi": self.wallet.value,
            "status": "open",
            "exchanger_id": None,
            "created_at": datetime.now().isoformat(),
            "channel_id": str(ticket_channel.id)
        }

        data["deals"].append(ticket_data)
        save_data(data)

        embed = discord.Embed(
            title=f"🎫 {ticket_id} — {'INR to Crypto' if self.exchange_type == 'I2C' else 'Crypto to INR'}",
            color=discord.Color.blue() if self.exchange_type == "I2C" else discord.Color.green()
        )
        embed.add_field(name="Client", value=interaction.user.mention, inline=True)
        embed.add_field(name="Type", value=self.exchange_type, inline=True)
        embed.add_field(name="Amount", value=f"${amount}", inline=True)
        embed.add_field(name="INR Value", value=f"₹{inr_amount:,.0f}", inline=True)
        embed.add_field(name="Rate", value=f"₹{I2C_RATE if self.exchange_type == 'I2C' else C2I_RATE}/$", inline=True)
        embed.add_field(name="Status", value="🟡 Waiting for Exchanger", inline=True)

        if self.exchange_type == "I2C":
            embed.add_field(name="Client Wallet", value=f"||{self.wallet.value}||", inline=False)
        else:
            embed.add_field(name="Client UPI", value=f"||{self.wallet.value}||", inline=False)

        embed.set_footer(text="⚡ Verified exchangers can claim this ticket below.")

        if exchanger_role:
            await ticket_channel.send(
                content=f"{exchanger_role.mention} — New {self.exchange_type} ticket available!",
                embed=embed,
                view=ClaimTicketView(ticket_data)
            )
        else:
            await ticket_channel.send(embed=embed, view=ClaimTicketView(ticket_data))

        await interaction.response.send_message(
            f"✅ Your ticket has been created: {ticket_channel.mention}",
            ephemeral=True
        )


class CompleteDealModal(discord.ui.Modal, title="Complete Deal"):
    """Modal for completing a deal with final amount"""
    def __init__(self, ticket_data):
        super().__init__()
        self.ticket_data = ticket_data

    final_amount = discord.ui.TextInput(
        label="Final Completed Amount in USD ($)",
        placeholder="Enter actual completed amount e.g. 50",
        required=True,
        max_length=10
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = float(self.final_amount.value)
            if amount <= 0:
                await interaction.response.send_message("❌ Amount must be greater than 0.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("❌ Invalid amount.", ephemeral=True)
            return

        data = load_data()
        exchanger_id = self.ticket_data.get("exchanger_id")
        ticket_id = self.ticket_data.get("ticket_id")
        exchange_type = self.ticket_data.get("type")

        commission = amount * COMMISSION_PER_DOLLAR

        for deal in data["deals"]:
            if deal.get("ticket_id") == ticket_id:
                deal["status"] = "completed"
                deal["final_amount"] = amount
                deal["commission"] = commission
                deal["completed_at"] = datetime.now().isoformat()
                break

        if exchanger_id not in data["commission_owed"]:
            data["commission_owed"][exchanger_id] = 0
        data["commission_owed"][exchanger_id] += commission

        save_data(data)

        guild = interaction.guild
        completed_channel = guild.get_channel(CHANNEL_COMPLETED_DEALS)
        logs_channel = guild.get_channel(CHANNEL_DEAL_LOGS)
        client_id = self.ticket_data.get("client_id")

        embed = discord.Embed(
            title=f"✅ Deal Completed — {ticket_id}",
            color=discord.Color.green()
        )
        embed.add_field(name="Type", value=exchange_type, inline=True)
        embed.add_field(name="Amount", value=f"${amount}", inline=True)
        embed.add_field(name="Exchanger", value=f"<@{exchanger_id}>", inline=True)
        embed.add_field(name="Client", value=f"<@{client_id}>", inline=True)
        embed.add_field(name="Commission", value=f"₹{commission:,.0f}", inline=True)
        embed.add_field(name="Completed At", value=datetime.now().strftime("%d/%m/%Y %H:%M"), inline=True)
        embed.set_footer(text="NexChange — Trusted Exchange Service")

        if completed_channel:
            await completed_channel.send(embed=embed)

        if logs_channel:
            await logs_channel.send(embed=embed)

        review_embed = discord.Embed(
            title="⭐ Deal Complete! Leave a Review",
            description=f"Deal {ticket_id} completed successfully!\n\nPlease leave a review in <#{CHANNEL_REVIEWS}>",
            color=discord.Color.gold()
        )
        await interaction.channel.send(embed=review_embed)
        await interaction.response.send_message(f"✅ Deal completed. Commission ₹{commission:,.0f} logged.", ephemeral=True)

        await asyncio.sleep(300)
        try:
            await interaction.channel.delete()
        except Exception:
            pass


class RegisterExchangerModal(discord.ui.Modal, title="Exchanger Registration"):
    """Modal for exchanger registration"""

    limit = discord.ui.TextInput(
        label="Your Exchange Limit in USD ($)",
        placeholder="e.g. 500",
        required=True,
        max_length=10
    )

    deposit_txn = discord.ui.TextInput(
        label="Deposit Transaction ID / UTR Number",
        placeholder="Paste your UPI UTR or transaction ID",
        required=True,
        max_length=200
    )

    shiba_username = discord.ui.TextInput(
        label="Your Shiba Server Username",
        placeholder="e.g. username#0000 or just username",
        required=True,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            limit = float(self.limit.value)
            if limit <= 0:
                await interaction.response.send_message("❌ Limit must be greater than 0.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("❌ Invalid limit amount.", ephemeral=True)
            return

        required_deposit_inr = limit * I2C_RATE * 2

        data = load_data()
        user_id = str(interaction.user.id)

        data["exchangers"][user_id] = {
            "name": interaction.user.display_name,
            "limit": limit,
            "required_deposit_inr": required_deposit_inr,
            "deposit_txn": self.deposit_txn.value,
            "shiba_username": self.shiba_username.value,
            "available": False,
            "registered_at": datetime.now().isoformat(),
            "total_deals": 0,
            "verified": False
        }

        save_data(data)

        guild = interaction.guild
        admin_role = guild.get_role(ROLE_ADMIN)

        embed = discord.Embed(
            title="📝 New Exchanger Application",
            color=discord.Color.orange()
        )
        embed.add_field(name="Applicant", value=interaction.user.mention, inline=True)
        embed.add_field(name="Requested Limit", value=f"${limit}", inline=True)
        embed.add_field(name="Required Deposit", value=f"₹{required_deposit_inr:,.0f}", inline=True)
        embed.add_field(name="Deposit Txn ID", value=self.deposit_txn.value, inline=False)
        embed.add_field(name="Shiba Username", value=self.shiba_username.value, inline=False)
        embed.add_field(
            name="⚠️ Staff Action Required",
            value=f"Manually verify **{self.shiba_username.value}** has a minimum of **20 vouches** on Shiba server before approving.\n\nUse `/verify_exchanger` to approve or `/reject_exchanger` to reject.",
            inline=False
        )
        embed.set_footer(text="DO NOT approve without verifying 20 Shiba vouches.")

        logs_channel = guild.get_channel(CHANNEL_DEAL_LOGS)
        if logs_channel:
            await logs_channel.send(content=admin_role.mention if admin_role else "", embed=embed)

        await interaction.response.send_message(
            f"✅ Application submitted!\n\n**Required deposit:** ₹{required_deposit_inr:,.0f}\n\n⚠️ Make sure you have at least **20 vouches** on the Shiba server or your application will be rejected.\n\nStaff will verify and respond within 24 hours.",
            ephemeral=True
        )


# ---------- HELPER FUNCTIONS ----------

async def update_available_exchangers_channel(guild, data):
    """Updates the available exchangers channel"""
    channel = guild.get_channel(CHANNEL_AVAILABLE_EXCHANGERS)
    if not channel:
        return

    available = [(uid, ex) for uid, ex in data["exchangers"].items()
                 if ex.get("available") and ex.get("verified")]

    embed = discord.Embed(
        title="🟢 Available Exchangers",
        description="These exchangers are currently online and ready to process deals.",
        color=discord.Color.green()
    )

    if not available:
        embed.description = "❌ No exchangers are currently available. Please check back later."
    else:
        for uid, ex in available:
            embed.add_field(
                name=f"💎 {ex['name']}",
                value=f"Limit: ${ex['limit']} | Deals: {ex.get('total_deals', 0)}",
                inline=True
            )

    embed.set_footer(text=f"Last updated: {datetime.now().strftime('%d/%m/%Y %H:%M')}")

    async for msg in channel.history(limit=10):
        await msg.delete()

    await channel.send(embed=embed)


# ---------- SLASH COMMANDS ----------

@bot.tree.command(name="setup_panel", description="Setup the main exchange panel [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def setup_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="💱 NEXCHANGE EXCHANGE PANEL",
        description="Welcome to NexChange — India's most trusted P2P crypto exchange.\n\nSelect your exchange type below to get started.",
        color=discord.Color.blue()
    )
    embed.add_field(name="💰 INR to Crypto (I2C)", value=f"Rate: ₹{I2C_RATE}/$\nSend INR, receive USDT", inline=True)
    embed.add_field(name="💸 Crypto to INR (C2I)", value=f"Rate: ₹{C2I_RATE}/$\nSend USDT, receive INR", inline=True)
    embed.add_field(
        name="ℹ️ Important",
        value="• Fixed rates, no negotiation\n• Read #tos before proceeding\n• ₹1/$ server handling charge included\n• All deals protected by escrow",
        inline=False
    )
    embed.set_footer(text="NexChange — Fast. Safe. Trusted.")

    await interaction.channel.send(embed=embed, view=ExchangeTypeView())
    await interaction.response.send_message("✅ Exchange panel setup complete.", ephemeral=True)


@bot.tree.command(name="setup_availability", description="Setup exchanger availability panel [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def setup_availability(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🔄 Exchanger Availability",
        description="Toggle your availability status here.\n\n⚠️ Always go offline when you are not available. Failure to do so will result in a penalty.",
        color=discord.Color.blue()
    )
    await interaction.channel.send(embed=embed, view=AvailabilityView())
    await interaction.response.send_message("✅ Availability panel setup complete.", ephemeral=True)


@bot.tree.command(name="apply_exchanger", description="Apply to become a verified exchanger")
async def apply_exchanger(interaction: discord.Interaction):
    if not operation_status["accepting_exchangers"]:
        await interaction.response.send_message("❌ Exchanger applications are currently closed. Please check announcements for updates.", ephemeral=True)
        return
    await interaction.response.send_modal(RegisterExchangerModal())


@bot.tree.command(name="verify_exchanger", description="Verify and approve an exchanger [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
@app_commands.describe(user="The exchanger to verify")
async def verify_exchanger(interaction: discord.Interaction, user: discord.Member):
    data = load_data()
    user_id = str(user.id)

    if user_id not in data["exchangers"]:
        await interaction.response.send_message("❌ This user has no pending application.", ephemeral=True)
        return

    data["exchangers"][user_id]["verified"] = True
    save_data(data)

    role = interaction.guild.get_role(ROLE_VERIFIED_EXCHANGER)
    if role:
        await user.add_roles(role)

    embed = discord.Embed(
        title="✅ Exchanger Verified",
        description=f"{user.mention} has been verified as an exchanger.",
        color=discord.Color.green()
    )
    embed.add_field(name="Limit", value=f"${data['exchangers'][user_id]['limit']}", inline=True)

    await interaction.response.send_message(embed=embed)
    await user.send(f"✅ Congratulations! You have been verified as an exchanger on **NexChange**.\n\nYour limit: **${data['exchangers'][user_id]['limit']}**\n\nToggle your availability in the availability panel to go online and start receiving deals.")


@bot.tree.command(name="reject_exchanger", description="Reject an exchanger application [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
@app_commands.describe(user="The exchanger to reject", reason="Reason for rejection")
async def reject_exchanger(interaction: discord.Interaction, user: discord.Member, reason: str):
    data = load_data()
    user_id = str(user.id)

    if user_id in data["exchangers"]:
        del data["exchangers"][user_id]
        save_data(data)

    await user.send(f"❌ Your exchanger application on **NexChange** has been rejected.\n\nReason: {reason}\n\nYou may reapply after resolving the issue.")
    await interaction.response.send_message(f"✅ Application rejected and {user.mention} notified.", ephemeral=True)


@bot.tree.command(name="add_penalty", description="Add a penalty to an exchanger [Staff only]")
@app_commands.checks.has_any_role("Owner", "Admin", "Moderator")
@app_commands.describe(user="The exchanger", amount="Penalty amount in INR", reason="Reason for penalty")
async def add_penalty(interaction: discord.Interaction, user: discord.Member, amount: int, reason: str):
    data = load_data()
    user_id = str(user.id)

    if user_id not in data["penalties"]:
        data["penalties"][user_id] = []

    data["penalties"][user_id].append({
        "amount": amount,
        "reason": reason,
        "paid": False,
        "date": datetime.now().isoformat(),
        "issued_by": str(interaction.user.id)
    })

    role = interaction.guild.get_role(ROLE_VERIFIED_EXCHANGER)
    if role and role in user.roles:
        await user.remove_roles(role)

    save_data(data)

    penalty_channel = interaction.guild.get_channel(CHANNEL_PENALTY_BOARD)
    embed = discord.Embed(title="⚠️ Penalty Issued", color=discord.Color.red())
    embed.add_field(name="Exchanger", value=user.mention, inline=True)
    embed.add_field(name="Amount", value=f"₹{amount}", inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text="Exchanger must pay penalty to resume trading.")

    if penalty_channel:
        await penalty_channel.send(embed=embed)

    await user.send(f"⚠️ You have received a penalty of **₹{amount}** on NexChange.\n\nReason: {reason}\n\nYou cannot exchange until this penalty is paid. Contact staff to settle.")
    await interaction.response.send_message(f"✅ Penalty of ₹{amount} issued to {user.mention}.", ephemeral=True)


@bot.tree.command(name="pay_penalty", description="Mark an exchanger's penalty as paid [Staff only]")
@app_commands.checks.has_any_role("Owner", "Admin", "Moderator")
@app_commands.describe(user="The exchanger who paid")
async def pay_penalty(interaction: discord.Interaction, user: discord.Member):
    data = load_data()
    user_id = str(user.id)

    if user_id not in data["penalties"] or not data["penalties"][user_id]:
        await interaction.response.send_message("❌ No pending penalties for this user.", ephemeral=True)
        return

    unpaid = [p for p in data["penalties"][user_id] if not p["paid"]]
    if not unpaid:
        await interaction.response.send_message("✅ No unpaid penalties found.", ephemeral=True)
        return

    total = sum(p["amount"] for p in unpaid)

    for p in data["penalties"][user_id]:
        if not p["paid"]:
            p["paid"] = True
            p["paid_at"] = datetime.now().isoformat()

    save_data(data)

    role = interaction.guild.get_role(ROLE_VERIFIED_EXCHANGER)
    if role and role not in user.roles:
        await user.add_roles(role)

    await user.send(f"✅ Your penalty of **₹{total}** has been marked as paid. You can now resume trading on NexChange.")
    await interaction.response.send_message(f"✅ Penalties cleared for {user.mention}. Role restored.", ephemeral=True)


@bot.tree.command(name="commission_status", description="Check commission owed by an exchanger [Staff only]")
@app_commands.checks.has_any_role("Owner", "Admin", "Moderator")
@app_commands.describe(user="The exchanger to check")
async def commission_status(interaction: discord.Interaction, user: discord.Member):
    data = load_data()
    user_id = str(user.id)
    owed = data["commission_owed"].get(user_id, 0)

    embed = discord.Embed(title="💰 Commission Status", color=discord.Color.blue())
    embed.add_field(name="Exchanger", value=user.mention, inline=True)
    embed.add_field(name="Commission Owed", value=f"₹{owed:,.0f}", inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="clear_commission", description="Clear commission after payment [Staff only]")
@app_commands.checks.has_any_role("Owner", "Admin", "Moderator")
@app_commands.describe(user="The exchanger who paid commission")
async def clear_commission(interaction: discord.Interaction, user: discord.Member):
    data = load_data()
    user_id = str(user.id)
    owed = data["commission_owed"].get(user_id, 0)
    data["commission_owed"][user_id] = 0
    save_data(data)

    await interaction.response.send_message(f"✅ Commission of ₹{owed:,.0f} cleared for {user.mention}.", ephemeral=True)


@bot.tree.command(name="exchanger_info", description="View your exchanger profile")
async def exchanger_info(interaction: discord.Interaction):
    data = load_data()
    user_id = str(interaction.user.id)

    if user_id not in data["exchangers"]:
        await interaction.response.send_message("❌ You are not a registered exchanger.", ephemeral=True)
        return

    ex = data["exchangers"][user_id]
    commission_owed = data["commission_owed"].get(user_id, 0)
    penalties = [p for p in data["penalties"].get(user_id, []) if not p["paid"]]

    embed = discord.Embed(title="💎 Your Exchanger Profile", color=discord.Color.blue())
    embed.add_field(name="Name", value=ex["name"], inline=True)
    embed.add_field(name="Limit", value=f"${ex['limit']}", inline=True)
    embed.add_field(name="Status", value="🟢 Online" if ex.get("available") else "🔴 Offline", inline=True)
    embed.add_field(name="Verified", value="✅ Yes" if ex.get("verified") else "⏳ Pending", inline=True)
    embed.add_field(name="Total Deals", value=ex.get("total_deals", 0), inline=True)
    embed.add_field(name="Commission Owed", value=f"₹{commission_owed:,.0f}", inline=True)
    embed.add_field(name="Pending Penalties", value=f"{len(penalties)} penalty(s)" if penalties else "None", inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="stats", description="View server exchange statistics [Staff only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def stats(interaction: discord.Interaction):
    data = load_data()

    completed = [d for d in data["deals"] if d.get("status") == "completed"]
    total_volume = sum(d.get("final_amount", 0) for d in completed)
    total_commission = sum(d.get("commission", 0) for d in completed)
    total_exchangers = len(data["exchangers"])
    verified_exchangers = len([e for e in data["exchangers"].values() if e.get("verified")])

    embed = discord.Embed(title="📊 NexChange Statistics", color=discord.Color.gold())
    embed.add_field(name="Total Deals", value=len(completed), inline=True)
    embed.add_field(name="Total Volume", value=f"${total_volume:,.2f}", inline=True)
    embed.add_field(name="Total Commission", value=f"₹{total_commission:,.0f}", inline=True)
    embed.add_field(name="Total Exchangers", value=total_exchangers, inline=True)
    embed.add_field(name="Verified Exchangers", value=verified_exchangers, inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------- SCHEDULED TASKS ----------

@tasks.loop(hours=1)
async def weekly_commission_report():
    """Sends weekly commission report every Sunday at 9 AM"""
    now = datetime.now()
    if now.weekday() == 6 and now.hour == 9:
        data = load_data()
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return

        channel = guild.get_channel(CHANNEL_WEEKLY_COMMISSION)
        if not channel:
            return

        embed = discord.Embed(
            title="📋 Weekly Commission Report",
            description=f"Week ending {now.strftime('%d/%m/%Y')}",
            color=discord.Color.gold()
        )

        total_commission = 0
        for user_id, amount in data["commission_owed"].items():
            if amount > 0:
                member = guild.get_member(int(user_id))
                name = member.display_name if member else f"User {user_id}"
                embed.add_field(name=name, value=f"₹{amount:,.0f} owed", inline=True)
                total_commission += amount

        embed.add_field(name="Total Commission Due", value=f"₹{total_commission:,.0f}", inline=False)
        embed.set_footer(text="All exchangers must pay by Monday midnight or trading will be suspended.")

        await channel.send(embed=embed)


# ---------- BOT EVENTS ----------

@bot.event
async def on_ready():
    print(f"✅ NexChange Bot is online as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} commands")
    except Exception as e:
        print(f"❌ Error syncing commands: {e}")

    weekly_commission_report.start()

    # FIX: Added persistent views for ClaimTicketView and CompleteOrCancelView
    # Note: these need ticket_data to function fully — persistent views across restarts
    # require storing ticket_data and rehydrating it; this registers the custom_ids at minimum
    bot.add_view(ExchangeTypeView())
    bot.add_view(AvailabilityView())


# ---------- OPERATIONAL CONTROLS ----------

@bot.tree.command(name="start_i2c", description="Open INR to Crypto exchanges [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def start_i2c(interaction: discord.Interaction):
    operation_status["I2C"] = True
    guild = interaction.guild
    announcements = guild.get_channel(CHANNEL_ANNOUNCEMENTS)

    embed = discord.Embed(
        title="✅ INR to Crypto — NOW OPEN",
        description="I2C exchanges are now open.\nHead to <#{}> to start your exchange.".format(CHANNEL_OPEN_TICKET),
        color=discord.Color.green()
    )
    embed.add_field(name="Rate", value=f"₹{I2C_RATE}/$", inline=True)
    embed.set_footer(text="NexChange — Fast. Safe. Trusted.")

    if announcements:
        await announcements.send(embed=embed)

    await interaction.response.send_message("✅ I2C exchanges are now open.", ephemeral=True)


@bot.tree.command(name="stop_i2c", description="Close INR to Crypto exchanges [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
@app_commands.describe(reason="Reason for closing I2C")
async def stop_i2c(interaction: discord.Interaction, reason: str = "Temporarily unavailable"):
    operation_status["I2C"] = False
    guild = interaction.guild
    announcements = guild.get_channel(CHANNEL_ANNOUNCEMENTS)

    embed = discord.Embed(
        title="🔴 INR to Crypto — CLOSED",
        description=f"I2C exchanges are temporarily closed.\n\n**Reason:** {reason}",
        color=discord.Color.red()
    )
    embed.set_footer(text="We will announce when I2C resumes. Apologies for the inconvenience.")

    if announcements:
        await announcements.send(embed=embed)

    await interaction.response.send_message("✅ I2C exchanges closed.", ephemeral=True)


@bot.tree.command(name="start_c2i", description="Open Crypto to INR exchanges [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def start_c2i(interaction: discord.Interaction):
    operation_status["C2I"] = True
    guild = interaction.guild
    announcements = guild.get_channel(CHANNEL_ANNOUNCEMENTS)

    embed = discord.Embed(
        title="✅ Crypto to INR — NOW OPEN",
        description="C2I exchanges are now open.\nHead to <#{}> to start your exchange.".format(CHANNEL_OPEN_TICKET),
        color=discord.Color.green()
    )
    embed.add_field(name="Rate", value=f"₹{C2I_RATE}/$", inline=True)
    embed.set_footer(text="NexChange — Fast. Safe. Trusted.")

    if announcements:
        await announcements.send(embed=embed)

    await interaction.response.send_message("✅ C2I exchanges are now open.", ephemeral=True)


@bot.tree.command(name="stop_c2i", description="Close Crypto to INR exchanges [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
@app_commands.describe(reason="Reason for closing C2I")
async def stop_c2i(interaction: discord.Interaction, reason: str = "Temporarily unavailable"):
    operation_status["C2I"] = False
    guild = interaction.guild
    announcements = guild.get_channel(CHANNEL_ANNOUNCEMENTS)

    embed = discord.Embed(
        title="🔴 Crypto to INR — CLOSED",
        description=f"C2I exchanges are temporarily closed.\n\n**Reason:** {reason}",
        color=discord.Color.red()
    )
    embed.set_footer(text="We will announce when C2I resumes. Apologies for the inconvenience.")

    if announcements:
        await announcements.send(embed=embed)

    await interaction.response.send_message("✅ C2I exchanges closed.", ephemeral=True)


@bot.tree.command(name="stop_all", description="Emergency stop — close ALL exchanges [Owner only]")
@app_commands.checks.has_any_role("Owner")
@app_commands.describe(reason="Reason for emergency stop")
async def stop_all(interaction: discord.Interaction, reason: str = "Emergency maintenance"):
    operation_status["I2C"] = False
    operation_status["C2I"] = False
    operation_status["accepting_exchangers"] = False
    guild = interaction.guild
    announcements = guild.get_channel(CHANNEL_ANNOUNCEMENTS)

    embed = discord.Embed(
        title="🚨 NEXCHANGE — ALL OPERATIONS SUSPENDED",
        description=f"All exchange operations have been temporarily suspended.\n\n**Reason:** {reason}\n\nAll pending deals will be honoured. No new tickets can be opened until further notice.",
        color=discord.Color.dark_red()
    )
    embed.set_footer(text="NexChange Management will update you shortly.")

    if announcements:
        await announcements.send(embed=embed)

    await interaction.response.send_message("🚨 All operations stopped.", ephemeral=True)


@bot.tree.command(name="start_all", description="Resume ALL exchanges after stop [Owner only]")
@app_commands.checks.has_any_role("Owner")
async def start_all(interaction: discord.Interaction):
    operation_status["I2C"] = True
    operation_status["C2I"] = True
    operation_status["accepting_exchangers"] = True
    guild = interaction.guild
    announcements = guild.get_channel(CHANNEL_ANNOUNCEMENTS)

    embed = discord.Embed(
        title="✅ NEXCHANGE — ALL OPERATIONS RESUMED",
        description="All exchange operations are back online.\n\nHead to <#{}> to start your exchange.".format(CHANNEL_OPEN_TICKET),
        color=discord.Color.green()
    )
    embed.add_field(name="I2C Rate", value=f"₹{I2C_RATE}/$", inline=True)
    embed.add_field(name="C2I Rate", value=f"₹{C2I_RATE}/$", inline=True)
    embed.set_footer(text="NexChange — Fast. Safe. Trusted.")

    if announcements:
        await announcements.send(embed=embed)

    await interaction.response.send_message("✅ All operations resumed.", ephemeral=True)


@bot.tree.command(name="stop_exchanger_applications", description="Stop accepting new exchanger applications [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def stop_exchanger_applications(interaction: discord.Interaction):
    operation_status["accepting_exchangers"] = False
    await interaction.response.send_message("✅ Exchanger applications are now closed.", ephemeral=True)


@bot.tree.command(name="start_exchanger_applications", description="Start accepting new exchanger applications [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def start_exchanger_applications(interaction: discord.Interaction):
    operation_status["accepting_exchangers"] = True
    await interaction.response.send_message("✅ Exchanger applications are now open.", ephemeral=True)


@bot.tree.command(name="server_status", description="Check current server operation status [Staff only]")
@app_commands.checks.has_any_role("Owner", "Admin", "Moderator")
async def server_status(interaction: discord.Interaction):
    embed = discord.Embed(title="📊 NexChange Operation Status", color=discord.Color.blue())
    embed.add_field(name="I2C Exchanges", value="🟢 Open" if operation_status["I2C"] else "🔴 Closed", inline=True)
    embed.add_field(name="C2I Exchanges", value="🟢 Open" if operation_status["C2I"] else "🔴 Closed", inline=True)
    embed.add_field(name="Exchanger Applications", value="🟢 Open" if operation_status["accepting_exchangers"] else "🔴 Closed", inline=True)
    embed.set_footer(text=f"Checked at {datetime.now().strftime('%d/%m/%Y %H:%M')}")

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------- RUN BOT ----------
bot.run(BOT_TOKEN)
