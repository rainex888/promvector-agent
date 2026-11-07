from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap, QPolygon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStatusBar,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .ai import gemini_secret
from .collectors.databases import source_secret
from .communication import Configuration
from .config import Settings, save_settings
from .runtime import Runtime
from .source_dialog import KINDS, SourceDialog, validation_message
from .worker import Worker

PAGES = ("Обзор", "Источники", "Оборудование", "Мониторинг", "Gemini AI", "Связь", "Настройки")
DESCRIPTIONS = (
    "Состояние локального агента и очереди телеметрии.",
    "Настройте разрешённые подключения и проверьте чтение данных.",
    "Оборудование, привязанное к локальным источникам.",
    "Последние измерения, сохранённые на этом компьютере.",
    "Результаты анализа Gemini. Гипотезы модели отделены от измерений.",
    "Регистрация, HTTPS-доставка и подтверждения центральной платформы.",
    "Идентификатор предприятия, транспорт, приватность и журналы.",
)
STATUS = {
    "collecting": "Сбор данных",
    "connected": "Подключён",
    "starting": "Запускается",
    "disabled": "Отключён",
    "error": "Ошибка",
    "setup_required": "Нужна настройка",
    "reachable": "Доступен",
    "unknown": "Не проверено",
    "registered": "Зарегистрирован",
    "denied": "Доступ отклонён",
    "unregistered": "Не зарегистрирован",
    "delivered": "Доставлено",
    "pending": "Ожидает отправки",
    "blocked": "Отклонено сервером",
    "good": "Хорошее",
    "bad": "Плохое",
    "uncertain": "Неопределённое",
    "available": "Доступен",
}


def label(value="", role=""):
    item = QLabel(str(value))
    item.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        item.setObjectName(role)
    return item


def panel(title):
    card = QFrame()
    card.setObjectName("Panel")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(18, 14, 18, 16)
    layout.addWidget(label(title, "PanelTitle"))
    return card, layout


def icon():
    image = QPixmap(48, 48)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#27c49a"))
    painter.drawRoundedRect(0, 0, 48, 48, 12, 12)
    painter.setBrush(QColor("#0b2626"))
    painter.drawPolygon(
        QPolygon(
            [
                QPoint(9, 30),
                QPoint(20, 13),
                QPoint(26, 23),
                QPoint(32, 15),
                QPoint(39, 30),
                QPoint(31, 30),
                QPoint(26, 37),
                QPoint(19, 27),
                QPoint(16, 32),
            ]
        )
    )
    painter.end()
    return QIcon(image)


def button(text, action, primary=False):
    control = QPushButton(text)
    control.clicked.connect(action)
    if primary:
        control.setObjectName("Primary")
    return control


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.verticalHeader().hide()
    widget.setAlternatingRowColors(True)
    widget.setShowGrid(False)
    widget.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    widget.setMinimumHeight(180)
    return widget


def fill(widget, rows):
    selected = widget.currentItem()
    selection = widget.item(selected.row(), 0).text() if selected else None
    widget.setUpdatesEnabled(False)
    widget.setRowCount(len(rows))
    for index, row in enumerate(rows):
        for column, value in enumerate(row):
            cell = QTableWidgetItem(str(value))
            cell.setToolTip(str(value))
            widget.setItem(index, column, cell)
        if selection is not None and str(row[0]) == selection:
            widget.selectRow(index)
    widget.setUpdatesEnabled(True)


def style(dark):
    bg, nav, surface, hover, text, muted, border = (
        ("#111a25", "#0d1520", "#182534", "#213447", "#f0f5fc", "#9eafc2", "#2a3b4e")
        if dark
        else ("#f0f5f8", "#e5eef3", "#ffffff", "#eaf3f4", "#132338", "#496078", "#cad8e3")
    )
    return f"""
    QWidget {{ color: {text}; font-size: 13px; }}
    QMainWindow, QStackedWidget, QScrollArea, QWidget#Page {{ background: {bg}; border: 0; }}
    QDialog, QMessageBox, QMenu {{ background: {surface}; }}
    QFrame#Nav {{ background: {nav}; border-right: 1px solid {border}; }}
    QFrame#Panel {{ background: {surface}; border: 1px solid {border}; border-radius: 11px; }}
    QLabel#Brand {{ font-size: 19px; font-weight: 700; }}
    QLabel#Kicker {{ color: {muted}; font-size: 11px; font-weight: 700; }}
    QLabel#Title {{ font-size: 33px; font-weight: 700; }}
    QLabel#Subtitle, QLabel#Secondary {{ color: {muted}; }}
    QLabel#PanelTitle {{ color: {muted}; font-weight: 600; }}
    QLabel#Value {{ font-size: 24px; font-weight: 700; }}
    QLabel#Indicator {{ color: {"#61dfb9" if dark else "#006753"}; font-weight: 700; }}
    QPushButton {{ background: {surface}; padding: 8px 12px; border: 1px solid {border};
                   border-radius: 7px; font-weight: 600; min-height: 16px; }}
    QPushButton:hover {{ background: {hover}; }}
    QPushButton:disabled {{ color: {muted}; }}
    QPushButton#Primary {{ background: #27c49a; color: #092724; border: 1px solid #27c49a; }}
    QPushButton#Primary:hover {{ background: #65e3bd; }}
    QPushButton#NavButton {{ background: transparent; border-color: transparent; text-align: left;
                             font-weight: 500; padding: 10px 12px; }}
    QPushButton#NavButton:checked, QPushButton#NavButton:hover {{ background: {hover}; }}
    QPushButton#NavButton:checked {{ border-left: 3px solid #27c49a; }}
    QLineEdit, QPlainTextEdit, QComboBox {{ background: {surface}; border: 1px solid {border};
                                           border-radius: 6px; padding: 6px; min-height: 20px; }}
    QTableWidget {{ background: {surface}; alternate-background-color: {hover};
                    border: 1px solid {border}; border-radius: 8px; selection-background-color: #19735e;
                    selection-color: white; }}
    QTableWidget::item {{ padding: 5px; }}
    QHeaderView::section {{ color: {muted}; background: {surface}; border: 0; padding: 8px;
                            font-weight: 600; }}
    QStatusBar {{ background: {nav}; color: {muted}; }}
    QPushButton:focus, QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{
        border: 1px solid #27c49a;
    }}
    """


class Window(QMainWindow):
    def __init__(self, path: Path, runtime: Runtime):
        super().__init__()
        self.path = path
        self.queue = runtime.queue
        self.settings = runtime.settings
        self.worker = Worker(runtime)
        self.worker.completed.connect(self.completed)
        self.is_exiting = False
        self.setWindowTitle("PromVector Agent")
        self.setWindowIcon(icon())
        self.resize(1180, 780)
        self.setMinimumSize(880, 590)
        self.setStatusBar(QStatusBar())

        body = QWidget()
        outer = QHBoxLayout(body)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("Nav")
        sidebar.setFixedWidth(210)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(16, 20, 16, 20)
        nav.setSpacing(7)
        brand = QHBoxLayout()
        brand_icon = label()
        brand_icon.setPixmap(self.windowIcon().pixmap(35, 35))
        brand.addWidget(brand_icon)
        brand.addWidget(label("PromVector", "Brand"), 1)
        nav.addLayout(brand)
        nav.addWidget(label("ЛОКАЛЬНЫЙ АГЕНТ", "Kicker"))
        nav.addSpacing(19)

        self.stack = QStackedWidget()
        self.navigation = []
        for index, name in enumerate(PAGES):
            link = button(f"{index + 1:02d}    {name}", lambda checked=False, i=index: self.select(i))
            link.setObjectName("NavButton")
            link.setCheckable(True)
            self.navigation.append(link)
            nav.addWidget(link)
        nav.addStretch()
        nav.addWidget(label(f"Версия {__version__}", "Kicker"))
        outer.addWidget(sidebar)
        outer.addWidget(self.stack, 1)
        self.setCentralWidget(body)
        for page, factory in enumerate(
            (
                self.overview,
                self.sources_page,
                self.equipment_page,
                self.monitor_page,
                self.ai_page,
                self.communication_page,
                self.settings_page,
            )
        ):
            area = QScrollArea()
            area.setWidgetResizable(True)
            page_body = QWidget()
            page_body.setObjectName("Page")
            layout = QVBoxLayout(page_body)
            layout.setContentsMargins(29, 23, 29, 25)
            layout.setSpacing(14)
            layout.addWidget(label(PAGES[page], "Title"))
            desc = label(DESCRIPTIONS[page], "Subtitle")
            desc.setWordWrap(True)
            layout.addWidget(desc)
            factory(layout)
            area.setWidget(page_body)
            self.stack.addWidget(area)

        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            self.tray.setToolTip("PromVector Agent")
            menu = QMenu(self)
            menu.addAction("Открыть", self.showNormal)
            menu.addAction("Завершить", self.quit)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(
                lambda reason: (
                    self.showNormal() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
                )
            )
            self.tray.show()

        self.apply_theme()
        self.worker.start()
        self.select(0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1250)

    def select(self, index):
        for number, link in enumerate(self.navigation):
            link.setChecked(index == number)
        self.stack.setCurrentIndex(index)
        self.refresh()

    def overview(self, layout):
        self.tiles = {}
        grid = QGridLayout()
        for index, key in enumerate(
            (
                "Собрано измерений",
                "Доставлено",
                "В очереди",
                "Подключено машин",
                "Активных источников",
                "Отклонено",
            )
        ):
            card, contents = panel(key)
            value = label("0", "Value")
            self.tiles[key] = value
            contents.addWidget(value)
            grid.addWidget(card, index // 3, index % 3)
        layout.addLayout(grid)
        card, contents = panel("Установка")
        self.overview_info = label()
        self.overview_info.setWordWrap(True)
        self.overview_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        contents.addWidget(self.overview_info)
        layout.addWidget(card)
        layout.addStretch()

    def sources_page(self, layout):
        controls = QHBoxLayout()
        for text, action, primary in (
            ("Добавить источник", self.add_source, True),
            ("Изменить", self.edit_source, False),
            ("Проверить", self.test_source, False),
            ("Включить / отключить", self.toggle_source, False),
            ("Учётные данные", self.source_credential, False),
        ):
            controls.addWidget(button(text, action, primary))
        controls.addStretch()
        layout.addLayout(controls)
        self.source_table = table(
            ("ID источника", "Тип", "Оборудование", "Интервал", "Состояние", "Диагностика")
        )
        layout.addWidget(self.source_table, 1)

    def equipment_page(self, layout):
        self.equipment_table = table(
            ("ID оборудования", "Название", "Источник", "Состояние", "Метрики", "Последний замер UTC")
        )
        layout.addWidget(self.equipment_table, 1)

    def monitor_page(self, layout):
        filters = QHBoxLayout()
        self.equipment_filter = QLineEdit()
        self.equipment_filter.setPlaceholderText("ID оборудования")
        self.metric_filter = QLineEdit()
        self.metric_filter.setPlaceholderText("Код метрики")
        for entry in (self.equipment_filter, self.metric_filter):
            entry.textChanged.connect(self.refresh)
            filters.addWidget(entry)
        filters.addStretch()
        layout.addLayout(filters)
        self.monitor_table = table(
            ("UTC", "Оборудование", "Метрика", "Значение", "Ед.", "Качество", "Тип", "Доставка")
        )
        layout.addWidget(self.monitor_table, 1)

    def ai_page(self, layout):
        card, contents = panel("Анализ выбранных данных")
        self.ai_status = label("", "Indicator")
        contents.addWidget(self.ai_status)
        self.ai_mode = QComboBox()
        self.ai_mode.addItem("Структура и метаданные", "metadata")
        self.ai_mode.addItem("Измерения и возможные аномалии", "measurements")
        contents.addWidget(self.ai_mode)
        self.ai_input = QPlainTextEdit()
        self.ai_input.setPlaceholderText(
            "Вставьте только проверенный текст, разрешённый для отправки Gemini."
        )
        self.ai_input.setMaximumHeight(125)
        contents.addWidget(self.ai_input)
        actions = QHBoxLayout()
        actions.addWidget(button("Запросить анализ", self.analyze, True))
        actions.addWidget(button("Сохранить ключ Gemini", self.ai_credential))
        actions.addStretch()
        contents.addLayout(actions)
        self.ai_result = QPlainTextEdit()
        self.ai_result.setReadOnly(True)
        self.ai_result.setPlaceholderText("Результат запроса появится здесь.")
        self.ai_result.setMaximumHeight(190)
        contents.addWidget(self.ai_result)
        layout.addWidget(card)
        self.ai_table = table(("UTC", "Тип запроса", "Модель", "Вывод Gemini", "Возможные аномалии"))
        layout.addWidget(self.ai_table, 1)

    def communication_page(self, layout):
        card, contents = panel("Центральный PromVector")
        self.connection_info = label()
        self.connection_info.setWordWrap(True)
        self.connection_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        contents.addWidget(self.connection_info)
        actions = QHBoxLayout()
        actions.addWidget(button("Зарегистрировать агент", self.register, True))
        actions.addWidget(button("Проверить HTTPS", self.check_connection))
        actions.addWidget(button("Повторить отклонённые", self.retry))
        actions.addStretch()
        contents.addLayout(actions)
        layout.addWidget(card)
        self.blocked_table = table(("UTC", "Оборудование", "Состояние", "Код отказа"))
        layout.addWidget(self.blocked_table, 1)

    def settings_page(self, layout):
        card, contents = panel("Установка и передача")
        self.inputs = {}
        form = QFormLayout()
        values = self.settings.model_dump(mode="json")
        backend = values["backend"]
        gemini = values["gemini"]
        for key, text, value in (
            ("enterprise_id", "ID предприятия / площадки", values["enterprise_id"]),
            ("url", "HTTPS-адрес бэкенда", backend["url"]),
            ("interval", "Отправка каждые, с", backend["interval"]),
            ("heartbeat_interval", "Проверка связи каждые, с", backend["heartbeat_interval"]),
            ("batch_size", "Максимум сообщений в пакете", backend["batch_size"]),
            ("connect_timeout", "Таймаут подключения, с", backend["connect_timeout"]),
            ("request_timeout", "Таймаут запроса, с", backend["request_timeout"]),
            ("ca_file", "Сертификат доверенного CA (опционально)", backend["ca_file"]),
            ("model", "Идентификатор модели Gemini", gemini["model"]),
        ):
            item = QLineEdit("" if value is None else str(value))
            self.inputs[key] = item
            if key == "ca_file":
                row = QWidget()
                ca = QHBoxLayout(row)
                ca.setContentsMargins(0, 0, 0, 0)
                ca.addWidget(item)
                ca.addWidget(button("Выбрать…", self.select_ca))
                form.addRow(text, row)
            else:
                form.addRow(text, item)
        self.theme = QComboBox()
        for key, text in (("system", "Системная"), ("light", "Светлая"), ("dark", "Тёмная")):
            self.theme.addItem(text, key)
        self.theme.setCurrentIndex(self.theme.findData(self.settings.theme))
        form.addRow("Тема", self.theme)
        for key, text, enabled in (
            ("background", "При закрытии оставлять агент в системном трее", values["background"]),
            ("send_simulated", "Разрешить отправку тестовой телеметрии", backend["send_simulated"]),
            ("enabled", "Включить Gemini", gemini["enabled"]),
            (
                "authorize_metadata",
                "Разрешаю анализ метаданных при явном запросе",
                gemini["authorize_metadata"],
            ),
            (
                "authorize_measurements",
                "Разрешаю анализ измерений при явном запросе",
                gemini["authorize_measurements"],
            ),
        ):
            item = QCheckBox(text)
            item.setChecked(enabled)
            self.inputs[key] = item
            form.addRow(item)
        contents.addLayout(form)
        actions = QHBoxLayout()
        actions.addWidget(button("Сохранить настройки", self.save, True))
        actions.addStretch()
        contents.addLayout(actions)
        layout.addWidget(card)
        self.log_table = table(("UTC", "Событие", "Описание"))
        layout.addWidget(label(f"Журнал агента · {self.path.parent / 'agent.log'}", "Secondary"))
        layout.addWidget(self.log_table, 1)

    def select_ca(self):
        path, _ = QFileDialog.getOpenFileName(self, "Сертификат центра сертификации", "", "PEM (*.pem *.crt)")
        if path:
            self.inputs["ca_file"].setText(path)

    def apply_theme(self):
        app = QApplication.instance()
        system_dark = app.styleHints().colorScheme() == Qt.ColorScheme.Dark
        dark = self.settings.theme == "dark" or self.settings.theme == "system" and system_dark
        self.setStyleSheet(style(dark))
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#182534" if dark else "#ffffff"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#f0f5fc" if dark else "#132338"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#182534" if dark else "#ffffff"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#f0f5fc" if dark else "#132338"))
        self.setPalette(palette)

    def save(self):
        data = self.settings.model_dump(mode="json")

        def text(key):
            return self.inputs[key].text().strip()

        def checked(key):
            return self.inputs[key].isChecked()

        try:
            data.update(
                enterprise_id=text("enterprise_id") or None,
                theme=self.theme.currentData(),
                background=checked("background"),
            )
            data["backend"].update(
                url=text("url"),
                ca_file=text("ca_file") or None,
                interval=float(text("interval")),
                heartbeat_interval=float(text("heartbeat_interval")),
                batch_size=int(text("batch_size")),
                connect_timeout=float(text("connect_timeout")),
                request_timeout=float(text("request_timeout")),
                send_simulated=checked("send_simulated"),
            )
            data["gemini"].update(
                model=text("model"),
                enabled=checked("enabled"),
                authorize_metadata=checked("authorize_metadata"),
                authorize_measurements=checked("authorize_measurements"),
            )
            self.apply(Settings.model_validate(data))
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Проверьте настройки", validation_message(error))

    def apply(self, settings):
        save_settings(self.path, settings)
        self.settings = settings
        self.queue.log("settings_saved", "Local configuration updated")
        self.worker.submit("Настройки", lambda: self.worker.replace(settings))
        self.apply_theme()
        self.refresh()

    def current_source(self):
        row = self.source_table.currentRow()
        if row >= 0 and (item := self.source_table.item(row, 0)):
            return next((source for source in self.settings.sources if source.id == item.text()), None)
        self.statusBar().showMessage("Выберите источник в списке.", 4000)
        return None

    def add_source(self):
        self.source_editor(None)

    def edit_source(self):
        if source := self.current_source():
            self.source_editor(source)

    def source_editor(self, source):
        dialog = SourceDialog(self, source)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.store_source(dialog.result_source)

    def store_source(self, source):
        data = self.settings.model_dump()
        data["sources"] = [item for item in data["sources"] if item["id"] != source.id] + [source]
        try:
            self.apply(Settings.model_validate(data))
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Источник", validation_message(error))

    def toggle_source(self):
        if source := self.current_source():
            if not source.authorized:
                self.source_editor(source)
            else:
                self.store_source(source.model_copy(update={"enabled": not source.enabled}))

    def test_source(self):
        if source := self.current_source():

            async def test():
                await self.worker.runtime.test(source)
                return f"{source.id}: проверка чтения пройдена."

            self.worker.submit("Проверка источника", test)

    def ask_secret(self, title, prompt):
        secret, confirmed = QInputDialog.getText(self, title, prompt, QLineEdit.EchoMode.Password)
        return secret if confirmed and secret.strip() else None

    def source_credential(self):
        source = self.current_source()
        if not source:
            return
        if source.kind not in ("postgresql", "opc_ua"):
            self.statusBar().showMessage("Для выбранного типа отдельный пароль не используется.", 5000)
            return
        prompt = "DSN PostgreSQL с sslmode=verify-full" if source.kind == "postgresql" else "Пароль OPC UA"
        if secret := self.ask_secret("Хранилище ОС", prompt):
            self.save_credential(lambda runtime: source_secret(source, runtime.namespace), secret)

    def ai_credential(self):
        if secret := self.ask_secret("Хранилище ОС", "Ключ Gemini API"):
            self.save_credential(lambda runtime: gemini_secret(runtime.namespace), secret)

    def save_credential(self, key, secret):
        async def save():
            runtime = self.worker.runtime
            runtime.vault.set(key(runtime), secret)
            self.queue.log("credential_saved", "Credential updated in OS store")
            return "Учётные данные сохранены в защищённом хранилище ОС."

        self.worker.submit("Учётные данные", save)

    def register(self):
        if not self.settings.backend.url or not self.settings.enterprise_id:
            self.statusBar().showMessage("Сначала сохраните ID предприятия и HTTPS-адрес.", 5000)
            return
        prompt = f"ID агента {self.settings.agent_id}\nОдноразовый токен регистрации"
        if secret := self.ask_secret("Регистрация агента", prompt):

            async def register_agent():
                await self.worker.runtime.communication.register(secret)
                return "Регистрация подтверждена сервером."

            self.worker.submit("Регистрация", register_agent)

    def check_connection(self):
        self.worker.submit("Проверка HTTPS", lambda: self.worker.runtime.communication.check())

    def retry(self):
        async def requeue():
            agent = self.worker.runtime.communication
            if not agent.enterprise:
                return "Сначала настройте предприятие."
            count = self.queue.requeue_blocked(agent.enterprise, agent.agent, agent.scope)
            self.queue.log("requeue", f"{count} message(s) moved to pending")
            return f"Возвращено в очередь: {count}."

        self.worker.submit("Очередь", requeue)

    def analyze(self):
        mode, text = self.ai_mode.currentData(), self.ai_input.toPlainText()
        if not self.settings.gemini.enabled or not text.strip():
            self.statusBar().showMessage("Включите Gemini в настройках и введите текст.", 5000)
            return
        result = QMessageBox.question(
            self,
            "Отправка данных в Gemini",
            "Отправить введённый текст в Google Gemini? Подтвердите, что у вас есть разрешение.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            self.ai_result.setPlainText("Выполняется запрос…")
            self.worker.submit(
                "Анализ Gemini", lambda: self.worker.intelligence.analyze(mode, text, approved=True)
            )

    def completed(self, action, result, error):
        if error:
            if action == "Анализ Gemini":
                self.ai_result.setPlainText(
                    f"Запрос не выполнен ({error}). Проверьте разрешения, модель и ключ."
                )
            self.statusBar().showMessage(f"{action} · {error}", 9000)
        elif isinstance(result, Configuration):
            ids = ", ".join(result.equipment_ids)
            self.statusBar().showMessage(f"HTTPS доступен. Разрешённое оборудование: {ids}", 9000)
        elif action == "Анализ Gemini":
            advice, cached = result
            text = advice.model_dump(mode="json")
            details = [
                text["summary"],
                *(f"{i['field']} · {i['possible_type']} · {i['explanation']}" for i in text["findings"]),
                *text["anomalies"],
                *text["limitations"],
            ]
            self.ai_result.setPlainText(
                ("Gemini · из кэша" if cached else "Gemini · ответ модели") + "\n\n" + "\n\n".join(details)
            )
        else:
            self.statusBar().showMessage(str(result), 6000)
        self.refresh()

    def refresh(self):
        page = self.stack.currentIndex()
        try:
            self.render_status(page)
        except Exception as error:
            self.statusBar().showMessage(f"Локальное хранилище · {type(error).__name__}", 5000)

    def render_status(self, page):
        state = self.queue.state()
        sources = self.queue.sources()
        config = self.settings.sources
        registered = (
            STATUS.get(state.get("auth"), "Не настроено") if self.settings.backend.url else "Не настроено"
        )
        backend = (
            STATUS.get(state.get("backend"), "Не проверено") if self.settings.backend.url else "Не настроен"
        )
        sync = state.get("last_sync") or "—"
        gemini = (
            STATUS.get(state.get("gemini_status"), "Ожидает запроса")
            if self.settings.gemini.enabled
            else "Отключён"
        )
        if page == 0:
            counts = self.queue.counts()
            active = [
                s
                for s in config
                if s.enabled and sources.get(s.id, {}).get("status") in ("collecting", "connected")
            ]
            for name, value in zip(
                self.tiles,
                (
                    self.queue.total_measurements(),
                    counts.get("delivered", 0),
                    counts.get("pending", 0),
                    len({s.equipment_id for s in active if s.kind != "simulator"}),
                    len(active),
                    counts.get("blocked", 0),
                ),
                strict=True,
            ):
                self.tiles[name].setText(f"{value:,}".replace(",", " "))
            self.overview_info.setText(
                f"Агент: {'Запущен' if self.worker.isRunning() else 'Остановлен'}     •     Предприятие: "
                f"{self.settings.enterprise_id or 'Не настроено'}\nID агента: {self.settings.agent_id}\n"
                f"HTTPS: {backend}     •     Авторизация: {registered}     •     Gemini: {gemini}\n"
                f"Последняя синхронизация: {sync}"
            )
        elif page == 1:
            fill(
                self.source_table,
                [
                    (
                        s.id,
                        KINDS[s.kind],
                        s.equipment_id,
                        f"{s.interval:g} с",
                        STATUS.get(sources.get(s.id, {}).get("status"), "Ожидание"),
                        sources.get(s.id, {}).get("detail") or "—",
                    )
                    for s in config
                ],
            )
        elif page == 2:
            fill(
                self.equipment_table,
                [
                    (
                        s.equipment_id,
                        ("[СИМУЛЯТОР] " if s.kind == "simulator" else "") + (s.equipment_name or "—"),
                        s.id,
                        STATUS.get(sources.get(s.id, {}).get("status"), "Ожидание"),
                        ", ".join(last.metrics) if (last := self.queue.latest(s.equipment_id, s.id)) else "—",
                        last.timestamp.isoformat() if last else "—",
                    )
                    for s in config
                ],
            )
        elif page == 3:
            equipment, metric = self.equipment_filter.text().strip(), self.metric_filter.text().strip()
            fill(
                self.monitor_table,
                [
                    (
                        item.timestamp.isoformat(),
                        item.equipment_id,
                        name,
                        reading.value,
                        reading.unit or "—",
                        STATUS[item.quality],
                        "СИМУЛЯТОР" if item.simulated else KINDS[item.source],
                        STATUS[delivery] + (f" · {reason}" if reason else ""),
                    )
                    for item, delivery, reason in self.queue.history(equipment, metric, 120)
                    for name, reading in item.metrics.items()
                    if not metric or name == metric
                ][:600],
            )
        elif page == 4:
            self.ai_status.setText(f"Gemini · {gemini}")
            fill(
                self.ai_table,
                [
                    (
                        stamp,
                        entry["mode"],
                        entry["model"],
                        entry["advice"]["summary"],
                        " · ".join(entry["advice"]["anomalies"]),
                    )
                    for stamp, entry in self.queue.ai_history()
                ],
            )
        elif page == 5:
            counts = self.queue.counts()
            delay = state.get("latency_ms")
            self.connection_info.setText(
                f"Адрес: {self.settings.backend.url or 'Не задан'}\nHTTPS: {backend}     •     Авторизация: {registered}\n"
                f"Синхронизация: {sync}     •     Heartbeat: {state.get('last_heartbeat') or '—'}\n"
                f"Задержка: {str(delay) + ' мс' if delay is not None else '—'}     •     "
                f"В очереди: {counts.get('pending', 0)}     •     Ошибок запросов: {state.get('failed_requests', 0)}\n"
                f"Последняя ошибка: {state.get('last_error') or '—'}"
            )
            fill(
                self.blocked_table,
                [
                    (item.timestamp.isoformat(), item.equipment_id, STATUS[status], reason)
                    for item, status, reason in self.queue.history(limit=250, status="blocked")
                ],
            )
        else:
            fill(
                self.log_table,
                [(row["timestamp"], row["event"], row["detail"]) for row in self.queue.audit()],
            )

    def quit(self):
        self.is_exiting = True
        self.close()

    def closeEvent(self, event):
        if self.settings.background and self.tray and not self.is_exiting:
            self.hide()
            event.ignore()
            return
        self.timer.stop()
        self.worker.shutdown()
        self.worker.wait()
        event.accept()
        QApplication.instance().quit()


def launch(path, runtime):
    app = QApplication([])
    app.setApplicationName("PromVector Agent")
    app.setWindowIcon(icon())
    window = Window(path, runtime)
    window.show()
    return app.exec()
