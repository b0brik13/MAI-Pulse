"""Group isolation, onboarding, role requests, moderator deletion, old db migration."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from core.storage import Store
from core.calendar_service import needs_auto_sync


class GroupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'pulse.db'
        self.db=Store(self.path)
        self.admin=self.db.create_initial_admin('boss','Админ','AdminPass123!')
        self.a=self.db.register_student('first','Студент 1','Password123!')
        self.b=self.db.register_student('second','Студент 2','Password123!')
        self.c=self.db.register_student('third','Студент 3','Password123!')
        self.db.assign_group(self.admin,'М8О-101')
        self.db.assign_group(self.a,'М8О-101')
        self.db.assign_group(self.b,'М8О-102')
        self.db.assign_group(self.c,'М8О-101')

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_login_group_and_no_self_switch(self):
        x=self.db.register_student('fresh','Новичок','Password123!')
        self.assertIsNone(self.db.authenticate('fresh','Password123!')['group_code'])
        with self.assertRaises(PermissionError):
            self.db.tasks(actor_id=x)
        self.db.assign_group(x,'м8о-103')
        self.assertEqual(self.db.user(x)['group_code'],'М8О-103')
        with self.assertRaises(PermissionError):
            self.db.assign_group(x,'М8О-102')
        self.db.assign_group(self.admin,'М8О-102',target_id=x,admin_override=True)
        self.assertEqual(self.db.user(x)['group_code'],'М8О-102')
        with self.assertRaises(PermissionError):
            self.db.assign_group(self.a,'М8О-101',target_id=x)

    def test_requests_approve_and_reject(self):
        rid=self.db.create_role_request(self.a,'headman','Хочу вести домашку')
        with self.assertRaises(ValueError):
            self.db.create_role_request(self.a,'moderator','Ещё одна заявка')
        with self.assertRaises(PermissionError):
            self.db.review_role_request(self.b,rid,True)
        self.db.review_role_request(self.admin,rid,True)
        self.assertEqual(self.db.user(self.a)['role'],'headman')
        self.assertEqual(self.db.role_requests(self.admin)[0]['status'],'approved')
        with self.assertRaises(ValueError):
            self.db.review_role_request(self.admin,rid,False)
        rid2=self.db.create_role_request(self.c,'moderator','Следить за постами')
        self.db.review_role_request(self.admin,rid2,False)
        self.assertEqual(self.db.user(self.c)['role'],'student')
        self.assertEqual(self.db.role_requests(self.c)[0]['status'],'rejected')
        rid3=self.db.create_role_request(self.c,'moderator','Повторная заявка')
        self.db.review_role_request(self.admin,rid3,True)
        self.assertEqual(self.db.user(self.c)['role'],'moderator')
        with self.assertRaises(PermissionError):
            self.db.tasks(actor_id=self.c)

    def test_group_editor_isolated_from_other_groups(self):
        self.db.assign_group_role(self.admin,self.a,'headman')
        self.db.assign_group_role(self.admin,self.b,'group_moderator')
        self.db.add_post('Пост группы 101','Объявление','group',actor_id=self.a)
        pa=self.db.posts('group',actor_id=self.a)[0]['id']
        self.db.add_post('Пост группы 102','Объявление','group',actor_id=self.b)
        pb=self.db.posts('group',actor_id=self.b)[0]['id']
        self.assertIn(pa,[p['id'] for p in self.db.posts('group',actor_id=self.a)])
        self.assertEqual([p['id'] for p in self.db.posts('group',actor_id=self.b)],[pb])
        self.assertNotIn(pb,[p['id'] for p in self.db.posts('group',actor_id=self.a)])
        self.assertFalse(self.db.can_delete_post(pb,self.a))
        with self.assertRaises(PermissionError):
            self.db.delete_post(pb,actor_id=self.a)
        self.assertTrue(self.db.can_delete_post(pa,self.a))
        self.db.edit_post(pa,'Отредактировано',actor_id=self.a)
        self.assertEqual(self.db.posts('group',actor_id=self.a)[0]['content'],'Отредактировано')
        with self.assertRaises(PermissionError):
            self.db.edit_post(pa,'Нельзя',actor_id=self.b)
        with self.assertRaises(PermissionError):
            self.db.add_comment(pa,'Чужая группа',actor_id=self.b)
        with self.assertRaises(PermissionError):
            self.db.like_post(pa,actor_id=self.b)
        self.db.delete_post(pb,actor_id=self.b)
        self.assertTrue(self.db.posts('group',actor_id=self.a))

    def test_group_tasks_permissions_and_personal_progress(self):
        self.db.assign_group_role(self.admin,self.a,'headman')
        self.db.save_task('Общая ДЗ','Физика','2026-12-01','Высокий','',actor_id=self.a)
        task=next(x for x in self.db.tasks(actor_id=self.a) if x['title']=='Общая ДЗ')
        tid=task['id']
        self.assertFalse(any(x['id']==tid for x in self.db.tasks(actor_id=self.b)))
        self.assertTrue(any(x['id']==tid for x in self.db.tasks(actor_id=self.c)))
        self.db.update_task(tid,True,actor_id=self.c)
        self.assertFalse(next(x for x in self.db.tasks(actor_id=self.a) if x['id']==tid)['done'])
        self.assertTrue(next(x for x in self.db.tasks(actor_id=self.c) if x['id']==tid)['done'])
        with self.assertRaises(PermissionError):
            self.db.delete_task(tid,actor_id=self.c)
        with self.assertRaises(PermissionError):
            self.db.delete_task(tid,actor_id=self.b)
        self.db.save_task('Личное ДЗ','История','2026-12-01','Обычный','',actor_id=self.c)
        self.assertFalse(any(t['title']=='Личное ДЗ' for t in self.db.tasks(actor_id=self.a)))

    def test_group_classes_files_and_attendance(self):
        self.db.assign_group_role(self.admin,self.a,'group_moderator')
        self.db.add_class('Теория','2026-12-01T09:00:00','2026-12-01T10:30:00','10','',actor_id=self.a)
        lesson=next(c for c in self.db.classes('2026-12-01','2026-12-02',actor_id=self.a) if c['title']=='Теория')
        self.assertFalse(any(c['uid']==lesson['uid'] for c in self.db.classes('2026-12-01','2026-12-02',actor_id=self.b)))
        with self.assertRaises(PermissionError):
            self.db.add_class('Физика','2026-12-01T09:00:00','2026-12-01T10:30:00','10','',actor_id=self.c)
        with self.assertRaises(PermissionError):
            self.db.delete_class(lesson['uid'],actor_id=self.b)
        self.db.mark_attendance(lesson['uid'],'Был',actor_id=self.a)
        self.assertEqual(self.db.attendance(actor_id=self.a)[lesson['uid']],'Был')
        self.assertNotIn(lesson['uid'],self.db.attendance(actor_id=self.c))
        with self.assertRaises(PermissionError):
            self.db.mark_attendance(lesson['uid'],'Был',actor_id=self.b)
        self.db.add_resource('Учебник','Матан','','','',actor_id=self.a)
        res=self.db.resources(actor_id=self.a)[0]
        self.assertFalse(self.db.resources(actor_id=self.b))
        with self.assertRaises(PermissionError):
            self.db.delete_resource(res['id'],actor_id=self.b)
        self.db.delete_resource(res['id'],actor_id=self.a)

    def test_group_schedule_independent(self):
        self.db.assign_group_role(self.admin,self.a,'headman')
        self.db.assign_group_role(self.admin,self.b,'headman')
        self.db.set_group_ics(self.a,'https://example.com/101.ics')
        self.db.set_group_ics(self.b,'https://example.com/102.ics')
        self.assertNotEqual(self.db.group_schedule(self.a)['ics_url'],self.db.group_schedule(self.b)['ics_url'])
        with self.assertRaises(PermissionError):
            self.db.set_group_ics(self.c,'https://example.com/nope.ics')
        payload=[('calendar-uid','Алгебра','301','','2026-12-01T09:00:00','2026-12-01T10:30:00')]
        self.db.replace_ics_classes(payload,'М8О-101')
        self.db.replace_ics_classes(payload,'М8О-102')
        one=self.db.classes('2026-12-01','2026-12-02',actor_id=self.a)
        two=self.db.classes('2026-12-01','2026-12-02',actor_id=self.b)
        self.assertEqual(len(one),1)
        self.assertEqual(len(two),1)
        self.assertNotEqual(one[0]['uid'],two[0]['uid'])
        self.assertFalse(needs_auto_sync(self.db,group_code='М8О-101'))
        self.db.replace_ics_classes(payload,'М8О-102')
        self.assertEqual(len(self.db.classes('2026-12-01','2026-12-02',actor_id=self.a)),1)

    def test_admin_deletes_only_moderator_preserving_posts(self):
        mod=self.db.create_moderator_slot(self.admin,'moda','Мод А','ModeratorPass123')
        self.db.add_post('Пост модератора','Фото','university',actor_id=mod)
        pid=self.db.posts('university')[0]['id']
        with self.assertRaises(PermissionError):
            self.db.delete_moderator(self.a,mod)
        with self.assertRaises(ValueError):
            self.db.delete_moderator(self.admin,self.a)
        self.db.delete_moderator(self.admin,mod)
        self.assertIsNone(self.db.authenticate('moda','ModeratorPass123'))
        self.assertIsNone(self.db.posts('university')[0]['author_id'])
        self.assertEqual(self.db.posts('university')[0]['id'],pid)
        with self.assertRaises(PermissionError):
            self.db.delete_post(pid,actor_id=mod)

    def test_legacy_db_users_constraint_is_upgraded(self):
        # Recreate the exact v0.6 constraint and ensure posts still reference users.
        with self.db.connect() as db:
            db.execute('PRAGMA foreign_keys=OFF')
        # Use an independent connection because foreign_keys must be toggled outside TX.
        db=sqlite3.connect(self.path)
        db.execute('PRAGMA foreign_keys=OFF')
        try:
            db.executescript("""
            CREATE TABLE old_users(id INTEGER PRIMARY KEY,login TEXT UNIQUE,display_name TEXT,role TEXT,
              password_hash TEXT,created_at TEXT,is_active INTEGER DEFAULT 1,created_by INTEGER,group_code TEXT);
            INSERT INTO old_users(id,login,display_name,role,password_hash,created_at,is_active,created_by,group_code) SELECT id,login,display_name,role,password_hash,created_at,is_active,created_by,group_code FROM users;
            DROP TABLE users;
            CREATE TABLE users (id INTEGER PRIMARY KEY,login TEXT UNIQUE,display_name TEXT,
                role TEXT NOT NULL CHECK(role IN ('student','moderator','admin')),
                password_hash TEXT,created_at TEXT,is_active INTEGER DEFAULT 1,created_by INTEGER,group_code TEXT);
            INSERT INTO users(id,login,display_name,role,password_hash,created_at,is_active,created_by,group_code) SELECT id,login,display_name,role,password_hash,created_at,is_active,created_by,group_code FROM old_users;
            DROP TABLE old_users;
            """)
            db.commit()
        finally:
            db.close()
        reopened=Store(self.path)
        reopened.assign_group_role(self.admin,self.a,'headman')
        self.assertEqual(reopened.user(self.a)['role'],'headman')
        with reopened.connect() as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

if __name__=='__main__':
    unittest.main()
