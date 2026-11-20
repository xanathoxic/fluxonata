"""Adresse du client derrière des proxys : ORCH_NUM_PROXIES (fluxonata/proxyconf.py)."""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from rest_framework.settings import api_settings
from rest_framework.test import APIRequestFactory
from rest_framework.throttling import BaseThrottle

from fluxonata.proxyconf import MAX_PROXIES, num_proxies


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, None), ("", None), ("  ", None), ("0", 0), ("1", 1), (" 2 ", 2), ("10", 10)]
)
def test_num_proxies_accepts_absent_or_a_small_integer(raw, expected):
    assert num_proxies(raw) == expected


@pytest.mark.parametrize("raw", ["-1", "11", "1.5", "deux", "1,2", "١", "9" * 30, "0x1"])
def test_num_proxies_rejects_anything_else_at_startup(raw):
    with pytest.raises(ImproperlyConfigured, match="ORCH_NUM_PROXIES"):
        num_proxies(raw)
    assert MAX_PROXIES == 10


def ident(num_proxies_setting, forwarded: str | None, remote: str = "10.0.0.9") -> str:
    """Adresse que DRF donne aux limites de débit pour une requête portant cet `X-Forwarded-For`."""
    rest = {"NUM_PROXIES": num_proxies_setting}
    extra = {"REMOTE_ADDR": remote}
    if forwarded is not None:
        extra["HTTP_X_FORWARDED_FOR"] = forwarded
    with override_settings(REST_FRAMEWORK=rest):
        assert api_settings.NUM_PROXIES == num_proxies_setting
        return BaseThrottle().get_ident(APIRequestFactory().get("/", **extra))


def test_without_the_setting_a_client_chooses_its_own_address():
    # Comportement historique, conservé par défaut : deux requêtes du même client, deux adresses.
    assert ident(None, "1.1.1.1") != ident(None, "2.2.2.2")


def test_one_trusted_proxy_keeps_only_the_address_it_appended():
    # nginx livré ajoute l'adresse de connexion en fin de liste ; ce que le client a écrit avant est ignoré.
    assert ident(1, "1.1.1.1, 203.0.113.7") == ident(1, "2.2.2.2, 203.0.113.7") == "203.0.113.7"


def test_two_trusted_proxies_keep_the_address_seen_by_the_first_one():
    assert ident(2, "9.9.9.9, 203.0.113.7, 10.1.1.1") == "203.0.113.7"


def test_zero_proxy_uses_the_connection_address_only():
    assert ident(0, "1.1.1.1", remote="198.51.100.4") == "198.51.100.4"


def test_header_shorter_than_the_proxy_count_uses_what_is_there_and_no_header_uses_the_connection_address():
    # Un N trop grand ne protège plus (voir proxyconf.py) : DRF retient alors la première valeur de l'en-tête, écrite par le client.
    assert ident(3, "203.0.113.7", remote="198.51.100.4") == "203.0.113.7"
    assert ident(1, None, remote="198.51.100.4") == "198.51.100.4"
