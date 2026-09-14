from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime
from time import perf_counter
from pathlib import Path
import logging

from stevie_explorer.capabilities.state import CapabilityStateRepository

from stevie_explorer.capabilities.probes import PROBE_REGISTRY
from stevie_explorer.capabilities.probes.samsung import CAPABILITY_CATALOGUE
from stevie_explorer.capabilities.registry import CapabilityCatalogue
from stevie_explorer.devices import Device, DeviceRegistry
from stevie_explorer.targets import Target, TargetRegistry

from stevie_explorer.capabilities.models import (
    CapabilityProbe,
    CapabilityResult,
    CapabilityStatus,
    ProbeCapture,
    ProbeMode,
    ProbeSafety,
)
from stevie_explorer.capture import CaptureService
from stevie_explorer.identifiers import (
    MessageDirection,
    ServiceName,
    TelemetryMessage,
)
from stevie_explorer.kernel import BaseService, ExplorerKernel
from stevie_explorer.sessions import SessionManager


class CapabilityProbeService(BaseService):
    name = ServiceName.CAPABILITY_PROBE

    def __init__(self, kernel: ExplorerKernel, catalogue: CapabilityCatalogue | None = None, *, state_path: Path | None = None) -> None:
        self.kernel = kernel
        self._state_path = state_path
        self._state = None
        self._command_locks = defaultdict(asyncio.Lock)
        self.catalogue = catalogue if catalogue is not None else CAPABILITY_CATALOGUE
        self._results: dict[
            str,
            dict[str, CapabilityResult]
        ] = defaultdict(dict)

    def get_results(self, target_id: str) -> tuple[CapabilityResult, ...]:
        results = self._results.get(target_id, {})
        return tuple(results[probe_id] for probe_id in sorted(results))

    def get_capability_result(self, target_id: str, capability_id: str) -> CapabilityResult:
        """Read a recorded result without running a probe or changing state."""
        try:
            return self._results.get(target_id, {})[capability_id]
        except KeyError as exc:
            raise KeyError(
                f"No recorded result for capability '{capability_id}' on target '{target_id}'"
            ) from exc

    @property
    def state(self) -> CapabilityStateRepository:
        if self._state is None:
            path = self._state_path
            if path is None:
                devices = self.kernel.get(ServiceName.DEVICE_REGISTRY)
                path = devices.path.with_name("capabilities.json")
            self._state = CapabilityStateRepository(path)
        return self._state

    async def start(self) -> None:
        await self.state.load()
    
    async def stop(self) -> None:
        pass

    async def probe(self, device: Device, target: Target) -> CapabilityResult:
        try:
            probe = PROBE_REGISTRY.get(target.name)
        except KeyError:
            probe = None
        return await self._run_target_probe(device, target, probe)

    async def explore(self, device: Device, target: Target) -> tuple[CapabilityResult, ...]:
        if str(target.device_id) != str(device.id):
            raise ValueError("Target does not belong to this device")
        probes = self.catalogue.get(target.name)
        results = []
        for probe in probes:
            # READ_ONLY is the existing passive/safe classification.
            if probe.safety != ProbeSafety.READ_ONLY:
                continue
            results.append(await self._run_target_probe(device, target, probe, capability=True))
        return tuple(results)

    async def explore_capability(
        self, device: Device, target: Target, capability_id: str,
    ) -> CapabilityResult:
        """Run exactly the explicitly requested capability, including active probes."""
        if str(target.device_id) != str(device.id):
            raise ValueError("Target does not belong to this device")
        probe = self.catalogue.get_capability(target.name, capability_id)
        if probe.commands:
            raise ValueError(
                f"Capability '{capability_id}' requires an explicit command; use its command test endpoint"
            )
        return await self._run_target_probe(device, target, probe, capability=True)

    async def test_capability_command(
        self, device: Device, target: Target, capability_id: str, command: str,
    ) -> CapabilityResult:
        if str(target.device_id) != str(device.id):
            raise ValueError("Target does not belong to this device")
        probe = self.catalogue.get_capability(target.name, capability_id)
        if command not in {item.name for item in probe.commands}:
            raise ValueError(f"Unknown command '{command}' for capability '{capability_id}'")
        return await self._run_target_probe(
            device, target, replace(probe, command=command), capability=True,
        )

    async def test_capability_commands(self, device, target, capability_id, commands):
        if str(target.device_id) != str(device.id):
            raise ValueError("Target does not belong to this device")
        probe = self.catalogue.get_capability(target.name, capability_id)
        if probe.batch_runner is None:
            raise ValueError("Capability does not support batch commands")
        invalid = [key for key in commands if key not in {item.name for item in probe.commands}]
        if invalid:
            raise ValueError(f"Unknown commands for '{capability_id}': {', '.join(invalid)}")
        if not commands or len(set(commands)) != len(commands):
            raise ValueError("Commands must be nonempty and unique")
        async with self._command_locks[target.target_id]:
            # Load state before executing any physical command.
            await self.state.load()
            started = perf_counter()
            await self._emit(TelemetryMessage.CAPABILITY_PROBE_STARTED,
                             target_id=target.target_id, probe_id=capability_id, command_count=len(commands))
            devices = self.kernel.get(ServiceName.DEVICE_REGISTRY)
            device = devices.get(device.id)

            async def save_authentication(authentication):
                await devices.update_authentication(device.id, authentication)

            results = await probe.batch_runner(device, target, probe, save_authentication, commands)
            status = (CapabilityStatus.SUPPORTED if any(r.status == CapabilityStatus.SUPPORTED for r in results)
                      else CapabilityStatus.ERROR if any(r.status == CapabilityStatus.ERROR for r in results)
                      else CapabilityStatus.UNSUPPORTED)
            aggregate = CapabilityResult(
                target_id=target.target_id, probe_id=capability_id, probe_name=probe.name,
                status=status, duration_ms=(perf_counter() - started) * 1000,
                error=None if status == CapabilityStatus.SUPPORTED else "No requested command succeeded",
            )
            self._results[target.target_id][capability_id] = aggregate
            try:
                await self.state.save_commands(str(device.id), target.target_id, capability_id, results)
            except OSError:
                raise RuntimeError("Commands were tested but discovery state could not be saved; do not automatically retry") from None
            logging.getLogger(__name__).info("Command discovery results persisted for target %s", target.target_id)
            await self._emit(TelemetryMessage.CAPABILITY_PROBE_COMPLETED,
                             target_id=target.target_id, probe_id=capability_id, status=status.value,
                             command_count=len(results))
            return results

    async def _run_target_probe(
        self, device: Device, target: Target, probe: CapabilityProbe | None,
        *, capability: bool = False,
    ) -> CapabilityResult:
        started = perf_counter()
        await self._emit(
            TelemetryMessage.CAPABILITY_PROBE_STARTED,
            target_id=target.target_id, probe_id=probe.probe_id if probe else target.name,
        )
        try:
            if str(target.device_id) != str(device.id):
                raise ValueError("Target does not belong to this device")
            if probe is None or probe.runner is None or (not capability and probe.probe_id != target.name):
                raise ValueError(f"No probe implementation registered for target '{target.name}'")
            devices: DeviceRegistry = self.kernel.get(ServiceName.DEVICE_REGISTRY)
            device = devices.get(device.id)

            async def save_authentication(authentication):
                await devices.update_authentication(device.id, authentication)

            # Strategy waits have separate budgets; retain a bounded setup budget.
            execution_timeout = probe.timeout + sum(strategy.timeout for strategy in probe.strategies)
            async with asyncio.timeout(execution_timeout):
                result = await probe.runner(device, target, probe, save_authentication)
        except Exception as exc:
            status = CapabilityStatus.ERROR
            if isinstance(exc, PermissionError):
                status = CapabilityStatus.REJECTED
            elif (isinstance(exc, TimeoutError) and not (probe and (probe.strategies or probe.command))) or (
                not capability and isinstance(exc, (ConnectionError, OSError))
            ):
                status = CapabilityStatus.NO_RESPONSE
            result = CapabilityResult(
                target_id=target.target_id, probe_id=probe.probe_id if probe else target.name,
                probe_name=probe.name if probe else target.display_name,
                status=status, duration_ms=0,
                error=("Capability probe failed" if capability and status == CapabilityStatus.ERROR
                       else str(exc) or "Target did not respond before the probe timeout"),
            )
        result = replace(result, duration_ms=(perf_counter() - started) * 1000, timestamp=datetime.now(UTC))
        self._results[target.target_id][result.probe_id] = result
        await self._emit(
            TelemetryMessage.CAPABILITY_PROBE_FAILED if result.status == CapabilityStatus.ERROR
            else TelemetryMessage.CAPABILITY_PROBE_COMPLETED,
            target_id=target.target_id, probe_id=result.probe_id,
            status=result.status.value, duration_ms=result.duration_ms, error=result.error,
        )
        return result

    async def run(
        self,
        target_id: str,
        probe: CapabilityProbe,
    ) -> CapabilityResult:
        if probe.runner is not None:
            targets: TargetRegistry = self.kernel.get(ServiceName.TARGET_REGISTRY)
            devices: DeviceRegistry = self.kernel.get(ServiceName.DEVICE_REGISTRY)
            target = targets.get(target_id)
            return await self._run_target_probe(devices.get(target.device_id), target, probe)

        session_manager: SessionManager = self.kernel.get(
            ServiceName.SESSION_MANAGER
        )

        capture_service: CaptureService = self.kernel.get(
            ServiceName.CAPTURE
        )

        started = perf_counter()

        await self._emit(
            TelemetryMessage.CAPABILITY_PROBE_STARTED,
            target_id=target_id,
            probe_id=probe.probe_id,
            safety=probe.safety.value,
        )
        session = None

        try:
            session = await session_manager.create(target_id)
            await session_manager.connect(
                session.session_id
            )

            # Start recording immediately BEFORE sending
            # the actual probe request.
            capture = capture_service.start_capture(
                session.session_id
            )

            await session_manager.send(
                session_id=session.session_id,
                payload_type=probe.payload_type,
                payload=probe.payload,
            )

            if probe.mode != ProbeMode.SEND_ONLY:
                await asyncio.sleep(probe.timeout)

            capture = capture_service.stop_capture(
                capture.capture_id
            )

            inbound_messages = [
                message
                for message in capture.messages
                if message.direction
                == MessageDirection.INBOUND
            ]

            captures = tuple(
                ProbeCapture(
                    payload_type=message.payload_type,
                    payload=message.payload,
                    timestamp=message.timestamp,
                )
                for message in inbound_messages
            )

            status = CapabilityStatus.SUPPORTED
            response = None
            matched_event = None

            if probe.mode == ProbeMode.SEND_ONLY:
                status = CapabilityStatus.SUPPORTED

            elif probe.mode == ProbeMode.EXPECT_RESPONSE:
                if inbound_messages:
                    status = CapabilityStatus.SUPPORTED
                    response = inbound_messages[0].payload
                else:
                    status = CapabilityStatus.NO_RESPONSE

            elif probe.mode == ProbeMode.EXPECT_MATCH:
                matched_message = self._find_match(
                    inbound_messages,
                    probe.expected_event,
                )

                if matched_message is not None:
                    status = CapabilityStatus.SUPPORTED
                    response = matched_message.payload
                    matched_event = probe.expected_event

                elif inbound_messages:
                    status = CapabilityStatus.NO_MATCH

                else:
                    status = CapabilityStatus.NO_RESPONSE

            duration_ms = (
                perf_counter() - started
            ) * 1000

            result = CapabilityResult(
                target_id=target_id,
                probe_id=probe.probe_id,
                probe_name=probe.name,
                status=status,
                duration_ms=duration_ms,
                response=response,
                error=None,
                matched_event=matched_event,
                captures=captures,
            )

            self._results[target_id][probe.probe_id] = result

            await self._emit(
                TelemetryMessage.CAPABILITY_PROBE_COMPLETED,
                target_id=target_id,
                probe_id=probe.probe_id,
                status=result.status.value,
                duration_ms=result.duration_ms,
            )

            return result

        except Exception as exc:
            duration_ms = (
                perf_counter() - started
            ) * 1000

            result = CapabilityResult(
                target_id=target_id,
                probe_id=probe.probe_id,
                probe_name=probe.name,
                status=CapabilityStatus.ERROR,
                duration_ms=duration_ms,
                response=None,
                error=str(exc),
                matched_event=None,
                captures=(),
            )

            self._results[target_id][probe.probe_id] = result

            await self._emit(
                TelemetryMessage.CAPABILITY_PROBE_FAILED,
                target_id=target_id,
                probe_id=probe.probe_id,
                error=str(exc),
                duration_ms=result.duration_ms,
            )

            return result

        finally:
            if session is not None:
                try:
                    await session_manager.disconnect(session.session_id)
                except Exception:
                    pass

    async def _emit(self, message: TelemetryMessage, **context: object) -> None:
        try:
            telemetry = self.kernel.get(ServiceName.TELEMETRY)
        except KeyError:
            return
        await telemetry.emit(message, source=self.name, **context)

    @staticmethod
    def _find_match(
        messages,
        expected_event: str | None,
    ):
        if expected_event is None:
            return None

        for message in messages:
            payload = message.payload

            if not isinstance(payload, dict):
                continue

            if payload.get("event") == expected_event:
                return message

        return None
