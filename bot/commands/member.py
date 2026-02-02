"""
Member status and user linking commands
"""
import discord
from discord import app_commands
from discord.ext import commands
from datetime import date as date_class
import logging

from models import Member, QuotaHistory, Bomb, UserLink, Club

logger = logging.getLogger(__name__)


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
        """Send a detailed status embed for a member"""
        latest_history = await QuotaHistory.get_latest_for_member(member.member_id)
        
        if not latest_history:
            await interaction.followup.send(f"No quota data found for {member.trainer_name}")
            return
        
        active_bomb = await Bomb.get_active_for_member(member.member_id)
        
        # Get club info for daily quota
        from models import Club
        club = await Club.get_by_id(member.club_id)
        daily_quota = club.daily_quota if club else 1000000
        
        # Determine color based on status
        if active_bomb:
            color = 0xFF0000  # Red for bomb
        elif latest_history.deficit_surplus < 0:
            color = 0xFFA500  # Orange for behind
        else:
            color = 0x3498db  # Blue for on track
        
        # Build title
        if active_bomb:
            title = "💣 Quota Status - Bomb Active"
        elif latest_history.deficit_surplus < 0:
            title = "⚠️ Quota Status - Behind"
        else:
            title = "📊 Quota Status"
        
        embed = discord.Embed(
            title=title,
            color=color,
            timestamp=discord.utils.utcnow()
        )
        
        # Trainer info - split into two columns
        status_text = "✅ Active" if member.is_active else "❌ Inactive"
        if member.manually_deactivated:
            status_text += " (Manually Deactivated)"
        
        embed.add_field(
            name="👤 Trainer Information",
            value=f"**Name:** {member.trainer_name}\n"
                  f"**Trainer ID:** `{member.trainer_id or 'N/A'}`\n"
                  f"**Club:** {club.club_name if club else 'Unknown'}",
            inline=True
        )
        
        embed.add_field(
            name="📅 Membership",
            value=f"**Joined:** {member.join_date.strftime('%b %d, %Y')}\n"
                  f"**Status:** {status_text}",
            inline=True
        )
        
        # Progress bar
        if latest_history.expected_fans > 0:
            progress_pct = int((latest_history.cumulative_fans / latest_history.expected_fans) * 100)
        else:
            progress_pct = 0
        
        # Determine color indicator
        if progress_pct >= 500:
            color_indicator = "🟨"
        elif progress_pct >= 400:
            color_indicator = "🟧"
        elif progress_pct >= 300:
            color_indicator = "🟪"
        elif progress_pct >= 200:
            color_indicator = "🟦"
        elif progress_pct >= 100:
            color_indicator = "🟩"
        else:
            color_indicator = "⬜"
        
        # Calculate bar display
        if progress_pct >= 100:
            bar = "█" * 20
        else:
            filled = int(progress_pct / 5)
            empty = 20 - filled
            bar = "█" * filled + "░" * empty
        
        progress_title = "📈 Current Progress" if latest_history.deficit_surplus >= 0 else "📉 Current Progress"
        
        embed.add_field(
            name=progress_title,
            value=f"```\nCurrent:  {latest_history.cumulative_fans:,} 👥\n"
                  f"Expected: {latest_history.expected_fans:,} 👥\n"
                  f"━━━━━━━━━━━━━━━━━━━━\n"
                  f"Progress: {bar} {color_indicator}{progress_pct}%\n```",
            inline=False
        )
        
        # Performance section
        if latest_history.deficit_surplus >= 0:
            status_emoji = "🎯"
            deficit_text = f"+{latest_history.deficit_surplus:,}"
            performance_title = "🎯 Performance"
        else:
            status_emoji = "⚠️"
            deficit_text = f"{latest_history.deficit_surplus:,}"
            performance_title = "⚠️ Performance"
        
        embed.add_field(
            name=performance_title,
            value=f"**Surplus/Deficit:** {deficit_text} fans {status_emoji}\n"
                  f"**Days Behind:** {latest_history.days_behind} days\n"
                  f"**Daily Quota:** {daily_quota:,} fans/day",
            inline=True
        )
        
        # Bomb status
        if active_bomb:
            urgency_emoji = "🔴" if active_bomb.days_remaining <= 2 else "🟠" if active_bomb.days_remaining <= 4 else "🟡"
            
            embed.add_field(
                name="💣 Active Bomb",
                value=f"{urgency_emoji} **{active_bomb.days_remaining} days remaining**\n"
                      f"Activated: {active_bomb.activation_date.strftime('%b %d, %Y')}\n"
                      f"Get back on track!",
                inline=True
            )
        elif latest_history.days_behind == 2:
            embed.add_field(
                name="💣 Bomb Warning",
                value=f"🟡 **1 more day** behind\n"
                      f"and a bomb will activate!\n"
                      f"Get on track today.",
                inline=True
            )
        else:
            embed.add_field(name="\u200b", value="\u200b", inline=True)
        
        # Recommendations
        if latest_history.deficit_surplus < 0:
            deficit = abs(latest_history.deficit_surplus)
            recommended_daily = daily_quota + (deficit // max(1, 7 - latest_history.days_behind))
            
            embed.add_field(
                name="💡 To Catch Up",
                value=f"Earn **{deficit:,}+ fans** total\n"
                      f"Target: **{recommended_daily:,} fans/day**",
                inline=False
            )
        
        # Statistics
        current_date = date_class.today()
        
        # Calculate streak and get history
        history_records = await QuotaHistory.get_last_n_days(member.member_id, 100)
        
        # Use actual number of days with data
        days_active = len(history_records) if history_records else 1
        avg_daily = latest_history.cumulative_fans / max(1, days_active) if days_active > 0 else 0
        
        # Calculate streak
        streak_days = 0
        
        if latest_history.deficit_surplus >= 0:
            streak_days = 1
            for record in history_records[1:]:
                if record.deficit_surplus >= 0:
                    streak_days += 1
                else:
                    break
        
        # Get best day
        best_day_fans = 0
        if len(history_records) >= 2:
            for i in range(len(history_records) - 1):
                current = history_records[i]
                previous = history_records[i + 1]
                daily_gain = current.cumulative_fans - previous.cumulative_fans
                if daily_gain > best_day_fans:
                    best_day_fans = daily_gain
        
        # Format stats
        if avg_daily >= 1_000_000:
            avg_formatted = f"{avg_daily / 1_000_000:.2f}M"
        elif avg_daily >= 1_000:
            avg_formatted = f"{avg_daily / 1_000:.1f}K"
        else:
            avg_formatted = f"{int(avg_daily)}"
        
        if best_day_fans >= 1_000_000:
            best_formatted = f"{best_day_fans / 1_000_000:.2f}M"
        elif best_day_fans >= 1_000:
            best_formatted = f"{best_day_fans / 1_000:.1f}K"
        else:
            best_formatted = f"{best_day_fans}"
        
        # Streak emoji
        if streak_days >= 30:
            streak_emoji = "🔥🔥🔥"
        elif streak_days >= 14:
            streak_emoji = "🔥🔥"
        elif streak_days >= 7:
            streak_emoji = "🔥"
        elif streak_days >= 3:
            streak_emoji = "✨"
        else:
            streak_emoji = ""
        
        embed.add_field(
            name="📊 Statistics",
            value=f"**Days Active:** {days_active}\n"
                  f"**Avg Daily:** {avg_formatted}/day\n"
                  f"**Best Day:** +{best_formatted}\n"
                  f"**Streak:** {streak_days} day{'s' if streak_days != 1 else ''} {streak_emoji}",
            inline=True
        )
        
        # Rank
        all_members = await Member.get_all_active(member.club_id)
        member_rankings = []
        
        for m in all_members:
            m_history = await QuotaHistory.get_latest_for_member(m.member_id)
            if m_history:
                member_rankings.append({
                    'member_id': m.member_id,
                    'deficit_surplus': m_history.deficit_surplus
                })
        
        member_rankings.sort(key=lambda x: x['deficit_surplus'], reverse=True)
        
        member_rank = 0
        for idx, ranking in enumerate(member_rankings, start=1):
            if ranking['member_id'] == member.member_id:
                member_rank = idx
                break
        
        total_members = len(member_rankings)
        percentile = 100 - int((member_rank / total_members) * 100) if total_members > 0 else 0
        
        if percentile >= 90:
            percentile_desc = f"Top {100 - percentile}%"
        elif percentile >= 75:
            percentile_desc = f"Top {100 - percentile}%"
        elif percentile >= 50:
            percentile_desc = f"Top {100 - percentile}%"
        else:
            percentile_desc = f"Bottom {percentile}%"
        
        embed.add_field(
            name="🏆 Rank",
            value=f"**Club Rank:** #{member_rank} of {total_members}\n"
                  f"**Percentile:** {percentile_desc}",
            inline=True
        )
        
        embed.set_footer(text=f"Last updated: {latest_history.date.strftime('%b %d, %Y')}")
        
        await interaction.followup.send(embed=embed)
    
    # Apply autocomplete
    link_trainer.autocomplete('club')(club_autocomplete)
    member_status.autocomplete('club')(club_autocomplete)