"""Завантажує модель embeddings (multilingual-e5-small, ONNX) і перевіряє sha256.

Лише stdlib: працює в окремому stage Docker-образу ще до встановлення залежностей.

    python scripts/fetch_model.py .data/models/multilingual-e5-small
    python scripts/fetch_model.py --variant fp32 .data/models/multilingual-e5-small-fp32
"""

import argparse
import hashlib
import logging
import shutil
import sys
import time
import urllib.request
from pathlib import Path

REPO = "Xenova/multilingual-e5-small"
# Ревізія закріплена: той самий файл сьогодні й через рік; оновлення — свідомо, разом із sha256
REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
# sha256 файлів, а не секрети: gitleaks бачить «token» у назві й hex поруч
TOKENIZER = (
    "tokenizer.json",
    "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",  # gitleaks:allow
)
# int8 per-channel з reduce_range: точний і на CPU без AVX512-VNNI (AVX2, ARM)
VARIANTS = {
    "int8": (
        "onnx/model_quantized.onnx",
        "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193",
    ),
    "fp32": ("onnx/model.onnx", "4aa845c27760e06e9a686b9d8b5d440eae4b6612cd09e5b522b716d3941f77ff"),
}
ATTEMPTS = 3

logger = logging.getLogger("fetch_model")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(remote: str, expected: str, target: Path) -> None:
    if target.is_file() and sha256(target) == expected:
        logger.info("%s: уже є", target.name)
        return
    url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{remote}"
    partial = target.with_suffix(target.suffix + ".part")
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as out:
                shutil.copyfileobj(response, out, 1 << 20)
            break
        except OSError as error:
            if attempt == ATTEMPTS:
                raise
            logger.warning("%s: %s — повтор %d/%d", target.name, error, attempt, ATTEMPTS - 1)
            time.sleep(2 * attempt)
    actual = sha256(partial)
    if actual != expected:
        partial.unlink()
        sys.exit(f"{remote}: sha256 {actual} ≠ очікуваного {expected}")
    partial.replace(target)
    logger.info("%s: %.1f МБ", target.name, target.stat().st_size / 1e6)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dest", type=Path, help="тека моделі")
    parser.add_argument("--variant", choices=VARIANTS, default="int8")
    args = parser.parse_args()
    # Консоль Windows (cp1251) не друкує «МБ» з UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
    args.dest.mkdir(parents=True, exist_ok=True)
    fetch(*VARIANTS[args.variant], args.dest / "model.onnx")
    fetch(*TOKENIZER, args.dest / "tokenizer.json")


if __name__ == "__main__":
    main()
