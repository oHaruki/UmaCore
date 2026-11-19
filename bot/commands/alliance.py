"""
/alliance: the shared leaderboard of a club's alliance.
"""
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from models import Club
from services import alliance as alliance_service

logger = logging.getLogger(__name__)

SETUP_URL = f"{alliance_service.SITE_URL}/dashboard/alliance"


class AllianceCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def club_autocomplete(self, interaction: discord.Interaction, current: str):
        try:
            names = await Club.get_names_for_guild(interaction.guild_id)
            return [
                app_commands.Choice(name=n, value=n)
                for n in names if current.lower() in n.lower()
            ][:25]
        except Exception as e:
            logger.error(f"Error in club autocomplete: {e}")
            return []

    @app_commands.command(name="alliance", description="Show the shared leaderboard of your club's alliance")
    @app_commands.describe(club="Club whose alliance to show (only needed if this server has several)")
    @app_commands.guild_only()
    async def alliance(self, interaction: discord.Interaction, club: Optional[str] = None):
        await interaction.response.defer()
        try:
            clubs = [c for c in await Club.get_all_for_guild(interaction.guild_id) if c.is_active]
            if club:
                clubs = [c for c in clubs if c.club_name == club]
                if not clubs:
                    await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
                    return

            alliance_ids = []
            for c in clubs:
                aid = await alliance_service.alliance_id_for_club(c.club_id)
                if aid and aid not in alliance_ids:
                    alliance_ids.append(aid)

            if not alliance_ids:
                who = f"**{club}** is" if club else "No club in this server is"
                await interaction.followup.send(
                    f"{who} not in an alliance yet. A club manager can create or join one at {SETUP_URL}"
                )
                return
            if len(alliance_ids) > 1:
                await interaction.followup.send(
                    "Clubs in this server are in different alliances. Pick one with the `club` option."
                )
                return

            standings = await alliance_service.load_standings(alliance_ids[0])
            if not standings:
                await interaction.followup.send("❌ That alliance no longer exists.")
                return
            await interaction.followup.send(embed=alliance_service.build_embed(standings))
        except Exception as e:
            logger.error(f"/alliance failed: {e}", exc_info=True)
            await interaction.followup.send("❌ Couldn't load the alliance leaderboard. Try again in a bit.")

    alliance.autocomplete('club')(club_autocomplete)
