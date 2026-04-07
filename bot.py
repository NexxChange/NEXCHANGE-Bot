import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
from datetime import datetime
import asyncio
import re
import qrcode
import io
import pytz

# ============================================================
# NEXCHANGE BOT — FULL CODE
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

# ---------- CHANNEL IDs ----------
CHANNEL_OPEN_TICKET         = 0
CHANNEL_COMPLETED_DEALS     = 0
CHANNEL_REVIEWS             = 0
CHANNEL_PENALTY_BOARD       = 0
CHANNEL_AVAILABLE_EXCHANGERS= 0
CHANNEL_DEAL_LOGS           = 0
CHANNEL_WEEKLY_COMMISSION   = 0
CHANNEL_ANNOUNCEMENTS       = 0
CHANNEL_TRANSACTIONS        = 1488840012173676545  # transcript channel

# ---------- ROLE IDs ----------
ROLE_OWNER            = 1487024314246107206
ROLE_ADMIN            = 1487024413516890225
ROLE_MODERATOR        = 1487024595944079471
ROLE_VERIFIED_EXCHANGER = 1487024698746474516
ROLE_CLIENT           = 1487024799703109652
ROLE_MEMBER           = 1487024877075697665

STAFF_ROLE_IDS  = [ROLE_OWNER, ROLE_ADMIN, ROLE_MODERATOR]
ADMIN_ROLE_IDS  = [ROLE_OWNER, ROLE_ADMIN]
OWNER_ROLE_IDS  = [ROLE_OWNER]

GUILD_ID              = 1486984795014828064
COMMISSION_PER_DOLLAR = 1
IST                   = pytz.timezone("Asia/Kolkata")

# ---------- VOUCH TEMPLATE ----------
VOUCH_TEMPLATE = "[Shiba vouch] ⭐ Deal done with NexChange! Fast and trusted service. Highly recommend!"

# ---------- OPERATION STATUS ----------
operation_status = {
    "I2C": True,
    "C2I": True,
    "accepting_exchangers": True
}

# ============================================================
# DATA
# ============================================================

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
        "rates": {"I2C": 100, "C2I": 97},
        "custom_commands": {},
        "commission_wallet": "",
        "vouch_template": VOUCH_TEMPLATE,
        "client_stats": {}
    }

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

def get_rates():
    return load_data().get("rates", {"I2C": 100, "C2I": 97})

# ============================================================
# BOT SETUP
# ============================================================

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=".", intents=intents)

# ============================================================
# HELPERS
# ============================================================

def is_staff(member: discord.Member) -> bool:
    return any(r.id in STAFF_ROLE_IDS for r in member.roles)

def is_admin(member: discord.Member) -> bool:
    return any(r.id in ADMIN_ROLE_IDS for r in member.roles)

def is_owner(member: discord.Member) -> bool:
    return any(r.id in OWNER_ROLE_IDS for r in member.roles)

def is_exchanger(member: discord.Member) -> bool:
    return any(r.id == ROLE_VERIFIED_EXCHANGER for r in member.roles)

def can_use_command(member: discord.Member) -> bool:
    """True if member is staff or verified exchanger."""
    return is_staff(member) or is_exchanger(member)

async def update_available_exchangers_channel(guild, data):
    channel = guild.get_channel(CHANNEL_AVAILABLE_EXCHANGERS)
    if not channel:
        return
    rates = data.get("rates", {"I2C": 100, "C2I": 97})
    available = [(uid, ex) for uid, ex in data["exchangers"].items()
                 if ex.get("available") and ex.get("verified")]
    embed = discord.Embed(
        title="🟢 Available Exchangers",
        description="These exchangers are currently online and ready to process deals.",
        color=discord.Color.green()
    )
    embed.add_field(name="I2C Rate", value=f"₹{rates['I2C']}/$", inline=True)
    embed.add_field(name="C2I Rate", value=f"₹{rates['C2I']}/$", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    if not available:
        embed.description = "❌ No exchangers are currently available. Please check back later."
    else:
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


def generate_qr_image(upi_string: str) -> discord.File:
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(upi_string)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return discord.File(buf, filename="qr_code.png")


async def save_transcript(channel: discord.TextChannel, ticket_data: dict, guild: discord.Guild):
    """Saves a full text transcript + summary embed to CHANNEL_TRANSACTIONS."""
    trans_channel = guild.get_channel(CHANNEL_TRANSACTIONS)
    if not trans_channel:
        return

    # Collect all messages
    messages = []
    async for msg in channel.history(limit=500, oldest_first=True):
        ts = msg.created_at.strftime("%d/%m/%Y %H:%M:%S")
        content = msg.content or ""
        if msg.embeds:
            content += " [embed]"
        if msg.attachments:
            content += " [attachment]"
        messages.append(f"[{ts}] {msg.author.display_name}: {content}")

    transcript_text = "\n".join(messages)
    transcript_bytes = transcript_text.encode("utf-8")
    transcript_file = discord.File(
        io.BytesIO(transcript_bytes),
        filename=f"transcript-{ticket_data.get('ticket_id', 'unknown')}.txt"
    )

    # Summary embed
    embed = discord.Embed(
        title=f"📄 Transcript — {ticket_data.get('ticket_id', 'N/A')}",
        color=discord.Color.blurple()
    )
    embed.add_field(name="Type", value=ticket_data.get("type", "N/A"), inline=True)
    embed.add_field(name="Amount", value=f"${ticket_data.get('amount', 0)}", inline=True)
    embed.add_field(name="Client", value=f"<@{ticket_data.get('client_id', 0)}>", inline=True)
    embed.add_field(name="Exchanger", value=f"<@{ticket_data.get('exchanger_id', 0)}>" if ticket_data.get("exchanger_id") else "Unclaimed", inline=True)
    embed.add_field(name="Status", value=ticket_data.get("status", "N/A").title(), inline=True)
    embed.add_field(name="Opened", value=ticket_data.get("created_at", "N/A")[:16], inline=True)
    embed.set_footer(text="NexChange Transcript System")

    await trans_channel.send(embed=embed, file=transcript_file)


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
            await interaction.response.send_message("❌ You are currently marked as unavailable.", ephemeral=True)
            return
        if self.ticket_data.get("amount", 0) > exchanger.get("limit", 0):
            await interaction.response.send_message(f"❌ This deal exceeds your limit of ${exchanger.get('limit', 0)}.", ephemeral=True)
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
            embed.add_field(name="📋 Next Steps", value=f"**Exchanger:** Share your UPI ID here.\n**Client:** Send ₹{inr_amount:,} and upload screenshot.\n**Exchanger:** Verify and release crypto.", inline=False)
        else:
            inr_amount = amount * rates["C2I"]
            embed.add_field(name="📋 Next Steps", value=f"**Client:** Share your crypto wallet here.\n**Exchanger:** Send ${amount} USDT.\n**Client:** Confirm receipt, then send ₹{inr_amount:,}.\n**Exchanger:** Confirm INR received.", inline=False)

        embed.set_footer(text="⚠️ Never release funds based on screenshot alone.")
        await channel.send(embed=embed, view=CompleteOrCancelView(self.ticket_data))
        await interaction.response.send_message("✅ You have claimed this ticket.", ephemeral=True)


class CompleteOrCancelView(discord.ui.View):
    def __init__(self, ticket_data):
        super().__init__(timeout=None)
        self.ticket_data = ticket_data

    @discord.ui.button(label="✅ Complete Deal", style=discord.ButtonStyle.success, custom_id="complete_deal")
    async def complete_deal(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        exchanger_id = self.ticket_data.get("exchanger_id")
        if user_id != exchanger_id and not is_staff(interaction.user):
            await interaction.response.send_message("❌ Only the assigned exchanger or staff can complete this deal.", ephemeral=True)
            return
        await interaction.response.send_modal(CompleteDealModal(self.ticket_data))

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.secondary, custom_id="close_ticket_btn")
    async def close_ticket_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        exchanger_id = self.ticket_data.get("exchanger_id")
        if user_id != exchanger_id and not is_staff(interaction.user):
            await interaction.response.send_message("❌ Only the assigned exchanger or staff can close this ticket.", ephemeral=True)
            return
        await interaction.response.defer()
        await close_ticket(interaction.channel, self.ticket_data, interaction.guild, interaction.user)

    @discord.ui.button(label="❌ Cancel Deal", style=discord.ButtonStyle.danger, custom_id="cancel_deal")
    async def cancel_deal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Only Moderators and above can cancel a deal.", ephemeral=True)
            return
        data = load_data()
        for deal in data["deals"]:
            if deal.get("ticket_id") == self.ticket_data.get("ticket_id"):
                deal["status"] = "cancelled"
                deal["cancelled_at"] = datetime.now().isoformat()
                break
        save_data(data)
        self.complete_deal.disabled = True
        self.close_ticket_btn.disabled = True
        self.cancel_deal.disabled = True
        await interaction.message.edit(view=self)
        embed = discord.Embed(title="❌ Deal Cancelled", description="This deal has been cancelled. Open a dispute ticket if needed.", color=discord.Color.red())
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("Deal cancelled.", ephemeral=True)


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
# CLOSE TICKET LOGIC (shared between button and command)
# ============================================================

async def close_ticket(channel: discord.TextChannel, ticket_data: dict, guild: discord.Guild, closer: discord.Member):
    """Closes a ticket: saves transcript, locks channel, does NOT delete."""
    # Save transcript first
    await save_transcript(channel, ticket_data, guild)

    # Lock channel for everyone
    await channel.set_permissions(guild.default_role, read_messages=False, send_messages=False)
    exchanger_role = guild.get_role(ROLE_VERIFIED_EXCHANGER)
    if exchanger_role:
        await channel.set_permissions(exchanger_role, read_messages=False, send_messages=False)

    # Only admins/owners can still see it
    for role_id in ADMIN_ROLE_IDS:
        role = guild.get_role(role_id)
        if role:
            await channel.set_permissions(role, read_messages=True, send_messages=True)

    embed = discord.Embed(
        title="🔒 Ticket Closed",
        description=f"This ticket has been closed by {closer.mention}.\n\nTranscript saved to <#{CHANNEL_TRANSACTIONS}>.\n\n⚠️ Only Admins/Owner can delete this channel.",
        color=discord.Color.dark_gray()
    )
    await channel.send(embed=embed)

    # Update deal status
    data = load_data()
    for deal in data["deals"]:
        if deal.get("ticket_id") == ticket_data.get("ticket_id"):
            if deal.get("status") not in ["completed", "cancelled"]:
                deal["status"] = "closed"
            deal["closed_at"] = datetime.now().isoformat()
            break
    save_data(data)


# ============================================================
# MODALS
# ============================================================

class CreateTicketModal(discord.ui.Modal):
    def __init__(self, exchange_type):
        super().__init__(title=f"{'INR to Crypto' if exchange_type == 'I2C' else 'Crypto to INR'} Exchange")
        self.exchange_type = exchange_type

    amount = discord.ui.TextInput(label="Amount in USD ($)", placeholder="e.g. 50", required=True, max_length=10)
    wallet = discord.ui.TextInput(label="Wallet (I2C) / UPI ID (C2I)", placeholder="Your wallet or UPI ID", required=True, max_length=200)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = float(self.amount.value)
            if amount <= 0:
                await interaction.response.send_message("❌ Amount must be greater than 0.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("❌ Invalid amount.", ephemeral=True)
            return

        data = load_data()
        client_id = str(interaction.user.id)
        unclaimed = [d for d in data["deals"] if d.get("client_id") == client_id and d.get("status") == "open" and not d.get("exchanger_id")]
        inprogress = [d for d in data["deals"] if d.get("client_id") == client_id and d.get("status") == "in_progress"]

        if len(unclaimed) >= 4:
            await interaction.response.send_message("❌ You already have 4 unclaimed tickets open.", ephemeral=True)
            return
        if len(inprogress) >= 4:
            await interaction.response.send_message("❌ You already have 4 tickets in progress.", ephemeral=True)
            return

        ticket_id = f"NX-{len(data['deals']) + 1001}"
        guild = interaction.guild
        category = interaction.channel.category
        rates = get_rates()

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

        inr_amount = amount * rates[self.exchange_type]
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
        embed.add_field(name="Rate", value=f"₹{rates[self.exchange_type]}/$", inline=True)
        embed.add_field(name="Status", value="🟡 Waiting for Exchanger", inline=True)
        if self.exchange_type == "I2C":
            embed.add_field(name="Client Wallet", value=f"||{self.wallet.value}||", inline=False)
        else:
            embed.add_field(name="Client UPI", value=f"||{self.wallet.value}||", inline=False)
        embed.set_footer(text="⚡ Verified exchangers can claim this ticket below.")

        online_ids = [uid for uid, ex in data["exchangers"].items() if ex.get("available") and ex.get("verified")]
        mentions = " ".join(f"<@{uid}>" for uid in online_ids) if online_ids else ""
        content = f"{mentions} — New {self.exchange_type} ticket!" if mentions else None

        await ticket_channel.send(content=content, embed=embed, view=ClaimTicketView(ticket_data))
        await interaction.response.send_message(f"✅ Ticket created: {ticket_channel.mention}", ephemeral=True)


class CompleteDealModal(discord.ui.Modal, title="Complete Deal"):
    def __init__(self, ticket_data):
        super().__init__()
        self.ticket_data = ticket_data

    final_amount = discord.ui.TextInput(label="Final Completed Amount in USD ($)", placeholder="e.g. 50", required=True, max_length=10)

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

        # Update client stats
        if "client_stats" not in data:
            data["client_stats"] = {}
        if client_id not in data["client_stats"]:
            data["client_stats"][client_id] = {"total_exchanges": 0, "total_value": 0.0}
        data["client_stats"][client_id]["total_exchanges"] += 1
        data["client_stats"][client_id]["total_value"] += amount

        # Update exchanger deal count
        if exchanger_id in data["exchangers"]:
            data["exchangers"][exchanger_id]["total_deals"] = data["exchangers"][exchanger_id].get("total_deals", 0) + 1

        save_data(data)

        guild = interaction.guild
        completed_channel = guild.get_channel(CHANNEL_COMPLETED_DEALS)
        logs_channel = guild.get_channel(CHANNEL_DEAL_LOGS)

        embed = discord.Embed(title=f"✅ Deal Completed — {ticket_id}", color=discord.Color.green())
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

        # Client stats
        stats = data["client_stats"][client_id]
        avg = stats["total_value"] / stats["total_exchanges"]

        # Message 1 — Thanks
        await interaction.channel.send("✅ Thanks for choosing us, deal done.")

        # Message 2 — Vouch template (bot message, NOT counted as vouch)
        vouch_template = data.get("vouch_template", VOUCH_TEMPLATE)
        await interaction.channel.send(
            f"📋 **Please copy and paste the vouch below in this channel to complete your vouch:**\n```\n{vouch_template}\n```"
        )

        # Message 3 — Client stats
        stats_embed = discord.Embed(title="📊 Your NexChange Stats", color=discord.Color.gold())
        stats_embed.add_field(name="Total Exchanges", value=stats["total_exchanges"], inline=True)
        stats_embed.add_field(name="Total Volume", value=f"${stats['total_value']:,.2f}", inline=True)
        stats_embed.add_field(name="Average Exchange", value=f"${avg:,.2f}", inline=True)
        stats_embed.set_footer(text=f"Stats for {interaction.guild.get_member(int(client_id)).display_name if interaction.guild.get_member(int(client_id)) else 'Client'}")
        await interaction.channel.send(embed=stats_embed)

        # Message 4 — Ephemeral confirmation for exchanger
        await interaction.response.send_message(
            f"✅ Deal marked complete. Commission ₹{commission:,.0f} logged.\n\nUse `.done` or `/done` when ready to close this ticket.",
            ephemeral=True
        )


class RegisterExchangerModal(discord.ui.Modal, title="Exchanger Registration"):
    limit = discord.ui.TextInput(label="Exchange Limit in USD ($)", placeholder="e.g. 500", required=True, max_length=10)
    deposit_txn = discord.ui.TextInput(label="Deposit Transaction ID / UTR", placeholder="Paste your UPI UTR", required=True, max_length=200)
    shiba_username = discord.ui.TextInput(label="Shiba Server Username", placeholder="e.g. username#0000", required=True, max_length=100)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            limit = float(self.limit.value)
            if limit <= 0:
                await interaction.response.send_message("❌ Limit must be greater than 0.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("❌ Invalid limit.", ephemeral=True)
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
            "upi_slots": {"1": "", "2": "", "3": ""},
            "crypto_slots": {"1": "", "2": "", "3": ""}
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
        embed.add_field(name="⚠️ Staff Action", value=f"Verify **{self.shiba_username.value}** has 20+ Shiba vouches.\nUse `/verify_exchanger` or `/reject_exchanger`.", inline=False)
        embed.set_footer(text="DO NOT approve without verifying 20 Shiba vouches.")

        logs_channel = guild.get_channel(CHANNEL_DEAL_LOGS)
        if logs_channel:
            await logs_channel.send(content=admin_role.mention if admin_role else "", embed=embed)
        await interaction.response.send_message(
            f"✅ Application submitted!\n\n**Required deposit:** ₹{required_deposit_inr:,.0f}\n\n⚠️ Ensure 20+ Shiba vouches or application will be rejected.\n\nStaff will respond within 24 hours.",
            ephemeral=True
        )


class SetRateModal(discord.ui.Modal):
    def __init__(self, rate_type):
        super().__init__(title=f"Set {'INR to Crypto' if rate_type == 'I2C' else 'Crypto to INR'} Rate")
        self.rate_type = rate_type

    new_rate = discord.ui.TextInput(label="New Rate (INR per $1 USD)", placeholder="e.g. 100", required=True, max_length=10)

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
            data["rates"] = {"I2C": 100, "C2I": 97}
        old_rate = data["rates"][self.rate_type]
        data["rates"][self.rate_type] = rate
        save_data(data)

        announcements = interaction.guild.get_channel(CHANNEL_ANNOUNCEMENTS)
        embed = discord.Embed(title=f"📢 Rate Updated — {'I2C' if self.rate_type == 'I2C' else 'C2I'}", color=discord.Color.orange())
        embed.add_field(name="Old Rate", value=f"₹{old_rate}/$", inline=True)
        embed.add_field(name="New Rate", value=f"₹{rate}/$", inline=True)
        embed.add_field(name="Updated By", value=interaction.user.mention, inline=True)
        if announcements:
            await announcements.send(embed=embed)
        await interaction.response.send_message(f"✅ {self.rate_type} rate updated to ₹{rate}/$.", ephemeral=True)


class AddCustomCommandModal(discord.ui.Modal, title="Add / Edit Custom Command"):
    cmd_name = discord.ui.TextInput(label="Command Name (without comma)", placeholder="e.g. greet", required=True, max_length=30)
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
        await interaction.response.send_message(f"✅ {action} `,{name}`. Type `,{name}` in any channel to use it.", ephemeral=True)


class SetUPIModal(discord.ui.Modal):
    def __init__(self, slot, target_user_id=None):
        super().__init__(title=f"Set UPI Slot {slot}")
        self.slot = str(slot)
        self.target_user_id = target_user_id

    upi_id = discord.ui.TextInput(label="UPI ID", placeholder="e.g. name@upi", required=True, max_length=100)

    async def on_submit(self, interaction: discord.Interaction):
        data = load_data()
        user_id = self.target_user_id or str(interaction.user.id)
        if user_id not in data["exchangers"]:
            data["exchangers"][user_id] = {"upi_slots": {"1": "", "2": "", "3": ""}, "crypto_slots": {"1": "", "2": "", "3": ""}}
        if "upi_slots" not in data["exchangers"][user_id]:
            data["exchangers"][user_id]["upi_slots"] = {"1": "", "2": "", "3": ""}
        data["exchangers"][user_id]["upi_slots"][self.slot] = self.upi_id.value.strip()
        save_data(data)
        await interaction.response.send_message(f"✅ UPI Slot {self.slot} set to `{self.upi_id.value.strip()}`.", ephemeral=True)


class SetCryptoModal(discord.ui.Modal):
    def __init__(self, slot, target_user_id=None):
        super().__init__(title=f"Set Crypto Slot {slot}")
        self.slot = str(slot)
        self.target_user_id = target_user_id

    address = discord.ui.TextInput(label="Crypto Address", placeholder="e.g. TRX or ETH address", required=True, max_length=200)

    async def on_submit(self, interaction: discord.Interaction):
        data = load_data()
        user_id = self.target_user_id or str(interaction.user.id)
        if user_id not in data["exchangers"]:
            data["exchangers"][user_id] = {"upi_slots": {"1": "", "2": "", "3": ""}, "crypto_slots": {"1": "", "2": "", "3": ""}}
        if "crypto_slots" not in data["exchangers"][user_id]:
            data["exchangers"][user_id]["crypto_slots"] = {"1": "", "2": "", "3": ""}
        data["exchangers"][user_id]["crypto_slots"][self.slot] = self.address.value.strip()
        save_data(data)
        await interaction.response.send_message(f"✅ Crypto Slot {self.slot} set to `{self.address.value.strip()}`.", ephemeral=True)


class EditEmbedModal(discord.ui.Modal, title="Edit Panel Embed"):
    new_title = discord.ui.TextInput(label="New Title", required=False, max_length=200)
    new_description = discord.ui.TextInput(label="New Description", required=False, max_length=2000, style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "✅ To edit a panel embed, reply to the panel message with `/editembed` or use `.editembed` — this modal captured your input.\n\nNote: Use `/setup_panel` to redeploy a fresh panel with current rates.",
            ephemeral=True
        )


# ============================================================
# DONE COMMAND LOGIC (shared)
# ============================================================

async def handle_done(ctx_or_interaction, is_slash=False):
    """Shared logic for /done and .done"""
    if is_slash:
        channel = ctx_or_interaction.channel
        user = ctx_or_interaction.user
        guild = ctx_or_interaction.guild
        respond = ctx_or_interaction.response.send_message
        defer = ctx_or_interaction.response.defer
    else:
        channel = ctx_or_interaction.channel
        user = ctx_or_interaction.author
        guild = ctx_or_interaction.guild
        respond = None
        defer = None

    # Find the active deal for this channel
    data = load_data()
    ticket_data = None
    for deal in data["deals"]:
        if deal.get("channel_id") == str(channel.id):
            ticket_data = deal
            break

    if not ticket_data:
        msg = "❌ No active deal found in this channel."
        if is_slash:
            await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else:
            await channel.send(msg)
        return

    exchanger_id = ticket_data.get("exchanger_id")
    if str(user.id) != exchanger_id and not is_staff(user):
        msg = "❌ Only the assigned exchanger or staff can use this command."
        if is_slash:
            await ctx_or_interaction.response.send_message(msg, ephemeral=True)
        else:
            await channel.send(msg)
        return

    if is_slash:
        await ctx_or_interaction.response.defer(ephemeral=True)

    await close_ticket(channel, ticket_data, guild, user)

    if is_slash:
        await ctx_or_interaction.followup.send("✅ Ticket closed and transcript saved.", ephemeral=True)
    else:
        pass  # close_ticket already sends the embed


# ============================================================
# SLASH COMMANDS
# ============================================================

# --- Panel & Setup ---

@bot.tree.command(name="setup_panel", description="Setup the main exchange panel [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def setup_panel(interaction: discord.Interaction):
    rates = get_rates()
    embed = discord.Embed(
        title="💱 NEXCHANGE EXCHANGE PANEL",
        description="Welcome to NexChange — India's most trusted P2P crypto exchange.\n\nSelect your exchange type below to get started.",
        color=discord.Color.blue()
    )
    embed.add_field(name="💰 INR to Crypto (I2C)", value=f"Rate: ₹{rates['I2C']}/$\nSend INR, receive USDT", inline=True)
    embed.add_field(name="💸 Crypto to INR (C2I)", value=f"Rate: ₹{rates['C2I']}/$\nSend USDT, receive INR", inline=True)
    embed.add_field(name="ℹ️ Important", value="• Fixed rates, no negotiation\n• Read #tos before proceeding\n• ₹1/$ server handling charge\n• All deals protected by escrow", inline=False)
    embed.set_footer(text="NexChange — Fast. Safe. Trusted.")
    await interaction.channel.send(embed=embed, view=ExchangeTypeView())
    await interaction.response.send_message("✅ Exchange panel setup complete.", ephemeral=True)


@bot.tree.command(name="setup_availability", description="Setup exchanger availability panel [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def setup_availability(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🔄 Exchanger Availability",
        description="Toggle your availability status here.\n\n⚠️ Always go offline when unavailable. Failure to do so results in a penalty.",
        color=discord.Color.blue()
    )
    await interaction.channel.send(embed=embed, view=AvailabilityView())
    await interaction.response.send_message("✅ Availability panel setup complete.", ephemeral=True)


@bot.tree.command(name="setup_custom_panel", description="Create a custom ticket panel with custom title and description [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
@app_commands.describe(title="Panel title", description="Panel description", button_label="Button label (optional)")
async def setup_custom_panel(interaction: discord.Interaction, title: str, description: str, button_label: str = "📩 Open Ticket"):
    embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
    embed.set_footer(text="NexChange — Fast. Safe. Trusted.")

    class CustomTicketView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(label=button_label, style=discord.ButtonStyle.primary, custom_id="custom_ticket_open")
        async def open_ticket(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
            await btn_interaction.response.send_modal(CreateTicketModal("I2C"))

    await interaction.channel.send(embed=embed, view=CustomTicketView())
    await interaction.response.send_message("✅ Custom panel created.", ephemeral=True)


# --- Exchanger Management ---

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
        await interaction.response.send_message("❌ No pending application found.", ephemeral=True)
        return
    data["exchangers"][user_id]["verified"] = True
    save_data(data)
    role = interaction.guild.get_role(ROLE_VERIFIED_EXCHANGER)
    if role:
        await user.add_roles(role)
    embed = discord.Embed(title="✅ Exchanger Verified", description=f"{user.mention} verified.", color=discord.Color.green())
    embed.add_field(name="Limit", value=f"${data['exchangers'][user_id]['limit']}", inline=True)
    await interaction.response.send_message(embed=embed)
    await user.send(f"✅ You have been verified as an exchanger on **NexChange**!\nLimit: **${data['exchangers'][user_id]['limit']}**\nToggle availability in the panel to go online.")


@bot.tree.command(name="reject_exchanger", description="Reject an exchanger application [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
@app_commands.describe(user="Exchanger to reject", reason="Reason")
async def reject_exchanger(interaction: discord.Interaction, user: discord.Member, reason: str):
    data = load_data()
    user_id = str(user.id)
    if user_id in data["exchangers"]:
        del data["exchangers"][user_id]
        save_data(data)
    await user.send(f"❌ Your exchanger application was rejected.\n\nReason: {reason}\n\nYou may reapply after resolving the issue.")
    await interaction.response.send_message(f"✅ Rejected and notified {user.mention}.", ephemeral=True)


@bot.tree.command(name="update_limit", description="Update an exchanger's limit [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
@app_commands.describe(user="The exchanger", new_limit="New limit in USD")
async def update_limit(interaction: discord.Interaction, user: discord.Member, new_limit: float):
    data = load_data()
    user_id = str(user.id)
    if user_id not in data["exchangers"]:
        await interaction.response.send_message("❌ Not a registered exchanger.", ephemeral=True)
        return
    old = data["exchangers"][user_id]["limit"]
    data["exchangers"][user_id]["limit"] = new_limit
    save_data(data)
    await update_available_exchangers_channel(interaction.guild, data)
    await interaction.response.send_message(f"✅ Limit updated: ${old} → ${new_limit}", ephemeral=True)
    await user.send(f"📢 Your NexChange limit has been updated to **${new_limit}**.")


# --- Penalties ---

@bot.tree.command(name="add_penalty", description="Add a penalty to an exchanger [Staff only]")
@app_commands.checks.has_any_role("Owner", "Admin", "Moderator")
@app_commands.describe(user="Exchanger", amount="Penalty in INR", reason="Reason")
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
    if penalty_channel:
        await penalty_channel.send(embed=embed)
    await user.send(f"⚠️ Penalty of **₹{amount}** issued.\nReason: {reason}\nContact staff to settle.")
    await interaction.response.send_message(f"✅ Penalty issued to {user.mention}.", ephemeral=True)


@bot.tree.command(name="pay_penalty", description="Mark penalty as paid [Staff only]")
@app_commands.checks.has_any_role("Owner", "Admin", "Moderator")
@app_commands.describe(user="The exchanger who paid")
async def pay_penalty(interaction: discord.Interaction, user: discord.Member):
    data = load_data()
    user_id = str(user.id)
    unpaid = [p for p in data["penalties"].get(user_id, []) if not p["paid"]]
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
    await user.send(f"✅ Penalty of **₹{total}** cleared. You can resume trading.")
    await interaction.response.send_message(f"✅ Penalties cleared for {user.mention}.", ephemeral=True)


# --- Commission ---

@bot.tree.command(name="commission_status", description="Check commission owed [Staff only]")
@app_commands.checks.has_any_role("Owner", "Admin", "Moderator")
@app_commands.describe(user="The exchanger")
async def commission_status(interaction: discord.Interaction, user: discord.Member):
    data = load_data()
    owed = data["commission_owed"].get(str(user.id), 0)
    embed = discord.Embed(title="💰 Commission Status", color=discord.Color.blue())
    embed.add_field(name="Exchanger", value=user.mention, inline=True)
    embed.add_field(name="Commission Owed", value=f"₹{owed:,.0f}", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="clear_commission", description="Clear commission after payment [Staff only]")
@app_commands.checks.has_any_role("Owner", "Admin", "Moderator")
@app_commands.describe(user="The exchanger who paid")
async def clear_commission(interaction: discord.Interaction, user: discord.Member):
    data = load_data()
    user_id = str(user.id)
    owed = data["commission_owed"].get(user_id, 0)
    data["commission_owed"][user_id] = 0
    save_data(data)
    await interaction.response.send_message(f"✅ Commission ₹{owed:,.0f} cleared for {user.mention}.", ephemeral=True)


@bot.tree.command(name="paycommission", description="View commission wallet or set it [Owner only for set]")
@app_commands.describe(action="view / setaddress (Owner only)")
async def paycommission(interaction: discord.Interaction, action: str = "view"):
    data = load_data()
    action = action.lower().strip()

    if action == "view":
        wallet = data.get("commission_wallet", "")
        owed = data["commission_owed"].get(str(interaction.user.id), 0)
        if not wallet:
            await interaction.response.send_message("❌ Commission wallet not set yet. Ask the owner to set it with `/paycommission setaddress`.", ephemeral=True)
            return
        embed = discord.Embed(title="💳 Commission Payment", color=discord.Color.gold())
        embed.add_field(name="Your Commission Owed", value=f"₹{owed:,.0f}", inline=False)
        embed.add_field(name="Wallet Address", value=f"```{wallet}```", inline=False)
        embed.add_field(name="Due", value="Every Saturday at 7 PM IST", inline=False)
        embed.set_footer(text="Pay to the above address and notify staff after payment.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    elif action == "setaddress":
        if not is_owner(interaction.user):
            await interaction.response.send_message("❌ Only the Owner can set the commission wallet.", ephemeral=True)
            return
        await interaction.response.send_modal(SetCommissionWalletModal())

    else:
        await interaction.response.send_message("❌ Use `view` or `setaddress`.", ephemeral=True)


# --- Rates ---

@bot.tree.command(name="set_i2c_rate", description="Change I2C rate [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def set_i2c_rate(interaction: discord.Interaction):
    await interaction.response.send_modal(SetRateModal("I2C"))


@bot.tree.command(name="set_c2i_rate", description="Change C2I rate [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def set_c2i_rate(interaction: discord.Interaction):
    await interaction.response.send_modal(SetRateModal("C2I"))


@bot.tree.command(name="current_rates", description="View current exchange rates")
async def current_rates(interaction: discord.Interaction):
    rates = get_rates()
    embed = discord.Embed(title="💱 Current Exchange Rates", color=discord.Color.blue())
    embed.add_field(name="💰 I2C", value=f"₹{rates['I2C']} per $1", inline=True)
    embed.add_field(name="💸 C2I", value=f"₹{rates['C2I']} per $1", inline=True)
    await interaction.response.send_message(embed=embed)


# --- Custom Commands ---

@bot.tree.command(name="customcommand", description="Add, edit or remove custom comma commands [Staff only]")
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
        options = [discord.SelectOption(label=f",{n}", value=n) for n in list(data["custom_commands"].keys())[:25]]

        class RemoveSelect(discord.ui.Select):
            def __init__(self):
                super().__init__(placeholder="Select command to remove...", options=options)
            async def callback(self, sel: discord.Interaction):
                d = load_data()
                chosen = self.values[0]
                if chosen in d.get("custom_commands", {}):
                    del d["custom_commands"][chosen]
                    save_data(d)
                    await sel.response.send_message(f"✅ `,{chosen}` removed.", ephemeral=True)
                else:
                    await sel.response.send_message("❌ Not found.", ephemeral=True)

        class RemoveView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=30)
                self.add_item(RemoveSelect())

        await interaction.response.send_message("Select command to remove:", view=RemoveView(), ephemeral=True)
    elif action == "list":
        cmds = data.get("custom_commands", {})
        if not cmds:
            await interaction.response.send_message("📋 No custom commands yet.", ephemeral=True)
            return
        embed = discord.Embed(title="📋 Custom Commands", color=discord.Color.blue())
        for name, resp in cmds.items():
            preview = resp[:80] + "..." if len(resp) > 80 else resp
            embed.add_field(name=f",{name}", value=preview, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("❌ Use `add`, `remove`, or `list`.", ephemeral=True)


# --- Exchanger Info ---

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
    embed.add_field(name="Pending Penalties", value=f"{len(penalties)}" if penalties else "None", inline=True)
    upi = ex.get("upi_slots", {})
    crypto = ex.get("crypto_slots", {})
    embed.add_field(name="UPI Slots", value=f"1: `{upi.get('1','—')}` | 2: `{upi.get('2','—')}` | 3: `{upi.get('3','—')}`", inline=False)
    embed.add_field(name="Crypto Slots", value=f"1: `{crypto.get('1','—')}` | 2: `{crypto.get('2','—')}` | 3: `{crypto.get('3','—')}`", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- Stats ---

@bot.tree.command(name="stats", description="View server statistics [Staff only]")
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
    embed.add_field(name="Verified", value=len([e for e in data["exchangers"].values() if e.get("verified")]), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- Done & Ticket Control ---

@bot.tree.command(name="done", description="Close the ticket after deal is complete")
async def done_slash(interaction: discord.Interaction):
    await handle_done(interaction, is_slash=True)


# --- Operational Controls ---

@bot.tree.command(name="startallexchanges", description="Resume ALL exchanges [Admin/Owner]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def startallexchanges(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    operation_status["I2C"] = True
    operation_status["C2I"] = True
    operation_status["accepting_exchangers"] = True
    rates = get_rates()
    announcements = interaction.guild.get_channel(CHANNEL_ANNOUNCEMENTS)
    embed = discord.Embed(title="✅ ALL OPERATIONS RESUMED", description=f"All exchanges are back online.\nHead to <#{CHANNEL_OPEN_TICKET}> to start.", color=discord.Color.green())
    embed.add_field(name="I2C Rate", value=f"₹{rates['I2C']}/$", inline=True)
    embed.add_field(name="C2I Rate", value=f"₹{rates['C2I']}/$", inline=True)
    if announcements:
        await announcements.send(embed=embed)
    await interaction.followup.send("✅ All operations resumed.", ephemeral=True)


@bot.tree.command(name="stopallexchanges", description="Emergency stop — close ALL exchanges [Admin/Owner]")
@app_commands.checks.has_any_role("Owner", "Admin")
@app_commands.describe(reason="Reason for stopping")
async def stopallexchanges(interaction: discord.Interaction, reason: str = "Emergency maintenance"):
    await interaction.response.defer(ephemeral=True)
    operation_status["I2C"] = False
    operation_status["C2I"] = False
    operation_status["accepting_exchangers"] = False
    announcements = interaction.guild.get_channel(CHANNEL_ANNOUNCEMENTS)
    embed = discord.Embed(title="🚨 ALL OPERATIONS SUSPENDED", description=f"All exchange operations suspended.\n\n**Reason:** {reason}", color=discord.Color.dark_red())
    if announcements:
        await announcements.send(embed=embed)
    await interaction.followup.send("🚨 All operations stopped.", ephemeral=True)


@bot.tree.command(name="start_i2c", description="Open I2C exchanges [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def start_i2c(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    operation_status["I2C"] = True
    rates = get_rates()
    announcements = interaction.guild.get_channel(CHANNEL_ANNOUNCEMENTS)
    embed = discord.Embed(title="✅ INR to Crypto — NOW OPEN", description=f"Head to <#{CHANNEL_OPEN_TICKET}> to start.", color=discord.Color.green())
    embed.add_field(name="Rate", value=f"₹{rates['I2C']}/$", inline=True)
    if announcements:
        await announcements.send(embed=embed)
    await interaction.followup.send("✅ I2C exchanges open.", ephemeral=True)


@bot.tree.command(name="stop_i2c", description="Close I2C exchanges [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
@app_commands.describe(reason="Reason")
async def stop_i2c(interaction: discord.Interaction, reason: str = "Temporarily unavailable"):
    await interaction.response.defer(ephemeral=True)
    operation_status["I2C"] = False
    announcements = interaction.guild.get_channel(CHANNEL_ANNOUNCEMENTS)
    embed = discord.Embed(title="🔴 INR to Crypto — CLOSED", description=f"**Reason:** {reason}", color=discord.Color.red())
    if announcements:
        await announcements.send(embed=embed)
    await interaction.followup.send("✅ I2C exchanges closed.", ephemeral=True)


@bot.tree.command(name="start_c2i", description="Open C2I exchanges [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def start_c2i(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    operation_status["C2I"] = True
    rates = get_rates()
    announcements = interaction.guild.get_channel(CHANNEL_ANNOUNCEMENTS)
    embed = discord.Embed(title="✅ Crypto to INR — NOW OPEN", description=f"Head to <#{CHANNEL_OPEN_TICKET}> to start.", color=discord.Color.green())
    embed.add_field(name="Rate", value=f"₹{rates['C2I']}/$", inline=True)
    if announcements:
        await announcements.send(embed=embed)
    await interaction.followup.send("✅ C2I exchanges open.", ephemeral=True)


@bot.tree.command(name="stop_c2i", description="Close C2I exchanges [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
@app_commands.describe(reason="Reason")
async def stop_c2i(interaction: discord.Interaction, reason: str = "Temporarily unavailable"):
    await interaction.response.defer(ephemeral=True)
    operation_status["C2I"] = False
    announcements = interaction.guild.get_channel(CHANNEL_ANNOUNCEMENTS)
    embed = discord.Embed(title="🔴 Crypto to INR — CLOSED", description=f"**Reason:** {reason}", color=discord.Color.red())
    if announcements:
        await announcements.send(embed=embed)
    await interaction.followup.send("✅ C2I exchanges closed.", ephemeral=True)


@bot.tree.command(name="server_status", description="Check operation status [Staff only]")
@app_commands.checks.has_any_role("Owner", "Admin", "Moderator")
async def server_status(interaction: discord.Interaction):
    rates = get_rates()
    embed = discord.Embed(title="📊 NexChange Operation Status", color=discord.Color.blue())
    embed.add_field(name="I2C", value="🟢 Open" if operation_status["I2C"] else "🔴 Closed", inline=True)
    embed.add_field(name="C2I", value="🟢 Open" if operation_status["C2I"] else "🔴 Closed", inline=True)
    embed.add_field(name="Applications", value="🟢 Open" if operation_status["accepting_exchangers"] else "🔴 Closed", inline=True)
    embed.add_field(name="I2C Rate", value=f"₹{rates['I2C']}/$", inline=True)
    embed.add_field(name="C2I Rate", value=f"₹{rates['C2I']}/$", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="stop_exchanger_applications", description="Stop exchanger applications [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def stop_exchanger_applications(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    operation_status["accepting_exchangers"] = False
    await interaction.followup.send("✅ Applications closed.", ephemeral=True)


@bot.tree.command(name="start_exchanger_applications", description="Open exchanger applications [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def start_exchanger_applications(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    operation_status["accepting_exchangers"] = True
    await interaction.followup.send("✅ Applications open.", ephemeral=True)


# --- UPI / Crypto Slot Commands ---

@bot.tree.command(name="setupi", description="Set your UPI slot")
@app_commands.describe(slot="Slot number (1, 2, or 3)", user="User to set for (Admin only)")
async def setupi(interaction: discord.Interaction, slot: int, user: discord.Member = None):
    if slot not in [1, 2, 3]:
        await interaction.response.send_message("❌ Slot must be 1, 2, or 3.", ephemeral=True)
        return
    if user and not is_admin(interaction.user):
        await interaction.response.send_message("❌ Only Admins can set slots for others.", ephemeral=True)
        return
    target_id = str(user.id) if user else None
    await interaction.response.send_modal(SetUPIModal(slot, target_id))


@bot.tree.command(name="setcrypto", description="Set your crypto address slot")
@app_commands.describe(slot="Slot number (1, 2, or 3)", user="User to set for (Admin only)")
async def setcrypto(interaction: discord.Interaction, slot: int, user: discord.Member = None):
    if slot not in [1, 2, 3]:
        await interaction.response.send_message("❌ Slot must be 1, 2, or 3.", ephemeral=True)
        return
    if user and not is_admin(interaction.user):
        await interaction.response.send_message("❌ Only Admins can set slots for others.", ephemeral=True)
        return
    target_id = str(user.id) if user else None
    await interaction.response.send_modal(SetCryptoModal(slot, target_id))


# ============================================================
# SET COMMISSION WALLET MODAL
# ============================================================

class SetCommissionWalletModal(discord.ui.Modal, title="Set Commission Wallet Address"):
    wallet_address = discord.ui.TextInput(label="Wallet Address", placeholder="Enter crypto wallet address", required=True, max_length=200)

    async def on_submit(self, interaction: discord.Interaction):
        data = load_data()
        data["commission_wallet"] = self.wallet_address.value.strip()
        save_data(data)
        await interaction.response.send_message(f"✅ Commission wallet set to:\n```{self.wallet_address.value.strip()}```", ephemeral=True)


# ============================================================
# PREFIX (.dot) COMMANDS via on_message
# ============================================================

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    content = message.content.strip()

    # ---- .done ----
    if content.lower() == ".done":
        await handle_done(message, is_slash=False)
        return

    # ---- .i2c<amount> ----
    dot_i2c = re.match(r"^\.i2c(\d+(\.\d+)?)$", content, re.IGNORECASE)
    dot_c2i = re.match(r"^\.c2i(\d+(\.\d+)?)$", content, re.IGNORECASE)

    if dot_i2c or dot_c2i:
        guild = message.guild
        if not guild:
            return
        if not is_exchanger(message.author) and not is_staff(message.author):
            await message.reply("❌ Only verified exchangers can use dot commands.")
            return

        rates = get_rates()
        if dot_i2c:
            inr_amount = float(dot_i2c.group(1))
            usd_amount = inr_amount / rates["I2C"]
            exchange_type, rate, color = "I2C", rates["I2C"], discord.Color.blue()
            title = "💰 INR to Crypto — Deal Summary"
            description = f"Client sends **₹{inr_amount:,.0f}** → Exchanger releases **${usd_amount:.2f} USDT**"
        else:
            usd_amount = float(dot_c2i.group(1))
            inr_amount = usd_amount * rates["C2I"]
            exchange_type, rate, color = "C2I", rates["C2I"], discord.Color.green()
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

    # ---- .makeqr ----
    if content.lower() == ".makeqr":
        if not message.reference:
            await message.reply("❌ Reply to a message containing a UPI ID and use `.makeqr`.")
            return
        try:
            ref_msg = await message.channel.fetch_message(message.reference.message_id)
        except Exception:
            await message.reply("❌ Could not fetch the referenced message.")
            return
        upi_text = ref_msg.content.strip().replace("||", "")
        upi_match = re.search(r"[\w.\-]+@[\w]+", upi_text)
        if not upi_match:
            await message.reply("❌ No UPI ID found. It should look like `name@upi`.")
            return
        upi_id = upi_match.group(0)
        upi_link = f"upi://pay?pa={upi_id}&cu=INR"
        try:
            qr_file = generate_qr_image(upi_link)
            embed = discord.Embed(title="📱 UPI QR Code", description=f"Scan to pay\n\n**UPI ID:** `{upi_id}`", color=discord.Color.green())
            embed.set_image(url="attachment://qr_code.png")
            await message.reply(embed=embed, file=qr_file)
        except Exception as e:
            await message.reply(f"❌ Failed to generate QR: {e}")
        return

    # ---- .upi1 / .upi2 / .upi3 ----
    upi_match_cmd = re.match(r"^\.upi([123])$", content, re.IGNORECASE)
    if upi_match_cmd:
        slot = upi_match_cmd.group(1)
        data = load_data()
        # Find exchanger in the channel's ticket
        ticket_data = next((d for d in data["deals"] if d.get("channel_id") == str(message.channel.id)), None)
        if ticket_data and ticket_data.get("exchanger_id"):
            exchanger_id = ticket_data["exchanger_id"]
            ex = data["exchangers"].get(exchanger_id, {})
            upi = ex.get("upi_slots", {}).get(slot, "")
            if upi:
                await message.channel.send(f"💳 **UPI Slot {slot}:** `{upi}`")
            else:
                await message.channel.send(f"❌ UPI slot {slot} is not set by the exchanger.")
        else:
            # Outside ticket — show own slot
            user_id = str(message.author.id)
            ex = data["exchangers"].get(user_id, {})
            upi = ex.get("upi_slots", {}).get(slot, "")
            if upi:
                await message.channel.send(f"💳 **Your UPI Slot {slot}:** `{upi}`")
            else:
                await message.channel.send(f"❌ Your UPI slot {slot} is not set. Use `/setupi` to set it.")
        return

    # ---- .crypto1 / .crypto2 / .crypto3 ----
    crypto_match_cmd = re.match(r"^\.crypto([123])$", content, re.IGNORECASE)
    if crypto_match_cmd:
        slot = crypto_match_cmd.group(1)
        data = load_data()
        ticket_data = next((d for d in data["deals"] if d.get("channel_id") == str(message.channel.id)), None)
        if ticket_data and ticket_data.get("exchanger_id"):
            exchanger_id = ticket_data["exchanger_id"]
            ex = data["exchangers"].get(exchanger_id, {})
            addr = ex.get("crypto_slots", {}).get(slot, "")
            if addr:
                await message.channel.send(f"🔑 **Crypto Slot {slot}:** `{addr}`")
            else:
                await message.channel.send(f"❌ Crypto slot {slot} is not set by the exchanger.")
        else:
            user_id = str(message.author.id)
            ex = data["exchangers"].get(user_id, {})
            addr = ex.get("crypto_slots", {}).get(slot, "")
            if addr:
                await message.channel.send(f"🔑 **Your Crypto Slot {slot}:** `{addr}`")
            else:
                await message.channel.send(f"❌ Your crypto slot {slot} is not set. Use `/setcrypto` to set it.")
        return

    # ---- .setupi<slot> ----
    setupi_match = re.match(r"^\.setupi([123])$", content, re.IGNORECASE)
    if setupi_match:
        slot = int(setupi_match.group(1))
        # Send a modal via a fake interaction is not possible in on_message
        # So instruct user to use slash command
        await message.reply(f"Please use `/setupi slot:{slot}` to set your UPI slot via the slash command.")
        return

    # ---- .setcrypto<slot> ----
    setcrypto_match = re.match(r"^\.setcrypto([123])$", content, re.IGNORECASE)
    if setcrypto_match:
        slot = int(setcrypto_match.group(1))
        await message.reply(f"Please use `/setcrypto slot:{slot}` to set your crypto slot via the slash command.")
        return

    # ---- Prefix versions of slash commands ----
    # .startallexchanges / .stopallexchanges
    if content.lower() == ".startallexchanges":
        if not is_admin(message.author):
            await message.reply("❌ Only Admins/Owner can use this command.")
            return
        operation_status["I2C"] = True
        operation_status["C2I"] = True
        operation_status["accepting_exchangers"] = True
        rates = get_rates()
        announcements = message.guild.get_channel(CHANNEL_ANNOUNCEMENTS)
        embed = discord.Embed(title="✅ ALL OPERATIONS RESUMED", color=discord.Color.green())
        embed.add_field(name="I2C Rate", value=f"₹{rates['I2C']}/$", inline=True)
        embed.add_field(name="C2I Rate", value=f"₹{rates['C2I']}/$", inline=True)
        if announcements:
            await announcements.send(embed=embed)
        await message.reply("✅ All operations resumed.")
        return

    if content.lower().startswith(".stopallexchanges"):
        if not is_admin(message.author):
            await message.reply("❌ Only Admins/Owner can use this command.")
            return
        reason = content[len(".stopallexchanges"):].strip() or "Emergency maintenance"
        operation_status["I2C"] = False
        operation_status["C2I"] = False
        operation_status["accepting_exchangers"] = False
        announcements = message.guild.get_channel(CHANNEL_ANNOUNCEMENTS)
        embed = discord.Embed(title="🚨 ALL OPERATIONS SUSPENDED", description=f"**Reason:** {reason}", color=discord.Color.dark_red())
        if announcements:
            await announcements.send(embed=embed)
        await message.reply("🚨 All operations stopped.")
        return

    # ---- ,custom commands ----
    if content.startswith(","):
        cmd_name = content[1:].strip().lower().split()[0]
        data = load_data()
        cmds = data.get("custom_commands", {})
        if cmd_name in cmds:
            try:
                await message.delete()
            except Exception:
                pass
            await message.channel.send(cmds[cmd_name])
        return

    await bot.process_commands(message)


# ============================================================
# SCHEDULED TASKS
# ============================================================

@tasks.loop(minutes=30)
async def commission_reminder():
    """Every Saturday at 7 PM IST — remind exchangers to pay commission."""
    now = datetime.now(IST)
    if now.weekday() == 5 and now.hour == 19 and now.minute < 30:
        data = load_data()
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return
        commission_channel = guild.get_channel(CHANNEL_WEEKLY_COMMISSION)
        wallet = data.get("commission_wallet", "Not set — contact owner.")

        for user_id, amount in data["commission_owed"].items():
            if amount > 0:
                member = guild.get_member(int(user_id))
                if not member:
                    continue

                # Suspend exchanger role until paid
                role = guild.get_role(ROLE_VERIFIED_EXCHANGER)
                if role and role in member.roles:
                    await member.remove_roles(role)
                    # Update data
                    if user_id in data["exchangers"]:
                        data["exchangers"][user_id]["commission_suspended"] = True

                try:
                    await member.send(
                        f"⚠️ **NexChange Weekly Commission Due**\n\n"
                        f"You owe **₹{amount:,.0f}** in commission.\n\n"
                        f"**Pay to:** `{wallet}`\n\n"
                        f"Your exchanger role has been suspended until payment is confirmed by staff.\n"
                        f"After paying, notify staff with proof."
                    )
                except Exception:
                    pass

        save_data(data)

        if commission_channel:
            embed = discord.Embed(title="📋 Weekly Commission Due", description=f"Commission is due today (Saturday 7 PM IST).", color=discord.Color.gold())
            embed.add_field(name="Wallet", value=f"```{wallet}```", inline=False)
            total = sum(v for v in data["commission_owed"].values() if v > 0)
            embed.add_field(name="Total Outstanding", value=f"₹{total:,.0f}", inline=True)
            embed.set_footer(text="Exchangers who have not paid have been suspended. Pay and notify staff.")
            await commission_channel.send(embed=embed)


@tasks.loop(hours=1)
async def weekly_commission_report():
    now = datetime.now(IST)
    if now.weekday() == 6 and now.hour == 9:
        data = load_data()
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return
        channel = guild.get_channel(CHANNEL_WEEKLY_COMMISSION)
        if not channel:
            return
        embed = discord.Embed(title="📋 Weekly Commission Report", description=f"Week ending {now.strftime('%d/%m/%Y')}", color=discord.Color.gold())
        total = 0
        for user_id, amount in data["commission_owed"].items():
            if amount > 0:
                member = guild.get_member(int(user_id))
                name = member.display_name if member else f"User {user_id}"
                embed.add_field(name=name, value=f"₹{amount:,.0f} owed", inline=True)
                total += amount
        embed.add_field(name="Total Due", value=f"₹{total:,.0f}", inline=False)
        await channel.send(embed=embed)


# ============================================================
# BOT EVENTS
# ============================================================

@bot.event
async def on_ready():
    print(f"✅ NexChange Bot is online as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"❌ Sync error: {e}")
    commission_reminder.start()
    weekly_commission_report.start()
    bot.add_view(ExchangeTypeView())
    bot.add_view(AvailabilityView())


# ============================================================
# RUN
# ============================================================
bot.run(BOT_TOKEN)
