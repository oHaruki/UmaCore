"""Tests for the transfer queue's review panel.

The database side (the partial-index upsert, the pending-only decide guard) needs
a real Postgres and is exercised by the write-path suite. What is covered here is
the panel a club leader actually clicks, where the failure mode is letting the
wrong person into a club: an approve button that is live before anyone has been
picked would act on whatever the select happened to default to.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bot.commands.transfers import QueuePanel, _guide_embed, _queue_embed

UTC = timezone.utc


def club(**kw):
    base = dict(club_id=uuid4(), club_name="Horsecore")
    base.update(kw)
    return SimpleNamespace(**base)


def request(trainer_name="Scoff", origin="Turfcore", **kw):
    base = dict(
        request_id=uuid4(),
        trainer_name=trainer_name,
        trainer_id="277366264188",
        origin=origin,
        discord_user_id=4242,
        created_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestQueueEmbed:
    def test_numbers_the_queue_in_arrival_order(self):
        queue = [request(trainer_name="Scoff"), request(trainer_name="Auriga")]
        body = _queue_embed(club(), queue, can_review=True).description
        assert body.index("`#1` **Scoff**") < body.index("`#2` **Auriga**")

    def test_says_the_numbering_is_not_binding(self):
        """A numbered list reads as a rule unless it is told not to. Leaders are
        meant to accept out of order, which is the whole reason the queue is a
        list rather than a first-in-first-out."""
        embed = _queue_embed(club(), [request()], can_review=True)
        assert "approve anyone" in embed.footer.text

    def test_empty_queue_says_so_without_a_table(self):
        embed = _queue_embed(club(), [], can_review=True)
        assert "Nobody is waiting" in embed.description

    def test_long_queue_is_truncated_to_what_a_select_can_hold(self):
        """Discord refuses a select with more than 25 options, so the embed must
        not promise rows the picker cannot offer."""
        queue = [request(trainer_name=f"T{i}") for i in range(30)]
        embed = _queue_embed(club(), queue, can_review=True)
        assert "`#26`" not in embed.description
        assert "30 waiting" in embed.footer.text


class TestGuideEmbed:
    """The pinned guide is the only instructions most members will ever read."""

    def test_puts_linking_before_requesting(self):
        """`/transfer_request` is refused until a trainer is linked, so a guide
        that introduced it first would teach people the failing order."""
        fields = _guide_embed([club()]).fields
        body = " ".join(f"{f.name} {f.value}" for f in fields)
        assert body.index("/link_trainer") < body.index("/transfer_request")

    def test_never_tells_anyone_to_type_a_trainer_id(self):
        body = " ".join(f"{f.name} {f.value}" for f in _guide_embed([club()]).fields)
        assert "trainer_id:" not in body

    def test_lists_the_clubs_that_can_be_requested(self):
        clubs = [club(club_name="Horsecore"), club(club_name="Turfcore")]
        body = " ".join(f.value for f in _guide_embed(clubs).fields)
        assert "Horsecore" in body and "Turfcore" in body

    def test_survives_a_guild_with_no_clubs(self):
        """Posted during setup, before any club exists — must not render an
        empty field, which Discord rejects outright."""
        embed = _guide_embed([])
        assert all(f.value for f in embed.fields)
        assert "/transfer_request" in " ".join(f.value for f in embed.fields)

    def test_says_position_is_not_binding(self):
        assert "out of order" in _guide_embed([club()]).footer.text


class TestQueuePanel:
    def test_decisions_are_disabled_until_someone_is_picked(self):
        panel = QueuePanel(club(), [request(), request()], invoker_id=1)
        assert panel.approve_button.disabled
        assert panel.reject_button.disabled

    def test_picking_a_request_enables_both_decisions(self):
        queue = [request(), request()]
        panel = QueuePanel(club(), queue, invoker_id=1)
        panel.selected = queue[1].request_id
        panel._rebuild()
        assert not panel.approve_button.disabled
        assert not panel.reject_button.disabled

    def test_an_empty_queue_offers_nothing_to_click(self):
        panel = QueuePanel(club(), [], invoker_id=1)
        assert panel.picker.disabled
        assert panel.approve_button.disabled
        assert panel.reject_button.disabled

    def test_selection_survives_a_rebuild_as_the_marked_option(self):
        queue = [request(trainer_name="Scoff"), request(trainer_name="Auriga")]
        panel = QueuePanel(club(), queue, invoker_id=1)
        panel.selected = queue[1].request_id
        panel._rebuild()
        marked = [o for o in panel.picker.options if o.default]
        assert [o.value for o in marked] == [str(queue[1].request_id)]

    def test_find_returns_none_once_a_request_leaves_the_queue(self):
        """Someone else may decide a request between the panel opening and a
        button being pressed; the panel must notice rather than act on a stale
        row."""
        gone = request()
        panel = QueuePanel(club(), [request()], invoker_id=1)
        assert panel._find(gone.request_id) is None

    @pytest.mark.parametrize("count", [1, 25, 40])
    def test_picker_never_exceeds_discords_option_cap(self, count):
        queue = [request(trainer_name=f"T{i}") for i in range(count)]
        panel = QueuePanel(club(), queue, invoker_id=1)
        assert len(panel.picker.options) <= 25
