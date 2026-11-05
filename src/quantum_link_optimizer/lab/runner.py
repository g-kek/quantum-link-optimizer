import json
import platform
import subprocess
import time
import uuid
from datetime import UTC, datetime

from quantum_link_optimizer.telemetry_lab.linux import sample_namespace
from quantum_link_optimizer.telemetry_lab.manifest import begin, finish
from quantum_link_optimizer.telemetry_lab.report import summarize

from .command_workload import CommandWorkload
from .shaping import configure
from .system import command, run, snapshot
from .topology import Topology


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def version(*args):
    try:
        return run(*args).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        return str(error)


def run_scenario(config, output):
    provenance = begin(output, config)
    token = uuid.uuid4().hex[:8]
    topology = Topology(token, config["topology"] == "shared")
    processes = []
    handles = []
    manifest = {
        **provenance,
        "run_id": token,
        "started_utc": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "commit": version("git", "rev-parse", "HEAD"),
        "git_status": version("git", "status", "--porcelain"),
        "versions": {
            name: version(*args)
            for name, args in {
                "kernel": ("uname", "-a"),
                "ip": ("ip", "-Version"),
                "tc": ("tc", "-Version"),
                "iperf3": ("iperf3", "--version"),
                "tcpdump": ("tcpdump", "--version"),
            }.items()
        },
        "status": "running",
        "random_seed": config.get("seed"),
    }
    write_json(output / "scenario.json", config)
    write_json(output / "manifest.json", manifest)

    def spawn(filename, namespace, *args):
        handle = (output / filename).open("w")
        handles.append(handle)
        process = subprocess.Popen(
            command(*args, namespace=namespace),
            stdout=handle,
            stderr=handle,
        )
        processes.append(process)
        return process

    try:
        topology.create()
        configure(topology, config)
        workload = None
        if "commands" in config:
            workload = CommandWorkload(config["commands"], output, topology, spawn)
            workload.start_services()
        write_json(
            output / "topology.json",
            {
                "namespaces": topology.created,
                "points": topology.points,
            },
        )
        snapshot(output / "initial.json", topology.created)
        captures = [
            spawn(
                f"{node}.capture.log",
                namespace,
                "tcpdump",
                "-Z",
                "root",
                "-U",
                "-n",
                "-i",
                "any",
                "-w",
                str((output / f"{node}.pcap").resolve()),
                "icmp or udp",
            )
            for node, namespace in topology.names.items()
            if namespace in topology.created and node != "k"
        ]
        servers = []
        for index, branch in enumerate(("A", "B"), 1):
            if any(flow["path"] == branch for flow in config.get("background", [])):
                servers.append(
                    spawn(
                        f"{branch}.server.log",
                        topology.ns("r"),
                        "iperf3",
                        "-s",
                        "-B",
                        f"10.250.1.{index}",
                        "-p",
                        str(5200 + index),
                    )
                )
        time.sleep(0.5)
        if any(process.poll() is not None for process in captures + servers):
            raise RuntimeError("capture or background server failed; see logs")
        probes = []
        for index, branch in enumerate(("A", "B"), 1):
            probes.append(
                spawn(
                    f"{branch}.ping.log",
                    topology.ns("s"),
                    "ping",
                    "-n",
                    "-D",
                    "-O",
                    "-I",
                    f"10.250.0.{index}",
                    "-i",
                    str(config["interval_s"]),
                    "-w",
                    str(int(config["duration_s"]) + 2),
                    f"10.250.1.{index}",
                )
            )
        background = []
        for flow in config.get("background", []):
            index = 1 if flow["path"] == "A" else 2
            background.append(
                spawn(
                    f"{flow['path']}.iperf.json",
                    topology.ns("s"),
                    "iperf3",
                    "-c",
                    f"10.250.1.{index}",
                    "-B",
                    f"10.250.0.{index}",
                    "-p",
                    str(5200 + index),
                    "-u",
                    "-b",
                    f"{flow['rate_mbit']}M",
                    "-t",
                    str(int(config["duration_s"])),
                    "-J",
                )
            )
        if workload:
            workload.start_sender()
        started = time.monotonic()
        events = sorted(config.get("events", []), key=lambda event: event["at_s"])
        with (
            (output / "truth-network.jsonl").open("w") as event_log,
            (output / "queues.jsonl").open("w") as queues,
        ):
            while (elapsed := time.monotonic() - started) < config["duration_s"]:
                if any(
                    process.poll() is not None
                    for process in captures
                    + servers
                    + (workload.services if workload else [])
                ):
                    raise RuntimeError("capture or server exited during measurement")
                while events and events[0]["at_s"] <= elapsed:
                    event = events.pop(0)
                    topology.execute(
                        event["path"].lower(),
                        "ip",
                        "link",
                        "set",
                        "to-s",
                        event["action"],
                    )
                    event_log.write(
                        json.dumps({**event, "actual_s": time.monotonic() - started})
                        + "\n"
                    )
                    event_log.flush()
                for namespace in topology.created:
                    queues.write(
                        json.dumps(
                            {
                                "elapsed_s": time.monotonic() - started,
                                **sample_namespace(namespace),
                            }
                        )
                        + "\n"
                    )
                queues.flush()
                time.sleep(0.1)
        for process in probes + background:
            process.wait(timeout=5)
        if workload:
            workload.finish()
        snapshot(output / "final.json", topology.created)
        manifest["traffic_exit_codes"] = [p.returncode for p in probes + background]
        manifest["status"] = "collected"
    except BaseException as error:
        manifest["status"] = "failed"
        manifest["error"] = repr(error)
        raise
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for handle in handles:
            handle.close()
        manifest["cleanup_errors"] = topology.cleanup()
        manifest["finished_utc"] = datetime.now(UTC).isoformat()
        if (output / "sender.jsonl").exists():
            summarize(output)
            if "routing" in config.get("commands", {}):
                from quantum_link_optimizer.selection.report import report

                report(output, config["commands"]["routing"].get("target_ms", 100))
        finish(output, manifest)
