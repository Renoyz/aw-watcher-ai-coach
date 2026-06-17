"""Tests for SQLite storage layer."""

from datetime import datetime, timedelta

import pytest

from aw_coach.storage import Storage


@pytest.fixture
def storage(tmp_path):
    db_path = tmp_path / "test.db"
    return Storage(db_path)


class TestCostLog:
    def test_record_and_query_monthly(self, storage):
        storage.record_cost("gpt-4o-mini", 500, 100, 0.05, "batch_classify")
        storage.record_cost("gpt-4o-mini", 300, 80, 0.03, "generate_report")
        assert storage.get_monthly_cost() == pytest.approx(0.08)

    def test_monthly_cost_only_current_month(self, storage):
        storage.record_cost("gpt-4o-mini", 500, 100, 0.05, "batch_classify")
        # Manually insert old record
        last_month = datetime.now() - timedelta(days=35)
        storage._conn.execute(
            "INSERT INTO cost_log "
            "(timestamp, model, input_tokens, output_tokens, cost_usd, operation) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (last_month.isoformat(), "gpt-4o", 1000, 500, 1.00, "old"),
        )
        storage._conn.commit()
        assert storage.get_monthly_cost() == pytest.approx(0.05)

    def test_get_cost_breakdown(self, storage):
        storage.record_cost("gpt-4o-mini", 500, 100, 0.05, "batch_classify")
        storage.record_cost("gpt-4o-mini", 300, 80, 0.03, "batch_classify")
        storage.record_cost("gpt-4o-mini", 200, 50, 0.02, "generate_report")
        breakdown = storage.get_cost_breakdown()
        assert breakdown["batch_classify"] == pytest.approx(0.08)
        assert breakdown["generate_report"] == pytest.approx(0.02)


class TestBatchQueue:
    def test_enqueue_and_get_pending(self, storage):
        storage.enqueue_batch_item("09:00", "09:15", "chrome", "HN", None, 0.50)
        storage.enqueue_batch_item("09:15", "09:30", "unknown", "Win", None, 0.0)
        pending = storage.get_pending_batch(limit=10)
        assert len(pending) == 2
        assert pending[0]["app"] == "chrome"

    def test_get_pending_respects_limit(self, storage):
        for i in range(20):
            storage.enqueue_batch_item(f"{i}", f"{i+1}", "app", "t", None, 0.3)
        pending = storage.get_pending_batch(limit=8)
        assert len(pending) == 8

    def test_mark_processed(self, storage):
        storage.enqueue_batch_item("09:00", "09:15", "chrome", "HN", None, 0.50)
        pending = storage.get_pending_batch(limit=10)
        assert len(pending) == 1
        storage.mark_batch_processed([pending[0]["id"]])
        pending2 = storage.get_pending_batch(limit=10)
        assert len(pending2) == 0


class TestCorrections:
    def test_add_and_get_corrections(self, storage):
        storage.add_correction(
            timestamp="2026-05-30T09:00:00",
            app="chrome",
            title="HN",
            original_type="research",
            corrected_type="entertainment",
        )
        corrections = storage.get_corrections_last_30_days()
        assert len(corrections) == 1
        assert corrections[0]["corrected_type"] == "entertainment"

    def test_correction_count_by_app(self, storage):
        for _ in range(4):
            storage.add_correction("2026-05-30T09:00", "myapp", "t", "unknown", "programming")
        storage.add_correction("2026-05-30T10:00", "other", "t", "unknown", "writing")
        counts = storage.get_correction_counts()
        assert counts[("myapp", "programming")] == 4
        assert counts[("other", "writing")] == 1

    def test_rule_suggestion_stats_include_source_metadata(self, storage):
        for _ in range(3):
            storage.add_correction("2026-05-30T09:00", "myapp", "t", "unknown", "programming")

        stats = storage.get_rule_suggestion_stats()

        assert len(stats) == 1
        assert stats[0]["app"] == "myapp"
        assert stats[0]["corrected_type"] == "programming"
        assert stats[0]["correction_count"] == 3
        assert stats[0]["latest_corrected_at"]
        assert "unknown" in stats[0]["original_types"]

    def test_rule_suggestion_decision_is_persisted(self, storage):
        storage.set_rule_suggestion_status("MyApp", "programming", "rejected")

        decisions = storage.get_rule_suggestion_decisions()

        assert decisions[("myapp", "programming")] == "rejected"


class TestStorageInit:
    def test_creates_tables(self, tmp_path):
        db_path = tmp_path / "new.db"
        storage = Storage(db_path)
        # Should not raise
        storage.record_cost("m", 0, 0, 0, "op")
        storage.enqueue_batch_item("a", "b", "c", "d", None, 0.5)
        storage.add_correction("ts", "app", "t", "old", "new")

    def test_idempotent_migrate(self, tmp_path):
        db_path = tmp_path / "idem.db"
        s1 = Storage(db_path)
        s1.record_cost("m", 100, 50, 0.01, "op")
        s2 = Storage(db_path)  # Re-open, re-migrate
        assert s2.get_monthly_cost() == pytest.approx(0.01)


class TestDeliveryLog:
    def test_record_and_read_recent_delivery(self, storage):
        storage.record_delivery(
            "summary",
            "notify",
            "sent",
            reason="ok",
            title="AI Coach 摘要",
        )

        rows = storage.get_recent_delivery_logs(limit=5)

        assert len(rows) == 1
        assert rows[0]["kind"] == "summary"
        assert rows[0]["channel"] == "notify"
        assert rows[0]["status"] == "sent"
        assert rows[0]["reason"] == "ok"
        assert "T" in rows[0]["timestamp"]

    def test_recent_delivery_issue_filters_failed_or_suppressed(self, storage):
        storage.record_delivery("summary", "notify", "sent")
        storage.record_delivery("daily_report", "notify", "suppressed", "quiet_hours")

        issue = storage.get_recent_delivery_issue()

        assert issue is not None
        assert issue["kind"] == "daily_report"
        assert issue["status"] == "suppressed"
