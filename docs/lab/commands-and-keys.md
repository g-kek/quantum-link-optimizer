# Команды и KMS

## Запуск

```bash
uv sync --extra lab
mkdir -p artifacts/commands
```

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

KMS, receiver и sender запускаются в трёх терминалах
Для B: `--path B --remote 127.0.0.1:9502`
Для повторов задавайте новые журналы

## Передача

1. Sender получает тестовый ключ через TCP KMS
2. Команда `set(value)` передаётся по UDP с ChaCha20-Poly1305
3. Receiver получает тот же ключ и атомарно сохраняет dedup и состояние SQLite
4. ACK подтверждает исполнение либо возвращает статус отказа

- Команда имеет ID, логическую сессию, sequence и deadline
- Повтор/копия сохраняет тело и deadline, одинаковый запрос не исполняется второй раз
- Другой запрос с тем же ID даёт `conflict`, старый sequence — `stale`
- Копии A/B используют одну логическую сессию и отдельные ключевые сессии
- ACK связан с хешем полного запроса
- Без ACK исход `unknown`, отсутствие исполнения этим не доказано
- Дедлайн включает очередь, получение ключа и ротацию
- Очереди ограничены, незашифрованного fallback нет

## Ключи

`issue(session, path)` расходует единицу пула
`get(key_id, session, path)` возвращает согласованный ключ без нового расхода

| Конфиг lab/kms | Условие |
| --- | --- |
| `normal.json` | Общий пул, пополнение 1 ключ/с |
| `separate.json` | Раздельные пулы A/B |
| `slow.json` | 150 мс на API-запрос |
| `empty.json` | Нет запаса и пополнения |
| `no-refill.json` | Пополнение останавливается |
| `unavailable.json` | Временная недоступность API |

Очередь API, TTL и число сохранённых ключей ограничены
Рестарт KMS сбрасывает запас и ключи, SQLite получателя сохраняет dedup
События ключевого материала не содержат

## Границы протокола

- Wire v2, старые агенты обновляются одновременно
- UDP payload ограничен 1200 байт вместе с JSON/base64/AEAD
- Для IPv4 без options нужен MTU от 1228 байт без учёта туннелей
- Deadline receiver рассчитан на общий monotonic clock одного хоста/VM
- API KMS доверенный лабораторный, без TLS и аутентификации
- API KMS не публикуется во внешнюю сеть и исключён из PCAP
- Это макет KMS, физическая QKD и QBER/SKR не моделируются

```bash
uv run pytest -q tests/commands tests/kms
```

[Linux-стенд](network.md) · [Телеметрия](../data/telemetry.md) ·
[AEAD](https://cryptography.io/en/latest/hazmat/primitives/aead/)
