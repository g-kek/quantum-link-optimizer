# Запуск и входные данные

## Окружение

```bash
uv sync --locked --python 3.12 --extra lab
```

Python 3.12 или 3.13, обучение на CPU
Сетевой стенд с namespaces требует Linux и root

## Отчёт по командам

Порядок: [development](../research/forecast-benefit.md) →
[независимые испытания](../validation/independent-trials.md) → HTML

```bash
uv run python -m quantum_link_optimizer.experiments.demo.build \
  --development artifacts/experiments/forecast-benefit-01 \
  --validation artifacts/experiments/independent-trials-01 \
  --output artifacts/experiments/operator-demo
```

- Сборщик проверяет хеши данных, модель и policy lock
- Каталог вывода должен быть новым
- Откройте `index.html` как локальный файл
- Выберите сценарий, seed и политику
- Ползунок раскрывает события, общая таблица относится ко всему эпизоду
- Внесённые воздействия показаны отдельно от наблюдений контроллера
- JSON можно скачать из отчёта

## Синтетический RTT

```bash
uv run quantum-link demo --output artifacts/demo
uv run quantum-link demo --scenario normal --output artifacts/demo-normal
```

| Сценарий | Данные |
| --- | --- |
| `high-rtt` | К базовому RTT обоих путей добавлены 625 мс |
| `normal` | Базовые RTT 66/73 мс и эпизоды нагрузки |
| `shared-stress` | К обоим путям добавлены 25 мс |

По умолчанию 4000 samples, seed 42, 40 эпох
Для короткой проверки: `--samples 500 --epochs 2`
Это генератор данных, не измерения сети оператора

## Внешняя телеметрия

```bash
uv run quantum-link audit --input data/ping.csv --output artifacts/ping-audit
```

UTF-8 CSV с точным порядком колонок

```text
sample,timestamp,path,status,rtt_ms,source,provenance
```

| Поле | Значение |
| --- | --- |
| `sample` | Целый индекс, объединяющий пару A/B |
| `timestamp` | ISO 8601 с часовым поясом, общий для пары |
| `path` | 0 или 1, одна строка на путь и sample |
| `status` | `ok`, `timeout` или `missing` |
| `rtt_ms` | Положительный RTT для `ok`, иначе пусто |
| `source` | `real_telemetry` или `ripe_atlas` |
| `provenance` | Источник и метод измерения |

Нужно минимум два момента времени, timestamp между samples возрастает
При подготовке пар сохраняйте исходные времена и допустимое расхождение
Пропуски и timeout не удаляются и не считаются успешной доставкой

Для редких проб задайте допустимый возраст истории

```bash
uv run quantum-link audit --input data/ping.csv \
  --max-gap-seconds 330 --output artifacts/ping-audit
```

`audit` сравнивает fixed, previous RTT и EWMA без обучения модели
Покрытие и неизвестные исходы нужно читать вместе с RTT-квантилями
Причины задержки по тому же CSV ищет [`diagnose`](diagnosis.md)

## Файлы отчёта

| Файл | Содержание |
| --- | --- |
| `index.html` | Переносимый отчёт |
| `demo-data.json` | Данные отчёта команд |
| `report.json` | Данные RTT demo/audit |
| `experiment/` | Веса, телеметрия и replay синтетического demo |

Сборщики лабораторных опытов требуют новый каталог
RTT demo/audit могут перезаписать файлы в существующем каталоге
