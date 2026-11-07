import json

from pydantic import TypeAdapter, ValidationError
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .config import SourceConfig

KINDS = {
    "simulator": "Симулятор (тестовые данные)",
    "files": "CSV / JSON",
    "sqlite": "SQLite",
    "postgresql": "PostgreSQL",
    "opc_ua": "OPC UA",
    "modbus_tcp": "Modbus TCP",
}
FIELDS = {
    "files": ("path", "columns", "timestamp_column"),
    "sqlite": ("path", "query", "columns", "timestamp_column", "row_id_column", "row_limit"),
    "postgresql": ("query", "columns", "timestamp_column", "row_id_column", "row_limit"),
    "opc_ua": ("endpoint", "nodes", "subscribe", "username", "client_cert", "client_key", "server_cert"),
    "modbus_tcp": ("host", "port", "device_id", "registers"),
    "simulator": (),
}
LABELS = {
    "id": "ID источника",
    "equipment_id": "ID оборудования",
    "equipment_name": "Название оборудования",
    "interval": "Интервал, с",
    "path": "Путь",
    "columns": "Сопоставление столбцов (JSON)",
    "timestamp_column": "Столбец времени",
    "row_id_column": "Уникальный ID строки",
    "row_limit": "Максимум строк за запрос",
    "query": "Запрос SELECT",
    "endpoint": "Адрес OPC UA",
    "nodes": "Разрешённые узлы (JSON)",
    "subscribe": "Подписка на изменения",
    "username": "Имя пользователя",
    "client_cert": "Сертификат клиента",
    "client_key": "Закрытый ключ клиента",
    "server_cert": "Доверенный сертификат сервера",
    "host": "Адрес устройства",
    "port": "TCP-порт",
    "device_id": "ID Modbus",
    "registers": "Разрешённые регистры (JSON)",
}
EXAMPLES = {
    "columns": {"speed": {"name": "rpm", "unit": "rpm", "data_type": "integer"}},
    "nodes": {"rpm": {"node": "ns=2;s=Speed", "unit": "rpm"}},
    "registers": {"rpm": {"address": 0, "table": "holding", "data_type": "UINT16", "unit": "rpm"}},
}
NUMERIC = {"interval": 2, "row_limit": 1000, "port": 502, "device_id": 1}
PATHS = {"path", "client_cert", "client_key", "server_cert"}


def validation_message(error: Exception):
    if isinstance(error, ValidationError):
        return "\n".join(
            f"{'.'.join(map(str, issue['loc'])) or 'Настройки'} · {issue['msg']}"
            for issue in error.errors(include_input=False, include_url=False)[:8]
        )
    return str(error)


class SourceDialog(QDialog):
    def __init__(self, parent, source=None):
        super().__init__(parent)
        self.original = source
        self.result_source = None
        self.values = source.model_dump(mode="json") if source else {}
        self.setWindowTitle("Настройка источника" if source else "Новый источник")
        self.resize(720, 650)
        outer = QVBoxLayout(self)
        description = QLabel("Укажите оборудование и поля, которые агенту разрешено читать.")
        description.setWordWrap(True)
        outer.addWidget(description)

        self.kind = QComboBox()
        for value, label in KINDS.items():
            self.kind.addItem(label, value)
        self.kind.setCurrentIndex(self.kind.findData(self.values.get("kind", "files")))
        self.kind.setEnabled(source is None)
        outer.addWidget(self.kind)

        area = QScrollArea()
        area.setWidgetResizable(True)
        self.body = QWidget()
        self.fields = QFormLayout(self.body)
        area.setWidget(self.body)
        outer.addWidget(area, 1)

        self.enabled = QCheckBox("Запустить сбор после сохранения")
        self.enabled.setChecked(bool(self.values.get("enabled")))
        self.authorized = QCheckBox("Подтверждаю разрешение на чтение из этого источника")
        outer.addWidget(self.authorized)
        outer.addWidget(self.enabled)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.kind.currentIndexChanged.connect(self.render_fields)
        self.render_fields()

    def render_fields(self):
        while self.fields.rowCount():
            self.fields.removeRow(0)
        self.inputs = {}
        kind = self.kind.currentData()
        for name in ("id", "equipment_id", "equipment_name", "interval", *FIELDS[kind]):
            value = self.values.get(name, NUMERIC.get(name, ""))
            if name == "subscribe":
                control = QCheckBox()
                control.setChecked(self.values.get(name, True))
            elif name in EXAMPLES or name == "query":
                control = QPlainTextEdit()
                control.setMaximumHeight(100)
                if name in EXAMPLES:
                    control.setPlainText(json.dumps(self.values.get(name, EXAMPLES[name]), indent=2))
                else:
                    control.setPlainText(str(value))
                    control.setPlaceholderText(
                        "SELECT id, measured_at, speed FROM approved_view ORDER BY id DESC"
                    )
            else:
                control = QLineEdit("" if value is None else str(value))
                if name == "id" and self.original:
                    control.setReadOnly(True)
                if name == "id" and kind == "simulator" and not self.original:
                    control.setText("simulator")
                if name == "equipment_id" and kind == "simulator" and not self.original:
                    control.setText("sim-pump")
            self.inputs[name] = control
            if name in PATHS:
                self.fields.addRow(LABELS[name], self.path_picker(control, name, kind))
            else:
                self.fields.addRow(LABELS[name], control)

    def path_picker(self, field, name, kind):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(field)
        button = QPushButton("Файл…")
        filter_ = "Данные (*.csv *.json)" if kind == "files" else "Все файлы (*)"

        def select_file():
            path, _ = QFileDialog.getOpenFileName(self, LABELS[name], field.text(), filter_)
            if path:
                field.setText(path)

        button.clicked.connect(select_file)
        layout.addWidget(button)
        if kind == "files":
            folder = QPushButton("Папка…")

            def select_folder():
                path = QFileDialog.getExistingDirectory(self, "Наблюдаемая папка", field.text())
                if path:
                    field.setText(path)

            folder.clicked.connect(select_folder)
            layout.addWidget(folder)
        return row

    def submit(self):
        payload = {
            "kind": self.kind.currentData(),
            "enabled": self.enabled.isChecked(),
            "authorized": self.authorized.isChecked(),
        }
        try:
            if not payload["authorized"]:
                raise ValueError("Подтвердите разрешение на чтение из выбранного источника.")
            for key, widget in self.inputs.items():
                if isinstance(widget, QCheckBox):
                    value = widget.isChecked()
                elif isinstance(widget, QPlainTextEdit):
                    value = widget.toPlainText().strip()
                    if key in EXAMPLES:
                        value = json.loads(value)
                else:
                    value = widget.text().strip()
                    if key in NUMERIC:
                        value = float(value) if key == "interval" else int(value)
                    if key in {"path", "endpoint", "host", "id", "equipment_id"} and not value:
                        raise ValueError(f"Заполните поле «{LABELS[key]}».")
                if value != "" or key in {"id", "equipment_id", "query", "equipment_name"}:
                    payload[key] = value
            self.result_source = TypeAdapter(SourceConfig).validate_python(payload)
        except (ValueError, TypeError) as error:
            QMessageBox.warning(self, "Проверьте настройки источника", validation_message(error))
            return
        self.accept()
