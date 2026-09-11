from scripts.market_live_probe import _parse_include, _sanitize


def test_probe_include_aliases_are_stable():
    assert _parse_include("QUOTE,order_book,capital-flow") == ("quote", "order_book", "capital_flow")


def test_probe_second_pass_redacts_keys_and_credential_urls():
    secret = "LIVE_PROBE_SENTINEL"
    result = _sanitize({
        "provider": "tencent",
        "api_key": secret,
        "headers": {"Authorization": secret},
        "nested": {"message": f"https://user:{secret}@provider.example/path"},
        "quality": "B",
    })
    text = str(result)
    assert result == {"provider": "tencent", "nested": {}, "quality": "B"}
    assert secret not in text
