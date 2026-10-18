"""Синхронізація places.yaml → таблиці places / place_parts на старті (D-21)."""

import logging

from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from forest_bot.models import PlacePart, PlaceRow
from forest_bot.places import PlaceGraph

logger = logging.getLogger(__name__)


async def sync_places(session: AsyncSession, graph: PlaceGraph) -> None:
    """Upsert місць і зв'язків; зниклі з файлу — неактивні, документи з ними лишаються."""
    rows = [
        {"id": p.id, "kind": p.kind, "name": p.name, "sort_order": i, "is_active": True}
        for i, p in enumerate(graph.places)
    ]
    stmt = insert(PlaceRow).values(rows)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[PlaceRow.id],
            set_={
                "kind": stmt.excluded.kind,
                "name": stmt.excluded.name,
                "sort_order": stmt.excluded.sort_order,
                "is_active": True,
            },
        )
    )
    ids = [p.id for p in graph.places]
    gone = await session.execute(
        update(PlaceRow)
        .where(PlaceRow.id.not_in(ids), PlaceRow.is_active)
        .values(is_active=False)
        .returning(PlaceRow.id)
    )
    if inactive := gone.scalars().all():
        logger.warning("Місць більше немає у файлі, позначено неактивними: %s", ", ".join(inactive))

    # Зв'язків мало (≈10) — простіше перезаписати, ніж шукати різницю
    await session.execute(delete(PlacePart))
    links = [{"mitten_id": p.id, "part_id": part} for p in graph.places for part in p.parts]
    if links:
        await session.execute(insert(PlacePart).values(links))
