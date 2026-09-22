import discord
from discord.ext import commands
from discord.ui import Button, View, Select
import random
import string
import requests
import base64
import json
import asyncio
import os

# --- CONFIGURATION ---

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Permitted usernames or admins
ALLOWED_USERS = ["8y.o", "wallyisbad"]

# GitHub Configuration
GITHUB_REPO = "walx-x/kosmoskeystxt"
FILE_PATH = "KosmosPaidKeys.json"

# Discord Role Names
VISITOR_ROLE_NAME = "Visitor"
KOSMO_MEMBER_ROLE_NAME = "KoSMO Member"
MOD_ROLE_NAME = "Mod"  # Staff role allowed to claim and view tickets

# Image URLs
BANNER_IMAGE_URL = "https://media.discordapp.net/attachments/1483232566659125350/1550253828182646875/Gemini_Generated_Image_fnc696fnc696fnc6.jpg?ex=6aaefb62&is=6aada9e2&hm=df9f5fc001e0295dc566191389671abbca5a244055003b683739100b288bba87&=&format=webp"

# Status Configurations
STATUS_CONFIG = {
    "active": {
        "title": "🟢 STATUS: ACTIVE",
        "color": discord.Color.green(),
        "image": "https://media.discordapp.net/attachments/1483232566659125350/1550651429222617189/gemini-svg_4.png?ex=6aaf1c2e&is=6aadcaae&hm=79494f277193c5a58ebb4441c33de1a028c7d3d92e38bf8d66b6bd828772352f&=&format=webp&quality=lossless"
    },
    "updating": {
        "title": "🟡 STATUS: UPDATING",
        "color": discord.Color.gold(),
        "image": "https://media.discordapp.net/attachments/1483232566659125350/1550651430077997127/gemini-svg_5.png?ex=6aaf1c2e&is=6aadcaae&hm=f3d00300123bdf97d573da7d4cac2e9bf9e1aa1c3e53c3ff223723af6a149bb3&=&format=webp&quality=lossless"
    },
    "down": {
        "title": "🔴 STATUS: DOWN",
        "color": discord.Color.red(),
        "image": "https://media.discordapp.net/attachments/1483232566659125350/1550651430703206410/gemini-svg_6.png?ex=6aaf1c2e&is=6aadcaae&hm=88bdcc858095c38a51024be1c1cf59f44e234d01133fcb1b5df6232b7343769d&=&format=webp&quality=lossless"
    },
    "patched": {
        "title": "🔵 STATUS: PATCHED",
        "color": discord.Color.blue(),
        "image": "https://media.discordapp.net/attachments/1483232566659125350/1550651431378227260/gemini-svg_7.png?ex=6aaf1c2e&is=6aadcaae&hm=1df3466509b29571c6db4d08263f53d690f59236637813c0f9bd320438494294&=&format=webp&quality=lossless"
    }
}


# --- AUTOMATIC ROLE MANAGEMENT ---

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.roles != after.roles:
        has_visitor = any(role.name == VISITOR_ROLE_NAME for role in after.roles)
        has_kosmo_member = any(role.name == KOSMO_MEMBER_ROLE_NAME for role in after.roles)

        if has_visitor and has_kosmo_member:
            visitor_role = discord.utils.get(after.guild.roles, name=VISITOR_ROLE_NAME)
            if visitor_role:
                try:
                    await after.remove_roles(visitor_role)
                except Exception as e:
                    print(f"[ROLE ERROR] {e}")


# --- GITHUB HELPER FUNCTIONS ---

def get_github_data():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    res = requests.get(url, headers=headers)
    if res.status_code == 401:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
        res = requests.get(url, headers=headers)

    if res.status_code == 200:
        data = res.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        try:
            keys_data = json.loads(content)
        except:
            keys_data = {}
        return keys_data, data["sha"], headers
    return {}, None, headers


def update_github_data(keys_data, sha, headers):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{FILE_PATH}"
    updated_content = json.dumps(keys_data, indent=4)
    encoded_content = base64.b64encode(updated_content.encode("utf-8")).decode("utf-8")

    payload = {
        "message": "Updated keys database via Discord Bot",
        "content": encoded_content,
        "branch": "main"
    }
    if sha:
        payload["sha"] = sha

    push_res = requests.put(url, json=payload, headers=headers)
    return push_res.status_code in [200, 201]


# --- TICKET SYSTEM VIEWS & SELECTS ---

class TicketControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.green, custom_id="btn_claim_ticket", emoji="🔒")
    async def claim_button(self, interaction: discord.Interaction, button: Button):
        mod_role = discord.utils.get(interaction.guild.roles, name=MOD_ROLE_NAME)
        has_mod_perm = (mod_role in interaction.user.roles) or (interaction.user.name in ALLOWED_USERS)

        if not has_mod_perm:
            await interaction.response.send_message("❌ Only staff members with the **Mod** role can claim tickets!", ephemeral=True)
            return

        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}"
        button.style = discord.ButtonStyle.secondary
        
        await interaction.response.edit_message(view=self)
        
        embed = discord.Embed(
            title="🎫 Ticket Claimed",
            description=f"This ticket is now being handled by {interaction.user.mention}.",
            color=discord.Color.blue()
        )
        await interaction.channel.send(embed=embed)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, custom_id="btn_close_ticket", emoji="⛔")
    async def close_button(self, interaction: discord.Interaction, button: Button):
        mod_role = discord.utils.get(interaction.guild.roles, name=MOD_ROLE_NAME)
        has_mod_perm = (mod_role in interaction.user.roles) or (interaction.user.name in ALLOWED_USERS)

        if not has_mod_perm:
            await interaction.response.send_message("❌ You do not have permission to close this ticket!", ephemeral=True)
            return

        await interaction.response.send_message("🔒 Generating transcript and closing ticket in 5 seconds...")

        messages = []
        async for message in interaction.channel.history(limit=1000, oldest_first=True):
            timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
            messages.append(f"[{timestamp}] {message.author} ({message.author.id}): {message.clean_content}")

        transcript_text = f"=== TRANSCRIPT FOR {interaction.channel.name.upper()} ===\n\n" + "\n".join(messages)

        file_path = f"{interaction.channel.name}_transcript.txt"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(transcript_text)

        log_channel_name = '⮚︱logs❗'
        log_channel = discord.utils.get(interaction.guild.text_channels, name=log_channel_name)

        if log_channel:
            embed_log = discord.Embed(
                title="📝 Ticket Closed & Transcribed",
                description=f"**Ticket:** `{interaction.channel.name}`\n**Closed by:** {interaction.user.mention}",
                color=discord.Color.red()
            )
            try:
                await log_channel.send(embed=embed_log, file=discord.File(file_path))
            except Exception as e:
                print(f"[LOG ERROR] Failed to send transcript to log channel: {e}")
        else:
            print(f"[LOG ERROR] Could not find text channel named: {log_channel_name}")

        if os.path.exists(file_path):
            os.remove(file_path)

        await asyncio.sleep(5)
        await interaction.channel.delete()


class PlanSelectEphemeral(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="KoSMO STANDARD — 1 Week (€1.49)", value="STANDARD 1 Week (€1.49)", emoji="⚙️"),
            discord.SelectOption(label="KoSMO STANDARD — 1 Month (€2.99)", value="STANDARD 1 Month (€2.99)", emoji="⚙️"),
            discord.SelectOption(label="KoSMO STANDARD — Lifetime (€4.99)", value="STANDARD Lifetime (€4.99)", emoji="⚙️"),
            discord.SelectOption(label="KoSMO PRO — 1 Week (€6.99)", value="PRO 1 Week (€6.99)", emoji="🚀"),
            discord.SelectOption(label="KoSMO PRO — 1 Month (€7.99)", value="PRO 1 Month (€7.99)", emoji="🚀"),
            discord.SelectOption(label="KoSMO PRO — Lifetime (€11.99)", value="PRO Lifetime (€11.99)", emoji="🚀"),
            discord.SelectOption(label="KoSMO VIP — 1 Month (€14.99)", value="VIP 1 Month (€14.99)", emoji="👑"),
            discord.SelectOption(label="KoSMO VIP — Lifetime (€24.99)", value="VIP Lifetime (€24.99)", emoji="👑"),
        ]
        super().__init__(placeholder="Select the plan you want to buy...", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        user = interaction.user
        selected_plan = self.values[0]

        clean_username = user.name.lower().replace('.', '')
        ticket_channel_name = f"buy-{clean_username}"

        existing_channel = discord.utils.get(guild.channels, name=ticket_channel_name)
        if existing_channel:
            await interaction.followup.send(f"❌ You already have an open purchase ticket: {existing_channel.mention}", ephemeral=True)
            return

        category_name = "───🎫Tickets ───"
        ticket_category = discord.utils.get(guild.categories, name=category_name)
        if not ticket_category:
            try:
                ticket_category = await guild.create_category(category_name)
            except Exception as e:
                print(f"[CATEGORY ERROR] Could not create category: {e}")

        mod_role = discord.utils.get(guild.roles, name=MOD_ROLE_NAME)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        if mod_role:
            overwrites[mod_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        ticket_channel = await guild.create_text_channel(
            name=ticket_channel_name,
            category=ticket_category,
            overwrites=overwrites,
            reason=f"Purchase ticket opened by {user.name}"
        )

        await interaction.followup.send(f"✅ Purchase Ticket created! Check out {ticket_channel.mention}", ephemeral=True)

        buy_embed = discord.Embed(
            title="🚀 KoSMO — Purchase Hub",
            description=(
                "Thank you for choosing us!\n\n"
                "**Accepted Payment Methods:**\n"
                "- Crypto (LTC / BTC / USDT) <:emoji_3:1550284088269869127>\n"
                "- Revolut <:emoji_2:1550284071618478220>\n"
                "- PayPal (Friends & Family) <:emoji_1:1550284056145559593>\n"
                "- PayPal Giftcards <:emoji_5:1550284178090631310>\n"
                "- Robux / Roblox Giftcards <:emoji_4:1550284144473407508>\n\n"
                "**How to Order:**\n"
                "A staff member will assist you shortly with payment details!"
            ),
            color=discord.Color.purple()
        )
        buy_embed.set_footer(text="KoSMO Hub • Fast, Automated & Secure Delivery")

        order_details_embed = discord.Embed(
            title="🛒 User Order Details",
            color=discord.Color.green()
        )
        order_details_embed.add_field(name="Selected Plan", value=f"`{selected_plan}`", inline=False)

        await ticket_channel.send(
            content=f"Welcome {user.mention}! {mod_role.mention if mod_role else ''}", 
            embeds=[buy_embed, order_details_embed], 
            view=TicketControlView()
        )


class PlanStepView(View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(PlanSelectEphemeral())


class TicketPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Buy / Purchase", style=discord.ButtonStyle.green, custom_id="btn_create_buy_ticket", emoji="🛒")
    async def buy_ticket_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(
            content="🛒 Please select your preferred plan below:",
            view=PlanStepView(),
            ephemeral=True
        )


class SupportPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Get Support", style=discord.ButtonStyle.blurple, custom_id="btn_create_support_ticket", emoji="🛠️")
    async def support_ticket_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        user = interaction.user

        clean_username = user.name.lower().replace('.', '')
        ticket_channel_name = f"support-{clean_username}"

        existing_channel = discord.utils.get(guild.channels, name=ticket_channel_name)
        if existing_channel:
            await interaction.followup.send(f"❌ You already have an open support ticket: {existing_channel.mention}", ephemeral=True)
            return

        category_name = "───🎫Tickets ───"
        ticket_category = discord.utils.get(guild.categories, name=category_name)
        if not ticket_category:
            try:
                ticket_category = await guild.create_category(category_name)
            except Exception as e:
                print(f"[CATEGORY ERROR] Could not create category: {e}")

        mod_role = discord.utils.get(guild.roles, name=MOD_ROLE_NAME)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        if mod_role:
            overwrites[mod_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        ticket_channel = await guild.create_text_channel(
            name=ticket_channel_name,
            category=ticket_category,
            overwrites=overwrites,
            reason=f"Support ticket opened by {user.name}"
        )

        await interaction.followup.send(f"✅ Support Ticket created! Check out {ticket_channel.mention}", ephemeral=True)

        support_embed = discord.Embed(
            title="🛠️ KoSMO — Support Center",
            description=(
                f"Welcome {user.mention}!\n\n"
                "Please describe your issue in detail so our support team can assist you as fast as possible.\n\n"
                "**Information to include:**\n"
                "- Detailed description of your problem\n"
                "- Screenshots or error logs (if applicable)\n"
                "- Your order ID or key (if related to a purchase)\n\n"
                "A staff member will be with you shortly!"
            ),
            color=discord.Color.blue()
        )
        support_embed.set_footer(text="KoSMO Hub • Official Technical Support")

        await ticket_channel.send(
            content=f"Welcome {user.mention}! {mod_role.mention if mod_role else ''}", 
            embed=support_embed, 
            view=TicketControlView()
        )


# --- KEY MANAGEMENT INTERACTIVE VIEWS ---

class DeleteKeySelect(discord.ui.Select):
    def __init__(self, keys_data, sha, headers):
        options = [discord.SelectOption(label=k, value=k) for k in list(keys_data.keys())[:25]]
        super().__init__(placeholder="Select a key to delete...", options=options)
        self.sha = sha
        self.headers = headers

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.name not in ALLOWED_USERS:
            await interaction.response.send_message("❌ You do not have permission to perform this action!", ephemeral=True)
            return

        key_to_delete = self.values[0]
        keys_data, sha, headers = get_github_data()

        if key_to_delete in keys_data:
            del keys_data[key_to_delete]
            if update_github_data(keys_data, sha, headers):
                await interaction.response.send_message(f"🗑️ Key `{key_to_delete}` successfully deleted!", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Error deleting key from GitHub.", ephemeral=True)


class KeyManagementView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Key", style=discord.ButtonStyle.green, custom_id="btn_create_key")
    async def create_key_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.name not in ALLOWED_USERS:
            await interaction.response.send_message("❌ You do not have permission!", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        chars = string.ascii_uppercase + string.digits
        new_key = f"KoSMO-{''.join(random.choices(chars, k=4))}-{''.join(random.choices(chars, k=4))}"

        keys_data, sha, headers = get_github_data()
        keys_data[new_key] = {"hwid": None, "used": False}

        if update_github_data(keys_data, sha, headers):
            await interaction.followup.send(f"🔑 **New Single-Device Key Created:** `{new_key}`", ephemeral=True)
        else:
            await interaction.followup.send("❌ Error saving key to GitHub.", ephemeral=True)

    @discord.ui.button(label="Delete Key", style=discord.ButtonStyle.red, custom_id="btn_delete_key")
    async def delete_key_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.name not in ALLOWED_USERS:
            await interaction.response.send_message("❌ You do not have permission!", ephemeral=True)
            return

        keys_data, sha, headers = get_github_data()
        if not keys_data:
            await interaction.response.send_message("❌ No keys available to delete.", ephemeral=True)
            return

        view = View()
        view.add_item(DeleteKeySelect(keys_data, sha, headers))
        await interaction.response.send_message("Select the key you want to delete:", view=view, ephemeral=True)


# --- DISCORD COMMANDS ---

@bot.command(name="status")
async def status_command(ctx, state: str = None):
    if ctx.author.name not in ALLOWED_USERS:
        await ctx.send(f"❌ {ctx.author.mention}, you do not have permission to update the status!", delete_after=5)
        return

    try:
        await ctx.message.delete()
    except Exception:
        pass

    if not state or state.lower() not in STATUS_CONFIG:
        await ctx.send("❌ Correct usage: `!status active` | `!status updating` | `!status down` | `!status patched`", delete_after=5)
        return

    state_key = state.lower()
    config = STATUS_CONFIG[state_key]

    target_channel_name = "⮚︱status"
    status_channel = discord.utils.get(ctx.guild.text_channels, name=target_channel_name)

    if not status_channel:
        await ctx.send(f"❌ Unable to find channel `{target_channel_name}`!", delete_after=5)
        return

    embed = discord.Embed(
        title=config["title"],
        description=f"Software status updated to **{state_key.upper()}**.",
        color=config["color"]
    )
    embed.set_image(url=config["image"])
    embed.set_footer(text=f"Updated by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)

    await status_channel.send(embed=embed)


@bot.command()
async def ticketpanel(ctx):
    if ctx.author.name not in ALLOWED_USERS:
        await ctx.send(f"❌ {ctx.author.mention}, you do not have permission to send the ticket panel!")
        return

    embed = discord.Embed(
        title="📩 KoSMO - Purchase Panel",
        description=(
            "Welcome to the **KoSMO Hub** purchase portal!\n\n"
            "Click the button below to select your plan and open a private ticket.\n\n"
            "⏰ **Support Hours:** 24/7 Support\n"
            "⚠️ Please do not create multiple tickets at once."
        ),
        color=discord.Color.purple()
    )
    embed.set_image(url=BANNER_IMAGE_URL)
    embed.set_footer(text="KoSMO Hub • Official Ticket System")

    await ctx.send(embed=embed, view=TicketPanelView())


@bot.command()
async def supportpanel(ctx):
    if ctx.author.name not in ALLOWED_USERS:
        await ctx.send(f"❌ {ctx.author.mention}, you do not have permission to send the support panel!")
        return

    embed = discord.Embed(
        title="🛠️ KoSMO - Support Portal",
        description=(
            "Welcome to the **KoSMO Hub** Support Helpdesk!\n\n"
            "Need help or experiencing an issue? Click the button below to open a private support ticket.\n\n"
            "⏰ **Support Hours:** 24/7 Support\n"
            "⚠️ Please explain your problem clearly inside the ticket."
        ),
        color=discord.Color.blue()
    )
    embed.set_image(url=BANNER_IMAGE_URL)
    embed.set_footer(text="KoSMO Hub • Official Support Panel")

    await ctx.send(embed=embed, view=SupportPanelView())


@bot.command()
async def genkey(ctx, member: discord.Member = None):
    if ctx.author.name not in ALLOWED_USERS:
        await ctx.send(f"❌ {ctx.author.mention}, you do not have permission to use this command!")
        return

    target_user = member if member else ctx.author

    chars = string.ascii_uppercase + string.digits
    new_key = f"KoSMO-{''.join(random.choices(chars, k=4))}-{''.join(random.choices(chars, k=4))}"

    msg = await ctx.send("⏳ *Saving key to GitHub...*")

    keys_data, sha, headers = get_github_data()
    keys_data[new_key] = {"hwid": None, "used": False}

    if update_github_data(keys_data, sha, headers):
        embed = discord.Embed(
            title="🚀 Welcome to KoSMO Hub",
            description="Your access key has been successfully generated!",
            color=discord.Color.purple()
        )
        embed.add_field(name="🔑 Your Key", value=f"`{new_key}`", inline=False)
        embed.add_field(name="⚠️ Single-Use Warning", value="This key binds strictly to **1 device** (HWID Locked) upon first use.", inline=False)
        embed.add_field(name="✨ Status", value="Enjoy **KoSMO** and play smart!", inline=False)
        embed.set_image(url=BANNER_IMAGE_URL)
        embed.set_footer(text="KoSMO Hub System • Single-Device Authentication")

        try:
            await target_user.send(embed=embed)
            if target_user != ctx.author:
                await msg.edit(content=f"✅ Key generated and sent via DM Embed to {target_user.mention}!")
            else:
                await msg.edit(content=f"✅ {ctx.author.mention}, your key is ready! Check your Direct Messages.")
        except:
            await msg.edit(content=f"✅ Key generated: `{new_key}` (Failed to send DM to {target_user.mention}, check DM settings).")
    else:
        await msg.edit(content="❌ Error saving key to GitHub. Please check the logs.")


@bot.command(name="keylist", aliases=["kaylist"])
async def keylist(ctx):
    if ctx.author.name not in ALLOWED_USERS:
        await ctx.send(f"❌ {ctx.author.mention}, you do not have permission!")
        return

    keys_data, _, _ = get_github_data()
    embed = discord.Embed(title="🔑 KoSMO Key Management (HWID Bound)", color=discord.Color.purple())

    if keys_data:
        formatted = []
        for k, v in keys_data.items():
            status = "🔴 Used" if v.get("used") else "🟢 Available"
            formatted.append(f"`{k}` | {status}")
        embed.add_field(name=f"Total Keys ({len(keys_data)})", value="\n".join(formatted), inline=False)
    else:
        embed.description = "*No keys currently saved.*"

    await ctx.send(embed=embed, view=KeyManagementView())


GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]


@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user} (ID: {bot.user.id})")


bot.run(DISCORD_TOKEN)
