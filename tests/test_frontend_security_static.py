import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FrontendSecurityStaticTests(unittest.TestCase):
    def test_identity_session_ui_never_renders_token_hashes(self) -> None:
        panel = (ROOT / "frontend" / "src" / "components" / "adminPanel.ts").read_text(
            encoding="utf-8"
        )
        auth_api = (ROOT / "frontend" / "src" / "services" / "authApi.ts").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("token_hash", panel)
        self.assertNotIn('session["token_hash"]', auth_api)
        self.assertIn('session["session_id"] ?? session["id"]', auth_api)
        self.assertIn('session["current"] === true', auth_api)
        self.assertIn("session.device_label", panel)

    def test_phase_five_identity_mutations_use_scoped_contracts(self) -> None:
        auth_api = (ROOT / "frontend" / "src" / "services" / "authApi.ts").read_text(
            encoding="utf-8"
        )
        expected_contracts = (
            "apiPatch(`/api/auth/passkeys/${passkeyId}`",
            "apiDelete(`/api/auth/sessions/${encodedId}`",
            'apiPost("/api/auth/mfa/totp/cancel"',
            'apiPost("/api/auth/mfa/totp/confirm"',
        )
        for contract in expected_contracts:
            self.assertIn(contract, auth_api)
        self.assertIn('headers["x-garden-id"]', auth_api)
        self.assertIn("sessionStorage.setItem(ACTIVE_GARDEN_STORAGE_KEY", auth_api)

    def test_phase_five_identity_actions_have_confirmation_and_live_feedback(self) -> None:
        panel = (ROOT / "frontend" / "src" / "components" / "adminPanel.ts").read_text(
            encoding="utf-8"
        )
        gate = (ROOT / "frontend" / "src" / "features" / "authGate.ts").read_text(encoding="utf-8")
        for key in (
            "identity.passkeys.last_revoke_warning",
            "identity.sessions.revoke_confirm",
            "identity.mfa.cancel_confirm",
            "identity.mfa.regenerate_confirm",
            "identity.mfa.disable_confirm",
        ):
            self.assertIn(key, panel)
        self.assertIn('role="${state.identityNotice.error ? "alert" : "status"}"', panel)
        self.assertIn('error.setAttribute("role", "alert")', gate)
        self.assertIn('error.setAttribute("aria-live", "assertive")', gate)

    def test_plot_meaning_inputs_have_accessible_names(self) -> None:
        panel = (ROOT / "frontend" / "src" / "components" / "adminPanel.ts").read_text(
            encoding="utf-8"
        )
        for field in ("pattern", "label", "description"):
            self.assertIn(
                f'class="adm-input adm-plot-meaning-{field}" aria-label="${{t(',
                panel,
            )

    def test_password_fallback_reveals_before_aborting_passkey(self) -> None:
        gate = (ROOT / "frontend" / "src" / "features" / "authGate.ts").read_text(
            encoding="utf-8"
        )
        handler = re.search(
            r"const revealPasswordFallback = \(\): void => \{(?P<body>.*?)\n  \};",
            gate,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(handler)
        body = handler.group("body")
        self.assertLess(
            body.index("revealPasswordLogin();"),
            body.index("abortController?.abort();"),
        )
        self.assertIn(
            'passwordFallbackBtn.addEventListener("pointerdown", revealPasswordFallback)',
            gate,
        )
        self.assertIn(
            'passwordFallbackBtn.addEventListener("click", revealPasswordFallback)',
            gate,
        )

    def test_membership_mutations_refresh_capabilities(self) -> None:
        panel = (ROOT / "frontend" / "src" / "components" / "adminPanel.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn("async function refreshIdentityCapabilities", panel)
        self.assertIsNotNone(
            re.search(
                r"upsertGardenMembershipApi\(.*?refreshIdentityCapabilities\(\)",
                panel,
                flags=re.DOTALL,
            )
        )
        self.assertIsNotNone(
            re.search(
                r"deleteGardenMembershipApi\(.*?refreshIdentityCapabilities\(\)",
                panel,
                flags=re.DOTALL,
            )
        )

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
