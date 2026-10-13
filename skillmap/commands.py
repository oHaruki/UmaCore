"""
Slash commands for skill maps.

  /skill   where one skill fires on a course and what it gains there
  /track   every acceleration / speed / recovery skill on a course

Both default to the current Champions Meeting course and post a strategy
dropdown so the invoker can flip between running styles without retyping.
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Awaitable, Callable, Optional

import discord
from discord import app_commands
from discord.ext import commands

from .analysis import KIND_PASSIVE, analyze_skill, analyze_track, race_model, strategy_hint
from .gamedata import STRATEGIES, GameData, load
from .render import render_skill, render_track
from .sim import Build
from .text import skill_line, track_line

logger = logging.getLogger(__name__)

DEFAULT_STRATEGY = 2
KINDS = {"accel": "Acceleration", "speed": "Speed", "recovery": "Recovery"}
STRATEGY_CHOICES = [app_commands.Choice(name=n, value=k) for k, n in STRATEGIES.items()]
KIND_CHOICES = [app_commands.Choice(name=n, value=k) for k, n in KINDS.items()]

Renderer = Callable[[int, str], Awaitable[tuple]]


class SkillMapView(discord.ui.View):
    """Strategy (and for /track, skill type) dropdowns that redraw the map in place."""

    def __init__(self, invoker_id: int, render: Renderer, strategy: int, kind: Optional[str] = None,
                 *, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.invoker_id = invoker_id
        self.render = render
        self.strategy = strategy
        self.kind = kind
        self.message: Optional[discord.Message] = None
        if kind is None:
            self.remove_item(self.kind_select)
        self._sync()

    def _sync(self) -> None:
        self.strategy_select.options = [
            discord.SelectOption(label=name, value=str(k), default=k == self.strategy)
            for k, name in STRATEGIES.items()
        ]
        self.kind_select.options = [
            discord.SelectOption(label=f"{name} skills", value=k, default=k == self.kind)
            for k, name in KINDS.items()
        ]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Run the command yourself to get your own copy you can change.", ephemeral=True)
            return False
        return True

    async def _redraw(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self._sync()
        content, file = await self.render(self.strategy, self.kind or "")
        await interaction.edit_original_response(content=content, attachments=[file] if file else [], view=self)

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

    @staticmethod
    def _data() -> GameData:
        return load()

    # ── autocomplete ───────────────────────────────────────────────────────

    async def _skill_choices(self, interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=f"{s.name} · {s.rarity_label}"[:100], value=str(s.id))
                for s in self._data().search_skills(current, 25)]

    async def _course_choices(self, interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=c.label[:100], value=c.key)
                for c in self._data().course_choices(current, 25)]

    # ── /skill ─────────────────────────────────────────────────────────────

    @app_commands.command(name="skill", description="Where a skill fires on a course, and whether it pays off there")
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
        choice = data.resolve_course(course)
        if choice is None:
            await interaction.followup.send("I couldn't find that course. Pick one from the list.")
            return
        picked = strategy.value if strategy else (strategy_hint(found) or DEFAULT_STRATEGY)

        async def render(strat: int, _kind: str):
            build = Build(strategy=strat)

            def work():
                result = analyze_skill(found, choice, build)
                # Passive stat skills are on for the whole race; a map adds nothing.
                png = None if result.kind == KIND_PASSIVE else render_skill(
                    result, choice, build, race_model(choice, build))
                return result, png

            result, png = await asyncio.to_thread(work)
            file = discord.File(io.BytesIO(png), filename="skillmap.png") if png else None
            return skill_line(result, choice), file

        try:
            content, file = await render(picked, "")
        except Exception as e:
            logger.error(f"/skill {found.name} on {choice.key} failed: {e}", exc_info=True)
            await interaction.followup.send("Something went wrong drawing that skill map.")
            return
        view = SkillMapView(interaction.user.id, render, picked)
        kwargs = {"content": content, "view": view, "wait": True}
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
        choice = data.resolve_course(course)
        if choice is None:
            await interaction.followup.send("I couldn't find that course. Pick one from the list.")
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
        view = SkillMapView(interaction.user.id, render, picked, picked_kind)
        view.message = await interaction.followup.send(content=content, file=file, view=view, wait=True)
        logger.info(f"/track {choice.key} {picked_kind} ({STRATEGIES[picked]}) by {interaction.user}")

    track.autocomplete("course")(_course_choices)


async def setup(bot: commands.Bot):
    await bot.add_cog(SkillMapCommands(bot))
