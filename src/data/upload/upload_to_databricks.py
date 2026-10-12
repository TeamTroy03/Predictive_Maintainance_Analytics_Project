#!/usr/bin/env python3
"""Upload one generated run from ``src/data/raw/`` to a Databricks Unity Catalog volume.

Each dataset goes to its own folder, keeping the timestamped file name so runs never overwrite each other::

    equipment_<ts>.csv            -> <volume-path>/equipment/equipment_<ts>.csv
    maintenance_history_<ts>.csv  -> <volume-path>/maintenance_history/maintenance_history_<ts>.csv
    work_orders_<ts>.csv          -> <volume-path>/work_orders/work_orders_<ts>.csv
    sensor_events_<ts>.json       -> <volume-path>/sensor_events/sensor_events_<ts>.json

``<volume-path>`` is ``/Volumes/data2026acc123456/team3/datasets`` by default, or ``.../team3/test_datasets``
with ``--folder test_datasets``.

A *continuation* run (``generate_data.py --continue``) has no equipment file: the plant is unchanged. It is detected
from the files and uploads the other three datasets. Before uploading, the volume is checked so that data of
different plants or of an unknown run can never end up side by side (see :func:`check_volume`); ``--force`` overrides.

Usage
-----
    python upload_to_databricks.py                              # latest complete run in raw/
    python upload_to_databricks.py --timestamp 20261003_101629  # a specific run
    python upload_to_databricks.py --folder test_datasets       # upload to team3/test_datasets
    python upload_to_databricks.py --dry-run                    # show the mapping, upload nothing (no volume check)
    python upload_to_databricks.py --force                      # upload even though the volume check refuses

Credentials come from ``DATABRICKS_HOST`` and ``DATABRICKS_TOKEN`` (environment or the repo ``.env``).
Needs ``pip install -e ".[databricks]"`` for the actual upload (not for ``--dry-run``).
``generate_data.py --upload`` calls :func:`upload_run` after generating.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent  # src/data
DEFAULT_RAW_DIR = DATA_DIR / "raw"
ENV_FILE = DATA_DIR.parent.parent / ".env"
VOLUME_ROOT = "/Volumes/data2026acc123456/team3"
FOLDERS = ("datasets", "test_datasets")
DEFAULT_FOLDER = "datasets"

DATASETS = {"equipment": "csv", "maintenance_history": "csv", "work_orders": "csv", "sensor_events": "json"}
ACTIVITY = ("maintenance_history", "work_orders", "sensor_events")  # all a continuation run delivers
_FILE_RE = re.compile(rf"^({'|'.join(DATASETS)})_(\d{{8}}_\d{{6}})\.(csv|json)$")
_ANY_TAG_RE = re.compile(rf"^({'|'.join(DATASETS)})_(.+)\.(csv|json)$")
CHAIN_FILE = "chain.json"
UNTAGGED = "(untagged)"  # tag of an un-suffixed file such as equipment.csv (the original upload of the baseline)


def find_runs(raw_dir: Path) -> dict[str, dict[str, Path]]:
    """Group the source files in ``raw_dir`` by run timestamp: ``{ts: {dataset: path}}``."""
    runs: dict[str, dict[str, Path]] = {}
    for path in sorted(raw_dir.glob("*")):
        m = _FILE_RE.match(path.name)
        if m and m.group(3) == DATASETS[m.group(1)]:
            runs.setdefault(m.group(2), {})[m.group(1)] = path
    return runs


def select_run(runs: dict[str, dict[str, Path]], timestamp: str | None = None) -> tuple[str, dict[str, Path]]:
    """Pick the run to upload: ``timestamp`` if given, else the newest run that is complete.

    A run is complete with all four datasets (a new plant) or with the three activity datasets only (a
    continuation: ``equipment`` is the unchanged reference data of the plant and is not re-sent).

    Raises:
        ValueError: if the run is missing/incomplete or no complete run exists.
    """
    if timestamp is not None:
        files = runs.get(timestamp)
        if files is None:
            raise ValueError(f"no files for timestamp {timestamp}; available: {', '.join(sorted(runs)) or 'none'}")
        missing = sorted(set(ACTIVITY) - set(files))
        if missing:
            raise ValueError(f"run {timestamp} is incomplete, missing: {', '.join(missing)}")
        return timestamp, files
    complete = [ts for ts, files in runs.items() if set(ACTIVITY) <= set(files)]
    if not complete:
        raise ValueError("no complete run (work_orders, maintenance_history and sensor_events with the same "
                         "timestamp) found")
    ts = max(complete)
    return ts, runs[ts]


def resolve_volume_path(volume_path: str | None = None, folder: str | None = None) -> str:
    """Volume folder holding one sub-folder per dataset.

    Precedence: ``volume_path`` > ``folder`` (one of ``FOLDERS``) > env ``DATABRICKS_VOLUME_PATH`` > the default folder.

    Raises:
        ValueError: if ``folder`` is not one of ``FOLDERS``.
    """
    if volume_path:
        return volume_path
    if folder is not None:
        if folder not in FOLDERS:
            raise ValueError(f"unknown folder {folder!r}; choose one of: {', '.join(FOLDERS)}")
        return f"{VOLUME_ROOT}/{folder}"
    return os.environ.get("DATABRICKS_VOLUME_PATH") or f"{VOLUME_ROOT}/{DEFAULT_FOLDER}"


def plan_uploads(files: dict[str, Path], volume_path: str) -> list[tuple[Path, str]]:
    """``[(local_path, remote_path)]`` with remote ``<volume_path>/<dataset>/<file name>``."""
    base = volume_path.rstrip("/")
    return [(files[ds], f"{base}/{ds}/{files[ds].name}") for ds in DATASETS if ds in files]


def read_chain_tags(raw_dir: Path) -> set[str] | None:
    """Tags of every run listed in ``<raw_dir>/chain.json`` (baseline, its aliases, increments); ``None`` if absent."""
    path = Path(raw_dir) / CHAIN_FILE
    if not path.is_file():
        return None
    chain = json.loads(path.read_text(encoding="utf-8"))
    base = chain["baseline"]
    return {base["tag"], *base.get("aliases", []), *(i["tag"] for i in chain.get("increments", []))}


def volume_tags(client: Any, volume_path: str) -> set[str]:
    """Run tags of the files already in the volume's dataset folders (``equipment_<tag>.csv`` -> ``<tag>``;
    an un-suffixed ``equipment.csv`` counts as ``UNTAGGED``).

    Raises:
        RuntimeError: if a folder cannot be listed for another reason than not existing yet.
    """
    tags: set[str] = set()
    base = volume_path.rstrip("/")
    for ds, ext in DATASETS.items():
        try:
            entries = list(client.files.list_directory_contents(f"{base}/{ds}"))
        except Exception as exc:
            if type(exc).__name__ in ("NotFound", "ResourceDoesNotExist"):
                continue  # folder not created yet: nothing delivered
            raise RuntimeError(f"cannot list {base}/{ds}: {exc}") from exc
        for entry in entries:
            name = entry.name or ""
            m = _ANY_TAG_RE.match(name)
            if m and m.group(1) == ds:
                tags.add(m.group(2))
            elif name == f"{ds}.{ext}":
                tags.add(UNTAGGED)
    return tags


def check_volume(client: Any, volume_path: str, ts: str, continuation: bool, chain_tags: set[str] | None) -> None:
    """Refuse an upload that would put conflicting data into the volume (runs from different plants reuse the
    same ids and overlap each other's downtime).

    * New plant (four datasets): the volume must not hold any other run.
    * Continuation: the run must be recorded in ``chain.json`` and every run already in the volume must be one the
      chain knows (baseline, its aliases, increments), so no unknown increment can be overlapped.

    Raises:
        RuntimeError: describing the conflict and how to resolve it.
    """
    present = volume_tags(client, volume_path)
    others = present - {ts}
    if not continuation:
        if others:
            raise RuntimeError(
                f"{volume_path} already holds data of other runs ({', '.join(sorted(others))}). A new plant reuses "
                f"ids (EQ-10001, WO-26001, ...) and overlaps their downtime. Continue the delivered plant instead "
                f"(generate_data.py --continue), empty the folder first, or pass --force.")
        return
    if chain_tags is None:
        raise RuntimeError(f"continuation run {ts} but no {CHAIN_FILE} next to the files: nothing proves it follows "
                           f"what is in the volume")
    if ts not in chain_tags:
        raise RuntimeError(f"run {ts} is not recorded in {CHAIN_FILE}; generate it with --continue so the chain "
                           f"knows it, or pass --force")
    unknown = others - chain_tags
    if unknown:
        raise RuntimeError(
            f"{volume_path} holds runs unknown to {CHAIN_FILE}: {', '.join(sorted(unknown))}. If one is the baseline "
            f"under another name, add its tag to baseline.aliases in {CHAIN_FILE} ('{UNTAGGED}' stands for files "
            f"without a suffix such as equipment.csv); if it is an increment made by a "
            f"teammate, get its files and chain entry first; otherwise remove it from the volume. --force overrides.")
    if not present:
        print(f"  warning: {volume_path} is empty: this run extends data that is not in the volume")


def upload(client: Any, plan: list[tuple[Path, str]]) -> None:
    """Upload every planned file (overwrite is safe: same name means same content).

    Raises:
        RuntimeError: on the first failure, naming the files that were already uploaded.
    """
    done: list[str] = []
    for local, remote in plan:
        try:
            with local.open("rb") as f:
                client.files.upload(remote, f, overwrite=True)
        except Exception as exc:
            raise RuntimeError(f"upload of {local.name} to {remote} failed: {exc}. "
                               f"Already uploaded: {', '.join(done) or 'nothing'}") from exc
        done.append(local.name)
        print(f"  uploaded {local.name} -> {remote}")


def load_env_file(path: Path) -> None:
    """Load ``KEY=VALUE`` lines into ``os.environ`` without overriding variables that are already set."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            if value.strip():
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def make_client() -> Any:
    """Databricks workspace client from ``DATABRICKS_HOST`` / ``DATABRICKS_TOKEN``."""
    host, token = os.environ.get("DATABRICKS_HOST"), os.environ.get("DATABRICKS_TOKEN")
    missing = [n for n, v in (("DATABRICKS_HOST", host), ("DATABRICKS_TOKEN", token)) if not v]
    if missing:
        sys.exit(f"Missing {', '.join(missing)}: set in the environment or in {ENV_FILE}")
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError:
        sys.exit('databricks-sdk is not installed: pip install -e ".[databricks]"')
    return WorkspaceClient(host=host, token=token)


def upload_run(raw_dir: Path = DEFAULT_RAW_DIR, timestamp: str | None = None, volume_path: str | None = None,
               folder: str | None = None, dry_run: bool = False, force: bool = False) -> list[tuple[Path, str]]:
    """Upload one run (``timestamp``, default the latest complete one) and return the ``[(local, remote)]`` plan.

    The volume is checked first (:func:`check_volume`) unless ``dry_run`` (no connection) or ``force``.

    Raises:
        ValueError: if the run is missing/incomplete or ``folder`` is unknown.
        RuntimeError: if the volume check refuses or an upload fails (names the files already uploaded).
    """
    load_env_file(ENV_FILE)
    ts, files = select_run(find_runs(raw_dir), timestamp)
    target = resolve_volume_path(volume_path, folder)
    plan = plan_uploads(files, target)
    continuation = "equipment" not in files
    print(f"Run {ts} from {raw_dir} ({'continuation, no equipment file' if continuation else 'new plant'})")
    if dry_run:
        for local, remote in plan:
            print(f"  {local.name} -> {remote}")
        return plan
    client = make_client()
    if force:
        print("  --force: volume check skipped")
    else:
        check_volume(client, target, ts, continuation, read_chain_tags(raw_dir))
    upload(client, plan)
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--timestamp", help="run to upload, e.g. 20261003_101629 (default: latest complete run)")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR,
                        help="folder with the source files (default: src/data/raw)")
    parser.add_argument("--folder", choices=FOLDERS,
                        help=f"target folder under {VOLUME_ROOT} (default: env DATABRICKS_VOLUME_PATH or {DEFAULT_FOLDER})")
    parser.add_argument("--volume-path",
                        help="full volume folder that holds one sub-folder per dataset; overrides --folder")
    parser.add_argument("--dry-run", action="store_true", help="print the mapping and upload nothing")
    parser.add_argument("--force", action="store_true",
                        help="skip the check that the volume holds no conflicting runs (use with care)")
    args = parser.parse_args()
    try:
        upload_run(args.raw_dir, args.timestamp, args.volume_path, args.folder, args.dry_run, args.force)
    except (ValueError, RuntimeError) as exc:
        sys.exit(f"Error: {exc}")
    if not args.dry_run:
        print("Done.")


if __name__ == "__main__":
    main()
