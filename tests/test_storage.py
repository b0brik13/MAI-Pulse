import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import date, timedelta
from core.storage import Store
from core.config import DEFAULT_ICS_URL
from core.calendar_service import needs_auto_sync


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name)/'test.db'
        self.db = Store(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_seed_only_first_start(self):
        first = len(self.db.tasks())
        Store(self.db_path)
        self.assertEqual(len(self.db.tasks()), first)

    def test_default_ics_feed_is_preconfigured(self):
        self.assertEqual(self.db.get('ics_url'), DEFAULT_ICS_URL)
        self.assertTrue(needs_auto_sync(self.db))
        self.assertFalse(any(row['source'] == 'demo' for row in self.db.classes('2026-01-01', '2027-12-31')))

    def test_upgrading_old_install_adds_schedule_without_losing_data(self):
        # Simulate an existing installation, before MAI's supplied ICS was added.
        with self.db.connect() as db:
            db.execute("DELETE FROM settings WHERE key='ics_default_v2_applied'")
            db.execute("UPDATE settings SET value='' WHERE key='ics_url'")
        self.db.save_task('Сохранить меня', 'Матан', '2026-11-10', 'Обычный', '')
        migrated = Store(self.db_path)
        self.assertEqual(migrated.get('ics_url'), DEFAULT_ICS_URL)
        self.assertTrue(any(row['title']=='Сохранить меня' for row in migrated.tasks()))

    def test_custom_schedule_not_overwritten_or_restored_after_clear(self):
        self.db.set('ics_url', 'https://example.com/custom.ics')
        self.assertEqual(Store(self.db_path).get('ics_url'), 'https://example.com/custom.ics')
        self.db.set('ics_url', '')
        self.assertEqual(Store(self.db_path).get('ics_url'), '')
        self.assertFalse(needs_auto_sync(self.db))

    def test_auto_sync_daily_refresh(self):
        self.db.set('last_sync_at', '2026-10-28T09:00:00')
        self.assertFalse(needs_auto_sync(self.db, date(2026,10,28)))
        self.assertTrue(needs_auto_sync(self.db, date(2026,10,29)))

    def test_tasks_crud_and_persistence(self):
        day = (date.today() + timedelta(days=3)).isoformat()
        self.db.save_task('Демо тест', 'Алгебра', day, 'Высокий', 'Проверка')
        task = next(x for x in self.db.tasks() if x['title']=='Демо тест')
        self.db.update_task(task['id'], True)
        self.assertEqual(next(x for x in Store(self.db_path).tasks() if x['id']==task['id'])['done'], 1)
        self.db.save_task('Переименована', 'Алгебра', day, 'Низкий', '', task['id'])
        self.assertEqual(next(x for x in self.db.tasks() if x['id']==task['id'])['title'], 'Переименована')
        self.db.delete_task(task['id'])
        self.assertFalse(any(x['id']==task['id'] for x in self.db.tasks()))

    def test_old_schema_upgrade(self):
        second = self.db_path.parent/'old.db'
        cx = sqlite3.connect(second)
        try:
            cx.execute('CREATE TABLE tasks(id INTEGER PRIMARY KEY, title TEXT, subject TEXT, due_date TEXT, done INTEGER)')
            cx.execute("INSERT INTO tasks(title,subject,due_date,done) VALUES('Старое','Матан','2026-11-05',0)")
            cx.commit()
        finally:
            cx.close()
        migrated = Store(second)
        task = next(x for x in migrated.tasks() if x['title']=='Старое')
        self.assertEqual(task['priority'], 'Обычный')
        self.assertEqual(task['demo'], 0)

    def test_calendar_import_preserves_manual(self):
        self.db.add_class('Ручная', '2026-11-01T09:00:00', '2026-11-01T10:30:00','10','')
        payload = [('ics-1','Физика','301','','2026-11-02T09:00:00','2026-11-02T10:30:00')]
        self.db.replace_ics_classes(payload)
        all_rows = self.db.classes('2026-10-01T00','2026-12-01T00')
        self.assertTrue(any(row['source']=='manual' for row in all_rows))
        self.assertEqual(sum(row['source']=='ics' for row in all_rows), 1)
        self.db.replace_ics_classes(payload)
        self.assertEqual(sum(row['source']=='ics' for row in self.db.classes('2026-10-01T00','2026-12-01T00')), 1)

    def test_posts_attendance_materials(self):
        admin = self.db.create_initial_admin('root', 'Администратор', 'SecretPassword1!')
        self.db.assign_group(admin, 'М8О-101')
        self.db.add_post('Тест пост', 'Вопрос', actor_id=admin)
        p = next(p for p in self.db.posts() if p['content']=='Тест пост')
        self.db.like_post(p['id'], actor_id=admin)
        self.assertEqual(next(v for v in self.db.posts() if v['id']==p['id'])['likes'], 1)
        self.db.mark_attendance('test-123', 'Был')
        self.assertEqual(self.db.attendance()['test-123'], 'Был')
        self.db.add_resource('Задачи','Матан','https://example.com','Учебник')
        self.assertTrue(any(r['title']=='Задачи' for r in self.db.resources()))
        self.db.clear_demo()
        self.assertTrue(any(r['title']=='Задачи' for r in self.db.resources()))


if __name__ == '__main__':
    unittest.main()
