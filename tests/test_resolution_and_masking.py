from __future__ import annotations

import pytest

from derva.masking import RegexMasker
from derva.resolution import Caller, InMemoryDirectory, ResolutionStatus, resolve_clients


def test_single_match_becomes_reference(caller: Caller, directory: InMemoryDirectory) -> None:
    r = resolve_clients("Email Mary Byrne a summary of her Q3 fees.", caller, directory)
    assert r.status is ResolutionStatus.OK
    assert r.text == "Email client:C-1042 a summary of her Q3 fees."
    assert r.references == ["client:C-1042"]


def test_alias_and_case_insensitive(caller: Caller, directory: InMemoryDirectory) -> None:
    r = resolve_clients("what does sean o'brien hold?", caller, directory)
    assert r.references == ["client:C-1077"]


def test_two_clients_with_one_name_ask_the_user(
    caller: Caller, directory: InMemoryDirectory
) -> None:
    r = resolve_clients("What did John Kelly pay last year?", caller, directory)
    assert r.status is ResolutionStatus.AMBIGUOUS
    assert r.ambiguous == {"John Kelly": ["client:C-2210", "client:C-2211"]}


def test_client_outside_scope_is_refused_without_naming_it(
    caller: Caller, directory: InMemoryDirectory
) -> None:
    r = resolve_clients("Update Aoife Walsh's address", caller, directory)
    assert r.status is ResolutionStatus.DENIED
    assert r.denied_count == 1
    # The result never carries the denied name anywhere except the untouched text.
    assert "Aoife" not in r.model_dump_json(exclude={"text"})


def test_other_team_can_act_for_its_own_client(directory: InMemoryDirectory) -> None:
    other = Caller(key_id="k2", owner="someone@example.ie", teams=["wealth-b"])
    r = resolve_clients("Update Aoife Walsh's address", other, directory)
    assert r.status is ResolutionStatus.OK
    assert r.references == ["client:C-3001"]


def test_regex_masker_numbers_distinct_values() -> None:
    m = RegexMasker().mask("cc a@x.ie and b@y.ie, then a@x.ie again")
    assert m.text == "cc <EMAIL_ADDRESS_1> and <EMAIL_ADDRESS_2>, then <EMAIL_ADDRESS_1> again"
    assert m.counts == {"EMAIL_ADDRESS": 2}
    assert m.unmask(m.text) == "cc a@x.ie and b@y.ie, then a@x.ie again"


def test_masking_never_touches_client_references() -> None:
    m = RegexMasker().mask("Send client:C-1042 the IBAN IE29 AIBK 9311 5212 3456 78")
    assert "client:C-1042" in m.text
    assert "<IBAN_CODE_1>" in m.text


def test_mask_result_does_not_leak_mapping_in_repr() -> None:
    m = RegexMasker().mask("write to a@x.ie")
    assert "a@x.ie" not in repr(m)


@pytest.mark.presidio
def test_presidio_catches_names() -> None:
    pytest.importorskip("presidio_analyzer")
    from derva.masking import PresidioMasker

    try:
        masker = PresidioMasker()
    except OSError:
        pytest.skip("spaCy model not installed")
    m = masker.mask("Forward the statement to Peter Murphy at peter.m@gmail.com")
    assert "Peter Murphy" not in m.text
    assert "peter.m@gmail.com" not in m.text
