# --- nse calendar: keep byte-identical in every script that has it (tests/test_repo.py checks) ---
# The NSE equity-segment trading calendar. Built-in tables for 2024 to 2026 (NSE circulars), then
# trader/data/calendar.json (trader-data: data.py calendar, from NSE's holiday master and its trading
# circulars) laid on top: for a year the file lists, its holidays replace the built-in ones. A missing or
# unreadable file means the built-in tables; nothing here raises.
BUILTIN_HOLIDAYS = {   # weekday closures of the regular 09:15-15:30 market
    "2024-01-22": "Special holiday (Ram Mandir)", "2024-01-26": "Republic Day",
    "2024-03-08": "Mahashivratri", "2024-03-25": "Holi", "2024-03-29": "Good Friday",
    "2024-04-11": "Id-Ul-Fitr (Ramadan Eid)", "2024-04-17": "Shri Ram Navmi",
    "2024-05-01": "Maharashtra Day", "2024-05-20": "Lok Sabha election (Mumbai)",
    "2024-06-17": "Bakri Id", "2024-07-17": "Moharram", "2024-08-15": "Independence Day",
    "2024-10-02": "Mahatma Gandhi Jayanti", "2024-11-01": "Diwali Laxmi Pujan",
    "2024-11-15": "Gurunanak Jayanti", "2024-11-20": "Maharashtra assembly election",
    "2024-12-25": "Christmas",
    "2025-02-26": "Mahashivratri", "2025-03-14": "Holi", "2025-03-31": "Id-Ul-Fitr (Ramadan Eid)",
    "2025-04-10": "Shri Mahavir Jayanti", "2025-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2025-04-18": "Good Friday", "2025-05-01": "Maharashtra Day", "2025-08-15": "Independence Day",
    "2025-08-27": "Ganesh Chaturthi", "2025-10-02": "Mahatma Gandhi Jayanti/Dussehra",
    "2025-10-21": "Diwali Laxmi Pujan", "2025-10-22": "Diwali-Balipratipada",
    "2025-11-05": "Prakash Gurpurb Sri Guru Nanak Dev", "2025-12-25": "Christmas",
    "2026-01-15": "Maharashtra municipal elections (special holiday)", "2026-01-26": "Republic Day",
    "2026-03-03": "Holi", "2026-03-26": "Shri Ram Navami", "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday", "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day", "2026-05-28": "Bakri Id", "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi", "2026-10-02": "Mahatma Gandhi Jayanti", "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali-Balipratipada", "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
BUILTIN_YEARS = (2024, 2025, 2026)
BUILTIN_SPECIAL = {    # normal-hours sessions on a weekend or a holiday
    "2024-01-20": "Saturday special session", "2024-03-02": "Saturday DR session",
    "2024-05-18": "Saturday DR session", "2025-02-01": "Union Budget (Saturday)",
    "2026-02-01": "Union Budget (Sunday)",
}
BUILTIN_MUHURAT = {    # one evening hour at times set by circular: the regular market is closed that day
    "2023-11-12": "Diwali Laxmi Pujan (Muhurat), Sunday", "2024-11-01": "Diwali Laxmi Pujan (Muhurat)",
    "2025-10-21": "Diwali Laxmi Pujan (Muhurat)", "2026-11-08": "Diwali Laxmi Pujan (Muhurat), Sunday",
}
NSE_HOLIDAYS = dict(BUILTIN_HOLIDAYS)    # these four hold the built-in tables plus calendar.json
SPECIAL_SESSIONS = dict(BUILTIN_SPECIAL)
MUHURAT_SESSIONS = dict(BUILTIN_MUHURAT)
CALENDAR_YEARS = BUILTIN_YEARS
CALENDAR_PATH = os.path.join("trader", "data", "calendar.json")


def _cal_dates(m):
    """{"2027-01-26": "Republic Day"} with bad keys dropped; anything but an object is corrupt."""
    if m is None:
        return {}
    if not isinstance(m, dict):
        raise ValueError("not an object")
    out = {}
    for k, v in m.items():
        try:
            datetime.date.fromisoformat(k)
        except (TypeError, ValueError):
            continue
        out[k] = str(v)
    return out


def load_calendar(path=None):
    """trader/data/calendar.json -> {"years", "holidays", "special_sessions", "fetched_at",
    "last_circular_check", "warnings"}, or None when it is missing or unreadable."""
    try:
        with open(path or CALENDAR_PATH, encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict):
            return None
        warns = doc.get("warnings")
        return {"years": sorted(set(int(y) for y in (doc.get("years") or []))),
                "holidays": _cal_dates(doc.get("holidays")),
                "special_sessions": _cal_dates(doc.get("special_sessions")),
                "fetched_at": doc.get("fetched_at"), "last_circular_check": doc.get("last_circular_check"),
                "warnings": [str(w) for w in warns] if isinstance(warns, list) else []}
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def is_muhurat(name):
    low = str(name).lower()
    return "muhurat" in low or "laxmi pujan" in low


def apply_calendar(path=None):
    """Rebuild NSE_HOLIDAYS, SPECIAL_SESSIONS, MUHURAT_SESSIONS and CALENDAR_YEARS from the built-in
    tables plus calendar.json (path None = CALENDAR_PATH). Returns the file's contents, or None."""
    global CALENDAR_YEARS
    cal = load_calendar(path)
    for table, base in ((NSE_HOLIDAYS, BUILTIN_HOLIDAYS), (SPECIAL_SESSIONS, BUILTIN_SPECIAL),
                        (MUHURAT_SESSIONS, BUILTIN_MUHURAT)):
        table.clear()
        table.update(base)
    CALENDAR_YEARS = BUILTIN_YEARS
    if not cal:
        return None
    for y in cal["years"]:
        for iso in [k for k in NSE_HOLIDAYS if k.startswith("%04d-" % y)]:
            del NSE_HOLIDAYS[iso]
    NSE_HOLIDAYS.update(cal["holidays"])
    for iso, name in cal["special_sessions"].items():
        SPECIAL_SESSIONS.pop(iso, None)
        MUHURAT_SESSIONS.pop(iso, None)
        (MUHURAT_SESSIONS if is_muhurat(name) else SPECIAL_SESSIONS)[iso] = name
    CALENDAR_YEARS = tuple(sorted(set(BUILTIN_YEARS) | set(cal["years"])))
    return cal


def years_text():
    return ", ".join(str(y) for y in CALENDAR_YEARS)


def trading_day(d):
    """(open, reason) for the regular 09:15-15:30 market on date d. A normal-hours special session is
    open; a weekend, a listed holiday and a Muhurat day are closed. Outside CALENDAR_YEARS the weekday
    rule is a guess, which calendar_known says."""
    iso = d.isoformat()
    if iso in MUHURAT_SESSIONS:
        return False, ("special session: %s, about an hour in the evening at times set by NSE; "
                       "no normal 09:15-15:30 market" % MUHURAT_SESSIONS[iso])
    if iso in SPECIAL_SESSIONS:
        return True, ""
    if d.weekday() >= 5:
        return False, "weekend"
    name = NSE_HOLIDAYS.get(iso)
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def calendar_known(d):
    return d.year in CALENDAR_YEARS


def bars_expected(d):
    """Does NSE publish a daily bar for d? Every regular trading day, and a Muhurat session too."""
    return trading_day(d)[0] or d.isoformat() in MUHURAT_SESSIONS


apply_calendar()   # at import, from trader/data/calendar.json in the working directory when it exists
# --- end nse calendar ---
