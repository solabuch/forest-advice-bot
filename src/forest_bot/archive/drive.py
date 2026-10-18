"""Публічна тека Google Диска без API-ключа: лістинг і завантаження (ADR-14).

Лістинг — статична сторінка `embeddedfolderview`: усі файли теки з id, назвою,
позначкою зміни й MIME (з іконки). Звичайна сторінка теки показує лише ~50 файлів,
решту довантажує скрипт. Ендпоінт неофіційний, тому сторінку, яку не вдалося
впізнати, вважаємо збоєм лістингу, а не порожньою текою: так зниклими файли
не позначаються помилково (D-40).
"""

import asyncio
import html
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

LIST_URL = "https://drive.google.com/embeddedfolderview"
DOWNLOAD_URL = "https://drive.usercontent.google.com/download"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# Документи архіву — до 2 МБ; більше — щось не те, не тягнемо
MAX_FILE_BYTES = 50 * 1024 * 1024
FOLDER_MIME = "application/vnd.google-apps.folder"
# Drive іноді відповідає повільно: таймаут чи 5xx — ще дві спроби з паузою
ATTEMPTS = 3
RETRY_PAUSE = 2.0

logger = logging.getLogger(__name__)

_ENTRIES = 'class="flip-entries"'
_ENTRY_START = '<div class="flip-entry" id="entry-'
_ENTRY_ID = re.compile(r"^([\w-]+)\"")
_TITLE = re.compile(r'<div class="flip-entry-title">(.*?)</div>', re.S)
_MODIFIED = re.compile(r'<div class="flip-entry-last-modified"><div>(.*?)</div>', re.S)
_MIME = re.compile(r'googleusercontent\.com/\d+/type/([^"?]+)')


class DriveError(Exception):
    """Drive недоступний або віддав не те; повідомлення — для логів."""


class _TransientError(DriveError):
    """Тимчасовий збій: таймаут, мережа, 5xx — є сенс повторити."""


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    # Позначка зміни з лістингу («Oct 13»): дешевий сигнал, що файл варто перекачати
    modified: str
    mime: str | None


def parse_listing(page: str) -> list[DriveFile]:
    """HTML `embeddedfolderview` → файли теки; підтеки пропускаються."""
    if _ENTRIES not in page:
        raise DriveError("сторінку теки не впізнано — Drive змінив розмітку або тека закрита")
    files = []
    for chunk in page.split(_ENTRY_START)[1:]:
        entry_id, title = _ENTRY_ID.match(chunk), _TITLE.search(chunk)
        if entry_id is None or title is None:
            raise DriveError("запис у лістингу без id чи назви")
        modified, mime = _MODIFIED.search(chunk), _MIME.search(chunk)
        file = DriveFile(
            id=entry_id.group(1),
            name=html.unescape(title.group(1)).strip(),
            modified=html.unescape(modified.group(1)).strip() if modified else "",
            mime=mime.group(1) if mime else None,
        )
        if file.mime != FOLDER_MIME:
            files.append(file)
    return files


class DriveClient:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "forest-bot (+https://github.com/solabuch/forest-advice-bot)"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def list_folder(self, folder_id: str) -> list[DriveFile]:
        return await self._retry(f"лістинг теки {folder_id}", self._list, folder_id)

    async def download(self, file_id: str) -> bytes:
        return await self._retry(f"файл {file_id}", self._download, file_id)

    async def _retry[T](self, what: str, call: Callable[[str], Awaitable[T]], arg: str) -> T:
        for attempt in range(1, ATTEMPTS + 1):
            try:
                return await call(arg)
            except _TransientError as error:
                if attempt == ATTEMPTS:
                    raise DriveError(f"{what}: {error}") from error
                logger.warning("Drive, %s: %s — повтор %d/%d", what, error, attempt, ATTEMPTS - 1)
                await self._sleep(RETRY_PAUSE * attempt)
            except DriveError as error:
                raise DriveError(f"{what}: {error}") from error
        raise AssertionError("недосяжно")

    async def _list(self, folder_id: str) -> list[DriveFile]:
        try:
            response = await self._http.get(LIST_URL, params={"id": folder_id})
        except httpx.HTTPError as error:
            raise _TransientError(type(error).__name__) from error
        _check_status(response)
        return parse_listing(response.text)

    async def _download(self, file_id: str) -> bytes:
        params = {"id": file_id, "export": "download"}
        try:
            async with self._http.stream("GET", DOWNLOAD_URL, params=params) as response:
                _check_status(response)
                # Замість файлу — сторінка (квота, перевірка на віруси, доступ закрито)
                if response.headers.get("content-type", "").startswith("text/html"):
                    raise DriveError("Drive віддав сторінку замість файлу")
                data = bytearray()
                async for part in response.aiter_bytes():
                    data += part
                    if len(data) > MAX_FILE_BYTES:
                        raise DriveError(f"більший за {MAX_FILE_BYTES >> 20} МБ")
        except httpx.HTTPError as error:
            raise _TransientError(type(error).__name__) from error
        return bytes(data)


def _check_status(response: httpx.Response) -> None:
    if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
        raise _TransientError(f"HTTP {response.status_code}")
    if response.status_code != httpx.codes.OK:
        raise DriveError(f"HTTP {response.status_code}")
