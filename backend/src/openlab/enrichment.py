"""Bounded technical enrichment from reviewed local knowledge sources."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Job, Pin, Thing
from .providers import ProviderError


@dataclass(frozen=True)
class CuratedPin:
    name: str
    role: str
    electrical_type: str
    alternate_functions: tuple[str, ...] = ()
    restrictions: str | None = None
    details: dict[str, object] = field(default_factory=dict)


CATALOG_VERSION = 1
CATALOG_SOURCE = f"openlab:curated-common-modules:v{CATALOG_VERSION}"


CURATED_PINOUTS: dict[str, tuple[CuratedPin, ...]] = {
    "esp32-devkit-v1": (
        CuratedPin("3V3", "3.3 V supply", "power_out", details={"voltage": 3.3}),
        CuratedPin(
            "5V",
            "USB/VIN 5 V rail",
            "power_out",
            restrictions="Treat as an output only while the board is USB powered; otherwise provide a regulated 5 V source.",
            details={"voltage": 5.0},
        ),
        CuratedPin("GND", "ground", "ground"),
        CuratedPin(
            "GPIO34",
            "analog input",
            "input",
            ("ADC", "ANALOG", "ADC1_CH6"),
            "Input-only GPIO; do not use it to drive a load.",
        ),
        CuratedPin(
            "GPIO5",
            "digital output",
            "output",
            ("DATA", "SPI_CS"),
            "Confirm boot behavior before attaching a circuit that can hold this pin high or low.",
        ),
        CuratedPin("GPIO21", "I2C data", "open_drain", ("SDA",)),
        CuratedPin("GPIO22", "I2C clock", "open_drain", ("SCL",)),
    ),
    "soil-capacitive": (
        CuratedPin(
            "VCC",
            "sensor supply",
            "power_in",
            ("3V3",),
            "Verify the voltage printed on the specific module before applying power.",
            {"voltage": 3.3},
        ),
        CuratedPin("GND", "ground", "ground"),
        CuratedPin(
            "AOUT",
            "analog moisture output",
            "output",
            ("ADC", "ANALOG"),
            "Connect only to an ADC-capable controller input.",
        ),
    ),
    "ws2812-ring8": (
        CuratedPin("5V", "LED supply", "power_in", details={"voltage": 5.0}),
        CuratedPin("GND", "ground", "ground"),
        CuratedPin("DIN", "pixel data input", "input", ("DATA",)),
        CuratedPin("DOUT", "pixel data output", "output", ("DATA_OUT",)),
    ),
    "ws2812-ring16": (
        CuratedPin("5V", "LED supply", "power_in", details={"voltage": 5.0}),
        CuratedPin("GND", "ground", "ground"),
        CuratedPin("DIN", "pixel data input", "input", ("DATA",)),
        CuratedPin("DOUT", "pixel data output", "output", ("DATA_OUT",)),
    ),
}

CURATED_NAMES = {
    "esp32 devkit v1": "esp32-devkit-v1",
    "capacitive soil moisture sensor": "soil-capacitive",
    "ws2812b 8 led ring": "ws2812-ring8",
    "ws2812b 16 led ring": "ws2812-ring16",
}


def _normalized(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def curated_pinout_key(thing: Thing) -> str | None:
    seed_key = thing.metadata_json.get("demo_seed_key")
    if isinstance(seed_key, str) and seed_key in CURATED_PINOUTS:
        return seed_key
    return CURATED_NAMES.get(_normalized(thing.name))


def curated_pinout_for(thing: Thing) -> tuple[CuratedPin, ...] | None:
    key = curated_pinout_key(thing)
    return CURATED_PINOUTS.get(key) if key else None


def enrich_thing(db: Session, lab_id: str, thing_id: str) -> dict[str, object]:
    """Add only reviewed local pin data; never ask a model to guess a pinout."""
    thing = db.scalar(select(Thing).where(Thing.id == thing_id, Thing.lab_id == lab_id))
    if not thing:
        raise ProviderError("Thing for technical enrichment is unavailable")
    existing = list(db.scalars(select(Pin).where(Pin.thing_id == thing.id)).all())
    if existing:
        return {
            "thing_id": thing.id,
            "status": "already_enriched",
            "pin_count": len(existing),
            "source_ref": existing[0].source_ref,
        }
    curated = curated_pinout_for(thing)
    key = curated_pinout_key(thing)
    if not curated or not key:
        return {
            "thing_id": thing.id,
            "status": "needs_source",
            "pin_count": 0,
            "reason": "No reviewed pinout source is available for this exact module.",
        }
    source_ref = f"{CATALOG_SOURCE}:{key}"
    pins = [
        Pin(
            thing_id=thing.id,
            name=value.name,
            number=value.name,
            role=value.role,
            electrical_type=value.electrical_type,
            alternate_functions=list(value.alternate_functions),
            restrictions=value.restrictions,
            details=value.details,
            source_ref=source_ref,
            verification_state="accepted",
        )
        for value in curated
    ]
    db.add_all(pins)
    thing.revision += 1
    return {
        "thing_id": thing.id,
        "status": "enriched",
        "pin_count": len(pins),
        "source_ref": source_ref,
    }


def queue_thing_enrichment(
    db: Session, lab_id: str, thing_id: str, project_id: str | None = None
) -> Job:
    pending = db.scalars(
        select(Job).where(
            Job.lab_id == lab_id,
            Job.kind == "thing.enrich",
            Job.status.in_(["queued", "running"]),
        )
    ).all()
    for job in pending:
        if str(job.payload.get("thing_id", "")) == thing_id:
            if project_id and not job.payload.get("project_id"):
                job.payload = {**job.payload, "project_id": project_id}
            return job
    payload: dict[str, object] = {"thing_id": thing_id}
    if project_id:
        payload["project_id"] = project_id
    job = Job(lab_id=lab_id, kind="thing.enrich", payload=payload)
    db.add(job)
    db.flush()
    return job
