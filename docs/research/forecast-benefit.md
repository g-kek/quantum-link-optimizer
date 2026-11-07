# Польза прогноза

## Сбор

```bash
uv sync --locked --extra lab
uv run python -m quantum_link_optimizer.experiments.forecasting.run \
  --plan lab/experiments/forecast-benefit.json \
  --output artifacts/experiments/forecast-benefit-01
```

Новый каталог обязателен
20 train-эпизодов, 10 validation-эпизодов и 50 живых сравнений
Seeds: train 11/23, validation 101, независимый final 1009/1013

## Модель

- Цели: ACK позже 100 мс и пустой пул ключей в момент t+250 мс
- 15 признаков и 15 индикаторов пропуска, MLP со скрытым слоем 12 ReLU
- 80 эпох Adam, lr 0,01, seed 42, preprocessing только по train
- Вход: прошлые RTT, потери, очереди, размер команды и ответы KMS API
- Будущие исходы и truth используются только для меток
- Результат команды на невыбранном пути остаётся неизвестным

## Доставка в validation

| Политика | ACK ≤100 мс |
| --- | ---: |
| EWMA | 35 / 90 |
| Прогноз | 35 / 90 |
| EWMA + копии | 38 / 90 |
| Прогноз + копии | 37 / 90 |
| Варианты с pacing | 3 / 90 |

Копии и pacing проверялись отдельно от прогноза при одинаковых ограничениях
В сценариях обоих путей 700 мс и медленного KMS все политики дали 0/18 в срок

## Ошибка прогноза

| Метка | Brier модели | Постоянная частота train |
| --- | ---: | ---: |
| Опоздание | 0,0631 | 0,1902 |
| Пустой пул | 0,000034 | 0,1600 |

Меньшая ошибка не дала выигрыша доставки, ML по умолчанию не включаем
Результаты [независимого final](../validation/independent-trials.md) согласуются с этим выводом

[Числа и хеши](../evidence/forecast-benefit.json) ·
[PR #11](https://github.com/g-kek/quantum-link-optimizer/pull/11)
