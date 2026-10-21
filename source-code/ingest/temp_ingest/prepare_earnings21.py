"""Download Earnings-21 transcripts and convert them into the ingest parquet schema."""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from temp_ingest.config import Settings

REPO_URL = "https://github.com/revdotcom/speech-datasets.git"
DATASET_DIR = "earnings21"

# Columns expected by temp_ingest.ingest._build_arrow (all strings; ingest coerces).
PARQUET_COLUMNS = [
    "id",
    "engagement_id",
    "account_id",
    "customer",
    "opportunity_id",
    "opportunity",
    "current_sales_stage_name",
    "sales_stage",
    "index",
    "snippet_time",
    "snippet_length",
    "date_time",
    "speaker_name",
    "speaker_type",
    "participant",
    "engaging",
    "snippet_word_count",
    "snippet",
]


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def download_earnings21(dest: Path, force: bool = False) -> Path:
    """Sparse-clone transcripts + metadata only (skips multi-GB audio)."""
    root = dest / "speech-datasets"
    earnings21 = root / DATASET_DIR
    if earnings21.exists() and not force:
        print(f"[prepare] reusing existing clone: {earnings21}")
        return earnings21

    if root.exists() and force:
        shutil.rmtree(root)

    dest.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--filter=blob:none",
            "--sparse",
            REPO_URL,
            str(root),
        ]
    )
    # Cone paths: metadata CSVs + token-level transcripts. No media/.
    _run(
        [
            "git",
            "sparse-checkout",
            "set",
            "--skip-checks",
            f"{DATASET_DIR}/transcripts/nlp_references",
            f"{DATASET_DIR}/transcripts/wer_tags",
            f"{DATASET_DIR}/earnings21-file-metadata.csv",
            f"{DATASET_DIR}/eval10-file-metadata.csv",
            f"{DATASET_DIR}/speaker-metadata.csv",
            f"{DATASET_DIR}/README.md",
            f"{DATASET_DIR}/LICENSE.md",
        ],
        cwd=root,
    )
    if not earnings21.exists():
        raise FileNotFoundError(f"sparse checkout failed — missing {earnings21}")
    print(f"[prepare] downloaded {earnings21}")
    return earnings21


def _load_file_metadata(path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            # CSV headers sometimes have a trailing space (curator_id ).
            cleaned = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            file_id = cleaned.get("file_id", "")
            if file_id:
                out[file_id] = cleaned
    return out


def _load_speaker_metadata(path: Path) -> dict[str, dict[str, str]]:
    """file_id -> {speaker_id -> speaker_name}."""
    out: dict[str, dict[str, str]] = defaultdict(dict)
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            cleaned = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            fid = cleaned.get("file_id", "")
            sid = cleaned.get("speaker_id", "")
            name = cleaned.get("speaker_name", "") or f"Speaker {sid}"
            if fid and sid != "":
                out[fid][sid] = name
    return out


def _apply_case(token: str, case: str) -> str:
    if not token:
        return token
    case = (case or "").upper()
    if case == "UC":
        return token[:1].upper() + token[1:].lower()
    if case == "LC":
        return token.lower()
    if case == "CA":
        return token.upper()
    return token  # MC / unknown — keep as written


def _parse_nlp_turns(
    nlp_path: Path,
    speaker_names: dict[str, str],
) -> list[dict]:
    """Collapse token stream into consecutive same-speaker utterances."""
    turns: list[dict] = []
    cur_speaker: str | None = None
    cur_tokens: list[str] = []
    cur_start: float | None = None
    cur_end: float = 0.0

    def flush() -> None:
        nonlocal cur_speaker, cur_tokens, cur_start, cur_end
        if cur_speaker is None or not cur_tokens:
            cur_tokens = []
            return
        text = " ".join(cur_tokens).strip()
        text = text.replace(" ,", ",").replace(" .", ".").replace(" ?", "?").replace(" !", "!")
        text = text.replace(" ;", ";").replace(" :", ":")
        if text:
            turns.append(
                {
                    "speaker_id": cur_speaker,
                    "speaker_name": speaker_names.get(cur_speaker, f"Speaker {cur_speaker}"),
                    "time_start": float(cur_start or 0.0),
                    "time_end": float(cur_end or cur_start or 0.0),
                    "text": text,
                }
            )
        cur_tokens = []

    with nlp_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="|")
        for row in reader:
            token = (row.get("token") or "").strip()
            if not token:
                continue
            speaker = (row.get("speaker") or "0").strip()
            case = (row.get("case") or "").strip()
            punct = (row.get("punctuation") or "").strip()
            try:
                ts = float(row.get("ts") or 0.0) if row.get("ts") else None
                end_ts = float(row.get("endTs") or 0.0) if row.get("endTs") else None
            except ValueError:
                ts, end_ts = None, None

            word = _apply_case(token, case) + (punct if punct else "")

            if cur_speaker is None:
                cur_speaker = speaker
                cur_start = ts if ts is not None else 0.0
            elif speaker != cur_speaker:
                flush()
                cur_speaker = speaker
                cur_start = ts if ts is not None else cur_end

            cur_tokens.append(word)
            if end_ts is not None:
                cur_end = end_ts
            elif ts is not None:
                cur_end = ts

    flush()
    return turns


def _speaker_type(name: str) -> str:
    lowered = name.strip().lower()
    if lowered in {"operator", "meeting host"} or lowered.startswith("operator"):
        return "operator"
    # Heuristic: named "Speaker N" / anonymous → analyst/external; otherwise company side.
    if lowered.startswith("speaker"):
        return "analyst"
    return "company"


def _merge_turns(turns: list[dict], target_words: int, max_seconds: float) -> list[dict]:
    """Pack short speaker turns into ~target_words windows (still speaker-homogeneous)."""
    if target_words <= 0:
        return turns
    merged: list[dict] = []
    buf: list[dict] = []

    def flush_buf() -> None:
        nonlocal buf
        if not buf:
            return
        text = " ".join(t["text"] for t in buf).strip()
        words = len(text.split())
        merged.append(
            {
                "speaker_id": buf[0]["speaker_id"],
                "speaker_name": buf[0]["speaker_name"],
                "time_start": buf[0]["time_start"],
                "time_end": buf[-1]["time_end"],
                "text": text,
                "word_count": words,
            }
        )
        buf = []

    for turn in turns:
        words = len(turn["text"].split())
        if not buf:
            buf = [turn]
            continue
        same_speaker = turn["speaker_id"] == buf[0]["speaker_id"]
        buf_words = sum(len(t["text"].split()) for t in buf)
        buf_span = turn["time_end"] - buf[0]["time_start"]
        if (
            same_speaker
            and buf_words + words <= target_words
            and buf_span <= max_seconds
        ):
            buf.append(turn)
        else:
            flush_buf()
            buf = [turn]
    flush_buf()
    return merged


def convert_call(
    file_id: str,
    meta: dict[str, str],
    speakers: dict[str, str],
    nlp_path: Path,
    target_words: int,
    max_seconds: float,
) -> list[dict]:
    turns = _parse_nlp_turns(nlp_path, speakers)
    chunks = _merge_turns(turns, target_words=target_words, max_seconds=max_seconds)

    company = meta.get("company_name") or f"call-{file_id}"
    quarter = meta.get("financial_quarter") or ""
    sector = meta.get("sector") or "Earnings Call"
    opportunity = f"{company} Q{quarter} 2020 earnings".strip()
    call_date = f"2020-Q{quarter}" if quarter else "2020"

    rows: list[dict] = []
    for idx, chunk in enumerate(chunks):
        text = chunk["text"]
        words = int(chunk.get("word_count") or len(text.split()))
        t0 = float(chunk["time_start"])
        t1 = float(chunk["time_end"])
        speaker_name = chunk["speaker_name"]
        rows.append(
            {
                "id": f"{file_id}_{idx}",
                "engagement_id": file_id,
                "account_id": file_id,
                "customer": company,
                "opportunity_id": file_id,
                "opportunity": opportunity,
                "current_sales_stage_name": sector,
                "sales_stage": sector,
                "index": str(idx),
                "snippet_time": str(t0),
                "snippet_length": str(max(0.0, t1 - t0)),
                "date_time": call_date,
                "speaker_name": speaker_name,
                "speaker_type": _speaker_type(speaker_name),
                "participant": str(chunk.get("speaker_id") or "0"),
                "engaging": "false",
                "snippet_word_count": str(words),
                "snippet": text,
            }
        )
    return rows


def prepare(
    earnings21_root: Path,
    out_dir: Path,
    *,
    eval10: bool = False,
    limit: int = 0,
    target_words: int = 180,
    max_seconds: float = 90.0,
) -> list[Path]:
    meta_name = "eval10-file-metadata.csv" if eval10 else "earnings21-file-metadata.csv"
    file_meta = _load_file_metadata(earnings21_root / meta_name)
    speakers = _load_speaker_metadata(earnings21_root / "speaker-metadata.csv")
    nlp_dir = earnings21_root / "transcripts" / "nlp_references"
    if not nlp_dir.is_dir():
        raise FileNotFoundError(f"missing nlp_references at {nlp_dir}")

    file_ids = sorted(file_meta)
    if limit and limit > 0:
        file_ids = file_ids[:limit]

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    schema = pa.schema([(c, pa.string()) for c in PARQUET_COLUMNS])

    for file_id in file_ids:
        nlp_path = nlp_dir / f"{file_id}.nlp"
        if not nlp_path.exists():
            print(f"[prepare] WARN: missing transcript {nlp_path.name}; skip")
            continue
        rows = convert_call(
            file_id,
            file_meta[file_id],
            speakers.get(file_id, {}),
            nlp_path,
            target_words=target_words,
            max_seconds=max_seconds,
        )
        if not rows:
            print(f"[prepare] WARN: empty transcript {file_id}; skip")
            continue
        table = pa.Table.from_pylist(rows, schema=schema)
        target = out_dir / f"{file_id}.parquet"
        pq.write_table(table, target)
        written.append(target)
        company = file_meta[file_id].get("company_name", file_id)
        print(f"[prepare] {file_id}  {company:<32}  {len(rows):4d} chunks -> {target.name}")

    return written


def main() -> int:
    settings = Settings.load()
    parser = argparse.ArgumentParser(prog="temp_ingest.prepare_earnings21")
    parser.add_argument(
        "--download-dir",
        default="data/raw",
        help="where to sparse-clone speech-datasets (default: data/raw)",
    )
    parser.add_argument("--out", default=settings.ingest.data_dir, help="per-call parquet dir")
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="reuse an existing sparse clone under --download-dir",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="delete and re-clone even if present",
    )
    parser.add_argument(
        "--eval10",
        action="store_true",
        help="only the Eval-10 subset (~10 hours / ~10 calls)",
    )
    parser.add_argument("--limit", type=int, default=0, help="cap number of calls; 0 = all")
    parser.add_argument(
        "--target-words",
        type=int,
        default=180,
        help="merge same-speaker turns up to this many words per chunk (0 = no merge)",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=90.0,
        help="also cap merged chunk duration in seconds",
    )
    args = parser.parse_args()

    download_dir = Path(args.download_dir)
    if args.skip_download:
        earnings21 = download_dir / "speech-datasets" / DATASET_DIR
        if not earnings21.exists():
            raise FileNotFoundError(
                f"--skip-download set but {earnings21} not found; run without it once"
            )
    else:
        earnings21 = download_earnings21(download_dir, force=args.force_download)

    written = prepare(
        earnings21,
        Path(args.out),
        eval10=args.eval10,
        limit=args.limit,
        target_words=args.target_words,
        max_seconds=args.max_seconds,
    )
    total = sum(pq.read_metadata(p).num_rows for p in written)
    print(f"[prepare] wrote {len(written)} calls / {total} chunks -> {args.out}")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
