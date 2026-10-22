"""Tests for per-club embed styles: validation, the report and live board renders,
and the two ways of counting days behind."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from scrapers.umamoe_api_scraper import LiveSnapshot, MemberGain
from services import embed_preview, embed_style, live_board
from services.quota_calculator import BehindCount, quota_days
from services.report_generator import ReportGenerator

REPORT, LIVE = embed_style.REPORT, embed_style.LIVE


def club(**kw):
    base = dict(club_id=uuid4(), club_name="Cute", quota_period='daily',
                report_daily_gain=False, embed_style={}, days_behind_mode='streak',
                bombs_enabled=True, period_anchor_date=None, timezone='UTC')
    base.update(kw)
    return SimpleNamespace(**base)


def member(name, cumulative, surplus, streak=0, day_fans=1_000_000, short=None):
    history = SimpleNamespace(cumulative_fans=cumulative, expected_fans=cumulative - surplus,
                              deficit_surplus=surplus, days_behind=streak)
    return {'member': SimpleNamespace(trainer_name=name), 'history': history,
            'day_fans': day_fans, 'streak': streak,
            'quota_days': short if short is not None else quota_days(surplus, 8_000_000)}


def summary():
    on = [member("Aegis", 180_000_000, 12_000_000), member("Ennuyee", 170_000_000, 2_000_000)]
    behind = [member("solar", 120_000_000, -47_000_000, streak=14, day_fans=3_400_000),
              member("iza", 150_000_000, -13_200_000, streak=3, day_fans=3_300_000)]
    return {'on_track': on, 'behind': behind, 'total_members': 4, 'period_info': None}


def report(style=None, mode='streak', bombs=()):
    return ReportGenerator().create_daily_report(
        "Cute", 8_000_000, summary(), list(bombs), date(2026, 10, 20),
        style=style, days_mode=mode)


def report_style(**changes):
    return embed_style.resolve(REPORT, club(), changes)


# --------------------------------------------------------------------------- #
# validation and resolution
# --------------------------------------------------------------------------- #

class TestValidate:
    def test_unknown_token_is_named(self):
        _, errors = embed_style.validate(REPORT, {'line_behind': "{name} {deficitt}"})
        assert errors == {'line_behind': "Unknown: {deficitt}"}

    def test_token_from_another_kind_is_unknown(self):
        _, errors = embed_style.validate(LIVE, {'line_raced': "{name} {streak}"})
        assert 'line_raced' in errors

    def test_line_cannot_be_empty_but_a_title_can(self):
        clean, errors = embed_style.validate(REPORT, {'line_behind': "  ", 'title_behind': ""})
        assert errors == {'line_behind': "Can't be empty"}
        assert clean == {'title_behind': ""}

    def test_too_long(self):
        _, errors = embed_style.validate(REPORT, {'line_on_track': "x" * 121})
        assert errors['line_on_track'].startswith("Longer than")

    def test_colour_must_be_hex(self):
        clean, errors = embed_style.validate(REPORT, {'color_behind': "orange",
                                                      'color_on_track': "#00FF00"})
        assert 'color_behind' in errors
        assert clean == {'color_on_track': "#00ff00"}

    def test_choice_does_not_accept_true_for_one(self):
        _, errors = embed_style.validate(REPORT, {'decimals': True})
        assert 'decimals' in errors

    def test_unknown_keys_are_dropped_quietly(self):
        assert embed_style.validate(REPORT, {'nope': 1}) == ({}, {})


class TestResolve:
    def test_stored_changes_apply(self):
        c = club(embed_style={'report': {'sort_behind': 'deficit_asc'}})
        assert embed_style.resolve(REPORT, c)['sort_behind'] == 'deficit_asc'

    def test_broken_stored_value_falls_back(self):
        c = club(embed_style={'report': {'sort_behind': 'sideways', 'decimals': 2}})
        style = embed_style.resolve(REPORT, c)
        assert style['sort_behind'] == 'deficit'
        assert style['decimals'] == 2

    def test_daily_gain_switch_picks_the_default_lines(self):
        style = embed_style.resolve(REPORT, club(report_daily_gain=True))
        assert style['line_on_track'].startswith("**{name}**: {gain}")

    def test_changes_keep_only_what_differs(self):
        style = embed_style.defaults(REPORT)
        style['decimals'] = 2
        assert embed_style.changes(REPORT, style) == {'decimals': 2}

    def test_daily_gain_lines_are_stored_when_saved(self):
        """Saving turns the old switch off, so its lines have to be kept explicitly."""
        c = club(report_daily_gain=True)
        stored, errors = embed_preview.to_store(c, {'report': embed_style.resolve(REPORT, c)})
        assert not errors
        assert stored['report']['line_on_track'] == "**{name}**: {gain} ({total_full} total)"
        assert 'live' not in stored

    def test_every_preset_validates(self):
        for kind in embed_style.KINDS:
            for period in ('daily', 'weekly', 'biweekly'):
                for preset in embed_style.presets(kind, period):
                    assert embed_style.validate(kind, preset['values'])[1] == {}, preset


class TestRender:
    def test_width_pads(self):
        assert embed_style.render("`{pos:>2}`", {'pos': "3"}) == "` 3`"

    def test_unknown_token_stays_visible(self):
        assert embed_style.render("{name} {oops}", {'name': "iza"}) == "iza {oops}"

    def test_decimals(self):
        assert embed_style.compact(47_031_234, 0) == "47M"
        assert embed_style.compact(47_031_234, 2) == "47.03M"
        assert embed_style.compact(3_400, 2) == "3.4K"
        assert embed_style.signed(0) == "+0"


# --------------------------------------------------------------------------- #
# days behind
# --------------------------------------------------------------------------- #

class TestQuotaDays:
    def test_matches_a_clubs_own_count(self):
        """45.5M short at 8M a day is about 6 days, rounding up."""
        assert quota_days(-45_500_000, 8_000_000) == 6
        assert quota_days(-4_100_000, 8_000_000) == 1
        assert quota_days(-16_700_000, 8_000_000) == 3

    def test_on_track_or_no_quota_is_zero(self):
        assert quota_days(5_000_000, 8_000_000) == 0
        assert quota_days(-5_000_000, 0) == 0

    def test_behind_count_reads_the_mode(self):
        history = SimpleNamespace(deficit_surplus=-47_000_000, days_behind=14)
        assert BehindCount().of(history) == 14
        assert BehindCount('quota', 8_000_000).of(history) == 6

    def test_wording(self):
        assert BehindCount().describe(1) == "1 consecutive day"
        assert BehindCount('quota', 1).describe(6) == "6 days of quota"
        assert "3+ days of quota" in BehindCount('quota', 1).trigger_reason(3)


# --------------------------------------------------------------------------- #
# daily report
# --------------------------------------------------------------------------- #

def lines(embed):
    return embed.description.split("\n")


class TestReport:
    def test_days_behind_follows_the_mode(self):
        assert lines(report()[2])[0] == "**solar**: -47.0M (14 days behind)"
        assert lines(report(mode='quota')[2])[0] == "**solar**: -47.0M (6 days behind)"

    def test_custom_line(self):
        style = report_style(line_behind="{name}: {gain} ({balance}, {quota_days} behind)")
        assert lines(report(style)[2])[0] == "solar: +3.4M (-47.0M, 6 days behind)"

    def test_least_behind_first(self):
        style = report_style(sort_behind='deficit_asc')
        assert [l.split("**")[1] for l in lines(report(style)[2])] == ["iza", "solar"]

    def test_name_order_on_track(self):
        style = report_style(sort_on_track='name', line_on_track="{pos}. {name}")
        assert lines(report(style)[1]) == ["1. Aegis", "2. Ennuyee"]

    def test_hidden_on_track_list(self):
        embeds = report(report_style(show_on_track=False))
        assert [e.title for e in embeds] == ["📊 Daily Quota Report - Cute", "⚠️ Behind Quota"]

    def test_titles_colours_and_count(self):
        style = report_style(title_behind="Behind ({count})", color_behind="#123456")
        behind = report(style)[2]
        assert behind.title == "Behind (2)"
        assert behind.colour.value == 0x123456

    def test_empty_title_means_none(self):
        assert report(report_style(title_behind=""))[2].title is None

    def test_summary_and_footer_can_go(self):
        head = report(report_style(show_summary=False, footer=""))[0]
        assert head.fields == []
        assert head.footer.text is None

    def test_decimals_apply_to_every_figure(self):
        style = report_style(decimals=2, line_behind="{balance} {gain}")
        assert lines(report(style)[2])[0] == "-47.00M +3.40M"

    def test_progress(self):
        style = report_style(line_on_track="{progress}")
        assert lines(report(style)[1])[0] == "107%"

    def test_bomb_line_and_days(self):
        bomb = {'member': SimpleNamespace(trainer_name="solar"),
                'history': SimpleNamespace(deficit_surplus=-47_000_000, days_behind=14),
                'bomb': SimpleNamespace(days_remaining=2)}
        style = report_style(line_bomb="{marker} {name} {days_left} ({days_behind})")
        embeds = report(style, mode='quota', bombs=[bomb])
        assert embeds[-1].description == "🔴 solar 2 days (6 days)"

    def test_activation_alert_reason(self):
        alert = ReportGenerator().create_bomb_activation_alert(
            "Cute", [{'member': SimpleNamespace(trainer_name="solar"),
                      'bomb': SimpleNamespace(days_remaining=7)}],
            BehindCount('quota', 1).trigger_reason(3))
        assert alert[0].description.startswith("The following members are 3+ days of quota behind")


# --------------------------------------------------------------------------- #
# live board
# --------------------------------------------------------------------------- #

def snap(**kw):
    base = dict(
        circle_id="1", jst_day=date(2026, 7, 25),
        as_of=datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
        live_points=1_100_000_000, live_rank=300,
        monthly_point=1_000_000_000, monthly_rank=310,
        gains=[MemberGain("1", "Alpha", 5_000_000, 60_000_000),
               MemberGain("2", "Beta", 3_000_000, 40_000_000),
               MemberGain("3", "Idle", 0, 20_000_000)],
    )
    base.update(kw)
    return LiveSnapshot(**base)


def live(style=None, **kw):
    c = club()
    return live_board.build_embeds(c, snap(), style=embed_style.resolve(LIVE, c, style), **kw)


class TestLiveBoard:
    def test_default_matches_the_original(self):
        assert lines(live()[1])[0] == "` 1` **Alpha**: +5.00M (60.00M total)"

    def test_quota_tokens(self):
        embeds = live({'line_raced': "{name} {quota_pct} ({quota_left} left)"}, quota=8_000_000)
        assert lines(embeds[1]) == ["Alpha 62% (3.00M left)", "Beta 38% (5.00M left)"]

    def test_quota_tokens_without_a_quota(self):
        embeds = live({'line_raced': "{name} {quota_left}"})
        assert lines(embeds[1])[0] == "Alpha —"

    def test_sort_and_hide(self):
        embeds = live({'sort_raced': 'name', 'line_raced': "{name}", 'show_idle': False,
                       'show_rank': False})
        assert [e.title for e in embeds] == ["📊 Live Board - Cute", "✅ Raced Today"]
        assert lines(embeds[1]) == ["Alpha", "Beta"]
        assert [f.name for f in embeds[0].fields] == ["Fans today", "Month total", "📈 Summary"]

    def test_style_change_counts_as_a_different_board(self):
        a, b = club(), club()
        b.club_id = a.club_id
        b.embed_style = {'live': {'decimals': 1}}
        assert live_board._shown(a, snap()) != live_board._shown(b, snap())

    def test_board_stays_inside_one_message(self):
        gains = [MemberGain(str(i), f"Trainer {i:02}", 1_000_000 + i, 50_000_000)
                 for i in range(60)]
        c = club()
        style = embed_style.resolve(LIVE, c, {'line_raced': "x" * 100 + " {name}"})
        embeds = live_board.build_embeds(c, snap(gains=gains), style=style)
        assert sum(len(e) for e in embeds) <= live_board.MESSAGE_BUDGET


# --------------------------------------------------------------------------- #
# preview
# --------------------------------------------------------------------------- #

class TestSamplePreview:
    def test_sample_report_renders_for_each_period(self):
        for period in ('daily', 'weekly', 'biweekly'):
            c = club(quota_period=period)
            data, bombs, quota = embed_preview._sample_report(c, date(2026, 10, 20))
            embeds = ReportGenerator().create_daily_report(
                "Cute", quota, data, bombs, date(2026, 10, 20), quota_period=period,
                style=embed_style.resolve(REPORT, c))
            assert embeds[0].title == "📊 Daily Quota Report - Cute"
            assert any(e.title == "💣 Active Bombs" for e in embeds)

    def test_sample_live_board_renders(self):
        c = club()
        embeds = live_board.build_embeds(c, embed_preview._sample_snapshot(date(2026, 10, 20)))
        assert [e.title for e in embeds][-1] == "🆕 Joined Today"


# --------------------------------------------------------------------------- #
# extras a club adds
# --------------------------------------------------------------------------- #

def field_block(**kw):
    return {'type': 'field', 'title': "Reminder", 'text': "Race!", 'inline': False, **kw}


def list_block(**kw):
    base = {'type': 'list', 'title': "", 'who': 'all', 'order': 'gain', 'limit': 0,
            'line': "{name}", 'color': "#123456"}
    return {**base, **kw}


class TestBlockValidation:
    def test_problems_are_named_by_position(self):
        clean, errors = embed_style.validate(REPORT, {'extras': [
            field_block(), list_block(line=""), {'type': 'chart'}]})
        assert errors == {'extras.1.line': "Can't be empty", 'extras.2': "Unknown kind of block"}
        assert [b['type'] for b in clean['extras']] == ['field']

    def test_filters_are_per_kind(self):
        _, errors = embed_style.validate(REPORT, {'extras': [list_block(who='under_quota')]})
        assert 'extras.0.who' in errors

    def test_at_most_eight(self):
        clean, errors = embed_style.validate(REPORT, {'extras': [field_block()] * 9})
        assert errors == {'extras': "At most 8"}
        assert len(clean['extras']) == 8

    def test_links_must_be_https(self):
        _, errors = embed_style.validate(REPORT, {'thumbnail': "http://x.png", 'image': ""})
        assert errors == {'thumbnail': "Must be an https:// link"}

    def test_every_block_preset_validates(self):
        for kind in embed_style.KINDS:
            for preset in embed_style.block_presets(kind):
                assert embed_style.validate(kind, {'extras': [preset['block']]})[1] == {}, preset


class TestReportExtras:
    def test_field_with_club_tokens(self):
        style = report_style(extras=[field_block(
            title="Today", text="{fans_today} gained, top {top_name} ({top_gain})")])
        head = report(style)[0]
        assert (head.fields[-1].name, head.fields[-1].value) == ("Today", "8.7M gained, top solar (+3.4M)")

    def test_note_thumbnail_and_image(self):
        style = report_style(note="Next tally on {date}", thumbnail="https://x/logo.png",
                             image="https://x/banner.png")
        head = report(style)[0]
        assert head.description.endswith("\n\nNext tally on October 20")
        assert head.thumbnail.url == "https://x/logo.png"
        assert head.image.url == "https://x/banner.png"

    def test_top_gainers_list(self):
        style = report_style(extras=[list_block(title="Top {count}", who='gained', limit=3,
                                                line="{pos}. {name} {gain}")])
        extra = report(style)[-1]
        assert extra.title == "Top 3"
        assert lines(extra) == ["1. solar +3.4M", "2. iza +3.3M", "3. Aegis +1.0M"]
        assert extra.colour.value == 0x123456

    def test_a_size_that_is_not_offered_is_refused(self):
        _, errors = embed_style.validate(REPORT, {'extras': [list_block(limit=2)]})
        assert 'extras.0.limit' in errors

    def test_close_means_less_than_a_day_of_quota(self):
        style = report_style(extras=[list_block(who='close', title="Close")])
        # iza is 13.2M behind and solar 47M: at 8M a day neither is close,
        # at 20M a day iza is
        assert len(report(style)) == 3
        embeds = ReportGenerator().create_daily_report(
            "Cute", 20_000_000, summary(), [], date(2026, 10, 20), style=style)
        assert (embeds[-1].title, lines(embeds[-1])) == ("Close", ["iza"])

    def test_empty_list_is_left_out(self):
        style = report_style(extras=[list_block(who='no_gain')])
        assert len(report(style)) == 3

    def test_message_above_the_report(self):
        style = report_style(message="<@&42> report for {date}: {behind} behind")
        text = ReportGenerator().daily_report_message(
            "Cute", 8_000_000, summary(), [], date(2026, 10, 20), style=style)
        assert text == "<@&42> report for October 20: 2 behind"
        assert ReportGenerator().daily_report_message(
            "Cute", 8_000_000, summary(), [], date(2026, 10, 20), style=report_style()) is None


class TestLiveExtras:
    def test_under_quota_needs_the_quota(self):
        block = list_block(who='under_quota', line="{name} {quota_left}", order='gain')
        style = {'extras': [block], 'line_raced': "{name}"}
        assert live_board.needs_quota(embed_style.resolve(LIVE, club(), style))
        with_quota = live(style, quota=4_000_000)
        assert lines(with_quota[-1]) == ["Beta 1.00M", "Idle 4.00M"]
        without = live(style)
        assert [e.title for e in without][-1] == "⚠️ No Fans Yet Today"

    def test_plain_board_needs_no_quota(self):
        assert not live_board.needs_quota(embed_style.resolve(LIVE, club()))

    def test_message_and_field(self):
        c = club()
        style = embed_style.resolve(LIVE, c, {
            'message': "<@&7> {raced} raced so far",
            'extras': [field_block(title="Top", text="{top_name} {top_gain}")]})
        assert live_board.build_message(c, snap(), style=style) == "<@&7> 2 raced so far"
        head = live_board.build_embeds(c, snap(), style=style)[0]
        assert head.fields[-1].value == "Alpha +5.00M"
