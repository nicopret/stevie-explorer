from __future__ import annotations

import asyncio

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from fnmatch import fnmatch

from stevie_explorer.events import ExplorerEvent
from stevie_explorer.identifiers import ServiceName
from stevie_explorer.kernel import BaseComponent

EventHandler = Callable[[ExplorerEvent], Awaitable[None]]

@dataclass(frozen=True, slots=True)
class Subscription:
    topic_pattern: str
    handler: EventHandler

class EventBus(BaseComponent):
    name = ServiceName.EVENTBUS

    def __init__(self) -> None:
        self._subscriptions: list[Subscription] = []
        self._tasks: set[asyncio.Task[None]] = set()
    
    def subscribe(
        self,
        topic_pattern: str,
        handler: EventHandler
    ) -> None:
        subscription = Subscription(
            topic_pattern=str(topic_pattern),
            handler=handler
        )

        if subscription not in self._subscriptions:
            self._subscriptions.append(subscription)
    
    def unsubscribe(
        self,
        topic_pattern: str,
        handler: EventHandler
    ) -> None:
        self._subscriptions = [
            subscription
            for subscription in self._subscriptions
            if not (
                subscription.topic_pattern == str(topic_pattern)
                and subscription.handler == handler
            )
        ]
    
    async def publish(self, event: ExplorerEvent) -> None:
        for handler in self._matching_handlers(event.topic):
            task = asyncio.create_task(
                self._run_handler(handler, event)
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
    
    async def publish_sync(self, event: ExplorerEvent) -> None:
        handlers = self._matching_handlers(event.topic)

        await asyncio.gather(
            *(self._run_handler(handler, event) for handler in handlers)
        )

    async def stop(self) -> None:
        if not self._tasks:
            return
        
        await asyncio.gather(
            *self._tasks,
            return_exception=True
        )

        self._tasks.clear()
    
    def _matching_handlers(
        self,
        topic: str
    ) -> list[EventHandler]:
        return [
            subscription.handler
            for subscription in self._subscriptions
            if self._topic_matches(
                subscription.topic_pattern,
                topic
            )
        ]
    
    @staticmethod
    def _topic_matches(
        pattern: str,
        topic: str
    ) -> bool:
        if pattern == "*":
            return True
        
        return fnmatch(topic, pattern)
    
    @staticmethod
    async def _run_handler(
        handler: EventHandler,
        event: ExplorerEvent
    ) -> None:
        await handler(event)
