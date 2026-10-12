"""Unit tests for src/data/upload/upload_to_databricks.py (no network, no databricks-sdk needed)."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from data.upload import upload_to_databricks as up

TS_OLD, TS_NEW = "20261001_090000", "20261003_101847"


def make_run(raw: Path, ts: str, datasets=tuple(up.DATASETS)) -> None:
    for ds in datasets:
        (raw / f"{ds}_{ts}.{up.DATASETS[ds]}").write_text("x", encoding="utf-8")


class NotFound(Exception):
    """Same name as the SDK's error for a missing folder."""


class FakeFiles:
    def __init__(self, fail_on: str | None = None, existing: tuple[str, ...] = ()):
        self.calls: list[tuple[str, bool]] = []
        self.fail_on = fail_on
        self.existing = list(existing)  # remote paths already in the volume

    def list_directory_contents(self, path):
        names = [p.rsplit("/", 1)[1] for p in self.existing if p.rsplit("/", 1)[0] == path.rstrip("/")]
        if not names:
            raise NotFound(path)
        return [SimpleNamespace(name=n) for n in names]

    def upload(self, path, contents, overwrite=False):
        if self.fail_on and self.fail_on in path:
            raise OSError("boom")
        self.calls.append((path, overwrite))


class FakeClient:
    def __init__(self, **kw):
        self.files = FakeFiles(**kw)


def test_select_run_picks_latest_complete(tmp_path):
    make_run(tmp_path, TS_OLD)
    make_run(tmp_path, TS_NEW)
    make_run(tmp_path, "20261009_000000", datasets=("equipment",))  # newer but incomplete
    ts, files = up.select_run(up.find_runs(tmp_path))
    assert ts == TS_NEW and set(files) == set(up.DATASETS)


def test_select_run_rejects_unknown_and_incomplete(tmp_path):
    make_run(tmp_path, TS_NEW, datasets=("equipment", "work_orders"))
    runs = up.find_runs(tmp_path)
    with pytest.raises(ValueError, match="incomplete"):
        up.select_run(runs, TS_NEW)
    with pytest.raises(ValueError, match="no files"):
        up.select_run(runs, "20200101_000000")


def test_plan_uploads_one_subfolder_per_dataset(tmp_path):
    make_run(tmp_path, TS_NEW)
    _, files = up.select_run(up.find_runs(tmp_path))
    plan = up.plan_uploads(files, "/Volumes/c/s/test_datasets/")
    assert [remote for _, remote in plan] == [
        f"/Volumes/c/s/test_datasets/equipment/equipment_{TS_NEW}.csv",
        f"/Volumes/c/s/test_datasets/maintenance_history/maintenance_history_{TS_NEW}.csv",
        f"/Volumes/c/s/test_datasets/work_orders/work_orders_{TS_NEW}.csv",
        f"/Volumes/c/s/test_datasets/sensor_events/sensor_events_{TS_NEW}.json",
    ]


def test_resolve_volume_path_precedence(monkeypatch):
    monkeypatch.delenv("DATABRICKS_VOLUME_PATH", raising=False)
    assert up.resolve_volume_path() == "/Volumes/data2026acc123456/team3/datasets"
    assert up.resolve_volume_path(folder="test_datasets") == "/Volumes/data2026acc123456/team3/test_datasets"
    monkeypatch.setenv("DATABRICKS_VOLUME_PATH", "/Volumes/env/path")
    assert up.resolve_volume_path() == "/Volumes/env/path"
    assert up.resolve_volume_path(folder="test_datasets").endswith("/team3/test_datasets")  # --folder beats env
    assert up.resolve_volume_path("/Volumes/explicit", "test_datasets") == "/Volumes/explicit"
    with pytest.raises(ValueError):
        up.resolve_volume_path(folder="nope")


def test_upload_overwrites_each_file(tmp_path):
    make_run(tmp_path, TS_NEW)
    _, files = up.select_run(up.find_runs(tmp_path))
    client = FakeClient()
    up.upload(client, up.plan_uploads(files, "/Volumes/v"))
    assert len(client.files.calls) == 4 and all(overwrite for _, overwrite in client.files.calls)


def test_upload_failure_names_already_uploaded(tmp_path):
    make_run(tmp_path, TS_NEW)
    _, files = up.select_run(up.find_runs(tmp_path))
    with pytest.raises(RuntimeError, match=rf"work_orders_{TS_NEW}.csv.*Already uploaded: equipment_{TS_NEW}.csv"):
        up.upload(FakeClient(fail_on="work_orders"), up.plan_uploads(files, "/Volumes/v"))


def test_upload_run_uses_requested_run_and_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABRICKS_VOLUME_PATH", raising=False)
    monkeypatch.setattr(up, "ENV_FILE", tmp_path / "missing.env")
    make_run(tmp_path, TS_OLD)
    make_run(tmp_path, TS_NEW)
    client = FakeClient()
    monkeypatch.setattr(up, "make_client", lambda: client)
    plan = up.upload_run(tmp_path, TS_OLD, folder="test_datasets")
    assert {r for r, _ in client.files.calls} == {remote for _, remote in plan}
    assert all("/team3/test_datasets/" in r and TS_OLD in r for r, _ in client.files.calls)


def test_upload_run_dry_run_uploads_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "ENV_FILE", tmp_path / "missing.env")
    make_run(tmp_path, TS_NEW)
    monkeypatch.setattr(up, "make_client", lambda: pytest.fail("client must not be created in a dry run"))
    assert len(up.upload_run(tmp_path, dry_run=True)) == 4


# --- continuation runs and the volume check ---------------------------------------------------------------------
VOL = "/Volumes/c/s/test_datasets"
TS_INC = "20261012_144652"


def remote(ds: str, tag: str) -> str:
    return f"{VOL}/{ds}/{ds}_{tag}.{up.DATASETS[ds]}"


def write_chain(raw: Path, increments=(), aliases=()) -> None:
    chain = {"baseline": {"tag": "2", "aliases": list(aliases), "window": ["a", "b"], "rows_per_file": 10},
             "increments": [{"tag": t} for t in increments]}
    (raw / "chain.json").write_text(json.dumps(chain), encoding="utf-8")


@pytest.fixture
def no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABRICKS_VOLUME_PATH", raising=False)
    monkeypatch.setattr(up, "ENV_FILE", tmp_path / "missing.env")


def use_client(monkeypatch, **kw) -> FakeClient:
    client = FakeClient(**kw)
    monkeypatch.setattr(up, "make_client", lambda: client)
    return client


def test_continuation_run_without_equipment_is_complete(tmp_path):
    make_run(tmp_path, TS_INC, datasets=up.ACTIVITY)
    ts, files = up.select_run(up.find_runs(tmp_path))
    assert ts == TS_INC and "equipment" not in files
    assert [r.rsplit("/", 2)[1] for _, r in up.plan_uploads(files, VOL)] == list(up.ACTIVITY)


def test_continuation_upload_goes_to_three_folders(tmp_path, monkeypatch, no_env):
    make_run(tmp_path, TS_INC, datasets=up.ACTIVITY)
    write_chain(tmp_path, increments=[TS_INC])
    client = use_client(monkeypatch, existing=[remote("equipment", "2"), remote("work_orders", "2")])
    up.upload_run(tmp_path, volume_path=VOL)
    assert sorted(p for p, _ in client.files.calls) == sorted(remote(ds, TS_INC) for ds in up.ACTIVITY)


def test_new_plant_refused_when_volume_holds_another_run(tmp_path, monkeypatch, no_env):
    make_run(tmp_path, TS_NEW)
    client = use_client(monkeypatch, existing=[remote("equipment", "2")])
    with pytest.raises(RuntimeError, match=r"already holds data of other runs \(2\)"):
        up.upload_run(tmp_path, volume_path=VOL)
    assert client.files.calls == []  # nothing was uploaded


def test_new_plant_allowed_on_empty_volume_and_for_its_own_partial_upload(tmp_path, monkeypatch, no_env):
    make_run(tmp_path, TS_NEW)
    use_client(monkeypatch)
    assert len(up.upload_run(tmp_path, volume_path=VOL)) == 4
    client = use_client(monkeypatch, existing=[remote("equipment", TS_NEW)])  # re-run after a partial failure
    up.upload_run(tmp_path, volume_path=VOL)
    assert len(client.files.calls) == 4


def test_continuation_refused_for_unknown_run_in_volume(tmp_path, monkeypatch, no_env):
    make_run(tmp_path, TS_INC, datasets=up.ACTIVITY)
    write_chain(tmp_path, increments=[TS_INC])
    client = use_client(monkeypatch, existing=[remote("work_orders", "2"), remote("work_orders", "20261020_101500")])
    with pytest.raises(RuntimeError, match="unknown to chain.json: 20261020_101500"):
        up.upload_run(tmp_path, volume_path=VOL)
    assert client.files.calls == []


def test_baseline_alias_makes_its_volume_name_known(tmp_path, monkeypatch, no_env):
    make_run(tmp_path, TS_INC, datasets=up.ACTIVITY)
    write_chain(tmp_path, increments=[TS_INC], aliases=["20261003_101847"])
    client = use_client(monkeypatch, existing=[remote("equipment", "20261003_101847")])
    up.upload_run(tmp_path, volume_path=VOL)
    assert len(client.files.calls) == 3


def test_continuation_must_be_recorded_in_chain(tmp_path, monkeypatch, no_env):
    make_run(tmp_path, TS_INC, datasets=up.ACTIVITY)
    use_client(monkeypatch)
    with pytest.raises(RuntimeError, match="no chain.json"):
        up.upload_run(tmp_path, volume_path=VOL)
    write_chain(tmp_path, increments=[])
    with pytest.raises(RuntimeError, match="not recorded in chain.json"):
        up.upload_run(tmp_path, volume_path=VOL)


def test_force_skips_the_volume_check(tmp_path, monkeypatch, no_env):
    make_run(tmp_path, TS_NEW)
    client = use_client(monkeypatch, existing=[remote("equipment", "2")])
    up.upload_run(tmp_path, volume_path=VOL, force=True)
    assert len(client.files.calls) == 4


def test_listing_errors_other_than_not_found_are_not_treated_as_empty(tmp_path, monkeypatch, no_env):
    make_run(tmp_path, TS_NEW)
    client = use_client(monkeypatch)

    def denied(path):
        raise PermissionError("403")
    client.files.list_directory_contents = denied
    with pytest.raises(RuntimeError, match="cannot list"):
        up.upload_run(tmp_path, volume_path=VOL)
    assert client.files.calls == []


def test_unsuffixed_files_count_as_data_in_the_volume(tmp_path, monkeypatch, no_env):
    existing = [f"{VOL}/{ds}/{ds}.{ext}" for ds, ext in up.DATASETS.items()]  # e.g. .../equipment/equipment.csv
    make_run(tmp_path, TS_NEW)
    client = use_client(monkeypatch, existing=existing)
    with pytest.raises(RuntimeError, match=r"already holds data of other runs \(\(untagged\)\)"):
        up.upload_run(tmp_path, volume_path=VOL)
    assert client.files.calls == []
    # a continuation is accepted once the chain declares the un-suffixed files as its baseline
    make_run(tmp_path, TS_INC, datasets=up.ACTIVITY)
    write_chain(tmp_path, increments=[TS_INC], aliases=[up.UNTAGGED])
    up.upload_run(tmp_path, timestamp=TS_INC, volume_path=VOL)
    assert len(client.files.calls) == 3
