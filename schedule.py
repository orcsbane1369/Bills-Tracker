"""Due-date math for recurring bills. Pure functions, no web or database code."""
import calendar
from datetime import date, timedelta

UNITS = ("day", "week", "month", "year")


def add_interval(d, value, unit, anchor_day=None):
    """The date `value` `unit`s after `d`.

    Month and year steps keep the bill's anchor day (the day of the month it is normally due), clamped to the length
    of the target month. A bill due on the 31st goes Jan 31 -> Feb 28 -> Mar 31, instead of drifting to the 28th."""
    if unit == "day":
        return d + timedelta(days=value)
    if unit == "week":
        return d + timedelta(weeks=value)
    if unit not in ("month", "year"):
        raise ValueError("Unknown frequency unit: %r" % (unit,))
    months = value * (12 if unit == "year" else 1)
    idx = d.year * 12 + (d.month - 1) + months
    year, month = divmod(idx, 12)
    month += 1
    day = min(anchor_day or d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
