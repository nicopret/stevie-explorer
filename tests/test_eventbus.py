import pytest

from stevie_explorer.eventbus import EventBus
from stevie_explorer.events import ExplorerEvent

@pytest.mark.asyncio
async def test_eventbus_delivers_matching_event() -> None:
    bus = EventBus()
    received: list[ExplorerEvent] = []

    async def handler(event: ExplorerEvent) -> None:
        received.append(event)
    
    bus.subscribe("device.*", handler)

    event = ExplorerEvent(
        topic="device.connected",
        source="test",
        payload={"connected": True}
    )

    await bus.publish_sync(event)

    assert received == [event]
