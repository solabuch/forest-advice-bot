"""Drive без ключа: розбір лістингу теки, завантаження, повтори, збої."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from forest_bot.archive.drive import (
    ATTEMPTS,
    DriveClient,
    DriveError,
    DriveFile,
    parse_listing,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ARCHIVE = Path(__file__).resolve().parents[2] / "data" / "archive"


def listing() -> str:
    # Справжня сторінка embeddedfolderview (18.10), скорочена до двох файлів і підтеки
    return (FIXTURES / "drive_folder.html").read_text(encoding="utf-8")


def test_parse_real_listing() -> None:
    assert parse_listing(listing()) == [
        DriveFile(
            id="1okSXljk-RcvJCS94MHR_mBduoRgJYZqg",
            name="Їстівне й отруйне.pdf",
            modified="Oct 13",
            mime="application/pdf",
        ),
        DriveFile(
            id="1zHZkZSVrBy_-CDVimzK2E0hfDEOrM66U",
            name="Календар сезонів.txt",
            modified="Oct 13",
            mime="text/plain",
        ),
    ]


@pytest.mark.parametrize(
    "page",
    ["<html><body>Sign in</body></html>", ""],
    ids=["login-page", "empty"],
)
def test_unknown_page_is_an_error_not_an_empty_folder(page: str) -> None:
    # Інакше всі документи архіву помилково вважалися б зниклими (D-40)
    with pytest.raises(DriveError, match="не впізнано"):
        parse_listing(page)


def test_empty_folder_is_empty() -> None:
    assert parse_listing('<div class="flip-entries"></div>') == []


async def no_sleep(_: float) -> None:
    return None


def client(handler: httpx.MockTransport) -> DriveClient:
    return DriveClient(transport=handler, sleep=no_sleep)


async def test_list_folder() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.params["id"] == "folder"
        return httpx.Response(200, text=listing())

    drive = client(httpx.MockTransport(handle))
    files = await drive.list_folder("folder")
    await drive.aclose()
    assert [f.name for f in files] == ["Їстівне й отруйне.pdf", "Календар сезонів.txt"]


async def test_download_retries_transient_errors() -> None:
    replies: list[httpx.Response | Exception] = [
        httpx.ReadTimeout("slow"),
        httpx.Response(503),
        httpx.Response(200, content=b"%PDF"),
    ]

    def handle(_: httpx.Request) -> httpx.Response:
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    assert await client(httpx.MockTransport(handle)).download("f1") == b"%PDF"
    assert replies == []


async def test_download_gives_up_after_attempts() -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(502)

    with pytest.raises(DriveError, match="файл f1: HTTP 502"):
        await client(httpx.MockTransport(handle)).download("f1")
    assert len(calls) == ATTEMPTS


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(404), "HTTP 404"),
        (
            httpx.Response(200, text="<html>quota</html>", headers={"content-type": "text/html"}),
            "сторінку замість файлу",
        ),
    ],
    ids=["missing", "html-instead-of-file"],
)
async def test_download_permanent_errors(response: httpx.Response, message: str) -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response

    with pytest.raises(DriveError, match=message):
        await client(httpx.MockTransport(handle)).download("f1")
    assert len(calls) == 1


async def test_download_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("forest_bot.archive.drive.MAX_FILE_BYTES", 10)
    drive = client(httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 11)))
    with pytest.raises(DriveError, match="більший"):
        await drive.download("f1")


def test_snapshot_matches_manifest() -> None:
    # Знімок у репо — ті самі байти, що на Диску: git і pre-commit їх не чіпають
    manifest = json.loads((ARCHIVE / "manifest.json").read_text(encoding="utf-8"))
    files = manifest["files"]
    assert len(files) == 78
    for entry in files:
        data = (ARCHIVE / entry["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["file"]
    on_disk = {p.name for p in ARCHIVE.iterdir()} - {"manifest.json"}
    assert on_disk == {entry["file"] for entry in files}
