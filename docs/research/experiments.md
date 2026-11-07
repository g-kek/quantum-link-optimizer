# Методика экспериментов

## RTT-ряд

```bash
uv run quantum-link generate --samples 4000 --seed 42 --output data/demo.csv
uv run quantum-link experiment --input data/demo.csv --output artifacts/from-csv
```

- Синтетика: базовые 66/73 мс, общий фон, дрейф и эпизоды нагрузки
- Данные генератора не откалиброваны по сети оператора
- CSV: `sample,time_s,channel_0_rtt_ms,channel_1_rtt_ms,source`
- `source=synthetic_uncalibrated`, минимум 100 строк
- Параметры — `uv run quantum-link experiment --help`

## Обучение и сравнение

- RTT-модель получает историю строго до целевого момента
- Разбиение по времени 60/20/20, нормализация только по train
- Эпоха выбирается по validation, test не используется для настройки
- Для горизонтов удаляются целевые окна, пересекающие границы split
- Fixed, previous RTT, EWMA и ML сравниваются при одинаковых ограничениях
- Oracle видит будущие исходы и служит только ориентиром
- Replay загружает фиксированные веса и сохраняет решение до раскрытия исхода
- Стоимость переключения в RTT-replay условная, не измеренная на оборудовании

## Метрики

| Задача | Метрики |
| --- | --- |
| RTT | Среднее, p95/p99, доля выше 100 мс и покрытие |
| Доставка | ACK в срок / все поступления, отказы и unknown |
| Прогноз | MAE, RMSE, Brier, log loss |
| Управление | Переключения, копии, дополнительные байты |
| Агент | p99 решения и обработки, CPU/RSS, очередь |

Меньший MAE сам по себе не доказывает пользу доставки
Квантили только успешных ответов читаются вместе с потерями и покрытием

## Другие команды

```bash
uv run quantum-link warning-experiment --seed 42 --epochs 40 --output artifacts/warning
uv run quantum-link risk-experiment --seed 42 --scenario normal --output artifacts/risk
uv run quantum-link shadow-replay --model artifacts/risk/warning-model.pt --synthetic-input artifacts/risk/telemetry.csv --output artifacts/replay
uv run quantum-link benchmark-suite --seeds 41 42 43 --samples 4000 --epochs 40 --output artifacts/benchmark
uv run quantum-link atlas-benchmark --input path/to/atlas.json --output artifacts/atlas
```

Эти команды относятся к RTT/replay-экспериментам
Результаты UDP-команд с KMS — [#6](forecast-benefit.md) и
[#7](../validation/independent-trials.md)
