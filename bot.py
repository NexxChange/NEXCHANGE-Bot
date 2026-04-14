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
# NEXCHANGE MAIN BOT — CLEANED
# Commission, penalties, scheduled tasks → commission bot
# All config now lives in bot_config.json (shared with commission bot)
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
IST = pytz.timezone("Asia/Kolkata")

# ============================================================
# CONFIG & DATA HELPERS
# ============================================================

MAIN_DATA_FILE = "nexchange_data.json"
CONFIG_FILE    = "bot_config.json"

DEFAULT_CONFIG = {
    "guild_id": 1486984795014828064,
    "roles": {
        "owner":              1487024314246107206,
        "admin":              1487024413516890225,
        "moderator":          1487024595944079471,
        "verified_exchanger": 1487024698746474516,
        "client":             1487024799703109652,
        "member":             1487024877075697665
    },
    "channels": {
        "open_ticket":          0,
        "completed_deals":      0,
        "reviews":              0,
        "penalty_board":        0,
        "available_exchangers": 0,
        "deal_logs":            0,
        "weekly_commission":    0,
        "announcements":        0,
        "transactions":         1488840012173676545
    },
    "commission": {
        "rate_per_dollar": 1
    },
    "messages": {
        "vouch_template": "[Shiba vouch] ⭐ Deal done with NexChange! Fast and trusted service. Highly recommend!"
    }
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
        def deep_merge(base, override):
            for k, v in base.items():
                if k not in override:
                    override[k] = v
                elif isinstance(v, dict) and isinstance(override[k], dict):
                    deep_merge(v, override[k])
        deep_merge(DEFAULT_CONFIG, cfg)
        return cfg
    # First run — create config
    with open(CONFIG_FILE, "w") as f:
        json.dump(DEFAULT_CONFIG, f, indent=4)
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)

def load_data():
    if os.path.exists(MAIN_DATA_FILE):
        with open(MAIN_DATA_FILE, "r") as f:
            return json.load(f)
    return {
        "exchangers": {},
        "deals": [],
        "penalties": {},
        "commission_owed": {},
        "rates": {"I2C": 100, "C2I": 97},
        "custom_commands": {},
        "client_stats": {}
    }

def save_data(data):
    with open(MAIN_DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

def get_rates():
    return load_data().get("rates", {"I2C": 100, "C2I": 97})

def get_commission_rate():
    cfg = load_config()
    return cfg["commission"]["rate_per_dollar"]

def get_channel(guild, key):
    cfg = load_config()
    ch_id = cfg["channels"].get(key, 0)
    return guild.get_channel(ch_id) if ch_id else None

# ============================================================
# OPERATION STATUS (runtime only — reset on restart)
# ============================================================

operation_status = {
    "I2C": True,
    "C2I": True,
    "accepting_exchangers": True
}

# ============================================================
# BOT SETUP
# ============================================================

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=".", intents=intents)

# ============================================================
# HELPERS
# ============================================================

def is_staff(member: discord.Member) -> bool:
    cfg = load_config()
    return any(r.id in [cfg["roles"]["owner"], cfg["roles"]["admin"], cfg["roles"]["moderator"]] for r in member.roles)

def is_admin(member: discord.Member) -> bool:
    cfg = load_config()
    return any(r.id in [cfg["roles"]["owner"], cfg["roles"]["admin"]] for r in member.roles)

def is_owner(member: discord.Member) -> bool:
    cfg = load_config()
    return any(r.id == cfg["roles"]["owner"] for r in member.roles)

def is_exchanger(member: discord.Member) -> bool:
    cfg = load_config()
    return any(r.id == cfg["roles"]["verified_exchanger"] for r in member.roles)

async def update_available_exchangers_channel(guild, data):
    cfg = load_config()
    channel = guild.get_channel(cfg["channels"].get("available_exchangers", 0))
    if not channel:
        return
    rates = data.get("rates", {"I2C": 100, "C2I": 97})
    available = [
        (uid, ex) for uid, ex in data["exchangers"].items()
        if ex.get("available") and ex.get("verified") and not ex.get("commission_suspended")
    ]
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
    cfg = load_config()
    trans_channel = guild.get_channel(cfg["channels"].get("transactions", 0))
    if not trans_channel:
        return

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
# CLOSE TICKET LOGIC
# ============================================================

async def close_ticket(channel: discord.TextChannel, ticket_data: dict, guild: discord.Guild, closer: discord.Member):
    cfg = load_config()
    await save_transcript(channel, ticket_data, guild)

    await channel.set_permissions(guild.default_role, read_messages=False, send_messages=False)
    exchanger_role = guild.get_role(cfg["roles"]["verified_exchanger"])
    if exchanger_role:
        await channel.set_permissions(exchanger_role, read_messages=False, send_messages=False)

    for role_id in [cfg["roles"]["admin"], cfg["roles"]["owner"]]:
        role = guild.get_role(role_id)
        if role:
            await channel.set_permissions(role, read_messages=True, send_messages=True)

    trans_channel_id = cfg["channels"].get("transactions", 0)
    embed = discord.Embed(
        title="🔒 Ticket Closed",
        description=f"This ticket has been closed by {closer.mention}.\n\nTranscript saved to <#{trans_channel_id}>.\n\n⚠️ Only Admins/Owner can delete this channel.",
        color=discord.Color.dark_gray()
    )
    await channel.send(embed=embed)

    data = load_data()
    for deal in data["deals"]:
        if deal.get("ticket_id") == ticket_data.get("ticket_id"):
            if deal.get("status") not in ["completed", "cancelled"]:
                deal["status"] = "closed"
            deal["closed_at"] = datetime.now().isoformat()
            break
    save_data(data)


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
        cfg = load_config()
        data = load_data()
        user_id = str(interaction.user.id)

        role = discord.utils.get(interaction.guild.roles, id=cfg["roles"]["verified_exchanger"])
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
        if exchanger.get("commission_suspended"):
            await interaction.response.send_message("❌ Your account is suspended due to unpaid commission. Pay via `/paycommission submit`.", ephemeral=True)
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
        if data["exchangers"][user_id].get("commission_suspended"):
            await interaction.response.send_message("❌ You cannot go online while suspended for unpaid commission. Use `/paycommission submit` to pay.", ephemeral=True)
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
        cfg = load_config()

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }
        for role_key in ["moderator", "admin", "owner"]:
            role = guild.get_role(cfg["roles"][role_key])
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        exchanger_role = guild.get_role(cfg["roles"]["verified_exchanger"])
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

        online_ids = [uid for uid, ex in data["exchangers"].items() if ex.get("available") and ex.get("verified") and not ex.get("commission_suspended")]
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
        commission_rate = get_commission_rate()
        exchanger_id = self.ticket_data.get("exchanger_id")
        client_id = self.ticket_data.get("client_id")
        ticket_id = self.ticket_data.get("ticket_id")
        exchange_type = self.ticket_data.get("type")
        commission = amount * commission_rate

        for deal in data["deals"]:
            if deal.get("ticket_id") == ticket_id:
                deal["status"] = "completed"
                deal["final_amount"] = amount
                deal["commission"] = commission
                deal["completed_at"] = datetime.now().isoformat()
                break

        # Track commission owed — commission bot will handle payment
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

        cfg = load_config()
        guild = interaction.guild
        completed_channel = guild.get_channel(cfg["channels"].get("completed_deals", 0))
        logs_channel = guild.get_channel(cfg["channels"].get("deal_logs", 0))

        embed = discord.Embed(title=f"✅ Deal Completed — {ticket_id}", color=discord.Color.green())
        embed.add_field(name="Type", value=exchange_type, inline=True)
        embed.add_field(name="Amount", value=f"${amount}", inline=True)
        embed.add_field(name="Exchanger", value=f"<@{exchanger_id}>", inline=True)
        embed.add_field(name="Client", value=f"<@{client_id}>", inline=True)
        embed.add_field(name="Commission", value=f"${commission:.2f}", inline=True)
        embed.add_field(name="Completed At", value=datetime.now().strftime("%d/%m/%Y %H:%M"), inline=True)
        embed.set_footer(text="NexChange — Trusted Exchange Service")

        if completed_channel:
            await completed_channel.send(embed=embed)
        if logs_channel:
            await logs_channel.send(embed=embed)

        # Check threshold alert (commission bot reads same data)
        owed_now = data["commission_owed"].get(exchanger_id, 0)
        threshold = cfg.get("commission", {}).get("alert_threshold", 50)
        if owed_now >= threshold:
            member = guild.get_member(int(exchanger_id))
            if member:
                try:
                    await member.send(
                        f"💸 **Commission Balance Alert**\n\nYour NexChange commission balance has reached **${owed_now:.2f}**.\n\nConsider paying early to avoid suspension on Saturday.\n\nUse `/paycommission view` to see payment details."
                    )
                except Exception:
                    pass

        stats = data["client_stats"][client_id]
        avg = stats["total_value"] / stats["total_exchanges"]

        vouch_template = cfg.get("messages", {}).get("vouch_template", "[Shiba vouch] ⭐ Deal done with NexChange!")

        await interaction.channel.send("✅ Thanks for choosing us, deal done.")
        await interaction.channel.send(
            f"📋 **Please copy and paste the vouch below in this channel to complete your vouch:**\n```\n{vouch_template}\n```"
        )

        stats_embed = discord.Embed(title="📊 Your NexChange Stats", color=discord.Color.gold())
        stats_embed.add_field(name="Total Exchanges", value=stats["total_exchanges"], inline=True)
        stats_embed.add_field(name="Total Volume", value=f"${stats['total_value']:,.2f}", inline=True)
        stats_embed.add_field(name="Average Exchange", value=f"${avg:,.2f}", inline=True)
        stats_embed.set_footer(text=f"Stats for {interaction.guild.get_member(int(client_id)).display_name if interaction.guild.get_member(int(client_id)) else 'Client'}")
        await interaction.channel.send(embed=stats_embed)

        await interaction.response.send_message(
            f"✅ Deal marked complete. Commission **${commission:.2f}** logged.\n\nUse `.done` or `/done` when ready to close this ticket.",
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

        cfg = load_config()
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
            "commission_suspended": False,
            "upi_slots": {"1": "", "2": "", "3": ""},
            "crypto_slots": {"1": "", "2": "", "3": ""}
        }
        save_data(data)

        guild = interaction.guild
        admin_role = guild.get_role(cfg["roles"]["admin"])
        logs_channel = guild.get_channel(cfg["channels"].get("deal_logs", 0))

        embed = discord.Embed(title="📝 New Exchanger Application", color=discord.Color.orange())
        embed.add_field(name="Applicant", value=interaction.user.mention, inline=True)
        embed.add_field(name="Requested Limit", value=f"${limit}", inline=True)
        embed.add_field(name="Required Deposit", value=f"₹{required_deposit_inr:,.0f}", inline=True)
        embed.add_field(name="Deposit Txn ID", value=self.deposit_txn.value, inline=False)
        embed.add_field(name="Shiba Username", value=self.shiba_username.value, inline=False)
        embed.add_field(name="⚠️ Staff Action", value=f"Verify **{self.shiba_username.value}** has 20+ Shiba vouches.\nUse `/verify_exchanger` or `/reject_exchanger`.", inline=False)
        embed.set_footer(text="DO NOT approve without verifying 20 Shiba vouches.")

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
        cfg = load_config()
        if "rates" not in data:
            data["rates"] = {"I2C": 100, "C2I": 97}
        old_rate = data["rates"][self.rate_type]
        data["rates"][self.rate_type] = rate
        save_data(data)

        announcements = interaction.guild.get_channel(cfg["channels"].get("announcements", 0))
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


# ============================================================
# DONE COMMAND LOGIC
# ============================================================

async def handle_done(ctx_or_interaction, is_slash=False):
    if is_slash:
        channel = ctx_or_interaction.channel
        user = ctx_or_interaction.user
        guild = ctx_or_interaction.guild
    else:
        channel = ctx_or_interaction.channel
        user = ctx_or_interaction.author
        guild = ctx_or_interaction.guild

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


# ============================================================
# SLASH COMMANDS
# ============================================================

@bot.tree.command(name="setup_panel", description="Setup the main exchange panel [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def setup_panel(interaction: discord.Interaction):
    cfg = load_config()
    rates = get_rates()
    open_ticket_id = cfg["channels"].get("open_ticket", 0)
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


@bot.tree.command(name="setup_custom_panel", description="Create a custom ticket panel [Admin only]")
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
    cfg = load_config()
    data = load_data()
    user_id = str(user.id)
    if user_id not in data["exchangers"]:
        await interaction.response.send_message("❌ No pending application found.", ephemeral=True)
        return
    data["exchangers"][user_id]["verified"] = True
    save_data(data)
    role = interaction.guild.get_role(cfg["roles"]["verified_exchanger"])
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


@bot.tree.command(name="exchanger_info", description="View your exchanger profile")
async def exchanger_info(interaction: discord.Interaction):
    data = load_data()
    user_id = str(interaction.user.id)
    if user_id not in data["exchangers"]:
        await interaction.response.send_message("❌ You are not a registered exchanger.", ephemeral=True)
        return
    ex = data["exchangers"][user_id]
    commission_owed = data["commission_owed"].get(user_id, 0)
    embed = discord.Embed(title="💎 Your Exchanger Profile", color=discord.Color.blue())
    embed.add_field(name="Name", value=ex["name"], inline=True)
    embed.add_field(name="Limit", value=f"${ex['limit']}", inline=True)
    embed.add_field(name="Status", value="🟢 Online" if ex.get("available") else "🔴 Offline", inline=True)
    embed.add_field(name="Verified", value="✅ Yes" if ex.get("verified") else "⏳ Pending", inline=True)
    embed.add_field(name="Total Deals", value=ex.get("total_deals", 0), inline=True)
    embed.add_field(name="Commission Owed", value=f"${commission_owed:.2f}", inline=True)
    embed.add_field(name="Suspended", value="⚠️ Yes — Pay commission" if ex.get("commission_suspended") else "✅ No", inline=True)
    upi = ex.get("upi_slots", {})
    crypto = ex.get("crypto_slots", {})
    embed.add_field(name="UPI Slots", value=f"1: `{upi.get('1','—')}` | 2: `{upi.get('2','—')}` | 3: `{upi.get('3','—')}`", inline=False)
    embed.add_field(name="Crypto Slots", value=f"1: `{crypto.get('1','—')}` | 2: `{crypto.get('2','—')}` | 3: `{crypto.get('3','—')}`", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="stats", description="View server statistics [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def stats(interaction: discord.Interaction):
    data = load_data()
    completed = [d for d in data["deals"] if d.get("status") == "completed"]
    total_volume = sum(d.get("final_amount", 0) for d in completed)
    total_commission = sum(d.get("commission", 0) for d in completed)
    embed = discord.Embed(title="📊 NexChange Statistics", color=discord.Color.gold())
    embed.add_field(name="Total Deals", value=len(completed), inline=True)
    embed.add_field(name="Total Volume", value=f"${total_volume:,.2f}", inline=True)
    embed.add_field(name="Total Commission", value=f"${total_commission:.2f}", inline=True)
    embed.add_field(name="Total Exchangers", value=len(data["exchangers"]), inline=True)
    embed.add_field(name="Verified", value=len([e for e in data["exchangers"].values() if e.get("verified")]), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="done", description="Close the ticket after deal is complete")
async def done_slash(interaction: discord.Interaction):
    await handle_done(interaction, is_slash=True)


@bot.tree.command(name="startallexchanges", description="Resume ALL exchanges [Admin/Owner]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def startallexchanges(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    operation_status["I2C"] = True
    operation_status["C2I"] = True
    operation_status["accepting_exchangers"] = True
    cfg = load_config()
    rates = get_rates()
    announcements = interaction.guild.get_channel(cfg["channels"].get("announcements", 0))
    open_ticket_id = cfg["channels"].get("open_ticket", 0)
    embed = discord.Embed(title="✅ ALL OPERATIONS RESUMED", description=f"All exchanges are back online.\nHead to <#{open_ticket_id}> to start.", color=discord.Color.green())
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
    cfg = load_config()
    announcements = interaction.guild.get_channel(cfg["channels"].get("announcements", 0))
    embed = discord.Embed(title="🚨 ALL OPERATIONS SUSPENDED", description=f"All exchange operations suspended.\n\n**Reason:** {reason}", color=discord.Color.dark_red())
    if announcements:
        await announcements.send(embed=embed)
    await interaction.followup.send("🚨 All operations stopped.", ephemeral=True)


@bot.tree.command(name="start_i2c", description="Open I2C exchanges [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def start_i2c(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    operation_status["I2C"] = True
    cfg = load_config()
    rates = get_rates()
    open_ticket_id = cfg["channels"].get("open_ticket", 0)
    announcements = interaction.guild.get_channel(cfg["channels"].get("announcements", 0))
    embed = discord.Embed(title="✅ INR to Crypto — NOW OPEN", description=f"Head to <#{open_ticket_id}> to start.", color=discord.Color.green())
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
    cfg = load_config()
    announcements = interaction.guild.get_channel(cfg["channels"].get("announcements", 0))
    embed = discord.Embed(title="🔴 INR to Crypto — CLOSED", description=f"**Reason:** {reason}", color=discord.Color.red())
    if announcements:
        await announcements.send(embed=embed)
    await interaction.followup.send("✅ I2C exchanges closed.", ephemeral=True)


@bot.tree.command(name="start_c2i", description="Open C2I exchanges [Admin only]")
@app_commands.checks.has_any_role("Owner", "Admin")
async def start_c2i(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    operation_status["C2I"] = True
    cfg = load_config()
    rates = get_rates()
    open_ticket_id = cfg["channels"].get("open_ticket", 0)
    announcements = interaction.guild.get_channel(cfg["channels"].get("announcements", 0))
    embed = discord.Embed(title="✅ Crypto to INR — NOW OPEN", description=f"Head to <#{open_ticket_id}> to start.", color=discord.Color.green())
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
    cfg = load_config()
    announcements = interaction.guild.get_channel(cfg["channels"].get("announcements", 0))
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
# PREFIX (.dot) COMMANDS
# ============================================================

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    content = message.content.strip()

    if content.lower() == ".done":
        await handle_done(message, is_slash=False)
        return

    dot_i2c = re.match(r"^\.i2c(\d+(\.\d+)?)$", content, re.IGNORECASE)
    dot_c2i = re.match(r"^\.c2i(\d+(\.\d+)?)$", content, re.IGNORECASE)

    if dot_i2c or dot_c2i:
        if not is_exchanger(message.author) and not is_staff(message.author):
            await message.reply("❌ Only verified exchangers can use dot commands.")
            return

        rates = get_rates()
        commission_rate = get_commission_rate()
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

        commission = usd_amount * commission_rate
        embed = discord.Embed(title=title, description=description, color=color)
        embed.add_field(name="Type", value=exchange_type, inline=True)
        embed.add_field(name="Rate", value=f"₹{rate}/$", inline=True)
        embed.add_field(name="USD Amount", value=f"${usd_amount:.2f}", inline=True)
        embed.add_field(name="INR Amount", value=f"₹{inr_amount:,.0f}", inline=True)
        embed.add_field(name="Commission", value=f"${commission:.2f}", inline=True)
        embed.set_footer(text=f"Calculated by {message.author.display_name} • NexChange")
        await message.reply(embed=embed)
        return

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

    upi_match_cmd = re.match(r"^\.upi([123])$", content, re.IGNORECASE)
    if upi_match_cmd:
        slot = upi_match_cmd.group(1)
        data = load_data()
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
            user_id = str(message.author.id)
            ex = data["exchangers"].get(user_id, {})
            upi = ex.get("upi_slots", {}).get(slot, "")
            if upi:
                await message.channel.send(f"💳 **Your UPI Slot {slot}:** `{upi}`")
            else:
                await message.channel.send(f"❌ Your UPI slot {slot} is not set. Use `/setupi` to set it.")
        return

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

    setupi_match = re.match(r"^\.setupi([123])$", content, re.IGNORECASE)
    if setupi_match:
        slot = int(setupi_match.group(1))
        await message.reply(f"Please use `/setupi slot:{slot}` to set your UPI slot.")
        return

    setcrypto_match = re.match(r"^\.setcrypto([123])$", content, re.IGNORECASE)
    if setcrypto_match:
        slot = int(setcrypto_match.group(1))
        await message.reply(f"Please use `/setcrypto slot:{slot}` to set your crypto slot.")
        return

    if content.lower() == ".startallexchanges":
        if not is_admin(message.author):
            await message.reply("❌ Only Admins/Owner can use this command.")
            return
        operation_status["I2C"] = True
        operation_status["C2I"] = True
        operation_status["accepting_exchangers"] = True
        cfg = load_config()
        rates = get_rates()
        announcements = message.guild.get_channel(cfg["channels"].get("announcements", 0))
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
        cfg = load_config()
        announcements = message.guild.get_channel(cfg["channels"].get("announcements", 0))
        embed = discord.Embed(title="🚨 ALL OPERATIONS SUSPENDED", description=f"**Reason:** {reason}", color=discord.Color.dark_red())
        if announcements:
            await announcements.send(embed=embed)
        await message.reply("🚨 All operations stopped.")
        return

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
# BOT EVENTS
# ============================================================

@bot.event
async def on_ready():
    print(f"✅ NexChange Main Bot online as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"❌ Sync error: {e}")
    bot.add_view(ExchangeTypeView())
    bot.add_view(AvailabilityView())


# ============================================================
# RUN
# ============================================================
bot.run(BOT_TOKEN)
