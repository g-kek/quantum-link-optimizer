import numpy as np
import pytest

from quantum_link_optimizer.telemetry import (
    generate_telemetry,
    read_telemetry,
    validate_telemetry,
    write_telemetry,
)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("synthetic_uncalibrated", "real"),
        ("sample,time_s", "time_s,sample"),
        ("\n0,0,", "\n1,0,"),
    ],
)
def test_reader_rejects_wrong_schema(tmp_path, old, new):
    path = tmp_path / "invalid.csv"
    write_telemetry(path, generate_telemetry(100))
    path.write_text(path.read_text().replace(old, new, 1))
    with pytest.raises(ValueError):
        read_telemetry(path)


@pytest.mark.parametrize("value", [0.0, -1.0, np.nan, np.inf])
def test_invalid_rtt(value):
    values = np.ones((100, 2))
    values[42, 0] = value
    with pytest.raises(ValueError):
        validate_telemetry(values)


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("extra", [False, True])
def test_readers_reject_short_and_long_rows(tmp_path, external, extra):
    from quantum_link_optimizer.observations import COLUMNS, read_observations

    path = tmp_path / "invalid.csv"
    if external:
        header = ",".join(COLUMNS)
        row = "0,2026-01-01T00:00:00Z,0,ok,7,real_telemetry,fixture"
        reader = read_observations
    else:
        write_telemetry(path, generate_telemetry(100))
        header, row = path.read_text().splitlines()[:2]
        reader = read_telemetry
    row = row + ",extra" if extra else row.rsplit(",", 1)[0]
    path.write_text(header + "\n" + row + "\n")
    with pytest.raises(ValueError, match="CSV row 2: expected exactly"):
        reader(path)
