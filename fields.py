"""GL form schema: field map, mandatory rules, xlsx cell map, completeness check.

Mirrors the Broadspire General Liability Reporting Form (Starr),
`general_liability_reporting_form_Starr.xlsx`. This is the single source of
truth used by: the online form (render order = tab order), the AI extractor
(JSON schema), the completeness check, and the Excel fill-back (cell map).
"""

# The four narrative fields scanned by the language guard.
NARRATIVE_FIELDS = [
    "accident_description",
    "injury_illness_description",
    "injured_person_doing",
    "general_remarks",
]

# type: text | date | time | phone | email | textarea | yn | ynu | choice | money
# required: True | False | "<condition name>" (see CONDITIONS below)
# cell: xlsx coordinate the value is written to on fill-back (None = web-only)
FIELDS = [
    # ---- Gate: answered first, decides which sections below apply -----------
    dict(key="type_of_loss", label="Was someone hurt, or was something damaged?", section="Start here",
         type="choice", required=True, choices=["injury", "property", "both"],
         choice_labels={"injury": "Someone was hurt", "property": "Something was damaged",
                        "both": "Both"},
         cell={"injury": "F40", "property": "H40", "both": "J40"},
         hint="Your answer decides which questions come next — the rest stay hidden."),

    # ---- Header -------------------------------------------------------------
    dict(key="emergency_claim", label="Does this need the adjuster urgently?", section="Reporting Person & Accident Date/Time",
         type="yn", required=False, cell={"yes": "H11", "no": "J11"},
         hint="Yes only for a death, someone admitted to hospital, or serious damage "
              "the adjuster needs to see right away. Otherwise No."),
    dict(key="reported_by_name", label="Reported by (person's name)", section="Reporting Person & Accident Date/Time",
         type="text", required=True, cell="D12"),
    dict(key="reported_by_title", label="Your job title (e.g. Front Desk Manager, GM)", section="Reporting Person & Accident Date/Time",
         type="text", required=True, cell="B13"),
    dict(key="reported_by_phone", label="Your direct phone", section="Reporting Person & Accident Date/Time",
         type="phone", required=False, cell="G13"),
    dict(key="reported_by_phone_ext", label="Your ext.", section="Reporting Person & Accident Date/Time",
         type="text", required=False, cell="J13"),
    # Fax boxes stay on the carrier form (and are read off uploads) but aren't
    # asked on screen: display=False renders them as hidden inputs.
    dict(key="reported_by_fax", label="Your fax number", section="Reporting Person & Accident Date/Time",
         type="phone", required=False, cell="C14", display=False),
    # It's where "changes requested" and "approved" emails go, so the online form
    # insists on it (form_required). It doesn't block approval, though: paper
    # forms that arrive by upload often have no email, and the GM gets copied.
    dict(key="reported_by_email", label="Your e-mail address", section="Reporting Person & Accident Date/Time",
         type="email", required=False, form_required=True, cell="G14",
         hint="We'll email you if the reviewer needs anything, and when it's approved."),
    dict(key="date_of_accident", label="Date of accident (MM/DD/YYYY)", section="Reporting Person & Accident Date/Time",
         type="date", required=True, cell="D15"),
    dict(key="time_of_accident", label="Time of accident (HH:MM AM/PM)", section="Reporting Person & Accident Date/Time",
         type="time", required=False, cell="I15"),

    # ---- A. Local business address -------------------------------------------
    dict(key="parent_co_name", label="Named insured (parent company)", section="A. Local Business Address",
         type="text", required=True, cell="C17",
         hint="Filled in by the hotel picker — leave it as it comes in."),
    dict(key="subsidiary_name", label="Ownership entity (subsidiary)", section="A. Local Business Address",
         type="text", required=False, cell="H17",
         hint="Filled in by the hotel picker — leave it as it comes in."),
    dict(key="business_address", label="Hotel street address", section="A. Local Business Address",
         type="text", required=True, cell="C18"),
    dict(key="business_city_state_zip", label="Hotel City, State, Zip", section="A. Local Business Address",
         type="text", required=True, cell="C19"),
    dict(key="business_county", label="Hotel county", section="A. Local Business Address",
         type="text", required=True, cell="I19"),
    dict(key="business_phone", label="Hotel main phone", section="A. Local Business Address",
         type="phone", required=True, cell="C20"),
    dict(key="business_phone_ext", label="Hotel ext.", section="A. Local Business Address",
         type="text", required=False, cell="F20"),
    dict(key="business_fax", label="Hotel fax number", section="A. Local Business Address",
         type="phone", required=False, cell="I20", display=False),
    dict(key="location_code", label="Location code (3-letter property code)",
         section="A. Local Business Address", type="text", required=True, cell="C21"),
    dict(key="policy_number", label="Policy number", section="A. Local Business Address",
         type="text", required=False, cell="H21",
         hint="Filled in from the hotel list. Leave blank if you don't know it."),

    # ---- B. Loss location -----------------------------------------------------
    dict(key="loss_location_name", label="Hotel name", section="B. Loss Location",
         type="text", required=True, cell="D23"),
    dict(key="on_insured_premises", label="Did this happen on hotel property?",
         section="B. Loss Location", type="yn", required=True,
         cell={"yes": "F24", "no": "H24"},
         hint="Yes includes the parking lot, pool, and sidewalks you maintain."),
    dict(key="offsite_address", label="Off-site street address where it happened",
         section="B. Loss Location", type="text", required="off_premises", cell="D25"),
    dict(key="offsite_city_state_zip", label="Off-site City, State, Zip", section="B. Loss Location",
         type="text", required="off_premises", cell="C26"),
    dict(key="offsite_county", label="Off-site county", section="B. Loss Location",
         type="text", required="off_premises", cell="I26"),

    # ---- C. Insured contact ---------------------------------------------------
    dict(key="reporter_is_contact", label="Should the adjuster contact you about this claim?",
         section="C. Insured Contact", type="yn", required=False,
         cell={"yes": "F28", "no": "H28"},
         hint="Answer No only if someone else at the property should take the "
              "adjuster's calls — then name them below."),
    dict(key="contact_person", label="Other contact — name", section="C. Insured Contact",
         type="text", required=False, cell="D29"),
    dict(key="contact_title", label="Other contact — job title", section="C. Insured Contact",
         type="text", required=False, cell="H29"),
    dict(key="contact_phone", label="Other contact — phone", section="C. Insured Contact",
         type="phone", required=False, cell="C30"),
    dict(key="contact_email", label="Other contact — e-mail", section="C. Insured Contact",
         type="email", required=False, cell="H30"),

    # ---- D. Loss information --------------------------------------------------
    dict(key="authority_contacted", label="Were police, fire or EMS called?",
         section="D. Loss Information", type="ynu", required=False,
         cell={"yes": "E32", "no": "G32", "unknown": "I32"}),
    dict(key="authority_name", label="Which agency responded?", section="D. Loss Information",
         type="text", required=False, cell="C33",
         hint="e.g. Memphis PD, Shelby County Fire, AMR ambulance."),
    dict(key="authority_report_number", label="Their report / run number",
         section="D. Loss Information", type="text", required=False, cell="I33",
         hint="Leave blank if they didn't give you one."),
    dict(key="accident_description", label="What happened?", section="D. Loss Information",
         type="textarea", required=True, cell="C34", narrative=True,
         placeholder="Date and time it happened · exactly where (room number, area) · "
                     "what the guest says happened · what staff actually saw · "
                     "first aid or assistance given · who was notified · photos/CCTV taken.",
         hint="The event itself only. Injury details go in Section E. Follow-up "
              "calls, doctor's appointments and anything that happened afterward "
              "go in Section K — General remarks."),
    dict(key="notification_date", label="Date the hotel first learned of this (MM/DD/YYYY)",
         section="D. Loss Information", type="date", required=True, cell="C39",
         hint="Not today's date unless you found out today. If the guest told the "
              "front desk on the 18th and called you on the 20th, this is the 18th."),

    # ---- E. Injured person ----------------------------------------------------
    dict(key="injured_name", label="Injured person — full name (First, MI, Last)",
         section="E. Injured Person", type="text", required="injury_loss", cell="C42"),
    # Optional: a guest who declines help and walks out often never gives an
    # address, and Marsh asks that missing detail not hold up a filing. Their
    # name still is required — the adjuster needs someone to contact.
    dict(key="injured_address", label="Injured person — home street address",
         section="E. Injured Person", type="text", required=False, cell="C43",
         hint="Their home address, not the hotel's. Leave blank if you don't have it."),
    dict(key="injured_city_state_zip", label="Injured person — City, State, Zip",
         section="E. Injured Person", type="text", required=False, cell="C44"),
    dict(key="injured_home_phone", label="Injured person — home phone", section="E. Injured Person",
         type="phone", required=False, cell="C45"),
    dict(key="injured_work_phone", label="Injured person — work phone", section="E. Injured Person",
         type="phone", required=False, cell="G45"),
    dict(key="injury_illness_description", label="What is the injury?",
         section="E. Injured Person", type="textarea", required="injury_loss",
         cell="D46", narrative=True,
         placeholder="e.g. Sprained left ankle. · Laceration to right forearm. · "
                     "Bruising to right hip and lower back.",
         hint="Body part and type of injury only — not the story of how it "
              "happened. If the guest reported it but no one saw it, write "
              "\"reported\" (e.g. \"Reported sprained left ankle\")."),
    dict(key="injured_person_doing", label="What were they doing at that moment?",
         section="E. Injured Person", type="textarea", required="injury_loss",
         cell="D47", narrative=True,
         placeholder="e.g. Stepping out of the bathtub. · Walking through the lobby "
                     "toward the elevators. · Carrying luggage down the exterior stairs.",
         hint="A few words about the physical activity only — not where, not why."),
    dict(key="injured_dob", label="Injured person — date of birth", section="E. Injured Person",
         type="date", required=False, cell="B48"),
    dict(key="injured_age", label="Injured person — age", section="E. Injured Person",
         type="text", required=False, cell="E48"),
    dict(key="injured_gender", label="Injured person — gender", section="E. Injured Person",
         type="text", required=False, cell="G48"),
    dict(key="removed_via_ambulance", label="Was the person taken away by ambulance?",
         section="E. Injured Person", type="ynu", required="injury_loss", cell="D49",
         hint="Choose Unknown if they left before you could find out."),
    dict(key="fatality", label="Did anyone die as a result?", section="E. Injured Person",
         type="yn", required="injury_loss", cell={"yes": "H49", "no": "J49"}),

    # ---- F. Medical facility ---------------------------------------------------
    dict(key="injured_taken_to", label="Where were they taken for treatment?",
         section="F. Medical Facility", type="choice", required=False,
         choices=["hospital_clinic", "physician"],
         cell={"hospital_clinic": "E51", "physician": "H51"},
         hint="Leave blank if they refused treatment or went nowhere."),
    dict(key="medical_facility_name", label="Hospital / clinic name", section="F. Medical Facility",
         type="text", required=False, cell="D52"),
    dict(key="physician_name", label="Physician name", section="F. Medical Facility",
         type="text", required=False, cell="D53"),
    dict(key="medical_address", label="Hospital / physician street address",
         section="F. Medical Facility", type="text", required=False, cell="C54"),
    dict(key="medical_city_state_zip", label="Hospital / physician City, State, Zip",
         section="F. Medical Facility", type="text", required=False, cell="C55"),
    dict(key="medical_phone", label="Hospital / physician phone", section="F. Medical Facility",
         type="phone", required=False, cell="I55"),

    # ---- G. Damaged property ----------------------------------------------------
    dict(key="damaged_property_vehicle", label="Was a vehicle damaged?",
         section="G. Damaged Property", type="yn", required="property_loss",
         cell={"yes": "E57", "no": "G57"},
         hint="Yes for any car, truck or motorcycle. No for luggage, a phone, "
              "clothing, or hotel property."),
    dict(key="vehicle_year", label="Vehicle year", section="G. Damaged Property",
         type="text", required="vehicle_damaged", cell="C58"),
    dict(key="vehicle_make", label="Vehicle make", section="G. Damaged Property",
         type="text", required="vehicle_damaged", cell="E58"),
    dict(key="vehicle_model", label="Vehicle model", section="G. Damaged Property",
         type="text", required="vehicle_damaged", cell="I58"),
    dict(key="vehicle_plate_state", label="Licence plate — state", section="G. Damaged Property",
         type="text", required=False, cell="D59"),
    dict(key="vehicle_plate_number", label="Licence plate — number", section="G. Damaged Property",
         type="text", required=False, cell="G59"),
    dict(key="property_description", label="What property was damaged?",
         section="G. Damaged Property", type="textarea",
         required="nonvehicle_property_damaged", cell="D60",
         placeholder="e.g. Guest's laptop, black Dell XPS 13.",
         hint="The item itself — describe the damage in the next box."),
    dict(key="damage_description", label="Describe the damage",
         section="G. Damaged Property", type="textarea", required=False, cell="E61",
         placeholder="e.g. Dent and paint transfer to the driver-side rear bumper.",
         hint="If more than one vehicle or item was damaged, list each on its own line."),
    dict(key="estimated_damage_amount", label="Estimated cost of the damage ($)",
         section="G. Damaged Property", type="money", required=False, cell="D62",
         hint="A rough estimate is fine. Leave blank if you have no idea."),
    dict(key="damage_when_to_view", label="When can the adjuster inspect it?",
         section="G. Damaged Property", type="text", required=False, cell="G62"),
    dict(key="damage_where_to_view", label="Where can the adjuster inspect it?",
         section="G. Damaged Property", type="text", required=False, cell="C63"),

    # ---- H. Owner of damaged property -------------------------------------------
    # Section H is optional throughout. Marsh's marked-up form does not flag any
    # of it as priority (rows 65-70 are unhighlighted, unlike the vehicle rows),
    # and their guidance is to file by end of shift with whatever is known
    # rather than hold a report for detail the adjuster can chase. Requiring an
    # owner name meant a burst pipe soaking a guest's suitcase couldn't be filed
    # until someone decided whether a suitcase is "personally or company owned".
    dict(key="property_owner_type", label="Who owns the damaged property?",
         section="H. Owner of Damaged Property", type="choice", required=False,
         choices=["personally", "company"],
         cell={"personally": "E65", "company": "H65"},
         hint="Optional. Personally = an individual owns it (a guest's own car "
              "or laptop). Company = a business owns it (a rental company, the "
              "hotel itself). Leave blank if you're not sure."),
    dict(key="company_owner_name", label="Owner — company name", section="H. Owner of Damaged Property",
         type="text", required=False, cell="C66"),
    dict(key="personal_owner_name", label="Owner — person's full name (First, MI, Last)",
         section="H. Owner of Damaged Property", type="text",
         required=False, cell="D67"),
    dict(key="owner_address", label="Owner — street address", section="H. Owner of Damaged Property",
         type="text", required=False, cell="C68",
         hint="The owner's address. If the owner is also the injured person, "
              "you can repeat what you entered in Section E."),
    dict(key="owner_city_state_zip", label="Owner — City, State, Zip",
         section="H. Owner of Damaged Property", type="text", required=False, cell="C69"),
    dict(key="owner_residence_phone", label="Owner — home phone", section="H. Owner of Damaged Property",
         type="phone", required=False, cell="C70"),
    dict(key="owner_work_phone", label="Owner — work phone", section="H. Owner of Damaged Property",
         type="phone", required=False, cell="G70"),

    # ---- I. Product information (if applicable) ----------------------------------
    dict(key="product_name", label="Product name", section="I. Product (If Applicable)",
         type="text", required=False, cell="C72"),
    dict(key="product_purchase_date", label="Purchase date", section="I. Product (If Applicable)",
         type="date", required=False, cell="I72"),
    dict(key="product_description", label="Product description (part or ingredient)",
         section="I. Product (If Applicable)", type="text", required=False, cell="D73"),
    dict(key="product_supplier", label="Supplier/manufacturer", section="I. Product (If Applicable)",
         type="text", required=False, cell="C74"),
    dict(key="product_serial", label="Product # (model, serial, etc.)",
         section="I. Product (If Applicable)", type="text", required=False, cell="D78"),
    dict(key="product_installed", label="Was the product installed?",
         section="I. Product (If Applicable)", type="ynu", required=False,
         cell={"yes": "E83", "no": "G83", "unknown": "I83"}),
    dict(key="product_installed_date", label="Date installed", section="I. Product (If Applicable)",
         type="date", required=False, cell="I85"),

    # ---- J. Witnesses ---------------------------------------------------------
    # The carrier form has no yes/no for witnesses; "None" in the first name box
    # is its convention. Asking the question instead of making people type
    # "None" — fill_xlsx writes "None" back when the answer is No.
    dict(key="any_witnesses", label="Did anyone see it happen?", section="J. Witnesses",
         type="yn", required="no_witness_named", cell=None,
         hint="A witness is someone who saw it happen — not someone the guest told afterward."),
    dict(key="witness1_name", label="Witness 1 — name", section="J. Witnesses",
         type="text", required="witnesses_seen", cell="B87"),
    dict(key="witness1_address", label="Witness 1 — address", section="J. Witnesses",
         type="text", required=False, cell="C88"),
    dict(key="witness1_city_state_zip", label="Witness 1 — City, State, Zip", section="J. Witnesses",
         type="text", required=False, cell="C89"),
    dict(key="witness1_phone", label="Witness 1 — phone", section="J. Witnesses",
         type="phone", required=False, cell="C90"),
    dict(key="witness2_name", label="Witness 2 — name", section="J. Witnesses",
         type="text", required=False, cell="G87"),
    dict(key="witness2_address", label="Witness 2 — address", section="J. Witnesses",
         type="text", required=False, cell="G88"),
    dict(key="witness2_city_state_zip", label="Witness 2 — City, State, Zip", section="J. Witnesses",
         type="text", required=False, cell="H89"),
    dict(key="witness2_phone", label="Witness 2 — phone", section="J. Witnesses",
         type="phone", required=False, cell="H90"),

    # ---- K. General remarks -----------------------------------------------------
    dict(key="general_remarks", label="Anything else we should know?",
         section="K. General Remarks", type="textarea", required=False, cell="C92",
         narrative=True,
         placeholder="Follow-up calls with the guest · doctor's appointments they "
                     "mentioned · paperwork you're still waiting on · what was "
                     "said after the fact.",
         hint="Everything that happened AFTER the incident belongs here, not in "
              "\"What happened?\" above."),
]

# --------------------------------------------------------------------------- #
# Screen layout
#
# FIELDS above is written in the carrier form's order, section letters and all,
# because that's the workbook it fills. People don't think in that order: they
# start with their hotel and what happened, not the named insured. LAYOUT is the
# order the online form asks in, and it renames the sections in plain words.
# The Excel fill-back is unaffected: every answer still goes to its own cell.
# --------------------------------------------------------------------------- #
LAYOUT = [
    ("Hotel details", [
        "loss_location_name", "parent_co_name", "subsidiary_name", "business_address",
        "business_city_state_zip", "business_county", "business_phone", "business_phone_ext",
        "business_fax", "location_code", "policy_number"]),
    ("About you", [
        "reported_by_name", "reported_by_title", "reported_by_phone", "reported_by_phone_ext",
        "reported_by_email", "reported_by_fax"]),
    ("What happened", [
        "type_of_loss", "date_of_accident", "time_of_accident", "notification_date",
        "on_insured_premises", "offsite_address", "offsite_city_state_zip", "offsite_county",
        "accident_description", "authority_contacted", "authority_name",
        "authority_report_number"]),
    ("The injured person", [
        "injured_name", "injury_illness_description", "injured_person_doing",
        "removed_via_ambulance", "fatality", "injured_address", "injured_city_state_zip",
        "injured_home_phone", "injured_work_phone", "injured_dob", "injured_age",
        "injured_gender"]),
    ("Medical treatment", [
        "injured_taken_to", "medical_facility_name", "physician_name", "medical_address",
        "medical_city_state_zip", "medical_phone"]),
    ("Damaged property", [
        "damaged_property_vehicle", "vehicle_year", "vehicle_make", "vehicle_model",
        "vehicle_plate_state", "vehicle_plate_number", "property_description",
        "damage_description", "estimated_damage_amount", "damage_when_to_view",
        "damage_where_to_view"]),
    ("Owner of the damaged property", [
        "property_owner_type", "company_owner_name", "personal_owner_name", "owner_address",
        "owner_city_state_zip", "owner_residence_phone", "owner_work_phone"]),
    ("Witnesses", [
        "any_witnesses", "witness1_name", "witness1_address", "witness1_city_state_zip",
        "witness1_phone", "witness2_name", "witness2_address", "witness2_city_state_zip",
        "witness2_phone"]),
    ("Adjuster contact", [
        "reporter_is_contact", "contact_person", "contact_title", "contact_phone",
        "contact_email", "emergency_claim"]),
    ("Product (if a product caused it)", [
        "product_name", "product_purchase_date", "product_description", "product_supplier",
        "product_serial", "product_installed", "product_installed_date"]),
    ("Anything else", ["general_remarks"]),
]


def _apply_layout(fields):
    by_key = {f["key"]: f for f in fields}
    laid_out = [k for _s, keys in LAYOUT for k in keys]
    assert sorted(laid_out) == sorted(by_key), (
        "LAYOUT must list every field exactly once: "
        f"missing {sorted(set(by_key) - set(laid_out))}, "
        f"extra {sorted(set(laid_out) - set(by_key))}")
    out = []
    for section, keys in LAYOUT:
        for k in keys:
            out.append(dict(by_key[k], section=section))
    return out


FIELDS = _apply_layout(FIELDS)
FIELDS_BY_KEY = {f["key"]: f for f in FIELDS}
SECTIONS = [s for s, _keys in LAYOUT]
HOTEL_SECTION = "Hotel details"


def _val(record, key):
    v = record.get(key)
    return v.strip() if isinstance(v, str) else v


# Conditional-requirement predicates. Referenced by name in FIELDS[required].
CONDITIONS = {
    # Off-site address block required when accident NOT on insured premises.
    "off_premises": lambda r: _val(r, "on_insured_premises") == "no",
    # Injured-person block required only once the loss type says a person was hurt.
    # A blank type_of_loss deliberately does NOT trigger this: type_of_loss is
    # itself mandatory, so an unanswered gate raises one clear error instead of
    # six phantom ones about an injured person who may not exist.
    "injury_loss": lambda r: _val(r, "type_of_loss") in ("injury", "both"),
    # Damaged-property block required unless the loss is injury-only.
    "property_loss": lambda r: _val(r, "type_of_loss") in ("property", "both"),
    "vehicle_damaged": lambda r: (_val(r, "type_of_loss") in ("property", "both")
                                  and _val(r, "damaged_property_vehicle") == "yes"),
    "nonvehicle_property_damaged": lambda r: (_val(r, "type_of_loss") in ("property", "both")
                                              and _val(r, "damaged_property_vehicle") == "no"),
    # Ask "did anyone see it?" unless a witness is already named (older reports
    # and uploaded workbooks carry the name, or "None", without the question).
    "no_witness_named": lambda r: not _val(r, "witness1_name"),
    "witnesses_seen": lambda r: _val(r, "any_witnesses") == "yes",
    # company_owned / personally_owned used to gate the Section H owner names.
    # Section H is optional now (see the note there), so nothing references them.
}


def is_required(field, record):
    req = field.get("required", False)
    if req is True:
        return True
    if req is False:
        return False
    return CONDITIONS[req](record)


# --------------------------------------------------------------------------- #
# Visibility — which sections / fields actually apply to this report
#
# The GL form asks ~90 questions covering every kind of liability loss. Any one
# incident needs a fraction of them. Showing all of them at once is what put
# injured-person answers on a fender-bender and property-owner answers on a
# slip-and-fall. Each rule below is a list of clauses; ALL must match.
#   {"field": <gate key>, "values": [<acceptable values>]}
# --------------------------------------------------------------------------- #
_INJURY = [dict(field="type_of_loss", values=["injury", "both"])]
_PROPERTY = [dict(field="type_of_loss", values=["property", "both"])]

SECTION_VISIBILITY = {
    "The injured person": _INJURY,
    "Medical treatment": _INJURY,
    "Damaged property": _PROPERTY,
    "Owner of the damaged property": _PROPERTY,
}

# Sections that are rare enough to stay collapsed until someone opens them.
OPTIONAL_SECTIONS = ["Product (if a product caused it)"]

# Short plain-English blurb shown under each section heading on the form.
SECTION_NOTES = {
    "Hotel details": "The hotel's own details, as they go on the carrier form.",
    "About you": "Who is filing this report.",
    "What happened": "When and where it happened, and what happened.",
    "The injured person": "The person who was hurt — their details, not the hotel's.",
    "Medical treatment": "Only if they were treated somewhere. Skip it if they refused treatment.",
    "Damaged property": "What was damaged.",
    "Owner of the damaged property": "Who owns the damaged item. Often the guest — but say so explicitly.",
    "Witnesses": "People who saw it happen.",
    "Adjuster contact": "Who the insurance adjuster should call about this claim.",
    "Product (if a product caused it)": "Rare. Only if a product the hotel supplied caused the loss "
                                        "(e.g. food, an appliance, a piece of furniture that gave way).",
    "Anything else": "Anything that happened after the incident.",
}

FIELD_VISIBILITY = {
    "offsite_address": [dict(field="on_insured_premises", values=["no"])],
    "offsite_city_state_zip": [dict(field="on_insured_premises", values=["no"])],
    "offsite_county": [dict(field="on_insured_premises", values=["no"])],

    "contact_person": [dict(field="reporter_is_contact", values=["no"])],
    "contact_title": [dict(field="reporter_is_contact", values=["no"])],
    "contact_phone": [dict(field="reporter_is_contact", values=["no"])],
    "contact_email": [dict(field="reporter_is_contact", values=["no"])],

    "authority_name": [dict(field="authority_contacted", values=["yes"])],
    "authority_report_number": [dict(field="authority_contacted", values=["yes"])],

    "medical_facility_name": [dict(field="injured_taken_to", values=["hospital_clinic"])],
    "physician_name": [dict(field="injured_taken_to", values=["physician"])],

    "vehicle_year": _PROPERTY + [dict(field="damaged_property_vehicle", values=["yes"])],
    "vehicle_make": _PROPERTY + [dict(field="damaged_property_vehicle", values=["yes"])],
    "vehicle_model": _PROPERTY + [dict(field="damaged_property_vehicle", values=["yes"])],
    "vehicle_plate_state": _PROPERTY + [dict(field="damaged_property_vehicle", values=["yes"])],
    "vehicle_plate_number": _PROPERTY + [dict(field="damaged_property_vehicle", values=["yes"])],
    "property_description": _PROPERTY + [dict(field="damaged_property_vehicle", values=["no"])],

    "company_owner_name": _PROPERTY + [dict(field="property_owner_type", values=["company"])],
    "personal_owner_name": _PROPERTY + [dict(field="property_owner_type", values=["personally"])],

    "product_installed_date": [dict(field="product_installed", values=["yes"])],

    **{k: [dict(field="any_witnesses", values=["yes"])] for k in (
        "witness1_name", "witness1_address", "witness1_city_state_zip", "witness1_phone",
        "witness2_name", "witness2_address", "witness2_city_state_zip", "witness2_phone")},
}


def rules_for(field_key):
    """All visibility clauses governing one field (its own plus its section's)."""
    clauses = list(FIELD_VISIBILITY.get(field_key, []))
    section = FIELDS_BY_KEY[field_key]["section"]
    for c in SECTION_VISIBILITY.get(section, []):
        if c not in clauses:
            clauses.append(c)
    return clauses


def applies(field_key, record):
    """Server-side: does this field apply to this record?

    Deliberately forgiving — a field is only ruled out when its gate holds a
    definite value that excludes it. An unanswered gate leaves the field in
    play, so nothing a reporter typed is ever discarded on a guess.
    """
    for c in rules_for(field_key):
        gate = _val(record, c["field"])
        if gate and gate not in c["values"]:
            return False
    return True


# "N/A", "none", "n\a"… typed into an optional phone/fax/date box. The format
# check reads them as malformed, which blocks approval until the reviewer blanks
# them by hand — the single most repeated edit in the audit trail. Blank means
# "not provided" already; treat the placeholder as blank.
_PLACEHOLDER_VALUES = {"n/a", "n\\a", "na", "n.a.", "none", "no", "n/a.", "not applicable",
                       "unknown", "unk", "-", "--", "x", "xxx", "tbd", "0"}
_PLACEHOLDER_TYPES = ("phone", "email", "date", "time", "money")


# --------------------------------------------------------------------------- #
# Date / time normalisation
#
# The browser's native pickers speak ISO — <input type="date"> submits
# YYYY-MM-DD and <input type="time"> submits 24-hour HH:MM. The Broadspire form
# wants MM/DD/YYYY and a 12-hour clock. We convert on the way in, so the record
# and every export keep the carrier's format, and convert back for display so
# the picker opens on the right day.
#
# Anything that doesn't parse is left exactly as typed — a value we can't read
# is still the reporter's answer, and losing it silently would be worse than
# showing it in a plain box.
# --------------------------------------------------------------------------- #
import datetime as _dt

_DATE_IN = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y")
_TIME_IN = ("%H:%M", "%I:%M %p", "%I:%M%p", "%H:%M:%S")


def _parse_date(value):
    v = (value or "").strip()
    for fmt in _DATE_IN:
        try:
            return _dt.datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _parse_time(value):
    v = (value or "").strip().upper().replace(".", "")
    for fmt in _TIME_IN:
        try:
            return _dt.datetime.strptime(v, fmt).time()
        except ValueError:
            continue
    return None


def to_carrier_date(value):
    """Anything parseable -> MM/DD/YYYY. Unparseable -> unchanged."""
    d = _parse_date(value)
    return d.strftime("%m/%d/%Y") if d else value


def to_carrier_time(value):
    """Anything parseable -> 'H:MM AM/PM'. Unparseable -> unchanged."""
    t = _parse_time(value)
    return t.strftime("%I:%M %p").lstrip("0") if t else value


def to_iso_date(value):
    """MM/DD/YYYY -> YYYY-MM-DD for <input type="date">. '' if unparseable."""
    d = _parse_date(value)
    return d.strftime("%Y-%m-%d") if d else ""


def to_iso_time(value):
    """'2:15 PM' -> '14:15' for <input type="time">. '' if unparseable."""
    t = _parse_time(value)
    return t.strftime("%H:%M") if t else ""


def normalize_dates(record):
    """Rewrite date/time answers into the carrier's format. Returns
    (cleaned_record, [keys_changed])."""
    cleaned, changed = dict(record), []
    for key, value in list(cleaned.items()):
        f = FIELDS_BY_KEY.get(key)
        if not f or not isinstance(value, str):
            continue
        if f["type"] == "date":
            new = to_carrier_date(value)
        elif f["type"] == "time":
            new = to_carrier_time(value)
        else:
            continue
        if new != value:
            cleaned[key] = new
            changed.append(key)
    return cleaned, changed


def clear_placeholders(record):
    """Blank out placeholder text in optional phone/e-mail/date/time/money boxes.

    Only optional fields, and only fields where a placeholder can't be a real
    answer — a witness genuinely named "None" is left alone.
    """
    cleaned, dropped = dict(record), []
    for key, value in list(cleaned.items()):
        f = FIELDS_BY_KEY.get(key)
        if not f or f["type"] not in _PLACEHOLDER_TYPES:
            continue
        if is_required(f, record):
            continue
        if isinstance(value, str) and value.strip().lower() in _PLACEHOLDER_VALUES:
            del cleaned[key]
            dropped.append(key)
    return cleaned, dropped


def strip_inapplicable(record):
    """Drop answers that belong to a section this loss type rules out.

    Stops Section H owner details riding along on an injury-only report (and
    vice versa) — the single biggest source of reviewer clean-up. Returns
    (cleaned_record, [keys_dropped]).
    """
    cleaned, dropped = dict(record), []
    for key in list(cleaned):
        if key in FIELDS_BY_KEY and cleaned[key] not in (None, "", []) \
                and not applies(key, record):
            del cleaned[key]
            dropped.append(key)
    return cleaned, dropped


_NO_WITNESS = {"none", "none.", "n/a", "na", "no", "nobody", "no one", "no-one",
               "no witnesses", "no witness", "unwitnessed"}


def derive(record, hotels=()):
    """Fill in answers the form no longer asks for, from answers it does.

    - "Did anyone see it?": older reports and uploaded workbooks carry a witness
      name (or "None") without the question, so answer it from the name.
    - Policy number and hotel name come from the hotel list by location code,
      so nobody has to know or type them.
    Never overwrites something already answered. Returns a new dict.
    """
    r = dict(record)
    w = (r.get("witness1_name") or "").strip()
    if not r.get("any_witnesses"):
        if w.lower() in _NO_WITNESS:
            r["any_witnesses"] = "no"
        elif w:
            r["any_witnesses"] = "yes"
    if r.get("any_witnesses") == "no" and w.lower() in _NO_WITNESS:
        r.pop("witness1_name", None)   # the "None" placeholder; fill_xlsx writes it back
    code = (r.get("location_code") or "").strip().upper()
    hotel = next((h for h in hotels if (h.get("location_code") or "").upper() == code), None) \
        if code else None
    if hotel:
        if not r.get("policy_number") and hotel.get("policy_number"):
            r["policy_number"] = hotel["policy_number"]
        if not r.get("loss_location_name") and hotel.get("name"):
            r["loss_location_name"] = hotel["name"]
    return r


def visibility_payload():
    """Serialisable rule set the browser uses to show/hide as answers come in.

    Note the difference from applies(): in the browser an unanswered gate HIDES
    the dependent fields (progressive disclosure), while on the server it keeps
    them (never discard on a guess).
    """
    return dict(sections=SECTION_VISIBILITY, fields=FIELD_VISIBILITY)


import re as _re

_WELLFORMED = {
    "date": _re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$|^\d{4}-\d{2}-\d{2}$"),
    "email": _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    "phone": _re.compile(r"[\d]{7,}"),
}


def check_completeness(record):
    """Return {'complete': bool, 'missing': [...], 'malformed': [...], 'checklist': [...]}.

    checklist rows: {key, label, section, required, ok, problem}
    """
    missing, malformed, checklist = [], [], []
    for f in FIELDS:
        required = is_required(f, record)
        raw = record.get(f["key"])
        value = raw.strip() if isinstance(raw, str) else raw
        present = value not in (None, "", [])
        problem = None
        if required and not present:
            problem = "missing"
            missing.append(f)
        elif present and f["type"] in _WELLFORMED:
            pat = _WELLFORMED[f["type"]]
            digits = _re.sub(r"\D", "", str(value)) if f["type"] == "phone" else str(value)
            ok_format = bool(pat.search(digits if f["type"] == "phone" else str(value)))
            if not ok_format:
                problem = "malformed"
                malformed.append(f)
        elif present and f["type"] in ("yn", "ynu", "choice"):
            allowed = f.get("choices") or (["yes", "no"] if f["type"] == "yn" else ["yes", "no", "unknown"])
            if str(value).lower() not in allowed:
                problem = "malformed"
                malformed.append(f)
        checklist.append(dict(key=f["key"], label=f["label"], section=f["section"],
                              required=required, ok=problem is None, problem=problem))
    return dict(complete=not missing and not malformed,
                missing=[dict(key=f["key"], label=f["label"], section=f["section"]) for f in missing],
                malformed=[dict(key=f["key"], label=f["label"], section=f["section"]) for f in malformed],
                checklist=checklist)


def extraction_schema():
    """JSON-schema-ish field list handed to the AI extractor prompt."""
    out = []
    for f in FIELDS:
        entry = dict(key=f["key"], label=f["label"], section=f["section"], type=f["type"])
        if f.get("choices"):
            entry["choices"] = f["choices"]
        elif f["type"] == "yn":
            entry["choices"] = ["yes", "no"]
        elif f["type"] == "ynu":
            entry["choices"] = ["yes", "no", "unknown"]
        out.append(entry)
    return out
