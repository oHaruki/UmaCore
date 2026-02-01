"""
Discord bot client setup
"""
import discord
from discord.ext import commands
import logging

from config.settings import DISCORD_TOKEN
from config.database import db
from .tasks import BotTasks

logger = logging.getLogger(__name__)


class UmamusumeBot(commands.Bot):
    """Custom Discord bot for Umamusume quota tracking"""
    
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )
        
        self.tasks_manager = None
    
    async def setup_hook(self):
        """Called when the bot is starting up"""
        # Load commands cog
        await self.load_extension('bot.commands')
        
        # Sync slash commands
        await self.tree.sync()
        
        logger.info("Commands synced successfully")
    
    async def on_ready(self):
        """Called when the bot is ready"""
        logger.info(f"Logged in as {self.user.name} ({self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guild(s)")
        
        # Set bot status
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Umamusume quotas 📊"
            )
        )
        
        # One-time backfill for clubs created before guild_id existed
        await self._backfill_guild_ids()
        
        # Start scheduled tasks
        if not self.tasks_manager:
            self.tasks_manager = BotTasks(self)
            self.tasks_manager.start_tasks()
            logger.info("Scheduled tasks started")
    
    async def _backfill_guild_ids(self):
        """
        Populate guild_id for clubs that were created before the column existed.
        Resolves each club's report_channel_id to its guild using the bot's
        channel cache (no API calls needed). Becomes a no-op once all clubs
        have been backfilled.
        """
        try:
            rows = await db.fetch("""
                SELECT club_id, club_name, report_channel_id
                FROM clubs
                WHERE guild_id IS NULL AND report_channel_id IS NOT NULL
            """)
            
            if not rows:
                logger.debug("Guild ID backfill: nothing to do")
                return
            
            backfilled = 0
            skipped = 0
            
            for row in rows:
                channel = self.get_channel(row["report_channel_id"])
                if channel is None:
                    # Bot isn't in that guild yet — will pick it up on next restart
                    skipped += 1
                    logger.debug(f"Guild ID backfill: skipped {row['club_name']} (channel not in cache)")
                    continue
                
                guild_id = channel.guild.id
                await db.execute(
                    "UPDATE clubs SET guild_id = $1 WHERE club_id = $2",
                    guild_id, row["club_id"]
                )
                backfilled += 1
                logger.info(f"Guild ID backfill: {row['club_name']} → guild {guild_id}")
            
            if backfilled or skipped:
                logger.info(f"Guild ID backfill complete: {backfilled} updated, {skipped} skipped")
        
        except Exception as e:
            # Non-fatal: bot still starts even if backfill fails
            logger.error(f"Guild ID backfill failed: {e}", exc_info=True)
    
    async def on_command_error(self, ctx, error):
        """Global error handler for commands"""
        if isinstance(error, commands.CommandNotFound):
            return
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You don't have permission to use this command")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Missing required argument: {error.param}")
        else:
            logger.error(f"Command error: {error}", exc_info=error)
            await ctx.send(f"❌ An error occurred: {str(error)}")
    
    async def close(self):
        """Cleanup when bot is shutting down"""
        if self.tasks_manager:
            self.tasks_manager.stop_tasks()
        
        await super().close()
        logger.info("Bot shut down successfully")


def create_bot() -> UmamusumeBot:
    """Create and return the bot instance"""
    return UmamusumeBot()