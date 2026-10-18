"""Знімок «Лісового архіву»: усі файли теки + manifest.json у data/archive.

Знімок комітиться й іде в образ: бот знає архів з першого старту навіть без
доступу до Drive (D-40), а eval має сталий корпус (S-08).

    uv run python scripts/snapshot_archive.py
"""

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
from pathlib import Path

from forest_bot.archive.drive import DriveClient, DriveFile
from forest_bot.config import DEFAULT_ARCHIVE_FOLDER_ID

logger = logging.getLogger("snapshot_archive")

DEST = Path("data/archive")
MANIFEST = "manifest.json"
PARALLEL = 4


def file_names(files: list[DriveFile]) -> dict[str, str]:
    """Назва на диску для кожного id: без «/» і без колізій однакових назв."""
    names: dict[str, str] = {}
    taken: set[str] = set()
    for file in sorted(files, key=lambda f: (f.name, f.id)):
        name = re.sub(r'[\\/:*?"<>|]', "_", file.name)
        if name.casefold() in taken:
            stem, dot, ext = name.rpartition(".")
            name = f"{stem} ({file.id[:6]}){dot}{ext}" if dot else f"{name} ({file.id[:6]})"
        taken.add(name.casefold())
        names[file.id] = name
    return names


async def snapshot(folder_id: str, dest: Path) -> list[dict[str, object]]:
    client = DriveClient()
    slots = asyncio.Semaphore(PARALLEL)
    try:
        files = await client.list_folder(folder_id)
        names = file_names(files)

        async def fetch(file: DriveFile) -> tuple[DriveFile, bytes]:
            async with slots:
                return file, await client.download(file.id)

        downloaded = await asyncio.gather(*(fetch(f) for f in files))
    finally:
        await client.aclose()

    entries = []
    for file, data in sorted(downloaded, key=lambda item: names[item[0].id]):
        entries.append(
            {
                "id": file.id,
                "name": file.name,
                "file": names[file.id],
                "modified": file.modified,
                "mime": file.mime,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        )
        (dest / names[file.id]).write_bytes(data)
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--folder", default=DEFAULT_ARCHIVE_FOLDER_ID)
    parser.add_argument("--dest", type=Path, default=DEST)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    args.dest.mkdir(parents=True, exist_ok=True)
    stale = {p.name for p in args.dest.iterdir() if p.name != MANIFEST}
    entries = asyncio.run(snapshot(args.folder, args.dest))
    # Файли, яких більше немає в теці, зі знімка прибираємо
    for name in stale - {str(e["file"]) for e in entries}:
        (args.dest / name).unlink()
        logger.info("прибрано: %s", name)
    manifest = {"folder_id": args.folder, "files": entries}
    (args.dest / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    total = sum(int(str(e["size"])) for e in entries)
    logger.info("Файлів: %d, разом %.1f МБ → %s", len(entries), total / 1e6, args.dest)


if __name__ == "__main__":
    main()
