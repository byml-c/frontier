from frontier.config import Settings


def test_local_api_base_url_uses_configured_bind_address() -> None:
    configured = Settings(_env_file=None, host="172.16.120.97", port=12101)

    assert configured.local_api_base_url == "http://172.16.120.97:12101"


def test_local_api_base_url_maps_wildcard_addresses_to_loopback() -> None:
    ipv4 = Settings(_env_file=None, host="0.0.0.0", port=8000)
    ipv6 = Settings(_env_file=None, host="::", port=8000)

    assert ipv4.local_api_base_url == "http://127.0.0.1:8000"
    assert ipv6.local_api_base_url == "http://[::1]:8000"
