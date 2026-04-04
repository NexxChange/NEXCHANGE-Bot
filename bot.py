import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
from datetime import datetime
import asyncio
import re
import io

try:
    import qrcode
    QR_AVAILABLE = True
except ImportError:
    QR_AVAILABLE = False

# ============================================================
# NEXCHANGE BOT - FULL UPDATED CODE
# ============================================================

# ---------- CONFIGURATION ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Channel IDs — fill these in
CHANNEL_OPEN_TICKET = 0
CHANNEL_COMPLETED_DEALS = 0
CHANNEL_REVIEWS = 0
CHANNEL_PENALTY_BOARD = 0
CHANNEL_AVAILABLE_EXCHANGERS = 0
CHANNEL_DEAL_LOGS = 0
CHANNEL_WEEKLY_COMMISSION = 0
CHANNEL_ANNOUNCEMENTS = 0
CHANNEL_TRANSACTIONS = 1488840012173676545  # Transcript channel

# Role IDs
ROLE_OWNER = 1487024314246107206
ROLE_ADMIN = 1487024413516890225
ROLE_MODERATOR = 1487024595944079471
ROLE_VERIFIED_EXCHANGER = 1487024698746474516
ROLE_CLIENT = 1487024799703109652
ROLE_MEMBER = 1487024877075697665

STAFF_ROLE_IDS = [ROLE_OWNER, ROLE_ADMIN, ROLE_MODERATOR]

# Guild ID
GUILD_ID = 1486984795014828064

# Commission
COMMISSION_PER_DOLLAR = 1

# ---------- OPERATIONAL CONTROLS ----------
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
        "commission_owed": {},
        "rates": {"I2C": 97, "C2I": 95},
        "custom_commands": {},
        "commission_wallet": "",
        "upi_slots": {},       # {user_id: {"1": "upi@bank", "2": ..., "3": ...}}
        "crypto_slots": {},    # {user_id: {"1": "addr", "2": ..., "3": ...}}
        "vouches": {},         # {user_id: count}
        "user_stats": {}       # {user_id: {"total_deals": 0, "total_value": 0.0}}
    }

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

def get_rates():
    data = load_data()
    return data.get("rates", {"I2C": 100, "C2I": 98})

# ---------- BOT SETUP ----------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix=".", intents=intents)

# ============================================================
# HELPER: IS STAFF
# ============================================================

def is_staff(member: discord.Member) -> bool:
    return any(r.id in STAFF_ROLE_IDS for r in member.roles)

def is_owner(member: discord.Member) -> bool:
    return any(r.id == ROLE_OWNER for r in member.roles)

def is_exchanger(member: discord.Member) -> bool:
    return any(r.id == ROLE_VERIFIED_EXCHANGER for r in member.roles)

# ============================================================
# HELPER: SAVE TRANSCRIPT
# ============================================================

async def save_transcript(guild: discord.Guild, channel: discord.TextChannel, ticket_id: str):
    """Saves a transcript of the ticket to the transactions channel"""
    transcript_channel = guild.get_channel(CHANNEL_TRANSACTIONS)
    if not transcript_channel:
        return

    messages = []
    async for msg in channel.history(limit=500, oldest_first=True):
        timestamp = msg.created_at.strftime("%d/%m/%Y %H:%M")
        content = msg.content or "[embed/attachment]"
        messages.append(f"[{timestamp}] {msg.author.display_name}: {content}")

    transcript_text = "\n".join(messages)
    buf = io.BytesIO(transcript_text.encode("utf-8"))
    file = discord.File(buf, filename=f"transcript-{ticket_id}.txt")

    embed = discord.Embed(
        title=f"📄 Transcript — {ticket_id}",
        description=f"Ticket closed at {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        color=discord.Color.blue()
    )
    embed.set_footer(text="NexChange — Transaction Records")
    await transcript_channel.send(embed=embed, file=file)

# ============================================================
# HELPER: UPDATE AVAILABLE EXCHANGERS CHANNEL
# ============================================================

async def update_available_exchangers_channel(guild, data):
    channel = guild.get_channel(CHANNEL_AVAILABLE_EXCHANGERS)
    if not channel:
        return

    rates = data.get("rates", {"I2C": 100, "C2I": 98})
    available = [(uid, ex) for uid, ex in data["exchangers"].items()
                 if ex.get("available") and ex.get("verified")]

    embed = discord.Embed(
        title="🟢 Available Exchangers",
        color=discord.Color.green()
    )
    embed.add_field(name="I2C Rate", value=f"₹{rates['I2C']}/$", inline=True)
    embed.add_field(name="C2I Rate", value=f"₹{rates['C2I']}/$", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    if not available:
        embed.description = "❌ No exchangers are currently available. Please check back later."
    else:
        embed.description = "These exchangers are currently online and ready to process deals."
        for uid, ex in available:
            embed.add_field(
                name=f"💎 {ex['name']}",
                value=f"Limit: **${ex['limit']}** | Deals: {ex.get('total_deals', 0)}",
                inline=True
            )

    embed.set_footer(text=f"Last updated: {datetime.now().strftime('%d/%m/%Y %H:%M')}")

    async for msg in channel.history(limit=10):
        await msg.delete()

    await channel.send(embed=embed)

# ============================================================
# HELPER: QR CODE
# ============================================================

def generate_qr_image(upi_string: str) -> discord.File:
    if not QR_AVAILABLE:
        raise ImportError("qrcode library not installed")
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(upi_string)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return discord.File(buf, filename="qr_code.png")

# ============================================================
# VIEWS
# ============================================================

class ExchangeTypeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="💰 INR 2 Crypto", style=discord.ButtonStyle.primary, custom_id="i2c_button")
    async def i2c_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not operation_status["I2C"]:
            await interaction.response.send_message("❌ INR to Crypto exchanges are currently closed.", ephemeral=True)
            return
        await interaction.response.send_modal(CreateTicketModal("I2C"))

    @discord.ui.button(label="💸 Crypto 2 INR", style=discord.ButtonStyle.success, custom_id="c2i_button")
    async def c2i_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not operation_status["C2I"]:
            await interaction.response.send_message("❌ Crypto to INR exchanges are currently closed.", ephemeral=True)
            return
        await interaction.response.send_modal(CreateTicketModal("C2I"))


class ClaimTicketView(discord.ui.View):
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

        # Check if exchanger has unpaid commission
        commission_owed = data["commission_owed"].get(user_id, 0)
        if exchanger.get("commission_blocked", False) and commission_owed > 0:
            await interaction.response.send_message(
                f"❌ You have unpaid commission of ₹{commission_owed:,.0f}. Use `/paycommission` to pay before continuing.",
                ephemeral=True
            )
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

        # Give exchanger send permissions in ticket
        exchanger_role = interaction.guild.get_role(ROLE_VERIFIED_EXCHANGER)
        if exchanger_role:
            await channel.set_permissions(interaction.user, read_messages=True, send_messages=True)

        rates = get_rates()
        exchange_type = self.ticket_data.get("type")
        amount = self.ticket_data.get("amount")
        client_id = self.ticket_data.get("client_id")

        embed = discord.Embed(title="🤝 Exchanger Claimed — Deal In Progress", color=discord.Color.blue())
        embed.add_field(name="Exchanger", value=interaction.user.mention, inline=True)
        embed.add_field(name="Client", value=f"<@{client_id}>", inline=True)
        embed.add_field(name="Deal Type", value=exchange_type, inline=True)
        embed.add_field(name="Amount", value=f"${amount}", inline=True)

        if exchange_type == "I2C":
            inr_amount = amount * rates["I2C"]
            embed.add_field(
                name="📋 Next Steps",
                value=f"**Exchanger:** Share your UPI ID in this ticket.\n**Client:** Send ₹{inr_amount:,} to the exchanger's UPI and upload screenshot.\n**Exchanger:** Verify payment received then release ${amount} USDT to client's wallet.\n\n⚠️ Never release based on screenshot alone — verify in your bank app.",
                inline=False
            )
        else:
            inr_amount = amount * rates["C2I"]
            embed.add_field(
                name="📋 Next Steps",
                value=f"**Client:** Share your crypto wallet address in this ticket.\n**Exchanger:** Send ${amount} USDT to client's wallet.\n**Client:** Confirm receipt then send ₹{inr_amount:,} to exchanger's UPI.\n**Exchanger:** Confirm INR received.\n\n⚠️ Never release funds based on screenshot alone.",
                inline=False
            )

        embed.set_footer(text="⚠️ Use /done or .done when the deal is complete.")
        await channel.send(embed=embed, view=DealControlView(self.ticket_data))
        await interaction.response.send_message("✅ You have claimed this ticket.", ephemeral=True)


class DealControlView(discord.ui.View):
    """Buttons shown after exchanger claims — Done, Cancel, Close"""
    def __init__(self, ticket_data):
        super().__init__(timeout=None)
        self.ticket_data = ticket_data

    @discord.ui.button(label="✅ Done", style=discord.ButtonStyle.success, custom_id="done_deal")
    async def done_deal(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        exchanger_id = self.ticket_data.get("exchanger_id")
        if user_id != exchanger_id and not is_staff(interaction.user):
            await interaction.response.send_message("❌ Only the assigned exchanger or staff can mark this done.", ephemeral=True)
            return
        await interaction.response.send_modal(CompleteDealModal(self.ticket_data, view=self))

    @discord.ui.button(label="❌ Cancel Deal", style=discord.ButtonStyle.danger, custom_id="cancel_deal_v2")
    async def cancel_deal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Only Moderators and above can cancel a deal.", ephemeral=True)
            return

        data = load_data()
        for deal in data["deals"]:
            if deal.get("ticket_id") == self.ticket_data.get("ticket_id"):
                deal["status"] = "cancelled"
                deal["cancelled_at"] = datetime.now().isoformat()
                deal["cancelled_by"] = str(interaction.user.id)
                break
        save_data(data)

        self.done_deal.disabled = True
        self.cancel_deal.disabled = True
        self.close_ticket.disabled = True
        await interaction.message.edit(view=self)

        embed = discord.Embed(
            title="❌ Deal Cancelled",
            description=f"Cancelled by {interaction.user.mention}. Transcript will be saved.",
            color=discord.Color.red()
        )
        await interaction.channel.send(embed=embed)
        await save_transcript(interaction.guild, interaction.channel, self.ticket_data.get("ticket_id", "unknown"))

        # Lock channel — don't delete
        await interaction.channel.set_permissions(interaction.guild.default_role, read_messages=False, send_messages=False)
        for role_id in [ROLE_OWNER, ROLE_ADMIN, ROLE_MODERATOR]:
            role = interaction.guild.get_role(role_id)
            if role:
                await interaction.channel.set_permissions(role, read_messages=True, send_messages=True)

        await interaction.response.send_message("✅ Deal cancelled. Ticket locked and transcript saved.", ephemeral=True)

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.secondary, custom_id="close_ticket_v2")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Only exchanger assigned to this ticket OR staff can close
        user_id = str(interaction.user.id)
        exchanger_id = self.ticket_data.get("exchanger_id")

        if user_id != exchanger_id and not is_staff(interaction.user):
            await interaction.response.send_message("❌ Only the assigned exchanger or staff can close this ticket.", ephemeral=True)
            return

        await save_transcript(interaction.guild, interaction.channel, self.ticket_data.get("ticket_id", "unknown"))

        self.done_deal.disabled = True
        self.cancel_deal.disabled = True
        self.close_ticket.disabled = True
        await interaction.message.edit(view=self)

        embed = discord.Embed(
            title="🔒 Ticket Closed",
            description=f"Closed by {interaction.user.mention}. Transcript saved to transactions channel.",
            color=discord.Color.greyple()
        )
        await interaction.channel.send(embed=embed)

        # Lock channel — don't delete
        await interaction.channel.set_permissions(interaction.guild.default_role, read_messages=False, send_messages=False)
        for role_id in [ROLE_OWNER, ROLE_ADMIN, ROLE_MODERATOR]:
            role = interaction.guild.get_role(role_id)
            if role:
                await interaction.channel.set_permissions(role, read_messages=True, send_messages=True)

        await interaction.response.send_message("✅ Ticket closed and transcript saved.", ephemeral=True)


class AvailabilityView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🟢 Go Online", style=discord.ButtonStyle.success, custom_id="go_online")
    async def go_online(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_data()
        user_id = str(interaction.user.id)
        if user_id not in data["exchangers"]:
            await interaction.response.send_message("❌ You are not a registered exchanger.", ephemeral=True)
            return
        # Block if commission unpaid
        if data["exchangers"][user_id].get("commission_blocked", False):
            owed = data["commission_owed"].get(user_id, 0)
            await interaction.response.send_message(f"❌ You have unpaid commission of ₹{owed:,.0f}. Pay via `/paycommission` before going online.", ephemeral=True)
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

# ============================================================
# MODALS
# ============================================================

class CreateTicketModal(discord.ui.Modal):
    def __init__(self, exchange_type):
        super().__init__(title=f"{'INR to Crypto' if exchange_type == 'I2C' else 'Crypto to INR'} Exchange")
        self.exchange_type = exchange_type

    amount = discord.ui.TextInput(
        label="Amount — ₹ for I2C | $ for C2I",
        placeholder="I2C: Enter INR e.g. 5000 | C2I: Enter USD e.g. 50",
        required=True,
        max_length=15
    )
    wallet = discord.ui.TextInput(
        label="Wallet Address (I2C) / UPI ID (C2I)",
        placeholder="Enter your USDT wallet address or UPI ID",
        required=True,
        max_length=200
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            raw_amount = float(self.amount.value)
            if raw_amount <= 0:
                await interaction.response.send_message("❌ Amount must be greater than 0.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("❌ Invalid amount. Numbers only.", ephemeral=True)
            return

        rates = get_rates()
        if self.exchange_type == "I2C":
            amount_inr = raw_amount
            amount_usd = round(raw_amount / rates["I2C"], 2)
        else:
            amount_usd = raw_amount
            amount_inr = round(raw_amount * rates["C2I"], 2)

        data = load_data()
        client_id = str(interaction.user.id)

        unclaimed = [d for d in data["deals"] if d.get("client_id") == client_id and d.get("status") == "open" and d.get("exchanger_id") is None]
        inprogress = [d for d in data["deals"] if d.get("client_id") == client_id and d.get("status") == "in_progress"]

        if len(unclaimed) >= 4:
            await interaction.response.send_message("❌ You already have **4 unclaimed tickets** open.", ephemeral=True)
            return
        if len(inprogress) >= 4:
            await interaction.response.send_message("❌ You already have **4 tickets in progress**.", ephemeral=True)
            return

        ticket_id = f"NX-{len(data['deals']) + 1001}"
        guild = interaction.guild
        category = interaction.channel.category

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }
        for role_id in [ROLE_MODERATOR, ROLE_ADMIN, ROLE_OWNER]:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        exchanger_role = guild.get_role(ROLE_VERIFIED_EXCHANGER)
        if exchanger_role:
            overwrites[exchanger_role] = discord.PermissionOverwrite(read_messages=True, send_messages=False)

        ticket_channel = await guild.create_text_channel(
            name=f"{self.exchange_type.lower()}-{ticket_id}",
            overwrites=overwrites,
            category=category
        )

        ticket_data = {
            "ticket_id": ticket_id,
            "type": self.exchange_type,
            "amount": amount_usd,
            "amount_inr": amount_inr,
            "amount_usd": amount_usd,
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
        embed.add_field(name="Status", value="🟡 Waiting for Exchanger", inline=True)

        if self.exchange_type == "I2C":
            embed.add_field(name="₹ Sending", value=f"₹{amount_inr:,.0f}", inline=True)
            embed.add_field(name="$ Receiving", value=f"${amount_usd:.2f} USDT", inline=True)
            embed.add_field(name="Rate", value=f"₹{rates['I2C']}/$", inline=True)
            embed.add_field(name="Client Wallet", value=f"||{self.wallet.value}||", inline=False)
        else:
            embed.add_field(name="$ Sending", value=f"${amount_usd:.2f} USDT", inline=True)
            embed.add_field(name="₹ Receiving", value=f"₹{amount_inr:,.0f}", inline=True)
            embed.add_field(name="Rate", value=f"₹{rates['C2I']}/$", inline=True)
            embed.add_field(name="Client UPI", value=f"||{self.wallet.value}||", inline=False)

        embed.set_footer(text="⚡ Verified exchangers can claim this ticket below.")

        online_exchanger_ids = [
            uid for uid, ex in data["exchangers"].items()
            if ex.get("available") and ex.get("verified")
        ]

        if online_exchanger_ids:
            mentions = " ".join(f"<@{uid}>" for uid in online_exchanger_ids)
            await ticket_channel.send(content=f"{mentions} — New {self.exchange_type} ticket!", embed=embed, view=ClaimTicketView(ticket_data))
        else:
            await ticket_channel.send(embed=embed, view=ClaimTicketView(ticket_data))

        await interaction.response.send_message(f"✅ Ticket created: {ticket_channel.mention}", ephemeral=True)


class CompleteDealModal(discord.ui.Modal, title="Complete Deal"):
    def __init__(self, ticket_data, view=None):
        super().__init__()
        self.ticket_data = ticket_data
        self.parent_view = view

    final_amount = discord.ui.TextInput(
        label="Final Completed USDT Amount ($)",
        placeholder="Enter actual completed USDT amount e.g. 50",
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
        client_id = self.ticket_data.get("client_id")
        ticket_id = self.ticket_data.get("ticket_id")
        exchange_type = self.ticket_data.get("type")
        commission = amount * COMMISSION_PER_DOLLAR

        # Update deal record
        for deal in data["deals"]:
            if deal.get("ticket_id") == ticket_id:
                deal["status"] = "completed"
                deal["final_amount"] = amount
                deal["commission"] = commission
                deal["completed_at"] = datetime.now().isoformat()
                break

        # Update commission owed
        if exchanger_id not in data["commission_owed"]:
            data["commission_owed"][exchanger_id] = 0
        data["commission_owed"][exchanger_id] += commission

        # Update exchanger deal count
        if exchanger_id in data["exchangers"]:
            data["exchangers"][exchanger_id]["total_deals"] = data["exchangers"][exchanger_id].get("total_deals", 0) + 1

        # Update client stats
        if "user_stats" not in data:
            data["user_stats"] = {}
        if client_id not in data["user_stats"]:
            data["user_stats"][client_id] = {"total_deals": 0, "total_value": 0.0}
        data["user_stats"][client_id]["total_deals"] += 1
        data["user_stats"][client_id]["total_value"] += amount

        save_data(data)

        guild = interaction.guild
        completed_channel = guild.get_channel(CHANNEL_COMPLETED_DEALS)
        logs_channel = guild.get_channel(CHANNEL_DEAL_LOGS)

        deal_embed = discord.Embed(title=f"✅ Deal Completed — {ticket_id}", color=discord.Color.green())
        deal_embed.add_field(name="Type", value=exchange_type, inline=True)
        deal_embed.add_field(name="Amount", value=f"${amount:.2f}", inline=True)
        deal_embed.add_field(name="Exchanger", value=f"<@{exchanger_id}>", inline=True)
        deal_embed.add_field(name="Client", value=f"<@{client_id}>", inline=True)
        deal_embed.add_field(name="Commission Owed", value=f"₹{commission:,.0f}", inline=True)
        deal_embed.add_field(name="Completed At", value=datetime.now().strftime("%d/%m/%Y %H:%M"), inline=True)
        deal_embed.set_footer(text="NexChange — Trusted Exchange Service")

        if completed_channel:
            await completed_channel.send(embed=deal_embed)
        if logs_channel:
            await logs_channel.send(embed=deal_embed)

        # Message 1 — Thanks
        await interaction.channel.send("✅ **Thanks for choosing us, deal done.**")

        # Message 2 — Vouch template for client to copy
        client_member = guild.get_member(int(client_id))
        exchanger_member = guild.get_member(int(exchanger_id)) if exchanger_id else None
        await interaction.channel.send(
            f"<@{client_id}> Please copy and paste this vouch in this channel to complete your review:\n\n"
            f"```[NexChange Vouch]\nExchanger: {exchanger_member.display_name if exchanger_member else 'N/A'}\n"
            f"Amount: ${amount:.2f} USDT\nType: {exchange_type}\nRating: ⭐⭐⭐⭐⭐\nComment: Fast and trusted!```"
        )

        # Message 3 — Client stats
        client_stats = data["user_stats"].get(client_id, {"total_deals": 0, "total_value": 0.0})
        total_deals = client_stats["total_deals"]
        total_value = client_stats["total_value"]
        avg_value = total_value / total_deals if total_deals > 0 else 0

        stats_embed = discord.Embed(title=f"📊 Client Stats — {client_member.display_name if client_member else 'Client'}", color=discord.Color.blue())
        stats_embed.add_field(name="Total Exchanges", value=str(total_deals), inline=True)
        stats_embed.add_field(name="Total Volume", value=f"${total_value:.2f}", inline=True)
        stats_embed.add_field(name="Average Deal Value", value=f"${avg_value:.2f}", inline=True)
        await interaction.channel.send(embed=stats_embed)

        # Message 4 — Ephemeral confirmation to exchanger
        await interaction.response.send_message(
            f"✅ Deal completed!\n\n**Commission logged:** ₹{commission:,.0f}\n\nTicket will now be closed. Transcript saved automatically.",
            ephemeral=True
        )

        # Disable parent view buttons
        if self.parent_view:
            for child in self.parent_view.children:
                child.disabled = True
            try:
                await self.parent_view.message.edit(view=self.parent_view)
            except Exception:
                pass

        # Save transcript and lock ticket (not delete)
        await asyncio.sleep(10)
        await save_transcript(guild, interaction.channel, ticket_id)

        await interaction.channel.set_permissions(guild.default_role, read_messages=False, send_messages=False)
        for role_id in [ROLE_OWNER, ROLE_ADMIN, ROLE_MODERATOR]:
            role = guild.get_role(role_id)
            if role:
                await interaction.channel.set_permissions(role, read_messages=True, send_messages=True)

        lock_embed = discord.Embed(
            title="🔒 Ticket Closed",
            description="This ticket has been closed after deal completion. Transcript saved.",
            color=discord.Color.greyple()
        )
        await interaction.channel.send(embed=lock_embed)


class RegisterExchangerModal(discord.ui.Modal, title="Exchanger Registration"):
    limit = discord.ui.TextInput(label="Your Exchange Limit in USD ($)", placeholder="e.g. 500", required=True, max_length=10)
    deposit_txn = discord.ui.TextInput(label="Deposit Transaction ID / UTR Number", placeholder="Paste your UPI UTR or transaction ID", required=True, max_length=200)
    shiba_username = discord.ui.TextInput(label="Your Shiba Server Username", placeholder="e.g. username#0000", required=True, max_length=100)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            limit = float(self.limit.value)
            if limit <= 0:
                await interaction.response.send_message("❌ Limit must be greater than 0.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("❌ Invalid limit amount.", ephemeral=True)
            return

        rates = get_rates()
        required_deposit_inr = limit * rates["I2C"] * 2
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
            "verified": False,
            "commission_blocked": False
        }
        save_data(data)

        guild = interaction.guild
        admin_role = guild.get_role(ROLE_ADMIN)
        embed = discord.Embed(title="📝 New Exchanger Application", color=discord.Color.orange())
        embed.add_field(name="Applicant", value=interaction.user.mention, inline=True)
        embed.add_field(name="Requested Limit", value=f"${limit}", inline=True)
        embed.add_field(name="Required Deposit", value=f"₹{required_deposit_inr:,.0f}", inline=True)
        embed.add_field(name="Deposit Txn ID", value=self.deposit_txn.value, inline=False)
        embed.add_field(name="Shiba Username", value=self.shiba_username.value, inline=False)
        embed.add_field(
            name="⚠️ Staff Action Required",
            value=f"Verify **{self.shiba_username.value}** has **20+ vouches** on Shiba.\nUse `/verify_exchanger` to approve or `/reject_exchanger` to reject.",
            inline=False
        )
        embed.set_footer(text="DO NOT approve without verifying 20 Shiba vouches.")

        logs_channel = guild.get_channel(CHANNEL_DEAL_LOGS)
        if logs_channel:
            await logs_channel.send(content=admin_role.mention if admin_role else "", embed=embed)

        await interaction.response.send_message(
            f"✅ Application submitted!\n\n**Required deposit:** ₹{required_deposit_inr:,.0f}\n\n⚠️ You need **20+ vouches** on Shiba or your application will be rejected.\n\nStaff will respond within 24 hours.",
            ephemeral=True
        )


class SetRateModal(discord.ui.Modal):
    def __init__(self, rate_type):
        super().__init__(title=f"Set {'INR to Crypto' if rate_type == 'I2C' else 'Crypto to INR'} Rate")
        self.rate_type = rate_type

    new_rate = discord.ui.TextInput(label="New Rate (INR per $1 USD)", placeholder="e.g. 97", required=True, max_length=10)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rate = float(self.new_rate.value)
            if rate <= 0:
                await interaction.response.send_message("❌ Rate must be greater than 0.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("❌ Invalid rate.", ephemeral=True)
            return

        data = load_data()
        if "rates" not in data:
            data["rates"] = {"I2C": 97, "C2I": 95}
        old_rate = data["rates"][self.rate_type]
        data["rates"][self.rate_type] = rate
        save_data(data)

        announcements = interaction.guild.get_channel(CHANNEL_ANNOUNCEMENTS)
        embed = discord.Embed(title=f"📢 Rate Updated — {'INR to Crypto' if self.rate_type == 'I2C' else 'Crypto to INR'}", color=discord.Color.orange())
        embed.add_field(name="Old Rate", value=f"₹{old_rate}/$", inline=True)
        embed.add_field(name="New Rate", value=f"₹{rate}/$", inline=True)
        embed.add_field(name="Updated By", value=interaction.user.mention, inline=True)
        if announcements:
            await announcements.send(embed=embed)
        await interaction.response.send_message(f"✅ {self.rate_type} rate updated to ₹{rate}/$ and announced.", ephemeral=True)


class EditEmbedModal(discord.ui.Modal, title="Edit Embed Content"):
    new_title = discord.ui.TextInput(label="New Title", placeholder="Leave blank to keep current", required=False, max_length=256)
    new_description = discord.ui.TextInput(label="New Description", placeholder="Leave blank to keep current", required=False, max_length=2000, style=discord.TextStyle.paragraph)
    new_footer = discord.ui.TextInput(label="New Footer", placeholder="Leave blank to keep current", required=False, max_length=200)
    message_id = discord.ui.TextInput(label="Message ID to edit", placeholder="Right click message → Copy ID", required=True, max_length=25)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            msg = await interaction.channel.fetch_message(int(self.message_id.value))
        except Exception:
            await interaction.response.send_message("❌ Message not found in this channel.", ephemeral=True)
            return

        if not msg.embeds:
            await interaction.response.send_message("❌ That message has no embed to edit.", ephemeral=True)
            return

        embed = msg.embeds[0]
        if self.new_title.value:
            embed.title = self.new_title.value
        if self.new_description.value:
            embed.description = self.new_description.value
        if self.new_footer.value:
            embed.set_footer(text=self.new_footer.value)

        try:
            await msg.edit(embed=embed)
            await interaction.response.send_message("✅ Embed updated successfully.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I can only edit my own messages.", ephemeral=True)


class AddCustomCommandModal(discord.ui.Modal, title="Add / Edit Custom Command"):
    cmd_name = discord.ui.TextInput(label="Command Name (without dot)", placeholder="e.g. greet", required=True, max_length=30)
    cmd_response = discord.ui.TextInput(label="Response", placeholder="What should the bot say?", required=True, max_length=1000, style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        name = self.cmd_name.value.strip().lower().replace(" ", "")
        response = self.cmd_response.value.strip()
        data = load_data()
        if "custom_commands" not in data:
            data["custom_commands"] = {}
        action = "Updated" if name in data["custom_commands"] else "Created"
        data["custom_commands"][name] = response
        save_data(data)
        await interaction.response.send_message(f"✅ {action} command `.{name}` successfully.", ephemeral=True)


class ManualTicketModal(discord.ui.Modal, title="Create Manual Ticket"):
    ticket_title = discord.ui.TextInput(label="Ticket Title", placeholder="e.g. Support Request", required=True, max_length=100)
    ticket_description = discord.ui.TextInput(label="Ticket Description", placeholder="Describe the purpose of this ticket", required=True, max_length=1000, style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        category = interaction.channel.category

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }
        for role_id in [ROLE_MODERATOR, ROLE_ADMIN, ROLE_OWNER]:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        ticket_channel = await guild.create_text_channel(
            name=f"ticket-{interaction.user.name[:10]}",
            overwrites=overwrites,
            category=category
        )

        embed = discord.Embed(title=self.ticket_title.value, description=self.ticket_description.value, color=discord.Color.blue())
        embed.add_field(name="Opened By", value=interaction.user.mention, inline=True)
        embed.add_field(name="Time", value=datetime.now().strftime("%d/%m/%Y %H:%M"), inline=True)
        embed.set_footer(text="NexChange — Staff will assist you shortly.")
        await ticket_channel.send(embed=embed)
        await interaction.response.send_message(f"✅ Ticket created: {ticket_channel.mention}", ephemeral=True)

# ============================================================
# SLASH COMMANDS
# ============================================================

# --- Setup ---

@bot.tree.command(name="setup_panel", description="Setup the main exchange panel [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def setup_panel(interaction: discord.Interaction):
    rates = get_rates()
    embed = discord.Embed(title="💱 NEXCHANGE EXCHANGE PANEL", description="Welcome to NexChange — India's most trusted P2P crypto exchange.\n\nSelect your exchange type below to get started.", color=discord.Color.blue())
    embed.add_field(name="💰 INR to Crypto (I2C)", value=f"Rate: ₹{rates['I2C']}/$\nEnter ₹ amount — receive USDT", inline=True)
    embed.add_field(name="💸 Crypto to INR (C2I)", value=f"Rate: ₹{rates['C2I']}/$\nEnter $ amount — receive INR", inline=True)
    embed.add_field(name="ℹ️ Important", value="• Fixed rates, no negotiation\n• Read #tos before proceeding\n•  All deals protected", inline=False)
    embed.set_footer(text="NexChange — Fast. Safe. Trusted.")
    await interaction.channel.send(embed=embed, view=ExchangeTypeView())
    await interaction.response.send_message("✅ Exchange panel setup complete.", ephemeral=True)


@bot.tree.command(name="setup_availability", description="Setup exchanger availability panel [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def setup_availability(interaction: discord.Interaction):
    embed = discord.Embed(title="🔄 Exchanger Availability", description="Toggle your availability status here.\n\n⚠️ Always go offline when you are not available. Failure to do so will result in a penalty.", color=discord.Color.blue())
    await interaction.channel.send(embed=embed, view=AvailabilityView())
    await interaction.response.send_message("✅ Availability panel setup complete.", ephemeral=True)


@bot.tree.command(name="create_ticket_panel", description="Create a manual ticket panel [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
@app_commands.describe(title="Panel title", description="Panel description")
async def create_ticket_panel(interaction: discord.Interaction, title: str = "📬 Open a Ticket", description: str = "Click the button below to open a support ticket."):
    embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
    embed.set_footer(text="NexChange Support")

    class OpenTicketView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(label="📬 Open Ticket", style=discord.ButtonStyle.primary, custom_id="open_manual_ticket")
        async def open_ticket(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
            await btn_interaction.response.send_modal(ManualTicketModal())

    await interaction.channel.send(embed=embed, view=OpenTicketView())
    await interaction.response.send_message("✅ Ticket panel created.", ephemeral=True)


# --- Done command (slash + prefix handled in on_message) ---

@bot.tree.command(name="done", description="Mark a deal as complete [Exchanger only]")
async def done_slash(interaction: discord.Interaction):
    data = load_data()
    user_id = str(interaction.user.id)
    channel_id = str(interaction.channel.id)

    ticket = next((d for d in data["deals"] if d.get("channel_id") == channel_id and d.get("status") == "in_progress"), None)

    if not ticket:
        await interaction.response.send_message("❌ No active deal found in this ticket.", ephemeral=True)
        return

    if ticket.get("exchanger_id") != user_id and not is_staff(interaction.user):
        await interaction.response.send_message("❌ Only the assigned exchanger or staff can mark this done.", ephemeral=True)
        return

    await interaction.response.send_modal(CompleteDealModal(ticket))


# --- Exchanger management ---

@bot.tree.command(name="apply_exchanger", description="Apply to become a verified exchanger")
async def apply_exchanger(interaction: discord.Interaction):
    if not operation_status["accepting_exchangers"]:
        await interaction.response.send_message("❌ Exchanger applications are currently closed.", ephemeral=True)
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
    embed = discord.Embed(title="✅ Exchanger Verified", description=f"{user.mention} has been verified.", color=discord.Color.green())
    embed.add_field(name="Limit", value=f"${data['exchangers'][user_id]['limit']}", inline=True)
    await interaction.response.send_message(embed=embed)
    await user.send(f"✅ You have been verified as an exchanger on **NexChange**!\n\nYour limit: **${data['exchangers'][user_id]['limit']}**\n\nToggle availability to start receiving deals.")


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


@bot.tree.command(name="update_limit", description="Update an exchanger's limit [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
@app_commands.describe(user="The exchanger", new_limit="New limit in USD")
async def update_limit(interaction: discord.Interaction, user: discord.Member, new_limit: float):
    data = load_data()
    user_id = str(user.id)
    if user_id not in data["exchangers"]:
        await interaction.response.send_message("❌ Not a registered exchanger.", ephemeral=True)
        return
    old_limit = data["exchangers"][user_id]["limit"]
    data["exchangers"][user_id]["limit"] = new_limit
    save_data(data)
    await update_available_exchangers_channel(interaction.guild, data)
    await interaction.response.send_message(f"✅ Limit updated for {user.mention}: ${old_limit} → ${new_limit}", ephemeral=True)
    await user.send(f"📢 Your exchange limit on **NexChange** has been updated to **${new_limit}**.")


# --- Penalty ---

@bot.tree.command(name="add_penalty", description="Add a penalty to an exchanger [Staff only]")
@app_commands.checks.has_any_role("Owner", "Admin", "Moderator")
@app_commands.describe(user="The exchanger", amount="Penalty amount in INR", reason="Reason for penalty")
async def add_penalty(interaction: discord.Interaction, user: discord.Member, amount: int, reason: str):
    data = load_data()
    user_id = str(user.id)
    if user_id not in data["penalties"]:
        data["penalties"][user_id] = []
    data["penalties"][user_id].append({"amount": amount, "reason": reason, "paid": False, "date": datetime.now().isoformat(), "issued_by": str(interaction.user.id)})
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
    await user.send(f"⚠️ You have received a penalty of **₹{amount}** on NexChange.\n\nReason: {reason}\n\nContact staff to settle.")
    await interaction.response.send_message(f"✅ Penalty of ₹{amount} issued to {user.mention}.", ephemeral=True)


@bot.tree.command(name="pay_penalty", description="Mark an exchanger's penalty as paid [Staff only]")
@app_commands.checks.has_any_role("Owner", "Admin", "Moderator")
@app_commands.describe(user="The exchanger who paid")
async def pay_penalty(interaction: discord.Interaction, user: discord.Member):
    data = load_data()
    user_id = str(user.id)
    if user_id not in data["penalties"] or not data["penalties"][user_id]:
        await interaction.response.send_message("❌ No penalties found.", ephemeral=True)
        return
    unpaid = [p for p in data["penalties"][user_id] if not p["paid"]]
    if not unpaid:
        await interaction.response.send_message("✅ No unpaid penalties.", ephemeral=True)
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
    await user.send(f"✅ Your penalty of **₹{total}** has been marked as paid. You can now resume trading.")
    await interaction.response.send_message(f"✅ Penalties cleared for {user.mention}. Role restored.", ephemeral=True)


# --- Commission ---

@bot.tree.command(name="commission_status", description="Check commission owed [Staff only]")
@app_commands.checks.has_any_role("Owner", "Admin", "Moderator")
@app_commands.describe(user="The exchanger to check")
async def commission_status(interaction: discord.Interaction, user: discord.Member):
    data = load_data()
    owed = data["commission_owed"].get(str(user.id), 0)
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
    if user_id in data["exchangers"]:
        data["exchangers"][user_id]["commission_blocked"] = False
    save_data(data)
    role = interaction.guild.get_role(ROLE_VERIFIED_EXCHANGER)
    if role and role not in user.roles:
        await user.add_roles(role)
    await user.send(f"✅ Your commission of **₹{owed:,.0f}** has been cleared. You can now resume trading on NexChange.")
    await interaction.response.send_message(f"✅ Commission of ₹{owed:,.0f} cleared for {user.mention}. Role restored.", ephemeral=True)


@bot.tree.command(name="paycommission", description="View crypto wallet to pay commission / set wallet address")
@app_commands.describe(address="[Owner only] Set commission payment wallet address")
async def paycommission(interaction: discord.Interaction, address: str = None):
    data = load_data()

    # Setting address — owner only
    if address:
        if not is_owner(interaction.user):
            await interaction.response.send_message("❌ Only the Owner can set the commission wallet address.", ephemeral=True)
            return
        data["commission_wallet"] = address
        save_data(data)
        await interaction.response.send_message(f"✅ Commission wallet address set to:\n`{address}`", ephemeral=True)
        return

    # Viewing — exchanger pays
    user_id = str(interaction.user.id)
    wallet = data.get("commission_wallet", "")
    owed = data["commission_owed"].get(user_id, 0)

    if not wallet:
        await interaction.response.send_message("❌ Commission wallet address not configured yet. Contact the owner.", ephemeral=True)
        return

    embed = discord.Embed(title="💰 Pay Your Commission", color=discord.Color.gold())
    embed.add_field(name="Commission Owed", value=f"₹{owed:,.0f}", inline=True)
    embed.add_field(name="Due Every", value="Saturday 7:00 PM IST", inline=True)
    embed.add_field(name="Payment Wallet (USDT TRC20)", value=f"```{wallet}```", inline=False)
    embed.add_field(name="After Payment", value="Send transaction proof to a staff member to get cleared.", inline=False)
    embed.set_footer(text="Failure to pay by Saturday 7 PM IST will result in trading suspension.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- Stats & Info ---

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
    embed.add_field(name="Commission Blocked", value="🔴 Yes" if ex.get("commission_blocked") else "🟢 No", inline=True)
    embed.add_field(name="Pending Penalties", value=f"{len(penalties)} penalty(s)" if penalties else "None", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="stats", description="View server exchange statistics [Staff only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def stats(interaction: discord.Interaction):
    data = load_data()
    completed = [d for d in data["deals"] if d.get("status") == "completed"]
    total_volume = sum(d.get("final_amount", 0) for d in completed)
    total_commission = sum(d.get("commission", 0) for d in completed)
    embed = discord.Embed(title="📊 NexChange Statistics", color=discord.Color.gold())
    embed.add_field(name="Total Deals", value=len(completed), inline=True)
    embed.add_field(name="Total Volume", value=f"${total_volume:,.2f}", inline=True)
    embed.add_field(name="Total Commission", value=f"₹{total_commission:,.0f}", inline=True)
    embed.add_field(name="Total Exchangers", value=len(data["exchangers"]), inline=True)
    embed.add_field(name="Verified Exchangers", value=len([e for e in data["exchangers"].values() if e.get("verified")]), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- Rates ---

@bot.tree.command(name="set_i2c_rate", description="Change INR to Crypto rate [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def set_i2c_rate(interaction: discord.Interaction):
    await interaction.response.send_modal(SetRateModal("I2C"))


@bot.tree.command(name="set_c2i_rate", description="Change Crypto to INR rate [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def set_c2i_rate(interaction: discord.Interaction):
    await interaction.response.send_modal(SetRateModal("C2I"))


@bot.tree.command(name="current_rates", description="View current exchange rates")
async def current_rates(interaction: discord.Interaction):
    rates = get_rates()
    embed = discord.Embed(title="💱 Current Exchange Rates", color=discord.Color.blue())
    embed.add_field(name="💰 INR to Crypto (I2C)", value=f"₹{rates['I2C']} per $1 USD", inline=True)
    embed.add_field(name="💸 Crypto to INR (C2I)", value=f"₹{rates['C2I']} per $1 USD", inline=True)
    await interaction.response.send_message(embed=embed)


# --- Edit Embed ---

@bot.tree.command(name="edit_embed", description="Edit an embed message [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def edit_embed(interaction: discord.Interaction):
    await interaction.response.send_modal(EditEmbedModal())


# --- Custom Commands ---

@bot.tree.command(name="customcommand", description="Add, edit or remove custom dot commands [Staff only]")
@app_commands.checks.has_any_role("Owner", "Admin", "Moderator")
@app_commands.describe(action="add / remove / list")
async def customcommand(interaction: discord.Interaction, action: str):
    action = action.lower().strip()
    data = load_data()
    if "custom_commands" not in data:
        data["custom_commands"] = {}

    if action == "add":
        await interaction.response.send_modal(AddCustomCommandModal())

    elif action == "remove":
        if not data["custom_commands"]:
            await interaction.response.send_message("❌ No custom commands exist.", ephemeral=True)
            return
        options = [discord.SelectOption(label=f".{name}", value=name) for name in list(data["custom_commands"].keys())[:25]]

        class RemoveSelect(discord.ui.Select):
            def __init__(self):
                super().__init__(placeholder="Select a command to remove...", options=options)
            async def callback(self, sel_interaction: discord.Interaction):
                d = load_data()
                chosen = self.values[0]
                if chosen in d.get("custom_commands", {}):
                    del d["custom_commands"][chosen]
                    save_data(d)
                    await sel_interaction.response.send_message(f"✅ Command `.{chosen}` removed.", ephemeral=True)
                else:
                    await sel_interaction.response.send_message("❌ Command not found.", ephemeral=True)

        class RemoveView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=30)
                self.add_item(RemoveSelect())

        await interaction.response.send_message("Select a command to remove:", view=RemoveView(), ephemeral=True)

    elif action == "list":
        cmds = data.get("custom_commands", {})
        if not cmds:
            await interaction.response.send_message("📋 No custom commands yet.", ephemeral=True)
            return
        embed = discord.Embed(title="📋 Custom Commands", color=discord.Color.blue())
        for name, response in cmds.items():
            preview = response[:80] + "..." if len(response) > 80 else response
            embed.add_field(name=f".{name}", value=preview, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("❌ Invalid action. Use `add`, `remove`, or `list`.", ephemeral=True)


# --- Operational Controls ---

@bot.tree.command(name="startallexchanges", description="Open ALL exchanges [Admin/Owner]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def startallexchanges(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    operation_status["I2C"] = True
    operation_status["C2I"] = True
    operation_status["accepting_exchangers"] = True
    rates = get_rates()
    announcements = interaction.guild.get_channel(CHANNEL_ANNOUNCEMENTS)
    embed = discord.Embed(title="✅ NEXCHANGE — ALL OPERATIONS RESUMED", description=f"All operations are back online.\nHead to <#{CHANNEL_OPEN_TICKET}> to start.", color=discord.Color.green())
    embed.add_field(name="I2C Rate", value=f"₹{rates['I2C']}/$", inline=True)
    embed.add_field(name="C2I Rate", value=f"₹{rates['C2I']}/$", inline=True)
    embed.set_footer(text="NexChange — Fast. Safe. Trusted.")
    if announcements:
        await announcements.send(embed=embed)
    await interaction.followup.send("✅ All operations resumed.", ephemeral=True)


@bot.tree.command(name="stopallexchanges", description="Stop ALL exchanges [Admin/Owner]")
@app_commands.checks.has_any_role("Owner", "Admin")
@app_commands.describe(reason="Reason for stopping")
async def stopallexchanges(interaction: discord.Interaction, reason: str = "Maintenance"):
    await interaction.response.defer(ephemeral=True)
    operation_status["I2C"] = False
    operation_status["C2I"] = False
    operation_status["accepting_exchangers"] = False
    announcements = interaction.guild.get_channel(CHANNEL_ANNOUNCEMENTS)
    embed = discord.Embed(title="🚨 NEXCHANGE — ALL OPERATIONS SUSPENDED", description=f"All operations suspended.\n\n**Reason:** {reason}\n\nAll pending deals will be honoured.", color=discord.Color.dark_red())
    embed.set_footer(text="NexChange Management will update you shortly.")
    if announcements:
        await announcements.send(embed=embed)
    await interaction.followup.send("🚨 All operations stopped.", ephemeral=True)


@bot.tree.command(name="start_i2c", description="Open INR to Crypto exchanges [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def start_i2c(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    operation_status["I2C"] = True
    rates = get_rates()
    announcements = interaction.guild.get_channel(CHANNEL_ANNOUNCEMENTS)
    embed = discord.Embed(title="✅ INR to Crypto — NOW OPEN", description=f"I2C exchanges now open.\nHead to <#{CHANNEL_OPEN_TICKET}>", color=discord.Color.green())
    embed.add_field(name="Rate", value=f"₹{rates['I2C']}/$", inline=True)
    if announcements:
        await announcements.send(embed=embed)
    await interaction.followup.send("✅ I2C open.", ephemeral=True)


@bot.tree.command(name="stop_i2c", description="Close INR to Crypto exchanges [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
@app_commands.describe(reason="Reason")
async def stop_i2c(interaction: discord.Interaction, reason: str = "Temporarily unavailable"):
    await interaction.response.defer(ephemeral=True)
    operation_status["I2C"] = False
    announcements = interaction.guild.get_channel(CHANNEL_ANNOUNCEMENTS)
    embed = discord.Embed(title="🔴 INR to Crypto — CLOSED", description=f"I2C closed.\n\n**Reason:** {reason}", color=discord.Color.red())
    if announcements:
        await announcements.send(embed=embed)
    await interaction.followup.send("✅ I2C closed.", ephemeral=True)


@bot.tree.command(name="start_c2i", description="Open Crypto to INR exchanges [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def start_c2i(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    operation_status["C2I"] = True
    rates = get_rates()
    announcements = interaction.guild.get_channel(CHANNEL_ANNOUNCEMENTS)
    embed = discord.Embed(title="✅ Crypto to INR — NOW OPEN", description=f"C2I exchanges now open.\nHead to <#{CHANNEL_OPEN_TICKET}>", color=discord.Color.green())
    embed.add_field(name="Rate", value=f"₹{rates['C2I']}/$", inline=True)
    if announcements:
        await announcements.send(embed=embed)
    await interaction.followup.send("✅ C2I open.", ephemeral=True)


@bot.tree.command(name="stop_c2i", description="Close Crypto to INR exchanges [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
@app_commands.describe(reason="Reason")
async def stop_c2i(interaction: discord.Interaction, reason: str = "Temporarily unavailable"):
    await interaction.response.defer(ephemeral=True)
    operation_status["C2I"] = False
    announcements = interaction.guild.get_channel(CHANNEL_ANNOUNCEMENTS)
    embed = discord.Embed(title="🔴 Crypto to INR — CLOSED", description=f"C2I closed.\n\n**Reason:** {reason}", color=discord.Color.red())
    if announcements:
        await announcements.send(embed=embed)
    await interaction.followup.send("✅ C2I closed.", ephemeral=True)


@bot.tree.command(name="server_status", description="Check current server operation status [Staff only]")
@app_commands.checks.has_any_role("Owner", "Admin", "Moderator")
async def server_status(interaction: discord.Interaction):
    rates = get_rates()
    embed = discord.Embed(title="📊 NexChange Operation Status", color=discord.Color.blue())
    embed.add_field(name="I2C", value="🟢 Open" if operation_status["I2C"] else "🔴 Closed", inline=True)
    embed.add_field(name="C2I", value="🟢 Open" if operation_status["C2I"] else "🔴 Closed", inline=True)
    embed.add_field(name="Applications", value="🟢 Open" if operation_status["accepting_exchangers"] else "🔴 Closed", inline=True)
    embed.add_field(name="I2C Rate", value=f"₹{rates['I2C']}/$", inline=True)
    embed.add_field(name="C2I Rate", value=f"₹{rates['C2I']}/$", inline=True)
    embed.set_footer(text=f"Checked at {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ============================================================
# PREFIX COMMANDS (dot commands)
# ============================================================

@bot.command(name="done")
async def done_prefix(ctx):
    """Prefix version of /done"""
    data = load_data()
    channel_id = str(ctx.channel.id)
    user_id = str(ctx.author.id)

    ticket = next((d for d in data["deals"] if d.get("channel_id") == channel_id and d.get("status") == "in_progress"), None)

    if not ticket:
        await ctx.reply("❌ No active deal found in this ticket.")
        return

    if ticket.get("exchanger_id") != user_id and not is_staff(ctx.author):
        await ctx.reply("❌ Only the assigned exchanger or staff can mark this done.")
        return

    await ctx.reply("✅ Please use the **✅ Done** button in the ticket or use `/done` command with the amount.")


@bot.command(name="startallexchanges")
async def prefix_startall(ctx):
    if not is_staff(ctx.author):
        await ctx.reply("❌ Staff only.")
        return
    operation_status["I2C"] = True
    operation_status["C2I"] = True
    operation_status["accepting_exchangers"] = True
    await ctx.reply("✅ All exchanges started.")


@bot.command(name="stopallexchanges")
async def prefix_stopall(ctx):
    if not is_staff(ctx.author):
        await ctx.reply("❌ Staff only.")
        return
    operation_status["I2C"] = False
    operation_status["C2I"] = False
    operation_status["accepting_exchangers"] = False
    await ctx.reply("🚨 All exchanges stopped.")


@bot.command(name="paycommission")
async def prefix_paycommission(ctx):
    data = load_data()
    user_id = str(ctx.author.id)
    wallet = data.get("commission_wallet", "")
    owed = data["commission_owed"].get(user_id, 0)
    if not wallet:
        await ctx.reply("❌ Commission wallet not configured yet.")
        return
    embed = discord.Embed(title="💰 Pay Your Commission", color=discord.Color.gold())
    embed.add_field(name="Commission Owed", value=f"₹{owed:,.0f}", inline=True)
    embed.add_field(name="Due Every", value="Saturday 7:00 PM IST", inline=True)
    embed.add_field(name="Payment Wallet (USDT TRC20)", value=f"```{wallet}```", inline=False)
    embed.set_footer(text="Send proof to staff after paying.")
    await ctx.reply(embed=embed)


@bot.command(name="rates")
async def prefix_rates(ctx):
    rates = get_rates()
    embed = discord.Embed(title="💱 Current Exchange Rates", color=discord.Color.blue())
    embed.add_field(name="💰 I2C", value=f"₹{rates['I2C']}/$", inline=True)
    embed.add_field(name="💸 C2I", value=f"₹{rates['C2I']}/$", inline=True)
    await ctx.reply(embed=embed)


@bot.command(name="stats")
async def prefix_stats(ctx):
    if not is_staff(ctx.author):
        await ctx.reply("❌ Staff only.")
        return
    data = load_data()
    completed = [d for d in data["deals"] if d.get("status") == "completed"]
    total_volume = sum(d.get("final_amount", 0) for d in completed)
    total_commission = sum(d.get("commission", 0) for d in completed)
    embed = discord.Embed(title="📊 NexChange Statistics", color=discord.Color.gold())
    embed.add_field(name="Total Deals", value=len(completed), inline=True)
    embed.add_field(name="Total Volume", value=f"${total_volume:,.2f}", inline=True)
    embed.add_field(name="Total Commission", value=f"₹{total_commission:,.0f}", inline=True)
    await ctx.reply(embed=embed)


# ============================================================
# MESSAGE HANDLER — UPI slots, crypto slots, calc, QR, custom
# ============================================================

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    content = message.content.strip()
    user_id = str(message.author.id)
    data = load_data()

    # ---- UPI Slots: .upi1, .upi2, .upi3 ----
    upi_match = re.match(r"^\.upi([123])(?:\s+(.+))?$", content, re.IGNORECASE)
    if upi_match:
        slot = upi_match.group(1)
        value = upi_match.group(2)

        if "upi_slots" not in data:
            data["upi_slots"] = {}
        if user_id not in data["upi_slots"]:
            data["upi_slots"][user_id] = {}

        if value:
            # Setting slot
            data["upi_slots"][user_id][slot] = value.strip()
            save_data(data)
            await message.reply(f"✅ UPI Slot {slot} set to: `{value.strip()}`")
        else:
            # Displaying slot
            upi = data["upi_slots"].get(user_id, {}).get(slot)
            if upi:
                embed = discord.Embed(title=f"💳 UPI Slot {slot}", color=discord.Color.green())
                embed.add_field(name="UPI ID", value=f"`{upi}`", inline=False)
                embed.set_footer(text=f"Shared by {message.author.display_name}")
                await message.reply(embed=embed)
            else:
                await message.reply(f"❌ UPI Slot {slot} is empty. Set it with `.upi{slot} your@upi`")
        return

    # ---- Crypto Slots: .crypto1, .crypto2, .crypto3 ----
    crypto_match = re.match(r"^\.crypto([123])(?:\s+(.+))?$", content, re.IGNORECASE)
    if crypto_match:
        slot = crypto_match.group(1)
        value = crypto_match.group(2)

        if "crypto_slots" not in data:
            data["crypto_slots"] = {}
        if user_id not in data["crypto_slots"]:
            data["crypto_slots"][user_id] = {}

        if value:
            data["crypto_slots"][user_id][slot] = value.strip()
            save_data(data)
            await message.reply(f"✅ Crypto Slot {slot} set to: `{value.strip()}`")
        else:
            addr = data["crypto_slots"].get(user_id, {}).get(slot)
            if addr:
                embed = discord.Embed(title=f"🔑 Crypto Slot {slot}", color=discord.Color.blue())
                embed.add_field(name="Address", value=f"`{addr}`", inline=False)
                embed.set_footer(text=f"Shared by {message.author.display_name}")
                await message.reply(embed=embed)
            else:
                await message.reply(f"❌ Crypto Slot {slot} is empty. Set it with `.crypto{slot} your_address`")
        return

    # ---- .i2c<amount> — INR in, USDT out ----
    dot_i2c = re.match(r"^\.i2c(\d+(\.\d+)?)$", content, re.IGNORECASE)
    dot_c2i = re.match(r"^\.c2i(\d+(\.\d+)?)$", content, re.IGNORECASE)

    if dot_i2c or dot_c2i:
        exchanger_role = message.guild.get_role(ROLE_VERIFIED_EXCHANGER) if message.guild else None
        if exchanger_role and exchanger_role not in message.author.roles and not is_staff(message.author):
            await message.reply("❌ Only verified exchangers can use dot calculation commands.")
            return

        rates = get_rates()

        if dot_i2c:
            inr_amount = float(dot_i2c.group(1))
            usd_amount = inr_amount / rates["I2C"]
            exchange_type = "I2C"
            rate = rates["I2C"]
            color = discord.Color.blue()
            title = "💰 INR to Crypto — Deal Summary"
            description = f"Client sends **₹{inr_amount:,.0f}** → Exchanger releases **${usd_amount:.2f} USDT**"
        else:
            usd_amount = float(dot_c2i.group(1))
            inr_amount = usd_amount * rates["C2I"]
            exchange_type = "C2I"
            rate = rates["C2I"]
            color = discord.Color.green()
            title = "💸 Crypto to INR — Deal Summary"
            description = f"Client sends **${usd_amount:.2f} USDT** → Exchanger releases **₹{inr_amount:,.0f}**"

        commission = usd_amount * COMMISSION_PER_DOLLAR
        embed = discord.Embed(title=title, description=description, color=color)
        embed.add_field(name="Type", value=exchange_type, inline=True)
        embed.add_field(name="Rate", value=f"₹{rate}/$", inline=True)
        embed.add_field(name="USD Amount", value=f"${usd_amount:.2f}", inline=True)
        embed.add_field(name="INR Amount", value=f"₹{inr_amount:,.0f}", inline=True)
        embed.add_field(name="Commission", value=f"₹{commission:.0f}", inline=True)
        embed.set_footer(text=f"Calculated by {message.author.display_name} • NexChange")
        await message.reply(embed=embed)
        return

    # ---- .makeqr — Generate QR code for a UPI ID ----
    if content.lower() == ".makeqr":
        if not message.reference:
            await message.reply("❌ Reply to a message containing a UPI ID and then use `.makeqr`.")
            return
        try:
            ref_msg = await message.channel.fetch_message(message.reference.message_id)
        except Exception:
            await message.reply("❌ Could not fetch the referenced message.")
            return

        upi_text = ref_msg.content.strip().replace("||", "")
        upi_match_qr = re.search(r"[\w.\-]+@[\w]+", upi_text)
        if not upi_match_qr:
            await message.reply("❌ No UPI ID found. It should look like `name@upi`.")
            return

        upi_id = upi_match_qr.group(0)
        upi_link = f"upi://pay?pa={upi_id}&cu=INR"

        try:
            qr_file = generate_qr_image(upi_link)
            embed = discord.Embed(title="📱 UPI QR Code", description=f"Scan to pay\n\n**UPI ID:** `{upi_id}`", color=discord.Color.green())
            embed.set_image(url="attachment://qr_code.png")
            embed.set_footer(text="Generated by NexChange Bot")
            await message.reply(embed=embed, file=qr_file)
        except Exception as e:
            await message.reply(f"❌ Failed to generate QR: {e}")
        return

    # ---- Custom dot commands ----
    if content.startswith("."):
        cmd_name = content[1:].strip().lower().split()[0]
        custom_commands = data.get("custom_commands", {})
        if cmd_name in custom_commands:
            try:
                await message.delete()
            except Exception:
                pass
            await message.channel.send(custom_commands[cmd_name])
            return

    await bot.process_commands(message)

# ============================================================
# SCHEDULED TASKS
# ============================================================

@tasks.loop(hours=1)
async def saturday_commission_reminder():
    """Every Saturday at 7 PM IST (13:30 UTC) block unpaid exchangers and send reminder"""
    now = datetime.utcnow()
    # Saturday = weekday 5, 13:30 UTC = 7 PM IST
    if now.weekday() == 5 and now.hour == 13 and now.minute < 60:
        data = load_data()
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return

        channel = guild.get_channel(CHANNEL_WEEKLY_COMMISSION)
        wallet = data.get("commission_wallet", "Not configured")

        embed = discord.Embed(
            title="📋 Weekly Commission Due — Saturday 7 PM IST",
            description=f"All exchangers must pay their commission now.\n\n**Payment Wallet (USDT TRC20):**\n```{wallet}```",
            color=discord.Color.gold()
        )

        total_commission = 0
        blocked_count = 0

        for user_id, amount in data["commission_owed"].items():
            if amount > 0:
                member = guild.get_member(int(user_id))
                name = member.display_name if member else f"User {user_id}"
                embed.add_field(name=name, value=f"₹{amount:,.0f} owed", inline=True)
                total_commission += amount

                # Block exchanger
                if user_id in data["exchangers"]:
                    data["exchangers"][user_id]["commission_blocked"] = True
                    data["exchangers"][user_id]["available"] = False
                    if member:
                        role = guild.get_role(ROLE_VERIFIED_EXCHANGER)
                        if role and role in member.roles:
                            await member.remove_roles(role)
                        try:
                            await member.send(
                                f"⚠️ Your commission of **₹{amount:,.0f}** is due.\n\n"
                                f"**Payment Wallet:** `{wallet}`\n\n"
                                f"You have been suspended from trading until payment is confirmed by staff.\n"
                                f"Use `/paycommission` or `.paycommission` for details."
                            )
                        except Exception:
                            pass
                    blocked_count += 1

        embed.add_field(name="Total Due", value=f"₹{total_commission:,.0f}", inline=False)
        embed.add_field(name="Exchangers Blocked", value=str(blocked_count), inline=True)
        embed.set_footer(text="Pay and send proof to staff to get unblocked.")

        save_data(data)

        if channel:
            await channel.send(embed=embed)


@tasks.loop(hours=1)
async def weekly_commission_report():
    """Sends weekly commission summary every Sunday 9 AM IST"""
    now = datetime.utcnow()
    if now.weekday() == 6 and now.hour == 3 and now.minute < 60:  # 3:30 UTC = 9 AM IST
        data = load_data()
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return
        channel = guild.get_channel(CHANNEL_WEEKLY_COMMISSION)
        if not channel:
            return
        embed = discord.Embed(title="📋 Weekly Commission Report — Sunday Summary", description=f"Week ending {datetime.now().strftime('%d/%m/%Y')}", color=discord.Color.gold())
        total_commission = 0
        for user_id, amount in data["commission_owed"].items():
            if amount > 0:
                member = guild.get_member(int(user_id))
                name = member.display_name if member else f"User {user_id}"
                embed.add_field(name=name, value=f"₹{amount:,.0f} owed", inline=True)
                total_commission += amount
        embed.add_field(name="Total Commission Due", value=f"₹{total_commission:,.0f}", inline=False)
        embed.set_footer(text="Commissions should have been paid by Saturday 7 PM IST.")
        await channel.send(embed=embed)

# ============================================================
# BOT EVENTS
# ============================================================

@bot.event
async def on_ready():
    print(f"✅ NexChange Bot is online as {bot.user}")
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"✅ Synced {len(synced)} commands to guild instantly")
    except Exception as e:
        print(f"❌ Error syncing commands: {e}")

    saturday_commission_reminder.start()
    weekly_commission_report.start()
    bot.add_view(ExchangeTypeView())
    bot.add_view(AvailabilityView())
    print("✅ All persistent views registered")

# ============================================================
# RUN
# ============================================================
bot.run(BOT_TOKEN)
