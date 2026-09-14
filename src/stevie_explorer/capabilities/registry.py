from __future__ import annotations

from dataclasses import dataclass

from stevie_explorer.capabilities.models import CapabilityProbe, ProbeSafety


@dataclass(frozen=True, slots=True)
class ProbePack:
    pack_id: str
    name: str
    probe_ids: tuple[str, ...]


class ProbeRegistry:
    def __init__(
        self,
        probes: tuple[CapabilityProbe, ...],
        packs: tuple[ProbePack, ...],
    ) -> None:
        self._probes = {probe.probe_id: probe for probe in probes}
        self._packs = {pack.pack_id: pack for pack in packs}
        if len(self._probes) != len(probes):
            raise ValueError("Probe IDs must be unique")
        if len(self._packs) != len(packs):
            raise ValueError("Probe pack IDs must be unique")
        for pack in packs:
            for probe_id in pack.probe_ids:
                probe = self.get(probe_id)
                if probe.safety != ProbeSafety.READ_ONLY:
                    raise ValueError(
                        f"Probe pack {pack.pack_id} contains unsafe probe {probe_id}"
                    )

    def get(self, probe_id: str) -> CapabilityProbe:
        try:
            return self._probes[probe_id]
        except KeyError as exc:
            raise KeyError(f"Unknown probe: {probe_id}") from exc

    def get_pack(self, pack_id: str) -> ProbePack:
        try:
            return self._packs[pack_id]
        except KeyError as exc:
            raise KeyError(f"Unknown probe pack: {pack_id}") from exc



class UnknownCapabilityError(KeyError):
    """The explorer exists but has no definition for this capability."""


class CapabilityCatalogue:
    """Known tests by target name; registration never implies device support."""

    def __init__(self) -> None:
        self._targets: dict[str, tuple[CapabilityProbe, ...]] = {}

    def register(self, target_name: str, probes: tuple[CapabilityProbe, ...]) -> None:
        if target_name in self._targets:
            raise ValueError(f"Capability explorer already registered for '{target_name}'")
        if len({probe.probe_id for probe in probes}) != len(probes):
            raise ValueError("Capability IDs must be unique within a target")
        if any(probe.runner is None or not 0 < probe.timeout < float("inf") for probe in probes):
            raise ValueError("Capability probes require a runner and a finite positive timeout")
        for probe in probes:
            if probe.commands and probe.safety == ProbeSafety.READ_ONLY:
                raise ValueError("Command capabilities require explicit active execution")
            if len({command.name for command in probe.commands}) != len(probe.commands):
                raise ValueError("Command names must be unique within a capability")
            if len({strategy.strategy_id for strategy in probe.strategies}) != len(probe.strategies):
                raise ValueError("Strategy IDs must be unique within a capability")
            if any(not 0 < strategy.timeout < float("inf") for strategy in probe.strategies):
                raise ValueError("Strategy timeouts must be finite and positive")
        self._targets[target_name] = probes

    def get(self, target_name: str) -> tuple[CapabilityProbe, ...]:
        try:
            return self._targets[target_name]
        except KeyError as exc:
            raise KeyError(f"No capability explorer registered for target '{target_name}'") from exc

    def get_capability(self, target_name: str, capability_id: str) -> CapabilityProbe:
        for probe in self.get(target_name):
            if probe.probe_id == capability_id:
                return probe
        raise UnknownCapabilityError(
            f"Capability '{capability_id}' is not registered for target '{target_name}'"
        )
