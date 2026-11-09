"""Masquage des secrets dans les journaux d'une tentative (vault.injection.mask_text, runs.finalize.mask_cut_fragment).

Secrets synthétiques. Défauts couverts : un secret contenu dans un autre laissait le reste du long en clair selon l'ordre ; un journal coupé
à ses N derniers octets AVANT le masquage pouvait trancher un secret et en laisser la fin en clair."""

import base64
import json
import time
from urllib.parse import quote, quote_plus

import pytest

from runs import finalize
from runs.runners import _parse
from runs.runtime_config import settings as runtime_settings
from vault.injection import all_forms, mask_json, mask_text, secret_forms

SECRET = "sk-synth-ABCDEF123456"  # scan:allow valeur synthétique de test, aucun secret réel


def test_exact_secrets_are_masked_and_other_text_is_untouched():
    assert mask_text(f"token={SECRET} ok", [SECRET]) == "token=*** ok"
    assert mask_text("rien à masquer", [SECRET]) == "rien à masquer"


@pytest.mark.parametrize("order", [["Pass", "Pass1234-long-secret"], ["Pass1234-long-secret", "Pass"]])
def test_a_secret_contained_in_another_never_leaves_the_rest_in_clear(order):
    assert mask_text("a=Pass1234-long-secret", order) == "a=***"


def test_an_empty_secret_is_ignored_instead_of_matching_everywhere():
    assert mask_text("abc", ["", SECRET]) == "abc"


def test_the_end_of_a_secret_cut_by_the_log_limit_is_masked():
    assert finalize.mask_cut_fragment(f"{SECRET[-8:]} suite du journal", [SECRET]) == "*** suite du journal"


def test_a_log_that_does_not_open_with_a_secret_fragment_is_unchanged():
    text = "début normal " + SECRET[-8:]  # a fragment in the middle was not produced by a cut
    assert finalize.mask_cut_fragment(text, [SECRET]) == text
    assert finalize.mask_cut_fragment("", [SECRET]) == ""
    assert finalize.mask_cut_fragment("tout", []) == "tout"


def test_fragment_floor_is_four_characters():
    assert finalize.mask_cut_fragment(SECRET[-4:] + " x", [SECRET]) == "*** x"
    assert finalize.mask_cut_fragment(SECRET[-3:] + " x", [SECRET]) == SECRET[-3:] + " x"  # below the floor


def test_a_character_cut_in_two_by_the_byte_cut_does_not_hide_the_fragment():
    secret = "clé-é-secrète-9876"
    cut = secret.encode()[3:]  # starts inside the two-byte « é »
    text = cut.decode(errors="replace") + " fin"
    assert text.startswith("�")
    assert finalize.mask_cut_fragment(text, [secret]) == "*** fin"


def test_the_longest_matching_fragment_wins():
    assert finalize.mask_cut_fragment("ABCDEF123456", [SECRET, "CDEF123456"]) == "***"


def test_real_cut_of_the_runner_output_then_platform_masking_leaves_no_trace_of_the_secret():
    limit = runtime_settings.ORCH_MAX_LOG_BYTES
    stderr = SECRET.encode() + b"y" * (limit - 5)  # the byte cut falls inside the secret: its last 5 bytes remain
    cut = _parse(b'{"ok": true}', stderr, 0).logs
    assert cut.startswith(SECRET[-5:])  # what the platform receives
    final = finalize.mask_cut_fragment(mask_text(cut, [SECRET]), [SECRET])
    assert final.startswith("***") and SECRET[-5:] not in final


# --- formes dérivées (secret_forms) -------------------------------------------------------------------------------------
PASS = 'p@ss w0rd/é+"x'  # characters that change in every supported encoding


def test_supported_forms_are_exactly_these():
    raw = PASS.encode()
    expected = {
        PASS,
        base64.b64encode(raw).decode(),
        base64.b64encode(raw).decode().rstrip("="),
        base64.urlsafe_b64encode(raw).decode(),
        base64.urlsafe_b64encode(raw).decode().rstrip("="),
        quote(PASS, safe=""),
        quote_plus(PASS, safe=""),
        json.dumps(PASS)[1:-1],
        json.dumps(PASS, ensure_ascii=False)[1:-1],
    }
    assert set(secret_forms(PASS)) == expected
    assert secret_forms(PASS) == sorted(secret_forms(PASS), key=len, reverse=True)


@pytest.mark.parametrize("form", secret_forms(PASS))
def test_every_supported_form_is_masked_in_text(form):
    assert mask_text(f"avant {form} après", [PASS]) == "avant *** après"


@pytest.mark.parametrize("form", secret_forms(PASS))
def test_every_supported_form_is_masked_in_json_and_the_structure_is_kept(form):
    out = mask_json({"k": f"v {form}", "n": 3, "l": [form, "x"]}, [PASS])
    assert out == {"k": "v ***", "n": 3, "l": ["***", "x"]}


def test_a_plain_secret_has_no_phantom_forms_and_a_short_one_no_derived_form_below_the_floor():
    assert "abcdefgh" in secret_forms("abcdefgh")
    for form in secret_forms("abcd"):
        assert form == "abcd" or len(form) >= 6  # base64 of 4 bytes: 8 padded, 6 unpadded
    assert secret_forms("abcd")[-1] == "abcd"


def test_duplicates_and_equal_forms_are_collapsed():
    forms = all_forms([SECRET, SECRET, "", SECRET])
    assert len(forms) == len(set(forms))
    assert "" not in forms


def test_overlap_between_a_secret_and_the_form_of_another_is_masked_whatever_the_order():
    other = base64.b64encode(SECRET.encode()).decode()  # one secret IS the base64 of another
    text = f"a={SECRET} b={other}"
    assert mask_text(text, [SECRET, other]) == mask_text(text, [other, SECRET]) == "a=*** b=***"


def test_decoding_is_never_attempted_so_unrelated_base64_is_untouched():
    other = base64.b64encode(b"un autre texte sans rapport").decode()
    assert mask_text(f"data={other}", [SECRET]) == f"data={other}"


def test_ordinary_text_is_not_masked_by_derived_forms_of_a_four_character_secret():
    text = "The quick brown fox: abc%20d neither; token abcd is."
    assert mask_text(text, ["abcd"]) == "The quick brown fox: abc%20d neither; token *** is."


def test_json_syntax_never_breaks_even_with_a_secret_made_of_structure_characters():
    out = mask_json({"a": 'x"y,z}'}, ['x"y,z}'])
    assert out == {"a": "***"}


def test_a_cut_inside_an_encoded_form_is_masked_at_the_start_of_the_log():
    form = base64.b64encode(SECRET.encode()).decode()
    assert finalize.mask_cut_fragment(form[-12:] + " suite", [SECRET]) == "*** suite"
    assert finalize.mask_cut_fragment(form[-7:] + " suite", [SECRET]) == form[-7:] + " suite"  # below the derived floor


def test_a_representative_volume_is_masked_quickly_and_only_where_needed():
    secrets = [f"sk-synth-{i:04d}-ABCDEFGHIJKL" for i in range(20)]
    noise = "".join(
        f"2026-11-09 INFO étape {i} terminée en {i % 90} ms payload=eyJhIjoxfQ== chemin=/a%20b\n" for i in range(2500)
    )
    leaks = "".join(quote(s) + base64.b64encode(s.encode()).decode() + " " + s + "\n" for s in secrets)
    text = noise + leaks
    started = time.monotonic()
    out = mask_text(text, secrets)
    elapsed = time.monotonic() - started
    assert elapsed < 2
    assert not any(s in out for s in secrets) and not any(base64.b64encode(s.encode()).decode() in out for s in secrets)
    assert out.count("***") == 60  # three occurrences of each secret, none other
    assert out.startswith(noise)  # the useful content (including unrelated base64 and %20) is intact


def test_the_http_step_masks_derived_forms_in_nested_responses_and_still_ignores_values_under_four_characters():
    from runs.http_node import _mask

    form = base64.b64encode(SECRET.encode()).decode()
    body = {"a": [f"x {form}", {"b": quote(SECRET, safe="") + SECRET}], "n": 1}
    assert _mask(body, [SECRET, "abc", ""]) == {"a": ["x ***", {"b": "******"}], "n": 1}
    assert _mask("abc abc", ["abc"]) == "abc abc"  # below the floor of 4: never masked
