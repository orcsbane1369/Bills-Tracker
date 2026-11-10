from datetime import date
from schedule import add_interval as ai


def test_days_and_weeks():
    assert ai(date(2026, 11, 6), 14, "day") == date(2026, 11, 20)
    assert ai(date(2026, 12, 30), 3, "day") == date(2027, 1, 2)
    assert ai(date(2026, 11, 6), 2, "week") == date(2026, 11, 20)


def test_months():
    assert ai(date(2026, 11, 22), 1, "month") == date(2026, 12, 22)
    assert ai(date(2026, 12, 22), 1, "month") == date(2027, 1, 22)
    assert ai(date(2027, 1, 1), 3, "month") == date(2027, 4, 1)      # quarterly trash
    assert ai(date(2026, 11, 1), 14, "month") == date(2028, 1, 1)


def test_month_end_keeps_its_anchor_day():
    d = date(2027, 1, 31)
    d = ai(d, 1, "month", 31); assert d == date(2027, 2, 28)
    d = ai(d, 1, "month", 31); assert d == date(2027, 3, 31)         # back to the 31st, no drift to the 28th
    assert ai(date(2028, 1, 31), 1, "month", 31) == date(2028, 2, 29)  # leap year


def test_years():
    assert ai(date(2026, 12, 28), 1, "year") == date(2027, 12, 28)
    assert ai(date(2028, 2, 29), 1, "year", 29) == date(2029, 2, 28)
    assert ai(date(2028, 2, 29), 4, "year", 29) == date(2032, 2, 29)


def test_bad_unit():
    try:
        ai(date(2026, 1, 1), 1, "decade")
    except ValueError:
        return
    raise AssertionError("accepted a bad unit")


if __name__ == "__main__":
    n = 0
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v(); n += 1
    print(n, "tests passed")
