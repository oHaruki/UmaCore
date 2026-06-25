"""
Club management commands (add, remove, edit, list)
"""
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, time
import logging
import pytz

from models import Club, ClubPermission, GuildManagerRole
from utils.permissions import ensure_can_manage, can_create_club, creator_role_ids, is_full_manager

logger = logging.getLogger(__name__)

# Bot author ID for pre-migration club deletion
AUTHOR_ID = 139769063948681217


class DeleteConfirmModal(discord.ui.Modal, title="Confirm Club Deletion"):
    confirmation = discord.ui.TextInput(
        label="Type the club name to confirm",
        required=True,
    )

    def __init__(self, club_obj, club_name: str):
        super().__init__()
        self.club_obj = club_obj
        self.club_name = club_name
        self.confirmation.placeholder = club_name

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirmation.value.strip() != self.club_name:
            await interaction.response.send_message(
                f"❌ Incorrect name. Type exactly: `{self.club_name}`", ephemeral=True
            )
            return

        await interaction.response.defer()
        await self.club_obj.delete()

        embed = discord.Embed(
            title="✅ Club Deleted",
            description=f"**{self.club_name}** and all associated data have been permanently deleted.",
            color=discord.Color.dark_gray(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text=f"Deleted by {interaction.user}")
        await interaction.followup.send(embed=embed)
        logger.warning(f"Club '{self.club_name}' permanently deleted by {interaction.user} (ID: {interaction.user.id})")


class DeleteConfirmView(discord.ui.View):
    def __init__(self, club_obj, club_name: str, requester_id: int):
        super().__init__(timeout=60)
        self.club_obj = club_obj
        self.club_name = club_name
        self.requester_id = requester_id
        self._confirmed = False
        self.message: discord.Message | None = None

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("❌ Only the command invoker can confirm this.", ephemeral=True)
            return
        if self._confirmed:
            await interaction.response.send_message("❌ Already processing deletion.", ephemeral=True)
            return
        self._confirmed = True
        await interaction.response.send_modal(DeleteConfirmModal(self.club_obj, self.club_name))
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("❌ Only the command invoker can cancel this.", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("❌ Deletion cancelled.", ephemeral=True)
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class ClubManagementCommands(commands.Cog):
    """Commands for managing club registrations"""
    
    def __init__(self, bot):
        self.bot = bot
    
    async def club_autocomplete(self, interaction: discord.Interaction, current: str):
        """Autocomplete for club names visible in this guild"""
        try:
            club_names = await Club.get_names_for_guild(interaction.guild_id)
            return [
                app_commands.Choice(name=name, value=name)
                for name in club_names
                if current.lower() in name.lower()
            ][:25]
        except Exception as e:
            logger.error(f"Error in club autocomplete: {e}")
            return []
    
    @app_commands.command(name="add_club", description="Register a new club to track (Admin or club editor role)")
    @app_commands.choices(quota_period=[
        app_commands.Choice(name="Daily", value="daily"),
        app_commands.Choice(name="Weekly", value="weekly"),
        app_commands.Choice(name="Biweekly (every 2 weeks)", value="biweekly"),
    ])
    async def add_club(self, interaction: discord.Interaction,
                       club_name: str,
                       circle_id: str,
                       daily_quota: int,
                       quota_period: app_commands.Choice[str] = None,
                       timezone: str = "Europe/Amsterdam",
                       scrape_time: str = "16:00",
                       scrape_url: str = None):
        """Register a new club"""
        await interaction.response.defer()

        try:
            # Permission: admin, or a holder of any club-editor role in this guild
            auto_bind_roles = await creator_role_ids(interaction)
            if not await is_full_manager(interaction) and not auto_bind_roles:
                await interaction.followup.send(
                    "❌ You don't have permission to create a club.\n"
                    "You need Discord administrator, or a role that an admin has assigned to an existing club."
                )
                return

            # Check for duplicate
            existing = await Club.get_by_name(club_name)
            if existing:
                await interaction.followup.send(f"❌ Club '{club_name}' already exists")
                return
            
            # Validate circle_id format if provided
            if circle_id is not None and circle_id != "" and not circle_id.isdigit():
                await interaction.followup.send(
                    f"❌ Invalid Circle ID format: `{circle_id}`\n\n"
                    f"The circle_id must be a **numeric ID** from Uma.moe.\n\n"
                    f"**How to find it:**\n"
                    f"1. Go to https://uma.moe/circles/\n"
                    f"2. Search for **{club_name}**\n"
                    f"3. Click on it and copy the **number** from the URL\n"
                    f"   Example: `https://uma.moe/circles/860280110` → use `860280110`"
                )
                return
            
            # Validate timezone
            try:
                pytz.timezone(timezone)
            except pytz.exceptions.UnknownTimeZoneError:
                await interaction.followup.send(f"❌ Invalid timezone: `{timezone}`")
                return
            
            # Parse scrape time
            try:
                hour, minute = map(int, scrape_time.split(':'))
                if not (0 <= hour < 24 and 0 <= minute < 60):
                    raise ValueError
                scrape_time_obj = time(hour=hour, minute=minute)
            except (ValueError, AttributeError):
                await interaction.followup.send("❌ Invalid scrape time format. Use HH:MM (e.g., 16:00)")
                return
            
            # Normalise circle_id: treat empty string as None
            resolved_circle_id = circle_id if circle_id and circle_id != "" else None
            
            resolved_quota_period = quota_period.value if quota_period else 'daily'

            club = await Club.create(
                club_name=club_name,
                scrape_url=scrape_url or "",
                circle_id=resolved_circle_id,
                guild_id=interaction.guild_id,
                daily_quota=daily_quota,
                quota_period=resolved_quota_period,
                timezone=timezone,
                scrape_time=scrape_time_obj
            )

            # Auto-bind the creator's editor roles so the new club lands "under them".
            # (Admins keep blanket access regardless; this only matters for editor-role creators.)
            for role_id in auto_bind_roles:
                await ClubPermission.add(club.club_id, role_id)

            # Format quota for display
            if daily_quota >= 1_000_000:
                quota_formatted = f"{daily_quota / 1_000_000:.1f}M"
            elif daily_quota >= 1_000:
                quota_formatted = f"{daily_quota / 1_000:.1f}K"
            else:
                quota_formatted = str(daily_quota)

            period_label = {'daily': 'day', 'weekly': 'week', 'biweekly': '2 weeks'}.get(resolved_quota_period, 'day')
            
            embed = discord.Embed(
                title="✅ Club Added",
                description=f"Successfully registered **{club_name}**",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            embed.add_field(
                name="Club Details",
                value=f"**Name:** {club_name}\n"
                      f"**Circle ID:** {resolved_circle_id or 'Not set'}\n"
                      f"**URL:** {scrape_url or 'Not set'}",
                inline=False
            )
            
            embed.add_field(
                name="Settings",
                value=f"**Quota:** {quota_formatted} fans per {period_label}\n"
                      f"**Scrape Time:** {scrape_time} {timezone}\n"
                      f"**Bomb Rules:** 3 days trigger, 7 days countdown",
                inline=False
            )
            
            # Show scraper info based on whether circle_id was provided
            if resolved_circle_id:
                embed.add_field(
                    name="🚀 Scraper",
                    value="Using Uma.moe API (fast path)",
                    inline=False
                )
            else:
                embed.add_field(
                    name="⚠️ Scraper",
                    value="Using ChronoGenesis scraper.\n"
                          "Add circle_id later with `/edit_club` for better performance.",
                    inline=False
                )
            
            embed.add_field(
                name="Next Steps",
                value=f"1. Set channels: `/set_report_channel club:{club_name}` and `/set_alert_channel club:{club_name}`\n"
                      f"2. Adjust settings: `/edit_club club:{club_name}`\n"
                      f"3. Manual check: `/force_check club:{club_name}`",
                inline=False
            )
            
            embed.set_footer(text=f"Added by {interaction.user}")
            
            await interaction.followup.send(embed=embed)
            logger.info(f"Club '{club_name}' added by {interaction.user} (circle_id: {resolved_circle_id}, guild_id: {interaction.guild_id})")
            
        except Exception as e:
            logger.error(f"Error in add_club: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="remove_club", description="Permanently delete a club (Admin or manager role)")
    async def remove_club(self, interaction: discord.Interaction, club: str):
        """Permanently delete a club and all associated data"""
        await interaction.response.defer()

        try:
            club_obj = await Club.get_by_name(club)

            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return

            # Pre-migration clubs (guild_id IS NULL) can only be deleted by bot author
            if club_obj.guild_id is None:
                if interaction.user.id != AUTHOR_ID:
                    await interaction.followup.send(
                        f"❌ Cannot delete **{club}**\n\n"
                        f"This club was created before multi-server support and can only be deleted by the bot author."
                    )
                    return
            else:
                if not club_obj.belongs_to_guild(interaction.guild_id):
                    await interaction.followup.send(
                        f"❌ Club '{club}' is not registered in this server."
                    )
                    return
                # Deletion requires full manager rights (admin or manager role) — not a per-club editor.
                if not await is_full_manager(interaction):
                    await interaction.followup.send(
                        "❌ You don't have permission to delete clubs.\n"
                        "Only Discord admins or holders of a manager role can delete a club."
                    )
                    return

            warning_embed = discord.Embed(
                title="⚠️ Confirm Club Deletion",
                description=f"You are about to **permanently delete** the club: **{club}**",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            warning_embed.add_field(
                name="🗑️ What will be deleted",
                value="• All member records\n"
                      "• All quota history\n"
                      "• All active bombs\n"
                      "• All quota requirements\n"
                      "• All user links\n"
                      "• All settings",
                inline=False
            )
            warning_embed.add_field(
                name="⚠️ This action is irreversible",
                value="**This cannot be undone.** All data will be permanently lost.\n\n"
                      f"Click **Delete** and type the club name to confirm.",
                inline=False
            )
            warning_embed.set_footer(text=f"Requested by {interaction.user}")

            view = DeleteConfirmView(club_obj, club, interaction.user.id)
            view.message = await interaction.followup.send(embed=warning_embed, view=view)

        except Exception as e:
            logger.error(f"Error in remove_club: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="activate_club", description="Reactivate a club (Admin or club editor role)")
    async def activate_club(self, interaction: discord.Interaction, club: str):
        """Reactivate a deactivated club"""
        await interaction.response.defer()

        try:
            club_obj = await Club.get_by_name(club)

            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return

            if not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
                return

            if not await ensure_can_manage(interaction, club_obj):
                return

            if club_obj.is_active:
                await interaction.followup.send(f"ℹ️ Club '{club}' is already active")
                return
            
            await club_obj.activate()
            
            embed = discord.Embed(
                title="✅ Club Reactivated",
                description=f"**{club}** has been reactivated",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            embed.add_field(
                name="ℹ️ What's next",
                value="Daily scraping will resume at the scheduled time.",
                inline=False
            )
            
            embed.set_footer(text=f"Reactivated by {interaction.user}")
            
            await interaction.followup.send(embed=embed)
            logger.info(f"Club '{club}' reactivated by {interaction.user}")
            
        except Exception as e:
            logger.error(f"Error in activate_club: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="list_clubs", description="View all registered clubs")
    async def list_clubs(self, interaction: discord.Interaction):
        """List clubs registered in this server"""
        await interaction.response.defer()
        
        try:
            # Only show clubs belonging to the current guild (plus any pre-migration clubs)
            clubs = await Club.get_all_for_guild(interaction.guild_id)
            
            if not clubs:
                await interaction.followup.send("No clubs registered in this server. Use `/add_club` to add one.")
                return
            
            embed = discord.Embed(
                title="🏆 Registered Clubs",
                description=f"Total: {len(clubs)} club{'s' if len(clubs) != 1 else ''}",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            
            for club in clubs:
                status = "✅ Active" if club.is_active else "❌ Inactive"

                # Format quota
                if club.daily_quota >= 1_000_000:
                    quota_formatted = f"{club.daily_quota / 1_000_000:.1f}M"
                elif club.daily_quota >= 1_000:
                    quota_formatted = f"{club.daily_quota / 1_000:.1f}K"
                else:
                    quota_formatted = str(club.daily_quota)

                period_label = {'daily': 'day', 'weekly': 'week', 'biweekly': '2 weeks'}.get(
                    getattr(club, 'quota_period', 'daily'), 'day'
                )
                
                # Scraper type indicator
                if club.circle_id:
                    if club.is_circle_id_valid():
                        scraper_info = "\n**Scraper:** Uma.moe API 🚀"
                    else:
                        scraper_info = "\n**Scraper:** ⚠️ Invalid circle_id"
                else:
                    scraper_info = "\n**Scraper:** ChronoGenesis"

                # Bomb status indicator
                bomb_status = "Enabled ✅" if club.bombs_enabled else "Disabled ❌"

                embed.add_field(
                    name=f"{status} {club.club_name}",
                    value=f"**Quota:** {quota_formatted} fans/{period_label}\n"
                          f"**Schedule:** {club.get_scrape_time_str()} {club.timezone}"
                          f"{scraper_info}\n"
                          f"**Bombs:** {bomb_status}",
                    inline=False
                )
            
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Error in list_clubs: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="edit_club", description="Edit club settings (Admin or club editor role)")
    @app_commands.choices(quota_period=[
        app_commands.Choice(name="Daily", value="daily"),
        app_commands.Choice(name="Weekly", value="weekly"),
        app_commands.Choice(name="Biweekly (every 2 weeks)", value="biweekly"),
    ])
    async def edit_club(self, interaction: discord.Interaction,
                       club: str,
                       circle_id: str = None,
                       daily_quota: int = None,
                       quota_period: app_commands.Choice[str] = None,
                       scrape_time: str = None,
                       timezone: str = None,
                       bomb_trigger_days: int = None,
                       bomb_countdown_days: int = None,
                       bombs_enabled: bool = None,
                       image_report_enabled: bool = None):
        """Edit club configuration"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            if not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
                return

            if not await ensure_can_manage(interaction, club_obj):
                return

            # Validate circle_id if being updated
            if circle_id is not None and circle_id != "" and not circle_id.isdigit():
                await interaction.followup.send(
                    f"❌ Invalid Circle ID format: `{circle_id}`\n\n"
                    f"The circle_id must be a **numeric ID** from Uma.moe.\n\n"
                    f"**How to find it:**\n"
                    f"1. Go to https://uma.moe/circles/\n"
                    f"2. Search for **{club}**\n"
                    f"3. Click on it and copy the **number** from the URL\n"
                    f"   Example: `https://uma.moe/circles/860280110` → use `860280110`\n\n"
                    f"To remove circle_id (use ChronoGenesis), use an empty string."
                )
                return
            
            updates = {}
            if circle_id is not None:
                updates['circle_id'] = circle_id if circle_id != "" else None
            if daily_quota is not None:
                updates['daily_quota'] = daily_quota
            if quota_period is not None:
                updates['quota_period'] = quota_period.value
            if scrape_time is not None:
                try:
                    hour, minute = map(int, scrape_time.split(':'))
                    if not (0 <= hour < 24 and 0 <= minute < 60):
                        raise ValueError
                    updates['scrape_time'] = time(hour=hour, minute=minute)
                except (ValueError, AttributeError):
                    await interaction.followup.send("❌ Invalid time format. Use HH:MM (e.g., 16:00)")
                    return
            if timezone is not None:
                try:
                    pytz.timezone(timezone)
                except pytz.exceptions.UnknownTimeZoneError:
                    await interaction.followup.send(f"❌ Invalid timezone: `{timezone}`")
                    return
                updates['timezone'] = timezone
            if bomb_trigger_days is not None:
                updates['bomb_trigger_days'] = bomb_trigger_days
            if bomb_countdown_days is not None:
                updates['bomb_countdown_days'] = bomb_countdown_days
            if bombs_enabled is not None:
                updates['bombs_enabled'] = bombs_enabled
            if image_report_enabled is not None:
                updates['image_report_enabled'] = image_report_enabled

            if not updates:
                await interaction.followup.send("❌ No changes specified")
                return

            # If bombs are being disabled, deactivate all active bombs
            from datetime import date
            from models import Bomb
            deactivated_count = 0
            if bombs_enabled is False and club_obj.bombs_enabled:
                deactivated_count = await Bomb.deactivate_all(club_obj.club_id, date.today())

            await club_obj.update_settings(**updates)
            
            embed = discord.Embed(
                title="✅ Club Settings Updated",
                description=f"Successfully updated **{club}**",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            # Determine the effective quota_period for display (may have just been changed)
            effective_period = updates.get('quota_period', club_obj.quota_period)
            period_label = {'daily': 'day', 'weekly': 'week', 'biweekly': '2 weeks'}.get(effective_period, 'day')

            # Warn if changing quota_period mid-month
            period_warning = ""
            if 'quota_period' in updates and updates['quota_period'] != club_obj.quota_period:
                period_warning = "\n⚠️ Quota period changed mid-month — historical data may be inconsistent until the next monthly reset."

            changes_text = []
            for key, value in updates.items():
                if key == 'circle_id':
                    if value:
                        changes_text.append(f"**Circle ID:** {value} (Uma.moe API enabled 🚀)")
                    else:
                        changes_text.append(f"**Circle ID:** Removed (will use ChronoGenesis)")
                elif key == 'daily_quota':
                    if value >= 1_000_000:
                        formatted = f"{value / 1_000_000:.1f}M"
                    elif value >= 1_000:
                        formatted = f"{value / 1_000:.1f}K"
                    else:
                        formatted = str(value)
                    changes_text.append(f"**Quota:** {formatted} fans per {period_label}")
                elif key == 'quota_period':
                    period_names = {'daily': 'Daily', 'weekly': 'Weekly', 'biweekly': 'Biweekly'}
                    changes_text.append(f"**Quota Period:** {period_names.get(value, value)}")
                elif key == 'scrape_time':
                    changes_text.append(f"**Scrape Time:** {value}")
                elif key == 'timezone':
                    changes_text.append(f"**Timezone:** {value}")
                elif key == 'bomb_trigger_days':
                    changes_text.append(f"**Bomb Trigger:** {value} days")
                elif key == 'bomb_countdown_days':
                    changes_text.append(f"**Bomb Countdown:** {value} days")
                elif key == 'bombs_enabled':
                    status = "Enabled ✅" if value else "Disabled ❌"
                    changes_text.append(f"**Bombs:** {status}")
                    if not value and deactivated_count > 0:
                        changes_text.append(f"  ↳ Deactivated {deactivated_count} active bomb{'s' if deactivated_count != 1 else ''}")

            embed.add_field(
                name="Changes Applied",
                value="\n".join(changes_text) + period_warning,
                inline=False
            )

            embed.set_footer(text=f"Updated by {interaction.user}")
            
            await interaction.followup.send(embed=embed)
            logger.info(f"Club '{club}' settings updated by {interaction.user}: {updates}")
            
        except Exception as e:
            logger.error(f"Error in edit_club: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="add_club_editor", description="Give a role permission to manage a club (Admin or manager role)")
    async def add_club_editor(self, interaction: discord.Interaction, club: str, role: discord.Role):
        """Bind a Discord role to a club so its holders can manage that club"""
        await interaction.response.defer()

        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return

            if not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
                return

            if not await is_full_manager(interaction):
                await interaction.followup.send(
                    "❌ Only Discord admins or manager-role holders can assign club editors."
                )
                return

            await ClubPermission.add(club_obj.club_id, role.id)

            embed = discord.Embed(
                title="✅ Club Editor Added",
                description=f"{role.mention} can now manage **{club_obj.club_name}**.",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(
                name="What this grants",
                value="• Edit settings, channels, quota, and members for **this club only**\n"
                      "• Create new clubs (auto-assigned to this role)\n"
                      "• ❌ Cannot delete clubs (admin only)",
                inline=False
            )
            embed.set_footer(text=f"Added by {interaction.user}")
            await interaction.followup.send(embed=embed)
            logger.info(f"Role {role.id} bound to club '{club_obj.club_name}' by {interaction.user}")

        except Exception as e:
            logger.error(f"Error in add_club_editor: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(name="remove_club_editor", description="Revoke a role's permission to manage a club (Admin or manager role)")
    async def remove_club_editor(self, interaction: discord.Interaction, club: str, role: discord.Role):
        """Unbind a Discord role from a club"""
        await interaction.response.defer()

        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return

            if not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
                return

            if not await is_full_manager(interaction):
                await interaction.followup.send(
                    "❌ Only Discord admins or manager-role holders can revoke club editors."
                )
                return

            await ClubPermission.remove(club_obj.club_id, role.id)

            embed = discord.Embed(
                title="✅ Club Editor Removed",
                description=f"{role.mention} can no longer manage **{club_obj.club_name}**.",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            embed.set_footer(text=f"Removed by {interaction.user}")
            await interaction.followup.send(embed=embed)
            logger.info(f"Role {role.id} unbound from club '{club_obj.club_name}' by {interaction.user}")

        except Exception as e:
            logger.error(f"Error in remove_club_editor: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(name="list_club_editors", description="List roles that can manage a club")
    async def list_club_editors(self, interaction: discord.Interaction, club: str):
        """Show which roles are bound to a club"""
        await interaction.response.defer()

        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return

            if not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
                return

            role_ids = await ClubPermission.get_role_ids(club_obj.club_id)
            if not role_ids:
                await interaction.followup.send(
                    f"ℹ️ **{club_obj.club_name}** has no editor roles. Only admins can manage it.\n"
                    f"Use `/add_club_editor` to grant a role access."
                )
                return

            mentions = []
            for rid in role_ids:
                role = interaction.guild.get_role(rid)
                mentions.append(role.mention if role else f"`(deleted role {rid})`")

            embed = discord.Embed(
                title=f"🛡️ Club Editors — {club_obj.club_name}",
                description="\n".join(f"• {m}" for m in mentions),
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            embed.set_footer(text="These roles can manage this club (not delete it).")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in list_club_editors: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(name="add_manager_role", description="Grant a role full management of ALL clubs in this server (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def add_manager_role(self, interaction: discord.Interaction, role: discord.Role):
        """Bind a guild-wide manager role (full powers over every club here)"""
        await interaction.response.defer()
        try:
            await GuildManagerRole.add(interaction.guild_id, role.id)
            embed = discord.Embed(
                title="✅ Manager Role Added",
                description=f"{role.mention} can now manage **every club** in this server.",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(
                name="What this grants",
                value="• Manage, create and **delete** any club in this server\n"
                      "• Assign club-editor roles\n"
                      "• ❌ Cannot assign manager roles (admins only)",
                inline=False,
            )
            embed.set_footer(text=f"Added by {interaction.user}")
            await interaction.followup.send(embed=embed)
            logger.info(f"Manager role {role.id} added for guild {interaction.guild_id} by {interaction.user}")
        except Exception as e:
            logger.error(f"Error in add_manager_role: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(name="remove_manager_role", description="Revoke a server-wide manager role (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_manager_role(self, interaction: discord.Interaction, role: discord.Role):
        """Unbind a guild-wide manager role"""
        await interaction.response.defer()
        try:
            await GuildManagerRole.remove(interaction.guild_id, role.id)
            embed = discord.Embed(
                title="✅ Manager Role Removed",
                description=f"{role.mention} no longer manages clubs in this server.",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(text=f"Removed by {interaction.user}")
            await interaction.followup.send(embed=embed)
            logger.info(f"Manager role {role.id} removed for guild {interaction.guild_id} by {interaction.user}")
        except Exception as e:
            logger.error(f"Error in remove_manager_role: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(name="list_manager_roles", description="List server-wide manager roles (Admin only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def list_manager_roles(self, interaction: discord.Interaction):
        """Show roles that can manage every club in this server"""
        await interaction.response.defer()
        try:
            role_ids = await GuildManagerRole.get_role_ids(interaction.guild_id)
            if not role_ids:
                await interaction.followup.send(
                    "ℹ️ No manager roles set for this server.\n"
                    "Use `/add_manager_role` to grant a role full management of all clubs."
                )
                return
            mentions = []
            for rid in role_ids:
                r = interaction.guild.get_role(rid)
                mentions.append(r.mention if r else f"`(deleted role {rid})`")
            embed = discord.Embed(
                title="🛡️ Manager Roles",
                description="\n".join(f"• {m}" for m in mentions),
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(text="These roles can manage every club in this server.")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in list_manager_roles: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    # Autocomplete for club parameter
    remove_club.autocomplete('club')(club_autocomplete)
    activate_club.autocomplete('club')(club_autocomplete)
    edit_club.autocomplete('club')(club_autocomplete)
    add_club_editor.autocomplete('club')(club_autocomplete)
    remove_club_editor.autocomplete('club')(club_autocomplete)
    list_club_editors.autocomplete('club')(club_autocomplete)


async def setup(bot):
    """Setup function for loading the cog"""
    await bot.add_cog(ClubManagementCommands(bot))