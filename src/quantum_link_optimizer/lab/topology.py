from .system import run


class Topology:
    def __init__(self, token, shared=False):
        self.names = {
            key: f"ql-{token}-{key}" for key in ("s", "a", "b", "r", "c", "k")
        }
        self.shared = shared
        self.created = []
        self.points = {}

    def ns(self, key):
        return self.names[key]

    def execute(self, key, *args):
        return run(*args, namespace=self.ns(key))

    def link(self, left, right, subnet):
        left_dev, right_dev = f"to-{right}", f"to-{left}"
        self.execute(
            left,
            "ip",
            "link",
            "add",
            left_dev,
            "type",
            "veth",
            "peer",
            "name",
            right_dev,
        )
        self.execute(left, "ip", "link", "set", right_dev, "netns", self.ns(right))
        for node, device, host in ((left, left_dev, 1), (right, right_dev, 2)):
            self.execute(
                node, "ip", "addr", "add", f"10.240.{subnet}.{host}/30", "dev", device
            )
            self.execute(node, "ip", "link", "set", device, "up")

    def route(self, node, destination, gateway):
        self.execute(node, "ip", "route", "add", destination, "via", gateway)

    def create(self):
        nodes = ("s", "a", "b", "r", "c") if self.shared else ("s", "a", "b", "r")
        for node in nodes:
            run("ip", "netns", "add", self.ns(node))
            self.created.append(self.ns(node))
            self.execute(node, "ip", "link", "set", "lo", "up")
            self.execute(node, "sysctl", "-qw", "net.ipv4.ip_forward=1")
            self.execute(node, "sysctl", "-qw", "net.ipv4.conf.all.rp_filter=0")
            self.execute(node, "sysctl", "-qw", "net.ipv4.conf.default.rp_filter=0")
        for index, branch in enumerate(("a", "b"), 1):
            source, target = f"10.250.0.{index}", f"10.250.1.{index}"
            self.execute("s", "ip", "addr", "add", source + "/32", "dev", "lo")
            self.execute("r", "ip", "addr", "add", target + "/32", "dev", "lo")
            self.link("s", branch, index)
            end = "c" if self.shared else "r"
            self.link(branch, end, index + 2)
            self.route("s", target, f"10.240.{index}.2")
            self.route(branch, source, f"10.240.{index}.1")
            self.route(branch, target, f"10.240.{index + 2}.2")
            self.route(end, source, f"10.240.{index + 2}.1")
            self.points[branch.upper()] = ((branch, f"to-{end}"), (end, f"to-{branch}"))
        if self.shared:
            self.link("c", "r", 5)
            self.route("c", "10.250.1.0/24", "10.240.5.2")
            self.route("r", "10.250.0.0/24", "10.240.5.1")
            self.points["shared"] = (("c", "to-r"), ("r", "to-c"))

    def create_kms(self):
        run("ip", "netns", "add", self.ns("k"))
        self.created.append(self.ns("k"))
        self.execute("k", "ip", "link", "set", "lo", "up")
        self.link("s", "k", 6)
        self.link("r", "k", 7)

    def cleanup(self):
        errors = []
        for namespace in reversed(self.created):
            try:
                run("ip", "netns", "del", namespace)
            except Exception as error:
                errors.append(str(error))
        return errors
