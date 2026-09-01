from __future__ import annotations

import json
import unittest

from support import ROOT  # noqa: I001 - puts lib/ on sys.path


class PanelContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qml = (ROOT / "Panel.qml").read_text(encoding="utf-8")
        cls.icon = (ROOT / "BeeperIcon.qml").read_text(encoding="utf-8")
        cls.manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_panel_only_ever_runs_the_cli(self) -> None:
        self.assertIn('Qt.resolvedUrl("bin/omarchy-beeper")', self.qml)
        self.assertIn('listProc.command = argv', self.qml)
        self.assertIn('readProc.command = [root.script, "read", id]', self.qml)
        self.assertIn('readAllProc.command = [root.script, "read-all"]', self.qml)
        self.assertIn('openProc.command = [root.script, "open", message.id]', self.qml)
        self.assertIn('openProc.command = [root.script, "open"]', self.qml)

    def test_passes_limit_from_widget_settings(self) -> None:
        self.assertIn('setting("max", 25)', self.qml)
        self.assertIn('setting("refreshIntervalSec", 60)', self.qml)
        self.assertIn('"--limit"', self.qml)
        self.assertIn('"--page"', self.qml)

    def test_settings_schema_matches_the_panel_clamps(self) -> None:
        schema = {item["key"]: item for item in self.manifest["barWidget"]["schema"]}
        self.assertEqual((schema["max"]["min"], schema["max"]["max"]), (1, 50))
        self.assertEqual(schema["max"]["defaultValue"], 25)
        self.assertEqual(
            (schema["refreshIntervalSec"]["min"], schema["refreshIntervalSec"]["max"]), (15, 3600)
        )
        self.assertEqual(schema["refreshIntervalSec"]["defaultValue"], 60)
        self.assertEqual(self.manifest["barWidget"]["defaults"], {"max": 25, "refreshIntervalSec": 60})
        self.assertEqual(self.manifest["kinds"], ["bar-widget"])
        self.assertEqual(self.manifest["entryPoints"]["barWidget"], "Panel.qml")
        self.assertEqual(self.manifest["schemaVersion"], 1)
        self.assertIn("Math.min(50, n)", self.qml)
        self.assertIn("Math.min(3600, n)", self.qml)

    def test_every_untrusted_string_is_plain_text(self) -> None:
        # One `textFormat: Text.PlainText` per Text that renders API data.
        self.assertGreaterEqual(self.qml.count("textFormat: Text.PlainText"), 12)
        for banned in ("Text.RichText", "Text.StyledText", "Text.MarkdownText"):
            self.assertNotIn(banned, self.qml)

    def test_ids_are_validated_before_use(self) -> None:
        self.assertIn("/^beeper:[A-Za-z0-9_-]{1,1024}$/", self.qml)
        self.assertIn("/^[0-9]{1,9}$/", self.qml)
        self.assertIn("if (!message || !validId(message.id)) return", self.qml)
        self.assertIn("nextPage = validToken(data.nextPage)", self.qml)

    def test_row_click_opens_dismisses_and_marks_read(self) -> None:
        self.assertIn("function openChat(message)", self.qml)
        self.assertRegex(
            self.qml,
            r"openProc\.running = true\n    dismissLocal\(message\.id\)\n    enqueueRead\(message\.id\)\n    close\(\)",
        )

    def test_local_dismiss_and_read_queue_survive_a_slow_api(self) -> None:
        self.assertIn("property var dismissedIds", self.qml)
        self.assertIn("property var readQueue", self.qml)
        self.assertIn("property string pendingId", self.qml)
        self.assertIn("function enqueueRead(", self.qml)
        self.assertIn("function pumpRead()", self.qml)
        self.assertIn("unread = Math.max(0, (data.unread || 0) - dropped)", self.qml)
        self.assertNotIn("unread = 0", self.qml)
        self.assertNotIn("messages = []", self.qml)

    def test_keyboard_map_is_complete(self) -> None:
        self.assertIn("onMoveRequested", self.qml)
        self.assertIn("onActivateRequested: root.activateCursor()", self.qml)
        self.assertIn("onTabRequested", self.qml)
        self.assertIn("onCloseRequested", self.qml)
        for key in ("o", "i", "a", "A", "n", "p"):
            self.assertIn(f't === "{key}"', self.qml)
        self.assertRegex(self.qml, r't === "a"\)\s+root\.markCursorRead\(\)')
        self.assertRegex(self.qml, r't === "A"\)\s+root\.requestMarkAll\(\)')

    def test_mark_all_needs_two_presses_and_shows_progress(self) -> None:
        self.assertIn("Mark all unread as read (A)", self.qml)
        self.assertIn("Click again to confirm", self.qml)
        self.assertIn("Marking unread chats as read…", self.qml)
        self.assertIn("property bool markAllArmed", self.qml)
        self.assertIn("property bool markAllBusy", self.qml)
        self.assertIn("applyReadAllPayload", self.qml)
        self.assertIn("opacity: root.markAllBusy ? 0.4 : 1", self.qml)
        self.assertIn("markAllArmTimer.restart()", self.qml)

    def test_unreachable_state_dims_the_icon_and_keeps_the_list(self) -> None:
        self.assertIn("opacity: root.reachable ? 1 : 0.5", self.qml)
        self.assertIn("Could not reach Beeper. Showing the last list.", self.qml)
        self.assertIn("if (!reachable) return", self.qml)
        self.assertIn("Beeper unreachable", self.qml)

    def test_tooltip_counts_unread_chats(self) -> None:
        self.assertIn("function tooltipFor()", self.qml)
        self.assertIn("tooltipText: root.tooltipFor()", self.qml)
        self.assertIn('return root.unread + " unread chats"', self.qml)
        self.assertIn('return "No unread chats"', self.qml)

    def test_badge_uses_bar_foreground_never_the_urgent_colour(self) -> None:
        self.assertIn("color: button.foreground", self.qml)
        self.assertIn("dotColor: button.foreground", self.qml)
        self.assertIn(
            "color: Qt.rgba(button.foreground.r, button.foreground.g,\n                           button.foreground.b, 0.14)",
            self.qml,
        )
        self.assertNotIn("button.activeColor", self.qml)
        self.assertNotIn("badgeCount\n              color: bar.urgent", self.qml)
        self.assertNotIn("active: root.opened", self.qml)

    def test_network_chip_only_shows_with_more_than_one_account(self) -> None:
        self.assertIn("visible: root.accountCount > 1", self.qml)
        self.assertIn("(row.modelData.labels || []).slice(0, 1)", self.qml)
        self.assertIn("elide: Text.ElideRight", self.qml)
        self.assertIn("Style.space(64)", self.qml)
        self.assertIn("Math.max(Style.space(40)", self.qml)

    def test_icon_is_a_stroked_bubble_with_no_layer_effects(self) -> None:
        self.assertIn("ctx.stroke()", self.icon)
        self.assertIn("ctx.arcTo(", self.icon)
        self.assertIn("dotAmount", self.icon)
        self.assertIn("snapStroke", self.icon)
        self.assertNotIn("layer.enabled", self.icon)
        self.assertNotIn("Image {", self.icon)

    def test_bubble_and_dots_stay_inside_the_canvas(self) -> None:
        for size in (10, 12, 16, 20, 24, 32, 48):
            for dpr in (1.0, 1.25, 1.5, 2.0):
                geom = bubble_geometry(size, dpr)
                self.assertGreater(geom["bodyH"], 0, msg=f"size={size} dpr={dpr}")
                self.assertGreater(geom["tailRight"], geom["tailLeft"])
                for amount in (0.0, 0.5, 1.0):
                    for x, y in painted_extents(geom, amount):
                        self.assertGreaterEqual(x, -0.51, msg=f"size={size} dpr={dpr} t={amount}")
                        self.assertGreaterEqual(y, -0.51, msg=f"size={size} dpr={dpr} t={amount}")
                        self.assertLessEqual(x, size + 0.51, msg=f"size={size} dpr={dpr} t={amount}")
                        self.assertLessEqual(y, size + 0.51, msg=f"size={size} dpr={dpr} t={amount}")

    def test_tail_never_runs_into_a_corner_arc(self) -> None:
        for size in (10, 16, 24, 48):
            geom = bubble_geometry(size, 1.0)
            self.assertGreaterEqual(geom["tailLeft"], geom["bodyX"] + geom["radius"] - 0.01)
            self.assertLessEqual(
                geom["tailRight"], geom["bodyX"] + geom["bodyW"] - geom["radius"] + 0.01
            )

    def test_readme_documents_install_setup_and_removal(self) -> None:
        self.assertIn("https://github.com/prdlk/omarchy-beeper.git", self.readme)
        self.assertIn("omarchy plugin add", self.readme)
        self.assertIn("--enable", self.readme)
        self.assertIn("omarchy plugin update prdlk.beeper", self.readme)
        self.assertIn("omarchy-beeper auth", self.readme)
        self.assertIn("~/.config/omarchy-beeper/", self.readme)
        self.assertIn("Settings", self.readme)
        self.assertIn("Integrations", self.readme)
        self.assertIn("revoke", self.readme)
        self.assertIn("muted", self.readme)
        self.assertIn("archived", self.readme)
        self.assertIn("low-priority", self.readme)
        self.assertIn("indexing", self.readme)
        self.assertIn("never sends", self.readme.lower())
        self.assertIn("preview.png", self.readme)
        self.assertTrue((ROOT / "preview.png").is_file(), "marketplace listing wants preview.png")
        self.assertTrue((ROOT / "docs" / "SETUP.md").is_file())
        self.assertTrue((ROOT / "LICENSE").is_file())

    def test_readme_key_map_matches_the_panel(self) -> None:
        for key in ("`j`", "`k`", "`o`", "`a`", "`A`", "`n`", "`p`", "`i`", "`Tab`", "`Esc`"):
            self.assertIn(key, self.readme)

    def test_changelog_matches_manifest(self) -> None:
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## {self.manifest['version']}", changelog)


def _snap(value: float, dpr: float) -> float:
    return round(value * dpr) / dpr


def _snap_stroke(value: float, dpr: float) -> float:
    return max(1, round(value * dpr)) / dpr


def bubble_geometry(icon_size: float, dpr: float = 1.0) -> dict[str, float]:
    """Mirror of BeeperIcon.qml so the geometry can be checked without a GPU."""
    stroke = _snap_stroke(max(1.5, icon_size * 0.11), dpr)
    pad = _snap(max(stroke / 2, 0.5), dpr)
    tail_h = _snap(max(stroke * 1.2, icon_size * 0.16), dpr)
    body_x = _snap(pad, dpr)
    body_y = _snap(pad, dpr)
    body_w = _snap(icon_size - pad * 2, dpr)
    body_h = _snap(max(stroke * 3, icon_size - pad * 2 - tail_h), dpr)
    radius = _snap(min(body_w, body_h) * 0.3, dpr)
    tail_left = _snap(body_x + max(radius, body_w * 0.2), dpr)
    tail_right = _snap(tail_left + max(stroke, body_w * 0.2), dpr)
    tail_tip_x = _snap(tail_left + max(stroke / 2, body_w * 0.04), dpr)
    dot_r = _snap(max(1, min(icon_size * 0.075, body_h * 0.16)), dpr)
    dot_gap = _snap(max(dot_r * 2.2, body_w * 0.24), dpr)
    dot_y = _snap(body_y + body_h / 2, dpr)
    return {
        "size": icon_size,
        "stroke": stroke,
        "pad": pad,
        "tailH": tail_h,
        "bodyX": body_x,
        "bodyY": body_y,
        "bodyW": body_w,
        "bodyH": body_h,
        "radius": min(radius, body_w / 2, body_h / 2),
        "tailLeft": tail_left,
        "tailRight": tail_right,
        "tailTipX": tail_tip_x,
        "dotR": dot_r,
        "dotGap": dot_gap,
        "dotY": dot_y,
    }


def painted_extents(geom: dict[str, float], dot_amount: float) -> list[tuple[float, float]]:
    """Outermost painted points, stroke width included."""
    half = geom["stroke"] / 2
    x = geom["bodyX"]
    y = geom["bodyY"]
    w = geom["bodyW"]
    h = geom["bodyH"]
    points = [
        (x - half, y - half),
        (x + w + half, y - half),
        (x + w + half, y + h + half),
        (x - half, y + h + half),
        (geom["tailTipX"] - half, y + h + geom["tailH"] + half),
        (geom["tailTipX"] + half, y + h + geom["tailH"] + half),
    ]
    if dot_amount > 0.01:
        scaled = geom["dotR"] * (0.6 + 0.4 * dot_amount)
        cx = x + w / 2
        for i in (-1, 0, 1):
            points.append((cx + i * geom["dotGap"] - scaled, geom["dotY"] - scaled))
            points.append((cx + i * geom["dotGap"] + scaled, geom["dotY"] + scaled))
    return points


if __name__ == "__main__":
    unittest.main()
