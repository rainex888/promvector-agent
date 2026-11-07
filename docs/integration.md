# Интеграция с центральным PromVector

## Архитектурная оценка

Проверенная база — PromVector FastAPI с миграциями Alembic до `0006`. PostgreSQL содержит площадки `factories`, оборудование, датчики и телеметрию. В этой схеме ID предприятия агента соответствует ID площадки. Административный `POST /api/v1/telemetry` требует операторской cookie-сессии и CSRF. Учётные данные устройств действующего промышленного транспорта отдельны от операторских. REST-контур агента использует собственные токены. Изменения оставляют штатные маршруты и процессы в работе.

Сервис `services.telemetry.ingest` пишет числовые измерения в штатную таблицу `telemetry`, проверяет уникальность пары `sensor_id, at` и в той же транзакции ставит задачу аналитики и уведомляет WebSocket. Расширение повторно использует этот сервис. Миграция добавляет таблицу регистрации и квитанции с исходным сообщением в **той же базе**. Строковые состояния и данные плохого или неопределённого качества сохраняются в квитанции. В штатный график поступают числовые метрики хорошего качества с разрешённым кодом датчика и совпадающей единицей. Квитанции также содержат исходные метаданные и признак симулятора.

## Подготовка отдельного изменения бэкенда

Создайте отдельную ветку центрального репозитория и проверьте патч перед применением. Скрипт проверяет опорные фрагменты и выводит unified diff. Выполняйте миграцию сначала на выделенной тестовой базе.

```sh
python scripts/prepare_backend_patch.py /path/to/promvector/backend > /tmp/promvector-edge.patch
cd /path/to/promvector
git apply --check --directory=backend /tmp/promvector-edge.patch
git apply --directory=backend /tmp/promvector-edge.patch
cd backend
uv run alembic upgrade head
uv run alembic check
```

Команды `git apply --directory=backend` запускайте **из корня центрального Git-репозитория**, а команды Alembic — из `backend`. Для отдельной копии бэкенда без Git выполните `git apply /tmp/promvector-edge.patch` из её корня без `--directory`. Патч рассчитан на миграцию 0006. Для более свежей ветки согласуйте новую ревизию Alembic и просмотрите пересечения.

Проверьте TLS на reverse proxy, максимальный размер входящего JSON и доступность маршрутов. В продакшене открывайте REST по HTTPS. Храните токены приглашения в защищённом канале и не записывайте в журналы. У клиента можно указать доверенный частный CA.

```sh
python -m promvector.agent_admin provision AGENT_UUID FACTORY_ID EQUIPMENT_ID [OTHER_EQUIPMENT_ID]
python -m promvector.agent_admin revoke AGENT_UUID
```

Приглашение по умолчанию действует 15 минут. Повтор того же запроса с тем же ключом агента допустим в пределах срока, например после потери ответа. Другой ключ отвергается. Отзыв блокирует все последующие запросы. Для симулятора создайте отдельное оборудование с именем, начинающимся с `[SIMULATOR] `, настройте его датчики и добавьте `--allow-simulated` при выдаче приглашения. В агенте отдельно включите отправку симуляции.

## REST-контракт v1

| Метод | Тело запроса / ответ |
| --- | --- |
| `POST /api/v1/agents/register` | `enterprise_id`, `agent_id`, `agent_token`, `version` → `enterprise_id`, `agent_id` |
| `POST /api/v1/agents/heartbeat` | `version`, `pending` → `{"status":"ok"}` |
| `GET /api/v1/agents/configuration` | → `enterprise_id`, `agent_id`, `equipment_ids`, `allow_simulated` |
| `POST /api/v1/telemetry/batch` | `{"messages":[Telemetry, …]}` → `{"receipts":[{"message_id":"…","status":"accepted"}]}` |

Регистрация использует Bearer-приглашение, остальные маршруты — индивидуальный Bearer-токен агента. `Telemetry` описан в [Pydantic-модели](../promvector_agent/models.py). Время приводится к UTC, `schema_version` равен `1.0`, отсутствующая единица представляется `null`. Размер пакета от 1 до 500 сообщений.

`accepted` подтверждает коммит. `duplicate` подтверждает совпадающее сохранённое сообщение. `rejected` включает код причины и оставляет сообщение в локальном списке отклонений. После исправления конфигурации эти сообщения можно повторно отправить из раздела «Связь». 401, 403, транспортный сбой и несовпадающее подтверждение оставляют пакет в очереди. Повтор использует исходный ID и время. Сервер блокирует запись за пределами назначенной площадки и оборудования.

События агента записываются локально. Серверный REST-маршрут событий не входит в данный контракт.

## Сквозной тест

Подготовьте копию бэкенда с патчем, отдельным PostgreSQL и применёнными миграциями. Тест создаёт тестовое оборудование, сертификат для loopback и процесс FastAPI. Указывайте только отдельную тестовую базу. `PV_EDGE_BACKEND_PYTHONPATH` должен содержать путь к бэкенду и, если нужно, путь к его site-packages для Python 3.12.

```sh
export PV_EDGE_TEST_DSN='postgresql://USER@localhost/promvector_agent_test'
export PV_EDGE_BACKEND_STAGE='/path/to/patched/backend'
export PV_EDGE_BACKEND_PYTHONPATH='/path/to/patched/backend:/path/to/backend/.venv/lib/python3.12/site-packages'
uv run --extra test pytest -q tests/test_backend_e2e.py
```
