# Linux-стенд A/B

Namespaces и veth задают два независимых направления A/B
В варианте с bottleneck обе ветви проходят через общую очередь C

```text
Sender → A → Receiver
       → B → Receiver

Sender → A → C → Receiver
       → B ↗
```

## Установка

```bash
brew install lima
limactl start --name=qkd-lab --vm-type=vz --cpus=4 --memory=4 --disk=20 template:ubuntu
limactl shell qkd-lab
```

```bash
sudo apt-get update
sudo apt-get install -y iproute2 iputils-ping iperf3 tcpdump python3
sudo modprobe sch_netem
```

```bash
sudo env PYTHONPATH=src python3 -m quantum_link_optimizer.lab doctor
sudo env PYTHONPATH=src python3 -m quantum_link_optimizer.lab run \
  --config lab/scenarios/baseline.json --output artifacts/lab/baseline-01
```

На Mac команды стенда выполняются внутри Linux VM
Linux-механика прошла CI, локальная Lima отдельно не подтверждена
Нужен новый каталог запуска, штатное завершение удаляет namespaces
После SIGKILL оставшиеся ресурсы перечислены в `topology.json`

## Сценарии в lab/scenarios

| Конфиг | Воздействие |
| --- | --- |
| `baseline.json` | 10 мс на направление A/B |
| `asymmetric.json` | A: 40 мс туда, 60 мс обратно |
| `congest-a.json` | A: 10 Мбит/с, фон 15 Мбит/с |
| `shared-bottleneck.json` | Общие 10 Мбит/с, фон 16 Мбит/с |
| `outage-a.json` | Отключение и восстановление A |
| `loss-a.json` | Потери A 5% вперёд |
| `reorder-a.json` | Задержка и перестановка A |
| `bad-reverse-a.json` | Обратная задержка A 90 мс |

## Данные

- PCAP, ping и iperf3 — путь пакетов и нагрузка
- `queues.jsonl` — backlog, drops и overlimits
- `truth-network.jsonl` — внесённые воздействия
- `manifest.json` — конфиг, версии, commit и хеши

RTT и очереди зависят от планировщика VM
Статус `collected` означает завершённый сбор, эффект подтверждается по событиям и PCAP

[Приёмка](../validation/acceptance.md) · [Команды и KMS](commands-and-keys.md) ·
[netem](https://man7.org/linux/man-pages/man8/tc-netem.8.html)
