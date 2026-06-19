from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_production_nginx_template_enables_asset_compression() -> None:
    config = (ROOT / "deploy" / "nginx.production.example.conf").read_text(encoding="utf-8")

    assert "gzip on;" in config
    assert "gzip_vary on;" in config
    assert "application/javascript" in config
    assert "text/css" in config
    assert "application/json" in config
