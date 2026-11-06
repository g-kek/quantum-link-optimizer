import json
import subprocess


def command(*args, namespace=None):
    prefix = ["ip", "netns", "exec", namespace] if namespace else []
    return prefix + [str(arg) for arg in args]


def run(*args, namespace=None):
    return subprocess.run(
        command(*args, namespace=namespace),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def snapshot(path, namespaces):
    data = {}
    for name in namespaces:
        data[name] = {
            "addresses": json.loads(run("ip", "-j", "addr", namespace=name)),
            "routes": json.loads(run("ip", "-j", "route", namespace=name)),
            "queues": json.loads(run("tc", "-s", "-j", "qdisc", namespace=name)),
        }
    path.write_text(json.dumps(data, indent=2) + "\n")
