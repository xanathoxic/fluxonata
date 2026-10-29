from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from triggers.cron import CronError, next_fire, parse, upcoming

UTC_ = UTC


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def test_every_five_minutes():
    assert next_fire("*/5 * * * *", utc(2026, 1, 1, 10, 3), "UTC") == utc(2026, 1, 1, 10, 5)
    assert next_fire("*/5 * * * *", utc(2026, 1, 1, 10, 5), "UTC") == utc(2026, 1, 1, 10, 10)  # strictly after


def test_daily_in_timezone_is_stored_in_utc():
    # 08:00 Paris in winter = 07:00 UTC, in summer = 06:00 UTC
    assert next_fire("0 8 * * *", utc(2026, 1, 10, 0, 0), "Europe/Paris") == utc(2026, 1, 10, 7, 0)
    assert next_fire("0 8 * * *", utc(2026, 7, 10, 0, 0), "Europe/Paris") == utc(2026, 7, 10, 6, 0)


def test_dst_gap_is_skipped_and_ambiguous_hour_fires_once():
    # 2026-03-29 02:30 does not exist in Paris -> that day is skipped
    assert next_fire("30 2 * * *", utc(2026, 3, 28, 12, 0), "Europe/Paris") == utc(2026, 3, 30, 0, 30)
    # 2026-10-25 02:30 happens twice -> exactly one fire, the first occurrence (00:30 UTC)
    fires = upcoming("30 2 * * *", "Europe/Paris", 3, after=utc(2026, 10, 24, 12, 0))
    assert fires[0] == utc(2026, 10, 25, 0, 30) and fires[1].date().day == 26


def test_weekday_and_month_restrictions():
    # Mondays only (cron dow 1), 2026-01-05 is a Monday
    assert next_fire("0 9 * * 1", utc(2026, 1, 1, 0, 0), "UTC") == utc(2026, 1, 5, 9, 0)
    assert next_fire("0 0 1 3 *", utc(2026, 1, 1, 0, 0), "UTC") == utc(2026, 3, 1, 0, 0)
    assert next_fire("0 12 * * 1-5", utc(2026, 1, 3, 0, 0), "UTC") == utc(2026, 1, 5, 12, 0)  # Saturday -> Monday
    assert next_fire("0 12 * * 0", utc(2026, 1, 1, 0, 0), "UTC").weekday() == 6 and parse("0 0 * * 7")


def test_dom_or_dow_semantics():
    # day-of-month 15 OR Monday
    nxt = next_fire("0 0 15 * 1", utc(2026, 1, 1, 0, 0), "UTC")
    assert nxt == utc(2026, 1, 5, 0, 0)


@pytest.mark.parametrize(
    "bad", ["", "* * * *", "61 * * * *", "* 24 * * *", "a b c d e", "*/0 * * * *", "5-2 * * * *", "* * 32 * *"]
)
def test_invalid_expressions(bad):
    with pytest.raises(CronError):
        parse(bad)


def test_lists_ranges_steps():
    assert parse("0,30 8-10 * * *")[0][0] == {0, 30} and parse("0,30 8-10 * * *")[1][0] == {8, 9, 10}
    assert ZoneInfo("UTC")
