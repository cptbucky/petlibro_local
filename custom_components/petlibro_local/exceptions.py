"""Errors that should reach the user rather than only the log.

A button press that logs an error and returns looks identical to one that
worked: the UI reports success and nothing happens. Raising instead lets the
entity surface the reason.

Defined here rather than in feeders/ so the protocol layer stays free of Home
Assistant imports - entities translate these into HomeAssistantError.
"""

from __future__ import annotations


class PetlibroError(Exception):
    """Base for errors worth showing the user."""


class NoPlanForPlate(PetlibroError):
    """Asked to serve a plate that no stored plan covers.

    A feed command carries a planId, not a plate - the device reads the plate
    from the referenced plan - so a plate with no plan cannot be served.
    """

    def __init__(self, plate: int, known_plates: list[int]) -> None:
        self.plate = plate
        self.known_plates = known_plates
        if known_plates:
            have = ", ".join(str(p) for p in sorted(known_plates))
            detail = f"Plans exist for plate(s) {have}."
        else:
            detail = "No feeding plans are configured."
        super().__init__(
            f"No feeding plan covers plate {plate}. {detail} "
            "Add a plan for this plate in the feeding schedule card first."
        )


class PlateNotHomed(PetlibroError):
    """The carousel has not completed homing.

    The firmware accepts a feed in this state and then does nothing, so this is
    raised rather than letting the press appear to succeed.
    """

    def __init__(self, zero_state: str | None) -> None:
        self.zero_state = zero_state
        super().__init__(
            f"The plate has not homed (zeroState={zero_state or 'unknown'}), so the "
            "feeder will ignore a feed command. Check for a jam and reseat the "
            "plate, then power-cycle the feeder."
        )
