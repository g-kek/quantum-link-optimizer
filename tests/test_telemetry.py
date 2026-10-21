import numpy as np
import pytest

from quantum_link_optimizer.dataset import make_windows
from quantum_link_optimizer.telemetry import (
    generate_telemetry,
    read_telemetry,
    write_telemetry,
)


def test_simulator_deterministic():
    a = generate_telemetry(300, 17)
    np.testing.assert_array_equal(a, generate_telemetry(300, 17))
    assert not np.array_equal(a, generate_telemetry(300, 18))
    np.testing.assert_array_equal(a[:100], generate_telemetry(100, 17))
    assert a.shape == (300, 2)
    assert np.isfinite(a).all() and (a > 0).all()


def test_csv_roundtrip(tmp_path):
    values = generate_telemetry(100)
    path = tmp_path / "telemetry.csv"
    write_telemetry(path, values)
    np.testing.assert_array_equal(read_telemetry(path), values)


def test_split_and_scaling_no_future_leakage():
    values = generate_telemetry(300)
    original = make_windows(values, 8)
    changed = values.copy()
    changed[original.train_end :] += 999
    modified = make_windows(changed, 8)
    np.testing.assert_array_equal(original.mean, modified.mean)
    np.testing.assert_array_equal(original.scale, modified.scale)
    train = original.mask("train")
    np.testing.assert_array_equal(original.x[train], modified.x[train])
    np.testing.assert_array_equal(original.y[train], modified.y[train])
    for left, right in (("train", "val"), ("val", "test")):
        assert original.indices[original.mask(left)].max() < (
            original.indices[original.mask(right)].min()
        )
    assert sum(original.mask(s).sum() for s in ("train", "val", "test")) == 292


def test_inputs_strictly_past():
    values = generate_telemetry(200)
    original = make_windows(values, 8)
    t = 125
    changed = values.copy()
    changed[t:] += 400
    modified = make_windows(changed, 8)
    index = np.where(original.indices == t)[0][0]
    np.testing.assert_array_equal(original.x[index], modified.x[index])
    assert not np.array_equal(original.y[index], modified.y[index])


@pytest.mark.parametrize("window", [0, -1, 1000])
def test_invalid_windows(window):
    with pytest.raises(ValueError):
        make_windows(generate_telemetry(100), window)
