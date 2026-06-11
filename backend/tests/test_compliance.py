"""Mt. compliance engine — one test per rule, plus a clean happy path."""

import uuid
from datetime import UTC, date, datetime, time, timedelta

from app.services.wfm.compliance import (
    ShiftSpan,
    check_daily_rest,
    check_max_daily,
    check_max_weekly,
    check_modify_notice,
    check_publish_notice,
    check_schedule,
    check_six_consecutive_days,
    check_weekly_rest,
)

EMP = uuid.uuid4()


def span(day: date, start: str, end: str, break_min: int = 0) -> ShiftSpan:
    return ShiftSpan(
        id=uuid.uuid4(),
        employee_id=EMP,
        work_date=day,
        start_time=time.fromisoformat(start),
        end_time=time.fromisoformat(end),
        break_minutes=break_min,
    )


MONDAY = date(2026, 7, 6)  # hétfő, jövőbeli hét


def test_max_daily_over_12h_errors():
    violations = check_max_daily([span(MONDAY, "06:00", "19:30")])  # 13.5h
    assert [v.code for v in violations] == ["MAX_DAILY"]
    assert violations[0].severity == "error"


def test_max_daily_break_not_counted():
    # 12.5h jelenlét, 60 perc szünettel = 11.5h munkaidő → nincs sértés
    assert check_max_daily([span(MONDAY, "06:00", "18:30", break_min=60)]) == []


def test_max_weekly_over_48h_errors():
    shifts = [
        span(MONDAY + timedelta(days=i), "06:00", "16:30")  # 10.5h × 5 = 52.5h
        for i in range(5)
    ]
    violations = check_max_weekly(shifts)
    assert [v.code for v in violations] == ["MAX_WEEKLY"]


def test_daily_rest_under_11h_errors():
    shifts = [
        span(MONDAY, "12:00", "22:00"),
        span(MONDAY + timedelta(days=1), "06:00", "14:00"),  # csak 8h pihenő
    ]
    violations = check_daily_rest(shifts)
    assert [v.code for v in violations] == ["DAILY_REST"]


def test_daily_rest_11h_ok():
    shifts = [
        span(MONDAY, "12:00", "20:00"),
        span(MONDAY + timedelta(days=1), "07:00", "15:00"),  # 11h pihenő
    ]
    assert check_daily_rest(shifts) == []


def test_overlapping_shifts_error():
    shifts = [
        span(MONDAY, "08:00", "16:00"),
        span(MONDAY, "14:00", "22:00"),
    ]
    codes = [v.code for v in check_daily_rest(shifts)]
    assert "OVERLAP" in codes


def test_overnight_shift_crosses_midnight():
    s = span(MONDAY, "22:00", "06:00")  # éjszakás
    assert s.end.date() == MONDAY + timedelta(days=1)
    assert abs(s.worked_hours - 8.0) < 0.01


def test_six_consecutive_days_errors():
    shifts = [span(MONDAY + timedelta(days=i), "08:00", "16:00") for i in range(7)]
    violations = check_six_consecutive_days(shifts)
    assert [v.code for v in violations] == ["SIX_DAYS"]


def test_six_days_with_rest_ok():
    days = [0, 1, 2, 3, 4, 6]  # szombat pihenő
    shifts = [span(MONDAY + timedelta(days=i), "08:00", "16:00") for i in days]
    assert check_six_consecutive_days(shifts) == []


def test_weekly_rest_missing_errors():
    # 21 egymást követő nap — sehol nincs 48h egybefüggő pihenő
    shifts = [span(MONDAY + timedelta(days=i), "08:00", "16:00") for i in range(21)]
    codes = {v.code for v in check_weekly_rest(shifts)}
    assert "WEEKLY_REST" in codes


def test_weekly_rest_weekend_ok():
    # hétfő–péntek munka, hétvége szabad (~63h pihenő)
    shifts = [span(MONDAY + timedelta(days=i), "08:00", "16:00") for i in range(5)]
    assert check_weekly_rest(shifts) == []


def test_publish_notice_under_168h_warns():
    now = datetime.combine(MONDAY - timedelta(days=3), time(12, 0), tzinfo=UTC)
    violations = check_publish_notice([span(MONDAY, "08:00", "16:00")], now=now)
    assert [v.code for v in violations] == ["PUBLISH_NOTICE"]
    assert violations[0].severity == "warning"


def test_publish_notice_168h_ok():
    now = datetime.combine(MONDAY - timedelta(days=8), time(8, 0), tzinfo=UTC)
    assert check_publish_notice([span(MONDAY, "08:00", "16:00")], now=now) == []


def test_modify_notice_under_96h_warns():
    now = datetime.combine(MONDAY - timedelta(days=2), time(8, 0), tzinfo=UTC)
    violations = check_modify_notice(span(MONDAY, "08:00", "16:00"), now=now)
    assert [v.code for v in violations] == ["MODIFY_NOTICE"]


def test_modify_notice_96h_ok():
    now = datetime.combine(MONDAY - timedelta(days=5), time(8, 0), tzinfo=UTC)
    assert check_modify_notice(span(MONDAY, "08:00", "16:00"), now=now) == []


def test_clean_week_no_violations():
    """Szabályos hét: Mo–Fr 8h, hétvége szabad, 8 nappal előre közölve."""
    shifts = [span(MONDAY + timedelta(days=i), "08:00", "16:30", break_min=30) for i in range(5)]
    now = datetime.combine(MONDAY - timedelta(days=8), time(8, 0), tzinfo=UTC)
    assert check_schedule(shifts, now=now, publish=True) == []
