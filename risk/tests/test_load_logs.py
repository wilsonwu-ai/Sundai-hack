import pytest

from risk.ingest.load_logs import LogValidationError, load_logs, main, read_events, validate_event
from risk.scoring.detector import NoDetector, sessions_by_agent


def test_sample_logs_load_without_labels():
    sequences, labels = load_logs()
    assert len(sequences) == 8 and sum(labels.values()) == 4
    assert all("pressure_mode" not in e for s in sequences for e in s)
    assert [e["step"] for e in sequences[1]] == [1, 2, 3, 4]


def test_cli_still_runs(capsys):
    assert main(["load_logs.py"]) == 0
    assert "payments-01 | s_002 | add_beneficiary -> send_ach" in capsys.readouterr().out


@pytest.mark.parametrize(
    "change, message",
    [
        ({"action": "drop_table"}, "not in shared/actions.md"),
        ({"outcome": "maybe"}, "invalid value"),
        ({"step": True}, "should be integer"),
        ({"extra": 1}, "unknown field"),
    ],
)
def test_validate_event_rejects(change, message):
    event = {**read_events()[0], **change}
    with pytest.raises(LogValidationError, match=message):
        validate_event(event)


def test_offline_sessions_feed_the_detector_interface():
    agents = sessions_by_agent(load_logs()[0])
    assert list(agents["kyc-01"]) == ["s_003", "s_004"]
    assert NoDetector().score_agent("kyc-01", agents["kyc-01"])["status"] == "no_detector"
