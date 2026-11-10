"""Incident classes grouped by domain, following the ITU-T Y.3804 split of a
QKD network into the quantum, key management, network and service layers
"""

CLASSES = {
    "branch_degradation": {
        "class": "path_degradation",
        "domain": "network",
        "area": "performance",
        "title": "Деградация пути",
        "action": "Перевести команды на другой путь, проверить загрузку и потери",
    },
    "path_change": {
        "class": "route_change",
        "domain": "network",
        "area": "fault",
        "title": "Ступень задержки пути",
        "action": "Сверить с журналом изменений маршрутов и аварий",
    },
    "local_overload": {
        "class": "sender_overload",
        "domain": "endpoint",
        "area": "performance",
        "title": "Перегрузка отправителя",
        "action": "Снизить поток или отказывать сразу, проверить ёмкость путей",
    },
    "receiver_problem": {
        "class": "receiver_slow",
        "domain": "endpoint",
        "area": "performance",
        "title": "Медленный получатель",
        "action": "Проверить загрузку получателя и выдачу ему ключей",
    },
    "key_pool_empty": {
        "class": "key_exhaustion",
        "domain": "key_management",
        "area": "fault",
        "title": "Пул ключей пуст",
        "action": "Снизить расход ключей, проверить генерацию QKD-линка",
    },
    "kms_delay": {
        "class": "key_delivery_delay",
        "domain": "key_management",
        "area": "performance",
        "title": "Медленная выдача ключей",
        "action": "Запрашивать ключ заранее, проверить очередь и транспорт KMS",
    },
    "clock_unusable": {
        "class": "clock",
        "domain": "synchronization",
        "area": "fault",
        "title": "Часы непригодны",
        "action": "Проверить источник времени и holdover",
    },
    "telemetry_stale": {
        "class": "telemetry_gap",
        "domain": "telemetry",
        "area": "fault",
        "title": "Устаревшие данные",
        "action": "Проверить сборщик и доставку телеметрии",
    },
    "telemetry_missing": {
        "class": "telemetry_gap",
        "domain": "telemetry",
        "area": "fault",
        "title": "Нет данных",
        "action": "Проверить сборщик и доставку телеметрии",
    },
}

# Need QBER, key rate or configuration feeds that the laboratory does not have
PLANNED = {
    "qber_rise": ("qkd_link", "Рост QBER"),
    "key_rate_drop": ("qkd_link", "Падение скорости генерации ключей"),
    "configuration_change": ("service", "Изменение конфигурации"),
}

DOMAINS = {
    "network": "Сеть",
    "endpoint": "Конечные точки",
    "key_management": "Управление ключами",
    "qkd_link": "QKD-линк",
    "synchronization": "Синхронизация",
    "telemetry": "Телеметрия",
    "service": "Сервис",
}


def describe(state):
    item = CLASSES.get(state)

    if item is None:
        return {
            "class": state,
            "domain": "unknown",
            "area": "fault",
            "title": state,
            "action": None,
        }

    return dict(item)
