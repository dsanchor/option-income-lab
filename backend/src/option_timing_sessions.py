"""US equity session cutoffs for timing; no live option probe or fixed UTC open."""

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from typing import ClassVar
from zoneinfo import ZoneInfo

from pandas.tseries.holiday import (
    AbstractHolidayCalendar,
    GoodFriday,
    Holiday,
    USLaborDay,
    USMartinLutherKingJr,
    USMemorialDay,
    USPresidentsDay,
    USThanksgivingDay,
    nearest_workday,
    sunday_to_monday,
)

NY = ZoneInfo("America/New_York")


class _NYSEHolidays(AbstractHolidayCalendar):
    rules: ClassVar[list] = [
        Holiday("New Year", month=1, day=1, observance=sunday_to_monday),
        USMartinLutherKingJr, USPresidentsDay, GoodFriday, USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, start_date="2022-01-01",
                observance=nearest_workday),
        Holiday("Independence", month=7, day=4, observance=nearest_workday),
        USLaborDay, USThanksgivingDay,
        Holiday("Christmas", month=12, day=25, observance=nearest_workday),
    ]


@lru_cache(maxsize=8)
def _holidays(year: int) -> frozenset[date]:
    dates = set(_NYSEHolidays().holidays(f"{year}-01-01", f"{year}-12-31").date)
    # Most recent extraordinary exchange closure, within the provider 2y window.
    if year == 2025:
        dates.add(date(2025, 1, 9))  # National day of mourning for President Carter.
    return frozenset(dates)


def is_session(day: date) -> bool:
    return day.weekday() < 5 and day not in _holidays(day.year)


def session_close(day: date) -> datetime:
    thanksgiving = next(
        date(day.year, 11, d) for d in range(22, 29)
        if date(day.year, 11, d).weekday() == 3
    )
    independence = date(day.year, 7, 4)
    early_july = (independence - timedelta(days=2) if independence.weekday() == 5
                  else independence - timedelta(days=1))
    early = day == thanksgiving + timedelta(days=1) or (
        day == early_july and is_session(day)
    ) or (day.month == 12 and day.day == 24 and is_session(day))
    return datetime.combine(day, time(13 if early else 16), NY).astimezone(timezone.utc)


def session_open(day: date) -> datetime:
    return datetime.combine(day, time(9, 30), NY).astimezone(timezone.utc)


def latest_session(now: datetime) -> date:
    day = now.astimezone(NY).date()
    if not is_session(day) or now < session_open(day):
        day -= timedelta(days=1)
        while not is_session(day):
            day -= timedelta(days=1)
    return day


def observation_cutoff(day: date, fetched_at: datetime) -> tuple[datetime, bool]:
    close = session_close(day)
    # Yahoo's last daily candle is mutable until the closing print settles.
    if fetched_at < close + timedelta(minutes=15):
        return min(fetched_at, close), True
    return close, False


def freshness(point: dict, now: datetime) -> str:
    """Session-aware freshness from effective input cutoff, not computation time."""
    try:
        as_of = datetime.fromisoformat(point["as_of"].replace("Z", "+00:00"))
        source = date.fromisoformat(point["source_session"])
        if as_of.tzinfo is None or as_of > now + timedelta(minutes=5):
            return "unknown"
        expected = latest_session(now)
        if source != expected:
            return "stale"
        close = session_close(expected)
        if now >= close:
            return "fresh" if as_of >= close else "stale"
        # Three-hour cron spacing plus up to one hour of provider/scheduler lag.
        return "fresh" if now - as_of <= timedelta(hours=4) else "stale"
    except (KeyError, ValueError, TypeError):
        return "unknown"
