#!/usr/bin/env python
r"""Convert dated UIH DICOM folders into this project's BIDS layout.

The script expects this project structure by default:
  H:\lcy_data\fMRI_raw\YYYYMMDD\<name>_Lcy_subXXX_<time>\<series>\*.dcm
  H:\lcy_data\behavior\sub-XXX\sub-XXX_ses-YY_task-tactile_run-ZZ_YYYYMMDD_HHMMSS.xlsx
  H:\lcy_data\bids

It uses the dated behavior xlsx filenames to determine the BIDS session/run,
then copies the matching events.tsv file into the BIDS func folder.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_RAW_ROOT = Path(r"H:\lcy_data\fMRI_raw")
DEFAULT_BEHAVIOR_ROOT = Path(r"H:\lcy_data\behavior")
DEFAULT_BIDS_ROOT = Path(r"H:\lcy_data\bids")
TASK_NAME = "tactile"

SUB_RE = re.compile(r"sub[-_]?0*(\d+)", re.IGNORECASE)
TASK_SERIES_RE = re.compile(r"task(\d+)", re.IGNORECASE)
BEHAVIOR_XLSX_RE = re.compile(
    r"^(sub-\d+)_ses-(\d+)_task-tactile_run-(\d+)_(\d{8})_(\d{6})\.xlsx$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BehaviorRun:
    sub_id: str
    ses_id: str
    run_id: str
    run_number: int
    date: str
    clock: str
    xlsx: Path
    events: Path

    @property
    def prefix(self) -> str:
        return f"{self.sub_id}_{self.ses_id}_task-{TASK_NAME}_run-{self.run_id}"


@dataclass(frozen=True)
class SubjectPlan:
    sub_id: str
    date: str
    raw_subject_dir: Path
    ses_id: str
    task_series: list[tuple[int, Path, BehaviorRun]]
    rest_series: list[Path]
    t1_series: Path | None
    fmap_echo1: Path | None
    fmap_echo2: Path | None
    fmap_b0map: Path | None


def info(message: str) -> None:
    print(message, flush=True)


def fail(message: str) -> None:
    raise RuntimeError(message)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert one or more dated fMRI_raw folders into H:\\lcy_data\\bids."
    )
    parser.add_argument(
        "--date",
        nargs="+",
        help="Date folder(s) to convert, for example: --date 20260708. "
        "If omitted, the latest dated raw folder is used.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Convert all YYYYMMDD folders under --raw-root.",
    )
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--behavior-root", type=Path, default=DEFAULT_BEHAVIOR_ROOT)
    parser.add_argument("--bids-root", type=Path, default=DEFAULT_BIDS_ROOT)
    parser.add_argument(
        "--dcm2niix",
        default="dcm2niix",
        help="dcm2niix executable name or full path.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing BIDS NIfTI/JSON/events files for the selected date(s).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the conversion plan without writing files or running dcm2niix.",
    )
    return parser.parse_args(argv)


def assert_existing_dir(path: Path, label: str) -> None:
    if not path.is_dir():
        fail(f"{label} does not exist or is not a directory: {path}")


def discover_dates(raw_root: Path) -> list[str]:
    assert_existing_dir(raw_root, "raw root")
    dates = sorted(p.name for p in raw_root.iterdir() if p.is_dir() and re.fullmatch(r"\d{8}", p.name))
    if not dates:
        fail(f"No YYYYMMDD folders found under {raw_root}")
    return dates


def normalize_dates(args: argparse.Namespace) -> list[str]:
    dates = discover_dates(args.raw_root)
    if args.all and args.date:
        fail("Use either --all or --date, not both.")
    if args.all:
        return dates
    if args.date:
        requested = []
        for date in args.date:
            if not re.fullmatch(r"\d{8}", date):
                fail(f"Invalid date '{date}'. Use YYYYMMDD, for example 20260708.")
            if date not in dates:
                fail(f"Date folder not found under {args.raw_root}: {date}")
            requested.append(date)
        return requested
    latest = dates[-1]
    info(f"No --date given; using latest raw date: {latest}")
    return [latest]


def normalize_sub_id(raw_name: str) -> str:
    match = SUB_RE.search(raw_name)
    if not match:
        fail(f"Cannot find subject id in raw folder name: {raw_name}")
    return f"sub-{int(match.group(1)):03d}"


def sorted_dirs(path: Path) -> list[Path]:
    return sorted((p for p in path.iterdir() if p.is_dir()), key=lambda p: p.name.lower())


def find_behavior_events(sub_dir: Path, prefix: str, date: str, clock: str) -> Path:
    candidates = [
        sub_dir / f"{prefix}_events.tsv",
        sub_dir / f"{prefix}_events_{date}_{clock}.tsv",
    ]
    candidates.extend(sorted(sub_dir.glob(f"{prefix}_events*.tsv")))
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            return candidate
    fail(f"No events.tsv found for {prefix} in {sub_dir}")


def discover_behavior_runs(behavior_root: Path, sub_id: str, date: str) -> list[BehaviorRun]:
    sub_dir = behavior_root / sub_id
    assert_existing_dir(sub_dir, f"behavior folder for {sub_id}")
    runs: list[BehaviorRun] = []
    for xlsx in sorted(sub_dir.glob("*.xlsx")):
        match = BEHAVIOR_XLSX_RE.match(xlsx.name)
        if not match:
            continue
        file_sub, ses_num, run_num, file_date, clock = match.groups()
        if file_sub.lower() != sub_id.lower() or file_date != date:
            continue
        ses_id = f"ses-{int(ses_num):02d}"
        run_id = f"{int(run_num):02d}"
        prefix = f"{sub_id}_{ses_id}_task-{TASK_NAME}_run-{run_id}"
        events = find_behavior_events(sub_dir, prefix, file_date, clock)
        runs.append(
            BehaviorRun(
                sub_id=sub_id,
                ses_id=ses_id,
                run_id=run_id,
                run_number=int(run_num),
                date=file_date,
                clock=clock,
                xlsx=xlsx,
                events=events,
            )
        )
    runs.sort(key=lambda item: item.run_number)
    return runs


def classify_series(raw_subject_dir: Path) -> tuple[list[tuple[int, Path]], list[Path], Path | None, Path | None, Path | None, Path | None]:
    tasks: list[tuple[int, Path]] = []
    rests: list[Path] = []
    t1: Path | None = None
    echo1: Path | None = None
    echo2: Path | None = None
    b0map: Path | None = None

    for series in sorted_dirs(raw_subject_dir):
        name = series.name.lower()
        if name.startswith("bold") and "task" in name:
            match = TASK_SERIES_RE.search(series.name)
            if match:
                tasks.append((int(match.group(1)), series))
        elif name.startswith("bold") and "rest" in name:
            rests.append(series)
        elif name.startswith("t1_mprage") and t1 is None:
            t1 = series
        elif name.startswith("fieldmap") and "echo1" in name and echo1 is None:
            echo1 = series
        elif name.startswith("fieldmap") and "echo2" in name and echo2 is None:
            echo2 = series
        elif name.startswith("fieldmap") and "b0map" in name and b0map is None:
            b0map = series

    tasks.sort(key=lambda item: item[0])
    rests.sort(key=lambda item: item.name.lower())
    return tasks, rests, t1, echo1, echo2, b0map


def build_subject_plan(raw_root: Path, behavior_root: Path, date: str, raw_subject_dir: Path) -> SubjectPlan:
    sub_id = normalize_sub_id(raw_subject_dir.name)
    behavior_runs = discover_behavior_runs(behavior_root, sub_id, date)
    tasks, rests, t1, echo1, echo2, b0map = classify_series(raw_subject_dir)

    if tasks and not behavior_runs:
        fail(f"{sub_id} {date}: task DICOM folders exist, but no dated behavior xlsx/events were found.")
    if len(tasks) != len(behavior_runs):
        task_names = ", ".join(series.name for _, series in tasks) or "none"
        behavior_names = ", ".join(run.prefix for run in behavior_runs) or "none"
        fail(
            f"{sub_id} {date}: task DICOM count ({len(tasks)}) does not match behavior run count "
            f"({len(behavior_runs)}).\n  DICOM: {task_names}\n  behavior: {behavior_names}"
        )

    ses_ids = sorted({run.ses_id for run in behavior_runs})
    if len(ses_ids) > 1:
        fail(f"{sub_id} {date}: behavior runs span multiple sessions: {', '.join(ses_ids)}")
    ses_id = ses_ids[0] if ses_ids else "ses-01"

    paired_tasks = [
        (task_number, series, behavior_run)
        for (task_number, series), behavior_run in zip(tasks, behavior_runs)
    ]

    return SubjectPlan(
        sub_id=sub_id,
        date=date,
        raw_subject_dir=raw_subject_dir,
        ses_id=ses_id,
        task_series=paired_tasks,
        rest_series=rests,
        t1_series=t1,
        fmap_echo1=echo1,
        fmap_echo2=echo2,
        fmap_b0map=b0map,
    )


def build_plans(raw_root: Path, behavior_root: Path, date: str) -> list[SubjectPlan]:
    date_dir = raw_root / date
    assert_existing_dir(date_dir, f"raw date folder {date}")
    raw_subject_dirs = sorted_dirs(date_dir)
    if not raw_subject_dirs:
        fail(f"No subject folders found in {date_dir}")

    by_sub: dict[str, list[Path]] = {}
    for raw_subject_dir in raw_subject_dirs:
        sub_id = normalize_sub_id(raw_subject_dir.name)
        by_sub.setdefault(sub_id, []).append(raw_subject_dir)
    duplicates = {sub_id: dirs for sub_id, dirs in by_sub.items() if len(dirs) > 1}
    if duplicates:
        details = "; ".join(f"{sub_id}: {', '.join(str(p) for p in dirs)}" for sub_id, dirs in duplicates.items())
        fail(f"Duplicate raw folders for the same subject/date are not supported: {details}")

    return [build_subject_plan(raw_root, behavior_root, date, p) for p in raw_subject_dirs]


# Keys that dcm2niix may write incorrectly for non-EPI UIH sequences.
# These are physically meaningless for GRE fieldmaps and T1w/MPRAGE and
# cause fMRIPrep to emit "TotalReadoutTime > 10 s" warnings or worse.
_KEYS_TO_STRIP_FROM_NON_BOLD = (
    "TotalReadoutTime",
    "EffectiveEchoSpacing",
    "DerivedVendorReportedEchoSpacing",
    "BandwidthPerPixelPhaseEncode",
)


def sanitize_non_bold_json(json_path: Path, dry_run: bool) -> None:
    """Remove EPI-only readout fields that dcm2niix wrongly adds to
    non-EPI (fieldmap / T1w) sidecars on UIH scanners."""
    if dry_run:
        info(f"  DRY-RUN sanitize: {json_path.name}")
        return
    if not json_path.is_file():
        return
    with json_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    removed = [key for key in _KEYS_TO_STRIP_FROM_NON_BOLD if key in data]
    if not removed:
        return
    for key in removed:
        del data[key]
    with json_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    info(f"  SANITIZE {json_path.name}: removed {', '.join(removed)}")


def ensure_json_updates(json_path: Path, updates: dict, dry_run: bool) -> None:
    if dry_run:
        info(f"  DRY-RUN update JSON: {json_path.name} <- {sorted(updates)}")
        return
    if not json_path.is_file():
        fail(f"Missing JSON sidecar after conversion: {json_path}")
    with json_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    changed = False
    for key, value in updates.items():
        if data.get(key) != value:
            data[key] = value
            changed = True
    if changed:
        with json_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")


def convert_series(
    source_dir: Path,
    out_dir: Path,
    bids_base: str,
    dcm2niix: str,
    overwrite: bool,
    dry_run: bool,
) -> tuple[Path, Path]:
    expected_nii = out_dir / f"{bids_base}.nii.gz"
    expected_json = out_dir / f"{bids_base}.json"

    if expected_nii.exists() and expected_json.exists() and not overwrite:
        info(f"  SKIP existing: {expected_nii.name}")
        return expected_nii, expected_json
    if (expected_nii.exists() or expected_json.exists()) and not overwrite:
        fail(f"Partial output exists for {bids_base}; rerun with --overwrite after checking it.")

    info(f"  CONVERT {source_dir.name} -> {expected_nii.name}")
    if dry_run:
        return expected_nii, expected_json

    out_dir.mkdir(parents=True, exist_ok=True)
    if overwrite:
        for path in (expected_nii, expected_json):
            if path.exists():
                path.unlink()

    with tempfile.TemporaryDirectory(prefix=".dcm2niix_", dir=str(out_dir)) as tmp_name:
        tmp_dir = Path(tmp_name)
        cmd = [dcm2niix, "-z", "y", "-b", "y", "-f", bids_base, "-o", str(tmp_dir), str(source_dir)]
        subprocess.run(cmd, check=True)

        nii_candidates = sorted(
            p
            for p in tmp_dir.iterdir()
            if p.name.startswith(bids_base) and (p.name.endswith(".nii.gz") or p.name.endswith(".nii"))
        )
        json_candidates = sorted(p for p in tmp_dir.iterdir() if p.name.startswith(bids_base) and p.suffix == ".json")

        if len(nii_candidates) != 1:
            fail(f"Expected exactly one NIfTI for {bids_base}, got {len(nii_candidates)} in {tmp_dir}")
        if len(json_candidates) != 1:
            fail(f"Expected exactly one JSON for {bids_base}, got {len(json_candidates)} in {tmp_dir}")

        shutil.move(str(nii_candidates[0]), expected_nii)
        shutil.move(str(json_candidates[0]), expected_json)

    return expected_nii, expected_json


def copy_events(source: Path, target: Path, overwrite: bool, dry_run: bool) -> None:
    if target.exists() and not overwrite:
        info(f"  SKIP existing events: {target.name}")
        return
    info(f"  COPY events: {source.name} -> {target.name}")
    if dry_run:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def write_rest_events(target: Path, overwrite: bool, dry_run: bool) -> None:
    if target.exists() and not overwrite:
        info(f"  SKIP existing rest events: {target.name}")
        return
    info(f"  WRITE rest events: {target.name}")
    if dry_run:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("onset\tduration\ttrial_type\n", encoding="utf-8", newline="\n")


def load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def update_phasediff_json(
    phasediff_json: Path,
    mag1_json: Path,
    mag2_json: Path,
    intended_for: list[str],
    dry_run: bool,
) -> None:
    updates: dict = {"IntendedFor": intended_for}
    if not dry_run:
        mag1 = load_json(mag1_json)
        mag2 = load_json(mag2_json)
        phase = load_json(phasediff_json)
        if "EchoTime1" not in phase and "EchoTime" in mag1:
            updates["EchoTime1"] = mag1["EchoTime"]
        if "EchoTime2" not in phase and "EchoTime" in mag2:
            updates["EchoTime2"] = mag2["EchoTime"]
    ensure_json_updates(phasediff_json, updates, dry_run)


def ensure_dataset_description(bids_root: Path, dry_run: bool) -> None:
    target = bids_root / "dataset_description.json"
    if target.exists():
        return
    info("WRITE missing dataset_description.json")
    if dry_run:
        return
    bids_root.mkdir(parents=True, exist_ok=True)
    data = {
        "Name": "MindShape Tactile fMRI",
        "BIDSVersion": "1.8.0",
        "DatasetType": "raw",
    }
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def ensure_participants(bids_root: Path, sub_ids: Iterable[str], dry_run: bool) -> None:
    target = bids_root / "participants.tsv"
    sub_ids = sorted(set(sub_ids))
    if dry_run:
        info(f"DRY-RUN ensure participants.tsv contains: {', '.join(sub_ids)}")
        return

    bids_root.mkdir(parents=True, exist_ok=True)
    if target.exists():
        with target.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            fieldnames = reader.fieldnames or ["participant_id", "age", "sex", "handedness"]
            rows = list(reader)
    else:
        fieldnames = ["participant_id", "age", "sex", "handedness"]
        rows = []

    if "participant_id" not in fieldnames:
        fail(f"participants.tsv must contain participant_id column: {target}")

    existing = {row.get("participant_id", "") for row in rows}
    changed = False
    for sub_id in sub_ids:
        if sub_id not in existing:
            row = {field: "n/a" for field in fieldnames}
            row["participant_id"] = sub_id
            rows.append(row)
            changed = True

    if changed or not target.exists():
        rows.sort(key=lambda row: row.get("participant_id", ""))
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)


def process_plan(plan: SubjectPlan, bids_root: Path, dcm2niix: str, overwrite: bool, dry_run: bool) -> None:
    info(f"\n{plan.sub_id} {plan.ses_id} from {plan.raw_subject_dir}")
    sub_root = bids_root / plan.sub_id / plan.ses_id
    anat_dir = sub_root / "anat"
    fmap_dir = sub_root / "fmap"
    func_dir = sub_root / "func"

    intended_for: list[str] = []

    if plan.t1_series:
        base = f"{plan.sub_id}_{plan.ses_id}_T1w"
        _nii, json_path = convert_series(plan.t1_series, anat_dir, base, dcm2niix, overwrite, dry_run)
        sanitize_non_bold_json(json_path, dry_run)
    else:
        info("  WARN no T1 series found")

    for rest_index, rest_series in enumerate(plan.rest_series, start=1):
        if len(plan.rest_series) == 1:
            base = f"{plan.sub_id}_{plan.ses_id}_task-rest_bold"
            events_base = f"{plan.sub_id}_{plan.ses_id}_task-rest_events.tsv"
        else:
            base = f"{plan.sub_id}_{plan.ses_id}_task-rest_run-{rest_index:02d}_bold"
            events_base = f"{plan.sub_id}_{plan.ses_id}_task-rest_run-{rest_index:02d}_events.tsv"
        nii, json_path = convert_series(rest_series, func_dir, base, dcm2niix, overwrite, dry_run)
        ensure_json_updates(json_path, {"TaskName": "rest"}, dry_run)
        write_rest_events(func_dir / events_base, overwrite, dry_run)
        intended_for.append(f"{plan.ses_id}/func/{nii.name}")

    for _task_number, series, behavior_run in plan.task_series:
        base = f"{behavior_run.prefix}_bold"
        nii, json_path = convert_series(series, func_dir, base, dcm2niix, overwrite, dry_run)
        ensure_json_updates(json_path, {"TaskName": TASK_NAME}, dry_run)
        copy_events(behavior_run.events, func_dir / f"{behavior_run.prefix}_events.tsv", overwrite, dry_run)
        intended_for.append(f"{plan.ses_id}/func/{nii.name}")

    fmap_sources = [
        ("magnitude1", plan.fmap_echo1),
        ("magnitude2", plan.fmap_echo2),
        ("phasediff", plan.fmap_b0map),
    ]
    fmap_jsons: dict[str, Path] = {}
    missing_fmaps = [name for name, source in fmap_sources if source is None]
    if missing_fmaps:
        info(f"  WARN missing fieldmap series: {', '.join(missing_fmaps)}")
    else:
        for suffix, source in fmap_sources:
            assert source is not None
            base = f"{plan.sub_id}_{plan.ses_id}_{suffix}"
            _nii, json_path = convert_series(source, fmap_dir, base, dcm2niix, overwrite, dry_run)
            sanitize_non_bold_json(json_path, dry_run)
            fmap_jsons[suffix] = json_path
        update_phasediff_json(
            fmap_jsons["phasediff"],
            fmap_jsons["magnitude1"],
            fmap_jsons["magnitude2"],
            intended_for,
            dry_run,
        )


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    assert_existing_dir(args.raw_root, "raw root")
    assert_existing_dir(args.behavior_root, "behavior root")

    if not args.dry_run and shutil.which(args.dcm2niix) is None and not Path(args.dcm2niix).exists():
        fail(f"dcm2niix not found: {args.dcm2niix}. Add it to PATH or pass --dcm2niix.")

    dates = normalize_dates(args)
    all_plans: list[SubjectPlan] = []
    for date in dates:
        plans = build_plans(args.raw_root, args.behavior_root, date)
        all_plans.extend(plans)

    ensure_dataset_description(args.bids_root, args.dry_run)
    ensure_participants(args.bids_root, (plan.sub_id for plan in all_plans), args.dry_run)

    info("\nConversion plan:")
    for plan in all_plans:
        task_pairs = ", ".join(f"task{n}->run-{run.run_id}" for n, _series, run in plan.task_series) or "none"
        rest_count = len(plan.rest_series)
        info(f"  {plan.date} {plan.sub_id} {plan.ses_id}: tactile {task_pairs}; rest={rest_count}")

    for plan in all_plans:
        process_plan(plan, args.bids_root, args.dcm2niix, args.overwrite, args.dry_run)

    info("\nDone.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: dcm2niix failed with exit code {exc.returncode}: {' '.join(exc.cmd)}", file=sys.stderr)
        raise SystemExit(exc.returncode)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
