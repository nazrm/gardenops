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
