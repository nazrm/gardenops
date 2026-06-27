import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FrontendSecurityStaticTests(unittest.TestCase):
    def test_auth_expiry_clears_offline_queue(self) -> None:
        app_ts = (ROOT / "frontend" / "src" / "app.ts").read_text(encoding="utf-8")
        match = re.search(
            r"function handleAuthExpired\(\): void \{(?P<body>.*?)\n\}",
            app_ts,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        self.assertIn("clearOfflineQueue", match.group("body"))

    def test_map_view_does_not_assign_raw_plot_color_to_css_background(self) -> None:
        map_view = (ROOT / "frontend" / "src" / "components" / "mapView.ts").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("el.style.background = plot.color", map_view)


if __name__ == "__main__":
    unittest.main()
