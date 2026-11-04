from quantum_link_optimizer.commands.receiver import Receiver
from quantum_link_optimizer.commands.sender import Sender
from quantum_link_optimizer.commands.session import Session
from quantum_link_optimizer.telemetry_lab.relay import Relay

from .probes import Echo, Probe


async def open_path(
    loop, path, config, adapter, store, sender_log, receiver_log, resources
):
    settings = config["network"][path]
    receiver = Receiver(
        path, adapter, store, receiver_log, queue_limit=config["queue_limit"]
    )
    received, _ = await loop.create_datagram_endpoint(
        lambda: receiver, local_addr=("127.0.0.1", 0)
    )
    resources["agents"].append(receiver)
    relay = Relay(
        received.get_extra_info("sockname"), settings, config["seed"] + (path == "B")
    )
    proxy, _ = await loop.create_datagram_endpoint(
        lambda: relay, local_addr=("127.0.0.1", 0)
    )
    resources["relays"].append(relay)
    sender = Sender(
        Session(adapter, path, rotate_after=config["rotate_after"]),
        proxy.get_extra_info("sockname"),
        sender_log,
        queue_limit=config["queue_limit"],
    )
    await loop.create_datagram_endpoint(lambda: sender, local_addr=("127.0.0.1", 0))
    resources["agents"].append(sender)
    echo, _ = await loop.create_datagram_endpoint(Echo, local_addr=("127.0.0.1", 0))
    resources["transports"].append(echo)
    probe_relay = Relay(
        echo.get_extra_info("sockname"), settings, config["seed"] + 2 + (path == "B")
    )
    probe_proxy, _ = await loop.create_datagram_endpoint(
        lambda: probe_relay, local_addr=("127.0.0.1", 0)
    )
    resources["relays"].append(probe_relay)
    probe = Probe(
        path,
        probe_proxy.get_extra_info("sockname"),
        sender_log,
        config["probe_timeout_s"],
        config["probe_limit"],
    )
    await loop.create_datagram_endpoint(lambda: probe, local_addr=("127.0.0.1", 0))
    resources["probes"].append(probe)
    return sender, probe, (relay, probe_relay)
