"""Utility helpers for filename pattern matching and datetime mangling.

Patterns are regular expressions that may either use named groups to
extract year/month/etc. or be paired with a strptime-like format string.
This module is responsible for compiling those patterns, guessing a
`datetime` from a filename, and normalizing/merging time components.
"""

import re
from typing import List, Tuple, Optional, Dict
from datetime import datetime, timezone

# compile_patterns returns a list of (compiled_regex, optional_strptime_format)
def compile_patterns(patterns_text: str) -> List[Tuple[re.Pattern, Optional[str]]]:
    """
    Accepts multi-line text. Each line may be:
      - a raw regex (string), OR
      - a JSON-like 2-tuple that was flattened before, we still just take the regex.
    If a line contains '::fmt=' at the end (manual override), we treat the rhs as strptime fmt.
      Example: ^IMG-(\\d{8})-WA\\d+$::fmt=%Y%m%d
    """
    compiled: List[Tuple[re.Pattern, Optional[str]]] = []
    for raw in (patterns_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue

        fmt: Optional[str] = None
        if "::fmt=" in line:
            # optional inline format hint
            parts = line.split("::fmt=", 1)
            line, fmt = parts[0].strip(), parts[1].strip()

        try:
            rx = re.compile(line)
            compiled.append((rx, fmt))
        except re.error:
            # skip invalid regex
            continue
    return compiled

def _from_named_groups(m: re.Match) -> Optional[datetime]:
    """If a match contains named date/time groups, convert them to datetime.

    The supported names are `year`, `month`, `day`, `hour`, `minute`, and
    `second`.  Missing fields are defaulted to sensible values (e.g. month
    and day default to 1).  If no `year` group is present we return `None`.
    """
    gd = m.groupdict()
    if not gd:
        return None
    try:
        year = int(gd.get("year")) if gd.get("year") else None
        month = int(gd.get("month")) if gd.get("month") else 1
        day = int(gd.get("day")) if gd.get("day") else 1
        hour = int(gd.get("hour")) if gd.get("hour") else 0
        minute = int(gd.get("minute")) if gd.get("minute") else 0
        second = int(gd.get("second")) if gd.get("second") else 0
        if year is None:
            return None
        return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    except Exception:
        return None

def guess_info_from_filename(filename: str, patterns: List[Tuple[re.Pattern, Optional[str]]]) -> Dict[str, Optional[datetime]]:
    """Extract metadata from a filename using the given patterns.

    Returns a dictionary with keys:

    * ``dt`` – a :class:`datetime` if one could be parsed, otherwise ``None``.
    * ``descr`` – the contents of a named capture group called ``descr`` if
      the regex supplies one (useful for guessing a description from the
      filename).

    The extraction logic supports both named datetime groups and optional
    `::fmt=` parsing, while preserving the named-group description.
    """
    for rx, fmt in patterns:
        m = rx.search(filename or "")
        if not m:
            continue

        info: Dict[str, Optional[datetime]] = {"dt": None, "descr": None}

        # try named groups for datetime
        dt = _from_named_groups(m)
        if dt:
            info["dt"] = dt
        elif fmt:
            # fall back to strptime if format string provided
            try_text = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
            try:
                info["dt"] = datetime.strptime(try_text, fmt).replace(tzinfo=timezone.utc)
            except Exception:
                pass

        # capture a description if the regex supplied it
        gd = m.groupdict()
        if "descr" in gd and gd.get("descr"):
            info["descr"] = gd.get("descr")

        return info

    # if we fall out of the loop we never matched any pattern; return
    # an empty result rather than None so callers don't blow up.
    return {"dt": None, "descr": None}


def to_iso_z(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    # standardize with Z suffix (UTC)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def merge_time_from_source(guessed_dt: Optional[datetime], source_dt: Optional[datetime]) -> Optional[datetime]:
    """
    Merge the time-of-day from source_dt into guessed_dt.
    If guessed_dt has no time component (00:00:00) and source_dt exists,
    replace just the time components while keeping the date from guessed_dt.
    """
    if not guessed_dt:
        return None
    if not source_dt:
        return guessed_dt
    
    # Check if guessed datetime has no time (midnight)
    if guessed_dt.hour == 0 and guessed_dt.minute == 0 and guessed_dt.second == 0:
        # Replace time components from source_dt
        return guessed_dt.replace(
            hour=source_dt.hour,
            minute=source_dt.minute,
            second=source_dt.second,
            microsecond=source_dt.microsecond
        )
    
    # Guessed datetime already has time, keep it as is
    return guessed_dt
