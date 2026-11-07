# Датасеты

## Сбор

```bash
uv sync --extra lab
uv run python -m quantum_link_optimizer.collection plan --plan lab/datasets/high-rtt.json
uv run python -m quantum_link_optimizer.collection collect \
  --plan lab/datasets/high-rtt.json --splits train validation --output artifacts/datasets/development
uv run python -m quantum_link_optimizer.collection build \
  --series artifacts/datasets/development --output artifacts/datasets/features
```

Для короткой проверки используйте `lab/datasets/smoke.json`
Новый каталог обязателен, прерванный сбор продолжается с `--resume`
`--allow-partial` предназначен для отладки и не допускается к freeze

| План | Эпизоды | Плановая длительность |
| --- | ---: | ---: |
| `high-rtt.json` | 144 × 600 с | 24 часа |
| `equipment-clocks.json` | 240 × 600 с | 40 часов |
| `optimizer-full.json` | 384 × 600 с | 64 часа |

Длительные планы не выполнены, готовые короткие серии #6–7 описаны отдельно
`high-rtt` задаёт 700 мс на весь RTT, не на каждое направление
При A≈700/B≈60 выбор может помочь, при A≈700/B≈700 не создаёт быстрый путь

## Признаки и метки

- Строка — кандидат A/B в одной точке решения
- Признаки берутся из доступной истории RTT, потерь, KMS, очередей и компонентов
- Исход команды размечен только на фактически выбранной ветви
- Невыбранный исход, missing и timeout не заменяются нулём
- `feature_available_ns <= decision_ns`
- Идентификатор профиля и truth не являются признаками
- Медианы, mean и scale вычисляются только по train
- Train, validation и final разделены по целым эпизодам и seeds

## Freeze и final

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

Lock связывает политику, preprocessing и manifest
Final не переобучает модель и статистики
Collector в этом режиме остаётся fixed A/B для разметки
Живые сравнения контроллеров — в [исследовании #6](../research/forecast-benefit.md)

## Оборудование и часы

```bash
uv run python -m quantum_link_optimizer.collection plan --plan lab/datasets/equipment-clocks.json
uv run python -m quantum_link_optimizer.collection collect \
  --plan lab/datasets/equipment-clocks-smoke.json --splits train validation \
  --output artifacts/datasets/components-smoke
uv run python -m quantum_link_optimizer.collection build \
  --series artifacts/datasets/components-smoke --output artifacts/datasets/components-features
```

| Семейство | Воздействия |
| --- | --- |
| `equipment-*` | Задержка обработки, rate limit, очередь, обрыв, общий отказ |
| `bc-*` | Holdover, drift, step, смена источника, потеря и смещение мониторинга |

Числа явно помечены как допущения, а не характеристики оборудования оператора
Телеметрия компонентов имеет времена измерения и доставки, старые значения маскируются
Schema v2 сохраняет отдельные метки истинного состояния и признаки наблюдений
При смене схемы preprocessing и lock пересоздаются

Ошибка часов: `offset = offset_anchor + drift_ppb × elapsed_seconds`, offset в нс
Ошибка BC не добавляет задержку к RTT, низкий RTT не доказывает пригодность часов
Это функциональная модель без PTP-пакетов, servo, PHC и аппаратных timestamps
Сценарий роста дефекта не является физической моделью старения

[Телеметрия](telemetry.md) · [Результаты final](../validation/independent-trials.md) ·
[LinuxPTP](https://www.linuxptp.org/documentation/ptp4l/)
