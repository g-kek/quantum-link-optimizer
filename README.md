# Quantum Link Optimizer

Выбор пути A/B для защищённых команд с учётом RTT, очереди и доступности ключей
Python 3.12–3.13 · PyTorch CPU · UDP/TCP · Apache 2.0

## Результаты

96 лабораторных эпизодов, 2112 команд, восемь сценариев
Модель зафиксирована до независимых испытаний

| Режим | ACK ≤100 мс, одна команда в полёте | Восемь команд | Байты копий |
| --- | ---: | ---: | ---: |
| Постоянный A | 101 / 352 | 151 / 352 | 0 |
| Постоянный B | 24 / 352 | 127 / 352 | 0 |
| EWMA | 126 / 352 | **167 / 352** | 0 |
| Прогноз | 128 / 352 | 167 / 352 | 0 |
| EWMA + копии | 136 / 352 | 166 / 352 | 77 000 |
| EWMA + pacing | 127 / 352 | 167 / 352 | 0 |

- Агент ждал ACK перед следующей командой, и очередь росла при RTT больше интервала
- Несколько команд в полёте дали EWMA +41 своевременную команду, больше любой модели
- Прогноз и копии после исправления не добавляют своевременных команд
- Оставшиеся нули — оба пути 700 мс, медленный KMS, общий участок 20 кбит/с
- В 12 рестартах сохранилось состояние получателя, повторных исполнений в серии нет

В измерении #7 при 1000 командах/с p99 решения EWMA составил 75–85 мкс,
прогноза — 195–205 мкс при цели 100 мкс
Полный бюджет обработки 1 мс для 1024 прикладных байт не подтверждён

Это реальные сокеты с лабораторными задержками, а не измерения сети оператора
Результат показывает долю ACK в срок, а не снижение задержки на заданное число мс
Если оба пути дают 700 мс, выбор между ними не создаёт путь быстрее 100 мс
[Сравнение окна команд](docs/validation/pipelining.md)

## Диагностика и LSTM

Добавлены состояния деградации A/B, локальной очереди, пустого пула, задержки KMS,
проблемы receiver и устаревшей телеметрии с измерениями и историей инцидента
LSTM проверена на 48 новых эпизодах: 101/288 ACK в срок против 111/288 у EWMA
[Диагностика](docs/control/path-selection.md#диагностические-инциденты) ·
[LSTM и результаты](docs/research/forecast-benefit.md#эксперимент-с-lstm)

## Запуск отчёта

```bash
uv sync --locked --python 3.12 --extra lab
uv run python -m quantum_link_optimizer.experiments.forecasting.run \
  --plan lab/experiments/forecast-benefit.json \
  --output artifacts/experiments/forecast-benefit-01
uv run python -m quantum_link_optimizer.experiments.validation.run \
  --development artifacts/experiments/forecast-benefit-01 \
  --plan lab/experiments/independent-trials.json \
  --output artifacts/experiments/independent-trials-01
uv run python -m quantum_link_optimizer.experiments.demo.build \
  --development artifacts/experiments/forecast-benefit-01 \
  --validation artifacts/experiments/independent-trials-01 \
  --output artifacts/experiments/operator-demo
```

Для каждого сбора нужен новый каталог
Откройте `artifacts/experiments/operator-demo/index.html` в браузере
Готовый HTML работает без сервера и подключения к сети

В отчёте — RTT, исходы команд, очередь, ключи, решения и сравнение политик
Данные и веса сохраняются в `artifacts/`, в Git входят численные сводки

## Другие режимы

```bash
uv run quantum-link demo --output artifacts/demo
uv run quantum-link audit --input data/ping.csv --output artifacts/ping-audit
```

`demo` создаёт синтетический RTT-ряд, `audit` анализирует собственный CSV
Формат CSV и команды стенда — в [инструкции](docs/guide/quickstart.md)

## Документация и код

- [Навигация](docs/README.md)
- [Польза прогноза](docs/research/forecast-benefit.md)
- [Независимые испытания](docs/validation/independent-trials.md)
- [План развития](docs/planning/roadmap.md)
- [Задачи](docs/planning/backlog.md)

Код — `src/quantum_link_optimizer/`, тесты — `tests/`, конфиги — `lab/`
Новые результаты подготовлены в PR [#11](https://github.com/g-kek/quantum-link-optimizer/pull/11),
[#12](https://github.com/g-kek/quantum-link-optimizer/pull/12),
[#13](https://github.com/g-kek/quantum-link-optimizer/pull/13)

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv build --wheel
```

[Изменения](CHANGELOG.md) · [Лицензия Apache 2.0](LICENSE)
