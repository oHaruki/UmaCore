"""
Bot commands package
"""

async def setup(bot):
    """Load all command cogs"""
    from .settings import SettingsCommands
    from .admin import AdminCommands
    from .member import MemberCommands
    from .club_management import ClubManagementCommands
    from .author import AuthorCommands

    await bot.add_cog(SettingsCommands(bot))
    await bot.add_cog(AdminCommands(bot))
    await bot.add_cog(MemberCommands(bot))
    await bot.add_cog(ClubManagementCommands(bot))
    await bot.add_cog(AuthorCommands(bot))
