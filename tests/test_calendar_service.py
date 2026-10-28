import unittest
from datetime import date, datetime
from unittest.mock import patch, Mock
from core.storage import Store
from core.calendar_service import parse_ics, sync_calendar
import tempfile
from pathlib import Path

ICS = b'''BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//MAI Pulse//Test//RU\r\nBEGIN:VEVENT\r\nUID:math@example.org\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:20261026T090000\r\nDTEND:20261026T103000\r\nRRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:Mathematics\r\nLOCATION:301\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'''


class CalendarTests(unittest.TestCase):
    def test_ics_recurrence(self):
        try:
            import icalendar  # noqa
            import recurring_ical_events  # noqa
        except ImportError:
            self.skipTest('Install calendar dependencies to test parser')
        rows = parse_ics(ICS, date(2026, 10, 26), date(2026, 11, 24))
        self.assertEqual(len(rows), 4)
        self.assertEqual(len(set(row[0] for row in rows)), 4)
        self.assertEqual(rows[0][1], 'Mathematics')


if __name__ == '__main__':
    unittest.main()
