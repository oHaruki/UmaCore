"""
Main entry point for the Umamusume Discord Bot
"""
import asyncio
import logging
import sys

from config.database import db
from config.settings import DISCORD_TOKEN, DATABASE_URL
from bot import create_bot
from utils.logger import setup_logging

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)


async def main():
    """Main function to start the bot"""
    # Validate configuration
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN not set in environment variables")
        sys.exit(1)
    
    if not DATABASE_URL:
        logger.error("DATABASE_URL not set in environment variables")
        sys.exit(1)
    
    # Set database URL
    db.url = DATABASE_URL
    
    try:
        # Connect to database
        logger.info("Connecting to database...")
        await db.connect()
        
        # Initialize schema
        logger.info("Initializing database schema...")
        await db.initialize_schema()
        
        # Create and start bot
        logger.info("Starting Discord bot...")
        bot = create_bot()
        
        async with bot:
            await bot.start(DISCORD_TOKEN)
            
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Cleanup
        if db.pool:
            logger.info("Closing database connection...")
            await db.disconnect()
        
        logger.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown by user")
