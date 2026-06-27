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

    def test_plot_plant_path_helpers_encode_public_ids(self) -> None:
        api_ts = (ROOT / "frontend" / "src" / "services" / "api.ts").read_text(encoding="utf-8")
        self.assertIn("function encodeApiPathSegment", api_ts)
        for function_name in (
            "addPlantToPlotApi",
            "removePlantFromPlotApi",
            "updatePlotPlant",
            "movePlantBetweenPlotsApi",
            "deletePlantApi",
            "getPlantAssignmentsApi",
        ):
            match = re.search(
                rf"export async function {function_name}\((?P<body>.*?)\n\}}",
                api_ts,
                flags=re.DOTALL,
            )
            self.assertIsNotNone(match, function_name)
            self.assertIn("encodeApiPathSegment", match.group("body"))

    def test_shademap_tooltip_does_not_bind_raw_label_as_html(self) -> None:
        shade_panel = (ROOT / "frontend" / "src" / "components" / "shadePanel.ts").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("bindTooltip(target.label", shade_panel)
        self.assertNotIn('bindTooltip(t("shade.house_center"', shade_panel)
        self.assertIn("tooltipLabel.textContent = target.label", shade_panel)
        self.assertIn("houseTooltipLabel.textContent", shade_panel)


if __name__ == "__main__":
    unittest.main()
