"""
Administrative commands for quota management
"""
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, time
import logging
import os
import pytz
import asyncio
from typing import Optional

from scrapers import ChronoGenesisScraper, UmaMoeAPIScraper
from services import QuotaCalculator, BombManager, ReportGenerator, MonthlyInfoService
from services.tally_renderer import generate_tally_image
from services.recalculator import recalculate_club
from models import Member, QuotaRequirement, BotSettings, Club, ClubRankHistory
from models.quota_requirement import QuotaSchedule
from config.settings import (
    USE_UMAMOE_API, UMAMOE_RATE_PER_MIN, UMAMOE_RATE_BURST,
    COLOR_INFO, COLOR_BOMB, COLOR_ON_TRACK, COLOR_BEHIND,
)
from utils.rate_limiter import umamoe_limiter, PRIORITY_INTERACTIVE
from utils.timezone_helper import resolve_timezone
from utils.audit import log_audit
from utils.permissions import ensure_can_manage

logger = logging.getLogger(__name__)


class AdminCommands(commands.Cog):
    """Administrative commands for quota management"""

    def __init__(self, bot):
        self.bot = bot
        self.quota_calculator = QuotaCalculator()
        self.bomb_manager = BombManager()
        self.report_generator = ReportGenerator()
        self.monthly_info_service = MonthlyInfoService()

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

    async def _update_monthly_info_board(self, club_obj: Club, current_date) -> bool:
        """Auto-update the monthly info board after quota changes"""
        try:
            channel_id, message_id = await club_obj.get_monthly_info_location()
            if channel_id and message_id:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    try:
                        message = await channel.fetch_message(message_id)
                        updated_embed = await self.monthly_info_service.create_monthly_info_embed(
                            club_obj.club_id, club_obj.club_name, current_date, club_obj.quota_period
                        )
                        await message.edit(embed=updated_embed)
                        logger.info(f"Auto-updated monthly info board for {club_obj.club_name}")
                        return True
                    except discord.NotFound:
                        logger.warning(f"Monthly info message not found for {club_obj.club_name}")
                    except discord.Forbidden:
                        logger.error(f"No permission to edit monthly info message for {club_obj.club_name}")
                    except Exception as e:
                        logger.error(f"Error editing monthly info message: {e}")
        except Exception as e:
            logger.error(f"Error updating monthly info board: {e}")
        return False

    @app_commands.command(name="quota", description="Set the daily quota requirement")
    async def set_quota(self, interaction: discord.Interaction, amount: int, club: str):
        """Set the daily quota requirement"""
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

            if amount < 0:
                await interaction.followup.send("❌ Quota amount must be positive")
                return

            period_caps = {'daily': 10_000_000, 'weekly': 100_000_000, 'biweekly': 200_000_000}
            max_quota = period_caps.get(club_obj.quota_period, 10_000_000)
            if amount > max_quota:
                cap_label = f"{max_quota // 1_000_000}M"
                await interaction.followup.send(f"❌ Quota amount seems unreasonably high (>{cap_label} for {club_obj.quota_period} quota). Please check your input.")
                return

            club_tz = resolve_timezone(club_obj.timezone)
            current_datetime = datetime.now(club_tz)
            current_date = current_datetime.date()

            set_by = f"{interaction.user.name}#{interaction.user.discriminator}"
            quota_req = await QuotaRequirement.create(
                club_id=club_obj.club_id,
                effective_date=current_date,
                daily_quota=amount,
                set_by=set_by
            )

            if amount >= 1_000_000:
                formatted = f"{amount / 1_000_000:.1f}M"
            elif amount >= 1_000:
                formatted = f"{amount / 1_000:.1f}K"
            else:
                formatted = str(amount)

            period_label = {'daily': 'day', 'weekly': 'week', 'biweekly': '2 weeks'}.get(club_obj.quota_period, 'day')
            period_name = {'daily': 'Daily', 'weekly': 'Weekly', 'biweekly': 'Biweekly'}.get(club_obj.quota_period, 'Daily')

            embed = discord.Embed(
                title=f"✅ Quota Updated - {club}",
                description=f"{period_name} quota has been set to **{formatted} fans/{period_label}**",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )

            embed.add_field(
                name="Effective Date",
                value=current_date.strftime('%Y-%m-%d'),
                inline=True
            )

            embed.add_field(
                name="Exact Amount",
                value=f"{amount:,} fans",
                inline=True
            )

            embed.add_field(
                name="Set By",
                value=set_by,
                inline=True
            )

            embed.add_field(
                name="ℹ️ Important",
                value="This quota applies from today onwards. Previous days are unaffected.",
                inline=False
            )

            await interaction.followup.send(embed=embed)
            await log_audit(
                interaction, 'quota_req.create', 'quota_requirement',
                entity_id=quota_req.id, club_id=club_obj.club_id,
                details={
                    'effective_date': current_date.isoformat(),
                    'daily_quota': amount,
                },
            )
            logger.info(f"Quota set to {amount:,} for {club} by {set_by} effective {current_date}")

            # Auto-update monthly info board
            updated = await self._update_monthly_info_board(club_obj, current_date)
            if updated:
                await interaction.followup.send("✅ Monthly info board auto-updated!", ephemeral=True)

        except Exception as e:
            logger.error(f"Error in set_quota: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(name="update_monthly_info", description="Update the monthly info board")
    async def update_monthly_info(self, interaction: discord.Interaction, club: str):
        """Update the existing monthly info board"""
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

            channel_id, message_id = await club_obj.get_monthly_info_location()

            if not channel_id or not message_id:
                await interaction.followup.send(
                    f"❌ No monthly info board found for {club}. Use `/post_monthly_info` first."
                )
                return

            channel = self.bot.get_channel(channel_id)
            if not channel:
                await interaction.followup.send(f"❌ Channel not found. The board may have been deleted.")
                return

            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                await interaction.followup.send(f"❌ Message not found. Use `/post_monthly_info` to create a new one.")
                return

            club_tz = resolve_timezone(club_obj.timezone)
            current_datetime = datetime.now(club_tz)
            current_date = current_datetime.date()

            embed = await self.monthly_info_service.create_monthly_info_embed(
                club_obj.club_id,
                club_obj.club_name,
                current_date,
                club_obj.quota_period
            )

            await message.edit(embed=embed)
            await interaction.followup.send(f"✅ Monthly info board updated for {club}!")

        except Exception as e:
            logger.error(f"Error in update_monthly_info: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(name="quota_history", description="View quota changes this month")
    async def quota_history(self, interaction: discord.Interaction, club: str):
        """View quota change history for the current month"""
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

            club_tz = resolve_timezone(club_obj.timezone)
            current_datetime = datetime.now(club_tz)
            current_date = current_datetime.date()

            quota_reqs = await QuotaRequirement.get_all_for_month(
                club_obj.club_id, current_date.year, current_date.month
            )

            quota_period_label = {'daily': 'day', 'weekly': 'week', 'biweekly': '2 weeks'}.get(club_obj.quota_period, 'day')

            if not quota_reqs:
                embed = discord.Embed(
                    title=f"📊 Quota History - {club} - Current Month",
                    description=f"No quota changes this month.\n"
                                f"Using default: **{club_obj.daily_quota:,} fans/{quota_period_label}**",
                    color=discord.Color.blue(),
                    timestamp=discord.utils.utcnow()
                )
                await interaction.followup.send(embed=embed)
                return

            embed = discord.Embed(
                title=f"📊 Quota History - {club} - Current Month",
                description=f"Showing {len(quota_reqs)} quota change(s)",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )

            for quota_req in quota_reqs:
                amount = quota_req.daily_quota
                if amount >= 1_000_000:
                    formatted = f"{amount / 1_000_000:.1f}M"
                elif amount >= 1_000:
                    formatted = f"{amount / 1_000:.1f}K"
                else:
                    formatted = str(amount)

                embed.add_field(
                    name=f"{quota_req.effective_date.strftime('%B %d, %Y')}",
                    value=f"**{formatted} fans/{quota_period_label}** ({amount:,})\n"
                          f"Set by: {quota_req.set_by or 'Unknown'}",
                    inline=False
                )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in quota_history: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(name="delete_quota", description="Delete a specific quota requirement entry by date and amount")
    async def delete_quota(self, interaction: discord.Interaction, club: str, date: str, amount: int):
        """Delete a specific quota requirement entry (use /quota_history to find the values)"""
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

            try:
                effective_date = datetime.strptime(date, "%Y-%m-%d").date()
            except ValueError:
                await interaction.followup.send("❌ Invalid date format. Use YYYY-MM-DD")
                return

            deleted = await QuotaRequirement.delete_by_date_and_amount(
                club_obj.club_id, effective_date, amount
            )

            if deleted == 0:
                await interaction.followup.send(
                    f"❌ No quota requirement found for **{club}** on `{date}` with amount `{amount:,}`. "
                    f"Use `/quota_history` to see existing entries."
                )
                return

            if amount >= 1_000_000:
                formatted = f"{amount / 1_000_000:.1f}M"
            elif amount >= 1_000:
                formatted = f"{amount / 1_000:.1f}K"
            else:
                formatted = str(amount)

            embed = discord.Embed(
                title=f"✅ Quota Entry Deleted - {club}",
                description=f"Removed **{formatted} fans/day** effective `{date}`",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(
                name="ℹ️ Next Steps",
                value="The bot will now use the next applicable quota entry. "
                      "Run `/quota_history` to verify, or `/force_check` to recalculate.",
                inline=False
            )
            embed.set_footer(text=f"Deleted by {interaction.user}")

            await interaction.followup.send(embed=embed)
            # Deleted by date+amount rather than id, so there is no entity_id to record.
            await log_audit(
                interaction, 'quota_req.delete', 'quota_requirement',
                club_id=club_obj.club_id,
                details={
                    'effective_date': effective_date.isoformat(),
                    'daily_quota': amount,
                },
            )
            logger.info(f"Quota entry deleted for {club} ({amount:,} on {date}) by {interaction.user}")

            await self._update_monthly_info_board(club_obj, effective_date)

        except Exception as e:
            logger.error(f"Error in delete_quota: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(name="force_check", description="Manually trigger a quota check and report")
    async def force_check(self, interaction: discord.Interaction, club: str):
        """Manually trigger the daily check"""
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

            report_channel = self.bot.get_channel(club_obj.report_channel_id)
            alert_channel = self.bot.get_channel(club_obj.alert_channel_id or club_obj.report_channel_id)

            if not report_channel:
                await interaction.followup.send(f"❌ Report channel not found for {club}. Use `/set_report_channel` first.")
                return

            if not alert_channel:
                alert_channel = report_channel

            club_tz = resolve_timezone(club_obj.timezone)
            current_datetime = datetime.now(club_tz)
            current_date = current_datetime.date()

            # Select scraper
            if USE_UMAMOE_API:
                if not club_obj.circle_id:
                    await interaction.followup.send(
                        f"❌ **Missing Circle ID for {club}**\n\n"
                        f"Uma.moe API is enabled but no circle_id has been set.\n\n"
                        f"**To fix this:**\n"
                        f"Use `/edit_club club:{club} circle_id:<numeric_id>`\n\n"
                        f"**How to find your Circle ID:**\n"
                        f"1. Go to https://uma.moe/circles/\n"
                        f"2. Search for **{club}**\n"
                        f"3. Copy the number from the URL"
                    )
                    logger.error(f"No circle_id configured for {club_obj.club_name} (required when Uma.moe API is enabled)")
                    return

                if not club_obj.is_circle_id_valid():
                    error_msg = club_obj.get_circle_id_help_message()
                    await interaction.followup.send(error_msg)
                    logger.error(f"Invalid circle_id format for {club}: '{club_obj.circle_id}'")
                    return

                scraper = UmaMoeAPIScraper(club_obj.circle_id, priority=PRIORITY_INTERACTIVE)
                await interaction.followup.send(f"Using Uma.moe API scraper for {club}...")
                logger.info(f"Using Uma.moe API scraper for {club_obj.club_name} (circle_id: {club_obj.circle_id})")
            else:
                scraper = ChronoGenesisScraper(club_obj.scrape_url)
                await interaction.followup.send(f"Using ChronoGenesis scraper for {club}...")
                logger.info(f"Using ChronoGenesis scraper for {club_obj.club_name}")

            # Scrape with retry logic
            max_retries = 3
            retry_delay = 10
            scraped_data = None
            current_day = None

            for attempt in range(1, max_retries + 1):
                try:
                    await interaction.followup.send(f"🔄 Scraping {club} (attempt {attempt}/{max_retries})...")
                    scraped_data = await scraper.scrape()
                    current_day = scraper.get_current_day()

                    if scraped_data:
                        break
                except Exception as e:
                    if attempt == max_retries:
                        raise
                    await interaction.followup.send(f"⚠️ Attempt {attempt} failed, retrying in {retry_delay}s...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2

            if not scraped_data:
                await interaction.followup.send("❌ Failed to scrape data after all retries")
                return

            # Use the scraper's data date in case of previous-month fallback (e.g. Day 1)
            data_date = scraper.get_data_date()
            if data_date:
                current_date = data_date
                logger.info(f"Using scraper's data date: {current_date} (previous-month fallback)")

            # Extract and persist club rank data (Uma.moe API only)
            rank_data = None
            if isinstance(scraper, UmaMoeAPIScraper):
                monthly_rank = scraper.get_monthly_rank()
                last_month_rank = scraper.get_last_month_rank()
                yesterday_rank = scraper.get_yesterday_rank()

                if monthly_rank is not None:
                    try:
                        await ClubRankHistory.save(club_obj.club_id, current_date, monthly_rank, monthly_rank)
                    except Exception as e:
                        logger.error(f"Failed to save rank data for {club_obj.club_name}: {e}", exc_info=True)

                    rank_data = {
                        'monthly_rank': monthly_rank,
                        'last_month_rank': last_month_rank,
                        'yesterday_rank': yesterday_rank,
                    }
                    logger.info(
                        f"Rank data for {club_obj.club_name}: "
                        f"monthly={monthly_rank}, yesterday={yesterday_rank}, "
                        f"last_month={last_month_rank}"
                    )

                    # Best-effort: how far to the next rank milestone. Never let
                    # this optional enrichment break the report.
                    try:
                        from services.promotion_calculator import compute_promotion
                        promo = await compute_promotion(club_obj)
                        if promo and not promo.already_reached and promo.fans_needed:
                            rank_data['promotion'] = {
                                'target_rank': promo.target_rank,
                                'fans_needed': promo.fans_needed,
                                'extra_per_day': promo.extra_per_day,
                                'days_remaining': promo.days_remaining,
                            }
                    except Exception as e:
                        logger.warning(f"Promotion calc failed for {club_obj.club_name}: {e}")

            # Process scraped data
            await interaction.followup.send("⚙️ Processing data...")
            new_members, updated_members = await self.quota_calculator.process_scraped_data(
                club_obj.club_id, scraped_data, current_date, current_day,
                quota_period=club_obj.quota_period
            )

            # Bomb management
            newly_activated = []
            deactivated = []
            members_to_kick = []

            if club_obj.bombs_enabled:
                newly_activated = await self.bomb_manager.check_and_activate_bombs(club_obj, current_date)
                await self.bomb_manager.update_bomb_countdowns(club_obj.club_id, current_date)
                deactivated = await self.bomb_manager.check_and_deactivate_bombs(club_obj.club_id, current_date)
                members_to_kick = await self.bomb_manager.check_expired_bombs(club_obj.club_id)
                logger.info(f"Bomb checks complete for {club_obj.club_name}")
            else:
                logger.info(f"Skipping bomb management for {club_obj.club_name} (bombs disabled)")

            # Generate and send daily reports
            status_summary = await self.quota_calculator.get_member_status_summary(
                club_obj.club_id, current_date, quota_period=club_obj.quota_period
            )

            if club_obj.bombs_enabled:
                bombs_data = await self.bomb_manager.get_active_bombs_with_members(club_obj.club_id)
            else:
                bombs_data = []

            effective_quota = await QuotaRequirement.get_quota_for_date(club_obj.club_id, current_date)

            if club_obj.image_report_enabled:
                monthly_rank = rank_data.get("monthly_rank") if rank_data else None
                img_path = None
                try:
                    img_path = await generate_tally_image(
                        club_obj.club_id, club_obj.club_name, current_date,
                        daily_quota=effective_quota, monthly_rank=monthly_rank,
                    )
                    await report_channel.send(file=discord.File(str(img_path), filename="quota_report.png"))
                    logger.info(f"✅ Tally image report sent for {club_obj.club_name}")
                except Exception as img_err:
                    logger.error(f"❌ Tally image failed for {club_obj.club_name}, falling back to embeds: {img_err}", exc_info=True)
                    daily_reports = self.report_generator.create_daily_report(
                        club_obj.club_name, effective_quota, status_summary, bombs_data, current_date,
                        rank_data=rank_data, quota_period=club_obj.quota_period,
                        club_timezone=club_obj.timezone,
                    )
                    for embed in daily_reports:
                        await report_channel.send(embed=embed)
                finally:
                    if img_path and img_path.exists():
                        os.unlink(img_path)
            else:
                daily_reports = self.report_generator.create_daily_report(
                    club_obj.club_name, effective_quota, status_summary, bombs_data, current_date,
                    rank_data=rank_data, quota_period=club_obj.quota_period,
                    club_timezone=club_obj.timezone,
                )
                for embed in daily_reports:
                    await report_channel.send(embed=embed)

            if deactivated:
                deactivation_embeds = self.report_generator.create_bomb_deactivation_report(
                    club_obj.club_name, deactivated
                )
                for embed in deactivation_embeds:
                    await report_channel.send(embed=embed)
                logger.info(f"✅ Bomb deactivation report sent ({len(deactivated)} member(s))")

            if newly_activated:
                bomb_data = []
                for bomb in newly_activated:
                    member = await Member.get_by_id(bomb.member_id)
                    bomb_data.append({'bomb': bomb, 'member': member})
                for embed in self.report_generator.create_bomb_activation_alert(club_obj.club_name, bomb_data):
                    await alert_channel.send(embed=embed)

            if members_to_kick:
                for embed in self.report_generator.create_kick_alert(club_obj.club_name, members_to_kick):
                    await alert_channel.send(embed=embed)

            # Auto-update monthly info board
            await self._update_monthly_info_board(club_obj, current_date)

            if deactivated:
                await interaction.followup.send(
                    f"✅ Check complete for {club}: {updated_members} members updated, {new_members} new members, {len(deactivated)} bombs defused"
                )
            else:
                await interaction.followup.send(
                    f"✅ Check complete for {club}: {updated_members} members updated, {new_members} new members"
                )

            await log_audit(
                interaction, 'sync.trigger', 'club',
                entity_id=club_obj.club_id, club_id=club_obj.club_id,
                details={'success': True, 'updated_members': updated_members},
            )

        except Exception as e:
            logger.error(f"Error in force_check: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(name="add_member", description="Manually add a new member")
    async def add_member(self, interaction: discord.Interaction,
                         trainer_name: str, join_date: str, club: str, trainer_id: str = None):
        """Manually add a member (format: YYYY-MM-DD)"""
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

            join_date_obj = datetime.strptime(join_date, "%Y-%m-%d").date()

            if trainer_id:
                existing = await Member.get_by_trainer_id(club_obj.club_id, trainer_id)
            else:
                existing = await Member.get_by_name(club_obj.club_id, trainer_name)

            if existing:
                await interaction.followup.send(f"❌ Member '{trainer_name}' already exists in {club}")
                return

            member = await Member.create(club_obj.club_id, trainer_name, join_date_obj, trainer_id)

            await interaction.followup.send(
                f"✅ Added member to {club}: {trainer_name} (joined {join_date}, ID: {trainer_id or 'N/A'})"
            )
            await log_audit(
                interaction, 'member.create', 'member',
                entity_id=member.member_id, club_id=club_obj.club_id,
                details={
                    'trainer_name': trainer_name,
                    'trainer_id': trainer_id,
                    'join_date': join_date_obj.isoformat(),
                },
            )

        except ValueError:
            await interaction.followup.send("❌ Invalid date format. Use YYYY-MM-DD")
        except Exception as e:
            logger.error(f"Error in add_member: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(name="deactivate_member", description="Manually deactivate a member")
    async def deactivate_member(self, interaction: discord.Interaction, trainer_name: str, club: str):
        """Manually deactivate a member"""
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

            member = await Member.get_by_name(club_obj.club_id, trainer_name)

            if not member:
                await interaction.followup.send(f"❌ Member '{trainer_name}' not found in {club}")
                return

            if not member.is_active:
                await interaction.followup.send(f"ℹ️ {trainer_name} is already inactive")
                return

            await member.deactivate(manual=True)

            embed = discord.Embed(
                title=f"✅ Member Manually Deactivated - {club}",
                description=f"**{trainer_name}** has been deactivated and will not be auto-reactivated.",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(
                name="ℹ️ Note",
                value="This member will stay inactive even if they appear in scraped data. "
                      "Use `/activate_member` to reactivate them.",
                inline=False
            )

            await interaction.followup.send(embed=embed)
            await log_audit(
                interaction, 'member.deactivate', 'member',
                entity_id=member.member_id, club_id=club_obj.club_id,
                details={'trainer_name': trainer_name},
            )
            logger.info(f"Manually deactivated member: {trainer_name} in {club} by {interaction.user}")

        except Exception as e:
            logger.error(f"Error in deactivate_member: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(name="activate_member", description="Reactivate a member")
    async def activate_member(self, interaction: discord.Interaction, trainer_name: str, club: str):
        """Reactivate a deactivated member"""
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

            member = await Member.get_by_name(club_obj.club_id, trainer_name)

            if not member:
                await interaction.followup.send(f"❌ Member '{trainer_name}' not found in {club}")
                return

            if member.is_active:
                await interaction.followup.send(f"ℹ️ {trainer_name} is already active")
                return

            await member.activate()

            embed = discord.Embed(
                title=f"✅ Member Reactivated - {club}",
                description=f"**{trainer_name}** has been reactivated.",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )

            await interaction.followup.send(embed=embed)
            await log_audit(
                interaction, 'member.reactivate', 'member',
                entity_id=member.member_id, club_id=club_obj.club_id,
                details={'trainer_name': trainer_name},
            )
            logger.info(f"Reactivated member: {trainer_name} in {club} by {interaction.user}")

        except Exception as e:
            logger.error(f"Error in activate_member: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(name="bomb_status", description="View all active bombs")
    async def bomb_status(self, interaction: discord.Interaction, club: str):
        """View all active bombs for a club"""
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

            bombs_data = await self.bomb_manager.get_active_bombs_with_members(club_obj.club_id)

            if not bombs_data:
                await interaction.followup.send(f"✅ No active bombs in {club}!")
                return

            embed = discord.Embed(
                title=f"💣 Active Bombs - {club}",
                description=f"Total: {len(bombs_data)}",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )

            for item in bombs_data[:25]:
                member = item['member']
                bomb = item['bomb']
                history = item['history']

                deficit = abs(history.deficit_surplus)

                embed.add_field(
                    name=f"{member.trainer_name}",
                    value=f"**Days Remaining:** {bomb.days_remaining}\n"
                          f"**Behind by:** {deficit:,} fans\n"
                          f"**Activated:** {bomb.activation_date.strftime('%Y-%m-%d')}",
                    inline=True
                )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in bomb_status: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(
        name="recalculate",
        description="Re-fetch from Uma.moe and repair this month's quota history and bombs",
    )
    async def recalculate(self, interaction: discord.Interaction, club: str):
        """Repair a club's month from the authoritative source.

        Use this whenever the numbers look wrong. It re-fetches the month from
        Uma.moe and rewrites the stored fan counts, join dates, deficits,
        days-behind counts and bombs to match — rather than recomputing from
        stored values that may themselves be wrong.
        """
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

            club_tz = resolve_timezone(club_obj.timezone)
            current_date = datetime.now(club_tz).date()

            await interaction.followup.send(
                f"🔄 Re-fetching **{club}** from Uma.moe and repairing this month…"
            )

            result = await recalculate_club(club_obj, current_date)

            colour = COLOR_BEHIND if result.warnings else COLOR_ON_TRACK
            embed = discord.Embed(
                title=f"✅ Recalculation complete — {club}",
                colour=colour,
                timestamp=discord.utils.utcnow(),
            )

            if result.month:
                embed.description = (
                    f"Rebuilt **{result.month[0]}-{result.month[1]:02d}** from Uma.moe."
                )

            if result.repaired_anything:
                fixed = []
                if result.fans_corrected:
                    fixed.append(f"**{result.fans_corrected}** wrong fan totals rewritten")
                if result.rows_added:
                    fixed.append(f"**{result.rows_added}** missing days filled in")
                if result.join_dates_fixed:
                    fixed.append(f"**{result.join_dates_fixed}** join dates corrected")
                embed.add_field(name="🔧 Repaired", value="\n".join(fixed), inline=False)
            else:
                embed.add_field(
                    name="🔧 Repaired",
                    value="_nothing was wrong — stored data already matched Uma.moe_",
                    inline=False,
                )

            embed.add_field(name="Rows rewritten", value=f"{result.rows_written:,}", inline=True)
            embed.add_field(
                name="Bombs",
                value=f"{result.bombs_cleared} cleared → {result.bombs_activated} re-armed",
                inline=True,
            )
            if result.members_not_in_api:
                embed.add_field(
                    name="Not in Uma.moe",
                    value=f"{result.members_not_in_api} member(s) left untouched",
                    inline=True,
                )

            if result.activated_names:
                shown = result.activated_names[:15]
                more = len(result.activated_names) - len(shown)
                embed.add_field(
                    name="💣 Bombs re-armed",
                    value="\n".join(shown) + (f"\n_…and {more} more_" if more else ""),
                    inline=False,
                )

            for warning in result.warnings:
                embed.add_field(name="⚠️ Partial", value=warning, inline=False)

            embed.set_footer(text=f"Run /force_check for a fresh report · by {interaction.user}")
            await interaction.followup.send(embed=embed)

            await log_audit(
                interaction, 'club.recalculate', 'club',
                entity_id=club_obj.club_id, club_id=club_obj.club_id,
                details={
                    'rows_written': result.rows_written,
                    'fans_corrected': result.fans_corrected,
                    'rows_added': result.rows_added,
                    'join_dates_fixed': result.join_dates_fixed,
                    'bombs_cleared': result.bombs_cleared,
                    'bombs_activated': result.bombs_activated,
                },
            )

            logger.info(
                f"recalculate {club} by {interaction.user}: "
                f"{result.rows_written} rows, {result.fans_corrected} fans corrected, "
                f"{result.rows_added} added, {result.join_dates_fixed} join dates, "
                f"bombs {result.bombs_cleared}->{result.bombs_activated}"
            )

        except Exception as e:
            logger.error(f"Error in recalculate: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(name="reset_month", description="Manually trigger monthly reset: clears all history, bombs, and quota requirements")
    async def reset_month(self, interaction: discord.Interaction, club: str):
        """Manually reset all monthly data for a club (for use when auto-reset fails)"""
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

            from config.database import db as _db

            await _db.execute("DELETE FROM quota_history WHERE club_id = $1", club_obj.club_id)
            await _db.execute("DELETE FROM bombs WHERE club_id = $1", club_obj.club_id)
            await _db.execute("DELETE FROM quota_requirements WHERE club_id = $1", club_obj.club_id)
            await _db.execute(
                "UPDATE members SET manually_deactivated = FALSE WHERE club_id = $1 AND manually_deactivated = TRUE",
                club_obj.club_id
            )

            embed = discord.Embed(
                title=f"🔄 Monthly Reset Complete - {club}",
                description=(
                    "All monthly data has been cleared.\n\n"
                    "**Cleared:**\n"
                    "• All quota history\n"
                    "• All active bombs\n"
                    "• All quota requirements\n"
                    "• Manual deactivation flags\n\n"
                    f"Run `/force_check club:{club}` to populate fresh data."
                ),
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            embed.set_footer(text=f"Reset by {interaction.user}")
            await interaction.followup.send(embed=embed)
            await log_audit(
                interaction, 'club.reset_month', 'club',
                entity_id=club_obj.club_id, club_id=club_obj.club_id,
                details={'club_name': club_obj.club_name},
            )
            logger.warning(f"Manual monthly reset performed for {club} by {interaction.user}")

        except Exception as e:
            logger.error(f"Error in reset_month: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    @app_commands.command(
        name="limiter_test",
        description="[DEV] Fire N concurrent requests to verify the Uma.moe rate limiter holds",
    )
    @app_commands.describe(
        count="How many requests to fire at once (default 50, max 300)",
        mode="dry = limiter only, no API call (default); real = actual scrapes (needs a club)",
        club="Club to scrape in 'real' mode (ignored in dry mode)",
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="dry (limiter only, no API hit)", value="dry"),
        app_commands.Choice(name="real (actual API scrapes)", value="real"),
    ])
    async def limiter_test(self, interaction: discord.Interaction,
                           count: int = 50,
                           mode: app_commands.Choice[str] = None,
                           club: str = None):
        """Stress-test the shared Uma.moe limiter: fire `count` requests at once
        and report whether the aggregate rate ever exceeded the cap.

        Owner-only: it consumes the shared global API budget, so it must not be
        runnable by every server admin the bot is invited to.
        """
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "❌ `/limiter_test` is a developer tool restricted to the bot owner "
                "(it draws from the shared API budget that every server's scrapes share).",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        mode_val = mode.value if mode else "dry"
        count = max(1, min(count, 300))

        circle_id = None
        if mode_val == "real":
            if not USE_UMAMOE_API:
                await interaction.followup.send("❌ Real mode needs `USE_UMAMOE_API` enabled.")
                return
            if not club:
                await interaction.followup.send("❌ Real mode needs a `club` to scrape against.")
                return
            club_obj = await Club.get_by_name(club)
            if not club_obj or not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(f"❌ Club '{club}' not found in this server.")
                return
            if not club_obj.is_circle_id_valid():
                await interaction.followup.send(f"❌ Club '{club}' has no valid circle_id.")
                return
            circle_id = club_obj.circle_id

        loop = asyncio.get_running_loop()
        stamps = []   # acquisition/completion time per request (seconds from start)
        order = []    # request index in completion order (FIFO check, dry mode)
        errors = []

        est = count * 60 // max(1, UMAMOE_RATE_PER_MIN)
        await interaction.followup.send(
            f"🚦 Firing **{count}** concurrent requests in **{mode_val}** mode through the shared "
            f"limiter (cap **{UMAMOE_RATE_PER_MIN}/min**, burst {UMAMOE_RATE_BURST}).\n"
            f"It paces them under the cap, so this should take roughly **~{est}s**…"
        )

        start = loop.time()

        async def fire(i):
            try:
                if mode_val == "real":
                    await UmaMoeAPIScraper(circle_id).scrape()  # self-limits internally
                else:
                    await umamoe_limiter.acquire()
                stamps.append(loop.time() - start)
                order.append(i)
            except Exception as e:
                errors.append(str(e))

        await asyncio.gather(*[fire(i) for i in range(count)])
        elapsed = loop.time() - start

        # Peak requests in any 60s sliding window — the number that must stay under cap.
        times = sorted(stamps)
        peak, j = 0, 0
        for i in range(len(times)):
            while times[i] - times[j] > 60:
                j += 1
            peak = max(peak, i - j + 1)

        cap = UMAMOE_RATE_PER_MIN + UMAMOE_RATE_BURST
        rate = (len(times) / elapsed * 60) if elapsed > 0 else 0
        under_cap = peak <= cap

        embed = discord.Embed(
            title="🚦 Rate Limiter Test",
            color=discord.Color.green() if under_cap and not errors else discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Mode", value=mode_val, inline=True)
        embed.add_field(name="Completed", value=f"{len(times)}/{count}", inline=True)
        embed.add_field(name="Errors", value=str(len(errors)), inline=True)
        embed.add_field(name="Wall time", value=f"{elapsed:.1f}s", inline=True)
        embed.add_field(name="Effective rate", value=f"{rate:.0f}/min", inline=True)
        embed.add_field(name="Peak / 60s", value=f"{peak} (cap {cap})", inline=True)
        if mode_val == "dry":
            embed.add_field(name="FIFO order",
                            value="✅ preserved" if order == sorted(order) else "⚠️ reordered",
                            inline=True)
        embed.add_field(
            name="Verdict",
            value=("✅ Limiter held — aggregate never exceeded the cap."
                   if under_cap else "❌ Cap exceeded — investigate!"),
            inline=False,
        )
        if errors:
            embed.add_field(name="Sample error", value=f"`{errors[0][:300]}`", inline=False)

        await interaction.followup.send(embed=embed)
        logger.info(f"limiter_test: mode={mode_val} count={count} peak60={peak} "
                    f"rate={rate:.0f}/min elapsed={elapsed:.1f}s errors={len(errors)}")

    @app_commands.command(
        name="live_board",
        description="Enable or disable the live board for a club",
    )
    @app_commands.describe(
        club="Which club",
        channel="Channel for the live board. Leave empty to turn it off.",
    )
    async def live_board(self, interaction: discord.Interaction, club: str,
                         channel: Optional[discord.TextChannel] = None):
        """Opt a club into the live board, or turn it off.

        The board is one self-editing message per competition day: posted when the
        day opens at 15:00 UTC, updated through the day, and given a final edit
        with the finished numbers once it closes. Edits don't ping anyone.

        It is display only — the daily report still runs on the club's own scrape
        time and remains the thing that drives bombs and DMs.
        """
        await interaction.response.defer()

        club_obj = await Club.get_by_name(club)
        if not club_obj:
            await interaction.followup.send(f"❌ Club '{club}' not found")
            return
        if not club_obj.belongs_to_guild(interaction.guild_id):
            await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
            return
        if not await ensure_can_manage(interaction, club_obj):
            return

        if channel is None:
            if not club_obj.live_board_enabled:
                await interaction.followup.send(
                    f"ℹ️ The live board is already off for **{club}**.\n"
                    f"Turn it on with `/live_board club:{club} channel:#some-channel`."
                )
                return
            await club_obj.set_live_board(None)
            await interaction.followup.send(
                f"✅ Live board disabled for **{club}**. The last message stays where it is."
            )
            await log_audit(
                interaction, 'club.update', 'club',
                entity_id=club_obj.club_id, club_id=club_obj.club_id,
                details={'changes': {'live_board_channel_id': None}},
            )
            return

        if not club_obj.is_circle_id_valid():
            await interaction.followup.send(club_obj.get_circle_id_help_message())
            return

        perms = channel.permissions_for(interaction.guild.me)
        missing = [n for n, ok in (("View Channel", perms.view_channel),
                                   ("Send Messages", perms.send_messages),
                                   ("Embed Links", perms.embed_links)) if not ok]
        if missing:
            await interaction.followup.send(
                f"❌ I'm missing these permissions in {channel.mention}: "
                f"**{', '.join(missing)}**"
            )
            return

        await club_obj.set_live_board(channel.id)

        embed = discord.Embed(
            title="✅ Live board enabled",
            description=f"**{club}** → {channel.mention}",
            colour=COLOR_ON_TRACK,
        )
        embed.add_field(
            name="How it works",
            value=("One message per competition day. It's posted when the day opens "
                   "(15:00 UTC), edited as new figures arrive, then given a final "
                   "edit once the day closes. Edits don't notify anyone."),
            inline=False,
        )
        embed.add_field(
            name="Note",
            value=("Live numbers are **not final** — the day is still running. Your "
                   "daily report is unchanged and still drives quota, bombs and DMs."),
            inline=False,
        )
        embed.set_footer(text="The first board appears within the hour.")
        await interaction.followup.send(embed=embed)
        await log_audit(
            interaction, 'club.update', 'club',
            entity_id=club_obj.club_id, club_id=club_obj.club_id,
            details={'changes': {'live_board_channel_id': str(channel.id)}},
        )
        logger.info(f"Live board enabled for {club} in channel {channel.id}")

    @app_commands.command(
        name="live_refresh",
        description="Force the live board to update now instead of waiting for its slot",
    )
    @app_commands.describe(club="Which club's board to refresh")
    async def live_refresh(self, interaction: discord.Interaction, club: str):
        """Update a club's live board immediately.

        The board normally refreshes on a fixed minute of the hour so that many
        clubs spread their API calls out. This bypasses that for one club, which
        is what you want when checking a change rather than waiting up to an hour.
        """
        await interaction.response.defer(ephemeral=True)

        club_obj = await Club.get_by_name(club)
        if not club_obj:
            await interaction.followup.send(f"❌ Club '{club}' not found")
            return
        if not club_obj.belongs_to_guild(interaction.guild_id):
            await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
            return
        if not await ensure_can_manage(interaction, club_obj):
            return
        if not club_obj.live_board_enabled:
            await interaction.followup.send(
                f"❌ The live board is off for **{club}**.\n"
                f"Enable it with `/live_board club:{club} channel:#some-channel`."
            )
            return

        from services.live_board import update_club as refresh_board
        from utils.jst_calendar import resolve_live

        target = resolve_live()
        status = await refresh_board(self.bot, club_obj)

        if status == "no_data":
            embed = discord.Embed(
                title=f"⏳ Nothing to show yet - {club}",
                description=(
                    f"Uma.moe has no rows for **{target.year}-{target.month:02d}** "
                    f"slot `{target.slot}` (competition day **{target.data_date}**) yet.\n\n"
                    "A competition month becomes queryable before its member data is "
                    "published, and each day is empty until the first live update. "
                    "The existing board was left untouched rather than blanked — "
                    "try again later."
                ),
                colour=COLOR_BEHIND,
            )
            await interaction.followup.send(embed=embed)
            return

        if status == "failed":
            await interaction.followup.send(
                f"❌ Couldn't post or edit the board for **{club}**. Uma.moe returned "
                f"data, so this is a Discord-side problem — most likely the bot lacks "
                f"permission in <#{club_obj.live_board_channel_id}>. Check the logs "
                f"for the exact error."
            )
            return

        action = "Board edited in place." if status == "edited" else "Posted a new board."
        embed = discord.Embed(
            title=f"✅ Live board refreshed - {club}",
            description=action,
            colour=COLOR_ON_TRACK,
        )
        embed.add_field(
            name="Reading",
            value=(f"`{target.year}-{target.month:02d}` slot `{target.slot}`\n"
                   f"JST day {target.jst_day} → competition day **{target.data_date}**"),
            inline=False,
        )
        if club_obj.live_board_message_id:
            embed.add_field(
                name="Message",
                value=(f"https://discord.com/channels/{interaction.guild_id}/"
                       f"{club_obj.live_board_channel_id}/{club_obj.live_board_message_id}"),
                inline=False,
            )
        await interaction.followup.send(embed=embed)
        logger.info(f"/live_refresh {club} by {interaction.user}: {status}")

    @app_commands.command(
        name="backup",
        description="Run a database backup now, or show the retained backups",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="status (list retained backups)", value="status"),
        app_commands.Choice(name="now (run a backup immediately)", value="now"),
    ])
    async def backup(self, interaction: discord.Interaction,
                     action: app_commands.Choice[str] = None):
        """Inspect or trigger the daily database backup.

        Owner-only: it reveals host paths and dumps the whole database, which is
        not something a guild admin of an invited server should be able to do.
        """
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "❌ `/backup` is a developer tool restricted to the bot owner.",
                ephemeral=True,
            )
            return

        from pathlib import Path
        from config.settings import (
            DATABASE_URL, DB_BACKUP_DIR, DB_BACKUP_KEEP, DB_BACKUP_ENABLED,
            DB_BACKUP_UTC_TIME, DB_BACKUP_TIMEOUT_SEC,
        )
        from services.backup_service import (
            create_backup, list_backups, find_pg_dump,
        )

        action_val = action.value if action else "status"
        backup_dir = Path(DB_BACKUP_DIR)

        if action_val == "status":
            existing = list_backups(backup_dir)
            pg_dump = find_pg_dump()

            embed = discord.Embed(
                title="💾 Database Backups",
                colour=COLOR_INFO if pg_dump else COLOR_BOMB,
            )
            embed.add_field(
                name="Schedule",
                value=(f"{'Enabled' if DB_BACKUP_ENABLED else '**Disabled**'} — "
                       f"daily at {DB_BACKUP_UTC_TIME} UTC, keeping {DB_BACKUP_KEEP}"),
                inline=False,
            )
            embed.add_field(
                name="pg_dump",
                value=f"`{pg_dump}`" if pg_dump else
                      "❌ **not found** — install `postgresql-client` or set `PG_DUMP_PATH`",
                inline=False,
            )
            embed.add_field(name="Directory", value=f"`{backup_dir.resolve()}`", inline=False)

            if existing:
                total = sum(p.stat().st_size for p in existing)
                lines = [
                    f"`{p.name}` — {p.stat().st_size / 1024:.0f} KB"
                    for p in existing[:10]
                ]
                embed.add_field(
                    name=f"Retained ({len(existing)}, {total / 1024 / 1024:.1f} MB total)",
                    value="\n".join(lines),
                    inline=False,
                )
            else:
                embed.add_field(
                    name="Retained",
                    value="_none yet_" + ("" if DB_BACKUP_ENABLED else " (backups are disabled)"),
                    inline=False,
                )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # action == "now"
        await interaction.response.defer(ephemeral=True)
        result = await create_backup(
            database_url=DATABASE_URL, backup_dir=backup_dir,
            keep=DB_BACKUP_KEEP, timeout_sec=DB_BACKUP_TIMEOUT_SEC,
        )

        if result.ok:
            embed = discord.Embed(
                title="✅ Backup complete",
                description=f"`{result.path.name}`",
                colour=COLOR_ON_TRACK,
            )
            embed.add_field(name="Size", value=result.size_human)
            embed.add_field(name="Took", value=f"{result.duration_sec:.1f}s")
            embed.add_field(name="Retained", value=str(len(list_backups(backup_dir))))
            if result.pruned:
                embed.set_footer(text=f"Pruned {result.pruned} old backup(s)")
        else:
            embed = discord.Embed(
                title="❌ Backup failed",
                description=result.error,
                colour=COLOR_BOMB,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)
        logger.info(f"/backup now → ok={result.ok} error={result.error}")

    # Register autocomplete for all club arguments
    set_quota.autocomplete('club')(club_autocomplete)
    update_monthly_info.autocomplete('club')(club_autocomplete)
    quota_history.autocomplete('club')(club_autocomplete)
    delete_quota.autocomplete('club')(club_autocomplete)
    force_check.autocomplete('club')(club_autocomplete)
    add_member.autocomplete('club')(club_autocomplete)
    deactivate_member.autocomplete('club')(club_autocomplete)
    activate_member.autocomplete('club')(club_autocomplete)
    bomb_status.autocomplete('club')(club_autocomplete)
    recalculate.autocomplete('club')(club_autocomplete)
    reset_month.autocomplete('club')(club_autocomplete)
    limiter_test.autocomplete('club')(club_autocomplete)
    live_board.autocomplete('club')(club_autocomplete)
    live_refresh.autocomplete('club')(club_autocomplete)


async def setup(bot):
    """Setup function for loading the cog"""
    await bot.add_cog(AdminCommands(bot))