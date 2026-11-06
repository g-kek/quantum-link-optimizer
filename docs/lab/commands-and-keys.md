# Передача команд и макет ключевого сервиса


```mermaid
sequenceDiagram
    participant S as Sender
    participant K as KMS
    participant R as Receiver
    S->>K: issue(session, path), старт / ротация
    K-->>S: key ID и тестовый ключ
    S->>R: AEAD: ID, sequence, set(value), deadline
    R->>K: get(key ID), если нет в cache
    K-->>R: согласованный ключ
    R->>R: SQLite: дедупликация и set в одной транзакции
    R-->>S: AEAD ACK executed / expired / stale / conflict
```

## Запуск на MacBook без VM

```bash
uv sync --extra lab
mkdir -p artifacts/commands
```

Три терминала из корня репозитория (для повторов нужны новые файлы журналов):

```bash
uv run --extra lab python -m quantum_link_optimizer.kms \
  --config lab/kms/normal.json --events artifacts/commands/kms.jsonl
```

```bash
uv run --extra lab python -m quantum_link_optimizer.commands receive \
  --events artifacts/commands/receiver.jsonl --state artifacts/commands/state.sqlite
```

```bash
uv run --extra lab python -m quantum_link_optimizer.commands send \
  --path A --remote 127.0.0.1:9501 --count 20 --rotate-after 5 \
  --events artifacts/commands/sender.jsonl
```

Для B: `--path B --remote 127.0.0.1:9502`. Оба приёмных сокета отдельные.
Время сессии и ID команды записываются в журнал. При повторе запуска receiver
с тем же SQLite сохраняется дедупликация; новый sender создаёт новую сессию.

## В namespaces Linux

После установки `cryptography` в интерпретаторе Linux runner:

```bash
sudo env PYTHONPATH=src python3 -m quantum_link_optimizer.lab run \
  --config lab/scenarios/commands-a.json --output artifacts/lab/commands-a-01
```

| Конфиг | Проверка |
| --- | --- |
| `commands-a.json` / `commands-b.json` | Команды и ACK по фиксированной ветви |
| `commands-slow-kms.json` | Здоровая сеть, ожидание выдачи ключа |
| `commands-empty-kms.json` | Истечение дедлайна во время ожидания KMS |

KMS живёт в отдельном namespace с двумя служебными veth к sender/receiver.
Он не проходит через netem ветвей. PCAP фильтруется по ICMP/UDP и исключает
служебный TCP API с тестовыми ключами. Сервис не имеет внешнего интерфейса.

## Семантика и ограничения

- Команда — идемпотентный `set(value)` с монотонным sequence внутри сессии.
  Старые новые команды получают `stale`; пропуски sequence разрешены.
- ACK `executed` подтверждает транзакцию тестового состояния SQLite.
  Это не гарантия exactly-once для произвольного внешнего оборудования.
- Повтор сохраняет ID, deadline и тело. Поздняя копия исполненного запроса
  получает сохранённый результат, а не повторное исполнение.
- Без ACK исход `unknown`: отсутствие ответа не доказывает отсутствие исполнения.
- Дедлайн включает очередь отправителя, начальную выдачу и обязательную ротацию.
  В журнале `session_ready` разделены startup, steady и rotation.
- Дедлайн на receiver использует общие monotonic-часы одной VM/хоста.
  На разных физических хостах нужна отдельная модель часов.
- Очереди sender/receiver конечны. Нет тихого перехода на незашифрованный обмен.
- AEAD: ChaCha20-Poly1305 из `cryptography`, HKDF разделяет ключи command/ACK,
  каждый пакет имеет случайный 96-битный nonce. Заголовок аутентифицируется.
  Это лабораторный протокол, не аттестованный криптошлюз и не реализация QKD.
- Лимит **всего UDP payload** 1200 байт, включая JSON/base64/AEAD.
  Для IPv4 без options нужен MTU хотя бы 1228; при туннелях вычитаем их заголовки.
  Фактический MTU записан в snapshot. Jumbo/туннели требуют отдельной проверки.
- KMS после рестарта теряет тестовые ключи: receiver возвращается к работе
  после новой выдачи/ротации. SQLite сохраняет дедупликацию до удаления базы.

Проверки: `uv run --extra lab pytest -q tests/commands tests/kms`.
Покрыты реальный TCP KMS + UDP, потеря ACK, повтор, ротация, restart состояния,
поздняя команда, порядок, конфликт ID, аутентификация, MTU-бюджет и очереди.
Linux-интеграция подтверждена в CI; локальная Lima отдельно не проверена.

Источники: [AEAD](https://cryptography.io/en/latest/hazmat/primitives/aead/),
[HKDF](https://cryptography.io/en/latest/hazmat/primitives/key-derivation-functions/).

## Дедупликация A/B

Логическая сессия общая для команды на обоих путях; ключевые сессии различаются:
`<logical-id>/A` и `<logical-id>/B`. Получатель выводит логический ID из
аутентифицированной ключевой сессии, а не из произвольного поля команды.
SQLite атомарно проверяет `(logical session, command ID)` и меняет состояние.
Одинаковое тело с тем же дедлайном возвращает сохранённый результат даже после
смены пути/рестарта; другое тело с тем же ID — `conflict`. ACK привязан к хешу
полного запроса. Для копий используется `Sender.submit(body)` с одним заранее
созданным телом; повторный вызов `send()` создаёт новый дедлайн.

Wire-протокол обновлён до v2: старые агенты нужно обновить одновременно.
Сохранённые записи SQLite остаются; старые несвязанные ключевые сессии не
принимаются новыми получателями. KMS остаётся доверенным лабораторным сервисом,
эта схема не добавляет изоляцию клиентов и аутентификацию API KMS.

## Ключевой сервис: запуск и ограничения


Тестовые 32-байтовые ключи. Один `issue` расходует единицу пула;
`get(key_id, session, path)` выдаёт второй стороне тот же материал без нового расхода.
Политика ротации принадлежит клиентской сессии, не отдельному пакету.

```mermaid
flowchart LR
    S[Sender] -->|issue: session, path| K[KMS: пул и очередь]
    K -->|key ID и тестовый ключ| S
    S -->|key ID в пакете| R[Receiver]
    R -->|get: key ID, session, path| K
    K -->|тот же ключ| R
```

```bash
PYTHONPATH=src python3 -m quantum_link_optimizer.kms \
  --config lab/kms/normal.json --events /tmp/kms-events.jsonl
```

Файл событий должен быть новым. API: JSON-строка поверх TCP, по умолчанию
`127.0.0.1:9400`. Это доверенный лабораторный канал с тестовыми ключами,
без TLS и аутентификации; нельзя публиковать его в недоверенную сеть.
Материал ключей не записывается в журнал; сетевой capture канала API
содержал бы материал, поэтому этот канал нельзя включать в сохраняемый PCAP.

| Конфиг `lab/kms/` | Сценарий |
| --- | --- |
| `normal.json` | Общий запас A/B, пополнение 1 ключ/с |
| `separate.json` | Отдельный запас каждой ветви |
| `slow.json` | 150 мс обслуживания каждого API-запроса |
| `empty.json` | Пустой пул без пополнения, ожидание до timeout |
| `no-refill.json` | Остановка пополнения через 2 с |
| `unavailable.json` | API недоступно с 2 до 5 с |

`queue_limit` ограничивает все принятые запросы, включая обслуживаемый.
Обслуживание сериализовано. Ожидание пустого пула освобождает worker,
чтобы `get` ранее выданного ключа продолжал работать. Клиент задаёт timeout.
Ключи хранятся до `key_ttl_s`, число записей ограничено `retained_limit`.
При рестарте KMS запас и ключи сбрасываются; неизвестный ID возвращает ошибку.

Проверки: `uv run pytest -q tests/kms`. Проверяются согласованность сторон,
общий/раздельный расход, пополнение, истечение, переполнение очереди,
отмена, timeout, недоступность и ошибочный ID. QBER/SKR не моделируются.

События содержат времена возникновения/доступности. Инъекции хранятся отдельно
в `<имя-журнала>.truth.jsonl`; API-состояние снимается каждые 100 мс.
Полный опыт и manifest: [телеметрия](../data/telemetry.md).
