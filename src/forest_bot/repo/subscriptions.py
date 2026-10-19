"""Підписки на місця (D-26)."""

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from forest_bot.models import Subscription
from forest_bot.places import PlaceGraph


async def places_of(session: AsyncSession, user_id: int) -> set[str]:
    return set(
        await session.scalars(select(Subscription.place_id).where(Subscription.user_id == user_id))
    )


async def toggle(session: AsyncSession, user_id: int, place_id: str) -> bool:
    """Підписує або відписує; True — підписка тепер є."""
    removed = await session.execute(
        delete(Subscription)
        .where(Subscription.user_id == user_id, Subscription.place_id == place_id)
        .returning(Subscription.place_id)
    )
    if removed.first() is not None:
        return False
    await session.execute(
        insert(Subscription).values(user_id=user_id, place_id=place_id).on_conflict_do_nothing()
    )
    return True


async def remove(session: AsyncSession, user_id: int, place_id: str) -> None:
    await session.execute(
        delete(Subscription).where(
            Subscription.user_id == user_id, Subscription.place_id == place_id
        )
    )


async def audience(session: AsyncSession, graph: PlaceGraph, place_id: str) -> dict[int, list[str]]:
    """Кому важливий документ місця `place_id`: підписникам місць, для яких він доречний.

    Порада лісу — усім; частини — їй і її рукавичкам; рукавички — лише їй (R-10, R-17,
    R-27). Місця кожного звіра — від конкретнішого.
    """
    concerned = [
        p.id for p in graph.places if any(a.place.id == place_id for a in graph.applicable(p.id))
    ]
    rows = await session.execute(
        select(Subscription.user_id, Subscription.place_id).where(
            Subscription.place_id.in_(concerned)
        )
    )
    found: dict[int, list[str]] = {}
    for user_id, subscribed in rows:
        found.setdefault(user_id, []).append(subscribed)
    for places in found.values():
        places.sort(key=lambda p: graph.get(p).level)
    return found
