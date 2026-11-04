import asyncio
import json
import time


class Echo(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, packet, address):
        self.transport.sendto(packet, address)


class Probe(asyncio.DatagramProtocol):
    def __init__(self, path, remote, emit, timeout_s, limit):
        self.path = path
        self.remote = remote
        self.emit = emit
        self.timeout_s = timeout_s
        self.limit = limit
        self.pending = {}
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def send(self, sample):
        if len(self.pending) >= self.limit:
            self.emit(
                "probe_result",
                sample=sample,
                path=self.path,
                status="missing",
                rtt_ms=None,
                reason="outstanding_limit",
            )
            return
        sent = time.monotonic_ns()
        timer = asyncio.get_running_loop().call_later(
            self.timeout_s, self.expire, sample
        )
        self.pending[sample] = sent, timer
        self.transport.sendto(json.dumps({"sample": sample}).encode(), self.remote)
        self.emit("probe_sent", sample=sample, path=self.path, sent_ns=sent)

    def expire(self, sample):
        sent, _ = self.pending.pop(sample)
        self.emit(
            "probe_result",
            sample=sample,
            path=self.path,
            sent_ns=sent,
            status="timeout",
            rtt_ms=None,
        )

    def datagram_received(self, packet, address):
        if address != self.remote:
            return
        try:
            sample = json.loads(packet)["sample"]
        except (ValueError, KeyError, TypeError):
            return
        pending = self.pending.pop(sample, None)
        if pending is None:
            self.emit("probe_late", sample=sample, path=self.path)
            return
        sent, timer = pending
        timer.cancel()
        received = time.monotonic_ns()
        self.emit(
            "probe_result",
            sample=sample,
            path=self.path,
            sent_ns=sent,
            received_ns=received,
            status="ok",
            rtt_ms=(received - sent) / 1e6,
        )

    def close(self):
        for _, timer in self.pending.values():
            timer.cancel()
        self.pending.clear()
        self.transport.close()
