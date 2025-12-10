"""
Bot commands package
"""

async def setup(bot):
    """Load all command cogs"""
    from .settings import SettingsCommands
    from .admin import AdminCommands
    from .member import MemberCommands
    
    await bot.add_cog(SettingsCommands(bot))
    await bot.add_cog(AdminCommands(bot))
    await bot.add_cog(MemberCommands(bot))
