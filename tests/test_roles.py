"""Authorization regression tests: slots, isolation, migration and deletion scope."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from core.storage import Store


class RoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'data.db'
        self.store = Store(self.path)
        self.admin = self.store.create_initial_admin('owner', 'Владелец', 'StrongAdmin123!')
        self.student = self.store.create_user(self.admin, 'student1', 'Первокурсник', 'MyPassword123')
        self.mod = self.store.create_moderator_slot(self.admin, 'mod_a', 'Модератор А', 'ModeratorPass123')
        self.mod2 = self.store.create_moderator_slot(self.admin, 'mod_b', 'Модератор Б', 'DifferentPass321')
        self.store.assign_group(self.admin, 'М8О-101')
        self.store.assign_group(self.student, 'М8О-101')

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, actor, scope, text):
        self.store.add_post(text, 'Сообщество', scope=scope, actor_id=actor)
        return next(p['id'] for p in self.store.posts(scope) if p['content'] == text)

    def test_separate_passwords_for_slots(self):
        self.assertEqual(self.store.authenticate('mod_a', 'ModeratorPass123')['id'], self.mod)
        self.assertEqual(self.store.authenticate('MOD_B', 'DifferentPass321')['id'], self.mod2)
        self.assertIsNone(self.store.authenticate('mod_b', 'ModeratorPass123'))
        self.assertIsNone(self.store.authenticate('mod_a', 'DifferentPass321'))
        self.assertEqual(len(self.store.moderator_slots(self.admin)), 2)
        with self.store.connect() as db:
            hashes = [r[0] for r in db.execute("SELECT password_hash FROM users WHERE role='moderator'")]
        self.assertTrue(all('Pass' not in value for value in hashes))
        self.assertNotEqual(hashes[0], hashes[1])

    def test_only_admin_can_create_slots(self):
        for actor in [self.student, self.mod]:
            with self.assertRaises(PermissionError):
                self.store.create_moderator_slot(actor, 'newmod', 'Test', 'SeparatePass123')
            with self.assertRaises(PermissionError):
                self.store.moderator_slots(actor)
            with self.assertRaises(PermissionError):
                self.store.set_moderator_active(actor, self.mod2, False)
            with self.assertRaises(PermissionError):
                self.store.reset_moderator_password(actor, self.mod2, 'AnotherPass123')

    def test_moderator_only_university_delete(self):
        group = self.post(self.student, 'group', 'Групповой пост')
        university = self.post(self.student, 'university', 'Общий пост')
        self.assertFalse(self.store.can_delete_post(group, self.mod))
        self.assertTrue(self.store.can_delete_post(university, self.mod))
        with self.assertRaises(PermissionError):
            self.store.delete_post(group, actor_id=self.mod)
        self.store.delete_post(university, actor_id=self.mod)
        self.assertFalse(any(p['id'] == university for p in self.store.posts('university')))
        self.assertTrue(any(p['id'] == group for p in self.store.posts('group')))

    def test_student_only_own_posts(self):
        student_post = self.post(self.student, 'university', 'Студент написал')
        admin_post = self.post(self.admin, 'university', 'Админ написал')
        self.assertTrue(self.store.can_delete_post(student_post, self.student))
        self.assertFalse(self.store.can_delete_post(admin_post, self.student))
        with self.assertRaises(PermissionError):
            self.store.delete_post(admin_post, actor_id=self.student)
        self.store.delete_post(student_post, actor_id=self.student)
        self.assertTrue(self.store.can_delete_post(admin_post, self.admin))

    def test_moderator_cannot_write_to_group(self):
        with self.assertRaises(PermissionError):
            self.post(self.mod, 'group', 'Запрещено')
        grp = self.post(self.student, 'group', 'В группе')
        with self.assertRaises(PermissionError):
            self.store.add_comment(grp, 'Запрещённый комментарий', actor_id=self.mod)
        with self.assertRaises(PermissionError):
            self.store.like_post(grp, actor_id=self.mod)
        campus = self.post(self.mod, 'university', 'В общей ленте')
        self.store.add_comment(campus, 'Разрешено', actor_id=self.mod)
        self.store.like_post(campus, actor_id=self.mod)

    def test_block_and_reset_moderator_password(self):
        self.store.set_moderator_active(self.admin, self.mod, False)
        self.assertIsNone(self.store.authenticate('mod_a', 'ModeratorPass123'))
        campus = self.post(self.student, 'university', 'Общий пост')
        with self.assertRaises(PermissionError):
            self.store.delete_post(campus, actor_id=self.mod)
        self.store.reset_moderator_password(self.admin, self.mod, 'BrandNewPassABC')
        self.store.set_moderator_active(self.admin, self.mod, True)
        self.assertIsNone(self.store.authenticate('mod_a', 'ModeratorPass123'))
        self.assertIsNotNone(self.store.authenticate('mod_a', 'BrandNewPassABC'))

    def test_survives_restart_and_keeps_legacy_posts(self):
        campus = self.post(self.student, 'university', 'Сохраняем')
        reopened = Store(self.path)
        self.assertEqual(reopened.authenticate('mod_a', 'ModeratorPass123')['id'], self.mod)
        self.assertTrue(reopened.can_delete_post(campus, self.mod))
        self.assertEqual(reopened.user(self.admin)['role'], 'admin')

    def test_legacy_user_migration(self):
        with self.store.connect() as db:
            db.execute("UPDATE posts SET author_id=NULL WHERE demo=1")
            # Earlier v0.5 stores may lack active and creator columns.
            db.execute("ALTER TABLE users RENAME TO users_old")
            db.execute("""CREATE TABLE users(id INTEGER PRIMARY KEY, login TEXT UNIQUE, display_name TEXT,
                         role TEXT, password_hash TEXT, created_at TEXT)""")
            db.execute("""INSERT INTO users(id,login,display_name,role,password_hash,created_at)
                           SELECT id,login,display_name,role,password_hash,created_at FROM users_old""")
            db.execute('DROP TABLE users_old')
        restarted = Store(self.path)
        self.assertEqual(restarted.authenticate('mod_a','ModeratorPass123')['role'], 'moderator')
        self.assertEqual(restarted.user(self.mod)['is_active'], 1)


if __name__ == '__main__':
    unittest.main()
