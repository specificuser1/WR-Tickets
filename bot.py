import discord
from discord.ext import commands
from discord import app_commands
import json
import asyncio
import datetime
from typing import Optional, Dict, Any
import logging
import os
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TicketBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        
        super().__init__(
            command_prefix='!',
            intents=intents,
            help_command=None
        )
        
        self.config = self.load_config()
        self.tickets = {}  # Store active tickets
        
    def load_config(self) -> Dict[str, Any]:
        """Load configuration from config.json"""
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error("config.json file not found!")
            return {}
        except json.JSONDecodeError:
            logger.error("Invalid JSON in config.json!")
            return {}
    
    def save_config(self):
        """Save configuration to config.json"""
        try:
            with open('config.json', 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving config: {e}")
    
    async def setup_hook(self):
        """Setup hook for slash commands"""
        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} command(s)")
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")
    
    async def on_ready(self):
        logger.info(f'{self.user} has connected to Discord!')
        logger.info(f'Bot is in {len(self.guilds)} guilds')
        
        # Set bot status
        activity = discord.Activity(
            type=discord.ActivityType.playing,
            name="TICKET SYSTEM"
        )
        await self.change_presence(activity=activity)
    
    async def on_member_remove(self, member):
        """Auto-close tickets when member leaves"""
        guild_id = str(member.guild.id)
        if guild_id in self.tickets:
            for ticket_id, ticket_data in list(self.tickets[guild_id].items()):
                if ticket_data['opener_id'] == member.id:
                    # Close the ticket
                    channel = self.get_channel(ticket_data['channel_id'])
                    if channel:
                        await self.close_ticket(
                            channel=channel,
                            closer=self.user,
                            reason="Member left the server"
                        )
# Ticket Selection View
class TicketSelectView(discord.ui.View):
    def __init__(self, bot: TicketBot):
        super().__init__(timeout=None)
        self.bot = bot
        
        # Create select menu with options from config
        options = []
        for option in bot.config.get('ticket_options', []):
            if option.get('enabled', True):
                options.append(discord.SelectOption(
                    label=option['label'],
                    description=option['description'],
                    emoji=option.get('emoji'),
                    value=option['value']
                ))
        
        if options:
            self.ticket_select.options = options
    
    @discord.ui.select(
        placeholder="🎫 Select ticket type...",
        min_values=1,
        max_values=1
    )
    async def ticket_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        
        # Check if user already has an open ticket
        guild_id = str(interaction.guild_id)
        if guild_id in self.bot.tickets:
            for ticket_data in self.bot.tickets[guild_id].values():
                if ticket_data['opener_id'] == interaction.user.id:
                    await interaction.followup.send(
                        "❌ You already have an open ticket! Please close it before opening a new one.",
                        ephemeral=True
                    )
                    return
        
        # Find the selected option
        selected_option = None
        for option in self.bot.config.get('ticket_options', []):
            if option['value'] == select.values[0]:
                selected_option = option
                break
        
        if not selected_option:
            await interaction.followup.send("❌ Invalid ticket option!", ephemeral=True)
            return
        
        try:
            # Create ticket channel
            guild = interaction.guild
            category = discord.utils.get(guild.categories, id=selected_option['category_id'])
            
            if not category:
                await interaction.followup.send("❌ Ticket category not found!", ephemeral=True)
                return
            
            # Generate ticket number
            guild_tickets = self.bot.tickets.get(guild_id, {})
            ticket_number = len(guild_tickets) + 1
            
            # Create channel
            channel_name = f"🎫-{ticket_number}"
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            
            # Add support role permissions if configured
            support_role_id = self.bot.config.get('support_role_id')
            allowed_support_categories = self.bot.config.get('support_role_allowed_categories', [])
            if support_role_id:
                    support_role = guild.get_role(support_role_id)
                    if support_role and selected_option['category_id'] in allowed_support_categories:
                        overwrites[support_role] = discord.PermissionOverwrite(
                            read_messages=True,
                            send_messages=True
                        )
                  
            # Add admin permissions
            for admin_id in self.bot.config.get('admin_ids', []):
                admin_user = guild.get_member(admin_id)
                if admin_user:
                    overwrites[admin_user] = discord.PermissionOverwrite(
                        read_messages=True,
                        send_messages=True,
                        manage_channels=True
                    )
            
            channel = await category.create_text_channel(
                name=channel_name,
                overwrites=overwrites
            )
            
            # Store ticket data
            if guild_id not in self.bot.tickets:
                self.bot.tickets[guild_id] = {}
            
            self.bot.tickets[guild_id][str(channel.id)] = {
                'ticket_id': ticket_number,
                'opener_id': interaction.user.id,
                'channel_id': channel.id,
                'ticket_type': selected_option['label'],
                'opened_at': datetime.datetime.utcnow().isoformat(),
                'status': 'open'
            }
            
            # Create ticket embed
            embed = discord.Embed(
                title=f"🎫 Ticket #{ticket_number}",
                description=f"**Ticket Type:** {selected_option['label']}\n**Opened by:** {interaction.user.mention}\n**Opened at:** <t:{int(datetime.datetime.utcnow().timestamp())}:F>",
                color=0x00ff00,
                timestamp=datetime.datetime.utcnow()
            )
            embed.set_footer(text="Warrior Ticket System", icon_url=guild.icon.url if guild.icon else None)
            
            # Create close button view
            close_view = TicketCloseView(self.bot)
            
            # Send ticket message with mentions
            mentions = []
            if support_role_id:
                support_role = guild.get_role(support_role_id)
                if support_role:
                    mentions.append(support_role.mention)
            
            owner_id = self.bot.config.get('owner_id')
            if owner_id:
                owner = guild.get_member(owner_id)
                if owner:
                    mentions.append(owner.mention)
            
            mention_text = " ".join(mentions) if mentions else ""
            
            await channel.send(
                content=f"{interaction.user.mention} {mention_text}",
                embed=embed,
                view=close_view
            )
            
            # Log ticket opening
            await self.log_ticket_open(guild, interaction.user, ticket_number, selected_option['label'])
            
            await interaction.followup.send(
                f"✅ Ticket created! Check {channel.mention}",
                ephemeral=True
            )
            
        except Exception as e:
            logger.error(f"Error creating ticket: {e}")
            await interaction.followup.send("❌ Error creating ticket!", ephemeral=True)
    
    async def log_ticket_open(self, guild, user, ticket_number, ticket_type):
        """Log ticket opening"""
        log_channel_id = self.bot.config.get('ticket_open_log_channel')
        if not log_channel_id:
            return
        
        log_channel = guild.get_channel(log_channel_id)
        if not log_channel:
            return
        
        embed = discord.Embed(
            title="🎫 Ticket Opened",
            description=f"**Opener:** {user.mention} ({user})\n**Ticket:** #{ticket_number}\n**Type:** {ticket_type}\n**Date:** <t:{int(datetime.datetime.utcnow().timestamp())}:F>",
            color=0x00ff00,
            timestamp=datetime.datetime.utcnow()
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        
        try:
            await log_channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Error logging ticket open: {e}")

# Ticket Close View
class TicketCloseView(discord.ui.View):
    def __init__(self, bot: TicketBot):
        super().__init__(timeout=None)
        self.bot = bot
    
    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if user has permission to close
        if not self.can_close_ticket(interaction.user, interaction.guild):
            await interaction.response.send_message(
                "❌ You don't have permission to close this ticket!",
                ephemeral=True
            )
            return
        
        await interaction.response.send_modal(CloseTicketModal(self.bot, interaction.channel))
    
    def can_close_ticket(self, user, guild):
        """Check if user can close ticket"""
        # Check if admin
        if user.id in self.bot.config.get('admin_ids', []):
            return True
        
        # Check if support role
        support_role_id = self.bot.config.get('support_role_id')
        if support_role_id and discord.utils.get(user.roles, id=support_role_id):
            return True
        
        # Check if owner
        if user.id == self.bot.config.get('owner_id'):
            return True
        
        return False

# Close Ticket Modal
class CloseTicketModal(discord.ui.Modal, title="Close Ticket"):
    def __init__(self, bot: TicketBot, channel):
        super().__init__()
        self.bot = bot
        self.channel = channel
    
    reason = discord.ui.TextInput(
        label="Reason for closing",
        placeholder="Enter reason for closing this ticket...",
        required=False,
        max_length=500
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        reason = self.reason.value or "No reason provided"
        await self.bot.close_ticket(self.channel, interaction.user, reason)
        
        await interaction.followup.send("✅ Ticket will be closed in 5 seconds!")

# Bot instance
bot = TicketBot()

# Add close_ticket method to bot
async def close_ticket(self, channel, closer, reason="No reason provided"):
    """Close a ticket"""
    guild_id = str(channel.guild.id)
    channel_id = str(channel.id)
    
    # Get ticket data
    if guild_id not in self.tickets or channel_id not in self.tickets[guild_id]:
        return
    
    ticket_data = self.tickets[guild_id][channel_id]
    opener = self.get_user(ticket_data['opener_id'])
    
    # Send DM to opener
    if opener:
        embed = discord.Embed(
            title="🎫 Ticket Closed",
            description=f"**Opened by:** {opener.mention}\n**Closed by:** {closer.mention}\n**Opened:** <t:{int(datetime.datetime.fromisoformat(ticket_data['opened_at']).timestamp())}:F>\n**Closed:** <t:{int(datetime.datetime.utcnow().timestamp())}:F>\n**Reason:** {reason}",
            color=0xff0000,
            timestamp=datetime.datetime.utcnow()
        )
        
        try:
            await opener.send(embed=embed)
        except:
            pass  # User has DMs disabled
    
    # Log ticket closing
    await self.log_ticket_close(channel.guild, opener, ticket_data['ticket_id'], closer, reason)
    
    # Remove from active tickets
    del self.tickets[guild_id][channel_id]
    
    # Delete channel after 5 seconds
    await asyncio.sleep(5)
    try:
        await channel.delete()
    except:
        pass

# Add method to bot class
TicketBot.close_ticket = close_ticket

async def log_ticket_close(self, guild, opener, ticket_number, closer, reason):
    """Log ticket closing"""
    log_channel_id = self.config.get('ticket_close_log_channel')
    if not log_channel_id:
        return
    
    log_channel = guild.get_channel(log_channel_id)
    if not log_channel:
        return
    
    embed = discord.Embed(
        title="🔒 Ticket Closed",
        description=f"**Opener:** {opener.mention if opener else 'Unknown'} ({opener if opener else 'Unknown'})\n**Ticket:** #{ticket_number}\n**Closed by:** {closer.mention} ({closer})\n**Reason:** {reason}\n**Date:** <t:{int(datetime.datetime.utcnow().timestamp())}:F>",
        color=0xff0000,
        timestamp=datetime.datetime.utcnow()
    )
    if opener:
        embed.set_thumbnail(url=opener.display_avatar.url)
    
    try:
        await log_channel.send(embed=embed)
    except Exception as e:
        logger.error(f"Error logging ticket close: {e}")

# Add method to bot class
TicketBot.log_ticket_close = log_ticket_close

# Commands
@bot.command(name='ticketpanel')
async def ticket_panel(ctx):
    """Create ticket panel (Admin only)"""
    if ctx.author.id not in bot.config.get('admin_ids', []) and ctx.author.id != bot.config.get('owner_id'):
        await ctx.send("❌ You don't have permission to use this command!")
        return
    
    # Get panel config
    panel_config = bot.config.get('ticket_panel', {})
    
    embed = discord.Embed(
        title=panel_config.get('title', '🎫 Ticket System'),
        description=panel_config.get('description', 'Select a ticket type from the menu below:'),
        color=int(panel_config.get('color', '0x00ff00'), 16)
    )
    
    if panel_config.get('thumbnail'):
        embed.set_thumbnail(url=panel_config['thumbnail'])
    
    if panel_config.get('image'):
        embed.set_image(url=panel_config['image'])
    
    if panel_config.get('footer'):
        embed.set_footer(
            text=panel_config['footer'],
            icon_url=panel_config.get('footer_icon')
        )
    
    # Add title icon if available
    title_icon = panel_config.get('title_icon')
    if title_icon:
        embed.set_author(name=panel_config.get('title', '🎫 Ticket System'), icon_url=title_icon)
    
    view = TicketSelectView(bot)
    await ctx.send(embed=embed, view=view)
    
    # Delete command message
    try:
        await ctx.message.delete()
    except:
        pass

# Slash Commands
@bot.tree.command(name="addnewoption", description="Add new ticket option")
@app_commands.describe(
    label="Option label",
    description="Option description",
    value="Option value (unique)",
    emoji="Option emoji",
    category_id="Category ID where tickets will be created"
)
async def add_new_option(interaction: discord.Interaction, label: str, description: str, value: str, category_id: str, emoji: Optional[str] = None):
    if interaction.user.id not in bot.config.get('admin_ids', []) and interaction.user.id != bot.config.get('owner_id'):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    try:
        category_id = int(category_id)
        category = interaction.guild.get_channel(category_id)
        if not category or not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message("❌ Invalid category ID!", ephemeral=True)
            return
        
        # Check if value already exists
        for option in bot.config.get('ticket_options', []):
            if option['value'] == value:
                await interaction.response.send_message("❌ Option value already exists!", ephemeral=True)
                return
        
        new_option = {
            'label': label,
            'description': description,
            'value': value,
            'emoji': emoji,
            'category_id': category_id,
            'enabled': True
        }
        
        if 'ticket_options' not in bot.config:
            bot.config['ticket_options'] = []
        
        bot.config['ticket_options'].append(new_option)
        bot.save_config()
        
        await interaction.response.send_message(f"✅ Added new option: **{label}**", ephemeral=True)
        
    except ValueError:
        await interaction.response.send_message("❌ Invalid category ID format!", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)

@bot.tree.command(name="removeoption", description="Temporarily disable ticket option")
@app_commands.describe(value="Option value to remove")
async def remove_option(interaction: discord.Interaction, value: str):
    if interaction.user.id not in bot.config.get('admin_ids', []) and interaction.user.id != bot.config.get('owner_id'):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    for option in bot.config.get('ticket_options', []):
        if option['value'] == value:
            option['enabled'] = False
            bot.save_config()
            await interaction.response.send_message(f"✅ Disabled option: **{option['label']}**", ephemeral=True)
            return
    
    await interaction.response.send_message("❌ Option not found!", ephemeral=True)

@bot.tree.command(name="addoption", description="Re-enable disabled ticket option")
@app_commands.describe(value="Option value to re-enable")
async def add_option(interaction: discord.Interaction, value: str):
    if interaction.user.id not in bot.config.get('admin_ids', []) and interaction.user.id != bot.config.get('owner_id'):
        await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)
        return
    
    for option in bot.config.get('ticket_options', []):
        if option['value'] == value:
            option['enabled'] = True
            bot.save_config()
            await interaction.response.send_message(f"✅ Enabled option: **{option['label']}**", ephemeral=True)
            return
    
    await interaction.response.send_message("❌ Option not found!", ephemeral=True)

# Error handler
@bot.event
async def on_error(event, *args, **kwargs):
    logger.error(f"Error in {event}: {args}, {kwargs}")

if __name__ == "__main__":
    # Check if config exists
    if not os.path.exists('config.json'):
        print("❌ config.json not found! Please create it first.")
        exit(1)
    
    # Load token
    try:
        with open('config.json', 'r', encoding='utf-8') as f:   # ✅ FIXED HERE
            config = json.load(f)
            token = config.get('bot_token')
            if not token:
                print("❌ Bot token not found in config.json!")
                exit(1)
    except Exception as e:
        print(f"❌ Error loading config: {e}")
        exit(1)
    
    # Run bot
    bot.run(token)
