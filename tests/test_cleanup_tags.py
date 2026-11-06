import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from cleanup_tags import classify, normalise, AMOUNT

def t(i, name, n):
    return {"id": i, "name": name, "document_count": n}

def test_classify():
    tags = [t(1, "114.19", 1), t(2, "amount: 10.00", 1), t(3, "Amount: 103.70", 2), t(4, "€ 1.234,56", 1),
            t(5, "insurance", 40), t(6, "Insurance", 3), t(7, "123 3D", 2), t(8, "123-3D", 5),
            t(9, "unused", 0), t(10, "ai-processed", 732), t(11, "2026", 4)]
    res = {tag["name"]: (cls, target) for tag, cls, target in classify(tags, {"ai-processed"})}
    assert res["114.19"] == ("amount", None)
    assert res["amount: 10.00"] == ("amount", None)
    assert res["Amount: 103.70"] == ("amount", None)
    assert res["€ 1.234,56"] == ("amount", None)
    assert res["Insurance"] == ("variant", 5)
    assert res["insurance"] == ("keep", None)
    assert res["123 3D"] == ("variant", 8)
    assert res["unused"] == ("empty", None)
    assert res["ai-processed"] == ("keep", None)
    assert res["2026"] == ("amount", None)   # a bare number is not a tag either; year tags should read "tax 2026"
    assert normalise("Akoestische  Isolatie!") == "akoestische isolatie"
    assert not AMOUNT.match("tax 2026")

if __name__ == "__main__":
    test_classify(); print("ok")
