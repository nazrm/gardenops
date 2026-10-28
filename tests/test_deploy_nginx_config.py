from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_production_nginx_template_scopes_gzip_to_static_assets() -> None:
    config = (ROOT / "deploy" / "nginx.production.example.conf").read_text(encoding="utf-8")

    static_location_start = config.index("location ~* ^/(?!api/).+")
    api_location_start = config.index("# --- admin reads")
    static_location = config[static_location_start:api_location_start]
    server_preamble = config[:static_location_start]

    assert "gzip on;" not in server_preamble
    assert "gzip on;" in static_location
    assert "gzip_vary on;" in static_location
    assert "application/javascript" in static_location
    assert "text/css" in static_location
    assert "application/json" not in static_location
    assert "text/plain" not in static_location


def test_production_nginx_logs_hide_feed_tokens_and_retired_upload_allowance() -> None:
    config = (ROOT / "deploy" / "nginx.production.example.conf").read_text(encoding="utf-8")

    assert "map $uri $gardenops_log_uri" in config
    assert "/calendar/subscriptions/[redacted]" in config
    assert '"$http_referer"' not in config
    assert config.count("access_log /var/log/nginx/gardenops-access.log gardenops_redacted") == 2
    assert config.count("location ^~ /calendar/subscriptions/") == 2
    assert config.count("error_log /dev/null;") == 2
    assert "/lidar" not in config
    assert "256m" not in config


def test_service_examples_disable_duplicate_access_log_and_include_hardening() -> None:
    service = (ROOT / "deploy" / "gardenops.service.example").read_text(encoding="utf-8")
    release = (ROOT / "deploy" / "gardenops-atomic-release.conf.example").read_text(
        encoding="utf-8"
    )
    hardening = (ROOT / "deploy" / "gardenops-hardening.conf.example").read_text(encoding="utf-8")

    assert "--no-access-log" in service
    assert "--no-access-log" in release
    for setting in (
        "PrivateDevices=true",
        "CapabilityBoundingSet=",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
    ):
        assert setting in service
        assert setting in hardening
