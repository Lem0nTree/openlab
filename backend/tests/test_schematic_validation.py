from unittest.mock import MagicMock

from openlab.models import Pin, Project
from openlab.schematics import WireEndpoint, WireNet, WiringProposal, validate_wiring


def _pin(
    pin_id: str,
    thing_id: str,
    name: str,
    electrical_type: str,
    *alternate_functions: str,
) -> Pin:
    return Pin(
        id=pin_id,
        thing_id=thing_id,
        name=name,
        role=name.lower(),
        number=None,
        electrical_type=electrical_type,
        alternate_functions=list(alternate_functions),
        restrictions=None,
        details={},
        source_ref="test:reviewed",
        verification_state="accepted",
    )


def test_unused_i2c_pins_do_not_block_non_i2c_wiring() -> None:
    pins = [
        _pin("controller-5v", "controller", "5V", "power_out"),
        _pin("controller-3v3", "controller", "3V3", "power_out"),
        _pin("controller-gnd", "controller", "GND", "ground"),
        _pin("controller-adc", "controller", "GPIO34", "input", "ANALOG"),
        _pin("controller-data", "controller", "GPIO5", "output", "DATA"),
        _pin("controller-sda", "controller", "GPIO21", "open_drain", "SDA"),
        _pin("controller-scl", "controller", "GPIO22", "open_drain", "SCL"),
        _pin("sensor-vcc", "sensor", "VCC", "power_in"),
        _pin("sensor-gnd", "sensor", "GND", "ground"),
        _pin("sensor-out", "sensor", "AOUT", "output", "ANALOG"),
        _pin("indicator-5v", "indicator", "5V", "power_in"),
        _pin("indicator-gnd", "indicator", "GND", "ground"),
        _pin("indicator-data", "indicator", "DIN", "input", "DATA"),
    ]
    pins_by_id = {pin.id: pin for pin in pins}
    components = [
        {"role_key": "controller", "thing_id": "controller", "pins": []},
        {"role_key": "sensor", "thing_id": "sensor", "pins": []},
        {"role_key": "indicator", "thing_id": "indicator", "pins": []},
    ]
    for component in components:
        component["pins"] = [
            {"id": pin.id} for pin in pins if pin.thing_id == component["thing_id"]
        ]
    proposal = WiringProposal(
        summary="Plant monitor wiring",
        nets=[
            WireNet(
                name="5V_POWER",
                endpoints=[
                    WireEndpoint(role_key="controller", pin_id="controller-5v"),
                    WireEndpoint(role_key="indicator", pin_id="indicator-5v"),
                ],
            ),
            WireNet(
                name="3V3_POWER",
                endpoints=[
                    WireEndpoint(role_key="controller", pin_id="controller-3v3"),
                    WireEndpoint(role_key="sensor", pin_id="sensor-vcc"),
                ],
            ),
            WireNet(
                name="GND",
                endpoints=[
                    WireEndpoint(role_key="controller", pin_id="controller-gnd"),
                    WireEndpoint(role_key="sensor", pin_id="sensor-gnd"),
                    WireEndpoint(role_key="indicator", pin_id="indicator-gnd"),
                ],
            ),
            WireNet(
                name="MOISTURE_ANALOG",
                endpoints=[
                    WireEndpoint(role_key="sensor", pin_id="sensor-out"),
                    WireEndpoint(role_key="controller", pin_id="controller-adc"),
                ],
            ),
            WireNet(
                name="LED_DATA",
                endpoints=[
                    WireEndpoint(role_key="controller", pin_id="controller-data"),
                    WireEndpoint(role_key="indicator", pin_id="indicator-data"),
                ],
            ),
        ],
    )
    db = MagicMock()
    project = Project(id="project", lab_id="lab", name="Plant monitor", design_json={})

    result = validate_wiring(db, project, proposal, components, pins_by_id)

    assert result["status"] == "valid"
    assert result["errors"] == []
    db.scalars.assert_not_called()
