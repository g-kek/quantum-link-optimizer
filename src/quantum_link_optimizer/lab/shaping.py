def apply_link(topology, point, settings, seed=None):
    node, device = point
    parent = ["root"]
    if settings.get("rate_mbit"):
        topology.execute(
            node,
            "tc",
            "qdisc",
            "replace",
            "dev",
            device,
            "root",
            "handle",
            "1:",
            "tbf",
            "rate",
            f"{settings['rate_mbit']}mbit",
            "burst",
            "16kb",
            "limit",
            "128kb",
        )
        parent = ["parent", "1:1", "handle", "10:"]
    if settings.get("queue_discipline") == "fq_codel":
        topology.execute(
            node,
            "tc",
            "qdisc",
            "replace",
            "dev",
            device,
            *parent,
            "fq_codel",
            "limit",
            str(settings.get("limit", 1000)),
        )
        return
    args = [
        "tc",
        "qdisc",
        "replace",
        "dev",
        device,
        *parent,
        "netem",
        "limit",
        str(settings.get("limit", 1000)),
    ]
    if settings.get("delay_ms"):
        args += ["delay", f"{settings['delay_ms']}ms"]
        if settings.get("jitter_ms"):
            args += [f"{settings['jitter_ms']}ms"]
    for field, option in (("loss_pct", "loss"), ("reorder_pct", "reorder")):
        if settings.get(field):
            args += [option, f"{settings[field]}%"]
    if seed is not None:
        args += ["seed", str(seed)]
    topology.execute(node, *args)


def configure(topology, config):
    links = dict(config["paths"])
    if topology.shared:
        links["shared"] = config.get("shared", {})
    for name, directions in links.items():
        for index, direction in enumerate(("forward", "reverse")):
            seed = config.get("seed")
            if seed is not None:
                seed = (seed + {"A": 0, "B": 2, "shared": 4}[name] + index) % 2**32
            apply_link(
                topology,
                topology.points[name][index],
                directions.get(direction, {}),
                seed,
            )
