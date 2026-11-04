import json
import os
import time
from pathlib import Path

from quantum_link_optimizer.lab.system import run


def sample_namespace(namespace):
    started = time.monotonic_ns()
    processes = []
    vanished = 0
    for pid in run("ip", "netns", "pids", namespace).split():
        try:
            stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            processes.append(
                {
                    "pid": int(pid),
                    "user_ticks": int(stat[11]),
                    "system_ticks": int(stat[12]),
                    "rss_pages": int(stat[21]),
                }
            )
        except (FileNotFoundError, ProcessLookupError):
            vanished += 1
    queues = json.loads(run("tc", "-s", "-j", "qdisc", namespace=namespace))
    links = json.loads(run("ip", "-s", "-j", "link", namespace=namespace))
    return {
        "namespace": namespace,
        "started_ns": started,
        "available_ns": time.monotonic_ns(),
        "qdisc": queues,
        "links": links,
        "processes": processes,
        "vanished_processes": vanished,
        "clock_ticks_per_s": os.sysconf("SC_CLK_TCK"),
        "page_size": os.sysconf("SC_PAGE_SIZE"),
        "algorithm_available_ns": None,
    }
