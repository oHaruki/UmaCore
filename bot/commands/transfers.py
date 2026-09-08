"""
Club transfer queue commands.

Replaces the hand-run version of this — post your name, trainer ID and where you
are moving from in a channel, then ping a mod — with a list that survives the
channel scrolling, knows who is still waiting, and tells people when they are
in.

Review happens in one ephemeral panel per invocation (``/transfer_queue``)
rather than a button pair per request. A club taking a dozen requests a week
would otherwise leave a dozen live control panels lying around in one channel,
where the one that gets clicked is whichever scrolled past last.
"""
import logging
from typing import List, Optional
from uuid import UUID

import discord
from discord import app_commands
from discord.ext import commands

from models import Club, Member, TransferRequest, UserLink
from services import transfers as transfer_service
from utils.audit import log_audit
from utils.permissions import can_manage_club

logger = logging.getLogger(__name__)

MAX_PANEL_ROWS = 25  # Discord's hard cap on select-menu options


def _line(index: int, request: TransferRequest) -> str:
    who = f"**{request.trainer_name}**"
    ident = f" `{request.trainer_id}`" if request.trainer_id else ""
    return (f"`#{index}` {who}{ident}\n"
            f"　from {request.origin} · <@{request.discord_user_id}>")


def _queue_embed(club: Club, queue: List[TransferRequest], *, can_review: bool) -> discord.Embed:
    embed = discord.Embed(
        title=f"📋 Transfer queue — {club.club_name}",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )

    if not queue:
        embed.description = "Nobody is waiting to transfer in right now."
        return embed

    shown = queue[:MAX_PANEL_ROWS]
    embed.description = "\n".join(_line(i, r) for i, r in enumerate(shown, start=1))

    if len(queue) > len(shown):
        embed.set_footer(text=f"{len(queue)} waiting · showing the first {len(shown)}")
    elif can_review:
        # Said plainly because the numbering invites the opposite reading.
        embed.set_footer(text="Positions are order of arrival — you can approve anyone on this list.")
    else:
        embed.set_footer(text=f"{len(queue)} waiting")

    return embed


class RejectReasonModal(discord.ui.Modal, title="Decline transfer request"):
    """Optional reason, sent on to the requester with the decline."""

    reason = discord.ui.TextInput(
        label="Reason (optional)",
        placeholder="Shown to the requester in their DM",
        required=False,
        max_length=400,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, panel: 'QueuePanel', request: TransferRequest):
        super().__init__()
        self.panel = panel
        self.request = request

    async def on_submit(self, interaction: discord.Interaction):
        # An untouched optional field can come back empty or unset; both mean
        # "no reason given" and neither may reach the requester as text.
        reason = (self.reason.value or '').strip() or None
        await self.panel.decide(interaction, self.request, 'rejected', reason)


class QueuePanel(discord.ui.View):
    """Ephemeral review panel: pick someone from the queue, then decide.

    Ephemeral and short-lived on purpose, so it needs no persistent-view
    registration: the panel belongs to the leader who ran the command, and a
    stale one is re-opened rather than clicked.
    """

    def __init__(self, club: Club, queue: List[TransferRequest],
                 invoker_id: int, *, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.club = club
        self.invoker_id = invoker_id
        self.queue = queue
        self.selected: Optional[UUID] = None
        self._rebuild()

    def _rebuild(self) -> None:
        """Re-render the select from the current queue and selection."""
        shown = self.queue[:MAX_PANEL_ROWS]

        self.picker.options = [
            discord.SelectOption(
                label=f"#{i} {r.trainer_name}"[:100],
                value=str(r.request_id),
                description=f"from {r.origin}"[:100],
                default=(r.request_id == self.selected),
            )
            for i, r in enumerate(shown, start=1)
        ] or [discord.SelectOption(label="Queue is empty", value="none", default=True)]

        empty = not self.queue
        self.picker.disabled = empty
        # Nothing to act on until a specific request is picked — an approve
        # button that acts on "whoever happens to be first" is how the wrong
        # person gets let in.
        self.approve_button.disabled = empty or self.selected is None
        self.reject_button.disabled = empty or self.selected is None

    def _find(self, request_id: UUID) -> Optional[TransferRequest]:
        return next((r for r in self.queue if r.request_id == request_id), None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the person who opened this panel can use it.", ephemeral=True
            )
            return False
        return True

    async def refresh(self, interaction: discord.Interaction, notice: Optional[str] = None) -> None:
        """Reload the queue from the database and redraw the panel."""
        self.queue = await TransferRequest.get_queue(self.club.club_id)
        if self.selected and not self._find(self.selected):
            self.selected = None
        self._rebuild()

        embed = _queue_embed(self.club, self.queue, can_review=True)
        if notice:
            embed.description = f"{notice}\n\n{embed.description}"

        if not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
            return

        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except discord.HTTPException:
            # A decline arrives here on the modal's interaction rather than the
            # panel's, and the decision has already been taken by this point. If
            # Discord will not let us redraw the panel from it, the leader must
            # still be told the outcome landed rather than left reading a stale
            # queue and deciding the click did nothing.
            if notice:
                await interaction.followup.send(notice, ephemeral=True)

    async def decide(self, interaction: discord.Interaction, request: TransferRequest,
                     status: str, reason: Optional[str]) -> None:
        """Settle one request, then redraw the panel around what is left."""
        await interaction.response.defer()

        result = await transfer_service.apply_decision(
            interaction.client, request.request_id, status,
            interaction.user.display_name, reason,
        )

        if not result.get('ok'):
            await self.refresh(
                interaction,
                notice=f"⚠️ **{request.trainer_name}**'s request was already handled by someone else.",
            )
            return

        await log_audit(
            interaction, f'transfer.{status}', 'transfer_request',
            entity_id=request.request_id, club_id=self.club.club_id,
            details={'trainer_name': request.trainer_name,
                     'discord_user_id': str(request.discord_user_id),
                     'reason': reason},
        )

        verb = "approved" if status == 'approved' else "declined"
        notice = f"✅ **{request.trainer_name}** {verb}."
        if not result.get('notified'):
            notice += " (Couldn't DM them — their DMs are closed.)"

        await self.refresh(interaction, notice=notice)

    @discord.ui.select(placeholder="Pick a request to review…", min_values=1, max_values=1)
    async def picker(self, interaction: discord.Interaction, select: discord.ui.Select):
        value = select.values[0]
        self.selected = None if value == "none" else UUID(value)
        self._rebuild()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, row=1)
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        request = self._find(self.selected) if self.selected else None
        if request is None:
            await self.refresh(interaction, notice="⚠️ That request is no longer in the queue.")
            return
        await self.decide(interaction, request, 'approved', None)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, row=1)
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        request = self._find(self.selected) if self.selected else None
        if request is None:
            await self.refresh(interaction, notice="⚠️ That request is no longer in the queue.")
            return
        await interaction.response.send_modal(RejectReasonModal(self, request))

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.refresh(interaction)


class MyRequestsView(discord.ui.View):
    """Withdraw one of your own pending requests."""

    def __init__(self, requests: List[TransferRequest], club_names: dict, invoker_id: int,
                 *, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.invoker_id = invoker_id
        self.cancel_picker.options = [
            discord.SelectOption(
                label=club_names.get(r.to_club_id, "Unknown club")[:100],
                value=str(r.request_id),
                description=f"as {r.trainer_name}"[:100],
            )
            for r in requests
        ]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.invoker_id

    @discord.ui.select(placeholder="Withdraw a request…", min_values=1, max_values=1)
    async def cancel_picker(self, interaction: discord.Interaction, select: discord.ui.Select):
        request_id = UUID(select.values[0])

        request = await TransferRequest.get_by_id(request_id)
        # Ownership is re-checked against the row rather than trusted from the
        # component, which is client-supplied data.
        if request is None or request.discord_user_id != interaction.user.id:
            await interaction.response.edit_message(
                content="That request is no longer yours to withdraw.", embed=None, view=None
            )
            return

        cancelled = await TransferRequest.decide(
            request_id, 'cancelled', interaction.user.display_name
        )
        if cancelled is None:
            await interaction.response.edit_message(
                content="That request was already decided by a club leader.",
                embed=None, view=None,
            )
            return

        club = await Club.get_by_id(cancelled.to_club_id)
        await interaction.response.edit_message(
            content=f"✅ Withdrew your transfer request to **{club.club_name if club else 'that club'}**.",
            embed=None, view=None,
        )


class TransferCommands(commands.Cog):
    """Queue for a spot in another club, and review who is waiting."""

    def __init__(self, bot):
        self.bot = bot

    async def club_autocomplete(self, interaction: discord.Interaction, current: str):
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

    async def _resolve_club(self, interaction: discord.Interaction, name: str) -> Optional[Club]:
        """Look up a club and confirm it is one this server can see."""
        club = await Club.get_by_name(name)
        if not club:
            await interaction.followup.send(f"❌ Club '{name}' not found.", ephemeral=True)
            return None
        if not club.belongs_to_guild(interaction.guild_id):
            await interaction.followup.send(
                f"❌ Club '{name}' is not registered in this server.", ephemeral=True
            )
            return None
        return club

    @app_commands.command(
        name="transfer_request",
        description="Queue for a spot in another club",
    )
    @app_commands.describe(
        club="The club you want to transfer into",
        note="Anything the club leaders should know (optional)",
    )
    async def transfer_request(self, interaction: discord.Interaction, club: str,
                               note: Optional[str] = None):
        """Queue for a spot, using the trainer already linked to this account.

        Nothing about the trainer is typed here. A retyped ID is the one thing in
        this flow nobody can check — a leader sending an invite to a wrong digit
        gets silence, and no part of the queue can tell that apart from someone
        ignoring it. The link is already the answer to who you are, so it is the
        only accepted answer.
        """
        await interaction.response.defer(ephemeral=True)

        try:
            target = await self._resolve_club(interaction, club)
            if target is None:
                return

            if not target.is_active:
                await interaction.followup.send(
                    f"❌ **{target.club_name}** isn't accepting transfers — it's not an active club.",
                    ephemeral=True,
                )
                return

            link = await UserLink.get_by_discord_id(interaction.user.id)
            if link is None:
                await interaction.followup.send(
                    "❌ Link your trainer first: `/link_trainer`.\n"
                    "Transfer requests take your name and ID from that link, so you "
                    "never have to type them — and a leader can trust the ID they're "
                    "sending an invite to.",
                    ephemeral=True,
                )
                return

            member = await Member.get_by_id(link.member_id)
            if member is None:
                await interaction.followup.send(
                    "❌ Your trainer link points at a member record that no longer exists. "
                    "Run `/link_trainer` again to re-link, then retry.",
                    ephemeral=True,
                )
                return

            origin = await Club.get_by_id(member.club_id)

            # Only an *active* membership blocks it: someone whose old record sits
            # in the target club because they left it is rejoining, which is the
            # ordinary case rather than a mistake.
            if member.club_id == target.club_id and member.is_active:
                await interaction.followup.send(
                    f"❌ You're already in **{target.club_name}**.", ephemeral=True
                )
                return

            request = await TransferRequest.submit(
                to_club_id=target.club_id,
                discord_user_id=interaction.user.id,
                discord_name=interaction.user.display_name,
                trainer_name=member.trainer_name,
                trainer_id=member.trainer_id,
                from_club_id=member.club_id,
                from_club_name=origin.club_name if origin else None,
                note=note,
            )
            position = await request.position()

            embed = discord.Embed(
                title="✅ You're in the queue",
                description=(
                    f"Your request to transfer into **{target.club_name}** has been "
                    f"logged. A club leader will review it."
                ),
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow(),
            )
            trainer = member.trainer_name
            if member.trainer_id:
                trainer += f"\n`{member.trainer_id}`"
            embed.add_field(name="Trainer", value=trainer, inline=True)
            embed.add_field(name="Queue position", value=f"#{position}", inline=True)
            if request.from_club_name:
                embed.add_field(name="Coming from", value=request.from_club_name, inline=True)
            embed.add_field(
                name="What happens next",
                value=("You'll get a DM as soon as a leader approves or declines it.\n"
                       "Use `/my_transfers` to check on it or withdraw it."),
                inline=False,
            )
            embed.set_footer(text="Position is order of arrival — leaders can accept out of order.")

            await interaction.followup.send(embed=embed, ephemeral=True)
            await transfer_service.announce(self.bot, target, request)

        except Exception as e:
            logger.error(f"Error in transfer_request: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

    @app_commands.command(
        name="transfer_queue",
        description="See who's waiting to transfer into a club (leaders can approve here)",
    )
    @app_commands.describe(club="The club whose queue you want to see")
    async def transfer_queue(self, interaction: discord.Interaction, club: str):
        await interaction.response.defer(ephemeral=True)

        try:
            target = await self._resolve_club(interaction, club)
            if target is None:
                return

            queue = await TransferRequest.get_queue(target.club_id)
            can_review = await can_manage_club(interaction, target)
            embed = _queue_embed(target, queue, can_review=can_review)

            if not can_review:
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            view = QueuePanel(target, queue, interaction.user.id)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in transfer_queue: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

    @app_commands.command(
        name="my_transfers",
        description="Check or withdraw your own transfer requests",
    )
    async def my_transfers(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            requests = await TransferRequest.get_pending_for_user(interaction.user.id)

            if not requests:
                await interaction.followup.send(
                    "You have no transfer requests waiting. Use `/transfer_request` to queue for a club.",
                    ephemeral=True,
                )
                return

            club_names = {}
            lines = []
            for request in requests:
                club_obj = await Club.get_by_id(request.to_club_id)
                name = club_obj.club_name if club_obj else "Unknown club"
                club_names[request.to_club_id] = name
                position = await request.position()
                lines.append(f"**{name}** — position `#{position}` as {request.trainer_name}")

            embed = discord.Embed(
                title="📋 Your transfer requests",
                description="\n".join(lines),
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(text="You'll be DMed when a leader decides.")

            view = MyRequestsView(requests, club_names, interaction.user.id)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in my_transfers: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

    transfer_request.autocomplete('club')(club_autocomplete)
    transfer_queue.autocomplete('club')(club_autocomplete)
