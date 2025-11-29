"""
Discord bot client setup
"""
import discord
from discord.ext import commands
import logging

from config.settings import DISCORD_TOKEN
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
        
        # Start scheduled tasks
        if not self.tasks_manager:
            self.tasks_manager = BotTasks(self)
            self.tasks_manager.start_tasks()
            logger.info("Scheduled tasks started")
    
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
