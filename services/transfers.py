"""
What happens around a transfer request besides the database write.

A decision can be taken from two places — the ``/transfer_queue`` panel in
Discord and the dashboard's Transfers page — and both owe the requester the same
DM and the same tidied-up announcement. That is why this lives here rather than
in the cog: the web path reaches it through the bot's internal API, so the two
surfaces cannot drift into telling people different things.
"""
import logging
from typing import Optional

import discord

from config.settings import COLOR_BEHIND
from models import Club, TransferRequest
from models.transfer_request import APPROVED, REJECTED

logger = logging.getLogger(__name__)


def _summary(request: TransferRequest, club: Club) -> str:
    parts = [f"**{request.trainer_name}**"]
    if request.trainer_id:
        parts.append(f"`{request.trainer_id}`")
    parts.append(f"{request.origin} → **{club.club_name}**")
    return " · ".join(parts)


async def announce(bot, club: Club, request: TransferRequest) -> None:
    """Post a new request into the club's transfer channel, if it has one.

    Deliberately a plain line with no buttons on it. A club taking a dozen
    requests a week would otherwise accumulate a dozen live control panels in
    one channel, and the one that gets clicked is whichever scrolled past most
    recently rather than whichever is next in the queue. Decisions are taken in
    one place — the ``/transfer_queue`` panel, or the dashboard.
    """
    if not club.transfer_channel_id:
        return

    channel = bot.get_channel(club.transfer_channel_id)
    if channel is None:
        logger.warning(
            f"Transfer channel {club.transfer_channel_id} for {club.club_name} is not in cache"
        )
        return

    position = await request.position()
    embed = discord.Embed(
        title="📥 New transfer request",
        description=_summary(request, club),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Requested by", value=f"<@{request.discord_user_id}>", inline=True)
    embed.add_field(name="Queue position", value=f"#{position}", inline=True)
    if request.note:
        embed.add_field(name="Note", value=request.note[:1000], inline=False)
    embed.set_footer(text=f"Review with /transfer_queue club:{club.club_name}")

    try:
        message = await channel.send(embed=embed)
    except discord.Forbidden:
        logger.warning(f"Cannot post transfer announcement in {club.transfer_channel_id}")
        return
    except Exception as e:
        logger.error(f"Failed to announce transfer request: {e}")
        return

    await request.set_announcement(channel.id, message.id)


async def _close_announcement(bot, request: TransferRequest, club: Club) -> None:
    """Rewrite the announcement to show how the request ended.

    Without this the channel fills with requests that all still read as open,
    which is the exact confusion the queue exists to remove.
    """
    if not (request.announce_channel_id and request.announce_message_id):
        return

    channel = bot.get_channel(request.announce_channel_id)
    if channel is None:
        return

    approved = request.status == APPROVED
    embed = discord.Embed(
        title="✅ Transfer approved" if approved else "🚫 Transfer declined",
        description=_summary(request, club),
        color=discord.Color.green() if approved else discord.Color.dark_grey(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Requested by", value=f"<@{request.discord_user_id}>", inline=True)
    embed.add_field(name="Decided by", value=request.decided_by_name or "—", inline=True)
    if request.decision_note:
        embed.add_field(name="Reason", value=request.decision_note[:1000], inline=False)

    try:
        message = await channel.fetch_message(request.announce_message_id)
        await message.edit(embed=embed)
    except discord.NotFound:
        pass
    except Exception as e:
        logger.warning(f"Could not update transfer announcement: {e}")


async def _notify_requester(bot, request: TransferRequest, club: Club) -> bool:
    """DM the requester the outcome. False if the DM could not be delivered.

    The requester is addressed by Discord ID taken from the request itself, not
    through ``user_links`` — someone transferring in from outside has no member
    row to link to, and would otherwise be the one person who never hears back.
    """
    approved = request.status == APPROVED

    if approved:
        embed = discord.Embed(
            title="✅ Transfer request approved",
            description=(
                f"Your transfer request to **{club.club_name}** has been approved.\n\n"
                f"Make sure to check your in-game notifications for your new invite."
            ),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
    else:
        embed = discord.Embed(
            title="🚫 Transfer request declined",
            description=f"Your transfer request to **{club.club_name}** was not accepted.",
            color=COLOR_BEHIND,
            timestamp=discord.utils.utcnow(),
        )
        if request.decision_note:
            embed.add_field(name="Reason", value=request.decision_note[:1000], inline=False)

    embed.add_field(name="Trainer", value=request.trainer_name, inline=True)
    if request.decided_by_name:
        embed.add_field(name="Decided by", value=request.decided_by_name, inline=True)
    embed.set_footer(text=club.club_name)

    try:
        user = await bot.fetch_user(request.discord_user_id)
        await user.send(embed=embed)
        return True
    except discord.Forbidden:
        logger.warning(f"Cannot DM {request.discord_user_id} about transfer (DMs disabled)")
        return False
    except Exception as e:
        logger.error(f"Failed to DM transfer decision to {request.discord_user_id}: {e}")
        return False


async def apply_decision(bot, request_id, status: str, decided_by_name: str,
                         reason: Optional[str] = None) -> dict:
    """Settle a request and tell everyone who needs to know.

    Returns ``{'ok': bool, ...}``. ``ok`` is False only when the request could
    not be settled at all — a DM that could not be delivered still leaves the
    decision made, because the club's queue must not depend on the requester's
    privacy settings.
    """
    request = await TransferRequest.decide(request_id, status, decided_by_name, reason)
    if request is None:
        return {'ok': False, 'error': 'already decided'}

    club = await Club.get_by_id(request.to_club_id)
    if club is None:
        return {'ok': True, 'notified': False, 'status': request.status}

    notified = await _notify_requester(bot, request, club)
    await _close_announcement(bot, request, club)

    return {
        'ok': True,
        'status': request.status,
        'notified': notified,
        'trainer_name': request.trainer_name,
        'club_name': club.club_name,
    }


async def approve(bot, request_id, decided_by_name: str, reason: Optional[str] = None) -> dict:
    return await apply_decision(bot, request_id, APPROVED, decided_by_name, reason)


async def reject(bot, request_id, decided_by_name: str, reason: Optional[str] = None) -> dict:
    return await apply_decision(bot, request_id, REJECTED, decided_by_name, reason)
