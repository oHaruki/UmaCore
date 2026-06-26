"""
Member status and user linking commands
"""
import os
import discord
from discord import app_commands
from discord.ext import commands
import logging

from models import Member, UserLink, Club

logger = logging.getLogger(__name__)


class CardCarousel(discord.ui.View):
    """A ◀ ▶ carousel that pages between a member's status card and team card.

    Each page is a pre-rendered PNG on disk; flipping re-attaches the relevant
    file to the original message. Only the invoker can use the buttons, and the
    controls disable themselves on timeout. The temp PNGs are cleaned up when the
    view times out.
    """

    def __init__(self, invoker_id: int, pages: list, *, timeout: float = 180):
        super().__init__(timeout=timeout)
        # pages: list of (path, filename, label)
        self.invoker_id = invoker_id
        self.pages = pages
        self.index = 0
        self.message: discord.Message = None
        self._sync_state()

    def current_file(self) -> discord.File:
        path, filename, _ = self.pages[self.index]
        return discord.File(path, filename=filename)

    def _sync_state(self):
        self.prev_button.disabled = self.index <= 0
        self.next_button.disabled = self.index >= len(self.pages) - 1
        _, _, label = self.pages[self.index]
        self.page_label.label = label
        self.page_label.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the person who ran the command can flip these cards.",
                ephemeral=True,
            )
            return False
        return True

    async def _show(self, interaction: discord.Interaction):
        self._sync_state()
        await interaction.response.edit_message(
            attachments=[self.current_file()], view=self
        )

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.index > 0:
            self.index -= 1
        await self._show(interaction)

    @discord.ui.button(label="Status", style=discord.ButtonStyle.primary, disabled=True)
    async def page_label(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Non-interactive page indicator.
        pass

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.index < len(self.pages) - 1:
            self.index += 1
        await self._show(interaction)

    async def on_timeout(self):
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except Exception:
                pass
        for path, _, _ in self.pages:
            try:
                os.remove(path)
            except OSError:
                pass


class MemberCommands(commands.Cog):
    """Member status and user linking commands"""
    
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
    
    @app_commands.command(name="link_trainer", description="Link your Discord account to your trainer")
    async def link_trainer(self, interaction: discord.Interaction, trainer_name: str, club: str):
        """Link your Discord account to a trainer"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(
                    f"❌ Club '{club}' not found.",
                    ephemeral=True
                )
                return
            
            member = await Member.get_by_name(club_obj.club_id, trainer_name)
            
            if not member:
                await interaction.followup.send(
                    f"❌ Trainer '{trainer_name}' not found in {club}. Make sure the name matches exactly.",
                    ephemeral=True
                )
                return
            
            # Check if already linked to another trainer
            existing_link = await UserLink.get_by_discord_id(interaction.user.id)
            if existing_link:
                existing_member = await Member.get_by_id(existing_link.member_id)
                if existing_member.member_id == member.member_id:
                    await interaction.followup.send(
                        f"ℹ️ You're already linked to **{trainer_name}** in **{club}**",
                        ephemeral=True
                    )
                    return
                else:
                    # Unlink from old trainer
                    await UserLink.delete(interaction.user.id)
                    logger.info(f"Unlinked user {interaction.user.id} from {existing_member.trainer_name}")
            
            # Create link
            user_link = await UserLink.create(
                discord_user_id=interaction.user.id,
                member_id=member.member_id,
                notify_on_bombs=True,
                notify_on_deficit=False
            )
            
            embed = discord.Embed(
                title="✅ Trainer Linked!",
                description=f"Your Discord account is now linked to **{trainer_name}** in **{club}**",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            embed.add_field(
                name="🔔 Notifications Enabled",
                value="• **Bomb Warnings:** ✅ Enabled\n"
                      "• **Deficit Alerts:** ❌ Disabled",
                inline=False
            )
            
            embed.add_field(
                name="💡 Next Steps",
                value="• Use `/my_status` to check your progress\n"
                      "• Use `/notification_settings` to customize alerts\n"
                      "• Use `/unlink` to remove the link",
                inline=False
            )
            
            embed.set_footer(text="You'll receive DMs when important events happen")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"User {interaction.user.id} linked to {trainer_name} in {club}")
            
        except Exception as e:
            logger.error(f"Error in link_trainer: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)
    
    @app_commands.command(name="unlink", description="Unlink your Discord account from your trainer")
    async def unlink(self, interaction: discord.Interaction):
        """Unlink your Discord account"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            user_link = await UserLink.get_by_discord_id(interaction.user.id)
            
            if not user_link:
                await interaction.followup.send(
                    "ℹ️ You don't have a linked trainer",
                    ephemeral=True
                )
                return
            
            member = await Member.get_by_id(user_link.member_id)
            await UserLink.delete(interaction.user.id)
            
            embed = discord.Embed(
                title="✅ Trainer Unlinked",
                description=f"Your Discord account has been unlinked from **{member.trainer_name}**",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            
            embed.add_field(
                name="ℹ️ What this means",
                value="You will no longer receive DM notifications about quota status.",
                inline=False
            )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"User {interaction.user.id} unlinked from {member.trainer_name}")
            
        except Exception as e:
            logger.error(f"Error in unlink: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)
    
    @app_commands.command(name="notification_settings", description="Manage your notification preferences")
    async def notification_settings(self, interaction: discord.Interaction, 
                                   bomb_warnings: bool = None, 
                                   deficit_alerts: bool = None):
        """Manage notification settings"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            user_link = await UserLink.get_by_discord_id(interaction.user.id)
            
            if not user_link:
                await interaction.followup.send(
                    "❌ You need to link a trainer first using `/link_trainer`",
                    ephemeral=True
                )
                return
            
            # If no settings provided, show current settings
            if bomb_warnings is None and deficit_alerts is None:
                member = await Member.get_by_id(user_link.member_id)
                
                embed = discord.Embed(
                    title="🔔 Notification Settings",
                    description=f"Settings for **{member.trainer_name}**",
                    color=discord.Color.blue(),
                    timestamp=discord.utils.utcnow()
                )
                
                bomb_status = "✅ Enabled" if user_link.notify_on_bombs else "❌ Disabled"
                deficit_status = "✅ Enabled" if user_link.notify_on_deficit else "❌ Disabled"
                
                embed.add_field(
                    name="Current Settings",
                    value=f"**💣 Bomb Warnings:** {bomb_status}\n"
                          f"**⚠️ Deficit Alerts:** {deficit_status}",
                    inline=False
                )
                
                embed.add_field(
                    name="ℹ️ How to change",
                    value="Use `/notification_settings bomb_warnings:True` or similar to update settings",
                    inline=False
                )
                
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            
            # Update settings
            new_bomb_setting = bomb_warnings if bomb_warnings is not None else user_link.notify_on_bombs
            new_deficit_setting = deficit_alerts if deficit_alerts is not None else user_link.notify_on_deficit
            
            await user_link.update_notifications(new_bomb_setting, new_deficit_setting)
            
            member = await Member.get_by_id(user_link.member_id)
            
            embed = discord.Embed(
                title="✅ Settings Updated",
                description=f"Notification settings for **{member.trainer_name}** have been updated",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            bomb_status = "✅ Enabled" if new_bomb_setting else "❌ Disabled"
            deficit_status = "✅ Enabled" if new_deficit_setting else "❌ Disabled"
            
            embed.add_field(
                name="New Settings",
                value=f"**💣 Bomb Warnings:** {bomb_status}\n"
                      f"**⚠️ Deficit Alerts:** {deficit_status}",
                inline=False
            )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"User {interaction.user.id} updated notification settings")
            
        except Exception as e:
            logger.error(f"Error in notification_settings: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)
    
    @app_commands.command(name="my_status", description="View your own quota status")
    async def my_status(self, interaction: discord.Interaction):
        """View your own linked trainer status"""
        await interaction.response.defer()
        
        try:
            user_link = await UserLink.get_by_discord_id(interaction.user.id)
            
            if not user_link:
                await interaction.followup.send(
                    "❌ You haven't linked a trainer yet. Use `/link_trainer` to get started!"
                )
                return
            
            member = await Member.get_by_id(user_link.member_id)
            await self._send_member_status(interaction, member)
            
        except Exception as e:
            logger.error(f"Error in my_status: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="member_status", description="View status of a specific member")
    async def member_status(self, interaction: discord.Interaction, trainer_name: str, club: str):
        """Get detailed status for a specific member"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            member = await Member.get_by_name(club_obj.club_id, trainer_name)
            
            if not member:
                await interaction.followup.send(f"❌ Member '{trainer_name}' not found in {club}")
                return
            
            await self._send_member_status(interaction, member)
            
        except Exception as e:
            logger.error(f"Error in member_status: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    async def _send_member_status(self, interaction: discord.Interaction, member: Member):
        """Render and send the trainer card(s) for a member.

        Sends a single image when there's no team data, or a ◀ ▶ carousel
        (status card + Team Trials card) when the trainer has team_stadium data.
        """
        from services.trainer_card_renderer import generate_member_cards

        try:
            status_path, team_path = await generate_member_cards(member)
        except Exception as e:
            logger.error(
                f"Failed to render trainer card for {member.trainer_name}: {e}",
                exc_info=True,
            )
            await interaction.followup.send(f"❌ Couldn't render the trainer card: {str(e)}")
            return

        if status_path is None:
            await interaction.followup.send(
                f"No quota data found for **{member.trainer_name}** yet."
            )
            return

        # Single-page: no team data, just send the status card and clean up.
        if not team_path:
            try:
                file = discord.File(str(status_path), filename="trainer_card.png")
                await interaction.followup.send(file=file)
            finally:
                try:
                    os.remove(status_path)
                except OSError:
                    pass
            return

        # Two-page carousel. The view owns temp-file cleanup on timeout.
        pages = [
            (str(status_path), "trainer_card.png", "Status"),
            (str(team_path), "team_card.png", "Team Trials"),
        ]
        view = CardCarousel(interaction.user.id, pages)
        try:
            message = await interaction.followup.send(file=view.current_file(), view=view, wait=True)
            view.message = message
        except Exception:
            for path, _, _ in pages:
                try:
                    os.remove(path)
                except OSError:
                    pass
            raise
    
    # Apply autocomplete
    link_trainer.autocomplete('club')(club_autocomplete)
    member_status.autocomplete('club')(club_autocomplete)