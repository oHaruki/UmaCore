"""
Author-only bot statistics command
"""
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
import logging

from config.database import db

logger = logging.getLogger(__name__)

AUTHOR_ID = 139769063948681217


def is_bot_author():
    """Restrict command to the bot author only"""
    def predicate(interaction: discord.Interaction):
        return interaction.user.id == AUTHOR_ID
    return app_commands.check(predicate)


class AuthorCommands(commands.Cog):
    """Author-only commands for monitoring and management"""

    def __init__(self, bot):
        self.bot = bot
        self.start_time = datetime.utcnow()

    @app_commands.command(name="stats", description="View bot statistics")
    @is_bot_author()
    async def stats(self, interaction: discord.Interaction):
        """Display bot-wide statistics"""
        await interaction.response.defer(ephemeral=True)

        try:
            # Overview counts
            club_stats = await db.fetchrow("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN is_active THEN 1 ELSE 0 END) as active
                FROM clubs
            """)

            member_stats = await db.fetchrow("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN is_active THEN 1 ELSE 0 END) as active
                FROM members
            """)

            bomb_count = await db.fetchval(
                "SELECT COUNT(*) FROM bombs WHERE is_active = TRUE"
            )

            # Per-club breakdown
            club_breakdown = await db.fetch("""
                SELECT
                    c.club_name,
                    c.is_active as club_active,
                    (SELECT COUNT(*) FROM members m WHERE m.club_id = c.club_id) as total_members,
                    (SELECT COUNT(*) FROM members m WHERE m.club_id = c.club_id AND m.is_active) as active_members,
                    (SELECT COUNT(*) FROM bombs b
                     JOIN members m ON b.member_id = m.member_id
                     WHERE m.club_id = c.club_id AND b.is_active) as active_bombs
                FROM clubs c
                ORDER BY c.club_name
            """)

            # Uptime
            uptime = datetime.utcnow() - self.start_time
            uptime_str = self._format_uptime(uptime)

            # Build embed
            embed = discord.Embed(
                title="📊 Bot Statistics",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )

            embed.add_field(
                name="🌐 Overview",
                value=f"**Servers:** {len(self.bot.guilds)}\n"
                      f"**Clubs:** {club_stats['active']} active / {club_stats['total']} total\n"
                      f"**Members:** {member_stats['active']} active / {member_stats['total']} total\n"
                      f"**Active Bombs:** {bomb_count or 0}\n"
                      f"**Uptime:** {uptime_str}",
                inline=False
            )

            # Per-club breakdown
            if club_breakdown:
                lines = []
                for club in club_breakdown:
                    status = "✅" if club['club_active'] else "❌"
                    line = (
                        f"{status} **{club['club_name']}**: "
                        f"{club['active_members']} active / {club['total_members']} total"
                    )
                    if club['active_bombs']:
                        line += f" · 💣 {club['active_bombs']}"
                    lines.append(line)

                embed.add_field(
                    name="🏆 Clubs",
                    value="\n".join(lines),
                    inline=False
                )

            # Server list (capped at 20 to stay within field value limits)
            if self.bot.guilds:
                guild_list = [f"• {g.name}" for g in self.bot.guilds[:20]]
                if len(self.bot.guilds) > 20:
                    guild_list.append(f"• *...and {len(self.bot.guilds) - 20} more*")

                embed.add_field(
                    name="🏠 Servers",
                    value="\n".join(guild_list),
                    inline=False
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in stats command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

    @staticmethod
    def _format_uptime(uptime) -> str:
        """Format a timedelta into a readable uptime string"""
        total_seconds = int(uptime.total_seconds())
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60

        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")

        return " ".join(parts) if parts else "< 1m"


async def setup(bot):
    """Setup function for loading the cog"""
    await bot.add_cog(AuthorCommands(bot))
