"""bank/sim/final_score.py: confusion counts, rates and AUC."""

from bank.sim.final_score import auc, confusion


def test_confusion_counts_and_rates():
    c = confusion([(True, True), (True, False), (False, True), (False, False), (False, False)])
    assert (c["tp"], c["fn"], c["fp"], c["tn"]) == (1, 1, 1, 2)
    assert c["precision"] == 0.5 and c["recall"] == 0.5 and c["f1"] == 0.5
    assert abs(c["fpr"] - 1 / 3) < 1e-9 and c["accuracy"] == 0.6


def test_confusion_with_no_predictions_has_no_precision():
    c = confusion([(True, False), (False, False)])
    assert c["precision"] is None and c["recall"] == 0.0 and c["f1"] is None


def test_auc_counts_ties_as_half():
    assert auc([0.9, 0.1], [True, False]) == 1.0
    assert auc([0.5, 0.5], [True, False]) == 0.5
    assert auc([0.2, 0.8], [True, False]) == 0.0
    assert auc([0.3], [False]) is None
