"""Per-model feeder profiles.

Petlibro's feeders do not share one command set. An auger feeder dispenses a
quantity of kibble; a wet feeder rotates a plate and holds a door open for a
duration, and does not implement the auger commands at all - the firmware drops
them silently, exactly as it drops a command that does not exist.

Rather than branch on model inside a shared class, each model contributes a
*profile*: the commands it handles, the capabilities it exposes, and how it
feeds. PetlibroDevice composes one and delegates to it.

Profiles satisfy FeederProfile structurally (typing.Protocol) - there is no base
class and nothing inherits. Shared behaviour lives in `common.py` as plain
functions that profiles call.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

_LOGGER = logging.getLogger(__name__)

# Handlers are called as handler(device, payload) - the same shape the device's
# own handler map uses, so the two merge without adaptation.
Handler = Callable[[Any, dict], Awaitable[None]]


@runtime_checkable
class FeederProfile(Protocol):
    """What a model must provide. Implementations do not inherit this."""

    #: Product ids this profile claims, e.g. {"PLAF203", "PLAF203S"}.
    PRODUCT_IDS: frozenset[str]

    #: Capability constants from const.py, used to decide which entities exist.
    CAPABILITIES: frozenset[str]

    #: Human-readable model name for the HA device registry.
    MODEL_NAME: str

    #: Model-specific wire-key -> state-key mappings. Take precedence over
    #: the shared maps, since the same key can mean different things per
    #: model (planId/execStep/finished differ on auger vs plate feeders).
    FIELDS: dict[str, str]

    def handlers(self) -> dict[str, Handler]:
        """Command handlers this model adds to the shared set."""
        ...

    # Feeding is deliberately NOT part of this protocol. An auger feeder
    # dispenses a quantity; a wet feeder runs a plate/door sequence with no
    # quantity at all, and the two need different state as well as different
    # arguments. Each profile exposes its own verb - DryFeeder.dispense(),
    # WetFeeder.serve_plate() - and entity platforms select by capability.


def _registry() -> list[FeederProfile]:
    # Imported lazily to keep module import order simple and avoid a cycle
    # back through device.py.
    from .dry import DryFeeder
    from .wet import WetFeeder

    return [DryFeeder(), WetFeeder()]


def get_profile(product_id: str | None) -> FeederProfile:
    """Return the profile for a product id.

    Falls back to the auger profile for unknown models, matching the historical
    behaviour of this integration, and logs it so an unrecognised feeder is
    visible rather than silently mistreated.
    """
    pid = (product_id or "").upper()
    for profile in _registry():
        if pid in profile.PRODUCT_IDS:
            return profile

    from .dry import DryFeeder

    if pid:
        _LOGGER.info(
            "Unknown product id %s - using the auger profile. If this feeder "
            "does not respond to feed commands it likely needs its own profile.",
            pid,
        )
    return DryFeeder()


def known_product_ids() -> set[str]:
    """Every product id with a dedicated profile."""
    return {pid for p in _registry() for pid in p.PRODUCT_IDS}
