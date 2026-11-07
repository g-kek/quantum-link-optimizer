# Телеметрия

## Сбор

```bash
uv sync --extra lab
uv run python -m quantum_link_optimizer.telemetry_lab run \
  --config lab/episodes/slow-kms.json --output artifacts/episodes/slow-kms-01
uv run python -m quantum_link_optimizer.telemetry_lab verify \
  --run artifacts/episodes/slow-kms-01
```

Новый каталог обязателен
Loopback использует реальные UDP/TCP-сокеты и программный relay
Linux-сбор дополнительно даёт namespaces, qdisc и PCAP

## Файлы

| Файл | Данные |
| --- | --- |
| `sender.jsonl` | Поступления, попытки, ACK и завершения |
| `receiver.jsonl` | Приём, исполнение, dedup и ACK |
| `kms.jsonl` | Выдача, ожидание и запас без материала ключей |
| `samples.jsonl` | Очереди агентов, KMS и CPU |
| `truth*.jsonl` | Внесённые воздействия, только для оценки |
| `queues.jsonl` | Linux backlog, drops и overlimits |
| `receiver.sqlite` | Состояние и дедупликация |
| `summary.json/.md` | Сводка исходов и ожиданий |
| `scenario.json`, `source.zip` | Конфиг и снимок кода |
| `manifest.json` | Версии, seed, clock anchor и SHA-256 |

## Время

- `occurred_ns` — возникновение события
- `available_ns` — доступность в журнале
- `algorithm_available_ns` — доступность наблюдения контроллеру
- `record_id` и `source` — порядок внутри источника
- Для удалённых наблюдений возраст считается от измерения, не от доставки
- Truth и будущие события не входят в признаки
- Общий monotonic clock действует в пределах одного хоста/VM

В независимых испытаниях latency начинается с запланированного поступления
Опоздание генератора включено в результат

## Потери и нагрузка

- Без ACK исход команды `unknown`, исполнение сверяется с receiver
- Timeout, поздний ответ, duplicate и reorder считаются отдельно
- Нет завершения — `missing_completion`, такая команда не исчезает из отчёта
- Счётчики RX/TX/drops накопленные, скорость вычисляется по разности и времени
- Reset счётчика и потеря самого сборщика не выдаются за потерю трафика
- Linux PCAP позволяет сверить попытки на концах пути
- В loopback qdisc и отдельная загрузка узлов отсутствуют

`verify` сверяет файлы по SHA-256, manifest не имеет цифровой подписи
Seed воспроизводит настройки воздействий, но не расписание ОС

[Датасеты](datasets.md) · [Независимые испытания](../validation/independent-trials.md)
