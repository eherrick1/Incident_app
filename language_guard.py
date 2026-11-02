"""Language guard — the module that replaces the expense app's code-validator.

Scans the four narrative fields for blame- or liability-assigning language.
For each risky phrase it produces a *finding*: the flagged phrase, the reason
(from the config rulebook), and a suggested neutral rewrite of the containing
sentence. The reviewer accepts / edits / keeps each one; nothing is ever
changed silently, and the reporter's original words are preserved in
record["original_narratives"].

The rulebook lives in language_rules.json (config, not code) so risk/counsel
can tune it without a deploy.
"""
import json
import re

import config
from fields import NARRATIVE_FIELDS, FIELDS_BY_KEY

_rules_cache = None


def load_rules(path=None):
    global _rules_cache
    if _rules_cache is None or path is not None:
        with open(path or config.LANGUAGE_RULES_PATH, encoding="utf-8") as fh:
            _rules_cache = json.load(fh)
    return _rules_cache


def _sentences(text):
    """Split text into sentences, keeping character offsets."""
    out = []
    for m in re.finditer(r"[^.!?\n]+[.!?]?", text):
        s = m.group().strip()
        if s:
            out.append((s, m.start()))
    return out


def _find_ci(haystack, needle):
    m = re.search(re.escape(needle), haystack, re.IGNORECASE)
    return m


def _paragraph_prefix(text, offset):
    """The text before `offset` within the same paragraph (blank-line separated)."""
    start = 0
    for m in re.finditer(r"\n\s*\n", text[:offset]):
        start = m.end()
    return text[start:offset]


# ----------------------------------------------------------------------------- #
# Neutral rewrite of a single sentence (deterministic, template-based)
# ----------------------------------------------------------------------------- #
def _agree(subject, verb_word):
    """Pick a pronoun and an attribution verb that agree with the subject.

    'the guest'  -> ('they', 'alleges')  ->  "the guest alleges they fell"
    'he'         -> ('he',   'alleges')  ->  "he alleges he slipped"
    'they'       -> ('they', 'allege')   ->  "they allege they fell"

    Before this, the pronoun was hardcoded to 'they' regardless of subject,
    which produced "he alleges they slipped" — text that then went out on the
    carrier form. Grammar matters here: the rewrite is the record.
    """
    bare = re.sub(r"^the\s+", "", subject.strip(), flags=re.IGNORECASE).lower()
    plural = verb_word[:-1] if verb_word.endswith("s") else verb_word
    if bare in ("he", "she"):
        return bare, verb_word
    if bare == "they":
        return "they", plural
    if bare == "i":
        return "I", plural
    return "they", verb_word


def _attribute_clause(clause, rules):
    """'The guest fell in the tub' -> 'The guest alleges they fell in the tub'."""
    a = rules["attribution"]
    subjects = "|".join(re.escape(s) for s in sorted(a["subjects"], key=len, reverse=True))
    verbs = "|".join(re.escape(v) for v in sorted(a["incident_verbs"], key=len, reverse=True))
    verb_word = a.get("attribution_verb_for_suggestion", "alleges")
    pat = re.compile(rf"\b((?:the\s+)?(?:{subjects}))\s+({verbs})\b", re.IGNORECASE)

    def repl(m):
        subj, incident_verb = m.group(1), m.group(2)
        pronoun, attrib_verb = _agree(subj, verb_word)
        # "the guest was injured" -> "the guest alleges they were injured"
        if pronoun == "they" and incident_verb.lower().startswith("was "):
            incident_verb = "were" + incident_verb[3:]
        return f"{subj} {attrib_verb} {pronoun} {incident_verb}"

    return pat.sub(repl, clause, count=1)


def _is_attributed(text, rules):
    """True if this text already frames the event as someone's account.

    Whole-word matching, deliberately: a substring test counts "claim" inside
    "the claimant" and silently excuses an unattributed assertion. Every marker
    is listed in its inflected forms in the rulebook, so word boundaries lose
    nothing.
    """
    a = rules["attribution"]
    low = (text or "").lower()
    if not low.strip():
        return False
    markers = list(a["attribution_markers"]) + list(a["witnessed_markers"])
    pat = "|".join(re.escape(m) for m in sorted(markers, key=len, reverse=True))
    return bool(re.search(rf"\b(?:{pat})\b", low))


def _needs_attribution(sentence, rules, context=""):
    """Does this sentence assert an unwitnessed event in the property's voice?

    `context` is the earlier text of the same paragraph. A GM who opens with
    "Pedro stated that he noticed condensation…" has already attributed the
    whole passage; flagging "As a result, he slipped…" two sentences later
    reads as a false positive and invites a rewrite that mangles a report
    that was fine as written.
    """
    a = rules["attribution"]
    if _is_attributed(sentence, rules) or _is_attributed(context, rules):
        return False
    subjects = "|".join(re.escape(s) for s in sorted(a["subjects"], key=len, reverse=True))
    verbs = "|".join(re.escape(v) for v in sorted(a["incident_verbs"], key=len, reverse=True))
    m = re.search(rf"\b((?:the\s+)?(?:{subjects}))\s+({verbs})\b", sentence, re.IGNORECASE)
    return m


def _split_causal(sentence, rules):
    for conn in sorted(rules["causation"]["connectors"], key=len, reverse=True):
        m = re.search(rf"[,\s]\b{re.escape(conn)}\b", sentence, re.IGNORECASE)
        if m:
            main = sentence[:m.start()].rstrip(" ,")
            cause = sentence[m.end():].strip().rstrip(".!?")
            return conn.strip(), main, cause
    return None, sentence, None


def neutral_rewrite(sentence, rules, context=""):
    """Best-effort neutral rewrite of one sentence. Returns (rewrite, notes)."""
    notes = []
    conn, main, cause = _split_causal(sentence, rules)

    if _needs_attribution(main, rules, context):
        main = _attribute_clause(main, rules)
        notes.append("attributed the unwitnessed event to the guest")

    if cause:
        cause = re.sub(r"^(it|the \w+) was\b", r"\1 was", cause, flags=re.IGNORECASE)
        main = f"{main.rstrip('.!?')} and stated they believed {cause}"
        notes.append(f"converted the '{conn}' causal claim into an attributed belief; "
                     "if the condition was directly observed, describe it separately "
                     "(e.g. 'At the time of the report, … was observed.')")

    # Person-characterizing adjectives -> pointer to observed behavior.
    for term in rules["characterization"]["terms"]:
        if re.search(rf"\b{re.escape(term)}\b", main, re.IGNORECASE):
            main = re.sub(rf"\b{re.escape(term)}\b", "[describe observed behavior]",
                          main, flags=re.IGNORECASE)
            notes.append(f"replaced characterization '{term}' — describe the behavior observed")

    if not main.rstrip().endswith((".", "!", "?")):
        main = main.rstrip() + "."
    return main, notes


# ----------------------------------------------------------------------------- #
# Scan
# ----------------------------------------------------------------------------- #
def scan_text(field_key, text, rules=None):
    """Return a list of findings for one narrative field."""
    rules = rules or load_rules()
    findings = []
    if not text or not text.strip():
        return findings
    seq = 0
    for sent, offset in _sentences(text):
        sentence_findings = []
        # Everything earlier in this paragraph — used to tell a genuinely
        # unattributed assertion from a continuation of an attributed passage.
        context = _paragraph_prefix(text, offset)

        # 1) Apologies / admissions — recommend removal of the sentence.
        for phrase in rules["apology"]["phrases"]:
            m = _find_ci(sent, phrase)
            if m:
                sentence_findings.append(dict(
                    rule="apology_admission", phrase=m.group(),
                    reason=rules["apology"]["reason"],
                    suggestion="", suggestion_is_removal=True))
                break

        # 2) Causation connectors in the property's voice.
        conn, _main, cause = _split_causal(sent, rules)
        rewrite, rewrite_notes = neutral_rewrite(sent, rules, context)
        if conn and cause:
            sentence_findings.append(dict(
                rule="fault_causation",
                phrase=f"{conn} {cause}".strip(),
                reason=rules["causation"]["reason"],
                suggestion=rewrite, suggestion_is_removal=False))

        # 3) Fault vocabulary (skip words already inside the causal phrase finding).
        for term, hint in rules["fault_terms"]["terms"].items():
            m = _find_ci(sent, term)
            if m and not (cause and _find_ci(cause, term) and conn):
                sentence_findings.append(dict(
                    rule="fault_term", phrase=m.group(),
                    reason=rules["fault_terms"]["reason"] + " " + hint,
                    suggestion=rewrite, suggestion_is_removal=False))

        # 4) Person-characterizing adjectives.
        for term, hint in rules["characterization"]["terms"].items():
            m = _find_ci(sent, term)
            if m:
                sentence_findings.append(dict(
                    rule="characterization", phrase=m.group(),
                    reason=rules["characterization"]["reason"] + " " + hint,
                    suggestion=rewrite, suggestion_is_removal=False))

        # 5) Unattributed, unwitnessed incident assertion.
        m = _needs_attribution(sent, rules, context)
        if m:
            sentence_findings.append(dict(
                rule="attribution", phrase=m.group(),
                reason=rules["attribution"]["reason"],
                suggestion=rewrite, suggestion_is_removal=False))

        for f in sentence_findings:
            f.update(id=f"{field_key}:{offset}:{f['rule']}:{seq}",
                     field=field_key,
                     field_label=FIELDS_BY_KEY[field_key]["label"],
                     sentence=sent, sentence_offset=offset,
                     decision="pending", final_text=None,
                     decided_by=None, decided_at=None)
            findings.append(f)
            seq += 1
    return findings


def scan_record(fields_dict, rules=None):
    """Scan all four narrative fields. Returns list of findings."""
    rules = rules or load_rules()
    findings = []
    for key in NARRATIVE_FIELDS:
        findings.extend(scan_text(key, fields_dict.get(key, "") or "", rules))
    return findings


# ----------------------------------------------------------------------------- #
# Reviewer decisions
# ----------------------------------------------------------------------------- #
def apply_decision(doc, finding_id, decision, edited_text=None, actor="reviewer"):
    """decision: accept | edit | keep. Applies at sentence level; findings that
    share the sentence are superseded when the sentence is replaced.
    Returns the finding, or raises KeyError/ValueError.
    """
    from storage import audit, now_iso

    finding = next((f for f in doc.get("language_findings", []) if f["id"] == finding_id), None)
    if finding is None:
        raise KeyError(finding_id)
    if decision not in ("accept", "edit", "keep"):
        raise ValueError(decision)

    field, sentence = finding["field"], finding["sentence"]
    if decision == "keep":
        new_text = None
    elif decision == "edit":
        new_text = (edited_text or "").strip()
    else:  # accept
        new_text = finding["suggestion"]

    if decision in ("accept", "edit"):
        current = doc["fields"].get(field, "") or ""
        if sentence in current:
            replaced = current.replace(sentence, new_text, 1)
            doc["fields"][field] = re.sub(r"  +", " ", replaced).strip()
        else:
            # Sentence already rewritten by an earlier decision — record only.
            pass
        # Supersede sibling findings on the same (now gone) sentence.
        for sib in doc.get("language_findings", []):
            if (sib["id"] != finding_id and sib["field"] == field
                    and sib["sentence"] == sentence and sib["decision"] == "pending"):
                sib.update(decision="superseded", decided_by=actor, decided_at=now_iso(),
                           final_text=new_text)

    finding.update(decision={"accept": "accepted", "edit": "edited", "keep": "kept_original"}[decision],
                   final_text=new_text if decision != "keep" else sentence,
                   decided_by=actor, decided_at=now_iso())
    audit(doc, f"language_{finding['decision']}", actor=actor,
          detail=f"{finding['field_label']}: \"{finding['phrase']}\"",
          extra=dict(finding_id=finding_id, rule=finding["rule"],
                     final_text=finding["final_text"]))
    return finding


def pending_findings(doc):
    return [f for f in doc.get("language_findings", []) if f["decision"] == "pending"]
