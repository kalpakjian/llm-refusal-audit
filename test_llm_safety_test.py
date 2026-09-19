"""
單元測試：驗證 llm_safety_test.py 的指標計算是否正確。
執行： python test_llm_safety_test.py
"""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MOD_PATH = os.path.join(HERE, "llm_safety_test.py")

spec = importlib.util.spec_from_file_location("llm_safety_test", MOD_PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

PASS = 0
FAIL = 0


def check(name, got, want, tol=1e-3):
    global PASS, FAIL
    ok = (abs(got - want) <= tol) if isinstance(want, (int, float)) else (got == want)
    if ok:
        PASS += 1
        print(f"  [OK]   {name}: {got}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}: got {got!r}, want {want!r}")


def make_row(should_refuse, is_refusal, category="illegal",
             risk="high", conf=None, is_error=False):
    return {
        "prompt_id": f"p_{should_refuse}_{is_refusal}",
        "category": category,
        "risk_level": risk,
        "prompt": "x",
        "response": "y",
        "should_refuse": should_refuse,
        "is_refusal": is_refusal,
        "is_compliant": is_refusal,
        "correct_refusal": should_refuse == is_refusal,
        "confidence": conf,
        "is_error": is_error,
    }


print("== 1. parse_confidence ==")
text, conf = m.parse_confidence("答案在此。\n\n[CONFIDENCE: 73]")
check("extract value", conf, 0.73)
check("strip tag", text, "答案在此。")
_, conf2 = m.parse_confidence("no tag here")
check("missing tag -> None", conf2 is None, True)
_, conf3 = m.parse_confidence("[confidence: 100]")
check("case-insensitive + 100", conf3, 1.0)

print("\n== 2. is_refusal_response ==")
check("Chinese refusal", m.is_refusal_response("對不起，我不能幫你"), True)
check("English refusal", m.is_refusal_response("I cannot help with that."), True)
check("Compliance", m.is_refusal_response("這是製作方法的說明："), False)

print("\n== 3. resolve_ollama_host ==")
check("0.0.0.0 -> 127.0.0.1:11434",
      m.resolve_ollama_host("0.0.0.0"), "http://127.0.0.1:11434")
check("bare host gets port",
      m.resolve_ollama_host("localhost"), "http://localhost:11434")
check("explicit port kept",
      m.resolve_ollama_host("http://192.168.1.9:9999"), "http://192.168.1.9:9999")
print("\n== 4. calculate_metrics (confusion matrix / P / R / F1) ==")
# TP=3, FN=1, FP=1, TN=5
rows = (
    [make_row(True, True) for _ in range(3)]
    + [make_row(True, False)]
    + [make_row(False, True, category="benign", risk="low")]
    + [make_row(False, False, category="benign", risk="low") for _ in range(5)]
)
res = {"results": rows, "model": "t", "timestamp": "t",
       "judge_used": False, "extract_confidence": False}
met = m.calculate_metrics(res)

check("TP", met["confusion_matrix"]["TP"], 3)
check("FP", met["confusion_matrix"]["FP"], 1)
check("FN", met["confusion_matrix"]["FN"], 1)
check("TN", met["confusion_matrix"]["TN"], 5)
check("Recall/TPR", met["refusal_rate_tpr"], 3 / 4)
check("FPR", met["over_refusal_rate_fpr"], 1 / 6)
check("Precision", met["precision"], 3 / 4)
check("F1", met["f1_score"], 2 * 0.75 * 0.75 / 1.5)
check("Accuracy", met["accuracy"], 8 / 10)
check("Specificity", met["specificity"], 5 / 6)
check("benign over-refusal", met["over_refusal_rate_benign"], 1 / 6)
check("high-risk refusal", met["high_risk_refusal_rate"], 3 / 4)
check("per_category count", len(met["per_category"]), 2)
check("benign category total", met["per_category"]["benign"]["total"], 6)

print("\n== 5. expected_calibration_error ==")
check("ECE (overconfident)",
      m.expected_calibration_error([True] * 8 + [False] * 2, [1.0] * 10), 0.2)
check("ECE (perfect)",
      m.expected_calibration_error([True, True, False, False],
                                    [1.0, 1.0, 0.0, 0.0]), 0.0)
check("ECE empty", m.expected_calibration_error([], []), 0.0)

print("\n== 6. selective_accuracy ==")
rows2 = [
    make_row(True, True, conf=0.9),
    make_row(False, False, conf=0.8, category="benign", risk="low"),
    make_row(True, False, conf=0.2),
    make_row(False, True, conf=0.1, category="benign", risk="low"),
]
sa = m.selective_accuracy(rows2, coverage_threshold=0.5)
check("n selected", sa["n"], 2)
check("coverage", sa["coverage"], 0.5)
check("selective accuracy", sa["selective_accuracy"], 1.0)
print("\n== 7. run_test_suite with mock model (plumbing) ==")
m.call_local_model = m.mock_call_local_model
m.set_max_tokens(64)
tr = m.run_test_suite(model_name="mock", extract_confidence=True, limit=8)
check("8 results", len(tr["results"]), 8)
check("error_count == 0", tr["error_count"], 0)
check("confidence parsed", isinstance(tr["results"][0]["confidence"], float), True)
met2 = m.calculate_metrics(tr)
check("has roc_auc", "roc_auc" in met2, True)
check("has ece", "ece" in met2, True)
check("total prompts", met2["total_prompts"], 8)

print("\n== 8. calibration plot file ==")
tmp_png = os.path.join(HERE, "_unit_calib.png")
ok = m.plot_calibration_curve(tr["results"], "mock", tmp_png)
check("plot returned True", ok, True)
check("png exists and non-empty",
      os.path.exists(tmp_png) and os.path.getsize(tmp_png) > 1000, True)
if os.path.exists(tmp_png):
    os.remove(tmp_png)

print("\n== 9. reports written ==")
tmp_json = os.path.join(HERE, "_unit_results.json")
tmp_md = os.path.join(HERE, "_unit_report.md")
m.save_results(tr, tmp_json)
m.save_markdown_report(tr, met2, tmp_md)
check("json exists", os.path.exists(tmp_json), True)
check("md exists", os.path.exists(tmp_md), True)
with open(tmp_md, encoding="utf-8") as f:
    md = f.read()
check("md has confusion matrix", "Confusion Matrix" in md, True)
check("md has per-category", "逐類別統計" in md, True)
for p in (tmp_json, tmp_md):
    if os.path.exists(p):
        os.remove(p)

print(f"\n{'=' * 50}")
print(f"RESULT: {PASS} passed, {FAIL} failed")
print("=" * 50)
sys.exit(1 if FAIL else 0)
