# Выбор пути

## Политики

| Режим | Решение |
| --- | --- |
| `fixed-a`, `fixed-b` | Постоянный путь |
| `last` | Последний доступный RTT |
| `ewma` | Сглаженный RTT, α=0,3 |
| Прогноз | Локальная модель, исследование #6 |

Учитываются свежесть наблюдений, доступность устройства и готовность ключа
Удержание и гистерезис ограничивают переключения
Аварийный переход разрешён после наблюдаемого отказа
Truth не поступает в решение

## Копии и очередь

- Конечная очередь, admission и pacing входят в deadline
- Для разрешённого `set` вторая ветвь стартует после `hedge_delay_ms`
- Одна команда имеет общее тело, ID и дедлайн на A/B
- Первый успешный ACK отменяет остальные ожидания
- Уже отправленная копия может дойти позже, receiver сохраняет dedup
- Попытки и дополнительные байты ограничены
- При общей деградации копия подавляется

## Запуск

```bash
uv sync --extra lab
uv run python -m quantum_link_optimizer.selection \
  --plan lab/selection/comparison.json --output artifacts/selection/compare-01
uv run python -m quantum_link_optimizer.selection \
  --plan lab/selection/shared-queue.json --output artifacts/selection/queue-01
uv run pytest -q
```

Новый каталог обязателен, `smoke.json` даёт короткий прогон
Результаты: `comparison.json/.md`, `routing-summary.json`, события и manifest

Linux-режим и общий FQ-CoDel

```bash
uv sync --extra lab
sudo modprobe sch_fq_codel
sudo env PYTHONPATH=src .venv/bin/python -m quantum_link_optimizer.lab run \
  --config lab/scenarios/online-selection.json --output artifacts/lab/online-01
sudo env PYTHONPATH=src .venv/bin/python -m quantum_link_optimizer.lab run \
  --config lab/scenarios/shared-fq-codel.json --output artifacts/lab/fq-01
sudo env PYTHONPATH=src .venv/bin/python -m pytest -q tests/lab
```

## Диагностика

Отдельно показываются отправитель, A/B, общий участок, receiver и KMS
У оснований есть возраст, у гипотез — альтернативы
При устаревших или неполных данных — `insufficient_evidence` / `not_established`
Высокий RTT сам по себе не определяет точную причину

[Результаты](../validation/independent-trials.md) ·
[Приёмка правил](../validation/acceptance.md)
