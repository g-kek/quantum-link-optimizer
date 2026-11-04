from .report import records


def available_to_sender(output, at_ns):
    return [
        record
        for record in records(output / "sender.jsonl")
        if record.get("algorithm_available_ns") is not None
        and record["algorithm_available_ns"] <= at_ns
    ]
