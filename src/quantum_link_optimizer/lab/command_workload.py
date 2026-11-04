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

    def finish(self):
        self.sender.wait(timeout=self.config.get("deadline_s", 1) + 5)
        if self.sender.returncode:
            raise RuntimeError("sender failed; see sender.log")
