from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OfflineReplayFrontendStaticTests(unittest.TestCase):
    def test_queue_persists_a_distinct_attachment_operation_id(self) -> None:
        source = (ROOT / "frontend" / "src" / "services" / "offlineQueue.ts").read_text(
            encoding="utf-8"
        )

        self.assertIn("const DB_VERSION = 4", source)
        self.assertIn("operation_id: generateOperationId()", source)
        self.assertIn("backfillDraftOperationIds", source)
        self.assertIn('payload["_serialized_media"]', source)

    def test_replay_wires_draft_and_attachment_ids_to_transport(self) -> None:
        feature = (ROOT / "frontend" / "src" / "features" / "offlineFeature.ts").read_text(
            encoding="utf-8"
        )
        app = (ROOT / "frontend" / "src" / "app.ts").read_text(encoding="utf-8")
        api = (ROOT / "frontend" / "src" / "services" / "api.ts").read_text(encoding="utf-8")

        self.assertGreaterEqual(feature.count("operationId: draft.operation_id"), 7)
        self.assertIn("uploadOfflineAttachments", feature)
        self.assertIn("operationId: operationIds[index]!", feature)
        self.assertEqual(feature.count("operationIds: attachmentOperationIds"), 2)
        self.assertIn("uploadOptions.operationId = options.operationIds[i]!", app)
        self.assertIn("OFFLINE_OPERATION_ID_HEADER", api)
        self.assertIn("mergedHeaders.set(OFFLINE_OPERATION_ID_HEADER, options.operationId)", api)

    def test_migration_keeps_only_the_garden_cascade(self) -> None:
        migration = (ROOT / "migrations" / "0022_offline_operation_idempotency.sql").read_text(
            encoding="utf-8"
        )

        self.assertIn("FOREIGN KEY (garden_id)", migration)
        self.assertNotIn("FOREIGN KEY (journal_entry_id", migration)
        self.assertNotIn("FOREIGN KEY (issue_id", migration)
        self.assertNotIn("FOREIGN KEY (harvest_entry_id", migration)


if __name__ == "__main__":
    unittest.main()
