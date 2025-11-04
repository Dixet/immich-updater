import re
from typing import List, Tuple, Optional
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

def guess_dt_from_filename(filename: str, patterns: List[Tuple[re.Pattern, Optional[str]]]) -> Optional[datetime]:
    """
    Try each compiled pattern.
    - If the regex has named groups (year/month/day[/hour/minute/second]), use them.
    - Else if a strptime format is present via ::fmt= (or future pair), try that on the first full match.
    """
    for rx, fmt in patterns:
        m = rx.search(filename or "")
        if not m:
            continue

        # 1) Try named groups first
        dt = _from_named_groups(m)
        if dt:
            return dt

        # 2) Try strptime on the first capturing group or the whole match
        if fmt:
            try_text = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
            try:
                dt2 = datetime.strptime(try_text, fmt).replace(tzinfo=timezone.utc)
                return dt2
            except Exception:
                pass

    return None

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
