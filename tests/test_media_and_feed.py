"""Check group/university separation, comments, attachments and legacy migration."""
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from core.storage import Store
from core.media import MediaStore


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Store(self.root/'mai_pulse.db')
        self.media = MediaStore(self.root/'mai_pulse_files')
        self.admin = self.db.create_initial_admin('admin', 'Владелец', 'SecurePass123!')
        self.db.assign_group(self.admin, 'М8О-101')

    def tearDown(self):
        self.temp.cleanup()

    def test_photos_posts_and_comments_survive_restart(self):
        buf = io.BytesIO()
        Image.new('RGB', (12, 12), (150, 80, 220)).save(buf, format='PNG')
        saved = self.media.save('my-photo.png', buf.getvalue(), 'photos')
        self.db.add_post('Фото из корпуса', 'Фото', 'university', saved, actor_id=self.admin)
        post = next(p for p in self.db.posts('university') if p['content'] == 'Фото из корпуса')
        self.assertEqual(post['image_path'], saved)
        self.assertEqual(self.media.load(saved), buf.getvalue())
        self.db.add_comment(post['id'], 'Красиво!', actor_id=self.admin)
        reopened = Store(self.root/'mai_pulse.db')
        self.assertEqual(len(reopened.comments(post['id'])), 1)
        self.assertTrue(any(p['id'] == post['id'] for p in reopened.posts('university')))
        self.assertFalse(any(p['id'] == post['id'] for p in reopened.posts('group')))
        reopened.delete_post(post['id'], actor_id=self.admin)
        self.assertEqual(reopened.comments(post['id']), [])

    def test_real_file_material_saved_inside_project(self):
        location = self.media.save('course.pdf', b'%PDF-1.4\nHello test', 'materials')
        self.db.add_resource('Конспект', 'Физика', '', 'лекция', location, 'course.pdf', 19)
        row = next(r for r in self.db.resources() if r['title'] == 'Конспект')
        self.assertEqual(row['file_path'], location)
        self.assertTrue(self.media.resolve(location).exists())
        self.media.remove(location)
        self.assertFalse(self.media.resolve(location).exists())

    def test_unsafe_media_blocked(self):
        with self.assertRaises(ValueError):
            self.media.resolve('../private')
        with self.assertRaises(ValueError):
            self.media.save('bad.exe', b'hello', 'materials')
        with self.assertRaises(ValueError):
            self.media.save('fake.jpg', b'not really a photo', 'photos')

    def test_legacy_records_receive_default_fields(self):
        cx = sqlite3.connect(self.root/'old.db')
        cx.execute('''CREATE TABLE posts(id INTEGER PRIMARY KEY, author TEXT, content TEXT,
                      category TEXT, created_at TEXT, likes INTEGER DEFAULT 0, demo INTEGER DEFAULT 0)''')
        cx.execute('''INSERT INTO posts(author,content,category,created_at) VALUES
                      ('Староста','Не потерять','Объявление','2026-10-10')''')
        cx.execute('''CREATE TABLE resources(id INTEGER PRIMARY KEY, title TEXT, subject TEXT,
                      url TEXT, description TEXT, demo INTEGER DEFAULT 0)''')
        cx.execute("INSERT INTO resources(title,subject,url,description) VALUES('Лаба','Физика','','')")
        cx.commit()
        cx.close()
        migrated = Store(self.root/'old.db')
        self.assertEqual(migrated.posts('group')[0]['content'], 'Не потерять')
        self.assertEqual(migrated.resources()[0]['file_path'], '')
        self.assertTrue('file_size' in migrated.resources()[0])


if __name__ == '__main__':
    unittest.main()
