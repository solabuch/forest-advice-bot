"""Підписка на місце (D-26): /subscribe — місця з позначками, натискання вмикає чи вимикає;
під кожним сповіщенням — відписка однією кнопкою. Самі сповіщення — `notify.py`."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot import keyboards, texts
from forest_bot.keyboards import SubscribeAction
from forest_bot.places import PlaceGraph
from forest_bot.repo import subscriptions

Sessions = async_sessionmaker[AsyncSession]


async def on_subscribe(message: Message, sessions: Sessions, graph: PlaceGraph) -> None:
    if message.from_user is None:
        return
    async with sessions() as session:
        subscribed = await subscriptions.places_of(session, message.from_user.id)
    await message.answer(texts.SUBSCRIBE, reply_markup=keyboards.subscriptions(graph, subscribed))


async def on_button(
    query: CallbackQuery, callback_data: SubscribeAction, sessions: Sessions, graph: PlaceGraph
) -> None:
    place_id, user_id = callback_data.value, query.from_user.id
    if place_id not in graph:
        await query.answer()
        return
    citation = graph.get(place_id).citation
    async with sessions.begin() as session:
        if callback_data.action == "toggle":
            on = await subscriptions.toggle(session, user_id, place_id)
        else:
            await subscriptions.remove(session, user_id, place_id)
            on = False
        subscribed = await subscriptions.places_of(session, user_id)
    await query.answer((texts.SUBSCRIBED if on else texts.UNSUBSCRIBED).format(place=citation))
    if callback_data.action == "toggle" and isinstance(query.message, Message):
        await query.message.edit_reply_markup(
            reply_markup=keyboards.subscriptions(graph, subscribed)
        )


def build_router() -> Router:
    router = Router(name="subscriptions")
    router.message.register(on_subscribe, Command("subscribe"))
    router.callback_query.register(on_button, SubscribeAction.filter())
    return router
