from __future__ import annotations

import logging
from typing import Any

import structlog

from stevie_explorer.eventbus import EventBus
from stevie_explorer.events import ExplorerEvent
from stevie_explorer.identifiers import (
    ServiceName,
    TelemetryLevel,
    TelemetryMessage,
    Topic
)
from stevie_explorer.kernel import BaseService

class TelemetryService(BaseService):
    name = ServiceName.TELEMETRY

    def __init__(self, eventbus: EventBus) -> None:
        self.eventbus = eventbus
        self.log  = structlog.get_logger()

    async def emit(self, message: TelemetryMessage, source: str | ServiceName, **context: Any) -> None:
        definition = message.value

        self._write_local(
            message = message,
            source = source,
            context = context
        )

        await self.eventbus.publish(
            ExplorerEvent(
                topic=Topic.SYSTEM_TELEMETRY_CREATED,
                source=str(source),
                payload={
                    "key": definition.key,
                    "grafana_key": definition.grafana_key,
                    "level": definition.level,
                    "category": definition.category,
                    "description": definition.description,
                    "source": str(source),
                    "context": context
                }
            )
        )

    async def start(self) -> None:
        self._configure_structlog()

    async def stop(self) -> None:
        pass

    @staticmethod
    def _configure_structlog() -> None:
        logging.basicConfig(
            format="%(message)s",
            level=logging.DEBUG
        )

        structlog.configure(
            processors=[
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.add_log_level,
                structlog.processors.JSONRenderer()
            ],
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True
        )

    def _log_method(self, level: TelemetryLevel):
        if level >= TelemetryLevel.CRITICAL:
            return self.log.critical
        
        if level >= TelemetryLevel.ERROR:
            return self.log.error
        
        if level >= TelemetryLevel.WARNING:
            return self.log.warning
        
        if level >= TelemetryLevel.INFO:
            return self.log.info
        
        return self.log.debug

    def _write_local(self, message: TelemetryMessage, source: str | ServiceName, context: dict[str, Any]) -> None:
        definition = message.value
        log_method=self._log_method(definition.level)

        log_method(
            definition.key,
            telemetry_key=definition.key,
            grafana_key=definition.grafana_key,
            telemetry_level=definition.level.name,
            category=definition.category.value,
            source=str(source),
            description=definition.description,
            **context
        )
