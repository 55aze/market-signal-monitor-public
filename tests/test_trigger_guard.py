import unittest

from scripts.validate_trigger import validate


class TriggerGuardTests(unittest.TestCase):
    def test_external_live_uses_configured_universe(self):
        result = validate(
            "workflow_dispatch", "live", "external",
            "2026-09-11T12:07:00Z", "",
        )
        self.assertEqual(result["trigger_source"], "external")
        self.assertEqual(result["scheduled_for"], "2026-09-11T12:07:00Z")
        self.assertIsNone(result["instruments_override"])

    def test_external_live_rejects_instrument_override(self):
        with self.assertRaisesRegex(ValueError, "may not override instruments"):
            validate(
                "workflow_dispatch", "live", "external",
                "2026-09-11T12:07:00Z", "QQQ",
            )

    def test_external_requires_aware_scheduled_time(self):
        for value in ("", "2026-09-11T12:07:00", "not-a-time"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate("workflow_dispatch", "live", "external", value, "")

    def test_external_scheduler_cannot_backfill(self):
        with self.assertRaisesRegex(ValueError, "may not run backfill"):
            validate(
                "workflow_dispatch", "backfill", "external",
                "2026-09-11T12:07:00Z", "",
            )

    def test_external_dry_run_may_be_bounded_for_rollout(self):
        result = validate(
            "workflow_dispatch", "dry-run", "external",
            "2026-09-11T12:07:00+00:00", "QQQ",
        )
        self.assertEqual(result["scheduled_for"], "2026-09-11T12:07:00Z")
        self.assertEqual(result["instruments_override"], "QQQ")

    def test_manual_dispatch_remains_safe_and_separate(self):
        result = validate("workflow_dispatch", "dry-run", "manual", "", "QQQ")
        self.assertEqual(result["trigger_source"], "manual")
        with self.assertRaisesRegex(ValueError, "manual dispatch must not set scheduled_for"):
            validate(
                "workflow_dispatch", "dry-run", "manual",
                "2026-09-11T12:07:00Z", "QQQ",
            )

    def test_native_events_are_audit_labeled_without_dispatch_inputs(self):
        scheduled = validate("schedule", "live", "", "", "")
        pushed = validate("push", "dry-run", "", "", "QQQ")
        self.assertEqual(scheduled["trigger_source"], "github-schedule")
        self.assertEqual(pushed["trigger_source"], "push")


if __name__ == "__main__":
    unittest.main()
