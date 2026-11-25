"""Cleaning a saved "starting view" for the pages whose view lives in the address (Confluence,
DAS Explorer): what a user pastes, or the page's own query string, is reduced to that page's
recognised settings. Pure functions, no DB."""
import pytest

from data_viz.starting_views import PAGES, clean_view_query


class TestCleanViewQuery:
    def test_query_string_keeps_recognised_settings(self):
        assert clean_view_query("confluence", "province=ontario&visual=deaths&grain=year") == \
            "province=ontario&visual=deaths&grain=year"

    def test_leading_question_mark_is_fine(self):
        assert clean_view_query("confluence", "?province=ontario&visual=deaths") == \
            "province=ontario&visual=deaths"

    def test_unknown_settings_are_dropped(self):
        assert clean_view_query("confluence", "province=ontario&utm_source=mail&next=/evil") == \
            "province=ontario"

    def test_full_link_to_the_page_is_accepted(self):
        link = "https://canask.example/v1/national/confluence?province=ontario&visual=deaths"
        assert clean_view_query("confluence", link) == "province=ontario&visual=deaths"

    def test_site_relative_link_is_accepted(self):
        assert clean_view_query("das-explorer", "/v1/national/das-explorer?ds=quant") == "ds=quant"

    def test_link_to_another_page_is_refused(self):
        with pytest.raises(ValueError, match="Confluence"):
            clean_view_query("confluence", "https://canask.example/v1/national/das-explorer?ds=quant")

    def test_link_to_another_site_path_is_refused(self):
        with pytest.raises(ValueError):
            clean_view_query("confluence", "https://elsewhere.example/steal?province=ontario")

    def test_nothing_recognised_is_refused(self):
        with pytest.raises(ValueError):
            clean_view_query("confluence", "utm_source=mail")

    def test_empty_is_refused(self):
        with pytest.raises(ValueError):
            clean_view_query("confluence", "   ")

    def test_das_filters_keep_their_prefix_and_repeats_in_order(self):
        raw = "ds=id_all&f_province=BC&f_province=AB&f_drugs_identified=fentanyl+AND+cocaine&sort=date_received.desc"
        assert clean_view_query("das-explorer", raw) == raw

    def test_das_chart_settings_are_kept(self):
        raw = "ds=id_all&rows=province&cols=year_received&measure=samples&chart=map_province"
        assert clean_view_query("das-explorer", raw) == raw

    def test_map_slider_period_is_kept_on_both_pages(self):
        assert clean_view_query("confluence", "province=ontario&visual=heat&period=2023") == \
            "province=ontario&visual=heat&period=2023"
        assert clean_view_query("das-explorer", "ds=id_all&chart=map_province&period=2024-06") == \
            "ds=id_all&chart=map_province&period=2024-06"

    def test_special_characters_survive_a_round_trip(self):
        raw = clean_view_query("confluence", 'province=ontario&expr=(fentanyl AND "crack cocaine") OR lsd')
        assert clean_view_query("confluence", raw) == raw
        assert "%28fentanyl" in raw

    def test_overlong_value_is_refused(self):
        with pytest.raises(ValueError):
            clean_view_query("confluence", "province=" + "x" * 5000)

    def test_too_many_settings_is_refused(self):
        with pytest.raises(ValueError):
            clean_view_query("das-explorer", "&".join(f"f_field{i}={'v' * 300}" for i in range(20)))

    def test_control_characters_are_refused(self):
        with pytest.raises(ValueError):
            clean_view_query("confluence", "province=onta%00rio")

    def test_unknown_page_is_refused(self):
        with pytest.raises(ValueError):
            clean_view_query("somewhere", "a=b")

    def test_every_page_has_a_label_and_path(self):
        for page in PAGES.values():
            assert page["label"] and page["path"].startswith("/v1/")
