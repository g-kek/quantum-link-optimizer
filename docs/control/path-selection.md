# Выбор пути

## Политики

| Режим | Решение |
| --- | --- |
| `fixed-a`, `fixed-b` | Постоянный путь |
| `last` | Последний доступный RTT |
| `ewma` | Сглаженный RTT, α=0,3, с `ewma_restart` после ступени считается заново |
| Прогноз | Локальная модель, исследование #6 |

Учитываются свежесть наблюдений, доступность устройства и готовность ключа
Удержание и гистерезис ограничивают переключения
Аварийный переход разрешён после наблюдаемого отказа
Truth не поступает в решение

## Копии и очередь

- В полёте до `max_in_flight` команд, по умолчанию 8, ACK предыдущей не ждём
- `coalesce` отменяет старые `set` в очереди, когда пришёл более новый
- Pacing с `pacing_max_wait_ms` отказывает сразу, а не держит команду в очереди
- `adaptive_hedge` ставит задержку копии на p90 недавних RTT основного пути
- `hedge_risk` отменяет копию, если доля медленных проб основного пути ниже порога
- Следующий ключ запрашивается заранее, получатель не держит очередь на запросе ключа
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

## Диагностические инциденты

Правила — `diagnostics/rules.py`, жизненный цикл — `diagnostics/engine.py`
Router проверяет состояние каждые 50 мс и при выборе пути
Ожидание ключа и устаревание видны без новой команды или успешного ACK

| Состояние | Класс и домен | Наблюдаемое основание |
| --- | --- | --- |
| `branch_degradation` | Деградация пути, сеть | RTT выше target, повторные timeout, потери от 15%, отчёт устройства |
| `path_change` | Ступень задержки, сеть | CUSUM отметил рост уровня RTT |
| `local_overload` | Перегрузка отправителя | Очередь от двух команд или отказ допуска |
| `key_pool_empty` | Исчерпание ключей, KMS | Свежий ответ KMS API с нулевым запасом |
| `kms_delay` | Задержка выдачи, KMS | Ожидание ключа или запрос выше нормы |
| `receiver_problem` | Медленный получатель | Очередь, ключ или обработка выше нормы |
| `telemetry_stale` | Пробел телеметрии | Возраст наблюдения превышает max_age_ms |

Норма сигнала — медиана, MAD и 1,5 × p99 последних обычных значений,
до 20 наблюдений и выше 20–25% бюджета SLO действует жёсткий предел
Компонент выбирается по графу зависимостей `diagnostics/topology.py`
Одинаковый высокий RTT A/B без заявленного общего участка остаётся неоднозначным
Состояния часов сохраняются отдельно как `clock_unusable`

Каждый вывод содержит scope, численные measurements с единицами, источником
и возрастом, first/last_observed_ns, detected_ns и альтернативы
`physical_onset_ns=null`: точное физическое начало по этим данным неизвестно
Времена монотонные, привязка к UTC находится в manifest.clock_anchor
ID инцидента локален для Router, при объединении нужен ID запуска

События: `incident_opened`, `incident_updated`, `incident_unobserved`,
`incident_resolved`, все сохраняются в sender.jsonl
Пропавшее свидетельство переводит прежний инцидент в unobserved, а не в recovery
Новый свежий результат позволяет закрыть условие как больше не наблюдаемое
История хранится в журнале, в памяти только текущие инциденты

Запас ключей поступает через API, truth не используется правилами
Обычный live-агент также опрашивает state KMS, цена polling входит в обмен
Точность по инцидентам — в [отдельном отчёте](../validation/diagnostics.md)
