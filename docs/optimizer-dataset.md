# Датасет оптимизатора: RTT около 700 мс, цель 100 мс

Данные лабораторные. Реальные измерения целевой сети пока не предоставлены.
Сборщик использует настоящие TCP/UDP-сокеты на MacBook, две параллельные ветви
и программный relay. Он не воспроизводит физическую QKD и очереди Linux.

## Условие 700 → 100

| Профиль | Заданные условия | Что проверяем |
| --- | --- | --- |
| `recoverable-700` | A: RTT 700 мс, B: 60 мс | Выбор B потенциально укладывается в 100 мс; fixed B — обязательный конкурент |
| `swap-700` | A/B меняются ролями в середине | Помогает ли причинное переключение относительно обоих fixed |
| `common-700` | Оба RTT 700 мс | Отрицательный контроль: выбор A/B не создаёт путь 100 мс |
| `reverse-700` | A: 30 мс туда, 670 обратно | Отличаем плохой обратный путь |
| `slow-kms` | Оба RTT 60 мс, выдача ключа 150 мс | Быстрая сеть не гарантирует команду за 100 мс |
| `loss-a` | RTT 60 мс, потери A 10% | Тайм-ауты, пропуски и качество доставки |

700 мс задаются **на весь RTT**, а не в каждую сторону. Постоянный delay —
сценарное допущение, не доказанная причина исходных 700 мс. Наличие реального
пути 60 мс тоже пока не подтверждено. Для переноса на сеть нужны её измерения.

Метрики разделены: RTT проб и время команды от поступления до ACK `executed`,
включая очередь, старт сессии и ротацию. Успех команды — ACK исполнения за
100 мс; для фиксации медленных исходов рабочий timeout составляет 2 с.
Пробы используют UDP echo, не ICMP. Их размеры и поведение отличаются от команд.

## Серии и запуск

```bash
uv sync --extra lab
uv run python -m quantum_link_optimizer.collection plan --plan lab/datasets/high-rtt.json
uv run python -m quantum_link_optimizer.collection collect \
  --plan lab/datasets/high-rtt.json --splits train validation --output artifacts/datasets/development
uv run python -m quantum_link_optimizer.collection build \
  --series artifacts/datasets/development --output artifacts/datasets/features
```

Полный план: **144 эпизода по 600 с**, примерно 24 часа измерений:
72 train, 24 validation, 48 final test. По два fixed-режима A/B, шесть профилей,
разные seeds и интервалы проб. Первые две выборки занимают около 16 часов.
Нужно заранее выделить место под журналы: объём зависит от повторов/потерь;
сборщик останавливается перед следующим эпизодом, если свободно менее 256 МиБ.

Для проверки замените план на `lab/datasets/smoke.json`: это короткий тест
конвейера, не полноценный обучающий набор. Выходной каталог должен быть новым.
Прерванную серию продолжайте той же командой с `--resume`: проверяются параметры
и хеши завершённых эпизодов. Повреждённый/незавершённый эпизод не переиспользуется;
для пересбора используйте новый каталог. `--allow-partial` при build предназначен
для отладки; такой датасет помечен `complete: false` и не допускается к freeze.

## Признаки и метки

```mermaid
flowchart LR
    P[Параллельные пробы A/B] --> LOG[Журнал sender: времена доступности]
    C[Команды fixed A или B] --> LOG
    LOG --> H[История до точки решения]
    H --> X[Признаки]
    LOG --> Y[Будущий результат пробы / команды]
    X --> TRAIN[train / validation по целым эпизодам]
    Y --> TRAIN
    T[Отдельный truth] --> QA[Проверка причин после опыта]
    TRAIN --> LOCK[Фиксация политики и preprocessing]
    LOCK --> FINAL[Отдельный final test]
```

Строка — кандидат A или B в общей точке решения. Пара имеет один episode/sample.
`features`: последние RTT, EWMA, timeout/missing fraction, возраст и устаревание
истории, последнее наблюдаемое ожидание ключа, возраст этого наблюдения,
локальная очередь отправителя. Сигналы KMS берутся из локального `session_ready`;
внутренние журналы KMS/receiver и truth в признаки не входят.

`targets` хранят RTT новой пробы, timeout/missing, время доступности результата,
а также статус/длительность команды **только на фактически выбранной ветви**.
Исход команды на альтернативной ветви остаётся `null`. Пропуски и тайм-ауты не
превращаются в нулевой RTT. `feature_available_ns <= decision_ns` проверяется.
Вход модели — только `features`, идентификаторы/профиль/targets не признаки.

Результаты: `train.jsonl`, `validation.jsonl`, `preprocessing.json`, `quality.json`,
`feasibility.json`, manifest с хешами и ссылками на исходные эпизоды.
Preprocessing обучается только на train: медианы заполнения, mean/scale;
`transform()` добавляет индикаторы отсутствия. Экспорт идёт по эпизодам;
для статистик хранятся компактные числовые столбцы, не весь набор JSON-объектов.

`feasibility.json` отдельно по split/профилю сравнивает fixed A/B, причинный EWMA
и явно обозначенную oracle-границу с доступом к будущим меткам. Показываются
покрытие, доля ≤100 мс, среднее/p95/p99 успешных проб и доля пар, где оба пути
медленные. Это replay проб, не доказательство улучшения доставки команд:
выбранную политику затем нужно реально запустить с теми же ограничениями.

## Финальный тест

Seeds train/validation/final не пересекаются. Интервалы final — 0,08/0,25 с,
train — 0,1/0,2 с, validation — 0,15 с. Эпизоды не делятся случайными строками;
история сбрасывается на границе эпизода. Стартовая часть не скрывается из метрик.

После выбора политики сохраните её параметры/веса в файл и зафиксируйте:

```bash
uv run python -m quantum_link_optimizer.collection freeze \
  --dataset artifacts/datasets/features --policy path/to/policy-file --output artifacts/policy-lock.json
uv run python -m quantum_link_optimizer.collection collect \
  --plan lab/datasets/high-rtt.json --splits final_test \
  --frozen-policy artifacts/policy-lock.json --output artifacts/datasets/final-series
uv run python -m quantum_link_optimizer.collection build \
  --series artifacts/datasets/final-series --output artifacts/datasets/final-features \
  --preprocessing artifacts/datasets/features/preprocessing.json
```

Lock связывает хеши политики, preprocessing и manifest обучающего набора.
Эти файлы после фиксации не изменяем. Final собирается отдельно; preprocessing
не переобучается. Политика в collector остаётся фиксированной A/B для получения
размеченных опытов: lock фиксирует момент до раскрытия final, но не запускает
обученный оптимизатор. Его онлайн-сравнение — следующий этап.

## Длительный ICMP ping в Linux

По [инструкции стенда](packet-lab.md) запустите runner с одним из конфигов:

- `lab/scenarios/long-ping-700.json`: A около 700 мс, B около 60 мс, 10 минут.
- `lab/scenarios/long-ping-common-700.json`: оба около 700 мс, 10 минут.
- `lab/scenarios/calibrate-queue-700.json`: очередь от rate limit и UDP-фона.
  RTT здесь не фиксирован в 700 мс: нужно подобрать нагрузку/лимит и проверить
  backlog/PCAP. Только после калибровки можно трактовать опыт как перегрузку.

Эти Linux-журналы пока не подмешиваются в новый UDP-датасет автоматически.
VM/netem остаются непроверенными из-за нехватки места на MacBook.
Полный длительный сбор и final test в рамках подготовки не запускались.

Проверки: `uv run pytest -q tests/collection tests/lab`.

Проверочный сбор: `artifacts/datasets/smoke-development/`, признаки —
`artifacts/datasets/smoke-features-v2/`: 8 эпизодов, 24 train-строки и
16 validation-строк (20 пар A/B), 12 наблюдаемых исходов команд.
Это только smoke. Финальные эпизоды не выполнялись.
