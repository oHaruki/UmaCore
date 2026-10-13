"""
Slash commands for skill maps.

  /skill   one skill on a course: whether it pays off, where it fires, by running style
  /track   every acceleration / speed / recovery skill on a course

Both default to the current (or next) Champions Meeting, which comes from
uma.moe's timeline and is refreshed in the background.
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Awaitable, Callable, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .analysis import KIND_PASSIVE, analyze_skill, analyze_track, race_model, strategy_hint
from .gamedata import STRATEGIES, CourseChoice, GameData, Skill, SupportCard, load
from .render import render_skill, render_track
from .schedule import CmSchedule
from .sim import Build
from .text import card_footer, condition_lines, effect_line, kind_label, stat_fields, style_line, track_line, verdict

logger = logging.getLogger(__name__)

DEFAULT_STRATEGY = 2
KINDS = {"accel": "Acceleration", "speed": "Speed", "recovery": "Recovery"}
STRATEGY_CHOICES = [app_commands.Choice(name=n, value=k) for k, n in STRATEGIES.items()]
KIND_CHOICES = [app_commands.Choice(name=n, value=k) for k, n in KINDS.items()]
NO_CM = "No Champions Meeting is scheduled right now, so pick a course."


def visualizer_url(course_id: int, skill_id: int) -> str:
    return f"https://umalator.app/umalator-global/skill-visualizer/v2/#cid={course_id},sid={skill_id}"


def _style_options(current: int) -> list:
    return [discord.SelectOption(label=name, value=str(k), default=k == current) for k, name in STRATEGIES.items()]


def card_embed(card: SupportCard, data: GameData, highlight: int) -> discord.Embed:
    lines = []
    for sid in card.hints:
        skill = data.skills.get(sid)
        if skill:
            lines.append(f"- **{skill.name}**" if sid == highlight else f"- {skill.name}")
    embed = discord.Embed(title=f"[{card.title}] {card.chara}", colour=0x76BE38,
                          description=f"{card.rarity_label} {card.kind}\n\n**Skill hints**\n" + "\n".join(lines))
    embed.set_footer(text="Hint skills from the game data. Skills from card events aren't listed.")
    return embed


class SkillCard(discord.ui.View):
    """Running style, support cards with the hint, and the weaker/stronger version."""

    def __init__(self, cog: "SkillMapCommands", invoker_id: int, skill: Skill, choice: CourseChoice,
                 strategy: int, *, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.invoker_id = invoker_id
        self.skill = skill
        self.choice = choice
        self.strategy = strategy
        self.message: Optional[discord.Message] = None
        self._cards_id = ""
        self._build()

    def _build(self) -> None:
        self.clear_items()
        data = load()
        styles = discord.ui.Select(placeholder="Running style", options=_style_options(self.strategy), row=0)
        styles.callback = self._on_style
        self.add_item(styles)

        lower, higher = data.related(self.skill)
        # Gold skills aren't hinted directly; you get the white one's hint and upgrade it.
        cards = data.cards_for(self.skill.id) or (data.cards_for(lower.id) if lower else [])
        if cards:
            picker = discord.ui.Select(
                placeholder="Support cards with this hint", row=1,
                options=[discord.SelectOption(label=f"[{c.rarity_label}] {c.chara}"[:100], value=str(c.id),
                                              description=f"{c.title} · {c.kind}"[:100]) for c in cards[:25]])
            picker.callback = self._on_card
            self._cards_id = picker.custom_id
            self.add_item(picker)

        for other, arrow in ((lower, "↓"), (higher, "↑")):
            if other:
                button = discord.ui.Button(label=f"{arrow} {other.name}"[:80], row=2,
                                           style=discord.ButtonStyle.secondary)
                button.callback = self._switch_to(other)
                self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (interaction.data or {}).get("custom_id") == self._cards_id:
            return True     # a read-only lookup, fine for anyone
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Run /skill yourself to get your own copy you can change.", ephemeral=True)
            return False
        return True

    async def _redraw(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        embed, file = await self.cog.skill_card(self.skill, self.choice, self.strategy)
        self._build()
        await interaction.edit_original_response(embed=embed, attachments=[file] if file else [], view=self)

    async def _on_style(self, interaction: discord.Interaction) -> None:
        self.strategy = int(interaction.data["values"][0])
        await self._redraw(interaction)

    def _switch_to(self, other: Skill) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            self.skill = other
            self.strategy = strategy_hint(other) or self.strategy
            await self._redraw(interaction)
        return callback

    async def _on_card(self, interaction: discord.Interaction) -> None:
        data = load()
        card = data.cards.get(int(interaction.data["values"][0]))
        if card is None:
            await interaction.response.send_message("That card isn't in the game data.", ephemeral=True)
            return
        await interaction.response.send_message(embed=card_embed(card, data, self.skill.id), ephemeral=True)

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


class TrackView(discord.ui.View):
    """Running style and skill type dropdowns that redraw the /track image in place."""

    def __init__(self, invoker_id: int, render: Callable[[int, str], Awaitable[tuple]], strategy: int, kind: str,
                 *, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.invoker_id = invoker_id
        self.render = render
        self.strategy = strategy
        self.kind = kind
        self.message: Optional[discord.Message] = None
        self._sync()

    def _sync(self) -> None:
        self.strategy_select.options = _style_options(self.strategy)
        self.kind_select.options = [
            discord.SelectOption(label=f"{name} skills", value=k, default=k == self.kind) for k, name in KINDS.items()
        ]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Run /track yourself to get your own copy you can change.", ephemeral=True)
            return False
        return True

    async def _redraw(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self._sync()
        content, file = await self.render(self.strategy, self.kind)
        await interaction.edit_original_response(content=content, attachments=[file], view=self)

    @discord.ui.select(placeholder="Running style", min_values=1, max_values=1, row=0)
    async def strategy_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.strategy = int(select.values[0])
        await self._redraw(interaction)

    @discord.ui.select(placeholder="Skill type", min_values=1, max_values=1, row=1)
    async def kind_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.kind = select.values[0]
        await self._redraw(interaction)

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


class SkillMapCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        feed = getattr(bot, "_event_feed", None)
        self._own_client = None
        if feed is not None:
            fetch = feed.umamoe.timeline
        else:
            from events.umamoe_client import UmaMoeEventsClient
            self._own_client = UmaMoeEventsClient()
            fetch = self._own_client.timeline
        self.schedule = CmSchedule(fetch, load())

    async def cog_load(self) -> None:
        self.refresh_schedule.start()

    async def cog_unload(self) -> None:
        self.refresh_schedule.cancel()
        if self._own_client:
            await self._own_client.close()

    @tasks.loop(minutes=30)
    async def refresh_schedule(self) -> None:
        try:
            await self.schedule.refresh()
        except Exception as e:
            logger.warning(f"Skill map CM schedule refresh failed, keeping the last one: {e}")

    @staticmethod
    def _data() -> GameData:
        return load()

    def _cms(self) -> list:
        return self.schedule.choices()

    # ── autocomplete ───────────────────────────────────────────────────────

    async def _skill_choices(self, interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=f"{s.name} · {s.rarity_label}"[:100], value=str(s.id))
                for s in self._data().search_skills(current, 25)]

    async def _course_choices(self, interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=c.label[:100], value=c.key)
                for c in self._data().course_choices(current, 25, cms=self._cms())]

    # ── /skill ─────────────────────────────────────────────────────────────

    async def skill_card(self, skill: Skill, choice: CourseChoice, strategy: int) -> tuple:
        def work():
            by_style = {k: analyze_skill(skill, choice, Build(strategy=k)) for k in STRATEGIES}
            result, build = by_style[strategy], Build(strategy=strategy)
            # Passive stat skills are on for the whole race; a map adds nothing.
            png = None if result.kind == KIND_PASSIVE else render_skill(
                result, choice, build, race_model(choice, build))
            return result, by_style, png

        result, by_style, png = await asyncio.to_thread(work)
        head, fact, colour = verdict(result)
        embed = discord.Embed(title=skill.name, url=visualizer_url(choice.course.id, skill.id),
                              description=f"**{head}.** {fact}", colour=colour)
        embed.set_author(name=kind_label(result), icon_url=skill.icon_url)
        for name, value in stat_fields(result):
            embed.add_field(name=name, value=value, inline=True)
        embed.add_field(name="When", value="\n".join(condition_lines(skill, result.alt_index))[:1024], inline=False)
        embed.add_field(name="Effect", value=effect_line(skill.alts[result.alt_index], choice.course.distance)[:1024],
                        inline=False)
        styles = style_line(by_style, strategy)
        if styles:
            embed.add_field(name="By running style", value=styles, inline=False)
        embed.set_footer(text=card_footer(choice, strategy))
        file = None
        if png:
            embed.set_image(url="attachment://skillmap.png")
            file = discord.File(io.BytesIO(png), filename="skillmap.png")
        return embed, file

    @app_commands.command(name="skill", description="Whether a skill pays off on a course, where it fires and what it wins")
    @app_commands.describe(
        name="Skill name",
        course="Course, race or Champions Meeting (default: the current Champions Meeting)",
        strategy="Running style (default: the one the skill is for, else Pace Chaser)",
    )
    @app_commands.choices(strategy=STRATEGY_CHOICES)
    async def skill(self, interaction: discord.Interaction, name: str, course: Optional[str] = None,
                    strategy: Optional[app_commands.Choice[int]] = None):
        await interaction.response.defer()
        data = self._data()
        found = data.find_skill(name)
        if found is None:
            await interaction.followup.send(f"No skill matches **{discord.utils.escape_markdown(name)}**.")
            return
        choice = data.resolve_course(course, cms=self._cms())
        if choice is None:
            await interaction.followup.send("I couldn't find that course. Pick one from the list." if course else NO_CM)
            return
        picked = strategy.value if strategy else (strategy_hint(found) or DEFAULT_STRATEGY)
        try:
            embed, file = await self.skill_card(found, choice, picked)
        except Exception as e:
            logger.error(f"/skill {found.name} on {choice.key} failed: {e}", exc_info=True)
            await interaction.followup.send("Something went wrong drawing that skill map.")
            return
        view = SkillCard(self, interaction.user.id, found, choice, picked)
        kwargs = {"embed": embed, "view": view, "wait": True}
        if file:
            kwargs["file"] = file
        view.message = await interaction.followup.send(**kwargs)
        logger.info(f"/skill {found.name} on {choice.key} ({STRATEGIES[picked]}) by {interaction.user}")

    skill.autocomplete("name")(_skill_choices)
    skill.autocomplete("course")(_course_choices)

    # ── /track ─────────────────────────────────────────────────────────────

    @app_commands.command(name="track", description="Every skill of a type on a course, and where acceleration actually pays")
    @app_commands.describe(
        course="Course, race or Champions Meeting (default: the current Champions Meeting)",
        kind="Which skills to show (default: acceleration)",
        strategy="Running style (default: Pace Chaser)",
    )
    @app_commands.rename(kind="type")
    @app_commands.choices(kind=KIND_CHOICES, strategy=STRATEGY_CHOICES)
    async def track(self, interaction: discord.Interaction, course: Optional[str] = None,
                    kind: Optional[app_commands.Choice[str]] = None,
                    strategy: Optional[app_commands.Choice[int]] = None):
        await interaction.response.defer()
        data = self._data()
        choice = data.resolve_course(course, cms=self._cms())
        if choice is None:
            await interaction.followup.send("I couldn't find that course. Pick one from the list." if course else NO_CM)
            return
        picked_kind = kind.value if kind else "accel"
        picked = strategy.value if strategy else DEFAULT_STRATEGY

        async def render(strat: int, which: str):
            build = Build(strategy=strat)

            def work():
                analysis = analyze_track(choice, build, which, data)
                return analysis, render_track(analysis, which)

            analysis, png = await asyncio.to_thread(work)
            return track_line(analysis, which), discord.File(io.BytesIO(png), filename="trackmap.png")

        try:
            content, file = await render(picked, picked_kind)
        except Exception as e:
            logger.error(f"/track {choice.key} {picked_kind} failed: {e}", exc_info=True)
            await interaction.followup.send("Something went wrong drawing that track.")
            return
        view = TrackView(interaction.user.id, render, picked, picked_kind)
        view.message = await interaction.followup.send(content=content, file=file, view=view, wait=True)
        logger.info(f"/track {choice.key} {picked_kind} ({STRATEGIES[picked]}) by {interaction.user}")

    track.autocomplete("course")(_course_choices)


async def setup(bot: commands.Bot):
    await bot.add_cog(SkillMapCommands(bot))
