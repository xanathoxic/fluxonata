"""Minimal 5-field cron (minute hour day-of-month month day-of-week) evaluated in an IANA timezone.

DST policy (documented, tested): a local time that does not exist (spring forward) is skipped for that day;
an ambiguous local time (fall back) fires once, at its first occurrence. Stored/compared instants are UTC.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]  # day-of-week: 0 and 7 are both Sunday


class CronError(ValueError):
    pass


def _field(text: str, lo: int, hi: int, dow: bool = False) -> tuple[set[int], bool]:
    values: set[int] = set()
    restricted = text != "*"
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, _, s = part.partition("/")
            if not s.isdigit() or int(s) < 1:
                raise CronError(f"Pas invalide : {s}")
            step = int(s)
            restricted = True
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            x, _, y = part.partition("-")
            if not (x.isdigit() and y.isdigit()):
                raise CronError(f"Intervalle invalide : {part}")
            a, b = int(x), int(y)
        elif part.isdigit():
            a = int(part)
            b = hi if step > 1 else a
        else:
            raise CronError(f"Valeur invalide : {part}")
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            raise CronError(f"Valeur hors limites : {part}")
        values |= set(range(a, b + 1, step))
    if dow:
        values = {0 if v == 7 else v for v in values}
    return values, restricted


def parse(expr: str):
    parts = expr.split()
    if len(parts) != 5:
        raise CronError("Une expression cron comporte 5 champs : minute heure jour mois jour-de-semaine.")
    parsed = [_field(p, *RANGES[i], dow=(i == 4)) for i, p in enumerate(parts)]
    return parsed


def _day_matches(d: date, parsed) -> bool:
    (_, _), (_, _), (doms, dom_r), (months, _), (dows, dow_r) = parsed
    if d.month not in months:
        return False
    cron_dow = (d.weekday() + 1) % 7  # Python Monday=0 -> cron Sunday=0
    dom_ok, dow_ok = d.day in doms, cron_dow in dows
    if dom_r and dow_r:
        return dom_ok or dow_ok  # classic cron: either restriction may match
    return dom_ok and dow_ok


def next_fire(expr: str, after: datetime, tz_name: str) -> datetime | None:
    """First fire instant strictly after `after` (aware datetime), returned in UTC."""
    parsed = parse(expr)
    tz = ZoneInfo(tz_name)
    after = after.astimezone(UTC)
    start_day = after.astimezone(tz).date()
    minutes, hours = sorted(parsed[0][0]), sorted(parsed[1][0])
    for offset in range(0, 800):
        day = start_day + timedelta(days=offset)
        if not _day_matches(day, parsed):
            continue
        for h in hours:
            for m in minutes:
                naive = datetime(day.year, day.month, day.day, h, m)
                utc = naive.replace(tzinfo=tz).astimezone(UTC)
                if utc.astimezone(tz).replace(tzinfo=None) != naive:
                    continue  # nonexistent local time (DST gap)
                if utc > after:
                    return utc
    return None


def upcoming(expr: str, tz_name: str, count: int = 5, after: datetime | None = None) -> list[datetime]:
    out, cur = [], after or datetime.now(UTC)
    for _ in range(count):
        cur = next_fire(expr, cur, tz_name)
        if cur is None:
            break
        out.append(cur)
    return out
