"""Sourced pin wiring proposals with deterministic validation and KiCad export."""

from __future__ import annotations

import json
import math
import re
import subprocess
import tempfile
import uuid
from itertools import pairwise
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Job, Lab, Pin, Project, Requirement, Thing, ThingInterface
from .providers import ProviderError
from .services import active_provider
from .system_settings import effective_kicad_cli


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WireEndpoint(StrictModel):
    role_key: str = Field(min_length=1, max_length=120)
    pin_id: str = Field(min_length=1, max_length=36)


class WireNet(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    endpoints: list[WireEndpoint] = Field(min_length=2, max_length=50)


class WiringProposal(StrictModel):
    summary: str = Field(min_length=1, max_length=600)
    nets: list[WireNet] = Field(default_factory=list, max_length=100)
    required_support: list[str] = Field(default_factory=list, max_length=20)


def _pin_signals(pin: Pin) -> set[str]:
    values = [pin.name, pin.role, *pin.alternate_functions]
    normalized = {
        token for value in values for token in re.sub(r"[^A-Z0-9]+", " ", value.upper()).split()
    }
    aliases = {
        "GROUND": "GND",
        "VSS": "GND",
        "VDD": "VCC",
        "SDI": "MOSI",
        "SDO": "MISO",
        "SCK": "SCLK",
        "CLK": "SCLK",
    }
    return {aliases.get(token, token) for token in normalized}


def _component_data(
    db: Session, project: Project
) -> tuple[list[dict[str, object]], dict[str, Pin]]:
    requirements = list(
        db.scalars(
            select(Requirement).where(
                Requirement.project_id == project.id,
                Requirement.selected_thing_id.is_not(None),
            )
        ).all()
    )
    components: list[dict[str, object]] = []
    pins_by_id: dict[str, Pin] = {}
    for requirement in requirements:
        if not requirement.selected_thing_id or not requirement.role_key:
            continue
        thing = db.get(Thing, requirement.selected_thing_id)
        if not thing:
            continue
        pins = list(
            db.scalars(select(Pin).where(Pin.thing_id == requirement.selected_thing_id)).all()
        )
        for pin in pins:
            pins_by_id[pin.id] = pin
        components.append(
            {
                "role_key": requirement.role_key,
                "thing_id": thing.id,
                "name": thing.name,
                "quantity": str(requirement.quantity),
                "pins": [
                    {
                        "id": pin.id,
                        "name": pin.name,
                        "number": pin.number,
                        "role": pin.role,
                        "electrical_type": pin.electrical_type,
                        "alternate_functions": pin.alternate_functions,
                        "details": pin.details,
                        "verification_state": pin.verification_state,
                        "source_ref": pin.source_ref,
                    }
                    for pin in pins
                ],
            }
        )
    return components, pins_by_id


def _deterministic_wiring(
    components: list[dict[str, object]], pins_by_id: dict[str, Pin]
) -> WiringProposal:
    role_by_pin: dict[str, str] = {}
    for component in components:
        role_key = str(component["role_key"])
        pins_value = component.get("pins", [])
        if not isinstance(pins_value, list):
            continue
        for pin_data in pins_value:
            if isinstance(pin_data, dict):
                role_by_pin[str(pin_data["id"])] = role_key
    signals = [
        "GND",
        "SDA",
        "SCL",
        "MOSI",
        "MISO",
        "SCLK",
        "CS",
        "ANALOG",
        "DATA",
        "3V3",
        "5V",
        "VCC",
    ]
    nets: list[WireNet] = []
    used: set[str] = set()
    for signal in signals:
        endpoints = []
        component_roles: set[str] = set()
        for pin_id, pin in pins_by_id.items():
            role_key = role_by_pin[pin_id]
            if (
                pin_id not in used
                and signal in _pin_signals(pin)
                and role_key not in component_roles
            ):
                endpoints.append(WireEndpoint(role_key=role_key, pin_id=pin_id))
                component_roles.add(role_key)
        if len(endpoints) >= 2:
            nets.append(WireNet(name=signal, endpoints=endpoints))
            used.update(endpoint.pin_id for endpoint in endpoints)
    return WiringProposal(
        summary="Conservative wiring derived only from matching sourced pin roles.",
        nets=nets,
        required_support=[],
    )


def _ai_wiring(
    db: Session,
    project: Project,
    components: list[dict[str, object]],
    pins_by_id: dict[str, Pin],
    notes: str | None,
    repair: tuple[WiringProposal, list[dict[str, object]]] | None = None,
) -> WiringProposal:
    provider, _ = active_provider(db, project.lab_id)
    fallback = repair[0] if repair else _deterministic_wiring(components, pins_by_id)
    if not provider:
        return fallback
    repair_instruction = ""
    if repair:
        previous, findings = repair
        repair_instruction = (
            " This is a repair of an existing proposal. Preserve its safe connections unless an "
            "electrical finding requires a change. OpenLab fixes endpoint_off_grid and "
            "lib_symbol_issues in the KiCad exporter; do not change wiring for those types. "
            "Previous proposal and KiCad findings: "
            f"{json.dumps({'proposal': previous.model_dump(), 'findings': findings}, separators=(',', ':'), default=str)}."
        )
    prompt = (
        "Propose a conservative wiring plan using only the supplied role_key and pin id values. "
        "Return exactly {summary,nets,required_support}; each net has name and at least two endpoints, "
        "and each endpoint has role_key and pin_id. Never invent a pin, voltage, or technical fact. "
        "Do not connect a pin more than once. If sourced information is insufficient, leave the net "
        "out and state a generic missing supporting component or unresolved need. Components and pins: "
        f"{json.dumps(components, separators=(',', ':'), default=str)}. Notes: {notes or '[none]'}"
        f"{repair_instruction}"
    )
    try:
        return WiringProposal.model_validate(
            provider.generate_structured(
                prompt, "WiringProposal", WiringProposal.model_json_schema()
            )
        )
    except (ProviderError, ValidationError, ValueError) as first_error:
        try:
            return WiringProposal.model_validate(
                provider.generate_structured(
                    f"{prompt}\nPrevious output was invalid: {str(first_error)[:1200]}. Correct it.",
                    "WiringProposal",
                    WiringProposal.model_json_schema(),
                )
            )
        except (ProviderError, ValidationError, ValueError):
            return fallback


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def validate_wiring(
    db: Session,
    project: Project,
    proposal: WiringProposal,
    components: list[dict[str, object]],
    pins_by_id: dict[str, Pin],
) -> dict[str, object]:
    role_to_thing = {
        str(component["role_key"]): str(component["thing_id"]) for component in components
    }
    used: set[str] = set()
    errors: list[str] = []
    warnings: list[str] = []
    connected_roles: dict[str, set[str]] = {role_key: set() for role_key in role_to_thing}
    net_roles: dict[str, set[str]] = {}
    if not proposal.nets:
        errors.append("No supported electrical connections could be proposed")
    for net in proposal.nets:
        electrical_types: list[str] = []
        voltage_ranges: list[tuple[float, float]] = []
        for endpoint in net.endpoints:
            pin = pins_by_id.get(endpoint.pin_id)
            if not pin or endpoint.role_key not in role_to_thing:
                errors.append(f"{net.name}: endpoint references an unknown component or pin")
                continue
            if pin.thing_id != role_to_thing[endpoint.role_key]:
                errors.append(f"{net.name}: pin does not belong to {endpoint.role_key}")
                continue
            if endpoint.pin_id in used:
                errors.append(f"{pin.name}: pin is connected to more than one net")
            used.add(endpoint.pin_id)
            connected_roles[endpoint.role_key].add(endpoint.pin_id)
            net_roles.setdefault(net.name.upper(), set()).add(endpoint.role_key)
            electrical_types.append(pin.electrical_type)
            if pin.verification_state == "unverified":
                warnings.append(f"{endpoint.role_key}.{pin.name}: pin data is not yet accepted")
            minimum = _number(pin.details.get("voltage_min"))
            maximum = _number(pin.details.get("voltage_max"))
            nominal = _number(pin.details.get("voltage"))
            if nominal is not None:
                minimum = maximum = nominal
            if minimum is not None or maximum is not None:
                voltage_ranges.append(
                    (
                        minimum if minimum is not None else -math.inf,
                        maximum if maximum is not None else math.inf,
                    )
                )
        driving = sum(value in {"output", "power_out"} for value in electrical_types)
        if driving > 1:
            errors.append(f"{net.name}: multiple output drivers are connected")
        if net.name.upper() in {"VCC", "3V3", "5V"} and "power_out" not in electrical_types:
            warnings.append(f"{net.name}: no recorded power source drives this net")
        if voltage_ranges:
            common_min = max(value[0] for value in voltage_ranges)
            common_max = min(value[1] for value in voltage_ranges)
            if common_min > common_max:
                errors.append(f"{net.name}: recorded voltage ranges do not overlap")

    for component in components:
        role_key = str(component["role_key"])
        connected = connected_roles[role_key]
        pins_value = component.get("pins", [])
        component_pins = (
            [pins_by_id[str(value["id"])] for value in pins_value if isinstance(value, dict)]
            if isinstance(pins_value, list)
            else []
        )
        grounds = [pin for pin in component_pins if pin.electrical_type == "ground"]
        power_inputs = [pin for pin in component_pins if pin.electrical_type == "power_in"]
        if grounds and not any(pin.id in connected for pin in grounds):
            errors.append(f"{role_key}: no ground pin is connected")
        if power_inputs and not any(pin.id in connected for pin in power_inputs):
            errors.append(f"{role_key}: no power input pin is connected")

    connected_i2c_roles = {
        role_key
        for role_key, connected_pin_ids in connected_roles.items()
        if any(
            _pin_signals(pins_by_id[pin_id]).intersection({"SDA", "SCL"})
            for pin_id in connected_pin_ids
        )
    }
    connected_i2c_signals = {
        signal
        for pin_id in used
        for signal in _pin_signals(pins_by_id[pin_id])
        if signal in {"SDA", "SCL"}
    }
    addresses: dict[str, list[str]] = {}
    has_i2c = False
    pullups_present = False
    for role_key, thing_id in role_to_thing.items():
        # A selected controller can expose many optional interfaces. Only validate
        # I2C completeness when this wiring proposal actually uses its I2C pins.
        if role_key not in connected_i2c_roles:
            continue
        for interface in db.scalars(
            select(ThingInterface).where(
                ThingInterface.thing_id == thing_id, func.lower(ThingInterface.kind) == "i2c"
            )
        ).all():
            has_i2c = True
            role_pins = [pin for pin in pins_by_id.values() if pin.thing_id == thing_id]
            for signal in ("SDA", "SCL"):
                signal_pins = [pin for pin in role_pins if signal in _pin_signals(pin)]
                if signal_pins and not any(
                    pin.id in connected_roles.get(role_key, set()) for pin in signal_pins
                ):
                    errors.append(f"{role_key}: {signal} is not connected")
            address = interface.details.get("address")
            if address is not None:
                addresses.setdefault(str(address), []).append(role_key)
            pullups_present = pullups_present or interface.details.get("pullups_present") is True
    for address, roles in addresses.items():
        shared_bus_roles = net_roles.get("SDA", set()).intersection(roles)
        if len(shared_bus_roles) > 1:
            errors.append(
                f"I2C address {address} is shared by {', '.join(sorted(shared_bus_roles))}"
            )
    required_support = list(proposal.required_support)
    if has_i2c and {"SDA", "SCL"}.issubset(connected_i2c_signals) and not pullups_present:
        warning = "Confirm that the I2C bus has suitable pull-up resistors"
        warnings.append(warning)
        required_support.append("I2C pull-up resistors if not already present on a selected module")
    required_support = list(dict.fromkeys(required_support))
    status: Literal["valid", "needs_review", "blocked"]
    status = "blocked" if errors else "needs_review" if warnings else "valid"
    return {
        "status": status,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "required_support": required_support,
    }


def propose_schematic(
    db: Session,
    project_id: str,
    lab_id: str,
    notes: str | None = None,
    repair_job_id: str | None = None,
) -> dict[str, object]:
    project = db.scalar(select(Project).where(Project.id == project_id, Project.lab_id == lab_id))
    if not project:
        raise ProviderError("Project for schematic generation is unavailable")
    if not project.design_json.get("solution"):
        raise ProviderError("Accept a BUILD solution before generating a schematic")
    components, pins_by_id = _component_data(db, project)
    if not components:
        raise ProviderError("Accepted BUILD solution has no inventory components")
    missing_pinouts = [
        str(component["role_key"]) for component in components if not component["pins"]
    ]
    if missing_pinouts:
        return {
            "project_id": project.id,
            "project_revision": project.revision,
            "status": "blocked",
            "summary": "Pin data is required before wiring can be proposed.",
            "components": components,
            "nets": [],
            "validation": {
                "status": "blocked",
                "errors": [f"Missing pinout: {role_key}" for role_key in missing_pinouts],
                "warnings": [],
                "required_support": [],
            },
            "erc": {"status": "not_run", "reason": "internal_validation_blocked"},
        }
    repair: tuple[WiringProposal, list[dict[str, object]]] | None = None
    if repair_job_id:
        source_job = db.scalar(
            select(Job).where(
                Job.id == repair_job_id,
                Job.lab_id == lab_id,
                Job.kind == "project.schematic",
            )
        )
        if (
            not source_job
            or source_job.status != "completed"
            or not source_job.result
            or str(source_job.payload.get("project_id", "")) != project.id
        ):
            raise ProviderError(
                "The KiCad repair source is unavailable or does not match this build"
            )
        source_validation = source_job.result.get("validation")
        required_support = (
            source_validation.get("required_support", [])
            if isinstance(source_validation, dict)
            else []
        )
        try:
            previous = WiringProposal.model_validate(
                {
                    "summary": source_job.result.get("summary"),
                    "nets": source_job.result.get("nets", []),
                    "required_support": required_support,
                }
            )
        except ValidationError as exc:
            raise ProviderError("The previous wiring proposal cannot be repaired") from exc
        source_erc = source_job.result.get("erc")
        findings = (
            parse_kicad_erc_report(str(source_erc.get("report", "")))
            if isinstance(source_erc, dict)
            else []
        )
        repair = (previous, findings)
    proposal = _ai_wiring(db, project, components, pins_by_id, notes, repair)
    validation = validate_wiring(db, project, proposal, components, pins_by_id)
    result: dict[str, object] = {
        "project_id": project.id,
        "project_revision": project.revision,
        "status": validation["status"],
        "summary": proposal.summary,
        "components": components,
        "nets": [net.model_dump() for net in proposal.nets],
        "validation": validation,
        "erc": {
            "status": "not_run",
            "reason": "KiCad CLI is unavailable. Configure it under Settings → KiCad.",
        },
    }
    kicad_cli, _ = effective_kicad_cli(db.get(Lab, lab_id), get_settings())
    if validation["status"] != "blocked" and kicad_cli:
        result["erc"] = run_kicad_erc(result, kicad_cli)
    return result


def _escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _kicad_endpoint_rows(design: dict[str, object]) -> list[dict[str, object]]:
    pin_catalog: dict[str, dict[str, object]] = {}
    components = design.get("components", [])
    if isinstance(components, list):
        for component in components:
            if not isinstance(component, dict):
                continue
            role_key = str(component.get("role_key", "component"))
            pins = component.get("pins", [])
            if isinstance(pins, list):
                for pin in pins:
                    if isinstance(pin, dict) and pin.get("id"):
                        pin_catalog[str(pin["id"])] = {**pin, "role_key": role_key}

    endpoint_rows: list[dict[str, object]] = []
    nets = design.get("nets", [])
    if isinstance(nets, list):
        for net_index, net in enumerate(nets):
            if not isinstance(net, dict):
                continue
            endpoints = net.get("endpoints", [])
            if not isinstance(endpoints, list):
                continue
            for endpoint_index, endpoint in enumerate(endpoints):
                if not isinstance(endpoint, dict):
                    continue
                pin_id = str(endpoint.get("pin_id", ""))
                pin = pin_catalog.get(pin_id, {})
                endpoint_rows.append(
                    {
                        "net_index": net_index,
                        "endpoint_index": endpoint_index,
                        "net_name": str(net.get("name", f"NET_{net_index + 1}")),
                        "role_key": str(endpoint.get("role_key", pin.get("role_key", "component"))),
                        "pin_id": pin_id,
                        "pin_name": str(pin.get("name", pin_id)),
                        "pin_number": str(pin.get("number") or endpoint_index + 1),
                        "electrical_type": str(pin.get("electrical_type", "passive")),
                    }
                )
    return endpoint_rows


def _kicad_symbol_definition(
    endpoint: dict[str, object], index: int, *, library_qualified: bool, indent: str
) -> list[str]:
    electrical_types = {
        "power_in": "power_in",
        "power_out": "power_out",
        "input": "input",
        "output": "output",
        "bidirectional": "bidirectional",
        "open_drain": "open_collector",
        "passive": "passive",
        "no_connect": "no_connect",
        # Ground continuity is already checked deterministically. Treating every generated
        # ground endpoint as a KiCad power input creates a false "not driven" error unless
        # the documentation-only schematic also invents a PWR_FLAG source.
        "ground": "passive",
    }
    symbol_name = f"OLPin{index + 1}"
    library_id = f"OpenLab:{symbol_name}" if library_qualified else symbol_name
    pin_type = electrical_types.get(str(endpoint["electrical_type"]), "passive")
    value = f"{endpoint['role_key']}.{endpoint['pin_name']}"
    return [
        f'{indent}(symbol "{library_id}" (pin_names (offset 0.762)) (in_bom yes) (on_board no)',
        f'{indent}  (property "Reference" "P" (id 0) (at 0 6.858 0) (effects (font (size 1.27 1.27))))',
        f'{indent}  (property "Value" "{_escape(value)}" (id 1) (at 0 5.08 0) (effects (font (size 1.27 1.27))))',
        f'{indent}  (property "Footprint" "" (id 2) (at 5.08 0 0) (effects (font (size 1.27 1.27)) hide))',
        f'{indent}  (property "Datasheet" "" (id 3) (at 5.08 0 0) (effects (font (size 1.27 1.27)) hide))',
        f'{indent}  (symbol "{symbol_name}_0_1" (circle (center 0 3.302) (radius 0.762) (stroke (width 0)) (fill (type none))))',
        f'{indent}  (symbol "{symbol_name}_1_1" (pin {pin_type} line (at 0 0 90) (length 2.54)',
        f'{indent}    (name "{_escape(endpoint["pin_name"])}" (effects (font (size 1.27 1.27))))',
        f'{indent}    (number "{_escape(endpoint["pin_number"])}" (effects (font (size 1.27 1.27))))',
        f"{indent}  ))",
        f"{indent})",
    ]


def export_kicad_symbol_library(design: dict[str, object]) -> str:
    """Export the generated endpoint symbols for project-local ERC resolution."""
    lines = ["(kicad_symbol_lib (version 20210619) (generator openlab)"]
    for index, endpoint in enumerate(_kicad_endpoint_rows(design)):
        lines.extend(
            _kicad_symbol_definition(endpoint, index, library_qualified=False, indent="  ")
        )
    lines.append(")")
    return "\n".join(lines) + "\n"


def export_kicad_schematic(design: dict[str, object]) -> str:
    """Export actual KiCad nets using generic one-pin symbols for each sourced endpoint."""
    root_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"openlab:{design.get('project_id', 'project')}")
    role_names: dict[str, str] = {}
    components = design.get("components", [])
    if isinstance(components, list):
        for component in components:
            if isinstance(component, dict):
                role_key = str(component.get("role_key", "component"))
                role_names[role_key] = str(component.get("name", role_key))
    endpoint_rows = _kicad_endpoint_rows(design)

    lines = [
        "(kicad_sch (version 20210621) (generator openlab)",
        f"  (uuid {root_uuid})",
        '  (paper "A4")',
        "  (lib_symbols",
    ]

    for index, endpoint in enumerate(endpoint_rows):
        lines.extend(
            _kicad_symbol_definition(endpoint, index, library_qualified=True, indent="    ")
        )
    lines.append("  )")

    instance_rows: list[tuple[str, str, str, str]] = []
    by_net: dict[int, list[dict[str, object]]] = {}
    for endpoint in endpoint_rows:
        by_net.setdefault(int(str(endpoint["net_index"])), []).append(endpoint)
    for net_index, endpoints in by_net.items():
        # KiCad's default connection grid is 2.54 mm. Keeping every pin and wire endpoint
        # on that grid prevents exporter geometry from being reported as an ERC warning.
        y = round(35.56 + net_index * 17.78, 2)
        coordinates: list[tuple[float, float]] = []
        for endpoint_index, endpoint in enumerate(endpoints):
            overall_index = endpoint_rows.index(endpoint)
            x = round(30.48 + endpoint_index * 35.56, 2)
            coordinates.append((x, y))
            symbol_name = f"OLPin{overall_index + 1}"
            symbol_uuid = uuid.uuid5(root_uuid, f"symbol:{overall_index}:{endpoint['pin_id']}")
            pin_uuid = uuid.uuid5(symbol_uuid, "pin")
            reference = f"P{overall_index + 1}"
            value = f"{endpoint['role_key']}.{endpoint['pin_name']}"
            lines.extend(
                [
                    f'  (symbol (lib_id "OpenLab:{symbol_name}") (at {x} {y} 0) (unit 1)',
                    "    (in_bom yes) (on_board no)",
                    f"    (uuid {symbol_uuid})",
                    f'    (property "Reference" "{reference}" (id 0) (at {x + 2.54} {y - 3.1116} 0) (effects (font (size 1.27 1.27)) (justify left)))',
                    f'    (property "Value" "{_escape(value)}" (id 1) (at {x + 2.54} {y - 0.5716} 0) (effects (font (size 1.27 1.27)) (justify left)))',
                    f'    (property "Footprint" "" (id 2) (at {x + 5.08} {y} 0) (effects (font (size 1.27 1.27)) hide))',
                    f'    (property "Datasheet" "" (id 3) (at {x + 5.08} {y} 0) (effects (font (size 1.27 1.27)) hide))',
                    f'    (pin "{_escape(endpoint["pin_number"])}" (uuid {pin_uuid}))',
                    "  )",
                ]
            )
            instance_rows.append((str(symbol_uuid), reference, value, ""))
        if len(coordinates) >= 2:
            net_name = str(endpoints[0]["net_name"])
            for segment_index, (start, end) in enumerate(pairwise(coordinates)):
                points = f"(xy {start[0]} {start[1]}) (xy {end[0]} {end[1]})"
                wire_uuid = uuid.uuid5(root_uuid, f"wire:{net_index}:{net_name}:{segment_index}")
                lines.extend(
                    [
                        f"  (wire (pts {points})",
                        "    (stroke (width 0) (type solid) (color 0 0 0 0))",
                        f"    (uuid {wire_uuid})",
                        "  )",
                    ]
                )
            label_uuid = uuid.uuid5(root_uuid, f"label:{net_index}:{endpoint['net_name']}")
            first_x, first_y = coordinates[0]
            lines.extend(
                [
                    f'  (label "{_escape(endpoint["net_name"])}" (at {first_x} {first_y} 0)',
                    "    (effects (font (size 1.27 1.27)) (justify left bottom))",
                    f"    (uuid {label_uuid})",
                    "  )",
                ]
            )

    text_lines = ["OpenLab sourced wiring plan", str(design.get("summary", ""))]
    for role_key, name in role_names.items():
        text_lines.append(f"{role_key}: {name}")
    for net_index, endpoints in by_net.items():
        if endpoints:
            endpoint_names = [f"{value['role_key']}.{value['pin_id']}" for value in endpoints]
            text_lines.append(f"NET {endpoints[0]['net_name']}: {' -- '.join(endpoint_names)}")
    for index, value in enumerate(text_lines):
        text_uuid = uuid.uuid5(root_uuid, f"text:{index}:{value}")
        y = 15 + index * 4
        lines.extend(
            [
                f'  (text "{_escape(value)}"',
                f"    (at 20 {y} 0)",
                "    (effects (font (size 1.27 1.27)) (justify left bottom))",
                f"    (uuid {text_uuid})",
                "  )",
            ]
        )
    lines.extend(['  (sheet_instances (path "/" (page "1")))', "  (symbol_instances"])
    for instance_uuid, reference, value, footprint in instance_rows:
        lines.extend(
            [
                f'    (path "/{instance_uuid}"',
                f'      (reference "{reference}") (unit 1) (value "{_escape(value)}") (footprint "{footprint}")',
                "    )",
            ]
        )
    lines.extend(["  )", ")"])
    return "\n".join(lines) + "\n"


def parse_kicad_erc_report(report: str | None) -> list[dict[str, object]]:
    """Return stable, UI-safe findings from KiCad's versioned JSON report."""
    if not report:
        return []
    try:
        parsed: object = json.loads(report)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, dict):
        return []
    sheets = parsed.get("sheets")
    if not isinstance(sheets, list):
        return []
    findings: list[dict[str, object]] = []
    for sheet in sheets:
        if not isinstance(sheet, dict):
            continue
        violations = sheet.get("violations")
        if not isinstance(violations, list):
            continue
        for violation in violations:
            if not isinstance(violation, dict):
                continue
            items: list[dict[str, object]] = []
            violation_items = violation.get("items")
            if isinstance(violation_items, list):
                for item in violation_items:
                    if not isinstance(item, dict):
                        continue
                    safe_item: dict[str, object] = {
                        "description": str(item.get("description", "Affected schematic item"))[
                            :1000
                        ]
                    }
                    position = item.get("pos")
                    if isinstance(position, dict):
                        safe_item["position"] = {
                            "x": position.get("x"),
                            "y": position.get("y"),
                        }
                    items.append(safe_item)
            findings.append(
                {
                    "severity": str(violation.get("severity", "warning"))[:40],
                    "type": str(violation.get("type", "unknown"))[:120],
                    "description": str(violation.get("description", "KiCad ERC finding"))[:1000],
                    "items": items[:50],
                }
            )
            if len(findings) >= 500:
                return findings
    return findings


def kicad_erc_error_count(design: dict[str, object]) -> int:
    erc = design.get("erc")
    if not isinstance(erc, dict):
        return 0
    findings = erc.get("findings")
    if not isinstance(findings, list):
        findings = parse_kicad_erc_report(
            str(erc.get("report")) if erc.get("report") is not None else None
        )
    return sum(
        1
        for finding in findings
        if isinstance(finding, dict) and finding.get("severity") == "error"
    )


def run_kicad_erc(design: dict[str, object], cli: str | None = None) -> dict[str, object]:
    cli = cli or get_settings().kicad_cli
    if not cli:
        return {
            "status": "not_run",
            "reason": "KiCad CLI is unavailable. Configure it under Settings → KiCad.",
        }
    with tempfile.TemporaryDirectory(prefix="openlab-erc-") as temporary:
        schematic_path = Path(temporary) / "openlab.kicad_sch"
        project_path = Path(temporary) / "openlab.kicad_pro"
        symbol_library_path = Path(temporary) / "openlab.kicad_sym"
        symbol_table_path = Path(temporary) / "sym-lib-table"
        report_path = Path(temporary) / "erc.json"
        schematic_path.write_text(export_kicad_schematic(design), encoding="utf-8")
        project_path.write_text("{}\n", encoding="utf-8")
        symbol_library_path.write_text(export_kicad_symbol_library(design), encoding="utf-8")
        symbol_table_path.write_text(
            '(sym_lib_table (version 7) (lib (name "OpenLab")(type "KiCad")'
            '(uri "${KIPRJMOD}/openlab.kicad_sym")(options "")(descr "OpenLab generated symbols")))\n',
            encoding="utf-8",
        )
        try:
            completed = subprocess.run(
                [
                    cli,
                    "sch",
                    "erc",
                    "--format",
                    "json",
                    "--output",
                    str(report_path),
                    "--exit-code-violations",
                    str(schematic_path),
                ],
                capture_output=True,
                check=False,
                cwd=temporary,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"status": "failed", "reason": str(exc)[:1000]}
        report = report_path.read_text(encoding="utf-8")[:100_000] if report_path.exists() else None
        status = (
            "passed"
            if completed.returncode == 0
            else "violations"
            if report is not None
            else "failed"
        )
        findings = parse_kicad_erc_report(report)
        return {
            "status": status,
            "exit_code": completed.returncode,
            "report": report,
            "findings": findings,
            "counts": {
                "errors": sum(value["severity"] == "error" for value in findings),
                "warnings": sum(value["severity"] == "warning" for value in findings),
                "total": len(findings),
            },
            "stderr": completed.stderr[-4000:],
            "reason": completed.stderr.strip()[-1000:] if status == "failed" else None,
            "scope": "KiCad electrical nets plus OpenLab sourced-pin safety rules",
        }


def accept_schematic(
    project: Project, job_result: dict[str, object], job_id: str, expected_revision: int
) -> Project:
    if project.revision != expected_revision:
        raise ValueError("Project was updated elsewhere; reload and retry")
    if job_result.get("status") == "blocked":
        raise ValueError("Blocked schematic proposals cannot be accepted")
    erc = job_result.get("erc")
    if isinstance(erc, dict) and erc.get("status") == "failed":
        raise ValueError("KiCad ERC must complete successfully before acceptance")
    if kicad_erc_error_count(job_result):
        raise ValueError("Resolve KiCad ERC errors before accepting this schematic")
    design = dict(project.design_json)
    design["status"] = "schematic_accepted"
    design["schematic"] = {**job_result, "source_job_id": job_id}
    project.design_json = design
    project.revision += 1
    return project
