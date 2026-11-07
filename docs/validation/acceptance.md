# Приёмка #1–5

## Результат

- #1: две сетевые ветви, общий bottleneck, потери, обрыв/восстановление и reorder
- #2: общий/раздельный пул KMS, очередь, пополнение, TTL и отказы
- #3: AEAD-команды, ACK, дедупликация, порядок и рестарты
- #4: события, времена доступности, manifest и изоляция truth
- #5: EWMA, копии, pacing и диагностика со сроком годности наблюдений

18 сетевых запусков: шесть условий по три повтора
Root Linux — 100 passed, общий CI — 171 passed и 24 Linux-only пропуска
Это результат приёмки #1–5 до добавления экспериментов #6–8

## Исправления

- После `up` восстанавливается обратный маршрут
- Соседи прогреваются до внесения netem
- Устаревшие KMS/queue/receiver-наблюдения исключаются из диагноза
- Функциональные тесты не подменяют независимую оценку задержек

## Воспроизведение

```bash
sudo modprobe sch_netem
sudo modprobe sch_fq_codel
sudo env PYTHONPATH=src python3 -m pytest -q \
  tests/lab tests/selection tests/kms tests/commands tests/telemetry_lab \
  --basetemp=/tmp/ql-acceptance --junitxml=/tmp/ql-acceptance-results.xml
```

Нужны Linux, root, iproute2, iputils-ping, iperf3, tcpdump, pytest и cryptography
Workflow `Linux packet lab` сохраняет события, PCAP и JUnit на 14 дней
API KMS исключён из capture

[Численная сводка](../evidence/mvp-acceptance.json) ·
[PR #9](https://github.com/g-kek/quantum-link-optimizer/pull/9) ·
[Root Linux CI](https://github.com/g-kek/quantum-link-optimizer/actions/runs/42293533647)

Независимые результаты и overhead — в [испытаниях #7](independent-trials.md)
