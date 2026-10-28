"""Persistent, size-limited local file storage for posts and class resources.

Only files inside the application's managed media directory can be opened/deleted.
Files are copied on selection, not referenced by their original locations.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

MAX_PHOTO = 12 * 1024 * 1024
MAX_RESOURCE = 30 * 1024 * 1024
PHOTO_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
RESOURCE_EXTS = PHOTO_EXTS | {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                               '.txt', '.md', '.csv', '.zip', '.7z', '.py', '.ipynb'}


class MediaStore:
    def __init__(self, base):
        self.base = Path(base).resolve()
        self.base.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative: str) -> Path:
        if not relative:
            raise ValueError('Путь к файлу не указан.')
        target = (self.base / relative).resolve()
        if not target.is_relative_to(self.base) or target == self.base:
            raise ValueError('Недопустимый путь к файлу.')
        return target

    def save(self, file_name: str, data: bytes, kind: str) -> str:
        if kind not in ('photos', 'materials'):
            raise ValueError('Неподдерживаемая категория файла.')
        original = Path(file_name).name
        extension = Path(original).suffix.lower()
        allowed = PHOTO_EXTS if kind == 'photos' else RESOURCE_EXTS
        limit = MAX_PHOTO if kind == 'photos' else MAX_RESOURCE
        if extension not in allowed:
            raise ValueError('Формат файла не поддерживается: ' + (extension or 'без расширения'))
        if not data:
            raise ValueError('Нельзя загрузить пустой файл.')
        if len(data) > limit:
            raise ValueError(f'Максимальный размер: {limit // (1024*1024)} МБ.')
        if kind == 'photos':
            # Check signature, not just extension, before showing uploads as images.
            import io
            from PIL import Image
            try:
                with Image.open(io.BytesIO(data)) as im:
                    im.verify()
            except Exception as exc:
                raise ValueError('Выбранный файл не является корректным изображением.') from exc
        stored = f'{kind}/{uuid.uuid4().hex}{extension}'
        target = self.resolve(stored)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents silent overwrites.
        with target.open('xb') as handle:
            handle.write(data)
        return stored

    def load(self, relative: str, max_size: int = MAX_PHOTO) -> bytes:
        path = self.resolve(relative)
        if path.stat().st_size > max_size:
            raise ValueError('Файл слишком большой для предпросмотра.')
        return path.read_bytes()

    def remove(self, relative: str):
        if relative:
            self.resolve(relative).unlink(missing_ok=True)

    def open_file(self, relative: str):
        path = self.resolve(relative)
        if not path.is_file():
            raise FileNotFoundError('Файл не найден. Возможно, папка mai_pulse_files была перемещена.')
        if sys.platform == 'win32':
            os.startfile(str(path))
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(path)])
        else:
            subprocess.Popen(['xdg-open', str(path)])
