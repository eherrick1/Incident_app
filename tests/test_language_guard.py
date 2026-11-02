import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import language_guard as lg


def rules():
    return lg.load_rules()


# ---- Acceptance criterion sentence -------------------------------------------
def test_tub_sentence_flags_and_rewrite():
    text = "The guest fell in the tub because it was slippery."
    findings = lg.scan_text("accident_description", text, rules())
    rules_hit = {f["rule"] for f in findings}
    assert "attribution" in rules_hit, "must flag the unwitnessed 'fell in the tub'"
    assert "fault_causation" in rules_hit, "must flag 'because … slippery'"
    attr = next(f for f in findings if f["rule"] == "attribution")
    assert "fell" in attr["phrase"]
    caus = next(f for f in findings if f["rule"] == "fault_causation")
    assert "because" in caus["phrase"].lower() and "slippery" in caus["phrase"].lower()
    # Both findings propose the same neutral rewrite of the sentence.
    for f in findings:
        s = f["suggestion"].lower()
        assert "alleges they fell" in s
        assert "stated they believed" in s
        assert "because" not in s


def test_attribution_not_flagged_when_attributed():
    t = "The guest states they fell in the tub."
    assert not [f for f in lg.scan_text("accident_description", t) if f["rule"] == "attribution"]


def test_attribution_not_flagged_when_witnessed():
    t = "Staff observed the guest slipped near the lobby entrance."
    assert not [f for f in lg.scan_text("accident_description", t) if f["rule"] == "attribution"]


def test_fault_terms_flagged():
    for term, sample in [
        ("broken", "The step was broken."),
        ("failed to", "Our staff failed to put out a sign."),
        ("should have", "We should have mopped sooner."),
        ("hazardous", "The walkway was hazardous."),
    ]:
        findings = lg.scan_text("general_remarks", sample)
        assert any(term in f["phrase"].lower() for f in findings), sample


def test_characterization_flagged_with_behavior_hint():
    findings = lg.scan_text("general_remarks", "The guest was drunk and careless.")
    terms = {f["phrase"].lower() for f in findings if f["rule"] == "characterization"}
    assert "drunk" in terms and "careless" in terms
    f = next(f for f in findings if f["phrase"].lower() == "drunk")
    assert "behavior" in f["reason"].lower() or "stumbling" in f["reason"].lower()


def test_apology_flagged_for_removal():
    findings = lg.scan_text("general_remarks", "We're sorry this happened and we'll cover it.")
    ap = [f for f in findings if f["rule"] == "apology_admission"]
    assert ap and ap[0]["suggestion_is_removal"]


def test_factual_specificity_not_flagged():
    t = ("At 3:15 PM in room 204, staff provided a bandage and ice pack. "
         "The duty manager was notified at 3:20 PM. CCTV footage of the corridor was saved.")
    assert lg.scan_text("general_remarks", t) == []


def test_clean_attributed_narrative_passes():
    t = ("The guest reported that they slipped near the lobby entrance at approximately 9:40 AM. "
         "The floor in that area was wet at the time of the report. "
         "First aid was offered and declined; photos were taken.")
    assert [f for f in lg.scan_text("accident_description", t) if f["decision"] == "pending"] == []


# ---- Pronoun agreement in the suggested rewrite --------------------------------
def test_rewrite_agrees_with_the_sentence_subject():
    """INC-20260723 shipped "he alleges they slipped" to the carrier — the
    pronoun used to be hardcoded regardless of subject."""
    cases = [
        ("He slipped on the tile.", "he alleges he slipped"),
        ("She tripped on the mat.", "she alleges she tripped"),
        ("They fell near the pool.", "they allege they fell"),
        ("The guest fell in the tub.", "the guest alleges they fell"),
        ("The claimant was injured in the lobby.", "the claimant alleges they were injured"),
    ]
    for sentence, expected in cases:
        got, _ = lg.neutral_rewrite(sentence, rules())
        assert expected in got.lower(), f"{sentence!r} -> {got!r}"


# ---- Paragraph-level attribution context ---------------------------------------
def test_attribution_context_carries_across_the_paragraph():
    """A passage opened with "Pedro stated that…" is already attributed; the
    continuation sentence should not be flagged."""
    text = ("Pedro stated that he noticed condensation dripping from the ceiling. "
            "As a result, he slipped, struck his right foot on the bathtub, and hit his head.")
    assert not [f for f in lg.scan_text("accident_description", text)
                if f["rule"] == "attribution"]


def test_attribution_context_does_not_leak_across_paragraphs():
    text = ("Pedro stated that he noticed condensation on the ceiling.\n\n"
            "The guest fell in the lobby.")
    hits = [f for f in lg.scan_text("accident_description", text) if f["rule"] == "attribution"]
    assert len(hits) == 1 and "fell" in hits[0]["phrase"]


def test_claimant_is_not_mistaken_for_the_word_claim():
    """Marker matching is whole-word: "claim" inside "the claimant" used to
    excuse an unattributed assertion."""
    hits = [f for f in lg.scan_text("accident_description", "The claimant slipped in the lobby.")
            if f["rule"] == "attribution"]
    assert len(hits) == 1


# ---- Reviewer decisions -------------------------------------------------------
def _doc_with(text):
    doc = dict(id="T-1", fields={"accident_description": text},
               original_narratives={"accident_description": text},
               language_findings=lg.scan_text("accident_description", text),
               audit=[])
    return doc


def test_accept_applies_rewrite_supersedes_sibling_and_audits():
    text = "The guest fell in the tub because it was slippery."
    doc = _doc_with(text)
    first = doc["language_findings"][0]
    lg.apply_decision(doc, first["id"], "accept", actor="reviewer")
    new_text = doc["fields"]["accident_description"]
    assert "alleges they fell" in new_text and "because" not in new_text
    assert doc["original_narratives"]["accident_description"] == text  # never altered
    # sibling finding on the same sentence superseded, nothing pending
    assert lg.pending_findings(doc) == []
    assert any(a["action"] == "language_accepted" for a in doc["audit"])


def test_edit_applies_reviewer_text():
    doc = _doc_with("The guest fell in the tub because it was slippery.")
    f = doc["language_findings"][0]
    custom = "The guest reported that they fell in the bathtub; no bath mat was observed at the time of the report."
    lg.apply_decision(doc, f["id"], "edit", edited_text=custom)
    assert custom in doc["fields"]["accident_description"]
    assert any(a["action"] == "language_edited" for a in doc["audit"])


def test_keep_leaves_text_untouched_but_logs():
    text = "The guest fell in the tub because it was slippery."
    doc = _doc_with(text)
    for f in list(doc["language_findings"]):
        if f["decision"] == "pending":
            lg.apply_decision(doc, f["id"], "keep")
    assert doc["fields"]["accident_description"] == text
    assert lg.pending_findings(doc) == []
    assert any(a["action"] == "language_kept_original" for a in doc["audit"])
