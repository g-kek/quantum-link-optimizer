import argparse
import asyncio
import json
from pathlib import Path

from quantum_link_optimizer.kms.api import Client
from quantum_link_optimizer.kms.journal import Journal

from .receiver import Receiver
from .sender import Sender
from .session import Session
from .store import Store


def address(value):
    host, port = value.rsplit(":", 1)
    return host, int(port)


async def launch(args):
    loop = asyncio.get_running_loop()
    journal = Journal(
        args.events, source="sender" if args.role == "send" else "receiver"
    )
    adapter = Client(*address(args.kms))
    agents = []
    store = None
    try:
        if args.role == "receive":
            store = Store(args.state)
            for path, local in (("A", args.a), ("B", args.b)):
                agent = Receiver(
                    path,
                    adapter,
                    store,
                    journal.emit,
                    queue_limit=args.queue_limit,
                    drop_acks=args.drop_acks,
                )
                await loop.create_datagram_endpoint(
                    lambda agent=agent: agent, local_addr=address(local)
                )
                agents.append(agent)
            if args.ready:
                args.ready.write_text("ready\n")
            await asyncio.Event().wait()
        else:
            session = Session(
                adapter,
                args.path,
                rotate_after=args.rotate_after,
                rotate_s=args.rotate_s,
            )
            sender = Sender(
                session,
                address(args.remote),
                journal.emit,
                queue_limit=args.queue_limit,
            )
            await loop.create_datagram_endpoint(
                lambda: sender, local_addr=address(args.local)
            )
            agents.append(sender)
            tasks = []
            for sequence in range(args.count):
                tasks.append(
                    asyncio.create_task(
                        sender.send(sequence, sequence, args.deadline_s)
                    )
                )
                await asyncio.sleep(args.interval_s)
            results = await asyncio.gather(*tasks)
            print(json.dumps(results))
    finally:
        for agent in agents:
            await agent.close()
        if store:
            store.close()
        journal.close()


def main():
    parser = argparse.ArgumentParser(description="Fixed-path laboratory UDP commands")
    parser.add_argument("role", choices=["send", "receive"])
    parser.add_argument("--kms", default="127.0.0.1:9400")
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--queue-limit", type=int, default=64)
    parser.add_argument("--a", default="127.0.0.1:9501")
    parser.add_argument("--b", default="127.0.0.1:9502")
    parser.add_argument("--state", type=Path, default=Path("receiver.sqlite"))
    parser.add_argument("--ready", type=Path)
    parser.add_argument("--drop-acks", type=int, default=0)
    parser.add_argument("--path", choices=["A", "B"], default="A")
    parser.add_argument("--local", default="127.0.0.1:0")
    parser.add_argument("--remote", default="127.0.0.1:9501")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--interval-s", type=float, default=0.02)
    parser.add_argument("--deadline-s", type=float, default=1)
    parser.add_argument("--rotate-after", type=int, default=64)
    parser.add_argument("--rotate-s", type=float, default=30)
    args = parser.parse_args()
    if (
        args.queue_limit < 1
        or args.count < 1
        or args.interval_s < 0
        or args.deadline_s <= 0
        or args.drop_acks < 0
    ):
        parser.error("invalid queue, workload or deadline")
    try:
        asyncio.run(launch(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
