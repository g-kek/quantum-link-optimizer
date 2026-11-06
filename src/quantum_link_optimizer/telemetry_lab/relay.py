import asyncio
import random


class Relay(asyncio.DatagramProtocol):
    def __init__(self, target, settings, seed, device=None):
        self.device = device
        self.target = target
        self.settings = settings
        self.random = random.Random(seed)
        self.client = None
        self.transport = None
        self.timers = set()

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, packet, address):
        direction = "reverse" if address == self.target else "forward"
        if direction == "forward":
            self.client = address
        destination = self.client if direction == "reverse" else self.target
        settings = self.settings.get(direction, {})
        device_delay = 0
        generation = None
        if self.device:
            device_delay = self.device.transit(
                len(packet) + 28, direction, asyncio.get_running_loop().time()
            )
            generation = self.device.generation
            if device_delay is None:
                return
        if self.random.random() < settings.get("loss_pct", 0) / 100:
            return
        if destination is None or len(self.timers) >= 256:
            return
        delay = settings.get("delay_ms", 0) / 1000 + device_delay
        timer = None

        def deliver():
            self.timers.discard(timer)
            if self.device and not self.device.valid_delivery(generation):
                return
            self.transport.sendto(packet, destination)

        timer = asyncio.get_running_loop().call_later(delay, deliver)
        self.timers.add(timer)

    def close(self):
        for timer in self.timers:
            timer.cancel()
        self.timers.clear()
        self.transport.close()
