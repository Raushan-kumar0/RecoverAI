"""
RecoverAI — Mandate Retry Sequencer: Persistence

Sequences need to be UPDATED over time (new attempts, status changes) —
unlike real_results_log.jsonl, which is append-only. This store keeps one
JSON file, keyed by case_id, holding each case's full MandateRetrySequence.

Deliberately kept in its OWN file (mandate_retry_sequences.json), never
merged into real_results_log.jsonl — retry-attempt bookkeeping must never
be mistaken for, or summed into, genuine recovered revenue.
"""

import json
import os
from pathlib import Path
from typing import Dict, Optional

from sequencer_models import MandateRetrySequence

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
SEQUENCE_STORE_PATH = _PROJECT_ROOT / "mandate_retry_sequences.json"


def load_all_sequences(path: Optional[Path] = None) -> Dict[str, MandateRetrySequence]:
    path = path or SEQUENCE_STORE_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}  # corrupted/missing file -> fail safe, never fabricate state
    return {case_id: MandateRetrySequence.from_dict(d) for case_id, d in raw.items()}


def save_all_sequences(sequences: Dict[str, MandateRetrySequence], path: Optional[Path] = None) -> bool:
    path = path or SEQUENCE_STORE_PATH
    try:
        raw = {case_id: seq.to_dict() for case_id, seq in sequences.items()}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, default=str)
        return True
    except OSError:
        return False


def get_sequence(case_id: str, path: Optional[Path] = None) -> Optional[MandateRetrySequence]:
    return load_all_sequences(path).get(case_id)


def save_sequence(sequence: MandateRetrySequence, path: Optional[Path] = None) -> bool:
    sequences = load_all_sequences(path)
    sequences[sequence.case_id] = sequence
    return save_all_sequences(sequences, path)