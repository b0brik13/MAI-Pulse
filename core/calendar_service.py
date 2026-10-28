"""Convert ICS feeds (including recurring lectures) into local schedule items."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import hashlib

from core.config import DEFAULT_ICS_URL

MOSCOW = ZoneInfo('Europe/Moscow')


def _local_dt(dt):
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return datetime(dt.year, dt.month, dt.day)
    if dt.tzinfo is not None:
        return dt.astimezone(MOSCOW).replace(tzinfo=None)
    return dt


def parse_ics(raw: bytes, start: date, end: date):
    from icalendar import Calendar
    import recurring_ical_events

    calendar = Calendar.from_ical(raw)
    events = recurring_ical_events.of(calendar).between(start, end)
    items = []
    used = set()
    for ev in events:
        try:
            st = _local_dt(ev.decoded('DTSTART'))
            en = _local_dt(ev.decoded('DTEND')) if ev.get('DTEND') else st + timedelta(minutes=90)
            if not (start <= st.date() < end):
                continue
            title = str(ev.get('SUMMARY', 'Без названия'))
            location = str(ev.get('LOCATION', ''))
            description = str(ev.get('DESCRIPTION', ''))
            uid = str(ev.get('UID', title))
            unique = hashlib.sha1(f'{uid}|{st.isoformat()}'.encode()).hexdigest()
            if unique in used:
                continue
            used.add(unique)
            teacher = description.splitlines()[0][:120] if description else ''
            items.append((f'ics-{unique}', title, location, teacher,
                          st.isoformat(timespec='seconds'), en.isoformat(timespec='seconds')))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(items, key=lambda row: row[4])


def sync_calendar(store, url, group_code=None):
    """Return import count. DB remains unchanged on failures."""
    import requests
    from urllib.parse import urlsplit
    parts = urlsplit(url.strip())
    if parts.scheme not in ('https', 'http') or not parts.netloc:
        raise ValueError('Введите полную ссылку http(s) на ICS-календарь.')
    response = requests.get(url, timeout=18, headers={'User-Agent': 'MAI-Pulse/1.0'})
    response.raise_for_status()
    if len(response.content) > 6_000_000:
        raise ValueError('Файл расписания слишком большой (более 6 МБ).')
    today = date.today()
    # Includes past classes for attendance, and upcoming term for planning.
    items = parse_ics(response.content, today-timedelta(days=45), today+timedelta(days=185))
    if not items:
        raise ValueError('События в календаре не найдены. Проверьте ICS-ссылку.')
    store.replace_ics_classes(items, group_code=group_code)
    if group_code is None:
        store.set('ics_url', url)
    return len(items)


def needs_auto_sync(store, now=None, group_code=None):
    """Download on first run, then at most once daily when reopening the app."""
    if group_code is not None:
        group_code = store.normalize_group(group_code)
        rows = store.query('SELECT ics_url,last_sync_at FROM group_schedules WHERE group_code=?',(group_code,))
        if not rows or not rows[0]['ics_url'].strip():
            return False
        last=(rows[0]['last_sync_at'] or '').strip()
    else:
        if not store.get('ics_url').strip():
            return False
        last = store.get('last_sync_at').strip()
    if not last:
        return True
    try:
        last_date = datetime.fromisoformat(last).date()
    except ValueError:
        return True
    return last_date < (now or date.today())
