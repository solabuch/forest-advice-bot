"""Документи з examples/ — те, що обіцяє examples/README.md рев'юеру, перевірено на конвеєрі."""

import os
import shutil
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot import texts
from forest_bot.ingest.upload import UploadPipeline
from forest_bot.models import UploadStatus
from tests.integration.test_upload import (
    FEES,
    SOVA,
    make_pipeline,
    messenger,
    run_index_jobs,
    state,
    upload,
)

pytestmark = pytest.mark.integration

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
Sessions = async_sessionmaker[AsyncSession]

# Файл → (місце, назва): розібрано, місце знайдено без LLM, документ уже шукається
INDEXED = {
    "Нічна тиша в рукавичці У соснах.pdf": ("u-sosnakh", "Нічна тиша в рукавичці «У соснах»"),
    "Як перезимувати біля річки.doc": ("river", "Як перезимувати біля річки"),
    "Ягоди на узліссі.rtf": ("na-uzlissi", "Ягоди на узліссі"),
    "Свято першого снігу.odt": ("forest", "Свято першого снігу"),
    "Розклад толоки в хвойному лісі.docx": ("coniferous", "Розклад толоки в хвойному лісі"),
    "Як ходити повз мурашник.md": ("bilia-murashnyka", "Як ходити повз мурашник"),
}
NEW_VERSION = "Членські внески рукавички Під дубом (новий варіант).txt"
NO_PLACE = "Як сушити гриби.txt"
UNKNOWN_PLACE = "Листопад у рукавичці Під кленом.txt"
SEVERAL_PLACES = "Чергування рукавичок Під дубом і У ліщині.txt"
TOOLS = {".pdf": "tesseract", ".doc": "antiword"}


def require_tool(name: str) -> None:
    tool = TOOLS.get(Path(name).suffix)
    if tool and shutil.which(tool) is None:
        if os.environ.get("CI"):
            pytest.fail(f"У CI має бути {tool}")
        pytest.skip(f"немає {tool}")


@pytest.fixture
async def pipeline(sessions: Sessions) -> UploadPipeline:
    return await make_pipeline(sessions)


async def send(pipeline: UploadPipeline, name: str) -> int:
    upload_id = await upload(pipeline, (EXAMPLES / name).read_bytes(), name)
    await pipeline.prepare(upload_id)
    await run_index_jobs(pipeline)
    return upload_id


def test_every_example_is_covered() -> None:
    files = {p.name for p in EXAMPLES.iterdir() if p.name != "README.md"}
    assert files == {*INDEXED, NEW_VERSION, NO_PLACE, UNKNOWN_PLACE, SEVERAL_PLACES}


@pytest.mark.parametrize("name", sorted(INDEXED))
async def test_formats_are_indexed_with_place_and_title(
    pipeline: UploadPipeline, name: str
) -> None:
    require_tool(name)
    done = await state(pipeline, await send(pipeline, name))
    assert (done.status, done.place_id, done.title) == (UploadStatus.DONE, *INDEXED[name])


async def test_new_version_asks_to_replace(pipeline: UploadPipeline) -> None:
    await pipeline.prepare(await upload(pipeline, FEES, "внески.txt", user=SOVA))
    await run_index_jobs(pipeline)
    draft = await state(pipeline, await send(pipeline, NEW_VERSION))
    assert draft.status is UploadStatus.NEEDS_CHOICE
    assert "новий варіант документа" in messenger(pipeline).sent[-1][1]


async def test_no_place_asks_with_buttons(pipeline: UploadPipeline) -> None:
    draft = await state(pipeline, await send(pipeline, NO_PLACE))
    assert draft.status is UploadStatus.NEEDS_PLACE
    assert messenger(pipeline).sent[-1][1] == texts.ASK_PLACE.format(title="Як сушити гриби")


async def test_unknown_place_is_named(pipeline: UploadPipeline) -> None:
    draft = await state(pipeline, await send(pipeline, UNKNOWN_PLACE))
    assert draft.status is UploadStatus.NEEDS_PLACE
    assert messenger(pipeline).sent[-1][1] == texts.ASK_UNKNOWN_PLACE.format(
        title="Листопад у рукавичці «Під кленом»", name="Під кленом"
    )


async def test_several_places_ask_to_split(pipeline: UploadPipeline) -> None:
    draft = await state(pipeline, await send(pipeline, SEVERAL_PLACES))
    assert draft.status is UploadStatus.CANCELLED
    assert messenger(pipeline).sent[-1][1] == texts.SEVERAL_PLACES.format(
        name=SEVERAL_PLACES, places="Під дубом, У ліщині"
    )
