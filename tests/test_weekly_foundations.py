import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.weekly.config import WeeklyConfig
from app.weekly.coverage import source_coverage
from app.weekly.counts import weekly_event_counts
from app.weekly.windows import WeekWindow, last_closed_week, previous_week_starts, week_start_for, week_window


IST = ZoneInfo("Asia/Kolkata")
WEEK_START = date(2026, 11, 9)
WEEK_DAYS = [WEEK_START + timedelta(days=offset) for offset in range(7)]


def make_target(created_at=None, **overrides):
    values = {
        "created_at": created_at or datetime(2026, 1, 1, tzinfo=IST),
        "github_url": "https://github.com/example/project",
        "ats_url": "https://jobs.example.com",
        "blog_url": "https://example.com/blog",
        "web_social_url": "https://example.com/news",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_summary(day, source, status="success", **counters):
    values = {
        "collection_date": day,
        "source_type": source,
        "status": status,
        "candidates_total": 0,
        "known_items": 0,
        "failed_items": 0,
        "cap_skipped_items": 0,
    }
    values.update(counters)
    return SimpleNamespace(**values)


def make_event(day, source, baseline=False):
    return SimpleNamespace(collection_date=day, source_type=source, is_baseline=baseline)


class WeeklyWindowTests(unittest.TestCase):
    def test_ist_week_boundaries_and_utc_midnight_crossing(self):
        cases = [
            (datetime(2026, 11, 9, 23, 59, tzinfo=IST), date(2026, 11, 9)),
            (datetime(2026, 11, 10, 0, 0, tzinfo=IST), date(2026, 11, 9)),
            (datetime(2026, 11, 8, 18, 29, tzinfo=timezone.utc), date(2026, 11, 2)),
            (datetime(2026, 11, 8, 18, 30, tzinfo=timezone.utc), date(2026, 11, 9)),
        ]
        for instant, expected in cases:
            with self.subTest(instant=instant):
                self.assertEqual(week_start_for(instant), expected)

    def test_week_windows_cross_month_and_year_boundaries(self):
        cases = [
            (date(2026, 11, 30), date(2026, 11, 30), date(2026, 12, 6)),
            (date(2027, 1, 1), date(2026, 12, 28), date(2027, 1, 3)),
        ]
        for value, expected_start, expected_end in cases:
            with self.subTest(value=value):
                self.assertEqual(
                    week_window(value),
                    WeekWindow(start_date=expected_start, end_date=expected_end),
                )

    def test_previous_weeks_are_oldest_first_and_exclude_current(self):
        self.assertEqual(
            previous_week_starts(date(2027, 1, 4), 2),
            [date(2026, 12, 21), date(2026, 12, 28)],
        )
        with self.assertRaises(ValueError):
            previous_week_starts(date(2026, 11, 10), 2)

    def test_last_closed_week_is_the_week_before_the_current_monday(self):
        self.assertEqual(last_closed_week(datetime(2026, 11, 16, 3, 0, tzinfo=IST)), WEEK_START)
        self.assertEqual(last_closed_week(datetime(2026, 11, 15, 23, 59, tzinfo=IST)), date(2026, 11, 2))


class WeeklyConfigTests(unittest.TestCase):
    def test_defaults_match_the_decided_weekly_design(self):
        config = WeeklyConfig.from_env({})
        self.assertEqual(config.history_weeks, 12)
        self.assertEqual(config.half_life_weeks, 2.0)
        self.assertEqual(config.schedule_hour, 3)
        self.assertEqual(config.weekly_llm_model, "openai/gpt-oss-120b")
        self.assertEqual(config.embed_model_id, "BAAI/bge-small-en-v1.5")
        self.assertEqual(config.unverified_threshold, 0.20)

    def test_environment_values_override_defaults(self):
        config = WeeklyConfig.from_env({
            "RAVN_WEEKLY_HISTORY_WEEKS": "6",
            "RAVN_WEEKLY_UNVERIFIED_THRESHOLD": "0.25",
            "RAVN_WEEKLY_MIN_SIMILARITY": "0.72",
            "RAVN_WEEKLY_WEEKLY_LLM_MODEL": "test/model",
        })
        self.assertEqual(config.history_weeks, 6)
        self.assertEqual(config.unverified_threshold, 0.25)
        self.assertEqual(config.min_similarity, 0.72)
        self.assertEqual(config.weekly_llm_model, "test/model")

    def test_invalid_threshold_and_non_monday_start_are_rejected(self):
        with self.assertRaises(ValueError):
            WeeklyConfig(unverified_threshold=1.1)
        with self.assertRaises(ValueError):
            WeeklyConfig(week_start="sunday")


class SourceCoverageTests(unittest.TestCase):
    def test_target_onboarded_wednesday_has_no_pre_onboarding_misses(self):
        target = make_target(datetime(2026, 11, 11, 10, 0, tzinfo=IST))
        statuses = source_coverage(
            target,
            events=[],
            summaries=[make_summary(date(2026, 11, 11), source) for source in ("github", "ats", "blog", "web_social")],
            week_start=WEEK_START,
            today=date(2026, 11, 12),
        )
        for state in statuses.values():
            self.assertEqual(state.expected_days, 1)
            self.assertEqual(state.collected_days, 1)
            self.assertEqual(state.coverage, "complete")

    def test_complete_quiet_and_active_are_independent(self):
        statuses = source_coverage(
            make_target(),
            events=[make_event(date(2026, 11, 10), "github")],
            summaries=[make_summary(day, "github") for day in WEEK_DAYS],
            week_start=WEEK_START,
            today=date(2026, 11, 16),
        )
        self.assertEqual(statuses["github"].activity, "active")
        self.assertEqual(statuses["github"].coverage, "complete")
        self.assertEqual(statuses["ats"].activity, "quiet")
        self.assertEqual(statuses["ats"].coverage, "unavailable")

    def test_failed_day_and_missing_summary_row_each_make_coverage_partial(self):
        cases = [
            ([make_summary(day, "github") for day in WEEK_DAYS[:-1]] + [make_summary(WEEK_DAYS[-1], "github", status="failed")], 6),
            ([make_summary(day, "github") for day in WEEK_DAYS[:-1]], 6),
        ]
        for summaries, collected_days in cases:
            with self.subTest(summaries=len(summaries), collected=collected_days):
                state = source_coverage(
                    make_target(), [], summaries, WEEK_START, today=date(2026, 11, 16)
                )["github"]
                self.assertEqual(state.coverage, "partial")
                self.assertEqual(state.collected_days, collected_days)
                self.assertEqual(len(state.missing_dates), 1)

    def test_zero_collected_days_are_unavailable_and_skipped_days_do_not_count(self):
        summaries = [make_summary(day, "web_social", status="skipped") for day in WEEK_DAYS]
        state = source_coverage(
            make_target(), [], summaries, WEEK_START, today=date(2026, 11, 16)
        )["web_social"]
        self.assertEqual(state.coverage, "unavailable")
        self.assertEqual(state.collected_days, 0)
        self.assertEqual(state.expected_days, 7)

    def test_baseline_only_summary_counts_as_collected(self):
        state = source_coverage(
            make_target(), [], [make_summary(WEEK_DAYS[0], "github", status="baseline_only")],
            WEEK_START, today=date(2026, 11, 10),
        )["github"]
        self.assertEqual(state.collected_days, 1)
        self.assertEqual(state.coverage, "complete")

    def test_exact_threshold_is_partial_but_below_threshold_is_complete(self):
        cases = [
            (5, 0, 1, 0.20, "partial"),
            (10, 0, 1, 0.10, "complete"),
        ]
        for total, known, failed, expected_share, expected_status in cases:
            with self.subTest(total=total, known=known, failed=failed):
                summaries = [make_summary(day, "ats") for day in WEEK_DAYS]
                summaries[0] = make_summary(
                    WEEK_DAYS[0], "ats", candidates_total=total,
                    known_items=known, failed_items=failed,
                )
                state = source_coverage(
                    make_target(), [], summaries, WEEK_START,
                    today=date(2026, 11, 16), unverified_threshold=0.20,
                )["ats"]
                self.assertAlmostEqual(state.unverified_share, expected_share)
                self.assertEqual(state.coverage, expected_status)

    def test_known_urls_are_removed_from_unverified_denominator(self):
        summaries = [make_summary(day, "blog") for day in WEEK_DAYS]
        summaries[0] = make_summary(
            WEEK_DAYS[0], "blog", candidates_total=10, known_items=5, failed_items=1,
        )
        state = source_coverage(
            make_target(), [], summaries, WEEK_START, today=date(2026, 11, 16)
        )["blog"]
        self.assertAlmostEqual(state.unverified_share, 0.20)
        self.assertEqual(state.coverage, "partial")

    def test_not_configured_source_has_distinct_state(self):
        target = make_target(blog_url=None)
        state = source_coverage(
            target, [], [], WEEK_START, today=date(2026, 11, 16)
        )["blog"]
        self.assertFalse(state.configured)
        self.assertEqual(state.activity, "not_configured")
        self.assertEqual(state.coverage, "not_configured")

    def test_baseline_events_do_not_make_source_active(self):
        state = source_coverage(
            make_target(), [make_event(date(2026, 11, 10), "github", baseline=True)],
            [make_summary(day, "github") for day in WEEK_DAYS],
            WEEK_START, today=date(2026, 11, 16),
        )["github"]
        self.assertEqual(state.event_count, 0)
        self.assertEqual(state.activity, "quiet")
        self.assertEqual(state.coverage, "complete")


class WeeklyEventCountTests(unittest.TestCase):
    def test_exact_counts_cover_history_and_current_week_excluding_baselines(self):
        events = [
            make_event(date(2026, 10, 26), "github"),
            make_event(date(2026, 10, 30), "blog"),
            make_event(date(2026, 11, 2), "ats"),
            make_event(date(2026, 11, 8), "github"),
            make_event(date(2026, 11, 9), "github"),
            make_event(date(2026, 11, 15), "web_social"),
            make_event(date(2026, 11, 10), "github", baseline=True),
        ]
        counts = weekly_event_counts(events, WEEK_START, history_weeks=2)
        self.assertEqual(list(counts), [date(2026, 10, 26), date(2026, 11, 2), WEEK_START])
        self.assertEqual(counts[date(2026, 10, 26)], {"github": 1, "ats": 0, "blog": 1, "web_social": 0})
        self.assertEqual(counts[date(2026, 11, 2)], {"github": 1, "ats": 1, "blog": 0, "web_social": 0})
        self.assertEqual(counts[WEEK_START], {"github": 1, "ats": 0, "blog": 0, "web_social": 1})


if __name__ == "__main__":
    unittest.main()
