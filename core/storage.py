"""SQLite-backed local persistence for MAI Pulse. No external server required."""
from __future__ import annotations

import sqlite3
import hashlib
import hmac
import secrets
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from core.config import DEFAULT_ICS_URL

ROLES = ('student', 'moderator', 'headman', 'group_moderator', 'admin')
ROLE_NAMES = {'student': 'Студент', 'moderator': 'Модератор всего МАИ', 'headman': 'Староста', 'group_moderator': 'Модератор группы', 'admin': 'Администратор'}
GROUP_EDITORS = {'headman', 'group_moderator', 'admin'}
LEGACY = '__legacy__'
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "mai_pulse.db"


class Store:
    def __init__(self, path=DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()
        self._upgrade_role_constraint()
        self._migrate_group_schema()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA busy_timeout = 3000")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self):
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '',
                    due_date TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
                    priority TEXT NOT NULL DEFAULT 'Обычный',
                    details TEXT NOT NULL DEFAULT '', demo INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS classes(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL, location TEXT NOT NULL DEFAULT '',
                    teacher TEXT NOT NULL DEFAULT '', start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'manual');
                CREATE TABLE IF NOT EXISTS posts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    author TEXT NOT NULL, content TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'Объявление',
                    created_at TEXT NOT NULL, likes INTEGER NOT NULL DEFAULT 0,
                    demo INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS attendance(
                    class_uid TEXT PRIMARY KEY, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS resources(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL, subject TEXT NOT NULL,
                    url TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
                    demo INTEGER NOT NULL DEFAULT 0);
            """)
            # Add multimedia fields and comments without touching existing records.
            for table, additions in {
                'posts': [('scope', "TEXT NOT NULL DEFAULT 'group'"),
                          ('image_path', "TEXT NOT NULL DEFAULT ''")],
                'resources': [('file_path', "TEXT NOT NULL DEFAULT ''"),
                              ('file_name', "TEXT NOT NULL DEFAULT ''"),
                              ('file_size', 'INTEGER NOT NULL DEFAULT 0')],
            }.items():
                columns = {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
                for name, ddl in additions:
                    if name not in columns:
                        db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}')
            db.execute('''CREATE TABLE IF NOT EXISTS post_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                author TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
            )''')
            # Local users and posts attribution (v0.5). Older posts remain intact.
            db.execute('''CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                login TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )''')
            user_columns = {r['name'] for r in db.execute('PRAGMA table_info(users)')}
            if 'is_active' not in user_columns:
                db.execute('ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1')
            if 'created_by' not in user_columns:
                db.execute('ALTER TABLE users ADD COLUMN created_by INTEGER')
            post_columns = {r['name'] for r in db.execute('PRAGMA table_info(posts)')}
            if 'author_id' not in post_columns:
                db.execute('ALTER TABLE posts ADD COLUMN author_id INTEGER REFERENCES users(id)')
            db.execute('CREATE INDEX IF NOT EXISTS idx_posts_author_id ON posts(author_id)')
            # Preserve databases created by earlier MAI Pulse versions.
            existing = {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}
            for name, ddl in [('priority', "TEXT NOT NULL DEFAULT 'Обычный'"),
                              ('details', "TEXT NOT NULL DEFAULT ''"),
                              ('demo', 'INTEGER NOT NULL DEFAULT 0')]:
                if name not in existing:
                    db.execute(f'ALTER TABLE tasks ADD COLUMN {name} {ddl}')
            row = db.execute("SELECT value FROM settings WHERE key='initialized'").fetchone()
            if not row:
                self._seed(db)
                db.execute("INSERT INTO settings VALUES('initialized','1')")
            # Apply the supplied group ICS URL exactly once, including on older installs.
            # Never replace an already configured calendar or restore a URL the user cleared.
            applied = db.execute("SELECT 1 FROM settings WHERE key='ics_default_v2_applied'").fetchone()
            if not applied:
                current = db.execute("SELECT value FROM settings WHERE key='ics_url'").fetchone()
                if current is None:
                    db.execute("INSERT INTO settings(key,value) VALUES('ics_url',?)", (DEFAULT_ICS_URL,))
                    installed_default = True
                elif not current['value'].strip():
                    db.execute("UPDATE settings SET value=? WHERE key='ics_url'", (DEFAULT_ICS_URL,))
                    installed_default = True
                else:
                    installed_default = False
                if installed_default:
                    # A connected calendar must not display fake sample lessons.
                    db.execute("DELETE FROM classes WHERE source='demo'")
                db.execute("INSERT INTO settings(key,value) VALUES('ics_default_v2_applied','1')")

    @staticmethod
    def _normalize_login(login):
        login = (login or '').strip().casefold()
        if not (3 <= len(login) <= 32) or not all(ch.isalnum() or ch in '_-.' for ch in login):
            raise ValueError('Логин: 3–32 символа, буквы, цифры, точка, дефис или _.')
        return login

    @staticmethod
    def _make_hash(password):
        if len(password or '') < 8:
            raise ValueError('Пароль должен содержать минимум 8 символов.')
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 260_000)
        return f'pbkdf2_sha256$260000${salt.hex()}${digest.hex()}'

    @staticmethod
    def _check_hash(password, stored):
        try:
            algorithm, iterations, salt, digest = stored.split('$')
            if algorithm != 'pbkdf2_sha256':
                return False
            candidate = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                                             bytes.fromhex(salt), int(iterations))
            return hmac.compare_digest(candidate, bytes.fromhex(digest))
        except (ValueError, TypeError, AttributeError):
            return False

    def has_users(self):
        return bool(self.query('SELECT id FROM users LIMIT 1'))

    def create_initial_admin(self, login, display_name, password):
        """One-time local onboarding; existing posts belong to the original owner."""
        login = self._normalize_login(login)
        display_name = (display_name or '').strip()[:80]
        if not display_name:
            raise ValueError('Введи имя для отображения.')
        password_hash = self._make_hash(password)
        with self.connect() as db:
            if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                raise PermissionError('Администратор уже создан.')
            cursor = db.execute('''INSERT INTO users(login,display_name,role,password_hash,created_at)
                                   VALUES(?,?,?,?,?)''',
                                (login, display_name, 'admin', password_hash,
                                 datetime.now().isoformat(timespec='seconds')))
            # The prior versions had one local owner and no user IDs.
            original = db.execute("SELECT value FROM settings WHERE key='name'").fetchone()
            legacy_name = original['value'] if original else 'Студент МАИ'
            db.execute('UPDATE posts SET author_id=? WHERE author_id IS NULL AND author=? AND demo=0',
                       (cursor.lastrowid, legacy_name))
            db.execute("UPDATE settings SET value=? WHERE key='name'", (display_name,))
            return cursor.lastrowid

    def authenticate(self, login, password):
        try:
            login = self._normalize_login(login)
        except ValueError:
            return None
        with self.connect() as db:
            row = db.execute('SELECT * FROM users WHERE login=?', (login,)).fetchone()
            if not row or not row['is_active'] or not self._check_hash(password or '', row['password_hash']):
                return None
            return {key: row[key] for key in ('id','login','display_name','role','is_active','group_code')}

    @staticmethod
    def _actor(db, actor_id):
        row = db.execute('SELECT id, login, display_name, role, is_active, group_code FROM users WHERE id=?',
                         (actor_id,)).fetchone()
        if not row or not row['is_active']:
            raise PermissionError('Учётная запись неактивна. Выполни вход снова.')
        return row

    def user(self, user_id):
        rows = self.query('SELECT id, login, display_name, role, is_active, group_code FROM users WHERE id=?', (user_id,))
        return rows[0] if rows else None

    def users(self):
        return self.query('''SELECT id, login, display_name, role, is_active, group_code FROM users
                             ORDER BY CASE role WHEN 'admin' THEN 0 WHEN 'moderator' THEN 1 ELSE 2 END, id''')

    def create_user(self, actor_id, login, display_name, password):
        login = self._normalize_login(login)
        display_name = (display_name or '').strip()[:80]
        if not display_name:
            raise ValueError('Введи имя студента.')
        password_hash = self._make_hash(password)
        with self.connect() as db:
            if self._actor(db, actor_id)['role'] != 'admin':
                raise PermissionError('Только администратор может создавать аккаунты.')
            try:
                cursor = db.execute('''INSERT INTO users(login,display_name,role,password_hash,created_at)
                                       VALUES(?,?,?,?,?)''',
                                    (login, display_name, 'student', password_hash,
                                     datetime.now().isoformat(timespec='seconds')))
            except sqlite3.IntegrityError as exc:
                raise ValueError('Такой логин уже занят.') from exc
            return cursor.lastrowid

    def _require_admin(self, db, actor_id):
        actor = self._actor(db, actor_id)
        if actor['role'] != 'admin':
            raise PermissionError('Это действие доступно только администратору.')
        return actor

    def create_moderator_slot(self, actor_id, login, display_name, password):
        # One independent account per slot; passwords are hashed.
        login = self._normalize_login(login)
        display_name = (display_name or '').strip()[:80]
        if not display_name:
            raise ValueError('Укажи имя модератора.')
        hashed = self._make_hash(password)
        with self.connect() as db:
            self._require_admin(db, actor_id)
            try:
                cur = db.execute('''INSERT INTO users(login,display_name,role,password_hash,created_at,created_by)
                                    VALUES(?,?,?,?,?,?)''',
                                 (login, display_name, 'moderator', hashed,
                                  datetime.now().isoformat(timespec='seconds'), actor_id))
            except sqlite3.IntegrityError as exc:
                raise ValueError('Такой логин уже существует.') from exc
            return cur.lastrowid

    def reset_moderator_password(self, actor_id, target_id, new_password):
        hashed = self._make_hash(new_password)
        with self.connect() as db:
            self._require_admin(db, actor_id)
            target = db.execute('SELECT role FROM users WHERE id=?', (target_id,)).fetchone()
            if not target or target['role'] != 'moderator':
                raise PermissionError('Сменить пароль здесь можно только модератору.')
            db.execute('UPDATE users SET password_hash=? WHERE id=?', (hashed, target_id))

    def set_moderator_active(self, actor_id, target_id, enabled):
        with self.connect() as db:
            self._require_admin(db, actor_id)
            # Allow restoring a blocked moderator (cannot use _actor for inactive targets).
            target = db.execute('SELECT role FROM users WHERE id=?', (target_id,)).fetchone()
            if not target or target['role'] != 'moderator':
                raise ValueError('Слот модератора не найден.')
            db.execute('UPDATE users SET is_active=? WHERE id=?', (int(bool(enabled)), target_id))

    def moderator_slots(self, actor_id):
        with self.connect() as db:
            self._require_admin(db, actor_id)
            return [dict(r) for r in db.execute('''SELECT id,login,display_name,role,is_active,group_code,created_at
                                                 FROM users WHERE role='moderator' ORDER BY id''').fetchall()]

    def change_role(self, actor_id, target_id, role):
        if role not in ROLES:
            raise ValueError('Недопустимая роль.')
        with self.connect() as db:
            admin = self._actor(db, actor_id)
            if admin['role'] != 'admin':
                raise PermissionError('Назначать роли может только администратор.')
            target = self._actor(db, target_id)
            if target['id'] == admin['id']:
                raise PermissionError('Нельзя менять роль собственной учётной записи.')
            db.execute('UPDATE users SET role=? WHERE id=?', (role, target_id))

    def rename_user(self, actor_id, display_name):
        display_name = (display_name or '').strip()[:80]
        if not display_name:
            raise ValueError('Имя не может быть пустым.')
        with self.connect() as db:
            self._actor(db, actor_id)
            db.execute('UPDATE users SET display_name=? WHERE id=?', (display_name, actor_id))

    @staticmethod
    def _post_delete_allowed(actor, post):
        if actor['role'] == 'admin':
            return True
        if actor['role'] == 'moderator':
            # A moderator can moderate ONLY the campus-wide feed.
            return post['scope'] == 'university'
        return post['author_id'] is not None and post['author_id'] == actor['id']

    def can_delete_post(self, pid, actor_id):
        with self.connect() as db:
            actor = self._actor(db, actor_id)
            post = db.execute('SELECT author_id, scope FROM posts WHERE id=?', (pid,)).fetchone()
            return bool(post and self._post_delete_allowed(actor, post))

    def _seed(self, db):
        if db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0:
            now = date.today()
            tasks = [
                ('Лабораторная работа №5', 'Физика', 2, 'Высокий', 'Подготовить отчёт'),
                ('Контрольная — матрицы', 'Линейная алгебра', 4, 'Высокий', 'Повторить метод Гаусса'),
                ('Упражнения по английскому', 'Английский язык', 6, 'Обычный', ''),
                ('Подготовить конспект', 'Информатика', 9, 'Низкий', ''),
            ]
            db.executemany('''INSERT INTO tasks(title, subject, due_date, priority, details, demo)
                                  VALUES(?, ?, ?, ?, ?, 1)''',
                           [(title, sub, (now + timedelta(days=days)).isoformat(), pr, detail)
                            for title, sub, days, pr, detail in tasks])
        if db.execute('SELECT COUNT(*) FROM classes').fetchone()[0] == 0:
            monday = date.today() - timedelta(days=date.today().weekday())
            demos = [(0, '09:00', '10:30', 'Математический анализ', '301', 'Лекция'),
                     (0, '10:45', '12:15', 'Физика', '212', 'Практика'),
                     (1, '12:30', '14:00', 'Программирование', '405', 'Лабораторная'),
                     (2, '09:00', '10:30', 'Линейная алгебра', '318', 'Практика'),
                     (3, '10:45', '12:15', 'Английский язык', '225', 'Семинар'),
                     (4, '12:30', '14:00', 'Информатика', '406', 'Лабораторная')]
            for week in range(3):
                for i, (weekday, st, en, name, room, teacher) in enumerate(demos):
                    day = monday + timedelta(days=weekday + week * 7)
                    db.execute('''INSERT OR IGNORE INTO classes(uid,title,location,teacher,start_at,end_at,source)
                                  VALUES(?,?,?,?,?,?,?)''',
                               (f'demo-{week}-{i}', name, room, teacher,
                                f'{day.isoformat()}T{st}:00', f'{day.isoformat()}T{en}:00', 'demo'))
        if db.execute('SELECT COUNT(*) FROM posts').fetchone()[0] == 0:
            db.executemany('''INSERT INTO posts(author,content,category,created_at,likes,demo)
                                  VALUES(?,?,?,?,?,1)''', [
                ('Староста', 'Добро пожаловать в MAI Pulse! Здесь будут объявления вашей группы. Пока это демонстрационные публикации.', 'Объявление', datetime.now().isoformat(timespec='seconds'), 7),
                ('Сообщество МАИ', 'Поделитесь полезными конспектами в разделе «Материалы».', 'Сообщество', datetime.now().isoformat(timespec='seconds'), 3),
            ])
        if db.execute('SELECT COUNT(*) FROM resources').fetchone()[0] == 0:
            db.executemany('''INSERT INTO resources(title,subject,url,description,demo)
                                  VALUES(?,?,?,?,1)''', [
                ('Материалы к лабораторным', 'Физика', '', 'Пример карточки материала: добавьте ссылку на свою методичку'),
                ('Формулы и матрицы', 'Линейная алгебра', '', 'Пример заметки для подготовки к контрольной'),
            ])
        db.execute("INSERT OR IGNORE INTO settings VALUES('name','Студент МАИ')")
        db.execute("INSERT OR IGNORE INTO settings VALUES('group','Моя группа')")
        db.execute("INSERT OR IGNORE INTO settings VALUES('ics_url','')")
        db.execute("INSERT OR IGNORE INTO settings VALUES('theme','dark')")

    def query(self, sql, args=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args).fetchall()]

    def exec(self, sql, args=()):
        with self.connect() as db:
            db.execute(sql, args)

    def get(self, key, default=''):
        rows = self.query('SELECT value FROM settings WHERE key=?', (key,))
        return rows[0]['value'] if rows else default

    def set(self, key, value):
        self.exec('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                  (key, str(value)))

    def tasks(self):
        return self.query('SELECT * FROM tasks ORDER BY done ASC, due_date ASC, id DESC')

    def save_task(self, title, subject, due_date, priority, details, task_id=None):
        date.fromisoformat(due_date)
        if task_id is None:
            self.exec('INSERT INTO tasks(title,subject,due_date,priority,details) VALUES(?,?,?,?,?)',
                      (title, subject, due_date, priority, details))
        else:
            self.exec('UPDATE tasks SET title=?,subject=?,due_date=?,priority=?,details=? WHERE id=?',
                      (title, subject, due_date, priority, details, task_id))

    def update_task(self, task_id, done):
        self.exec('UPDATE tasks SET done=? WHERE id=?', (int(done), task_id))

    def delete_task(self, task_id):
        self.exec('DELETE FROM tasks WHERE id=?', (task_id,))

    def classes(self, start, end):
        return self.query('''SELECT * FROM classes WHERE start_at>=? AND start_at<?
                             ORDER BY start_at''', (start, end))

    def class_count(self):
        return self.query('SELECT COUNT(*) AS n FROM classes')[0]['n']

    def add_class(self, title, start_at, end_at, location, teacher):
        import uuid
        self.exec('INSERT INTO classes(uid,title,start_at,end_at,location,teacher,source) VALUES(?,?,?,?,?,?,?)',
                  (f'manual-{uuid.uuid4().hex}', title, start_at, end_at, location, teacher, 'manual'))

    def delete_class(self, uid):
        self.exec('DELETE FROM classes WHERE uid=?', (uid,))

    def replace_ics_classes(self, rows):
        with self.connect() as db:
            db.execute("DELETE FROM classes WHERE source='ics'")
            db.execute("DELETE FROM classes WHERE source='demo'")
            db.executemany('''INSERT INTO classes(uid,title,location,teacher,start_at,end_at,source)
                              VALUES(?,?,?,?,?,?,'ics')''', rows)
            now = datetime.now()
            db.execute("INSERT INTO settings(key,value) VALUES('last_sync',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (now.strftime('%d.%m.%Y %H:%M'),))
            db.execute("INSERT INTO settings(key,value) VALUES('last_sync_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (now.isoformat(timespec='seconds'),))

    def posts(self, scope=None):
        if scope in ('group', 'university'):
            return self.query('SELECT * FROM posts WHERE scope=? ORDER BY created_at DESC, id DESC', (scope,))
        return self.query('SELECT * FROM posts ORDER BY created_at DESC, id DESC')

    def add_post(self, text, category, scope='group', image_path='', *, actor_id):
        if scope not in ('group', 'university'):
            raise ValueError('Неверный раздел публикации.')
        if not (text or '').strip() and not image_path:
            raise ValueError('Добавь текст или фотографию.')
        with self.connect() as db:
            actor = self._actor(db, actor_id)
            if actor['role'] == 'moderator' and scope != 'university':
                raise PermissionError('Модератор может публиковать только в «Весь МАИ».')
            db.execute('''INSERT INTO posts(author,author_id,content,category,created_at,scope,image_path)
                          VALUES(?,?,?,?,?,?,?)''',
                       (actor['display_name'], actor['id'], text, category,
                        datetime.now().isoformat(timespec='seconds'), scope, image_path))

    def comments(self, post_id):
        return self.query('SELECT * FROM post_comments WHERE post_id=? ORDER BY id', (post_id,))

    def add_comment(self, post_id, body, *, actor_id):
        body = (body or '').strip()
        if not body:
            raise ValueError('Комментарий не может быть пустым.')
        if len(body) > 1000:
            raise ValueError('Комментарий слишком длинный (до 1000 символов).')
        with self.connect() as db:
            actor = self._actor(db, actor_id)
            post = db.execute('SELECT scope FROM posts WHERE id=?', (post_id,)).fetchone()
            if not post:
                raise ValueError('Публикация не найдена.')
            if actor['role'] == 'moderator' and post['scope'] != 'university':
                raise PermissionError('Модераторы могут комментировать только общую ленту.')
            db.execute('''INSERT INTO post_comments(post_id,author,content,created_at)
                          VALUES(?,?,?,?)''',
                       (post_id, actor['display_name'], body,
                        datetime.now().isoformat(timespec='seconds')))

    def like_post(self, pid, *, actor_id):
        with self.connect() as db:
            actor = self._actor(db, actor_id)
            post = db.execute('SELECT scope FROM posts WHERE id=?', (pid,)).fetchone()
            if not post:
                raise ValueError('Публикация не найдена.')
            if actor['role'] == 'moderator' and post['scope'] != 'university':
                raise PermissionError('Модератору доступна только общая лента.')
            db.execute('UPDATE posts SET likes=likes+1 WHERE id=?', (pid,))

    def delete_post(self, pid, *, actor_id):
        """Permission check and DELETE execute within one SQLite transaction."""
        with self.connect() as db:
            actor = self._actor(db, actor_id)
            post = db.execute('SELECT * FROM posts WHERE id=?', (pid,)).fetchone()
            if not post:
                raise ValueError('Публикация уже удалена.')
            if not self._post_delete_allowed(actor, post):
                raise PermissionError('Нет прав для удаления этой публикации.')
            db.execute('DELETE FROM posts WHERE id=?', (pid,))
            return dict(post)

    def attendance(self):
        return {row['class_uid']: row['status'] for row in self.query('SELECT * FROM attendance')}

    def mark_attendance(self, uid, status):
        self.exec('INSERT INTO attendance(class_uid,status) VALUES(?,?) ON CONFLICT(class_uid) DO UPDATE SET status=excluded.status',
                  (uid, status))

    def resources(self):
        return self.query('SELECT * FROM resources ORDER BY id DESC')

    def add_resource(self, title, subject, url, description, file_path='', file_name='', file_size=0):
        self.exec('''INSERT INTO resources(title,subject,url,description,file_path,file_name,file_size)
                     VALUES(?,?,?,?,?,?,?)''',
                  (title, subject, url, description, file_path, file_name, file_size))

    def delete_resource(self, rid):
        self.exec('DELETE FROM resources WHERE id=?', (rid,))


    def _upgrade_role_constraint(self):
        'Upgrade old v0.6 users CHECK constraint so new group roles are valid.'
        db = sqlite3.connect(self.path)
        try:
            db.execute('PRAGMA foreign_keys=OFF')
            sql = db.execute("SELECT sql FROM sqlite_master WHERE name='users' AND type='table'").fetchone()
            if not sql or 'CHECK(role IN' not in (sql[0] or ''):
                return
            db.execute('BEGIN IMMEDIATE')
            columns={r[1] for r in db.execute('PRAGMA table_info(users)')}
            db.execute("CREATE TABLE users_v07 (id INTEGER PRIMARY KEY AUTOINCREMENT, login TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL, role TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1, created_by INTEGER, group_code TEXT)")
            group_select='group_code' if 'group_code' in columns else 'NULL'
            db.execute(f"INSERT INTO users_v07(id,login,display_name,role,password_hash,created_at,is_active,created_by,group_code) SELECT id,login,display_name,role,password_hash,created_at,is_active,created_by,{group_select} FROM users")
            db.execute('DROP TABLE users')
            db.execute('ALTER TABLE users_v07 RENAME TO users')
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ---------- v0.7: groups, first-login onboarding and role requests ----------
    def _migrate_group_schema(self):
        "Idempotent migration that keeps all legacy tasks, posts and media records."
        with self.connect() as db:
            for table, additions in {
                'users': [('group_code', 'TEXT')],
                'tasks': [('group_code', "TEXT NOT NULL DEFAULT '__legacy__'"), ('owner_id', 'INTEGER')],
                'classes': [('group_code', "TEXT NOT NULL DEFAULT '__legacy__'")],
                'posts': [('group_code', "TEXT NOT NULL DEFAULT '__legacy__'")],
                'resources': [('group_code', "TEXT NOT NULL DEFAULT '__legacy__'"), ('owner_id', 'INTEGER')],
            }.items():
                cols = {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
                for col, ddl in additions:
                    if col not in cols:
                        db.execute(f'ALTER TABLE {table} ADD COLUMN {col} {ddl}')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS role_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    requested_role TEXT NOT NULL,
                    group_code TEXT,
                    message TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    reviewed_by INTEGER,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_requests_status ON role_requests(status);
                CREATE TABLE IF NOT EXISTS group_schedules (
                    group_code TEXT PRIMARY KEY,
                    ics_url TEXT NOT NULL DEFAULT '',
                    last_sync TEXT,
                    last_sync_at TEXT
                );
                CREATE TABLE IF NOT EXISTS task_progress (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    done INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(user_id,task_id)
                );
                CREATE TABLE IF NOT EXISTS attendance_user (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    class_uid TEXT NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY(user_id,class_uid)
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_group ON tasks(group_code);
                CREATE INDEX IF NOT EXISTS idx_posts_group ON posts(group_code,scope);
            ''')
            legacy = db.execute("SELECT value FROM settings WHERE key='group'").fetchone()
            name = legacy['value'].strip().upper() if legacy and legacy['value'] else ''
            if name and name not in ('МОЯ ГРУППА', 'Моя группа'.upper()):
                self._bind_legacy_in_db(db, name)
            # A preexisting admin is not forced to choose group if a meaningful legacy group exists.
            if name and name != 'МОЯ ГРУППА':
                db.execute("UPDATE users SET group_code=? WHERE role='admin' AND group_code IS NULL", (name,))

    @staticmethod
    def normalize_group(group):
        group = ' '.join((group or '').strip().upper().split())
        if not (2 <= len(group) <= 40) or not all(x.isalnum() or x in '-_/ ' for x in group):
            raise ValueError('Укажи реальный номер группы (2–40 символов: буквы, цифры, пробел, - / _).')
        return group

    @staticmethod
    def _bind_legacy_in_db(db, code):
        for table in ('tasks','classes','posts','resources'):
            db.execute(f'UPDATE {table} SET group_code=? WHERE group_code=?', (code, LEGACY))
        old_ics = db.execute("SELECT value FROM settings WHERE key='ics_url'").fetchone()
        db.execute('''INSERT OR IGNORE INTO group_schedules(group_code,ics_url,last_sync,last_sync_at)
                      VALUES(?,?,?,?)''',
                   (code, old_ics['value'] if old_ics else '',
                    (db.execute("SELECT value FROM settings WHERE key='last_sync'").fetchone() or {'value':None})['value'],
                    (db.execute("SELECT value FROM settings WHERE key='last_sync_at'").fetchone() or {'value':None})['value']))

    def assign_group(self, actor_id, code, *, admin_override=False, target_id=None):
        code = self.normalize_group(code)
        with self.connect() as db:
            actor = self._actor(db, actor_id)
            target_id = actor_id if target_id is None else target_id
            if target_id != actor_id and actor['role'] != 'admin':
                raise PermissionError('Назначить группу другому может только администратор.')
            target = db.execute('SELECT id,group_code FROM users WHERE id=?', (target_id,)).fetchone()
            if not target:
                raise ValueError('Аккаунт не найден.')
            if target['group_code'] and target['group_code'] != code and not (actor['role']=='admin' and admin_override):
                raise PermissionError('Группа уже назначена. Для смены обратитесь к администратору.')
            # Once: attach legacy coursework to the FIRST actual group (not to all groups).
            if db.execute("SELECT 1 FROM tasks WHERE group_code=? UNION SELECT 1 FROM posts WHERE group_code=? LIMIT 1", (LEGACY, LEGACY)).fetchone() or db.execute("SELECT 1 FROM classes WHERE group_code=? UNION SELECT 1 FROM resources WHERE group_code=? LIMIT 1", (LEGACY, LEGACY)).fetchone():
                self._bind_legacy_in_db(db, code)
            db.execute('UPDATE users SET group_code=? WHERE id=?', (code, target_id))
            db.execute('INSERT OR IGNORE INTO group_schedules(group_code,ics_url) VALUES(?,?)', (code, ''))
            return code

    def list_groups(self):
        return [r['group_code'] for r in self.query('''SELECT DISTINCT group_code FROM users
              WHERE group_code IS NOT NULL AND group_code!='' ORDER BY group_code''')]

    @staticmethod
    def _require_group(db, actor_id, *, write=False):
        actor = Store._actor(db, actor_id)
        if actor['role'] == 'moderator':
            raise PermissionError('Модератор всего МАИ не имеет доступа к данным учебных групп.')
        if not actor['group_code']:
            raise PermissionError('Сначала укажи свою учебную группу.')
        if write and actor['role'] not in GROUP_EDITORS:
            raise PermissionError('Изменять общие данные группы может только староста, модератор группы или администратор.')
        return actor

    def has_group_editor_rights(self, actor_id):
        with self.connect() as db:
            actor = self._actor(db, actor_id)
            return bool(actor['group_code'] and actor['role'] in GROUP_EDITORS)

    def create_role_request(self, actor_id, requested_role, message=''):
        if requested_role not in ('moderator','headman'):
            raise ValueError('Можно подать заявку только на модератора МАИ или старосту.')
        message = (message or '').strip()[:500]
        with self.connect() as db:
            actor = self._actor(db, actor_id)
            if actor['role'] not in ('student','group_moderator','headman'):
                raise PermissionError('Для этой учётной записи подача заявки недоступна.')
            if requested_role=='headman' and not actor['group_code']:
                raise ValueError('Сначала укажи учебную группу.')
            if actor['role']==requested_role:
                raise ValueError('У тебя уже есть эта роль.')
            existing = db.execute('''SELECT id FROM role_requests WHERE user_id=? AND status='pending' ''', (actor_id,)).fetchone()
            if existing:
                raise ValueError('У тебя уже есть заявка на рассмотрении.')
            cur=db.execute('''INSERT INTO role_requests(user_id,requested_role,group_code,message,status,created_at)
                              VALUES(?,?,?,?,'pending',?)''', (actor_id,requested_role,actor['group_code'],message,datetime.now().isoformat(timespec='seconds')))
            return cur.lastrowid

    def role_requests(self, actor_id, pending_only=False):
        with self.connect() as db:
            actor = self._actor(db, actor_id)
            if actor['role']=='admin':
                sql='''SELECT rr.*,u.login,u.display_name FROM role_requests rr JOIN users u ON u.id=rr.user_id'''
                if pending_only:
                    sql += " WHERE rr.status='pending'"
                sql += ' ORDER BY rr.id DESC'
                return [dict(r) for r in db.execute(sql)]
            return [dict(r) for r in db.execute('''SELECT * FROM role_requests WHERE user_id=? ORDER BY id DESC''',(actor_id,))]

    def review_role_request(self, actor_id, request_id, approved):
        with self.connect() as db:
            self._require_admin(db, actor_id)
            req = db.execute("SELECT * FROM role_requests WHERE id=? AND status='pending'", (request_id,)).fetchone()
            if not req:
                raise ValueError('Заявка уже рассмотрена или не найдена.')
            applicant = self._actor(db, req['user_id'])
            if req['requested_role']=='headman':
                if not applicant['group_code'] or applicant['group_code'] != req['group_code']:
                    raise ValueError('Группа заявителя изменилась — сначала уточни его группу.')
            if approved:
                db.execute('UPDATE users SET role=? WHERE id=?', (req['requested_role'], req['user_id']))
            db.execute('''UPDATE role_requests SET status=?,reviewed_by=?,reviewed_at=? WHERE id=?''',
                       ('approved' if approved else 'rejected', actor_id,datetime.now().isoformat(timespec='seconds'),request_id))

    def register_student(self, login, display_name, password):
        login=self._normalize_login(login)
        display_name=(display_name or '').strip()[:80]
        if not display_name:
            raise ValueError('Укажи имя студента.')
        hashed=self._make_hash(password)
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                raise PermissionError('Сначала необходимо создать администратора.')
            try:
                cur=db.execute('INSERT INTO users(login,display_name,role,password_hash,created_at) VALUES(?,?,?,?,?)',
                    (login,display_name,'student',hashed,datetime.now().isoformat(timespec='seconds')))
            except sqlite3.IntegrityError as exc:
                raise ValueError('Логин уже занят.') from exc
            return cur.lastrowid

    def delete_moderator(self, actor_id, target_id):
        "Hard-delete only campus moderator accounts, leave their posts and photos intact."
        with self.connect() as db:
            self._require_admin(db, actor_id)
            user = db.execute('SELECT id,role FROM users WHERE id=?', (target_id,)).fetchone()
            if not user or user['role']!='moderator':
                raise ValueError('Удалить здесь можно только модератора МАИ.')
            db.execute('UPDATE posts SET author_id=NULL WHERE author_id=?', (target_id,))
            db.execute('DELETE FROM role_requests WHERE user_id=?', (target_id,))
            db.execute('DELETE FROM users WHERE id=?', (target_id,))

    def assign_group_role(self, admin_id, target_id, role):
        if role not in ('student','headman','group_moderator'):
            raise ValueError('Разрешено назначать студента, старосту или модератора группы.')
        with self.connect() as db:
            self._require_admin(db, admin_id)
            target = db.execute('SELECT role,group_code FROM users WHERE id=?', (target_id,)).fetchone()
            if not target or target['role'] not in ('student','headman','group_moderator') or not target['group_code']:
                raise ValueError('Выбери студента с назначенной группой.')
            db.execute('UPDATE users SET role=? WHERE id=?', (role,target_id))

    def group_schedule(self, actor_id):
        with self.connect() as db:
            actor = self._require_group(db,actor_id)
            row = db.execute('SELECT * FROM group_schedules WHERE group_code=?',(actor['group_code'],)).fetchone()
            return dict(row) if row else {'ics_url':'','last_sync':'','last_sync_at':''}

    def set_group_ics(self, actor_id, url):
        url=(url or '').strip()
        with self.connect() as db:
            actor=self._require_group(db,actor_id,write=True)
            db.execute('''INSERT INTO group_schedules(group_code,ics_url) VALUES(?,?)
                          ON CONFLICT(group_code) DO UPDATE SET ics_url=excluded.ics_url,
                          last_sync_at=CASE WHEN group_schedules.ics_url!=excluded.ics_url THEN NULL ELSE group_schedules.last_sync_at END,
                          last_sync=CASE WHEN group_schedules.ics_url!=excluded.ics_url THEN NULL ELSE group_schedules.last_sync END''',(actor['group_code'],url))

    def tasks(self, *, actor_id=None):
        if actor_id is None:
            return self.query('SELECT * FROM tasks ORDER BY done,due_date,id DESC')
        with self.connect() as db:
            user=self._require_group(db,actor_id)
            return [dict(r) for r in db.execute('''SELECT t.id,t.title,t.subject,t.due_date,COALESCE(tp.done,t.done) AS done,t.priority,t.details,t.demo,t.group_code,t.owner_id
                FROM tasks t LEFT JOIN task_progress tp ON tp.task_id=t.id AND tp.user_id=?
                WHERE t.group_code=? AND (t.owner_id IS NULL OR t.owner_id=?)
                ORDER BY done,due_date,t.id DESC''',(actor_id,user['group_code'],actor_id))]

    def can_edit_task(self, actor_id, task_id):
        with self.connect() as db:
            actor=self._require_group(db,actor_id)
            task=db.execute('SELECT group_code,owner_id FROM tasks WHERE id=?',(task_id,)).fetchone()
            return bool(task and task['group_code']==actor['group_code'] and
                        (task['owner_id']==actor_id or actor['role'] in GROUP_EDITORS))

    def save_task(self, title, subject, due_date, priority, details, task_id=None, *, actor_id=None):
        date.fromisoformat(due_date)
        if actor_id is None:
            return self._legacy_task_save(title,subject,due_date,priority,details,task_id)
        with self.connect() as db:
            actor=self._require_group(db,actor_id)
            if task_id is None:
                owner=None if actor['role'] in GROUP_EDITORS else actor_id
                db.execute('''INSERT INTO tasks(title,subject,due_date,priority,details,group_code,owner_id)
                              VALUES(?,?,?,?,?,?,?)''',(title,subject,due_date,priority,details,actor['group_code'],owner))
            else:
                task=db.execute('SELECT group_code,owner_id FROM tasks WHERE id=?',(task_id,)).fetchone()
                if not task or task['group_code']!=actor['group_code'] or not (task['owner_id']==actor_id or actor['role'] in GROUP_EDITORS):
                    raise PermissionError('Нельзя менять чужие задания или задания другой группы.')
                db.execute('UPDATE tasks SET title=?,subject=?,due_date=?,priority=?,details=? WHERE id=?',
                           (title,subject,due_date,priority,details,task_id))

    def _legacy_task_save(self,title,subject,due_date,priority,details,task_id):
        if task_id is None:
            self.exec('INSERT INTO tasks(title,subject,due_date,priority,details) VALUES(?,?,?,?,?)',
                      (title,subject,due_date,priority,details))
        else:
            self.exec('UPDATE tasks SET title=?,subject=?,due_date=?,priority=?,details=? WHERE id=?',
                      (title,subject,due_date,priority,details,task_id))

    def update_task(self, task_id, done, *, actor_id=None):
        if actor_id is None:
            return self.exec('UPDATE tasks SET done=? WHERE id=?',(int(done),task_id))
        with self.connect() as db:
            actor=self._require_group(db,actor_id)
            row=db.execute('SELECT group_code,owner_id FROM tasks WHERE id=?',(task_id,)).fetchone()
            if not row or row['group_code']!=actor['group_code'] or (row['owner_id'] is not None and row['owner_id']!=actor_id):
                raise PermissionError('Задание не найдено в твоей группе.')
            db.execute('''INSERT INTO task_progress(user_id,task_id,done) VALUES(?,?,?)
                          ON CONFLICT(user_id,task_id) DO UPDATE SET done=excluded.done''',(actor_id,task_id,int(bool(done))))

    def delete_task(self, task_id, *, actor_id=None):
        if actor_id is None:
            return self.exec('DELETE FROM tasks WHERE id=?',(task_id,))
        with self.connect() as db:
            actor=self._require_group(db,actor_id)
            row=db.execute('SELECT group_code,owner_id FROM tasks WHERE id=?',(task_id,)).fetchone()
            if not row or row['group_code']!=actor['group_code'] or not (row['owner_id']==actor_id or actor['role'] in GROUP_EDITORS):
                raise PermissionError('Только автор личной задачи или руководство группы может удалить это задание.')
            db.execute('DELETE FROM tasks WHERE id=?',(task_id,))

    def classes(self, start, end, *, actor_id=None):
        if actor_id is None:
            return self.query('SELECT * FROM classes WHERE start_at>=? AND start_at<? ORDER BY start_at',(start,end))
        with self.connect() as db:
            actor=self._require_group(db,actor_id)
            return [dict(r) for r in db.execute('''SELECT * FROM classes WHERE start_at>=? AND start_at<?
                       AND group_code=? ORDER BY start_at''',(start,end,actor['group_code']))]

    def add_class(self, title, start_at, end_at, location, teacher, *, actor_id=None):
        import uuid
        if actor_id is None:
            return self.exec('''INSERT INTO classes(uid,title,start_at,end_at,location,teacher,source)
                          VALUES(?,?,?,?,?,?,?)''',(f'manual-{uuid.uuid4().hex}',title,start_at,end_at,location,teacher,'manual'))
        with self.connect() as db:
            actor=self._require_group(db,actor_id,write=True)
            db.execute('''INSERT INTO classes(uid,title,start_at,end_at,location,teacher,source,group_code)
                       VALUES(?,?,?,?,?,?,?,?)''',(f'manual-{uuid.uuid4().hex}',title,start_at,end_at,location,teacher,'manual',actor['group_code']))

    def delete_class(self, uid, *, actor_id=None):
        if actor_id is None:
            return self.exec('DELETE FROM classes WHERE uid=?',(uid,))
        with self.connect() as db:
            actor=self._require_group(db,actor_id,write=True)
            db.execute("DELETE FROM classes WHERE uid=? AND group_code=? AND source='manual'",(uid,actor['group_code']))

    def replace_ics_classes(self, rows, group_code=None):
        import hashlib
        with self.connect() as db:
            group_code = self.normalize_group(group_code) if group_code else LEGACY
            db.execute("DELETE FROM classes WHERE group_code=? AND source IN ('ics','demo')",(group_code,))
            prefix=hashlib.sha1(group_code.encode()).hexdigest()[:12]+'-'
            db.executemany('''INSERT INTO classes(uid,title,location,teacher,start_at,end_at,source,group_code)
                          VALUES(?,?,?,?,?,?,'ics',?)''',[(prefix+uid, title,loc,teacher,start,end,group_code)
                                                           for uid,title,loc,teacher,start,end in rows])
            now=datetime.now()
            if group_code!=LEGACY:
                db.execute('''INSERT INTO group_schedules(group_code,ics_url,last_sync,last_sync_at)
                      VALUES(?,'',?,?) ON CONFLICT(group_code) DO UPDATE SET
                      last_sync=excluded.last_sync,last_sync_at=excluded.last_sync_at''',
                           (group_code,now.strftime('%d.%m.%Y %H:%M'),now.isoformat(timespec='seconds')))
            else:
                for key,val in (('last_sync',now.strftime('%d.%m.%Y %H:%M')),('last_sync_at',now.isoformat(timespec='seconds'))):
                    db.execute('''INSERT INTO settings(key,value) VALUES(?,?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value''',(key,val))

    def posts(self, scope=None, *, actor_id=None):
        if scope=='group' and actor_id is not None:
            with self.connect() as db:
                actor=self._require_group(db,actor_id)
                return [dict(r) for r in db.execute('''SELECT * FROM posts WHERE scope='group' AND group_code=?
                       ORDER BY created_at DESC,id DESC''',(actor['group_code'],))]
        if scope in ('group','university'):
            return self.query('SELECT * FROM posts WHERE scope=? ORDER BY created_at DESC,id DESC',(scope,))
        return self.query('SELECT * FROM posts ORDER BY created_at DESC,id DESC')

    @staticmethod
    def _post_delete_allowed(actor, post):
        if actor['role']=='admin':
            return True
        if post['scope']=='university':
            return actor['role']=='moderator' or post['author_id']==actor['id']
        if actor['role']=='moderator' or actor['group_code']!=post['group_code']:
            return False
        return actor['role'] in ('headman','group_moderator') or (post['author_id'] is not None and post['author_id']==actor['id'])

    def add_post(self, text, category, scope='group', image_path='', *, actor_id):
        if scope not in ('group','university'):
            raise ValueError('Неверный раздел публикации.')
        if not (text or '').strip() and not image_path:
            raise ValueError('Добавь текст или фотографию.')
        with self.connect() as db:
            actor=self._actor(db,actor_id)
            group_code=LEGACY
            if scope=='group':
                actor=self._require_group(db,actor_id)
                group_code=actor['group_code']
            db.execute('''INSERT INTO posts(author,author_id,content,category,created_at,scope,image_path,group_code)
                         VALUES(?,?,?,?,?,?,?,?)''',(actor['display_name'],actor_id,text,category,
                           datetime.now().isoformat(timespec='seconds'),scope,image_path,group_code))

    def _check_post_scope(self, db, actor_id, post_id):
        actor=self._actor(db,actor_id)
        post=db.execute('SELECT scope,group_code FROM posts WHERE id=?',(post_id,)).fetchone()
        if not post:
            raise ValueError('Публикация не найдена.')
        if post['scope']=='group':
            if actor['role']=='moderator' or actor['group_code']!=post['group_code']:
                raise PermissionError('Публикации другой группы недоступны.')
        return actor

    def add_comment(self, post_id, body, *, actor_id):
        body=(body or '').strip()
        if not body or len(body)>1000:
            raise ValueError('Комментарий: от 1 до 1000 символов.')
        with self.connect() as db:
            actor=self._check_post_scope(db,actor_id,post_id)
            db.execute('''INSERT INTO post_comments(post_id,author,content,created_at)
                          VALUES(?,?,?,?)''',(post_id,actor['display_name'],body,datetime.now().isoformat(timespec='seconds')))

    def like_post(self, pid, *, actor_id):
        with self.connect() as db:
            self._check_post_scope(db,actor_id,pid)
            db.execute('UPDATE posts SET likes=likes+1 WHERE id=?',(pid,))

    def can_delete_post(self, pid, actor_id):
        with self.connect() as db:
            actor=self._actor(db,actor_id)
            post=db.execute('SELECT author_id,scope,group_code FROM posts WHERE id=?',(pid,)).fetchone()
            return bool(post and self._post_delete_allowed(actor,post))

    def delete_post(self, pid, *, actor_id):
        with self.connect() as db:
            actor=self._actor(db,actor_id)
            post=db.execute('SELECT * FROM posts WHERE id=?',(pid,)).fetchone()
            if not post:
                raise ValueError('Публикация уже удалена.')
            if not self._post_delete_allowed(actor,post):
                raise PermissionError('Нет прав удалять этот пост.')
            db.execute('DELETE FROM posts WHERE id=?',(pid,))
            return dict(post)

    def edit_post(self, post_id, content, *, actor_id):
        if len(content or '')>5000:
            raise ValueError('Слишком длинный текст публикации.')
        with self.connect() as db:
            actor=self._actor(db,actor_id)
            post=db.execute('SELECT author_id,scope,group_code,image_path FROM posts WHERE id=?',(post_id,)).fetchone()
            if not post:
                raise ValueError('Пост не найден.')
            if not self._post_delete_allowed(actor,post):
                raise PermissionError('Нет прав редактировать этот пост.')
            if not (content or '').strip() and not post['image_path']:
                raise ValueError('Публикация должна содержать текст или фотографию.')
            db.execute('UPDATE posts SET content=? WHERE id=?',((content or '').strip(),post_id))

    def resources(self, *, actor_id=None):
        if actor_id is None:
            return self.query('SELECT * FROM resources ORDER BY id DESC')
        with self.connect() as db:
            actor=self._require_group(db,actor_id)
            return [dict(r) for r in db.execute('SELECT * FROM resources WHERE group_code=? ORDER BY id DESC',(actor['group_code'],))]

    def can_delete_resource(self, actor_id, rid):
        with self.connect() as db:
            actor=self._require_group(db,actor_id)
            row=db.execute('SELECT group_code,owner_id FROM resources WHERE id=?',(rid,)).fetchone()
            return bool(row and row['group_code']==actor['group_code'] and (row['owner_id']==actor_id or actor['role'] in GROUP_EDITORS))

    def add_resource(self, title, subject, url, description, file_path='', file_name='', file_size=0, *, actor_id=None):
        if actor_id is None:
            return self.exec('''INSERT INTO resources(title,subject,url,description,file_path,file_name,file_size)
                    VALUES(?,?,?,?,?,?,?)''',(title,subject,url,description,file_path,file_name,file_size))
        with self.connect() as db:
            actor=self._require_group(db,actor_id)
            db.execute('''INSERT INTO resources(title,subject,url,description,file_path,file_name,file_size,group_code,owner_id)
                          VALUES(?,?,?,?,?,?,?,?,?)''',(title,subject,url,description,file_path,file_name,file_size,
                                                       actor['group_code'],actor_id))

    def delete_resource(self, rid, *, actor_id=None):
        if actor_id is None:
            return self.exec('DELETE FROM resources WHERE id=?',(rid,))
        with self.connect() as db:
            actor=self._require_group(db,actor_id)
            row=db.execute('SELECT group_code,owner_id FROM resources WHERE id=?',(rid,)).fetchone()
            if not row or row['group_code']!=actor['group_code'] or not (row['owner_id']==actor_id or actor['role'] in GROUP_EDITORS):
                raise PermissionError('Удалять материал может его автор или руководство группы.')
            db.execute('DELETE FROM resources WHERE id=?',(rid,))

    def attendance(self, *, actor_id=None):
        if actor_id is None:
            return {r['class_uid']:r['status'] for r in self.query('SELECT * FROM attendance')}
        with self.connect() as db:
            self._require_group(db,actor_id)
            return {r['class_uid']:r['status'] for r in db.execute('SELECT * FROM attendance_user WHERE user_id=?',(actor_id,))}

    def mark_attendance(self, uid, status, *, actor_id=None):
        if status not in ('Был','Пропуск','Не отмечено'):
            raise ValueError('Неизвестный статус.')
        if actor_id is None:
            return self.exec('''INSERT INTO attendance(class_uid,status) VALUES(?,?)
                   ON CONFLICT(class_uid) DO UPDATE SET status=excluded.status''',(uid,status))
        with self.connect() as db:
            actor=self._require_group(db,actor_id)
            row=db.execute('SELECT group_code FROM classes WHERE uid=?',(uid,)).fetchone()
            if not row or row['group_code']!=actor['group_code']:
                raise PermissionError('Нельзя отмечать посещаемость другой группы.')
            db.execute('''INSERT INTO attendance_user(user_id,class_uid,status) VALUES(?,?,?)
                  ON CONFLICT(user_id,class_uid) DO UPDATE SET status=excluded.status''',(actor_id,uid,status))

    def clear_demo(self):
        with self.connect() as db:
            db.execute('DELETE FROM tasks WHERE demo=1')
            db.execute("DELETE FROM classes WHERE source='demo'")
            db.execute('DELETE FROM posts WHERE demo=1')
            db.execute('DELETE FROM resources WHERE demo=1')
