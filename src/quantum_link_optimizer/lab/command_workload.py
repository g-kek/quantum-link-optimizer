import json
import sys
import time
from pathlib import Path


class CommandWorkload:
    def __init__(self, config, output, topology, spawn):
        self.config = config
        self.output = output
        self.topology = topology
        self.spawn = spawn
        self.services = []
        self.sender = None

    def start_services(self):
        from quantum_link_optimizer.kms.model import Settings

        settings = json.loads(Path(self.config["kms_config"]).read_text())
        Settings(**settings["settings"])
        self.output = self.output.resolve()
        kms_config = self.output / "kms-config.json"
        kms_config.write_text(json.dumps(settings, indent=2) + "\n")
        self.topology.create_kms()
        self.services.append(
            self.spawn(
                "kms.log",
                self.topology.ns("k"),
                sys.executable,
                "-m",
                "quantum_link_optimizer.kms",
                "--config",
                kms_config,
                "--events",
                self.output / "kms.jsonl",
                "--host",
                "0.0.0.0",
            )
        )
        # The KMS listener must be ready before starting agents.
        time.sleep(0.3)
        self.services.append(
            self.spawn(
                "receiver.log",
                self.topology.ns("r"),
                sys.executable,
                "-m",
                "quantum_link_optimizer.commands",
                "receive",
                "--kms",
                "10.240.7.2:9400",
                "--a",
                "10.250.1.1:9501",
                "--b",
                "10.250.1.2:9502",
                "--events",
                self.output / "receiver.jsonl",
                "--state",
                self.output / "receiver.sqlite",
                "--ready",
                self.output / "receiver.ready",
            )
        )
        deadline = time.monotonic() + 5
        while not (self.output / "receiver.ready").exists():
            if time.monotonic() >= deadline or any(
                p.poll() is not None for p in self.services
            ):
                raise RuntimeError("command services failed to start; see logs")
            time.sleep(0.05)

    def start_sender(self):
        if "routing" in self.config:
            return self.start_online()
        index = 1 if self.config["path"] == "A" else 2
        self.sender = self.spawn(
            "sender.log",
            self.topology.ns("s"),
            sys.executable,
            "-m",
            "quantum_link_optimizer.commands",
            "send",
            "--kms",
            "10.240.6.2:9400",
            "--path",
            self.config["path"],
            "--local",
            f"10.250.0.{index}:0",
            "--remote",
            f"10.250.1.{index}:{9500 + index}",
            "--events",
            self.output / "sender.jsonl",
            "--count",
            str(self.config["count"]),
            "--interval-s",
            str(self.config.get("interval_s", 0.02)),
            "--deadline-s",
            str(self.config.get("deadline_s", 1)),
            "--rotate-after",
            str(self.config.get("rotate_after", 64)),
        )

    def start_online(self):
        ready = self.output / "probes.ready"
        self.services.append(
            self.spawn(
                "probe-server.log",
                self.topology.ns("r"),
                sys.executable,
                "-m",
                "quantum_link_optimizer.selection.agent",
                "probes",
                "--a",
                "10.250.1.1:9601",
                "--b",
                "10.250.1.2:9602",
                "--ready",
                ready,
            )
        )
        deadline = time.monotonic() + 5
        while not ready.exists():
            if time.monotonic() > deadline or self.services[-1].poll() is not None:
                raise RuntimeError("probe server failed to start")
            time.sleep(0.02)
        config = {
            "kms": ["10.240.6.2", 9400],
            "routing": self.config["routing"],
            "rotate_after": self.config.get("rotate_after", 64),
            "count": self.config["count"],
            "interval_s": self.config.get("interval_s", 0.2),
            "deadline_s": self.config.get("deadline_s", 1),
            "warmup_s": self.config.get("warmup_s", 0.2),
            "allow_copy": self.config.get("allow_copy", False),
            "paths": {
                path: {
                    "local": [f"10.250.0.{index}", 0],
                    "remote": [f"10.250.1.{index}", 9500 + index],
                    "probe": [f"10.250.1.{index}", 9600 + index],
                }
                for index, path in enumerate(("A", "B"), 1)
            },
        }
        path = self.output / "online-config.json"
        path.write_text(json.dumps(config, indent=2) + "\n")
        self.sender = self.spawn(
            "sender.log",
            self.topology.ns("s"),
            sys.executable,
            "-m",
            "quantum_link_optimizer.selection.agent",
            "send",
            "--config",
            path,
            "--events",
            self.output / "sender.jsonl",
        )

    def finish(self):
        self.sender.wait(timeout=self.config.get("deadline_s", 1) + 5)
        if self.sender.returncode:
            raise RuntimeError("sender failed; see sender.log")
