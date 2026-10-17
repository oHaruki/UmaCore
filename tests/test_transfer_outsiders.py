"""Tests for transfer requests from people no club in the server tracks.

They have no member row and so nothing to ``/link_trainer`` to; they give a
trainer ID instead, and the name queued is the one uma.moe holds for it. The
failure modes worth pinning down are the ones that put the wrong name in front
of a leader, tell someone their correct ID is wrong because uma.moe was down,
or let a tracked member around the link that records where they are leaving.
"""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bot.commands import transfers as cog_module
from bot.commands.transfers import TransferCommands, _clean_trainer_id
from scrapers import umamoe_profile
from scrapers.umamoe_profile import ProfileUnavailable, TrainerIdentity

GUILD = 1426560692932317186
SCOFF_ID = "277366264188"


def club(name="Horsecore", **kw):
    base = dict(club_id=uuid4(), club_name=name, circle_id=None)
    base.update(kw)
    return SimpleNamespace(**base)


def member(club_obj, *, name="Scoff", trainer_id=SCOFF_ID, active=True):
    return SimpleNamespace(
        member_id=uuid4(), club_id=club_obj.club_id, trainer_name=name,
        trainer_id=trainer_id, is_active=active,
    )


class Interaction:
    """Records what the command tells the person, which is all that matters."""

    def __init__(self):
        self.guild_id = GUILD
        self.sent = []
        self.followup = SimpleNamespace(send=self._send)

    async def _send(self, content=None, **kwargs):
        self.sent.append(content)

    @property
    def said(self) -> str:
        return " ".join(m for m in self.sent if m)


def profile(name="Scoff", circle=("860280110", "Turfcore")):
    payload = {"trainer": {"account_id": SCOFF_ID, "name": name}}
    if circle is not None:
        payload["circle"] = {"circle_id": int(circle[0]), "name": circle[1]}
    else:
        payload["circle"] = None
    return payload


@pytest.fixture
def server(monkeypatch):
    """A server with two tracked clubs and nobody in them, and a uma.moe that
    knows Scoff in an untracked club. Tests rearrange what they need."""
    state = SimpleNamespace(
        clubs=[club("Horsecore"), club("Turfcore Two")],
        members=[],
        lookup=lambda account_id: TrainerIdentity(
            account_id, "Scoff", "860280110", "Turfcore"),
        lookups=[],
    )

    async def get_all_for_guild(guild_id):
        assert guild_id == GUILD
        return state.clubs

    async def get_by_trainer_id(club_id, trainer_id):
        return next((m for m in state.members
                     if m.club_id == club_id and m.trainer_id == trainer_id), None)

    async def lookup(account_id):
        state.lookups.append(account_id)
        result = state.lookup(account_id)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(cog_module.Club, "get_all_for_guild", get_all_for_guild)
    monkeypatch.setattr(cog_module.Member, "get_by_trainer_id", get_by_trainer_id)
    monkeypatch.setattr(cog_module, "lookup_trainer", lookup)
    return state


def outside(target, typed_id):
    interaction = Interaction()
    cog = TransferCommands(bot=None)
    result = asyncio.run(cog._outside_applicant(interaction, target, typed_id))
    return result, interaction


class TestCleanTrainerId:
    def test_accepts_the_id_as_the_game_groups_it(self):
        assert _clean_trainer_id("277 366 264 188") == SCOFF_ID

    def test_accepts_it_plain(self):
        assert _clean_trainer_id(SCOFF_ID) == SCOFF_ID

    @pytest.mark.parametrize("raw", ["Scoff", "", "   ", "1234", "2773662641881234", "27736626418x"])
    def test_refuses_what_cannot_be_an_id(self, raw):
        assert _clean_trainer_id(raw) is None


class TestTrainerIdentity:
    def test_reads_name_and_circle(self):
        identity = TrainerIdentity.from_profile(SCOFF_ID, profile())
        assert identity == TrainerIdentity(SCOFF_ID, "Scoff", "860280110", "Turfcore")

    def test_someone_in_no_circle_has_no_origin(self):
        identity = TrainerIdentity.from_profile(SCOFF_ID, profile(circle=None))
        assert identity.circle_id is None and identity.circle_name is None

    def test_a_profile_without_a_name_is_no_identity(self):
        assert TrainerIdentity.from_profile(SCOFF_ID, profile(name="  ")) is None


class TestLookupTrainer:
    """The two kinds of "no" must stay apart, or an outage reads as a typo."""

    def patch_request(self, monkeypatch, outcome):
        async def request(account_id):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        monkeypatch.setattr(umamoe_profile, "_request_profile", request)

    def test_unknown_id_is_none(self, monkeypatch):
        self.patch_request(monkeypatch, None)
        assert asyncio.run(umamoe_profile.lookup_trainer(SCOFF_ID)) is None

    def test_outage_raises(self, monkeypatch):
        self.patch_request(monkeypatch, ProfileUnavailable("HTTP 503"))
        with pytest.raises(ProfileUnavailable):
            asyncio.run(umamoe_profile.lookup_trainer(SCOFF_ID))

    def test_nameless_profile_is_treated_as_unanswered(self, monkeypatch):
        """Telling someone uma.moe has no such trainer when it has one would
        send them hunting for a typo that is not there."""
        self.patch_request(monkeypatch, profile(name=""))
        with pytest.raises(ProfileUnavailable):
            asyncio.run(umamoe_profile.lookup_trainer(SCOFF_ID))

    def test_trainer_card_still_degrades_to_none(self, monkeypatch):
        """The card's enrichment is best-effort and must never see the raise."""
        self.patch_request(monkeypatch, ProfileUnavailable("no key"))
        assert asyncio.run(umamoe_profile.fetch_trainer_profile(SCOFF_ID)) is None


class TestOutsideApplicant:
    def test_queues_the_name_uma_moe_holds_not_a_typed_one(self, server):
        result, interaction = outside(server.clubs[0], "277 366 264 188")
        assert result == dict(
            trainer_name="Scoff", trainer_id=SCOFF_ID,
            from_club_id=None, from_club_name="Turfcore",
        )
        assert interaction.sent == []

    def test_refuses_something_that_is_not_an_id_without_asking_uma_moe(self, server):
        result, interaction = outside(server.clubs[0], "Scoff")
        assert result is None
        assert "isn't a trainer ID" in interaction.said
        assert server.lookups == []

    def test_unknown_id_is_refused_as_wrong(self, server):
        server.lookup = lambda account_id: None
        result, interaction = outside(server.clubs[0], SCOFF_ID)
        assert result is None
        assert "no trainer with ID" in interaction.said

    def test_uma_moe_down_asks_to_retry_rather_than_calling_the_id_wrong(self, server):
        server.lookup = lambda account_id: ProfileUnavailable("HTTP 503")
        result, interaction = outside(server.clubs[0], SCOFF_ID)
        assert result is None
        assert "Try again" in interaction.said
        assert "no trainer" not in interaction.said

    def test_a_tracked_member_is_sent_to_link_instead(self, server):
        """Their link is what records the club they are leaving, and it is the
        one identity a member already has."""
        target, theirs = server.clubs
        server.members = [member(theirs)]
        result, interaction = outside(target, SCOFF_ID)
        assert result is None
        assert "/link_trainer" in interaction.said
        assert "Turfcore Two" in interaction.said
        assert server.lookups == []

    def test_a_current_member_of_the_target_is_told_so(self, server):
        target = server.clubs[0]
        server.members = [member(target)]
        result, interaction = outside(target, SCOFF_ID)
        assert result is None
        assert "already in **Horsecore**" in interaction.said

    def test_someone_who_left_a_tracked_club_can_come_back(self, server):
        target = server.clubs[0]
        server.members = [member(target, active=False)]
        result, _ = outside(target, SCOFF_ID)
        assert result is not None and result["trainer_name"] == "Scoff"


class TestLinkedApplicant:
    @pytest.fixture
    def linked(self, monkeypatch):
        home, target = club("Turfcore"), club("Horsecore")
        me = member(home)

        async def get_member(member_id):
            return me

        async def get_club(club_id):
            return home if club_id == home.club_id else target

        monkeypatch.setattr(cog_module.Member, "get_by_id", get_member)
        monkeypatch.setattr(cog_module.Club, "get_by_id", get_club)
        return SimpleNamespace(home=home, target=target, member=me,
                               link=SimpleNamespace(member_id=me.member_id))

    def run(self, linked, typed_id):
        interaction = Interaction()
        cog = TransferCommands(bot=None)
        result = asyncio.run(cog._linked_applicant(
            interaction, linked.target, linked.link, typed_id))
        return result, interaction

    def test_uses_the_link_when_no_id_is_typed(self, linked):
        result, _ = self.run(linked, None)
        assert result["trainer_id"] == SCOFF_ID
        assert result["from_club_id"] == linked.home.club_id
        assert result["from_club_name"] == "Turfcore"

    def test_typing_your_own_linked_id_is_harmless(self, linked):
        result, _ = self.run(linked, "277 366 264 188")
        assert result is not None

    def test_a_different_id_is_refused_rather_than_swapped_for_the_link(self, linked):
        """Someone asking for an alt must not have their main queued instead."""
        result, interaction = self.run(linked, "123456789012")
        assert result is None
        assert "linked to **Scoff**" in interaction.said


class TestRequestWithoutLinkOrId:
    def test_explains_both_ways_in(self, monkeypatch):
        target = SimpleNamespace(club_id=uuid4(), club_name="Horsecore", is_active=True)

        async def resolve(self, interaction, name):
            return target

        async def no_link(discord_user_id):
            return None

        monkeypatch.setattr(TransferCommands, "_resolve_club", resolve)
        monkeypatch.setattr(cog_module.UserLink, "get_by_discord_id", no_link)

        interaction = Interaction()
        interaction.user = SimpleNamespace(id=4242, display_name="Scoff")

        async def defer(**kwargs):
            pass
        interaction.response = SimpleNamespace(defer=defer)

        cog = TransferCommands(bot=None)
        asyncio.run(TransferCommands.transfer_request.callback(
            cog, interaction, "Horsecore"))

        assert "/link_trainer" in interaction.said
        assert "trainer_id:" in interaction.said
