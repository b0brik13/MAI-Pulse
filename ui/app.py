"""MAI Pulse — polished local student dashboard built with Flet.

The application stores data locally. Feed and attendance are NOT multiuser services.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
import flet as ft

from core.photo_layout import image_size, fit_inside, feed_height

from core.storage import Store, ROLE_NAMES, GROUP_EDITORS
from core.media import MediaStore, MAX_PHOTO, MAX_RESOURCE
from core.calendar_service import sync_calendar, needs_auto_sync

DAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
          'августа', 'сентября', 'октября', 'ноября', 'декабря']
THEMES = {
    'dark': {
        'bg': '#090D1B', 'sidebar': '#0E1426', 'surface': '#131B2F',
        'surface2': '#1A2440', 'input': '#1D2944', 'line': '#28324D',
        'text': '#F4F7FF', 'muted': '#94A3BE', 'accent': '#8F84FF',
        'accent2': '#49C8F5', 'soft': '#28264F', 'green': '#52D6AD',
        'amber': '#FFCE75', 'red': '#FF7C98',
    },
    'light': {
        'bg': '#F2F5FD', 'sidebar': '#FFFFFF', 'surface': '#FFFFFF',
        'surface2': '#F5F7FE', 'input': '#F0F2FA', 'line': '#E0E6F2',
        'text': '#1E2943', 'muted': '#76829D', 'accent': '#6657E8',
        'accent2': '#007DB9', 'soft': '#EAE7FF', 'green': '#008D68',
        'amber': '#B97A00', 'red': '#D33861',
    }
}
NAV = [
    ('Моя группа', ft.Icons.GROUPS_ROUNDED),
    ('Весь МАИ', ft.Icons.PUBLIC_ROUNDED),
    ('Материалы', ft.Icons.FOLDER_COPY_ROUNDED),
    ('Аналитика', ft.Icons.INSIGHTS_ROUNDED),
    ('Настройки', ft.Icons.SETTINGS_ROUNDED),
    ('Модераторы', ft.Icons.ADMIN_PANEL_SETTINGS_ROUNDED),
    ('Мой аккаунт', ft.Icons.ACCOUNT_CIRCLE_ROUNDED),
    ('Заявки на роли', ft.Icons.ASSIGNMENT_TURNED_IN_ROUNDED),
]
GROUP_SECTIONS = ['Обзор', 'Расписание', 'Домашка', 'Объявления', 'Посещаемость', 'Материалы']



class PulseUI:
    def __init__(self, page: ft.Page):
        self.page = page
        self.db = Store()
        self.media = MediaStore(self.db.path.parent / 'mai_pulse_files')
        self.picker = ft.FilePicker()
        self.tab = 0
        self.group_section = 'Обзор'
        self.week = date.today() - timedelta(days=date.today().weekday())
        self.day = date.today()
        self.task_filter = 'Все'
        self.task_search = ''
        self.feed_filter = 'Все'
        self.calendar_error = ''
        self.calendar_sync_in_progress = False
        self.theme = self.db.get('theme', 'dark')
        if self.theme not in THEMES:
            self.theme = 'dark'
        self.sidebar_menu = ft.Column(spacing=4)
        self.switcher = ft.AnimatedSwitcher(
            content=ft.Container(),
            duration=250,
            reverse_duration=120,
            transition=ft.AnimatedSwitcherTransition.FADE,
            switch_in_curve=ft.AnimationCurve.EASE_OUT_CUBIC,
        )
        self.user = None
        self.auth_screen()


    def allowed_tabs(self):
        if not self.user:
            return set()
        if self.user['role'] == 'moderator':
            return {1, 6, 7}
        if self.user['role'] == 'admin':
            return set(range(8))
        return {0, 1, 2, 3, 4, 6, 7}

    def auth_screen(self):
        """No default password: first startup creates the owner's admin login."""
        self.page.title = 'MAI Pulse · Вход'
        self.page.theme_mode = ft.ThemeMode.DARK if self.theme == 'dark' else ft.ThemeMode.LIGHT
        self.page.bgcolor = self.c('bg')
        self.page.padding = 0
        onboarding = not self.db.has_users()
        login = self.field('Логин')
        display = self.field('Отображаемое имя') if onboarding else None
        password = ft.TextField(label='Пароль (от 8 символов)' if onboarding else 'Пароль',
                                password=True, can_reveal_password=True, border_radius=12,
                                filled=True, fill_color=self.c('input'), border_color=self.c('line'))
        info = self.t('', 12, 'red')

        def enter(e):
            try:
                if onboarding:
                    uid = self.db.create_initial_admin(login.value, display.value, password.value)
                    account = self.db.user(uid)
                else:
                    account = self.db.authenticate(login.value, password.value)
                    if not account:
                        raise ValueError('Неверный логин или пароль либо учётная запись заблокирована.')
                self.start_session(account)
            except (PermissionError, ValueError) as exc:
                info.value = str(exc)
                info.update()

        def open_registration(e):
            reg_login = self.field('Придумай логин')
            reg_name = self.field('Твоё имя')
            reg_password = ft.TextField(label='Пароль (не менее 8 символов)', password=True,
                                      can_reveal_password=True, filled=True,
                                      border_radius=12, fill_color=self.c('input'))
            def register():
                uid = self.db.register_student(reg_login.value, reg_name.value, reg_password.value)
                self._just_registered = self.db.user(uid)
            def register_and_login():
                try:
                    register()
                except (ValueError, PermissionError) as ex:
                    self.notify(str(ex), True)
                    return
                self.page.pop_dialog()
                self.start_session(self._just_registered)
            self.page.show_dialog(ft.AlertDialog(modal=True, bgcolor=self.c('surface'),
                title=self.t('Регистрация студента', 21, bold=True),
                content=ft.Column(controls=[reg_login,reg_name,reg_password,
                     self.t('Учебную группу выберешь сразу после регистрации.', 12, 'muted')],
                     tight=True, width=420, spacing=13),
                actions=[ft.TextButton('Назад',on_click=lambda e:self.page.pop_dialog()),
                         self.action('Зарегистрироваться', lambda e:register_and_login(), primary=True)]))

        screen = self.panel([
            ft.Row(controls=[self.icon(ft.Icons.BOLT_ROUNDED, 'accent', 32),
                             self.t('MAI PULSE', 27, bold=True)], spacing=12),
            self.t('Первый запуск · настройка администратора' if onboarding else 'Вход в аккаунт', 22, bold=True),
            self.t('Придумай свой пароль администратора. Он нигде не задан заранее.' if onboarding else
                   'Войди под своим логином и паролем. У модераторов отдельные аккаунты.', 12, 'muted'),
            login,
            *([display] if display else []),
            password,
            info,
            self.action('Создать администратора' if onboarding else 'Войти', enter,
                        ft.Icons.LOGIN_ROUNDED, primary=True),
            *([] if onboarding else [self.action('Зарегистрироваться как студент',open_registration,ft.Icons.PERSON_ADD_ROUNDED)]),
            self.t('Аккаунты работают только на этом компьютере. Для онлайн-сети нужен сервер.', 11, 'muted'),
        ], width=440, padding=28)
        self.page.controls.clear()
        self.page.add(ft.Container(expand=True, alignment=ft.Alignment.CENTER, content=screen,
                                   padding=ft.Padding.all(20)))
        self.page.update()

    def start_session(self, account):
        self.user = account
        self.tab = 1 if account['role'] == 'moderator' else 0
        if account['role'] != 'moderator' and not account.get('group_code'):
            self.group_onboarding()
            return
        self.setup()
        if account['role'] != 'moderator':
            src = self.db.group_schedule(account['id'])
            if needs_auto_sync(self.db, group_code=account['group_code']):
                self.page.run_task(self._sync_calendar, src['ics_url'], False)

    def group_onboarding(self):
        "Mandatory first-login group assignment. Students cannot silently switch groups."
        self.page.controls.clear()
        self.page.bgcolor = self.c('bg')
        self.page.padding = 0
        name = self.field('Номер учебной группы *', '')
        existing = self.db.list_groups()
        info = self.t('После выбора студент не сможет самостоятельно поменять группу. '
                      'Администратор может исправить ошибку.', 12, 'muted')
        def save(e):
            try:
                self.db.assign_group(self.user['id'], name.value.strip())
                self.user = self.db.user(self.user['id'])
                self.setup()
                src = self.db.group_schedule(self.user['id'])
                if needs_auto_sync(self.db, group_code=self.user['group_code']):
                    self.page.run_task(self._sync_calendar, src['ics_url'], False)
            except (ValueError,PermissionError) as exc:
                info.value = str(exc)
                info.color = self.c('red')
                info.update()
        controls = [self.icon(ft.Icons.GROUPS_ROUNDED,'accent',38),
                    self.t('Выбери учебную группу',25,bold=True),
                    self.t('Расписание, домашка, объявления и материалы будут привязаны к этой группе.',13,'muted'),
                    name]
        if existing:
            controls.append(self.t('Уже добавленные группы: '+', '.join(existing[:12]), 12, 'muted'))
        controls.extend([info,self.action('Сохранить группу и продолжить',save,ft.Icons.CHECK_CIRCLE_ROUNDED,True),
                         self.action('Выйти',self.logout,ft.Icons.LOGOUT_ROUNDED)])
        self.page.add(ft.Container(expand=True,alignment=ft.Alignment.CENTER,
                   content=self.panel(controls,width=500,padding=30)))
        self.page.update()

    def logout(self, e=None):
        self.user = None
        self.tab = 0
        self.auth_screen()

    async def _sync_calendar(self, url, manual=False):
        if self.calendar_sync_in_progress:
            if manual:
                self.notify('Расписание уже загружается.')
            return
        if not url.strip():
            if manual:
                self.notify('В настройках нет ICS-ссылки.', True)
            return
        self.calendar_sync_in_progress = True
        message = ''
        is_error = False
        try:
            # HTTP and calendar parsing run outside the GUI event loop.
            count = await asyncio.to_thread(sync_calendar, self.db, url, self.user['group_code'])
            self.calendar_error = ''
            if manual:
                self.week = date.today() - timedelta(days=date.today().weekday())
                self.day = date.today()
                message = f'Расписание обновлено: {count} занятий.'
        except Exception as exc:
            self.calendar_error = str(exc)
            if manual:
                message = f'Ошибка загрузки: {exc}'
                is_error = True
        finally:
            self.calendar_sync_in_progress = False
            if manual or self.tab in (0, 3):
                self.refresh()
            if message:
                self.notify(message, is_error)

    def c(self, name):
        return THEMES[self.theme][name]

    def setup(self):
        p = self.page
        p.title = 'MAI Pulse · Студенческий центр'
        p.theme_mode = ft.ThemeMode.DARK if self.theme == 'dark' else ft.ThemeMode.LIGHT
        p.theme = ft.Theme(color_scheme_seed='#746AFF')
        p.bgcolor = self.c('bg')
        p.padding = 0
        try:
            p.window.width = 1360
            p.window.height = 865
            p.window.min_width = 960
            p.window.min_height = 680
        except AttributeError:
            pass
        # New switcher when rebuilding shell (theme/profile changes), to avoid reusing
        # a control still attached to the previous page tree.
        self.switcher = ft.AnimatedSwitcher(
            content=ft.Container(), duration=250, reverse_duration=120,
            transition=ft.AnimatedSwitcherTransition.FADE,
            switch_in_curve=ft.AnimationCurve.EASE_OUT_CUBIC,
        )
        self.sidebar_menu.controls = self.build_nav()
        left = ft.Container(
            width=238, bgcolor=self.c('sidebar'), padding=ft.Padding.only(left=18, right=18, top=30, bottom=20),
            border=ft.Border.only(right=ft.BorderSide(1, self.c('line'))),
            content=ft.Column(controls=[
                ft.Row(controls=[
                    ft.Container(width=43, height=43, border_radius=14,
                                 gradient=ft.LinearGradient(colors=['#6979F8', '#A75BFD']),
                                 content=ft.Icon(ft.Icons.BOLT_ROUNDED, color='#FFFFFF', size=27),
                                 alignment=ft.Alignment.CENTER),
                    ft.Column(controls=[self.t('MAI PULSE', 19, bold=True),
                                        self.t('STUDENT WORKSPACE', 9, 'muted')], spacing=1),
                ], spacing=11),
                ft.Container(height=25),
                self.t('МЕНЮ', 11, 'muted', bold=True),
                ft.Container(height=7),
                ft.Container(expand=True, content=self.sidebar_menu),
                ft.Container(border_radius=14, bgcolor=self.c('surface2'), padding=13,
                    content=ft.Column(controls=[
                        ft.Row(controls=[ft.Icon(ft.Icons.AUTO_AWESOME_ROUNDED, color=self.c('amber'), size=18),
                                         self.t('ТВОЙ ПРОГРЕСС', 10, 'muted', bold=True)], spacing=6),
                        self.t('Учёба под контролем', 13, bold=True),
                        self.t('Планируй, учись, успевай.', 11, 'muted'),
                        ft.ProgressBar(value=.75, bar_height=5, color=self.c('accent'), bgcolor=self.c('line'), border_radius=5),
                    ], spacing=10)),
                ft.Container(height=12),
                ft.Container(on_click=lambda e: self.navigate(6), ink=True, border_radius=14, padding=9,
                    content=ft.Row(controls=[
                        ft.CircleAvatar(content=ft.Icon(ft.Icons.PERSON_ROUNDED, color='#FFFFFF'),
                                        bgcolor='#6458CE', radius=18),
                        ft.Column(controls=[self.t(self.user['display_name'][:19], 12, bold=True),
                                            self.t(ROLE_NAMES.get(self.user['role'],self.user['role']), 10, 'muted')], spacing=1),
                    ], spacing=9))
            ], expand=True, spacing=0))
        main_column = ft.Container(
            expand=True, padding=ft.Padding.only(left=30, right=30, top=23, bottom=18),
            content=ft.Column(controls=[
                self.build_topbar(),
                ft.Container(height=12),
                ft.Container(expand=True, content=self.switcher),
            ], expand=True, spacing=4)
        )
        p.controls.clear()
        p.add(ft.Row(controls=[left, main_column], expand=True, spacing=0, vertical_alignment=ft.CrossAxisAlignment.STRETCH))
        self.refresh()

    def t(self, value, size=14, color='text', bold=False, **kwargs):
        return ft.Text(str(value), size=size, color=self.c(color) if color in THEMES[self.theme] else color,
                       weight=ft.FontWeight.BOLD if bold else ft.FontWeight.NORMAL, **kwargs)

    def icon(self, name, color='muted', size=21):
        return ft.Icon(name, color=self.c(color) if color in THEMES[self.theme] else color, size=size)

    def action(self, label, click, icon=None, primary=False, small=False):
        return ft.Button(content=label, icon=icon, on_click=click,
                         bgcolor=self.c('accent') if primary else self.c('surface2'),
                         color='#FFFFFF' if primary else self.c('text'),
                         elevation=0, height=37 if small else 44,
                         style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=12)))

    def panel(self, controls, padding=20, width=None, expand=False, bgcolor=None):
        return ft.Container(
            width=width, expand=expand, padding=padding, bgcolor=bgcolor or self.c('surface'),
            border_radius=20, border=ft.Border.all(1, self.c('line')),
            content=ft.Column(controls=controls, spacing=13, tight=True))

    def tiny_badge(self, label, tone='accent'):
        colors = {'accent': self.c('accent'), 'red': self.c('red'), 'amber': self.c('amber'), 'green': self.c('green'), 'muted': self.c('muted')}
        return ft.Container(padding=ft.Padding.symmetric(horizontal=10, vertical=6), bgcolor=self.c('soft') if tone == 'accent' else self.c('surface2'),
                            border_radius=20, content=ft.Text(label, size=11, color=colors.get(tone, self.c('accent')),
                                                                weight=ft.FontWeight.BOLD))

    def build_nav(self):
        items = []
        for i, (label, ico) in enumerate(NAV):
            if i not in self.allowed_tabs():
                continue
            active = self.tab == i
            box = ft.Container(
                height=45, bgcolor=self.c('soft') if active else self.c('sidebar'), border_radius=12,
                padding=ft.Padding.symmetric(horizontal=14),
                animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT_CUBIC),
                ink=True,
                on_click=lambda e, k=i: self.navigate(k),
                content=ft.Row(controls=[
                    self.icon(ico, 'accent' if active else 'muted', 19),
                    self.t(label, 13, 'text' if active else 'muted', bold=active),
                    ft.Container(expand=True),
                    ft.Container(width=5, height=18, border_radius=5, bgcolor=self.c('accent')) if active else ft.Container(width=5),
                ], spacing=10),
            )
            items.append(box)
        return items

    def build_topbar(self):
        return ft.Row(controls=[
            ft.Column(controls=[
                self.t('ТВОЁ ПРОСТРАНСТВО · MAI', 10, 'accent', bold=True),
                self.t('Добро пожаловать 👋', 21, bold=True),
            ], spacing=1),
            ft.Container(expand=True),
            ft.Container(border_radius=12, padding=ft.Padding.symmetric(horizontal=14, vertical=11),
                         bgcolor=self.c('surface'), border=ft.Border.all(1, self.c('line')),
                         content=ft.Row(controls=[self.icon(ft.Icons.CALENDAR_TODAY_ROUNDED, 'accent', 17),
                                                  self.t(self.date_label(date.today()), 12)], spacing=9)),
            ft.Container(width=6),
            ft.Container(border_radius=13, width=44, height=44, bgcolor=self.c('surface2'),
                         alignment=ft.Alignment.CENTER, on_click=lambda e: self.navigate(6), ink=True,
                         content=self.icon(ft.Icons.TUNE_ROUNDED, 'muted', 21)),
        ])

    @staticmethod
    def date_label(d):
        return f'{d.day} {MONTHS[d.month-1]} {d.year}'

    def navigate(self, index):
        if index not in self.allowed_tabs():
            self.notify('Нет доступа к этому разделу.', True)
            return
        self.tab = index
        self.sidebar_menu.controls = self.build_nav()
        self.refresh()

    def open_group(self, section='Обзор'):
        self.group_section = section
        self.navigate(0)

    def group_space(self):
        options = []
        for name in GROUP_SECTIONS:
            active = name == self.group_section
            options.append(ft.Container(
                padding=ft.Padding.symmetric(horizontal=16, vertical=12), border_radius=13,
                bgcolor=self.c('soft') if active else self.c('surface'),
                border=ft.Border.all(1, self.c('accent') if active else self.c('line')),
                on_click=lambda e, chosen=name: self.open_group(chosen), ink=True,
                animate=ft.Animation(180, ft.AnimationCurve.EASE_OUT_CUBIC),
                content=self.t(name, 12, 'accent' if active else 'muted', bold=active)))
        menu = ft.Row(controls=options, wrap=True, spacing=9, run_spacing=9)
        contents = {
            'Обзор': self.home,
            'Расписание': self.schedule,
            'Домашка': self.deadlines,
            'Объявления': self.group_feed,
            'Посещаемость': self.attendance,
            'Материалы': self.materials,
        }
        return [self.title('Моя группа · ' + (self.user.get('group_code') or 'Выбери группу'),
                           'Учёба, расписание, домашка и новости группы.'), menu,
                ft.Divider(color=self.c('line')),
                *contents[self.group_section]()]

    def refresh(self):
        if not self.user:
            return
        current = self.db.user(self.user['id'])
        if not current or not current['is_active']:
            self.logout()
            return
        self.user = current
        if self.tab not in self.allowed_tabs():
            self.tab = 1 if self.user['role'] == 'moderator' else 6
        screens = [self.group_space, self.university_feed, self.materials,
                   self.analytics, self.settings, self.moderators, self.my_account, self.role_requests_screen]
        self.switcher.content = ft.Container(content=ft.Column(controls=screens[self.tab](),
                                           expand=True, scroll=ft.ScrollMode.AUTO, spacing=17),
                                             expand=True, padding=ft.Padding.only(bottom=10))
        self.page.update()

    async def pick_local(self, limit):
        # Flet 1.0 FilePicker returns byte data in web and desktop modes.
        selected = await self.picker.pick_files(allow_multiple=False, with_data=True)
        if not selected:
            return None
        f = selected[0]
        if f.size > limit:
            raise ValueError(f'Файл больше {limit // (1024 * 1024)} МБ.')
        if f.bytes is not None:
            return (f.name, bytes(f.bytes))
        if f.path:
            with open(f.path, 'rb') as handle:
                return (f.name, handle.read(limit + 1))
        raise ValueError('Не удалось получить содержимое файла.')

    def open_local(self, relative):
        try:
            self.media.open_file(relative)
        except (OSError, ValueError) as exc:
            self.notify(f'Не удалось открыть файл: {exc}', error=True)

    def notify(self, message, error=False):
        self.page.show_dialog(ft.SnackBar(content=ft.Text(message),
                                         bgcolor='#A53256' if error else '#343264', duration=3500))

    def title(self, title, subtitle, action=None):
        return ft.Row(controls=[
            ft.Column(controls=[self.t(title, 29, bold=True), self.t(subtitle, 13, 'muted')], spacing=4),
            ft.Container(expand=True),
            *([action] if action else []),
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def modal(self, title, fields, on_save, subtitle=''):
        dialog = None
        def save(e):
            try:
                on_save()
            except (ValueError, TypeError, PermissionError) as exc:
                self.notify(str(exc), error=True)
                return
            self.page.pop_dialog()
            self.refresh()
        body = ft.Column(controls=[*( [self.t(subtitle, 12, 'muted')] if subtitle else [] ), *fields], spacing=12,
                         width=435, tight=True, scroll=ft.ScrollMode.AUTO)
        dialog = ft.AlertDialog(
            modal=True, title=self.t(title, 21, bold=True), content=body,
            actions=[ft.TextButton('Отмена', on_click=lambda e: self.page.pop_dialog()),
                     self.action('Сохранить', save, ft.Icons.CHECK_ROUNDED, primary=True)],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.show_dialog(dialog)

    def field(self, label, value='', multiline=False):
        return ft.TextField(label=label, value=str(value or ''), multiline=multiline,
                            min_lines=3 if multiline else 1, max_lines=5 if multiline else 1,
                            border_radius=12, filled=True, fill_color=self.c('input'),
                            border_color=self.c('line'), text_size=13, content_padding=14)

    def select(self, label, options, value=None, on_change=None, width=None):
        return ft.Dropdown(label=label, width=width, value=value or options[0],
                           options=[ft.DropdownOption(key=v, text=v) for v in options],
                           on_select=on_change)

    def empty(self, title, desc, ico=ft.Icons.INBOX_ROUNDED):
        return ft.Container(padding=32, bgcolor=self.c('surface'), border_radius=20,
                            alignment=ft.Alignment.CENTER,
                            content=ft.Column(controls=[self.icon(ico, 'accent', 36),
                                                       self.t(title, 17, bold=True),
                                                       self.t(desc, 12, 'muted', text_align=ft.TextAlign.CENTER)],
                                              horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10,
                                              tight=True))

    def stat(self, value, label, ico, color='accent'):
        def hover(e):
            e.control.bgcolor = self.c('surface2') if str(e.data).lower() == 'true' else self.c('surface')
            e.control.update()
        return ft.Container(width=204, height=138, border_radius=20, bgcolor=self.c('surface'),
                            padding=20, border=ft.Border.all(1, self.c('line')),
                            animate=ft.Animation(170, ft.AnimationCurve.EASE_OUT_CUBIC), on_hover=hover,
                            content=ft.Column(controls=[
                                ft.Row(controls=[self.icon(ico, color, 23), ft.Container(expand=True),
                                                 self.icon(ft.Icons.NORTH_EAST_ROUNDED, 'muted', 13)]),
                                self.t(value, 31, bold=True), self.t(label, 12, 'muted')
                            ], spacing=4))

    def task_row(self, task, show_edit=False):
        days = (date.fromisoformat(task['due_date']) - date.today()).days
        if task['done']:
            state, tone = 'Готово', 'green'
        elif days < 0:
            state, tone = f'Просрочено · {-days} дн.', 'red'
        elif days == 0:
            state, tone = 'Сегодня', 'red'
        elif days <= 2:
            state, tone = f'{days} дн.', 'amber'
        else:
            state, tone = f'{days} дн.', 'accent'
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=12, vertical=10), bgcolor=self.c('surface2'),
            border_radius=13,
            content=ft.Row(controls=[
                ft.Checkbox(value=bool(task['done']), active_color=self.c('accent'),
                            on_change=lambda e, tid=task['id']: self.toggle_task(tid, e.control.value)),
                ft.Column(controls=[self.t(task['title'], 13, bold=True),
                                    self.t(f"{task['subject']}  ·  {task['due_date']}", 11, 'muted')],
                          spacing=2, expand=True),
                self.tiny_badge(state, tone),
                *([ft.IconButton(icon=ft.Icons.EDIT_OUTLINED, icon_color=self.c('muted'),
                                 tooltip='Редактировать', on_click=lambda e, t=task: self.task_editor(t)),
                   ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=self.c('red'),
                                 tooltip='Удалить', on_click=lambda e, tid=task['id']: self.delete_task(tid))] if show_edit and self.db.can_edit_task(self.user['id'], task['id']) else []),
            ], spacing=5))

    def toggle_task(self, task_id, checked):
        self.db.update_task(task_id, checked, actor_id=self.user['id'])
        self.refresh()

    def delete_task(self, task_id):
        self.db.delete_task(task_id, actor_id=self.user['id'])
        self.notify('Задание удалено')
        self.refresh()

    def task_editor(self, task=None):
        name = self.field('Название задания *', task['title'] if task else '')
        subject = self.field('Предмет *', task['subject'] if task else '')
        when = self.field('Дедлайн (ГГГГ-ММ-ДД) *', task['due_date'] if task else
                          (date.today() + timedelta(days=3)).isoformat())
        priority = self.select('Приоритет', ['Обычный', 'Высокий', 'Низкий'], task['priority'] if task else 'Обычный')
        details = self.field('Описание (необязательно)', task['details'] if task else '', multiline=True)
        def save():
            if not name.value.strip() or not subject.value.strip():
                raise ValueError('Укажи название и предмет.')
            self.db.save_task(name.value.strip(), subject.value.strip(), when.value.strip(),
                              priority.value, details.value.strip(), task['id'] if task else None, actor_id=self.user['id'])
        self.modal('Редактировать задание' if task else 'Новое задание',
                   [name, subject, when, priority, details], save)

    def home(self):
        tasks = self.db.tasks(actor_id=self.user['id'])
        active = [t for t in tasks if not t['done']]
        finished = len(tasks) - len(active)
        ratio = finished / len(tasks) if tasks else 0
        today = date.today()
        todays_classes = self.db.classes(today.isoformat()+'T00', (today+timedelta(days=1)).isoformat()+'T00', actor_id=self.user['id'])
        important = sorted(active, key=lambda t: t['due_date'])[:4]
        welcome = self.user['display_name'].split()[0]
        banner = ft.Container(
            padding=28, border_radius=23, height=192,
            gradient=ft.LinearGradient(begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT,
                                       colors=['#574DCC', '#7D59D1', '#3E61B8']),
            content=ft.Row(controls=[
                ft.Column(controls=[
                    ft.Text('ТВОЙ ПЕРСОНАЛЬНЫЙ СТУДЕНЧЕСКИЙ ЦЕНТР', size=10, color='#DAD9FF', weight='bold'),
                    ft.Text(f'Всё под контролем, {welcome}!', size=24, color='#FFFFFF', weight='bold'),
                    ft.Text('Пары, задачи и студенческая жизнь — в одном месте.',
                            size=12, color='#EFEBFF'),
                    ft.Container(height=5),
                    ft.Button('Открыть расписание', icon=ft.Icons.ARROW_FORWARD_ROUNDED,
                              bgcolor='#FFFFFF', color='#5946C3', on_click=lambda e: self.open_group('Расписание')),
                ], spacing=8, expand=True),
                ft.Icon(ft.Icons.ROCKET_LAUNCH_ROUNDED, size=118, color='#C7BEFF'),
            ]))
        cards = ft.Row(controls=[
            self.stat(len(todays_classes), 'Пар сегодня', ft.Icons.AUTO_STORIES_ROUNDED),
            self.stat(len(active), 'Активных дедлайнов', ft.Icons.ALARM_ROUNDED, 'amber'),
            self.stat(f'{round(ratio*100)}%', 'Заданий выполнено', ft.Icons.CHECK_CIRCLE_ROUNDED, 'green'),
            self.stat(len(self.db.posts('group',actor_id=self.user['id'])), 'Постов группы', ft.Icons.FORUM_ROUNDED, 'accent'),
        ], wrap=True, spacing=13, run_spacing=13)
        upcoming = self.panel([
            ft.Row(controls=[self.t('Ближайшие дедлайны', 17, bold=True), ft.Container(expand=True),
                             ft.TextButton('Все задачи', on_click=lambda e: self.open_group('Домашка'))]),
            *([self.task_row(t) for t in important] if important else [self.t('Ты закрыл все задания! ✨', 13, 'green')]),
        ], expand=True)
        todaybox = self.panel([
            ft.Row(controls=[self.t('Сегодня по расписанию', 17, bold=True),
                             ft.Container(expand=True), self.icon(ft.Icons.EVENT_ROUNDED, 'accent')]),
            self.t(self.date_label(today), 11, 'muted'),
            *([self.simple_class(row) for row in todays_classes[:4]] if todays_classes else
              [self.t('Сегодня пар нет. Отдохни или займись задачами.', 12, 'muted')]),
            ft.Container(height=8),
            self.action('Открыть неделю', lambda e: self.open_group('Расписание'), ft.Icons.CALENDAR_MONTH_ROUNDED, small=True),
        ], width=320)
        overview = self.panel([
            ft.Row(controls=[self.t('Прогресс по заданиям', 17, bold=True), ft.Container(expand=True),
                             self.t(f'{finished}/{len(tasks)}', 13, 'accent', bold=True)]),
            ft.ProgressBar(value=ratio, color=self.c('accent'), bgcolor=self.c('line'), bar_height=10,
                           border_radius=10),
            self.t('Закрывай задачи и следи за результатом на графиках.', 11, 'muted'),
        ])
        return [self.title('Главная', 'Планируй учебный день и будь в курсе событий.'),
                banner, cards, ft.Row(controls=[upcoming, todaybox], spacing=16,
                                     vertical_alignment=ft.CrossAxisAlignment.START), overview]

    def simple_class(self, item):
        return ft.Container(padding=12, border_radius=12, bgcolor=self.c('surface2'),
                            content=ft.Row(controls=[
                                ft.Container(width=4, height=37, border_radius=4, bgcolor=self.c('accent')),
                                ft.Column(controls=[self.t(item['start_at'][11:16], 12, 'accent', bold=True),
                                                    self.t(item['end_at'][11:16], 10, 'muted')], spacing=1),
                                ft.Column(controls=[self.t(item['title'], 12, bold=True),
                                                    self.t('Ауд. '+item['location'] if item['location'] else 'Аудитория не указана', 10, 'muted')],
                                          spacing=2, expand=True),
                            ], spacing=10))

    def schedule(self):
        start = self.week
        end = start + timedelta(days=7)
        lessons = self.db.classes(start.isoformat()+'T00', end.isoformat()+'T00', actor_id=self.user['id'])
        day_rows = [item for item in lessons if item['start_at'].startswith(self.day.isoformat())]
        def move(weeks):
            self.week += timedelta(days=7*weeks)
            self.day = self.week
            self.refresh()
        def jump():
            self.day = date.today()
            self.week = self.day - timedelta(days=self.day.weekday())
            self.refresh()
        toolbar = ft.Row(controls=[
            self.action('Сегодня', lambda e: jump(), ft.Icons.TODAY_ROUNDED, small=True),
            ft.Container(expand=True),
            ft.IconButton(icon=ft.Icons.CHEVRON_LEFT_ROUNDED, on_click=lambda e: move(-1), icon_color=self.c('text')),
            self.t(f'{start.day} {MONTHS[start.month-1]} — {(end-timedelta(days=1)).day} {MONTHS[(end-timedelta(days=1)).month-1]}', 14, bold=True),
            ft.IconButton(icon=ft.Icons.CHEVRON_RIGHT_ROUNDED, on_click=lambda e: move(1), icon_color=self.c('text')),
        ])
        days = []
        for i in range(7):
            d = start + timedelta(days=i)
            selected = self.day == d
            count = sum(1 for row in lessons if row['start_at'].startswith(d.isoformat()))
            days.append(ft.Container(
                width=124, height=101, border_radius=15, ink=True,
                bgcolor=self.c('soft') if selected else self.c('surface2'),
                border=ft.Border.all(1, self.c('accent') if selected else self.c('line')),
                on_click=lambda e, day=d: self.choose_day(day),
                content=ft.Column(controls=[self.t(DAYS[i], 12, 'accent' if selected else 'muted'),
                                            self.t(d.day, 26, bold=True),
                                            self.t(f'{count} пар', 11, 'muted')],
                                  horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                  alignment=ft.MainAxisAlignment.CENTER, spacing=2)))
        rows = []
        for cls in day_rows:
            rows.append(ft.Container(padding=18, border_radius=15, bgcolor=self.c('surface2'),
                content=ft.Row(controls=[
                    ft.Column(controls=[self.t(cls['start_at'][11:16], 18, 'accent', bold=True),
                                        self.t(cls['end_at'][11:16], 11, 'muted')], spacing=4, width=74),
                    ft.Container(height=54, width=3, bgcolor=self.c('accent'), border_radius=3),
                    ft.Column(controls=[self.t(cls['title'], 16, bold=True),
                                        self.t(f"{cls['teacher']}  ·  {cls['location']}".strip('  ·'), 12, 'muted'),
                                        self.t('Демо' if cls['source']=='demo' else ('ICS' if cls['source']=='ics' else 'Добавлено вручную'), 10, 'accent')],
                              spacing=4, expand=True),
                    *([ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=self.c('red'),
                                    on_click=lambda e, uid=cls['uid']: self.remove_class(uid))]
                      if cls['source']=='manual' and self.db.has_group_editor_rights(self.user['id']) else []),
                ], spacing=14)))
        mode = 'Демоданные' if any(row['source']=='demo' for row in lessons) else ('Календарь ICS' if self.db.group_schedule(self.user['id']).get('last_sync') else 'Локальный режим')
        status = ('Загрузка календаря…' if self.calendar_sync_in_progress else
                  ('Не удалось загрузить ICS. Открой настройки или повтори попытку.' if self.calendar_error else
                   ('Автообновление включено · ' + mode)))
        return [self.title('Расписание', 'Пары на неделю, аудитории и преподаватели.',
                           (self.action('Добавить пару', lambda e: self.class_editor(), ft.Icons.ADD_ROUNDED, True)
                           if self.db.has_group_editor_rights(self.user['id']) else None)),
                ft.Row(controls=[self.t(status, 12, 'red' if self.calendar_error else 'accent', bold=True),
                                 ft.Container(expand=True),
                                 self.action('Обновить ICS', lambda e: self.page.run_task(self._sync_calendar, self.db.group_schedule(self.user['id'])['ics_url'], True), ft.Icons.SYNC_ROUNDED, small=True),
                                 self.action('Настройки', lambda e: self.navigate(4), ft.Icons.SETTINGS_ROUNDED, small=True)], wrap=True),
                self.panel([toolbar, ft.Row(controls=days, wrap=True, spacing=9, run_spacing=9)]),
                self.t(f'{DAYS[self.day.weekday()]}, {self.date_label(self.day)}', 18, bold=True),
                *(rows if rows else [self.empty('Пар нет', 'Выбери другой день или добавь занятие.', ft.Icons.EVENT_BUSY_ROUNDED)])]

    def choose_day(self, day):
        self.day = day
        self.refresh()

    def class_editor(self):
        title = self.field('Название дисциплины *')
        day = self.field('Дата (ГГГГ-ММ-ДД) *', self.day.isoformat())
        start = self.field('Начало (ЧЧ:ММ) *', '09:00')
        end = self.field('Конец (ЧЧ:ММ) *', '10:30')
        room = self.field('Аудитория')
        teacher = self.field('Преподаватель / тип занятия')
        def save():
            if not title.value.strip():
                raise ValueError('Укажи предмет.')
            d = date.fromisoformat(day.value.strip())
            a = datetime.strptime(start.value.strip(), '%H:%M').time()
            b = datetime.strptime(end.value.strip(), '%H:%M').time()
            st, en = datetime.combine(d,a), datetime.combine(d,b)
            if en <= st:
                raise ValueError('Конец занятия должен быть позже начала.')
            self.db.add_class(title.value.strip(), st.isoformat(), en.isoformat(),
                              room.value.strip(), teacher.value.strip(), actor_id=self.user['id'])
            self.day = d
            self.week = d - timedelta(days=d.weekday())
        self.modal('Новая пара', [title, day, start, end, room, teacher], save)

    def remove_class(self, uid):
        self.db.delete_class(uid, actor_id=self.user['id'])
        self.refresh()

    def deadlines(self):
        tasks = self.db.tasks(actor_id=self.user['id'])
        def update_filter(e):
            self.task_filter = e.control.value
            self.refresh()
        def search(e):
            self.task_search = e.control.value
            self.refresh()
        needle = self.task_search.strip().lower()
        if needle:
            tasks = [x for x in tasks if needle in (x['title']+' '+x['subject']).lower()]
        if self.task_filter == 'Активные':
            tasks = [t for t in tasks if not t['done']]
        if self.task_filter == 'Готовые':
            tasks = [t for t in tasks if t['done']]
        if self.task_filter == 'Просроченные':
            tasks = [t for t in tasks if not t['done'] and t['due_date'] < date.today().isoformat()]
        searchbox = ft.TextField(label='Поиск по названию или предмету', value=self.task_search,
                                 prefix_icon=ft.Icons.SEARCH_ROUNDED, expand=True,
                                 border_radius=12, filled=True, fill_color=self.c('surface'),
                                 border_color=self.c('line'), on_submit=search)
        # Apply search on Enter to avoid recreating focused field on every keystroke.
        top = ft.Row(controls=[searchbox,
                               self.action('Найти', lambda e: self.search_tasks(searchbox.value),
                                           ft.Icons.SEARCH_ROUNDED, small=True),
                               self.select('Статус', ['Все','Активные','Готовые','Просроченные'],
                                           self.task_filter, update_filter, width=175)], spacing=12)
        return [self.title('Дедлайны', 'Общие задания группы и твои личные задачи.',
                           self.action('Новое задание', lambda e: self.task_editor(), ft.Icons.ADD_ROUNDED, True)),
                top,
                self.panel([self.t(f'Найдено заданий: {len(tasks)}', 12, 'muted'),
                            *([self.task_row(t, show_edit=True) for t in tasks] if tasks else
                              [self.empty('Здесь пока пусто', 'Добавь первое задание или поменяй фильтр.')])])]

    def search_tasks(self, value):
        self.task_search = value
        self.refresh()

    def post_editor(self, scope='group'):
        """Post composer, styled like the rest of MAI Pulse, with image preview."""
        if self.user['role'] == 'moderator' and scope != 'university':
            self.notify('Модератор работает только с общей лентой.', True)
            return
        university = scope == 'university'
        heading = 'Новая публикация' if university else 'Новое объявление'
        destination = 'Весь МАИ' if university else 'Моя группа'
        kinds = (['Сообщество', 'Фото', 'События', 'Вопрос'] if university
                 else ['Объявление', 'Вопрос', 'Сообщество', 'Полезное'])
        chosen = {'file': None}

        body = ft.TextField(
            hint_text='Расскажи, что происходит в МАИ…' if university
                      else 'Что нужно сообщить одногруппникам?',
            multiline=True, min_lines=3, max_lines=5,
            text_size=14, color=self.c('text'),
            border=ft.OutlineInputBorder(border_radius=15,
                                         side=ft.BorderSide(1, self.c('line'))),
            filled=True, fill_color=self.c('input'),
            content_padding=16,
        )
        counter = self.t('0 / 5000', 11, 'muted')

        def count(e):
            length = len(body.value or '')
            counter.value = f'{length} / 5000'
            counter.color = self.c('red') if length > 5000 else self.c('muted')
            counter.update()
        body.on_change = count

        category = ft.Dropdown(
            label='Категория', value=kinds[0], width=245,
            options=[ft.DropdownOption(key=k, text=k) for k in kinds],
            filled=True, fill_color=self.c('input'),
            color=self.c('text'), border_radius=14,
            border_color=self.c('line'), content_padding=14,
        )
        file_status = self.t('JPG, PNG, WEBP, GIF  ·  до 12 МБ', 11, 'muted')

        def placeholder():
            return ft.Column(controls=[
                ft.Container(
                    width=47, height=47, border_radius=15,
                    bgcolor=self.c('soft'), alignment=ft.Alignment.CENTER,
                    content=self.icon(ft.Icons.ADD_PHOTO_ALTERNATE_ROUNDED, 'accent', 26)),
                self.t('Нажми, чтобы выбрать фотографию', 13, bold=True),
                self.t('Фото покажем целиком, без обрезки', 11, 'muted'),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER, spacing=7)

        upload_area = ft.Container(
            width=540, height=147, padding=12,
            border_radius=17, bgcolor=self.c('surface2'),
            border=ft.Border.all(1, self.c('line')),
            alignment=ft.Alignment.CENTER,
            ink=True,
            animate=ft.Animation(210, ft.AnimationCurve.EASE_OUT_CUBIC),
            content=placeholder(),
        )

        def clear_image(e):
            chosen['file'] = None
            upload_area.content = placeholder()
            upload_area.height = 147
            upload_area.on_click = pick_click
            file_status.value = 'JPG, PNG, WEBP, GIF  ·  до 12 МБ'
            upload_area.update()
            file_status.update()

        async def choose_photo():
            try:
                picked = await self.pick_local(MAX_PHOTO)
                if not picked:
                    return
                name, data = picked
                if Path(name).suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
                    raise ValueError('Выбери изображение JPG, PNG, WEBP или GIF.')
                try:
                    natural = image_size(data)
                except (OSError, ValueError) as exc:
                    raise ValueError('Не удалось прочитать изображение.') from exc
                chosen['file'] = (name, data)
                # Display the *whole* photo, without a fixed cover crop.
                preview_w, preview_h = fit_inside(*natural, 510, 260)
                upload_area.height = 330
                upload_area.on_click = None
                upload_area.content = ft.Column(controls=[
                    ft.Container(height=265, alignment=ft.Alignment.CENTER,
                                 content=ft.Image(src=data, width=preview_w,
                                                  height=preview_h, fit=ft.BoxFit.CONTAIN)),
                    ft.Row(controls=[
                        self.icon(ft.Icons.CHECK_CIRCLE_ROUNDED, 'green', 18),
                        self.t(name if len(name) < 35 else name[:32] + '…', 12, bold=True),
                        ft.Container(expand=True),
                        ft.IconButton(icon=ft.Icons.CLOSE_ROUNDED,
                                      icon_color=self.c('muted'),
                                      tooltip='Убрать изображение', on_click=clear_image),
                    ], spacing=8),
                ], spacing=2)
                file_status.value = (f'{natural[0]} × {natural[1]} пикселей · '
                                     f'{len(data) / (1024*1024):.1f} МБ · без обрезки')
                upload_area.update()
                file_status.update()
            except (OSError, ValueError) as exc:
                self.notify(str(exc), True)

        def pick_click(e):
            self.page.run_task(choose_photo)
        upload_area.on_click = pick_click

        def publish(e):
            text = (body.value or '').strip()
            if not text and not chosen['file']:
                self.notify('Добавь текст или фотографию.', True)
                return
            if len(text) > 5000:
                self.notify('Подпись не должна быть длиннее 5000 символов.', True)
                return
            relative = ''
            try:
                if chosen['file']:
                    relative = self.media.save(*chosen['file'], kind='photos')
                self.db.add_post(text, category.value, scope=scope, image_path=relative, actor_id=self.user['id'])
            except Exception as exc:
                if relative:
                    self.media.remove(relative)
                self.notify(f'Не удалось создать публикацию: {exc}', True)
                return
            self.page.pop_dialog()
            self.refresh()
            self.notify('Публикация сохранена!')

        dialog = ft.AlertDialog(
            modal=True, bgcolor=self.c('surface'),
            barrier_color='#BB050713',
            shape=ft.RoundedRectangleBorder(radius=24),
            title_padding=ft.Padding.only(left=24, right=24, top=22, bottom=9),
            content_padding=ft.Padding.only(left=24, right=24, top=5, bottom=10),
            actions_padding=ft.Padding.only(left=24, right=24, top=8, bottom=22),
            title=ft.Row(controls=[
                ft.Container(width=45, height=45, border_radius=15,
                             gradient=ft.LinearGradient(colors=['#6257D8', '#AF68E8']),
                             alignment=ft.Alignment.CENTER,
                             content=ft.Icon(ft.Icons.ADD_PHOTO_ALTERNATE_ROUNDED,
                                             color='#FFFFFF', size=24)),
                ft.Column(controls=[self.t(heading, 21, bold=True),
                                    self.t(f'Публикация в «{destination}»', 12, 'muted')], spacing=3),
                ft.Container(expand=True),
                ft.IconButton(icon=ft.Icons.CLOSE_ROUNDED,
                              icon_color=self.c('muted'),
                              tooltip='Закрыть',
                              on_click=lambda e: self.page.pop_dialog()),
            ], spacing=13),
            content=ft.Column(controls=[
                ft.Container(padding=ft.Padding.symmetric(horizontal=12, vertical=9),
                             border_radius=11, bgcolor=self.c('surface2'),
                             content=ft.Row(controls=[
                                 self.icon(ft.Icons.LOCK_OUTLINE_ROUNDED, 'accent', 16),
                                 self.t('Пока видно только на этом компьютере', 11, 'muted')], spacing=9)),
                ft.Container(height=3),
                self.t('ТЕКСТ ПУБЛИКАЦИИ', 11, 'muted', bold=True),
                body,
                ft.Row(controls=[category, ft.Container(expand=True), counter],
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(height=3),
                ft.Row(controls=[self.t('ФОТОГРАФИЯ', 11, 'muted', bold=True),
                                 ft.Container(expand=True),
                                 self.t('Необязательно', 11, 'muted')]),
                upload_area,
                ft.Row(controls=[file_status, ft.Container(expand=True),
                                 ft.TextButton('Выбрать / заменить',
                                               on_click=pick_click)], spacing=8),
            ], width=540, spacing=12, tight=True, scroll=ft.ScrollMode.AUTO),
            actions=[
                ft.TextButton('Отмена', on_click=lambda e: self.page.pop_dialog(),
                              style=ft.ButtonStyle(color=self.c('muted'))),
                self.action('Опубликовать', publish, ft.Icons.SEND_ROUNDED, primary=True),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            scrollable=True,
        )
        self.page.show_dialog(dialog)

    def open_photo_viewer(self, relative):
        """Zoomable full-resolution viewer; the original is never resized on disk."""
        try:
            data = self.media.load(relative)
            natural = image_size(data)
        except (OSError, ValueError) as exc:
            self.notify(f'Не удалось открыть фото: {exc}', True)
            return

        avail_width = max(520, min(1030, (self.page.width or 1280) - 160))
        avail_height = max(350, min(690, (self.page.height or 850) - 210))
        image_w, image_h = fit_inside(*natural, avail_width, avail_height)
        viewer = ft.InteractiveViewer(
            min_scale=1, max_scale=8,
            content=ft.Container(width=avail_width, height=avail_height,
                                 alignment=ft.Alignment.CENTER,
                                 content=ft.Image(src=data, width=image_w, height=image_h,
                                                  fit=ft.BoxFit.CONTAIN,
                                                  filter_quality=ft.FilterQuality.HIGH)),
        )
        self.page.show_dialog(ft.AlertDialog(
            modal=True, bgcolor='#090D1B', barrier_color='#E8000208',
            shape=ft.RoundedRectangleBorder(radius=20),
            title=ft.Text(f'Оригинал · {natural[0]} × {natural[1]} пикселей',
                          color='#FFFFFF', size=15, weight=ft.FontWeight.BOLD),
            content=ft.Container(width=avail_width, height=avail_height,
                                 bgcolor='#060912', border_radius=13,
                                 clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                                 content=viewer),
            actions=[ft.TextButton('Открыть файл', on_click=lambda e: self.open_local(relative)),
                     ft.TextButton('Закрыть', on_click=lambda e: self.page.pop_dialog())],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    def comment_editor(self, post_id):
        body = self.field('Написать комментарий *', multiline=True)
        def save():
            self.db.add_comment(post_id, body.value, actor_id=self.user['id'])
        self.modal('Комментарий к посту', [body], save)

    def post_card(self, post):
        comments = self.db.comments(post['id'])
        photo = []
        if post['image_path']:
            try:
                img = self.media.load(post['image_path'])
                natural = image_size(img)
                # The height follows the original aspect ratio: no 680x360 crop.
                photo_h = feed_height(*natural, display_width=676, max_height=950)
                photo = [
                    ft.Container(
                        width=676, border_radius=16,
                        bgcolor='#090D1B', clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                        ink=True,
                        tooltip='Нажми, чтобы открыть фото в полном разрешении',
                        on_click=lambda e, rel=post['image_path']: self.open_photo_viewer(rel),
                        content=ft.Image(src=img, fit=ft.BoxFit.CONTAIN,
                                         width=676, height=photo_h,
                                         filter_quality=ft.FilterQuality.HIGH)),
                    ft.Container(
                        ink=True, border_radius=9,
                        on_click=lambda e, rel=post['image_path']: self.open_photo_viewer(rel),
                        content=ft.Row(controls=[
                            self.icon(ft.Icons.ZOOM_IN_ROUNDED, 'accent', 17),
                            self.t(f'Открыть оригинал · {natural[0]} × {natural[1]}', 11, 'muted'),
                        ], spacing=7)),
                ]
            except (OSError, ValueError):
                photo = [self.t('Изображение отсутствует на диске', 12, 'red')]
        comments_controls = []
        for comment in comments[-3:]:
            comments_controls.append(ft.Row(controls=[
                self.t(comment['author'], 11, bold=True),
                self.t(comment['content'], 12, 'muted', expand=True, no_wrap=False)
            ], spacing=10))
        return self.panel([
            ft.Row(controls=[
                ft.CircleAvatar(bgcolor=self.c('soft'), radius=21,
                                content=self.icon(ft.Icons.PERSON_ROUNDED, 'accent', 22)),
                ft.Column(controls=[self.t(post['author'], 14, bold=True),
                                    self.t(post['created_at'][:16].replace('T', ' · '), 10, 'muted')], spacing=2),
                ft.Container(expand=True), self.tiny_badge(post['category']),
                *([ft.IconButton(icon=ft.Icons.EDIT_ROUNDED,icon_color=self.c('accent'),
                               tooltip='Редактировать',on_click=lambda e,p=post:self.edit_post(p)),
                   ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=self.c('red'),
                              tooltip='Удалить публикацию',
                              on_click=lambda e, pid=post['id']: self.remove_post(pid))]
                  if self.db.can_delete_post(post['id'], self.user['id']) else []),
            ]),
            *photo,
            *([self.t(post['content'], 14)] if post['content'] else []),
            ft.Row(controls=[
                self.action(f"❤ {post['likes']}  Нравится", lambda e, pid=post['id']: self.like_post(pid),
                            ft.Icons.FAVORITE_BORDER_ROUNDED, small=True),
                self.action(f'Комментарии · {len(comments)}', lambda e, pid=post['id']: self.comment_editor(pid),
                            ft.Icons.CHAT_BUBBLE_OUTLINE_ROUNDED, small=True),
            ], wrap=True, spacing=10),
            *comments_controls,
        ], width=720)

    def edit_post(self, post):
        text=self.field('Текст публикации',post['content'],multiline=True)
        def save():
            self.db.edit_post(post['id'],text.value,actor_id=self.user['id'])
        self.modal('Редактировать публикацию',[text],save,
                   'Изменять пост может его автор, модератор своего раздела или администратор.')

    def post_feed(self, scope):
        posts = self.db.posts(scope, actor_id=self.user['id'] if scope=='group' else None)
        if self.feed_filter != 'Все':
            posts = [p for p in posts if p['category'] == self.feed_filter]
        if scope == 'group':
            title, subtitle = ('Объявления группы',
                               'Вопросы, новости старосты и полезные публикации.')
        else:
            title, subtitle = ('Весь МАИ · Campus Feed',
                               'Фотографии, студенческие события, посты и комментарии.')
        items = [self.title(title, subtitle,
                            self.action('Создать пост', lambda e: self.post_editor(scope),
                                        ft.Icons.ADD_PHOTO_ALTERNATE_ROUNDED, True)),
                 self.panel([self.t('Локальный прототип: публикации пока не видят другие студенты. '
                                    'Для общей ленты потребуется сервер и аккаунты.', 12, 'muted')],
                            padding=14, bgcolor=self.c('surface2'))]
        if scope == 'university':
            items.append(ft.Container(
                height=130, border_radius=22, padding=24,
                gradient=ft.LinearGradient(colors=['#5649C6','#9D50BB','#D36495']),
                content=ft.Column(controls=[
                    ft.Text('CAMPUS STORIES', color='#FFFFFF', size=12, weight='bold'),
                    ft.Text('Твоя жизнь в МАИ — в кадре 📸', color='#FFFFFF', size=23, weight='bold'),
                    ft.Text('Добавь фото и поделись моментом со студенческим сообществом.',
                            size=12, color='#FFF1FA')], spacing=7)))
        items.extend(self.post_card(post) for post in posts)
        if not posts:
            items.append(self.empty('В ленте пока пусто',
                                    'Нажми «Создать пост» — можно добавить фотографию.',
                                    ft.Icons.ADD_PHOTO_ALTERNATE_ROUNDED))
        return items

    def group_feed(self):
        return self.post_feed('group')

    def university_feed(self):
        return self.post_feed('university')

    def like_post(self, pid):
        self.db.like_post(pid, actor_id=self.user['id'])
        self.refresh()

    def remove_post(self, pid):
        if not self.db.can_delete_post(pid, self.user['id']):
            self.notify('У тебя нет прав на удаление этой публикации.', True)
            return
        post = next((p for p in self.db.posts() if p['id'] == pid), None)
        if not post:
            self.notify('Публикация не найдена.', True)
            return
        description = (post['content'] or 'Публикация с фотографией').strip()[:110]

        def confirmed(e):
            try:
                deleted = self.db.delete_post(pid, actor_id=self.user['id'])
            except (PermissionError, ValueError) as exc:
                self.page.pop_dialog()
                self.notify(str(exc), True)
                return
            self.page.pop_dialog()
            if deleted['image_path']:
                self.media.remove(deleted['image_path'])
            self.refresh()
            self.notify('Публикация удалена.')

        self.page.show_dialog(ft.AlertDialog(
            modal=True, bgcolor=self.c('surface'),
            title=self.t('Удалить публикацию?', 20, bold=True),
            content=ft.Column(controls=[
                self.t(f'Автор: {post["author"]}', 13),
                self.t(description, 12, 'muted'),
                self.t('Удаление нельзя отменить. Фото публикации также будет удалено.', 12, 'red'),
            ], tight=True, width=430, spacing=12),
            actions=[ft.TextButton('Отмена', on_click=lambda e: self.page.pop_dialog()),
                     self.action('Да, удалить', confirmed, ft.Icons.DELETE_ROUNDED, primary=True)],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    def attendance(self):
        end = date.today()+timedelta(days=1)
        start = end-timedelta(days=32)
        classes = self.db.classes(start.isoformat()+'T00', end.isoformat()+'T00', actor_id=self.user['id'])
        marked = self.db.attendance(actor_id=self.user['id'])
        yes = sum(v=='Был' for v in marked.values())
        no = sum(v=='Пропуск' for v in marked.values())
        ratio = round(100*yes/(yes+no)) if yes+no else 0
        rows = []
        for row in reversed(classes):
            status = marked.get(row['uid'], 'Не отмечено')
            rows.append(ft.Container(border_radius=13, bgcolor=self.c('surface2'), padding=14,
                content=ft.Row(controls=[
                    ft.Container(width=46, height=46, border_radius=13, bgcolor=self.c('soft'),
                                 content=self.icon(ft.Icons.SCHOOL_ROUNDED,'accent',22), alignment=ft.Alignment.CENTER),
                    ft.Column(controls=[self.t(row['title'], 13, bold=True),
                                        self.t(f"{row['start_at'][:10]} · {row['start_at'][11:16]}", 11, 'muted')],
                              spacing=3, expand=True),
                    self.tiny_badge(status, 'green' if status=='Был' else 'red' if status=='Пропуск' else 'muted'),
                    self.action('Был', lambda e, uid=row['uid']: self.mark(uid, 'Был'), small=True),
                    self.action('Пропуск', lambda e, uid=row['uid']: self.mark(uid, 'Пропуск'), small=True),
                ], spacing=8)))
        return [self.title('Посещаемость', 'Отмечай пары и отслеживай личную статистику.'),
                ft.Row(controls=[self.stat(yes, 'Посещено', ft.Icons.CHECK_CIRCLE_ROUNDED, 'green'),
                                 self.stat(no, 'Пропущено', ft.Icons.CANCEL_ROUNDED, 'red'),
                                 self.stat(f'{ratio}%', 'Процент посещаемости', ft.Icons.PERCENT_ROUNDED)],
                       wrap=True, spacing=12),
                self.panel([self.t('Последние занятия (32 дня)', 18, bold=True),
                            *(rows if rows else [self.empty('Нет занятий', 'Подключи расписание или добавь пару.')])]),
                self.t('Статистика ведётся вручную и только локально. Это не официальный журнал МАИ.', 11, 'muted')]

    def mark(self, uid, status):
        self.db.mark_attendance(uid, status, actor_id=self.user['id'])
        self.refresh()

    def resource_editor(self):
        title = self.field('Название материала *')
        subject = self.field('Предмет *')
        url = self.field('Ссылка (необязательно)')
        desc = self.field('Описание', multiline=True)
        chosen = {'file': None}
        file_label = self.t('Файл не выбран · PDF, DOCX, PPTX, ZIP, изображения и др. · до 30 МБ', 11, 'muted')
        async def choose_file():
            try:
                picked = await self.pick_local(MAX_RESOURCE)
                if picked:
                    chosen['file'] = picked
                    file_label.value = f'Прикреплено: {picked[0]} · {len(picked[1]) // 1024} КБ'
                    if not title.value.strip():
                        title.value = Path(picked[0]).stem
                        title.update()
                    file_label.update()
            except (OSError, ValueError) as exc:
                self.notify(str(exc), True)
        def save():
            if not title.value.strip() or not subject.value.strip():
                raise ValueError('Название и предмет обязательны.')
            link = url.value.strip()
            if link and not (link.startswith('https://') or link.startswith('http://')):
                raise ValueError('Ссылка должна начинаться с https:// или http://')
            saved = ''
            try:
                if chosen['file']:
                    saved = self.media.save(*chosen['file'], kind='materials')
                self.db.add_resource(title.value.strip(), subject.value.strip(), link,
                                     desc.value.strip(), saved,
                                     chosen['file'][0] if chosen['file'] else '',
                                     len(chosen['file'][1]) if chosen['file'] else 0, actor_id=self.user['id'])
            except Exception:
                self.media.remove(saved)
                raise
        self.modal('Добавить учебный материал',
                   [title, subject, desc, url,
                    self.action('Загрузить файл с компьютера',
                                lambda e: self.page.run_task(choose_file),
                                ft.Icons.UPLOAD_FILE_ROUNDED), file_label], save,
                   'Файлы копируются в папку mai_pulse_files/materials внутри проекта.')

    def materials(self):
        resources = self.db.resources(actor_id=self.user['id'])
        cards = []
        for item in resources:
            has_file = bool(item['file_path'])
            has_url = bool(item['url'])
            if has_file:
                icon_name = ft.Icons.ATTACH_FILE_ROUNDED
            elif has_url:
                icon_name = ft.Icons.LINK_ROUNDED
            else:
                icon_name = ft.Icons.DESCRIPTION_ROUNDED
            buttons = []
            if has_file:
                buttons.append(self.action('Открыть файл', lambda e, path=item['file_path']: self.open_local(path),
                                           ft.Icons.FILE_OPEN_ROUNDED, small=True))
            if has_url:
                buttons.append(ft.Button('Ссылка', icon=ft.Icons.OPEN_IN_NEW_ROUNDED, url=item['url'],
                                         color=self.c('accent')))
            cards.append(ft.Container(width=316, height=241, border_radius=17, bgcolor=self.c('surface'),
                border=ft.Border.all(1, self.c('line')), padding=16,
                content=ft.Column(controls=[
                    ft.Row(controls=[self.icon(icon_name,'accent',24), ft.Container(expand=True),
                                     (ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=self.c('muted'),
                                                   on_click=lambda e, rid=item['id']: self.remove_resource(rid))
                                      if self.db.can_delete_resource(self.user['id'],item['id']) else ft.Container())]),
                    self.t(item['title'], 14, bold=True, max_lines=2),
                    self.t(item['subject'], 11, 'accent'),
                    self.t(item['description'][:90] or 'Учебный материал', 11, 'muted', max_lines=2),
                    *([self.t(item['file_name'] + ' · ' + str(round(item['file_size']/1024)) + ' КБ',
                               10, 'muted', max_lines=1)] if has_file else []),
                    ft.Container(expand=True),
                    ft.Row(controls=buttons, wrap=True, spacing=8),
                ], spacing=5)))
        return [self.title('Материалы группы', 'Загружай PDF, конспекты и презентации или сохраняй ссылки.',
                           self.action('Загрузить материал', lambda e: self.resource_editor(),
                                       ft.Icons.UPLOAD_FILE_ROUNDED, True)),
                ft.Row(controls=cards, wrap=True, spacing=13, run_spacing=13) if cards else
                self.empty('Материалов нет', 'Прикрепи первый PDF, конспект или ссылку.')]

    def remove_resource(self, rid):
        row = next((r for r in self.db.resources(actor_id=self.user['id']) if r['id'] == rid), None)
        self.db.delete_resource(rid, actor_id=self.user['id'])
        if row and row['file_path']:
            self.media.remove(row['file_path'])
        self.refresh()

    def analytics(self):
        tasks = self.db.tasks(actor_id=self.user['id'])
        subjects = sorted(set(t['subject'] for t in tasks))
        stats = []
        for subj in subjects:
            these = [t for t in tasks if t['subject']==subj]
            done = sum(t['done'] for t in these)
            frac = done/len(these) if these else 0
            stats.extend([ft.Row(controls=[self.t(subj, 13, bold=True), ft.Container(expand=True),
                                           self.t(f'{done} из {len(these)}', 12, 'muted')]),
                          ft.ProgressBar(value=frac, bar_height=10, color=self.c('accent'),
                                         bgcolor=self.c('line'), border_radius=10), ft.Container(height=6)])
        marked = self.db.attendance(actor_id=self.user['id'])
        yes = sum(x == 'Был' for x in marked.values())
        no = sum(x == 'Пропуск' for x in marked.values())
        completed = sum(x['done'] for x in tasks)
        delayed = sum(not x['done'] and x['due_date'] < date.today().isoformat() for x in tasks)
        return [self.title('Аналитика', 'Твои показатели в одной панели.'),
                ft.Row(controls=[self.stat(completed, 'Выполнено заданий', ft.Icons.VERIFIED_ROUNDED,'green'),
                                 self.stat(delayed, 'Просрочено заданий', ft.Icons.WARNING_AMBER_ROUNDED,'red'),
                                 self.stat(yes+no, 'Отмечено посещений', ft.Icons.FACT_CHECK_ROUNDED)],
                       wrap=True, spacing=12),
                self.panel([self.t('Выполнение задач по предметам', 18, bold=True),
                            *(stats if stats else [self.t('Пока нет заданий', 12, 'muted')])]),
                self.panel([self.t('Личная посещаемость', 18, bold=True),
                            ft.ProgressBar(value=(yes/(yes+no)) if yes+no else 0,
                                           color=self.c('green'), bgcolor=self.c('line'),
                                           bar_height=14, border_radius=10),
                            self.t(f'Посещено {yes} · Пропущено {no}', 12, 'muted')])]

    def my_account(self):
        user = self.db.user(self.user['id'])
        role_label = ROLE_NAMES.get(user['role'],user['role'])
        return [self.title('Мой аккаунт', 'Личный профиль и текущие права доступа.'),
                self.panel([
                    ft.Row(controls=[self.icon(ft.Icons.ACCOUNT_CIRCLE_ROUNDED, 'accent', 37),
                                     ft.Column(controls=[self.t(user['display_name'], 20, bold=True),
                                                         self.t('Логин: '+user['login'], 12, 'muted')], spacing=3)]),
                    self.tiny_badge(role_label),
                    self.t('Учебная группа: '+(user['group_code'] or 'не назначена'),12,'muted'),
                    self.action('Заявки на роли',lambda e:self.navigate(7),ft.Icons.ASSIGNMENT_TURNED_IN_ROUNDED),
                    self.t('Модератор видит только «Весь МАИ» и может удалять публикации только в общей ленте.'
                           if user['role']=='moderator' else
                           'Администратор может управлять аккаунтами и всеми публикациями.'
                           if user['role']=='admin' else
                           'Староста и модератор группы управляют только собственной учебной группой.'
                           if user['role'] in ('headman','group_moderator') else
                           'Студент может удалять только свои публикации.', 13, 'muted'),
                    self.action('Выйти из аккаунта', self.logout, ft.Icons.LOGOUT_ROUNDED),
                ], width=630)]

    def new_moderator(self):
        if self.user['role'] != 'admin':
            return
        login = self.field('Логин модератора *')
        name = self.field('Отображаемое имя *')
        password = ft.TextField(label='Отдельный пароль (не менее 8 символов) *',
                                password=True, can_reveal_password=True, filled=True,
                                fill_color=self.c('input'), border_radius=12,
                                border_color=self.c('line'))
        def save():
            self.db.create_moderator_slot(self.user['id'], login.value, name.value, password.value)
        self.modal('Новый слот модератора', [login, name, password], save,
                   'Сохрани пароль перед созданием. В приложении он хранится только в виде защищённого хеша.')

    def new_student(self):
        if self.user['role'] != 'admin':
            return
        login = self.field('Логин студента *')
        name = self.field('Отображаемое имя *')
        password = ft.TextField(label='Пароль студента *', password=True,
                                can_reveal_password=True, filled=True,
                                fill_color=self.c('input'), border_radius=12)
        def save():
            self.db.create_user(self.user['id'], login.value, name.value, password.value)
        self.modal('Аккаунт студента', [login, name, password], save)

    def reset_mod_password(self, mod):
        password = ft.TextField(label='Новый пароль (от 8 символов)', password=True,
                                can_reveal_password=True, filled=True,
                                fill_color=self.c('input'), border_radius=12)
        def save():
            self.db.reset_moderator_password(self.user['id'], mod['id'], password.value)
        self.modal('Новый пароль · '+mod['login'], [password], save,
                   'Старый пароль перестанет действовать. Новый пароль не отображается в списке слотов.')

    def toggle_moderator(self, mod):
        enabling = not bool(mod['is_active'])
        title = 'Включить слот?' if enabling else 'Заблокировать модератора?'
        def confirm(e):
            try:
                self.db.set_moderator_active(self.user['id'], mod['id'], enabling)
            except (ValueError, PermissionError) as exc:
                self.notify(str(exc), True)
                return
            self.page.pop_dialog()
            self.refresh()
            self.notify('Слот активирован.' if enabling else 'Слот заблокирован.')
        self.page.show_dialog(ft.AlertDialog(
            modal=True, bgcolor=self.c('surface'),
            title=self.t(title, 20, bold=True),
            content=self.t(f"{mod['display_name']} (@{mod['login']}). " +
                           ('Он снова сможет войти.' if enabling else 'Он больше не сможет войти или удалять посты.'), 13),
            actions=[ft.TextButton('Отмена', on_click=lambda e: self.page.pop_dialog()),
                     self.action('Подтвердить', confirm, primary=True)],
        ))

    def delete_moderator_dialog(self, mod):
        def confirm(e):
            try:
                self.db.delete_moderator(self.user['id'], mod['id'])
            except (ValueError, PermissionError) as exc:
                self.page.pop_dialog()
                self.notify(str(exc), True)
                return
            self.page.pop_dialog()
            self.refresh()
            self.notify('Аккаунт модератора удалён. Его публикации сохранены.')
        self.page.show_dialog(ft.AlertDialog(modal=True,bgcolor=self.c('surface'),
            title=self.t('Удалить модератора навсегда?',20,bold=True),
            content=ft.Column(controls=[
                self.t(f"{mod['display_name']} (@{mod['login']})",14,bold=True),
                self.t('Логин и пароль больше не будут работать. Это действие необратимо. '
                       'Опубликованные посты и фотографии останутся в ленте.',12,'red')],tight=True,width=420),
            actions=[ft.TextButton('Отмена',on_click=lambda e:self.page.pop_dialog()),
                     self.action('Удалить слот', confirm, ft.Icons.DELETE_FOREVER_ROUNDED,primary=True)]))

    def moderators(self):
        if self.user['role'] != 'admin':
            raise PermissionError('Раздел доступен только администратору.')
        mods = self.db.moderator_slots(self.user['id'])
        rows = []
        for mod in mods:
            active = bool(mod['is_active'])
            rows.append(self.panel([
                ft.Row(controls=[
                    self.icon(ft.Icons.SHIELD_ROUNDED, 'accent', 26),
                    ft.Column(controls=[self.t(mod['display_name'], 16, bold=True),
                                        self.t('@'+mod['login'], 12, 'muted')], spacing=2),
                    ft.Container(expand=True),
                    self.tiny_badge('Активен' if active else 'Заблокирован', 'green' if active else 'red'),
                ]),
                ft.Row(controls=[
                    self.action('Сменить пароль', lambda e, m=mod: self.reset_mod_password(m),
                                ft.Icons.LOCK_RESET_ROUNDED, small=True),
                    self.action('Заблокировать' if active else 'Разблокировать',
                                lambda e, m=mod: self.toggle_moderator(m),
                                ft.Icons.BLOCK_ROUNDED if active else ft.Icons.CHECK_CIRCLE_ROUNDED, small=True),
                    self.action('Удалить', lambda e,m=mod:self.delete_moderator_dialog(m),
                                ft.Icons.DELETE_FOREVER_ROUNDED,small=True),
                ], wrap=True, spacing=8),
            ], width=720))
        return [self.title('Слоты модераторов', 'Создавай независимые аккаунты для контроля общей ленты.',
                           self.action('Добавить слот', lambda e: self.new_moderator(),
                                       ft.Icons.PERSON_ADD_ROUNDED, True)),
                self.panel([
                    self.t('Права модераторов', 17, bold=True),
                    self.t('Модератору доступен только раздел «Весь МАИ»: просмотр, публикации, комментарии, '
                           'удаление любых постов общей ленты с подтверждением. Расписание, задания, материалы '
                           'и объявления группы для него скрыты.', 12, 'muted'),
                    self.action('Создать аккаунт студента', lambda e: self.new_student(),
                                ft.Icons.PERSON_ADD_ALT_1_ROUNDED),
                ], width=720),
                *(rows if rows else [self.empty('Модераторов пока нет',
                                              'Создай первый отдельный слот и передай логин с паролем модератору.')])]

    def role_requests_screen(self):
        records = self.db.role_requests(self.user['id'])
        if self.user['role'] == 'admin':
            rows = []
            for req in records:
                label = 'Модератор всего МАИ' if req['requested_role']=='moderator' else 'Староста'
                actions = []
                if req['status']=='pending':
                    actions = [self.action('Одобрить', lambda e,r=req: self.decide_role(r,True),
                                           ft.Icons.CHECK_CIRCLE_ROUNDED,primary=True,small=True),
                               self.action('Отклонить',lambda e,r=req:self.decide_role(r,False),
                                           ft.Icons.CANCEL_OUTLINED,small=True)]
                rows.append(self.panel([
                    ft.Row(controls=[self.icon(ft.Icons.PENDING_ACTIONS_ROUNDED,'accent',25),
                        ft.Column(controls=[self.t(req['display_name']+' (@'+req['login']+')',14,bold=True),
                            self.t(label+' · '+(req['group_code'] or 'Без группы'),12,'muted')],spacing=3),
                        ft.Container(expand=True),self.tiny_badge({'pending':'На рассмотрении',
                         'approved':'Одобрено','rejected':'Отклонено'}[req['status']])]),
                    self.t(req['message'] or 'Без комментария',12,'muted'),
                    ft.Row(controls=actions,wrap=True)],width=740))
            group_users=[u for u in self.db.users() if u['role'] in ('student','headman','group_moderator')]
            staff=[]
            for u in group_users:
                staff.append(self.panel([ft.Row(controls=[
                    ft.Column(controls=[self.t(u['display_name'],14,bold=True),
                        self.t(f"@{u['login']} · {u['group_code'] or 'Группа не выбрана'} · {ROLE_NAMES.get(u['role'])}",11,'muted')],expand=True),
                    self.action('Группа',lambda e,u=u:self.admin_edit_group(u),
                        ft.Icons.GROUPS_ROUNDED,small=True),
                    self.action('Назначить роль',lambda e,u=u:self.assign_group_staff(u),
                        ft.Icons.MANAGE_ACCOUNTS_ROUNDED,small=True)])],width=740))
            return [self.title('Заявки на роли', 'Решения принимает только администратор.'),
                    *(rows if rows else [self.empty('Заявок пока нет','Студенты могут подать заявку из своего аккаунта.')]),
                    self.title('Управление группами и ролями','Исправляй группу студента и назначай старост или модераторов группы.'),
                    *(staff if staff else [self.empty('Студентов пока нет','Пригласи студентов зарегистрироваться.')])]
        if self.user['role'] == 'moderator':
            return [self.title('Заявки на роли','Ты уже модератор общей ленты.'),
                    self.empty('Роль назначена','Заявка не требуется.')]
        label={'pending':'На рассмотрении','approved':'Одобрено','rejected':'Отклонено'}
        rows=[self.panel([self.t(('Модератор МАИ' if r['requested_role']=='moderator' else 'Староста')+
                        ' — '+label[r['status']],14,bold=True),
                         self.t(r['message'] or 'Без комментария',12,'muted')],width=650) for r in records]
        return [self.title('Заявки на роли','Подай заявку на модератора МАИ или старосту своей группы.',
                          self.action('Подать заявку',lambda e:self.submit_role_request(),
                                      ft.Icons.ADD_ROUNDED,True)),
                self.panel([self.t('Модератор МАИ следит только за общей лентой. Староста редактирует '
                                   'общие задания, пары и объявления только своей учебной группы.',12,'muted')]),
                *(rows if rows else [self.empty('Заявок пока нет','Отправь первую заявку администратору.')])]

    def submit_role_request(self):
        role = self.select('Желаемая роль',['Модератор МАИ','Староста'])
        message = self.field('Почему ты хочешь получить эту роль?',multiline=True)
        def save():
            self.db.create_role_request(self.user['id'],
                'moderator' if role.value=='Модератор МАИ' else 'headman',message.value)
        self.modal('Заявка на роль',[role,message],save,'Заявка появится в панели администратора.')

    def decide_role(self,req,approved):
        def confirm(e):
            try:
                self.db.review_role_request(self.user['id'],req['id'],approved)
            except (ValueError,PermissionError) as exc:
                self.notify(str(exc),True)
                return
            self.page.pop_dialog()
            self.refresh()
            self.notify('Заявка рассмотрена.')
        self.page.show_dialog(ft.AlertDialog(modal=True,bgcolor=self.c('surface'),
            title=self.t('Одобрить заявку?' if approved else 'Отклонить заявку?',19,bold=True),
            content=self.t(req['display_name']+' · '+req['requested_role'],13,'muted'),
            actions=[ft.TextButton('Отмена',on_click=lambda e:self.page.pop_dialog()),
                     self.action('Подтвердить',confirm,primary=True)]))

    def admin_edit_group(self,user):
        group=self.field('Учебная группа *',user['group_code'] or '')
        def save():
            self.db.assign_group(self.user['id'],group.value.strip(),admin_override=True,target_id=user['id'])
        self.modal('Изменить группу · '+user['display_name'],[group],save,
            'Доступ к материалам и заданиям будет переключён на выбранную группу.')

    def assign_group_staff(self,user):
        role = self.select('Роль',['Студент','Староста','Модератор группы'],
             {'student':'Студент','headman':'Староста','group_moderator':'Модератор группы'}[user['role']])
        def save():
            self.db.assign_group_role(self.user['id'],user['id'],
                {'Студент':'student','Староста':'headman','Модератор группы':'group_moderator'}[role.value])
        self.modal('Роль в группе · '+user['display_name'],[role],save,
            'Полномочия распространяются только на закреплённую учебную группу.')

    def settings(self):
        user = self.db.user(self.user['id'])
        name = self.field('Твоё имя',user['display_name'])
        group=self.field('Учебная группа',user['group_code'] or '')
        src=self.db.group_schedule(self.user['id'])
        url=self.field('ICS-календарь группы',src['ics_url'])
        can_edit=self.db.has_group_editor_rights(self.user['id'])
        def save(e):
            try:
                self.db.rename_user(self.user['id'],name.value.strip())
                if self.user['role']=='admin' and group.value.strip() != user['group_code']:
                    self.db.assign_group(self.user['id'],group.value,admin_override=True)
                if can_edit:
                    self.db.set_group_ics(self.user['id'],url.value)
            except (ValueError,PermissionError) as exc:
                self.notify(str(exc),True)
                return
            self.user=self.db.user(self.user['id'])
            self.notify('Настройки сохранены')
            self.setup()
        def sync(e):
            link=self.db.group_schedule(self.user['id'])['ics_url']
            if not link:
                self.notify('Для группы не задана ICS-ссылка.',True)
                return
            self.page.run_task(self._sync_calendar,link,True)
        def switch_theme(e):
            self.theme='light' if self.theme=='dark' else 'dark'
            self.db.set('theme',self.theme)
            self.setup()
        return [self.title('Настройки','Профиль и расписание выбранной группы.'),
                self.panel([self.t('Профиль',18,bold=True),name,
                    self.t('Твоя группа: '+(user['group_code'] or 'не выбрана'),13,'accent'),
                    *([group,self.t('Администратор может переключать группу для просмотра.',11,'muted')]
                       if self.user['role']=='admin' else []),
                    self.action('Сохранить',save,ft.Icons.SAVE_ROUNDED,True)]),
                self.panel([self.t('Расписание группы',18,bold=True),
                    *([url] if can_edit else [self.t('Изменять источник расписания может староста, '
                        'модератор группы или администратор.',12,'muted')]),
                    self.action('Обновить ICS',sync,ft.Icons.SYNC_ROUNDED,True),
                    self.t('Обновлено: '+str(src.get('last_sync') or 'пока не обновлялось'),12,'muted')]),
                self.panel([self.t('Тема приложения',18,bold=True),
                    self.action('Светлая' if self.theme=='dark' else 'Тёмная',switch_theme,ft.Icons.PALETTE_ROUNDED),
                    self.t('MAI Pulse — локальный учебный проект, не официальный сервис МАИ.',11,'muted')])]


def main(page: ft.Page):
    PulseUI(page)
